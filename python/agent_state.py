#!/usr/bin/env python3
# ==============================================================================
# agent_state.py – Version 2.7.1
#   - Parser now interprets coordinate pairs as percentages (0.0 – 1.0)
#     and converts them to absolute pixels using the real viewport (W, H).
#   - Old integer‑pixel parsing removed.
#   - probe_clickable_bounds() and update_viewport() unchanged.
# ==============================================================================

import os, time, re, glob, threading, traceback, random, base64
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException

# ---------- Log helper (will be overridden by main) ----------
def log(msg: str) -> None:
    pass

# ---------- Global driver & viewport ----------
driver = None
W, H = 1920, 1080          # default; overwritten by probe_clickable_bounds()
cursor_x, cursor_y = 0, 0

# Thread‑safety lock – set by main script to a common lock
driver_lock = threading.Lock()

# ---------- Optional modules ----------
HAS_GEMINI = False
HAS_PYPERCLIP = False
pyperclip = None

# ---------- Allowed secrets ----------
allowed_secrets = []

# ---------- Active tab tracking (1‑based index) ----------
ACTIVE_TAB_INDEX = 1

def update_viewport():
    """
    Read the CSS viewport size from the browser and update W, H.
    This is an approximation; the real clickable area may be slightly
    smaller and will be refined by probe_clickable_bounds().
    """
    global W, H
    if driver is None:
        return
    try:
        with driver_lock:
            w = driver.execute_script("return window.innerWidth;")
            h = driver.execute_script("return window.innerHeight;")
            if w and h:
                W, H = int(w), int(h)
                log(f"Viewport CSS size updated to {W}x{H}")
    except Exception as e:
        log(f"update_viewport error: {e}")


def _try_raw_move(x: int, y: int) -> bool:
    """
    Attempt an absolute move to (x, y) without any coordinate clamping.
    Returns True if the driver accepted the move, False otherwise.
    """
    try:
        with driver_lock:
            action = ActionBuilder(driver)
            action.pointer_action.move_to_location(x, y)
            action.perform()
        return True
    except (WebDriverException, InvalidSessionIdException):
        return False
    except Exception:
        return False


def probe_clickable_bounds():
    """
    Find the largest X and Y coordinates that the browser will accept
    by probing from the current (W-5, H-5) downwards.  Only a few
    attempts are needed because the unreachable border is tiny.
    Updates W, H and sends an autonomous report 'viewsize:WxH'.
    """
    global W, H
    if driver is None:
        return

    ensure_active_tab()
    log("Probing clickable bounds...")

    # ---- find max X ----
    max_x = W - 5
    while max_x > 0:
        if _try_raw_move(max_x, 1):
            break
        max_x -= 1
    if max_x == 0:
        max_x = W - 5
    log(f"Max clickable X = {max_x}")

    # ---- find max Y ----
    max_y = H - 5
    while max_y > 0:
        if _try_raw_move(1, max_y):
            break
        max_y -= 1
    if max_y == 0:
        max_y = H - 5
    log(f"Max clickable Y = {max_y}")

    # Update global dimensions
    W, H = max_x, max_y
    log(f"Clickable bounds set to {W}x{H}")

    # Report to the client
    add_autonomous_report("viewsize", f"viewsize:{W}x{H}")


def ensure_active_tab():
    """
    Make sure the browser is pointing to the expected tab.
    Never raises – all driver exceptions are caught and logged.
    IMPORTANT: No JavaScript execution is performed here, so the call
    will never block the driver thread.
    """
    global ACTIVE_TAB_INDEX
    if driver is None:
        return
    try:
        with driver_lock:
            handles = list(driver.window_handles)
            if not handles:
                return
            idx = ACTIVE_TAB_INDEX - 1
            if idx < 0 or idx >= len(handles):
                current = driver.current_window_handle
                if current in handles:
                    ACTIVE_TAB_INDEX = handles.index(current) + 1
                else:
                    ACTIVE_TAB_INDEX = 1
                    idx = 0
            else:
                if driver.current_window_handle != handles[idx]:
                    driver.switch_to.window(handles[idx])
                    try:
                        driver.set_window_size(W, H)
                    except Exception:
                        pass
            # Viewport detection is intentionally NOT called here.
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"ensure_active_tab driver error: {e}")
    except Exception as e:
        log(f"ensure_active_tab unexpected error: {e}")


# ---------- Improved drag‑and‑drop file injection ----------
def drag_file_to_target(driver_ref, file_path, x, y):
    try:
        with driver_lock:
            elements = driver_ref.execute_script(
                "return document.elementsFromPoint(arguments[0], arguments[1]);",
                x, y
            )
            if elements:
                for el in elements:
                    tag = driver_ref.execute_script("return arguments[0].tagName.toLowerCase();", el)
                    type_attr = driver_ref.execute_script("return arguments[0].type;", el)
                    if tag == "input" and type_attr == "file":
                        el.send_keys(file_path)
                        return True
                parent_el = elements[0]
                for _ in range(10):
                    if parent_el is None:
                        break
                    file_inputs = driver_ref.execute_script(
                        "return arguments[0].querySelectorAll('input[type=file]');",
                        parent_el
                    )
                    if file_inputs and len(file_inputs) > 0:
                        file_inputs[0].send_keys(file_path)
                        return True
                    parent_el = driver_ref.execute_script("return arguments[0].parentElement;", parent_el)
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"drag_file_to_target: driver error: {e}")
    except Exception as e:
        log(f"drag_file_to_target: unexpected error: {e}")

    script = """
    var x = arguments[0], y = arguments[1], filePath = arguments[2];
    var elements = document.elementsFromPoint(x, y);
    if (!elements || elements.length === 0) return false;
    var target = null;
    for (var i = 0; i < elements.length; i++) {
        var el = elements[i];
        if (el.tagName === 'INPUT' && el.type === 'file') {
            target = el;
            break;
        }
    }
    if (!target) target = elements[0];
    if (target.tagName === 'INPUT' && target.type === 'file') return false;

    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.style.display = 'none';
    fileInput.multiple = false;
    fileInput.onchange = function() {
        if (!fileInput.files.length) return;
        var file = fileInput.files[0];
        var dt = new DataTransfer();
        dt.items.add(file);
        var dragEnter = new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: dt});
        var dragOver = new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt});
        var drop = new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt});
        target.dispatchEvent(dragEnter);
        target.dispatchEvent(dragOver);
        target.dispatchEvent(drop);
        setTimeout(function() { document.body.removeChild(fileInput); }, 500);
    };
    document.body.appendChild(fileInput);
    return fileInput;
    """
    try:
        with driver_lock:
            result = driver_ref.execute_script(script, x, y, file_path)
            if result and not isinstance(result, bool):
                result.send_keys(file_path)
                return True
        return False
    except Exception as e:
        log(f"drag_file_to_target fallback error: {e}")
        return False


# ---------- EXISTING UTILITY FUNCTIONS (with driver_lock) ----------

def _try_gemini_click(prompt: str) -> bool:
    if not HAS_GEMINI:
        return False
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return False
    try:
        from google import genai
        from google.genai.types import Tool, GenerateContentConfig
        client = genai.Client(api_key=api_key)
        computer_tool = Tool(computer_use={})
        config = GenerateContentConfig(tools=[computer_tool])
        tmp = "/tmp/gemini_click.png"
        with driver_lock:
            driver.save_screenshot(tmp)
        with open(tmp, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        resp = client.models.generate_content(
            model="gemini-2.5-computer-use-preview-10-2025",
            contents=[{"role":"user","parts":[{"text":prompt},{"inline_data":{"mime_type":"image/png","data":img_data}}]}],
            config=config)
        if not resp.candidates:
            return False
        fc = resp.candidates[0].content.parts[0].function_call
        if fc.name == "click_at":
            ax = int(fc.args["x"]/1000*W)
            ay = int(fc.args["y"]/1000*H)
            _perform_human_click_at(ax, ay)
            return True
        return False
    except Exception as e:
        log(f"Gemini click error: {e}")
        return False


# ── move_cursor_absolute – returns bool, uses safe W/H ─────────────────
def move_cursor_absolute(x: int, y: int) -> bool:
    """
    Move the cursor to (x, y), clamped to the current clickable bounds.
    Returns True if the driver operation succeeded, False otherwise.
    On success, global cursor_x/cursor_y are updated.
    """
    ensure_active_tab()
    global cursor_x, cursor_y
    x = max(0, min(W-1, x))
    y = max(0, min(H-1, y))
    try:
        with driver_lock:
            action = ActionBuilder(driver)
            action.pointer_action.move_to_location(x, y)
            action.perform()
        cursor_x, cursor_y = x, y
        log(f"Cursor moved to ({x}, {y})")
        return True
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"move_cursor_absolute driver error: {e}")
        return False
    except Exception as e:
        log(f"move_cursor_absolute unexpected error: {e}")
        return False


def move_cursor_relative(dx: int, dy: int) -> bool:
    ensure_active_tab()
    global cursor_x, cursor_y
    new_x = max(0, min(W-1, cursor_x + dx))
    new_y = max(0, min(H-1, cursor_y + dy))
    return move_cursor_absolute(new_x, new_y)


# ── Other interaction functions ────────────────────
def _driver_action(func, *args, **kwargs):
    """Wrapper to execute a driver action safely."""
    try:
        with driver_lock:
            func(*args, **kwargs)
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"Driver action error: {e}")
    except Exception as e:
        log(f"Driver action unexpected error: {e}")


def left_click() -> None:
    ensure_active_tab()
    _driver_action(lambda: ActionChains(driver).click().perform())


def left_button_down() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.click_and_hold()
        action.perform()
    _driver_action(_do)


def left_button_up() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.release()
        action.perform()
    _driver_action(_do)


def right_button_down() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.pointer_down(PointerInput.Button.RIGHT)
        action.perform()
    _driver_action(_do)


def right_button_up() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.pointer_up(PointerInput.Button.RIGHT)
        action.perform()
    _driver_action(_do)


def middle_button_down() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.pointer_down(PointerInput.Button.MIDDLE)
        action.perform()
    _driver_action(_do)


def middle_button_up() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.pointer_up(PointerInput.Button.MIDDLE)
        action.perform()
    _driver_action(_do)


def double_click() -> None:
    ensure_active_tab()
    _driver_action(lambda: ActionChains(driver).double_click().perform())


def right_click() -> None:
    ensure_active_tab()
    _driver_action(lambda: ActionChains(driver).context_click().perform())


def middle_click() -> None:
    ensure_active_tab()
    def _do():
        action = ActionBuilder(driver)
        action.pointer_action.pointer_down(PointerInput.Button.MIDDLE)
        action.pointer_action.pointer_up(PointerInput.Button.MIDDLE)
        action.perform()
    _driver_action(_do)


def scroll_by(amount: int) -> None:
    ensure_active_tab()
    try:
        with driver_lock:
            scrollable = driver.execute_script("""
                var elem = document.elementFromPoint(arguments[0], arguments[1]);
                while (elem) {
                    var overflowY = window.getComputedStyle(elem).overflowY;
                    if (overflowY === 'auto' || overflowY === 'scroll') {
                        if (elem.scrollHeight > elem.clientHeight) {
                            return elem;
                        }
                    }
                    elem = elem.parentElement;
                }
                return null;
            """, cursor_x, cursor_y)
            if scrollable:
                driver.execute_script("arguments[0].scrollBy(0, arguments[1]);", scrollable, amount)
            else:
                driver.execute_script(f"window.scrollBy(0, {amount});")
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"scroll_by driver error: {e}")
    except Exception as e:
        log(f"scroll_by unexpected error: {e}")


def drag_from_to(x1, y1, x2, y2) -> None:
    ensure_active_tab()
    move_cursor_absolute(x1, y1)
    left_button_down()
    time.sleep(0.1)
    move_cursor_absolute(x2, y2)
    time.sleep(0.1)
    left_button_up()


def _perform_human_click_at(x: int, y: int) -> None:
    ensure_active_tab()
    move_cursor_absolute(x, y)
    time.sleep(0.1)
    for _ in range(random.randint(1, 3)):
        dx = random.randint(-2, 2)
        dy = random.randint(-2, 2)
        move_cursor_relative(dx, dy)
        time.sleep(random.uniform(0.015, 0.040))
    left_button_down()
    time.sleep(random.uniform(0.030, 0.080))
    dx = random.randint(1, 3) * (1 if random.random() > 0.5 else -1)
    dy = random.randint(1, 3) * (1 if random.random() > 0.5 else -1)
    move_cursor_relative(dx, dy)
    time.sleep(random.uniform(0.010, 0.040))
    left_button_up()


def human_click(prompt: str = "Click the verify button") -> str:
    ensure_active_tab()
    if _try_gemini_click(prompt):
        return f"Gemini click successful (prompt: {prompt})"
    _perform_human_click_at(cursor_x, cursor_y)
    return "Fallback human click at current cursor."


def human_click_at(x: int, y: int) -> str:
    ensure_active_tab()
    move_cursor_absolute(x, y)
    time.sleep(0.1)
    if _try_gemini_click("Click the button at this position"):
        return f"Gemini click at ({x},{y})"
    _perform_human_click_at(x, y)
    return f"Human click at ({x},{y})"


KEY_MAP = {
    "enter": Keys.ENTER, "tab": Keys.TAB, "escape": Keys.ESCAPE, "esc": Keys.ESCAPE,
    "backspace": Keys.BACKSPACE, "delete": Keys.DELETE, "del": Keys.DELETE,
    "home": Keys.HOME, "end": Keys.END, "pageup": Keys.PAGE_UP, "pagedown": Keys.PAGE_DOWN,
    "arrowup": Keys.ARROW_UP, "arrowdown": Keys.ARROW_DOWN, "arrowleft": Keys.ARROW_LEFT, "arrowright": Keys.ARROW_RIGHT,
    "space": Keys.SPACE, "insert": Keys.INSERT, "f1": Keys.F1, "f2": Keys.F2, "f3": Keys.F3, "f4": Keys.F4, "f5": Keys.F5, "f6": Keys.F6,
    "f7": Keys.F7, "f8": Keys.F8, "f9": Keys.F9, "f10": Keys.F10, "f11": Keys.F11, "f12": Keys.F12,
    "ctrl": Keys.CONTROL, "shift": Keys.SHIFT, "alt": Keys.ALT, "meta": Keys.META, "command": Keys.META
}


def press_key(key_name: str) -> None:
    ensure_active_tab()
    kn = key_name.strip().lower()
    try:
        with driver_lock:
            if kn in KEY_MAP:
                ActionChains(driver).send_keys(KEY_MAP[kn]).perform()
            elif len(kn) == 1:
                ActionChains(driver).send_keys(kn).perform()
            else:
                ActionChains(driver).send_keys(key_name).perform()
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"press_key driver error: {e}")
    except Exception as e:
        log(f"press_key unexpected error: {e}")


def press_combo(combo_str: str) -> None:
    ensure_active_tab()
    parts = [p.strip() for p in combo_str.split('+')]
    if len(parts) < 2:
        press_key(combo_str)
        return
    mods = parts[:-1]
    main = parts[-1]
    try:
        with driver_lock:
            actions = ActionChains(driver)
            for m in mods:
                mk = m.lower()
                if mk in KEY_MAP:
                    actions = actions.key_down(KEY_MAP[mk])
                else:
                    actions = actions.key_down(m)
            mk_main = main.lower()
            if mk_main in KEY_MAP:
                actions = actions.send_keys(KEY_MAP[mk_main])
            else:
                actions = actions.send_keys(main)
            for m in reversed(mods):
                mk = m.lower()
                if mk in KEY_MAP:
                    actions = actions.key_up(KEY_MAP[mk])
                else:
                    actions = actions.key_up(m)
            actions.perform()
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"press_combo driver error: {e}")
    except Exception as e:
        log(f"press_combo unexpected error: {e}")


def type_secret(name: str) -> bool:
    ensure_active_tab()
    if name not in allowed_secrets:
        return False
    val = os.environ.get(name, "")
    if not val:
        return False
    try:
        with driver_lock:
            ActionChains(driver).send_keys(val).perform()
        return True
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"type_secret driver error: {e}")
        return False
    except Exception as e:
        log(f"type_secret unexpected error: {e}")
        return False


# ---------- COMMAND PARSER – percentage coordinates ----------
def _pct_to_abs(pctx: float, pcty: float):
    """Convert percentage (0.0‑1.0) to absolute pixel coordinates."""
    x = int(pctx * W)
    y = int(pcty * H)
    # Clamp to valid range (just in case)
    x = max(0, min(W - 1, x))
    y = max(0, min(H - 1, y))
    return x, y


def parse_single_command(raw: str):
    raw = raw.strip()
    lo = raw.lower()

    # Percentage‑based move and click commands
    # Format: (0.123456,0.456789)
    m = re.match(r'^\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', raw)
    if m:
        pctx = float(m.group(1))
        pcty = float(m.group(2))
        x, y = _pct_to_abs(pctx, pcty)
        return ("move", (x, y))

    # click(pctx, pcty)
    m = re.match(r'^click\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', lo)
    if m:
        pctx = float(m.group(1))
        pcty = float(m.group(2))
        x, y = _pct_to_abs(pctx, pcty)
        return ("click_at", (x, y))

    # humanclick(pctx, pcty)
    m = re.match(r'^humanclick\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', lo)
    if m:
        pctx = float(m.group(1))
        pcty = float(m.group(2))
        x, y = _pct_to_abs(pctx, pcty)
        return ("humanclick_at", (x, y))

    # doubleclick(pctx, pcty)
    m = re.match(r'^doubleclick\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', lo)
    if m:
        pctx = float(m.group(1))
        pcty = float(m.group(2))
        x, y = _pct_to_abs(pctx, pcty)
        return ("doubleclick_at", (x, y))

    # rightclick(pctx, pcty)
    m = re.match(r'^rightclick\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', lo)
    if m:
        pctx = float(m.group(1))
        pcty = float(m.group(2))
        x, y = _pct_to_abs(pctx, pcty)
        return ("rightclick_at", (x, y))

    # drag(pctx1, pcty1, pctx2, pcty2)
    m = re.match(r'^drag\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', lo)
    if m:
        pctx1 = float(m.group(1)); pcty1 = float(m.group(2))
        pctx2 = float(m.group(3)); pcty2 = float(m.group(4))
        x1, y1 = _pct_to_abs(pctx1, pcty1)
        x2, y2 = _pct_to_abs(pctx2, pcty2)
        return ("drag", (x1, y1, x2, y2))

    # ---------- other commands unchanged ----------
    if lo == "exit": return ("exit", None)
    if lo == "uploadtoyoutube": return ("uploadtoyoutube", None)
    if lo == "screenshot": return ("screenshot", None)
    if lo == "shoot": return ("shoot", None)
    if lo == "humanclick": return ("humanclick", None)
    if lo == "refresh": return ("refresh", None)
    if lo == "paste": return ("paste", None)
    if lo == "doubleshoot": return ("doubleshoot", None)
    if lo == "rightshoot": return ("rightshoot", None)
    if lo == "middleshoot": return ("middleshoot", None)
    if lo in ("leftdown","leftmousedown"): return ("leftdown", None)
    if lo in ("leftup","leftmouseup"): return ("leftup", None)
    if lo in ("rightdown","rightmousedown"): return ("rightdown", None)
    if lo in ("rightup","rightmouseup"): return ("rightup", None)
    if lo in ("middledown","middle mousedown"): return ("middledown", None)
    if lo in ("middleup","middle mouseup"): return ("middleup", None)
    if lo == "save": return ("save", None)
    if lo == "filedrop": return ("filedrop", None)
    if lo == "downselected": return ("downselected", None)
    if lo == "deleteselected": return ("deleteselected", None)
    if lo.startswith("moveby"):  # moveby is obsolete but keep for now? no, remove
        return ("key", raw)
    if lo.startswith("scroll:"):
        try:
            val = int(float(lo.split(":",1)[1].strip()))
            return ("scroll", val)
        except:
            return ("key", raw)
    if lo.startswith("wait:"):
        try:
            val = float(lo.split(":",1)[1].strip())
            return ("wait", val)
        except:
            return ("key", raw)
    if lo.startswith("key:"):
        return ("key", raw.split(":",1)[1].strip())
    if lo.startswith("combo:"):
        return ("combo", raw.split(":",1)[1].strip())
    if lo.startswith('secret:'): return ("secret", raw.split(':',1)[1].strip())
    if lo.startswith('decode:'): return ("decode", raw.split(':',1)[1].strip())
    if lo.startswith('humantype:'): return ("humantype", raw.split(':',1)[1].strip())
    if lo.startswith("navigate:"):
        return ("navigate", raw.split(":",1)[1].strip())
    if lo in ("download","download:"): return ("download", None)
    if lo in ("upload","upload:"): return ("upload", None)
    if lo == "dir": return ("dir", None)
    if lo == "tabs": return ("tabs", None)
    if lo.startswith("tabnumber:"): return ("tabnumber", raw.split(":",1)[1].strip())
    if lo.startswith("closetab:"): return ("closetab", raw.split(":",1)[1].strip())
    if lo == "lastdownload": return ("lastdownload", None)
    if lo.startswith("uploadnumber:"): return ("uploadnumber", raw.split(":",1)[1].strip())
    if lo == "savestate": return ("savestate", None)
    if lo.startswith("setinterval:"):
        try:
            val = float(lo.split(":",1)[1].strip())
            return ("setinterval", val)
        except:
            return ("key", raw)
    if lo.startswith("zoom:"):
        return ("zoom", raw.split(":",1)[1].strip())

    return ("key", raw)


# ---------- FILE REGISTRY & UPLOAD PATHS ----------
_file_registry = {}
_previous_file_set = set()
_upload_file_paths = []
_last_reported_files_str = None

DOWNLOAD_DIR = ""   # set by main

def refresh_file_registry():
    global _file_registry, _previous_file_set, _last_reported_files_str
    try:
        files = sorted([f for f in os.listdir(DOWNLOAD_DIR) if not f.endswith(".crdownload")])
        new_set = set(files)
        new_files = new_set - _previous_file_set
        for nf in new_files:
            add_autonomous_report("filedownloaded", f"New file: {nf}")
        _previous_file_set = new_set

        _file_registry.clear()
        for i, fname in enumerate(files, start=1):
            _file_registry[i] = fname

        if _file_registry:
            lines = [f"{fid}: {fname}" for fid, fname in sorted(_file_registry.items())]
            current_str = "Files: " + " | ".join(lines)
        else:
            current_str = "Files: (empty)"

        if current_str != _last_reported_files_str:
            _last_reported_files_str = current_str
            add_autonomous_report("files", current_str)

    except Exception as e:
        try: log(f"ERROR refreshing file registry: {e}")
        except: pass


def get_upload_paths():
    paths = []
    for fname in _upload_file_paths:
        paths.append(os.path.join(DOWNLOAD_DIR, fname))
    if paths: return [paths[0]]
    return []


# ---------- TAB HANDLE TRACKING ----------
_known_handles = set()

def refresh_known_handles():
    """
    Detect new window handles and report the full tab list as an autonomous report.
    """
    global _known_handles
    try:
        with driver_lock:
            handles = list(driver.window_handles)
            new_handles = set(handles) - _known_handles
            for h in new_handles:
                add_autonomous_report("tabopened", f"New tab/window handle: {h}")
            _known_handles = set(handles)

            tab_lines = []
            for i, h in enumerate(handles):
                try:
                    driver.switch_to.window(h)
                    title = (driver.title or "Untitled")[:60]
                except Exception:
                    title = "(error)"
                tab_lines.append(f"{i+1}: {title}")
            idx = ACTIVE_TAB_INDEX - 1
            if 0 <= idx < len(handles):
                driver.switch_to.window(handles[idx])
                try: driver.set_window_size(W, H)
                except: pass
            else:
                driver.switch_to.window(handles[0])
            tab_report = "Tabs: " + " | ".join(tab_lines)
            add_autonomous_report("tabs", tab_report)
    except (WebDriverException, InvalidSessionIdException) as e:
        log(f"refresh_known_handles driver error: {e}")
    except Exception as e:
        log(f"refresh_known_handles unexpected error: {e}")


# ---------- URL MONITOR ----------
_last_known_url = ""
_url_monitor_stop = threading.Event()

def url_monitor_worker():
    global _last_known_url
    time.sleep(3)
    while not _url_monitor_stop.is_set():
        try:
            with driver_lock:
                cur = driver.current_url
            if cur and cur != _last_known_url:
                _last_known_url = cur
                add_autonomous_report("navigate", f"navigate({cur})")
        except (WebDriverException, InvalidSessionIdException) as e:
            log(f"url_monitor driver error: {e}")
        except Exception as e:
            log(f"url_monitor unexpected error: {e}")
        _url_monitor_stop.wait(2)


# ---------- AUTONOMOUS REPORTS ----------
autonomous_counter = 1
pending_autonomous_reports = []
AUTONOMOUS_TIMEOUT = 60

def add_autonomous_report(report_type, text):
    global autonomous_counter
    now = int(time.time())
    aut_id = f"AUT-{autonomous_counter}-{now}"
    autonomous_counter += 1
    pending_autonomous_reports.append({"id":aut_id, "text":text, "timestamp":time.time()})
    try: log(f"New autonomous report: {aut_id} -> {text}")
    except: pass


def cull_expired_autonomous_reports():
    now = time.time()
    before = len(pending_autonomous_reports)
    pending_autonomous_reports[:] = [r for r in pending_autonomous_reports if now - r["timestamp"] < AUTONOMOUS_TIMEOUT]
    if before > len(pending_autonomous_reports):
        try: log(f"Culled {before - len(pending_autonomous_reports)} expired autonomous reports.")
        except: pass