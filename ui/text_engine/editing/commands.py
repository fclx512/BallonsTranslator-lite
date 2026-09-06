"""Undo commands for the ported text engine (Stage 4).

Port of upstream v1.5.9 ``text_engine/editing/commands.py`` — only
``SetTextTransformCommand`` is in scope (route map §7). The command is
snapshot-based: it never touches the QTextDocument undo stack.
"""

from typing import Callable, Optional, Sequence

from qtpy.QtCore import QCoreApplication

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.fontformat import TextTransformState
from utils.text_effects import TextEffectStack
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
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Transform")
        )
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


class SetTextEffectStackCommand(QUndoCommand):
    """Atomically replace complete effect state on selected text items.

    Port of upstream v1.5.13 ``editing/commands.py`` — same snapshot
    semantics as ``SetTextTransformCommand``: never touches the
    QTextDocument undo stack; page tags are applied by the canvas at
    push time (``_tag_text_command``).
    """

    def __init__(
        self,
        items: Sequence[TextBlkItem],
        before: Sequence[TextEffectStack],
        after: Sequence[TextEffectStack],
        refresh_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Text Effect")
        )
        self.items = tuple(items)
        if len(self.items) != len(before) or len(self.items) != len(after):
            raise ValueError(
                "items, before, and after must have the same length"
            )
        self.before = tuple(before)
        self.after = tuple(after)
        self.refresh_callback = refresh_callback

    @classmethod
    def create(
        cls,
        items: Sequence[TextBlkItem],
        before: Sequence[TextEffectStack],
        after: Sequence[TextEffectStack],
        refresh_callback: Optional[Callable[[], None]] = None,
    ) -> Optional["SetTextEffectStackCommand"]:
        command = cls(items, before, after, refresh_callback)
        return None if command.before == command.after else command

    def _apply(self, states: Sequence[TextEffectStack]) -> None:
        for item, state in zip(self.items, states):
            item.set_text_effects(state, preview=False)
        if self.refresh_callback is not None:
            self.refresh_callback()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)
