"""A QPushButton that displays a color swatch.

Replaces the repeated pattern::

    btn = QPushButton()
    btn.setStyleSheet(
        f"background-color: rgb({r},{g},{b}); "
        f"border: 1px solid #888; border-radius: 3px;"
    )

Callers can connect ``colorChanged`` or just read ``color()``.
The button does NOT open a color picker — that is left to the caller.
"""

from qtpy.QtCore import Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QPushButton


class ColorSwatchBtn(QPushButton):
    """PushButton whose background reflects a stored QColor.

    Signals
    -------
    colorChanged(color: QColor)
        Emitted whenever the color is updated via ``setColor()``.
    """

    colorChanged = Signal(QColor)

    def __init__(self, color: QColor | list | None = None, parent=None):
        super().__init__(parent)
        self._color = QColor(0, 0, 0)
        if color is not None:
            self.setColor(color)

    # ── public API ────────────────────────────────────────────

    def setColor(self, color: QColor | list):
        """Update the swatch color and refresh the button style.

        Accepts a QColor or a 3-element list [r, g, b].
        """
        if isinstance(color, (list, tuple)):
            self._color = QColor(*[max(0, min(255, int(c))) for c in color[:3]])
        else:
            self._color = QColor(color)
        self._update_style()
        self.colorChanged.emit(self._color)

    def color(self) -> QColor:
        """Return the current swatch color."""
        return QColor(self._color)

    # ── internal ──────────────────────────────────────────────

    def _update_style(self):
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        self.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); "
            f"border: 1px solid #888; border-radius: 3px;"
        )
