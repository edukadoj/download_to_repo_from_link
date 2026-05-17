#!/usr/bin/env python3
# ==============================================================================
# command_mouse_keyboard.py – Version 39.26.0
#   - Extensive logging + push_logs after EVERY setup step so crash location
#     is pinpointed exactly.
#   - SyncRepo._call_and_wait has a timeout (30s) – if the repo worker dies,
#     the main loop logs an error and recovers.
#   - RepoWrapper worker loop catches and logs all exceptions; never dies.
#   - Watchdog thread: if the main loop hasn't processed a command for 120 s,
#     it logs "Main loop stalled" and triggers a restart of the main loop.
#   - Browser restart now navigates back to START_URL and reports itself.
# ==============================================================================

import os, sys, time, subprocess, hashlib, base64, json, random, threading, traceback, io, shutil, tarfile, glob, re, tempfile, signal, queue as queue_module
from datetime import datetime
from pyvirtualdisplay import Display
from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException
from selenium.webdriver.chrome.options import Options
from PIL import Image, ImageDraw

from crypto_utils import encrypt_string, decode_string
from comments import find_marker_comment
from uploader import reassemble_flat
from execution_queue import ExecutionQueue
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
    log("Received signal, performing emergency save...")
    push_logs()
    save_profile()
    stop_log_thread()
    sys.exit(0)

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ---------- Logging (queue‑based, auto‑restarting) ----------
LOG_FILENAME = "logs/command_mouse_keyboard.log"
os.makedirs("logs", exist_ok=True)

_log_queue = queue_module.Queue()
_log_thread = None
_log_thread_stop = threading.Event()
_log_thread_lock = threading.Lock()

def _log_writer():
    with open(LOG_FILENAME, "a", encoding="utf-8") as f:
        while not _log_thread_stop.is_set() or not _log_queue.empty():
            try:
                msg = _log_queue.get(timeout=1)
                print(msg, flush=True)
                f.write(msg + "\n")
                f.flush()
            except queue_module.Empty:
                continue
            except Exception:
                pass

def start_log_thread():
    global _log_thread
    with _log_thread_lock:
        if _log_thread and _log_thread.is_alive():
            return
        _log_thread_stop.clear()
        _log_thread = threading.Thread(target=_log_writer, daemon=True)
        _log_thread.start()

def stop_log_thread():
    _log_thread_stop.set()
    if _log_thread:
        _log_thread.join(timeout=5)

def monitor_log_thread():
    while not _log_thread_stop.is_set():
        time.sleep(10)
        with _log_thread_lock:
            if not _log_thread or not _log_thread.is_alive():
                log("Log writer thread died – restarting...")
                start_log_thread()

def echo(msg: str) -> None:
    print(msg, flush=True)
    try:
        _log_queue.put(msg)
    except Exception:
        pass

def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    line = f"[{now}] {msg}"
    try:
        _log_queue.put(line)
    except Exception:
        print(line, flush=True)

start_log_thread()
threading.Thread(target=monitor_log_thread, daemon=True).start()

echo(f"{'='*60}\n  Remote Control v39.26.0 started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'='*60}")
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
        exception = [None]
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

    def comment_exists(self, comment_id):
        return self._call_and_wait(self._rw.comment_exists, comment_id)

    def report_memory(self):
        self._rw.report_memory()

# ---------- Repo and SyncRepo setup ----------
ISSUE_NUMBER = os.environ.get("ISSUE_NUMBER","4").strip()
START_URL = os.environ.get("START_URL") or "https://studio.youtube.com"
REPO = os.environ['GITHUB_REPOSITORY']

repo_wrapper = RepoWrapper(REPO, int(ISSUE_NUMBER), LOG_FILENAME)
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
    log(f"PROFILE KEY ERROR: {e}")
    raise

os.makedirs(CACHE_DIR, exist_ok=True)

def load_profile():
    if not glob.glob(os.path.join(CACHE_DIR, "*.part*")):
        log("⚠️ No profile cache chunks found – starting with fresh browser profile.")
        return False
    log("♻️  Reassembling profile cache chunks …")
    tmp_reassemble = tempfile.mkdtemp(prefix="profile_reassemble_")
    try:
        count = reassemble_flat(CACHE_DIR, tmp_reassemble)
        if count == 0:
            log("⚠️ Reassembly produced no file – starting fresh.")
            return False
        files = [f for f in os.listdir(tmp_reassemble) if os.path.isfile(os.path.join(tmp_reassemble, f))]
        if not files:
            log("⚠️ No reassembled file found.")
            return False
        reassembled_path = os.path.join(tmp_reassemble, files[0])
        log(f"   Reassembled: {files[0]} ({os.path.getsize(reassembled_path)} bytes)")
        with open(reassembled_path, "rb") as f:
            encrypted = f.read()
        decrypted = Fernet(ENCRYPTION_KEY).decrypt(encrypted)
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz').extractall('/tmp')
        log("Profile cache loaded successfully.")
        for f in glob.glob(os.path.join(CACHE_DIR, "*.part*")):
            os.remove(f)
        return True
    except Exception as e:
        log(f"ERROR loading profile cache: {type(e).__name__}: {e}")
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
        log(f"Running chunker: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(tmp_path)
        if result.returncode != 0:
            return (False, f"chunker.py failed: {result.stderr.strip() or result.stdout.strip()}")
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], capture_output=True)
        sync_repo.push_log_file()   # pushes cache
        return (True, "Profile cache saved successfully.")
    except Exception as e:
        return (False, f"save_profile exception: {e}")

# ---------- Browser setup ----------
DOWNLOAD_DIR = "/home/runner/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
log("Virtual display started.")

load_profile()
driver = None
try:
    driver = create_driver()
    log("Stealth JS injected.")
    try:
        from upload_injector import _init_cdp
        if _init_cdp(driver, log):
            log("CDP interception active.")
    except Exception as e_cdp:
        log(f"CDP not available ({e_cdp}) – using send_keys fallback.")
    log("Browser launched.")
except Exception as e:
    log(f"BROWSER ERROR: {e}\n{traceback.format_exc()}")
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
    agent_state.log = log

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

def heartbeat_worker():
    while not _heartbeat_stop.is_set():
        _heartbeat_stop.wait(30)
        if not _heartbeat_stop.is_set():
            log("Agent alive")
            sync_repo.report_memory()
            push_logs()

_heartbeat_stop = threading.Event()
threading.Thread(target=heartbeat_worker, daemon=True).start()

# ---------- Download watcher ----------
_download_watcher_stop = threading.Event()
_known_downloads = {}

def download_watcher():
    global _known_downloads
    while not _download_watcher_stop.is_set():
        try:
            time.sleep(2)
            current_files = set(os.listdir(DOWNLOAD_DIR))
            for fname in current_files:
                fpath = os.path.join(DOWNLOAD_DIR, fname)
                if fname.endswith(".crdownload"):
                    if fname not in _known_downloads:
                        _known_downloads[fname] = 0
                        add_autonomous_report("downloadstarted", f"Download started: {fname}")
                else:
                    if fname not in _known_downloads:
                        add_autonomous_report("downloadstarted", f"Download started: {fname}")
                        add_autonomous_report("downloadcompleted", f"Download completed: {fname}")
                        _known_downloads[fname] = os.path.getsize(fpath) if os.path.isfile(fpath) else 0
                        refresh_file_registry()
                    else:
                        crdownload_name = fname + ".crdownload"
                        if crdownload_name in _known_downloads:
                            del _known_downloads[crdownload_name]
                            add_autonomous_report("downloadcompleted", f"Download completed: {fname}")
                            _known_downloads[fname] = os.path.getsize(fpath) if os.path.isfile(fpath) else 0
                            refresh_file_registry()
            for fname in list(_known_downloads.keys()):
                if fname not in current_files:
                    if fname.endswith(".crdownload"):
                        add_autonomous_report("downloadfailed", f"Download failed: {fname}")
                    del _known_downloads[fname]
        except Exception as e:
            log(f"Download watcher error: {e}")

threading.Thread(target=download_watcher, daemon=True).start()

# ---------- Screenshot logic ----------
counter = [0]

def ss(desc="screenshot", push=True, response_suffix=""):
    counter[0] += 1
    now = datetime.now().strftime("%H%M%S")
    fname = f"screenshots/{counter[0]:03d}_{now}_{desc}.png"
    log(f"Taking screenshot: {fname}")
    try:
        ensure_active_tab()
        driver.save_screenshot(fname)
        img = Image.open(fname); draw = ImageDraw.Draw(img)
        x, y = agent_state.cursor_x if agent_state else 960, agent_state.cursor_y if agent_state else 540
        r = 12
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline='red', width=3)
        draw.line([(x-15,y),(x+15,y)], fill='red', width=3)
        draw.line([(x,y-15),(x,y+15)], fill='red', width=3)
        img.save(fname)
    except BaseException as e:
        log(f"Screenshot image processing error: {e}")
    if not push: return fname

    log("Pushing screenshot to repo...")
    sync_repo.report_memory()
    sync_repo.push_screenshots([fname])
    log(f"Pushed {fname} + log")
    return fname

_screenshot_stop = threading.Event()
_screenshot_thread = None

def screenshot_worker():
    while not _screenshot_stop.is_set():
        try:
            time.sleep(2)
            while not _screenshot_stop.is_set():
                start = time.time()
                try:
                    ss("auto", push=True)
                except BaseException as e:
                    log(f"Screenshot worker error: {e}")
                    time.sleep(5)
                    continue
                elapsed = time.time() - start
                _screenshot_stop.wait(max(0, COMM_INTERVAL * slow_mode - elapsed))
        except BaseException as outer_e:
            log(f"Screenshot worker crashed: {outer_e}. Restarting in 5s...")
            _screenshot_stop.wait(5)

def start_screenshot_worker():
    global _screenshot_thread
    if _screenshot_thread and _screenshot_thread.is_alive(): return
    _screenshot_stop.clear()
    _screenshot_thread = threading.Thread(target=screenshot_worker, daemon=True)
    _screenshot_thread.start()
    log("Screenshot worker started.")

def monitor_screenshot_worker():
    while not _screenshot_stop.is_set():
        time.sleep(10)
        if not _screenshot_thread or not _screenshot_thread.is_alive():
            log("Screenshot worker is dead! Restarting...")
            start_screenshot_worker()

start_screenshot_worker()
threading.Thread(target=monitor_screenshot_worker, daemon=True).start()

# ---------- Browser recovery ----------
_calibration_in_progress = False

def restart_browser():
    global driver
    log("Attempting browser restart...")
    if _calibration_in_progress:
        log("Calibration in progress – cancelling instead of restarting browser.")
        add_autonomous_report("calibration_error", "Browser crashed during calibration – calibration cancelled.")
        return False
    try:
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
            log("Browser restarted and navigated to START_URL.")
            add_autonomous_report("browser_restarted", "Browser was restarted after a crash.")
            start_screenshot_worker()
            return True
        except Exception as e:
            log(f"Browser restart attempt {attempt+1} failed: {e}")
            time.sleep(5)
    return False

# ---------- Watchdog ----------
_main_loop_last_active = time.time()

def watchdog():
    global _main_loop_last_active
    while not _watchdog_stop.is_set():
        _watchdog_stop.wait(60)
        if time.time() - _main_loop_last_active > 120:
            log("WATCHDOG: Main loop stalled for >120s. Triggering restart...")
            # The main loop will be restarted by the outer while in main()
            _restart_main_loop.set()

_watchdog_stop = threading.Event()
_restart_main_loop = threading.Event()
threading.Thread(target=watchdog, daemon=True).start()

# ---------- Main loop ----------
def main():
    global slow_mode, last_command_time, _calibration_in_progress, _main_loop_last_active

    # ── Initial navigation with logging ─────────────────────
    log("STEP 1: Loading start URL...")
    try:
        driver.get(START_URL)
        log("STEP 2: Start URL loaded – sleeping 5s")
    except Exception as e:
        log(f"FATAL STARTUP: driver.get failed: {e}")
        push_logs()
        if not restart_browser():
            return
    time.sleep(5)
    log("STEP 3: Scrolling to top")
    try:
        driver.execute_script("window.scrollTo(0,0);")
    except Exception as e:
        log(f"FATAL STARTUP: scrollTo failed: {e}")
        push_logs()
        if not restart_browser():
            return
    log("STEP 4: Taking first screenshot")
    try:
        ss("01_start_page", push=True)
    except Exception as e:
        log(f"FATAL STARTUP: first screenshot failed: {e}")
        push_logs()
        if not restart_browser():
            return
    log("STEP 5: First screenshot saved – pushing logs")
    push_logs()

    log("STEP 6: refresh_known_handles()")
    try:
        refresh_known_handles()
    except Exception as e:
        log(f"Warning: refresh_known_handles failed: {e}")
    log("STEP 7: Set last known URL")
    try:
        agent_state._last_known_url = driver.current_url
    except Exception as e:
        log(f"Warning: driver.current_url failed: {e}")
    log("STEP 8: Start URL monitor")
    try:
        threading.Thread(target=url_monitor_worker, daemon=True).start()
    except Exception as e:
        log(f"Warning: URL monitor start failed: {e}")
    log("STEP 9: URL monitor started")
    push_logs()

    RESPONSE_MARKER = "## Remote Agent Responses"
    APP_COMMAND_MARKER = "## App Commands"

    log("STEP 10: Fetching comments via sync_repo.get_all_comments()")
    try:
        all_comments = sync_repo.get_all_comments()
    except Exception as e:
        log(f"CRITICAL: get_all_comments failed: {e}\n{traceback.format_exc()}")
        push_logs()
        # Try to restart browser and continue – maybe transient network
        if not restart_browser():
            return
        # Retry comments after restart
        try:
            all_comments = sync_repo.get_all_comments()
        except Exception as e2:
            log(f"FATAL: get_all_comments still failing: {e2}")
            return
    log(f"STEP 11: Comments fetched – {len(all_comments)} entries")
    push_logs()

    log("STEP 12: Locating markers in comments")
    resp_comment = find_marker_comment(all_comments, RESPONSE_MARKER)
    if resp_comment:
        response_comment_id = resp_comment["id"]
        log(f"Found response comment: {response_comment_id}")
    else:
        log("No response comment found, creating one...")
        try:
            response_comment_id = sync_repo.create_comment(f"{RESPONSE_MARKER}\n")
            if response_comment_id:
                add_autonomous_report("responsecommentid", f"responsecommentid:{response_comment_id}")
                log(f"Created response comment: {response_comment_id}")
        except Exception as e:
            log(f"CRITICAL: create_comment failed: {e}")
            push_logs()
            return

    app_cmd = find_marker_comment(all_comments, APP_COMMAND_MARKER)
    app_cmd_id = app_cmd["id"] if app_cmd else None
    log(f"STEP 13: App command comment id = {app_cmd_id}")

    if app_cmd_id:
        try:
            sync_repo.edit_comment(app_cmd_id, "## App Commands\n")
            log("STEP 14: Blanked app command comment")
        except Exception as e:
            log(f"Warning: Could not blank app cmd comment: {e}")

    log("STEP 15: Entering main command loop...")
    push_logs()
    _main_loop_last_active = time.time()

    executed_cache = {}
    unsent_reports = []

    def publish_reports(comment_id):
        nonlocal response_comment_id
        cull_expired_autonomous_reports()
        lines = ["## Remote Agent Responses"]
        for ts, seq, result in unsent_reports:
            lines.append(f"[{ts}]: response to command number [{seq}]: {result}")
        for r in pending_autonomous_reports:
            lines.append(f"{r['id']}; {r['text']}")
        body = "\n".join(lines)
        pending_autonomous_reports.clear()
        try:
            if not sync_repo.comment_exists(comment_id):
                for _ in range(3):
                    try:
                        new_id = sync_repo.create_comment(body)
                        log(f"Created new response comment: {new_id}")
                        response_comment_id = new_id
                        add_autonomous_report("responsecommentid", f"responsecommentid:{new_id}")
                        unsent_reports.clear()
                        push_logs()
                        return new_id
                    except Exception:
                        time.sleep(2)
                return comment_id
            sync_repo.edit_comment(comment_id, body)
            unsent_reports.clear()
            push_logs()
            return comment_id
        except Exception as e:
            log(f"Error publishing reports: {e}")
            return comment_id

    while True:
        # Check if watchdog requested a restart
        if _restart_main_loop.is_set():
            _restart_main_loop.clear()
            log("Main loop restart triggered by watchdog.")
            break   # will return to outer while to restart

        if time.time() - last_command_time > 120:
            slow_mode = 15
        else:
            slow_mode = 1

        try:
            if app_cmd_id:
                try:
                    test = sync_repo.get_comment_body(app_cmd_id)
                    if not test:
                        log(f"App command comment {app_cmd_id} vanished – resetting.")
                        app_cmd_id = None
                except Exception as e:
                    log(f"Error reading app cmd comment: {e}")

            if not app_cmd_id:
                time.sleep(COMM_INTERVAL * slow_mode)
                try:
                    allc = sync_repo.get_all_comments()
                except Exception as e:
                    log(f"Error in get_all_comments: {e}")
                    time.sleep(COMM_INTERVAL * slow_mode)
                    continue
                if not allc:
                    time.sleep(COMM_INTERVAL * slow_mode)
                    continue
                app_c = find_marker_comment(allc, APP_COMMAND_MARKER)
                if app_c:
                    app_cmd_id = app_c["id"]
                    log(f"Re‑found app cmd: {app_cmd_id}")
                continue

            try:
                app_body = sync_repo.get_comment_body(app_cmd_id)
            except Exception as e:
                log(f"Error reading app cmd body: {e}")
                time.sleep(COMM_INTERVAL * slow_mode)
                continue
            if not app_body:
                time.sleep(COMM_INTERVAL * slow_mode)
                continue
            lines = app_body.strip().splitlines()
            if not lines:
                time.sleep(COMM_INTERVAL * slow_mode)
                continue

            capture = False
            new_cmds = []
            for line_idx, line in enumerate(lines, start=1):
                m = re.match(r'^\[(\d+)\]: app commands:', line)
                if m:
                    capture = True
                    continue
                if capture:
                    if re.match(r'^\[', line):
                        break
                    parts = line.split(';', 1)
                    if len(parts) == 2:
                        cid = parts[0].strip()
                        ctext = parts[1].strip()
                        if ctext:
                            new_cmds.append((cid, ctext, line_idx))

            for cid, ctext, line_num in new_cmds:
                if cid in executed_cache:
                    continue

                log(f"Executing [{cid}] from line {line_num}: {ctext}")
                sync_repo.report_memory()

                cmd_type, arg = parse_single_command(ctext)
                result = execute_one_command(
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
                    encrypt_string=encrypt_string, gh=None,
                    get_all_comments=lambda: sync_repo.get_all_comments(),
                    delete_comment=lambda cid: sync_repo.delete_comment(cid),
                    issue_comment=lambda body: sync_repo.create_comment(body),
                    smart_edit_comment=lambda cid, body: sync_repo.edit_comment(cid, body),
                    git_push_with_retry=None,
                    comm_interval=COMM_INTERVAL * slow_mode, inject_file=None
                )
                ts = int(time.time() * 1_000_000)
                seq_num = 0
                if cid.startswith("APP-"):
                    parts_cid = cid.split('-')
                    if len(parts_cid) >= 2:
                        try:
                            seq_num = int(parts_cid[1])
                        except ValueError:
                            pass
                executed_cache[cid] = (ts, seq_num, result)
                unsent_reports.append((ts, seq_num, result))
                last_command_time = time.time()
                _main_loop_last_active = time.time()
                log(f"Executed: {cid} → {result}")
                push_logs()

                if cmd_type == "exit":
                    response_comment_id = publish_reports(response_comment_id)
                    log("Exit command received – saving profile cache...")
                    ok, msg = save_profile()
                    log(f"Profile save: {msg}")
                    time.sleep(1)
                    response_comment_id = publish_reports(response_comment_id)
                    ss("final", push=True)
                    _screenshot_stop.set()
                    _url_monitor_stop.set()
                    _download_watcher_stop.set()
                    _log_pusher_stop.set()
                    _heartbeat_stop.set()
                    _watchdog_stop.set()
                    driver.quit()
                    display.stop()
                    push_logs()
                    echo("\n🎉 Remote session ended.")
                    sys.exit(0)

            response_comment_id = publish_reports(response_comment_id)

        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except (WebDriverException, InvalidSessionIdException) as driver_error:
            log(f"Driver error: {driver_error}. Attempting browser restart...")
            if not restart_browser():
                log("Browser restart failed permanently. Exiting.")
                push_logs()
                break
        except Exception as e:
            log(f"Main loop exception: {traceback.format_exc()}")
            push_logs()
            time.sleep(5)

    # If we break out of the inner loop, restart the main function
    log("Restarting main loop...")
    push_logs()

if __name__ == "__main__":
    while True:
        try:
            main()
        except SystemExit:
            break
        except Exception as ex:
            log(f"FATAL in main: {ex}\n{traceback.format_exc()}")
            push_logs()
            time.sleep(10)
            # Restart
        finally:
            # Cleanup that must happen on each restart
            _screenshot_stop.set()
            _url_monitor_stop.set()
            _download_watcher_stop.set()
            _log_pusher_stop.set()
            _heartbeat_stop.set()
            _watchdog_stop.set()
            _restart_main_loop.clear()
            ok, msg = save_profile()
            log(f"Final profile save: {msg}")
            push_logs()
            # Re-initiate threads for the next main() call
            _screenshot_stop.clear()
            _url_monitor_stop.clear()
            _download_watcher_stop.clear()
            _log_pusher_stop.clear()
            _heartbeat_stop.clear()
            _watchdog_stop.clear()
            # Restart the background threads
            threading.Thread(target=log_pusher, daemon=True).start()
            threading.Thread(target=heartbeat_worker, daemon=True).start()
            threading.Thread(target=watchdog, daemon=True).start()
    stop_log_thread()