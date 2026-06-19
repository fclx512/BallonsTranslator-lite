"""Advanced alignment configuration dialog."""

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QButtonGroup,
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

    Supports X-axis (left/center/right) and Y-axis (top/center/bottom).
    Only one axis can be active at a time, selected via radio buttons.

    Signals:
        pick_clicked: Emitted when the user clicks the "Pick" button.
            The handler should hide this dialog, enter pick mode on the
            canvas, then call :meth:`set_picked_value` once a coordinate
            is captured.
    """

    pick_clicked = Signal()

    # ── Axis / mode label tables ───────────────────────────────
    _AXIS_LABELS = {"x": "X:", "y": "Y:"}
    _MODE_LABELS = {
        "x": ("Align Left Edges", "Align Centers", "Align Right Edges"),
        "y": ("Align Top Edges", "Align Centers", "Align Bottom Edges"),
    }
    _MODE_VALUES = {
        "x": ("left", "center", "right"),
        "y": ("top", "center", "bottom"),
    }

    def __init__(self, num_pages: int, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Advanced Alignment"))
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # ── Axis selection ────────────────────────────────────
        axis_frame = QFrame()
        axis_frame.setFrameShape(QFrame.Shape.StyledPanel)
        axis_layout = QVBoxLayout(axis_frame)
        axis_layout.addWidget(QLabel(self.tr("Alignment Axis")))

        self.axis_group = QButtonGroup(self)
        self.radio_x = QRadioButton(self.tr("X Axis"))
        self.radio_y = QRadioButton(self.tr("Y Axis"))
        self.axis_group.addButton(self.radio_x, 1)
        self.axis_group.addButton(self.radio_y, 2)
        self.radio_y.setChecked(True)  # default: Y axis

        axis_row = QHBoxLayout()
        axis_row.addWidget(self.radio_x)
        axis_row.addWidget(self.radio_y)
        axis_layout.addLayout(axis_row)
        layout.addWidget(axis_frame)

        # ── Target coordinate ──────────────────────────────────
        self._pos_frame = QFrame()
        self._pos_frame.setFrameShape(QFrame.Shape.StyledPanel)
        pos_layout = QVBoxLayout(self._pos_frame)
        self._pos_header = QLabel(self.tr("Target Position"))
        self._pos_header.setObjectName("GroupTitle")
        pos_layout.addWidget(self._pos_header)

        pos_row = QHBoxLayout()
        self._pos_label = QLabel("Y:")  # placeholder — _refresh_ui sets it
        pos_row.addWidget(self._pos_label)
        self._spin = QSpinBox()
        self._spin.setRange(-99999, 99999)
        self._spin.setValue(0)
        pos_row.addWidget(self._spin, 1)

        self.pick_btn = QPushButton(self.tr("Pick"))
        self.pick_btn.clicked.connect(self._on_pick)
        pos_row.addWidget(self.pick_btn)
        pos_layout.addLayout(pos_row)
        layout.addWidget(self._pos_frame)

        # ── Alignment mode ─────────────────────────────────────
        self._mode_frame = QFrame()
        self._mode_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mode_layout = QVBoxLayout(self._mode_frame)
        mode_layout.addWidget(QLabel(self.tr("Alignment Mode")))

        self.mode_0 = QRadioButton()
        self.mode_1 = QRadioButton()
        self.mode_2 = QRadioButton()
        self.mode_0.setChecked(True)

        mode_layout.addWidget(self.mode_0)
        mode_layout.addWidget(self.mode_1)
        mode_layout.addWidget(self.mode_2)
        layout.addWidget(self._mode_frame)

        # ── Page range ─────────────────────────────────────────
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
        self.slider.rangeChanged.connect(lambda a, b: self._update_range_info())

        # ── Buttons ────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("OK"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        # ── Initial UI refresh ─────────────────────────────────
        self._refresh_ui()
        self.axis_group.buttonClicked.connect(self._on_axis_changed)

    # ── Public accessors ─────────────────────────────────────

    def target_value(self) -> int:
        """Return the target coordinate (X or Y)."""
        return self._spin.value()

    def alignment_axis(self) -> str:
        """Return ``"x"`` or ``"y"``."""
        return "x" if self.radio_x.isChecked() else "y"

    def alignment_mode(self) -> str:
        """Return mode string for the current axis.

        Y: ``"top"`` | ``"center"`` | ``"bottom"``.
        X: ``"left"`` | ``"center"`` | ``"right"``.
        """
        axis = self.alignment_axis()
        modes = self._MODE_VALUES[axis]
        if self.mode_0.isChecked():
            return modes[0]
        elif self.mode_1.isChecked():
            return modes[1]
        else:
            return modes[2]

    def page_filter(self):
        """Return ``None`` for all pages, or ``List[str]`` of page names."""
        if self.all_pages_cb.isChecked():
            return None
        return [self.slider.low(), self.slider.high()]

    def set_picked_value(self, val: int):
        """Fill coordinate value after canvas pick completes."""
        self._spin.setValue(val)

    # ── Internal ────────────────────────────────────────────

    def _on_pick(self):
        """User clicked the Pick button — notify the handler."""
        self.pick_clicked.emit()

    def _on_axis_changed(self):
        self._refresh_ui()

    def _refresh_ui(self):
        """Update labels and mode radio buttons for the current axis."""
        axis = self.alignment_axis()

        # Coordinate label
        self._pos_label.setText(self.tr(self._AXIS_LABELS[axis]))

        # Mode radio buttons
        labels = self._MODE_LABELS[axis]
        self.mode_0.setText(self.tr(labels[0]))
        self.mode_1.setText(self.tr(labels[1]))
        self.mode_2.setText(self.tr(labels[2]))

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
