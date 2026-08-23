"""PS-style panel rail: launcher icons docked to the format area.

A narrow (~26px) vertical strip on the left edge of the text panel's
format row (``ui/scenetext_manager.py::TextPanel``).  Launcher icons open
``ui/custom_widget/rail_dock_panel.py::RailDockPanel`` canvas panels —
hard-docked to the rail's left side (not freely draggable).  The strip
paints no background itself: it stays transparent, only the icon buttons
carry their hover/checked cues.

Row numbering, the selection accent cue and drag initiation stay inside
the row cards themselves (``ui/textedit_area.py::TransPairWidget``).
"""

from qtpy.QtCore import QPointF, Qt
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import QToolButton, QVBoxLayout, QWidget

RAIL_WIDTH = 26


class RailLauncherButton(QToolButton):
    """Checkable rail icon: procedurally drawn glyph, annotation dot badge.

    ``glyph`` is drawn centered; ``deco="dots"`` adds three small dots
    above the glyph (ruby/emphasis reading mark).  ``set_dot(True)``
    shows an accent dot at the top-right corner — "current block has
    content in this panel" hint.  Checked (panel open) paints the accent
    background via CSS; the glyph/dot flip to white for contrast.
    """

    def __init__(self, glyph: str, deco: str = "", parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._deco = deco
        self._dot = False
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_dot(self, dot: bool) -> None:
        if self._dot != dot:
            self._dot = dot
            self.update()

    def paintEvent(self, event) -> None:
        from ui.misc import get_theme_color

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        checked = self.isChecked()
        color = QColor("white") if checked else self.palette().text().color()
        if not self.isEnabled():
            color.setAlpha(110)
        if self._deco == "dots":
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(color)
            pen.setWidthF(1.2)
            painter.setPen(pen)
            dot_y = 4.0
            for dx in (-5.0, 0.0, 5.0):
                painter.drawPoint(QPointF(self.width() / 2 + dx, dot_y))
        font = self.font()
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            0,
            6,
            self.width(),
            self.height() - 6,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._glyph,
        )
        if self._dot:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("white") if checked else get_theme_color())
            painter.drawEllipse(self.width() - 7, 2, 5, 5)


class PanelRail(QWidget):
    """Vertical launcher icon column; hosts ``RailLauncherButton``s."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(RAIL_WIDTH)
        # 透明窄条：不画底色也不声明 WA_StyledBackground，
        # 背后格式区底色透出，图标 hover/checked 由 CSS 承担
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 3, 1, 3)
        layout.setSpacing(3)
        layout.addStretch(1)

    def add_launcher(self, button: RailLauncherButton) -> None:
        self.layout().insertWidget(
            self.layout().count() - 1, button, 0, Qt.AlignmentFlag.AlignHCenter
        )

    def launcher_at(self, index: int) -> RailLauncherButton:
        return self.layout().itemAt(index).widget()
