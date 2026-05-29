"""
Data models for the AI chat subsystem.

Pure data — no Qt widgets.  Kept separate from UI so the controller
and worker layers never import presentation code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Value objects ───────────────────────────────────────────────────────

@dataclass
class ChangeItem:
    """A single field-level modification proposed by the AI."""
    block_id: str
    field: str
    old_value: Any
    new_value: Any
    accepted: Optional[bool] = None
    src_text: str = ''


@dataclass
class ChatMessage:
    """One message in the conversation."""
    role: str
    content: str
    changes: List[ChangeItem] = field(default_factory=list)
    segments: List[Dict] = field(default_factory=list)
    """Display segments for history reconstruction.

    Each element is ``{"type": "text"|"tool_trace", "content": str}``.
    ``text`` → ``_add_assistant_bubble``, ``tool_trace`` → ``add_system_message``.
    Empty list = legacy message: strip ``[tool]`` markers and show as one bubble.
    """


# ── Helpers ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token count: CJK chars ~1.5 tok/char, others ~0.25 tok/char."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if '一' <= ch <= '鿿' or '぀' <= ch <= 'ヿ')
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))
