"""框级 AI 动作就地确认卡片（批次 C）：草稿层 + 显式人工「应用」。

AI 产出只是提案：处理中显示 spinner 与取消钮；完成显示可编辑草稿，
「应用」才写回块数据（进全局撤销栈），「放弃」不动数据。锚定目标块
上方（与选中跟随工具栏同一套定位钳制）。

**确定性输入面**（2026-09-13 重构，设计 §5 的「代码预组装上下文」）：
- 切图预览：把送给视觉模型的同一张图（按行透视纠正的拼图）摆到人眼前，
  手写体这类模型靠不住的情形，人可以直接照着敲。
- 逐行接受：OCR 校正回复能与行对齐时，逐行「采纳/保留原值」，
  不必整块重掷，也不会被一处改坏带偏其余行。
- 上下文包：本次实际送出的原文/邻近块/生效标签指令/补充要求逐项列出，
  不再是黑箱。
- 补充要求：一句话交给模型重跑（用户最懂具体情景），失败的请求也可以
  直接人工填写后应用。
"""

import base64

from qtpy.QtCore import QByteArray, Qt, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigCheckBox, ConfigLineEdit, ConfigTextEdit

CARD_WIDTH = 280
EXPANDED_SIZE = (520, 430)
_WIDGET_SIZE_MAX = 16777215
# 切图预览上限：(宽, 高)，紧凑 / 展开两态
_PREVIEW_MAX = {False: (CARD_WIDTH - 16, 110), True: (EXPANDED_SIZE[0] - 40, 200)}
# 结果区（逐行行/上下文）滚动上限
_DETAILS_MAX_H = {False: 210, True: 300}


class _ClickableLabel(QLabel):
    """点了就发信号（切图预览用：点击放大）。"""

    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class BlockActionCard(QWidget):
    apply_clicked = Signal(str)
    cancelled = Signal()
    retry_requested = Signal(str)  # 带补充要求重跑

    def __init__(self, host: QWidget, canvas) -> None:
        super().__init__(host)
        self.setObjectName("BlockActionCard")
        # QSS 容器背景/边框：裸 QWidget 子类必须开此属性才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._canvas = canvas
        self._blkitem = None
        self._busy = False
        self._expanded = False
        self._preview_pixmap = None
        self._zoom_dialog = None
        self._zoom_label = None
        self._context_items = []
        self._line_texts = []
        self._per_line = False
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(4)
        self._title = QLabel("", self)
        self._title.setObjectName("RailDockTitle")
        expand_btn = QToolButton(self)
        expand_btn.setObjectName("RailDockCloseBtn")
        expand_btn.setText("↗")
        expand_btn.setFixedSize(20, 20)
        expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        expand_btn.setToolTip(self.tr("Expand / Collapse"))
        expand_btn.clicked.connect(self.toggle_form)
        close_btn = QToolButton(self)
        close_btn.setObjectName("RailDockCloseBtn")
        close_btn.setText("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.clicked.connect(self._on_cancel)
        header.addWidget(self._title, 1)
        header.addWidget(expand_btn)
        header.addWidget(close_btn)
        root.addLayout(header)
        self._expand_btn = expand_btn

        # 处理中：状态行 + 取消
        self._busy_row = QWidget(self)
        busy_layout = QHBoxLayout(self._busy_row)
        busy_layout.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("", self._busy_row)
        self._cancel_btn = QToolButton(self._busy_row)
        self._cancel_btn.setText(self.tr("Cancel"))
        self._cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel_btn.clicked.connect(self._on_cancel)
        busy_layout.addWidget(self._status, 1)
        busy_layout.addWidget(self._cancel_btn)
        root.addWidget(self._busy_row)

        # 结果：原文 / 上下文包 / 切图 / 逐行或整块草稿 / 补充要求 / 应用
        self._result_row = QWidget(self)
        result_layout = QVBoxLayout(self._result_row)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(3)
        self._src_label = QLabel("", self._result_row)
        self._src_label.setObjectName("BlockActionSrcLabel")
        self._src_label.setWordWrap(True)
        result_layout.addWidget(self._src_label)

        self._context_label = QLabel("", self._result_row)
        self._context_label.setObjectName("BlockActionContextLabel")
        self._context_label.setWordWrap(True)
        self._context_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._context_label.hide()
        result_layout.addWidget(self._context_label)

        self._preview_label = _ClickableLabel(self._result_row)
        self._preview_label.setObjectName("BlockActionPreview")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_label.setToolTip(self.tr("Click to enlarge"))
        self._preview_label.clicked.connect(self._open_zoom)
        self._preview_label.hide()
        result_layout.addWidget(self._preview_label)

        self._rows_scroll = QScrollArea(self._result_row)
        self._rows_scroll.setObjectName("BlockActionScroll")
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._rows_scroll.setMaximumHeight(_DETAILS_MAX_H[False])
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_scroll.setWidget(self._rows_host)
        self._rows_scroll.hide()
        result_layout.addWidget(self._rows_scroll)

        self._editor = ConfigTextEdit(self._result_row)
        self._editor.setFixedWidth(CARD_WIDTH - 16)
        self._editor.setMinimumHeight(52)
        # 大面板形态下由编辑器吸收垂直富余空间
        self._editor.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        result_layout.addWidget(self._editor)

        self._error_label = QLabel("", self._result_row)
        self._error_label.setObjectName("BlockActionContextLabel")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        result_layout.addWidget(self._error_label)

        self._footer = QWidget(self._result_row)
        footer_layout = QVBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(3)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(4)
        self._hint_edit = ConfigLineEdit("", self._footer)
        self._hint_edit.setPlaceholderText(self.tr("Extra requirement (optional)"))
        self._hint_edit.returnPressed.connect(self._on_retry)
        self._retry_btn = QToolButton(self._footer)
        self._retry_btn.setText(self.tr("Regenerate"))
        self._retry_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._retry_btn.setToolTip(
            self.tr("Ask the model again, this time with your requirement.")
        )
        self._retry_btn.clicked.connect(self._on_retry)
        hint_row.addWidget(self._hint_edit, 1)
        hint_row.addWidget(self._retry_btn)
        footer_layout.addLayout(hint_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._discard_btn = QToolButton(self._footer)
        self._discard_btn.setText(self.tr("Discard"))
        self._discard_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._discard_btn.clicked.connect(self._on_cancel)
        self._apply_btn = QToolButton(self._footer)
        self._apply_btn.setText(self.tr("Apply"))
        self._apply_btn.setObjectName("TagActionBtn")
        self._apply_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._discard_btn)
        btn_row.addWidget(self._apply_btn)
        footer_layout.addLayout(btn_row)
        result_layout.addWidget(self._footer)

        root.addWidget(self._result_row)

        self.setFixedWidth(CARD_WIDTH)
        self._result_row.hide()
        self.hide()

    # ── 状态机：busy → proposal / error ─────────────────────────

    def begin(
        self,
        action_name: str,
        blkitem,
        src_text: str,
        *,
        preview_b64: str = "",
        line_texts=None,
        context_items=None,
        per_line: bool = False,
    ) -> None:
        """进入处理中。载荷信息（切图/逐行原文/上下文包）由调用方给出。"""
        self._blkitem = blkitem
        self._busy = True
        self._title.setText(action_name)
        self._src_label.setText(src_text)
        self._line_texts = list(line_texts or [])
        self._per_line = bool(per_line) and bool(self._line_texts)
        self._set_preview(preview_b64)
        self._set_context(context_items)
        self._clear_rows()
        self._error_label.hide()
        self._status.setText(self.tr("Processing..."))
        self._status.show()
        self._apply_btn.show()
        self._editor.setPlainText("")
        # 处理中也把切图摆出来：手写体可以边等边读
        self._result_row.show()
        self._editor.hide()
        self._rows_scroll.hide()
        self._footer.hide()
        self._busy_row.show()
        self._place_near_block()
        self.show()
        self.raise_()

    def show_proposal(self, text: str, line_corrections=None) -> None:
        """草稿就绪：逐行模式给出可逐行取舍的行，否则给整块编辑器。"""
        self._busy = False
        self._busy_row.hide()
        self._result_row.show()
        self._error_label.hide()
        self._footer.show()
        if self._per_line and line_corrections:
            self._build_rows(line_corrections)
            self._rows_scroll.show()
            self._editor.hide()
        else:
            self._clear_rows()
            self._rows_scroll.hide()
            self._editor.setPlainText(text)
            self._editor.show()
            self._editor.setFocus()
        self._apply_preview_scale()
        self._place_near_block()
        self.raise_()

    def show_error(self, message: str) -> None:
        """失败态：仍留人工填写路径（读着切图自己敲，尤其手写体）。"""
        self._busy = False
        self._busy_row.hide()
        self._result_row.show()
        self._footer.show()
        self._apply_btn.show()
        self._error_label.setText(self.tr("Action failed:") + " " + message)
        self._error_label.show()
        if self._per_line:
            # 逐行原文预填成行，人工按行改正
            self._build_rows(list(self._line_texts))
            self._rows_scroll.show()
            self._editor.hide()
        else:
            self._clear_rows()
            self._rows_scroll.hide()
            self._editor.setPlainText("")
            self._editor.show()
        self._apply_preview_scale()
        self._place_near_block()
        self.raise_()

    def is_busy(self) -> bool:
        return self._busy

    def result_text(self) -> str:
        """当前草稿：逐行模式按「勾选=采纳，不勾=保留原值」拼装。"""
        if not self._rows:
            return self._editor.toPlainText().strip()
        parts = [
            edit.text().strip() if box.isChecked() else original
            for box, edit, original in self._rows
        ]
        return "\n".join(parts).strip()

    # ── 双形态：紧凑卡片 ↔ 大交互面板 ────────────────────────────

    def toggle_form(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._editor.setFixedWidth(EXPANDED_SIZE[0] - 32)
            self._editor.setMinimumHeight(160)
            self.setFixedSize(*EXPANDED_SIZE)
        else:
            self._editor.setFixedWidth(CARD_WIDTH - 16)
            self._editor.setMinimumHeight(52)
            self.setMaximumSize(_WIDGET_SIZE_MAX, _WIDGET_SIZE_MAX)
            self.setFixedWidth(CARD_WIDTH)
        self._expand_btn.setText("↙" if self._expanded else "↗")
        self._rows_scroll.setMaximumHeight(_DETAILS_MAX_H[self._expanded])
        self._context_label.setVisible(
            self._expanded and bool(self._context_items)
        )
        self._apply_preview_scale()
        self._place_near_block()
        self.raise_()

    def close_card(self) -> None:
        self._busy = False
        self._result_row.hide()
        self._busy_row.hide()
        self._apply_btn.show()
        self._status.show()
        self._clear_rows()
        self._rows_scroll.hide()
        self._editor.show()
        self._context_label.hide()
        self._error_label.hide()
        if self._zoom_dialog is not None:
            self._zoom_dialog.close()
        self.hide()

    # ── 切图预览 ────────────────────────────────────────────────

    def _set_preview(self, preview_b64: str) -> None:
        self._preview_pixmap = None
        if preview_b64:
            try:
                data = QByteArray(base64.b64decode(preview_b64))
                pixmap = QPixmap()
                if pixmap.loadFromData(data, "JPEG"):
                    self._preview_pixmap = pixmap
            except Exception:
                self._preview_pixmap = None
        if self._preview_pixmap is None:
            self._preview_label.hide()
        else:
            self._preview_label.show()
            self._apply_preview_scale()

    def _apply_preview_scale(self) -> None:
        if self._preview_pixmap is None:
            return
        max_w, max_h = _PREVIEW_MAX[self._expanded]
        self._preview_label.setPixmap(
            self._preview_pixmap.scaled(
                max_w,
                max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _open_zoom(self) -> None:
        """原生尺寸查看（滚动），手写体对着放大图敲字用。"""
        if self._preview_pixmap is None:
            return
        if self._zoom_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Preview"))
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(6, 6, 6, 6)
            area = QScrollArea(dialog)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidgetResizable(False)
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            area.setWidget(label)
            layout.addWidget(area)
            self._zoom_dialog = dialog
            self._zoom_label = label
        self._zoom_label.setPixmap(self._preview_pixmap)
        self._zoom_label.adjustSize()
        pixmap = self._preview_pixmap
        self._zoom_dialog.resize(
            min(max(pixmap.width() + 40, 360), 1100),
            min(max(pixmap.height() + 60, 240), 820),
        )
        self._zoom_dialog.show()
        self._zoom_dialog.raise_()
        self._zoom_dialog.activateWindow()

    # ── 上下文包（本次实际送出的东西）────────────────────────────

    def _set_context(self, context_items=None) -> None:
        self._context_items = [
            (str(label), str(value))
            for label, value in (context_items or [])
            if str(value).strip()
        ]
        self._context_label.setText(
            "\n\n".join(f"{label}\n{value}" for label, value in self._context_items)
        )
        self._context_label.setVisible(
            self._expanded and bool(self._context_items)
        )

    # ── 逐行行 ──────────────────────────────────────────────────

    def _build_rows(self, corrections) -> None:
        self._clear_rows()
        for i, original in enumerate(self._line_texts):
            row = QWidget(self._rows_host)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            box = ConfigCheckBox("", row)
            box.setChecked(True)
            box.setToolTip(self.tr("Uncheck to keep the original line"))
            edit = ConfigLineEdit("", row)
            edit.setText(
                corrections[i] if i < len(corrections) else original
            )
            edit.setToolTip(self.tr("Original") + ": " + (original or ""))
            row_layout.addWidget(box)
            row_layout.addWidget(edit, 1)
            self._rows_layout.addWidget(row)
            self._rows.append((box, edit, original))

    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先隐再删：可见控件 setParent(None) 会短暂变成顶层窗口闪一下
                widget.hide()
                widget.deleteLater()
        self._rows = []

    # ── 动作 ────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        text = self.result_text()
        if text:
            self.apply_clicked.emit(text)

    def _on_retry(self) -> None:
        hint = self._hint_edit.text().strip()
        self.retry_requested.emit(hint)

    def _on_cancel(self) -> None:
        # 卡片自行关闭（立即反馈），mainwindow 经信号停掉在途请求
        self.cancelled.emit()
        self.close_card()

    # ── 定位（目标块上方，越界翻转下方，钳制宿主；同 TagToolbar）──

    def _place_near_block(self) -> None:
        host = self.parentWidget()
        if host is None or self._blkitem is None:
            return
        rect = self._blkitem.sceneBoundingRect()
        gv = self._canvas.gv
        tl = host.mapFromGlobal(gv.mapToGlobal(gv.mapFromScene(rect.topLeft())))
        br = host.mapFromGlobal(
            gv.mapToGlobal(gv.mapFromScene(rect.bottomRight()))
        )
        self.adjustSize()
        y = tl.y() - self.height() - 6
        if y < 0:
            y = br.y() + 6
        x = max(0, min(tl.x(), host.width() - self.width()))
        y = max(0, min(y, host.height() - self.height()))
        self.move(x, y)
