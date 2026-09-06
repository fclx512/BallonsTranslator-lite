"""Blender 式拖拽数值输入框（无箭头图标）。

交互：悬停显示 ↔ 光标；按住横向拖动连续调值（Shift 精调 ×0.1）；
单击进入正常键盘编辑；箭头键/滚轮等原生行为保留。
样式由 ``config/stylesheet.css``（NoArrowsSpinBox / NoArrowsDoubleSpinBox
选择器）负责，箭头经 ``ButtonSymbols.NoButtons`` 在代码层去除。

提供：
  - :class:`DragAdjustMixin` — 拖拽调值混入（:class:`SizeComboBox` 亦复用）
  - :class:`NoArrowsSpinBox`  — ``QSpinBox``（整数）
  - :class:`NoArrowsDoubleSpinBox` — ``QDoubleSpinBox``（浮点）
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QMouseEvent
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QSpinBox,
)

from utils import shared


def _drag_global_x(ev: QMouseEvent) -> float:
    if shared.FLAG_QT6:
        return float(ev.globalPosition().x())
    return float(ev.globalPos().x())


class DragAdjustMixin:
    """横向拖拽调值混入。

    press → move 超过阈值进入拖拽态（emit drag_started）→ release 结束
    （emit drag_finished）；未达阈值的 press/release 视为单击，进入编辑态。
    子类/宿主需实现 ``_apply_drag_value(value)``；QAbstractSpinBox 子类
    直接混入即可（mousePress/Move/Release + 光标管理已接管）。
    """

    drag_started = Signal()
    drag_finished = Signal()

    #: 每 singleStep 对应的横向像素数
    drag_px_per_step = 5.0
    #: 进入拖拽态的最小横向位移（px），小于此值的按住-松开视为单击
    drag_start_threshold = 4.0
    #: QAbstractSpinBox 子类直接接管鼠标事件；经 lineEdit 事件代理接入的
    #: 组合框置 False，避免吞掉下拉箭头区的点击
    drag_mixin_direct_mouse = True

    def _init_drag_state(self):
        self._drag_pending = False
        self._drag_active = False
        self._drag_press_x = 0.0
        self._drag_start_value = 0.0

    # ---- 子类钩子 -------------------------------------------------------

    def _drag_allowed(self) -> bool:
        readonly = getattr(self, "isReadOnly", lambda: False)()
        return self.isEnabled() and not readonly

    def _drag_step(self) -> float:
        """每 drag_px_per_step 像素对应的值增量。"""
        return self.singleStep()

    def _drag_baseline_value(self) -> float:
        """按下瞬间的基准值；QAbstractSpinBox 先把未确认的文本落成值。"""
        self.interpretText()
        return self.value()

    def _drag_value_for(self, raw: float) -> float:
        if hasattr(self, "decimals"):
            return round(raw, self.decimals())
        return int(round(raw))

    def _apply_drag_value(self, value: float):
        self.setValue(self._drag_value_for(value))

    # ---- 三段式（QAbstractSpinBox 子类直接用；组合框经 eventFilter 调） —

    def _drag_begin(self, ev: QMouseEvent) -> bool:
        if ev.button() != Qt.MouseButton.LeftButton or not self._drag_allowed():
            return False
        self._drag_pending = True
        self._drag_active = False
        self._drag_press_x = _drag_global_x(ev)
        self._drag_start_value = self._drag_baseline_value()
        ev.accept()
        return True

    def _drag_move(self, ev: QMouseEvent) -> bool:
        if not (self._drag_pending or self._drag_active):
            return False
        dx = _drag_global_x(ev) - self._drag_press_x
        if not self._drag_active and abs(dx) >= self.drag_start_threshold:
            self._drag_pending = False
            self._drag_active = True
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.drag_started.emit()
        if self._drag_active:
            step = self._drag_step()
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step *= 0.1
            self._apply_drag_value(
                self._drag_start_value + dx / self.drag_px_per_step * step
            )
            ev.accept()
            return True
        return True  # pending 态吞掉 move，避免 lineEdit 开始拖选文本

    def _drag_end(self, ev: QMouseEvent) -> bool:
        if ev.button() != Qt.MouseButton.LeftButton or not (
            self._drag_pending or self._drag_active
        ):
            return False
        was_active = self._drag_active
        self._drag_pending = False
        self._drag_active = False
        if was_active:
            self.setCursor(Qt.CursorShape.SizeHorCursor)  # 仍在悬停，恢复 ↔
            self.drag_finished.emit()
        else:
            self._enter_edit_mode()
        ev.accept()
        return True

    def _enter_edit_mode(self):
        self.setFocus()
        self.selectAll()
        self.setCursor(Qt.CursorShape.IBeamCursor)

    # ---- 光标管理 --------------------------------------------------------

    def _drag_hover_cursor(self):
        if self._drag_allowed():
            editing = getattr(self.lineEdit(), "hasFocus", lambda: False)()
            self.setCursor(
                Qt.CursorShape.IBeamCursor if editing
                else Qt.CursorShape.SizeHorCursor
            )

    def enterEvent(self, ev):
        self._drag_hover_cursor()
        return super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.unsetCursor()
        return super().leaveEvent(ev)

    def focusInEvent(self, ev):
        self.setCursor(Qt.CursorShape.IBeamCursor)
        return super().focusInEvent(ev)

    def focusOutEvent(self, ev):
        self._drag_hover_cursor()
        return super().focusOutEvent(ev)

    # ---- 鼠标事件 --------------------------------------------------------

    def mousePressEvent(self, ev: QMouseEvent):
        if self.drag_mixin_direct_mouse and self._drag_begin(ev):
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QMouseEvent):
        if (
            self.drag_mixin_direct_mouse
            and (self._drag_pending or self._drag_active)
        ):
            self._drag_move(ev)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if (
            self.drag_mixin_direct_mouse
            and (self._drag_pending or self._drag_active)
        ):
            self._drag_end(ev)
            return
        super().mouseReleaseEvent(ev)


class NoArrowsSpinBox(DragAdjustMixin, QSpinBox):
    """无箭头 + Blender 式拖拽的整数输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._init_drag_state()


class NoArrowsDoubleSpinBox(DragAdjustMixin, QDoubleSpinBox):
    """无箭头 + Blender 式拖拽的浮点输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._init_drag_state()
