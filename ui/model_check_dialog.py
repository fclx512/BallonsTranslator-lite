"""Model file inventory dialog.

Scans all registered modules (detector / OCR / inpainter / translator)
for their declared model files and reports whether each file is present
on disk.  Models are grouped by type, with per-category availability
warnings and clickable download links.

YAGNI note: this scans ``download_file_list`` on each registered module
*class* — it never instantiates a module or loads a model into memory.
"""

import os.path as osp
from pathlib import Path

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QColor, QDesktopServices, QFont
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from utils import shared
from utils.download_util import check_local_file
from utils.logger import logger as LOGGER

# ── Known modules that carry extra files outside download_file_list ──
# Translations for labels are applied at render time via self.tr().

_EXTRA_FILES: list[dict] = [
    {
        "module": "font_detect",
        "type": "utility",
        "file": "data/models/YuzuMarker.FontDetection/name=4x-epoch=18-step=368676.ckpt",
        "source": "https://huggingface.co/gyrojeff/YuzuMarker.FontDetection",
        "sha256": "4544568829be10a98653a2c965f82fb229d5e02146578ccb3402518d9c022b1a",
        "note": "",
    },
    {
        "module": "ysgyolo",
        "type": "textdetector",
        "file": "data/models/ysgyolo_1.2_OS1.0.pt",
        "source": "",
        "sha256": None,
        # note is set in _build_ui to allow translation
        "note_key": "ysgyolo_note",
    },
]

_TYPE_LABEL = {
    "textdetector": "Text Detection",
    "ocr": "OCR",
    "inpainter": "Inpainting",
    "translator": "Translator",
    "utility": "Utility",
}

# Pipeline display order for type groups
_TYPE_ORDER = ["textdetector", "ocr", "inpainter", "translator", "utility"]

# ── URL shortening ──────────────────────────────────────────────────


def _to_repo_url(url: str) -> str:
    """Convert a direct download URL to its project/repository page."""
    if not url:
        return url
    # GitHub release assets → repo root
    # e.g. https://github.com/owner/repo/releases/download/v1.0/file → https://github.com/owner/repo
    if "github.com" in url and "/releases/download/" in url:
        parts = url.split("/")
        try:
            idx = parts.index("releases")
            return "/".join(parts[: idx - 1])  # owner/repo level
        except (ValueError, IndexError):
            pass
    # HuggingFace direct resolve → model page
    # e.g. https://huggingface.co/owner/repo/resolve/main/file → https://huggingface.co/owner/repo
    if "huggingface.co" in url and "/resolve/" in url:
        parts = url.split("/")
        try:
            idx = parts.index("resolve")
            return "/".join(parts[:idx])
        except (ValueError, IndexError):
            pass
    # Already a repo page or unknown pattern — return as-is
    return url


def _shorten_url(url: str, max_len: int = 50) -> str:
    """Truncate a URL for display, keeping the domain readable."""
    if not url:
        return ""
    # Try to extract a meaningful short form
    from urllib.parse import urlparse

    parsed = urlparse(url)
    # Show domain + last path segment
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


# ── Colours ──────────────────────────────────────────────────────────

_COL_INSTALLED = QColor("#27ae60")
_COL_MISSING = QColor("#e74c3c")
_COL_NOSOURCE = QColor("#95a5a6")
_COL_HEADER_BG = QColor("#2c3e50")
_COL_HEADER_TEXT = QColor("#ecf0f1")
_COL_LINK = QColor("#3498db")


def _scan_module_models() -> list[dict]:
    """Iterate registries and collect model file entries."""
    from modules import INPAINTERS, OCR, TEXTDETECTORS, TRANSLATORS

    registry_map = {
        "textdetector": TEXTDETECTORS,
        "ocr": OCR,
        "inpainter": INPAINTERS,
        "translator": TRANSLATORS,
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
                # save_files overrides raw_files as the final on-disk paths
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
                    entries.append(
                        {
                            "module": key,
                            "type": type_name,
                            "file": fpath,
                            "source": source,
                            "sha256": sha,
                            "note": "",
                            "note_key": "",
                        }
                    )

    # Extra files not declared via download_file_list
    for extra in _EXTRA_FILES:
        entries.append(dict(extra))

    return entries


def _check_entry(entry: dict) -> str:
    """Return ``"installed"`` or ``"missing"``."""
    fpath = osp.join(shared.PROGRAM_PATH, entry["file"])
    if not osp.exists(fpath):
        return "missing"
    if entry["sha256"]:
        exists, valid, _ = check_local_file(fpath, entry["sha256"])
        return "installed" if valid else "mismatch"
    return "installed"


# ── Non-editable, non-focusable header item ─────────────────────────


class _HeaderRowItem(QTableWidgetItem):
    """An item that looks like a section header — spans columns, bold,
    distinct background."""

    def __init__(self, text: str, colspan: int = 4):
        super().__init__(text)
        self._colspan = colspan
        self.setFlags(Qt.ItemFlag.NoItemFlags)
        f = QFont()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.setFont(f)
        self.setBackground(_COL_HEADER_BG)
        self.setForeground(_COL_HEADER_TEXT)


# ── Dialog ───────────────────────────────────────────────────────────


class ModelCheckDialog(QDialog):
    """Model file inventory — what's installed, what's missing, and where
    to get it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Model Files"))
        self.setMinimumSize(760, 500)
        self._entries: list[dict] = []
        self._build_ui()
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Resolve translatable notes
        for entry in _EXTRA_FILES:
            if entry.get("note_key") == "ysgyolo_note":
                entry["note"] = self.tr(
                    "No public download source. Obtain from community cloud drive."
                )

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.tr("Model"),
            self.tr("Category"),
            self.tr("Status"),
            self.tr("Source / Notes"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # Click on a source cell → open URL in browser
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table, 1)

        # Per-category availability warnings
        self.warnings_box = QVBoxLayout()
        layout.addLayout(self.warnings_box)

        # Summary
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        # Buttons
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton(self.tr("Refresh"))
        refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ── Refresh ───────────────────────────────────────────────────────

    def _refresh(self):
        self.table.setRowCount(0)
        self._entries = _scan_module_models()

        # Check each entry
        for entry in self._entries:
            entry["status"] = _check_entry(entry)

        # Group by type
        grouped: dict[str, list[dict]] = {}
        for entry in self._entries:
            grouped.setdefault(entry["type"], []).append(entry)

        # Sort groups by pipeline order; sort within each group:
        # main model files (.pt / .ckpt) first, utility or config files after
        def _model_sort_key(e):
            return (0 if e["file"].endswith((".pt", ".ckpt", ".pth")) else 1)

        row = 0
        total_installed = 0
        total_entries = 0
        for type_name in _TYPE_ORDER:
            group = grouped.get(type_name)
            if not group:
                continue
            group.sort(key=_model_sort_key)

            # ── Header row ──
            label = _TYPE_LABEL.get(type_name, type_name)
            # Translate the label
            tr_label = self.tr(label)
            self.table.insertRow(row)
            header_item = _HeaderRowItem(tr_label)
            self.table.setItem(row, 0, header_item)
            # Span all columns via empty items (QTableWidget doesn't
            # natively support colspan on items, so we just fill the row)
            for col in range(1, 4):
                empty = QTableWidgetItem("")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setBackground(_COL_HEADER_BG)
                self.table.setItem(row, col, empty)
            self.table.setRowHeight(row, 30)
            row += 1

            # ── Model rows ──
            for entry in group:
                total_entries += 1
                if entry["status"] == "installed":
                    total_installed += 1

                self.table.insertRow(row)

                # Name — filename only
                name_item = QTableWidgetItem(osp.basename(entry["file"]))
                name_item.setToolTip(entry["file"])
                self.table.setItem(row, 0, name_item)

                # Category — sub-type within group (module key) in lighter
                # text so it doesn't compete with the header
                cat_item = QTableWidgetItem(entry["module"])
                cat_item.setForeground(QColor("#7f8c8d"))
                self.table.setItem(row, 1, cat_item)

                # Status
                if entry["status"] == "missing" and not entry["source"]:
                    st = self.tr("No source / User-provided")
                    colour = _COL_NOSOURCE
                else:
                    st = {
                        "installed": self.tr("Installed"),
                        "missing": self.tr("Missing"),
                        "mismatch": self.tr("Hash mismatch"),
                    }.get(entry["status"], entry["status"])
                    colour = (
                        _COL_MISSING if entry["status"] == "missing"
                        else _COL_INSTALLED if entry["status"] == "installed"
                        else QColor("#f39c12")
                    )
                status_item = QTableWidgetItem(st)
                status_item.setForeground(colour)
                self.table.setItem(row, 2, status_item)

                # Source / Notes — clickable if it's a URL
                note = entry.get("note", "")
                src = entry.get("source", "")
                text = note if note else src
                src_item = QTableWidgetItem(text)
                if text.startswith("http://") or text.startswith("https://"):
                    src_item.setForeground(_COL_LINK)
                    # Underline to look like a link
                    f = src_item.font()
                    f.setUnderline(True)
                    src_item.setFont(f)
                    src_item.setToolTip(text)  # full URL as tooltip
                    # Store full URL so _on_cell_clicked can open it
                    src_item.setData(Qt.ItemDataRole.UserRole, text)
                self.table.setItem(row, 3, src_item)

                row += 1

            # ── Spacer row after group (just for visual separation) ──
            self.table.insertRow(row)
            spacer = QTableWidgetItem("")
            spacer.setFlags(Qt.ItemFlag.NoItemFlags)
            spacer.setSizeHint(spacer.sizeHint())  # keep it minimal
            self.table.setItem(row, 0, spacer)
            row += 1

        # Summary
        self.summary_label.setText(
            self.tr("{installed}/{total} model files on disk").format(
                installed=total_installed, total=total_entries
            )
        )

        # ── Per-category availability warnings ──
        self._update_warnings(grouped)

    def _update_warnings(self, grouped: dict[str, list[dict]]):
        """Show informative per-category availability notes."""
        # Clear old warnings
        while self.warnings_box.count():
            w = self.warnings_box.takeAt(0)
            if w.widget():
                w.widget().deleteLater()

        for type_name in _TYPE_ORDER:
            group = grouped.get(type_name)
            if not group:
                continue

            installed = sum(1 for e in group if e["status"] == "installed")
            total = len(group)
            label = _TYPE_LABEL.get(type_name, type_name)
            tr_label = self.tr(label)

            if installed == 0:
                msg = self.tr(
                    "No {category} models installed — {desc}"
                ).format(
                    category=tr_label,
                    desc={
                        "textdetector": self.tr("text detection requires a local model file."),
                        "ocr": self.tr("OCR requires a local model file."),
                        "inpainter": self.tr("inpainting requires a local model file (no online service available)."),
                        "translator": self.tr("most translators use remote APIs and don't need local models."),
                        "utility": self.tr("utility models are optional extras."),
                    }.get(type_name, ""),
                )
                colour = "#e67e22"  # orange — informative, not alarming
            elif installed < total:
                msg = self.tr(
                    "{installed}/{total} {category} models ready, {missing} missing"
                ).format(installed=installed, total=total,
                         category=tr_label, missing=total - installed)
                colour = "#f39c12"  # yellow
            else:
                msg = self.tr("All {category} models ready").format(category=tr_label)
                colour = "#27ae60"  # green

            w = QLabel(msg)
            w.setStyleSheet(f"color: {colour}; font-size: 12px; padding: 1px 0;")
            self.warnings_box.addWidget(w)

    # ── Cell click handler ─────────────────────────────────────────────

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
