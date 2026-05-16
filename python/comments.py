#!/usr/bin/env python3
# ==============================================================================
# comments.py – Version 1.2.0
#   - Always prefers the PAT secret for API calls
#   - If PAT is set, it temporarily replaces GITHUB_TOKEN for gh api
# ==============================================================================
import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def gh_api(*args: str, input_data: Optional[str] = None, **kwargs: Any) -> str:
    """
    Run `gh api` with the given arguments.
    If the environment variable `PAT` is set, it is used as the token
    for this call (by overriding GITHUB_TOKEN).  Otherwise the default
    Actions token is used.
    """
    env = os.environ.copy()
    pat = env.get("PAT")
    if pat:
        # Override GITHUB_TOKEN so `gh` picks up the PAT
        env["GITHUB_TOKEN"] = pat

    cmd = ["gh", "api"] + list(args)
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        input=input_data,
        env=env,
        **kwargs,
    )
    return res.stdout.strip()


def issue_comment(repo: str, issue_number: int, markdown_body: str) -> str:
    """Post a new comment and return its ID."""
    return gh_api(
        f"repos/{repo}/issues/{issue_number}/comments",
        "--method", "POST",
        "-f", f"body={markdown_body}",
        "--jq", ".id",
    )


def get_all_comments(repo: str, issue_number: int) -> List[Dict[str, str]]:
    """
    Fetch every comment of the given issue (paginated) and return a list of dicts
    with keys 'id', 'body', 'user_type'.
    """
    raw = gh_api(
        f"repos/{repo}/issues/{issue_number}/comments",
        "--jq", ".[] | {id: .id, body: .body, user_type: .user.type}",
        "--paginate",
    )
    if not raw.strip():
        return []

    comments: List[Dict[str, str]] = []
    decoder = json.JSONDecoder()
    idx = 0
    raw_len = len(raw)
    while idx < raw_len:
        while idx < raw_len and raw[idx].isspace():
            idx += 1
        if idx >= raw_len:
            break
        try:
            obj, end = decoder.raw_decode(raw, idx)
            comments.append(
                {
                    "id": str(obj.get("id", "")),
                    "body": obj.get("body", ""),
                    "user_type": obj.get("user_type", ""),
                }
            )
            idx = end
        except json.JSONDecodeError:
            idx += 1
    return comments


def find_marker_comment(comments: List[Dict[str, str]], marker: str) -> Optional[Dict[str, str]]:
    """Return the first comment whose body starts with `marker`, or None."""
    for c in comments:
        if c.get("body", "").startswith(marker):
            return c
    return None


def delete_comment(repo: str, comment_id: str) -> bool:
    """Delete a single comment. Returns True on success, False otherwise."""
    try:
        gh_api(f"repos/{repo}/issues/comments/{comment_id}", "--method", "DELETE")
        return True
    except subprocess.CalledProcessError:
        return False


def edit_comment(repo: str, comment_id: str, new_body: str) -> None:
    """Update the body of an existing comment (uses JSON input)."""
    gh_api(
        f"repos/{repo}/issues/comments/{comment_id}",
        "--method", "PATCH",
        "--input", "-",
        input_data=json.dumps({"body": new_body}),
    )


def comment_exists(repo: str, comment_id: str) -> bool:
    """Return True if the comment exists (HTTP 200)."""
    try:
        gh_api(f"repos/{repo}/issues/comments/{comment_id}", "--jq", ".id")
        return True
    except subprocess.CalledProcessError:
        return False
