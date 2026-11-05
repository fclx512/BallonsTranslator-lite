"""气泡参数面板：右栏窄栏浮层的内容控件（设计见 docs/技术实现/气泡绘制_设计方案.md）。

只管「控件 ↔ ``BubbleSpec``（样式 + 可选自由区域）」的往返，不碰撤销栈、
不碰项目数据：``bubble_changed`` 每次整份意图吐出，由 ``ui/text_panel.py``
组装成 ``ui/textedit_commands.py::SetBlockBubbleCommand`` 入文本命令栈。

**文本块气泡与自由气泡是同一份东西的两种区域模式**（2026-11-04 合并）：
覆盖区域缺省跟随文本块轮廓，「自定义」时用偏移 + 尺寸盖到任意位置。原先
绘图区那套独立的页面级气泡（bubbleLayer / BubbleTool / page_bubbles）已整体
删除——同一件事两个入口，理解成本高于功能收益。

排版原则（2026-11-05 重排）：**标签与控件同行、参数只在相关时出现**，
260 最小宽下默认态 6 行放完，全部展开 9 行：

| 行 | 控件 |
|---|---|
| 启用 | 复选框（取消 = 移除气泡） |
| 区域 | 标签 + 下拉：跟随文本块 / 自定义（自定义时才显示下面 2×2 数值格） |
| 位置尺寸 | X / Y / W / H 前缀内嵌数值格（相对块内容区左上角，页面像素） |
| 形状+圆角 | 分段钮：矩形 / 椭圆（自带语义，无独立标签）+ 圆角数值格（椭圆时禁用） |
| 填充 | 标签 + 取色 + 透明度 |
| 描边 | 复选框 + 宽 + 色（同行，关闭时宽/色禁用） |
| 尾巴 | 复选框 + 方向角（关闭时角度禁用）；勾选才追加长/宽一行 |

圆角与描边上限随气泡短边收紧（``_apply_limits``）：圆角最多半个短边、
描边最多四分之一个短边，拖到顶就停，不会出现胶囊化或实心块这类无效值。
"""

from qtpy.QtCore import QPointF, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QPainter, QPen
from qtpy.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

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

# 绝对上限（大尺寸气泡仍可用）；实际滑块上限按气泡短边再收紧，
# 见 ``_apply_limits``——圆角超过短边一半、描边超过短边四分之一
# 都只是把形状吃成胶囊/实心块，没有实用意义。
CORNER_MAX = 200
BORDER_WIDTH_MAX = 64.0


class _ShapeSegments(QWidget):
    """两段互斥条（矩形/椭圆）：轻量自绘，风格对齐整本对齐的分段钮
    （``ui/point_align_dialog.py::_SegmentedBar``），仅本面板使用。

    段内容是自绘小图标而非文案——「Rectangle」这类长词在 260 最小宽的
    两个半段里放不下（实测英文截成 "ectang"），图形符号则与语言无关。
    名称经整条 tooltip 提供。
    """

    selection_changed = Signal(int)

    _HEIGHT = 26
    _SEG_W = 56

    def __init__(self, tooltips, parent=None):
        super().__init__(parent)
        # tooltips: List[str]（每段悬停名，仅提示用，不绘制）
        self._tooltips = list(tooltips)
        self._current = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self._HEIGHT)
        self.setMinimumWidth(self._SEG_W * len(self._tooltips))

    def current_index(self) -> int:
        return self._current

    def set_current_index(self, idx: int, emit: bool = False) -> None:
        idx = max(0, min(idx, len(self._tooltips) - 1))
        changed = idx != self._current
        self._current = idx
        self.update()
        if changed and emit:
            self.selection_changed.emit(idx)

    def _seg_rects(self):
        n = max(1, len(self._tooltips))
        w = self.width() / n
        return [QRectF(i * w, 0, w, self.height()) for i in range(n)]

    def mousePressEvent(self, event):
        pos = event.position()
        for i, r in enumerate(self._seg_rects()):
            if r.contains(QPointF(pos)):
                self.set_current_index(i, emit=True)
                break

    def sizeHint(self):
        return QSize(self._SEG_W * len(self._tooltips) + 8, self._HEIGHT)

    def paintEvent(self, event):
        from ui.misc import get_theme_color

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        fg_key = "@qwidgetForegroundColor" if enabled else "@disabledForegroundColor"
        fg = QColor(get_theme_color(key=fg_key))
        border = QColor(get_theme_color(key="@borderColor"))
        bg = QColor(get_theme_color(key="@inputBackgroundColor"))
        accent = QColor(get_theme_color()) if enabled else fg
        sel_bg = QColor(accent)
        sel_bg.setAlpha(36 if enabled else 20)

        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        for i, r in enumerate(self._seg_rects()):
            selected = i == self._current
            if selected:
                painter.setPen(QPen(accent, 1))
                painter.setBrush(QBrush(sel_bg))
                painter.drawRoundedRect(r.adjusted(2, 2, -3, -3), 5, 5)
            glyph = QRectF(0, 0, 14, 12)
            glyph.moveCenter(r.center())
            painter.setPen(QPen(accent if selected else fg, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if i == 0:
                painter.drawRoundedRect(glyph, 3, 3)
            else:
                painter.drawEllipse(glyph)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._update_hover_tooltip()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self._update_hover_tooltip()

    def _update_hover_tooltip(self):
        # 鼠标在哪段就显示哪段的名字；光标不在分段上时回退整条名
        pos = self.mapFromGlobal(self.cursor().pos())
        names = [
            self._tooltips[i]
            for i, r in enumerate(self._seg_rects())
            if r.contains(QPointF(pos))
        ]
        if names:
            self.setToolTip(names[0])
        elif self._tooltips:
            self.setToolTip(" / ".join(self._tooltips))


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
        vlayout.setSpacing(6)

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
        params.setSpacing(6)
        vlayout.addWidget(self.params_widget)

        def _row(*items) -> QHBoxLayout:
            """同行排布；``"stretch"`` = 弹簧， ``(widget, n)`` = 带 stretch 系数。"""
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            for item in items:
                if item == "stretch":
                    layout.addStretch(1)
                elif isinstance(item, tuple):
                    widget, stretch = item
                    layout.addWidget(widget, stretch)
                elif isinstance(item, QWidget):
                    layout.addWidget(item)
                else:
                    layout.addLayout(item)
            return layout

        # ── 区域：标签 + 下拉同行；自定义时才展开数值格 ─────────────
        self.region_box = SmallComboBox(self.params_widget)
        self.region_box.addItem(self.tr("Follow text block"), REGION_FOLLOW)
        self.region_box.addItem(self.tr("Custom area"), REGION_CUSTOM)
        self.region_box.setToolTip(
            self.tr("Follow the text block outline, or cover a custom area")
        )
        self.region_box.currentIndexChanged.connect(self._on_region_mode_changed)
        params.addLayout(
            _row(SmallParamLabel(self.tr("Area")), (self.region_box, 1))
        )

        self.region_controls = QWidget(self.params_widget)
        region_grid = QGridLayout(self.region_controls)
        region_grid.setContentsMargins(0, 0, 0, 0)
        region_grid.setHorizontalSpacing(6)
        region_grid.setVerticalSpacing(4)
        self.region_x_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_y_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_w_spin = NoArrowsDoubleSpinBox(self.region_controls)
        self.region_h_spin = NoArrowsDoubleSpinBox(self.region_controls)
        # X/Y/W/H 是语言中立符号（同 ° 后缀，不进翻译表）
        for col, (spin, prefix, tip) in enumerate((
            (self.region_x_spin, "X", self.tr("Offset X from the block's top-left")),
            (self.region_y_spin, "Y", self.tr("Offset Y from the block's top-left")),
        )):
            spin.setPrefix(f"{prefix} ")
            region_grid.addWidget(spin, 0, col)
            region_grid.setColumnStretch(col, 1)
        for col, (spin, prefix, tip) in enumerate((
            (self.region_w_spin, "W", self.tr("Width")),
            (self.region_h_spin, "H", self.tr("Height")),
        )):
            spin.setPrefix(f"{prefix} ")
            region_grid.addWidget(spin, 1, col)
            region_grid.setColumnStretch(col, 1)
        for spin, tip, slot in (
            (
                self.region_x_spin,
                self.tr("Offset X from the block's top-left"),
                self._emit,
            ),
            (
                self.region_y_spin,
                self.tr("Offset Y from the block's top-left"),
                self._emit,
            ),
            (self.region_w_spin, self.tr("Width"), self._on_region_size_changed),
            (self.region_h_spin, self.tr("Height"), self._on_region_size_changed),
        ):
            spin.setRange(-100000.0, 100000.0)
            spin.setSingleStep(1.0)
            spin.setDecimals(0)
            spin.setToolTip(tip)
            spin.valueChanged.connect(slot)
        params.addWidget(self.region_controls)

        # ── 形状 + 圆角：图标分段钮自带语义，无独立「形状」标签行 ────
        self.shape_segments = _ShapeSegments(
            [self.tr("Rectangle"), self.tr("Ellipse")], self.params_widget
        )
        self.shape_segments.selection_changed.connect(self._emit)
        self.corner_label = SmallParamLabel(self.tr("Corner"))
        self.corner_spin = NoArrowsSpinBox(self.params_widget)
        self.corner_spin.setRange(0, CORNER_MAX)
        self.corner_spin.setSingleStep(1)
        self.corner_spin.setToolTip(
            self.tr("Corner radius (px), at most half the shorter side")
        )
        self.corner_spin.valueChanged.connect(self._emit)
        shape_row = _row(
            self.shape_segments, self.corner_label, (self.corner_spin, 1)
        )
        params.addLayout(shape_row)

        # ── 填充：标签 + 取色 + 透明度同行 ───────────────────────────
        self.fill_picker = ColorPickerLabel(self.params_widget, param_name="bubble_fill")
        self.fill_picker.colorChanged.connect(self._emit)
        self.alpha_spin = NoArrowsSpinBox(self.params_widget)
        self.alpha_spin.setRange(0, 100)
        self.alpha_spin.setSuffix(" %")
        self.alpha_spin.setToolTip(self.tr("Fill opacity"))
        self.alpha_spin.valueChanged.connect(self._emit)
        params.addLayout(
            _row(SmallParamLabel(self.tr("Fill")), self.fill_picker, (self.alpha_spin, 1))
        )

        # ── 描边：复选框当行标签 + 宽 + 色同行 ──────────────────────
        self.border_checker = ConfigCheckBox(self.tr("Border"), self.params_widget)
        self.border_checker.toggled.connect(self._on_border_toggled)
        self.border_width_spin = NoArrowsDoubleSpinBox(self.params_widget)
        self.border_width_spin.setRange(0.0, BORDER_WIDTH_MAX)
        self.border_width_spin.setSingleStep(0.5)
        self.border_width_spin.setDecimals(1)
        self.border_width_spin.setToolTip(
            self.tr(
                "Border width (px, drawn inward), at most a quarter of the shorter side"
            )
        )
        self.border_width_spin.valueChanged.connect(self._emit)
        self.border_picker = ColorPickerLabel(
            self.params_widget, param_name="bubble_border"
        )
        self.border_picker.colorChanged.connect(self._emit)
        params.addLayout(
            _row(self.border_checker, (self.border_width_spin, 1), self.border_picker)
        )

        # ── 尾巴：复选框 + 方向角同行；勾选才追加长/宽一行 ──────────
        self.tail_checker = ConfigCheckBox(self.tr("Tail"), self.params_widget)
        self.tail_checker.toggled.connect(self._on_tail_toggled)
        self.tail_angle_spin = NoArrowsSpinBox(self.params_widget)
        self.tail_angle_spin.setRange(0, 359)
        self.tail_angle_spin.setSuffix("°")
        self.tail_angle_spin.setToolTip(self.tr("Direction (0 = right, clockwise)"))
        self.tail_angle_spin.valueChanged.connect(self._emit)
        params.addLayout(_row(self.tail_checker, (self.tail_angle_spin, 1)))

        self.tail_controls = QWidget(self.params_widget)
        tail_grid = QHBoxLayout(self.tail_controls)
        tail_grid.setContentsMargins(0, 0, 0, 0)
        tail_grid.setSpacing(6)
        self.tail_length_spin = NoArrowsDoubleSpinBox(self.tail_controls)
        self.tail_length_spin.setRange(0.0, 2.0)
        self.tail_length_spin.setSingleStep(0.01)
        self.tail_length_spin.setDecimals(2)
        self.tail_length_spin.setPrefix(self.tr("Length") + " ")
        self.tail_length_spin.setToolTip(self.tr("Length (ratio of the shorter side)"))
        self.tail_length_spin.valueChanged.connect(self._emit)
        self.tail_width_spin = NoArrowsDoubleSpinBox(self.tail_controls)
        self.tail_width_spin.setRange(0.02, 1.0)
        self.tail_width_spin.setSingleStep(0.01)
        self.tail_width_spin.setDecimals(2)
        self.tail_width_spin.setPrefix(self.tr("Width") + " ")
        self.tail_width_spin.setToolTip(self.tr("Base width (ratio of the shorter side)"))
        self.tail_width_spin.valueChanged.connect(self._emit)
        tail_grid.addWidget(self.tail_length_spin, 1)
        tail_grid.addWidget(self.tail_width_spin, 1)
        params.addWidget(self.tail_controls)

        # 描边宽与尾巴宽的耦合警示（设计 §2.2 实测坑：描边超过嘴宽一半时
        # 尾巴会被描边填满，看起来是一根黑刺）。只提示、不改参数。
        self.hint_label = SmallParamLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()
        params.addWidget(self.hint_label)
        # 面板被拖高时余量留在底部，别把各行摊开（标签与控件要成行）
        params.addStretch(1)

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
        shape = SHAPE_ELLIPSE if self.shape_segments.current_index() == 1 else SHAPE_RECT
        return BubbleStyle(
            shape=shape,
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
            self._apply_limits()
        finally:
            self._loading = False
        return changed

    def _limit_rect_size(self) -> tuple[float, float] | None:
        """气泡实际绘制尺寸：自定义区域优先，其次块内容区；都没有返回 None。"""
        if self.region_box.currentData() == REGION_CUSTOM:
            w = float(self.region_w_spin.value())
            h = float(self.region_h_spin.value())
            if w > 0 and h > 0:
                return w, h
        base = self._base_rect
        if not base.isEmpty():
            return base.width(), base.height()
        return None

    def _apply_limits(self) -> None:
        """按气泡短边收紧圆角与描边的输入上限。

        圆角到短边一半就退化成胶囊、描边到四分之一时内侧只剩一半空间，
        再往里拖都没有实用意义（只会得到畸形或实心块）。直接在 spinbox
        上限里挡掉，拖拽手感是「到顶即停」而不是事后纠错。
        """
        size = self._limit_rect_size()
        if size is None:  # 没选中块：不猜尺寸，按绝对上限放开
            self.corner_spin.setMaximum(CORNER_MAX)
            self.border_width_spin.setMaximum(BORDER_WIDTH_MAX)
            return
        short = min(size)
        self.corner_spin.setMaximum(max(0, min(CORNER_MAX, int(short / 2))))
        border_max = max(0.5, round(short * 0.25 * 2) / 2)  # 对齐 0.5 步进
        self.border_width_spin.setMaximum(min(BORDER_WIDTH_MAX, border_max))

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

        *region* 为 ``None`` 时区域回到「跟随文本块」；带区域时宽高以它为准
        整体覆盖（换块/撤销回显都不能沿用上一块的数值）。圆角/描边的上限
        也在这里按气泡短边重算。
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
                # 区域数值以块数据为准（换块/撤销后都要覆盖旧值，否则改一项
                # 会把上一块的尺寸写进当前块）
                self.region_x_spin.setValue(self._region.x())
                self.region_y_spin.setValue(self._region.y())
                self.region_w_spin.setValue(self._region.width())
                self.region_h_spin.setValue(self._region.height())
            # 区域尺寸决定圆角/描边上限，必须在写回数值之后、写样式之前
            self._apply_limits()

            self.shape_segments.set_current_index(
                1 if style.shape == SHAPE_ELLIPSE else 0
            )
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

    def seed_region(self, base: QRectF, reset_size: bool = False) -> None:
        """记住块内容区，并在宽高还空着时用它兜底。

        从「跟随块」切到「自定义」，最自然的起点就是块现在的大小（用户再改
        差值），而不是 0×0——0 会被钳到 1×1，画出来是个点。

        *reset_size* 用于切换块：新块没有自定义区域时，把宽高重置成新块的
        尺寸，免得沿用上一块留下的数值。
        """
        if base.isEmpty():
            return
        self._base_rect = QRectF(base)
        self._loading = True
        try:
            if reset_size or self.region_w_spin.value() <= 0:
                self.region_w_spin.setValue(base.width())
            if reset_size or self.region_h_spin.value() <= 0:
                self.region_h_spin.setValue(base.height())
            self._apply_limits()
        finally:
            self._loading = False

    # ── 内部 ────────────────────────────────────────────────────────

    def _sync_enabled_states(self) -> None:
        enabled = self.enable_checker.isChecked()
        is_rect = self.shape_segments.current_index() == 0
        is_custom = self.region_box.currentData() == REGION_CUSTOM
        # 参数行「只在相关时出现」：跟随态收起数值格、尾巴关闭收起长/宽
        self.region_controls.setVisible(enabled and is_custom)
        self.corner_label.setEnabled(enabled and is_rect)
        self.corner_spin.setEnabled(enabled and is_rect)
        self.border_width_spin.setEnabled(enabled and self.border_checker.isChecked())
        self.border_picker.setEnabled(enabled and self.border_checker.isChecked())
        self.tail_angle_spin.setEnabled(enabled and self.tail_checker.isChecked())
        self.tail_controls.setVisible(enabled and self.tail_checker.isChecked())

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

    def _on_region_size_changed(self, *_args) -> None:
        """区域宽高一变，气泡短边就变——先收紧圆角/描边上限再统一发一次。

        收上限可能把这两个值压回范围内；用 ``_loading`` 挡掉中途的
        ``valueChanged``，避免一次改尺寸发多条撤销命令。
        """
        if self._loading:
            return
        self._loading = True
        try:
            self._apply_limits()
        finally:
            self._loading = False
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
