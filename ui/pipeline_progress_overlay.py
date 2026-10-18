"""画布中央的集中任务视图：整批进度、当前页阶段、活动记录与结果。

表现层不访问 runner 或项目。阶段列表只表达当前页，整批进度与写回数分开；
没有逐块回调的阶段只显示忙碌态。所有状态使用文字，不使用 emoji。
面板正文可滚动，停止/关闭操作固定在底部，小窗口和高 DPI 不裁切内容。
"""
from __future__ import annotations

from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtGui import QTextCursor
from qtpy.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.custom_widget import ConfigTextEdit

STAGE_IDS = ("detect", "ocr", "translate", "inpaint", "render")


def _label(text, name, parent):
    label = QLabel(text, parent)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class StageProgressCard(QFrame):
    """当前页阶段列表中的紧凑行；详细活动放在右侧，不嵌套卡片。"""

    def __init__(self, stage_id, title, parent=None):
        super().__init__(parent)
        self.stage_id = stage_id
        self.setObjectName("PipelineStageCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self.title = _label(title, "PipelineStageTitle", self)
        self.status = _label("", "PipelineStageStatus", self)
        self.bar = QProgressBar(self)
        self.bar.setObjectName("PipelineStageBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        layout.addWidget(self.title)
        layout.addWidget(self.status)
        layout.addWidget(self.bar)
        self.set_waiting()

    def _set_state(self, state, text):
        self.status.setText(text)
        if self.property("state") != state:
            self.setProperty("state", state)
            for widget in (self, self.title, self.status):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

    def set_waiting(self, message=""):
        self._set_state("waiting", message or self.tr("Waiting"))
        self.bar.hide()

    def set_running(self, message="", progress=None, total=None):
        self._set_state("running", message or self.tr("Running"))
        self.bar.show()
        if progress is not None and total:
            self.bar.setRange(0, total)
            self.bar.setValue(min(progress, total))
        else:
            self.bar.setRange(0, 0)

    def set_completed(self, message=""):
        self._set_state("done", message or self.tr("Done"))
        self.bar.hide()

    def set_failed(self, message=""):
        self._set_state("error", message or self.tr("Failed"))
        self.bar.hide()


class PipelineProgressPanel(QFrame):
    """居中的任务面板；正文滚动、底部操作固定，所有外部文本按纯文本显示。"""

    stop_graceful_requested = Signal()
    stop_force_requested = Signal()
    PANEL_WIDTH = 760

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PipelineProgressPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._stopping = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("PipelineProgressScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("PipelineProgressBody")
        self.scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        self.title = _label(self.tr("Preparing task"), "PipelineProgressTitle", body)
        layout.addWidget(self.title)
        self.context = _label("", "PipelineProgressContext", body)
        layout.addWidget(self.context)

        progress_row = QHBoxLayout()
        self.progress_label = _label(self.tr("Waiting to start"), "PipelineProgressCount", body)
        self.elapsed = _label("", "PipelineProgressElapsed", body)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.progress_label, 2)
        progress_row.addWidget(self.elapsed, 1)
        layout.addLayout(progress_row)
        self.progress = QProgressBar(body)
        self.progress.setObjectName("PipelineOverallBar")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.current = QWidget(body)
        self.current.setObjectName("PipelineCurrentPage")
        columns = QHBoxLayout(self.current)
        columns.setContentsMargins(0, 6, 0, 0)
        columns.setSpacing(20)
        self.stage_column = QWidget(self.current)
        self.stage_column.setObjectName("PipelineStageColumn")
        self.stage_column.setFixedWidth(152)
        stages = QVBoxLayout(self.stage_column)
        stages.setContentsMargins(0, 0, 12, 0)
        stages.setSpacing(6)
        titles = {
            "detect": self.tr("Text detection"), "ocr": self.tr("OCR"),
            "translate": self.tr("Translation"), "inpaint": self.tr("Image repair"),
            "render": self.tr("Save results"),
        }
        self.stage_cards = {}
        for stage_id in STAGE_IDS:
            card = StageProgressCard(stage_id, titles[stage_id], self.stage_column)
            self.stage_cards[stage_id] = card
            stages.addWidget(card)
        stages.addStretch()
        columns.addWidget(self.stage_column)
        content = QVBoxLayout()
        content.setSpacing(8)
        self.page_label = _label(self.tr("Preparing page data..."), "PipelineProgressPage", self.current)
        content.addWidget(self.page_label)
        self.current_status = _label("", "PipelineCurrentStatus", self.current)
        content.addWidget(self.current_status)
        self.activity_age = _label("", "PipelineProgressContext", self.current)
        content.addWidget(self.activity_age)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        self.page_facts = {}
        for row, (key, title) in enumerate((
            ("target", self.tr("Page target")),
            ("round", self.tr("Latest response")),
            ("accepted", self.tr("Translations accepted")),
            ("tools", self.tr("Requested tools")),
        )):
            caption = _label(title, "PipelineFactCaption", self.current)
            caption.setMinimumWidth(88)
            caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            value = _label("—", "PipelineFactValue", self.current)
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
            self.page_facts[key] = (caption, value)
        grid.setColumnMinimumWidth(0, 100)
        grid.setColumnStretch(1, 1)
        content.addLayout(grid)
        content.addStretch()
        columns.addLayout(content, 1)
        layout.addWidget(self.current)

        self.summary = _label("", "PipelineProgressSummary", body)
        layout.addWidget(self.summary)
        self.usage = _label("", "PipelineProgressContext", body)
        self.usage.hide()
        layout.addWidget(self.usage)
        layout.addWidget(_label(self.tr("Recent activity"), "PipelineSectionTitle", body))
        self.activity_log = ConfigTextEdit(body)
        self.activity_log.setObjectName("PipelineActivityLog")
        self.activity_log.setReadOnly(True)
        self.activity_log.setMinimumHeight(112)
        self.activity_log.setMaximumHeight(148)
        self.activity_log.document().setMaximumBlockCount(100)
        layout.addWidget(self.activity_log, 1)
        self.notice = _label("", "PipelineProgressNotice", body)
        self.notice.hide()
        # 停止/错误后果紧跟整批进度，不能埋在活动日志之后。
        layout.insertWidget(4, self.notice)
        root.addWidget(self.scroll, 1)

        footer = QWidget(self)
        footer.setObjectName("PipelineProgressFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(22, 12, 22, 12)
        row.setSpacing(10)
        self.footer_hint = _label(self.tr("Saved pages are kept when stopping."), "PipelineProgressContext", footer)
        row.addWidget(self.footer_hint, 1)
        self.stop_force = QPushButton(self.tr("End program"), footer)
        self.stop_force.setObjectName("PipelineStopForce")
        self.stop_force.clicked.connect(self.stop_force_requested.emit)
        self.stop_force.hide()
        row.addWidget(self.stop_force)
        self.stop_graceful = QPushButton(self.tr("Stop task"), footer)
        self.stop_graceful.setObjectName("PipelineStopGraceful")
        self.stop_graceful.clicked.connect(self._on_graceful_clicked)
        row.addWidget(self.stop_graceful)
        root.addWidget(footer)

    def set_stages(self, enabled):
        for stage_id, card in self.stage_cards.items():
            card.setVisible(stage_id in enabled)
        for key, widgets in self.page_facts.items():
            visible = "translate" in enabled or (key == "target" and "ocr" in enabled)
            for widget in widgets:
                widget.setVisible(visible)

    def reset_page(self):
        for card in self.stage_cards.values():
            card.set_waiting()
        for _, value in self.page_facts.values():
            value.setText("—")
        self.current_status.setText(self.tr("Preparing page data..."))

    def set_fact(self, key, value):
        self.page_facts[key][1].setText(str(value))

    def update_current_page(self, number, total, name):
        page = self.tr("Page %1 of %2").replace("%1", str(number)).replace("%2", str(total))
        self.page_label.setText(page + " · " + name if name else page)

    def update_progress(self, processed, total, elapsed):
        self.progress_label.setText(self.tr("Processed %1 / %2 pages").replace("%1", str(processed)).replace("%2", str(total)))
        self.elapsed.setText(self.tr("Elapsed %1").replace("%1", elapsed))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(processed, total))

    def update_summary(self, text):
        self.summary.setText(text)

    def append_activity(self, text):
        bar = self.activity_log.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 2
        cursor = QTextCursor(self.activity_log.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.activity_log.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(text)
        if follow:
            bar.setValue(bar.maximum())

    def show_result(self, title, message):
        self.reset_warning()
        self.title.setText(title)
        self.current.hide()
        self.activity_log.setMaximumHeight(16777215)
        self.stop_graceful.hide()
        self.footer_hint.setText(self.tr("Task ended. Review the results before continuing."))
        self.stop_force.show()
        self.set_notice(message)

    def enter_stopping_state(self, elapsed=0):
        self._stopping = True
        self.title.setText(self.tr("Stopping"))
        self.stop_graceful.setEnabled(False)
        self.stop_graceful.setText(self.tr("Stopping..."))
        self.stop_force.show()
        self.set_notice(self.tr("Waiting for the current call to return (%1 s). Unsaved results will be discarded; saved pages are kept.").replace("%1", str(int(elapsed))))

    def set_notice(self, text):
        self.notice.setText(text or "")
        self.notice.setVisible(bool(text))

    def enter_unresponsive_state(self, elapsed):
        self.setProperty("state", "warning")
        self.title.setText(self.tr("No response"))
        self.stop_force.show()
        self.set_notice(self.tr("The current call has not returned after %1 s. You can keep waiting or end the program. Ending the program discards unsaved results.").replace("%1", str(int(elapsed))))
        self._repolish()

    def _repolish(self):
        for widget in (self, self.title, self.notice):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def reset_warning(self):
        self.setProperty("state", None)
        self._repolish()

    def set_force_enabled(self, enabled):
        self.stop_force.setEnabled(enabled)

    def relayout(self):
        host = self.parentWidget()
        if host is None:
            return
        width = min(self.PANEL_WIDTH, max(1, host.width() - 32))
        height = min(640, max(1, host.height() - 32))
        self.setFixedSize(width, height)
        self.stage_column.setFixedWidth(120 if width < 600 else 152)
        self.move((host.width() - width) // 2, (host.height() - height) // 2)

    def _on_graceful_clicked(self):
        if not self._stopping:
            self.stop_graceful_requested.emit()

    def focusNextPrevChild(self, next):
        # 日志可选择复制，但 Tab/Shift+Tab 只能在任务面板中循环。
        controls = [widget for widget in (self.activity_log, self.stop_force, self.stop_graceful)
                    if widget.isVisible() and widget.isEnabled()]
        if controls:
            focused = QApplication.focusWidget()
            index = controls.index(focused) if focused in controls else (-1 if next else 0)
            controls[(index + (1 if next else -1)) % len(controls)].setFocus(Qt.FocusReason.TabFocusReason)
        return True


class PipelineProgressOverlay(QWidget):
    """铺满宿主的半透明遮罩，居中放一张进度卡片。"""

    stop_graceful_requested = Signal()
    stop_force_requested = Signal()

    #: 遮罩浓淡：要明确压暗底下的画布（否则用户以为还能接着点），但又得
    #: 看得见底下在处理哪一页
    SCRIM_ALPHA = 0.62

    def __init__(self, host: QWidget, parent=None):
        super().__init__(host if parent is None else parent)
        self.setObjectName("PipelineProgressOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = PipelineProgressPanel(self)
        layout.addWidget(self.panel, 0, Qt.AlignmentFlag.AlignCenter)

        self.panel.stop_graceful_requested.connect(self.stop_graceful_requested.emit)
        self.panel.stop_force_requested.connect(self.stop_force_requested.emit)

        self._host = host
        self.setGeometry(host.rect())
        host.installEventFilter(self)

        # 遮罩必须吃掉鼠标与滚轮，不能穿透到底下的画布。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── 几何 ────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.panel.relayout()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return super().eventFilter(obj, event)

    # ── 输入拦截 ────────────────────────────────────────────────────

    def event(self, event):
        """吞掉 Tab／方向键等焦点与快捷键事件，别让它们跑到画布或侧栏去。

        Esc 刻意**不**当停止键：停止有后果（丢弃在途结果），绑定到随手一按
        的键太容易误触；要停就去按按钮。
        """
        if event.type() in (
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
            QEvent.Type.Shortcut,
            QEvent.Type.ShortcutOverride,
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    # ── 生命周期 ────────────────────────────────────────────────────

    def close_overlay(self):
        """收尾：摘过滤器、还键盘、删自己。"""
        self.releaseKeyboard()
        try:
            self._host.removeEventFilter(self)
        except (RuntimeError, AttributeError):
            pass  # 宿主可能已经先走一步（关窗路径）
        self.hide()
        self.deleteLater()
