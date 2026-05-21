"""Photoshop-style color picker dialog with user-editable palette."""

import json
from pathlib import Path

from qtpy.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QSpinBox, QLineEdit, QWidget)
from qtpy.QtCore import Qt, Signal, QRectF, QRegularExpression
from qtpy.QtGui import (QColor, QPainter, QBrush, QPen, QMouseEvent,
                         QLinearGradient, QPixmap, QRegularExpressionValidator)

# ── Default palette ───────────────────────────────────────

_PALETTE_FILE = Path(__file__).parent.parent.parent / 'config' / 'palette.json'

_DEFAULT_PALETTE = [
    '#000000', '#434343', '#6B6B6B', '#969696', '#C2C2C2', '#E0E0E0', '#F5F5F5', '#FFFFFF',
    '#ff0000', '#cc3300', '#ff6600', '#ff9900', '#ffcc00', '#ffff00', '#99cc00', '#66cc00',
    '#33cc33', '#00cc66', '#009999', '#006699', '#0033cc', '#0000ff', '#3333ff', '#6633cc',
    '#9933cc', '#cc33cc', '#ff3399', '#ff6699', '#cc6666', '#996633',
]

_SWATCH_SIZE = 20
_SWATCH_GAP = 2

_NO_BTN_STYLE = '''
QSpinBox {
    background: rgba(128,128,128,0.13);
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 4px;
    padding: 1px 2px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 0px;
    height: 0px;
}
QSpinBox::disabled {
    background: transparent;
    border: 1px solid rgba(128,128,128,0.1);
}
'''

# ── Palette I/O ───────────────────────────────────────────

def _load_palette():
    try:
        if _PALETTE_FILE.exists():
            data = json.loads(_PALETTE_FILE.read_text(encoding='utf-8'))
            if isinstance(data, list) and all(isinstance(s, str) for s in data):
                return [QColor(c) for c in data if QColor(c).isValid()]
    except Exception:
        pass
    return [QColor(c) for c in _DEFAULT_PALETTE]


def _save_palette(colors):
    try:
        _PALETTE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [c.name(QColor.NameFormat.HexRgb) for c in colors]
        _PALETTE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass

# ── Widgets ───────────────────────────────────────────────

class _ColorSquare(QWidget):
    """2D saturation-value field at a fixed hue."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._sat = 1.0
        self._val = 1.0
        self.setMinimumSize(180, 180)

    def set_hsv(self, h, s, v):
        self._hue = h
        self._sat = max(0.0, min(1.0, s))
        self._val = max(0.0, min(1.0, v))
        self.update()

    def sat_value(self):
        return self._sat, self._val

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        hue_color = QColor.fromHsvF(self._hue, 1.0, 1.0)
        p.fillRect(0, 0, w, h, hue_color)

        sg = QLinearGradient(0, 0, w, 0)
        sg.setColorAt(0.0, Qt.GlobalColor.white)
        sg.setColorAt(1.0, Qt.GlobalColor.transparent)
        p.fillRect(0, 0, w, h, sg)

        vg = QLinearGradient(0, 0, 0, h)
        vg.setColorAt(0.0, Qt.GlobalColor.transparent)
        vg.setColorAt(1.0, Qt.GlobalColor.black)
        p.fillRect(0, 0, w, h, vg)

        cx = int(self._sat * (w - 1))
        cy = int((1.0 - self._val) * (h - 1))
        p.setPen(QPen(Qt.GlobalColor.white, 2))
        p.drawLine(cx - 7, cy, cx - 2, cy)
        p.drawLine(cx + 2, cy, cx + 7, cy)
        p.drawLine(cx, cy - 7, cx, cy - 2)
        p.drawLine(cx, cy + 2, cx, cy + 7)
        p.setPen(QPen(Qt.GlobalColor.black, 1))
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

    def mousePressEvent(self, e: QMouseEvent):
        self._update_pos(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        self._update_pos(e)

    def _update_pos(self, e: QMouseEvent):
        w, h = self.width(), self.height()
        x = max(0, min(int(e.position().x()), w - 1)) if w > 1 else 0
        y = max(0, min(int(e.position().y()), h - 1)) if h > 1 else 0
        self._sat = x / (w - 1) if w > 1 else 1.0
        self._val = 1.0 - y / (h - 1) if h > 1 else 1.0
        self.update()
        self.changed.emit()


class _HueSlider(QWidget):
    """Vertical hue slider."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self.setFixedWidth(22)

    def hue(self):
        return self._hue

    def set_hue(self, h):
        self._hue = max(0.0, min(1.0, h))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, h)
        for pos, hue in [(0.0, 0.0), (1/6, 1/6), (2/6, 2/6), (3/6, 3/6),
                         (4/6, 4/6), (5/6, 5/6), (1.0, 1.0)]:
            grad.setColorAt(pos, QColor.fromHsvF(hue, 1.0, 1.0))
        p.fillRect(2, 0, w - 4, h, grad)

        hy = int(self._hue * (h - 1))
        p.setPen(QPen(Qt.GlobalColor.white, 2))
        p.setBrush(Qt.GlobalColor.transparent)
        p.drawRect(0, hy - 5, w - 1, 10)

    def mousePressEvent(self, e: QMouseEvent):
        self._update_pos(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        self._update_pos(e)

    def _update_pos(self, e: QMouseEvent):
        h = self.height()
        self._hue = max(0.0, min(1.0, e.position().y() / (h - 1))) if h > 1 else 0.0
        self.update()
        self.changed.emit()


class _SwatchLabel(QWidget):
    """Small color swatch for preview."""

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = QColor(color)

    def set_color(self, c: QColor):
        self._color = QColor(c)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rf = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QBrush(self._color))
        p.setPen(QPen(QColor(128, 128, 128, 100), 1))
        p.drawRoundedRect(rf, 3, 3)


class _PaletteGrid(QWidget):
    """User-editable color palette: left-click to select, right-click to store current."""

    colorSelected = Signal(QColor)
    storeRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cols = 10
        self._colors = _load_palette()
        self._update_size()
        self.setMouseTracking(True)
        self._highlight = -1

    def _update_size(self):
        self._rows = (len(self._colors) + self._cols - 1) // self._cols
        cell = _SWATCH_SIZE + _SWATCH_GAP
        self.setFixedSize(self._cols * cell + _SWATCH_GAP,
                          self._rows * cell + _SWATCH_GAP)

    def _cell_rect(self, idx):
        cell = _SWATCH_SIZE + _SWATCH_GAP
        col = idx % self._cols
        row = idx // self._cols
        return QRectF(_SWATCH_GAP + col * cell, _SWATCH_GAP + row * cell,
                      _SWATCH_SIZE, _SWATCH_SIZE)

    def _idx_at(self, pos):
        for i in range(len(self._colors)):
            if self._cell_rect(i).contains(pos):
                return i
        return -1

    def set_at(self, idx: int, color: QColor):
        if 0 <= idx < len(self._colors):
            self._colors[idx] = QColor(color)
            _save_palette(self._colors)
            self.update()

    def mousePressEvent(self, e: QMouseEvent):
        idx = self._idx_at(e.position())
        if idx < 0:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.colorSelected.emit(self._colors[idx])
        elif e.button() == Qt.MouseButton.RightButton:
            self.storeRequested.emit(idx)

    def mouseMoveEvent(self, e: QMouseEvent):
        idx = self._idx_at(e.position())
        if idx != self._highlight:
            self._highlight = idx
            self.update()

    def leaveEvent(self, e):
        self._highlight = -1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, c in enumerate(self._colors):
            rf = self._cell_rect(i)
            p.setBrush(QBrush(c))
            p.setPen(QPen(QColor(180, 180, 180, 120), 1))
            p.drawRoundedRect(rf, 3, 3)

        if self._highlight >= 0:
            rf = self._cell_rect(self._highlight)
            p.setPen(QPen(QColor(30, 147, 229), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rf.adjusted(-1, -1, 1, 1), 4, 4)


# ── Dialog ────────────────────────────────────────────────

class ColorPickerDialog(QDialog):
    """Photoshop-style color picker dialog with editable palette."""

    def __init__(self, current: QColor, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Color Picker"))
        self.setFixedSize(400, 380)

        self._result = QColor(current)
        self._old = QColor(current)

        h, s, v, a = current.getHsvF()
        self._hue = h if h >= 0 else 0.0
        self._sat = s
        self._val = v

        self._setup_ui()
        self._sync_all_from_hsv()

    def get_color(self):
        return self._result

    # ── UI ────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.setSpacing(8)

        self.square = _ColorSquare(self)
        self.square.set_hsv(self._hue, self._sat, self._val)
        self.square.changed.connect(self._on_square_changed)
        top.addWidget(self.square)

        self.hue_slider = _HueSlider(self)
        self.hue_slider.set_hue(self._hue)
        self.hue_slider.changed.connect(self._on_hue_changed)
        top.addWidget(self.hue_slider)

        # Numeric inputs — each row is label + spinbox, center-aligned
        def make_spin(lo, hi, w=60):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setFixedWidth(w)
            s.setStyleSheet(_NO_BTN_STYLE)
            return s

        self.h_spin = make_spin(0, 360)
        self.s_spin = make_spin(0, 100)
        self.v_spin = make_spin(0, 100)
        self.r_spin = make_spin(0, 255)
        self.g_spin = make_spin(0, 255)
        self.b_spin = make_spin(0, 255)

        self.h_spin.setSuffix('°')
        self.s_spin.setSuffix('%')
        self.v_spin.setSuffix('%')

        self.h_spin.valueChanged.connect(lambda v: self._on_hsv_spin('h', v / 360.0))
        self.s_spin.valueChanged.connect(lambda v: self._on_hsv_spin('s', v / 100.0))
        self.v_spin.valueChanged.connect(lambda v: self._on_hsv_spin('v', v / 100.0))
        self.r_spin.valueChanged.connect(lambda v: self._on_rgb_spin())
        self.g_spin.valueChanged.connect(lambda v: self._on_rgb_spin())
        self.b_spin.valueChanged.connect(lambda v: self._on_rgb_spin())

        num_col = QVBoxLayout()
        num_col.setSpacing(3)

        def lbl_row(label, widget):
            r = QHBoxLayout()
            r.setSpacing(4)
            lbl = QLabel(label)
            lbl.setFixedWidth(14)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(lbl)
            r.addWidget(widget)
            return r

        num_col.addLayout(lbl_row('H', self.h_spin))
        num_col.addLayout(lbl_row('S', self.s_spin))
        num_col.addLayout(lbl_row('V', self.v_spin))
        num_col.addWidget(QLabel('─' * 4), alignment=Qt.AlignmentFlag.AlignCenter)
        num_col.addLayout(lbl_row('R', self.r_spin))
        num_col.addLayout(lbl_row('G', self.g_spin))
        num_col.addLayout(lbl_row('B', self.b_spin))
        num_col.addWidget(QLabel('─' * 4), alignment=Qt.AlignmentFlag.AlignCenter)

        hex_row = QHBoxLayout()
        hex_row.setSpacing(4)
        hex_lbl = QLabel('#')
        hex_lbl.setFixedWidth(14)
        hex_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hex_row.addWidget(hex_lbl)
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(6)
        self.hex_edit.setFixedWidth(78)
        self.hex_edit.setStyleSheet('''
            QLineEdit {
                background: rgba(128,128,128,0.13);
                border: 1px solid rgba(128,128,128,0.25);
                border-radius: 4px;
                padding: 2px 4px;
            }
        ''')
        rx = QRegularExpression('[0-9A-Fa-f]{0,6}')
        self.hex_edit.setValidator(QRegularExpressionValidator(rx))
        self.hex_edit.textChanged.connect(self._on_hex_changed)
        hex_row.addWidget(self.hex_edit)
        num_col.addLayout(hex_row)

        # Center the numeric column in the available space
        wrapper = QHBoxLayout()
        wrapper.addStretch()
        wrapper.addLayout(num_col)
        wrapper.addStretch()

        top.addLayout(wrapper)
        layout.addLayout(top)

        # Bottom: swatches + palette + buttons
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        swatch_col = QVBoxLayout()
        swatch_col.setSpacing(2)
        self._old_swatch = _SwatchLabel(self._old, self)
        self._new_swatch = _SwatchLabel(self._result, self)
        self._old_swatch.setFixedSize(40, 24)
        self._new_swatch.setFixedSize(40, 24)
        swatch_col.addWidget(QLabel(self.tr('Old')))
        swatch_col.addWidget(self._old_swatch)
        swatch_col.addWidget(QLabel(self.tr('New')))
        swatch_col.addWidget(self._new_swatch)
        bottom.addLayout(swatch_col)

        self._palette = _PaletteGrid(self)
        self._palette.colorSelected.connect(self._on_palette_selected)
        self._palette.storeRequested.connect(self._on_palette_store)
        bottom.addWidget(self._palette)

        bottom.addStretch()

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        ok_btn = QPushButton(self.tr('OK'))
        cancel_btn = QPushButton(self.tr('Cancel'))
        ok_btn.setFixedWidth(80)
        cancel_btn.setFixedWidth(80)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_col.addWidget(ok_btn)
        btn_col.addWidget(cancel_btn)
        bottom.addLayout(btn_col)

        layout.addLayout(bottom)

    # ── Sync ──────────────────────────────────────────────

    def _block_spins(self, block: bool):
        for s in [self.h_spin, self.s_spin, self.v_spin,
                  self.r_spin, self.g_spin, self.b_spin]:
            s.blockSignals(block)
        self.hex_edit.blockSignals(block)

    def _sync_all_from_hsv(self):
        self._block_spins(True)
        c = QColor.fromHsvF(self._hue, self._sat, self._val)
        self.h_spin.setValue(int(self._hue * 360))
        self.s_spin.setValue(int(self._sat * 100))
        self.v_spin.setValue(int(self._val * 100))
        self.r_spin.setValue(c.red())
        self.g_spin.setValue(c.green())
        self.b_spin.setValue(c.blue())
        self.hex_edit.setText(c.name()[1:].upper())
        self._block_spins(False)

        self.square.set_hsv(self._hue, self._sat, self._val)
        self._result = c
        self._new_swatch.set_color(c)

    def _on_square_changed(self):
        self._sat, self._val = self.square.sat_value()
        self._sync_all_from_hsv()

    def _on_hue_changed(self):
        self._hue = self.hue_slider.hue()
        self._sync_all_from_hsv()

    def _on_hsv_spin(self, channel, value):
        if channel == 'h':
            self._hue = value
            self.hue_slider.set_hue(value)
        elif channel == 's':
            self._sat = value
        elif channel == 'v':
            self._val = value
        self._sync_all_from_hsv()

    def _on_rgb_spin(self):
        c = QColor(self.r_spin.value(), self.g_spin.value(), self.b_spin.value())
        h, s, v, a = c.getHsvF()
        self._hue = h if h >= 0 else 0.0
        self._sat = s
        self._val = v
        self.hue_slider.set_hue(self._hue)
        self.square.set_hsv(self._hue, self._sat, self._val)
        self._sync_all_from_hsv()

    def _on_hex_changed(self, text):
        if len(text) == 6:
            try:
                c = QColor('#' + text)
                if c.isValid():
                    h, s, v, a = c.getHsvF()
                    self._hue = h if h >= 0 else 0.0
                    self._sat = s
                    self._val = v
                    self.hue_slider.set_hue(self._hue)
                    self.square.set_hsv(self._hue, self._sat, self._val)
                    self._sync_all_from_hsv()
            except Exception:
                pass

    def _on_palette_selected(self, color: QColor):
        self.set_color_direct(color)

    def _on_palette_store(self, idx: int):
        self._palette.set_at(idx, self._result)

    def set_color_direct(self, color: QColor):
        h, s, v, a = color.getHsvF()
        self._hue = h if h >= 0 else 0.0
        self._sat = s
        self._val = v
        self.hue_slider.set_hue(self._hue)
        self.square.set_hsv(self._hue, self._sat, self._val)
        self._sync_all_from_hsv()
