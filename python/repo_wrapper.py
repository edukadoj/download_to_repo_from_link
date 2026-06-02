# python/repo_wrapper.py (full updated content)
#!/usr/bin/env python3
# ==============================================================================
# repo_wrapper.py – Version 2.3.4
#   - Slow worker now always invokes callbacks on error to prevent hang.
# ==============================================================================

import os, time, base64, json, hashlib, threading, queue as queue_module, subprocess, tempfile, shutil, re
from typing import Any, Callable, Dict, List, Optional


class RepoWrapper:
    def __init__(self, repo: str, issue_number: int,
                 log_filename: str = "logs/command_mouse_keyboard.log",
                 screenshots_dir: str = "screenshots",
                 max_screenshots: int = 5):
        self.repo = repo
        self.issue_number = issue_number
        self.log_filename = log_filename
        self.screenshots_dir = screenshots_dir
        self.max_screenshots = max_screenshots

        self._pat = os.environ.get("PAT", "")
        self._branch = "main"

        # ── Lock serialising EVERY Git write ──────────────────────
        self._write_lock = threading.Lock()

        # ── Fast queue for comment operations (unchanged) ──
        self._fast_queue: queue_module.Queue = queue_module.Queue()
        self._fast_stop = threading.Event()
        self._fast_worker = threading.Thread(target=self._fast_worker_loop, daemon=True)
        self._fast_worker.start()

        # ── Single slow queue for HEAVY file operations (serial) ──
        self._slow_queue: queue_module.Queue = queue_module.Queue()
        self._slow_stop = threading.Event()
        self._slow_worker = threading.Thread(target=self._slow_worker_loop, daemon=True)
        self._slow_worker.start()

        self.report_callback: Optional[Callable[[str, str], None]] = None
        self.error_log: Optional[Callable[[str], None]] = None

    # ── Public API (unchanged) ────────────────────────────────────
    def edit_comment(self, comment_id: str, new_body: str) -> None:
        self._fast_queue.put(("edit_comment", (comment_id, new_body), None))

    def create_comment(self, body: str) -> None:
        self._fast_queue.put(("create_comment", (body,), None))

    def post_comment_and_get_id(self, body: str, callback: Callable[[str], None]) -> None:
        self._fast_queue.put(("create_comment_callback", (body,), callback))

    def delete_comment(self, comment_id: str) -> None:
        self._fast_queue.put(("delete_comment", (comment_id,), None))

    def get_comment_body(self, comment_id: str, callback: Callable[[str], None]) -> None:
        self._fast_queue.put(("get_comment_body", (comment_id,), callback))

    def get_all_comments(self, callback: Callable[[List[Dict[str, str]]], None]) -> None:
        self._fast_queue.put(("get_all_comments", (), callback))

    def comment_exists(self, comment_id: str, callback: Callable[[bool], None]) -> None:
        self._fast_queue.put(("comment_exists", (comment_id,), callback))

    def report_memory(self) -> None:
        self._fast_queue.put(("report_memory", (), None))

    def upload_file(self, rel_path: str, content: bytes, sha: Optional[str] = None,
                     callback: Optional[Callable[[bool], None]] = None) -> None:
        self._slow_queue.put(("upload_file", (rel_path, content, sha), callback))

    def upload_file_now(self, rel_path: str, content: bytes, sha: Optional[str] = None,
                        timeout: int = 300) -> bool:
        result = [False]
        event = threading.Event()
        def callback(ok):
            result[0] = ok
            event.set()
        self._slow_queue.put(("upload_file", (rel_path, content, sha), callback))
        if not event.wait(timeout):
            if self.error_log:
                self.error_log("upload_file_now timed out")
            return False
        return result[0]

    def download_file(self, rel_path: str, callback: Callable[[bytes], None]) -> None:
        self._slow_queue.put(("download_file", (rel_path,), callback))

    def delete_file(self, rel_path: str, sha: str) -> None:
        self._slow_queue.put(("delete_file", (rel_path, sha), None))

    def delete_file_by_name(self, rel_path: str) -> None:
        self._slow_queue.put(("delete_file_by_name", (rel_path,), None))

    def list_directory(self, dir_path: str, callback: Callable[[List[tuple]], None]) -> None:
        self._slow_queue.put(("list_directory", (dir_path,), callback))

    # ── Synchronous helpers ──────────────────────────────────────
    def upload_screenshot_sync(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                content = f.read()

            sha: Optional[str] = None
            for attempt in range(2):
                try:
                    ok = self._upload_file_api(path, content, sha)
                    if ok:
                        return True
                except subprocess.CalledProcessError as e:
                    err = e.stderr or e.output or ""
                    if "409" in err or "conflict" in err.lower():
                        sha = self._get_file_sha(path)
                        continue
                    raise
                break
            return False
        except Exception as e:
            if self.error_log:
                self.error_log(f"upload_screenshot_sync error ({path}): {e}")
            return False

    def delete_file_by_name_sync(self, rel_path: str) -> bool:
        sha = self._get_file_sha(rel_path)
        if sha is None:
            if self.error_log:
                self.error_log(f"Cannot delete {rel_path}: file not found in repo.")
            return False
        return self._delete_file_via_contents_api(rel_path, sha)

    def request_screenshot_purge(self) -> None:
        self._slow_queue.put(("purge_old_screenshots", (), None))

    def purge_old_screenshots_now(self) -> None:
        """Direct call from screenshot worker – does not use slow queue."""
        self._purge_old_screenshots()

    def push_log_file(self) -> None:
        self._slow_queue.put(("push_log_file", (), None))

    def push_screenshots(self, paths: List[str]) -> None:
        pass

    def push_screenshots_now(self, paths: List[str], timeout: int = 300) -> bool:
        if not paths:
            return False
        return self.upload_screenshot_sync(paths[0])

    # ── NEW: Direct per‑chunk upload (atomic, retries on fast‑forward) ──
    def upload_file_direct(self, rel_path: str, content: bytes,
                           max_retries: int = 20) -> bool:
        """
        Upload a single file via the Git data API, holding the write lock
        and re‑fetching the latest commit on every retry to avoid 422 errors.
        Returns True on success.
        """
        with self._write_lock:
            for attempt in range(1, max_retries + 1):
                try:
                    # 1. Fetch the latest commit SHA and its tree SHA
                    ref_resp = self._gh_api(f"repos/{self.repo}/git/ref/heads/{self._branch}",
                                            description="Get ref for upload_file_direct")
                    ref_data = json.loads(ref_resp)
                    latest_commit_sha = ref_data["object"]["sha"]

                    commit_resp = self._gh_api(f"repos/{self.repo}/git/commits/{latest_commit_sha}",
                                               description="Get commit for upload_file_direct")
                    commit_data = json.loads(commit_resp)
                    base_tree_sha = commit_data["tree"]["sha"]

                    # 2. Create a blob for the file
                    b64_content = base64.b64encode(content).decode("utf-8")
                    blob_body = json.dumps({"content": b64_content, "encoding": "base64"}).encode("utf-8")
                    blob_resp = self._gh_api(
                        f"repos/{self.repo}/git/blobs",
                        "--method", "POST", "--input", "-",
                        input_data=blob_body,
                        description=f"Create blob for {rel_path}"
                    )
                    blob_sha = json.loads(blob_resp)["sha"]

                    # 3. Create a new tree with the base tree + our file
                    tree_body = json.dumps({
                        "base_tree": base_tree_sha,
                        "tree": [
                            {
                                "path": rel_path,
                                "mode": "100644",
                                "type": "blob",
                                "sha": blob_sha
                            }
                        ]
                    }).encode("utf-8")
                    tree_resp = self._gh_api(
                        f"repos/{self.repo}/git/trees",
                        "--method", "POST", "--input", "-",
                        input_data=tree_body,
                        description=f"Create tree for {rel_path}"
                    )
                    new_tree_sha = json.loads(tree_resp)["sha"]

                    # 4. Create a commit
                    commit_body = json.dumps({
                        "message": f"Agent download: chunk {os.path.basename(rel_path)}",
                        "tree": new_tree_sha,
                        "parents": [latest_commit_sha]
                    }).encode("utf-8")
                    commit_resp = self._gh_api(
                        f"repos/{self.repo}/git/commits",
                        "--method", "POST", "--input", "-",
                        input_data=commit_body,
                        description=f"Create commit for {rel_path}"
                    )
                    new_commit_sha = json.loads(commit_resp)["sha"]

                    # 5. Update the branch reference (force=False)
                    ref_body = json.dumps({
                        "sha": new_commit_sha,
                        "force": False
                    }).encode("utf-8")
                    self._gh_api(
                        f"repos/{self.repo}/git/refs/heads/{self._branch}",
                        "--method", "PATCH", "--input", "-",
                        input_data=ref_body,
                        description=f"Update ref for {rel_path}"
                    )

                    # 6. Verify remote size (optional, skip to speed up)
                    return True

                except subprocess.CalledProcessError as e:
                    err = e.stderr or e.output or ""
                    if ("422" in err or "not a fast forward" in err.lower()) and attempt < max_retries:
                        time.sleep(1)
                        continue
                    if self.error_log:
                        self.error_log(f"upload_file_direct FAILED ({rel_path}): {err}")
                    return False

            return False

    # ── Deletion via GitHub Contents API ─────────────────────────
    def _delete_file_via_contents_api(self, rel_path: str, sha: str) -> bool:
        with self._write_lock:
            # Single attempt – if the file is already gone (404) that's a success.
            try:
                encoded_path = "/".join(Uri.EscapeDataString(seg) for seg in rel_path.split("/"))
                url = f"repos/{self.repo}/contents/{encoded_path}"
                body = json.dumps({
                    "message": f"sync: delete {rel_path}",
                    "sha": sha,
                    "branch": self._branch
                }).encode("utf-8")
                args = [url, "--method", "DELETE", "--input", "-"]
                self._gh_api(*args, input_data=body,
                             description=f"Delete {rel_path}",
                             max_retries=1)   # no retry for delete
                return True
            except subprocess.CalledProcessError as e:
                err = e.stderr or e.output or ""
                if "404" in err or "Not Found" in err:
                    # Already deleted – success
                    return True
                if self.error_log:
                    self.error_log(f"_delete_file_via_contents_api FAILED ({rel_path}): {err}")
                return False

    # ── gh api helper ────────────────────────────────────────────
    def _gh_api(self, *args: str, input_data: Optional[bytes] = None,
                env_extra: Optional[Dict[str, str]] = None,
                timeout: int = 30,
                max_retries: int = 20,
                description: str = "") -> str:
        env = os.environ.copy()
        if self._pat:
            env["GITHUB_TOKEN"] = self._pat
        if env_extra:
            env.update(env_extra)

        cmd = ["gh", "api"] + list(args)

        stdin_str: Optional[str] = None
        if input_data is not None:
            stdin_str = input_data.decode("utf-8") if isinstance(input_data, bytes) else input_data

        for attempt in range(1, max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    input=stdin_str,
                    env=env,
                    timeout=timeout
                )
                if proc.returncode == 0:
                    return proc.stdout.strip()

                err_detail = proc.stderr.strip() if proc.stderr else "unknown error"
                msg = f"gh api failed: {description} (attempt {attempt}/{max_retries}): {err_detail}"
                if self.error_log:
                    self.error_log(msg)

                if attempt < max_retries:
                    time.sleep(1)
                else:
                    raise subprocess.CalledProcessError(proc.returncode, cmd,
                                                        output=proc.stdout, stderr=proc.stderr)
            except subprocess.TimeoutExpired:
                if attempt < max_retries:
                    time.sleep(1)
                else:
                    raise subprocess.CalledProcessError(-1, cmd, output="timeout", stderr="timeout")

        return ""

    # ── Universal upload function (locked wrapper) ────────────────
    def _upload_file_api(self, rel_path: str, content: bytes, sha: Optional[str]) -> bool:
        with self._write_lock:
            return self._upload_file_api_locked(rel_path, content, sha)

    def _upload_file_api_locked(self, rel_path: str, content: bytes, sha: Optional[str]) -> bool:
        if len(content) < 1_000_000:
            return self._upload_via_contents_api(rel_path, content, sha)
        else:
            return self._upload_via_git_data_api(rel_path, content)

    def _upload_via_contents_api(self, rel_path: str, content: bytes, sha: Optional[str]) -> bool:
        encoded = base64.b64encode(content).decode("utf-8")
        body = {
            "message": f"sync: update {rel_path}",
            "content": encoded,
            "branch": self._branch,
        }
        if sha:
            body["sha"] = sha

        json_data = json.dumps(body).encode("utf-8")
        encoded_path = "/".join(Uri.EscapeDataString(seg) for seg in rel_path.split("/"))
        endpoint = f"repos/{self.repo}/contents/{encoded_path}"
        args = [endpoint, "--method", "PUT", "--input", "-"]
        try:
            self._gh_api(*args, input_data=json_data,
                         description=f"Upload {rel_path} ({len(content)} bytes)")
            if self._verify_remote_size(rel_path, len(content)):
                return True
            if self.error_log:
                self.error_log(f"_upload_via_contents_api: size mismatch for {rel_path}")
            return False
        except subprocess.CalledProcessError as e:
            if self.error_log:
                self.error_log(f"_upload_via_contents_api FAILED ({rel_path}): {e.stderr or e.output}")
            return False

    def _upload_via_git_data_api(self, rel_path: str, content: bytes) -> bool:
        for attempt in range(1, 4):
            try:
                encoded = base64.b64encode(content).decode("utf-8")
                blob_body = json.dumps({"content": encoded, "encoding": "base64"}).encode("utf-8")
                blob_resp = self._gh_api(
                    f"repos/{self.repo}/git/blobs",
                    "--method", "POST", "--input", "-",
                    input_data=blob_body,
                    description=f"Create blob for {rel_path}"
                )
                blob_sha = json.loads(blob_resp)["sha"]

                ref_resp = self._gh_api(
                    f"repos/{self.repo}/git/ref/heads/{self._branch}",
                    description="Get current ref"
                )
                ref_json = json.loads(ref_resp)
                base_tree_sha = ref_json["object"]["sha"]

                tree_body = json.dumps({
                    "base_tree": base_tree_sha,
                    "tree": [
                        {
                            "path": rel_path,
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob_sha
                        }
                    ]
                }).encode("utf-8")
                tree_resp = self._gh_api(
                    f"repos/{self.repo}/git/trees",
                    "--method", "POST", "--input", "-",
                    input_data=tree_body,
                    description=f"Create tree for {rel_path}"
                )
                new_tree_sha = json.loads(tree_resp)["sha"]

                commit_body = json.dumps({
                    "message": f"sync: update {rel_path}",
                    "tree": new_tree_sha,
                    "parents": [ref_json["object"]["sha"]]
                }).encode("utf-8")
                commit_resp = self._gh_api(
                    f"repos/{self.repo}/git/commits",
                    "--method", "POST", "--input", "-",
                    input_data=commit_body,
                    description=f"Create commit for {rel_path}"
                )
                commit_sha = json.loads(commit_resp)["sha"]

                ref_body = json.dumps({
                    "sha": commit_sha,
                    "force": False
                }).encode("utf-8")
                self._gh_api(
                    f"repos/{self.repo}/git/refs/heads/{self._branch}",
                    "--method", "PATCH", "--input", "-",
                    input_data=ref_body,
                    description=f"Update ref for {rel_path}"
                )

                if self._verify_remote_size(rel_path, len(content)):
                    return True
                if self.error_log:
                    self.error_log(f"_upload_via_git_data_api: size mismatch for {rel_path}")
                return False
            except subprocess.CalledProcessError as e:
                err = e.stderr or e.output or ""
                if "not a fast forward" in err.lower() or "reference cannot be updated" in err.lower():
                    if attempt < 3:
                        time.sleep(1)
                        continue
                if self.error_log:
                    self.error_log(f"_upload_via_git_data_api FAILED ({rel_path}): {err}")
                return False
        return False

    def _verify_remote_size(self, rel_path: str, expected_size: int) -> bool:
        try:
            resp = self._gh_api(f"repos/{self.repo}/contents/{rel_path}", description="Verify size")
            data = json.loads(resp)
            remote_size = data.get("size", -1)
            return remote_size == expected_size
        except Exception:
            return False

    def _get_file_sha(self, rel_path: str) -> Optional[str]:
        try:
            entries = self._list_directory_api(os.path.dirname(rel_path))
            for path, sha in entries:
                if path == rel_path:
                    return sha
            return None
        except Exception:
            return None

    def _download_file_api(self, rel_path: str) -> bytes:
        encoded_path = "/".join(Uri.EscapeDataString(seg) for seg in rel_path.split("/"))
        endpoint = f"repos/{self.repo}/contents/{encoded_path}"
        args = [endpoint,
                "-H", "Accept: application/vnd.github.raw"]
        try:
            proc = subprocess.run(
                ["gh", "api"] + args,
                capture_output=True,
                env={**os.environ, "GITHUB_TOKEN": self._pat},
                timeout=30
            )
            if proc.returncode != 0:
                if self.error_log:
                    self.error_log(f"_download_file_api error ({rel_path}): {proc.stderr.decode()}")
                return b""
            return proc.stdout
        except subprocess.TimeoutExpired:
            if self.error_log:
                self.error_log(f"_download_file_api timeout ({rel_path})")
            return b""
        except Exception as e:
            if self.error_log:
                self.error_log(f"_download_file_api exception ({rel_path}): {e}")
            return b""

    def _list_directory_api(self, dir_path: str) -> List[tuple]:
        try:
            ref_resp = self._gh_api(f"repos/{self.repo}/git/ref/heads/{self._branch}", description="Get tree ref")
            ref_json = json.loads(ref_resp)
            tree_sha = ref_json["object"]["sha"]
        except Exception as e:
            if self.error_log:
                self.error_log(f"_list_directory_api: failed to get tree SHA: {e}")
            return []

        try:
            # Increased timeout from default 30s to 120s to handle large repos
            tree_resp = self._gh_api(f"repos/{self.repo}/git/trees/{tree_sha}?recursive=1",
                                     timeout=120,
                                     description="List tree")
            tree_data = json.loads(tree_resp)
            entries = []
            for item in tree_data.get("tree", []):
                if item["type"] == "blob" and item["path"].startswith(dir_path):
                    entries.append((item["path"], item["sha"]))
            return entries
        except Exception as e:
            if self.error_log:
                self.error_log(f"_list_directory_api: failed to list tree: {e}")
            return []

    # ── Slow worker serialiser (NOW ALWAYS INVOKES CALLBACKS ON ERROR) ──
    def _slow_worker_loop(self) -> None:
        while not self._slow_stop.is_set():
            try:
                task = self._slow_queue.get(timeout=1)
            except queue_module.Empty:
                continue
            if task is None:
                continue
            action, args, callback = task
            try:
                if action == "upload_file":
                    rel_path, content, sha = args
                    ok = self._upload_file_api(rel_path, content, sha)
                    if callback:
                        callback(ok)
                elif action == "download_file":
                    rel_path = args[0]
                    data = self._download_file_api(rel_path)
                    if callback:
                        callback(data)
                elif action == "delete_file":
                    rel_path, sha = args
                    ok = self._delete_file_via_contents_api(rel_path, sha)
                    if callback:
                        callback(ok)
                elif action == "delete_file_by_name":
                    rel_path = args[0]
                    sha = self._get_file_sha(rel_path)
                    if sha is None:
                        if self.error_log:
                            self.error_log(f"Cannot delete {rel_path}: file not found.")
                        ok = False
                    else:
                        ok = self._delete_file_via_contents_api(rel_path, sha)
                    if callback:
                        callback(ok)
                elif action == "list_directory":
                    dir_path = args[0]
                    listing = self._list_directory_api(dir_path)
                    if callback:
                        callback(listing)
                elif action == "push_log_file":
                    self._do_push_log_file()
                elif action == "purge_old_screenshots":
                    self._purge_old_screenshots()
                elif action == "push_screenshots":
                    pass
                elif action == "push_directory":
                    pass
            except Exception as e:
                err_msg = f"RepoWrapper slow error ({action}): {e}"
                if self.error_log:
                    self.error_log(err_msg)
                # Always invoke callback with failure value to prevent hang
                if callback:
                    try:
                        if action == "upload_file":
                            callback(False)
                        elif action == "download_file":
                            callback(b"")
                        elif action == "list_directory":
                            callback([])
                        elif action in ("delete_file", "delete_file_by_name"):
                            callback(False)
                    except Exception:
                        pass
                try:
                    self._do_push_log_file()
                except Exception:
                    pass

    def _do_push_log_file(self):
        if not os.path.exists(self.log_filename):
            return
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="log_", suffix=".log")
            os.close(tmp_fd)
            shutil.copy2(self.log_filename, tmp_path)
            with open(tmp_path, "rb") as f:
                content = f.read()

            with self._write_lock:
                for attempt in range(1, 4):
                    sha = self._get_file_sha(self.log_filename)
                    try:
                        ok = self._upload_file_api_locked(self.log_filename, content, sha)
                        if ok:
                            break
                    except subprocess.CalledProcessError as e:
                        err = e.stderr or e.output or ""
                        if "409" in err or "conflict" in err.lower():
                            if attempt < 3:
                                time.sleep(1)
                                continue
                        raise
        except Exception as e:
            if self.error_log:
                self.error_log(f"_do_push_log_file error: {e}")
        finally:
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _purge_old_screenshots(self):
        try:
            entries = self._list_directory_api(self.screenshots_dir)
            png_files = [(p, sha) for p, sha in entries if p.endswith(".png")]
            png_files.sort(key=lambda x: x[0])
            if len(png_files) <= self.max_screenshots:
                return
            to_delete = png_files[:-self.max_screenshots]
            for path, sha in to_delete:
                self._delete_file_via_contents_api(path, sha)
        except Exception as e:
            if self.error_log:
                self.error_log(f"_purge_old_screenshots error: {e}")

    # ── Fast worker loop (comments) ──────────────────────────────
    def _fast_worker_loop(self) -> None:
        while not self._fast_stop.is_set():
            try:
                task = self._fast_queue.get(timeout=1)
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
                elif action == "comment_exists":
                    exists = self._comment_exists(*args)
                    if callback:
                        callback(exists)
                elif action == "report_memory":
                    self._report_memory()
                elif action == "list_screenshot_files":
                    files = self._list_screenshot_files()
                    if callback:
                        callback(files)
                elif action == "download_file":
                    data = self._download_file(*args)
                    if callback:
                        callback(data)
            except Exception as e:
                err_msg = f"RepoWrapper fast error ({action}): {e}"
                if self.error_log:
                    self.error_log(err_msg)

    # ── Comment implementations (unchanged) ──────────────────────
    def _gh(self, *args: str, input_data: Optional[str] = None, **kwargs: Any) -> str:
        env = os.environ.copy()
        if self._pat:
            env["GITHUB_TOKEN"] = self._pat
        cmd = ["gh", "api"] + list(args)
        res = subprocess.run(cmd, capture_output=True, text=True, check=True,
                             input=input_data, env=env, **kwargs)
        return res.stdout.strip()

    def _edit_comment(self, comment_id: str, new_body: str, max_retries: int = 5) -> None:
        for attempt in range(max_retries):
            try:
                self._gh(f"repos/{self.repo}/issues/comments/{comment_id}",
                         "--method", "PATCH", "--input", "-",
                         input_data=json.dumps({"body": new_body}))
                return
            except subprocess.CalledProcessError:
                if attempt < max_retries - 1:
                    time.sleep(1)

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
        comments = []
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

    def _list_screenshot_files(self) -> List[str]:
        try:
            entries = self._list_directory_api(self.screenshots_dir)
            return [path for path, sha in entries if path.endswith(".png")]
        except Exception:
            return []


class Uri:
    @staticmethod
    def EscapeDataString(segment: str) -> str:
        import urllib.parse
        return urllib.parse.quote(segment, safe='')