"""泛用工作台的**待办队列视图**（原文待校对／译文待重译）。

这两项**不是批量任务**（批次 C 交接 §4.2③⑥、§8）：列表里既没有删除，也
没有"勾选＝本次要改／要删"的整批写回，更没有 ``utils/batch_versions.py``
的版本快照。行是**人工记下的待办**或**程序给出的建议**，出口三个：

1. **跳画布**——切到该行所在页并选中该块，之后你自己决定怎么处理；
2. **直接开那张确认卡**——不跳过去，主窗口先走同一条跳转链路再把动作交给
   既有的框级入口（卡片仍落在画布的选中块上，写回仍要人点「应用」）；
3. **把这条从列表移除**（人工待办出队）／**忽略**（程序建议表态已看）。

故本视图与 ``ui/workbench_batch_view.py::BatchTaskView`` 分开写：那个视图
的三段结构是「勾选 → 参数 → 执行（含 D27 告知弹窗与整批可撤）」，把待办
塞进它的 ``apply`` 语义会立刻变成"批量删除"的误读面。

**分区**：人工待办与程序建议各占一段（``ui/workbench_tasks.py::ReviewSection``），
各有自己的标题、空态说明与处置按钮——前者是用户明确承诺的工作，后者只
是提示，不能共用勾选与完成语义（§3.3）。

审批图与批量任务共用同一套转换与浮层（D44）：本视图只发
``preview_requested``，显示在 ``ui/workbench_preview.py::WorkbenchPreviewPanel``。
过时提示同样只亮灯不自动重扫，出口是刷新钮（``replan``）。
"""

from typing import Dict, List, Tuple

from qtpy.QtCore import QCoreApplication, Qt, Signal
from qtpy.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget.row_table import MODE_CARD, RowTable
from ui.workbench_batch_view import RefreshButton, draw_overlays, to_pixmap

# 一个分区最多一次铺多少行（更多就在表内滚动）：待办可能几十条，
# 不让第一个分区把第二个分区挤出屏外
_SECTION_MAX_ROWS = 6

# 分区控件建在 ``_SectionWidgets`` 里，拿的是父视图的 ``tr``——**那不是
# ``self.tr``，扫描器看不见**（实测：按 ``view.tr(...)`` 写的两个按钮在中文
# 界面里一直显示英文）。故按仓库的模块级翻译表规则，在字面量定义处用
# ``QCoreApplication.translate`` 显式标注上下文，下游直接用已翻译值。
_GO_TO_CANVAS = QCoreApplication.translate("ReviewQueueView", "Go to canvas")
_GO_TO_CANVAS_TIP = QCoreApplication.translate(
    "ReviewQueueView",
    "Switch to the row's page and select its block on the canvas.",
)
_OPEN_CARD_TIP = QCoreApplication.translate(
    "ReviewQueueView", "Open the %1 card for the selected block here."
)


class _SectionWidgets:
    """一个分区的界面件（列表 ＋ 空态 ＋ 三个动作钮：跳画布／开卡／出队）。"""

    def __init__(self, spec, view: "ReviewQueueView"):
        self.spec = spec
        self.rows = []
        self.caption = QLabel(spec.title, view)
        self.caption.setObjectName("WorkbenchTaskHint")
        self.caption.setWordWrap(True)
        self.table = RowTable(MODE_CARD, view)
        self.table.checkToggled.connect(
            lambda row, checked, sid=spec.id: view._on_check_toggled(sid, row, checked)
        )
        self.table.rowSelected.connect(
            lambda _row, sid=spec.id: view._on_row_selected(sid)
        )
        self.table.cellClicked.connect(
            lambda row, col, sid=spec.id: view._on_cell_clicked(sid, row, col)
        )
        self.table.cellDoubleClicked.connect(
            lambda _row, _col, sid=spec.id: view._on_double_clicked(sid)
        )
        self.empty = QLabel(spec.empty, view)
        self.empty.setObjectName("WorkbenchTaskHint")
        self.empty.setWordWrap(True)
        self.jump_btn = QPushButton(_GO_TO_CANVAS, view)
        self.jump_btn.setToolTip(_GO_TO_CANVAS_TIP)
        self.jump_btn.clicked.connect(lambda _checked=False, sid=spec.id: view._on_jump(sid))
        # 现场处理的第二条出口（交接 §4.2③⑥）：不跳过去、直接开同一张确认卡
        # ——卡片本身就落在画布上，主窗口会先切页选中再拉起它。
        self.process_btn = QPushButton("", view)
        self.process_btn.clicked.connect(
            lambda _checked=False, sid=spec.id: view._on_process(sid)
        )
        self.action_btn = QPushButton(spec.action_label, view)
        self.action_btn.clicked.connect(
            lambda _checked=False, sid=spec.id: view._on_section_action(sid)
        )


class ReviewQueueView(QWidget):
    """一个待办队列的界面（分区列表 ＋ 跳画布／开确认卡 ＋ 出队／忽略）。"""

    jump_requested = Signal(str, int)  # (页名, 块下标)：跳画布
    # (页名, 块下标, 动作 id)：**直接打开**该块对应的框级确认卡（交接 §4.2 ③⑥：
    # 「跳画布」与「打开同一张原文校对卡／重译卡」是两条并列出口）
    action_requested = Signal(str, int, str)
    preview_requested = Signal(object, str)  # (QPixmap | None, 标题)
    preview_dismissed = Signal()
    notify_requested = Signal(str, str)  # (文案, kind)
    status_requested = Signal(str)
    plan_changed = Signal()  # 行集换了：面板据此刷新导航计数

    def __init__(self, task, parent=None, *, pre_replan=None):
        """
        Args:
            pre_replan: 重扫前的前置对齐回调（面板传
                ``GlossaryAgentPanel._sync_before_plan``；``None`` ＝跳过）。
        """
        super().__init__(parent)
        self.task = task
        self._pre_replan = pre_replan
        self._planned = False
        self._sections: List[_SectionWidgets] = []
        self._section_by_id: Dict[str, _SectionWidgets] = {}
        self._preview_pixmap = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self._hint = QLabel(task.hint, self)
        self._hint.setObjectName("WorkbenchTaskHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._summary = QLabel("", self)
        self._summary.setObjectName("WorkbenchTaskSummary")
        self._summary.setWordWrap(True)
        top_row.addWidget(self._summary, 1)
        self._refresh_btn = RefreshButton(self)
        self._refresh_btn.clicked.connect(self.replan)
        top_row.addWidget(self._refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        self._detail = QLabel("", self)
        self._detail.setObjectName("WorkbenchTaskDetail")
        self._detail.setWordWrap(True)
        self._detail.hide()
        layout.addWidget(self._detail)

        # 过时提示（默认灭）：与批量任务同一口径——只亮灯，出口是刷新钮
        self._stale_label = QLabel(
            self.tr(
                "Content changed after this list was built — click refresh to rebuild it."
            ),
            self,
        )
        self._stale_label.setObjectName("WorkbenchStaleHint")
        self._stale_label.setWordWrap(True)
        self._stale_label.hide()
        layout.addWidget(self._stale_label)

        for spec in task.sections():
            widgets = _SectionWidgets(spec, self)
            actions = QHBoxLayout()
            actions.setSpacing(6)
            actions.addWidget(widgets.jump_btn)
            if task.action_id:
                from utils.block_actions import ACTION_REGISTRY

                action = ACTION_REGISTRY.get(task.action_id)
                widgets.process_btn.setText(
                    action.short_label if action else task.action_id
                )
                if action is not None:
                    widgets.process_btn.setToolTip(
                        _OPEN_CARD_TIP.replace("%1", action.name)
                    )
                actions.addWidget(widgets.process_btn)
            else:
                widgets.process_btn.hide()
            actions.addWidget(widgets.action_btn)
            actions.addStretch(1)
            layout.addWidget(widgets.caption)
            layout.addWidget(widgets.empty)
            layout.addWidget(widgets.table)
            layout.addLayout(actions)
            self._sections.append(widgets)
            self._section_by_id[spec.id] = widgets
        # 多分区同屏：分段各自按内容高（见 _fit_table），余量留在最下方，
        # 不让每个分区的表都去平分页面高度（那会在行下方留出大片空白）
        layout.addStretch(1)

    # ── 面板用的读取口 ──────────────────────────────────────────────

    def tables(self) -> List[Tuple[RowTable, list]]:
        """各分区的 ``(表, 行)``（渲染台按此选一行出审批图）。"""
        return [(widgets.table, widgets.rows) for widgets in self._sections]

    # ── 规划与填充 ──────────────────────────────────────────────────

    def mark_stale(self):
        """内容在列表建好后被改过：亮「列表可能过时」（只亮灯，不重扫）。"""
        if self._planned:
            self._stale_label.show()

    def forget_plan(self):
        """换项目：旧列表与过时灯一并作废（等下次 ``replan`` 重建）。"""
        self._planned = False
        self._stale_label.hide()
        self._dismiss_preview()

    def replan(self):
        """重读队列（磁盘/画布上的编辑先对齐，见 ``pre_replan``）。"""
        self._stale_label.hide()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self._pre_replan is not None:
                self._pre_replan()
            plan = self.task.plan()
        except Exception as error:  # 队列读取不该抛，抛了也要让面板活着
            self._planned = False
            self._fill_sections({})
            self._summary.setText(
                self.tr("Could not build the list: %1").replace("%1", str(error))
            )
            self._detail.hide()
            self.status_requested.emit(str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._planned = True
        self._dismiss_preview()  # 行集重建＝旧审批图作废
        self._fill_sections(plan.get("sections") or {})
        self._summary.setText(plan.get("summary") or "")
        detail = plan.get("detail") or ""
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        self.plan_changed.emit()

    def _fill_sections(self, rows_by_section: Dict[str, list]):
        for widgets in self._sections:
            rows = list(rows_by_section.get(widgets.spec.id) or [])
            widgets.rows = rows
            widgets.table.set_rows(
                [self._row_visual(widgets.spec, row) for row in rows]
            )
            widgets.table.setVisible(bool(rows))
            widgets.empty.setVisible(not rows)
            if rows:
                self._fit_table(widgets.table, len(rows))
            widgets.caption.setText(self._section_caption(widgets.spec, len(rows)))
            self._update_action_state(widgets)

    def _fit_table(self, table: RowTable, count: int):
        """分区表按内容取高（上限 ``_SECTION_MAX_ROWS`` 行，更多在表内滚动）。

        多个分区各自平分页面高度的话，每张表都会在最后一行下面留一大片空
        白；待办队列一次就几十条的情况也不该把第二个分区挤出屏外。
        """
        row_height = table.verticalHeader().defaultSectionSize()
        visible_rows = min(count, _SECTION_MAX_ROWS)
        table.setFixedHeight(
            max(row_height, visible_rows * row_height) + 2 * table.frameWidth()
        )

    def _row_visual(self, spec, row) -> dict:
        fields = self.task.card_fields(row)
        if fields is None:
            fields = {
                "primary": row.cells[1] if len(row.cells) > 1 else "",
                "meta": row.pagename,
                "badge": spec.badge,
                "badge_tone": spec.tone,
                "rejected": False,
            }
        visual = dict(fields)
        visual["checked"] = row.checked
        visual["tooltip"] = " · ".join(
            str(cell) for cell in row.cells if cell and cell != "—"
        )
        return visual

    def _section_caption(self, spec, count: int) -> str:
        if not count:
            return spec.title
        return self.tr("%1 (%2)").replace("%1", spec.title).replace(
            "%2", str(count)
        )

    # ── 交互 ────────────────────────────────────────────────────────

    def _on_check_toggled(self, section_id: str, row: int, checked: bool):
        widgets = self._section_by_id.get(section_id)
        if widgets is None:
            return
        if 0 <= row < len(widgets.rows):
            widgets.rows[row].checked = checked
        self._update_action_state(widgets)

    def _current_row(self, section_id: str):
        widgets = self._section_by_id.get(section_id)
        if widgets is None:
            return None
        index = widgets.table.current_row()
        if 0 <= index < len(widgets.rows):
            return widgets.rows[index]
        return None

    def _action_rows(self, section_id: str) -> list:
        """处置作用到的行：勾选的优先，没勾选就用当前行（点一行再按即可）。"""
        widgets = self._section_by_id.get(section_id)
        if widgets is None:
            return []
        selected = [row for row in widgets.rows if row.checked]
        if selected:
            return selected
        current = self._current_row(section_id)
        return [current] if current is not None else []

    def _update_action_state(self, widgets: "_SectionWidgets"):
        rows = self._action_rows(widgets.spec.id)
        widgets.action_btn.setEnabled(bool(rows))
        has_row = self._current_row(widgets.spec.id) is not None
        widgets.jump_btn.setEnabled(has_row)
        widgets.process_btn.setEnabled(has_row)

    def _on_section_action(self, section_id: str):
        rows = self._action_rows(section_id)
        result = self.task.run_section_action(section_id, rows)
        if not result:
            self.notify_requested.emit(self.tr("Select a row first."), "warning")
            return
        if result.get("notify"):
            self.status_requested.emit(result["notify"])
        if result.get("replan"):
            self.replan()

    def _on_jump(self, section_id: str):
        row = self._current_row(section_id)
        if row is None or row.block_index is None:
            return
        self.jump_requested.emit(row.pagename, row.block_index)

    def _on_process(self, section_id: str):
        """直接开确认卡：本队列的现场处理动作落到选中那一行上。

        不改任何数据——真正的写回仍要人在卡片上点「应用」（人工在环总纲）。
        """
        row = self._current_row(section_id)
        if row is None or row.block_index is None or not self.task.action_id:
            return
        self.action_requested.emit(row.pagename, row.block_index, self.task.action_id)

    def _on_double_clicked(self, section_id: str):
        self._on_jump(section_id)

    # ── 审批预览（与批量任务同一套口径：100% 原比例，浮层显示）──────

    def _on_row_selected(self, section_id: str):
        widgets = self._section_by_id.get(section_id)
        if widgets is not None:
            self._update_action_state(widgets)
        row = self._current_row(section_id)
        if row is None:
            self._dismiss_preview()
            return
        try:
            preview = self.task.preview(row)
        except Exception as error:
            self.status_requested.emit(str(error))
            preview = None
        if preview is None:
            self._preview_pixmap = None
            self.preview_requested.emit(
                None, self.tr("No preview for this row (page image missing).")
            )
            return
        pixmap = to_pixmap(preview.image)
        if pixmap is None:
            self._dismiss_preview()
            return
        draw_overlays(pixmap, preview)
        self._preview_pixmap = pixmap
        self.preview_requested.emit(pixmap, self.task.row_caption(row))

    def _on_cell_clicked(self, section_id: str, row: int, _column: int):
        """点行＝重新要一次预览（选中行没变时也生效，见批量视图同款注释）。"""
        widgets = self._section_by_id.get(section_id)
        if widgets is None or self._preview_pixmap is not None:
            return
        if row == widgets.table.current_row():
            self._on_row_selected(section_id)

    def _dismiss_preview(self):
        self._preview_pixmap = None
        self.preview_dismissed.emit()

    def forget_preview(self):
        """浮层被用户关掉（点画布／Esc，面板转达）：清掉"当前这张图"的标记。"""
        self._preview_pixmap = None
