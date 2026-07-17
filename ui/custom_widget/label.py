from typing import List, Tuple, Union

import numpy as np
from qtpy.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from qtpy.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from qtpy.QtWidgets import QDialog, QGraphicsOpacityEffect, QLabel, QMenu

from utils import shared
from utils.shared import CONFIG_FONTSIZE_CONTENT


class FadeLabel(QLabel):
    # QGraphicsOpacityEffect animation — stays widget-based.
    # Embedded in QGraphicsScene via QGraphicsProxyWidget, so QQuickWidget
    # is not feasible.  Qt6 RHI (ANGLE/D3D11) provides GPU compositing.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # https://stackoverflow.com/questions/57828052/qpropertyanimation-not-working-with-window-opacity
        effect = QGraphicsOpacityEffect(self, opacity=1.0)
        self.setGraphicsEffect(effect)
        self.fadeAnimation = QPropertyAnimation(
            self,
            propertyName=b"opacity",
            targetObject=effect,
            duration=1200,
            startValue=1.0,
            endValue=0.0,
        )
        self.fadeAnimation.setEasingCurve(QEasingCurve.Type.InOutExpo)
        self.fadeAnimation.finished.connect(self.hide)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self.setHidden(True)
        self.gv = None

    def startFadeAnimation(self):
        from utils.config import pcfg

        if pcfg.animation_fps < 0:
            self.show()
            self.fadeAnimation.stop()
            self.graphicsEffect().setOpacity(1.0)
            self.hide_timer.start(1200)
            return
        self.hide_timer.stop()
        self.show()
        self.fadeAnimation.stop()
        self.fadeAnimation.start()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.gv is not None:
            self.gv.wheelEvent(event)
        return super().wheelEvent(event)


class ColorPickerLabel(QLabel):
    colorChanged = Signal(bool)
    apply_color = Signal(str, tuple)
    changingColor = Signal()

    def __init__(self, parent=None, param_name="", *args, **kwargs):
        super().__init__(parent=parent, *args, **kwargs)
        self.color = QColor(0, 0, 0)
        self.param_name = param_name
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.changingColor.emit()
            from .color_picker import ColorPickerDialog

            dlg = ColorPickerDialog(self.color, self.window())
            if dlg.exec_() == QDialog.DialogCode.Accepted:
                self.setPickerColor(dlg.get_color())
            self.colorChanged.emit(True)
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            apply_act = menu.addAction(self.tr("Apply Color"))
            rst = menu.exec(event.globalPosition().toPoint())
            if rst == apply_act and self.color is not None:
                self.apply_color.emit(self.param_name, self.rgb())

    def setPickerColor(self, color: Union[QColor, List, Tuple]):
        if not isinstance(color, QColor):
            if isinstance(color, np.ndarray):
                color = np.round(color).astype(np.uint8).tolist()
            if isinstance(color, (list, tuple)):
                color = QColor(*[max(0, min(255, int(c))) for c in color[:4]])
            else:
                color = QColor(color)
        self.color = color
        r, g, b = color.red(), color.green(), color.blue()
        self.setToolTip(f"RGB({r}, {g}, {b})  #{r:02x}{g:02x}{b:02x}".upper())
        self.update()

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        return super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        return super().leaveEvent(e)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)
        radius = min(rect.width(), rect.height()) // 3
        rf = QRectF(rect)

        # Checkerboard background (shows when color has alpha < 255)
        check = QPixmap(8, 8)
        check.fill(QColor(255, 255, 255))
        cp = QPainter(check)
        cp.fillRect(0, 0, 4, 4, QColor(200, 200, 200))
        cp.fillRect(4, 4, 4, 4, QColor(200, 200, 200))
        cp.end()
        painter.setBrush(QBrush(check))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rf, radius, radius)

        # Color fill (blends with checkerboard when alpha < 255)
        painter.setBrush(QBrush(self.color))
        painter.drawRoundedRect(rf, radius, radius)

        # Border
        if self._hovered:
            border_color = QColor(30, 147, 229)
        else:
            border_color = QColor(self.color)
            border_color.setAlpha(120)
        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rf, radius, radius)

    def rgb(self) -> List:
        color = self.color
        return (color.red(), color.green(), color.blue())

    def rgba(self) -> List:
        color = self.color
        return (color.red(), color.green(), color.blue(), color.alpha())


class SmallColorPickerLabel(ColorPickerLabel):
    pass


class ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, text=None, parent=None, *args, **kwargs):
        super().__init__(parent=parent, *args, **kwargs)
        if text is not None:
            self.setText(text)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        return super().mousePressEvent(e)


class ConfigClickableLabel(ClickableLabel):
    pass


class CheckableLabel(QLabel):
    checkStateChanged = Signal(bool)

    def __init__(
        self,
        checked_text: str,
        unchecked_text: str,
        default_checked: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.checked_text = checked_text
        self.unchecked_text = unchecked_text
        self.checked = default_checked
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if default_checked:
            self.setText(checked_text)
        else:
            self.setText(unchecked_text)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self.checked)
            self.checkStateChanged.emit(self.checked)
        return super().mousePressEvent(e)

    def setChecked(self, checked: bool):
        self.checked = checked
        if checked:
            self.setText(self.checked_text)
        else:
            self.setText(self.unchecked_text)


class TextCheckerLabel(QLabel):
    checkStateChanged = Signal(bool)

    def __init__(self, text: str, checked: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setText(text)
        self.setCheckState(checked)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setCheckState(self, checked: bool):
        self.checked = checked
        if checked:
            from ui.misc import get_theme_color

            c = get_theme_color()
            self.setStyleSheet(
                f"QLabel {{ background-color: {c.name()}; color: white; "
                f"padding: 2px 10px; }}"
            )
        else:
            self.setStyleSheet("")

    def isChecked(self):
        return self.checked

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCheckState(not self.checked)
            self.checkStateChanged.emit(self.checked)


class ParamNameLabel(QLabel):
    def __init__(self, param_name: str, alignment=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if alignment is None:
            self.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        else:
            self.setAlignment(alignment)

        font = self.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT - 2)
        self.setFont(font)
        self.setText(param_name)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class SmallParamLabel(QLabel):
    def __init__(self, param_name: str, alignment=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if alignment is None:
            self.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        else:
            self.setAlignment(alignment)

        self.setText(param_name)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class SizeControlLabel(QLabel):
    btn_released = Signal()
    size_ctrl_changed = Signal(int)

    def __init__(
        self, parent=None, direction=0, text="", alignment=None, transparent_bg=True
    ):
        super().__init__(parent)
        if text:
            self.setText(text)
        if direction == 0:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.cur_pos = 0
        self.direction = direction
        self.mouse_pressed = False
        if transparent_bg:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if alignment is not None:
            self.setAlignment(alignment)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.mouse_pressed = True
            if shared.FLAG_QT6:
                g_pos = e.globalPosition().toPoint()
            else:
                g_pos = e.globalPos()
            self.cur_pos = g_pos.x() if self.direction == 0 else g_pos.y()
        return super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.mouse_pressed = False
            self.btn_released.emit()
        return super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self.mouse_pressed:
            if shared.FLAG_QT6:
                g_pos = e.globalPosition().toPoint()
            else:
                g_pos = e.globalPos()
            if self.direction == 0:
                new_pos = g_pos.x()
                self.size_ctrl_changed.emit(new_pos - self.cur_pos)
            else:
                new_pos = g_pos.y()
                self.size_ctrl_changed.emit(self.cur_pos - new_pos)
            self.cur_pos = new_pos
        return super().mouseMoveEvent(e)


class SmallSizeControlLabel(SizeControlLabel):
    pass
