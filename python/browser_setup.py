#!/usr/bin/env python3
# ==============================================================================
# browser_setup.py – Version 1.0.0 (stealth, CDP, and profile restore)
# ==============================================================================
import base64, io, os, shutil, tarfile, time, traceback
from hashlib import sha256
from pyvirtualdisplay import Display
from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def create_browser(profile_dir, download_dir, encryption_key, start_url):
    """Return (driver, display, W, H) with stealth, CDP, and page load timeout."""
    os.makedirs(download_dir, exist_ok=True)
    display = Display(visible=False, size=(1920, 1080))
    display.start()

    # ── restore profile if available ──
    if not os.path.exists("profile_cache.tar.enc"):
        try:
            import subprocess
            subprocess.run(["git", "pull", "--rebase", "--autostash"], check=True, capture_output=True)
        except Exception:
            pass

    if os.path.exists("profile_cache.tar.enc"):
        try:
            key = sha256(encryption_key.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key))
            with open("profile_cache.tar.enc", "rb") as fp:
                encrypted = fp.read()
            decrypted = f.decrypt(encrypted)
            shutil.rmtree(profile_dir, ignore_errors=True)
            buf = io.BytesIO(decrypted)
            with tarfile.open(fileobj=buf, mode='r:gz') as tar:
                tar.extractall('/tmp')
        except Exception:
            pass

    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--profile-directory=Default")
    opts.add_experimental_option("prefs", {"download.default_directory": download_dir})
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)

    # ── stealth ──
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.execute_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]})")
    driver.execute_script("Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']})")
    driver.execute_script("window.chrome = { runtime: {} };")
    driver.execute_script("Object.defineProperty(navigator, 'permissions', {get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })})")
    driver.execute_script("Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4})")

    # ── CDP file chooser ──
    try:
        driver.execute_cdp_cmd("Page.setInterceptFileChooserDialog", {"enabled": True})

        def _on_file_chooser_opened(event):
            # This function will be defined later by the main module; we just attach the listener here.
            # We'll pass the driver back and let the main module assign the listener after import.
            pass

        driver.add_cdp_listener("Page.fileChooserOpened", _on_file_chooser_opened)
    except Exception:
        print("CDP not available")

    return driver, display, 1920, 1080
