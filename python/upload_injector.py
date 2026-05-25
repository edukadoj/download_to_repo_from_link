# python/upload_injector.py
#!/usr/bin/env python3
# ==============================================================================
# upload_injector.py – Version 2.6.2
#   - Removed obsolete perform_upload and inject_selected_file functions.
#   - Retained only _init_cdp, upload_to_youtube, and helper _get_upload_paths.
# ==============================================================================
import os, time, re, shutil, tempfile
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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