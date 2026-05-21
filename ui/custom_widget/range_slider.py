from qtpy.QtWidgets import QWidget
from qtpy.QtCore import Qt, QRect, QRectF, QPoint, Signal
from qtpy.QtGui import QMouseEvent, QPainter, QColor

from .helper import isDarkTheme, themeColor


class RangeSlider(QWidget):
    """Dual-handle horizontal range slider for selecting page ranges."""

    rangeChanged = Signal(int, int)

    def __init__(self, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._low = minimum
        self._high = maximum
        self._dragging = None  # 'low' or 'high'
        self._handle_radius = 8
        self._groove_height = 4
        self.setMinimumHeight(28)

    def set_range(self, low: int, high: int):
        low = max(self._min, min(low, self._max))
        high = max(self._min, min(high, self._max))
        if low != self._low or high != self._high:
            self._low = min(low, high)
            self._high = max(low, high)
            self.update()
            self.rangeChanged.emit(self._low, self._high)

    def set_low(self, low: int):
        self.set_range(low, self._high)

    def set_high(self, high: int):
        self.set_range(self._low, high)

    def low(self) -> int:
        return self._low

    def high(self) -> int:
        return self._high

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._max

    def _pos_to_value(self, x: float) -> int:
        groove_start = self._handle_radius + 2
        groove_end = self.width() - self._handle_radius - 2
        if groove_end <= groove_start:
            return self._min
        ratio = (x - groove_start) / (groove_end - groove_start)
        ratio = max(0.0, min(1.0, ratio))
        return round(self._min + ratio * (self._max - self._min))

    def _value_to_pos(self, value: int) -> float:
        groove_start = self._handle_radius + 2
        groove_end = self.width() - self._handle_radius - 2
        if self._max == self._min:
            return groove_start
        ratio = (value - self._min) / (self._max - self._min)
        return groove_start + ratio * (groove_end - groove_start)

    def _handle_rect(self, value: int) -> QRect:
        cx = int(self._value_to_pos(value))
        r = self._handle_radius
        return QRect(cx - r, 0, 2 * r, 2 * r)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            low_rect = self._handle_rect(self._low)
            high_rect = self._handle_rect(self._high)
            if high_rect.contains(int(e.pos().x()), int(e.pos().y())):
                self._dragging = 'high'
                self._drag_start_val = self._high
            elif low_rect.contains(int(e.pos().x()), int(e.pos().y())):
                self._dragging = 'low'
                self._drag_start_val = self._low
            else:
                # click on groove - move nearest handle
                click_val = self._pos_to_value(e.pos().x())
                if abs(click_val - self._low) <= abs(click_val - self._high):
                    self._low = click_val
                    self._dragging = 'low'
                else:
                    self._high = click_val
                    self._dragging = 'high'
                self.update()
                self.rangeChanged.emit(self._low, self._high)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging == 'low':
            new_low = self._pos_to_value(e.pos().x())
            if new_low <= self._high:
                self._low = new_low
                self.update()
                self.rangeChanged.emit(self._low, self._high)
        elif self._dragging == 'high':
            new_high = self._pos_to_value(e.pos().x())
            if new_high >= self._low:
                self._high = new_high
                self.update()
                self.rangeChanged.emit(self._low, self._high)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._dragging = None

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        h = self.height()
        cy = h // 2
        groove_start = self._handle_radius + 2
        groove_end = self.width() - self._handle_radius - 2
        groove_w = groove_end - groove_start

        disabled = not self.isEnabled()

        # background groove
        painter.setPen(Qt.PenStyle.NoPen)
        if disabled:
            base = QColor(160, 160, 160, 60) if not isDarkTheme() else QColor(60, 60, 60, 60)
        else:
            base = QColor(200, 200, 200, 120) if not isDarkTheme() else QColor(80, 80, 80, 150)
        painter.setBrush(base)
        painter.drawRoundedRect(groove_start, cy - self._groove_height // 2, groove_w, self._groove_height, 2, 2)

        # highlighted range
        low_x = self._value_to_pos(self._low)
        high_x = self._value_to_pos(self._high)
        hl_color = themeColor()
        if disabled:
            hl_color = QColor(128, 128, 128, 80)
        painter.setBrush(hl_color)
        painter.drawRoundedRect(QRectF(low_x, cy - self._groove_height // 2, high_x - low_x, self._groove_height), 2, 2)

        # handles
        for val, label in [(self._low, str(self._low + 1)), (self._high, str(self._high + 1))]:
            cx = int(self._value_to_pos(val))
            # outer
            outer_color = QColor(128, 128, 128, 120) if disabled else themeColor()
            painter.setBrush(QColor(200, 200, 200) if disabled else QColor(255, 255, 255))
            painter.setPen(outer_color)
            painter.drawEllipse(QPoint(cx, cy), self._handle_radius, self._handle_radius)
            # inner dot
            painter.setPen(Qt.PenStyle.NoPen)
            dot_color = QColor(128, 128, 128, 120) if disabled else themeColor()
            painter.setBrush(dot_color)
            painter.drawEllipse(QPoint(cx, cy), 4, 4)
            # label below
            label_color = QColor(128, 128, 128, 120) if disabled else (
                QColor(255, 255, 255) if isDarkTheme() else QColor(60, 60, 60))
            painter.setPen(label_color)
            font = painter.font()
            font.setPixelSize(9)
            painter.setFont(font)
            tw = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(cx - tw // 2, h - 2, label)
