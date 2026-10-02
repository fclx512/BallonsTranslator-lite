"""整本对齐（原「高级对齐」）配置对话框。

非模态：打开时画布同步进入对齐模式（``ui/canvas.py::enter_align_mode``）——
基准线可拖动、点击文字块取其对齐边、点空白直接落线；本对话框按当前
方向/对齐边/目标坐标在当前页推算幽灵落点预览并推送画布。确定后由
``ui/mainwindow.py::on_open_whole_book_align`` 调
``ui/mainwindow.py::execute_advanced_align`` 批量执行。

轴/对齐边/范围口径记忆在 ``pcfg.point_align_*``；目标坐标是页面相关
值，不记忆——每次打开取当前页对齐边众数（差分本排版一致，众数即
正确值，典型用法退化成「打开→直接确定」）。
"""

from collections import Counter

from qtpy.QtCore import QCoreApplication, QPointF, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import (
    ConfigCheckBox,
    GroupFrame,
    NoArrowsSpinBox,
    NoBorderPushBtn,
    RangeSlider,
)

from utils.config import pcfg, save_config


def block_edge_value(br, axis: str, mode: str) -> float:
    """块矩形 (x, y, w, h) 在指定轴/对齐边上的基准值。

    y 轴：top→y / center→y+h/2 / bottom→y+h；
    x 轴：left→x / center→x+w/2 / right→x+w。
    """
    x, y, w, h = br
    if axis == "y":
        return {"top": y, "center": y + h / 2.0, "bottom": y + h}[mode]
    return {"left": x, "center": x + w / 2.0, "right": x + w}[mode]


def _blk_br(blk) -> tuple:
    """块的 (x, y, w, h)；``_bounding_rect`` 缺失时回退 xyxy（与执行口径一致）。"""
    br = blk._bounding_rect
    if br is None:
        x1, y1, x2, y2 = blk.xyxy
        br = (x1, y1, x2 - x1, y2 - y1)
    return br


def smart_default_target(blocks, axis: str, mode: str) -> int:
    """打开对话框时的默认目标坐标：各块对齐边取众数（跳过旋转块）。

    平票时取最接近中位数的一个，保证确定性；无可用块返回 0。
    """
    values = []
    for blk in blocks:
        if blk.angle != 0:
            continue
        values.append(block_edge_value(_blk_br(blk), axis, mode))
    if not values:
        return 0
    counts = Counter(values)
    best = max(counts.values())
    modes = sorted(v for v, c in counts.items() if c == best)
    mid = modes[len(modes) // 2] if len(modes) % 2 else modes[len(modes) // 2 - 1]
    return int(round(min(modes, key=lambda v: (abs(v - mid), v))))


class _SegmentedBar(QWidget):
    """轻量分段单选条（整本对齐专用）：互斥选中，选中段主题色染底。

    纯自绘（不进 QSS 体系），段 = 可选对齐小图标 + 文案。仅本对话框
    使用，暂不提炼进 ``ui/custom_widget``。
    """

    selection_changed = Signal(int)

    _HEIGHT = 30

    def __init__(self, options, parent=None):
        super().__init__(parent)
        # options: List[(label, icon_kind or None)]
        self._options = list(options)
        self._current = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self._HEIGHT)

    def set_options(self, options):
        """换文案/图标（轴切换时对齐边三项换标签），保持当前选中索引。"""
        self._options = list(options)
        if self._current >= len(self._options):
            self._current = 0
        self.update()

    def current(self) -> int:
        return self._current

    def set_current(self, idx: int, emit: bool = False):
        idx = max(0, min(idx, len(self._options) - 1))
        changed = idx != self._current
        self._current = idx
        self.update()
        if changed and emit:
            self.selection_changed.emit(idx)

    def _seg_rects(self):
        n = max(1, len(self._options))
        w = self.width() / n
        return [QRectF(i * w, 0, w, self.height()) for i in range(n)]

    def mousePressEvent(self, event):
        pos = event.position()
        for i, r in enumerate(self._seg_rects()):
            if r.contains(QPointF(pos)):
                self.set_current(i, emit=True)
                break

    def sizeHint(self):
        text_w = sum(
            QFontMetrics(self.font()).horizontalAdvance(label)
            + (20 if icon else 0)
            + 22
            for label, icon in self._options
        )
        return QSize(max(text_w + 8, 240), self._HEIGHT)

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

        fm = painter.fontMetrics()
        for i, r in enumerate(self._seg_rects()):
            label, icon = self._options[i]
            selected = i == self._current
            if selected:
                painter.setPen(QPen(accent, 1))
                painter.setBrush(QBrush(sel_bg))
                painter.drawRoundedRect(r.adjusted(2, 2, -3, -3), 5, 5)
            color = accent if selected else fg
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            text_w = fm.horizontalAdvance(label)
            icon_w = 16 if icon else 0
            total = text_w + icon_w + (4 if icon else 0)
            tx = r.left() + (r.width() - total) / 2
            if icon:
                self._draw_icon(
                    painter, icon, QRectF(tx, r.center().y() - 7, 14, 14), color
                )
                tx += icon_w + 4
            painter.drawText(
                QRectF(tx, r.top(), text_w + 2, r.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                label,
            )

    def _draw_icon(self, painter, kind: str, r: QRectF, color: QColor):
        """对齐示意小图标：基准线（粗）+ 两根不同长度的条（随轴镜像）。"""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(color, 2))
        x, y, w, h = r.left(), r.top(), r.width(), r.height()
        if kind in ("top", "vcenter", "bottom"):
            if kind == "top":
                line_y = y + 1
                bars = [(x + 3, y + 4.5, w - 6), (x + 3, y + 9.5, w - 10)]
            elif kind == "vcenter":
                line_y = y + h / 2
                bars = [(x + 3, y + 3, w - 6), (x + 3, y + h - 6, w - 10)]
            else:
                line_y = y + h - 1
                bars = [(x + 3, y + h - 7.5, w - 6), (x + 3, y + 3, w - 10)]
            painter.drawLine(QPointF(x + 1, line_y), QPointF(x + w - 1, line_y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            for bx, by, bw in bars:
                painter.drawRect(QRectF(bx, by, bw, 3))
        else:
            if kind == "left":
                line_x = x + 1
                bars = [(x + 4.5, y + 3, h - 6), (x + 9.5, y + 3, h - 10)]
            elif kind == "hcenter":
                line_x = x + w / 2
                bars = [(x + 3, y + 3, h - 6), (x + w - 6, y + 3, h - 6)]
            else:
                line_x = x + w - 1
                bars = [(x + w - 7.5, y + 3, h - 6), (x + w - 12.5, y + 3, h - 10)]
            painter.drawLine(QPointF(line_x, y + 1), QPointF(line_x, y + h - 1))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            for bx, by, bh in bars:
                painter.drawRect(QRectF(bx, by, 3, bh))
        painter.restore()


class PointAlignDialog(QDialog):
    """整本对齐配置对话框（非模态，随画布对齐模式联动）。"""

    # ── 方向/对齐边标签表（字面量处显式标注上下文，供 ts 工具链提取） ──
    _AXIS_LABELS = (
        QCoreApplication.translate("PointAlignDialog", "Vertical (Y)"),
        QCoreApplication.translate("PointAlignDialog", "Horizontal (X)"),
    )
    _AXIS_KEYS = ("y", "x")  # 分段索引 → 轴
    _EDGE_LABELS = {
        "y": (
            QCoreApplication.translate("PointAlignDialog", "Top Edges"),
            QCoreApplication.translate("PointAlignDialog", "Vertical Centers"),
            QCoreApplication.translate("PointAlignDialog", "Bottom Edges"),
        ),
        "x": (
            QCoreApplication.translate("PointAlignDialog", "Left Edges"),
            QCoreApplication.translate("PointAlignDialog", "Horizontal Centers"),
            QCoreApplication.translate("PointAlignDialog", "Right Edges"),
        ),
    }
    _EDGE_ICONS = {
        "y": ("top", "vcenter", "bottom"),
        "x": ("left", "hcenter", "right"),
    }
    _EDGE_VALUES = {
        "y": ("top", "center", "bottom"),
        "x": ("left", "center", "right"),
    }

    _INNER_MARGINS = (8, 4, 8, 4)

    def __init__(self, proj, st_manager, canvas, parent: QWidget = None):
        super().__init__(parent)
        self._proj = proj
        self._st = st_manager
        self._canvas = canvas
        self._picked_item = None  # 最近点选的文字块 item（换页失效）
        self._user_set_target = False  # 用户显式改过目标后不再抢写
        self._updating = False  # spin ↔ 基准线双向同步防回环

        axis = (
            pcfg.point_align_axis
            if pcfg.point_align_axis in self._AXIS_KEYS
            else "y"
        )
        self.setWindowTitle(self.tr("Whole-book Alignment"))
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 方向 + 对齐边 ─────────────────────────────────────
        align_frame = GroupFrame()
        align_layout = QVBoxLayout(align_frame)
        align_layout.setContentsMargins(*self._INNER_MARGINS)
        align_layout.addWidget(QLabel(self.tr("Align")))
        self._axis_bar = _SegmentedBar(
            [(label, None) for label in self._AXIS_LABELS]
        )
        align_layout.addWidget(self._axis_bar)
        self._edge_bar = _SegmentedBar(
            list(zip(self._EDGE_LABELS[axis], self._EDGE_ICONS[axis]))
        )
        edge = (
            pcfg.point_align_edge_y if axis == "y" else pcfg.point_align_edge_x
        )
        if edge in self._EDGE_VALUES[axis]:
            self._edge_bar.set_current(self._EDGE_VALUES[axis].index(edge))
        align_layout.addWidget(self._edge_bar)
        layout.addWidget(align_frame)

        # ── 目标坐标 ──────────────────────────────────────────
        pos_frame = GroupFrame()
        pos_layout = QVBoxLayout(pos_frame)
        pos_layout.setContentsMargins(*self._INNER_MARGINS)
        pos_layout.addWidget(QLabel(self.tr("Target Position")))
        pos_row = QHBoxLayout()
        self._axis_label = QLabel("Y:" if axis == "y" else "X:")
        pos_row.addWidget(self._axis_label)
        self._spin = NoArrowsSpinBox()
        self._spin.setRange(-99999, 99999)
        pos_row.addWidget(self._spin, 1)
        pos_layout.addLayout(pos_row)
        hint = QLabel(
            self.tr(
                "Drag the guide line on canvas, click a block to take its edge, or type a value"
            )
        )
        hint.setWordWrap(True)
        pos_layout.addWidget(hint)
        layout.addWidget(pos_frame)

        # ── 应用范围 ──────────────────────────────────────────
        range_frame = GroupFrame()
        range_layout = QVBoxLayout(range_frame)
        range_layout.setContentsMargins(*self._INNER_MARGINS)
        range_layout.addWidget(QLabel(self.tr("Apply To")))

        self._slider = RangeSlider(0, max(0, proj.num_pages - 1))
        range_layout.addWidget(self._slider)

        self._range_info = QLabel()
        range_layout.addWidget(self._range_info)

        self._all_pages_cb = ConfigCheckBox(self.tr("All Pages"))
        self._all_pages_cb.setChecked(bool(pcfg.point_align_all_pages))
        self._slider.setEnabled(not self._all_pages_cb.isChecked())
        range_layout.addWidget(self._all_pages_cb)
        layout.addWidget(range_frame)

        # ── 按钮 ──────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        ok_btn = NoBorderPushBtn(self.tr("OK"))
        cancel_btn = NoBorderPushBtn(self.tr("Cancel"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        # ── 接线 ──────────────────────────────────────────────
        self._axis_bar.selection_changed.connect(self._on_axis_changed)
        self._edge_bar.selection_changed.connect(self._on_edge_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)
        self._slider.rangeChanged.connect(self._on_slider_changed)
        self._all_pages_cb.toggled.connect(self._on_all_pages_toggled)
        canvas.align_target_changed.connect(self._on_canvas_target)
        canvas.align_block_picked.connect(self._on_block_picked)
        canvas.align_page_refreshed.connect(self._on_page_refreshed)

        # ── 初始态：记忆方向/对齐边 + 当前页智能默认 + 进对齐模式 ──
        self._axis_bar.set_current(self._AXIS_KEYS.index(axis))
        self._update_range_info()
        target = self._compute_target()
        if target is None:
            target = 0
        self._spin.setValue(target)
        canvas.enter_align_mode(axis, target)
        self._update_ghosts()

    # ── 供 MainWindow 读取的结果 ─────────────────────────────

    def axis(self) -> str:
        return self._AXIS_KEYS[self._axis_bar.current()]

    def edge(self) -> str:
        return self._EDGE_VALUES[self.axis()][self._edge_bar.current()]

    def target_value(self) -> int:
        return self._spin.value()

    def page_filter(self):
        """``None``＝全部页；``[lo, hi]``＝页索引闭区间。"""
        if self._all_pages_cb.isChecked():
            return None
        return [self._slider.low(), self._slider.high()]

    # ── 内部联动 ─────────────────────────────────────────────

    def _on_axis_changed(self, idx: int):
        ax = self._AXIS_KEYS[idx]
        pcfg.point_align_axis = ax
        self._axis_label.setText("Y:" if ax == "y" else "X:")
        self._edge_bar.set_options(
            list(zip(self._EDGE_LABELS[ax], self._EDGE_ICONS[ax]))
        )
        edge = pcfg.point_align_edge_y if ax == "y" else pcfg.point_align_edge_x
        if edge in self._EDGE_VALUES[ax]:
            self._edge_bar.set_current(self._EDGE_VALUES[ax].index(edge))
        self._refresh_target()

    def _on_edge_changed(self, idx: int):
        ax = self.axis()
        if ax == "y":
            pcfg.point_align_edge_y = self._EDGE_VALUES[ax][idx]
        else:
            pcfg.point_align_edge_x = self._EDGE_VALUES[ax][idx]
        self._refresh_target()

    def _refresh_target(self):
        """方向/对齐边变化后重算目标：点选块 > 未手改时的智能默认 > 保持。"""
        val = self._compute_target()
        if val is not None:
            self._spin.setValue(val)
        self._canvas.set_align_guide(self.axis(), self._spin.value())
        self._update_ghosts()

    def _compute_target(self):
        ax, mode = self.axis(), self.edge()
        item = self._picked_item
        if (
            item is not None
            and item.scene() is not None
            and getattr(item, "blk", None) is not None
        ):
            return int(round(block_edge_value(_blk_br(item.blk), ax, mode)))
        if not self._user_set_target:
            cur = self._proj.current_img
            page = self._proj.pages.get(cur) if cur else None
            if page:
                return smart_default_target(page, ax, mode)
        return None

    def _set_target(self, val: int, from_user: bool):
        if from_user:
            self._user_set_target = True
        self._updating = True
        self._spin.setValue(int(val))
        self._updating = False
        self._canvas.set_align_guide(self.axis(), int(val))
        self._update_ghosts()

    def _on_spin_changed(self, val: int):
        if self._updating:
            return
        self._set_target(val, from_user=True)

    def _on_canvas_target(self, val: int):
        # 拖线/点空白：用户显式定位
        self._set_target(val, from_user=True)

    def _on_block_picked(self, item):
        self._picked_item = item
        self._set_target(self._compute_target(), from_user=True)

    def _on_page_refreshed(self):
        # 换页后旧页点选失效，目标重置为新页智能默认
        self._picked_item = None
        self._user_set_target = False
        self._refresh_target()

    def _update_ghosts(self):
        """按当前方向/对齐边/目标推算当前页落点，推送画布幽灵预览。"""
        rects = []
        if self._current_page_in_range():
            target = self._spin.value()
            ax, mode = self.axis(), self.edge()
            for item in self._st.textblk_item_list:
                blk = getattr(item, "blk", None)
                if blk is None or blk.angle != 0:
                    continue
                br = _blk_br(blk)
                delta = target - block_edge_value(br, ax, mode)
                if abs(delta) < 0.5:
                    continue
                if ax == "y":
                    rects.append(QRectF(br[0], br[1] + delta, br[2], br[3]))
                else:
                    rects.append(QRectF(br[0] + delta, br[1], br[2], br[3]))
        self._canvas.set_align_ghosts(rects)

    def _current_page_in_range(self) -> bool:
        if self._all_pages_cb.isChecked():
            return True
        cur = self._proj.current_img
        if not cur or cur not in self._proj.pages:
            return False
        idx = list(self._proj.pages).index(cur)
        return self._slider.low() <= idx <= self._slider.high()

    def _on_all_pages_toggled(self, checked: bool):
        pcfg.point_align_all_pages = checked
        self._slider.setEnabled(not checked)
        self._update_range_info()
        self._update_ghosts()

    def _on_slider_changed(self, lo: int, hi: int):
        self._update_range_info()
        self._update_ghosts()

    def _update_range_info(self):
        lo = self._slider.low() + 1
        hi = self._slider.high() + 1
        self._range_info.setText(
            self.tr("Page %1 ~ Page %2 (%3 pages)")
            .replace("%1", str(lo))
            .replace("%2", str(hi))
            .replace("%3", str(hi - lo + 1))
        )

    def done(self, r):
        """accept/reject/关窗的单一收口：退出画布对齐模式 + 落盘记忆项。"""
        self._canvas.leave_align_mode()
        save_config()
        super().done(r)
