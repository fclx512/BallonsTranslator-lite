"""气泡参数面板：右栏窄栏浮层的内容控件（设计见 docs/技术实现/气泡绘制_设计方案.md）。

只管「控件 ↔ ``BubbleSpec``（样式 + 可选自由区域）」的往返，不碰撤销栈、
不碰项目数据：``bubble_changed`` 每次整份意图吐出，由 ``ui/text_panel.py``
组装成 ``ui/textedit_commands.py::SetBlockBubbleCommand`` 入文本命令栈。

**文本块气泡与自由气泡是同一份东西的两种区域模式**（2026-11-04 合并）：
覆盖区域缺省跟随文本块轮廓，「自定义」时用偏移 + 尺寸盖到任意位置。原先
绘图区那套独立的页面级气泡（bubbleLayer / BubbleTool / page_bubbles）已整体
删除——同一件事两个入口，理解成本高于功能收益。

控件布局一屏放下，不分折叠区（设计 §4）：

| 行 | 控件 |
|---|---|
| 启用 | 复选框（取消 = 移除气泡） |
| 区域 | 下拉：跟随文本块 / 自定义（自定义时显示下面四个数值格） |
| 位置尺寸 | X / Y / 宽 / 高（相对块内容区左上角，页面像素） |
| 形状 | 分段钮：矩形 / 椭圆 |
| 圆角 | 滑块（椭圆时整行禁用） |
| 填充 | 取色 + 透明度 |
| 描边 | 开关 + 宽 + 色（关闭时禁用） |
| 尾巴 | 开关 + 方向 + 长度 + 宽度（关闭时禁用） |
"""

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtWidgets import QHBoxLayout, QVBoxLayout

from ui.bubble import SHAPE_ELLIPSE, SHAPE_RECT, BubbleSpec, BubbleStyle, BubbleTail
from ui.custom_widget import (
    ColorPickerLabel,
    ConfigCheckBox,
    NoArrowsDoubleSpinBox,
    NoArrowsSpinBox,
    SmallComboBox,
    SmallParamLabel,
    Widget,
)

REGION_FOLLOW = 0
REGION_CUSTOM = 1


class BlockBubblePanel(Widget):
    """气泡参数面板（块气泡与自由气泡共用）。

    ``bubble_changed(spec_or_none)``：启用时吐整份 ``BubbleSpec``，关闭时
    吐 ``None``。调用方据此建撤销命令——面板**不**自己推命令，也不缓存
    撤销基线。
    """

    bubble_changed = Signal(object)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._loading = False
        self._style = BubbleStyle()
        self._region: QRectF | None = None
        # 块内容区快照（供「自定义」起步尺寸兜底；随 _load_bubble_panel_from 刷新）
        self._base_rect = QRectF()

        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(6, 6, 6, 6)
        vlayout.setSpacing(8)

        self.enable_checker = ConfigCheckBox(self.tr("Bubble"), self)
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

        # ── 覆盖区域 ────────────────────────────────────────────────
        self.region_box = SmallComboBox(self.params_widget)
        self.region_box.addItem(self.tr("Follow text block"), REGION_FOLLOW)
        self.region_box.addItem(self.tr("Custom area"), REGION_CUSTOM)
        self.region_box.setToolTip(
            self.tr("Follow the text block outline, or cover a custom area")
        )
        self.region_box.currentIndexChanged.connect(self._on_region_mode_changed)
        params.addWidget(_label(self.tr("Area"), bold=True))
        params.addWidget(self.region_box)

        self.region_controls = Widget(self.params_widget)
        region_layout = QVBoxLayout(self.region_controls)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setSpacing(6)
        self.region_x_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_y_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_w_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_h_spin = NoArrowsDoubleSpinBox(self.region_controls)
        for spin, tip in (
            (self.region_x_spin, self.tr("Offset X from the block's top-left")),
            (self.region_y_spin, self.tr("Offset Y from the block's top-left")),
            (self.region_w_spin, self.tr("Width")),
            (self.region_h_spin, self.tr("Height")),
        ):
            spin.setRange(-100000.0, 100000.0)
            spin.setSingleStep(1.0)
            spin.setDecimals(1)
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._emit)
        region_grid = QHBoxLayout()
        region_grid.setContentsMargins(0, 0, 0, 0)
        region_grid.setSpacing(6)
        for spin in (
            self.region_x_spin,
            self.region_y_spin,
            self.region_w_spin,
            self.region_h_spin,
        ):
            region_grid.addWidget(spin, 1)
        region_layout.addLayout(region_grid)
        params.addWidget(self.region_controls)

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

    def _ensure_region_size(self) -> None:
        """「自定义」模式下宽高仍为 0 时，用块内容区尺寸补上（避免 1×1）。"""
        base = self._base_rect
        if base.isEmpty():
            return
        changed = False
        self._loading = True
        try:
            if self.region_w_spin.value() <= 0:
                self.region_w_spin.setValue(base.width())
                changed = True
            if self.region_h_spin.value() <= 0:
                self.region_h_spin.setValue(base.height())
                changed = True
        finally:
            self._loading = False
        return changed

    def current_region(self) -> QRectF | None:
        """当前覆盖区域；「跟随文本块」返回 ``None``。"""
        if self.region_box.currentData() != REGION_CUSTOM:
            return None
        w = max(1.0, float(self.region_w_spin.value()))
        h = max(1.0, float(self.region_h_spin.value()))
        return QRectF(
            float(self.region_x_spin.value()),
            float(self.region_y_spin.value()),
            w,
            h,
        )

    def current_spec(self) -> BubbleSpec | None:
        """按控件状态组装意图；未启用返回 ``None``。"""
        style = self.current_style()
        if style is None:
            return None
        return BubbleSpec(style=style, region=self.current_region())

    @staticmethod
    def _hex_from_picker(picker) -> str:
        r, g, b = picker.rgb()
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── 写控件 ← BubbleStyle / BubbleSpec ──────────────────────────

    def set_from_style(
        self, style: BubbleStyle | None, region: QRectF | None = None
    ) -> None:
        """回显；``None`` 表示该块没有气泡（开关关、参数区禁用）。

        *region* 为 ``None`` 时区域回到「跟随文本块」。自定义区域回显时若
        控件里的宽高还是 0（首次切到自定义），用块实际内容区尺寸兜底，避免
        用户看到 0×0 的气泡。
        """
        self._loading = True
        try:
            enabled = style is not None
            self._style = (style or BubbleStyle()).normalized()
            style = self._style
            self._region = QRectF(region) if region is not None else None
            self.enable_checker.setChecked(enabled)
            self.params_widget.setEnabled(enabled)

            region_index = self.region_box.findData(
                REGION_CUSTOM if self._region is not None else REGION_FOLLOW
            )
            if region_index >= 0:
                self.region_box.setCurrentIndex(region_index)
            if self._region is not None:
                self.region_x_spin.setValue(self._region.x())
                self.region_y_spin.setValue(self._region.y())
                if self.region_w_spin.value() <= 0:
                    self.region_w_spin.setValue(self._region.width())
                if self.region_h_spin.value() <= 0:
                    self.region_h_spin.setValue(self._region.height())

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

    def seed_region(self, base: QRectF) -> None:
        """记住块内容区，并在宽高还空着时用它兜底。

        从「跟随块」切到「自定义」，最自然的起点就是块现在的大小（用户再改
        差值），而不是 0×0——0 会被钳到 1×1，画出来是个点。
        """
        if base.isEmpty():
            return
        self._base_rect = QRectF(base)
        self._loading = True
        try:
            if self.region_w_spin.value() <= 0:
                self.region_w_spin.setValue(base.width())
            if self.region_h_spin.value() <= 0:
                self.region_h_spin.setValue(base.height())
        finally:
            self._loading = False

    # ── 内部 ────────────────────────────────────────────────────────

    def _sync_enabled_states(self) -> None:
        enabled = self.enable_checker.isChecked()
        is_rect = (self.shape_box.currentData() or SHAPE_RECT) == SHAPE_RECT
        is_custom = self.region_box.currentData() == REGION_CUSTOM
        self.corner_row_widget.setEnabled(enabled and is_rect)
        self.region_controls.setEnabled(enabled and is_custom)
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

    def _on_region_mode_changed(self, _index: int) -> None:
        self._sync_enabled_states()
        # 切到自定义：先把宽高从 0 补成块尺寸，否则 _emit 会写出一条 1×1 的区域
        self._ensure_region_size()
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
        self.bubble_changed.emit(self.current_spec())
