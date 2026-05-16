#!/usr/bin/env python3
# ==============================================================================
# upload_handler.py – Version 2.0.7 (no file renaming, keep original names)
# ==============================================================================
import os, re, shutil, tempfile, time, json, threading, subprocess
from urllib.request import urlopen, Request
from uploader import reassemble_flat


def _cdp_send(driver, method, params=None, timeout=5):
    """Send a raw CDP command via the debugger URL (no Selenium DevTools)."""
    try:
        debugger_url = driver.command_executor._url
        base = debugger_url.rsplit("/", 1)[0]
        session_id = driver.session_id
        cdp_url = f"{base}/session/{session_id}/chromium/send_command_and_get_result"
        payload = json.dumps({"cmd": method, "params": params or {}}).encode("utf-8")
        req = Request(cdp_url, data=payload,
                      headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def inject_selected_file(driver, get_upload_paths, log_func=None):
    """
    Keep trying to accept a pending file‑chooser dialog for up to 12 seconds.
    """
    paths = get_upload_paths()
    if not paths:
        if log_func:
            log_func("⚠️ No file selected for upload.")
        return False
    file_path = paths[0]

    _cdp_send(driver, "Page.setInterceptFileChooserDialog", {"enabled": True})

    if log_func:
        log_func("🔍 Waiting for file‑chooser dialog…")
    deadline = time.time() + 12
    while time.time() < deadline:
        result = _cdp_send(driver, "Page.handleFileChooser",
                           {"action": "accept", "files": [file_path]})
        if result and "error" not in result:
            if log_func:
                log_func(f"✅ File injected via raw CDP: {file_path}")
            return True
        time.sleep(0.5)

    if log_func:
        log_func("❌ No file‑chooser dialog appeared within 12 seconds.")
    return False


def perform_upload(DOWNLOAD_DIR, LOG_FILENAME,
                   refresh_file_registry, add_autonomous_report,
                   _file_registry, _upload_file_paths,
                   git_push_with_retry,
                   inject_file_fn,
                   log_func=None):
    """Pull latest chunks, flat‑reassemble (like batch file), keep original names."""
    chunks_source = "chunks"

    # ── Pull latest repo to get newly pushed chunks ──
    try:
        git_pull = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                                  capture_output=True, text=True)
        if git_pull.returncode != 0:
            if log_func:
                log_func(f"⚠️ Git pull warning: {git_pull.stderr.strip()}")
        else:
            if log_func:
                log_func("✅ Git pull succeeded – chunks folder is up to date.")
    except Exception as e:
        if log_func:
            log_func(f"❌ Git pull error (continuing anyway): {e}")

    if not os.path.isdir(chunks_source):
        return f"ERR upload: {chunks_source} directory not found"

    chunk_files = [f for f in os.listdir(chunks_source) if ".part" in f]
    if not chunk_files:
        return "ERR upload: no .part files in chunks/"

    # Group by base name (everything before .partNNNN)
    groups = {}
    for f in chunk_files:
        m = re.match(r"(.+)\.part\d+$", f)
        if m:
            base = m.group(1)
            groups.setdefault(base, []).append(f)

    if not groups:
        return "ERR upload: could not parse part filenames"

    flat_temp = tempfile.mkdtemp(prefix="chunks_flat_")
    try:
        # ── Copy all parts into a single flat folder, preserving original filenames ──
        for parts in groups.values():
            for p in parts:
                src = os.path.join(chunks_source, p)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(flat_temp, p))
                    if log_func:
                        log_func(f"  Copied {p} ({os.path.getsize(src)} bytes)")

        # ── Compute total expected size for each base (sanity check) ──
        total_sizes = {}
        for base, parts in groups.items():
            total = sum(os.path.getsize(os.path.join(flat_temp, p)) for p in parts)
            total_sizes[base] = total
            if log_func:
                log_func(f"  Base '{base}': {len(parts)} parts, total size {total} bytes")

        # ── Reassemble using flat method (exactly like copy /b) ──
        count = reassemble_flat(flat_temp, DOWNLOAD_DIR)
        if count == 0:
            return "ERR upload: reassembly produced no files"

        # ── Verify assembled file sizes (optional, but helpful) ──
        for base, expected_size in total_sizes.items():
            assembled_path = os.path.join(DOWNLOAD_DIR, base)
            if os.path.isfile(assembled_path):
                actual_size = os.path.getsize(assembled_path)
                if actual_size == expected_size:
                    if log_func:
                        log_func(f"✅ Assembled '{base}' size OK: {actual_size} bytes")
                else:
                    if log_func:
                        log_func(f"⚠️ Size mismatch for '{base}': expected {expected_size}, got {actual_size}")
            else:
                if log_func:
                    log_func(f"❌ Missing assembled file: {base}")

        # ===== NO FILE RENAMING – keep original names =====
        # (The renaming block that produced 1.ext, 2.ext has been removed.)

        # ── Refresh file registry (this also sends "Files:" report) ──
        refresh_file_registry()

        if log_func:
            log_func(f"File registry after refresh: {_file_registry}")

        # ── Auto‑select newly assembled file(s) based on original names ──
        new_ids = []
        for fid, fname in _file_registry.items():
            if fname in groups:   # original base name matches
                new_ids.append(fid)
        if new_ids:
            _upload_file_paths.clear()
            sorted_ids = sorted(new_ids)
            _upload_file_paths.extend([_file_registry[fid] for fid in sorted_ids])
            add_autonomous_report("selectfiles",
                                  f"selectfiles({','.join(str(i) for i in sorted_ids)})")
            if log_func:
                log_func(f"Auto‑selected file IDs: {sorted_ids}")
        else:
            # Fallback: select the first file in the registry
            if _file_registry:
                first_id = min(_file_registry.keys())
                _upload_file_paths.clear()
                _upload_file_paths.append(_file_registry[first_id])
                add_autonomous_report("selectfiles", f"selectfiles({first_id})")
                if log_func:
                    log_func("No newly assembled files matched – auto-selected first file.")

        return f"OK upload({count} files) (ready – use 'uploadtoyoutube')"
    except Exception as e:
        if log_func:
            log_func(f"Upload error: {e}")
        return f"ERR upload: {e}"
    finally:
        shutil.rmtree(flat_temp, ignore_errors=True)
