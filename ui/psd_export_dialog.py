"""PSD export dialog — page range selector + export config.

Generates a single ExtendScript (.jsx) that recreates the project's text
blocks as editable text layers in Photoshop (run once via
File → Scripts → Browse).
"""

from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.proj_imgtrans import ProjImgTrans
from utils.psd_exporter import ExportOptions

from .custom_widget.slider import RangeSlider


class PsdExportDialog(QDialog):
    """Modal dialog for configuring and triggering PSD export."""

    def __init__(
        self,
        proj: ProjImgTrans,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._proj = proj

        self.setWindowTitle(self.tr("Export PSD"))
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setSizeGripEnabled(False)

        self._setup_ui()

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def get_options(self) -> ExportOptions:
        """Collect user choices after dialog is accepted."""
        all_pages = self._all_cb.isChecked()
        page_filter: Optional[List[str]] = None
        if not all_pages:
            lo = self._slider.low()
            hi = self._slider.high()
            page_filter = [self._proj.idx2pagename(i) for i in range(lo, hi + 1)]

        return ExportOptions(
            output_dir=self._dir_edit.text(),
            page_filter=page_filter,
            export_method="jsx",
            center_align=self._center_cb.isChecked(),
        )

    # ------------------------------------------------------------------
    # internal — UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ---- page range -------------------------------------------------
        layout.addWidget(self._make_section_label(self.tr("Page Range")))

        # slider row
        slider_row = QHBoxLayout()
        num_pages = self._proj.num_pages
        max_idx = max(num_pages - 1, 0)

        self._start_spin = QSpinBox()
        self._start_spin.setRange(1, num_pages)
        self._start_spin.setValue(1)
        self._start_spin.setFixedWidth(60)
        self._start_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)

        self._end_spin = QSpinBox()
        self._end_spin.setRange(1, num_pages)
        self._end_spin.setValue(num_pages)
        self._end_spin.setFixedWidth(60)
        self._end_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)

        self._slider = RangeSlider(0, max_idx)
        if max_idx > 0:
            self._slider.set_range(0, max_idx)

        slider_row.addStretch()
        slider_row.addWidget(self._start_spin)
        slider_row.addWidget(QLabel("~"))
        slider_row.addWidget(self._end_spin)
        slider_row.addStretch()
        layout.addLayout(slider_row)
        layout.addWidget(self._slider)

        self._range_label = QLabel()
        self._range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_range_info()
        layout.addWidget(self._range_label)

        self._all_cb = QCheckBox(self.tr("All Pages"))
        self._all_cb.toggled.connect(self._on_all_toggled)
        self._all_cb.setChecked(True)
        layout.addWidget(self._all_cb, alignment=Qt.AlignmentFlag.AlignCenter)

        # ---- separator --------------------------------------------------
        layout.addWidget(self._make_separator())

        # ---- export description -----------------------------------------
        layout.addWidget(self._make_section_label(self.tr("Export")))

        info = QLabel(
            self.tr(
                "Exports a single .jsx script for the selected pages.\nRun it once in Photoshop (File → Scripts → Browse);\ntext layers are fully editable."
            )
        )
        info.setWordWrap(True)
        info.setStyleSheet("padding: 4px 0;")
        layout.addWidget(info)

        self._center_cb = QCheckBox(self.tr("Center text within its block (recommended)"))
        self._center_cb.setChecked(True)
        self._center_cb.setToolTip(
            self.tr(
                "Shift each text layer so its center matches the block box. Absorbs font-metric differences between the app and Photoshop."
            )
        )
        layout.addWidget(self._center_cb)

        # ---- separator --------------------------------------------------
        layout.addWidget(self._make_separator())

        # ---- output directory -------------------------------------------
        layout.addWidget(self._make_section_label(self.tr("Output Directory")))
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText(self.tr("Select output directory..."))
        browse_btn = QPushButton(self.tr("Browse"))
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._dir_edit)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        # ---- separator --------------------------------------------------
        layout.addWidget(self._make_separator())

        # ---- font compatibility -----------------------------------------
        layout.addWidget(self._make_section_label(self.tr("Font Compatibility")))
        self._font_label = QLabel()
        self._font_label.setWordWrap(True)
        self._refresh_font_warning()
        layout.addWidget(self._font_label)

        # ---- buttons ----------------------------------------------------
        layout.addSpacing(4)
        btn_row = QHBoxLayout()
        self._export_btn = QPushButton(self.tr("Export"))
        self._export_btn.setDefault(True)
        self._export_btn.clicked.connect(self._validate_and_accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._export_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # ---- signal wiring ----------------------------------------------
        self._slider.rangeChanged.connect(self._sync_spinboxes)
        self._slider.rangeChanged.connect(lambda *a: self._update_range_info())
        self._start_spin.valueChanged.connect(self._on_spinbox_changed)
        self._end_spin.valueChanged.connect(self._on_spinbox_changed)

    # ------------------------------------------------------------------
    # internal — font warning
    # ------------------------------------------------------------------

    def _refresh_font_warning(self):
        families = set()
        for blk_list in self._proj.pages.values():
            for blk in blk_list:
                if blk.fontformat.font_family:
                    families.add(blk.fontformat.font_family)

        if not families:
            self._font_label.setText(self.tr("No text blocks in project."))
            return

        # Static map + local font name-table scan index — the target
        # Photoshop's font list is unknown here.
        from utils.font_mapping import QT_TO_PS_FONT_MAP, exact_ps_name

        unknown = [
            f
            for f in sorted(families)
            if f not in QT_TO_PS_FONT_MAP and exact_ps_name(f) is None
        ]
        if unknown:
            self._font_label.setText(
                self.tr("⚠  ")
                + str(len(unknown))
                + self.tr(" font(s) may need manual adjustment in Photoshop: ")
                + ", ".join(unknown[:5])
                + ("..." if len(unknown) > 5 else "")
            )
        else:
            self._font_label.setText(self.tr("✓ All fonts have known PS mappings."))

    # ------------------------------------------------------------------
    # internal — page range sync
    # ------------------------------------------------------------------

    def _sync_spinboxes(self, lo: int, hi: int):
        self._start_spin.blockSignals(True)
        self._end_spin.blockSignals(True)
        self._start_spin.setValue(lo + 1)
        self._end_spin.setValue(hi + 1)
        self._start_spin.blockSignals(False)
        self._end_spin.blockSignals(False)

    def _on_spinbox_changed(self):
        lo = self._start_spin.value() - 1
        hi = self._end_spin.value() - 1
        if lo > hi:
            lo, hi = hi, lo
        self._slider.blockSignals(True)
        self._slider.set_range(lo, hi)
        self._slider.blockSignals(False)
        # Sync spinboxes back (in case we swapped)
        self._start_spin.blockSignals(True)
        self._end_spin.blockSignals(True)
        self._start_spin.setValue(lo + 1)
        self._end_spin.setValue(hi + 1)
        self._start_spin.blockSignals(False)
        self._end_spin.blockSignals(False)
        self._update_range_info()

    def _update_range_info(self):
        lo = self._slider.low() + 1
        hi = self._slider.high() + 1
        count = hi - lo + 1
        self._range_label.setText(self.tr("Page ") + f"{lo} ~ {hi}  ({count} pages)")

    def _on_all_toggled(self, checked: bool):
        self._slider.setEnabled(not checked)
        self._start_spin.setEnabled(not checked)
        self._end_spin.setEnabled(not checked)
        if checked:
            num_pages = self._proj.num_pages
            max_idx = max(num_pages - 1, 0)
            self._slider.set_range(0, max_idx)
            self._start_spin.setValue(1)
            self._end_spin.setValue(num_pages)

    # ------------------------------------------------------------------
    # internal — output directory
    # ------------------------------------------------------------------

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, self.tr("Select Output Directory")
        )
        if path:
            self._dir_edit.setText(path)

    # ------------------------------------------------------------------
    # internal — validation
    # ------------------------------------------------------------------

    def _validate_and_accept(self):
        out_dir = self._dir_edit.text().strip()
        if not out_dir:
            self._dir_edit.setFocus()
            return
        if not self._proj.pages:
            # no pages — nothing to export
            return

        lo = self._slider.low()
        hi = self._slider.high()
        if lo > hi:
            lo, hi = hi, lo

        if not self._all_cb.isChecked() and (hi - lo + 1) < 1:
            return

        self.accept()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(QFont(label.font().family(), weight=QFont.Weight.Bold))
        return label

    @staticmethod
    def _make_separator() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        return f
