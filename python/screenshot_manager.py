#!/usr/bin/env python3
# ==============================================================================
# screenshot_manager.py – Version 1.1.0
#   - Screenshot data is now encrypted before being pushed to the repository.
#     Uses the same XOR‑with‑SHA256 keystream as crypto_utils, then Base64.
#     The client will decrypt after download.
# ==============================================================================

import os, time, threading, traceback, base64, hashlib
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

# Module‑level font
_watermark_font = _find_watermark_font()

def _encrypt_bytes(data: bytes, key: str) -> bytes:
    """Encrypt binary data with XOR keystream, then Base64‑encode."""
    key_bytes = key.encode("utf-8")
    result = bytearray()
    for i, b in enumerate(data):
        h = hashlib.sha256(key_bytes + str(i).encode()).digest()
        result.append(b ^ h[0])
    return base64.b64encode(bytes(result))

def _driver_health_check(driver, driver_lock, timeout=3):
    """Return True if the browser responds within *timeout* seconds."""
    def check():
        with driver_lock:
            driver.title
    t = threading.Thread(target=check, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive()

def _safe_save_screenshot(driver, filename, driver_lock, timeout=10):
    """
    Save a screenshot in a separate thread, with a timeout.
    Returns True on success, False on timeout or error.
    """
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

class ScreenshotWorker:
    """
    Object that holds all state for the screenshot loop.
    Use start() to begin the daemon thread.
    """
    def __init__(self, stop_event, driver, driver_lock, agent_state,
                 sync_repo, log_func, push_logs_func,
                 comm_interval_getter, slow_mode_getter,
                 encryption_key: str):
        self._stop = stop_event
        self._driver = driver
        self._driver_lock = driver_lock
        self._agent_state = agent_state
        self._sync_repo = sync_repo
        self._log = log_func
        self._push_logs = push_logs_func
        self._get_comm_interval = comm_interval_getter
        self._get_slow_mode = slow_mode_getter
        self._encryption_key = encryption_key
        self._counter = 0
        self._font = _watermark_font
        self._thread = None

    def _take_screenshot(self, desc="auto", push=False):
        """Capture, draw, watermark, encrypt. Returns filename or None."""
        self._counter += 1
        now = datetime.now()
        ms = now.microsecond // 1000
        timestamp_str = now.strftime("%H:%M:%S") + f".{ms:03d}"
        filename = f"screenshots/{self._counter:04d}_{now.strftime('%H%M%S')}_{ms:03d}_{desc}.png"
        self._log(f"Taking screenshot: {filename}")

        # Health check
        if not _driver_health_check(self._driver, self._driver_lock, timeout=3):
            self._log("Browser health check failed – skipping screenshot.")
            return None

        # Save with timeout
        if not _safe_save_screenshot(self._driver, filename, self._driver_lock, timeout=10):
            self._log(f"Screenshot save timed out or failed: {filename}")
            return None

        if not os.path.exists(filename):
            self._log(f"Screenshot file not created: {filename}")
            return None

        # Overlay
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

        # Encrypt the final PNG before push
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
            self._sync_repo.push_screenshots([filename])
            self._log(f"Enqueued {filename} + log")
        return filename

    def _push_screenshot(self, filename):
        """Push with timeout, return success."""
        self._log(f"Pushing screenshot {filename} with 10s timeout...")
        try:
            ok = self._sync_repo.push_screenshots_now([filename], timeout=10)
            return ok
        except Exception as e:
            self._log(f"Screenshot push exception: {e}")
            return False

    def _loop(self):
        self._log("Screenshot worker started.")
        while not self._stop.is_set():
            try:
                # Initial pause
                self._stop.wait(2)
                while not self._stop.is_set():
                    start = time.time()
                    filename = self._take_screenshot("auto", push=False)
                    if filename is None:
                        self._log("Screenshot capture failed – will retry after interval.")
                        self._push_logs()
                        interval = max(0, self._get_comm_interval() * self._get_slow_mode() - (time.time() - start))
                        self._stop.wait(interval)
                        continue

                    push_ok = self._push_screenshot(filename)
                    if push_ok:
                        self._log(f"Screenshot {filename} pushed successfully.")
                    else:
                        self._log(f"Screenshot push failed or timed out for {filename}.")

                    # Always push log after a screenshot attempt
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