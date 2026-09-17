from typing import Callable, List, Optional

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QDoubleValidator, QMouseEvent, QPalette, QWheelEvent
from qtpy.QtWidgets import (
    QComboBox,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QWidget,
)

from ui.icon_rendering import render_svg_pixmap
from utils.shared import (
    CONFIG_COMBOBOX_HEIGHT,
    CONFIG_COMBOBOX_LONG,
    CONFIG_COMBOBOX_MIDEAN,
    CONFIG_COMBOBOX_SHORT,
)

from .push_button import NoBorderPushBtn
from .spinbox import DragAdjustMixin


def _format_drag_value(value: float) -> str:
    """把拖拽/设置后的数值显示成去尾零字符串（5.0 → "5"，10.50 → "10.5"）。"""
    text = str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def themed_icon(filename: str) -> str:
    """惰性解析图标主题路径（避免 ui.misc 早导入环）。"""
    from ui.misc import themed_icon_path

    return themed_icon_path(filename)


class ComboBox(QComboBox):
    # https://stackoverflow.com/questions/3241830/qt-how-to-disable-mouse-scrolling-of-qcombobox
    def __init__(
        self,
        parent: QWidget = None,
        scrollWidget: QWidget = None,
        options: List[str] = None,
    ) -> None:
        super().__init__(parent)
        self.scrollWidget = scrollWidget
        if options is not None:
            self.addItems(options)

    def setScrollWidget(self, scrollWidget: QWidget):
        self.scrollWidget = scrollWidget

    def wheelEvent(self, *args, **kwargs):
        if self.scrollWidget is None or self.hasFocus():
            return super().wheelEvent(*args, **kwargs)
        else:
            return self.scrollWidget.wheelEvent(*args, **kwargs)


class SmallComboBox(ComboBox):
    pass


class WidePopupComboMixin:
    """下拉弹出列表按最长条目撑宽，闭合态宽度不受内容牵引。

    组合框本体保持布局给定的稳定宽度（长名截断显示）；点击展开时把
    弹出视图的最小宽度抬到最长条目宽，完整显示不裁剪。这正是效果卡
    选择器/行距类型等长文本下拉栏需要的「可变宽」特性——弹出层可以宽于
    闭合框（放弃对齐），但闭合态不撑破卡片布局。
    """

    def showPopup(self):
        view = self.view()
        if view is not None:
            need = view.sizeHintForColumn(0)
            if need >= 0:
                # 弹出后滚动条可能出现，预留其宽度
                need += view.verticalScrollBar().sizeHint().width()
                need += 2 * view.frameWidth() + 8
                view.setMinimumWidth(max(need, self.width()))
        super().showPopup()


class BottomBorderComboBox(WidePopupComboMixin, QComboBox):
    """效果卡头部/参数区的紧凑选择器：内联 chevron 图标 + 可选文本对齐。

    上游 v1.5.13 同名控件的下划线视觉由 fork QSS 重皮为填充输入框风格
    （objectName TextEffectParamEditor，与效果参数编辑器同款）；尺寸采样
    语义保留——setWidthSampleText 抬高 sizeHint 下限，闭合态仍可收缩。
    弹出列表经 :class:`WidePopupComboMixin` 撑宽到最长条目，闭合框被
    卡片压缩时选项名仍完整可读。
    """

    ARROW_SIZE = 12

    def __init__(
        self,
        parent: QWidget = None,
        *,
        text_alignment: Optional[Qt.AlignmentFlag] = None,
    ) -> None:
        super().__init__(parent)
        self._text_alignment = text_alignment
        self._width_sample_text: Optional[str] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 本控件自绘 chevron（paintEvent），须让 QSS 关掉原生 ::down-arrow，
        # 否则两个箭头叠在一起（fork QSS 的 [bottomBorderSelector] 规则）。
        self.setProperty('bottomBorderSelector', True)

    def setWidthSampleText(self, text: str) -> None:
        """Prefer room for ``text`` while retaining normal shrink behavior."""
        self._width_sample_text = text
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        if not self._width_sample_text:
            return size
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        contents = QSize(
            option.fontMetrics.horizontalAdvance(self._width_sample_text),
            option.fontMetrics.height(),
        )
        reference = self.style().sizeFromContents(
            QStyle.ContentsType.CT_ComboBox,
            option,
            contents,
            self,
        )
        size.setWidth(max(size.width(), reference.width()))
        return size

    def paintEvent(self, event) -> None:
        if self._text_alignment is None:
            super().paintEvent(event)
            painter = QStylePainter(self)
        else:
            option = QStyleOptionComboBox()
            self.initStyleOption(option)
            current_text = option.currentText
            option.currentText = ""
            painter = QStylePainter(self)
            painter.drawComplexControl(
                QStyle.ComplexControl.CC_ComboBox, option
            )
            painter.drawControl(
                QStyle.ControlElement.CE_ComboBoxLabel, option
            )
            text_rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                self,
            ).adjusted(2, 0, -2, 0)
            color_group = (
                QPalette.ColorGroup.Active
                if self.isEnabled()
                else QPalette.ColorGroup.Disabled
            )
            color_role = (
                QPalette.ColorRole.PlaceholderText
                if self.currentIndex() < 0
                else QPalette.ColorRole.Text
            )
            painter.setPen(option.palette.color(color_group, color_role))
            painter.drawText(
                text_rect,
                self._text_alignment | Qt.AlignmentFlag.AlignVCenter,
                option.fontMetrics.elidedText(
                    current_text,
                    Qt.TextElideMode.ElideRight,
                    max(0, text_rect.width()),
                ),
            )
        pixmap = render_svg_pixmap(
            themed_icon("chevron-down.svg"),
            self.ARROW_SIZE,
            self.ARROW_SIZE,
            self.devicePixelRatioF(),
        )
        x = self.width() - self.ARROW_SIZE - 4
        y = (self.height() - self.ARROW_SIZE) // 2
        painter.drawPixmap(x, y, pixmap)
        painter.end()


class ConfigComboBox(ComboBox):
    def __init__(
        self,
        fix_size=True,
        scrollWidget: QWidget = None,
        options: List[str] = None,
        stretch: bool = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(scrollWidget, *args, **kwargs)
        self.fix_size = fix_size
        # stretch=True 时不做宽度分级锁定，交给布局横向拉伸（整行下拉用）。
        self.stretch = stretch
        self.adjustSize()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if options:
            self.addItems(options)

    def addItems(self, texts: List[str]) -> None:
        super().addItems(texts)
        self.adjustSize()

    def adjustSize(self) -> None:
        super().adjustSize()
        if self.stretch:
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            return
        width = self.minimumSizeHint().width()
        if width < CONFIG_COMBOBOX_SHORT:
            width = CONFIG_COMBOBOX_SHORT
        elif width < CONFIG_COMBOBOX_MIDEAN:
            width = CONFIG_COMBOBOX_MIDEAN
        else:
            width = CONFIG_COMBOBOX_LONG
        if self.fix_size:
            self.setFixedWidth(width)
        else:
            self.setMaximumWidth(width)


class ParamComboBox(ComboBox):
    paramwidget_edited = Signal(str, str)
    flushbtn_clicked = Signal()
    pathbtn_clicked = Signal()

    def __init__(
        self,
        param_key: str,
        options: List[str],
        size=CONFIG_COMBOBOX_SHORT,
        scrollWidget: QWidget = None,
        flush_btn: bool = False,
        path_selector: bool = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(scrollWidget=scrollWidget, *args, **kwargs)
        self.param_key = param_key
        self.setFixedWidth(size)
        self.setFixedHeight(CONFIG_COMBOBOX_HEIGHT)
        options = [str(opt) for opt in options]
        self.addItems(options)
        self.currentTextChanged.connect(self.on_select_changed)

        if flush_btn:
            self.flush_btn = NoBorderPushBtn(self.tr("Flush"))
            self.flush_btn.clicked.connect(self.flushbtn_clicked)
        if path_selector:
            self.path_select_btn = NoBorderPushBtn(self.tr("Select Path"))
            self.path_select_btn.clicked.connect(self.pathbtn_clicked)

    def on_select_changed(self):
        self.paramwidget_edited.emit(self.param_key, self.currentText())


class SizeComboBox(DragAdjustMixin, QComboBox):
    """可编辑数值下拉框：预设项点选 + Blender 式横向拖拽调值 + 键盘输入。

    拖拽经 lineEdit 事件代理接入（:class:`DragAdjustMixin`）；按下区域在
    lineEdit 才拦截，下拉箭头区保持原生弹列表。拖拽中仅静默刷新显示，
    松手经 ``drag_finished`` 发一次 ``param_changed``（对齐旧标签
    btn_released 语义，避免撤销条目逐帧膨胀）；手输仍走 ``param_changed``。
    """

    param_changed = Signal(str, float)

    # 鼠标事件由 lineEdit 事件代理接管，混入的直接鼠标处理需绕过
    drag_mixin_direct_mouse = False

    def __init__(
        self,
        val_range: List = None,
        param_name: str = "",
        parent=None,
        init_value=None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.param_name = param_name
        # 每步灵敏度（drag_px_per_step 像素对应的值增量），可设为
        # callable 动态给出（如行距 Distance 类型加大步长）；None 用默认
        self.drag_step_provider = None
        # 值保留的小数位数（拖拽取整/显示用）；字号等粗调字段设为 1
        self.precision = 2
        self.editTextChanged.connect(self.on_text_changed)
        self.activated.connect(self.on_current_index_changed)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.min_val = val_range[0]
        self.max_val = val_range[1]
        validator = QDoubleValidator()
        if val_range is not None:
            validator.setTop(val_range[1])
            validator.setBottom(val_range[0])
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)

        self.setValidator(validator)
        self._value = 0
        if init_value is not None:
            self.setValue(init_value)
        self._init_drag_state()
        self._install_drag_edit_proxy()
        # 拖拽结束提交一次（对齐旧标签 btn_released 语义）
        self.drag_finished.connect(
            lambda: self.param_changed.emit(self.param_name, self.value())
        )

    # ---- DragAdjustMixin 钩子 --------------------------------------------

    def _drag_step(self) -> float:
        provider = self.drag_step_provider
        if provider is not None:
            return float(provider() if callable(provider) else provider)
        # 5px = 0.05，与旧 SizeControlLabel 拖拽标签的 1px=0.01 灵敏度一致
        return 0.05

    def _drag_baseline_value(self) -> float:
        return self.value()

    def _drag_value_for(self, raw: float) -> float:
        # drag_integer 时拖拽吸附到整数（字号粗调；速率不变）
        if getattr(self, "drag_integer", False):
            return int(round(raw))
        return round(raw, self.precision)

    def _apply_drag_value(self, value: float):
        # 拖拽中只静默刷新显示（沿用旧拖拽标签语义），提交统一在
        # drag_finished 发一次 param_changed，避免撤销条目逐帧膨胀
        was_blocked = self.blockSignals(True)
        try:
            self.setValue(self._drag_value_for(value))
        finally:
            self.blockSignals(was_blocked)
        self._value = self.value()

    def _enter_edit_mode(self):
        self.setFocus()
        self.lineEdit().selectAll()
        self._refresh_drag_appearance()

    # ---- 鼠标：按下区由 DragAdjustMixin 的 lineEdit 事件代理接管 --------

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def on_text_changed(self):
        if self.hasFocus():
            self.param_changed.emit(self.param_name, self.value())

    def on_current_index_changed(self):
        if self.hasFocus() or self.view().isVisible():
            self.param_changed.emit(self.param_name, self.value())

    def value(self) -> float:
        txt = self.currentText()
        try:
            val = float(txt)
            self._value = val
            return val
        except (ValueError, TypeError):
            return self._value

    def setValue(self, value: float):
        value = min(self.max_val, max(self.min_val, value))
        self.setCurrentText(_format_drag_value(round(value, self.precision)))

    def changeByDelta(self, delta: float, multiplier=0.01):
        if isinstance(multiplier, Callable):
            multiplier = multiplier()
        self.setValue(self.value() + delta * multiplier)


class SmallSizeComboBox(SizeComboBox):
    pass
