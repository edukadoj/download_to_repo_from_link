#!/usr/bin/env python3
# ==============================================================================
# profile_cache.py – Version 1.5.3
#   - load_profile now returns (success, loaded_dir) so the caller can purge
#     other caches after a successful load.
# ==============================================================================

import os, time, base64, hashlib, json, subprocess, re, tempfile, shutil, tarfile, io, glob
from typing import Optional, Tuple
from cryptography.fernet import Fernet
from uploader import reassemble_flat

# ---------------------------------------------------------------------------
# Essential files (located directly inside a profile directory, e.g. Default/)
# ---------------------------------------------------------------------------
_ESSENTIAL_FILES = {
    "Cookies",
    "Login Data",
    "Web Data",
    "Preferences",
    "Bookmarks",
    "Favicons",
    "History",
    "Top Sites",
    "Shortcuts",
    "Network Action Predictor",
    "Affiliation Database",
}

# ---------------------------------------------------------------------------
# Essential directories – their entire subtree is included
# ---------------------------------------------------------------------------
_ESSENTIAL_DIRS = {
    "Local Storage",
    "IndexedDB",
    "Service Worker",
    "Local Extension Settings",
    "Extensions",
    "Network",
}

# ---------------------------------------------------------------------------
# Directories that are excluded even if they match an essential name
# ---------------------------------------------------------------------------
_CACHE_EXCLUDE_DIRS = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
    "component_crx_cache",
    "extensions_crx_cache",
}


def load_profile(cache_dir: str, profile_dir: str, encryption_key: bytes, repo: str,
                 report_callback=None) -> Tuple[bool, Optional[str]]:
    """
    Find the newest timestamped cache directory, reassemble, decrypt, extract.
    Returns (success: bool, loaded_dir_name: str or None).
    """
    subdirs = []
    if os.path.isdir(cache_dir):
        for name in os.listdir(cache_dir):
            subpath = os.path.join(cache_dir, name)
            if os.path.isdir(subpath) and re.match(r'^\d{8}_\d{6}$', name):
                subdirs.append(name)
    subdirs.sort(reverse=True)

    for dirname in subdirs:
        cache_path = os.path.join(cache_dir, dirname)
        part_files = glob.glob(os.path.join(cache_path, "*.part*"))
        if not part_files:
            continue

        tmp_reassemble = tempfile.mkdtemp(prefix="profile_reassemble_")
        try:
            count = reassemble_flat(cache_path, tmp_reassemble)
            if count == 0:
                continue
            files = [f for f in os.listdir(tmp_reassemble) if os.path.isfile(os.path.join(tmp_reassemble, f))]
            if not files:
                continue
            reassembled_path = os.path.join(tmp_reassemble, files[0])
            with open(reassembled_path, "rb") as f:
                encrypted = f.read()
            decrypted = Fernet(encryption_key).decrypt(encrypted)
            shutil.rmtree(profile_dir, ignore_errors=True)
            tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz').extractall('/tmp')
            return True, dirname
        except Exception as e:
            shutil.rmtree(cache_path, ignore_errors=True)
            if report_callback:
                report_callback("cachecorrupted", f"Profile cache {dirname} is corrupted and has been deleted.")
        finally:
            shutil.rmtree(tmp_reassemble, ignore_errors=True)

    return False, None


def _copy_essential_profile(src_dir: str, dst_dir: str) -> None:
    """
    Copy only the files and directories necessary to preserve login sessions
    from src_dir to dst_dir.  Cache directories and unnecessary files are
    skipped so the copy stays small and avoids file‑lock issues with a
    live Chrome instance.
    """
    for root, dirs, files in os.walk(src_dir, topdown=True):
        rel_root = os.path.relpath(root, src_dir)

        if rel_root == ".":
            # Top level: keep only essential files and directories
            files[:] = [f for f in files if f in _ESSENTIAL_FILES]
            dirs[:] = [d for d in dirs if d in _ESSENTIAL_DIRS and d not in _CACHE_EXCLUDE_DIRS]
        else:
            # Inside an essential directory: keep all files, but prune cache dirs
            dirs[:] = [d for d in dirs if d not in _CACHE_EXCLUDE_DIRS]

        # Create corresponding directories in destination
        target_dir = os.path.join(dst_dir, rel_root)
        os.makedirs(target_dir, exist_ok=True)

        # Copy files
        for name in files:
            src_path = os.path.join(root, name)
            dst_path = os.path.join(target_dir, name)
            try:
                shutil.copy2(src_path, dst_path)
            except (OSError, IOError):
                # File may be locked; skip it
                pass


def save_profile(cache_dir, profile_dir, encryption_key, repo, pat,
                 rw,                  # RepoWrapper instance
                 chunk_size_mb=20):   # 20 MB chunks
    """
    Encrypt the current browser profile, split into chunks, upload each raw
    .part via RepoWrapper, and delete old caches.

    Returns (success: bool, message: str).
    """
    try:
        if not os.path.isdir(profile_dir):
            return (False, f"Profile directory not found: {profile_dir}")

        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        cache_subdir = os.path.join(cache_dir, ts)
        os.makedirs(cache_subdir, exist_ok=True)

        # ---- Copy essential profile data to a temporary directory ----
        tmp_profile = tempfile.mkdtemp(prefix="profile_copy_")
        try:
            # 1. Copy the Default/ subdirectory (the actual profile)
            default_dir = os.path.join(profile_dir, "Default")
            if os.path.isdir(default_dir):
                dst_default = os.path.join(tmp_profile, "Default")
                _copy_essential_profile(default_dir, dst_default)

            # 2. Copy the top‑level Local State file (encryption key for cookies)
            local_state = os.path.join(profile_dir, "Local State")
            if os.path.isfile(local_state):
                shutil.copy2(local_state, os.path.join(tmp_profile, "Local State"))
        except Exception:
            # Clean up on failure
            shutil.rmtree(tmp_profile, ignore_errors=True)
            raise

        # Create encrypted tar.gz – the archive root will be "chrome_profile"
        # so that extraction at /tmp recreates /tmp/chrome_profile
        try:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(tmp_profile, arcname="chrome_profile")
        finally:
            shutil.rmtree(tmp_profile, ignore_errors=True)

        encrypted = Fernet(encryption_key).encrypt(buf.getvalue())

        # Write encrypted blob to a fixed file name inside the timestamp folder
        profile_dat_path = os.path.join(cache_subdir, "profile.dat")
        with open(profile_dat_path, "wb") as f:
            f.write(encrypted)

        chunker_script = "python/chunker.py"
        if not os.path.exists(chunker_script):
            shutil.rmtree(cache_subdir, ignore_errors=True)
            return (False, f"Chunker script not found: {chunker_script}")

        cmd = [
            "python3", chunker_script, "--file", profile_dat_path,
            "--output-dir", cache_subdir, "--chunk-size", str(chunk_size_mb)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            shutil.rmtree(cache_subdir, ignore_errors=True)
            return (False, f"chunker.py failed: {result.stderr.strip() or result.stdout.strip()}")

        # Remove the un‑chunked profile.dat (we only upload the parts)
        if os.path.exists(profile_dat_path):
            os.remove(profile_dat_path)

        # Verify local parts are non‑zero
        part_files = glob.glob(os.path.join(cache_subdir, "*.part*"))
        if not part_files or any(os.path.getsize(p) == 0 for p in part_files):
            shutil.rmtree(cache_subdir, ignore_errors=True)
            return (False, "Chunking produced empty or missing parts.")

        # Upload each raw .part file via RepoWrapper
        for idx, part_file in enumerate(part_files, 1):
            rel_path = os.path.relpath(part_file, start=".")
            with open(part_file, "rb") as pf:
                content = pf.read()
            if not rw.upload_file_now(rel_path, content):
                shutil.rmtree(cache_subdir, ignore_errors=True)
                return (False, f"Failed to upload chunk {rel_path}")
            if rw.report_callback:
                rw.report_callback("save-progress",
                                   f"Saved chunk {idx}/{len(part_files)}: {os.path.basename(part_file)}")

        # Remove older caches
        for name in os.listdir(cache_dir):
            full = os.path.join(cache_dir, name)
            if os.path.isdir(full) and re.match(r'^\d{8}_\d{6}$', name) and name != ts:
                shutil.rmtree(full, ignore_errors=True)

        return (True, "Profile cache saved successfully.")
    except Exception as e:
        return (False, f"save_profile exception: {e}")