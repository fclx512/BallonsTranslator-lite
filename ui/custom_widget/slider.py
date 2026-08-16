from qtpy.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from qtpy.QtGui import QBrush, QColor, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QGraphicsOpacityEffect, QSlider, QWidget

from utils.config import pcfg

from .helper import borderColor, isDarkTheme, themeColor, widgetBackgroundColor


class SliderValueTip(QWidget):
    """MD3-style value bubble floating above the slider handle.

    A frameless top-level tool window with translucent background, so it
    can overflow the slider's small bounds without disturbing layout or
    capturing mouse events. Colors are re-resolved on every paint, so
    theme switches apply to a visible bubble immediately.
    """

    _BUBBLE_H = 26
    _TAIL_H = 7
    _PADDING_X = 10
    _GAP = 1  # gap between the tail tip and the handle top

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text = ""
        self._has_tail = True
        font = self.font()
        font.setPointSize(max(font.pointSize() - 1, 7))
        self.setFont(font)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._ani = QPropertyAnimation(self._effect, b"opacity", self)
        self._ani.setDuration(100)
        self._ani.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ani.finished.connect(lambda: self._effect.setOpacity(1.0))
        self.hide()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self._text) + 2 * self._PADDING_X
        h = self._BUBBLE_H + (self._TAIL_H if self._has_tail else 0)
        return QSize(w, h)

    def setHasTail(self, has: bool):
        """Whether the bubble draws its downward pointer triangle."""
        if has != self._has_tail:
            self._has_tail = has
            self.update()

    def showTip(self, slider: QWidget, handle_top_center: QPoint, text: str):
        """Show the bubble with its tail tip pointing at handle_top_center
        (a point in ``slider`` coordinates)."""
        if not slider.isEnabled():
            self.hideTip()
            return
        self._text = text
        self.adjustSize()
        w, h = self.sizeHint().width(), self.sizeHint().height()
        # never wider than the slider, so the bubble cannot cover neighbours
        w = min(w, slider.width())
        self.resize(w, h)
        c = slider.mapToGlobal(handle_top_center)
        x = c.x() - w // 2
        y = c.y() - self._GAP - h
        # keep the bubble within the slider's horizontal span
        left = slider.mapToGlobal(QPoint(0, 0)).x()
        right = left + slider.width()
        x = max(left, min(x, right - w))
        self.move(x, y)
        if not self.isVisible():
            self.show()
            self._fade_in()
        else:
            self.update()

    def hideTip(self):
        if self.isVisible():
            self.hide()

    def _fade_in(self):
        if pcfg.animation_fps < 0:
            self._effect.setOpacity(1.0)
            return
        self._effect.setOpacity(0.0)
        self._ani.stop()
        self._ani.setStartValue(0.0)
        self._ani.setEndValue(1.0)
        self._ani.start()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        bg = themeColor()
        painter.setBrush(bg)
        bubble = QRectF(0, 0, self.width(), self._BUBBLE_H)
        painter.drawRoundedRect(bubble, self._BUBBLE_H / 2, self._BUBBLE_H / 2)

        # tail: small triangle pointing down at the handle
        if self._has_tail:
            tail_top = bubble.bottom() - 1
            path = QPainterPath()
            path.moveTo(self.width() / 2 - 7, tail_top)
            path.lineTo(self.width() / 2 + 7, tail_top)
            path.lineTo(self.width() / 2, self.height() - 1)
            path.closeSubpath()
            painter.drawPath(path)

        painter.setFont(self.font())
        painter.setPen(self._textColor(bg))
        painter.drawText(bubble, Qt.AlignmentFlag.AlignCenter, self._text)

    @staticmethod
    def _textColor(bg: QColor) -> QColor:
        lum = (0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()) / 255
        return QColor(255, 255, 255) if lum < 0.5 else QColor(0, 0, 0)


class SliderHandle(QWidget):
    """Slider handle"""

    # Custom radius QPropertyAnimation — stays widget-based.
    # 100ms animation on a custom paint property; cost is one small
    # repaint per frame.  Qt6 RHI provides GPU compositing for paint output.

    pressed = Signal()
    released = Signal()

    def __init__(self, parent: QSlider):
        super().__init__(parent=parent)
        self.setFixedSize(22, 22)
        self._radius = 5
        self.radiusAni = QPropertyAnimation(self, b"radius", self)
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

        # draw outer circle — theme-aware border + fill (upstream pattern)
        painter.setPen(borderColor())
        painter.setBrush(widgetBackgroundColor())
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # draw inner circle — fully opaque accent color
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(themeColor())
        painter.drawEllipse(QPoint(11, 11), self.radius, self.radius)


class Slider(QSlider):
    """A slider can be clicked

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

        self._tip = SliderValueTip()
        self._label_tip = SliderValueTip()  # fixed caption bubble for draw_content
        self._label_tip.setHasTail(False)  # caption is self-explanatory, no pointer
        self._interacting = False
        self._value_format = str
        self._tip_prefix = ""
        self.sliderPressed.connect(self._onSliderPressed)
        self.sliderReleased.connect(self._onSliderReleased)
        self.valueChanged.connect(self._updateTip)

    def setValueFormat(self, fmt):
        """Set a callable ``fmt(value) -> str`` for the value bubble text."""
        self._value_format = fmt if callable(fmt) else str

    # caption bubble sits above the value bubble, horizontally centered and
    # fixed (does not follow the handle) so the label stays visually stable
    _LABEL_TIP_GAP = 38  # value bubble (33px) + gap (1) + spacing (4)

    def _showLabelTip(self):
        if not self._tip_prefix or not self.isEnabled():
            return
        self._label_tip.showTip(
            self, QPoint(self.width() // 2, -self._LABEL_TIP_GAP), self._tip_prefix
        )

    def _hideLabelTip(self):
        self._label_tip.hideTip()

    def _onSliderPressed(self):
        self._interacting = True
        self._updateTip()

    def _onSliderReleased(self):
        self._interacting = False
        if not self.hovering:
            self._tip.hideTip()
        else:
            self._updateTip()

    def _updateTip(self):
        if not self.isEnabled():
            self._tip.hideTip()
            return
        if not (self._interacting or self.hovering or self.hasFocus()):
            return
        handle_top = QPoint(
            self.handle.pos().x() + self.handle.width() // 2,
            self.handle.pos().y(),
        )
        self._tip.showTip(self, handle_top, self._value_format(self.value()))

    def setOrientation(self, orientation: Qt.Orientation) -> None:
        super().setOrientation(orientation)
        if orientation == Qt.Orientation.Horizontal:
            self.setMinimumHeight(22)
        else:
            self.setMinimumWidth(22)

    def mousePressEvent(self, e: QMouseEvent):
        self._pressedPos = e.pos()
        self._interacting = True
        self.setValue(self._posToValue(e.pos()))
        self.clicked.emit(self.value())
        self._updateTip()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._interacting = False
        if not self.hovering:
            self._tip.hideTip()
        return super().mouseReleaseEvent(e)

    def focusOutEvent(self, e) -> None:
        if not self.hovering:
            self._tip.hideTip()
        return super().focusOutEvent(e)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._tip.hideTip()
            self._hideLabelTip()

    def mouseMoveEvent(self, e: QMouseEvent):
        self.setValue(self._posToValue(e.pos()))
        self._pressedPos = e.pos()
        self.sliderMoved.emit(self.value())

    @property
    def grooveLength(self):
        length = (
            self.width()
            if self.orientation() == Qt.Orientation.Horizontal
            else self.height()
        )
        return length - self.handle.width()

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
        painter.setBrush(self.grooveColor())

        if self.orientation() == Qt.Orientation.Horizontal:
            self._drawHorizonGroove(painter)
        else:
            self._drawVerticalGroove(painter)

    def _drawHorizonGroove(self, painter: QPainter):
        w, r = self.width(), self.handle.width() / 2
        painter.drawRoundedRect(QRectF(r, r - 2, w - r * 2, 4), 2, 2)

        if self.maximum() - self.minimum() == 0:
            return

        painter.setBrush(themeColor())
        aw = (
            (self.value() - self.minimum())
            / (self.maximum() - self.minimum())
            * (w - r * 2)
        )
        painter.drawRoundedRect(QRectF(r, r - 2, aw, 4), 2, 2)

    def _drawVerticalGroove(self, painter: QPainter):
        h, r = self.height(), self.handle.width() / 2
        painter.drawRoundedRect(QRectF(r - 2, r, 4, h - 2 * r), 2, 2)

        if self.maximum() - self.minimum() == 0:
            return

        painter.setBrush(themeColor())
        ah = (
            (self.value() - self.minimum())
            / (self.maximum() - self.minimum())
            * (h - r * 2)
        )
        painter.drawRoundedRect(QRectF(r - 2, r, 4, ah), 2, 2)

    def grooveColor(self):
        return borderColor()

    def resizeEvent(self, e):
        self._adjustHandlePos()
        self._updateTip()
        if self.hovering:
            self._showLabelTip()

    def keyPressEvent(self, e):
        key = e.key()
        # Block number key responses — QSlider's built-in page-stepping
        # is acceptable, but 0-9 mapping to proportional values is
        # meaningless here and conflicts with other shortcuts.
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            e.ignore()
            return
        super().keyPressEvent(e)

    def enterEvent(self, event) -> None:
        self.hovering = True
        self._showLabelTip()
        self._updateTip()
        self.update()
        return super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovering = False
        if not self._interacting:
            self._hideLabelTip()
            self._tip.hideTip()
        self.update()
        return super().leaveEvent(event)


class PaintQSlider(Slider):
    mouse_released = Signal()

    def __init__(
        self, draw_content=None, orientation=Qt.Orientation.Horizontal, *args, **kwargs
    ):
        super().__init__(orientation, *args, **kwargs)
        self.draw_content = draw_content
        # description text (e.g. "Text layer opacity") leads the value bubble
        if draw_content:
            self._tip_prefix = draw_content
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
        self._hovered = None  # 'low', 'high', or None
        self._tip = SliderValueTip()

    def _showTip(self, value: int):
        c = self._handleCenter(value)
        handle_top = QPoint(c.x(), c.y() - self._handle_size // 2)
        self._tip.showTip(self, handle_top, str(value))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._tip.hideTip()

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
        return QRect(
            self._handle_size // 2,
            (self.height() - self._groove_height) // 2,
            self.width() - self._handle_size,
            self._groove_height,
        )

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
            self._dragging = "low"
        elif high_dist <= threshold:
            self._dragging = "high"
        else:
            v = self._posToValue(event.position().x())
            if abs(v - self._low) <= abs(v - self._high):
                self._dragging = "low"
                self._low = max(self._min, min(v, self._high))
            else:
                self._dragging = "high"
                self._high = min(self._max, max(v, self._low))
            self.rangeChanged.emit(self._low, self._high)
            self.update()
        if self._dragging:
            self._showTip(self._low if self._dragging == "low" else self._high)

    def mouseMoveEvent(self, event):
        if not self.isEnabled():
            return
        pos = event.position().toPoint()
        low_center = self._handleCenter(self._low)
        high_center = self._handleCenter(self._high)
        thresh = self._handle_size // 2
        hovered = None
        if (pos - low_center).manhattanLength() <= thresh:
            hovered = "low"
        elif (pos - high_center).manhattanLength() <= thresh:
            hovered = "high"
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()
            if hovered:
                self._showTip(self._low if hovered == "low" else self._high)
            else:
                self._tip.hideTip()

        if self._dragging is None:
            return
        v = self._posToValue(event.position().x())
        if self._dragging == "low":
            v = max(self._min, min(v, self._high))
            if v != self._low:
                self._low = v
                self.rangeChanged.emit(self._low, self._high)
                self.update()
        elif self._dragging == "high":
            v = min(self._max, max(v, self._low))
            if v != self._high:
                self._high = v
                self.rangeChanged.emit(self._low, self._high)
                self.update()
        if self._dragging:
            self._showTip(self._low if self._dragging == "low" else self._high)

    def mouseReleaseEvent(self, event):
        self._dragging = None
        self.update()
        if not self._hovered:
            self._tip.hideTip()

    def leaveEvent(self, event):
        self._hovered = None
        self._tip.hideTip()
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
        for val, is_hovered in (
            (self._low, self._hovered == "low"),
            (self._high, self._hovered == "high"),
        ):
            center = self._handleCenter(val)
            hs = self._handle_size
            outer_rect = QRect(
                center.x() - hs // 2 + 1, center.y() - hs // 2 + 1, hs - 2, hs - 2
            )

            if enabled:
                # Outer ring — slightly larger on hover
                ring_size = 7 if is_hovered else 5
                painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
                painter.setBrush(
                    QColor(55, 55, 55) if isDark else QColor(230, 233, 240)
                )
                painter.drawEllipse(outer_rect)

                # Inner colored dot
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(themeColor())
                painter.drawEllipse(center, ring_size, ring_size)
            else:
                # Disabled: muted outline, no fill dot
                painter.setPen(QPen(QColor(128, 128, 128, 80), 1))
                painter.setBrush(
                    QColor(100, 100, 100, 40) if isDark else QColor(200, 200, 200, 50)
                )
                painter.drawEllipse(outer_rect)
