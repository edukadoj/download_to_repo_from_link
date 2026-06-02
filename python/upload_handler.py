#!/usr/bin/env python3
# ==============================================================================
# upload_handler.py – Version 2.2.6
#   - Progress reports now sent for every downloaded chunk.
# ==============================================================================
import os, re, shutil, tempfile, time, json, threading
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
                   git_push_with_retry,   # kept for backward compatibility (unused)
                   inject_file_fn,
                   log_func=None,
                   rw=None):              # RepoWrapper instance
    """
    Pull latest chunks (raw .part files) from the remote 'chunks' directory
    via RepoWrapper, reassemble locally, and auto‑select.
    """
    if not rw:
        return "ERR upload: RepoWrapper not available"

    # ── List remote chunks directory ──
    if log_func:
        log_func("📋 Requesting listing of remote 'chunks' directory...")
    event = threading.Event()
    listing_result = []
    def callback(lst):
        listing_result.extend(lst)
        event.set()
    rw.list_directory("chunks", callback)
    if not event.wait(timeout=300):
        return "ERR upload: timed out listing chunks dir"
    all_entries = listing_result
    if log_func:
        log_func(f"📋 Received listing: {len(all_entries)} total entries in chunks/")

    # Filter to .part files
    part_entries = [(p, sha) for p, sha in all_entries if ".part" in os.path.basename(p)]
    if not part_entries:
        return "ERR upload: no .part files in chunks/"
    if log_func:
        log_func(f"📋 Found {len(part_entries)} .part files")

    # Group by base name (everything before .part####)
    groups = {}
    for path, sha in part_entries:
        fname = os.path.basename(path)
        m = re.match(r"(.+)\.part\d+$", fname)
        if m:
            base = m.group(1)
            groups.setdefault(base, []).append((path, sha))

    if not groups:
        return "ERR upload: could not parse part filenames"
    if log_func:
        log_func(f"📋 Grouped into {len(groups)} file(s)")

    flat_temp = tempfile.mkdtemp(prefix="chunks_flat_")
    try:
        # Download each .part file and place directly in flat_temp
        for base, entries in groups.items():
            total_chunks = len(entries)
            for idx, (rel_path, sha) in enumerate(entries, start=1):
                event.clear()
                data_holder = []
                def download_callback(data):
                    data_holder.append(data)
                    event.set()
                rw.download_file(rel_path, download_callback)
                if not event.wait(timeout=120):
                    shutil.rmtree(flat_temp, ignore_errors=True)
                    return f"ERR upload: timeout downloading {rel_path}"
                if not data_holder:
                    shutil.rmtree(flat_temp, ignore_errors=True)
                    return f"ERR upload: empty download {rel_path}"
                # Save raw .part directly
                dest_path = os.path.join(flat_temp, os.path.basename(rel_path))
                with open(dest_path, "wb") as f:
                    f.write(data_holder[0])
                if log_func:
                    log_func(f"  ✅ Downloaded {rel_path} ({len(data_holder[0])} bytes)")

                # ── Send autonomous progress report for every chunk ──
                add_autonomous_report("upload-progress",
                                      f"Chunk {idx}/{total_chunks} of {base} downloaded")

        # ── Reassemble using flat method ──
        count = reassemble_flat(flat_temp, DOWNLOAD_DIR)
        if count == 0:
            return "ERR upload: reassembly produced no files"

        # Auto‑select newly assembled files BEFORE refreshing the registry
        new_ids = []
        for fid, fname in _file_registry.items():
            if fname in groups:
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
            if _file_registry:
                first_id = min(_file_registry.keys())
                _upload_file_paths.clear()
                _upload_file_paths.append(_file_registry[first_id])
                add_autonomous_report("selectfiles", f"selectfiles({first_id})")
                if log_func:
                    log_func("No newly assembled files matched – auto-selected first file.")

        # Now refresh the file registry (sends Files: report)
        refresh_file_registry()

        if log_func:
            log_func(f"File registry after refresh: {_file_registry}")

        return f"OK upload({count} files) (ready – use 'uploadtoyoutube')"
    except Exception as e:
        if log_func:
            log_func(f"Upload error: {e}")
        return f"ERR upload: {e}"
    finally:
        shutil.rmtree(flat_temp, ignore_errors=True)