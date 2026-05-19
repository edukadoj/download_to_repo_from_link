#!/usr/bin/env python3
# ==============================================================================
# downloader.py – Version 2.0.0
#   - Robust chunk upload with SHA verification, rate‑limit handling,
#     exponential backoff, and SHA refresh on conflict.
# ==============================================================================

import os, time, base64, hashlib, json, subprocess, re
from typing import Optional, Dict, Any

# ── GitHub API helpers ──────────────────────────────────────────────

def _gh_api(repo: str, path: str, method: str = "GET",
            body: Optional[Dict[str, Any]] = None,
            extra_args: Optional[list] = None,
            pat: Optional[str] = None) -> subprocess.CompletedProcess:
    """
    Run a `gh api` command with optional JSON body.
    Returns the completed process.  The caller must check returncode.
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat

    cmd = ["gh", "api", f"repos/{repo}/contents/{path}"]
    if method != "GET":
        cmd += ["--method", method]
    if extra_args:
        cmd += extra_args
    if body is not None:
        cmd += ["--input", "-"]
        input_data = json.dumps(body)
    else:
        input_data = None

    return subprocess.run(
        cmd,
        capture_output=True, text=True,
        input=input_data, env=env
    )


def _get_remote_blob_sha(repo: str, path: str, pat: Optional[str] = None) -> Optional[str]:
    """
    Fetch the blob SHA of a file on GitHub (not the tree SHA).
    This is the SHA that can be compared with locally computed blob SHA.
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".sha"],
            capture_output=True, text=True, check=True, env=env,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    return None


def _get_rate_limit_reset(repo: str, pat: Optional[str] = None) -> Optional[int]:
    """
    Check the rate‑limit status and return the Unix timestamp when it resets,
    or None if we are not rate‑limited.
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat
    try:
        result = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".rate.reset"],
            capture_output=True, text=True, check=True, env=env,
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def _wait_for_rate_limit(repo: str, pat: Optional[str] = None) -> None:
    """
    If we are rate‑limited, wait until the reset time and then return.
    """
    reset = _get_rate_limit_reset(repo, pat)
    if reset is not None:
        now = int(time.time())
        if reset > now:
            wait = reset - now + 2   # extra margin
            print(f"Rate limited. Waiting {wait} seconds until {time.ctime(reset)}...")
            time.sleep(wait)


# ── SHA computation (same algorithm as Git blob SHA) ────────────────

def compute_blob_sha(local_path: str) -> str:
    """
    Compute the Git blob SHA for a file.
    """
    with open(local_path, "rb") as f:
        content = f.read()
    header = f"blob {len(content)}\0".encode("utf-8")
    sha1 = hashlib.sha1()
    sha1.update(header)
    sha1.update(content)
    return sha1.hexdigest()


# ── Main upload function ────────────────────────────────────────────

def upload_chunk_to_repo(repo: str, local_path: str, remote_path: str,
                         pat: Optional[str] = None, max_retries: int = 8) -> bool:
    """
    Upload a single file (chunk) to the GitHub repository.
    After uploading, verify that the remote blob SHA matches the local blob SHA.
    If verification fails, retry with exponential backoff.
    Handles rate‑limit waiting and SHA refresh on conflict.
    Returns True on success, False after exhausting all retries.
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat

    # Read file content once
    with open(local_path, "rb") as f:
        content_bytes = f.read()
    local_sha = compute_blob_sha(local_path)
    b64_content = base64.b64encode(content_bytes).decode()

    api_url = f"repos/{repo}/contents/{remote_path}"

    for attempt in range(1, max_retries + 1):
        try:
            # ── Before any API call, check / wait for rate limit ──
            _wait_for_rate_limit(repo, pat)

            # Get current remote SHA (if file exists)
            remote_sha = _get_remote_blob_sha(repo, remote_path, pat)
            # Build payload
            payload = {
                "message": f"Upload chunk {remote_path}",
                "content": b64_content,
                "branch": "main"
            }
            if remote_sha:
                payload["sha"] = remote_sha   # update existing file

            # ── Perform upload ──
            result = subprocess.run(
                ["gh", "api", api_url, "--method", "PUT", "--input", "-"],
                input=json.dumps(payload), capture_output=True, text=True, env=env,
            )

            if result.returncode != 0:
                # Handle conflict / unprocessable entity
                stderr = result.stderr or ""
                if "409" in stderr or "422" in stderr:
                    # Refresh remote SHA and retry without incrementing attempt count
                    print(f"Conflict on {remote_path}, refreshing remote SHA...")
                    time.sleep(1)
                    continue   # retry without counting this as an attempt
                # Other errors: wait and retry later
                print(f"Upload attempt {attempt} failed: {stderr.strip()}")
                if attempt < max_retries:
                    sleep_time = 2 ** (attempt - 1)   # 1,2,4,8,16,32,64,128 sec
                    time.sleep(sleep_time)
                    continue
                return False

            # ── Upload succeeded → verify SHA ──
            for verify_attempt in range(1, 6):
                _wait_for_rate_limit(repo, pat)
                remote_sha = _get_remote_blob_sha(repo, remote_path, pat)
                if remote_sha == local_sha:
                    print(f"Uploaded and verified {remote_path}")
                    return True
                # SHA mismatch or not yet available
                print(f"SHA verification attempt {verify_attempt} failed for {remote_path}")
                if verify_attempt < 5:
                    time.sleep(2)
                else:
                    print(f"SHA verification exhausted for {remote_path}, will retry upload")
                    break

            # If verification failed, the upload was corrupted; retry the whole thing
            if attempt < max_retries:
                sleep_time = 2 ** (attempt - 1)
                time.sleep(sleep_time)

        except Exception as e:
            print(f"Upload exception for {remote_path}: {e}")
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))

    return False


# ── Legacy helpers (kept for compatibility, but not used in new upload) ──

def get_remote_file_size(repo: str, path: str, pat: Optional[str] = None) -> Optional[int]:
    """
    Return the size (bytes) of a file on GitHub, or None if it doesn't exist.
    (Still used by profile_cache.py for quick check before upload.)
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".size"],
            capture_output=True, text=True, check=True, env=env,
        )
        size_str = out.stdout.strip()
        if size_str.isdigit():
            return int(size_str)
    except Exception:
        pass
    return None


def delete_remote_file(repo: str, path: str, pat: Optional[str] = None) -> None:
    """
    Delete a file on GitHub by path (uses its SHA).
    (Still used for cleanup of corrupt chunks.)
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat
    try:
        sha_out = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".sha"],
            capture_output=True, text=True, check=True, env=env,
        )
        sha = sha_out.stdout.strip()
        if not sha:
            return
        payload = json.dumps({"message": "cleanup corrupt chunk", "sha": sha, "branch": "main"})
        subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{path}", "--method", "DELETE", "--input", "-"],
            input=payload, capture_output=True, text=True, check=True, env=env,
        )
    except Exception:
        pass