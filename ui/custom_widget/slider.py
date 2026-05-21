from qtpy.QtWidgets import QWidget, QStyle, QSlider, QStyle, QStyleOptionSlider
from qtpy.QtCore import  Qt, QPropertyAnimation, QRect, QRectF, Signal, QPoint, Property
from qtpy.QtGui import QFontMetrics, QMouseEvent, QPainter, QFontMetrics, QColor, QBrush, QPen

from .helper import isDarkTheme, themeColor
from utils import shared as C


def slider_subcontrol_rect(r: QRect, widget: QWidget):
    if widget.orientation() == Qt.Orientation.Horizontal:
        y = widget.height() // 4
        h = y * 2
        r = QRect(r.x(), y, r.width(), h)
    else:
        x = widget.width() // 4
        w = x * 2
        r = QRect(x, r.y(), w, r.height())

    # seems a bit dumb, otherwise the handle is buggy
    if r.height() < r.width():
        r.setHeight(r.width())
    else:
        r.setWidth(r.height())
    return r


class SliderHandle(QWidget):
    """ Slider handle """

    pressed = Signal()
    released = Signal()

    def __init__(self, parent: QSlider):
        super().__init__(parent=parent)
        self.setFixedSize(22, 22)
        self._radius = 5
        self.radiusAni = QPropertyAnimation(self, b'radius', self)
        self.radiusAni.setDuration(100)

    @Property(int)
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, r):
        self._radius = r
        self.update()

    def enterEvent(self, e):
        self._startAni(6)

    def leaveEvent(self, e):
        self._startAni(5)

    def mousePressEvent(self, e):
        self._startAni(4)
        self.pressed.emit()

    def mouseReleaseEvent(self, e):
        self._startAni(6)
        self.released.emit()

    def _startAni(self, radius):
        self.radiusAni.stop()
        self.radiusAni.setStartValue(self.radius)
        self.radiusAni.setEndValue(radius)
        self.radiusAni.start()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # draw outer circle
        from ui.theme_helpers import slider_colors, is_dark_theme
        handle_outer, _ = slider_colors()
        painter.setPen(QColor(0, 0, 0, 90 if is_dark_theme() else 25))
        painter.setBrush(handle_outer)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # draw inner circle
        inner_color = themeColor()
        inner_color.setAlpha(127)
        painter.setBrush(inner_color)
        painter.drawEllipse(QPoint(11, 11), self.radius, self.radius)


class Slider(QSlider):
    """ A slider can be clicked

    modified from https://github.com/zhiyiYo/PyQt-Fluent-Widgets

    Constructors
    ------------
    * Slider(`parent`: QWidget = None)
    * Slider(`orient`: Qt.Orientation, `parent`: QWidget = None)
    """

    clicked = Signal(int)

    def __init__(self, orientation: Qt.Orientation, parent: QWidget = None):
        super().__init__(orientation, parent=parent)
        self.hovering = False
        self._postInit()

    def _postInit(self):
        self.handle = SliderHandle(self)
        self._pressedPos = QPoint()
        self.setOrientation(self.orientation())

        self.handle.pressed.connect(self.sliderPressed)
        self.handle.released.connect(self.sliderReleased)
        self.valueChanged.connect(self._adjustHandlePos)

    def setOrientation(self, orientation: Qt.Orientation) -> None:
        super().setOrientation(orientation)
        if orientation == Qt.Orientation.Horizontal:
            self.setMinimumHeight(22)
        else:
            self.setMinimumWidth(22)

    def mousePressEvent(self, e: QMouseEvent):
        self._pressedPos = e.pos()
        self.setValue(self._posToValue(e.pos()))
        self.clicked.emit(self.value())

    def mouseMoveEvent(self, e: QMouseEvent):
        self.setValue(self._posToValue(e.pos()))
        self._pressedPos = e.pos()
        self.sliderMoved.emit(self.value())

    @property
    def grooveLength(self):
        l = self.width() if self.orientation() == Qt.Orientation.Horizontal else self.height()
        return l - self.handle.width()

    def _adjustHandlePos(self):
        total = max(self.maximum() - self.minimum(), 1)
        delta = int((self.value() - self.minimum()) / total * self.grooveLength)

        if self.orientation() == Qt.Orientation.Vertical:
            self.handle.move(0, delta)
        else:
            self.handle.move(delta, 0)

    def _posToValue(self, pos: QPoint):
        pd = self.handle.width() / 2
        gs = max(self.grooveLength, 1)
        v = pos.x() if self.orientation() == Qt.Orientation.Horizontal else pos.y()
        return int((v - pd) / gs * (self.maximum() - self.minimum()) + self.minimum())

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        from ui.theme_helpers import slider_colors
        _, groove = slider_colors()
        painter.setBrush(groove)

        if self.orientation() == Qt.Orientation.Horizontal:
            self._drawHorizonGroove(painter)
        else:
            self._drawVerticalGroove(painter)

        if hasattr(self, 'draw_content') and self.hovering:
            # its a bad idea to display text like this, but I leave it as it is for now
            
            option = QStyleOptionSlider()
            self.initStyleOption(option)

            rect = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
            rect = slider_subcontrol_rect(rect, self)
            
            value = self.value()
            value_str = str(value)
                
            painter.setPen(QColor(*C.SLIDERHANDLE_COLOR,255))
            font = painter.font()
            font.setPointSizeF(8)
            fm = QFontMetrics(font)
            painter.setFont(font)

            is_hor = self.orientation() == Qt.Orientation.Horizontal
            if is_hor: 
                value_w = fm.boundingRect(value_str).width()
                dx = self.width() - value_w
            else:
                dx = dy = 0

            dy = self.height() - fm.height() + fm.descent()
            painter.drawText(dx, dy, value_str)

            if self.draw_content is not None:
                painter.drawText(0, dy, self.draw_content, )
                

    def _drawHorizonGroove(self, painter: QPainter):
        w, r = self.width(), self.handle.width() / 2
        painter.drawRoundedRect(QRectF(r, r-2, w-r*2, 4), 2, 2)

        if self.maximum() - self.minimum() == 0:
            return

        painter.setBrush(themeColor())
        aw = (self.value() - self.minimum()) / (self.maximum() - self.minimum()) * (w - r*2)
        painter.drawRoundedRect(QRectF(r, r-2, aw, 4), 2, 2)

    def _drawVerticalGroove(self, painter: QPainter):
        h, r = self.height(), self.handle.width() / 2
        painter.drawRoundedRect(QRectF(r-2, r, 4, h-2*r), 2, 2)

        if self.maximum() - self.minimum() == 0:
            return

        painter.setBrush(themeColor())
        ah = (self.value() - self.minimum()) / (self.maximum() - self.minimum()) * (h - r*2)
        painter.drawRoundedRect(QRectF(r-2, r, 4, ah), 2, 2)

    def resizeEvent(self, e):
        self._adjustHandlePos()

    def enterEvent(self, event) -> None:
        self.hovering = True
        self.update()
        return super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovering = False
        self.update()
        return super().leaveEvent(event)


class PaintQSlider(Slider):

    mouse_released = Signal()

    def __init__(self, draw_content = None, orientation=Qt.Orientation.Horizontal, *args, **kwargs):
        super().__init__(orientation, *args, **kwargs)
        self.draw_content = draw_content
        self.pressed: bool = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed = True
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed = False
            self.mouse_released.emit()
        return super().mouseReleaseEvent(event)


class RangeSlider(QWidget):
    """Dual-handle range slider for selecting a min-max range."""

    rangeChanged = Signal(int, int)

    def __init__(self, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._low = minimum
        self._high = maximum
        self._handle_size = 24
        self._groove_height = 6
        self.setFixedHeight(44)
        self.setMouseTracking(True)
        self._dragging = None  # 'low', 'high', or None
        self._hovered = None   # 'low', 'high', or None

    def low(self) -> int:
        return self._low

    def high(self) -> int:
        return self._high

    def set_range(self, lo: int, hi: int):
        lo = max(self._min, min(lo, self._max))
        hi = max(self._min, min(hi, self._max))
        if lo > hi:
            hi = lo
        if lo != self._low or hi != self._high:
            self._low = lo
            self._high = hi
            self.rangeChanged.emit(lo, hi)
            self.update()

    def _posToValue(self, x: float) -> int:
        w = self.width() - self._handle_size
        if w <= 0:
            return self._min
        ratio = (x - self._handle_size / 2) / w
        ratio = max(0.0, min(1.0, ratio))
        return int(self._min + ratio * (self._max - self._min))

    def _handleCenter(self, value: int) -> QPoint:
        w = self.width() - self._handle_size
        if w <= 0:
            x = self._handle_size // 2
        else:
            ratio = (value - self._min) / max(self._max - self._min, 1)
            x = self._handle_size // 2 + int(ratio * w)
        return QPoint(x, self.height() // 2)

    def _grooveRect(self) -> QRect:
        return QRect(self._handle_size // 2, (self.height() - self._groove_height) // 2,
                     self.width() - self._handle_size, self._groove_height)

    def _handleRect(self, value: int) -> QRect:
        c = self._handleCenter(value)
        hs = self._handle_size
        return QRect(c.x() - hs // 2, c.y() - hs // 2, hs, hs)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        pos = event.position().toPoint()
        low_dist = (pos - self._handleCenter(self._low)).manhattanLength()
        high_dist = (pos - self._handleCenter(self._high)).manhattanLength()

        threshold = self._handle_size
        if low_dist <= threshold and low_dist <= high_dist:
            self._dragging = 'low'
        elif high_dist <= threshold:
            self._dragging = 'high'
        else:
            v = self._posToValue(event.position().x())
            if abs(v - self._low) <= abs(v - self._high):
                self._dragging = 'low'
                self._low = max(self._min, min(v, self._high))
            else:
                self._dragging = 'high'
                self._high = min(self._max, max(v, self._low))
            self.rangeChanged.emit(self._low, self._high)
            self.update()

    def mouseMoveEvent(self, event):
        if not self.isEnabled():
            return
        pos = event.position().toPoint()
        low_center = self._handleCenter(self._low)
        high_center = self._handleCenter(self._high)
        thresh = self._handle_size // 2
        hovered = None
        if (pos - low_center).manhattanLength() <= thresh:
            hovered = 'low'
        elif (pos - high_center).manhattanLength() <= thresh:
            hovered = 'high'
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()

        if self._dragging is None:
            return
        v = self._posToValue(event.position().x())
        if self._dragging == 'low':
            v = max(self._min, min(v, self._high))
            if v != self._low:
                self._low = v
                self.rangeChanged.emit(self._low, self._high)
                self.update()
        elif self._dragging == 'high':
            v = min(self._max, max(v, self._low))
            if v != self._high:
                self._high = v
                self.rangeChanged.emit(self._low, self._high)
                self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None
        self.update()

    def leaveEvent(self, event):
        self._hovered = None
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        enabled = self.isEnabled()
        isDark = isDarkTheme()

        groove = self._grooveRect()

        if enabled:
            groove_bg = QColor(255, 255, 255, 100) if isDark else QColor(0, 0, 0, 60)
            active_brush = QBrush(themeColor())
        else:
            groove_bg = QColor(128, 128, 128, 40)
            active_brush = QBrush(QColor(128, 128, 128, 50))

        # Background groove
        painter.setBrush(groove_bg)
        painter.drawRoundedRect(groove, 3, 3)

        # Active (highlighted) range
        lo_center = self._handleCenter(self._low)
        hi_center = self._handleCenter(self._high)
        lx, hx = lo_center.x(), hi_center.x()
        active = QRect(lx, groove.y(), hx - lx, groove.height())
        if active.width() > 2:
            painter.setBrush(active_brush)
            painter.drawRoundedRect(active, 3, 3)

        # Handles
        for val, is_hovered in ((self._low, self._hovered == 'low'),
                                (self._high, self._hovered == 'high')):
            center = self._handleCenter(val)
            hs = self._handle_size
            outer_rect = QRect(center.x() - hs // 2 + 1, center.y() - hs // 2 + 1,
                               hs - 2, hs - 2)

            if enabled:
                # Outer ring — slightly larger on hover
                ring_size = 7 if is_hovered else 5
                painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
                painter.setBrush(QColor(55, 55, 55) if isDark else QColor(230, 233, 240))
                painter.drawEllipse(outer_rect)

                # Inner colored dot
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(themeColor())
                painter.drawEllipse(center, ring_size, ring_size)
            else:
                # Disabled: muted outline, no fill dot
                painter.setPen(QPen(QColor(128, 128, 128, 80), 1))
                painter.setBrush(QColor(100, 100, 100, 40) if isDark else QColor(200, 200, 200, 50))
                painter.drawEllipse(outer_rect)
