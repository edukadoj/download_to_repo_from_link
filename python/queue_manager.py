#!/usr/bin/env python3
# ==============================================================================
# queue_manager.py – Version 1.0.0
# ==============================================================================
# Generic queue manager for the reliable command‑report protocol.
#
# Used by both the agent (to manage reports) and the WPF app (to manage
# commands).  Each queue item has:
#   - id: unique string, e.g. "AGT-17-1735000130"
#   - body: the payload (command text or report text)
#   - created_at: datetime for timeout calculation
#
# The queue is stored as a list of dicts.  Helper functions:
#   - parse_comment(body) -> list of (id, text)
#   - build_comment(header, items) -> full comment body
#   - remove_ids(queue, ids) -> new list
#   - cull_expired(queue, max_age_seconds) -> new list
#   - cull_excess(queue, max_items) -> new list
#   - is_duplicate(seen_set, item_id) -> bool
# ==============================================================================

import time
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Set

# ── Parsing ────────────────────────────────────────────────────────────────
def parse_comment(body: str) -> List[Tuple[str, str]]:
    """
    Parse a comment body into a list of (id, text) pairs.
    Expected format:
        ## Header line (ignored)
        id; payload
    """
    items = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        # Split on the first semicolon
        if ";" in line:
            item_id, payload = line.split(";", 1)
            items.append((item_id.strip(), payload.strip()))
    return items

# ── Building ────────────────────────────────────────────────────────────────
def build_comment(header: str, items: List[Tuple[str, str]]) -> str:
    """
    Build a comment body from a header and a list of (id, text) items.
    """
    lines = [header]
    for item_id, text in items:
        lines.append(f"{item_id}; {text}")
    return "\n".join(lines)

# ── Removal ─────────────────────────────────────────────────────────────────
def remove_ids(items: List[Tuple[str, str]], ids_to_remove: Set[str]) -> List[Tuple[str, str]]:
    """Return a new list without items whose id is in ids_to_remove."""
    return [(iid, text) for (iid, text) in items if iid not in ids_to_remove]

# ── Timeout culling ─────────────────────────────────────────────────────────
def cull_expired(queue: List[Dict], max_age_seconds: int) -> List[Dict]:
    """Remove items older than max_age_seconds (based on 'created_at' UTC)."""
    now = datetime.now(timezone.utc)
    return [
        item for item in queue
        if (now - item["created_at"]).total_seconds() < max_age_seconds
    ]

# ── Size culling ────────────────────────────────────────────────────────────
def cull_excess(items: List[Tuple[str, str]], max_items: int) -> List[Tuple[str, str]]:
    """If the list has more than max_items, drop oldest (front)."""
    if len(items) > max_items:
        return items[-max_items:]
    return items

# ── Deduplication helper ────────────────────────────────────────────────────
def is_duplicate(seen_set: Set[str], item_id: str) -> bool:
    """Check if item_id was already processed."""
    return item_id in seen_set

# ── Generate unique IDs ─────────────────────────────────────────────────────
def generate_id(prefix: str, seq: int) -> str:
    """Create a unique ID: prefix-seq-unix_timestamp."""
    ts = int(time.time())
    return f"{prefix}-{seq}-{ts}"
