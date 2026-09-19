"""泛用工作台的**批量任务视图**（规划 D20／D23／D27）。

四个批量任务共用这一份三段视图（D20：**批处理与审批是同一任务的两端**，
需分开的是任务类型，不是这两端）：

1. **候选列表**：一行一条，首列是勾选框（勾选＝本次要处理的条目）；
2. **审批预览**：点选某行即把该条的截图（**100% 原比例**）交给
   ``ui/workbench_preview.py::WorkbenchPreviewPanel`` 浮层——D44 起它已不在
   本页里（原先固定 120~280px 高，大图看不全）；行集一变（重规划）或换任务
   就收起，不让旧图留在屏上对着一条已经不存在的行；
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
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigCheckBox, ConfigComboBox, NoArrowsSpinBox
from ui.custom_widget.row_table import MODE_CARD, MODE_TABLE, RowTable
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

    jump_requested = Signal(str, int)  # (页名, 块下标)：跳画布（D26）
    # (QPixmap | None, 标题)：审批图交给面板的浮层显示（D44：它已不在这页里）
    preview_requested = Signal(object, str)
    # 行集变了（重规划）／任务切走了：浮层该收起，旧图对应的行可能已不存在
    preview_dismissed = Signal()
    # 规划跑完（行集／摘要都换了）：面板据此刷新导航上的「还有 N 个未处理」
    plan_changed = Signal()
    notify_requested = Signal(str, str)  # (文案, kind)，kind 取值同通知中心
    status_requested = Signal(str)  # 面板底部日志行
    batch_applied = Signal(object)  # 本次批量写的版本号（供「撤销上次批量」）

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self._rows = []
        self._options = {"reversed_groups": set()}
        self._option_widgets = {}
        self._option_specs = {}
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

        self._card_mode = getattr(task, "row_view", "table") == MODE_CARD
        self._table = RowTable(
            MODE_CARD if self._card_mode else MODE_TABLE, self
        )
        if not self._card_mode:
            self._table.set_header_labels(list(task.columns))
        stretch = getattr(task, "stretch_column", -1)
        if stretch < 0 or stretch > len(task.columns):
            stretch = len(task.columns)
        self._table.set_stretch_column(stretch)
        self._table.checkToggled.connect(self._on_check_toggled)
        self._table.rowSelected.connect(self._on_row_selected)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, 1)

        # 审批图不再嵌在本页（D44）：选中行时经 preview_requested 交给
        # 面板的浮层（可缩放/平移，不占工作台宽度与纵向空间）

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
        specs = list(self.task.options_spec())
        self._option_specs = {spec.get("key"): spec for spec in specs}
        for spec in specs:
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
                # 必须用封装控件：裸 QCheckBox 的 indicator 走原生样式（只
                # #ConfigCheckBox/#ParamCheckBox/QDialog 有 QSS 规则）
                widget = ConfigCheckBox(spec.get("label", ""), self)
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
        self._sync_suffixes()

    def _sync_suffixes(self):
        """数值框后缀跟着被它依赖的那个选项走（扩张量：px 模式「10 px」、
        比例模式「10 %」）——两处单位说法不一致时用户没法判断哪个算数。

        映射由任务在 ``options_spec`` 里用 ``suffix_map=(选项键, {值: 后缀})``
        给出（后缀是通用单位记号，不翻译）。
        """
        for key, spec in self._option_specs.items():
            mapper = spec.get("suffix_map")
            widget = self._option_widgets.get(key)
            if not mapper or widget is None:
                continue
            control_key, suffix_by_value = mapper
            control = self._option_widgets.get(control_key)
            if control is None:
                continue
            if isinstance(control, ConfigComboBox):
                current = control.currentData()
            elif isinstance(control, NoArrowsSpinBox):
                current = control.value()
            else:
                current = control.isChecked()
            widget.setSuffix(suffix_by_value.get(current, ""))

    def options(self) -> dict:
        """当前参数值（含 ``run_action`` 写进 ``_options`` 的内部键）。"""
        values = dict(self._options)
        for key, widget in self._option_widgets.items():
            if isinstance(widget, NoArrowsSpinBox):
                values[key] = widget.value()
            elif isinstance(widget, ConfigComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, ConfigCheckBox):
                values[key] = widget.isChecked()
        return values

    def _on_option_changed(self, *_):
        # 参数（尤指扩张量）一变就重算，但打字/连点要合并成一次
        self._sync_suffixes()
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
        self._jump_btn.setEnabled(False)  # 没选中行时无目标，按钮别装成可点
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
        index = self._table.current_row()
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
        # 行集重建＝旧预览图作废（执行完批量后，原行可能已不存在）：先收起
        # 浮层再填表，填表若恢复选中行会重新发一张图上来。
        self._dismiss_preview()
        self._fill_table(self._rows)
        self._summary.setText(plan.get("summary") or "")
        hint = plan.get("options_hint") or ""
        self._summary.setToolTip(hint)
        self._update_execute_state()
        # 计数口径跟着行集走（参数一变候选就变），故规划完通知面板刷新导航
        self.plan_changed.emit()
        # 空列表不往面板日志再写一遍 summary：它就是页面上那行摘要，而日志
        # 是跨任务共用的（在术语表页看见别任务的「将覆盖 0 页」纯属噪声）。

    def _fill_table(self, rows):
        self._table.set_rows([self._row_visual(row) for row in rows])

    def _row_visual(self, row) -> dict:
        """``TaskRow`` → 自绘行的 dict（形状见 ``ui/custom_widget/row_table.py``）。"""
        visual = {"checked": row.checked, "tooltip": ""}
        if self._card_mode:
            fields = self.task.card_fields(row)
            if fields is None:
                cells = [str(c) for c in row.cells]
                fields = {
                    "primary": cells[0] if cells else "",
                    "meta": " · ".join(cells[1:]),
                    "badge": "",
                    "badge_tone": "muted",
                    "rejected": False,
                }
            visual.update(fields)
            visual["tooltip"] = " · ".join(
                str(c) for c in row.cells if c and c != "—"
            )
        else:
            visual["cells"] = [str(c) for c in row.cells]
            visual["rejected"] = False
            visual["tooltip"] = " · ".join(
                str(c) for c in row.cells if c and c != "—"
            )
        return visual

    def _on_check_toggled(self, row: int, checked: bool):
        if 0 <= row < len(self._rows):
            self._rows[row].checked = checked
        self._update_execute_state()

    def _update_execute_state(self):
        count = len(self._selected_rows())
        self._execute_btn.setText(self.task.execute_label(count))
        self._execute_btn.setEnabled(count > 0)
        self._jump_btn.setEnabled(self._current_row() is not None)
        for spec, button in getattr(self, "_action_buttons", []):
            if spec.get("needs_rows"):
                button.setEnabled(count > 0)

    # ── 审批预览（D11／D44：100% 原比例，显示在面板的浮层里）─────────

    def _on_row_selected(self):
        row = self._current_row()
        self._jump_btn.setEnabled(row is not None)
        if row is None:
            self._dismiss_preview()
            return
        try:
            preview = self.task.preview(row)
        except Exception as error:
            self.status_requested.emit(str(error))
            preview = None
        if preview is None:
            # 这张图取不到：让浮层把原因显示出来（标题＝原因，短到能塞进标题条）
            self._preview_pixmap = None
            self.preview_requested.emit(
                None,
                self.tr("No preview for this row (page image missing)."),
            )
            return
        pixmap = _to_pixmap(preview.image)
        if pixmap is None:
            self._dismiss_preview()
            return
        _draw_overlays(pixmap, preview)
        self._preview_pixmap = pixmap
        # 标题说清"在看哪一页的哪个框"：图尺寸／"100% 原比例"对审阅没有意义
        # （缩放与否看标题条右侧的读数）
        self.preview_requested.emit(pixmap, self.task.row_caption(row))

    def _dismiss_preview(self):
        """收起浮层（行集重建／换任务／当前行没了）。"""
        self._preview_pixmap = None
        self.preview_dismissed.emit()

    def forget_preview(self):
        """浮层被用户关掉了（点画布／Esc，面板转达）：清掉"当前这张图"的标记。

        它只影响"点行是否要重新取图"的判断（见 ``_on_cell_clicked``）——
        列表只有一行时，关掉浮层后再点那行不会发 ``itemSelectionChanged``，
        靠这个标记才知道该重新要一张。
        """
        self._preview_pixmap = None

    def _on_cell_clicked(self, row: int, _column: int):
        """点行＝重新要一次预览（选中行没变时也生效）。

        只有一行候选时，关掉浮层后再点那行不会触发 ``itemSelectionChanged``
        （选中行没变），浮层就再也弹不出来了——多行时点别行碰巧能弹，
        才让这个毛病看起来"时好时坏"。选中行**变了**的那次点击不在这里
        重取（``itemSelectionChanged`` 已经发过图了，避免一次点击取两遍）。
        """
        if self._preview_pixmap is None and row == self._table.current_row():
            self._on_row_selected()

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
