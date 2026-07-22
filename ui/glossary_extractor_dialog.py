"""
Glossary extraction dialog — extract term pairs from an existing project.

Two extraction modes:
• **Frequency** (fast, no LLM) — counts repeated source text and promotes
  frequently occurring terms to glossary entries.
• **LLM Extraction** — sends the project's source/translation pairs to
  an LLM for semantic term identification.

The dialog opens from the Run dialog's glossary section.  When the user
saves the extracted glossary, the file path is set as the active glossary
so it can be used immediately.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qtpy.QtCore import QThread, Signal
from qtpy.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from utils.proj_imgtrans import ProjImgTrans

from modules.context.glossary import GlossaryEntry
from modules.glossary_extractor import (
    TARGET_LANGUAGES,
    extract_by_frequency,
    extract_by_llm,
    save_glossary_json,
)
from ui.custom_widget import ConfigComboBox
from utils.config import pcfg
from utils.profile_manager import find_profile, get_profile_names

logger = logging.getLogger("glossary_extractor_dialog")

# ── Worker thread for non-blocking extraction ────────────────────────────


class _ExtractWorker(QThread):
    """Run glossary extraction in a background thread.

    Emits ``progress`` (status string) and ``finished`` (entries tuple, or
    ``None`` on failure).  The dialog remains responsive during extraction.
    """

    progress = Signal(str)
    finished = Signal(object)  # Tuple[GlossaryEntry, ...] or None

    def __init__(
        self,
        proj: "ProjImgTrans",
        mode: str,
        profile_name: str,
        target_language: str = "简体中文",
        parent=None,
    ):
        super().__init__(parent)
        self._proj = proj
        self._mode = mode
        self._profile_name = profile_name
        self._target_language = target_language

    def run(self):
        try:
            if self._mode == "frequency":
                self.progress.emit(self.tr("Analysing source text frequency..."))
                entries = extract_by_frequency(self._proj, min_count=2)
            else:
                profile = find_profile(self._profile_name)
                if not profile:
                    self.progress.emit(
                        self.tr("Error: profile '{}' not found.").format(
                            self._profile_name
                        )
                    )
                    self.finished.emit(None)
                    return
                api_config = {
                    "api_host": profile.get("api_host", ""),
                    "api_key": profile.get("api_key", ""),
                    "model": profile.get("model", "gpt-4o"),
                    "proxy": profile.get("proxy", ""),
                }
                self.progress.emit(self.tr("Sending to LLM for analysis..."))
                entries = extract_by_llm(
                    self._proj,
                    api_config=api_config,
                    status_cb=lambda msg: self.progress.emit(msg),
                    target_language=self._target_language,
                )
            self.finished.emit(entries)
        except Exception as exc:
            logger.exception("Extraction failed")
            self.progress.emit(
                self.tr("Error: {}").format(str(exc))
            )
            self.finished.emit(None)


# ── Dialog ────────────────────────────────────────────────────────────────


class GlossaryExtractorDialog(QDialog):
    """Dialog for extracting glossary entries from a translation project.

    Typical usage::

        dlg = GlossaryExtractorDialog(project, translator, self)
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            saved_path = dlg.get_saved_path()
            # use saved_path as the new glossary path
    """

    def __init__(
        self,
        proj: "ProjImgTrans",
        current_profile_name: str = "",
        existing_entries: tuple = (),
        parent=None,
    ):
        super().__init__(parent)
        self._proj = proj
        self._entries: tuple = existing_entries or ()
        self._saved_path: str = ""

        self.setWindowTitle(self.tr("Glossary Extraction"))
        self.setMinimumSize(660, 520)
        self.resize(780, 600)

        self._build_ui()
        self._populate_profiles(current_profile_name)

        # Restore previous extraction results if available
        if self._entries:
            self._populate_table(self._entries)
            self._save_btn.setEnabled(True)
            self._status_label.setText(
                self.tr("Previously extracted {} terms.").format(len(self._entries))
            )

    # ── UI construction ──────────────────────────────────────────────────

    def done(self, result: int):
        """Persist extracted entries on parent before dialog closes."""
        if self._entries and self.parent() is not None:
            self.parent()._glossary_extractor_entries = self._entries
        super().done(result)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # -- LLM Profile row --
        profile_row = QWidget()
        profile_layout = QHBoxLayout(profile_row)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.addWidget(QLabel(self.tr("LLM Profile")))
        self._profile_combo = ConfigComboBox()
        self._profile_combo.setMinimumWidth(180)
        profile_layout.addWidget(self._profile_combo)
        profile_layout.addStretch()
        layout.addWidget(profile_row)

        # -- Target language row --
        lang_row = QWidget()
        lang_layout = QHBoxLayout(lang_row)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.addWidget(QLabel(self.tr("Target Language")))
        self._target_lang_combo = ConfigComboBox()
        for lang in TARGET_LANGUAGES:
            self._target_lang_combo.addItem(lang, lang)
        # Default to the translator's current target language
        current_target = pcfg.module.translate_target
        idx = self._target_lang_combo.findData(current_target)
        if idx >= 0:
            self._target_lang_combo.setCurrentIndex(idx)
        lang_layout.addWidget(self._target_lang_combo)
        lang_layout.addStretch()
        layout.addWidget(lang_row)

        # -- Extraction mode --
        mode_frame = QFrame()
        mode_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mode_layout = QVBoxLayout(mode_frame)
        mode_layout.setContentsMargins(8, 6, 8, 6)

        mode_title = QLabel(self.tr("Extraction Mode"))
        mode_title.setStyleSheet("font-weight: bold;")
        mode_layout.addWidget(mode_title)

        self._freq_radio = QRadioButton(
            self.tr("Frequency (fast, no LLM) — count repeated terms")
        )
        self._llm_radio = QRadioButton(
            self.tr("LLM Extraction (slower, semantic) — detect named entities "
                     "and important terms")
        )
        self._llm_radio.setChecked(True)  # default
        mode_layout.addWidget(self._freq_radio)
        mode_layout.addWidget(self._llm_radio)

        layout.addWidget(mode_frame)

        # -- Extract button + progress --
        action_row = QHBoxLayout()
        self._extract_btn = QPushButton(self.tr("Extract Glossary"))
        self._extract_btn.setFixedWidth(160)
        self._extract_btn.clicked.connect(self._on_extract)
        action_row.addWidget(self._extract_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setVisible(False)
        action_row.addWidget(self._progress_bar, stretch=1)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        action_row.addWidget(self._status_label, stretch=2)

        layout.addLayout(action_row)

        # -- Results table --
        table_frame = QFrame()
        table_frame.setFrameShape(QFrame.Shape.StyledPanel)
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(8, 6, 8, 6)

        table_title = QLabel(self.tr("Extracted Terms"))
        table_title.setStyleSheet("font-weight: bold;")
        table_layout.addWidget(table_title)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            self.tr("Source"),
            self.tr("Translation"),
            self.tr("Note"),
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
        )
        table_layout.addWidget(self._table)

        layout.addWidget(table_frame, stretch=1)

        # -- Save / Cancel buttons --
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton(self.tr("Save as..."))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate_profiles(self, current_name: str):
        """Fill the profile combo box and select *current_name* if found."""
        names = get_profile_names()
        self._profile_combo.clear()
        for name in names:
            self._profile_combo.addItem(name, name)
        # Select current translator's profile
        if current_name:
            idx = self._profile_combo.findData(current_name)
            if idx >= 0:
                self._profile_combo.setCurrentIndex(idx)

    # ── Extraction ───────────────────────────────────────────────────────

    def _on_extract(self):
        self._set_ui_busy(True)

        mode = "llm" if self._llm_radio.isChecked() else "frequency"
        profile_name = self._profile_combo.currentData() or ""
        target_language = self._target_lang_combo.currentData() or "简体中文"

        if mode == "llm" and not profile_name:
            QMessageBox.warning(
                self,
                self.tr("No Profile"),
                self.tr("Please select an LLM profile for extraction."),
            )
            self._set_ui_busy(False)
            return

        # Check that there are enough pages with data
        if mode == "llm":
            has_data = any(
                blk.get_text().strip()
                for blk_list in self._proj.pages.values()
                for blk in blk_list
            )
            if not has_data:
                QMessageBox.information(
                    self,
                    self.tr("No Data"),
                    self.tr(
                        "The project has no source text to analyse.\n\n"
                        "Please run text detection and OCR first."
                    ),
                )
                self._set_ui_busy(False)
                return
        else:
            has_data = any(
                blk.get_text().strip() and (blk.translation or "").strip()
                for blk_list in self._proj.pages.values()
                for blk in blk_list
            )
            if not has_data:
                QMessageBox.information(
                    self,
                    self.tr("No Data"),
                    self.tr(
                        "The project has no source/translation pairs to analyse.\n\n"
                        "Frequency extraction requires translations to pair with "
                        "source text.  Try LLM Extraction mode instead, or run "
                        "the translation pipeline first."
                    ),
                )
                self._set_ui_busy(False)
                return

        self._worker = _ExtractWorker(
            proj=self._proj,
            mode=mode,
            profile_name=profile_name,
            target_language=target_language,
            parent=self,
        )
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_progress(self, msg: str):
        self._status_label.setText(msg)

    def _on_worker_finished(self, entries):
        self._set_ui_busy(False)
        if entries is None:
            self._status_label.setText(self.tr("Extraction failed — see log."))
            return

        self._entries = entries
        self._populate_table(entries)
        self._save_btn.setEnabled(bool(entries))

        if entries:
            self._status_label.setText(
                self.tr("Extracted {} terms.").format(len(entries))
            )
        else:
            self._status_label.setText(
                self.tr("No glossary terms found in this project.")
            )

    # ── Table population ─────────────────────────────────────────────────

    def _populate_table(self, entries):
        self._table.setRowCount(0)
        self._table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            self._table.setItem(row, 0, QTableWidgetItem(entry.source))
            self._table.setItem(row, 1, QTableWidgetItem(entry.translation))
            self._table.setItem(row, 2, QTableWidgetItem(entry.note))

    def _get_table_entries(self):
        """Read entries back from the table (allowing user edits)."""
        entries = []
        for row in range(self._table.rowCount()):
            src_item = self._table.item(row, 0)
            tr_item = self._table.item(row, 1)
            note_item = self._table.item(row, 2)
            if src_item and tr_item:
                src = src_item.text().strip()
                tr = tr_item.text().strip()
                note = (note_item.text().strip() or "") if note_item else ""
                if src and tr:
                    entries.append(GlossaryEntry(src, tr, note))
        return entries

    # ── Saving ───────────────────────────────────────────────────────────

    def _on_save(self):
        entries = self._get_table_entries()
        if not entries:
            QMessageBox.information(
                self,
                self.tr("No Entries"),
                self.tr("There are no entries to save."),
            )
            return

        default_name = "extracted_glossary.json"
        default_dir = default_name
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Glossary"),
            default_dir,
            self.tr("Glossary files (*.json);;All files (*)"),
        )
        if not path:
            return

        # Ensure .json extension
        if not path.lower().endswith(".json"):
            path += ".json"

        try:
            save_glossary_json(entries, path)
            self._saved_path = path

            # Offer to set as active glossary
            reply = QMessageBox.question(
                self,
                self.tr("Use Glossary"),
                self.tr(
                    "Glossary saved to:\n{}\n\n"
                    "Set this file as the active glossary now?"
                ).format(path),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.accept()
            else:
                # Just close without setting active path
                self._saved_path = ""
                self.reject()
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.tr("Save Error"),
                self.tr("Failed to save glossary:\n{}").format(str(exc)),
            )

    # ── Query results ────────────────────────────────────────────────────

    def get_saved_path(self) -> str:
        """Return the path of the saved glossary file, or empty if none."""
        return self._saved_path

    # ─── UI helpers ──────────────────────────────────────────────────────

    def _set_ui_busy(self, busy: bool):
        self._extract_btn.setEnabled(not busy)
        self._profile_combo.setEnabled(not busy)
        self._freq_radio.setEnabled(not busy)
        self._llm_radio.setEnabled(not busy)
        self._progress_bar.setVisible(busy)
        if not busy:
            self._progress_bar.setValue(0)
        self._status_label.setText("" if busy else self._status_label.text())
