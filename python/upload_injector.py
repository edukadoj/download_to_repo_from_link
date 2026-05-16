#!/usr/bin/env python3
# ==============================================================================
# upload_injector.py – Version 2.6.1 (robust file‑list reporting)
# ==============================================================================
import os, time, re, shutil, tempfile
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from uploader import reassemble

# ---------- CDP (optional) ----------
HAS_CDP = False
_cdp_session = None

def _init_cdp(driver, log_func=None):
    global HAS_CDP, _cdp_session
    if HAS_CDP: return True
    try:
        from selenium.webdriver.common.devtools import devtools
        from selenium.webdriver.common.devtools import DevTools
        _cdp_session = DevTools(driver)
        _cdp_session.create_session()
        _cdp_session.send(devtools.page.set_intercept_file_chooser_dialog(enabled=True))
        def _on_chooser(event):
            try:
                paths = _get_upload_paths()
                if paths:
                    _cdp_session.send(devtools.page.handle_file_chooser(action="accept", files=[paths[0]]))
                    if log_func: log_func(f"✅ CDP accepted: {paths[0]}")
            except Exception as ex:
                if log_func: log_func(f"CDP error: {ex}")
        _cdp_session.on(devtools.page.FileChooserOpened, _on_chooser)
        HAS_CDP = True
        if log_func: log_func("CDP interception active.")
        return True
    except Exception as e:
        if log_func: log_func(f"CDP unavailable ({e}) – using send_keys fallback.")
        return False

# ---------- send_keys into YouTube's hidden input ----------
def upload_to_youtube(driver, file_path, log_func=None):
    """Inject a file into YouTube Studio's hidden <input name='Filedata'>."""
    try:
        file_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "Filedata"))
        )
    except Exception:
        try:
            file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file'][name='Filedata']")
        except Exception:
            if log_func: log_func("❌ YouTube file input not found.")
            return False

    driver.execute_script("""
        arguments[0].removeAttribute('aria-hidden');
        arguments[0].removeAttribute('hidden');
        arguments[0].style.display = 'block';
        arguments[0].style.visibility = 'visible';
        arguments[0].style.opacity = '1';
        arguments[0].style.position = 'static';
        arguments[0].style.height = 'auto';
        arguments[0].style.width = 'auto';
        arguments[0].disabled = false;
    """, file_input)
    time.sleep(0.5)
    try:
        file_input.send_keys(file_path)
        if log_func: log_func(f"✅ YouTube upload started: {file_path}")
        return True
    except Exception as e:
        if log_func: log_func(f"❌ YouTube send_keys failed: {e}")
        return False

_upload_paths_callable = None

def _get_upload_paths():
    if _upload_paths_callable: return _upload_paths_callable()
    return []

def inject_selected_file(driver, get_upload_paths_fn, log_func=None):
    global _upload_paths_callable
    _upload_paths_callable = get_upload_paths_fn
    paths = get_upload_paths_fn()
    if not paths:
        if log_func: log_func("⚠️ No file selected.")
        return False
    return upload_to_youtube(driver, paths[0], log_func)

# ---------- Main upload logic ----------
def perform_upload(DOWNLOAD_DIR, LOG_FILENAME,
                   refresh_file_registry, add_autonomous_report,
                   _file_registry, _upload_file_paths,
                   git_push_with_retry, inject_file_fn, log_func=None):
    if not os.path.isdir("chunks"):
        return "ERR upload: chunks directory not found"
    chunk_files = [f for f in os.listdir("chunks") if ".part" in f]
    if not chunk_files:
        return "ERR upload: no .part files in chunks/"
    groups = {}
    for f in chunk_files:
        base = re.sub(r'\.part\d+$', '', f)
        groups.setdefault(base, []).append(f)
    temp_dir = tempfile.mkdtemp(prefix="chunks_")
    try:
        for base, parts in groups.items():
            dest_dir = os.path.join(temp_dir, base)
            os.makedirs(dest_dir, exist_ok=True)
            for p in parts:
                src = os.path.join("chunks", p)
                if os.path.isfile(src): shutil.copy2(src, os.path.join(dest_dir, p))
        count = reassemble(temp_dir, DOWNLOAD_DIR)
        if count == 0:
            return "ERR upload: reassembly produced no files"
        result = f"OK upload({count} files)"
        refresh_file_registry()
        # ✅ Always report the file list to the app
        if _file_registry:
            lines = [f"{fid}: {fname}" for fid, fname in sorted(_file_registry.items())]
            add_autonomous_report("files", "Files: " + " | ".join(lines))
        # Auto‑select newly assembled files
        new_ids = []
        for fid, fname in _file_registry.items():
            if fname in groups.keys(): new_ids.append(fid)
        if new_ids:
            _upload_file_paths.clear()
            _upload_file_paths.extend([_file_registry[fid] for fid in sorted(new_ids)])
            add_autonomous_report("selectfiles",
                                  f"selectfiles({','.join(str(i) for i in sorted(new_ids))})")
        if callable(inject_file_fn):
            if inject_file_fn(): result += " (injected)"
            else: result += " (ready; use 'uploadtoyoutube')"
        return result
    except Exception as e:
        if log_func: log_func(f"Upload error: {e}")
        return f"ERR upload: {e}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
