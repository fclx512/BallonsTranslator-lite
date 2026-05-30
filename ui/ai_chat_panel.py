"""
AiChatPanel — bottom-slide chat panel for the AI assistant.

Standard Qt widgets (no custom QPainter).  Connects to AiController
signals for streaming conversation display.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from qtpy.QtCore import (
    QEvent,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import QIntValidator, QKeyEvent
from qtpy.QtWidgets import (
    QApplication,
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
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from utils.config import pcfg

from .ai_chat_model import ChangeItem
from .overlay_slide import OverlaySlider

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
    open_review_requested = Signal(list, int)  # list[ChangeItem], message_index

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
        self._typing_indicator: Optional[QLabel] = None
        self._typing_timer: Optional[QTimer] = None
        self._typing_dot_count = 0
        self._bubble_hovered: Optional[QWidget] = None
        self._bubble_actions: Dict[QWidget, QWidget] = {}
        self._last_bubble_role: Optional[str] = None
        self._msg_counter: int = 0

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
        cl.setContentsMargins(16, 20, 16, 16)
        cl.setSpacing(6)

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

        cl.addSpacing(10)

        # ── Feature cards row ─────────────────────────────────
        features = [
            (self.tr("✏️ Edit"), self.tr("Change text, fonts, colors")),
            (self.tr("🔍 Search"), self.tr("Find and replace across pages")),
            (self.tr("🌐 Translate"), self.tr("Translate with context awareness")),
        ]
        feat_row = QHBoxLayout()
        feat_row.setSpacing(6)
        for feat_title, feat_desc in features:
            fc = QWidget()
            fc.setObjectName("AIWelcomeFeature")
            fl = QVBoxLayout(fc)
            fl.setContentsMargins(8, 6, 8, 6)
            fl.setSpacing(2)
            ft = QLabel(feat_title)
            ft.setObjectName("AIWelcomeFeatureTitle")
            fl.addWidget(ft)
            fd = QLabel(feat_desc)
            fd.setObjectName("AIWelcomeFeatureDesc")
            fd.setWordWrap(True)
            fl.addWidget(fd)
            feat_row.addWidget(fc)
        cl.addLayout(feat_row)

        cl.addSpacing(10)

        # ── Suggestion chips ──────────────────────────────────
        chip_label = QLabel(self.tr("Try asking:"))
        chip_label.setObjectName("AIWelcomeChipLabel")
        cl.addWidget(chip_label)

        self._welcome_chips_container = QWidget()
        self._welcome_chips_container.setObjectName("AIWelcomeChips")
        self._chips_layout = QVBoxLayout(self._welcome_chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        cl.addWidget(self._welcome_chips_container)

        # Populate default chips
        self._set_welcome_chips(
            [
                self.tr("List all pages"),
                self.tr("Search for 'hello'"),
                self.tr("Make font bold"),
                self.tr("Translate first page"),
            ]
        )

        cl.addStretch()

    def _set_welcome_chips(self, chip_texts: List[str]):
        """Replace suggestion chips in the welcome card."""
        # Clear existing chips
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Add new chips
        for chip_text in chip_texts:
            btn = QPushButton(chip_text)
            btn.setObjectName("AIWelcomeChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, t=chip_text: self._chip_clicked(t))
            self._chips_layout.addWidget(btn)

    def set_welcome_chips_from_project(
        self, project_info: Optional[Dict[str, Any]] = None
    ):
        """Update welcome card chips based on current project context."""
        if project_info:
            total = project_info.get("total_pages", 0)
            name = project_info.get("project", "")
            chips = [
                self.tr("List all pages"),
                self.tr("Translate first page"),
                self.tr("Adjust all fonts"),
            ]
            if total > 1:
                chips.append(self.tr("Compare page 1 and 2"))
            chips.append(self.tr("What can you do?"))
            self._set_welcome_chips(chips)
        else:
            self._set_welcome_chips(
                [
                    self.tr("List all pages"),
                    self.tr("Search for 'hello'"),
                    self.tr("Make font bold"),
                    self.tr("Translate first page"),
                ]
            )

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
                self,
                self.tr("Clear Conversation"),
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

    def _make_bubble_footer(self) -> Optional[QWidget]:
        """Build a small footer label for assistant bubbles (model name)."""
        if not self._controller:
            return None
        model = self._controller.api_config.get("model", "")
        if not model:
            return None
        footer = QLabel(model)
        footer.setObjectName("AITokenFooter")
        return footer

    def _install_bubble_actions(self, container: QWidget, text: str):
        """Add hover-reveal copy/delete action buttons to a bubble container.

        Uses eventFilter on the container to show/hide buttons on Enter/Leave.
        Buttons positioned top-right of the bubble, layering over its content.
        """
        actions = QWidget(container)
        actions.setObjectName("AIBubbleActions")
        al = QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)

        copy_btn = QPushButton("📋")
        copy_btn.setObjectName("AIBubbleActionBtn")
        copy_btn.setFixedSize(22, 22)
        copy_btn.setToolTip(self.tr("Copy"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text))

        del_btn = QPushButton("✕")
        del_btn.setObjectName("AIBubbleActionBtn")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip(self.tr("Delete"))
        del_btn.clicked.connect(lambda: self._remove_bubble_safe(container))

        al.addWidget(copy_btn)
        al.addWidget(del_btn)
        actions.hide()

        container.installEventFilter(self)
        self._bubble_actions[container] = actions
        # Initial positioning — subsequent resizes handled in eventFilter
        actions.move(container.width() - 56, 2)
        actions.raise_()

    def _remove_bubble_safe(self, container: QWidget):
        """Remove a bubble widget from layout and clean up."""
        self._msg_layout.removeWidget(container)
        self._bubble_actions.pop(container, None)
        container.deleteLater()

    def eventFilter(self, obj, event):
        """Show/hide bubble action buttons on hover; keep them positioned on resize."""
        if event.type() == QEvent.Type.Enter:
            actions = self._bubble_actions.get(obj)
            if actions:
                actions.show()
                actions.raise_()
        elif event.type() == QEvent.Type.Leave:
            actions = self._bubble_actions.get(obj)
            if actions:
                actions.hide()
        elif event.type() == QEvent.Type.Resize:
            actions = self._bubble_actions.get(obj)
            if actions:
                actions.move(obj.width() - 56, 2)
                actions.raise_()
        return super().eventFilter(obj, event)

    def _add_user_bubble(self, text: str):
        """Right-aligned user bubble with accent-tinted background and timestamp."""
        container = QWidget()
        container.setObjectName("AIUserBubble")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        max_w = self._bubble_max_width()
        inner = QLabel()
        inner.setObjectName("AIUserInner")
        inner.setText(text)
        inner.setWordWrap(True)
        inner.setMaximumWidth(max_w)
        inner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        # Bubble row (right-aligned)
        b_row = QHBoxLayout()
        b_row.setContentsMargins(0, 0, 0, 0)
        b_row.setSpacing(0)
        b_row.addStretch()
        b_row.addWidget(inner)
        lay.addLayout(b_row)

        self._install_bubble_actions(container, text)
        self._insert_bubble(container)
        self._last_bubble_role = "user"

    def add_system_message(self, text: str):
        label = QLabel(text)
        label.setObjectName("AISystemMsg")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self._insert_bubble(label)
        self._last_bubble_role = "system"

    def start_streaming_response(self):
        """Begin a new assistant bubble with typing indicator."""
        self._streaming = True
        self._streaming_text = ""
        self._send_btn.setVisible(False)
        self._stop_btn.setVisible(True)
        self._send_btn.setEnabled(False)

        # Outer container: transparent, left-aligned
        outer = QWidget()
        outer.setObjectName("AIAssistantBubble")
        oul = QVBoxLayout(outer)
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
        self._streaming_browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
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

        # Bubble row
        b_row = QHBoxLayout()
        b_row.setContentsMargins(0, 0, 0, 0)
        b_row.setSpacing(0)
        b_row.addWidget(self._streaming_browser, 1)
        oul.addLayout(b_row)

        # Typing indicator (three pulsing dots)
        self._typing_indicator = QLabel("● ● ●")
        self._typing_indicator.setObjectName("AITypingIndicator")
        self._typing_dot_count = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(350)
        self._typing_timer.timeout.connect(self._pulse_typing_dots)
        self._typing_timer.start()
        # Add below the browser row
        ti_row = QHBoxLayout()
        ti_row.setContentsMargins(8, 4, 0, 4)
        ti_row.setSpacing(0)
        ti_row.addWidget(self._typing_indicator)
        ti_row.addStretch()
        oul.addLayout(ti_row)

        # Add visual pairing if following a user message
        if self._last_bubble_role == "user":
            outer.setObjectName("AIAssistantBubblePaired")
        self._streaming_bubble_widget = outer
        self._insert_bubble(outer)
        self._last_bubble_role = "assistant"

    def _pulse_typing_dots(self):
        """Animate the typing indicator: cycle through dot patterns."""
        self._typing_dot_count = (self._typing_dot_count + 1) % 4
        dots = ["○ ○ ○", "● ○ ○", "● ● ○", "● ● ●"]
        if self._typing_indicator:
            self._typing_indicator.setText(dots[self._typing_dot_count])

    def _cleanup_typing(self):
        """Stop typing timer and remove indicator."""
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer.deleteLater()
            self._typing_timer = None
        if self._typing_indicator:
            self._typing_indicator.deleteLater()
            self._typing_indicator = None

    def append_stream_chunk(self, chunk: str):
        if not self._streaming or not self._streaming_browser:
            return
        # Remove typing indicator on first content chunk
        if self._typing_indicator:
            self._cleanup_typing()
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
        # Clean up typing indicator if streaming ended before content arrived
        self._cleanup_typing()
        # Remove empty streaming bubble that never received content
        if (
            self._streaming_bubble_widget
            and not full_text
            and not self._streaming_text.strip()
        ):
            self._msg_layout.removeWidget(self._streaming_bubble_widget)
            self._streaming_bubble_widget.deleteLater()

        self._streaming = False
        self._stop_btn.setVisible(False)
        self._send_btn.setVisible(True)
        self._on_input_changed()
        self._streaming_browser = None
        self._streaming_bubble_widget = None
        self._streaming_text = ""

    def set_changes(self, changes: List[ChangeItem], auto_open: bool = True):
        """Build a compact summary card (#AIChangeCard) with a button to open the review window.

        When *auto_open* is True (live response), also emits
        ``open_review_requested`` so the MainWindow can auto-open the
        standalone ChangeReviewWindow.
        """
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

        # ── Open review button ─────────────────────────────────
        btn_row = QWidget()
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(12, 8, 12, 8)

        self._msg_counter += 1
        msg_idx = self._msg_counter

        open_btn = QPushButton(self.tr("Open in Review Window"))
        open_btn.setObjectName("AIChangeReviewOpenBtn")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(
            lambda: self.open_review_requested.emit(changes, msg_idx)
        )
        brl.addStretch()
        brl.addWidget(open_btn)
        brl.addStretch()
        cl.addWidget(btn_row)

        self._insert_bubble(card)

        if auto_open:
            self.open_review_requested.emit(changes, msg_idx)

    def set_last_tool_trace(self, trace: List[Dict[str, Any]]):
        """Show tool execution trace as system messages."""
        for t in trace[-3:]:
            name = t.get("name", "?")
            summary = t.get("args_summary", "")
            self.add_system_message(f"🔧 {name}({summary})")

    def show_thinking(self):
        self.update_status(self.tr("Thinking..."), True)
        self._status_dot.setText("●")

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
        s = "" if n == 1 else "s"
        self._token_label.setText(f"~{n} token{s}")
        self._token_label.setToolTip(self.tr("~{n} token (estimated)").format(n=n))

    def reconcile_api_tokens(
        self, prompt_tokens: int, completion_tokens: int, total: int
    ):
        s = "" if total == 1 else "s"
        self._token_label.setText(f"{total} token{s}")
        self._token_label.setToolTip(
            self.tr("Context: {pt} token\nTool calls: {ct} token").format(
                pt=prompt_tokens, ct=completion_tokens
            )
        )

    def on_conversation_cleared(self):
        """Clear all message widgets and show welcome card."""
        self._cleanup_typing()
        self._streaming = False
        self._streaming_text = ""
        self._streaming_browser = None
        self._streaming_bubble_widget = None
        self._last_bubble_role = None
        self._msg_counter = 0
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
                # Replay display segments (intermediate text / tool traces)
                if m.segments:
                    for seg in m.segments:
                        if seg["type"] == "text":
                            self._add_assistant_bubble(seg["content"])
                        elif seg["type"] == "tool_trace":
                            self.add_system_message(seg["content"])
                elif not m.changes:
                    # Legacy: no segments, no changes — strip [tool] markers
                    clean = re.sub(
                        r"\[tool\].*?\[/tool\]\s*\n?", "", m.content, flags=re.DOTALL
                    )
                    if clean.strip():
                        self._add_assistant_bubble(clean)
                # Restore change review card when present
                if m.changes:
                    self.set_changes(m.changes, auto_open=False)
            elif m.role == "system":
                self.add_system_message(m.content)
        if self._msg_layout.count() <= 1:  # only the trailing stretch
            self._show_welcome_card()
        self._scroll_to_bottom()

    COLLAPSE_THRESHOLD = 800
    COLLAPSE_PREVIEW_LEN = 300

    def _add_assistant_bubble(self, text: str):
        """Add a completed assistant bubble with timestamp and optional collapse.

        Uses WidgetWidth + explicit textWidth management. WidgetWidth enables
        automatic text wrapping so long content fits the bubble. The textWidth
        is set explicitly before computing height, then restored after
        setFixedHeight (which triggers a WidgetWidth resizeEvent that may
        otherwise override textWidth with a stale viewport width).

        Long messages (>COLLAPSE_THRESHOLD chars) are collapsed by default
        with a Show-more toggle.
        """
        # Strip [tool]...[/tool] blocks that embed tool-call context for the LLM
        full = re.sub(r"\[tool\].*?\[/tool\]\s*\n?", "", text, flags=re.DOTALL)

        outer = QWidget()
        outer.setObjectName("AIAssistantBubble")
        oul = QVBoxLayout(outer)
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

        # Determine collapsed vs full content
        need_collapse = len(full) > self.COLLAPSE_THRESHOLD
        preview = full[: self.COLLAPSE_PREVIEW_LEN] + "\n\n..."
        is_collapsed = [need_collapse]  # mutable for closure

        def _set_content(content: str):
            inner.setMarkdown(content)
            max_w = self._bubble_max_width()
            inner.setMaximumWidth(max_w)
            inner.setSizePolicy(
                QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum
            )
            css_pad = 10
            inner.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
            inner.document().setTextWidth(max_w - 2 * css_pad)
            inner.setFixedHeight(int(inner.document().size().height()) + 2 * css_pad)
            inner.document().setTextWidth(max_w - 2 * css_pad)

        _set_content(preview if need_collapse else full)

        # Bubble row
        b_row = QHBoxLayout()
        b_row.setContentsMargins(0, 0, 0, 0)
        b_row.setSpacing(0)
        b_row.addWidget(inner, 1)
        oul.addLayout(b_row)

        # Collapse toggle button
        if need_collapse:
            toggle_btn = QPushButton(self.tr("Show more ▼"))
            toggle_btn.setObjectName("AICollapseToggle")
            toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle_btn.setFixedHeight(24)
            t_row = QHBoxLayout()
            t_row.setContentsMargins(4, 0, 0, 0)
            t_row.setSpacing(0)
            t_row.addWidget(toggle_btn)
            t_row.addStretch()
            oul.addLayout(t_row)

            def _toggle_collapse():
                if is_collapsed[0]:
                    _set_content(full)
                    toggle_btn.setText(self.tr("Show less ▲"))
                    is_collapsed[0] = False
                else:
                    _set_content(preview)
                    toggle_btn.setText(self.tr("Show more ▼"))
                    is_collapsed[0] = True

            toggle_btn.clicked.connect(_toggle_collapse)

        # Footer row (model name)
        footer = self._make_bubble_footer()
        if footer:
            ft_row = QHBoxLayout()
            ft_row.setContentsMargins(4, 0, 0, 0)
            ft_row.setSpacing(0)
            ft_row.addWidget(footer)
            ft_row.addStretch()
            oul.addLayout(ft_row)

        # Add visual pairing if following a user message
        if self._last_bubble_role == "user":
            outer.setObjectName("AIAssistantBubblePaired")
        self._install_bubble_actions(outer, text)
        self._insert_bubble(outer)
        self._last_bubble_role = "assistant"

    def on_error(self, msg: str):
        self._cleanup_typing()
        self.add_system_message(self.tr("-- Error: {msg} --").format(msg=msg))
        self._streaming = False
        self._stop_btn.setVisible(False)
        self._send_btn.setVisible(True)
        self._on_input_changed()
        self.update_status(self.tr("Ready"), False)

    # ── Settings ──────────────────────────────────────────────────

    FIELD_LABELS = {
        "src": "",
        "trans": "",
        "ff": "",
        "fs": "",
        "fg": "",
        "bg": "",
        "b": "",
        "i": "",
        "a": "",
        "sw": "",
        "ls": "",
    }

    def set_controller(self, controller: Any):
        """Set the AiController reference for reading/writing settings."""
        self._controller = controller
        # Sync title bar mode combo from controller state
        if controller:
            mode_map = {"auto": 0, "agent": 1, "chat": 2}
            self._title_mode.blockSignals(True)
            self._title_mode.setCurrentIndex(mode_map.get(controller.chat_mode, 0))
            self._title_mode.blockSignals(False)

            # Auto-populate api_config from active translator profile on first use
            if not controller.api_config.get(
                "api_host"
            ) or not controller.api_config.get("model"):
                active = self._get_active_profile_name()
                if active:
                    self._sync_profile_to_controller(active)

    def _get_translator_profiles(self) -> List[Dict]:
        """Read all API profiles from shared profile storage."""
        from utils.profile_manager import load_profiles

        return load_profiles()

    def _get_active_profile_name(self) -> str:
        """Return the currently active translator profile name."""
        from modules.translators.trans_llm_api import LLM_API_Translator

        params = getattr(LLM_API_Translator, "params", {})
        if not params:
            return ""
        active = params.get("active_profile", {})
        if isinstance(active, dict):
            return active.get("value", "")
        return active if isinstance(active, str) else ""

    def _set_active_profile(self, name: str):
        """Set the active translator profile and persist."""
        from utils.config import save_config

        params = pcfg.module.translator_params.get("LLM_API_Translator", {})
        ap = params.get("active_profile", "")
        if isinstance(ap, dict):
            ap["value"] = name
        else:
            params["active_profile"] = {"value": name}
        # Also update the class-level params (shared with the translator instance)
        from modules.translators.trans_llm_api import LLM_API_Translator

        cls_ap = LLM_API_Translator.params.get("active_profile", {})
        if isinstance(cls_ap, dict):
            cls_ap["value"] = name
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

    def _on_context_limit_changed(self):
        """Push context message limit to controller on editing finished."""
        if not self._controller:
            return
        try:
            value = int(self._context_limit_edit.text())
            self._controller.context_message_limit = value
        except ValueError:
            self._context_limit_edit.setText(
                str(self._controller.context_message_limit)
            )

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

        label = QLabel(
            self.tr(
                "Edit the system prompt used when translation mode is active. "
                "Use {from_lang} and {to_lang} as placeholders for source/target languages."
            )
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        editor = QPlainTextEdit()
        editor.setObjectName("AIPromptEditor")
        editor.setPlainText(
            self._controller.custom_prompt or self._default_translation_prompt()
        )
        editor.setMinimumHeight(200)
        layout.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Reset to Default"))
        reset_btn.clicked.connect(
            lambda: editor.setPlainText(self._default_translation_prompt())
        )
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
        if not self.FIELD_LABELS["src"]:
            self.FIELD_LABELS.update(
                {
                    "src": self.tr("Source"),
                    "trans": self.tr("Translation"),
                    "ff": self.tr("Font"),
                    "fs": self.tr("Size"),
                    "fg": self.tr("Text Color"),
                    "bg": self.tr("BG Color"),
                    "b": self.tr("Bold"),
                    "i": self.tr("Italic"),
                    "a": self.tr("Align"),
                    "sw": self.tr("Stroke"),
                    "ls": self.tr("Line Spacing"),
                }
            )

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

        hint = QLabel(
            self.tr("Select which text block properties the AI can read and modify.")
        )
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
                if key in ("src", "trans"):
                    cb.setChecked(True)
                else:
                    cb.setChecked(False)
                cb.setEnabled(False)
        else:
            # Restore previous whitelist state
            prev = getattr(self, "_field_wl_before_trans", None)
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
                self._settings_widget,
                direction="right",
                width=lambda: self._scroll_area.width(),
            )

        if visible:
            self._sync_settings_from_controller()
            self._hide_welcome_card()
            self._settingsSlide.show()
        else:
            self._sync_settings_to_controller()
            if not self._msg_layout.count():
                self._show_welcome_card()
            self._settingsSlide.hide()

    def _sync_settings_from_controller(self):
        """Read controller + translator config into the settings form."""
        if not hasattr(self, "_trans_mode_cb"):
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

        # Context limit
        ctx_limit = self._controller.context_message_limit if self._controller else 20
        self._context_limit_edit.blockSignals(True)
        self._context_limit_edit.setText(str(ctx_limit))
        self._context_limit_edit.blockSignals(False)

        # Auto compress
        auto_compress = self._controller.auto_compress if self._controller else False
        self._auto_compress_cb.setChecked(auto_compress)

        # Field whitelist
        wl = self._controller.fields_whitelist if self._controller else {"src", "trans"}
        for key, cb in self._field_cbs.items():
            cb.setChecked(key in wl)
            cb.setEnabled(not trans_mode or key in ("src", "trans"))

    def _sync_settings_to_controller(self):
        """Write settings form values back to the controller."""
        if not self._controller:
            return
        if not hasattr(self, "_trans_mode_cb"):
            return  # settings widget not built yet; controller defaults are correct

        # api_config is kept in sync via _on_profile_changed / _sync_profile_to_controller

        # translation_mode
        self._controller.translation_mode = self._trans_mode_cb.isChecked()

        # context limit (also pushed immediately via _on_context_limit_changed)
        try:
            self._controller.context_message_limit = int(
                self._context_limit_edit.text()
            )
        except ValueError:
            pass
        self._controller.auto_compress = self._auto_compress_cb.isChecked()

        # fields_whitelist
        if self._trans_mode_cb.isChecked():
            self._controller.fields_whitelist = {"src", "trans"}
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
        if cfg.get("api_host") and cfg.get("model"):
            return  # already valid
        # First try the profile currently selected in the settings widget
        if hasattr(self, "_settings_profile") and self._settings_profile.count() > 0:
            name = self._settings_profile.currentText()
            if name:
                self._sync_profile_to_controller(name)
                if self._controller.api_config.get(
                    "api_host"
                ) and self._controller.api_config.get("model"):
                    return
        # Fall back to the active translator profile
        active = self._get_active_profile_name()
        if active:
            self._sync_profile_to_controller(active)

    def set_project_loaded(self, loaded: bool):
        """Enable/disable the input bar based on project state."""
        self._input_edit.setEnabled(loaded)
        self._send_btn.setEnabled(
            loaded and bool(self._input_edit.toPlainText().strip())
        )
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
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble_widget)
        self._scroll_to_bottom()

    def _clear_messages(self):
        """Remove all bubble widgets from the message layout."""
        self._bubble_actions.clear()
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
