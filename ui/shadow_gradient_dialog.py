import math

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
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
    """Live preview of shadow/gradient effect on sample text with solid bg."""

    bg_color_changed = Signal(list)

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
        self.stroke_width = 0.0
        self.stroke_color = [0, 0, 0]
        self.shadow_include_stroke = False
        self._preview_text = "Preview"
        self._bg_color = [128, 128, 128]  # PS 50% gray default
        self.setMinimumHeight(90)

        # corner color swatch button for background
        self.bg_btn = QPushButton(self)
        self.bg_btn.setFixedSize(22, 22)
        self.bg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bg_btn.setToolTip(self.tr("Background color"))
        self.bg_btn.clicked.connect(self._pick_bg_color)
        self._update_bg_btn_style()

    def _update_bg_btn_style(self):
        r, g, b = [int(c) for c in self._bg_color]
        self.bg_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b}); "
            f"border: 2px solid #555; border-radius: 3px; }}"
            f"QPushButton:hover {{ border-color: #fff; }}"
        )

    def _pick_bg_color(self):
        c = QColor(*[max(0, min(255, int(v))) for v in self._bg_color])
        dlg = ColorPickerDialog(c, self.window())
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            picked = dlg.get_color()
            self._bg_color = [picked.red(), picked.green(), picked.blue()]
            self._update_bg_btn_style()
            self.bg_color_changed.emit(self._bg_color)
            self.update()

    def resizeEvent(self, event):
        bs = 22
        self.bg_btn.move(self.width() - bs - 4, self.height() - bs - 4)
        super().resizeEvent(event)

    def set_params(
        self,
        shadow_radius,
        shadow_strength,
        shadow_color,
        shadow_offset,
        gradient_enabled,
        gradient_start,
        gradient_end,
        gradient_angle,
        gradient_size,
        text_color=None,
        stroke_width=None,
        stroke_color=None,
        shadow_include_stroke=None,
    ):
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
        if stroke_width is not None:
            self.stroke_width = stroke_width
        if stroke_color is not None:
            self.stroke_color = stroke_color
        if shadow_include_stroke is not None:
            self.shadow_include_stroke = shadow_include_stroke
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # solid background (default PS 50% gray)
        c = QColor(*[max(0, min(255, int(v))) for v in self._bg_color])
        p.fillRect(0, 0, w, h, c)

        # compute font pixel size for shadow/offset scaling (matches repaint_background)
        from utils.fontformat import pt2px

        font_size_px = pt2px(24)

        # compute text brush: gradient or flat color
        if self.gradient_enabled:
            rad = math.radians(self.gradient_angle)
            dx = math.cos(rad)
            dy = math.sin(rad)
            text_w = w
            text_h = h
            cx = text_w / 2
            cy = text_h / 2
            size_val = max(text_w, text_h) * self.gradient_size
            grad = QLinearGradient(
                cx - dx * size_val,
                cy - dy * size_val,
                cx + dx * size_val,
                cy + dy * size_val,
            )
            grad.setColorAt(0, QColor(*[max(0, min(255, int(c))) for c in self.gradient_start]))
            grad.setColorAt(1, QColor(*[max(0, min(255, int(c))) for c in self.gradient_end]))
            fill_brush = QBrush(grad)
        else:
            fill_brush = QBrush(QColor(*[max(0, min(255, int(c))) for c in self.text_color]))

        # render text with stroke for display
        display_pm = self._render_text_pixmap(
            include_stroke=True, fill_brush=fill_brush
        )

        # shadow source (always uses flat fill — only the alpha mask matters for shadow)
        shadow_src_pm = self._render_text_pixmap(
            include_stroke=self.shadow_include_stroke
        )

        # compute offset for center placement
        ox = int((w - display_pm.width()) / 2)
        oy = int((h - display_pm.height()) / 2)

        # shadow
        shadow_enabled = self.shadow_radius > 0 and self.shadow_strength > 0
        if shadow_enabled:
            r = int(round(self.shadow_radius * font_size_px))
            sx = int(self.shadow_offset[0] * font_size_px)
            sy = int(self.shadow_offset[1] * font_size_px)
            shadow_pm, _ = apply_shadow_effect(
                shadow_src_pm, self.shadow_color, self.shadow_strength, r
            )
            p.drawPixmap(ox + sx, oy + sy, shadow_pm)

        # gradient or flat color text (always with stroke for display)
        p.drawPixmap(ox, oy, display_pm)

        p.end()

    def _render_text_pixmap(self, include_stroke=True, fill_brush=None) -> QPixmap:
        """Render preview text into a transparent pixmap.

        Args:
            include_stroke: If True, renders stroke outline when stroke_width > 0.
                            If False, text-only (used for PS-compatible shadow source).
            fill_brush: QBrush for text fill. If None, uses self.text_color (flat).
        """
        font = QFont()
        font.setPointSizeF(24)
        font.setBold(True)

        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(self._preview_text)
        text_w = text_rect.width() + 20
        text_h = text_rect.height() + 20

        pm = QPixmap(text_w, text_h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font)

        if fill_brush is None:
            fill_brush = QBrush(QColor(*[max(0, min(255, int(c))) for c in self.text_color]))

        if self.stroke_width > 0 and include_stroke:
            # Path-based stroke+fill rendering to match main app behavior
            from utils.fontformat import pt2px

            path = QPainterPath()
            # center text baseline in pixmap
            x = (text_w - text_rect.width()) / 2 - text_rect.x()
            y = (text_h + fm.ascent()) / 2
            path.addText(x, y, font, self._preview_text)
            sw = pt2px(24) * self.stroke_width
            pen = QPen(
                QColor(*[max(0, min(255, int(c))) for c in self.stroke_color]),
                sw,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            p.strokePath(path, pen)
            p.fillPath(path, fill_brush)
        else:
            p.setPen(QPen(fill_brush, 0))
            p.drawText(
                QRectF(0, 0, text_w, text_h),
                Qt.AlignmentFlag.AlignCenter,
                self._preview_text,
            )
        p.end()
        return pm


class ColorButton(QPushButton):
    """Button that shows a color swatch and opens ColorPickerDialog on click."""

    colorChanged = Signal(list)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = (
            list(color)
            if isinstance(color, (list, tuple))
            else [color.red(), color.green(), color.blue()]
        )
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
        c = QColor(*[max(0, min(255, int(v))) for v in self._color])
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


class ShadowGradientDialog(QDialog):
    """PS-style dialog for shadow and gradient settings with live preview."""

    applied = Signal(dict, dict)

    def __init__(
        self,
        font_format: FontFormat,
        tab="shadow",
        text_color=None,
        shadow_include_stroke=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Shadow & Gradient"))
        self.setFixedSize(520, 520)

        self._shadow_color = list(font_format.shadow_color)
        self._gradient_start = list(font_format.gradient_start_color)
        self._gradient_end = list(font_format.gradient_end_color)
        self._text_color = text_color if text_color else list(font_format.frgb)
        self._stroke_width = font_format.stroke_width
        self._stroke_color = list(font_format.srgb)
        self._shadow_include_stroke = (
            shadow_include_stroke
            if shadow_include_stroke is not None
            else font_format.shadow_include_stroke
        )

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
        self.tabs.setCurrentIndex(0 if initial_tab == "shadow" else 1)
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
        self.shadow_dial = ClockDial(mode="shadow")
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
        self.shadow_dial.distanceChanged.connect(
            lambda d: self._on_shadow_value_changed()
        )
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
        self.strength_slider.setValue(int(fmt.shadow_strength * 100))
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
        self.radius_slider.setValue(int(fmt.shadow_radius * 100))
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

        # include stroke in shadow
        ctrl.addSpacing(4)
        cb_row = QHBoxLayout()
        self.include_stroke_cb = QCheckBox(self.tr("Include stroke in shadow"))
        self.include_stroke_cb.setChecked(self._shadow_include_stroke)
        self.include_stroke_cb.toggled.connect(self._update_preview)
        cb_row.addWidget(self.include_stroke_cb)
        global_lbl = QLabel(self.tr("(global)"))
        global_lbl.setStyleSheet("font-size: 11px;")
        cb_row.addWidget(global_lbl)
        cb_row.addStretch()
        ctrl.addLayout(cb_row)
        ps_note = QLabel(
            self.tr(
                "Note: PSD export uses Photoshop's native drop shadow (glyph-only), regardless of this setting."
            )
        )
        ps_note.setWordWrap(True)
        ps_note.setStyleSheet("font-size: 11px;")
        ctrl.addWidget(ps_note)

        ctrl.addStretch()
        hlayout.addLayout(ctrl)
        self.tabs.addTab(page, self.tr("Shadow"))

    def _setup_gradient_tab(self, fmt: FontFormat):
        page = QWidget()
        hlayout = QHBoxLayout(page)
        hlayout.setSpacing(12)

        # left: clock dial
        self.gradient_dial = ClockDial(mode="gradient")
        self.gradient_dial.setColor(fmt.gradient_start_color)
        self.gradient_dial.setMinimumSize(170, 170)
        self.gradient_dial.setAngle(fmt.gradient_angle)
        self.gradient_dial.angleChanged.connect(
            lambda a: self._on_gradient_value_changed()
        )
        hlayout.addWidget(self.gradient_dial)

        # right: controls
        ctrl = QVBoxLayout()
        ctrl.setSpacing(6)

        # enable
        self.gradient_enable_cb = QCheckBox(self.tr("Enable"))
        self.gradient_enable_cb.setChecked(fmt.gradient_enabled)
        self.gradient_enable_cb.toggled.connect(self._on_gradient_value_changed)
        ctrl.addWidget(self.gradient_enable_cb)

        # PS-style gradient bar (clickable stops at each end)
        self.gradient_bar = GradientBar(
            fmt.gradient_start_color, fmt.gradient_end_color
        )
        self.gradient_bar.startColorChanged.connect(self._on_gradient_start_changed)
        self.gradient_bar.endColorChanged.connect(self._on_gradient_end_changed)
        ctrl.addWidget(self.gradient_bar)

        # reverse button
        rev_row = QHBoxLayout()
        rev_row.addStretch()
        reverse_btn = QPushButton(self.tr("↔ Reverse"))
        reverse_btn.setFixedWidth(100)
        reverse_btn.clicked.connect(self._on_reverse_clicked)
        rev_row.addWidget(reverse_btn)
        rev_row.addStretch()
        ctrl.addLayout(rev_row)

        # scale (PS terminology)
        ctrl.addWidget(QLabel(self.tr("Scale")))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(50, 200)  # 0.5 to 2.0
        self.scale_slider.setValue(int(fmt.gradient_size * 100))
        self.scale_slider.setFixedWidth(150)
        self.scale_slider.valueChanged.connect(self._on_gradient_value_changed)
        self.scale_label = QLabel(f"{fmt.gradient_size:.2f}")
        szr = QHBoxLayout()
        szr.addWidget(self.scale_slider)
        szr.addWidget(self.scale_label)
        ctrl.addLayout(szr)

        ctrl.addStretch()
        hlayout.addLayout(ctrl)
        self.tabs.addTab(page, self.tr("Gradient"))

    # ── Shadow handlers ─────────────────────────────────────

    def _shadow_strength(self):
        return self.strength_slider.value() / 100.0

    def _shadow_radius(self):
        return self.radius_slider.value() / 100.0

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
        return self.scale_slider.value() / 100.0

    def _on_gradient_value_changed(self, *args):
        self.scale_label.setText(f"{self._gradient_size():.2f}")
        self._update_preview()

    def _on_reverse_clicked(self):
        self._gradient_start, self._gradient_end = (
            self._gradient_end,
            self._gradient_start,
        )
        self.gradient_bar.setStartColor(self._gradient_start)
        self.gradient_bar.setEndColor(self._gradient_end)
        if self.tabs.currentIndex() == 1:
            self.gradient_dial.setColor(self._gradient_start)
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
            stroke_width=self._stroke_width,
            stroke_color=self._stroke_color,
            shadow_include_stroke=self.include_stroke_cb.isChecked(),
        )

    # ── Result accessors ────────────────────────────────────

    def get_shadow_params(self) -> dict:
        return {
            "shadow_radius": self._shadow_radius(),
            "shadow_strength": self._shadow_strength(),
            "shadow_color": self._shadow_color,
            "shadow_offset": self._shadow_offset(),
            "shadow_include_stroke": self.include_stroke_cb.isChecked(),
        }

    def get_gradient_params(self) -> dict:
        return {
            "gradient_enabled": self.gradient_enable_cb.isChecked(),
            "gradient_start_color": self._gradient_start,
            "gradient_end_color": self._gradient_end,
            "gradient_angle": self.gradient_dial.angle(),
            "gradient_size": self._gradient_size(),
        }

    # ── Buttons ─────────────────────────────────────────────

    def _on_apply(self):
        self.applied.emit(self.get_shadow_params(), self.get_gradient_params())

    def _on_ok(self):
        self.accept()
