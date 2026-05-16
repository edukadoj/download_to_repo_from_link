#!/usr/bin/env python3
# ==============================================================================
# command_mouse_keyboard.py – Version 39.20.2 (load profile cache on startup)
#   - Now calls load_profile() before creating Chrome, restoring session state
#   - Includes the sequence number fix (v39.20.1) for proper command acknowledgement
# ==============================================================================
import os, time, subprocess, hashlib, sys, base64, json, random, threading, traceback, io, shutil, tarfile, glob, re
from datetime import datetime, timezone
from pyvirtualdisplay import Display
from cryptography.fernet import Fernet, InvalidToken
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.chrome.options import Options
from PIL import Image, ImageDraw

from crypto_utils import encrypt_string, decode_string
from comments import (
    get_all_comments, find_marker_comment, issue_comment,
    delete_comment, edit_comment, comment_exists, gh_api as gh
)
from uploader import reassemble as upload_reassemble
from execution_queue import ExecutionQueue
from command_handlers import execute_one_command

from agent_state import (
    log as default_log,
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

# ---------- Logging ----------
LOG_FILENAME = "logs/command_mouse_keyboard.log"
os.makedirs("logs", exist_ok=True)

_logfile = open(LOG_FILENAME, "a", encoding="utf-8")
_log_lock = threading.Lock()
_log_closed = False

def safe_log_write(message: str) -> None:
    global _log_closed
    if _log_closed:
        return
    try:
        with _log_lock:
            _logfile.write(message + "\n")
            _logfile.flush()
    except Exception:
        pass

def echo(msg: str) -> None:
    print(msg, flush=True)
    safe_log_write(msg)

def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    echo(f"[{now}] {msg}")

echo(f"{'='*60}\n  Remote Control v39.20.2 started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'='*60}")
os.makedirs("screenshots", exist_ok=True)

COMM_INTERVAL = 5.0
slow_mode = 1
last_command_time = time.time()

# ---------- SINGLE global lock for all git sequences ----------
GIT_SEQUENCE_LOCK = threading.Lock()

# Screenshot retry queue
_screenshot_retry_queue = []
_retry_queue_lock = threading.Lock()

def git_cleanup():
    lock_file = ".git/index.lock"
    if os.path.exists(lock_file):
        try: os.remove(lock_file)
        except Exception: pass

def git_run(cmd, **kwargs):
    git_cleanup()
    return subprocess.run(cmd, **kwargs)

def git_push_with_retry() -> bool:
    for attempt in range(3):
        try:
            git_run(["git","push"], check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            log(f"Git push attempt {attempt+1} failed: {e.stderr.strip() if e.stderr else 'unknown'}")
            if attempt < 2:
                time.sleep(2 + random.random()*3)
                try: git_run(["git","pull","--rebase"], check=True, capture_output=True)
                except Exception: pass
    return False

# ---------- GitHub API helpers ----------
def gh_api_safe(*args, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            return gh(*args, **kwargs)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else "(no stderr)"
            log(f"GitHub API error: {err_msg}")
            if attempt < max_retries - 1:
                delay = 2 ** attempt + random.uniform(0, 1)
                log(f"Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                return None
    return None

# ---------- Profile cache (chunked, manual save only) ----------
PROFILE_DIR = "/tmp/chrome_profile"
CACHE_DIR = ".profile_cache"
CHUNK_SIZE = 45 * 1024 * 1024
ENCRYPTION_KEY = None
try:
    KEY = os.environ["KEY"]
    ENCRYPTION_KEY = base64.urlsafe_b64encode(hashlib.sha256(KEY.encode()).digest())
except Exception as e:
    log(f"PROFILE KEY ERROR: {e}")
    raise

os.makedirs(CACHE_DIR, exist_ok=True)

def load_profile():
    chunks = sorted(glob.glob(os.path.join(CACHE_DIR, "profile.enc.part*")))
    if not chunks:
        log("⚠️ No profile cache chunks found – starting with fresh browser profile.")
        return False
    log(f"Found {len(chunks)} cache chunk(s). Decrypting…")
    try:
        buf = io.BytesIO()
        for path in chunks:
            with open(path, "rb") as f:
                buf.write(f.read())
        decrypted = Fernet(ENCRYPTION_KEY).decrypt(buf.getvalue())
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        tarfile.open(fileobj=io.BytesIO(decrypted), mode='r:gz').extractall('/tmp')
        log("Profile cache loaded successfully.")
        return True
    except Exception as e:
        log(f"ERROR loading profile cache: {type(e).__name__}: {e}")
        return False

def save_profile():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(PROFILE_DIR, arcname="chrome_profile")
        encrypted = Fernet(ENCRYPTION_KEY).encrypt(buf.getvalue())
        for old in glob.glob(os.path.join(CACHE_DIR, "profile.enc.part*")):
            os.remove(old)
        chunk_paths = []
        for i in range(0, len(encrypted), CHUNK_SIZE):
            part_name = f"profile.enc.part{i//CHUNK_SIZE:04d}"
            part_path = os.path.join(CACHE_DIR, part_name)
            with open(part_path, "wb") as f:
                f.write(encrypted[i:i+CHUNK_SIZE])
            chunk_paths.append(part_path)
        log(f"Profile cache prepared: {len(chunk_paths)} chunk(s) locally.")

        upload_ok = True
        for part_path in chunk_paths:
            part_name = os.path.basename(part_path)
            remote_path = f"{CACHE_DIR}/{part_name}"
            local_size = os.path.getsize(part_path)
            with open(part_path, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode()
            success = _upload_chunk_with_retry(remote_path, content_b64, local_size)
            if not success:
                log(f"❌ Failed to upload {part_name} after all retries.")
                upload_ok = False
            else:
                log(f"✅ Chunk {part_name} uploaded and verified.")
        if upload_ok:
            log("🎉 Profile cache fully saved to repo.")
        else:
            log("⚠️ Some profile chunks were NOT uploaded. Try 'save' again.")
    except Exception as e:
        log(f"ERROR saving profile cache: {e}")

def _upload_chunk_with_retry(remote_path, content_b64, local_size, max_retries=3):
    for attempt in range(1, max_retries + 1):
        sha, remote_size = _get_remote_file_sha_and_size(remote_path)
        if remote_size == local_size:
            return True
        if sha is not None:
            log(f"  {os.path.basename(remote_path)}: remote size mismatch ({remote_size} vs {local_size}) – deleting old file.")
            _delete_remote_file(remote_path, sha)
        log(f"  Uploading {os.path.basename(remote_path)} (attempt {attempt}/{max_retries})…")
        if _create_remote_file(remote_path, content_b64):
            _, new_remote_size = _get_remote_file_sha_and_size(remote_path)
            if new_remote_size == local_size:
                return True
            else:
                log(f"  Verification failed: local {local_size}, remote {new_remote_size}. Retrying…")
        else:
            log(f"  Upload attempt {attempt} failed.")
        time.sleep(1)
    return False

def _get_remote_file_sha_and_size(remote_path):
    try:
        out = gh_api_safe(
            f"repos/{REPO}/contents/{remote_path}",
            "--jq", ".sha + '\n' + (.size | tostring)"
        )
        if out:
            sha, size_str = out.strip().split('\n', 1)
            return sha.strip(), int(size_str)
    except Exception:
        pass
    return None, None

def _delete_remote_file(remote_path, sha):
    payload = json.dumps({"message": "purge old chunk", "sha": sha, "branch": "main"})
    try:
        gh("--method", "DELETE", f"repos/{REPO}/contents/{remote_path}", input_data=payload)
        return True
    except subprocess.CalledProcessError:
        return False

def _create_remote_file(remote_path, content_b64):
    payload = json.dumps({
        "message": f"Upload profile chunk: {os.path.basename(remote_path)}",
        "content": content_b64,
        "branch": "main"
    })
    try:
        gh("--method", "PUT", f"repos/{REPO}/contents/{remote_path}", input_data=payload)
        return True
    except subprocess.CalledProcessError:
        return False

# ---------- Browser setup ----------
DOWNLOAD_DIR = "/home/runner/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ★ NEW: restore cached profile before launching Chrome ★
load_profile()

try:
    display = Display(visible=False, size=(1920,1080))
    display.start()
    log("Virtual display started.")
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

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.execute_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]})")
    driver.execute_script("Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']})")
    driver.execute_script("window.chrome = { runtime: {} };")
    driver.execute_script("Object.defineProperty(navigator, 'permissions', {get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })})")
    driver.execute_script("Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4})")
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
    raise

# Sync globals to agent_state
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

# ---------- Screenshot (with log push & immediate purge) ----------
counter = [0]

def ss(desc="screenshot", push=True, response_suffix=""):
    counter[0] += 1
    now = datetime.now().strftime("%H%M%S")
    fname = f"screenshots/{counter[0]:03d}_{now}_{desc}.png"
    log(f"Taking screenshot: {fname}")

    ensure_active_tab()
    driver.save_screenshot(fname)
    try:
        img = Image.open(fname); draw = ImageDraw.Draw(img)
        x, y = agent_state.cursor_x if agent_state else 960, agent_state.cursor_y if agent_state else 540
        r = 12
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline='red', width=3)
        draw.line([(x-15,y),(x+15,y)], fill='red', width=3)
        draw.line([(x,y-15),(x,y+15)], fill='red', width=3)
        img.save(fname)
    except Exception: pass
    if not push: return fname

    with _retry_queue_lock:
        _screenshot_retry_queue.append(fname)

    with GIT_SEQUENCE_LOCK:
        try:
            git_run(["git","stash","--include-untracked"], capture_output=True)
            try: git_run(["git","pull","--rebase"], check=True, capture_output=True)
            except Exception: pass
            git_run(["git","stash","pop"], capture_output=True)

            files_to_push = []
            with _retry_queue_lock:
                files_to_push = list(_screenshot_retry_queue)
                _screenshot_retry_queue.clear()

            pushed_any = False
            for f in files_to_push:
                if os.path.exists(f):
                    git_run(["git","add",f], check=True, capture_output=True)
                    pushed_any = True

            # Always add log file
            if os.path.exists(LOG_FILENAME):
                git_run(["git","add",LOG_FILENAME], check=True, capture_output=True)

            # Check if there's anything to commit (either screenshots or log changes)
            diff_check = subprocess.run(["git","diff","--cached","--quiet"], capture_output=True)
            if diff_check.returncode != 0:
                git_run(["git","commit","-m","Screenshots & log"], check=True, capture_output=True)
                if git_push_with_retry():
                    log(f"Pushed {len(files_to_push)} screenshot(s) + log")
                    # Immediately purge all old screenshots (only keep latest of each? keep only the newest)
                    # Actually purge all except the ones we just pushed? We'll just purge all older .png files.
                    purge_old_screenshots(fname)
                else:
                    log("ERROR: Failed to push screenshots – will retry")
                    with _retry_queue_lock:
                        for f in files_to_push:
                            if os.path.exists(f) and f not in _screenshot_retry_queue:
                                _screenshot_retry_queue.append(f)
            else:
                # No changes? That can happen if the same screenshot was already pushed.
                pass
        except Exception as e:
            log(f"Screenshot git error: {e}")
            with _retry_queue_lock:
                for f in files_to_push:
                    if os.path.exists(f) and f not in _screenshot_retry_queue:
                        _screenshot_retry_queue.append(f)
    return fname

def purge_old_screenshots(keep_path):
    """Delete all .png files in screenshots/ except the newest one (keep_path)."""
    try:
        raw = gh_api_safe(f"repos/{REPO}/contents/screenshots", "--jq", ".[].path")
        if not raw: return
        for path in raw.strip().splitlines():
            path = path.strip().strip('"')
            if not path.endswith(".png"): continue
            if path == keep_path: continue
            sha_raw = gh_api_safe(f"repos/{REPO}/contents/{path}", "--jq", ".sha")
            if not sha_raw: continue
            sha = sha_raw.strip().strip('"')
            gh_api_safe("--method","DELETE",f"repos/{REPO}/contents/{path}",
                         "-f","message=purge old screenshot","-f",f"sha={sha}","-f","branch=main")
            log(f"Purged old screenshot: {path}")
    except Exception as e:
        log(f"Error purging old screenshots: {e}")

_screenshot_stop = threading.Event()
_screenshot_thread = None
_screenshot_worker_running = False

def screenshot_worker():
    global _screenshot_worker_running
    _screenshot_worker_running = True
    while not _screenshot_stop.is_set():
        try:
            time.sleep(2)
            while not _screenshot_stop.is_set():
                start = time.time()
                try:
                    ss("auto", push=True)
                except Exception as e:
                    log(f"Screenshot worker error: {e}")
                    time.sleep(5)
                    continue
                elapsed = time.time() - start
                _screenshot_stop.wait(max(0, COMM_INTERVAL * slow_mode - elapsed))
        except Exception as outer_e:
            log(f"Screenshot worker crashed: {outer_e}. Restarting in 5s...")
            _screenshot_stop.wait(5)
    _screenshot_worker_running = False

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

# ---------- GitHub basics ----------
ISSUE_NUMBER = os.environ.get("ISSUE_NUMBER","4").strip()
START_URL = os.environ.get("START_URL") or "https://studio.youtube.com"
REPO = os.environ['GITHUB_REPOSITORY']

def push_logs():
    with GIT_SEQUENCE_LOCK:
        try:
            git_run(["git","add",LOG_FILENAME], check=True, capture_output=True)
            try: git_run(["git","diff","--cached","--quiet"], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                git_run(["git","commit","-m","Log update"], check=True, capture_output=True)
                git_push_with_retry()
        except Exception as e: echo(f"Could not push logs: {e}")

KEY_SECRET = os.environ["KEY"]

def smart_edit_comment(comment_id, new_body):
    for attempt in range(2):
        try:
            edit_comment(REPO, comment_id, new_body)
            return True
        except subprocess.CalledProcessError:
            if attempt == 0:
                log("Edit rate‑limited, retrying in 2s...")
                time.sleep(2)
            else:
                log("Edit failed after retry.")
    return False

# ---------- Main loop ----------
def main():
    global slow_mode, last_command_time

    try:
        log("Loading start URL...")
        driver.get(START_URL)
        log("Start URL loaded – sleeping 5s")
        time.sleep(5)
        log("Scrolling to top")
        driver.execute_script("window.scrollTo(0,0);")
        log("Taking first screenshot")
        ss("01_start_page", push=True)
        log("First screenshot saved")
        refresh_known_handles()
        agent_state._last_known_url = driver.current_url
        threading.Thread(target=url_monitor_worker, daemon=True).start()
        log("URL monitor started")
    except Exception as e:
        log(f"FATAL STARTUP: {e}\n{traceback.format_exc()}")
        push_logs()
        raise

    RESPONSE_MARKER = "## Remote Agent Responses"
    APP_COMMAND_MARKER = "## App Commands"

    # Fetch comments with retry and logging
    log("Fetching comments to locate markers...")
    all_comments = None
    for attempt in range(5):
        all_comments = gh_api_safe(f"repos/{REPO}/issues/{ISSUE_NUMBER}/comments",
                                    "--jq", ".[] | {id: .id, body: .body, user_type: .user.type}", "--paginate")
        if all_comments is not None:
            break
        log(f"Comment fetch attempt {attempt+1} failed, retrying...")
        time.sleep(2)
    if all_comments is None:
        log("CRITICAL: Could not fetch issue comments. Exiting.")
        return

    all_comments_raw = all_comments
    parsed = []
    try:
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(all_comments_raw):
            while idx < len(all_comments_raw) and all_comments_raw[idx].isspace(): idx += 1
            if idx >= len(all_comments_raw): break
            obj, end = decoder.raw_decode(all_comments_raw, idx)
            parsed.append({"id": str(obj.get("id","")), "body": obj.get("body",""), "user_type": obj.get("user_type","")})
            idx = end
    except Exception as e:
        log(f"Error parsing comments JSON: {e}")

    resp_comment = find_marker_comment(parsed, RESPONSE_MARKER)
    if resp_comment: response_comment_id = resp_comment["id"]
    else:
        log("No response comment found, creating one...")
        new_id = gh_api_safe(f"repos/{REPO}/issues/{ISSUE_NUMBER}/comments",
                              "--method", "POST", "-f", f"body={RESPONSE_MARKER}\n", "--jq", ".id")
        response_comment_id = new_id.strip() if new_id else None

    app_cmd = find_marker_comment(parsed, APP_COMMAND_MARKER)
    app_cmd_id = app_cmd["id"] if app_cmd else None
    log(f"App command comment: {app_cmd_id}")

    if app_cmd_id:
        try: edit_comment(REPO, app_cmd_id, "## App Commands\n")
        except Exception as e: log(f"Could not blank: {e}")

    executed_cache = {}
    unsent_reports = []
    exec_queue = ExecutionQueue()

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
        if not comment_exists(REPO, comment_id):
            for _ in range(3):
                try:
                    new_id = issue_comment(REPO, ISSUE_NUMBER, body)
                    log(f"Created new response comment: {new_id}")
                    unsent_reports.clear()
                    push_logs()
                    return new_id
                except Exception: time.sleep(2)
            return comment_id
        if smart_edit_comment(comment_id, body):
            unsent_reports.clear()
            push_logs()
            return comment_id
        return comment_id

    log("Entering main command loop...")
    while True:
        if time.time() - last_command_time > 120: slow_mode = 15
        else: slow_mode = 1

        try:
            if app_cmd_id:
                test = gh_api_safe(f"repos/{REPO}/issues/comments/{app_cmd_id}", "--jq", ".id")
                if test is None:
                    log(f"App command comment {app_cmd_id} vanished – resetting.")
                    app_cmd_id = None

            if not app_cmd_id:
                time.sleep(COMM_INTERVAL * slow_mode)
                allc = gh_api_safe(f"repos/{REPO}/issues/{ISSUE_NUMBER}/comments",
                                    "--jq", ".[] | {id: .id, body: .body, user_type: .user.type}", "--paginate")
                if allc is None:
                    time.sleep(COMM_INTERVAL * slow_mode)
                    continue
                parsed_all = []
                try:
                    decoder = json.JSONDecoder()
                    idx = 0
                    while idx < len(allc):
                        while idx < len(allc) and allc[idx].isspace(): idx += 1
                        if idx >= len(allc): break
                        obj, end = decoder.raw_decode(allc, idx)
                        parsed_all.append({"id": str(obj.get("id","")), "body": obj.get("body",""), "user_type": obj.get("user_type","")})
                        idx = end
                except Exception: pass
                app_c = find_marker_comment(parsed_all, APP_COMMAND_MARKER)
                if app_c:
                    app_cmd_id = app_c["id"]
                    log(f"Re‑found app cmd: {app_cmd_id}")
                continue

            app_body = gh_api_safe(f"repos/{REPO}/issues/comments/{app_cmd_id}", "--jq", ".body")
            if app_body is None: time.sleep(COMM_INTERVAL * slow_mode); continue
            if not app_body: time.sleep(COMM_INTERVAL * slow_mode); continue
            lines = app_body.strip().splitlines()
            if not lines: time.sleep(COMM_INTERVAL * slow_mode); continue

            capture = False
            new_cmds = []
            for line in lines:
                m = re.match(r'^\[(\d+)\]: app commands:', line)
                if m: capture = True; continue
                if capture:
                    if re.match(r'^\[', line): break
                    parts = line.split(';', 1)
                    if len(parts) == 2:
                        cid = parts[0].strip(); ctext = parts[1].strip()
                        if ctext: new_cmds.append((cid, ctext))

            for cid, ctext in new_cmds:
                if cid in executed_cache:
                    ts, seq_stored, cached_result = executed_cache[cid]
                    unsent_reports.append((ts, seq_stored, cached_result))
                else:
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
                        upload_reassemble=upload_reassemble,
                        HAS_PYPERCLIP_local=HAS_PYPERCLIP,
                        encrypt_string=encrypt_string, gh=gh,
                        get_all_comments=get_all_comments,
                        delete_comment=delete_comment, issue_comment=issue_comment,
                        smart_edit_comment=smart_edit_comment, git_push_with_retry=git_push_with_retry,
                        comm_interval=COMM_INTERVAL * slow_mode, inject_file=None
                    )
                    ts = int(time.time())
                    # extract sequence number from cid
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
                    log(f"Executed: {cid} → {result}")
                    if cmd_type == "exit":
                        response_comment_id = publish_reports(response_comment_id)
                        log("Exit command received – saving profile cache...")
                        save_profile()
                        time.sleep(1)
                        response_comment_id = publish_reports(response_comment_id)
                        ss("final", push=True)
                        _screenshot_stop.set()
                        _url_monitor_stop.set()
                        _download_watcher_stop.set()
                        driver.quit(); display.stop()
                        push_logs()
                        echo("\n🎉 Remote session ended.")
                        sys.exit(0)

            response_comment_id = publish_reports(response_comment_id)
        except Exception as e:
            log(f"Polling error: {traceback.format_exc()}")
            push_logs()
            time.sleep(COMM_INTERVAL * slow_mode)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception as ex:
        log(f"FATAL: {ex}\n{traceback.format_exc()}")
        push_logs()
    finally:
        _screenshot_stop.set()
        _url_monitor_stop.set()
        _download_watcher_stop.set()
        save_profile()
        push_logs()
        _log_closed = True
        try: _logfile.close()
        except Exception: pass