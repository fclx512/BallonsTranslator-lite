"""Undo commands for the ported text engine (Stage 4).

Port of upstream v1.5.9 ``text_engine/editing/commands.py`` — only
``SetTextTransformCommand`` is in scope (route map §7). The command is
snapshot-based: it never touches the QTextDocument undo stack.
"""

from typing import Callable, Optional, Sequence

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.fontformat import TextTransformState
from ui.textitem import TextBlkItem


class SetTextTransformCommand(QUndoCommand):
    """Atomically apply complete transform state to one or more items."""

    def __init__(
        self,
        items: Sequence[TextBlkItem],
        before: Sequence[TextTransformState],
        after: Sequence[TextTransformState],
        refresh_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.items = tuple(items)
        if len(self.items) != len(before) or len(self.items) != len(after):
            raise ValueError("items, before, and after must have the same length")
        self.before = tuple(
            TextTransformState(value.stack, value.glyph_slant_angle)
            for value in before
        )
        self.after = tuple(
            TextTransformState(value.stack, value.glyph_slant_angle)
            for value in after
        )
        self.refresh_callback = refresh_callback

    @classmethod
    def create(
        cls,
        items: Sequence[TextBlkItem],
        before: Sequence[TextTransformState],
        after: Sequence[TextTransformState],
        refresh_callback: Optional[Callable[[], None]] = None,
    ) -> Optional["SetTextTransformCommand"]:
        """Build a command, or return ``None`` for a normalized no-op."""
        command = cls(items, before, after, refresh_callback)
        return None if command.before == command.after else command

    def _apply(self, states: Sequence[TextTransformState]):
        for item, state in zip(self.items, states):
            item.set_text_transform(state, preview=False)
        if self.refresh_callback is not None:
            self.refresh_callback()

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)
