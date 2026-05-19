#!/usr/bin/env python3
# ==============================================================================
# agent_loops.py – Version 1.0.0
#   - Contains the four main agent loops, accepting globals as parameters.
#   - Used by command_mouse_keyboard.py to keep that file slim.
# ==============================================================================

import time, re, hashlib, json, threading, traceback, queue as queue_module
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from selenium.common.exceptions import WebDriverException, InvalidSessionIdException

from command_handlers import execute_one_command
from comments import find_marker_comment
from profile_cache import save_profile
from agent_state import (
    parse_single_command, move_cursor_absolute, update_viewport, probe_clickable_bounds,
    refresh_file_registry, get_upload_paths, refresh_known_handles,
    add_autonomous_report, cull_expired_autonomous_reports,
    _file_registry, _upload_file_paths,
    HAS_GEMINI, HAS_PYPERCLIP, pyperclip, allowed_secrets,
    human_click, human_click_at, _try_gemini_click, left_click, left_button_down, left_button_up,
    right_button_down, right_button_up, middle_button_down, middle_button_up,
    double_click, right_click, middle_click, scroll_by, drag_from_to,
    press_key, press_combo, type_secret
)
from crypto_utils import encrypt_string, decode_string


# ---------- Fetcher Loop ----------
def fetcher_loop(
    app_cmd_id, last_app_body_hash,
    sync_repo, COMM_INTERVAL, slow_mode,
    execution_queue, heavy_execution_queue,
    save_lock, upload_lock,
    report_history, _report_history_lock,
    save_report_history, add_to_report_queue,
    report_queue, remove_from_report_queue,
    safe_log, push_logs,
    _fetcher_stop
):
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

                    if ctext.startswith("ack:"):
                        aut_id = ctext.split(":",1)[1].strip()
                        remove_from_report_queue(aut_id)
                        safe_log(f"Acknowledged autonomous report {aut_id}")
                        continue

                    cmd_type, _ = parse_single_command(ctext)

                    # Route to heavy or light queue
                    if cmd_type in ("save", "upload", "downselected"):
                        target_queue = heavy_execution_queue
                    else:
                        target_queue = execution_queue

                    # Reject duplicate save/upload while in progress
                    if cmd_type == "save" and save_lock.locked():
                        ts = int(time.time() * 1_000_000)
                        seq_num = 0
                        if cid.startswith("APP-"):
                            parts_cid = cid.split('-')
                            if len(parts_cid) >= 2:
                                try: seq_num = int(parts_cid[1])
                                except: pass
                        error_result = "ERR save already in progress"
                        with _report_history_lock:
                            report_history[cid] = (ts, seq_num, error_result)
                        save_report_history()
                        add_to_report_queue(cid, ts, seq_num, error_result)
                        safe_log(f"Rejected duplicate save command {cid}")
                        continue
                    if cmd_type in ("upload", "uploadtoyoutube") and upload_lock.locked():
                        ts = int(time.time() * 1_000_000)
                        seq_num = 0
                        if cid.startswith("APP-"):
                            parts_cid = cid.split('-')
                            if len(parts_cid) >= 2:
                                try: seq_num = int(parts_cid[1])
                                except: pass
                        error_result = "ERR upload already in progress"
                        with _report_history_lock:
                            report_history[cid] = (ts, seq_num, error_result)
                        save_report_history()
                        add_to_report_queue(cid, ts, seq_num, error_result)
                        safe_log(f"Rejected duplicate upload command {cid}")
                        continue

                    # Check for existing report in history
                    with _report_history_lock:
                        existing = report_history.get(cid)
                    if existing:
                        ts, seq, res = existing
                        add_to_report_queue(cid, ts, seq, res)
                        safe_log(f"Re‑enqueued existing report for {cid}")
                        continue

                    # Try to add to the appropriate queue; if full, generate error immediately
                    try:
                        target_queue.put_nowait((cid, ctext))
                        safe_log(f"Enqueued for execution: {cid}")
                    except queue_module.Full:
                        ts = int(time.time() * 1_000_000)
                        seq_num = 0
                        if cid.startswith("APP-"):
                            parts_cid = cid.split('-')
                            if len(parts_cid) >= 2:
                                try: seq_num = int(parts_cid[1])
                                except: pass
                        error_result = "ERR too many commands"
                        with _report_history_lock:
                            report_history[cid] = (ts, seq_num, error_result)
                        save_report_history()
                        add_to_report_queue(cid, ts, seq_num, error_result)
                        safe_log(f"Rejected command {cid}: queue full")

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


# ---------- Light Execution Loop (10-second timeout) ----------
def executor_loop(
    execution_queue, _executor_stop, safe_log, parse_single_command, agent_state,
    driver, driver_lock, DOWNLOAD_DIR, LOG_FILENAME, KEY_SECRET, REPO, ISSUE_NUMBER,
    HAS_GEMINI, HAS_PYPERCLIP, allowed_secrets, ENCRYPTION_KEY,
    human_click, human_click_at, _try_gemini_click,
    move_cursor_absolute, move_cursor_relative, left_click, left_button_down, left_button_up,
    right_button_down, right_button_up, middle_button_down, middle_button_up,
    double_click, right_click, middle_click, scroll_by, drag_from_to,
    press_key, press_combo, type_secret, decode_string,
    refresh_file_registry, add_autonomous_report, refresh_known_handles,
    get_upload_paths, save_profile, _file_registry, _upload_file_paths,
    pyperclip, encrypt_string, sync_repo, COMM_INTERVAL, slow_mode,
    report_history, _report_history_lock, save_report_history, add_to_report_queue
):
    safe_log("Execution loop started (light, timeout=10s).")
    executor_pool = ThreadPoolExecutor(max_workers=1)
    while not _executor_stop.is_set():
        try:
            cid, ctext = execution_queue.get(timeout=1)
        except queue_module.Empty:
            continue

        safe_log(f"Execution loop: processing {cid}: {ctext}")

        cmd_type, _ = parse_single_command(ctext)
        timeout = 10

        def run_command():
            try:
                cmd_type_final, arg = parse_single_command(ctext)
                return execute_one_command(
                    cmd_type_final, arg,
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
                    ss=None,
                    refresh_file_registry=refresh_file_registry,
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
            except Exception as e:
                return f"ERR {e}"

        try:
            future = executor_pool.submit(run_command)
            result = future.result(timeout=timeout)
        except FutureTimeoutError:
            result = f"ERR timeout: command exceeded {timeout} seconds"
            safe_log(f"Command {cid} timed out.")
        except Exception as ex:
            result = f"ERR unexpected: {ex}"
            safe_log(f"Command {cid} crashed: {ex}")

        # After a successful navigate or tab switch, center cursor
        is_navigate = cmd_type == "navigate"
        is_tabswitch = cmd_type in ("tabnumber", "closetab")
        if is_navigate and result.startswith("OK navigate"):
            safe_log("Navigation completed – probing clickable bounds...")
            update_viewport()
            probe_clickable_bounds()
            move_cursor_absolute(agent_state.W // 2, agent_state.H // 2)
        if is_tabswitch and (result.startswith("Switched") or result.startswith("Closed")):
            safe_log("Tab switched – probing clickable bounds...")
            update_viewport()
            probe_clickable_bounds()
            move_cursor_absolute(agent_state.W // 2, agent_state.H // 2)

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


# ---------- Heavy Execution Loop (300-second timeout) ----------
def heavy_executor_loop(
    heavy_execution_queue, _heavy_executor_stop, safe_log, parse_single_command, agent_state,
    driver, driver_lock, DOWNLOAD_DIR, LOG_FILENAME, KEY_SECRET, REPO, ISSUE_NUMBER,
    HAS_GEMINI, HAS_PYPERCLIP, allowed_secrets, ENCRYPTION_KEY,
    human_click, human_click_at, _try_gemini_click,
    move_cursor_absolute, move_cursor_relative, left_click, left_button_down, left_button_up,
    right_button_down, right_button_up, middle_button_down, middle_button_up,
    double_click, right_click, middle_click, scroll_by, drag_from_to,
    press_key, press_combo, type_secret, decode_string,
    refresh_file_registry, add_autonomous_report, refresh_known_handles,
    get_upload_paths, save_profile, _file_registry, _upload_file_paths,
    pyperclip, encrypt_string, sync_repo, COMM_INTERVAL, slow_mode,
    report_history, _report_history_lock, save_report_history, add_to_report_queue,
    CACHE_DIR, PROFILE_DIR, ENCRYPTION_KEY_PROFILE, REPO, PAT, CHUNK_SIZE_MB
):
    safe_log("Heavy execution loop started (timeout=300s).")
    executor_pool = ThreadPoolExecutor(max_workers=1)
    while not _heavy_executor_stop.is_set():
        try:
            cid, ctext = heavy_execution_queue.get(timeout=1)
        except queue_module.Empty:
            continue

        safe_log(f"Heavy execution loop: processing {cid}: {ctext}")

        cmd_type, _ = parse_single_command(ctext)
        timeout = 300

        def run_command():
            if cmd_type == "save":
                save_lock_local = threading.Lock()   # not used; save_lock is passed and handled in fetcher
                save_lock_local.acquire()
            elif cmd_type in ("upload", "uploadtoyoutube"):
                upload_lock_local = threading.Lock()
                upload_lock_local.acquire()
            try:
                if cmd_type == "save":
                    ok, msg = save_profile(CACHE_DIR, PROFILE_DIR, ENCRYPTION_KEY_PROFILE, REPO, PAT, CHUNK_SIZE_MB)
                    return f"OK save: {msg}" if ok else f"ERR save: {msg}"

                cmd_type_final, arg = parse_single_command(ctext)
                return execute_one_command(
                    cmd_type_final, arg,
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
                    ss=None,
                    refresh_file_registry=refresh_file_registry,
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
            finally:
                if cmd_type == "save":
                    save_lock_local.release()
                elif cmd_type in ("upload", "uploadtoyoutube"):
                    upload_lock_local.release()

        try:
            future = executor_pool.submit(run_command)
            result = future.result(timeout=timeout)
        except FutureTimeoutError:
            result = f"ERR timeout: command exceeded {timeout} seconds"
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
            _heavy_executor_stop.set()
            break

    executor_pool.shutdown(wait=False)


# ---------- Sender Loop ----------
def sender_loop(
    sync_repo, COMM_INTERVAL, slow_mode,
    report_queue, _report_queue_lock,
    cull_timed_out_reports, get_report_queue_snapshot,
    add_autonomous_report, remove_from_report_queue,
    safe_log, push_logs, _sender_stop
):
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
            for key, (ts, seq, res) in snapshot.items():
                if not key.startswith("AUT-"):
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