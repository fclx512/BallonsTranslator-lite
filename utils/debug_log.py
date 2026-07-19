"""Simple file-based debug logger for development use.

Context translation status messages are written here when
pcfg.context_translation_debug_log is True.
"""

import os
import os.path as osp
from datetime import datetime

from . import shared

DEBUG_DIR = osp.join(shared.PROGRAM_PATH, "debug")


class DebugLogger:
    """Single-purpose logger for context translation debug output."""

    def __init__(self):
        self._file = None

    def start(self):
        """Open a new timestamped log file."""
        os.makedirs(DEBUG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = osp.join(DEBUG_DIR, f"context_translation_{ts}.log")
        self._file = open(path, "w", encoding="utf-8")
        self._file.write(
            f"=== Context Translation Log started at {datetime.now()} ===\n"
        )
        self._file.flush()

    def write(self, msg: str):
        """Append a timestamped line."""
        if self._file:
            ts = datetime.now().strftime("%H:%M:%S")
            self._file.write(f"[{ts}] {msg}\n")
            self._file.flush()

    def close(self):
        """Close the log file."""
        if self._file:
            self._file.write(f"=== Log ended at {datetime.now()} ===\n")
            self._file.close()
            self._file = None


# Singleton instance
debug_logger = DebugLogger()
