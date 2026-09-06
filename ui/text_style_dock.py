"""Text Style rail dock content: opacity, shadow and gradient sections.

This widget replaces the old modal ``TextStyleDialog`` (520×520 fixed,
blocked canvas interaction): the three sections now live in a
``ui/custom_widget/rail_dock_panel.py::RailDockPanel`` opened from the
format-area rail, so every control commits straight onto the selected
canvas text block while the panel stays open.

Routing contract with the hosting ``FontFormatPanel``:

* ``preview_changed(name, value)`` — live drag/preview ticks. The panel
  applies the value to the current block *without* an undo entry.
* ``commit_changed(name, value)`` — release of a drag or a discrete pick.
  The panel routes through ``on_param_changed`` (one undo entry per
  change) or the project-wide ``shadow_include_stroke`` path.
* ``shadow_include_stroke_changed(bool)`` — project-wide toggle (applies
  to all blocks on the page + global format), matching PS behavior.

Like the transform panel, the widget works in global mode too (no item
selected): commits write the global format via the panel's standard
``on_param_changed`` routing.
"""

import math

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from utils.fontformat import FontFormat

from .custom_widget import (
    ClockDial,
    ColorPickerDialog,
    ColorSwatchBtn,
    PaintQSlider,
    SmallComboBox,
    SmallParamLabel,
)

SHADOW_PARAMS = frozenset(
    {"shadow_radius", "shadow_strength", "shadow_color", "shadow_offset"}
)
GRADIENT_PARAMS = frozenset(
    {
        "gradient_enabled",
        "gradient_start_color",
        "gradient_end_color",
        "gradient_angle",
        "gradient_size",
    }
)


def _section_header(text: str) -> SmallParamLabel:
    label = SmallParamLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


class ColorButton(ColorSwatchBtn):
    """Swatch button that opens ColorPickerDialog and emits a [r, g, b] list."""

    colorChanged = Signal(list)  # override parent signal — emits [r, g, b]

    def __init__(self, color, parent=None):
        super().__init__(None, parent)
        self._color_list = [0, 0, 0]
        self.setFixedSize(32, 22)
        if color is not None:
            self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        if isinstance(color, (list, tuple)):
            self._color_list = list(color[:3])
            self._color = QColor(*[max(0, min(255, int(c))) for c in self._color_list])
        else:
            self._color_list = [color.red(), color.green(), color.blue()]
            self._color = QColor(color)
        self._update_style()
        self.colorChanged.emit(self._color_list)

    def color(self):
        return self._color_list

    def _pick(self):
        c = QColor(*[max(0, min(255, int(v))) for v in self._color_list])
        dlg = ColorPickerDialog(c, self.window())
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            self.set_color(dlg.get_color())


class GradientBar(QWidget):
    """PS-style clickable gradient bar with start/end color stops."""

    startColorChanged = Signal(list)
    endColorChanged = Signal(list)

    def __init__(self, start_color, end_color, parent=None):
        super().__init__(parent)
        self._start = list(start_color)
        self._end = list(end_color)
        self.setFixedHeight(40)
        self.setMinimumWidth(160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setStartColor(self, color):
        self._start = list(color)
        self.update()

    def setEndColor(self, color):
        self._end = list(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        bar_h = 22
        bar_y = (h - bar_h) / 2
        stop_r = 7

        # gradient bar
        bar_rect = QRectF(4, bar_y, w - 8, bar_h)
        path = QPainterPath()
        path.addRoundedRect(bar_rect, 4, 4)

        grad = QLinearGradient(bar_rect.topLeft(), bar_rect.topRight())
        grad.setColorAt(0, QColor(*[max(0, min(255, int(c))) for c in self._start]))
        grad.setColorAt(1, QColor(*[max(0, min(255, int(c))) for c in self._end]))
        p.fillPath(path, QBrush(grad))
        p.setPen(QPen(QColor(90, 90, 90), 1))
        p.drawPath(path)

        # stop positions
        stop_y = bar_rect.center().y()
        self._stop_lx = bar_rect.left() + stop_r + 6
        self._stop_rx = bar_rect.right() - stop_r - 6

        # start stop
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(QBrush(QColor(*[max(0, min(255, int(c))) for c in self._start])))
        p.drawEllipse(QPointF(self._stop_lx, stop_y), stop_r, stop_r)

        # end stop
        p.setBrush(QBrush(QColor(*[max(0, min(255, int(c))) for c in self._end])))
        p.drawEllipse(QPointF(self._stop_rx, stop_y), stop_r, stop_r)

        # store hit zone for click detection (margin = stop_r + 4)
        self._stop_area = stop_r + 4

        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if hasattr(self, "_stop_lx") and hasattr(self, "_stop_area"):
            d = self._stop_area
            # check distance to left / right stop centers
            lx = self._stop_lx
            rx = self._stop_rx
            dy = self.height() / 2 - pos.y()
            dxl = lx - pos.x()
            dxr = rx - pos.x()
            if abs(dxl) < d and abs(dy) < d:
                self._pick_color(is_start=True)
            elif abs(dxr) < d and abs(dy) < d:
                self._pick_color(is_start=False)

    def _pick_color(self, is_start=True):
        current = self._start if is_start else self._end
        c = QColor(*[max(0, min(255, int(v))) for v in current])
        dlg = ColorPickerDialog(c, self.window())
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            picked = dlg.get_color()
            rgb = [picked.red(), picked.green(), picked.blue()]
            if is_start:
                self._start = rgb
                self.startColorChanged.emit(rgb)
            else:
                self._end = rgb
                self.endColorChanged.emit(rgb)
            self.update()


class TextStyleGroup(QFrame):
    """Opacity / shadow / gradient sections for the Text Style rail dock.

    The group owns no font format state: it reads/writes through the
    signals above, and ``set_from_format`` restores control values from a
    ``FontFormat`` without emitting (selection sync).
    """

    preview_changed = Signal(str, object)
    commit_changed = Signal(str, object)
    shadow_include_stroke_changed = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ── Basic: opacity + line spacing type ─────────────────────
        self.opacity_combo = SmallComboBox(parent=self)
        self.opacity_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        from utils.config import pcfg

        for v in pcfg.opacity_presets:
            self.opacity_combo.addItem(str(v), v)
        self.opacity_combo.currentIndexChanged.connect(
            lambda _i: self.commit_changed.emit(
                "opacity", self.opacity_combo.currentData()
            )
        )

        self.linespacing_combo = SmallComboBox(parent=self)
        self.linespacing_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.linespacing_combo.addItem(self.tr("Proportional"), 0)
        self.linespacing_combo.addItem(self.tr("Distance"), 1)
        self.linespacing_combo.currentIndexChanged.connect(
            lambda _i: self.commit_changed.emit(
                "line_spacing_type", self.linespacing_combo.currentIndex()
            )
        )

        # ── Shadow ─────────────────────────────────────────────────
        # compact dial: the dock is small, so no degree ticks/labels —
        # the handle just gives a rough light-direction + offset drag.
        self.shadow_dial = ClockDial(mode="shadow", min_size=56, compact=True)
        self.shadow_dial.valueChanged.connect(self._emit_shadow_commit)
        self.shadow_dial.angleChanged.connect(self._emit_shadow_preview)
        self.shadow_dial.distanceChanged.connect(self._emit_shadow_preview)

        self.shadow_color_btn = ColorButton([0, 0, 0], self)
        self.shadow_color_btn.colorChanged.connect(
            lambda rgb: self.commit_changed.emit("shadow_color", list(rgb))
        )

        self.strength_slider = PaintQSlider(
            orientation=Qt.Orientation.Horizontal
        )
        self.strength_slider.setRange(0, 100)
        self.strength_slider.setValue(100)
        self.strength_slider.setValueFormat(lambda v: f"{v / 100:.2f}")
        self.strength_slider.valueChanged.connect(self._emit_strength_preview)
        self.strength_slider.sliderReleased.connect(self._emit_strength_commit)
        self.strength_label = QLabel("1.00")

        self.radius_slider = PaintQSlider(orientation=Qt.Orientation.Horizontal)
        self.radius_slider.setRange(0, 100)
        self.radius_slider.setValue(0)
        self.radius_slider.setValueFormat(lambda v: f"{v / 100:.2f}")
        self.radius_slider.valueChanged.connect(self._emit_radius_preview)
        self.radius_slider.sliderReleased.connect(self._emit_radius_commit)
        self.radius_label = QLabel("0.00")

        self.offset_label = QLabel("X: 0.00  Y: 0.00")

        self.include_stroke_cb = QCheckBox(
            self.tr("Include stroke in shadow"), self
        )
        self.include_stroke_cb.toggled.connect(
            self.shadow_include_stroke_changed
        )
        stroke_global = QLabel(self.tr("(global)"))
        stroke_global.setStyleSheet("font-size: 11px; color: gray;")

        # ── Gradient ───────────────────────────────────────────────
        self.gradient_dial = ClockDial(mode="gradient", min_size=56, compact=True)
        self.gradient_dial.valueChanged.connect(self._emit_gradient_commit)
        self.gradient_dial.angleChanged.connect(self._emit_gradient_preview)

        self.gradient_enable_cb = QCheckBox(self.tr("Enable"), self)
        self.gradient_enable_cb.toggled.connect(
            lambda checked: self.commit_changed.emit(
                "gradient_enabled", checked
            )
        )

        self.gradient_bar = GradientBar([0, 0, 0], [255, 255, 255], self)
        self.gradient_bar.startColorChanged.connect(
            lambda rgb: self.commit_changed.emit(
                "gradient_start_color", list(rgb)
            )
        )
        self.gradient_bar.endColorChanged.connect(
            lambda rgb: self.commit_changed.emit(
                "gradient_end_color", list(rgb)
            )
        )

        self.reverse_btn = QPushButton(self.tr("↔ Reverse"), self)
        self.reverse_btn.setFixedWidth(96)
        self.reverse_btn.clicked.connect(self._on_reverse_clicked)

        self.scale_slider = PaintQSlider(orientation=Qt.Orientation.Horizontal)
        self.scale_slider.setRange(50, 200)  # 0.5 to 2.0
        self.scale_slider.setValue(100)
        self.scale_slider.setValueFormat(lambda v: f"{v / 100:.2f}")
        self.scale_slider.valueChanged.connect(self._emit_scale_preview)
        self.scale_slider.sliderReleased.connect(self._emit_scale_commit)
        self.scale_label = QLabel("1.00")

        # ── Assemble ───────────────────────────────────────────────
        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(2, 2, 2, 2)
        vlayout.setSpacing(8)

        vlayout.addWidget(_section_header(self.tr("Opacity")))
        basic_row = QHBoxLayout()
        basic_row.setSpacing(10)
        basic_row.addWidget(SmallParamLabel(self.tr("Opacity")), 0)
        basic_row.addWidget(self.opacity_combo, 0)
        basic_row.addSpacing(6)
        basic_row.addWidget(SmallParamLabel(self.tr("Line Spacing")), 0)
        basic_row.addWidget(self.linespacing_combo, 0)
        basic_row.addStretch()
        vlayout.addLayout(basic_row)

        vlayout.addSpacing(4)
        vlayout.addWidget(_section_header(self.tr("Shadow")))

        shadow_row = QHBoxLayout()
        shadow_row.setSpacing(10)
        shadow_row.addWidget(
            self.shadow_dial, 0, Qt.AlignmentFlag.AlignTop
        )
        shadow_ctrl = QVBoxLayout()
        shadow_ctrl.setSpacing(4)
        color_row = QHBoxLayout()
        color_row.addWidget(SmallParamLabel(self.tr("Color")))
        color_row.addWidget(self.shadow_color_btn)
        color_row.addStretch()
        shadow_ctrl.addLayout(color_row)
        shadow_ctrl.addWidget(self.strength_slider)
        sr_row = QHBoxLayout()
        sr_row.setSpacing(4)
        sr_row.addWidget(SmallParamLabel(self.tr("Strength")))
        sr_row.addWidget(self.strength_label)
        sr_row.addStretch()
        shadow_ctrl.addLayout(sr_row)
        shadow_ctrl.addWidget(self.radius_slider)
        rr_row = QHBoxLayout()
        rr_row.setSpacing(4)
        rr_row.addWidget(SmallParamLabel(self.tr("Radius")))
        rr_row.addWidget(self.radius_label)
        rr_row.addStretch()
        shadow_ctrl.addLayout(rr_row)
        offset_row = QHBoxLayout()
        offset_row.setSpacing(4)
        offset_row.addWidget(SmallParamLabel(self.tr("Offset")))
        offset_row.addWidget(self.offset_label)
        offset_row.addStretch()
        shadow_ctrl.addLayout(offset_row)
        cb_row = QHBoxLayout()
        cb_row.setSpacing(4)
        cb_row.addWidget(self.include_stroke_cb)
        cb_row.addWidget(stroke_global)
        cb_row.addStretch()
        shadow_ctrl.addLayout(cb_row)
        shadow_row.addLayout(shadow_ctrl, 1)
        vlayout.addLayout(shadow_row)

        vlayout.addSpacing(4)
        vlayout.addWidget(_section_header(self.tr("Gradient")))

        gradient_row = QHBoxLayout()
        gradient_row.setSpacing(10)
        gradient_row.addWidget(
            self.gradient_dial, 0, Qt.AlignmentFlag.AlignTop
        )
        gradient_ctrl = QVBoxLayout()
        gradient_ctrl.setSpacing(6)
        gradient_ctrl.addWidget(self.gradient_enable_cb)
        gradient_ctrl.addWidget(self.gradient_bar)
        rev_row = QHBoxLayout()
        rev_row.addStretch()
        rev_row.addWidget(self.reverse_btn)
        rev_row.addStretch()
        gradient_ctrl.addLayout(rev_row)
        gradient_ctrl.addWidget(self.scale_slider)
        scale_row = QHBoxLayout()
        scale_row.setSpacing(4)
        scale_row.addWidget(SmallParamLabel(self.tr("Scale")))
        scale_row.addWidget(self.scale_label)
        scale_row.addStretch()
        gradient_ctrl.addLayout(scale_row)
        gradient_row.addLayout(gradient_ctrl, 1)
        vlayout.addLayout(gradient_row)

    # ── value helpers ────────────────────────────────────────

    def _shadow_strength(self):
        return self.strength_slider.value() / 100.0

    def _shadow_radius(self):
        return self.radius_slider.value() / 100.0

    def _shadow_offset(self):
        angle = self.shadow_dial.angle()
        dist = self.shadow_dial.distance()
        rad = math.radians(angle)
        return [
            round(math.cos(rad) * dist * 0.6, 2),
            round(-math.sin(rad) * dist * 0.6, 2),
        ]

    def _gradient_size(self):
        return self.scale_slider.value() / 100.0

    # ── signal emitters ──────────────────────────────────────

    def _emit_shadow_preview(self, *_args):
        self.offset_label.setText(
            f"X: {self._shadow_offset()[0]:.2f}  Y: {self._shadow_offset()[1]:.2f}"
        )
        self.preview_changed.emit("shadow_offset", self._shadow_offset())

    def _emit_shadow_commit(self):
        self._emit_shadow_preview()
        self.commit_changed.emit("shadow_offset", self._shadow_offset())

    def _emit_strength_preview(self, *_args):
        self.strength_label.setText(f"{self._shadow_strength():.2f}")
        self.preview_changed.emit("shadow_strength", self._shadow_strength())

    def _emit_strength_commit(self):
        self.commit_changed.emit("shadow_strength", self._shadow_strength())

    def _emit_radius_preview(self, *_args):
        self.radius_label.setText(f"{self._shadow_radius():.2f}")
        self.preview_changed.emit("shadow_radius", self._shadow_radius())

    def _emit_radius_commit(self):
        self.commit_changed.emit("shadow_radius", self._shadow_radius())

    def _emit_gradient_preview(self, *_args):
        self.preview_changed.emit("gradient_angle", self.gradient_dial.angle())

    def _emit_gradient_commit(self):
        self.commit_changed.emit("gradient_angle", self.gradient_dial.angle())

    def _emit_scale_preview(self, *_args):
        self.scale_label.setText(f"{self._gradient_size():.2f}")
        self.preview_changed.emit("gradient_size", self._gradient_size())

    def _emit_scale_commit(self):
        self.commit_changed.emit("gradient_size", self._gradient_size())

    def _on_reverse_clicked(self):
        swap = (self.gradient_bar._start, self.gradient_bar._end)
        self.gradient_bar.setStartColor(swap[1])
        self.gradient_bar.setEndColor(swap[0])
        self.gradient_dial.setColor(swap[1])
        self.commit_changed.emit("gradient_start_color", list(swap[1]))
        self.commit_changed.emit("gradient_end_color", list(swap[0]))

    # ── selection sync (no emissions) ────────────────────────

    def set_from_format(self, fmt: FontFormat) -> None:
        from utils.config import pcfg

        presets = pcfg.opacity_presets
        if presets:
            idx, best = 0, abs(presets[0] - fmt.opacity)
            for i, v in enumerate(presets):
                d = abs(v - fmt.opacity)
                if d < best:
                    best, idx = d, i
            self.opacity_combo.blockSignals(True)
            self.opacity_combo.setCurrentIndex(idx)
            self.opacity_combo.blockSignals(False)

        self.linespacing_combo.blockSignals(True)
        self.linespacing_combo.setCurrentIndex(fmt.line_spacing_type)
        self.linespacing_combo.blockSignals(False)

        self.shadow_dial.setColor(fmt.shadow_color)
        ox, oy = fmt.shadow_offset[0], fmt.shadow_offset[1]
        dist = max(0.0, min(1.0, math.sqrt(ox * ox + oy * oy) / 0.6))
        angle = math.degrees(math.atan2(-oy, ox))
        if angle < 0:
            angle += 360.0
        self.shadow_dial.setAngle(angle)
        self.shadow_dial.setDistance(dist)
        self.offset_label.setText(f"X: {ox:.2f}  Y: {oy:.2f}")
        self.shadow_color_btn.blockSignals(True)
        self.shadow_color_btn.set_color(fmt.shadow_color)
        self.shadow_color_btn.blockSignals(False)
        for slider in (
            self.strength_slider,
            self.radius_slider,
            self.scale_slider,
        ):
            slider.blockSignals(True)
        self.strength_slider.setValue(int(fmt.shadow_strength * 100))
        self.strength_label.setText(f"{fmt.shadow_strength:.2f}")
        self.radius_slider.setValue(int(fmt.shadow_radius * 100))
        self.radius_label.setText(f"{fmt.shadow_radius:.2f}")
        self.include_stroke_cb.blockSignals(True)
        self.include_stroke_cb.setChecked(fmt.shadow_include_stroke)
        self.include_stroke_cb.blockSignals(False)

        self.gradient_dial.setColor(fmt.gradient_start_color)
        self.gradient_dial.setAngle(fmt.gradient_angle)
        self.gradient_enable_cb.blockSignals(True)
        self.gradient_enable_cb.setChecked(fmt.gradient_enabled)
        self.gradient_enable_cb.blockSignals(False)
        self.gradient_bar.setStartColor(fmt.gradient_start_color)
        self.gradient_bar.setEndColor(fmt.gradient_end_color)
        self.scale_slider.setValue(int(fmt.gradient_size * 100))
        self.scale_label.setText(f"{fmt.gradient_size:.2f}")
        for slider in (
            self.strength_slider,
            self.radius_slider,
            self.scale_slider,
        ):
            slider.blockSignals(False)

    def current_include_stroke(self) -> bool:
        return self.include_stroke_cb.isChecked()