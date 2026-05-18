#!/usr/bin/env python3
# ==============================================================================
# command_mouse_keyboard.py – Version 39.31.0
# ==============================================================================
# Agent main script that runs inside GitHub Actions.
# Implements the communication logic with four independent loops:
#
# 1. Comment Fetcher Loop  – reads the App Commands comment, detects new
#    content, routes commands to Report Queue (if already executed) or
#    Execution Queue (if new).  Also handles acknowledgment commands.
# 2. Execution Loop        – takes commands from the Execution Queue,
#    executes them (with a 15‑second timeout), saves the result to
#    Report History (disk) and enqueues it into the Report Queue.
# 3. Comment Sender Loop   – periodically publishes the Report Queue to the
#    Remote Agent Responses comment.  Culls timed‑out reports, removes
#    command reports after successful publish, keeps autonomous reports
#    until acknowledged or timed out.
# 4. Screenshot Loop       – continuously captures the virtual display,
#    draws a red crosshair, adds a human‑readable timestamp watermark
#    (50 % opacity), and pushes the image to the repository.  Push has
#    a 10‑second timeout; the loop waits for success or timeout before
#    taking the next screenshot.  After every attempt the log file is
#    pushed immediately to help diagnose problems.
#
# All loops are crash‑proof – unhandled exceptions are logged and the loop
# continues.  The main process also logs any fatal exception and forces a
# log push before terminating.
# ==============================================================================

import os, sys, time, subprocess, hashlib, base64, json, random, threading, traceback, io, shutil, tarfile, glob, re, tempfile, signal
from datetime import datetime
from pyvirtualdisplay import Display
from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException
from selenium.webdriver.chrome.options import Options
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import queue as queue_module

from crypto_utils import encrypt_string, decode_string
from comments import find_marker_comment
from uploader import reassemble_flat
from command_handlers import execute_one_command
from repo_wrapper import RepoWrapper

from agent_state import (
    driver as state_driver, W as state_W, H as state_H,
    cursor_x as state_cx, cursor_y as state_cy,
    HAS_GEMINI, HAS_PYPERCLIP, pyperclip, allowed_secrets,
    move_cursor_absolute, move_cursor_relative,
    left_click, left_button_down, left_button_up,
    right_button_down, right_button_up,
    middle_button_down, middle_button_up,
    double_click, right_click, middle_click,
    scroll_by, drag_from_to,
    press_key, press_combo, type_secret,
    parse_single_command,
    refresh_file_registry, get_upload_paths,
    refresh_known_handles,
    url_monitor_worker,
    add_autonomous_report, cull_expired_autonomous_reports,
    pending_autonomous_reports,
    _file_registry, _upload_file_paths,
    _known_handles, _last_known_url, _url_monitor_stop,
    autonomous_counter,
    KEY_MAP, human_click, human_click_at,
    _perform_human_click_at, _try_gemini_click,
    ensure_active_tab, ACTIVE_TAB_INDEX
)

# ---------- Signal handlers ----------
def _signal_handler(signum, frame):
    safe_log("Received signal, performing emergency save...")
    push_logs()
    save_profile()
    sys.exit(0)

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ---------- LOGGING – synchronous, flushed ----------
LOG_FILENAME = "logs/command_mouse_keyboard.log"
os.makedirs("logs", exist_ok=True)

_log_lock = threading.Lock()

def safe_log(msg: str) -> None:
    """Write a timestamped line to log and stdout, then fsync."""
    now = datetime.now().strftime("%H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    try:
        with _log_lock:
            with open(LOG_FILENAME, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        print("[LOG ERROR] Could not write to log file", flush=True)

def echo(msg: str) -> None:
    print(msg, flush=True)
    try:
        with _log_lock:
            with open(LOG_FILENAME, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        pass

echo(f"{'='*60}\n  Remote Control v39.31.0 started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'='*60}")
os.makedirs("screenshots", exist_ok=True)

COMM_INTERVAL = 5.0
slow_mode = 1
last_command_time = time.time()

# ---------- Synchronous wrapper around RepoWrapper ----------
class SyncRepo:
    def __init__(self, repo_wrapper):
        self._rw = repo_wrapper

    def _call_and_wait(self, method, *args, timeout=30):
        event = threading.Event()
        result = [None]
        def callback(value):
            result[0] = value
            event.set()
        method(*args, callback)
        if not event.wait(timeout):
            raise TimeoutError(f"RepoWrapper call timed out after {timeout}s")
        return result[0]

    def edit_comment(self, comment_id, new_body):
        self._rw.edit_comment(comment_id, new_body)

    def get_comment_body(self, comment_id):
        return self._call_and_wait(self._rw.get_comment_body, comment_id)

    def get_all_comments(self):
        return self._call_and_wait(self._rw.get_all_comments)

    def create_comment(self, body):
        return self._call_and_wait(self._rw.post_comment_and_get_id, body)

    def delete_comment(self, comment_id):
        self._rw.delete_comment(comment_id)

    def push_log_file(self):
        self._rw.push_log_file()

    def push_screenshots(self, paths):
        self._rw.push_screenshots(paths)

    def push_screenshots_now(self, paths, timeout=10):
        return self._rw.push_screenshots_now(paths, timeout=timeout)

    def comment_exists(self, comment_id):
        return self._call_and_wait(self._rw.comment_exists, comment_id)

    def report_memory(self):
        self._rw.report_memory()

# ---------- Repo and SyncRepo setup ----------
ISSUE_NUMBER = os.environ.get("ISSUE_NUMBER","4").strip()
START_URL = os.environ.get("START_URL") or "https://studio.youtube.com"
REPO = os.environ['GITHUB_REPOSITORY']
KEY_SECRET = os.environ["KEY"]

repo_wrapper = RepoWrapper(REPO, int(ISSUE_NUMBER), LOG_FILENAME)
repo_wrapper.error_log = safe_log
sync_repo = SyncRepo(repo_wrapper)

def _autonomous_callback(report_type, text):
    add_autonomous_report(report_type, text)
repo_wrapper.report_callback = _autonomous_callback

# ---------- Profile cache ----------
PROFILE_DIR = "/tmp/chrome_profile"
CACHE_DIR = ".profile_cache"
CHUNK_SIZE_MB = 20
ENCRYPTION_KEY = None
try:
    KEY = os.environ["KEY"]
    ENCRYPTION_KEY = base64.urlsafe_b64encode(hashlib.sha256(KEY.encode()).digest())
except Exception as e:
    safe_log(f"PROFILE KEY ERROR: {e}")
    raise

os.makedirs(CACHE_DIR, exist_ok=True)

def load_profile():
    if not glob.glob(os.path.join(CACHE_DIR, "*.part*")):
        safe_log("⚠️ No profile cache chunks found – starting with fresh browser profile.")
        return False
    safe_log("♻️  Reassembling profile cache chunks …")
    tmp_reassemble = tempfile.mkdtemp(prefix="profile_reassemble_")
    try:
        count = reassemble_flat(CACHE_DIR, tmp_reassemble)
        if count == 0:
            safe_log("⚠️ Reassembly produced no file – starting fresh.")
            return False
        files = [f for f in os.listdir(tmp_reassemble) if os.path.isfile(os.path.join(tmp_reassemble, f))]
        if not files:
            safe_log("⚠️ No reassembled file found.")
            return False
        reassembled_path = os.path.join(tmp_reassemble, files[0])
        safe_log(f"   Reassembled: {files[0]} ({os.path.getsize(reassembled_path)} bytes)")
        with open(reassembled_path, "rb") as f:
            encrypted = f.read()
        decrypted = Fernet(ENCRYPTION_KEY).decrypt(encrypted)
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz').extractall('/tmp')
        safe_log("Profile cache loaded successfully.")
        for f in glob.glob(os.path.join(CACHE_DIR, "*.part*")):
            os.remove(f)
        return True
    except Exception as e:
        safe_log(f"ERROR loading profile cache: {type(e).__name__}: {e}")
        return False
    finally:
        shutil.rmtree(tmp_reassemble, ignore_errors=True)

def save_profile():
    try:
        if not os.path.isdir(PROFILE_DIR):
            return (False, f"Profile directory not found: {PROFILE_DIR}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(PROFILE_DIR, arcname="chrome_profile")
        encrypted = Fernet(ENCRYPTION_KEY).encrypt(buf.getvalue())
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="profile_", suffix=".dat")
        os.close(tmp_fd)
        with open(tmp_path, "wb") as f:
            f.write(encrypted)
        for old in glob.glob(os.path.join(CACHE_DIR, "*.part*")):
            os.remove(old)
        chunker_script = "python/chunker.py"
        if not os.path.exists(chunker_script):
            os.remove(tmp_path)
            return (False, f"Chunker script not found: {chunker_script}")
        cmd = ["python3", chunker_script, "--file", tmp_path, "--output-dir", CACHE_DIR, "--chunk-size", str(CHUNK_SIZE_MB)]
        safe_log(f"Running chunker: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(tmp_path)
        if result.returncode != 0:
            return (False, f"chunker.py failed: {result.stderr.strip() or result.stdout.strip()}")
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], capture_output=True)
        sync_repo.push_log_file()
        return (True, "Profile cache saved successfully.")
    except Exception as e:
        return (False, f"save_profile exception: {e}")

# ---------- Browser setup ----------
DOWNLOAD_DIR = "/home/runner/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Thread‑safety lock for all driver operations
driver_lock = threading.Lock()

def create_driver():
    opts = Options()
    opts.add_argument("--no-sandbox"); opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36")
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(30)
    drv.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    drv.execute_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]})")
    drv.execute_script("Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']})")
    drv.execute_script("window.chrome = { runtime: {} };")
    drv.execute_script("Object.defineProperty(navigator, 'permissions', {get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })})")
    drv.execute_script("Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4})")
    return drv

display = Display(visible=False, size=(1920,1080))
display.start()
safe_log("Virtual display started.")

load_profile()
driver = None
try:
    driver = create_driver()
    safe_log("Stealth JS injected.")
    try:
        from upload_injector import _init_cdp
        if _init_cdp(driver, safe_log):
            safe_log("CDP interception active.")
    except Exception as e_cdp:
        safe_log(f"CDP not available ({e_cdp}) – using send_keys fallback.")
    safe_log("Browser launched.")
except Exception as e:
    safe_log(f"BROWSER ERROR: {e}\n{traceback.format_exc()}")
    push_logs()
    raise

agent_state = sys.modules.get("agent_state")
if agent_state:
    agent_state.driver = driver
    agent_state.W = 1920; agent_state.H = 1080
    agent_state.cursor_x = 960; agent_state.cursor_y = 540
    agent_state.DOWNLOAD_DIR = DOWNLOAD_DIR
    agent_state.allowed_secrets = allowed_secrets
    agent_state.HAS_GEMINI = HAS_GEMINI
    agent_state.HAS_PYPERCLIP = HAS_PYPERCLIP
    agent_state.pyperclip = pyperclip
    agent_state.log = safe_log

# ---------- Background log pusher ----------
def push_logs():
    sync_repo.push_log_file()

def log_pusher():
    while not _log_pusher_stop.is_set():
        _log_pusher_stop.wait(30)
        if not _log_pusher_stop.is_set():
            push_logs()

_log_pusher_stop = threading.Event()
threading.Thread(target=log_pusher, daemon=True).start()

_last_memory_report = 0
def heartbeat_worker():
    global _last_memory_report
    while not _heartbeat_stop.is_set():
        _heartbeat_stop.wait(30)
        if not _heartbeat_stop.is_set():
            safe_log("Agent alive")
            now = time.time()
            if now - _last_memory_report >= 120:
                sync_repo.report_memory()
                _last_memory_report = now
            push_logs()

_heartbeat_stop = threading.Event()
threading.Thread(target=heartbeat_worker, daemon=True).start()

# ---------- Screenshot logic ----------
counter = [0]
# Try to load a reasonable font for the watermark
watermark_font = None
font_candidates = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
for path in font_candidates:
    if os.path.exists(path):
        try:
            watermark_font = ImageFont.truetype(path, 32)
            safe_log(f"Watermark font loaded: {path}")
            break
        except Exception:
            pass
if watermark_font is None:
    try:
        watermark_font = ImageFont.load_default()
        safe_log("Using PIL default font (watermark may be tiny).")
    except Exception:
        safe_log("No font available, watermark disabled.")

def ss(desc="screenshot", push=True):
    """
    Take a screenshot, overlay cursor and timestamp, and push to repo.
    Returns the filename if successful, None otherwise.
    """
    counter[0] += 1
    now = datetime.now()
    ms = now.microsecond // 1000
    timestamp_str = now.strftime("%H:%M:%S") + f".{ms:03d}"
    filename = f"screenshots/{counter[0]:04d}_{now.strftime('%H%M%S')}_{ms:03d}_{desc}.png"
    safe_log(f"Taking screenshot: {filename}")
    try:
        with driver_lock:
            driver.save_screenshot(filename)
        if not os.path.exists(filename):
            safe_log(f"Screenshot file not created: {filename}")
            return None
        img = Image.open(filename).convert("RGBA")
        draw = ImageDraw.Draw(img)
        x, y = agent_state.cursor_x if agent_state else 960, agent_state.cursor_y if agent_state else 540
        r = 12
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline='red', width=3)
        draw.line([(x-15,y),(x+15,y)], fill='red', width=3)
        draw.line([(x,y-15),(x,y+15)], fill='red', width=3)
        # watermark at 50% opacity
        if watermark_font:
            overlay = Image.new('RGBA', img.size, (0,0,0,0))
            overlay_draw = ImageDraw.Draw(overlay)
            text_bbox = overlay_draw.textbbox((0,0), timestamp_str, font=watermark_font)
            text_pos = (img.width - text_bbox[2] - 20, img.height - text_bbox[3] - 20)
            overlay_draw.text(text_pos, timestamp_str, font=watermark_font, fill=(255,255,255,128))
            img = Image.alpha_composite(img, overlay)
        img.save(filename)
    except BaseException as e:
        safe_log(f"Screenshot image processing error: {e}")
        return None
    if not push:
        return filename
    safe_log("Enqueuing screenshot for push...")
    sync_repo.push_screenshots([filename])
    safe_log(f"Enqueued {filename} + log")
    return filename

# ---------- Screenshot worker ----------
_screenshot_stop = threading.Event()
_screenshot_thread = None

def screenshot_worker():
    """
    Continuously take screenshots, push them with a 10‑second timeout,
    and push the log file after every attempt.  Never dies.
    """
    while not _screenshot_stop.is_set():
        try:
            _screenshot_stop.wait(2)
            while not _screenshot_stop.is_set():
                start = time.time()
                filename = ss("auto", push=False)
                if filename is None:
                    safe_log("Screenshot capture failed – will retry after interval.")
                    push_logs()
                    _screenshot_stop.wait(max(0, COMM_INTERVAL * slow_mode - (time.time() - start)))
                    continue

                safe_log(f"Pushing screenshot {filename} with 10s timeout...")
                push_ok = False
                try:
                    push_ok = sync_repo.push_screenshots_now([filename], timeout=10)
                except Exception as e:
                    safe_log(f"Screenshot push exception: {e}")
                    push_ok = False

                if push_ok:
                    safe_log(f"Screenshot {filename} pushed successfully.")
                else:
                    safe_log(f"Screenshot push failed or timed out for {filename}.")

                push_logs()  # always push logs after a screenshot attempt

                elapsed = time.time() - start
                _screenshot_stop.wait(max(0, COMM_INTERVAL * slow_mode - elapsed))
        except BaseException as outer_e:
            safe_log(f"Screenshot worker crashed: {outer_e}\n{traceback.format_exc()}")
            push_logs()
            _screenshot_stop.wait(5)

def start_screenshot_worker():
    global _screenshot_thread
    if _screenshot_thread and _screenshot_thread.is_alive():
        return
    _screenshot_stop.clear()
    _screenshot_thread = threading.Thread(target=screenshot_worker, daemon=True)
    _screenshot_thread.start()
    safe_log("Screenshot worker started.")

# ---------- Browser recovery ----------
_calibration_in_progress = False

def restart_browser():
    global driver
    safe_log("Attempting browser restart...")
    if _calibration_in_progress:
        safe_log("Calibration in progress – cancelling instead of restarting browser.")
        add_autonomous_report("calibration_error", "Browser crashed during calibration – calibration cancelled.")
        return False
    try:
        with driver_lock:
            driver.quit()
    except Exception:
        pass
    time.sleep(2)
    for attempt in range(5):
        try:
            driver = create_driver()
            if agent_state:
                agent_state.driver = driver
                agent_state.cursor_x = 960
                agent_state.cursor_y = 540
            driver.get(START_URL)
            safe_log("Browser restarted and navigated to START_URL.")
            add_autonomous_report("browser_restarted", "Browser was restarted after a crash.")
            start_screenshot_worker()
            return True
        except Exception as e:
            safe_log(f"Browser restart attempt {attempt+1} failed: {e}")
            time.sleep(5)
    return False

# ---------- Report History ----------
HISTORY_FILE = "logs/report_history.json"
_report_history_lock = threading.Lock()
report_history = {}

def load_report_history():
    global report_history
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                report_history = json.load(f)
            safe_log(f"Loaded report history with {len(report_history)} entries.")
        except Exception as e:
            safe_log(f"Failed to load report history: {e}")
            report_history = {}

def save_report_history():
    try:
        with _report_history_lock:
            with open(HISTORY_FILE, "w") as f:
                json.dump(report_history, f)
    except Exception as e:
        safe_log(f"Failed to save report history: {e}")

load_report_history()

# ---------- Report Queue (duplicate‑safe) ----------
_report_queue_lock = threading.Lock()
report_queue = {}   # key: command ID or AUT ID -> (timestamp, seq_num, result_string)

def add_to_report_queue(key, timestamp, seq_num, result):
    with _report_queue_lock:
        if key not in report_queue:
            report_queue[key] = (timestamp, seq_num, result)

def remove_from_report_queue(key):
    with _report_queue_lock:
        report_queue.pop(key, None)

def get_report_queue_snapshot():
    with _report_queue_lock:
        return dict(report_queue)

def cull_timed_out_reports(timeout_seconds=60):
    with _report_queue_lock:
        now = time.time()
        to_remove = []
        for key, (ts, seq, res) in report_queue.items():
            if now - (ts / 1_000_000) > timeout_seconds:
                to_remove.append(key)
        for key in to_remove:
            del report_queue[key]
        return len(to_remove)

# ---------- Execution Queue ----------
execution_queue = queue_module.Queue()

# ---------- Thread stop events ----------
_fetcher_stop = threading.Event()
_executor_stop = threading.Event()
_sender_stop = threading.Event()

# ---------- Fetcher Loop ----------
def fetcher_loop():
    global app_cmd_id, last_app_body_hash
    last_app_body_hash = None
    safe_log("Fetcher loop started.")
    while not _fetcher_stop.is_set():
        try:
            if app_cmd_id:
                try:
                    app_body = sync_repo.get_comment_body(app_cmd_id)
                except Exception as e:
                    safe_log(f"Fetcher error reading app cmd body: {e}")
                    _fetcher_stop.wait(COMM_INTERVAL * slow_mode)
                    continue
                if not app_body:
                    _fetcher_stop.wait(COMM_INTERVAL * slow_mode)
                    continue
                body_hash = hashlib.md5(app_body.encode()).hexdigest()
                if body_hash == last_app_body_hash:
                    _fetcher_stop.wait(1)
                    continue
                last_app_body_hash = body_hash

                safe_log("Fetcher detected new App Commands comment.")
                lines = app_body.strip().splitlines()
                capture = False
                for line in lines:
                    if re.match(r'^\[(\d+)\]: app commands:', line):
                        capture = True
                        continue
                    if capture and re.match(r'^\[', line):
                        capture = False
                    if not capture:
                        continue
                    parts = line.split(';', 1)
                    if len(parts) != 2:
                        continue
                    cid = parts[0].strip()
                    ctext = parts[1].strip()
                    if not ctext:
                        continue

                    # Ack command?
                    if ctext.startswith("ack:"):
                        aut_id = ctext.split(":",1)[1].strip()
                        remove_from_report_queue(aut_id)
                        safe_log(f"Acknowledged autonomous report {aut_id}, removed from queue.")
                        continue

                    # Normal command
                    with _report_history_lock:
                        existing = report_history.get(cid)
                    if existing:
                        ts, seq, res = existing
                        add_to_report_queue(cid, ts, seq, res)
                        safe_log(f"Re‑enqueued existing report for {cid}")
                    else:
                        execution_queue.put((cid, ctext))
                        safe_log(f"Enqueued for execution: {cid}")
            else:
                safe_log("App comment ID missing – rediscovering...")
                try:
                    all_comments = sync_repo.get_all_comments()
                    app_c = find_marker_comment(all_comments, "## App Commands")
                    if app_c:
                        app_cmd_id = app_c["id"]
                        safe_log(f"Re‑found app cmd: {app_cmd_id}")
                    else:
                        safe_log("Still no app comment found.")
                except Exception as e:
                    safe_log(f"Error rediscovering app comment: {e}")
                _fetcher_stop.wait(COMM_INTERVAL * slow_mode)
                continue

            _fetcher_stop.wait(0.1)

        except Exception as e:
            safe_log(f"Fetcher loop error: {traceback.format_exc()}")
            push_logs()
            _fetcher_stop.wait(5)

# ---------- Execution Loop ----------
def executor_loop():
    safe_log("Execution loop started.")
    executor_pool = ThreadPoolExecutor(max_workers=1)
    while not _executor_stop.is_set():
        try:
            cid, ctext = execution_queue.get(timeout=1)
        except queue_module.Empty:
            continue

        safe_log(f"Execution loop: processing {cid}: {ctext}")

        def run_command():
            cmd_type, arg = parse_single_command(ctext)
            return execute_one_command(
                cmd_type, arg,
                driver=driver, cursor_x=agent_state.cursor_x, cursor_y=agent_state.cursor_y,
                W=agent_state.W, H=agent_state.H, DOWNLOAD_DIR=DOWNLOAD_DIR, LOG_FILENAME=LOG_FILENAME,
                KEY_SECRET=KEY_SECRET, REPO=REPO, ISSUE_NUMBER=ISSUE_NUMBER,
                HAS_GEMINI=HAS_GEMINI, HAS_PYPERCLIP=HAS_PYPERCLIP,
                allowed_secrets=allowed_secrets, ENCRYPTION_KEY=ENCRYPTION_KEY,
                human_click_callable=human_click, human_click_at_callable=human_click_at,
                _try_gemini_click=_try_gemini_click,
                move_cursor_absolute=move_cursor_absolute,
                move_cursor_relative=move_cursor_relative,
                left_click=left_click, left_button_down=left_button_down,
                left_button_up=left_button_up, right_button_down=right_button_down,
                right_button_up=right_button_up, middle_button_down=middle_button_down,
                middle_button_up=middle_button_up, double_click=double_click,
                right_click=right_click, middle_click=middle_click,
                scroll_by=scroll_by, drag_from_to=drag_from_to,
                press_key=press_key, press_combo=press_combo,
                type_secret=type_secret, decode_string=decode_string,
                ss=ss, refresh_file_registry=refresh_file_registry,
                add_autonomous_report=add_autonomous_report,
                refresh_known_handles=refresh_known_handles,
                get_upload_paths=get_upload_paths, save_profile=save_profile,
                _file_registry=_file_registry, _upload_file_paths=_upload_file_paths,
                pyperclip=pyperclip if HAS_PYPERCLIP else None,
                upload_reassemble=None,
                HAS_PYPERCLIP_local=HAS_PYPERCLIP,
                encrypt_string=encrypt_string,
                get_all_comments=lambda: sync_repo.get_all_comments(),
                delete_comment=lambda cid: sync_repo.delete_comment(cid),
                issue_comment=lambda body: sync_repo.create_comment(body),
                smart_edit_comment=lambda cid, body: sync_repo.edit_comment(cid, body),
                git_push_with_retry=(lambda: sync_repo._rw._git_push_with_retry()),
                comm_interval=COMM_INTERVAL * slow_mode, inject_file=None
            )

        try:
            future = executor_pool.submit(run_command)
            result = future.result(timeout=15)
        except FutureTimeoutError:
            result = "ERR timeout: command exceeded 15 seconds"
            safe_log(f"Command {cid} timed out.")
        except Exception as ex:
            result = f"ERR unexpected: {ex}"
            safe_log(f"Command {cid} crashed: {ex}")

        ts = int(time.time() * 1_000_000)
        seq_num = 0
        if cid.startswith("APP-"):
            parts_cid = cid.split('-')
            if len(parts_cid) >= 2:
                try: seq_num = int(parts_cid[1])
                except: pass

        with _report_history_lock:
            report_history[cid] = (ts, seq_num, result)
        save_report_history()
        add_to_report_queue(cid, ts, seq_num, result)
        safe_log(f"Executed {cid} -> {result}")

        if ctext.strip().lower() == "exit":
            _executor_stop.set()
            break

    executor_pool.shutdown(wait=False)

# ---------- Sender Loop ----------
def sender_loop():
    safe_log("Sender loop started.")
    response_comment_id = None
    while not _sender_stop.is_set():
        try:
            all_comments = sync_repo.get_all_comments()
            resp_comment = find_marker_comment(all_comments, "## Remote Agent Responses")
            if resp_comment:
                response_comment_id = resp_comment["id"]
                safe_log(f"Sender using response comment: {response_comment_id}")
                break
            else:
                response_comment_id = sync_repo.create_comment("## Remote Agent Responses\n")
                add_autonomous_report("responsecommentid", f"responsecommentid:{response_comment_id}")
                safe_log(f"Created new response comment: {response_comment_id}")
                break
        except Exception as e:
            safe_log(f"Sender init error: {e}")
            _sender_stop.wait(5)

    while not _sender_stop.is_set():
        _sender_stop.wait(COMM_INTERVAL * slow_mode)
        try:
            removed = cull_timed_out_reports(60)
            if removed:
                safe_log(f"Sender culled {removed} timed‑out reports.")

            snapshot = get_report_queue_snapshot()
            if not snapshot:
                continue

            lines = ["## Remote Agent Responses"]
            for key, (ts, seq, res) in snapshot.items():
                if key.startswith("AUT-"):
                    lines.append(f"{key}; {res}")
                else:
                    lines.append(f"[{ts}]: response to command number [{seq}]: {res}")

            body = "\n".join(lines)

            try:
                if not sync_repo.comment_exists(response_comment_id):
                    for _ in range(3):
                        try:
                            new_id = sync_repo.create_comment(body)
                            response_comment_id = new_id
                            add_autonomous_report("responsecommentid", f"responsecommentid:{new_id}")
                            break
                        except:
                            time.sleep(2)
                else:
                    sync_repo.edit_comment(response_comment_id, body)
                safe_log("Sender published response comment.")

                with _report_queue_lock:
                    to_remove = [k for k in snapshot if not k.startswith("AUT-")]
                    for k in to_remove:
                        report_queue.pop(k, None)
            except Exception as e:
                safe_log(f"Sender failed to publish: {e}")
        except Exception as e:
            safe_log(f"Sender loop error: {traceback.format_exc()}")

# ---------- Main startup ----------
def main():
    global app_cmd_id, last_app_body_hash
    safe_log("STEP 1: Loading start URL...")
    try:
        with driver_lock:
            driver.get(START_URL)
        safe_log("STEP 2: Start URL loaded – sleeping 5s")
    except Exception as e:
        safe_log(f"FATAL STARTUP: driver.get failed: {e}")
        push_logs()
        if not restart_browser():
            return
    time.sleep(5)
    safe_log("STEP 3: Scrolling to top")
    try:
        with driver_lock:
            driver.execute_script("window.scrollTo(0,0);")
    except Exception as e:
        safe_log(f"Warning: scrollTo failed: {e}")
    safe_log("STEP 4: Taking first screenshot")
    try:
        ss("01_start_page", push=True)
    except Exception as e:
        safe_log(f"Warning: first screenshot push failed: {e}")
    push_logs()
    safe_log("STEP 5: First screenshot saved – pushing logs")
    safe_log("STEP 6: refresh_known_handles()")
    try:
        refresh_known_handles()
    except Exception as e:
        safe_log(f"Warning: refresh_known_handles failed: {e}")
    safe_log("STEP 7: Set last known URL")
    try:
        agent_state._last_known_url = driver.current_url
    except Exception as e:
        safe_log(f"Warning: driver.current_url failed: {e}")
    safe_log("STEP 8: Start URL monitor")
    try:
        threading.Thread(target=url_monitor_worker, daemon=True).start()
    except Exception as e:
        safe_log(f"Warning: URL monitor start failed: {e}")
    safe_log("STEP 9: URL monitor started")
    push_logs()

    all_comments = sync_repo.get_all_comments()
    app_cmd = find_marker_comment(all_comments, "## App Commands")
    app_cmd_id = app_cmd["id"] if app_cmd else None
    if not app_cmd_id:
        safe_log("No app command comment found – creating one? Not typical.")

    # Start loops
    fetcher_thread = threading.Thread(target=fetcher_loop, daemon=True)
    executor_thread = threading.Thread(target=executor_loop, daemon=True)
    sender_thread = threading.Thread(target=sender_loop, daemon=True)
    start_screenshot_worker()

    fetcher_thread.start()
    executor_thread.start()
    sender_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        safe_log("Keyboard interrupt, shutting down...")
    except Exception as e:
        safe_log(f"Main thread exception: {e}\n{traceback.format_exc()}")
        push_logs()
    finally:
        _fetcher_stop.set()
        _executor_stop.set()
        _sender_stop.set()
        _screenshot_stop.set()
        _url_monitor_stop.set()
        _log_pusher_stop.set()
        _heartbeat_stop.set()
        time.sleep(2)
        ok, msg = save_profile()
        safe_log(f"Final profile save: {msg}")
        push_logs()
        try:
            with driver_lock:
                driver.quit()
        except Exception:
            pass
        display.stop()

if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except SystemExit:
            break
        except Exception as ex:
            safe_log(f"FATAL in main: {ex}\n{traceback.format_exc()}")
            push_logs()
            time.sleep(10)
        finally:
            _fetcher_stop.set()
            _executor_stop.set()
            _sender_stop.set()
            _screenshot_stop.set()
            _url_monitor_stop.set()
            _log_pusher_stop.set()
            _heartbeat_stop.set()
            try:
                if driver:
                    with driver_lock:
                        driver.quit()
            except Exception:
                pass
            display.stop()