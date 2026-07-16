"""
Font Style Manager — batch editor for text-block font styles across the project.

Left panel: list of unique styles discovered from all text blocks.
Right panel: style detail, property summary, batch-edit controls, block list.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from qtpy.QtCore import QRect, QSize, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils import shared
from utils.fontformat import (
    FontFormat,
    px2pt,
)

from .custom_widget import ColorSwatchBtn, SeparatorWidget

# ═══════════════════════════════════════════════════════════════════════
# Style discovery
# ═══════════════════════════════════════════════════════════════════════

_SIGNATURE_FIELDS = [
    "font_family",
    "font_size",
    "stroke_width",
    "frgb",
    "srgb",
    "bold",
    "italic",
    "underline",
    "alignment",
    "vertical",
    "font_weight",
    "line_spacing",
    "letter_spacing",
    "opacity",
    "shadow_radius",
    "shadow_strength",
    "shadow_color",
    "shadow_offset",
    "gradient_enabled",
    "gradient_start_color",
    "gradient_end_color",
    "gradient_angle",
    "gradient_size",
    "line_spacing_type",
]


def compute_signature(ffmt: FontFormat) -> str:
    """Return a stable 12-char hex hash for a FontFormat's visible properties."""
    parts: List[str] = []
    for fname in _SIGNATURE_FIELDS:
        val = getattr(ffmt, fname)
        if isinstance(val, (list, np.ndarray)):
            parts.append(repr(tuple(val)))
        else:
            parts.append(repr(val))
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


@dataclass
class StyleEntry:
    """One unique style discovered from the project."""

    signature: str
    fontformat: FontFormat  # representative copy
    blocks: List[Tuple[str, int]] = field(default_factory=list)
    # (pagename, block_index)

    @property
    def count(self) -> int:
        return len(self.blocks)

    @property
    def page_count(self) -> int:
        return len({p for p, _ in self.blocks})


def discover_styles(proj) -> List[StyleEntry]:
    """Scan all pages in *proj* and return StyleEntry list sorted by use count desc."""
    sig_map: Dict[str, StyleEntry] = {}
    for pname, blklist in proj.pages.items():
        for bidx, blk in enumerate(blklist):
            sig = compute_signature(blk.fontformat)
            entry = sig_map.get(sig)
            if entry is None:
                entry = StyleEntry(
                    signature=sig,
                    fontformat=blk.fontformat.deepcopy(),
                )
                sig_map[sig] = entry
            entry.blocks.append((pname, bidx))
    return sorted(sig_map.values(), key=lambda e: e.count, reverse=True)


# ═══════════════════════════════════════════════════════════════════════
# StyleList (left panel)
# ═══════════════════════════════════════════════════════════════════════


class StyleListDelegate(QStyledItemDelegate):
    """Paint a compact style card: color swatch + font name + size + count."""

    ITEM_HEIGHT = 44
    SWATCH_SIZE = 12
    PADDING_H = 8
    PADDING_V = 4

    def __init__(self, parent=None):
        super().__init__(parent)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ITEM_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect: QRect = option.rect
        is_selected = option.state & QStyle.StateFlag.State_Selected

        # Background
        if is_selected:
            painter.fillRect(rect, option.palette.highlight())
        elif index.row() % 2 == 0:
            painter.fillRect(rect, QColor(255, 255, 255, 10))
        else:
            painter.fillRect(rect, Qt.GlobalColor.transparent)

        entry: StyleEntry = index.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            painter.restore()
            return

        ffmt = entry.fontformat
        x = rect.x() + self.PADDING_H
        y_mid = rect.y() + rect.height() // 2
        swatch_y = y_mid - self.SWATCH_SIZE // 2

        # ── Color swatch ──────────────────────────────────────────
        fg = ffmt.foreground_color()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*fg))

        swatch_rect = QRect(x, swatch_y, self.SWATCH_SIZE, self.SWATCH_SIZE)
        painter.drawRoundedRect(swatch_rect, 3, 3)

        # Stroke ring if applicable
        if ffmt.stroke_width > 0:
            srgb = ffmt.stroke_color()
            pen = QPen(QColor(*srgb), 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(swatch_rect, 3, 3)
            painter.setPen(Qt.PenStyle.NoPen)

        # ── Font name + size ──────────────────────────────────────
        text_x = x + self.SWATCH_SIZE + 8
        font = QFont(ffmt.font_family)
        font.setPixelSize(12)
        painter.setFont(font)

        text_color = (
            option.palette.highlightedText().color()
            if is_selected
            else option.palette.text().color()
        )
        painter.setPen(text_color)

        name_text = f"{ffmt.font_family}  {ffmt.font_size:.0f}px"
        fm = QFontMetrics(font)
        name_elided = fm.elidedText(
            name_text, Qt.TextElideMode.ElideRight, rect.width() - text_x - 60
        )
        painter.drawText(text_x, y_mid - 3, name_elided)

        # ── Subtitle line: style flags ────────────────────────────
        sub_parts: List[str] = []
        if ffmt.bold:
            sub_parts.append("B")
        if ffmt.italic:
            sub_parts.append("I")
        if ffmt.underline:
            sub_parts.append("U")
        if ffmt.vertical:
            sub_parts.append("V")
        if ffmt.stroke_width > 0:
            sub_parts.append(f"≡{ffmt.stroke_width:.1f}")

        sub_font = QFont()
        sub_font.setPixelSize(10)
        painter.setFont(sub_font)
        sub_color = (
            QColor(160, 160, 160)
            if not is_selected
            else option.palette.highlightedText().color()
        )
        painter.setPen(sub_color)

        sub_text = "  ".join(sub_parts) if sub_parts else ""
        painter.drawText(text_x, y_mid + 12, sub_text)

        # ── Count badge (right-aligned) ───────────────────────────
        count_text = f"{entry.count}"
        count_font = QFont()
        count_font.setPixelSize(11)
        count_font.setBold(True)
        painter.setFont(count_font)
        fmc = QFontMetrics(count_font)
        count_w = fmc.horizontalAdvance(count_text) + 10
        badge_rect = QRect(
            rect.right() - count_w - self.PADDING_H,
            y_mid - 9,
            count_w,
            18,
        )
        painter.setBrush(QColor(255, 255, 255, 20))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge_rect, 9, 9)
        painter.setPen(text_color)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, count_text)

        painter.restore()


class StyleList(QListWidget):
    """Left panel: scrollable list of unique styles."""

    style_selected = Signal(str)  # signature
    style_right_clicked = Signal(str)  # signature

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StyleList")
        self.setItemDelegate(StyleListDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)

    def populate(self, entries: List[StyleEntry]):
        """Replace contents with *entries*."""
        self.clear()
        for e in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e)
            item.setSizeHint(QSize(0, StyleListDelegate.ITEM_HEIGHT))
            self.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem):
        entry: StyleEntry = item.data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self.style_selected.emit(entry.signature)

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        entry: StyleEntry = item.data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self.style_selected.emit(entry.signature)
            self.style_right_clicked.emit(entry.signature)

    def set_active_signature(self, sig: str):
        """Programmatically select the item matching *sig*."""
        for i in range(self.count()):
            item = self.item(i)
            entry: StyleEntry = item.data(Qt.ItemDataRole.UserRole)
            if entry is not None and entry.signature == sig:
                self.setCurrentRow(i)
                return


# ═══════════════════════════════════════════════════════════════════════
# StyleDetail (right panel)
# ═══════════════════════════════════════════════════════════════════════


class _SectionHeader(QLabel):
    """Bold label used as a section divider."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = self.font()
        font.setBold(True)
        font.setPixelSize(13)
        self.setFont(font)


class _PropertyRow(QWidget):
    """Label : value row with optional action button."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = QLabel(label)
        self._label.setFixedWidth(100)
        self._value = QLabel()
        self._value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._value.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)

    def set_value(self, text: str):
        self._value.setText(text)


class StyleDetail(QScrollArea):
    """Right panel: header preview, property summary, batch controls, block list."""

    # block navigation signal
    navigate_to_block = Signal(str, int)  # pagename, block_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StyleDetail")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._entry: StyleEntry | None = None
        self._proj = None
        self._scene_manager = None

        # Container
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(12, 8, 12, 8)

        # ── 3a. Header ────────────────────────────────────────────
        self._preview_label = QLabel("Aa Bb Gg")
        self._preview_label.setObjectName("StylePreviewLabel")
        self._preview_label.setFixedHeight(36)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._preview_label)

        self._header_info = QLabel()
        self._header_info.setWordWrap(True)
        self._layout.addWidget(self._header_info)

        self._layout.addWidget(SeparatorWidget())

        # ── 3b. Property Summary ──────────────────────────────────
        self._layout.addWidget(_SectionHeader(self.tr("Properties")))

        self._prop_font = _PropertyRow(self.tr("Font"))
        self._prop_size = _PropertyRow(self.tr("Size"))
        self._prop_weight = _PropertyRow(self.tr("Weight"))
        self._prop_style = _PropertyRow(self.tr("Style"))
        self._prop_foreground = _PropertyRow(self.tr("Foreground"))
        self._prop_stroke = _PropertyRow(self.tr("Stroke"))
        self._prop_alignment = _PropertyRow(self.tr("Alignment"))
        self._prop_layout = _PropertyRow(self.tr("Layout"))
        self._prop_spacing = _PropertyRow(self.tr("Spacing"))
        self._prop_effects = _PropertyRow(self.tr("Effects"))

        for pw in [
            self._prop_font,
            self._prop_size,
            self._prop_weight,
            self._prop_style,
            self._prop_foreground,
            self._prop_stroke,
            self._prop_alignment,
            self._prop_layout,
            self._prop_spacing,
            self._prop_effects,
        ]:
            self._layout.addWidget(pw)

        self._layout.addWidget(SeparatorWidget())

        # ── 3c. Batch Edit Controls ───────────────────────────────
        self._layout.addWidget(_SectionHeader(self.tr("Batch Edit")))

        # Font family
        self._family_combo = QComboBox()
        self._family_combo.setEditable(False)
        self._family_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._layout.addLayout(
            _labeled_control(self.tr("Font Family"), self._family_combo)
        )

        # Font size
        self._size_spin = QDoubleSpinBox()
        self._size_spin.setRange(1, 999)
        self._size_spin.setDecimals(1)
        self._size_spin.setSuffix(" px")
        self._layout.addLayout(_labeled_control(self.tr("Font Size"), self._size_spin))

        # Bold / Italic / Underline / Vertical
        flags_widget = QWidget()
        flags_layout = QHBoxLayout(flags_widget)
        flags_layout.setContentsMargins(0, 0, 0, 0)
        self._bold_cb = QCheckBox(self.tr("Bold"))
        self._bold_cb.setObjectName('ConfigCheckBox')
        self._italic_cb = QCheckBox(self.tr("Italic"))
        self._italic_cb.setObjectName('ConfigCheckBox')
        self._underline_cb = QCheckBox(self.tr("Underline"))
        self._underline_cb.setObjectName('ConfigCheckBox')
        self._vertical_cb = QCheckBox(self.tr("Vertical"))
        self._vertical_cb.setObjectName('ConfigCheckBox')
        flags_layout.addWidget(self._bold_cb)
        flags_layout.addWidget(self._italic_cb)
        flags_layout.addWidget(self._underline_cb)
        flags_layout.addWidget(self._vertical_cb)
        flags_layout.addStretch()
        self._layout.addLayout(_labeled_control(self.tr("Flags"), flags_widget))

        # Foreground color
        self._fg_btn = ColorSwatchBtn()
        self._fg_btn.setFixedSize(24, 24)
        self._fg_btn.clicked.connect(self._pick_fg)
        self._fg_label = QLabel()
        fg_w = QWidget()
        fg_lay = QHBoxLayout(fg_w)
        fg_lay.setContentsMargins(0, 0, 0, 0)
        fg_lay.addWidget(self._fg_btn)
        fg_lay.addWidget(self._fg_label)
        fg_lay.addStretch()
        self._layout.addLayout(_labeled_control(self.tr("Text Color"), fg_w))

        # Stroke color + width
        self._stroke_color_btn = ColorSwatchBtn()
        self._stroke_color_btn.setFixedSize(24, 24)
        self._stroke_color_btn.clicked.connect(self._pick_stroke_color)
        self._stroke_color_label = QLabel()
        self._stroke_spin = QDoubleSpinBox()
        self._stroke_spin.setRange(0, 50)
        self._stroke_spin.setDecimals(1)
        self._stroke_spin.setSuffix(" px")
        self._stroke_spin.setFixedWidth(80)
        stroke_w = QWidget()
        stroke_lay = QHBoxLayout(stroke_w)
        stroke_lay.setContentsMargins(0, 0, 0, 0)
        stroke_lay.addWidget(self._stroke_color_btn)
        stroke_lay.addWidget(self._stroke_color_label)
        stroke_lay.addWidget(QLabel(self.tr("Width:")))
        stroke_lay.addWidget(self._stroke_spin)
        stroke_lay.addStretch()
        self._layout.addLayout(_labeled_control(self.tr("Stroke"), stroke_w))

        # Alignment
        self._align_combo = QComboBox()
        self._align_combo.addItems(
            [self.tr("Left"), self.tr("Center"), self.tr("Right")]
        )
        self._layout.addLayout(
            _labeled_control(self.tr("Alignment"), self._align_combo)
        )

        # ── Single Apply All button ──────────────────────────────
        self._apply_all_btn = QPushButton(self.tr("Apply Changes"))
        self._apply_all_btn.setObjectName("StyleApplyAllBtn")
        self._apply_all_btn.clicked.connect(self._apply_all)
        self._layout.addSpacing(6)
        self._layout.addWidget(self._apply_all_btn)

        self._layout.addWidget(SeparatorWidget())

        # ── 3d. Block List ────────────────────────────────────────
        self._layout.addWidget(_SectionHeader(self.tr("Blocks Using This Style")))
        self._block_tree = QTreeWidget()
        self._block_tree.setObjectName("StyleBlockList")
        self._block_tree.setHeaderHidden(True)
        self._block_tree.setIndentation(16)
        self._block_tree.setRootIsDecorated(True)
        self._block_tree.itemClicked.connect(self._on_block_item_clicked)
        self._layout.addWidget(self._block_tree)

        self._layout.addStretch()
        self.setWidget(container)

    # ── Public API ─────────────────────────────────────────────────

    def set_project(self, proj, scene_manager):
        """Store references for applying changes."""
        self._proj = proj
        self._scene_manager = scene_manager

    def show_entry(self, entry: StyleEntry):
        """Populate the detail panel for *entry*."""
        self._entry = entry
        ffmt = entry.fontformat

        # Header preview
        font = QFont(ffmt.font_family)
        font.setPixelSize(max(14, min(int(ffmt.font_size), 36)))
        if ffmt.bold:
            font.setBold(True)
        if ffmt.italic:
            font.setItalic(True)
        if ffmt.underline:
            font.setUnderline(True)
        self._preview_label.setFont(font)

        fg = [int(round(c)) for c in ffmt.frgb]
        bg = [int(round(c)) for c in ffmt.srgb]
        ss = (
            f"color: rgb({fg[0]},{fg[1]},{fg[2]}); "
            f"background-color: rgba({bg[0]},{bg[1]},{bg[2]},50); "
            f"border-radius: 4px;"
        )
        self._preview_label.setStyleSheet(ss)

        self._header_info.setText(
            self.tr("Applied to {n} blocks across {p} pages").format(
                n=entry.count, p=entry.page_count
            )
        )

        # Property summary
        self._prop_font.set_value(ffmt.font_family)
        self._prop_size.set_value(
            f"{ffmt.font_size:.1f} px  ({px2pt(ffmt.font_size):.1f} pt)"
        )
        wt = ffmt.font_weight
        self._prop_weight.set_value(str(wt) if wt is not None else self.tr("(default)"))
        style_parts = []
        if ffmt.bold:
            style_parts.append(self.tr("Bold"))
        if ffmt.italic:
            style_parts.append(self.tr("Italic"))
        if ffmt.underline:
            style_parts.append(self.tr("Underline"))
        self._prop_style.set_value(", ".join(style_parts) or self.tr("None"))
        self._prop_foreground.set_value(f"rgb({fg[0]}, {fg[1]}, {fg[2]})")
        if ffmt.stroke_width > 0:
            self._prop_stroke.set_value(
                f"rgb({bg[0]}, {bg[1]}, {bg[2]})  /  {ffmt.stroke_width:.1f} px"
            )
        else:
            self._prop_stroke.set_value(self.tr("None"))
        align_names = {0: self.tr("Left"), 1: self.tr("Center"), 2: self.tr("Right")}
        self._prop_alignment.set_value(
            align_names.get(ffmt.alignment, str(ffmt.alignment))
        )
        self._prop_layout.set_value(
            self.tr("Vertical") if ffmt.vertical else self.tr("Horizontal")
        )
        self._prop_spacing.set_value(
            self.tr("Line: {ls}  Letter: {lsp}").format(
                ls=ffmt.line_spacing, lsp=ffmt.letter_spacing
            )
        )
        eff_parts: List[str] = []
        if ffmt.shadow_radius > 0:
            eff_parts.append(self.tr("Shadow"))
        if ffmt.gradient_enabled:
            eff_parts.append(self.tr("Gradient"))
        if ffmt.opacity < 1.0:
            eff_parts.append(self.tr("Opacity: {o}").format(o=ffmt.opacity))
        self._prop_effects.set_value(", ".join(eff_parts) or self.tr("None"))

        # Batch controls: sync to current values
        self._sync_controls(ffmt)

        # Block list
        self._populate_block_tree(entry)

    def _sync_controls(self, ffmt: FontFormat):
        """Sync batch-edit control values to *ffmt*."""
        # Font family
        families = shared.ALL_FONT_FAMILIES
        self._family_combo.blockSignals(True)
        self._family_combo.clear()
        if families:
            self._family_combo.addItems(families)
        idx = self._family_combo.findText(ffmt.font_family)
        if idx >= 0:
            self._family_combo.setCurrentIndex(idx)
        self._family_combo.blockSignals(False)

        # Size
        self._size_spin.blockSignals(True)
        self._size_spin.setValue(ffmt.font_size)
        self._size_spin.blockSignals(False)

        # Flags
        self._bold_cb.blockSignals(True)
        self._italic_cb.blockSignals(True)
        self._underline_cb.blockSignals(True)
        self._vertical_cb.blockSignals(True)
        self._bold_cb.setChecked(ffmt.bold)
        self._italic_cb.setChecked(ffmt.italic)
        self._underline_cb.setChecked(ffmt.underline)
        self._vertical_cb.setChecked(ffmt.vertical)
        self._bold_cb.blockSignals(False)
        self._italic_cb.blockSignals(False)
        self._underline_cb.blockSignals(False)
        self._vertical_cb.blockSignals(False)

        # FG color
        fg = ffmt.foreground_color()
        self._fg_btn.setColor(QColor(*fg))
        self._fg_label.setText(f"rgb({fg[0]}, {fg[1]}, {fg[2]})")
        self._pending_fg = list(ffmt.frgb)

        # Stroke
        bg = ffmt.stroke_color()
        self._stroke_color_btn.setColor(QColor(*bg))
        self._stroke_color_label.setText(f"rgb({bg[0]}, {bg[1]}, {bg[2]})")
        self._pending_stroke_color = list(ffmt.srgb)
        self._stroke_spin.blockSignals(True)
        self._stroke_spin.setValue(ffmt.stroke_width)
        self._stroke_spin.blockSignals(False)

        # Alignment
        self._align_combo.blockSignals(True)
        self._align_combo.setCurrentIndex(ffmt.alignment)
        self._align_combo.blockSignals(False)

    def _populate_block_tree(self, entry: StyleEntry):
        """Fill the QTreeWidget with pages → blocks."""
        self._block_tree.clear()
        # Group by page
        page_map: Dict[str, List[int]] = {}
        for pname, bidx in entry.blocks:
            page_map.setdefault(pname, []).append(bidx)

        for pname, bidx_list in page_map.items():
            page_item = QTreeWidgetItem()
            page_item.setText(0, f"{pname}  ({len(bidx_list)})")
            page_item.setFlags(page_item.flags() | Qt.ItemFlag.ItemIsEnabled)
            page_item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "page", "pagename": pname}
            )

            for bidx in sorted(bidx_list):
                blk = self._proj.pages[pname][bidx]
                preview = blk.translation or blk.text
                if isinstance(preview, list):
                    preview = " ".join(str(t) for t in preview)
                preview = str(preview)[:60]
                blk_item = QTreeWidgetItem()
                blk_item.setText(
                    0, self.tr('Block #{n}:  "{t}"').format(n=bidx, t=preview)
                )
                blk_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "block", "pagename": pname, "block_idx": bidx},
                )
                page_item.addChild(blk_item)

            self._block_tree.addTopLevelItem(page_item)
            page_item.setExpanded(True)

    def _on_block_item_clicked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("type") == "block":
            self.navigate_to_block.emit(data["pagename"], data["block_idx"])

    # ── Batch apply handlers ───────────────────────────────────────

    def _make_change_dict(self, override: dict) -> List[Dict]:
        """Build change list for all blocks in the current entry."""
        if self._entry is None or self._proj is None:
            return []
        old_ffmt = self._entry.fontformat
        new_ffmt = old_ffmt.deepcopy()
        for k, v in override.items():
            setattr(new_ffmt, k, v)
        changes = []
        for pname, bidx in self._entry.blocks:
            changes.append(
                {
                    "pagename": pname,
                    "block_idx": bidx,
                    "old_ffmt": old_ffmt.deepcopy(),
                    "new_ffmt": new_ffmt.deepcopy(),
                }
            )
        return changes

    def _push_command(self, changes: List[Dict], description: str = ""):
        """Push a BatchFontformatCommand to the canvas undo stack."""
        if not changes:
            return
        from .fontstyle_manager_commands import BatchFontformatCommand

        cmd = BatchFontformatCommand(
            self._proj, self._scene_manager, changes, description
        )
        try:
            self._scene_manager.canvas.push_undo_command(cmd)
        except AttributeError:
            pass

    def _apply_all(self):
        """Apply all batch controls at once."""
        if self._entry is None:
            return

        family = self._family_combo.currentText()
        if not family:
            return

        override = {
            "font_family": family,
            "font_size": self._size_spin.value(),
            "bold": self._bold_cb.isChecked(),
            "italic": self._italic_cb.isChecked(),
            "underline": self._underline_cb.isChecked(),
            "vertical": self._vertical_cb.isChecked(),
            "frgb": list(self._pending_fg),
            "srgb": list(self._pending_stroke_color),
            "stroke_width": self._stroke_spin.value(),
            "alignment": self._align_combo.currentIndex(),
        }

        changes = self._make_change_dict(override)
        self._push_command(changes, self.tr("Batch edit font style"))
        # Update local entry
        ffmt = self._entry.fontformat
        for k, v in override.items():
            setattr(ffmt, k, v)
        self.show_entry(self._entry)

    def _pick_fg(self):
        if self._entry is None:
            return
        fg = self._pending_fg
        c = QColorDialog.getColor(
            QColor(max(0, min(255, int(fg[0]))), max(0, min(255, int(fg[1]))), max(0, min(255, int(fg[2])))),
            self,
            self.tr("Pick Text Color"),
        )
        if c.isValid():
            self._pending_fg = [c.red(), c.green(), c.blue()]
            self._fg_btn.setColor(c)
            self._fg_label.setText(f"rgb({c.red()}, {c.green()}, {c.blue()})")

    def _pick_stroke_color(self):
        if self._entry is None:
            return
        sc = self._pending_stroke_color
        c = QColorDialog.getColor(
            QColor(max(0, min(255, int(sc[0]))), max(0, min(255, int(sc[1]))), max(0, min(255, int(sc[2])))),
            self,
            self.tr("Pick Stroke Color"),
        )
        if c.isValid():
            self._pending_stroke_color = [c.red(), c.green(), c.blue()]
            self._stroke_color_btn.setColor(c)
            self._stroke_color_label.setText(f"rgb({c.red()}, {c.green()}, {c.blue()})")


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _labeled_control(
    label: str, control: QWidget, action_btn: QPushButton | None = None
) -> QHBoxLayout:
    """Build a horizontal row: [label 120px] [control stretch] [action_btn]."""
    lbl = QLabel(label)
    lbl.setFixedWidth(100)
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 1, 0, 1)
    layout.setSpacing(6)
    layout.addWidget(lbl)
    layout.addWidget(control, 1)
    if action_btn is not None:
        action_btn.setFixedWidth(52)
        layout.addWidget(action_btn)
    return layout


# ═══════════════════════════════════════════════════════════════════════
# FontStyleManager — top-level container
# ═══════════════════════════════════════════════════════════════════════


class FontStyleManager(QWidget):
    """Left-right split panel: style list + style detail.

    Designed to be used inside an OverlaySlider with split_mode=True,
    where the StyleList is split_left_widget and StyleDetail is
    split_right_widget.
    """

    # Emitted when user clicks a block in the detail panel
    navigate_to_block = Signal(str, int)  # pagename, block_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FontStyleManager")

        self._proj = None
        self._scene_manager = None
        self._entries: List[StyleEntry] = []
        self._sig_to_entry: Dict[str, StyleEntry] = {}

        # ── Left: StyleList ───────────────────────────────────────
        self.styleList = StyleList()
        self.styleList.style_selected.connect(self._on_style_selected)

        # ── Right: StyleDetail ────────────────────────────────────
        self.detailContent = StyleDetail()
        self.detailContent.navigate_to_block.connect(self.navigate_to_block)

        # ── Layout ────────────────────────────────────────────────
        hlayout = QHBoxLayout(self)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(0)
        hlayout.addWidget(self.styleList)
        hlayout.addWidget(self.detailContent, 1)

        self.styleList.setFixedWidth(240)

        # ── Empty state ───────────────────────────────────────────
        self._empty_label = QLabel(
            self.tr(
                "No text blocks in the project.\nRun detection + OCR to populate text blocks."
            )
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        hlayout.addWidget(self._empty_label)
        self._empty_label.hide()

    # ── Public API ─────────────────────────────────────────────────

    def set_project(self, proj, scene_manager):
        """Store references for style discovery and batch application."""
        self._proj = proj
        self._scene_manager = scene_manager
        self.detailContent.set_project(proj, scene_manager)

    def refresh(self, proj=None, scene_manager=None):
        """Re-discover styles and repopulate the list."""
        if proj is not None:
            self.set_project(proj, scene_manager)
        if self._proj is None:
            self._empty_label.show()
            self.styleList.hide()
            self.detailContent.hide()
            return

        entries = discover_styles(self._proj)
        self._entries = entries
        self._sig_to_entry = {e.signature: e for e in entries}

        if not entries:
            self._empty_label.show()
            self.styleList.hide()
            self.detailContent.hide()
            return

        self._empty_label.hide()
        self.styleList.show()
        self.detailContent.show()
        self.styleList.populate(entries)

    def _on_style_selected(self, sig: str):
        entry = self._sig_to_entry.get(sig)
        if entry is not None:
            self.detailContent.show_entry(entry)
