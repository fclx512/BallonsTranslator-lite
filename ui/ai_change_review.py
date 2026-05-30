"""
ChangeReviewWindow — standalone non-modal dialog for reviewing AI-proposed changes.

Card-based layout: each text block rendered as a card with source text,
change details, and accept/reject toggles.  Only accepted items are
applied when the user clicks "Apply Changes".
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .ai_chat_model import ChangeItem

# ── Constants ──────────────────────────────────────────────────────────

STYLE_FIELDS = frozenset({"ff", "fs", "fg", "bg", "b", "i", "a", "sw", "ls"})

FIELD_LABELS = {
    "ff": "Font",
    "fs": "Size",
    "fg": "Color",
    "bg": "BG",
    "b": "Bold",
    "i": "Italic",
    "a": "Align",
    "sw": "Stroke",
    "ls": "LineSp",
}


def _format_style_val(v) -> str:
    """Format a style field value for compact display."""
    if v is None:
        return "—"  # em dash
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


# ── _ChangeCard ────────────────────────────────────────────────────────


class _ChangeCard(QWidget):
    """Card rendering all ChangeItems for a single text block.

    Groups multiple field-level changes (translation, font, color, etc.)
    into one card with source text, change details, and accept/reject toggles.
    """

    state_changed = Signal()

    def __init__(self, block_id: str, changes: List[ChangeItem], parent=None):
        super().__init__(parent)
        self.setObjectName("AIReviewCard")
        self._block_id = block_id
        self._changes = changes

        # Analyse what this card contains
        self._has_trans = any(c.field == "trans" for c in changes)
        self._has_style = any(c.field in STYLE_FIELDS for c in changes)
        self._has_old_trans = self._has_trans and any(
            c.field == "trans" and c.old_value for c in changes
        )

        # Resolve source text
        self._src_text = ""
        for c in changes:
            if c.src_text:
                self._src_text = c.src_text
                break
        if not self._src_text:
            for c in changes:
                if c.field == "src" and c.old_value:
                    self._src_text = str(c.old_value)
                    break

        self._build_ui()
        self._sync_buttons_to_state()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_header(root)
        self._build_body(root)
        if self._has_style:
            self._build_footer(root)

    def _build_header(self, root: QVBoxLayout):
        header = QWidget()
        header.setObjectName("AIReviewCardHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 8, 12, 8)

        id_label = QLabel(self._block_id)
        id_label.setObjectName("AIReviewCardId")
        hl.addWidget(id_label)

        hl.addStretch()

        self._accept_btn = QPushButton("✓")
        self._accept_btn.setObjectName("AIReviewCardAccept")
        self._accept_btn.setCheckable(True)
        self._accept_btn.setFixedSize(30, 30)
        self._accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._accept_btn.setToolTip(self.tr("Accept changes for this block"))
        self._accept_btn.clicked.connect(self._on_accept_clicked)
        hl.addWidget(self._accept_btn)

        self._reject_btn = QPushButton("✗")
        self._reject_btn.setObjectName("AIReviewCardReject")
        self._reject_btn.setCheckable(True)
        self._reject_btn.setFixedSize(30, 30)
        self._reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reject_btn.setToolTip(self.tr("Reject changes for this block"))
        self._reject_btn.clicked.connect(self._on_reject_clicked)
        hl.addWidget(self._reject_btn)

        root.addWidget(header)

    def _build_body(self, root: QVBoxLayout):
        body = QWidget()
        body.setObjectName("AIReviewCardBody")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(12)

        # Left: source text
        src_label = QLabel(self._src_text)
        src_label.setObjectName("AIReviewSource")
        src_label.setWordWrap(True)
        src_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        bl.addWidget(src_label, 1)

        # Centre: arrow + operation labels
        arrow_col = QWidget()
        arrow_col.setObjectName("AIReviewArrow")
        al = QVBoxLayout(arrow_col)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.setAlignment(Qt.AlignmentFlag.AlignCenter)

        arrow = QLabel("→")
        arrow.setObjectName("AIReviewArrowIcon")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        al.addWidget(arrow)

        if self._has_trans:
            trans_lbl = QLabel(self.tr("Translation"))
            trans_lbl.setObjectName("AIReviewArrowLabel")
            trans_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            al.addWidget(trans_lbl)

        if self._has_style:
            style_lbl = QLabel(self.tr("Style"))
            style_lbl.setObjectName("AIReviewArrowLabel")
            style_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            al.addWidget(style_lbl)

        al.addStretch()
        bl.addWidget(arrow_col)

        # Right: result
        result_col = QWidget()
        result_col.setObjectName("AIReviewResult")
        rl = QVBoxLayout(result_col)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        if self._has_trans:
            trans_change = next((c for c in self._changes if c.field == "trans"), None)

            if self._has_old_trans and trans_change:
                old_box = QLabel(str(trans_change.old_value))
                old_box.setObjectName("AIReviewOldTranslation")
                old_box.setWordWrap(True)
                old_box.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                rl.addWidget(old_box)

            if trans_change:
                new_lbl = QLabel(str(trans_change.new_value))
                new_lbl.setObjectName("AIReviewNewTranslation")
                new_lbl.setWordWrap(True)
                new_lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                rl.addWidget(new_lbl)
        elif self._has_style:
            hint = QLabel(self.tr("(Style only)"))
            hint.setObjectName("AIReviewStyleHint")
            rl.addWidget(hint)

        rl.addStretch()
        bl.addWidget(result_col, 2)

        root.addWidget(body)

    def _build_footer(self, root: QVBoxLayout):
        footer = QWidget()
        footer.setObjectName("AIReviewCardFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(12, 6, 12, 8)
        fl.setSpacing(4)

        style_changes = [c for c in self._changes if c.field in STYLE_FIELDS]
        field_values = {c.field: c for c in style_changes}

        old_bar = self._make_font_bar(
            {f: field_values[f].old_value for f in field_values},
            self.tr("Old: "),
        )
        fl.addWidget(old_bar)

        new_bar = self._make_font_bar(
            {f: field_values[f].new_value for f in field_values},
            self.tr("New: "),
        )
        fl.addWidget(new_bar)

        root.addWidget(footer)

    def _make_font_bar(self, field_values: dict, prefix: str) -> QWidget:
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        prefix_lbl = QLabel(prefix)
        prefix_lbl.setObjectName("AIReviewFontBarLabel")
        bl.addWidget(prefix_lbl)

        for field in ("ff", "fs", "fg", "bg", "b", "i", "a", "sw", "ls"):
            if field not in field_values:
                continue
            val = field_values[field]
            label = FIELD_LABELS.get(field, field)

            if field in ("fg", "bg"):
                swatch = QLabel()
                swatch.setFixedSize(12, 12)
                swatch.setStyleSheet(
                    f"background-color: {val}; border: 1px solid #888; border-radius: 2px;"
                )
                bl.addWidget(swatch)

            text = QLabel(f"{label}: {_format_style_val(val)}")
            text.setObjectName("AIReviewFontBarValue")
            bl.addWidget(text)

        bl.addStretch()
        return bar

    # ── State management ──────────────────────────────────────────

    def _on_accept_clicked(self, checked: bool):
        self._reject_btn.blockSignals(True)
        self._reject_btn.setChecked(False)
        self._reject_btn.blockSignals(False)

        if checked:
            for c in self._changes:
                c.accepted = True
            self._apply_state("true")
        else:
            for c in self._changes:
                c.accepted = None
            self._apply_state("pending")
        self.state_changed.emit()

    def _on_reject_clicked(self, checked: bool):
        self._accept_btn.blockSignals(True)
        self._accept_btn.setChecked(False)
        self._accept_btn.blockSignals(False)

        if checked:
            for c in self._changes:
                c.accepted = False
            self._apply_state("false")
        else:
            for c in self._changes:
                c.accepted = None
            self._apply_state("pending")
        self.state_changed.emit()

    def _sync_buttons_to_state(self):
        """Align button visuals with current ChangeItem state."""
        state = self._changes[0].accepted if self._changes else None
        if state is True:
            self._apply_state("true")
            self._accept_btn.blockSignals(True)
            self._accept_btn.setChecked(True)
            self._accept_btn.blockSignals(False)
            self._reject_btn.blockSignals(True)
            self._reject_btn.setChecked(False)
            self._reject_btn.blockSignals(False)
        elif state is False:
            self._apply_state("false")
            self._accept_btn.blockSignals(True)
            self._accept_btn.setChecked(False)
            self._accept_btn.blockSignals(False)
            self._reject_btn.blockSignals(True)
            self._reject_btn.setChecked(True)
            self._reject_btn.blockSignals(False)
        else:
            self._apply_state("pending")
            self._accept_btn.blockSignals(True)
            self._accept_btn.setChecked(False)
            self._accept_btn.blockSignals(False)
            self._reject_btn.blockSignals(True)
            self._reject_btn.setChecked(False)
            self._reject_btn.blockSignals(False)

    def _apply_state(self, state: str):
        self.setProperty("accepted", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_accepted(self):
        if not self._accept_btn.isChecked():
            self._accept_btn.setChecked(True)
            self._on_accept_clicked(True)

    def set_rejected(self):
        if not self._reject_btn.isChecked():
            self._reject_btn.setChecked(True)
            self._on_reject_clicked(True)


# ── ChangeReviewWindow ─────────────────────────────────────────────────


class ChangeReviewWindow(QDialog):
    """Standalone, non-modal dialog for reviewing AI-proposed project changes.

    Changes are grouped by page and rendered as cards (one per text block).
    Each card supports three-way toggle: pending / accepted / rejected.
    Only accepted items are applied.
    """

    apply_changes_requested = Signal(list)  # list[ChangeItem] with accepted=True

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AIChangeReviewWindow")
        self.setWindowTitle(self.tr("AI Change Review"))
        self.setMinimumSize(850, 520)
        self.resize(1000, 650)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        # Internal state
        self._changes: List[ChangeItem] = []
        self._page_groups: OrderedDict[str, List[ChangeItem]] = OrderedDict()
        self._current_page_idx: int = 0
        self._page_ids: List[str] = []
        self._cards: List[_ChangeCard] = []
        self._message_index: int = 0
        self._dirty: bool = False

        self._build_ui()

    # ── Public API ──────────────────────────────────────────────────────

    def load_changes(self, changes: List[ChangeItem], message_index: int = 0):
        """Load a new batch of changes.  Replaces any existing data."""
        self._changes = changes
        self._message_index = message_index
        self._dirty = False
        self._group_by_page()
        self._current_page_idx = 0
        self._refresh_navigation()
        self._populate_cards()
        self._update_stats()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Navigation bar ---
        nav = QWidget()
        nav.setObjectName("AIReviewNavBar")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(12, 6, 12, 6)
        nl.setSpacing(8)

        self._prev_btn = QPushButton(self.tr("◀  Prev"))
        self._prev_btn.setObjectName("AIReviewNavBtn")
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(
            lambda: self._go_to_page(self._current_page_idx - 1)
        )
        nl.addWidget(self._prev_btn)

        self._page_info_label = QLabel()
        self._page_info_label.setObjectName("AIReviewNavInfo")
        nl.addWidget(self._page_info_label)

        self._next_btn = QPushButton(self.tr("Next  ▶"))
        self._next_btn.setObjectName("AIReviewNavBtn")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(
            lambda: self._go_to_page(self._current_page_idx + 1)
        )
        nl.addWidget(self._next_btn)

        nl.addStretch()

        accept_page_btn = QPushButton(self.tr("Accept Page"))
        accept_page_btn.setObjectName("AIReviewAcceptAll")
        accept_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        accept_page_btn.clicked.connect(self._accept_page)
        nl.addWidget(accept_page_btn)

        reject_page_btn = QPushButton(self.tr("Reject Page"))
        reject_page_btn.setObjectName("AIReviewAcceptAll")
        reject_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reject_page_btn.clicked.connect(self._reject_page)
        nl.addWidget(reject_page_btn)

        root.addWidget(nav)

        # --- Card scroll area ---
        scroll = QScrollArea()
        scroll.setObjectName("AIReviewScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._card_container = QWidget()
        self._card_container.setObjectName("AIReviewCardContainer")
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(12, 8, 12, 8)
        self._card_layout.setSpacing(8)

        scroll.setWidget(self._card_container)
        root.addWidget(scroll, 1)

        # --- Footer ---
        footer = QWidget()
        footer.setObjectName("AIReviewFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 8, 12, 8)

        self._stats_label = QLabel()
        self._stats_label.setObjectName("AIReviewStats")
        fl.addWidget(self._stats_label)

        fl.addStretch()

        self._apply_btn = QPushButton(self.tr("Apply Changes"))
        self._apply_btn.setObjectName("AIReviewApplyBtn")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        fl.addWidget(self._apply_btn)

        root.addWidget(footer)

    # ── Page grouping & navigation ──────────────────────────────────────

    def _group_by_page(self):
        self._page_groups = OrderedDict()
        for c in self._changes:
            pid = c.block_id.split(":")[0]
            self._page_groups.setdefault(pid, []).append(c)
        self._page_ids = list(self._page_groups.keys())

    def _current_page_changes(self) -> List[ChangeItem]:
        if not self._page_ids:
            return []
        return self._page_groups[self._page_ids[self._current_page_idx]]

    @staticmethod
    def _group_by_block(page_changes: List[ChangeItem]) -> OrderedDict:
        """Group changes within a page by block_id."""
        blocks: OrderedDict[str, List[ChangeItem]] = OrderedDict()
        for c in page_changes:
            blocks.setdefault(c.block_id, []).append(c)
        return blocks

    def _refresh_navigation(self):
        n_pages = len(self._page_ids)
        self._prev_btn.setEnabled(self._current_page_idx > 0)
        self._next_btn.setEnabled(self._current_page_idx < n_pages - 1)
        self._update_page_info_label()

    def _update_page_info_label(self):
        if not self._page_ids:
            self._page_info_label.setText("")
            return
        pid = self._page_ids[self._current_page_idx]
        cnt = len(self._page_groups[pid])
        page_num = self._current_page_idx + 1
        total = len(self._page_ids)
        self._page_info_label.setText(
            self.tr("Page {pid} — {cnt} change(s)  ({page_num}/{total})").format(
                pid=pid, cnt=cnt, page_num=page_num, total=total
            )
        )

    def _go_to_page(self, idx: int):
        if 0 <= idx < len(self._page_ids):
            self._current_page_idx = idx
            self._refresh_navigation()
            self._populate_cards()
            self._update_stats()

    # ── Card population ─────────────────────────────────────────────────

    def _clear_cards(self):
        layout = self._card_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()

    def _populate_cards(self):
        self._clear_cards()

        page_changes = self._current_page_changes()
        blocks = self._group_by_block(page_changes)

        for block_id, block_changes in blocks.items():
            card = _ChangeCard(block_id, block_changes, self._card_container)
            card.state_changed.connect(self._on_card_state_changed)
            self._cards.append(card)
            self._card_layout.addWidget(card)

        self._card_layout.addStretch()

    def _on_card_state_changed(self):
        self._dirty = True
        self._update_stats()

    # ── Batch operations ────────────────────────────────────────────────

    def _accept_all(self):
        for c in self._changes:
            c.accepted = True
        for card in self._cards:
            card._sync_buttons_to_state()
        self._dirty = True
        self._update_stats()

    def _reject_all(self):
        for c in self._changes:
            c.accepted = False
        for card in self._cards:
            card._sync_buttons_to_state()
        self._dirty = True
        self._update_stats()

    def _accept_page(self):
        for card in self._cards:
            card.set_accepted()
        self._dirty = True
        self._update_stats()

    def _reject_page(self):
        for card in self._cards:
            card.set_rejected()
        self._dirty = True
        self._update_stats()

    # ── Stats & apply ───────────────────────────────────────────────────

    def _update_stats(self):
        accepted_n = sum(1 for c in self._changes if c.accepted is True)
        rejected_n = sum(1 for c in self._changes if c.accepted is False)
        total = len(self._changes)
        self._stats_label.setText(
            self.tr("Accepted: {a} / Rejected: {r} / Total: {t}").format(
                a=accepted_n, r=rejected_n, t=total
            )
        )
        self._apply_btn.setEnabled(accepted_n > 0)

    def _on_apply(self):
        accepted = [c for c in self._changes if c.accepted is True]
        if accepted:
            self.apply_changes_requested.emit(accepted)
            self._dirty = False
        self.hide()

    # ── Window lifecycle ────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._dirty:
            accepted_n = sum(1 for c in self._changes if c.accepted is True)
            if accepted_n > 0:
                result = QMessageBox.question(
                    self,
                    self.tr("Unapplied Changes"),
                    self.tr(
                        "You have {n} accepted change(s) that have not been applied.\n\n"
                        "Apply them now?"
                    ).format(n=accepted_n),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if result == QMessageBox.StandardButton.Yes:
                    self._on_apply()
                    event.accept()
                    return
                elif result == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
        event.accept()
        self.hide()
