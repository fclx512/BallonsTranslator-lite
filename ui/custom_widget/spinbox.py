"""Blender 式拖拽数值输入框（无箭头图标）。

交互：悬停显示 ↔ 光标；按住横向拖动连续调值（Shift 精调 ×0.1）；
单击进入正常键盘编辑；箭头键/滚轮等原生行为保留。
样式由 ``config/stylesheet.css``（NoArrowsSpinBox / NoArrowsDoubleSpinBox
选择器）负责，箭头经 ``ButtonSymbols.NoButtons`` 在代码层去除。

按下区必须挂到内部 ``QLineEdit`` 上（``_install_drag_edit_proxy``）：
数值框的编辑区几乎铺满整个控件，鼠标按下落在子控件身上、不会冒泡到
``QAbstractSpinBox.mousePressEvent``，只在边框那几像素里重载才是生效的。

提供：
  - :class:`DragAdjustMixin` — 拖拽调值混入（:class:`SizeComboBox` 亦复用）
  - :class:`NoArrowsSpinBox`  — ``QSpinBox``（整数）
  - :class:`NoArrowsDoubleSpinBox` — ``QDoubleSpinBox``（浮点）
"""

from qtpy.QtCore import QEvent, QObject, Qt, Signal
from qtpy.QtGui import QColor, QMouseEvent, QPalette
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
    子类/宿主需实现 ``_apply_drag_value(value)``。

    按下区接入方式二选一，两者可同时启用（数值框即如此：编辑区走代理、
    边框几像素走宿主自己的事件）：

    * ``drag_mixin_direct_mouse`` 为真时由宿主的 mousePress/Move/Release 处理；
    * 调用 ``_install_drag_edit_proxy()`` 把三段式挂到内部 lineEdit 的事件
      过滤器上。**数值框与可编辑组合框都必须用这一条**——它们的编辑区是
      覆盖大部分面积的 ``QLineEdit`` 子控件，落在它身上的按下不会冒泡到
      宿主，被覆盖区的原生选区行为吞掉后只剩犄角旮旯能拖。
    """

    drag_started = Signal()
    drag_finished = Signal()

    #: 每 singleStep 对应的横向像素数
    drag_px_per_step = 5.0
    #: 进入拖拽态的最小横向位移（px），小于此值的按住-松开视为单击
    drag_start_threshold = 4.0
    #: 宿主自己的鼠标事件是否接管三段式；经 lineEdit 事件代理接入的组合框
    #: 置 False，避免吞掉下拉箭头区的点击
    drag_mixin_direct_mouse = True

    def _init_drag_state(self):
        self._drag_pending = False
        self._drag_active = False
        self._drag_press_x = 0.0
        self._drag_start_value = 0.0
        self._drag_state = ""
        self._drag_hovered = False
        self._drag_base_palette = None
        self._drag_proxy_edit = None
        try:
            self._drag_base_palette = self.lineEdit().palette()
        except Exception:
            self._drag_base_palette = None

    def _install_drag_edit_proxy(self):
        """把三段式挂到内部 ``lineEdit()``（数值框编辑区 / 组合框可编辑框）。

        Qt 的鼠标事件先给到光标下的子控件，编辑区铺满控件时宿主只会在
        边框拿到事件；同时 lineEdit 自带按下拖选文本，不拦下来就与横向
        拖拽调值直接冲突。
        """
        try:
            le = self.lineEdit()
        except Exception:
            le = None
        if le is None or le is self._drag_proxy_edit:
            return
        self._drag_proxy_edit = le
        le.installEventFilter(self)

    def eventFilter(self, obj: QObject, ev) -> bool:
        if obj is self._drag_proxy_edit:
            t = ev.type()
            if t == QEvent.Type.MouseButtonPress:
                if self._drag_begin(ev):
                    # 拖拽可能越出框体，需显式抓取路由后续事件
                    obj.grabMouse()
                    return True
            elif t == QEvent.Type.MouseMove and (
                self._drag_pending or self._drag_active
            ):
                self._drag_move(ev)
                return True
            elif t == QEvent.Type.MouseButtonRelease and (
                self._drag_pending or self._drag_active
            ):
                if self._drag_end(ev):
                    if obj.mouseGrabber() is obj:
                        obj.releaseMouse()
                    return True
            elif t in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self._drag_hover_cursor()
        return super().eventFilter(obj, ev)

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
        # drag_integer 时拖拽吸附到整数（速率不变，只是去掉小数抖动）；
        # 其余按字段精度取整（QSpinBox 天然整数 / QDoubleSpinBox 用 decimals）
        if getattr(self, "drag_integer", False):
            return int(round(raw))
        if hasattr(self, "decimals"):
            return round(raw, self.decimals())
        return int(round(raw))

    def _apply_drag_value(self, value: float):
        self.setValue(self._drag_value_for(value))

    # ---- 三段式（宿主与 lineEdit 代理两条入口共用） -----------------------

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
            self._refresh_drag_appearance()
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
            self._refresh_drag_appearance()  # 松手仍在悬停，回到 hover 外观
            self.drag_finished.emit()
        else:
            self._enter_edit_mode()
        ev.accept()
        return True

    def _enter_edit_mode(self):
        self.setFocus()
        self.selectAll()
        self._refresh_drag_appearance()

    # ---- 外观状态（光标 + hover/拖拽提亮） -------------------------------

    def _set_drag_state(self, state: str):
        """写 dragState 动态属性并 repolish，驱动 QSS 的 hover/拖拽背景描边。

        状态不变时提前返回，避免拖拽中每 move 反复 repolish。
        """
        if getattr(self, "_drag_state", None) == state:
            return
        self._drag_state = state
        self.setProperty("dragState", state)
        st = self.style()
        st.unpolish(self)
        st.polish(self)

    def _set_drag_cursor(self, cursor):
        """同时设置顶层控件与其内部 lineEdit 的光标。

        内部 QLineEdit 自带 IBeam 文本光标、会覆盖父控件光标，若不在此一并
        设置，悬停/拖拽时看到的仍是文本选择样式。
        """
        try:
            le = self.lineEdit()
        except Exception:
            le = None
        if cursor is None:
            self.unsetCursor()
            if le is not None:
                le.unsetCursor()
        else:
            self.setCursor(cursor)
            if le is not None:
                le.setCursor(cursor)

    def _set_drag_text_color(self, state: str):
        """hover/拖拽时提亮数字（设内部 lineEdit 的 Text 色），否则还原。"""
        if getattr(self, "_drag_base_palette", None) is None:
            return
        try:
            le = self.lineEdit()
        except Exception:
            return
        if state in ("hover", "drag"):
            pal = le.palette()
            pal.setColor(
                QPalette.ColorRole.Text,
                QColor(shared.get_theme_color("@dragTextColor")),
            )
            le.setPalette(pal)
        else:
            le.setPalette(self._drag_base_palette)

    def _drag_state_from_flags(self) -> str:
        if self._drag_active:
            return "drag"
        try:
            editing = self.lineEdit().hasFocus()
        except Exception:
            editing = False
        if self._drag_pending or (self._drag_hovered and not editing):
            return "hover"
        return ""

    def _refresh_drag_appearance(self):
        state = self._drag_state_from_flags() if self._drag_allowed() else ""
        self._set_drag_state(state)
        try:
            editing = self.lineEdit().hasFocus()
        except Exception:
            editing = False
        pressing = self._drag_pending or self._drag_active
        if pressing:
            # 按下/拖拽优先：即使 lineEdit 已获焦，拖拽态仍显水平调整光标
            cursor = Qt.CursorShape.SizeHorCursor
        elif editing:
            cursor = Qt.CursorShape.IBeamCursor
        elif self._drag_allowed() and self._drag_hovered:
            cursor = Qt.CursorShape.SizeHorCursor
        else:
            cursor = None
        self._set_drag_cursor(cursor)
        self._set_drag_text_color(state)

    def _drag_hover_cursor(self):
        """悬停光标刷新（组合框走 lineEdit 事件代理时亦调用）。"""
        self._refresh_drag_appearance()

    def enterEvent(self, ev):
        self._drag_hovered = True
        self._refresh_drag_appearance()
        return super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._drag_hovered = False
        self._refresh_drag_appearance()
        return super().leaveEvent(ev)

    def focusInEvent(self, ev):
        self._refresh_drag_appearance()
        return super().focusInEvent(ev)

    def focusOutEvent(self, ev):
        self._refresh_drag_appearance()
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
        self._install_drag_edit_proxy()


class NoArrowsDoubleSpinBox(DragAdjustMixin, QDoubleSpinBox):
    """无箭头 + Blender 式拖拽的浮点输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._init_drag_state()
        self._install_drag_edit_proxy()
