import math

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from utils.fontformat import FontFormat

from .custom_widget.clock_dial import ClockDial
from .custom_widget.color_picker import ColorPickerDialog
from .text_graphical_effect import apply_shadow_effect


class ShadowGradientPreview(QWidget):
    """Live preview of shadow/gradient effect on sample text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shadow_radius = 0.0
        self.shadow_strength = 1.0
        self.shadow_color = [0, 0, 0]
        self.shadow_offset = [0.0, 0.0]
        self.gradient_enabled = False
        self.gradient_start = [0, 0, 0]
        self.gradient_end = [0, 0, 0]
        self.gradient_angle = 0.0
        self.gradient_size = 1.0
        self.text_color = [0, 0, 0]
        self.setMinimumHeight(90)

    def set_params(self, shadow_radius, shadow_strength, shadow_color, shadow_offset,
                   gradient_enabled, gradient_start, gradient_end, gradient_angle, gradient_size,
                   text_color=None):
        self.shadow_radius = shadow_radius
        self.shadow_strength = shadow_strength
        self.shadow_color = shadow_color
        self.shadow_offset = shadow_offset
        self.gradient_enabled = gradient_enabled
        self.gradient_start = gradient_start
        self.gradient_end = gradient_end
        self.gradient_angle = gradient_angle
        self.gradient_size = gradient_size
        if text_color is not None:
            self.text_color = text_color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # checkerboard background
        cs = 8
        for row in range(0, h, cs):
            for col in range(0, w, cs):
                is_white = ((row // cs) + (col // cs)) % 2 == 0
                c = QColor(220, 220, 220) if is_white else QColor(255, 255, 255)
                p.fillRect(col, row, cs, cs, c)

        # draw sample text
        preview_text = "Preview"
        font = QFont()
        font.setPointSizeF(24)
        font.setBold(True)
        p.setFont(font)

        # compute font pixel size for shadow/offset scaling (matches repaint_background)
        from utils.fontformat import pt2px
        font_size_px = pt2px(24)

        fm = p.fontMetrics()
        text_rect = fm.boundingRect(preview_text)
        text_w = text_rect.width() + 20
        text_h = text_rect.height() + 20

        # render text to pixmap
        text_pixmap = QPixmap(text_w, text_h)
        text_pixmap.fill(Qt.GlobalColor.transparent)
        tp = QPainter(text_pixmap)
        tp.setRenderHint(QPainter.RenderHint.Antialiasing)
        tp.setFont(font)
        tp.setPen(QPen(QColor(*[int(c) for c in self.text_color])))
        tp.drawText(QRectF(0, 0, text_w, text_h), Qt.AlignmentFlag.AlignCenter, preview_text)
        tp.end()

        # compute offset for center placement
        ox = int((w - text_w) / 2)
        oy = int((h - text_h) / 2)

        # shadow (scale by font pixel size, matching repaint_background)
        shadow_enabled = self.shadow_radius > 0 and self.shadow_strength > 0
        if shadow_enabled:
            r = int(round(self.shadow_radius * font_size_px))
            sx = int(self.shadow_offset[0] * font_size_px)
            sy = int(self.shadow_offset[1] * font_size_px)
            shadow_pm, _ = apply_shadow_effect(text_pixmap, self.shadow_color, self.shadow_strength, r)
            p.drawPixmap(ox + sx, oy + sy, shadow_pm)

        # gradient or flat color text
        if self.gradient_enabled:
            rad = math.radians(self.gradient_angle)
            dx = math.cos(rad)
            dy = math.sin(rad)
            cx = w / 2
            cy = h / 2
            size = max(w, h) * self.gradient_size
            # match get_text_gradient: setStart(cx-dx*r, cy-dy*r), setFinalStop(cx+dx*r, cy+dy*r)
            grad = QLinearGradient(cx - dx * size, cy - dy * size,
                                   cx + dx * size, cy + dy * size)
            grad.setColorAt(0, QColor(*[int(c) for c in self.gradient_start]))
            grad.setColorAt(1, QColor(*[int(c) for c in self.gradient_end]))
            p.setPen(QPen(QBrush(grad), 0))
            p.setFont(font)
            p.drawText(QRectF(ox, oy, text_w, text_h),
                       Qt.AlignmentFlag.AlignCenter, preview_text)
        else:
            # flat text on top
            p.drawPixmap(ox, oy, text_pixmap)

        p.end()


class ColorButton(QPushButton):
    """Button that shows a color swatch and opens ColorPickerDialog on click."""

    colorChanged = Signal(list)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = list(color) if isinstance(color, (list, tuple)) else [color.red(), color.green(), color.blue()]
        self.setFixedSize(32, 22)
        self.clicked.connect(self._pick)
        self._update_style()

    def _update_style(self):
        r, g, b = [int(c) for c in self._color]
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b}); "
            f"border: 1px solid #888; border-radius: 3px; }}"
        )

    def set_color(self, color):
        if isinstance(color, (list, tuple)):
            self._color = list(color)
        else:
            self._color = [color.red(), color.green(), color.blue()]
        self._update_style()
        self.colorChanged.emit(self._color)

    def color(self):
        return self._color

    def _pick(self):
        c = QColor(*[int(v) for v in self._color])
        dlg = ColorPickerDialog(c, self.window())
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            self.set_color(dlg.get_color())


class ShadowGradientDialog(QDialog):
    """PS-style dialog for shadow and gradient settings with live preview."""

    applied = Signal(dict, dict)

    def __init__(self, font_format: FontFormat, tab='shadow', text_color=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Shadow & Gradient"))
        self.setFixedSize(520, 520)

        self._shadow_color = list(font_format.shadow_color)
        self._gradient_start = list(font_format.gradient_start_color)
        self._gradient_end = list(font_format.gradient_end_color)
        self._text_color = text_color if text_color else list(font_format.frgb)

        self._setup_ui(font_format, tab)

    # ── UI setup ────────────────────────────────────────────

    def _setup_ui(self, fmt: FontFormat, initial_tab: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # tabs
        self.tabs = QTabWidget()
        self._setup_shadow_tab(fmt)
        self._setup_gradient_tab(fmt)
        self.tabs.setCurrentIndex(0 if initial_tab == 'shadow' else 1)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # live preview
        self.preview = ShadowGradientPreview()
        self._update_preview()
        layout.addWidget(self.preview)

        # buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        apply_btn = QPushButton(self.tr("Apply"))
        ok_btn.setFixedWidth(80)
        cancel_btn.setFixedWidth(80)
        apply_btn.setFixedWidth(80)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn.clicked.connect(self.reject)
        apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(apply_btn)
        layout.addLayout(btn_layout)

    def _setup_shadow_tab(self, fmt: FontFormat):
        page = QWidget()
        hlayout = QHBoxLayout(page)
        hlayout.setSpacing(12)

        # left: clock dial
        self.shadow_dial = ClockDial(mode='shadow')
        self.shadow_dial.setColor(fmt.shadow_color)
        self.shadow_dial.setMinimumSize(170, 170)

        # offset from font format (screen Y inverted vs math coords)
        ox, oy = fmt.shadow_offset[0], fmt.shadow_offset[1]
        dist = math.sqrt(ox * ox + oy * oy) / 0.6
        dist = max(0.0, min(1.0, dist))
        angle = math.degrees(math.atan2(-oy, ox))
        if angle < 0:
            angle += 360.0
        self.shadow_dial.setAngle(angle)
        self.shadow_dial.setDistance(dist)

        self.shadow_dial.valueChanged.connect(self._on_shadow_dial_changed)
        self.shadow_dial.angleChanged.connect(lambda a: self._on_shadow_value_changed())
        self.shadow_dial.distanceChanged.connect(lambda d: self._on_shadow_value_changed())
        hlayout.addWidget(self.shadow_dial)

        # right: controls
        ctrl = QVBoxLayout()
        ctrl.setSpacing(6)

        # color
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel(self.tr("Color")))
        self.shadow_color_btn = ColorButton(fmt.shadow_color)
        self.shadow_color_btn.colorChanged.connect(self._on_shadow_color_changed)
        color_row.addWidget(self.shadow_color_btn)
        color_row.addStretch()
        ctrl.addLayout(color_row)

        # strength
        ctrl.addWidget(QLabel(self.tr("Strength")))
        self.strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.strength_slider.setRange(0, 100)
        self.strength_slider.setValue(int(fmt.shadow_strength / 3.0 * 100))
        self.strength_slider.setFixedWidth(150)
        self.strength_slider.valueChanged.connect(self._on_shadow_value_changed)
        self.strength_label = QLabel(f"{fmt.shadow_strength:.2f}")
        sr = QHBoxLayout()
        sr.addWidget(self.strength_slider)
        sr.addWidget(self.strength_label)
        ctrl.addLayout(sr)

        # blur radius
        ctrl.addWidget(QLabel(self.tr("Radius")))
        self.radius_slider = QSlider(Qt.Orientation.Horizontal)
        self.radius_slider.setRange(0, 100)
        self.radius_slider.setValue(int(fmt.shadow_radius / 2.0 * 100))
        self.radius_slider.setFixedWidth(150)
        self.radius_slider.valueChanged.connect(self._on_shadow_value_changed)
        self.radius_label = QLabel(f"{fmt.shadow_radius:.2f}")
        rr = QHBoxLayout()
        rr.addWidget(self.radius_slider)
        rr.addWidget(self.radius_label)
        ctrl.addLayout(rr)

        # computed X/Y offset
        ctrl.addWidget(QLabel(self.tr("Offset")))
        self.offset_xy_label = QLabel(f"X: {ox:.2f}  Y: {oy:.2f}")
        ctrl.addWidget(self.offset_xy_label)

        ctrl.addStretch()
        hlayout.addLayout(ctrl)
        self.tabs.addTab(page, self.tr("Shadow"))

    def _setup_gradient_tab(self, fmt: FontFormat):
        page = QWidget()
        hlayout = QHBoxLayout(page)
        hlayout.setSpacing(12)

        # left: clock dial
        self.gradient_dial = ClockDial(mode='gradient')
        self.gradient_dial.setColor(fmt.gradient_start_color)
        self.gradient_dial.setMinimumSize(170, 170)
        self.gradient_dial.setAngle(fmt.gradient_angle)
        self.gradient_dial.angleChanged.connect(lambda a: self._on_gradient_value_changed())
        hlayout.addWidget(self.gradient_dial)

        # right: controls
        ctrl = QVBoxLayout()
        ctrl.setSpacing(6)

        # enable
        self.gradient_enable_cb = QCheckBox(self.tr("Enable"))
        self.gradient_enable_cb.setChecked(fmt.gradient_enabled)
        self.gradient_enable_cb.toggled.connect(self._on_gradient_value_changed)
        ctrl.addWidget(self.gradient_enable_cb)

        # start color
        sc_row = QHBoxLayout()
        sc_row.addWidget(QLabel(self.tr("Start Color")))
        self.gradient_start_btn = ColorButton(fmt.gradient_start_color)
        self.gradient_start_btn.colorChanged.connect(self._on_gradient_start_changed)
        sc_row.addWidget(self.gradient_start_btn)
        sc_row.addStretch()
        ctrl.addLayout(sc_row)

        # end color
        ec_row = QHBoxLayout()
        ec_row.addWidget(QLabel(self.tr("End Color")))
        self.gradient_end_btn = ColorButton(fmt.gradient_end_color)
        self.gradient_end_btn.colorChanged.connect(self._on_gradient_end_changed)
        ec_row.addWidget(self.gradient_end_btn)
        ec_row.addStretch()
        ctrl.addLayout(ec_row)

        # size
        ctrl.addWidget(QLabel(self.tr("Size")))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(50, 200)  # 0.5 to 2.0
        self.size_slider.setValue(int(fmt.gradient_size * 100))
        self.size_slider.setFixedWidth(150)
        self.size_slider.valueChanged.connect(self._on_gradient_value_changed)
        self.size_label = QLabel(f"{fmt.gradient_size:.2f}")
        szr = QHBoxLayout()
        szr.addWidget(self.size_slider)
        szr.addWidget(self.size_label)
        ctrl.addLayout(szr)

        ctrl.addStretch()
        hlayout.addLayout(ctrl)
        self.tabs.addTab(page, self.tr("Gradient"))

    # ── Shadow handlers ─────────────────────────────────────

    def _shadow_strength(self):
        return self.strength_slider.value() / 100.0 * 3.0

    def _shadow_radius(self):
        return self.radius_slider.value() / 100.0 * 2.0

    def _shadow_offset(self):
        angle = self.shadow_dial.angle()
        dist = self.shadow_dial.distance()
        rad = math.radians(angle)
        ox = round(math.cos(rad) * dist * 0.6, 2)
        oy = round(-math.sin(rad) * dist * 0.6, 2)
        return [ox, oy]

    def _on_shadow_dial_changed(self):
        ox, oy = self._shadow_offset()
        self.offset_xy_label.setText(f"X: {ox:.2f}  Y: {oy:.2f}")
        self._update_preview()

    def _on_shadow_value_changed(self, *args):
        self.strength_label.setText(f"{self._shadow_strength():.2f}")
        self.radius_label.setText(f"{self._shadow_radius():.2f}")
        self._on_shadow_dial_changed()

    def _on_shadow_color_changed(self, color):
        self._shadow_color = list(color)
        self.shadow_dial.setColor(color)
        self._update_preview()

    # ── Gradient handlers ───────────────────────────────────

    def _gradient_size(self):
        return self.size_slider.value() / 100.0

    def _on_gradient_value_changed(self, *args):
        self.size_label.setText(f"{self._gradient_size():.2f}")
        self._update_preview()

    def _on_gradient_start_changed(self, color):
        self._gradient_start = list(color)
        if self.tabs.currentIndex() == 1:
            self.gradient_dial.setColor(color)
        self._update_preview()

    def _on_gradient_end_changed(self, color):
        self._gradient_end = list(color)
        self._update_preview()

    def _on_tab_changed(self, idx):
        self._update_preview()

    # ── Preview ─────────────────────────────────────────────

    def _update_preview(self):
        self.preview.set_params(
            shadow_radius=self._shadow_radius(),
            shadow_strength=self._shadow_strength(),
            shadow_color=self._shadow_color,
            shadow_offset=self._shadow_offset(),
            gradient_enabled=self.gradient_enable_cb.isChecked(),
            gradient_start=self._gradient_start,
            gradient_end=self._gradient_end,
            gradient_angle=self.gradient_dial.angle(),
            gradient_size=self._gradient_size(),
            text_color=self._text_color,
        )

    # ── Result accessors ────────────────────────────────────

    def get_shadow_params(self) -> dict:
        return {
            'shadow_radius': self._shadow_radius(),
            'shadow_strength': self._shadow_strength(),
            'shadow_color': self._shadow_color,
            'shadow_offset': self._shadow_offset(),
        }

    def get_gradient_params(self) -> dict:
        return {
            'gradient_enabled': self.gradient_enable_cb.isChecked(),
            'gradient_start_color': self._gradient_start,
            'gradient_end_color': self._gradient_end,
            'gradient_angle': self.gradient_dial.angle(),
            'gradient_size': self._gradient_size(),
        }

    # ── Buttons ─────────────────────────────────────────────

    def _on_apply(self):
        self.applied.emit(self.get_shadow_params(), self.get_gradient_params())

    def _on_ok(self):
        self.accept()
