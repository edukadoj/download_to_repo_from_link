#!/usr/bin/env python3
# ==============================================================================
# repo_wrapper.py – Version 1.0.1
#   - Uses only the PAT environment variable (from the GH_TOKEN secret).
#   - No fallback to GITHUB_TOKEN; if PAT is missing, calls will fail.
# ==============================================================================

import os, time, subprocess, json, re, glob, threading, queue as queue_module
from typing import Any, Callable, Dict, List, Optional
import urllib.request

class RepoWrapper:
    def __init__(self, repo: str, issue_number: int,
                 log_filename: str = "logs/command_mouse_keyboard.log",
                 screenshots_dir: str = "screenshots"):
        self.repo = repo
        self.issue_number = issue_number
        self.log_filename = log_filename
        self.screenshots_dir = screenshots_dir

        # Read the PAT once (must be set in the environment)
        self._pat = os.environ.get("PAT")
        if not self._pat:
            # If not set, we still can't use GITHUB_TOKEN – we'll rely on gh having it,
            # but our own download calls will fail. We'll keep it None and handle later.
            pass

        self._queue: queue_module.Queue = queue_module.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self.report_callback: Optional[Callable[[str, str], None]] = None

    # ---------- Public API ----------
    def edit_comment(self, comment_id: str, new_body: str) -> None:
        self._queue.put(("edit_comment", (comment_id, new_body), None))

    def create_comment(self, body: str) -> None:
        self._queue.put(("create_comment", (body,), None))

    def post_comment_and_get_id(self, body: str, callback: Callable[[str], None]) -> None:
        self._queue.put(("create_comment_callback", (body,), callback))

    def delete_comment(self, comment_id: str) -> None:
        self._queue.put(("delete_comment", (comment_id,), None))

    def get_comment_body(self, comment_id: str, callback: Callable[[str], None]) -> None:
        self._queue.put(("get_comment_body", (comment_id,), callback))

    def get_all_comments(self, callback: Callable[[List[Dict[str, str]]], None]) -> None:
        self._queue.put(("get_all_comments", (), callback))

    def push_log_file(self) -> None:
        self._queue.put(("push_log_file", (), None))

    def push_screenshots(self, screenshot_paths: List[str]) -> None:
        self._queue.put(("push_screenshots", (screenshot_paths,), None))

    def list_screenshot_files(self, callback: Callable[[List[str]], None]) -> None:
        self._queue.put(("list_screenshot_files", (), callback))

    def download_file(self, path: str, callback: Callable[[bytes], None]) -> None:
        self._queue.put(("download_file", (path,), callback))

    def delete_file(self, path: str, sha: str) -> None:
        self._queue.put(("delete_file", (path, sha), None))

    def report_memory(self) -> None:
        self._queue.put(("report_memory", (), None))

    def comment_exists(self, comment_id: str, callback: Callable[[bool], None]) -> None:
        self._queue.put(("comment_exists", (comment_id,), callback))

    def stop(self) -> None:
        self._stop.set()

    # ---------- Worker thread ----------
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=1)
            except queue_module.Empty:
                continue
            if task is None:
                continue
            action, args, callback = task
            try:
                if action == "edit_comment":
                    self._edit_comment(*args)
                elif action == "create_comment":
                    self._create_comment(*args)
                elif action == "create_comment_callback":
                    cid = self._create_comment(args[0])
                    if callback:
                        callback(cid)
                elif action == "delete_comment":
                    self._delete_comment(*args)
                elif action == "get_comment_body":
                    body = self._get_comment_body(*args)
                    if callback:
                        callback(body)
                elif action == "get_all_comments":
                    comments = self._get_all_comments()
                    if callback:
                        callback(comments)
                elif action == "push_log_file":
                    self._push_log_file()
                elif action == "push_screenshots":
                    self._push_screenshots(*args)
                elif action == "list_screenshot_files":
                    files = self._list_screenshot_files()
                    if callback:
                        callback(files)
                elif action == "download_file":
                    data = self._download_file(*args)
                    if callback:
                        callback(data)
                elif action == "delete_file":
                    self._delete_file(*args)
                elif action == "report_memory":
                    self._report_memory()
                elif action == "comment_exists":
                    exists = self._comment_exists(*args)
                    if callback:
                        callback(exists)
            except Exception:
                pass

    # ---------- Internal implementations ----------
    def _gh(self, *args: str, input_data: Optional[str] = None, **kwargs: Any) -> str:
        """Run `gh api` using the PAT env var exclusively."""
        env = os.environ.copy()
        if self._pat:
            env["GITHUB_TOKEN"] = self._pat   # gh uses GITHUB_TOKEN env var
        # If PAT is missing, we rely on gh's own fallback, but that's undesired.
        cmd = ["gh", "api"] + list(args)
        res = subprocess.run(cmd, capture_output=True, text=True, check=True,
                             input=input_data, env=env, **kwargs)
        return res.stdout.strip()

    def _git(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        lock = ".git/index.lock"
        if os.path.exists(lock):
            try: os.remove(lock)
            except Exception: pass
        return subprocess.run(["git"] + list(args), **kwargs)

    def _git_push_with_retry(self) -> bool:
        for attempt in range(3):
            try:
                self._git("push", check=True, capture_output=True, text=True)
                return True
            except subprocess.CalledProcessError:
                if attempt < 2:
                    time.sleep(2)
                    try: self._git("pull", "--rebase", check=True, capture_output=True)
                    except Exception: pass
        return False

    def _edit_comment(self, comment_id: str, new_body: str) -> None:
        for _ in range(2):
            try:
                self._gh(f"repos/{self.repo}/issues/comments/{comment_id}",
                         "--method", "PATCH", "--input", "-",
                         input_data=json.dumps({"body": new_body}))
                return
            except subprocess.CalledProcessError:
                time.sleep(2)

    def _create_comment(self, body: str) -> str:
        return self._gh(f"repos/{self.repo}/issues/{self.issue_number}/comments",
                        "--method", "POST", "-f", f"body={body}", "--jq", ".id")

    def _delete_comment(self, comment_id: str) -> None:
        try:
            self._gh(f"repos/{self.repo}/issues/comments/{comment_id}", "--method", "DELETE")
        except subprocess.CalledProcessError:
            pass

    def _get_comment_body(self, comment_id: str) -> str:
        return self._gh(f"repos/{self.repo}/issues/comments/{comment_id}", "--jq", ".body")

    def _get_all_comments(self) -> List[Dict[str, str]]:
        raw = self._gh(f"repos/{self.repo}/issues/{self.issue_number}/comments",
                       "--jq", ".[] | {id: .id, body: .body, user_type: .user.type}",
                       "--paginate")
        if not raw.strip():
            return []
        comments: List[Dict[str, str]] = []
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(raw):
            while idx < len(raw) and raw[idx].isspace():
                idx += 1
            if idx >= len(raw):
                break
            try:
                obj, end = decoder.raw_decode(raw, idx)
                comments.append({"id": str(obj.get("id", "")),
                                 "body": obj.get("body", ""),
                                 "user_type": obj.get("user_type", "")})
                idx = end
            except json.JSONDecodeError:
                idx += 1
        return comments

    def _push_log_file(self) -> None:
        self._push_file_to_repo(self.log_filename, "Log update")

    def _push_screenshots(self, paths: List[str]) -> None:
        self._git("stash", "--include-untracked", capture_output=True)
        try:
            self._git("pull", "--rebase", check=True, capture_output=True)
        except Exception:
            pass
        self._git("stash", "pop", capture_output=True)

        for p in paths:
            if os.path.exists(p):
                self._git("add", p, check=True, capture_output=True)
        if os.path.exists(self.log_filename):
            self._git("add", self.log_filename, check=True, capture_output=True)

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode != 0:
            self._git("commit", "-m", "Screenshots & log", check=True, capture_output=True)
            self._git_push_with_retry()
            if paths:
                self._purge_old_screenshots(paths[0])

    def _push_file_to_repo(self, path: str, commit_msg: str) -> None:
        self._git("add", path, check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode != 0:
            self._git("commit", "-m", commit_msg, check=True, capture_output=True)
            self._git_push_with_retry()

    def _list_screenshot_files(self) -> List[str]:
        try:
            raw = self._gh(f"repos/{self.repo}/contents/{self.screenshots_dir}", "--jq", ".[].path")
            if not raw:
                return []
            return [p.strip().strip('"') for p in raw.splitlines() if p.strip().endswith(".png")]
        except Exception:
            return []

    def _download_file(self, path: str) -> bytes:
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.3.raw")
        # Use only the PAT token
        req.add_header("Authorization", f"Bearer {self._pat or ''}")
        resp = urllib.request.urlopen(req)
        return resp.read()

    def _delete_file(self, path: str, sha: str) -> None:
        try:
            self._gh(f"repos/{self.repo}/contents/{path}",
                     "--method", "DELETE",
                     "-f", "message=cleanup",
                     "-f", f"sha={sha}",
                     "-f", "branch=main")
        except Exception:
            pass

    def _comment_exists(self, comment_id: str) -> bool:
        try:
            self._gh(f"repos/{self.repo}/issues/comments/{comment_id}", "--jq", ".id")
            return True
        except subprocess.CalledProcessError:
            return False

    def _report_memory(self) -> None:
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        mb = kb // 1024
                        msg = f"Available memory: {mb} MB"
                        if self.report_callback:
                            self.report_callback("memory", msg)
                        break
        except Exception:
            pass

    def _purge_old_screenshots(self, keep_path: str) -> None:
        try:
            files = self._list_screenshot_files()
            for path in files:
                if path == keep_path or not path.endswith(".png"):
                    continue
                sha_raw = self._gh(f"repos/{self.repo}/contents/{path}", "--jq", ".sha")
                sha = sha_raw.strip().strip('"') if sha_raw else None
                if sha:
                    self._delete_file(path, sha)
        except Exception:
            pass