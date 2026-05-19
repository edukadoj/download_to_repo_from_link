#!/usr/bin/env python3
# ==============================================================================
# downloader.py – Version 1.0.0
#   - Splits files into chunks and uploads them to the GitHub repository
#     with size verification.  Used by the 'download' command and the
#     profile cache saver.
#   - Pairs with uploader.py which handles reassembly.
# ==============================================================================

import os, base64, json, subprocess, time
from typing import Optional


def get_remote_file_size(repo: str, path: str, pat: Optional[str] = None) -> Optional[int]:
    """Return the size (bytes) of a file on GitHub, or None if it doesn't exist."""
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
    """Delete a file on GitHub by path (uses its SHA)."""
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


def upload_chunk_to_repo(repo: str, local_path: str, remote_path: str,
                         pat: Optional[str] = None, max_retries: int = 5) -> bool:
    """
    Upload a single file (chunk) to the GitHub repository.
    After uploading, verify that the remote file size matches the local file size.
    Returns True on success, False after exhausting all retries.
    """
    env = os.environ.copy()
    if pat:
        env["GITHUB_TOKEN"] = pat

    with open(local_path, "rb") as f:
        content_bytes = f.read()
    local_size = len(content_bytes)
    b64_content = base64.b64encode(content_bytes).decode()

    api_url = f"repos/{repo}/contents/{remote_path}"

    for attempt in range(1, max_retries + 1):
        try:
            sha = None
            sha_out = subprocess.run(
                ["gh", "api", api_url, "--jq", ".sha"],
                capture_output=True, text=True, env=env,
            )
            if sha_out.returncode == 0 and sha_out.stdout.strip():
                sha = sha_out.stdout.strip()

            payload = {
                "message": f"Upload chunk {remote_path}",
                "content": b64_content,
                "branch": "main"
            }
            if sha:
                payload["sha"] = sha

            result = subprocess.run(
                ["gh", "api", api_url, "--method", "PUT", "--input", "-"],
                input=json.dumps(payload), capture_output=True, text=True, env=env,
            )

            if result.returncode == 0:
                remote_size = get_remote_file_size(repo, remote_path, pat)
                if remote_size == local_size:
                    return True
                else:
                    delete_remote_file(repo, remote_path, pat)
            else:
                if attempt < max_retries:
                    delete_remote_file(repo, remote_path, pat)
                    time.sleep(1)
        except Exception:
            if attempt < max_retries:
                time.sleep(1)
        time.sleep(0.5)

    return False