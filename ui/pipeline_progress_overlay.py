"""管线运行进度覆盖层：盖住画布区的模态进度面板。

**为什么不是底部状态条**：旧版把进度缩成底部一行字，运行中还能接着编辑，
冲突、脏页、跳转补渲染这一串复杂度全是为了兼容"边跑边改"。改成覆盖层后
交互被屏蔽，那一整套复杂度就没有存在必要了（见交接文档与设计文档）。

**为什么不是独立窗口**：`ui/overlay_modal.py::OverlayModal` 那条路要独立
HWND、置顶关系与生命周期都得自己兜；进度面板全程存在且要紧跟画布，做成
in-window child（同 `ui/workbench_preview.py::WorkbenchPreviewPanel` /
`ui/tag_toolbar.py::TagToolbar`）更省事：跟着宿主走、随宿主尺寸变化。

**三层结构**：

- ``PipelineProgressOverlay``：铺满宿主的半透明遮罩。自己画底、吃掉鼠标事件
  （否则点击会穿透到底下的画布），居中摆放面板。
- ``PipelineProgressPanel``：圆角卡片。标题 + 页名 + 各阶段卡片 + 汇总 +
  两个停止按钮。只发信号，不认识 ``PipelineTasks``。
- ``StageProgressCard``：单阶段一行（图标 + 阶段名 + 状态 + 进度条），
  翻译那张多一个展开箭头与详情区。

**展开口径（刻意的裁剪）**：只有"翻译"可展开——它每轮都有工具调用与用量，
是真有细节可看；检测／OCR／修复／渲染只有一句"做了多少、花了多久"，
给它们也配箭头只是徒增点击。展开后按设计文档的规则**不显示思维链、不显示
译文正文**，只给轮次、工具名、用量与模式，并明确写出省略了什么。

**两种停止**（`AGENTS.md` 的用户总纲：人工在环、后果要讲清楚）：

- 优雅停止＝跑完当前页就停。已保存的页保留，在途结果丢弃。
- 强制停止＝后台调用卡住时的兜底。**不调用 ``QThread.terminate()``**
  （线程可能正握着 GIL 或在 CUDA 调用里，强杀会污染进程状态），而是
  先尽力落盘再走 ``os._exit``：把还在写的图等一小会儿，然后直接结束进程。

第三态（疑似无响应）由控制器在宽限期后触发：面板转警示色并给出"仍在等待"
的说明，**不自动弹窗**——用户可能已经去干别的了，弹个模态框把焦点抢回来
只会更烦；要放弃由用户自己按。
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# 阶段 id → 界面名（顺序即显示顺序，与管线实际执行顺序一致）
STAGE_IDS = ("detect", "ocr", "translate", "inpaint", "render")

#: 状态图标。用字符而不是 SVG：本仓 icons/ 是单色主题图标，进度图标要带
#: 语义色（等待灰／进行中主题色／完成绿），字符加 QSS 染色更直接。
ICON_WAITING = "⏸"
ICON_RUNNING = "⏳"
ICON_DONE = "✓"
ICON_SKIPPED = "⊘"
ICON_ERROR = "!"


class StageProgressCard(QFrame):
    """单阶段进度行：图标 + 阶段名 + 状态文字（+ 进度条 + 可选详情）。"""

    def __init__(self, stage_id: str, title: str, parent=None):
        super().__init__(parent)
        self.stage_id = stage_id
        self.setObjectName("PipelineStageCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 裸 QWidget 子类默认跳过 QSS 背景绘制，须显式开（仓库既有做法）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._expanded = False
        self._details = ""
        self._done = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(4)

        # ── 头部：图标 + 阶段名 + 状态 + 展开箭头 ──────────────────
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.icon = QLabel(ICON_WAITING, self)
        self.icon.setObjectName("PipelineStageIcon")
        self.icon.setFixedWidth(16)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.icon)

        self.title = QLabel(title, self)
        self.title.setObjectName("PipelineStageTitle")
        head.addWidget(self.title)

        self.status = QLabel(self.tr("Waiting"), self)
        self.status.setObjectName("PipelineStageStatus")
        self.status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        # "Ignored" 交给布局压扁，省得长状态文字把整块面板顶宽
        self.status.setMinimumWidth(60)
        head.addWidget(self.status, 1)

        self.expand_btn = QToolButton(self)
        self.expand_btn.setObjectName("PipelineStageExpand")
        self.expand_btn.setCheckable(True)
        self.expand_btn.setText("▼")
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.clicked.connect(self._toggle_expand)
        self.expand_btn.hide()  # 只有喂过详情的那张卡片才亮出来
        head.addWidget(self.expand_btn)
        layout.addLayout(head)

        # ── 进度条（紧跟状态行，展开详情时不被推到下面）──────────────
        self.bar = QProgressBar(self)
        self.bar.setObjectName("PipelineStageBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.hide()
        layout.addWidget(self.bar)

        # ── 摘要行（折叠时显示，展开后让位给详情）──────────────────
        self.summary = QLabel(self)
        self.summary.setObjectName("PipelineStageSummary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.summary.hide()
        layout.addWidget(self.summary)

        # ── 详情区（展开时显示）────────────────────────────────────
        self.details = QLabel(self)
        self.details.setObjectName("PipelineStageDetails")
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setWordWrap(True)
        self.details.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.details.hide()
        layout.addWidget(self.details)

    # ── 状态切换 ────────────────────────────────────────────────────

    def set_waiting(self, message: str = ""):
        """等待中（含"还没轮到"与"已跳过"两种）。

        ``⊘`` 前缀由调用方给（跳过与其他等待的语义不同），这里只画状态。
        """
        self._set_state(ICON_WAITING, "waiting", message or self.tr("Waiting"))

    def set_running(self, message: str = "", progress: int = None, total: int = None):
        """进行中。``progress``/``total`` 都给才画进度条，否则只转圈。"""
        self._set_state(ICON_RUNNING, "running", message)
        if progress is not None and total:
            self.bar.show()
            self.bar.setRange(0, max(1, total))
            self.bar.setValue(min(progress, total))
        else:
            # 没有确切进度时用忙碌动画（0/0 是 Qt 的忙碌态约定）
            self.bar.show()
            self.bar.setRange(0, 0)

    def set_completed(self, message: str = ""):
        self._set_state(ICON_DONE, "done", message or self.tr("Done"))
        self._done = True
        self.bar.hide()

    def set_failed(self, message: str = ""):
        self._set_state(ICON_ERROR, "error", message or self.tr("Failed"))
        self.bar.hide()

    def _set_state(self, icon: str, state: str, message: str):
        self.icon.setText(icon)
        self.status.setText(message)
        # 状态存进动态属性，QSS 用 [state="..."] 选择器染色。改属性后必须
        # unpolish/polish，否则选择器不会重算（Qt 的老规矩）。
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)

    # ── 摘要与详情 ──────────────────────────────────────────────────

    def set_summary(self, text: str):
        """折叠态的一行摘要（如"模型第 2 轮 · 调用 check_next_block"）。"""
        self.summary.setText(text or "")
        self.summary.setVisible(bool(text) and not self._expanded)

    def set_details(self, html: str):
        """展开态的详情（富文本）。喂过内容才亮出展开箭头。"""
        self._details = html or ""
        self.details.setText(self._details)
        self.expand_btn.setVisible(bool(self._details))
        if not self._details and self._expanded:
            self._toggle_expand(False)

    def is_expanded(self) -> bool:
        return self._expanded

    def _toggle_expand(self, checked: bool = None):
        self._expanded = bool(checked) if checked is not None else not self._expanded
        self.expand_btn.setChecked(self._expanded)
        self.expand_btn.setText("▲" if self._expanded else "▼")
        self.summary.setVisible(bool(self.summary.text()) and not self._expanded)
        self.details.setVisible(self._expanded and bool(self._details))
        # 面板高度跟着变，交给上层重排
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "relayout"):
            parent.relayout()


class PipelineProgressPanel(QFrame):
    """覆盖层里的进度卡片：标题 + 阶段卡片 + 汇总 + 停止按钮。"""

    stop_graceful_requested = Signal()
    stop_force_requested = Signal()

    PANEL_WIDTH = 520
    #: 展开详情后最多占宿主高度的比例——再高就该滚动了，但进度面板不做滚动
    #: （内容量可控：五个阶段各一行 + 一段详情）
    MAX_HOST_RATIO = 0.8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PipelineProgressPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(self.PANEL_WIDTH)

        self._stopping = False
        self._force_enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # ── 标题 + 页名 ────────────────────────────────────────────
        self.title = QLabel(self.tr("Running"), self)
        self.title.setObjectName("PipelineProgressTitle")
        layout.addWidget(self.title)

        self.page_label = QLabel(self)
        self.page_label.setObjectName("PipelineProgressPage")
        self.page_label.setTextFormat(Qt.TextFormat.PlainText)
        self.page_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.page_label)

        # ── 阶段卡片 ────────────────────────────────────────────────
        self.stage_cards = {}
        titles = {
            "detect": self.tr("Text detection"),
            "ocr": self.tr("OCR"),
            "translate": self.tr("Translation"),
            "inpaint": self.tr("Image repair"),
            "render": self.tr("Render result"),
        }
        for stage_id in STAGE_IDS:
            card = StageProgressCard(stage_id, titles[stage_id], self)
            self.stage_cards[stage_id] = card
            layout.addWidget(card)

        # ── 分隔线 ──────────────────────────────────────────────────
        divider = QFrame(self)
        divider.setObjectName("PipelineProgressDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # ── 汇总 ────────────────────────────────────────────────────
        self.summary = QLabel(self)
        self.summary.setObjectName("PipelineProgressSummary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.summary)

        # ── 停止提示（宽限期后出现的一句话说明）────────────────────
        self.notice = QLabel(self)
        self.notice.setObjectName("PipelineProgressNotice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)

        # ── 按钮行 ──────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.stop_graceful = QPushButton(self.tr("Stop"), self)
        self.stop_graceful.setObjectName("PipelineStopGraceful")
        self.stop_graceful.clicked.connect(self._on_graceful_clicked)
        row.addWidget(self.stop_graceful)

        self.stop_force = QPushButton(self.tr("Force stop"), self)
        self.stop_force.setObjectName("PipelineStopForce")
        self.stop_force.clicked.connect(self.stop_force_requested.emit)
        row.addWidget(self.stop_force)
        layout.addLayout(row)

        self.relayout()

    # ── 对外接口 ────────────────────────────────────────────────────

    def set_stages(self, enabled: set):
        """只显示本次真正会跑的阶段，其余整行收掉（读起来少四行噪音）。"""
        for stage_id, card in self.stage_cards.items():
            card.setVisible(stage_id in enabled)

    def update_current_page(self, number: int, total: int, name: str):
        page = self.tr("Page %1 of %2").replace("%1", str(number)).replace("%2", str(total))
        self.page_label.setText(name + "  ·  " + page if name else page)

    def update_summary(self, text: str):
        self.summary.setText(text)

    def enter_stopping_state(self, elapsed: int = 0):
        """进入"正在停止"：标题改口径，优雅停止钮禁用，强制停止钮留着。"""
        self._stopping = True
        self.title.setText(self.tr("Stopping"))
        self.stop_graceful.setEnabled(False)
        self.stop_graceful.setText(self.tr("Stopping..."))
        self.set_notice(
            self.tr("Waiting for the current request to return (%1 s). "
                    "The page in flight is discarded; saved pages are kept.")
            .replace("%1", str(int(elapsed)))
        )

    def set_notice(self, text: str):
        self.notice.setText(text or "")
        self.notice.setVisible(bool(text))
        self.relayout()

    def enter_unresponsive_state(self, elapsed: int):
        """超过宽限期：转警示口径。

        刻意**不自动弹确认框**：用户可能已经离开，抢焦点只会更烦。改为面板
        自身变色 + 明确提示，要放弃由用户按"强制停止"。
        """
        self.setProperty("state", "warning")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title.setText(self.tr("No response"))
        self.notice.setProperty("state", "warning")
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self)
        self.set_notice(
            self.tr("Waited %1 s and the background call has not returned. The "
                    "engine may be stuck in a model or network call. Force stop "
                    "ends the program now.").replace("%1", str(int(elapsed)))
        )

    def reset_warning(self):
        self.setProperty("state", None)
        self.style().unpolish(self)
        self.style().polish(self)
        self.notice.setProperty("state", None)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self)

    def set_force_enabled(self, enabled: bool):
        self._force_enabled = enabled
        self.stop_force.setEnabled(enabled)

    def relayout(self):
        """按内容重算自身高度并把面板居中（展开/折叠、提示增减后调用）。"""
        self.adjustSize()
        host = self.parentWidget()
        if host is None:
            return
        height = min(self.sizeHint().height(), int(host.height() * self.MAX_HOST_RATIO))
        self.resize(self.PANEL_WIDTH, max(1, height))
        self.move(
            max(0, (host.width() - self.width()) // 2),
            max(0, (host.height() - self.height()) // 2),
        )

    def _on_graceful_clicked(self):
        if not self._stopping:
            self.stop_graceful_requested.emit()


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
