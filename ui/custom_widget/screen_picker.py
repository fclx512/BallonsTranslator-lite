"""Screen color picker (eyedropper) with a full-screen magnifier overlay.

Stability notes — the earlier eyedropper attempt froze the UI; this design
structurally cannot:

- Event-driven only. The overlay runs its own modal event loop (``exec()``)
  and all interaction arrives via ``mouseMoveEvent`` / ``mousePressEvent`` /
  ``keyPressEvent``. No polling loop, no ``processEvents()`` busy-wait.
- The screens are captured once as a frozen frame before the overlay shows;
  sampling reads pixels from that frame instead of re-capturing per move.
- No ``grabMouse()`` / ``SetCapture`` / global hooks, so nothing is left
  grabbed when the overlay closes; the per-widget cursor restores itself.
- ``try/finally`` in :func:`pick_screen_color` guarantees the overlay is
  always closed and control always returns to the caller.
"""

from qtpy.QtCore import QPointF, QRectF, Qt, QThread
from qtpy.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication, QDialog

_MAG_RADIUS = 56  # magnifier radius, logical px
_MAG_ZOOM = 8  # pixel zoom factor
_VEIL_ALPHA = 70  # dark veil over the frozen frame


class _ScreenFrame:
    """One screen's capture: logical geometry, device-pixel image and DPR."""

    __slots__ = ("geometry", "image", "dpr")

    def __init__(self, geometry, image, dpr):
        self.geometry = geometry
        self.image = image
        self.dpr = dpr


class _PickerOverlay(QDialog):
    """Frameless, always-on-top overlay covering the whole virtual desktop.

    Left click picks the color under the cursor; right click or Esc cancels.
    """

    def __init__(self, frames, parent=None):
        super().__init__(parent)
        self._frames = frames
        self._result = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(QGuiApplication.primaryScreen().virtualGeometry())
        self._cursor_pos = self.mapFromGlobal(QCursor.pos()).toPointF()

    def picked_color(self):
        return self._result

    # ── probing ────────────────────────────────────────────

    def _probe(self, pos):
        """Return ``(frame, color, ix, iy)`` under overlay-local ``pos``.

        ``ix``/``iy`` are device-pixel coordinates inside ``frame.image``;
        ``(None, None, 0, 0)`` when no captured frame covers the position.
        """
        gpos = pos + QPointF(self.geometry().topLeft())
        for frame in self._frames:
            g = frame.geometry
            if (
                gpos.x() < g.x()
                or gpos.x() >= g.x() + g.width()
                or gpos.y() < g.y()
                or gpos.y() >= g.y() + g.height()
            ):
                continue
            local = gpos - QPointF(g.topLeft())
            ix = max(0, min(frame.image.width() - 1, int(local.x() * frame.dpr)))
            iy = max(0, min(frame.image.height() - 1, int(local.y() * frame.dpr)))
            return frame, frame.image.pixelColor(ix, iy), ix, iy
        return None, None, 0, 0

    # ── painting ───────────────────────────────────────────

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for frame in self._frames:
            p.drawImage(QRectF(frame.geometry), frame.image)
        p.fillRect(self.rect(), QColor(0, 0, 0, _VEIL_ALPHA))

        pos = self._cursor_pos
        frame, color, ix, iy = self._probe(pos)
        if frame is None or color is None:
            return
        self._paint_magnifier(p, pos, frame, ix, iy)
        self._paint_readout(p, pos, color)

    def _paint_magnifier(self, p, pos, frame, ix, iy):
        w, h = self.width(), self.height()
        tcx = max(_MAG_RADIUS, min(w - _MAG_RADIUS, pos.x()))
        tcy = max(_MAG_RADIUS, min(h - _MAG_RADIUS, pos.y()))

        # source square in device px around the sampled pixel
        half = int(_MAG_RADIUS / _MAG_ZOOM)
        sx0 = max(0, ix - half)
        sy0 = max(0, iy - half)
        sx1 = min(frame.image.width(), ix + half + 1)
        sy1 = min(frame.image.height(), iy + half + 1)

        target = QRectF(
            tcx - _MAG_RADIUS, tcy - _MAG_RADIUS, _MAG_RADIUS * 2, _MAG_RADIUS * 2
        )
        path = QPainterPath()
        path.addEllipse(target)
        p.save()
        p.setClipPath(path)
        p.drawImage(target, frame.image, QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
        p.restore()

        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(target)

        # crosshair in four segments, leaving the sampled center pixel clear
        p.setPen(QPen(QColor(255, 255, 255, 190), 1))
        p.drawLine(QPointF(target.left(), tcy), QPointF(tcx - 4, tcy))
        p.drawLine(QPointF(tcx + 4, tcy), QPointF(target.right(), tcy))
        p.drawLine(QPointF(tcx, target.top()), QPointF(tcx, tcy - 4))
        p.drawLine(QPointF(tcx, tcy + 4), QPointF(tcx, target.bottom()))

    def _paint_readout(self, p, pos, color):
        r, g, b = color.red(), color.green(), color.blue()
        text = f"#{r:02X}{g:02X}{b:02X}   R{r} G{g} B{b}"
        fm = p.fontMetrics()
        text_w = fm.horizontalAdvance(text) + 16
        text_h = fm.height() + 8
        w, h = self.width(), self.height()
        x = max(4, min(w - text_w - 4, pos.x() - text_w / 2))
        y = pos.y() - _MAG_RADIUS - text_h - 10
        if y < 4:
            y = pos.y() + _MAG_RADIUS + 10
        y = max(4, min(h - text_h - 4, y))
        rf = QRectF(x, y, text_w, text_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(rf, 4, 4)
        p.setPen(QPen(QColor(255, 255, 255, 240)))
        p.drawText(rf, Qt.AlignmentFlag.AlignCenter, text)

    # ── interaction ────────────────────────────────────────

    def mouseMoveEvent(self, e):
        self._cursor_pos = e.position()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            _, color, _, _ = self._probe(e.position())
            if color is not None:
                self._result = color
            self.accept()
        elif e.button() == Qt.MouseButton.RightButton:
            self.reject()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.activateWindow()
        self.setFocus()


def pick_screen_color():
    """Capture the virtual desktop and let the user pick a screen color.

    Returns the picked color, or ``None`` when cancelled or capture failed.
    """
    # let the screen settle (e.g. the caller's dialog just hid itself)
    for _ in range(2):
        QApplication.processEvents()
        QThread.msleep(40)

    frames = []
    for screen in QGuiApplication.screens():
        image = screen.grabWindow(0).toImage()
        if image.isNull():
            continue
        frames.append(_ScreenFrame(screen.geometry(), image, screen.devicePixelRatio()))
    if not frames:
        return None

    overlay = _PickerOverlay(frames)
    try:
        if overlay.exec() == QDialog.DialogCode.Accepted:
            return overlay.picked_color()
        return None
    finally:
        overlay.close()
