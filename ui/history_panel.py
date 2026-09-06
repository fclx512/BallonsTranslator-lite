"""PS 式撤销历史面板（二期：跨页分组）。

绑定 ``ui/canvas.py::Canvas.text_undo_stack``：自定义模型按页分组渲染
命令历史（跨页历史，阶段 4）——页头行 = 命令的页标签（pagename），
命令行 = 一个历史状态，行号语义 = 栈位置；当前位高亮自动跟随栈变化，
行右缘圆点标记保存点（cleanIndex，被撤销上限截断后为 -1 即无标记），
页屏障过期的僵尸命令灰显加「已过期」后缀。

点击命令行 = 循环调用 canvas.undo()/redo() 逐步跳转（auto_cross_page
路径：跨页自动切页，显式意图不二次确认）——复用每步记账（会话闭合、
手势取消、边界刷新），跳转期间经 ``_suppress_undo_toast`` 抑制撤回
toast。``image_filter=True`` 为修复区过滤视图（阶段4-3b）：只显示
当前页的修复命令（全局栈的过滤，非第二份栈），绘制模式跳转强制
全局栈步进。涂鸦（页级绘制栈）历史仍无入口，维持拍板。

QUndoStack 无公开列表模型（QUndoView 走私有 QUndoStackModel），分组
展示须自带模型；行结构 = 首行「原始状态」（对应 setEmptyLabel 语义）
+ 按栈序穿插的页头与命令状态行。
"""

from qtpy.QtCore import (
    QCoreApplication,
    QPointF,
    QSize,
    Qt,
    QAbstractListModel,
    QModelIndex,
)
from qtpy.QtGui import QColor, QPalette, QPainter
from qtpy.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ui import shared_widget as SW
from ui.textedit_commands import command_page_stale

# 跳转安全阀：undoLimit 上限 500 步，留一倍冗余防死循环
_JUMP_STEP_CAP = 1000

_ROLE_KIND = Qt.ItemDataRole.UserRole + 1  # 'header' | 'state'
_ROLE_STACK_POS = Qt.ItemDataRole.UserRole + 2  # 状态行对应栈位置；页头为 None
_ROLE_ZOMBIE = Qt.ItemDataRole.UserRole + 3


def _row_font(option, is_header=False):
    """紧凑行字号：整体比应用字体小一档，页头再小一档加粗。"""
    font = option.font
    if is_header:
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF() - 2.5, 7.5))
    else:
        font.setPointSizeF(max(font.pointSizeF() - 1.5, 8.0))
    return font


class _SavedDotDelegate(QStyledItemDelegate):
    """历史行渲染：页头/当前位高亮加粗/悬停底色/僵尸灰显/保存点圆点。

    自定义 paint 接管了整行绘制，QSS 的 ``QListView::item:hover`` 画不
    上来（曾是 hover 无效的根因）——悬停底色在这里自绘（@hoverBackgroundColor
    与全应用 hover 约定一致）；当前位行在 Highlight 底色上加粗。
    紧凑行高。
    """

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel

    def _palette_role(self, option, index, current_pos):
        stack = self._panel.stack
        if index.data(_ROLE_KIND) == "header":
            return "header"
        pos = index.data(_ROLE_STACK_POS)
        if pos == current_pos:
            return "current"
        zombie = index.data(_ROLE_ZOMBIE)
        if zombie or (stack is not None and pos is not None and pos > stack.index()):
            return "dim"
        return "normal"

    def sizeHint(self, option, index):
        is_header = index.data(_ROLE_KIND) == "header"
        font = _row_font(option, is_header)
        metrics = option.fontMetrics
        height = metrics.height() + (4 if is_header else 3)
        return QSize(metrics.horizontalAdvance(" ") * 24, height)

    def paint(self, painter, option, index):
        panel = self._panel
        stack = panel.stack
        current_pos = panel.current_filtered_pos() if panel.image_filter else (
            stack.index() if stack is not None else -1
        )
        role = self._palette_role(option, index, current_pos)
        is_header = role == "header"
        painter.save()
        rect = option.rect
        font = _row_font(option, is_header)
        if role == "current":
            # 激活位加粗：光靠 Highlight 底色在暗色主题下不够醒目
            font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        if role == "header":
            painter.fillRect(rect, option.palette.color(QPalette.ColorRole.Window))
            painter.setPen(option.palette.color(QPalette.ColorRole.PlaceholderText))
            margin = 6
            painter.drawText(
                rect.adjusted(margin, 2, -margin, -1),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(
                    index.data(Qt.ItemDataRole.DisplayRole),
                    Qt.TextElideMode.ElideRight,
                    rect.width() - margin * 2,
                ),
            )
            painter.restore()
            return

        # 悬停底色自绘（QSS ::item:hover 被自定义 paint 短路，见类 docstring）
        if option.state & QStyle.StateFlag.State_MouseOver:
            from ui.misc import get_theme_color

            painter.fillRect(
                rect, QColor(get_theme_color(key="@hoverBackgroundColor"))
            )

        if role == "current":
            painter.fillRect(rect, option.palette.color(QPalette.ColorRole.Highlight))
            painter.setPen(option.palette.color(QPalette.ColorRole.HighlightedText))
        elif role == "dim":
            painter.setPen(option.palette.color(QPalette.ColorRole.PlaceholderText))
        else:
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        margin = 8
        painter.drawText(
            rect.adjusted(margin, 0, -14, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(
                index.data(Qt.ItemDataRole.DisplayRole),
                Qt.TextElideMode.ElideRight,
                rect.width() - margin - 14,
            ),
        )

        # 保存点圆点：cleanIndex 对应状态行（含首行原始状态）；截断后
        # cleanIndex = -1 无标记
        if stack is not None:
            pos = index.data(_ROLE_STACK_POS)
            if pos is not None and stack.cleanIndex() == pos:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                if role != "current":
                    painter.setBrush(
                        option.palette.color(QPalette.ColorRole.Highlight)
                    )
                painter.drawEllipse(
                    QPointF(rect.right() - 8, rect.center().y()), 2.5, 2.5
                )
        painter.restore()


class _HistoryModel(QAbstractListModel):
    """栈命令 → 按页分组的平面行列表。

    行结构（栈序从旧到新）：首行原始状态（pos 0），其后每条命令一行，
    页标签变化处插入页头行。任何栈变化整体重建（≤500 行，重建廉价）。
    """

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._rows = []

    # ── 数据构建 ────────────────────────────────────────────────

    def rebuild(self):
        self.beginResetModel()
        panel = self._panel
        rows = []
        stack = panel.stack
        if stack is not None:
            proj = getattr(panel._canvas, "imgtrans_proj", None)
            if panel.image_filter:
                # 修复区过滤视图（阶段4-3b）：只显示当前页的修复命令，
                # 行号仍 = 全局栈位置（点击跳转沿全局栈逐步走）。切页后
                # 重建即空（handle_page_changed 钩子），回页恢复显示。
                cur_page = getattr(proj, "current_img", None)
                shown = [
                    i
                    for i in range(stack.count())
                    if getattr(stack.command(i), "image_history", False)
                    and getattr(stack.command(i), "pagename", None) == cur_page
                ]
                if shown:
                    rows.append({"kind": "state", "pos": shown[0],
                                 "text": panel.empty_label, "zombie": False})
                else:
                    rows.append({"kind": "state", "pos": stack.index(),
                                 "text": panel.empty_label, "zombie": False})
                for i in shown:
                    cmd = stack.command(i)
                    zombie = command_page_stale(cmd, proj)
                    text = cmd.text()
                    if zombie:
                        text = f"{text} ({panel.zombie_label})"
                    rows.append({"kind": "state", "pos": i + 1, "text": text,
                                 "zombie": zombie})
            else:
                rows.append({"kind": "state", "pos": 0,
                             "text": panel.empty_label, "zombie": False})
                last_page = None
                for i in range(stack.count()):
                    cmd = stack.command(i)
                    pname = getattr(cmd, "pagename", None)
                    if pname is None:
                        pname = "—"
                    if pname != last_page:
                        rows.append({"kind": "header", "pos": None,
                                     "text": pname, "zombie": False})
                        last_page = pname
                    zombie = command_page_stale(cmd, proj)
                    text = cmd.text()
                    # 组化命令（阶段 4 第二批）：单行组名 + 影响面摘要
                    summary_fn = getattr(cmd, "group_undo_summary", None)
                    if callable(summary_fn):
                        pages = summary_fn() or {}
                        if pages:
                            text = (
                                f"{text} · "
                                + QCoreApplication.translate(
                                    "HistoryPanel", "%1 pages / %2 blocks"
                                ).replace("%1", str(len(pages)))
                                .replace("%2", str(sum(pages.values())))
                            )
                    if zombie:
                        text = f"{text} ({panel.zombie_label})"
                    rows.append({"kind": "state", "pos": i + 1, "text": text,
                                 "zombie": zombie})
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row["text"]
        if role == _ROLE_KIND:
            return row["kind"]
        if role == _ROLE_STACK_POS:
            return row["pos"]
        if role == _ROLE_ZOMBIE:
            return row["zombie"]
        return None


class HistoryPanel(QWidget):
    """撤销历史浮层内容：按页分组的命令历史 + 点击跳转，见模块 docstring。

    ``image_filter=True`` 为修复区过滤视图（阶段4-3b）：只显示当前页的
    修复命令（全局栈的当前页过滤，非第二份栈），顶部带提示文案；点击
    跳转仍沿全局栈逐步走（途经的文本命令一并撤销/重做，线性史语义）。"""

    def __init__(self, parent=None, image_filter=False):
        super().__init__(parent)
        self._canvas = None
        self.stack = None
        self.image_filter = image_filter
        self.empty_label = self.tr("Original")
        self.zombie_label = self.tr("stale")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        if image_filter:
            hint_text = self.tr(
                "Only shows repair history of the current page — use the text-side history panel to undo across pages"
            )
            hint = QLabel(hint_text, self)
            hint.setWordWrap(True)
            hint.setObjectName("HistoryPanelHint")
            layout.addWidget(hint)
        self.model = _HistoryModel(self, self)
        self.view = QListView(self)
        self.view.setModel(self.model)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.view.clicked.connect(self._on_row_clicked)
        self.view.setItemDelegate(_SavedDotDelegate(self, self.view))
        self.view.setMouseTracking(True)
        self.view.setSpacing(0)
        self.view.setUniformItemSizes(True)
        self.view.setObjectName("HistoryPanelView")
        layout.addWidget(self.view)

    def sizeHint(self):
        # 紧凑默认宽度：纯文本行不需要宽面板（RailDockPanel 以内容
        # sizeHint 定尺寸下限）
        return QSize(176, 240)

    def showEvent(self, event):
        # 惰性绑定：canvas 全程单实例（ui/mainwindow.py 创建一次），首次
        # 展示时绑定文本栈并挂刷新钩子；showEvent 重建以捕获页屏障失效
        # 等不触发栈信号的状态变化。
        if self.stack is None:
            canvas = getattr(SW, "canvas", None)
            if canvas is not None:
                self._canvas = canvas
                self.stack = canvas.text_undo_stack
                self.stack.indexChanged.connect(lambda _i: self.model.rebuild())
                self.stack.cleanChanged.connect(lambda: self.model.rebuild())
                self.stack.canUndoChanged.connect(lambda: self.model.rebuild())
                self.stack.canRedoChanged.connect(lambda: self.model.rebuild())
        if self.stack is not None:
            self.model.rebuild()
        super().showEvent(event)

    def current_filtered_pos(self):
        """过滤视图的当前位高亮：已显示状态行中 pos ≤ 栈位置的最大者
        （栈顶未必是修复命令，直比 index 会失去高亮）。"""
        if self.stack is None:
            return -1
        idx = self.stack.index()
        best = -1
        for row in self.model._rows:
            pos = row.get("pos")
            if row["kind"] == "state" and pos is not None and pos <= idx:
                best = max(best, pos)
        return best

    def _on_row_clicked(self, index):
        canvas, stack = self._canvas, self.stack
        if canvas is None or stack is None or not index.isValid():
            return
        target = index.data(_ROLE_STACK_POS)
        if target is None or target == stack.index():
            return
        # 与 Ctrl+Z 同语义：手势悬开 = 取消恢复原值；会话先落账再跳
        if canvas._format_gesture is not None:
            canvas._cancel_format_gesture()
        canvas.commit_edit_sessions()
        if target == stack.index():
            return
        canvas._suppress_undo_toast = True
        # 文本模式走完整 undo()/redo() 入口；绘制模式（修复区过滤视图）
        # 必须强制全局栈步进——canvas.undo 的涂鸦栈优先路由会截胡，把
        # 跳转变成了撤销涂鸦
        if canvas.textEditMode():
            undo_step, redo_step = canvas.undo, canvas.redo
        else:
            undo_step = canvas._text_undo_step
            redo_step = canvas._text_redo_step
        try:
            guard = 0
            while stack.index() != target and guard < _JUMP_STEP_CAP:
                if stack.index() > target:
                    undo_step(auto_cross_page=True)
                else:
                    redo_step(auto_cross_page=True)
                guard += 1
        finally:
            canvas._suppress_undo_toast = False
