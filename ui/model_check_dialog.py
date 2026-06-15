"""Model file inventory dialog — collapsible category cards with table body.

Each pipeline stage (detector / OCR / inpainter) gets a ``CategoryCard``
whose header shows a status dot and summary; the collapsible body contains
a ``QTableWidget`` with checkbox, filename, status, and source columns.
All colours adapt to dark/light mode via ``pcfg.darkmode``.
"""

import os.path as osp

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QColor, QDesktopServices, QFont
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils import shared
from utils.config import pcfg
from utils.download_util import check_local_file
from utils.logger import logger as LOGGER

# ── Known modules that carry extra files outside download_file_list ──
_EXTRA_FILES: list[dict] = [
    {
        "module": "ysgyolo",
        "type": "textdetector",
        "file": "data/models/ysgyolo_1.2_OS1.0.pt",
        "source": "",
        "sha256": None,
        "note_key": "ysgyolo_note",
    },
]

# Pipeline display order and labels
_TYPE_ORDER = ["textdetector", "ocr", "inpainter"]
_TYPE_LABEL = {
    "textdetector": "Text Detection",
    "ocr": "OCR",
    "inpainter": "Inpainting",
}

# ── URL helpers ────────────────────────────────────────────────────────


def _to_repo_url(url: str) -> str:
    """Convert a direct download URL to its project/repository page."""
    if not url:
        return url
    if "github.com" in url and "/releases/download/" in url:
        parts = url.split("/")
        try:
            idx = parts.index("releases")
            return "/".join(parts[: idx - 1])
        except (ValueError, IndexError):
            pass
    if "huggingface.co" in url and "/resolve/" in url:
        parts = url.split("/")
        try:
            idx = parts.index("resolve")
            return "/".join(parts[:idx])
        except (ValueError, IndexError):
            pass
    return url


def _shorten_url(url: str, max_len: int = 50) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        tail = path_parts[-1]
        if len(tail) > 30:
            tail = tail[:27] + "..."
        short = f"{domain}/.../{tail}"
    else:
        short = domain
    if len(short) > max_len:
        short = short[: max_len - 3] + "..."
    return short


# ── Model scanning ─────────────────────────────────────────────────────


def _scan_module_models() -> list[dict]:
    """Iterate registries and collect model file entries."""
    from modules import INPAINTERS, OCR, TEXTDETECTORS

    registry_map = {
        "textdetector": TEXTDETECTORS,
        "ocr": OCR,
        "inpainter": INPAINTERS,
    }

    entries: list[dict] = []
    for type_name, registry in registry_map.items():
        for key in registry.module_dict:
            cls = registry.get(key)
            dfl = getattr(cls, "download_file_list", None)
            if not dfl:
                continue
            for dl_entry in dfl:
                url = dl_entry.get("url", "")
                raw_files = dl_entry.get("files") or []
                if isinstance(raw_files, str):
                    raw_files = [raw_files]
                sha_list = dl_entry.get("sha256_pre_calculated") or [None] * len(
                    raw_files
                )
                if isinstance(sha_list, str):
                    sha_list = [sha_list]
                save_files = dl_entry.get("save_files")
                if save_files:
                    if isinstance(save_files, str):
                        save_files = [save_files]
                    final_paths = save_files
                else:
                    final_paths = raw_files
                for i, fpath in enumerate(final_paths):
                    sha = sha_list[i] if i < len(sha_list) else None
                    source = _to_repo_url(url.rstrip("/"))
                    if dl_entry.get("archived_files"):
                        source += f" (archive: {dl_entry['archived_files']})"
                    entries.append({
                        "module": key,
                        "type": type_name,
                        "file": fpath,
                        "source": source,
                        "sha256": sha,
                        "note": "",
                        "note_key": "",
                        "_dl_kwargs": dl_entry,
                    })

    for extra in _EXTRA_FILES:
        entries.append(dict(extra))
    return entries


def _check_entry(entry: dict) -> str:
    """Return ``"installed"`` or ``"missing"``."""
    if entry.get("source_only"):
        return entry.get("_custom_check", lambda: "missing")()
    fpath = entry["file"]
    if not osp.isabs(fpath):
        fpath = osp.join(shared.PROGRAM_PATH, fpath)
    if not osp.exists(fpath):
        return "missing"
    if entry["sha256"]:
        exists, valid, _ = check_local_file(fpath, entry["sha256"])
        return "installed" if valid else "mismatch"
    return "installed"


# ── Theme helpers ─────────────────────────────────────────────────────


def _theme_colors():
    """Return a dict of colours adapting to the current theme."""
    dark = pcfg.darkmode
    return {
        "card_bg": "#2a2a2a" if dark else "#fafafa",
        "card_border": "#444" if dark else "#ddd",
        "title_bg": "#333" if dark else "#f0f0f0",
        "title_text": "#ddd" if dark else "#333",
        "body_bg": "#2a2a2a" if dark else "#fafafa",
        "header_bg": "#3a3a3a" if dark else "#e8e8e8",
        "header_text": "#ddd" if dark else "#333",
        "summary_text": "#999" if dark else "#666",
        "success": "#2ecc71",
        "warning": "#f39c12",
        "danger": "#e74c3c",
        "muted": "#777" if dark else "#95a5a6",
        "link": "#5dade2" if dark else "#3498db",
        "nosource_text": "#777" if dark else "#95a5a6",
        "source_muted": "#666" if dark else "#888",
        "row_even": "#303030" if dark else "#f5f5f5",
        "row_odd": "#2a2a2a" if dark else "#fafafa",
    }


# ── StatusDot ──────────────────────────────────────────────────────────


class StatusDot(QLabel):
    """Colored status indicator dot for a category card header."""

    def __init__(self, dot_state: str = "na", parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(dot_state)

    def set_state(self, state: str):
        tc = _theme_colors()
        _COLOR_MAP = {
            "all_ok": tc["success"],
            "partial": tc["warning"],
            "none": tc["danger"],
            "na": tc["muted"],
        }
        _CHAR_MAP = {
            "all_ok": "●",
            "partial": "◐",
            "none": "⬤",
            "na": "○",
        }
        self.setText(_CHAR_MAP.get(state, "?"))
        color = _COLOR_MAP.get(state, tc["muted"])
        self.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold;")
        self.setToolTip({
            "all_ok": self.tr("All ready"),
            "partial": self.tr("Some files missing"),
            "none": self.tr("No files ready"),
            "na": self.tr("No model files for this category"),
        }.get(state, ""))


# ── CategoryCard ───────────────────────────────────────────────────────


class CategoryCard(QFrame):
    """A collapsible card showing one pipeline stage's model files in a table."""

    def __init__(self, type_name: str, label: str, parent=None):
        super().__init__(parent)
        self.type_name = type_name
        self._expanded = True
        self.entries: list[dict] = []  # set by set_entries()

        tc = _theme_colors()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"CategoryCard {{ border: 1px solid {tc['card_border']}; "
            f"border-radius: 6px; background: {tc['card_bg']}; margin: 2px 0; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar (clickable) ──
        self.title_bar = QFrame()
        self.title_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_bar.setStyleSheet(
            f"QFrame {{ background: {tc['title_bg']}; border-top-left-radius: 6px; "
            f"border-top-right-radius: 6px; padding: 4px 6px; }}"
        )
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(4, 2, 4, 2)
        title_layout.setSpacing(6)

        self.dot = StatusDot()
        title_layout.addWidget(self.dot)

        self.name_label = QLabel(label)
        self.name_label.setStyleSheet(f"color: {tc['title_text']};")
        f = self.name_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.name_label.setFont(f)
        title_layout.addWidget(self.name_label)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet(f"color: {tc['summary_text']};")
        title_layout.addWidget(self.summary_label, 1)

        self.expand_icon = QLabel("▼")
        self.expand_icon.setStyleSheet("color: #999; font-size: 10px;")
        title_layout.addWidget(self.expand_icon)

        layout.addWidget(self.title_bar)

        # ── Table body ──
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            "",
            self.tr("File"),
            self.tr("Status"),
            self.tr("Source / Notes"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        # No internal scroll — parent scroll area handles the full dialog
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.cellClicked.connect(self._on_cell_clicked)
        # subtle alternating row colour via stylesheet
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {tc['body_bg']}; border: none; "
            f"alternate-background-color: {tc['row_even']}; "
            f"color: {tc['title_text']}; }} "
            f"QTableWidget::item {{ border: none; padding: 2px 4px; }} "
            f"QHeaderView::section {{ background: {tc['header_bg']}; "
            f"color: {tc['header_text']}; border: none; "
            f"padding: 3px 6px; font-weight: bold; }}"
        )
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table)

        # Click title bar to toggle
        self.title_bar.mousePressEvent = lambda e: self._toggle()

    def _toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._resize_to_content()
            self.table.setVisible(True)
        else:
            self.table.setMinimumHeight(0)
            self.table.setMaximumHeight(0)
            self.table.setVisible(False)
        self.expand_icon.setText("▲" if self._expanded else "▼")

    def set_entries(self, entries: list[dict]):
        """Populate the card table with model file rows."""
        tc = _theme_colors()
        self.table.setRowCount(0)

        if not entries:
            self.table.setRowCount(1)
            item = QTableWidgetItem(self.tr("No model files declared for this stage."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(QColor(tc["muted"]))
            self.table.setItem(0, 0, item)
            self._update_header("na", 0, 0)
            return

        installed = 0
        self.table.setRowCount(len(entries))

        for idx, entry in enumerate(entries):
            status = entry.get("status", "missing")
            if status == "installed":
                installed += 1

            is_downloadable = bool(entry.get("_dl_kwargs")) and not entry.get("source_only")

            # Column 0 — checkbox
            cb_item = QTableWidgetItem()
            cb_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            cb_item.setCheckState(Qt.CheckState.Unchecked)
            if not is_downloadable:
                cb_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(idx, 0, cb_item)

            # Column 1 — File name
            name_item = QTableWidgetItem(osp.basename(entry["file"]))
            name_item.setToolTip(entry["file"])
            if entry.get("source_only"):
                name_item.setToolTip(
                    self.tr("Auto-downloaded by PaddleOCR on first use")
                )
            self.table.setItem(idx, 1, name_item)

            # Column 2 — Status
            if status == "missing" and not entry["source"]:
                st = self.tr("No source / User-provided")
                colour = tc["nosource_text"]
            else:
                st = {
                    "installed": self.tr("Installed"),
                    "missing": self.tr("Missing"),
                    "mismatch": self.tr("Hash mismatch"),
                }.get(status, status)
                colour = (
                    tc["danger"] if status == "missing"
                    else tc["success"] if status == "installed"
                    else tc["warning"]
                )
            status_item = QTableWidgetItem(st)
            status_item.setForeground(QColor(colour))
            self.table.setItem(idx, 2, status_item)

            # Column 3 — Source / Notes
            note = entry.get("note", "")
            src = entry.get("source", "")
            display_text = note if note else src
            src_item = QTableWidgetItem(display_text)
            if display_text.startswith("http://") or display_text.startswith("https://"):
                src_item.setForeground(QColor(tc["link"]))
                f = src_item.font()
                f.setUnderline(True)
                src_item.setFont(f)
                src_item.setToolTip(display_text)
                src_item.setData(Qt.ItemDataRole.UserRole, display_text)
            elif display_text:
                src_item.setForeground(QColor(tc["source_muted"]))
            self.table.setItem(idx, 3, src_item)

        total = len(entries)
        if installed == 0:
            dot_state = "none"
        elif installed < total:
            dot_state = "partial"
        else:
            dot_state = "all_ok"

        self._update_header(dot_state, installed, total)

        self.entries = entries

        # Size table to show all rows (outer scroll area handles scrolling)
        self._resize_to_content()

        # Auto-collapse if all OK or no entries
        if dot_state in ("all_ok", "na") and self._expanded:
            self._toggle()

    def _update_header(self, dot_state: str, installed: int, total: int):
        self.dot.set_state(dot_state)
        self.summary_label.setText(
            self.tr("{}/{} ready").format(installed, total)
        )

    # ── Cell click handler ──────────────────────────────────────────

    def _resize_to_content(self):
        """Set table height to fit all rows without internal scroll."""
        h = self.table.horizontalHeader().height() + self.table.frameWidth() * 2
        for r in range(self.table.rowCount()):
            h += self.table.rowHeight(r)
        # Cap height so cards don't eat the whole screen for huge tables
        h = min(h, 400)
        self.table.setMinimumHeight(h)
        self.table.setMaximumHeight(h)

    def _on_cell_clicked(self, row: int, col: int):
        """Open URL in browser if the cell contains a clickable link."""
        if col != 3:
            return
        item = self.table.item(row, col)
        if item is None:
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ── Bulk selection helpers ──────────────────────────────────────

    def select_all(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def deselect_all(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)


# ── ModelCheckPanel ────────────────────────────────────────────────────


class ModelCheckPanel(QWidget):
    """Model file inventory with category cards and batch download."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[str, CategoryCard] = {}
        self._build_ui()

    def _build_ui(self):
        # Resolve translatable notes
        for entry in _EXTRA_FILES:
            if entry.get("note_key") == "ysgyolo_note":
                entry["note"] = self.tr(
                    "No public download source. Obtain from community cloud drive."
                )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_widget = QWidget()
        self._cards_layout = QVBoxLayout(scroll_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        # Placeholder shown when no cards exist (before first refresh / empty)
        self._placeholder = QLabel(
            self.tr(
                "No model data yet.\n"
                "Configure modules in Settings, then click Refresh to scan "
                "for model files."
            )
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tc = _theme_colors()
        self._placeholder.setStyleSheet(f"color: {tc['muted']}; font-size: 14px; padding: 40px;")
        self._cards_layout.addWidget(self._placeholder)
        self._cards_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

        # Bottom action bar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.select_all_btn = QPushButton(self.tr("Select All"))
        self.select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton(self.tr("Deselect All"))
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        btn_row.addWidget(self.deselect_all_btn)

        self.download_btn = QPushButton(self.tr("Download Selected"))
        self.download_btn.clicked.connect(self._download_selected)
        btn_row.addWidget(self.download_btn)

        btn_row.addStretch()

        self.refresh_btn = QPushButton(self.tr("Refresh"))
        self.refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self.refresh_btn)

        layout.addLayout(btn_row)

        # Summary
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        # Auto-scan on first open (models are static, no need for manual refresh)
        self._refreshed_once = False

    def showEvent(self, event):
        """Auto-refresh on first show so data is always present."""
        super().showEvent(event)
        if not self._refreshed_once:
            self._refreshed_once = True
            self._refresh()

    # ── Refresh ───────────────────────────────────────────────────────
    # (callable repeatedly to rescan)

    def _refresh(self):
        entries = _scan_module_models()

        for entry in entries:
            entry["status"] = _check_entry(entry)

        # Group by type
        grouped: dict[str, list[dict]] = {}
        for entry in entries:
            grouped.setdefault(entry["type"], []).append(entry)

        def _model_sort_key(e):
            return 0 if e["file"].endswith((".pt", ".ckpt", ".pth")) else 1

        # Rebuild cards
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        total_installed = 0
        total_entries = 0

        for type_name in _TYPE_ORDER:
            group = grouped.get(type_name)
            if not group:
                continue
            group.sort(key=_model_sort_key)

            label = _TYPE_LABEL.get(type_name, type_name)
            card = CategoryCard(type_name, self.tr(label))
            card.set_entries(group)
            self._cards[type_name] = card
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )

            for entry in group:
                total_entries += 1
                if entry["status"] == "installed":
                    total_installed += 1

        # Synchronize column widths so all cards' tables align
        self._sync_column_widths()

        self.summary_label.setText(
            self.tr("{}/{} model files on disk").format(
                total_installed, total_entries
            )
        )

        # Toggle placeholder: hidden when any card exists
        has_content = bool(self._cards)
        self._placeholder.setVisible(not has_content)

    # ── Bulk operations ───────────────────────────────────────────────

    def _select_all(self):
        for card in self._cards.values():
            card.select_all()

    def _deselect_all(self):
        for card in self._cards.values():
            card.deselect_all()

    def _download_selected(self):
        """Download model files for each checked row."""
        from utils.download_util import download_and_check_files

        seen_kwargs: list[int] = []
        for card in self._cards.values():
            for row in range(card.table.rowCount()):
                cb = card.table.item(row, 0)
                if not cb or not (cb.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    continue
                if cb.checkState() != Qt.CheckState.Checked:
                    continue
                if row >= len(card.entries):
                    continue
                entry = card.entries[row]
                dl_kwargs = entry.get("_dl_kwargs")
                if not dl_kwargs or entry.get("source_only"):
                    continue
                kw_id = id(dl_kwargs)
                if kw_id in seen_kwargs:
                    continue
                seen_kwargs.append(kw_id)

                LOGGER.info(
                    f"Downloading model: {entry['module']} — {dl_kwargs.get('url', '?')}"
                )
                ok = download_and_check_files(**dl_kwargs)
                if ok:
                    LOGGER.info(f"Download succeeded: {entry['module']}")
                else:
                    LOGGER.warning(f"Download failed: {entry['module']}")
        self._refresh()

    def _sync_column_widths(self):
        """Make column widths uniform across all category cards."""
        cards = list(self._cards.values())
        if not cards:
            return
        # Let each table resolve its content-based widths first
        for card in cards:
            card.table.resizeColumnsToContents()
        # Find the widest needed width for columns 0, 2, 3
        max_w = [0, 0, 0]
        col_idx = [0, 2, 3]
        for card in cards:
            for i, ci in enumerate(col_idx):
                w = card.table.columnWidth(ci)
                if w > max_w[i]:
                    max_w[i] = w
        # Apply uniform widths to all cards
        for card in cards:
            header = card.table.horizontalHeader()
            for i, ci in enumerate(col_idx):
                header.setSectionResizeMode(ci, QHeaderView.ResizeMode.Fixed)
                card.table.setColumnWidth(ci, max_w[i])
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)


class ModelCheckDialog(QDialog):
    """Dialog wrapper for ModelCheckPanel (backward-compatible)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Model Files"))
        self.setMinimumSize(760, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = ModelCheckPanel(self)
        layout.addWidget(self.panel)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
