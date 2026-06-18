"""Advanced alignment configuration dialog."""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import RangeSlider


class PointAlignDialog(QDialog):
    """Dialog for configuring advanced point alignment.

    Signals:
        pick_y_clicked: Emitted when the user clicks the "Pick from canvas"
            button.  The handler should hide this dialog, enter Y-pick mode
            on the canvas, then call :meth:`set_picked_y` once a Y is
            captured.
    """

    pick_y_clicked = Signal()

    def __init__(self, num_pages: int, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Advanced Alignment"))
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # ── Target Y ────────────────────────────────────────────
        y_frame = QFrame()
        y_frame.setFrameShape(QFrame.Shape.StyledPanel)
        y_layout = QVBoxLayout(y_frame)
        y_header = QLabel(self.tr("Target Position"))
        y_header.setObjectName("GroupTitle")
        y_layout.addWidget(y_header)

        y_row = QHBoxLayout()
        y_row.addWidget(QLabel(self.tr("Y:")))
        self.y_spin = QSpinBox()
        self.y_spin.setRange(-99999, 99999)
        self.y_spin.setValue(0)
        y_row.addWidget(self.y_spin, 1)

        self.pick_btn = QPushButton(self.tr("Pick"))
        self.pick_btn.clicked.connect(self._on_pick)
        y_row.addWidget(self.pick_btn)
        y_layout.addLayout(y_row)
        layout.addWidget(y_frame)

        # ── Alignment mode ──────────────────────────────────────
        mode_frame = QFrame()
        mode_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mode_layout = QVBoxLayout(mode_frame)
        mode_layout.addWidget(QLabel(self.tr("Alignment Mode")))

        self.mode_top = QRadioButton(self.tr("Align Top Edges"))
        self.mode_center = QRadioButton(self.tr("Align Centers"))
        self.mode_bottom = QRadioButton(self.tr("Align Bottom Edges"))
        self.mode_top.setChecked(True)

        mode_layout.addWidget(self.mode_top)
        mode_layout.addWidget(self.mode_center)
        mode_layout.addWidget(self.mode_bottom)
        layout.addWidget(mode_frame)

        # ── Page range ──────────────────────────────────────────
        range_frame = QFrame()
        range_frame.setFrameShape(QFrame.Shape.StyledPanel)
        range_layout = QVBoxLayout(range_frame)
        range_layout.addWidget(QLabel(self.tr("Apply To")))

        self.slider = RangeSlider(0, max(0, num_pages - 1))
        range_layout.addWidget(self.slider)

        self.range_info = QLabel()
        range_layout.addWidget(self.range_info)

        self.all_pages_cb = QCheckBox(self.tr("All Pages"))
        self.all_pages_cb.toggled.connect(self._on_all_pages_toggled)
        self.all_pages_cb.setChecked(True)
        range_layout.addWidget(self.all_pages_cb)
        layout.addWidget(range_frame)

        self._update_range_info()

        # ── Buttons ─────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("OK"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.slider.rangeChanged.connect(lambda a, b: self._update_range_info())

    # ── Public accessors ─────────────────────────────────────

    def target_y(self) -> int:
        return self.y_spin.value()

    def alignment_mode(self) -> str:
        if self.mode_top.isChecked():
            return "top"
        elif self.mode_center.isChecked():
            return "center"
        else:
            return "bottom"

    def page_filter(self):
        """Return ``None`` for all pages, or ``List[str]`` of page names."""
        if self.all_pages_cb.isChecked():
            return None
        return [self.slider.low(), self.slider.high()]

    def set_picked_y(self, y: int):
        """Fill Y value after canvas pick completes."""
        self.y_spin.setValue(y)

    # ── Internal ────────────────────────────────────────────

    def _on_pick(self):
        """User clicked the Pick button — notify the handler."""
        self.pick_y_clicked.emit()

    def _on_all_pages_toggled(self, checked: bool):
        self.slider.setEnabled(not checked)
        self._update_range_info()

    def _update_range_info(self):
        lo = self.slider.low() + 1
        hi = self.slider.high() + 1
        self.range_info.setText(
            self.tr("Page %1 ~ Page %2 (%3 pages)")
            .replace("%1", str(lo))
            .replace("%2", str(hi))
            .replace("%3", str(hi - lo + 1))
        )
