#!/usr/bin/env python3
# ==============================================================================
# browser_agent.py – Version 1.3.0 (Enhanced tabs, files, profile, stealth & speed)
# ==============================================================================

import os, time, subprocess, hashlib, sys, base64, shutil, tarfile, io, glob, re, json, math, random, threading, traceback
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set, Tuple

from pyvirtualdisplay import Display
from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.chrome.options import Options
from PIL import Image, ImageDraw

from crypto_utils import encrypt_string, decode_string
from comments import get_all_comments, issue_comment
from uploader import reassemble as upload_reassemble

try:
    from google import genai
    from google.genai.types import Tool, GenerateContentConfig
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False


# ═══════════════ BROWSER SETUP (Improved Stealth) ═══════════════
def create_browser(profile_dir: str, download_dir: str) -> tuple:
    os.makedirs(download_dir, exist_ok=True)
    display = Display(visible=False, size=(1920, 1080))
    display.start()

    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--profile-directory=Default")

    # Stronger stealth for Google
    opts.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "profile.password_manager_leak_detection": False,
        "credentials_enable_service": False,
    })
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    # Extra anti-detection
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.execute_script("Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});")

    return driver, display, 1920, 1080


# ═══════════════ GLOBALS ═══════════════
W, H = 1920, 1080
cursor_x, cursor_y = W // 2, H // 2
driver = None
REPO = None
ISSUE_NUMBER = None
KEY_SECRET = None
DOWNLOAD_DIR = None
PROFILE_DIR = None
ENCRYPTION_KEY = None


# ═══════════════ MOUSE & KEYBOARD ═══════════════
def move_cursor_absolute(x: int, y: int) -> None:
    global cursor_x, cursor_y
    x = max(0, min(W - 1, x))
    y = max(0, min(H - 1, y))
    action = ActionBuilder(driver)
    action.pointer_action.move_to_location(x, y)
    action.perform()
    cursor_x, cursor_y = x, y

def left_click(): ActionChains(driver).click().perform()
def double_click(): ActionChains(driver).double_click().perform()
def right_click(): ActionChains(driver).context_click().perform()

def _perform_human_click_at(x: int, y: int) -> None:
    move_cursor_absolute(x, y)
    time.sleep(0.1)
    for _ in range(random.randint(1, 3)):
        move_cursor_relative(random.randint(-3,3), random.randint(-3,3))
        time.sleep(random.uniform(0.015, 0.04))
    left_button_down()
    time.sleep(random.uniform(0.04, 0.09))
    move_cursor_relative(random.randint(-2,2), random.randint(-2,2))
    time.sleep(random.uniform(0.02, 0.05))
    left_button_up()

def human_click_at(x: int, y: int) -> str:
    _perform_human_click_at(x, y)
    return f"Human click at ({x},{y})"

def move_cursor_relative(dx: int, dy: int) -> None:
    global cursor_x, cursor_y
    new_x = max(0, min(W-1, cursor_x + dx))
    new_y = max(0, min(H-1, cursor_y + dy))
    action = ActionBuilder(driver)
    action.pointer_action.move_to_location(new_x, new_y)
    action.perform()
    cursor_x, cursor_y = new_x, new_y

def left_button_down():
    action = ActionBuilder(driver)
    action.pointer_action.click_and_hold()
    action.perform()

def left_button_up():
    action = ActionBuilder(driver)
    action.pointer_action.release()
    action.perform()

def scroll_by(amount: int):
    driver.execute_script(f"window.scrollBy(0, {amount});")


# ═══════════════ SCREENSHOT ═══════════════
counter = [0]
def ss(desc="screenshot", response_suffix=""):
    counter[0] += 1
    now = datetime.now().strftime("%H%M%S")
    safe = re.sub(r'[^a-zA-Z0-9 _\-.(),]', '', response_suffix)[:60]
    fname = f"screenshots/{counter[0]:03d}_{now}_{desc}_{safe}.png" if safe else f"screenshots/{counter[0]:03d}_{now}_{desc}.png"
    
    driver.save_screenshot(fname)
    try:
        img = Image.open(fname)
        draw = ImageDraw.Draw(img)
        x, y = cursor_x, cursor_y
        r = 12
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline='red', width=3)
        draw.line([(x-15,y),(x+15,y)], fill='red', width=3)
        draw.line([(x,y-15),(x,y+15)], fill='red', width=3)
        img.save(fname)
    except: pass

    # Git push
    subprocess.run(["git", "add", fname], capture_output=True)
    try:
        subprocess.run(["git", "commit", "-m", f"Screenshot {fname}"], capture_output=True)
        subprocess.run(["git", "push"], capture_output=True)
    except: pass
    return fname


# ═══════════════ FILE & TAB HELPERS ═══════════════
def refresh_file_registry() -> str:
    try:
        files = sorted([f for f in os.listdir(DOWNLOAD_DIR) if not f.endswith(".crdownload")])
        lines = [f"{i+1}: {fname}" for i, fname in enumerate(files)]
        return "Files:\n" + "\n".join(lines) if lines else "No files in download folder."
    except:
        return "Error reading download directory."

def get_tabs_info() -> str:
    try:
        handles = driver.window_handles
        lines = []
        for i, h in enumerate(handles):
            driver.switch_to.window(h)
            title = (driver.title or "Untitled")[:70]
            url = driver.current_url[:100]
            lines.append(f"{i+1}: {title} | {url}")
        driver.switch_to.window(handles[0])
        return "Tabs:\n" + "\n".join(lines)
    except:
        return "Tabs: Error retrieving tabs."


# ═══════════════ COMMAND EXECUTION ═══════════════
def execute_one_command(cmd: str, arg=None):
    try:
        if cmd == "screenshot":
            ss("manual")
            return "OK screenshot"
        elif cmd == "move":
            x, y = arg
            move_cursor_absolute(x, y)
            return f"OK move({x},{y})"
        elif cmd in ("click_at", "shoot"):
            x, y = arg if isinstance(arg, tuple) else (cursor_x, cursor_y)
            move_cursor_absolute(x, y)
            left_click()
            return f"OK click({x},{y})"
        elif cmd == "humanclick_at":
            x, y = arg
            return human_click_at(x, y)
        elif cmd == "doubleclick_at":
            x, y = arg
            move_cursor_absolute(x, y)
            double_click()
            return f"OK doubleclick({x},{y})"
        elif cmd == "rightclick_at":
            x, y = arg
            move_cursor_absolute(x, y)
            right_click()
            return f"OK rightclick({x},{y})"
        elif cmd == "scroll":
            scroll_by(int(arg))
            return f"OK scroll({arg})"
        elif cmd == "text":
            ActionChains(driver).send_keys(str(arg)).perform()
            return f"OK text({str(arg)[:30]})"
        elif cmd == "navigate":
            driver.get(str(arg))
            time.sleep(4)
            return f"OK navigate({driver.current_url})"
        elif cmd == "tabs":
            return get_tabs_info()
        elif cmd == "dir":
            return refresh_file_registry()
        elif cmd == "refresh":
            driver.refresh()
            time.sleep(3)
            return "OK refresh"
        elif cmd == "wait":
            time.sleep(float(arg)/1000)
            return f"OK wait({arg}ms)"
        # Add more commands as needed

        return "OK"
    except Exception as e:
        return f"ERR {str(e)}"


# ═══════════════ PROFILE MANAGEMENT ═══════════════
def save_profile():
    try:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(PROFILE_DIR, arcname="chrome_profile")
        encrypted = Fernet(ENCRYPTION_KEY).encrypt(buf.getvalue())
        with open("profile_cache.tar.enc", "wb") as f:
            f.write(encrypted)
        print("Profile saved successfully.")
    except Exception as e:
        print(f"Profile save failed: {e}")


print("=== Browser Agent 1.3.0 started ===")
