#!/usr/bin/env python3
# ==============================================================================
# profile_cache.py – Version 1.1.0
#   - Uses downloader.upload_chunk_to_repo for reliable chunk upload.
#   - Paired with uploader.py for reassembly.
# ==============================================================================

import os, time, base64, hashlib, json, subprocess, re, tempfile, shutil, tarfile, io, glob
from cryptography.fernet import Fernet
from uploader import reassemble_flat
from downloader import upload_chunk_to_repo, get_remote_file_size, delete_remote_file


def load_profile(cache_dir, profile_dir, encryption_key, repo, report_callback=None):
    """
    Find the newest timestamped cache directory, reassemble, decrypt, extract.
    Returns True if a profile was loaded successfully.
    report_callback(report_type, text) is called for autonomous reports (e.g., cachecorrupted).
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
            return True
        except Exception as e:
            shutil.rmtree(cache_path, ignore_errors=True)
            if report_callback:
                report_callback("cachecorrupted", f"Profile cache {dirname} is corrupted and has been deleted.")
        finally:
            shutil.rmtree(tmp_reassemble, ignore_errors=True)

    return False


def save_profile(cache_dir, profile_dir, encryption_key, repo, pat, chunk_size_mb=20):
    """
    Encrypt the current browser profile, split into chunks, upload each chunk
    directly via downloader.upload_chunk_to_repo (with size verification),
    and delete old caches.
    
    Returns (success: bool, message: str).
    Does NOT acquire any locks – the caller must handle concurrency.
    """
    try:
        if not os.path.isdir(profile_dir):
            return (False, f"Profile directory not found: {profile_dir}")

        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        cache_subdir = os.path.join(cache_dir, ts)
        os.makedirs(cache_subdir, exist_ok=True)

        # Create encrypted tar.gz
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(profile_dir, arcname="chrome_profile")
        encrypted = Fernet(encryption_key).encrypt(buf.getvalue())

        # Write encrypted blob to a temporary file
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="profile_", suffix=".dat")
        os.close(tmp_fd)
        with open(tmp_path, "wb") as f:
            f.write(encrypted)

        # Chunk the file using chunker.py
        chunker_script = "python/chunker.py"
        if not os.path.exists(chunker_script):
            os.remove(tmp_path)
            return (False, f"Chunker script not found: {chunker_script}")

        cmd = [
            "python3", chunker_script, "--file", tmp_path,
            "--output-dir", cache_subdir, "--chunk-size", str(chunk_size_mb)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(tmp_path)

        if result.returncode != 0:
            shutil.rmtree(cache_subdir, ignore_errors=True)
            return (False, f"chunker.py failed: {result.stderr.strip() or result.stdout.strip()}")

        # Verify local parts are non‑zero and record their sizes
        local_parts = glob.glob(os.path.join(cache_subdir, "*.part*"))
        if not local_parts or any(os.path.getsize(p) == 0 for p in local_parts):
            shutil.rmtree(cache_subdir, ignore_errors=True)
            return (False, "Chunking produced empty or missing parts.")

        # Upload each chunk via downloader.upload_chunk_to_repo
        for part_path in local_parts:
            part_name = os.path.basename(part_path)
            remote_path = f".profile_cache/{ts}/{part_name}"
            local_size = os.path.getsize(part_path)

            # If remote already matches, skip
            remote_size = get_remote_file_size(repo, remote_path, pat)
            if remote_size == local_size:
                continue

            # Delete any existing (likely corrupt) remote file and upload
            if remote_size is not None:
                delete_remote_file(repo, remote_path, pat)

            if not upload_chunk_to_repo(repo, part_path, remote_path, pat):
                shutil.rmtree(cache_subdir, ignore_errors=True)
                return (False, f"Failed to upload chunk {part_name}")

        # All chunks uploaded and verified – remove older caches
        for name in os.listdir(cache_dir):
            full = os.path.join(cache_dir, name)
            if os.path.isdir(full) and re.match(r'^\d{8}_\d{6}$', name) and name != ts:
                shutil.rmtree(full, ignore_errors=True)

        return (True, "Profile cache saved successfully.")
    except Exception as e:
        return (False, f"save_profile exception: {e}")