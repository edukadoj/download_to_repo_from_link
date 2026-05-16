#!/usr/bin/env python3
# ==============================================================================
# execution_queue.py – Version 1.1.2 (only one exit at a time)
# ==============================================================================
import threading
from typing import Optional, List, Dict

class ExecutionQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue: List[Dict] = []

    def add_command(self, cmd_id: str, cmd_text: str) -> None:
        with self._lock:
            if cmd_text.strip().lower() == "screenshot":
                self._queue = [item for item in self._queue if item["text"].strip().lower() != "screenshot"]
            if cmd_text.strip().lower() == "exit":
                # Keep only the newest exit – remove any older ones
                self._queue = [item for item in self._queue if item["text"].strip().lower() != "exit"]
            self._queue.append({"id": cmd_id, "text": cmd_text})

    def pop_next(self) -> Optional[Dict]:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def clear(self):
        with self._lock:
            self._queue.clear()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)
