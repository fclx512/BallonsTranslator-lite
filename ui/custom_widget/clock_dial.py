import math

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QRadialGradient,
)
from qtpy.QtWidgets import QWidget


class ClockDial(QWidget):
    """PS-style circular dial for angle+distance selection.

    Modes:
      - 'shadow': angle + radial distance (handle position = light direction + offset magnitude)
      - 'gradient': angle only (handle always at circle edge)
    """

    angleChanged = Signal(float)
    distanceChanged = Signal(float)
    valueChanged = Signal()

    def __init__(self, mode="shadow", parent=None, min_size=160, compact=False):
        super().__init__(parent)
        self._mode = mode
        self._compact = bool(compact)
        self._angle = 135.0  # degrees, 0=right, clockwise
        self._distance = 0.5 if mode == "shadow" else 1.0
        self._color = QColor(0, 0, 0)
        self._dragging = False
        self._hovered = False
        self.setMinimumSize(min_size, min_size)
        self.setMouseTracking(True)

    # ── Public API ──────────────────────────────────────────

    def setMode(self, mode: str):
        self._mode = mode
        if mode == "gradient":
            self._distance = 1.0
        self.update()

    def mode(self) -> str:
        return self._mode

    def setAngle(self, angle: float):
        self._angle = angle % 360.0
        self.update()

    def angle(self) -> float:
        return self._angle

    def setDistance(self, dist: float):
        self._distance = max(0.0, min(1.0, dist))
        self.update()

    def distance(self) -> float:
        return self._distance

    def setColor(self, color):
        if isinstance(color, (list, tuple)):
            color = QColor(*[max(0, min(255, int(c))) for c in color[:3]])
        self._color = QColor(color)
        self.update()

    # ── Helpers ─────────────────────────────────────────────

    def _dial_rect(self) -> QRectF:
        """The circle bounds, centered in the widget."""
        s = min(self.width(), self.height())
        margin = 4.0 if self._compact else 18.0
        size = s - 2 * margin
        x = (self.width() - size) / 2.0
        y = (self.height() - size) / 2.0
        return QRectF(x, y, size, size)

    def _handle_pos(self) -> QPointF:
        """Screen position of the draggable handle."""
        rect = self._dial_rect()
        cx, cy = rect.center().x(), rect.center().y()
        max_r = rect.width() / 2.0 - 6.0
        r = max_r * self._distance if self._mode == "shadow" else max_r
        rad = math.radians(self._angle)
        hx = cx + r * math.cos(rad)
        hy = cy - r * math.sin(rad)
        return QPointF(hx, hy)

    def _from_mouse(self, pos: QPointF):
        """Convert mouse position to (angle, distance)."""
        rect = self._dial_rect()
        cx, cy = rect.center().x(), rect.center().y()
        max_r = rect.width() / 2.0 - 6.0
        dx = pos.x() - cx
        dy = pos.y() - cy
        r = math.sqrt(dx * dx + dy * dy)
        if r < 2.0:
            return self._angle, 0.0
        if self._mode == "gradient":
            dist = 1.0
        else:
            dist = max(0.0, min(1.0, r / max_r))
        angle_rad = math.atan2(-dy, dx)
        angle_deg = math.degrees(angle_rad)
        if angle_deg < 0:
            angle_deg += 360.0
        return angle_deg, dist

    # ── Events ──────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self._dial_rect()
        cx, cy = rect.center().x(), rect.center().y()
        outer_r = rect.width() / 2.0
        inner_r = outer_r - 12.0
        max_handle_r = inner_r - 8.0

        # subtle dial background
        bg_grad = QRadialGradient(cx, cy, outer_r)
        bg_grad.setColorAt(0.0, QColor(245, 245, 245))
        bg_grad.setColorAt(0.85, QColor(220, 220, 220))
        bg_grad.setColorAt(1.0, QColor(180, 180, 180))
        p.setBrush(QBrush(bg_grad))
        p.setPen(QPen(QColor(160, 160, 160), 1.5))
        p.drawEllipse(rect.center(), outer_r, outer_r)

        # inner ring
        p.setPen(QPen(QColor(200, 200, 200), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect.center(), inner_r, inner_r)

        # tick marks every 30 degrees (dropped in compact mode — the dock
        # dial only needs a rough direction, no degree annotation)
        if not self._compact:
            font = QFont()
            font.setPointSizeF(7.5)
            p.setFont(font)
            p.setPen(QPen(QColor(130, 130, 130), 1))
            for deg in range(0, 360, 30):
                rad = math.radians(deg)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                # outer tick
                x1 = cx + cos_a * (inner_r - 1)
                y1 = cy - sin_a * (inner_r - 1)
                x2 = cx + cos_a * (inner_r - 10)
                y2 = cy - sin_a * (inner_r - 10)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                # degree label
                lx = cx + cos_a * (inner_r - 16) - 10
                ly = cy - sin_a * (inner_r - 16) - 5
                p.drawText(
                    QRectF(lx, ly, 20, 12),
                    Qt.AlignmentFlag.AlignCenter,
                    str(deg),
                )

        # dashed inner circle (shadow mode)
        if self._mode == "shadow":
            pen = QPen(QColor(170, 170, 170), 0.8, Qt.PenStyle.DashLine)
            pen.setDashPattern([4, 4])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.center(), max_handle_r * 0.25, max_handle_r * 0.25)

        # center dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(100, 100, 100)))
        p.drawEllipse(rect.center(), 4, 4)

        # line from center to handle
        hpos = self._handle_pos()
        line_pen = QPen(self._color, 2.0)
        p.setPen(line_pen)
        p.drawLine(QPointF(cx, cy), hpos)

        # handle
        handle_r = 5.0 if self._compact else 7.0
        handle_color = (
            self._color.lighter(130) if self._dragging or self._hovered else self._color
        )
        p.setPen(QPen(handle_color.darker(150), 1.5))
        p.setBrush(QBrush(handle_color))
        p.drawEllipse(hpos, handle_r, handle_r)

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_mouse(event.position())
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_from_mouse(event.position())
            self.update()
        else:
            # hover detection
            hpos = self._handle_pos()
            d = math.sqrt(
                (event.position().x() - hpos.x()) ** 2
                + (event.position().y() - hpos.y()) ** 2
            )
            was_hovered = self._hovered
            self._hovered = d < 12.0
            if was_hovered != self._hovered:
                self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._update_from_mouse(event.position())
            self.update()
            self.valueChanged.emit()

    def _update_from_mouse(self, pos):
        old_angle = self._angle
        old_dist = self._distance
        self._angle, self._distance = self._from_mouse(pos)
        if self._angle != old_angle:
            self.angleChanged.emit(self._angle)
        if self._distance != old_dist:
            self.distanceChanged.emit(self._distance)

    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        from qtpy.QtCore import QSize

        return QSize(self.minimumSize().width(), self.minimumSize().height())
