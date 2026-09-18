"""泛用工作台的**批量任务视图**（规划 D20／D23／D27）。

四个批量任务共用这一份三段视图（D20：**批处理与审批是同一任务的两端**，
需分开的是任务类型，不是这两端）：

1. **候选列表**：一行一条，首列是勾选框（勾选＝本次要处理的条目）；
2. **审批预览**：点选某行即在**列表下方**展开该条的截图，**100% 原比例**
   （D11／D23——缩放过的审批图不足以支撑审查，故这里只用滚动，不缩放）；
3. **执行行**：任务参数控件（由 ``ui/workbench_tasks.py::BatchTask.options_spec``
   描述）＋ 任务额外动作 ＋ 执行按钮；无勾选时执行按钮禁用。

界面**只管三件事**（设计 §8）：调 ``plan`` 填列表／算数字 → 收勾选 →
把标识交回 ``apply``。它不写几何、不改 ``proj.pages``、不绕开
``ui/batch_ops.py``——那些都在 ``ui/workbench_tasks.py`` 与各引擎里。

D27 的告知弹窗在此处统一弹（正文由任务自己给，见 ``BatchTask.confirm_html``）：
**弹窗必须说清确认后会发生什么**，故四个任务的正文各不相同、都照实写。
"""

import numpy as np
from qtpy.QtCore import QCoreApplication, Qt, QTimer, Signal
from qtpy.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigComboBox, NoArrowsSpinBox
from ui.misc import get_theme_color

# 预览叠加框的取色（主题变量，不硬编码色值）：目标块＝强调色，
# 扩张前＝危险色、扩张后＝成功色（一眼看出往哪边长）
_ROLE_THEME_KEYS = {
    "target": "@accentPrimary",
    "old": "@dangerColor",
    "new": "@successColor",
}

# 报告里的错误码 → 用户可见文案（口径见设计 §13；不自己猜含义）。
# 字面量定义处显式标注翻译上下文（i18n 模块级翻译表规则）。
_ERROR_TEXT = {
    "cancelled": QCoreApplication.translate(
        "WorkbenchBatchView", "Cancelled — nothing was changed."
    ),
    "stale": QCoreApplication.translate(
        "WorkbenchBatchView",
        "The data changed since the list was built. The list has been reloaded — review it and run again.",
    ),
    "no-groups": QCoreApplication.translate(
        "WorkbenchBatchView",
        "No group could be merged (suspect false groupings are excluded).",
    ),
    "empty-queue": QCoreApplication.translate(
        "WorkbenchBatchView", "Nothing left in the queue."
    ),
    "nothing-to-expand": QCoreApplication.translate(
        "WorkbenchBatchView",
        "Every selected block already touches a neighbour or the page edge.",
    ),
    "commit before snapshot failed": QCoreApplication.translate(
        "WorkbenchBatchView",
        "Could not save the project before taking the backup version. Nothing was changed — check the disk and try again.",
    ),
    "batch version not written": QCoreApplication.translate(
        "WorkbenchBatchView",
        "Could not write the backup version. Nothing was changed — check the disk and try again.",
    ),
    "version-not-written": QCoreApplication.translate(
        "WorkbenchBatchView",
        "Could not write the backup version. Nothing was changed — check the disk and try again.",
    ),
    "inpainter-not-block-capable": QCoreApplication.translate(
        "WorkbenchBatchView",
        "The current inpaint module has no per-block path, so simple backgrounds cannot be classified. Pick another inpaint module.",
    ),
    "no-pages-processed": QCoreApplication.translate(
        "WorkbenchBatchView", "No page could be filled."
    ),
    "commit after writeback failed": QCoreApplication.translate(
        "WorkbenchBatchView",
        "The change was applied but saving failed. The one-step rollback is still available.",
    ),
}


class BatchTaskView(QWidget):
    """一个批量任务的三段视图（四个任务共用，差异由 ``BatchTask`` 描述）。"""

    # 非拉伸列的宽度上限（超过就交给省略号 + tooltip，别把表撑出横向滚动条）
    _COLUMN_WIDTH_CAP = 120
    # 勾选框列宽（resizeColumnsToContents 会把它压到表头文字宽，故填完再钉回去）
    _CHECK_COLUMN_WIDTH = 30

    jump_requested = Signal(str, int)  # (页名, 块下标)：跳画布（D26）
    notify_requested = Signal(str, str)  # (文案, kind)，kind 取值同通知中心
    status_requested = Signal(str)  # 面板底部日志行
    batch_applied = Signal(object)  # 本次批量写的版本号（供「撤销上次批量」）

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self._rows = []
        self._syncing = False
        self._options = {"reversed_groups": set()}
        self._option_widgets = {}
        self._preview_pixmap = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self._hint = QLabel(task.hint, self)
        self._hint.setObjectName("WorkbenchTaskHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._summary = QLabel("", self)
        self._summary.setObjectName("WorkbenchTaskSummary")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._table = QTableWidget(0, 1 + len(task.columns), self)
        self._table.setHorizontalHeaderLabels([""] + list(task.columns))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, self._CHECK_COLUMN_WIDTH)
        stretch = getattr(task, "stretch_column", -1)
        if stretch < 0 or stretch > len(task.columns):
            stretch = len(task.columns)
        self._stretch_column = stretch
        for col in range(1, len(task.columns) + 1):
            header.setSectionResizeMode(
                col,
                QHeaderView.ResizeMode.Stretch
                if col == stretch
                else QHeaderView.ResizeMode.Interactive,
            )
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.cellDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, 1)

        # 审批预览（D23：列表下方展开、100% 原比例、只滚动不缩放）
        self._preview_box = QWidget(self)
        preview_lay = QVBoxLayout(self._preview_box)
        preview_lay.setContentsMargins(0, 0, 0, 0)
        preview_lay.setSpacing(2)
        self._preview_caption = QLabel("", self._preview_box)
        self._preview_caption.setObjectName("WorkbenchTaskHint")
        preview_lay.addWidget(self._preview_caption)
        self._preview_area = QScrollArea(self._preview_box)
        self._preview_area.setWidgetResizable(False)
        self._preview_area.setMinimumHeight(120)
        self._preview_area.setMaximumHeight(280)
        self._preview_image = QLabel()
        self._preview_image.setObjectName("WorkbenchPreviewImage")
        self._preview_image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._preview_area.setWidget(self._preview_image)
        preview_lay.addWidget(self._preview_area)
        self._preview_box.setVisible(False)
        layout.addWidget(self._preview_box)

        options_row = QHBoxLayout()
        options_row.setSpacing(6)
        self._options_host = options_row
        layout.addLayout(options_row)
        self._build_options()

        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)
        self._actions_host = actions_row
        layout.addLayout(actions_row)
        self._build_actions()

        self._replan_timer = QTimer(self)
        self._replan_timer.setSingleShot(True)
        self._replan_timer.setInterval(180)
        self._replan_timer.timeout.connect(self.replan)

    # ── 参数控件（由 options_spec 描述） ──────────────────────────────

    def _build_options(self):
        for spec in self.task.options_spec():
            kind = spec.get("kind")
            if kind == "int":
                widget = NoArrowsSpinBox(self)
                widget.setRange(int(spec.get("min", 0)), int(spec.get("max", 999)))
                widget.setValue(int(spec.get("value", 0)))
                if spec.get("suffix"):
                    widget.setSuffix(spec["suffix"])
                widget.valueChanged.connect(self._on_option_changed)
            elif kind == "choice":
                widget = ConfigComboBox(self)
                for value, label in spec.get("items", ()):
                    widget.addItem(label, value)
                index = widget.findData(spec.get("value"))
                widget.setCurrentIndex(max(0, index))
                widget.currentIndexChanged.connect(self._on_option_changed)
            elif kind == "bool":
                widget = QCheckBox(spec.get("label", ""), self)
                widget.setChecked(bool(spec.get("value", False)))
                widget.toggled.connect(self._on_option_changed)
                self._options_host.addWidget(widget)
                self._option_widgets[spec["key"]] = widget
                continue
            else:
                continue
            self._options_host.addWidget(QLabel(spec.get("label", ""), self))
            self._options_host.addWidget(widget)
            self._option_widgets[spec["key"]] = widget
        self._options_host.addStretch(1)

    def options(self) -> dict:
        """当前参数值（含 ``run_action`` 写进 ``_options`` 的内部键）。"""
        values = dict(self._options)
        for key, widget in self._option_widgets.items():
            if isinstance(widget, NoArrowsSpinBox):
                values[key] = widget.value()
            elif isinstance(widget, ConfigComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
        return values

    def _on_option_changed(self, *_):
        # 参数（尤指扩张量）一变就重算，但打字/连点要合并成一次
        self._replan_timer.start()

    # ── 动作按钮 ────────────────────────────────────────────────────

    def _build_actions(self):
        self._action_buttons = []
        for spec in self.task.actions():
            button = QPushButton(spec["label"], self)
            button.clicked.connect(
                lambda _checked=False, key=spec["key"]: self._on_action(key)
            )
            self._actions_host.addWidget(button)
            self._action_buttons.append((spec, button))
        self._jump_btn = QPushButton(self.tr("Go to canvas"), self)
        self._jump_btn.setToolTip(
            self.tr("Switch to the row's page and select its block on the canvas.")
        )
        self._jump_btn.clicked.connect(self._on_jump)
        self._actions_host.addWidget(self._jump_btn)
        self._actions_host.addStretch(1)
        self._execute_btn = QPushButton(self.task.execute_label(0), self)
        self._execute_btn.setObjectName("WorkbenchExecuteButton")
        self._execute_btn.setEnabled(False)  # 未规划（无候选）时不可执行
        self._execute_btn.clicked.connect(self._on_execute)
        self._actions_host.addWidget(self._execute_btn)

    def _selected_rows(self):
        return [row for row in self._rows if row.checked]

    def _current_row(self):
        index = self._table.currentRow()
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def _on_action(self, key: str):
        rows = self._selected_rows() or (
            [self._current_row()] if self._current_row() else []
        )
        result = self.task.run_action(key, rows, self._options)
        if not result:
            self.notify_requested.emit(
                self.tr("Select a row first."), "warning"
            )
            return
        if result.get("notify"):
            self.status_requested.emit(result["notify"])
        if result.get("replan"):
            self.replan()

    def _on_jump(self):
        row = self._current_row()
        if row is None or row.block_index is None:
            return
        self.jump_requested.emit(row.pagename, row.block_index)

    # ── 规划与列表填充 ──────────────────────────────────────────────

    def replan(self):
        """调引擎的只读 ``plan`` 重填列表（数据一变就得重跑）。"""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = self.task.plan(self.options())
        except Exception as error:  # 引擎不该抛，抛了也要让面板活着
            self._rows = []
            self._fill_table([])
            self._summary.setText(
                self.tr("Could not build the list: %1").replace(
                    "%1", str(error)
                )
            )
            self.status_requested.emit(str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._rows = list(plan.get("rows") or [])
        self._fill_table(self._rows)
        self._summary.setText(plan.get("summary") or "")
        hint = plan.get("options_hint") or ""
        self._summary.setToolTip(hint)
        self._clear_preview()
        self._update_execute_state()
        if not self._rows:
            self.status_requested.emit(
                plan.get("summary") or self.tr("Nothing to show.")
            )

    def _fill_table(self, rows):
        self._syncing = True
        try:
            self._table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                check.setCheckState(
                    Qt.CheckState.Checked if row.checked else Qt.CheckState.Unchecked
                )
                self._table.setItem(index, 0, check)
                for col, text in enumerate(row.cells, start=1):
                    item = QTableWidgetItem(str(text))
                    item.setToolTip(str(text))
                    self._table.setItem(index, col, item)
            # 非拉伸列按内容自适应但**设上限**：一列长文本（如误识别子类型
            # 全列出来）会把表撑出横向滚动条、把拉伸列挤没；超出的部分交给
            # Qt 省略号，全文在单元格 tooltip 里
            self._table.resizeColumnsToContents()
            self._table.setColumnWidth(0, self._CHECK_COLUMN_WIDTH)
            for col in range(1, self._table.columnCount()):
                if col == self._stretch_column:
                    continue
                if self._table.columnWidth(col) > self._COLUMN_WIDTH_CAP:
                    self._table.setColumnWidth(col, self._COLUMN_WIDTH_CAP)
        finally:
            self._syncing = False

    def _on_item_changed(self, item):
        if self._syncing or item.column() != 0:
            return
        index = item.row()
        if 0 <= index < len(self._rows):
            self._rows[index].checked = (
                item.checkState() == Qt.CheckState.Checked
            )
        self._update_execute_state()

    def _update_execute_state(self):
        count = len(self._selected_rows())
        self._execute_btn.setText(self.task.execute_label(count))
        self._execute_btn.setEnabled(count > 0)
        for spec, button in getattr(self, "_action_buttons", []):
            if spec.get("needs_rows"):
                button.setEnabled(count > 0)

    # ── 审批预览（D11：100% 原比例） ─────────────────────────────────

    def _on_row_selected(self):
        row = self._current_row()
        if row is None:
            self._clear_preview()
            return
        try:
            preview = self.task.preview(row)
        except Exception as error:
            self.status_requested.emit(str(error))
            preview = None
        if preview is None:
            self._clear_preview()
            self._preview_caption.setText(
                self.tr("No preview available for this row (the page image is missing).")
            )
            self._preview_box.setVisible(True)
            return
        pixmap = _to_pixmap(preview.image)
        if pixmap is None:
            self._clear_preview()
            return
        _draw_overlays(pixmap, preview)
        self._preview_pixmap = pixmap
        self._preview_image.setPixmap(pixmap)
        self._preview_image.resize(pixmap.size())
        self._preview_caption.setText(
            self.tr("Preview at 100% scale (%1 x %2 px)").replace(
                "%1", str(pixmap.width())
            ).replace("%2", str(pixmap.height()))
        )
        self._preview_box.setVisible(True)

    def _clear_preview(self):
        self._preview_pixmap = None
        self._preview_image.clear()
        self._preview_caption.setText("")
        self._preview_box.setVisible(False)

    def _on_double_clicked(self, *_):
        self._on_jump()

    # ── 执行（D27：先弹告知弹窗）────────────────────────────────────

    def _confirm(self, rows) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(self.task.title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            self.tr("Confirm this batch action:")
        )
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setInformativeText(self.task.confirm_html(rows, self.options()))
        box.addButton(QMessageBox.StandardButton.Ok).setText(self.tr("Run"))
        box.addButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def _on_execute(self):
        rows = self._selected_rows()
        if not rows:
            return
        if not self._confirm(rows):
            return
        keys = [row.key for row in rows]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            report = self.task.apply(keys, self.options())
        except Exception as error:
            self.notify_requested.emit(str(error), "error")
            self.status_requested.emit(str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._handle_report(report)

    def _handle_report(self, report: dict):
        if not report.get("started"):
            code = report.get("error")
            if code == "stale":
                self.notify_requested.emit(_ERROR_TEXT["stale"], "warning")
                self.replan()
                return
            if code:
                self.notify_requested.emit(
                    _ERROR_TEXT.get(code, str(code)), "error"
                )
                self.status_requested.emit(str(code))
            self.replan()
            return
        verified = (report.get("writeback") or {}).get("verified", True)
        self.status_requested.emit(self._report_line(report))
        self.batch_applied.emit(report.get("version"))
        if not verified:
            self.notify_requested.emit(
                self.tr(
                    "Writeback finished but the canvas did not rebuild from the new data — please report this."
                ),
                "error",
            )
        self.replan()

    def _report_line(self, report: dict) -> str:
        parts = []
        for key, label in (
            ("deleted", self.tr("deleted")),
            ("groups", self.tr("groups merged")),
            ("blocks", self.tr("blocks")),
            ("pages", self.tr("pages")),
        ):
            value = report.get(key)
            if isinstance(value, int) and value:
                parts.append(label + " " + str(value))
            elif isinstance(value, list) and value:
                parts.append(label + " " + str(len(value)))
        if report.get("stopped"):
            parts.append(self.tr("cancelled"))
        if report.get("rolled_back"):
            parts.append(self.tr("rolled back"))
        return ", ".join(parts) or self.tr("Done.")


def _to_pixmap(image) -> "QPixmap | None":
    """RGB ``ndarray`` → ``QPixmap``（**不缩放**：D11 要求 100% 原比例）。"""
    if image is None:
        return None
    array = np.ascontiguousarray(image)
    if array.ndim == 2:
        height, width = array.shape
        qimage = QImage(
            array.tobytes(), width, height, width, QImage.Format.Format_Grayscale8
        )
    elif array.ndim == 3 and array.shape[2] == 3:
        height, width = array.shape[:2]
        qimage = QImage(
            array.tobytes(),
            width,
            height,
            3 * width,
            QImage.Format.Format_RGB888,
        )
    else:
        return None
    # copy()：QImage 不持有 numpy 缓冲的所有权，不拷贝会读到已释放内存
    return QPixmap.fromImage(qimage.copy())


def _draw_overlays(pixmap: QPixmap, preview) -> None:
    """把页面坐标系的叠加框画到截图上（视图坐标 ＝ 页面坐标 − 原点）。"""
    if not preview.overlays:
        return
    origin_x, origin_y = preview.origin
    painter = QPainter(pixmap)
    try:
        for overlay in preview.overlays:
            rect = overlay.get("rect")
            if not rect:
                continue
            key = _ROLE_THEME_KEYS.get(overlay.get("role"))
            color = get_theme_color(key=key) if key else QColor("#1e93e5")
            pen = QPen(color)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(
                int(rect[0]) - origin_x,
                int(rect[1]) - origin_y,
                int(rect[2] - rect[0]),
                int(rect[3] - rect[1]),
            )
    finally:
        painter.end()
