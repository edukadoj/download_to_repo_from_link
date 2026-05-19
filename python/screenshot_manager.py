#!/usr/bin/env python3
# ==============================================================================
# screenshot_manager.py – Version 1.2.0
#   - Screenshots are now uploaded directly via the GitHub API (base64 content),
#     bypassing git entirely.  This prevents push failures from crashing the
#     screenshot worker.
#   - Upload retries with exponential backoff (1 s, 2 s, 4 s, 8 s, 16 s) and
#     verifies only the remote file size for speed.
#   - The worker loop is fully protected; any exception is logged and the loop
#     continues after a short sleep.
# ==============================================================================

import os, time, threading, traceback, base64, hashlib, json, subprocess
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

def _find_watermark_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, 32)
            except Exception:
                pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None

_watermark_font = _find_watermark_font()

def _encrypt_bytes(data: bytes, key: str) -> bytes:
    key_bytes = key.encode("utf-8")
    result = bytearray()
    for i, b in enumerate(data):
        h = hashlib.sha256(key_bytes + str(i).encode()).digest()
        result.append(b ^ h[0])
    return base64.b64encode(bytes(result))

def _driver_health_check(driver, driver_lock, timeout=3):
    def check():
        with driver_lock:
            driver.title
    t = threading.Thread(target=check, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive()

def _safe_save_screenshot(driver, filename, driver_lock, timeout=10):
    result = [False]
    def save():
        try:
            with driver_lock:
                driver.save_screenshot(filename)
            result[0] = True
        except Exception:
            result[0] = False
    t = threading.Thread(target=save, daemon=True)
    t.start()
    t.join(timeout)
    return result[0] and not t.is_alive()


# ── Direct API upload helper (same pattern as downloader.py) ────────
def _push_screenshot_via_api(repo: str, local_path: str, remote_path: str,
                             pat: str, max_retries: int = 5) -> bool:
    """Upload a screenshot file directly to the repository using the GitHub API.
    Retries with exponential backoff, and only checks remote file size (fast)."""
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat

    with open(local_path, "rb") as f:
        content = f.read()
    local_size = len(content)
    b64 = base64.b64encode(content).decode()

    url = f"repos/{repo}/contents/{remote_path}"

    for attempt in range(1, max_retries + 1):
        try:
            # Check if file already exists to get SHA (needed for update)
            sha = None
            sha_result = subprocess.run(
                ["gh", "api", url, "--jq", ".sha"],
                capture_output=True, text=True, env=env,
            )
            if sha_result.returncode == 0 and sha_result.stdout.strip():
                sha = sha_result.stdout.strip()

            payload = {
                "message": f"Screenshot {remote_path}",
                "content": b64,
                "branch": "main"
            }
            if sha:
                payload["sha"] = sha

            result = subprocess.run(
                ["gh", "api", url, "--method", "PUT", "--input", "-"],
                input=json.dumps(payload), capture_output=True, text=True, env=env,
            )

            if result.returncode == 0:
                # Fast verification: check remote file size
                size_result = subprocess.run(
                    ["gh", "api", url, "--jq", ".size"],
                    capture_output=True, text=True, env=env,
                )
                if size_result.returncode == 0:
                    remote_size_str = size_result.stdout.strip()
                    if remote_size_str.isdigit() and int(remote_size_str) == local_size:
                        return True
                    # Size mismatch – delete and retry
                    subprocess.run(
                        ["gh", "api", url, "--method", "DELETE", "--input", "-"],
                        input=json.dumps({"message": "size mismatch", "sha": sha or "", "branch": "main"}),
                        capture_output=True, text=True, env=env,
                    )
            # Exponential backoff before retry
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))
    return False


class ScreenshotWorker:
    def __init__(self, stop_event, driver, driver_lock, agent_state,
                 sync_repo, log_func, push_logs_func,
                 comm_interval_getter, slow_mode_getter,
                 encryption_key: str,
                 restart_browser_callback=None):
        self._stop = stop_event
        self._driver = driver
        self._driver_lock = driver_lock
        self._agent_state = agent_state
        self._sync_repo = sync_repo          # still used for log push etc.
        self._log = log_func
        self._push_logs = push_logs_func
        self._get_comm_interval = comm_interval_getter
        self._get_slow_mode = slow_mode_getter
        self._encryption_key = encryption_key
        self._restart_browser = restart_browser_callback
        self._counter = 0
        self._font = _watermark_font
        self._thread = None
        self._consecutive_health_fails = 0

        # Repo info for API calls
        self._repo = os.environ.get("GITHUB_REPOSITORY", "")
        self._pat = os.environ.get("PAT", "")

    def _take_screenshot(self, desc="auto", push=False):
        self._counter += 1
        now = datetime.now()
        ms = now.microsecond // 1000
        timestamp_str = now.strftime("%H:%M:%S") + f".{ms:03d}"
        filename = f"screenshots/{self._counter:04d}_{now.strftime('%H%M%S')}_{ms:03d}_{desc}.png"
        self._log(f"Taking screenshot: {filename}")

        if not _driver_health_check(self._driver, self._driver_lock, timeout=3):
            self._log("Browser health check failed – skipping screenshot.")
            self._consecutive_health_fails += 1
            if self._consecutive_health_fails >= 3 and self._restart_browser:
                self._log("Three consecutive health failures – restarting browser.")
                self._restart_browser()
                self._consecutive_health_fails = 0
            return None
        else:
            self._consecutive_health_fails = 0

        if not _safe_save_screenshot(self._driver, filename, self._driver_lock, timeout=10):
            self._log(f"Screenshot save timed out or failed: {filename}")
            return None

        if not os.path.exists(filename):
            self._log(f"Screenshot file not created: {filename}")
            return None

        try:
            img = Image.open(filename).convert("RGBA")
            draw = ImageDraw.Draw(img)
            x, y = (self._agent_state.cursor_x if self._agent_state else 960,
                    self._agent_state.cursor_y if self._agent_state else 540)
            r = 12
            draw.ellipse([(x-r,y-r),(x+r,y+r)], outline='red', width=3)
            draw.line([(x-15,y),(x+15,y)], fill='red', width=3)
            draw.line([(x,y-15),(x,y+15)], fill='red', width=3)
            if self._font:
                overlay = Image.new('RGBA', img.size, (0,0,0,0))
                overlay_draw = ImageDraw.Draw(overlay)
                bbox = overlay_draw.textbbox((0,0), timestamp_str, font=self._font)
                pos = (img.width - bbox[2] - 20, img.height - bbox[3] - 20)
                overlay_draw.text(pos, timestamp_str, font=self._font, fill=(255,255,255,128))
                img = Image.alpha_composite(img, overlay)
            img.save(filename)
        except Exception as e:
            self._log(f"Screenshot image processing error: {e}")
            return None

        try:
            with open(filename, "rb") as f:
                raw = f.read()
            encrypted = _encrypt_bytes(raw, self._encryption_key)
            with open(filename, "wb") as f:
                f.write(encrypted)
        except Exception as e:
            self._log(f"Screenshot encryption error: {e}")
            return None

        if push:
            self._log("Enqueuing screenshot for push...")
            # Use the direct API upload
            success = _push_screenshot_via_api(self._repo, filename, filename, self._pat)
            if success:
                self._log(f"Screenshot {filename} pushed successfully via API.")
            else:
                self._log(f"Screenshot API push failed for {filename}.")
            return filename
        return filename

    def _loop(self):
        self._log("Screenshot worker started.")
        while not self._stop.is_set():
            try:
                # Small delay between cycles
                self._stop.wait(2)
                while not self._stop.is_set():
                    start = time.time()
                    filename = self._take_screenshot("auto", push=True)
                    if filename is None:
                        self._log("Screenshot capture failed – will retry after interval.")
                        self._push_logs()
                        interval = max(0, self._get_comm_interval() * self._get_slow_mode() - (time.time() - start))
                        self._stop.wait(interval)
                        continue

                    # Already pushed inside _take_screenshot with push=True
                    self._push_logs()
                    elapsed = time.time() - start
                    interval = max(0, self._get_comm_interval() * self._get_slow_mode() - elapsed)
                    self._stop.wait(interval)
            except Exception as outer_e:
                self._log(f"Screenshot worker crashed: {outer_e}\n{traceback.format_exc()}")
                self._push_logs()
                self._stop.wait(5)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()