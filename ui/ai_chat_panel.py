"""
AiChatPanel — bottom-slide chat panel for the AI assistant.

Standard Qt widgets (no custom QPainter).  Connects to AiController
signals for streaming conversation display.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from qtpy.QtCore import (
    Qt,
    Signal,
)
from qtpy.QtGui import QIntValidator, QKeyEvent, QTextCursor
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QTextBrowser,
    QMenu,
)
from .ai_chat_model import ChangeItem
from .overlay_slide import OverlaySlider
from utils.config import pcfg


# ── Custom QPlainTextEdit that sends on Enter, newline on Shift+Enter ────

class _ChatInputEdit(QPlainTextEdit):
    """Text edit that captures Enter to send, Shift+Enter for newline."""

    send_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
            return
        super().keyPressEvent(event)


# ── Chat bubble browser (suppresses internal scrolling) ──────────────────

class _ChatBubbleBrowser(QTextBrowser):
    """QTextBrowser that suppresses internal scrolling so the parent QScrollArea handles all scroll interaction."""

    def wheelEvent(self, event):
        event.ignore()

    def scrollContentsBy(self, dx: int, dy: int):
        pass  # no-op: prevent selection-drag and other internal scrolling


# ── AiChatPanel ──────────────────────────────────────────────────────────

class AiChatPanel(QWidget):
    """Left-side slide-in chat panel — title bar, message list, input bar."""

    # Signals for MainWindow to connect to AiController
    send_message = Signal(str)
    stop_requested = Signal()
    clear_requested = Signal()
    apply_changes_requested = Signal(list)  # list[ChangeItem] with accepted=True

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("AiChatPanel")

        # State
        self._streaming = False
        self._streaming_text = ""
        self._streaming_browser: Optional[QTextBrowser] = None
        self._streaming_bubble_widget: Optional[QWidget] = None
        self._controller: Any = None
        self._settings_widget: Optional[QWidget] = None
        self._settingsSlide: Optional[OverlaySlider] = None
        self._word_wrap = True

        # Start hidden — OverlaySlider manages position; fixed width for bubble sizing
        self.setVisible(False)
        self.setFixedWidth(480)

        self._build_ui()
        self._build_welcome_card()

    # ── Build ──────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Divider ──────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(2)
        layout.addWidget(divider)

        # ── Title bar ────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setObjectName("AITitleBar")
        title_bar.setFixedHeight(36)
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(8, 0, 4, 0)
        tl.setSpacing(6)

        title_label = QLabel(self.tr("AI Chat"))
        title_label.setObjectName("AITitle")

        self._status_dot = QLabel()
        self._status_dot.setObjectName("AIStatusBadge")
        self._status_dot.setText("●")
        self._status_dot.setFixedWidth(16)

        self._status_text = QLabel(self.tr("Ready"))
        self._status_text.setObjectName("AIStatusBadge")

        self._token_label = QLabel("~0 tok")
        self._token_label.setObjectName("AITokenLabel")

        # Clear button (always visible in title bar)
        self._clear_btn = QPushButton("✕")
        self._clear_btn.setObjectName("AIClearBtn")
        self._clear_btn.setFixedWidth(32)
        self._clear_btn.clicked.connect(self._on_clear)

        tl.addWidget(title_label)
        tl.addWidget(self._status_dot)
        tl.addWidget(self._status_text)
        tl.addStretch()
        tl.addWidget(self._token_label)

        # Mode selector (Agent / Chat)
        self._title_mode = QComboBox()
        self._title_mode.setObjectName("AITitleMode")
        # Keep these in English — mode names are universal UI terms
        self._title_mode.addItems(["Auto", "Agent", "Chat"])
        self._title_mode.setFixedWidth(90)
        self._title_mode.currentIndexChanged.connect(self._on_title_mode_changed)
        tl.addWidget(self._title_mode)

        # Clear button
        tl.addWidget(self._clear_btn)

        # Settings button
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setObjectName("AISettingsBtn")
        self._settings_btn.setFixedWidth(28)
        self._settings_btn.setCheckable(True)
        self._settings_btn.toggled.connect(self._toggle_settings)
        tl.addWidget(self._settings_btn)

        layout.addWidget(title_bar)

        # ── Message scroll area ──────────────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("AIChatArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(8, 8, 8, 8)
        self._msg_layout.setSpacing(8)
        self._msg_layout.addStretch(1)  # pushes bubbles to top

        self._scroll_area.setWidget(self._msg_container)
        layout.addWidget(self._scroll_area, 1)

        # ── Welcome card placeholder ─────────────────────────
        self._welcome_card = QWidget()
        self._welcome_card.setObjectName("AIWelcomeCard")

        # ── Input bar ────────────────────────────────────────
        self._input_bar = QWidget()
        self._input_bar.setObjectName("AIInputBar")
        self._input_bar.setMinimumHeight(56)
        il = QHBoxLayout(self._input_bar)
        il.setContentsMargins(8, 6, 8, 6)
        il.setSpacing(6)

        self._input_edit = _ChatInputEdit()
        self._input_edit.setObjectName("AIInput")
        self._input_edit.setPlaceholderText(
            self.tr("Ask AI... (Enter to send, Shift+Enter for newline)")
        )
        self._input_edit.setMinimumHeight(80)
        self._input_edit.setMaximumHeight(200)
        self._input_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._input_edit.document().documentLayout().documentSizeChanged.connect(
            self._adjust_input_height
        )
        self._input_edit.send_requested.connect(self._on_send)
        self._input_edit.textChanged.connect(self._on_input_changed)

        self._send_btn = QPushButton(self.tr("Send"))
        self._send_btn.setObjectName("AISendBtn")
        self._send_btn.setFixedSize(60, 36)
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)

        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setObjectName("AIStopBtn")
        self._stop_btn.setFixedSize(60, 36)
        self._stop_btn.setVisible(False)
        self._stop_btn.clicked.connect(self._on_stop)

        il.addWidget(self._input_edit, 1)
        il.addWidget(self._send_btn)
        il.addWidget(self._stop_btn)

        layout.addWidget(self._input_bar)

    def _chip_clicked(self, text: str):
        """Fill the input edit with a suggestion chip text."""
        self._input_edit.setPlainText(text)
        self._input_edit.setFocus()

    def _build_welcome_card(self):
        """Build the welcome card with suggestion chips (#AIWelcomeCard)."""
        card = self._welcome_card
        card.setVisible(False)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 24, 16, 16)
        cl.setSpacing(8)

        title = QLabel(self.tr("AI Assistant"))
        title.setObjectName("AIWelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(title)

        subtitle = QLabel(
            self.tr("Modify text blocks, fonts, and styles through natural language.")
        )
        subtitle.setObjectName("AIWelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        cl.addWidget(subtitle)

        cl.addSpacing(12)

        chips = [
            self.tr("List all pages"),
            self.tr("Search for 'hello'"),
            self.tr("Make font bold"),
            self.tr("Translate first page"),
        ]
        for chip_text in chips:
            btn = QPushButton(chip_text)
            btn.setObjectName("AIWelcomeChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, t=chip_text: self._chip_clicked(t))
            cl.addWidget(btn)

        cl.addStretch()

    # ── Panel animation (slide in from left) ────────────────────

    def before_show(self):
        """Called before the panel slides in. Show welcome card if no messages."""
        if self._msg_layout.count() <= 1:  # only the trailing stretch
            self._show_welcome_card()
        self._input_edit.setFocus()

    # ── Input handling ──────────────────────────────────────────

    def _adjust_input_height(self):
        """Auto-grow the input edit as the user types multi-line text."""
        doc_height = self._input_edit.document().size().height()
        new_height = min(max(int(doc_height) + 10, 80), 200)
        self._input_edit.setFixedHeight(new_height)

    def _on_input_changed(self):
        text = self._input_edit.toPlainText().strip()
        self._send_btn.setEnabled(bool(text) and not self._streaming)

    def _on_send(self):
        text = self._input_edit.toPlainText().strip()
        if not text or self._streaming:
            return
        self._input_edit.clear()
        self._hide_welcome_card()
        self._add_user_bubble(text)
        self._sync_settings_to_controller()
        self._ensure_api_config_synced()
        self.send_message.emit(text)

    def _on_stop(self):
        self.stop_requested.emit()

    def _on_clear(self):
        """Confirm before clearing all conversation history."""
        if self._controller and self._controller.messages:
            reply = QMessageBox.question(
                self, self.tr("Clear Conversation"),
                self.tr(
                    "This will permanently delete all {n} messages "
                    "in this conversation. Continue?"
                ).format(n=len(self._controller.messages)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.clear_requested.emit()

    # ── Message display ─────────────────────────────────────────

    def _add_user_bubble(self, text: str):
        """Right-aligned user bubble with accent-tinted background."""
        container = QWidget()
        container.setObjectName("AIUserBubble")
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        max_w = self._bubble_max_width()
        inner = QLabel()
        inner.setObjectName("AIUserInner")
        inner.setText(text)
        inner.setWordWrap(True)
        inner.setMaximumWidth(max_w)
        inner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        lay.addStretch()
        lay.addWidget(inner)
        self._insert_bubble(container)

    def add_system_message(self, text: str):
        label = QLabel(text)
        label.setObjectName("AISystemMsg")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self._insert_bubble(label)

    def start_streaming_response(self):
        """Begin a new assistant bubble (no avatar)."""
        self._streaming = True
        self._streaming_text = ""
        self._send_btn.setVisible(False)
        self._stop_btn.setVisible(True)
        self._send_btn.setEnabled(False)

        # Outer container: transparent, left-aligned
        outer = QWidget()
        outer.setObjectName("AIAssistantBubble")
        oul = QHBoxLayout(outer)
        oul.setContentsMargins(0, 0, 0, 0)
        oul.setSpacing(0)

        # Inner visible container: rounded corners, dark background
        self._streaming_browser = _ChatBubbleBrowser()
        self._streaming_browser.setObjectName("AIAssistantInner")
        self._streaming_browser.setReadOnly(True)
        self._streaming_browser.setFrameShape(QFrame.NoFrame)
        self._streaming_browser.document().setDocumentMargin(0)
        self._streaming_browser.setViewportMargins(0, 0, 0, 0)
        self._streaming_browser.setOpenExternalLinks(True)
        self._streaming_browser.setLineWrapMode(
            QTextBrowser.LineWrapMode.WidgetWidth if self._word_wrap
            else QTextBrowser.LineWrapMode.NoWrap
        )
        self._streaming_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._streaming_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        max_w = self._bubble_max_width()
        self._streaming_browser.setMaximumWidth(max_w)
        self._streaming_browser.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum
        )

        oul.addWidget(self._streaming_browser, 1)
        self._streaming_bubble_widget = outer
        self._insert_bubble(outer)

    def append_stream_chunk(self, chunk: str):
        if not self._streaming or not self._streaming_browser:
            return
        self._streaming_text += chunk
        sb = self._streaming_browser
        sb.setMarkdown(self._streaming_text)
        # Adjust height as content grows
        expected_w = self._bubble_max_width()
        css_pad = 10
        sb.document().setTextWidth(expected_w - 2 * css_pad)
        sb.setFixedHeight(int(sb.document().size().height()) + 2 * css_pad)
        # setFixedHeight triggers a WidgetWidth resize that overrides textWidth; restore it
        sb.document().setTextWidth(expected_w - 2 * css_pad)
        self._scroll_to_bottom()

    def finish_streaming(self, full_text: str = ""):
        # Remove empty streaming bubble that never received content
        if self._streaming_bubble_widget and not full_text and not self._streaming_text.strip():
            self._msg_layout.removeWidget(self._streaming_bubble_widget)
            self._streaming_bubble_widget.deleteLater()

        self._streaming = False
        self._stop_btn.setVisible(False)
        self._send_btn.setVisible(True)
        self._on_input_changed()
        self._streaming_browser = None
        self._streaming_bubble_widget = None

    def set_changes(self, changes: List[ChangeItem]):
        """Build an inline change review card (#AIChangeCard) with accept/reject per item."""
        if not changes:
            return

        fields = sorted(set(c.field for c in changes))
        n = len(changes)

        # ── Card container ──────────────────────────────────────
        card = QWidget()
        card.setObjectName("AIChangeCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # ── Header ────────────────────────────────────────────
        header = QLabel(
            self.tr("AI proposed {n} change(s) on: {fields}").format(
                n=n, fields=", ".join(fields)
            )
        )
        header.setObjectName("AIChangeHeader")
        cl.addWidget(header)

        # ── Per-page summary lines ─────────────────────────────
        page_counts: Dict[str, int] = {}
        for c in changes:
            pid = c.block_id.split(":")[0]
            page_counts[pid] = page_counts.get(pid, 0) + 1
        for pid, cnt in sorted(page_counts.items()):
            pl = QLabel(self.tr("Page {pid}: {cnt} change(s)").format(pid=pid, cnt=cnt))
            pl.setObjectName("AIChangePageLine")
            cl.addWidget(pl)

        # ── Expandable detail section ──────────────────────────
        detail_container = QWidget()
        detail_container.setObjectName("AIReviewDialog")
        dl = QVBoxLayout(detail_container)
        dl.setContentsMargins(8, 4, 8, 4)
        dl.setSpacing(4)

        change_rows: List[tuple] = []  # (ChangeItem, accept_btn, reject_btn)

        for i, item in enumerate(changes):
            # Row container
            row = QWidget()
            row.setObjectName("AIReviewRow")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 2, 4, 2)
            rl.setSpacing(4)

            # ID + field
            id_label = QLabel(item.block_id)
            id_label.setObjectName("AIReviewId")
            id_label.setFixedWidth(60)
            rl.addWidget(id_label)

            field_label = QLabel(item.field)
            field_label.setObjectName("AIReviewFieldLabel")
            field_label.setFixedWidth(40)
            rl.addWidget(field_label)

            # Old → New values
            old_label = QLabel(str(item.old_value)[:30])
            old_label.setObjectName("AIReviewOldValue")
            old_label.setWordWrap(True)
            rl.addWidget(old_label, 1)

            arrow = QLabel("→")
            arrow.setObjectName("AIReviewFieldLabel")
            rl.addWidget(arrow)

            new_label = QLabel(str(item.new_value)[:30])
            new_label.setObjectName("AIReviewNewValue")
            new_label.setWordWrap(True)
            rl.addWidget(new_label, 1)

            # Accept / Reject buttons
            accept_btn = QPushButton("✓")
            accept_btn.setObjectName("AIReviewAccept")
            accept_btn.setFixedSize(28, 28)
            accept_btn.setCheckable(True)
            rl.addWidget(accept_btn)

            reject_btn = QPushButton("✗")
            reject_btn.setObjectName("AIReviewReject")
            reject_btn.setFixedSize(28, 28)
            reject_btn.setCheckable(True)
            rl.addWidget(reject_btn)

            # Wire toggle: only one active at a time
            def make_toggle(ci: ChangeItem, ab: QPushButton, rb: QPushButton):
                def on_accept(checked: bool):
                    ci.accepted = True if checked else None
                    rb.setChecked(False)
                    _update_apply_btn()

                def on_reject(checked: bool):
                    ci.accepted = False if checked else None
                    ab.setChecked(False)
                    _update_apply_btn()

                ab.toggled.connect(on_accept)
                rb.toggled.connect(on_reject)

            make_toggle(item, accept_btn, reject_btn)
            change_rows.append((item, accept_btn, reject_btn))
            dl.addWidget(row)

        # ── Batch action bar ──────────────────────────────────
        action_bar = QWidget()
        action_bar.setObjectName("AIReviewActions")
        al = QHBoxLayout(action_bar)
        al.setContentsMargins(8, 6, 8, 6)
        al.setSpacing(6)

        accept_all = QPushButton(self.tr("Accept All"))
        accept_all.setObjectName("AIReviewAcceptAll")
        accept_all.clicked.connect(
            lambda: self._set_all_accepted(change_rows, True)
        )

        reject_all = QPushButton(self.tr("Reject All"))
        reject_all.setObjectName("AIReviewRejectAll")
        reject_all.clicked.connect(
            lambda: self._set_all_accepted(change_rows, False)
        )

        al.addWidget(accept_all)
        al.addWidget(reject_all)
        al.addStretch()

        # Stats label
        stats_label = QLabel(
            self.tr("Accepted: 0 / {n}").format(n=n)
        )
        stats_label.setObjectName("AIReviewStats")
        al.addWidget(stats_label)

        detail_container.layout().addWidget(action_bar)

        # ── Expand / collapse toggle ──────────────────────────
        detail_container.setVisible(False)
        expand_btn = QPushButton(self.tr("Show details >"))
        expand_btn.setObjectName("AIChangeExpandBtn")
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def toggle_expand():
            collapsed = not detail_container.isVisible()
            detail_container.setVisible(collapsed)
            expand_btn.setText(
                self.tr("Hide details v") if collapsed else self.tr("Show details >")
            )

        expand_btn.clicked.connect(toggle_expand)
        cl.addWidget(expand_btn)

        # ── Apply button (footer) ─────────────────────────────
        footer = QWidget()
        footer.setObjectName("AIReviewFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(8, 6, 8, 6)

        apply_btn = QPushButton(self.tr("Apply Changes"))
        apply_btn.setObjectName("AIReviewApplyBtn")
        apply_btn.setEnabled(False)
        apply_btn.clicked.connect(
            lambda: self._emit_apply_changes(change_rows)
        )

        fl.addStretch()
        fl.addWidget(apply_btn)

        # Closure to update apply button state + stats
        def _update_apply_btn():
            accepted_n = sum(1 for c, _, _ in change_rows if c.accepted is True)
            apply_btn.setEnabled(accepted_n > 0)
            stats_label.setText(
                self.tr("Accepted: {n} / {total}").format(n=accepted_n, total=n)
            )

        dl.addWidget(footer)
        cl.addWidget(detail_container)

        self._insert_bubble(card)

    def _set_all_accepted(self, rows, accepted: bool):
        """Set all change items to accepted/rejected."""
        for item, ab, rb in rows:
            item.accepted = accepted
            ab.setChecked(accepted)
            rb.setChecked(not accepted)

    def _emit_apply_changes(self, rows):
        """Collect accepted changes and emit apply_changes_requested."""
        accepted = [item for item, _, _ in rows if item.accepted is True]
        if accepted:
            self.apply_changes_requested.emit(accepted)

    def set_last_tool_trace(self, trace: List[Dict[str, Any]]):
        """Show tool execution trace as system messages."""
        for t in trace[-3:]:
            name = t.get("name", "?")
            summary = t.get("args_summary", "")
            self.add_system_message(f"🔧 {name}({summary})")

    def show_thinking(self):
        self.update_status(self.tr("Thinking..."), True)
        self._status_dot.setText("●")
        self._status_dot.setObjectName("AIStatusBadgeActive")
        self._status_text.setText(self.tr("Thinking..."))

    def hide_thinking(self):
        self._status_dot.setText("●")
        self._status_dot.setObjectName("AIStatusBadge")

    # ── Status ──────────────────────────────────────────────────

    def update_status(self, text: str, active: bool):
        self._status_text.setText(text)
        if active:
            self._status_dot.setObjectName("AIStatusBadgeActive")
        else:
            self._status_dot.setObjectName("AIStatusBadge")

    def set_prompt_tokens(self, n: int):
        self._token_label.setText(f"~{n} token")
        self._token_label.setToolTip(self.tr("~{n} token (estimated)").format(n=n))

    def reconcile_api_tokens(self, prompt_tokens: int, completion_tokens: int, total: int):
        self._token_label.setText(f"{total} token")
        self._token_label.setToolTip(
            self.tr("Context: {pt} token\nTool calls: {ct} token").format(
                pt=prompt_tokens, ct=completion_tokens
            )
        )

    def on_conversation_cleared(self):
        """Clear all message widgets and show welcome card."""
        self._streaming = False
        self._streaming_text = ""
        self._streaming_browser = None
        self._streaming_bubble_widget = None
        self._clear_messages()
        self._show_welcome_card()
        self.update_status(self.tr("Ready"), False)
        self._token_label.setText("~0 token")
        self._token_label.setToolTip("")

    def rebuild_from_history(self, messages: list):
        """Rebuild visual bubbles from loaded ChatMessage history."""
        self._clear_messages()
        self._hide_welcome_card()
        for m in messages:
            if m.role == "user":
                self._add_user_bubble(m.content)
            elif m.role == "assistant":
                if m.segments:
                    for seg in m.segments:
                        if seg["type"] == "text":
                            self._add_assistant_bubble(seg["content"])
                        elif seg["type"] == "tool_trace":
                            self.add_system_message(seg["content"])
                else:
                    # Legacy: no segments — strip [tool] markers and show as one bubble
                    clean = re.sub(r'\[tool\].*?\[/tool\]\s*\n?', '', m.content, flags=re.DOTALL)
                    self._add_assistant_bubble(clean)
            elif m.role == "system":
                self.add_system_message(m.content)
        if self._msg_layout.count() <= 1:  # only the trailing stretch
            self._show_welcome_card()
        self._scroll_to_bottom()

    def _add_assistant_bubble(self, text: str):
        """Add a completed assistant bubble (static, not streaming).

        Uses WidgetWidth + explicit textWidth management. WidgetWidth enables
        automatic text wrapping so long content fits the bubble. The textWidth
        is set explicitly before computing height, then restored after
        setFixedHeight (which triggers a WidgetWidth resizeEvent that may
        otherwise override textWidth with a stale viewport width).
        """
        outer = QWidget()
        outer.setObjectName("AIAssistantBubble")
        oul = QHBoxLayout(outer)
        oul.setContentsMargins(0, 0, 0, 0)
        oul.setSpacing(0)

        inner = _ChatBubbleBrowser()
        inner.setObjectName("AIAssistantInner")
        inner.setReadOnly(True)
        inner.setFrameShape(QFrame.NoFrame)
        inner.document().setDocumentMargin(0)
        inner.setViewportMargins(0, 0, 0, 0)
        inner.setOpenExternalLinks(True)
        inner.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Strip [tool]...[/tool] blocks that embed tool-call context for the LLM
        clean = re.sub(r'\[tool\].*?\[/tool\]\s*\n?', '', text, flags=re.DOTALL)
        inner.setMarkdown(clean)
        max_w = self._bubble_max_width()
        inner.setMaximumWidth(max_w)
        inner.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
        css_pad = 10
        # WidgetWidth enables wrapping; set textWidth explicitly so the
        # height is computed from the capped width (not an unbounded one).
        inner.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        inner.document().setTextWidth(max_w - 2 * css_pad)
        inner.setFixedHeight(int(inner.document().size().height()) + 2 * css_pad)
        # setFixedHeight triggers a WidgetWidth resizeEvent that overrides
        # textWidth with the (possibly stale) viewport width. Restore it.
        inner.document().setTextWidth(max_w - 2 * css_pad)

        oul.addWidget(inner, 1)
        self._insert_bubble(outer)

    def on_error(self, msg: str):
        self.add_system_message(self.tr("-- Error: {msg} --").format(msg=msg))
        self._streaming = False
        self._stop_btn.setVisible(False)
        self._send_btn.setVisible(True)
        self._on_input_changed()
        self.update_status(self.tr("Ready"), False)

    # ── Settings ──────────────────────────────────────────────────

    FIELD_LABELS = {
        'src':   '',
        'trans': '',
        'ff':    '',
        'fs':    '',
        'fg':    '',
        'bg':    '',
        'b':     '',
        'i':     '',
        'a':     '',
        'sw':    '',
        'ls':    '',
    }

    def set_controller(self, controller: Any):
        """Set the AiController reference for reading/writing settings."""
        self._controller = controller
        # Sync title bar mode combo from controller state
        if controller:
            mode_map = {"auto": 0, "agent": 1, "chat": 2}
            self._title_mode.blockSignals(True)
            self._title_mode.setCurrentIndex(
                mode_map.get(controller.chat_mode, 0)
            )
            self._title_mode.blockSignals(False)

            # Auto-populate api_config from active translator profile on first use
            if not controller.api_config.get('api_host') or not controller.api_config.get('model'):
                active = self._get_active_profile_name()
                if active:
                    self._sync_profile_to_controller(active)

    def _get_translator_profiles(self) -> List[Dict]:
        """Read all translator API profiles."""
        try:
            params = pcfg.module.translator_params.get("LLM_API_Translator", {})
            storage = params.get("_profiles_storage", "[]")
            if isinstance(storage, dict):
                raw = storage.get("value", "[]")
            else:
                raw = storage
            profiles = json.loads(raw) if isinstance(raw, str) else raw
            return profiles if isinstance(profiles, list) else []
        except Exception:
            return []

    def _get_active_profile_name(self) -> str:
        """Return the currently active translator profile name."""
        try:
            params = pcfg.module.translator_params.get("LLM_API_Translator", {})
            active = params.get("active_profile", "")
            if isinstance(active, dict):
                return active.get("value", "")
            return active if isinstance(active, str) else ""
        except Exception:
            return ""

    def _set_active_profile(self, name: str):
        """Set the active translator profile and persist."""
        from utils.config import save_config
        params = pcfg.module.translator_params.get("LLM_API_Translator", {})
        ap = params.get("active_profile", "")
        if isinstance(ap, dict):
            ap["value"] = name
        else:
            params["active_profile"] = {"value": name}
        save_config()

    def _on_profile_changed(self, index: int):
        """Handle profile selection change in settings."""
        if index < 0:
            return
        name = self._settings_profile.itemText(index)
        self._set_active_profile(name)
        self._sync_profile_to_controller(name)

    def _sync_profile_to_controller(self, name: str):
        """Push the selected profile's API config into the controller."""
        if not self._controller:
            return
        profiles = self._get_translator_profiles()
        for p in profiles:
            if p.get("name") == name:
                self._controller.api_config = {
                    "api_host": p.get("api_host", ""),
                    "api_key": p.get("api_key", ""),
                    "model": p.get("model", ""),
                    "temperature": p.get("temperature", 0.7),
                    "max_tokens": p.get("max_tokens", ""),
                }
                self._controller.save_ai_settings()
                return

    def _on_title_mode_changed(self, index: int):
        """Handle title bar mode combo change."""
        if not self._controller:
            return
        mode_map = {0: "auto", 1: "agent", 2: "chat"}
        self._controller.chat_mode = mode_map.get(index, "auto")
        self._controller.save_ai_settings()

    def _on_word_wrap_toggled(self, checked: bool):
        """Toggle word wrap in chat bubbles."""
        self._word_wrap = checked

    def _on_context_limit_changed(self):
        """Push context message limit to controller on editing finished."""
        if not self._controller:
            return
        try:
            value = int(self._context_limit_edit.text())
            self._controller.context_message_limit = value
        except ValueError:
            self._context_limit_edit.setText(str(self._controller.context_message_limit))

    @staticmethod
    def _default_translation_prompt() -> str:
        """Return the default translation prompt template."""
        return (
            "You are a professional manga/comic translator. "
            "Translate the text in this project from {from_lang} to {to_lang}.\n\n"
            "Rules:\n"
            "- Output natural, idiomatic text in the target language\n"
            "- Preserve character voice and emotional tone\n"
            "- Keep terminology consistent across the entire project\n"
            "- Localize onomatopoeia naturally\n"
            "- Do NOT rewrite the source text; only output the translation\n"
            "- When in doubt about a term, keep the original with a note"
        )

    def _open_prompt_editor(self):
        """Open a dialog to edit the translation prompt."""
        if not self._controller:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Translation Prompt"))
        dlg.setMinimumSize(480, 360)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        label = QLabel(self.tr(
            "Edit the system prompt used when translation mode is active. "
            "Use {from_lang} and {to_lang} as placeholders for source/target languages."
        ))
        label.setWordWrap(True)
        layout.addWidget(label)

        editor = QPlainTextEdit()
        editor.setObjectName("AIPromptEditor")
        editor.setPlainText(self._controller.custom_prompt or self._default_translation_prompt())
        editor.setMinimumHeight(200)
        layout.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Reset to Default"))
        reset_btn.clicked.connect(lambda: editor.setPlainText(self._default_translation_prompt()))
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._controller.custom_prompt = editor.toPlainText().strip()
            self._controller.save_ai_settings()

    def _build_settings_widget(self) -> QWidget:
        """Build a compact settings form overlay."""
        w = QWidget()
        w.setObjectName("AISettingsDrawer")
        w.setVisible(False)

        # Lazy-init translatable field labels
        if not self.FIELD_LABELS['src']:
            self.FIELD_LABELS.update({
                'src':   self.tr("Source"),
                'trans': self.tr("Translation"),
                'ff':    self.tr("Font"),
                'fs':    self.tr("Size"),
                'fg':    self.tr("Text Color"),
                'bg':    self.tr("BG Color"),
                'b':     self.tr("Bold"),
                'i':     self.tr("Italic"),
                'a':     self.tr("Align"),
                'sw':    self.tr("Stroke"),
                'ls':    self.tr("Line Spacing"),
            })

        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        # ── Header ──────────────────────────────────────────
        header = QLabel(self.tr("Settings"))
        header.setObjectName("AISettingsHeader")
        layout.addWidget(header)

        # ── 1. Model API profile selector ───────────────────
        api_label = QLabel(self.tr("API Profile"))
        api_label.setObjectName("AISettingsSection")
        layout.addWidget(api_label)

        self._settings_profile = QComboBox()
        self._settings_profile.setObjectName("AISettingsField")
        self._settings_profile.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self._settings_profile)

        # ── 1b. Context settings ────────────────────────────
        ctx_label = QLabel(self.tr("Context"))
        ctx_label.setObjectName("AISettingsSection")
        layout.addWidget(ctx_label)

        ctx_row = QHBoxLayout()
        ctx_row.setSpacing(6)
        ctx_row.addWidget(QLabel(self.tr("History messages:")))
        self._context_limit_edit = QLineEdit()
        self._context_limit_edit.setObjectName("AISettingsField")
        self._context_limit_edit.setValidator(QIntValidator(10, 99))
        self._context_limit_edit.setText("20")
        self._context_limit_edit.setFixedWidth(48)
        self._context_limit_edit.editingFinished.connect(self._on_context_limit_changed)
        ctx_row.addWidget(self._context_limit_edit)
        ctx_row.addStretch()
        layout.addLayout(ctx_row)

        self._auto_compress_cb = QCheckBox(
            self.tr("Auto-compress when exceeding limit")
        )
        self._auto_compress_cb.setObjectName("AIDataToggle")
        layout.addWidget(self._auto_compress_cb)

        # ── 2. Data read settings ───────────────────────────
        data_label = QLabel(self.tr("Data Read Settings"))
        data_label.setObjectName("AISettingsSection")
        layout.addWidget(data_label)

        self._trans_mode_cb = QCheckBox(
            self.tr("Translation mode (lock to source/translation only)")
        )
        self._trans_mode_cb.setObjectName("AIDataToggle")
        self._trans_mode_cb.toggled.connect(self._on_translation_mode_toggled)
        layout.addWidget(self._trans_mode_cb)

        self._word_wrap_cb = QCheckBox(
            self.tr("Word wrap in chat bubbles")
        )
        self._word_wrap_cb.setObjectName("AIDataToggle")
        self._word_wrap_cb.setChecked(True)
        self._word_wrap_cb.toggled.connect(self._on_word_wrap_toggled)
        layout.addWidget(self._word_wrap_cb)

        # Edit translation prompt button (only meaningful with translation mode)
        prompt_btn = QPushButton(self.tr("Edit Translation Prompt..."))
        prompt_btn.setObjectName("AISettingsField")
        prompt_btn.clicked.connect(self._open_prompt_editor)
        self._trans_mode_cb.toggled.connect(
            lambda checked: prompt_btn.setEnabled(checked)
        )
        prompt_btn.setEnabled(self._trans_mode_cb.isChecked())
        layout.addWidget(prompt_btn)

        # ── 3. Readable block fields ────────────────────────
        misc_label = QLabel(self.tr("Readable Block Fields"))
        misc_label.setObjectName("AISettingsSection")
        layout.addWidget(misc_label)

        hint = QLabel(self.tr("Select which text block properties the AI can read and modify."))
        hint.setObjectName("AISettingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Field checkboxes in a 4-column grid
        grid = QGridLayout()
        grid.setSpacing(2)
        self._field_cbs: Dict[str, QCheckBox] = {}
        keys = list(self.FIELD_LABELS.keys())
        for i, key in enumerate(keys):
            cb = QCheckBox(self.FIELD_LABELS[key])
            cb.setObjectName("AIDataToggle")
            self._field_cbs[key] = cb
            grid.addWidget(cb, i // 4, i % 4)
        layout.addLayout(grid)

        layout.addStretch()

        return w

    def _on_translation_mode_toggled(self, checked: bool):
        """When translation mode is on, lock whitelist to src+trans only."""
        if checked:
            # Save current whitelist state before locking
            self._field_wl_before_trans = {
                key for key, cb in self._field_cbs.items() if cb.isChecked()
            }
            for key, cb in self._field_cbs.items():
                if key in ('src', 'trans'):
                    cb.setChecked(True)
                else:
                    cb.setChecked(False)
                cb.setEnabled(False)
        else:
            # Restore previous whitelist state
            prev = getattr(self, '_field_wl_before_trans', None)
            for key, cb in self._field_cbs.items():
                cb.setEnabled(True)
                if prev is not None:
                    cb.setChecked(key in prev)

    def _toggle_settings(self, visible: bool):
        """Slide settings overlay from the right using OverlaySlider."""
        if not self._settings_widget:
            self._settings_widget = self._build_settings_widget()
            self._settings_widget.setParent(self._scroll_area)
            self._settingsSlide = OverlaySlider(
                self._settings_widget, direction='right',
                width=lambda: self._scroll_area.width(),
            )

        if visible:
            self._sync_settings_from_controller()
            self._hide_welcome_card()
            self._settingsSlide.show()
        else:
            self._sync_settings_to_controller()
            self._settingsSlide.hide()

    def _sync_settings_from_controller(self):
        """Read controller + translator config into the settings form."""
        if not hasattr(self, '_trans_mode_cb'):
            return  # settings widget not built yet
        # Populate profile dropdown from translator profiles
        self._settings_profile.blockSignals(True)
        self._settings_profile.clear()
        profiles = self._get_translator_profiles()
        active = self._get_active_profile_name()
        active_idx = 0
        for i, p in enumerate(profiles):
            name = p.get("name", "")
            if name:
                self._settings_profile.addItem(name)
                if name == active:
                    active_idx = i
        if self._settings_profile.count() > 0:
            self._settings_profile.setCurrentIndex(active_idx)
        self._settings_profile.blockSignals(False)

        # Explicitly sync the active profile's API config into the controller.
        # blockSignals(True) suppressed currentIndexChanged so _on_profile_changed
        # never fired; the controller still has a potentially empty api_config.
        if self._settings_profile.count() > 0:
            name = self._settings_profile.currentText()
            if name:
                self._sync_profile_to_controller(name)

        # Sync title bar mode combo from controller
        mode_map = {"auto": 0, "agent": 1, "chat": 2}
        self._title_mode.blockSignals(True)
        self._title_mode.setCurrentIndex(
            mode_map.get(self._controller.chat_mode, 0) if self._controller else 0
        )
        self._title_mode.blockSignals(False)

        # Translation mode
        trans_mode = self._controller.translation_mode if self._controller else False
        self._trans_mode_cb.setChecked(trans_mode)

        # Word wrap
        self._word_wrap_cb.setChecked(self._word_wrap)

        # Context limit
        ctx_limit = self._controller.context_message_limit if self._controller else 20
        self._context_limit_edit.blockSignals(True)
        self._context_limit_edit.setText(str(ctx_limit))
        self._context_limit_edit.blockSignals(False)

        # Auto compress
        auto_compress = self._controller.auto_compress if self._controller else False
        self._auto_compress_cb.setChecked(auto_compress)

        # Field whitelist
        wl = self._controller.fields_whitelist if self._controller else {'src', 'trans'}
        for key, cb in self._field_cbs.items():
            cb.setChecked(key in wl)
            cb.setEnabled(not trans_mode or key in ('src', 'trans'))

    def _sync_settings_to_controller(self):
        """Write settings form values back to the controller."""
        if not self._controller:
            return
        if not hasattr(self, '_trans_mode_cb'):
            return  # settings widget not built yet; controller defaults are correct

        # api_config is kept in sync via _on_profile_changed / _sync_profile_to_controller

        # translation_mode
        self._controller.translation_mode = self._trans_mode_cb.isChecked()

        # context limit (also pushed immediately via _on_context_limit_changed)
        try:
            self._controller.context_message_limit = int(self._context_limit_edit.text())
        except ValueError:
            pass
        self._controller.auto_compress = self._auto_compress_cb.isChecked()

        # fields_whitelist
        if self._trans_mode_cb.isChecked():
            self._controller.fields_whitelist = {'src', 'trans'}
        else:
            self._controller.fields_whitelist = {
                key for key, cb in self._field_cbs.items() if cb.isChecked()
            }

        self._controller.save_ai_settings()

    def _ensure_api_config_synced(self):
        """Sync api_config from active translator profile if it's missing host/model.

        This bridges a gap: when the user has a valid translator profile configured
        but the controller's api_config was never populated (e.g. because the
        auto-populate in set_controller ran before translator modules were ready),
        we pull the config here right before sending the request.
        """
        if not self._controller:
            return
        cfg = self._controller.api_config
        if cfg.get('api_host') and cfg.get('model'):
            return  # already valid
        # First try the profile currently selected in the settings widget
        if hasattr(self, '_settings_profile') and self._settings_profile.count() > 0:
            name = self._settings_profile.currentText()
            if name:
                self._sync_profile_to_controller(name)
                if self._controller.api_config.get('api_host') and self._controller.api_config.get('model'):
                    return
        # Fall back to the active translator profile
        active = self._get_active_profile_name()
        if active:
            self._sync_profile_to_controller(active)

    def set_project_loaded(self, loaded: bool):
        """Enable/disable the input bar based on project state."""
        self._input_edit.setEnabled(loaded)
        self._send_btn.setEnabled(loaded and bool(self._input_edit.toPlainText().strip()))
        if not loaded:
            self._input_edit.setPlaceholderText(
                self.tr("Open a project to start chatting")
            )
        else:
            self._input_edit.setPlaceholderText(
                self.tr("Ask AI... (Enter to send, Shift+Enter for newline)")
            )

    # ── Welcome card helper ────────────────────────────────────

    def _show_welcome_card(self):
        """Show the welcome card in the message area."""
        if self._welcome_card.parent() != self._msg_container:
            self._msg_layout.insertWidget(0, self._welcome_card)
        self._welcome_card.setVisible(True)

    def _hide_welcome_card(self):
        """Hide the welcome card."""
        self._welcome_card.setVisible(False)

    # ── Helpers ─────────────────────────────────────────────────

    def _insert_bubble(self, bubble_widget: QWidget):
        """Insert a bubble widget before the trailing stretch."""
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1, bubble_widget
        )
        self._scroll_to_bottom()

    def _clear_messages(self):
        """Remove all bubble widgets from the message layout."""
        protected = {self._welcome_card}
        # Collect indices to remove (iterate in reverse to avoid index shift)
        for i in range(self._msg_layout.count() - 1, -1, -1):
            item = self._msg_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue  # skip spacers and stretches
            if w in protected:
                continue
            self._msg_layout.takeAt(i)
            w.deleteLater()

    def _scroll_to_bottom(self):
        """Scroll the message area to the latest message."""
        sb = self._scroll_area.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _bubble_max_width(self) -> int:
        """Max width for bubble inner widgets, from panel's fixed width.

        Uses maximumWidth() (reliably 480 from setFixedWidth) instead of
        width() which may be unreliable when the panel is hidden.
        """
        return min(460, max(200, self.maximumWidth() - 16))

    # ── Configuration access (used by AiController) ──────────────

    @property
    def _messages(self) -> List:
        """Stub for controller access — real history is in AiController."""
        return []

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Streaming assistant bubble: update width cap and recalculate height
        if self._streaming_browser:
            expected_w = self._bubble_max_width()
            css_pad = 10
            self._streaming_browser.setMaximumWidth(expected_w)
            self._streaming_browser.document().setTextWidth(expected_w - 2 * css_pad)
            self._streaming_browser.setFixedHeight(
                int(self._streaming_browser.document().size().height()) + 2 * css_pad
            )
            self._streaming_browser.document().setTextWidth(expected_w - 2 * css_pad)
        # Reposition settings overlay if visible
        if self._settingsSlide is not None:
            self._settingsSlide.resize()
