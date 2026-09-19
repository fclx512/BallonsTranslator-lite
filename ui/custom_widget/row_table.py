"""RowTable — 工作台批量任务的自绘候选列表（2026-09-19，替换裸 QTableWidget）。

两种行呈现模式（由 ``RowTable(mode=...)`` 定，一个实例只用一种）：

``MODE_TABLE``
    紧凑表格：列头 + 单行文本，列对齐可比较（框扩张的新旧矩形、背景修复的
    计数）。无竖网格线、无外框，只有极淡的行分隔线；选中＝主题色淡染整行。
``MODE_CARD``
    审批卡片：一行一张圆角卡（主文 + 次行元数据 + 右侧徽章），无列头
    （误识别清理、合并相邻框这类"字段少、靠预览图判断"的任务）。

**绘制全部在 delegate 里**（数据经 ``Qt.UserRole`` 传整行 dict），QSS 只管
底色与列头（``QTableView#WorkbenchRowTable``，见 ``config/stylesheet.css``
工作台段）——逐格 QSS 边框在 Qt 里渲染不全（会画出残缺竖线），且裸
QTableWidgetItem 无法画徽章/卡片。行 dict 的字段：

- 公共：``checked``（勾选态）、``tooltip``（悬浮全文）；
- MODE_TABLE：``cells``（与列头等长的文本列表）、可选 ``rejected``
  （整行删除线 + 弱化色）；
- MODE_CARD：``primary``（主文）、``meta``（次行）、``badge``（徽章文本，
  空＝不画）、``badge_tone``（``"warning"``/``"muted"``/``"accent"``）、
  ``rejected``。

主题配色走 ``ui/misc.py::get_theme_color``（按 key 现查），缓存于 delegate、
在 ``StyleChange``（全局换肤会重发样式表）时失效——不要在 paint 里直接
``_resolve_theme``（每次 json 落盘读，滚动会卡）。
"""

from qtpy.QtCore import (  # noqa: E402
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from qtpy.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QTableView,
)

MODE_TABLE = "table"
MODE_CARD = "card"

# 勾选框（对齐 ConfigCheckBox 的 13px 视觉，delegate 里自绘——icons 里的
# 对勾 svg 是蓝色描边，放在主题色填充上会隐形，故勾选态的白勾直接画）
_CHECK_SIZE = 14
_CHECK_RADIUS = 4
# MODE_TABLE 行高 / 非拉伸列宽上限（超宽交给省略号 + tooltip）
_ROW_HEIGHT = 34
_COLUMN_WIDTH_CAP = 120
# MODE_CARD 卡片行高与圆角
_CARD_HEIGHT = 56
_CARD_RADIUS = 8


def _theme_color(key: str, alpha: int = 255) -> QColor:
    from ui.misc import get_theme_color

    return get_theme_color(key=key, alpha=alpha)


def _luma(color: QColor) -> float:
    """感知亮度（0~1，够用于挑对比色，不做精确的线性化）。"""
    return (
        0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
    )


def _composite(tint: QColor, base: QColor) -> QColor:
    """半透明染底叠在底色上的实际观感色（对比度要按它算）。"""
    a = tint.alphaF()
    return QColor.fromRgbF(
        tint.redF() * a + base.redF() * (1 - a),
        tint.greenF() * a + base.greenF() * (1 - a),
        tint.blueF() * a + base.blueF() * (1 - a),
    )


class _RowModel(QAbstractTableModel):
    """行 dict 列表 + 列头；DisplayRole 只服务表格模式的文本。"""

    def __init__(self, mode, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.labels: list = []
        self.rows: list = []

    # QAbstractTableModel ────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return 1 if self.mode == MODE_CARD else 1 + len(self.labels)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()] if 0 <= index.row() < len(self.rows) else {}
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.CheckStateRole:
            if index.column() == 0:
                return (
                    Qt.CheckState.Checked
                    if row.get("checked")
                    else Qt.CheckState.Unchecked
                )
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("tooltip") or ""
        if self.mode == MODE_TABLE and role == Qt.ItemDataRole.DisplayRole:
            cells = row.get("cells") or []
            col = index.column()
            if 1 <= col <= len(cells):
                return cells[col - 1]
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        # 勾选态只经 RowTable._set_check 写入（用户点击 / 程序设置共用），
        # 模型自身不响应 setData，避免两条写路径
        return False

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 1 <= section <= len(self.labels)
        ):
            return self.labels[section - 1]
        return None


class _RowDelegate(QStyledItemDelegate):
    """全部行视觉都在这里：选中染底、行分隔线、勾选框、徽章、卡片。"""

    def __init__(self, view: "RowTable", parent=None):
        super().__init__(parent)
        self.view = view
        self._color_cache: dict = {}

    def invalidate_colors(self):
        self._color_cache.clear()

    def color(self, key: str, alpha: int = 255) -> QColor:
        cache_key = (key, alpha)
        if cache_key not in self._color_cache:
            self._color_cache[cache_key] = _theme_color(key, alpha)
        return self._color_cache[cache_key]

    def selection_text(self):
        """高亮态（选中染底）上的文字颜色：``(主文色, 次要色)``。

        部分主题的 ``@dragTextColor`` 是暗色（浅色主题用它），落在深色
        染底上就看不清——按染底的实际观感（主题色半透明叠在卡片底色上）
        的明暗，在 ``@dragTextColor``／``@inverseTextColor`` 两个候选里
        挑对比更高的那个（"反色"）；次要色取主文色降不透明度。
        """
        cached = self._color_cache.get("@_selection_text")
        if cached is None:
            bg = _composite(
                self.color("@accentPrimary", 46),
                self.color("@inputBackgroundColor"),
            )
            primary = max(
                (self.color("@dragTextColor"), self.color("@inverseTextColor")),
                key=lambda c: abs(_luma(c) - _luma(bg)),
            )
            sub = QColor(primary)
            sub.setAlpha(170)
            cached = (primary, sub)
            self._color_cache["@_selection_text"] = cached
        return cached

    # 尺寸 ───────────────────────────────────────────────────────────

    def sizeHint(self, option, index):
        if self.view.mode == MODE_CARD:
            return QSize(120, _CARD_HEIGHT)
        width = _ROW_HEIGHT  # 首列：勾选框列
        if index.column() > 0:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            metrics = QFontMetrics(option.font)
            width = min(
                metrics.horizontalAdvance(text) + 16, self.view.column_width_cap()
            )
        return QSize(width, _ROW_HEIGHT)

    # 绘制 ───────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, index):
        painter.save()
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        selected = bool(option.state & QStyle.State_Selected)
        hovered = (
            not selected
            and index.row() == getattr(self.view, "_hovered_row", -1)
            and index.row() >= 0
        )
        if self.view.mode == MODE_CARD:
            # 圆角卡/徽章/勾选框是形状绘制，须开 AA；文本不受该开关影响
            painter.setRenderHint(QPainter.Antialiasing, True)
            self._paint_card(painter, option.rect, row, selected, hovered)
        else:
            self._paint_cell(painter, option, index, row, selected, hovered)
        painter.restore()

    def _paint_cell(self, painter, option, index, row, selected, hovered):
        rect = QRectF(option.rect)
        if selected:
            painter.fillRect(option.rect, self.color("@accentPrimary", 46))
        elif hovered:
            painter.fillRect(option.rect, self.color("@hoverBackgroundColor", 90))
        # 行分隔线：每个格子画自己脚下的那段，拼起来即通栏细线
        painter.setPen(QPen(self.color("@borderColor", 70), 1))
        painter.drawLine(
            int(rect.left()), int(rect.bottom()), int(rect.right()), int(rect.bottom())
        )
        if index.column() == 0:
            # 勾选框是圆角矩形 + 斜线对勾，开 AA；行分隔线与文本保持硬边
            painter.setRenderHint(QPainter.Antialiasing, True)
            self._paint_check(
                painter, self._check_rect(option.rect), bool(row.get("checked"))
            )
            painter.setRenderHint(QPainter.Antialiasing, False)
            return
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        metrics = QFontMetrics(painter.font())
        if row.get("rejected") and not selected:
            font = painter.font()
            font.setStrikeOut(True)
            painter.setFont(font)
            painter.setPen(QPen(self.color("@disabledForegroundColor")))
        else:
            if selected:
                pen_color = self.selection_text()[0]
            else:
                pen_color = self.color("@textColor")
            if row.get("rejected"):
                font = painter.font()
                font.setStrikeOut(True)
                painter.setFont(font)
            painter.setPen(QPen(pen_color))
        painter.drawText(
            rect.adjusted(4, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(
                text, Qt.TextElideMode.ElideRight, max(10, int(rect.width()) - 10)
            ),
        )

    def _paint_card(self, painter, rect, row, selected, hovered):
        card = QRectF(rect).adjusted(2, 3, -2, -3)
        path = QPainterPath()
        path.addRoundedRect(card, _CARD_RADIUS, _CARD_RADIUS)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color("@accentPrimary", 36))
            painter.drawPath(path)
            painter.setPen(QPen(self.color("@accentPrimary"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            if hovered:
                painter.setBrush(self.color("@hoverBackgroundColor", 90))
            else:
                painter.setBrush(self.color("@inputBackgroundColor"))
            painter.drawPath(path)

        rejected = bool(row.get("rejected"))
        self._paint_check(
            painter,
            self._check_rect(QRect(rect.topLeft(), rect.size()), inside_card=True),
            bool(row.get("checked")),
        )
        text_left = card.left() + _CHECK_SIZE + 24
        text_right = card.right() - (56 if row.get("badge") else 12)
        primary = row.get("primary") or ""
        font = painter.font()
        if rejected:
            font.setStrikeOut(True)
        painter.setFont(font)
        # 高亮态（选中）文字按染底反色挑选；hover 只是轻微提亮底色，沿用常规色。
        # 已驳回行选中时同样反色（可读性优先，驳回感由删除线传达）
        if selected:
            primary_pen = QPen(self.selection_text()[0])
        else:
            primary_pen = QPen(
                self.color("@disabledForegroundColor" if rejected else "@dragTextColor")
            )
        painter.setPen(primary_pen)
        painter.drawText(
            QRectF(text_left, card.top() + 8, text_right - text_left, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._elided(painter, primary, text_right - text_left),
        )
        sub_font = QFont(font)
        sub_font.setPointSizeF(max(8.0, sub_font.pointSizeF() - 0.5))
        sub_font.setStrikeOut(rejected)
        painter.setFont(sub_font)
        # 次行（元数据）原用 disabled 灰，落在选中染底上几乎不可见（2026-09-19
        # 实测）：选中时改用反色挑选出的次要色，未选中维持原灰
        if selected:
            painter.setPen(QPen(self.selection_text()[1]))
        else:
            painter.setPen(QPen(self.color("@disabledForegroundColor")))
        painter.drawText(
            QRectF(text_left, card.top() + 29, text_right - text_left, 16),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._elided(painter, row.get("meta") or "", text_right - text_left),
        )
        badge = row.get("badge") or ""
        if badge:
            self._paint_badge(painter, card, badge, row.get("badge_tone") or "muted")

    def _paint_badge(self, painter, card: QRectF, text: str, tone: str):
        fg_key = {
            "warning": "@warningColor",
            "accent": "@accentPrimary",
        }.get(tone, "@disabledForegroundColor")
        fg = self.color(fg_key)
        bg = QColor(fg)
        bg.setAlpha(32 if tone != "muted" else 20)
        metrics = QFontMetrics(painter.font())
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 4
        rect = QRectF(
            card.right() - width - 10,
            card.center().y() - height / 2,
            width,
            height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QPen(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_check(self, painter, rect: QRect, checked: bool):
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), _CHECK_RADIUS, _CHECK_RADIUS)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color("@accentPrimary"))
            painter.drawPath(path)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            x, y = rect.left(), rect.top()
            s = rect.width()
            painter.drawPolyline(
                [
                    QPointF(x + s * 0.25, y + s * 0.55),
                    QPointF(x + s * 0.42, y + s * 0.72),
                    QPointF(x + s * 0.78, y + s * 0.28),
                ]
            )
        else:
            painter.setPen(QPen(self.color("@borderColor"), 1))
            painter.setBrush(self.color("@inputBackgroundColor"))
            painter.drawPath(path)

    def _check_rect(self, cell_rect: QRect, inside_card: bool = False) -> QRect:
        size = _CHECK_SIZE
        if inside_card:
            return QRect(
                cell_rect.left() + 12, cell_rect.center().y() - size // 2, size, size
            )
        return QRect(
            cell_rect.left() + (cell_rect.width() - size) // 2,
            cell_rect.top() + (cell_rect.height() - size) // 2,
            size,
            size,
        )

    @staticmethod
    def _elided(painter: QPainter, text: str, width: float) -> str:
        metrics = QFontMetrics(painter.font())
        return metrics.elidedText(
            text, Qt.TextElideMode.ElideRight, max(10, int(width))
        )

    # 用户点在勾选框上 → 切换（编辑事件不走 paint，须在此命中判定） ──────

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and index.column() == 0:
            rect = self._check_rect(
                option.rect, inside_card=self.view.mode == MODE_CARD
            )
            pos = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            if rect.contains(pos):
                self.view._user_toggled(index.row())
                return True
        return super().editorEvent(event, model, option, index)


class RowTable(QTableView):
    """自绘行列表（见模块 docstring；调用面见 ``set_rows`` 等方法）。"""

    checkToggled = Signal(int, bool)  # 用户切了勾选框 (行, 新状态)
    rowSelected = Signal(int)  # 选中行变化（-1 = 无）
    cellClicked = Signal(int, int)  # 点击某行（已选中行也会发；列号=视觉列）
    cellDoubleClicked = Signal(int, int)

    def __init__(self, mode=MODE_TABLE, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.setObjectName("WorkbenchRowTable")
        self._stretch_column = -1
        self._column_cap = _COLUMN_WIDTH_CAP
        self._hovered_row = -1  # 悬停高亮（delegate 读，见 _paint_card/_paint_cell）

        self._model = _RowModel(mode, self)
        self.setModel(self._model)
        self._delegate = _RowDelegate(self, self)
        self.setItemDelegate(self._delegate)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setShowGrid(False)
        self.setWordWrap(False)
        # 悬停行高亮要收 mouse move（默认只在按压时才有 move 事件）
        self.setMouseTracking(True)

        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        header.setHighlightSections(False)
        header.setVisible(mode == MODE_TABLE)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, _ROW_HEIGHT)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(
            _CARD_HEIGHT if mode == MODE_CARD else _ROW_HEIGHT
        )

        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    # ── 对外 API ─────────────────────────────────────────────────────

    def set_header_labels(self, labels) -> None:
        """表格模式列头（不含勾选列）。"""
        self._model.labels = list(labels)
        self._apply_header_layout()
        count = self._model.columnCount()
        if count > 1:
            self._model.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 1, count - 1
            )

    def set_stretch_column(self, column: int) -> None:
        """拉伸列号（含勾选列的表格列号；<0 ＝最后一列）。"""
        self._stretch_column = column
        self._apply_header_layout()

    def set_rows(self, rows) -> None:
        """整表替换行（dict 形状见模块 docstring）。"""
        self._model.beginResetModel()
        self._model.rows = list(rows)
        self._model.endResetModel()
        self.clearSelection()

    def set_checked(self, row: int, checked: bool) -> None:
        """程序设置勾选（不发 ``checkToggled``）。"""
        self._set_check(row, checked)

    def current_row(self) -> int:
        index = self.currentIndex()
        return index.row() if index.isValid() else -1

    def setCurrentCell(self, row: int, column: int):
        """兼容 QTableWidget 调用点（``BatchTaskView`` 的既有用户）。

        列号超出本控件的列数（如旧调用点按多列表格传列 1，而本表已是
        卡片模式单列）时夹回有效范围，保证选中确实发生。
        """
        if 0 <= row < len(self._model.rows):
            count = self._model.columnCount()
            index = self._model.index(row, max(0, min(column, count - 1)))
            self.setCurrentIndex(index)
            self.selectionModel().select(
                index,
                self.selectionModel().SelectionFlag.ClearAndSelect
                | self.selectionModel().SelectionFlag.Rows,
            )

    def column_width_cap(self) -> int:
        return self._column_cap

    # ── 内部 ─────────────────────────────────────────────────────────

    def _set_check(self, row: int, checked: bool) -> None:
        if 0 <= row < len(self._model.rows):
            self._model.rows[row]["checked"] = bool(checked)
            index = self._model.index(row, 0)
            self._model.dataChanged.emit(index, index)

    def _user_toggled(self, row: int) -> None:
        if 0 <= row < len(self._model.rows):
            state = not bool(self._model.rows[row].get("checked"))
            self._set_check(row, state)
            self.checkToggled.emit(row, state)

    def _apply_header_layout(self):
        if self.mode == MODE_CARD:
            header = self.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            return
        header = self.horizontalHeader()
        count = self._model.columnCount()
        stretch = self._stretch_column
        if stretch < 0 or stretch >= count:
            stretch = count - 1
        for col in range(count):
            if col == 0:
                header.setSectionResizeMode(
                    col, QHeaderView.ResizeMode.Fixed
                )
            elif col == stretch:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(
                    col, QHeaderView.ResizeMode.Interactive
                )
        header.resizeSection(0, _ROW_HEIGHT)

    def _on_selection_changed(self, *_):
        self.rowSelected.emit(self.current_row())

    def mouseMoveEvent(self, event):
        self._update_hover(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hovered_row != -1:
            self._hovered_row = -1
            self.viewport().update()
        super().leaveEvent(event)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        # 滚动后光标下的行变了但 move 事件不会来，跟着重算一次悬停行
        if self.viewport().underMouse():
            self._update_hover(self.viewport().mapFromGlobal(QCursor.pos()))

    def _update_hover(self, pos):
        index = self.indexAt(pos)
        row = index.row() if index.isValid() else -1
        if row != self._hovered_row:
            self._hovered_row = row
            # 行少、重绘便宜：整视口重绘比算新旧行区域并集更省心
            self.viewport().update()

    def _on_clicked(self, index: QModelIndex):
        if index.isValid():
            self.cellClicked.emit(index.row(), index.column())

    def _on_double_clicked(self, index: QModelIndex):
        if index.isValid():
            self.cellDoubleClicked.emit(index.row(), index.column())

    def changeEvent(self, event):
        # 全局换肤会重发样式表 → StyleChange：delegate 配色缓存失效
        if event.type() == QEvent.Type.StyleChange:
            self._delegate.invalidate_colors()
        super().changeEvent(event)
