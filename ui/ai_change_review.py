"""
ChangeReviewWindow — standalone non-modal dialog for reviewing AI-proposed changes.

Displays changes one page at a time.  Only an Accept button per row —
unaccepted items are implicitly rejected when Apply is clicked.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .ai_chat_model import ChangeItem


class _ReviewTable(QTableWidget):
    """QTableWidget that accounts for cell-widget heights in row sizing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recalculating = False
        self.horizontalHeader().sectionResized.connect(self._recalc_row_heights)

    def sizeHintForRow(self, row: int) -> int:
        height = super().sizeHintForRow(row)
        for col in range(self.columnCount()):
            widget = self.cellWidget(row, col)
            if widget is None:
                continue
            col_width = self.columnWidth(col)
            old_max = widget.maximumWidth()
            widget.setMaximumWidth(col_width)
            widget.ensurePolished()
            hint_h = widget.sizeHint().height()
            widget.setMaximumWidth(old_max)
            height = max(height, hint_h)
        return height

    def _recalc_row_heights(self):
        if self._recalculating:
            return
        self._recalculating = True
        self.resizeRowsToContents()
        self._recalculating = False


class ChangeReviewWindow(QDialog):
    """Standalone, non-modal dialog for reviewing AI-proposed project changes.

    Changes are grouped by page and shown one page at a time in a table.
    An Accept button per row toggles approval; unaccepted items are
    implicitly rejected when changes are applied.
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

        # ── Internal state ──────────────────────────────────────────
        self._changes: List[ChangeItem] = []
        self._page_groups: OrderedDict[str, List[ChangeItem]] = OrderedDict()
        self._current_page_idx: int = 0
        self._page_ids: List[str] = []
        self._row_data: List[Dict[str, Any]] = []  # {change, accept_btn}
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
        self._populate_table()
        self._update_stats()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        """Assemble the full window layout."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Header bar ---
        header = QWidget()
        header.setObjectName("AIReviewHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 8, 8, 8)

        title = QLabel(self.tr("AI Change Review"))
        title.setObjectName("AIReviewTitle")
        hl.addWidget(title)

        page_label = QLabel()
        page_label.setObjectName("AIReviewNavInfo")
        self._header_page_label = page_label
        hl.addWidget(page_label)

        hl.addStretch()

        close_btn = QPushButton("×")
        close_btn.setObjectName("AIReviewCloseBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        hl.addWidget(close_btn)

        root.addWidget(header)

        # --- Page navigation bar ---
        nav = QWidget()
        nav.setObjectName("AIReviewNavBar")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(12, 6, 12, 6)
        nl.setSpacing(8)

        self._prev_btn = QPushButton(self.tr("◀  Prev"))
        self._prev_btn.setObjectName("AIReviewNavBtn")
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(lambda: self._go_to_page(self._current_page_idx - 1))
        nl.addWidget(self._prev_btn)

        self._page_combo = QComboBox()
        self._page_combo.setObjectName("AIReviewFieldFilter")
        self._page_combo.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._page_combo.currentIndexChanged.connect(self._on_page_combo_changed)
        nl.addWidget(self._page_combo)

        self._next_btn = QPushButton(self.tr("Next  ▶"))
        self._next_btn.setObjectName("AIReviewNavBtn")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(lambda: self._go_to_page(self._current_page_idx + 1))
        nl.addWidget(self._next_btn)

        nl.addSpacing(24)

        goto_label = QLabel(self.tr("Go to page:"))
        goto_label.setObjectName("AIReviewNavInfo")
        nl.addWidget(goto_label)

        self._goto_spin = QSpinBox()
        self._goto_spin.setObjectName("AIReviewFieldFilter")
        self._goto_spin.setMinimum(1)
        self._goto_spin.setMaximum(1)
        self._goto_spin.setFixedWidth(64)
        self._goto_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        nl.addWidget(self._goto_spin)

        goto_btn = QPushButton(self.tr("Go"))
        goto_btn.setObjectName("AIReviewNavBtn")
        goto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        goto_btn.clicked.connect(self._on_goto_clicked)
        nl.addWidget(goto_btn)

        nl.addStretch()
        root.addWidget(nav)

        # --- Table (4 columns: Block | Source | Old→New | Accept) ---
        self._table = _ReviewTable()
        self._table.setObjectName("AIReviewTable")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            self.tr("Block"),
            self.tr("Source"),
            self.tr("Old  →  New"),
            "",  # accept
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)
        self._table.setWordWrap(True)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.horizontalHeader().setStretchLastSection(False)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)   # Block
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive) # Source
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch) # Old→New
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)   # Accept
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 280)
        self._table.setColumnWidth(3, 40)

        root.addWidget(self._table, 1)

        # --- Batch action bar ---
        batch_bar = QWidget()
        batch_bar.setObjectName("AIReviewActions")
        bl = QHBoxLayout(batch_bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(8)

        accept_all_btn = QPushButton(self.tr("Accept All"))
        accept_all_btn.setObjectName("AIReviewAcceptAll")
        accept_all_btn.clicked.connect(self._accept_all)
        bl.addWidget(accept_all_btn)

        accept_page_btn = QPushButton(self.tr("Accept Page"))
        accept_page_btn.setObjectName("AIReviewAcceptAll")
        accept_page_btn.clicked.connect(self._accept_page)
        bl.addWidget(accept_page_btn)

        bl.addStretch()
        root.addWidget(batch_bar)

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

    def _refresh_navigation(self):
        """Rebuild the page combo and update prev/next."""
        n_pages = len(self._page_ids)

        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for pid in self._page_ids:
            cnt = len(self._page_groups[pid])
            label = self.tr("Page {pid} ({cnt} changes)").format(pid=pid, cnt=cnt)
            self._page_combo.addItem(label)
        self._page_combo.setCurrentIndex(self._current_page_idx)
        self._page_combo.blockSignals(False)

        self._prev_btn.setEnabled(self._current_page_idx > 0)
        self._next_btn.setEnabled(self._current_page_idx < n_pages - 1)

        self._goto_spin.setMaximum(max(n_pages, 1))
        self._goto_spin.setValue(self._current_page_idx + 1)

        self._update_header_label()

    def _update_header_label(self):
        if not self._page_ids:
            self._header_page_label.setText("")
            return
        page_num = self._current_page_idx + 1
        total = len(self._page_ids)
        pid = self._page_ids[self._current_page_idx]
        self._header_page_label.setText(
            f"  —  {self.tr('Page')} {pid}  ({page_num}/{total})"
        )

    def _go_to_page(self, idx: int):
        if 0 <= idx < len(self._page_ids):
            self._current_page_idx = idx
            self._refresh_navigation()
            self._populate_table()
            self._update_stats()

    def _on_page_combo_changed(self, idx: int):
        if idx >= 0 and idx != self._current_page_idx:
            self._go_to_page(idx)

    def _on_goto_clicked(self):
        target = self._goto_spin.value() - 1  # 1-based → 0-based
        if 0 <= target < len(self._page_ids):
            self._go_to_page(target)

    # ── Table population ────────────────────────────────────────────────

    def _populate_table(self):
        """Fill the table with changes for the current page."""
        page_changes = self._current_page_changes()
        self._row_data.clear()

        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(page_changes))

        for i, change in enumerate(page_changes):
            self._fill_row(i, change)

        self._table.setUpdatesEnabled(True)
        self._table.resizeRowsToContents()

    def _fill_row(self, row_idx: int, change: ChangeItem):
        # Block ID
        id_item = QTableWidgetItem(change.block_id)
        id_item.setToolTip(change.block_id)
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(row_idx, 0, id_item)

        # Source text — use cell widget so text can word-wrap
        src = change.src_text or ""
        src_widget = QLabel(src)
        src_widget.setObjectName("AIReviewSrcValue")
        src_widget.setWordWrap(True)
        src_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._table.setCellWidget(row_idx, 1, src_widget)

        # Old → New (stacked vertically) — field label placed beside the arrow
        diff_widget = QWidget()
        diff_widget.setObjectName("AIReviewDiff")
        dl = QVBoxLayout(diff_widget)
        dl.setContentsMargins(6, 4, 6, 4)
        dl.setSpacing(2)

        old_lbl = QLabel(str(change.old_value))
        old_lbl.setObjectName("AIReviewOldValue")
        old_lbl.setWordWrap(True)
        old_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        old_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        dl.addWidget(old_lbl)

        # Arrow row:  ↓  (field)
        arrow_row = QWidget()
        ar = QHBoxLayout(arrow_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(4)

        arrow_lbl = QLabel("↓")
        arrow_lbl.setObjectName("AIReviewFieldLabel")
        ar.addWidget(arrow_lbl)

        field_lbl = QLabel(change.field)
        field_lbl.setObjectName("AIReviewFieldLabel")
        ar.addWidget(field_lbl)

        ar.addStretch()
        dl.addWidget(arrow_row)

        new_lbl = QLabel(str(change.new_value))
        new_lbl.setObjectName("AIReviewNewValue")
        new_lbl.setWordWrap(True)
        new_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        new_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        dl.addWidget(new_lbl)

        self._table.setCellWidget(row_idx, 2, diff_widget)

        # Accept button only
        accept_btn = QPushButton("✓")
        accept_btn.setObjectName("AIReviewAccept")
        accept_btn.setCheckable(True)
        accept_btn.setFixedSize(26, 26)
        accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        accept_btn.setToolTip(self.tr("Accept this change"))

        if change.accepted is True:
            accept_btn.setChecked(True)

        accept_btn.toggled.connect(
            lambda checked, c=change: self._on_accept_toggled(c, checked)
        )

        self._table.setCellWidget(row_idx, 3, accept_btn)

        self._row_data.append({
            "change": change,
            "accept_btn": accept_btn,
        })

    def _on_accept_toggled(self, change: ChangeItem, checked: bool):
        change.accepted = True if checked else None
        if checked:
            self._dirty = True
        self._update_stats()

    # ── Batch operations ────────────────────────────────────────────────

    def _accept_all(self):
        for rd in self._row_data:
            rd["change"].accepted = True
            rd["accept_btn"].blockSignals(True)
            rd["accept_btn"].setChecked(True)
            rd["accept_btn"].blockSignals(False)
        for changes in self._page_groups.values():
            for c in changes:
                c.accepted = True
        self._dirty = True
        self._update_stats()

    def _accept_page(self):
        for rd in self._row_data:
            rd["change"].accepted = True
            rd["accept_btn"].blockSignals(True)
            rd["accept_btn"].setChecked(True)
            rd["accept_btn"].blockSignals(False)
        self._dirty = True
        self._update_stats()

    # ── Stats & apply ───────────────────────────────────────────────────

    def _update_stats(self):
        accepted_n = sum(1 for c in self._changes if c.accepted is True)
        total = len(self._changes)
        self._stats_label.setText(
            self.tr("Accepted: {n} / {total}").format(n=accepted_n, total=total)
        )
        self._apply_btn.setEnabled(accepted_n > 0)
        self._update_header_label()

    def _on_apply(self):
        accepted = [c for c in self._changes if c.accepted is True]
        if accepted:
            self.apply_changes_requested.emit(accepted)
            self._dirty = False
        self.hide()

    # ── Window lifecycle ────────────────────────────────────────────────

    def closeEvent(self, event):
        """If there are unapplied accepted changes, ask before closing."""
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
