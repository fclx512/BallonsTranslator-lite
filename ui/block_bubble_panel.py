"""块气泡参数面板：右栏窄栏浮层的内容控件（设计见 docs/技术实现/气泡绘制_设计方案.md）。

只管「控件 ↔ ``BubbleStyle``」的往返，不碰撤销栈、不碰项目数据：
``bubble_changed`` 每次整份样式吐出，由 ``ui/text_panel.py`` 组装成
``ui/textedit_commands.py::SetBlockBubbleCommand`` 入文本命令栈。

控件布局一屏放下，不分折叠区（设计 §4）：

| 行 | 控件 |
|---|---|
| 启用 | 复选框 |
| 形状 | 分段钮：矩形 / 椭圆 |
| 圆角 | 滑块（椭圆时整行禁用） |
| 填充 | 取色 + 透明度 |
| 描边 | 开关 + 宽 + 色（关闭时禁用） |
| 尾巴 | 开关 + 方向 + 长度 + 宽度（关闭时禁用） |
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QHBoxLayout, QVBoxLayout

from ui.bubble import SHAPE_ELLIPSE, SHAPE_RECT, BubbleStyle, BubbleTail
from ui.custom_widget import (
    ColorPickerLabel,
    ConfigCheckBox,
    NoArrowsDoubleSpinBox,
    NoArrowsSpinBox,
    SmallComboBox,
    SmallParamLabel,
    Widget,
)


class BlockBubblePanel(Widget):
    """块气泡参数面板。

    ``bubble_changed(style_or_none)``：启用时吐整份 ``BubbleStyle``，关闭时
    吐 ``None``。调用方据此建撤销命令——面板**不**自己推命令，也不缓存
    撤销基线。
    """

    bubble_changed = Signal(object)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._loading = False
        self._style = BubbleStyle()

        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(6, 6, 6, 6)
        vlayout.setSpacing(8)

        self.enable_checker = ConfigCheckBox(self.tr("Block background"), self)
        self.enable_checker.setToolTip(
            self.tr("Draw a bubble shape behind this text block")
        )
        self.enable_checker.toggled.connect(self._on_enabled_toggled)
        vlayout.addWidget(self.enable_checker)

        # 参数区整体禁用跟随开关
        self.params_widget = Widget(self)
        params = QVBoxLayout(self.params_widget)
        params.setContentsMargins(0, 0, 0, 0)
        params.setSpacing(8)
        vlayout.addWidget(self.params_widget)

        def _label(text: str, bold: bool = False) -> SmallParamLabel:
            label = SmallParamLabel(text)
            if bold:
                font = label.font()
                font.setBold(True)
                label.setFont(font)
            return label

        # ── 形状 ────────────────────────────────────────────────────
        self.shape_box = SmallComboBox(self.params_widget)
        self.shape_box.addItem(self.tr("Rectangle"), SHAPE_RECT)
        self.shape_box.addItem(self.tr("Ellipse"), SHAPE_ELLIPSE)
        self.shape_box.currentIndexChanged.connect(self._emit)
        params.addWidget(_label(self.tr("Shape"), bold=True))
        params.addWidget(self.shape_box)

        # ── 圆角（仅矩形）────────────────────────────────────────────
        self.corner_label = _label(self.tr("Corner radius"))
        self.corner_spin = NoArrowsSpinBox(self.params_widget)
        self.corner_spin.setRange(0, 200)
        self.corner_spin.setSingleStep(1)
        self.corner_spin.valueChanged.connect(self._emit)
        corner_row = QHBoxLayout()
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(6)
        corner_row.addWidget(self.corner_label)
        corner_row.addWidget(self.corner_spin, 1)
        self.corner_row_widget = Widget(self.params_widget)
        self.corner_row_widget.setLayout(corner_row)
        params.addWidget(self.corner_row_widget)
        # ── 填充 ────────────────────────────────────────────────────
        params.addWidget(_label(self.tr("Fill"), bold=True))
        self.fill_picker = ColorPickerLabel(self.params_widget, param_name="bubble_fill")
        self.fill_picker.colorChanged.connect(self._emit)
        self.alpha_spin = NoArrowsSpinBox(self.params_widget)
        self.alpha_spin.setRange(0, 100)
        self.alpha_spin.setSuffix(" %")
        self.alpha_spin.setToolTip(self.tr("Fill opacity"))
        self.alpha_spin.valueChanged.connect(self._emit)
        fill_row = QHBoxLayout()
        fill_row.setContentsMargins(0, 0, 0, 0)
        fill_row.setSpacing(6)
        fill_row.addWidget(self.fill_picker)
        fill_row.addWidget(self.alpha_spin, 1)
        params.addLayout(fill_row)

        # ── 描边 ────────────────────────────────────────────────────
        self.border_checker = ConfigCheckBox(self.tr("Border"), self.params_widget)
        self.border_checker.toggled.connect(self._on_border_toggled)
        params.addWidget(self.border_checker)

        self.border_controls = Widget(self.params_widget)
        border_layout = QHBoxLayout(self.border_controls)
        border_layout.setContentsMargins(0, 0, 0, 0)
        border_layout.setSpacing(6)
        self.border_width_spin = NoArrowsDoubleSpinBox(self.border_controls)
        self.border_width_spin.setRange(0.0, 64.0)
        self.border_width_spin.setSingleStep(0.5)
        self.border_width_spin.setDecimals(1)
        self.border_width_spin.setToolTip(self.tr("Border width (px, drawn inward only)"))
        self.border_width_spin.valueChanged.connect(self._emit)
        self.border_picker = ColorPickerLabel(
            self.border_controls, param_name="bubble_border"
        )
        self.border_picker.colorChanged.connect(self._emit)
        border_layout.addWidget(self.border_width_spin, 1)
        border_layout.addWidget(self.border_picker)
        params.addWidget(self.border_controls)

        # ── 尾巴 ────────────────────────────────────────────────────
        self.tail_checker = ConfigCheckBox(self.tr("Tail"), self.params_widget)
        self.tail_checker.toggled.connect(self._on_tail_toggled)
        params.addWidget(self.tail_checker)

        self.tail_controls = Widget(self.params_widget)
        tail_layout = QVBoxLayout(self.tail_controls)
        tail_layout.setContentsMargins(0, 0, 0, 0)
        tail_layout.setSpacing(6)

        self.tail_angle_spin = NoArrowsSpinBox(self.tail_controls)
        self.tail_angle_spin.setRange(0, 359)
        self.tail_angle_spin.setSuffix("°")
        self.tail_angle_spin.setToolTip(self.tr("Direction (0 = right, clockwise)"))
        self.tail_angle_spin.valueChanged.connect(self._emit)
        self.tail_length_spin = NoArrowsDoubleSpinBox(self.tail_controls)
        self.tail_length_spin.setRange(0.0, 2.0)
        self.tail_length_spin.setSingleStep(0.01)
        self.tail_length_spin.setDecimals(2)
        self.tail_length_spin.setToolTip(self.tr("Length (ratio of the shorter side)"))
        self.tail_length_spin.valueChanged.connect(self._emit)
        self.tail_width_spin = NoArrowsDoubleSpinBox(self.tail_controls)
        self.tail_width_spin.setRange(0.02, 1.0)
        self.tail_width_spin.setSingleStep(0.01)
        self.tail_width_spin.setDecimals(2)
        self.tail_width_spin.setToolTip(self.tr("Base width (ratio of the shorter side)"))
        self.tail_width_spin.valueChanged.connect(self._emit)

        tail_grid = QHBoxLayout()
        tail_grid.setContentsMargins(0, 0, 0, 0)
        tail_grid.setSpacing(6)
        tail_grid.addWidget(self.tail_angle_spin)
        tail_grid.addWidget(self.tail_length_spin)
        tail_grid.addWidget(self.tail_width_spin)
        tail_layout.addLayout(tail_grid)

        # 描边宽与尾巴宽的耦合警示（设计 §2.2 实测坑：描边超过嘴宽一半时
        # 尾巴会被描边填满，看起来是一根黑刺）。只提示、不改参数。
        self.hint_label = SmallParamLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()
        tail_layout.addWidget(self.hint_label)
        params.addWidget(self.tail_controls)

        self.set_from_style(None)

    # ── 读控件 → BubbleStyle ────────────────────────────────────────

    def current_style(self) -> BubbleStyle | None:
        """按控件状态组装样式；未启用返回 ``None``。"""
        if not self.enable_checker.isChecked():
            return None
        tail = None
        if self.tail_checker.isChecked():
            tail = BubbleTail(
                angle=float(self.tail_angle_spin.value()),
                length=float(self.tail_length_spin.value()),
                width=float(self.tail_width_spin.value()),
            )
        return BubbleStyle(
            shape=self.shape_box.currentData() or SHAPE_RECT,
            corner=float(self.corner_spin.value()),
            fill=self._hex_from_picker(self.fill_picker),
            alpha=int(round(self.alpha_spin.value() / 100.0 * 255)),
            border=self.border_checker.isChecked(),
            border_width=float(self.border_width_spin.value()),
            border_color=self._hex_from_picker(self.border_picker),
            tail=tail,
        ).normalized()

    @staticmethod
    def _hex_from_picker(picker) -> str:
        r, g, b = picker.rgb()
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── 写控件 ← BubbleStyle ───────────────────────────────────────

    def set_from_style(self, style: BubbleStyle | None) -> None:
        """回显样式；``None`` 表示该块没有气泡（开关关、参数区禁用）。"""
        self._loading = True
        try:
            enabled = style is not None
            self._style = (style or BubbleStyle()).normalized()
            style = self._style
            self.enable_checker.setChecked(enabled)
            self.params_widget.setEnabled(enabled)

            index = self.shape_box.findData(style.shape)
            if index >= 0:
                self.shape_box.setCurrentIndex(index)
            self.corner_spin.setValue(int(round(style.corner)))
            self.fill_picker.setPickerColor(style.fill)
            self.alpha_spin.setValue(int(round(style.alpha / 255.0 * 100)))
            self.border_checker.setChecked(style.border)
            self.border_width_spin.setValue(style.border_width)
            self.border_picker.setPickerColor(style.border_color)
            has_tail = style.tail is not None
            self.tail_checker.setChecked(has_tail)
            tail = (style.tail or BubbleTail()).normalized()
            self.tail_angle_spin.setValue(int(round(tail.angle)))
            self.tail_length_spin.setValue(tail.length)
            self.tail_width_spin.setValue(tail.width)

            self._sync_enabled_states()
        finally:
            self._loading = False
        self._refresh_hint()

    # ── 内部 ────────────────────────────────────────────────────────

    def _sync_enabled_states(self) -> None:
        enabled = self.enable_checker.isChecked()
        is_rect = (self.shape_box.currentData() or SHAPE_RECT) == SHAPE_RECT
        self.corner_row_widget.setEnabled(enabled and is_rect)
        self.border_controls.setEnabled(enabled and self.border_checker.isChecked())
        self.tail_controls.setEnabled(enabled and self.tail_checker.isChecked())

    def _refresh_hint(self) -> None:
        """描边宽 > 尾巴根部半宽时提示，不自动改参数。"""
        text = ""
        if (
            self.enable_checker.isChecked()
            and self.border_checker.isChecked()
            and self.tail_checker.isChecked()
        ):
            # 根部全宽 = width × min(w,h)，描边每侧各占 border_width，
            # 两侧合起来吃掉 2×border_width，超过根部宽度就填满整条尾巴。
            tail_base = float(self.tail_width_spin.value())
            border = float(self.border_width_spin.value())
            # 以「短边 = 100px」为参照换算：border_width 是绝对 px，
            # 尾巴宽是比例，只能给出与块尺寸无关的粗略门槛。
            if border > 0 and tail_base > 0:
                ratio = border / (tail_base * 100.0 / 2.0)
                if ratio > 0.5:
                    text = self.tr("Border is thick relative to the tail width, the tail may be filled by the border.")
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    def _on_enabled_toggled(self, checked: bool) -> None:
        self.params_widget.setEnabled(checked)
        self._emit()

    def _on_border_toggled(self, _checked: bool) -> None:
        self._sync_enabled_states()
        self._emit()

    def _on_tail_toggled(self, _checked: bool) -> None:
        self._sync_enabled_states()
        self._emit()

    def _emit(self, *_args) -> None:
        if self._loading:
            return
        self._sync_enabled_states()
        self._refresh_hint()
        self.bubble_changed.emit(self.current_style())
