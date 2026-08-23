"""
Font Style Manager — base styles + derived variants across the project.

Left panel: tree of base styles (project-level named entities, identity =
font_family + vertical) with their auto-derived variants, plus an
"Ungrouped" section for blocks whose identity matches no base style
(clustered by full-parameter signature, the legacy discovery).

Right panel (StyleDetail) has three modes:

* base style — batch edit flattens the *changed* parameters onto every
  block of the style (other per-block overrides survive); rename, save as
  cross-project preset, delete.
* variant — batch edit writes only the changed parameters onto the
  variant's blocks; after re-discovery the variant may merge elsewhere or
  dissolve back into the base style.
* ungrouped signature — legacy full-parameter apply + "promote to base
  style".

All batch operations go through BatchFontformatCommand (undoable) and
emit pages_dirtied / data_committed like before.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from qtpy.QtCore import QRect, Qt, QTimer, Signal
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPixmap,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils import shared
from utils.base_styles import (
    BaseStyleNode,
    StyleEntry,
    StyleTree,
    VariantEntry,
    build_flatten_changes,
    build_variant_changes,
    compute_signature,
    copy_value,
    discover_style_tree,
    overrides_summary,
    quantize_field,
    variant_display_name,
)
from utils.fontformat import (
    FontFormat,
    px2pt,
)

from .custom_widget import ColorPickerDialog, ColorSwatchBtn, SeparatorWidget

# Re-exported for legacy importers (tests import these from this module).
__all__ = [
    "FontStyleManager",
    "StyleDetail",
    "StyleEntry",
    "compute_signature",
    "discover_styles",
]


def discover_styles(proj) -> List[StyleEntry]:
    """Legacy full-signature discovery (no base styles attached)."""
    return discover_style_tree(proj, []).ungrouped


# ═══════════════════════════════════════════════════════════════════════
# Style tree (left panel)
# ═══════════════════════════════════════════════════════════════════════


def _swatch_icon(ffmt: FontFormat) -> QIcon:
    """Two-tone rounded swatch: stroke ring outside, foreground inside."""
    d = 14
    pixmap = QPixmap(d, d)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    rect = QRect(1, 1, d - 2, d - 2)
    radius = d / 3
    if ffmt.stroke_width > 0:
        painter.setBrush(QColor(*ffmt.stroke_color(), 255))
        painter.drawRoundedRect(rect, radius, radius)
        inset = d // 4
        rect = QRect(inset, inset, d - 2 * inset, d - 2 * inset)
    painter.setBrush(QColor(*ffmt.foreground_color(), 255))
    painter.drawRoundedRect(rect, radius, radius)
    painter.end()
    return QIcon(pixmap)


class StyleTreeWidget(QTreeWidget):
    """Left panel: base styles → variants, plus an Ungrouped section.

    Node payloads (UserRole):
      {"type": "base",    "identity": (family, vertical)}
      {"type": "variant", "identity": (family, vertical), "key": tuple}
      {"type": "sig",     "signature": str}
    """

    node_selected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StyleTree")
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setRootIsDecorated(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.currentItemChanged.connect(self._on_current_changed)

    def populate(self, tree: StyleTree):
        self.blockSignals(True)
        self.clear()
        for node in tree.nodes:
            base = node.base
            orient = self.tr("V") if base.fontformat.vertical else self.tr("H")
            label = f"{base.name} · {orient}"
            if node.total_count:
                label += f"  ({node.total_count})"
            item = QTreeWidgetItem([label])
            item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "base", "identity": base.identity}
            )
            item.setIcon(0, _swatch_icon(base.fontformat))
            bold = item.font(0)
            bold.setBold(True)
            item.setFont(0, bold)
            item.setToolTip(
                0,
                self.tr("Font: {f}\nOrientation: {o}").format(
                    f=base.fontformat.font_family,
                    o=self.tr("Vertical")
                    if base.fontformat.vertical
                    else self.tr("Horizontal"),
                ),
            )
            self.addTopLevelItem(item)
            for var in node.variants:
                vlabel = variant_display_name(base.name, var.overrides)
                if var.count:
                    vlabel += f"  ({var.count})"
                child = QTreeWidgetItem([vlabel])
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "variant", "identity": base.identity, "key": var.key},
                )
                child.setToolTip(0, overrides_summary(var.overrides))
                item.addChild(child)
            item.setExpanded(True)

        if tree.ungrouped:
            root = QTreeWidgetItem([self.tr("Ungrouped")])
            root.setFlags(Qt.ItemFlag.ItemIsEnabled)  # header row, not selectable
            bold = root.font(0)
            bold.setBold(True)
            root.setFont(0, bold)
            for entry in tree.ungrouped:
                ffmt = entry.fontformat
                child = QTreeWidgetItem(
                    [f"{ffmt.font_family} {ffmt.font_size:.0f}px  ({entry.count})"]
                )
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "sig", "signature": entry.signature},
                )
                child.setIcon(0, _swatch_icon(ffmt))
                root.addChild(child)
            root.setExpanded(True)
            self.addTopLevelItem(root)
        self.blockSignals(False)

    def select_payload(self, payload: dict) -> bool:
        """Programmatically select the item matching *payload*."""
        if not payload:
            return False
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for item in [top] + [top.child(j) for j in range(top.childCount())]:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data is not None and self._payload_matches(data, payload):
                    self.setCurrentItem(item)
                    return True
        return False

    @staticmethod
    def _payload_matches(data: dict, payload: dict) -> bool:
        if data.get("type") != payload.get("type"):
            return False
        if data["type"] == "base":
            return data["identity"] == payload["identity"]
        if data["type"] == "variant":
            return (
                data["identity"] == payload["identity"]
                and data["key"] == payload.get("key")
            )
        return data["signature"] == payload["signature"]

    def current_payload(self) -> dict | None:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _on_current_changed(self, current, _previous):
        if current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if data is not None:
            self.node_selected.emit(data)


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
    """Label : value row."""

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
    """Right panel: base-style / variant / ungrouped-signature detail views."""

    # block navigation signal
    navigate_to_block = Signal(str, int)  # pagename, block_idx
    pages_dirtied = Signal()  # emitted after batch-apply modifies other pages
    data_committed = Signal()  # emitted after changes — caller should persist JSON
    # emitted after an apply / promote / delete; the container re-discovers
    # and reselects the node described by the payload (None = plain refresh)
    styles_changed = Signal(object)

    MODE_BASE = "base"
    MODE_VARIANT = "variant"
    MODE_SIG = "sig"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StyleDetail")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._mode: str | None = None
        self._base_node: BaseStyleNode | None = None
        self._variant: VariantEntry | None = None
        self._entry: StyleEntry | None = None
        self._baseline_ffmt: FontFormat | None = None
        self._proj = None
        self._scene_manager = None

        # Container
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(12, 8, 12, 8)

        # ── Header ───────────────────────────────────────────────
        self._preview_label = QLabel("Aa Bb Gg")
        self._preview_label.setObjectName("StylePreviewLabel")
        self._preview_label.setFixedHeight(36)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._preview_label)

        self._header_info = QLabel()
        self._header_info.setWordWrap(True)
        self._layout.addWidget(self._header_info)

        # ── Base-style identity row (rename) ─────────────────────
        self._name_edit = QLineEdit()
        self._name_edit.setToolTip(self.tr("Base style name"))
        self._name_edit.editingFinished.connect(self._on_name_edited)
        self._layout.addLayout(
            _labeled_control(self.tr("Style Name"), self._name_edit)
        )

        self._layout.addWidget(SeparatorWidget())

        # ── Property Summary ──────────────────────────────────────
        self._layout.addWidget(_SectionHeader(self.tr("Properties")))

        self._prop_overrides = _PropertyRow(self.tr("Overrides"))
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
        self._prop_pure = _PropertyRow(self.tr("Blocks Matching Base"))
        self._prop_variants = _PropertyRow(self.tr("Variants"))

        for pw in [
            self._prop_overrides,
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
            self._prop_pure,
            self._prop_variants,
        ]:
            self._layout.addWidget(pw)

        self._layout.addWidget(SeparatorWidget())

        # ── Batch Edit Controls ───────────────────────────────────
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

        # Font style / weight
        self._style_combo = QComboBox()
        self._style_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._layout.addLayout(
            _labeled_control(self.tr("Font Style"), self._style_combo)
        )
        self._family_combo.currentTextChanged.connect(self._on_family_for_style_changed)

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

        # ── Preset apply ─────────────────────────────────────────
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._preset_btn = QPushButton(self.tr("Apply Preset"))
        self._preset_btn.clicked.connect(self._apply_preset)
        preset_row = QWidget()
        preset_lay = QHBoxLayout(preset_row)
        preset_lay.setContentsMargins(0, 1, 0, 1)
        preset_lay.setSpacing(6)
        preset_lay.addWidget(self._preset_combo, 1)
        preset_lay.addWidget(self._preset_btn)
        self._layout.addLayout(
            _labeled_control(self.tr("Preset"), preset_row)
        )

        # ── Mode-specific action buttons ─────────────────────────
        self._promote_btn = QPushButton(self.tr("Promote to Base Style"))
        self._promote_btn.setToolTip(
            self.tr(
                "Create a base style from this parameter set; blocks with the same font and orientation will join it automatically."
            )
        )
        self._promote_btn.clicked.connect(self._promote_to_base)
        self._layout.addWidget(self._promote_btn)

        base_actions = QWidget()
        base_lay = QHBoxLayout(base_actions)
        base_lay.setContentsMargins(0, 1, 0, 1)
        base_lay.setSpacing(6)
        self._save_preset_btn = QPushButton(self.tr("Save as Preset"))
        self._save_preset_btn.setToolTip(
            self.tr("Add this base style to the cross-project preset list")
        )
        self._save_preset_btn.clicked.connect(self._save_base_as_preset)
        self._delete_base_btn = QPushButton(self.tr("Delete Style"))
        self._delete_base_btn.setToolTip(
            self.tr("Delete this base style; its blocks move to Ungrouped")
        )
        self._delete_base_btn.clicked.connect(self._delete_base_style)
        base_lay.addWidget(self._save_preset_btn)
        base_lay.addWidget(self._delete_base_btn)
        self._layout.addWidget(base_actions)

        # ── Single Apply button ──────────────────────────────────
        self._apply_all_btn = QPushButton(self.tr("Apply Changes"))
        self._apply_all_btn.setObjectName("StyleApplyAllBtn")
        self._apply_all_btn.clicked.connect(self._apply_all)
        self._layout.addSpacing(6)
        self._layout.addWidget(self._apply_all_btn)

        self._layout.addWidget(SeparatorWidget())

        # ── Block List ────────────────────────────────────────────
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

        self._pending_fg: List[int] = [0, 0, 0]
        self._pending_stroke_color: List[int] = [0, 0, 0]

    # ── Public API ─────────────────────────────────────────────────

    def set_project(self, proj, scene_manager):
        """Store references for applying changes."""
        self._proj = proj
        self._scene_manager = scene_manager

    # -- mode dispatch -------------------------------------------------

    def show_base_style(self, node: BaseStyleNode):
        self._mode = self.MODE_BASE
        self._base_node = node
        self._variant = None
        self._entry = None
        base = node.base
        ffmt = base.fontformat

        self._name_edit.blockSignals(True)
        self._name_edit.setText(base.name)
        self._name_edit.blockSignals(False)

        self._header_info.setText(
            self.tr("Base style — {n} blocks across {p} pages").format(
                n=node.total_count, p=node.page_count
            )
        )

        self._prop_overrides.hide()
        self._prop_pure.show()
        self._prop_variants.show()
        self._prop_pure.set_value(str(node.pure.count))
        self._prop_variants.set_value(str(len(node.variants)))

        self._fill_property_summary(ffmt)
        self._baseline_ffmt = ffmt.deepcopy()
        self._sync_controls(ffmt)

        blocks = list(node.pure.blocks)
        for var in node.variants:
            blocks.extend(var.blocks)
        self._populate_block_list(blocks)

        self._name_edit.show()
        self._promote_btn.hide()
        self._save_preset_btn.show()
        self._delete_base_btn.show()

    def show_variant(self, node: BaseStyleNode, variant: VariantEntry):
        self._mode = self.MODE_VARIANT
        self._base_node = node
        self._variant = variant
        self._entry = None
        base = node.base

        rep = self._representative_ffmt(variant, base.fontformat)
        self._header_info.setText(
            self.tr("Variant of “{name}” — {n} blocks across {p} pages").format(
                name=base.name, n=variant.count, p=variant.page_count
            )
        )

        self._prop_overrides.show()
        self._prop_pure.hide()
        self._prop_variants.hide()
        self._prop_overrides.set_value(overrides_summary(variant.overrides))

        self._fill_property_summary(rep)
        self._baseline_ffmt = rep.deepcopy()
        self._sync_controls(rep)

        self._populate_block_list(variant.blocks)

        self._name_edit.hide()
        self._promote_btn.hide()
        self._save_preset_btn.hide()
        self._delete_base_btn.hide()

    def show_entry(self, entry: StyleEntry):
        """Ungrouped signature entry (legacy view)."""
        self._mode = self.MODE_SIG
        self._base_node = None
        self._variant = None
        self._entry = entry
        ffmt = entry.fontformat

        self._header_info.setText(
            self.tr("Ungrouped — applied to {n} blocks across {p} pages").format(
                n=entry.count, p=entry.page_count
            )
        )

        self._prop_overrides.hide()
        self._prop_pure.hide()
        self._prop_variants.hide()

        self._fill_property_summary(ffmt)
        self._baseline_ffmt = ffmt.deepcopy()
        self._sync_controls(ffmt)

        self._populate_block_list(entry.blocks)

        self._name_edit.hide()
        self._promote_btn.show()
        self._save_preset_btn.hide()
        self._delete_base_btn.hide()

    def _representative_ffmt(
        self, variant: VariantEntry, fallback: FontFormat
    ) -> FontFormat:
        """First live block of the variant as the display representative."""
        for pname, bidx in variant.blocks:
            page = self._proj.pages.get(pname) if self._proj else None
            if page is not None and 0 <= bidx < len(page):
                return page[bidx].fontformat
        return fallback

    # -- property summary ----------------------------------------------

    def _fill_property_summary(self, ffmt: FontFormat):
        # Reload preset list so it reflects the latest saved presets
        self._load_presets()

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

    # -- control sync ---------------------------------------------------

    def _sync_controls(self, ffmt: FontFormat):
        """Sync batch-edit control values to *ffmt*."""
        from utils.config import pcfg
        families = shared.get_filtered_font_list(pcfg.excluded_fonts)
        self._family_combo.blockSignals(True)
        self._family_combo.clear()
        if families:
            self._family_combo.addItems(families)
        idx = self._family_combo.findText(ffmt.font_family)
        if idx >= 0:
            self._family_combo.setCurrentIndex(idx)
        self._family_combo.blockSignals(False)

        # Font style / weight
        self._populate_style_combo(ffmt.font_family, ffmt._style_name, ffmt.font_weight)

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

    def _populate_style_combo(
        self, family: str, style_name: str = "", weight: int | None = None
    ):
        """Fill the style combo with available styles for *family*."""
        self._style_combo.blockSignals(True)
        self._style_combo.clear()
        styles = shared.FONT_STYLES.get(family, [])
        self._style_combo.addItems(styles)

        # Add a "(default)" entry at top for resetting to font default weight
        self._style_combo.insertItem(0, "")
        self._style_combo.setItemText(0, self.tr("(default)"))

        # Try to match by style name first
        selected = False
        if style_name:
            idx = self._style_combo.findText(style_name)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
                selected = True

        # Fallback: match by best weight
        if not selected and weight is not None:
            for i in range(1, self._style_combo.count()):
                s = self._style_combo.itemText(i)
                sw = QFontDatabase.weight(family, s)
                if sw == weight:
                    self._style_combo.setCurrentIndex(i)
                    selected = True
                    break
        self._style_combo.blockSignals(False)

    def _on_family_for_style_changed(self, family: str):
        """Update style combo when font family changes."""
        self._populate_style_combo(family)

    # -- block list -----------------------------------------------------

    def _populate_block_list(self, blocks: List[Tuple[str, int]]):
        """Fill the QTreeWidget with pages → blocks."""
        self._block_tree.clear()
        if self._proj is None:
            return
        # Group by page
        page_map: Dict[str, List[int]] = {}
        for pname, bidx in blocks:
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

    # ── Change collection & apply ───────────────────────────────────

    def _control_values(self) -> Dict:
        """Current batch-edit control values as a FontFormat field dict."""
        family = self._family_combo.currentText()
        style_text = self._style_combo.currentText()
        if style_text:
            style_name = style_text
            font_weight = QFontDatabase.weight(family, style_text)
        else:
            style_name = ""
            font_weight = QFont.Weight.Normal  # default weight
        return {
            "font_family": family,
            "font_weight": font_weight,
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

    def _collect_changed(self) -> Dict:
        """Quantized diff of the controls against the baseline format.

        Only fields the user actually touched become part of the change —
        the flatten semantics rely on this (untouched parameters must not
        clobber per-block overrides).
        """
        changed: Dict = {}
        for k, v in self._control_values().items():
            old = getattr(self._baseline_ffmt, k, None)
            if old is None or quantize_field(k, v) != quantize_field(k, old):
                changed[k] = v
        return changed

    def _style_name_field(self) -> str:
        return self._style_combo.currentText()

    # -- shared apply flow ------------------------------------------------

    def _apply_ffmt_changes(self, changes: List[Dict], reselect, description: str):
        """Shared batch-apply flow: undo command → apply → rebuild → refresh."""
        from .fontstyle_manager_commands import BatchFontformatCommand

        # 1. Create the command FIRST — its constructor captures the current
        #    live-item state (HTML / rect) for undo BEFORE we modify anything.
        if changes:
            cmd = BatchFontformatCommand(
                self._proj, self._scene_manager, changes, description
            )
            self._scene_manager.canvas.push_text_command(cmd)
            self._apply_changes_to_blocks(changes)

        # Notify main window to refresh page list (dirty indicators)
        self.pages_dirtied.emit()
        # Notify main window to persist project data (JSON) immediately
        self.data_committed.emit()

        # 2. Rebuild the current page's canvas items from the project data
        #    so the visual is fully in sync with the updated blocks.
        if self._scene_manager is not None and changes:
            self._scene_manager.updateSceneTextitems()

        # 3. Let the container re-discover styles and reselect.
        self.styles_changed.emit(reselect)

    def _apply_changes_to_blocks(self, changes: List[Dict]):
        """Apply new_ffmt to every block in *changes* directly (no undo)."""
        from .fontstyle_manager_commands import _find_blk_item

        current_pname = self._proj.current_img if self._proj else None
        for ch in changes:
            pname = ch["pagename"]
            bidx = ch["block_idx"]
            new_ffmt = ch["new_ffmt"]

            if pname == current_pname:
                item = _find_blk_item(self._scene_manager, bidx)
                if item is not None:
                    item.set_fontformat(new_ffmt, set_char_format=True)
            else:
                page = self._proj.pages.get(pname)
                if page is None or not 0 <= bidx < len(page):
                    continue
                blk = page[bidx]
                blk.fontformat = new_ffmt
                self._proj.mark_page_needs_rerender(pname)

    # -- apply dispatch ----------------------------------------------------

    def _apply_all(self):
        """Apply all batch controls at once, per current mode."""
        if self._mode == self.MODE_BASE:
            self._apply_base()
        elif self._mode == self.MODE_VARIANT:
            self._apply_variant()
        elif self._mode == self.MODE_SIG:
            self._apply_sig()

    def _apply_base(self):
        """Flatten the *changed* parameters onto every block of the style."""
        if self._base_node is None or self._proj is None:
            return
        changed = self._collect_changed()
        if not changed:
            return
        base_style = self._base_node.base
        # Collect first (blocks are matched by the *current* identity key),
        # then update the base — a family/orientation change would otherwise
        # re-key the style before its own blocks are gathered.
        changes = build_flatten_changes(self._proj, base_style, changed)
        for k, v in changed.items():
            setattr(base_style.fontformat, k, copy_value(v))
        self._apply_ffmt_changes(
            changes,
            {"type": "base", "identity": base_style.identity},
            self.tr("Edit base style"),
        )

    def _apply_variant(self):
        """Write the *changed* parameters onto this variant's blocks only."""
        if self._variant is None or self._proj is None:
            return
        changed = self._collect_changed()
        if not changed:
            return
        changes = build_variant_changes(self._variant.blocks, self._proj, changed)
        reselect = (
            {"type": "base", "identity": self._base_node.base.identity}
            if self._base_node is not None
            else None
        )
        self._apply_ffmt_changes(changes, reselect, self.tr("Edit variant style"))

    def _apply_sig(self):
        """Legacy full-parameter apply onto signature-matched blocks."""
        if self._entry is None or self._proj is None:
            return
        candidate = self._control_values()
        candidate["_style_name"] = self._style_name_field()
        new_ffmt = self._entry.fontformat.deepcopy()
        for k, v in candidate.items():
            setattr(new_ffmt, k, copy_value(v))
        changes = self._changes_for_targets(new_ffmt)
        if not changes:
            return
        self._apply_ffmt_changes(
            changes,
            {"type": "base", "identity": (new_ffmt.font_family, bool(new_ffmt.vertical))},
            self.tr("Batch edit font style"),
        )

    def _collect_live_blocks(self) -> List[Tuple[str, int, "TextBlock"]]:
        """Re-derive the entry's blocks by matching signatures at apply time."""
        if self._entry is None or self._proj is None:
            return []
        sig = self._entry.signature
        live = []
        for pname, blklist in self._proj.pages.items():
            for bidx, blk in enumerate(blklist):
                if compute_signature(blk.fontformat) == sig:
                    live.append((pname, bidx, blk))
        return live

    def _changes_for_targets(self, new_ffmt: FontFormat) -> List[Dict]:
        """Full-replacement change list for every block carrying the style."""
        changes = []
        for pname, bidx, blk in self._collect_live_blocks():
            changes.append(
                {
                    "pagename": pname,
                    "block_idx": bidx,
                    "old_ffmt": blk.fontformat.deepcopy(),
                    "new_ffmt": new_ffmt.deepcopy(),
                }
            )
        return changes

    # -- preset apply --------------------------------------------------------

    def _load_presets(self):
        """Reload the preset combo from utils.config.text_styles."""
        from utils.config import text_styles

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem(self.tr("(Select a preset)"))
        for ts in text_styles:
            name = ts._style_name or self.tr("(unnamed)")
            self._preset_combo.addItem(name, userData=ts)
        self._preset_combo.blockSignals(False)

    def _apply_preset(self):
        if self._proj is None:
            return
        idx = self._preset_combo.currentIndex()
        if idx <= 0:  # 0 is the placeholder "(Select a preset)"
            return
        preset_ffmt = self._preset_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        if preset_ffmt is None:
            return

        if self._mode == self.MODE_BASE and self._base_node is not None:
            # Redefine the base style: full flatten of every differing field.
            base_style = self._base_node.base
            new_ffmt = preset_ffmt.deepcopy()
            changed: Dict = {}
            for k, v in self._control_fields_of(new_ffmt).items():
                old = getattr(base_style.fontformat, k, None)
                if old is None or quantize_field(k, v) != quantize_field(k, old):
                    changed[k] = v
            if not changed:
                return
            # Same ordering contract as _apply_base: collect by the old key
            # before re-keying the style.
            changes = build_flatten_changes(self._proj, base_style, changed)
            for k, v in changed.items():
                setattr(base_style.fontformat, k, copy_value(v))
            self._apply_ffmt_changes(
                changes,
                {"type": "base", "identity": base_style.identity},
                self.tr("Apply preset style"),
            )
            return

        # variant / sig modes: full-parameter replacement (legacy behavior)
        new_ffmt = preset_ffmt.deepcopy()
        if self._mode == self.MODE_VARIANT and self._variant is not None:
            changes = []
            for pname, bidx in self._variant.blocks:
                page = self._proj.pages.get(pname)
                if page is None or not 0 <= bidx < len(page):
                    continue
                blk = page[bidx]
                changes.append(
                    {
                        "pagename": pname,
                        "block_idx": bidx,
                        "old_ffmt": blk.fontformat.deepcopy(),
                        "new_ffmt": new_ffmt.deepcopy(),
                    }
                )
            reselect = (
                {"type": "base", "identity": (new_ffmt.font_family, bool(new_ffmt.vertical))}
            )
        else:
            changes = self._changes_for_targets(new_ffmt)
            reselect = (
                {"type": "base", "identity": (new_ffmt.font_family, bool(new_ffmt.vertical))}
            )
        if not changes:
            return
        self._apply_ffmt_changes(changes, reselect, self.tr("Apply preset style"))

    @staticmethod
    def _control_fields_of(ffmt: FontFormat) -> Dict:
        """The subset of fields the batch controls can edit."""
        return {
            "font_family": ffmt.font_family,
            "font_weight": ffmt.font_weight,
            "font_size": ffmt.font_size,
            "bold": ffmt.bold,
            "italic": ffmt.italic,
            "underline": ffmt.underline,
            "vertical": ffmt.vertical,
            "frgb": list(ffmt.frgb),
            "srgb": list(ffmt.srgb),
            "stroke_width": ffmt.stroke_width,
            "alignment": ffmt.alignment,
        }

    # -- base style management actions ------------------------------------

    def _on_name_edited(self):
        if self._mode != self.MODE_BASE or self._base_node is None:
            return
        new_name = self._name_edit.text().strip()
        base = self._base_node.base
        if new_name and new_name != base.name:
            base.name = new_name
            self.data_committed.emit()
            self.styles_changed.emit(
                {"type": "base", "identity": base.identity}
            )

    def _save_base_as_preset(self):
        """Add the base style to the cross-project preset list."""
        if self._base_node is None:
            return
        from utils.config import save_text_styles, text_styles

        ffmt = self._base_node.base.fontformat.deepcopy()
        ffmt._style_name = self._base_node.base.name
        text_styles.append(ffmt)
        save_text_styles()
        self._load_presets()

    def _delete_base_style(self):
        if self._base_node is None or self._proj is None:
            return
        base = self._base_node.base
        ret = QMessageBox.question(
            self,
            self.tr("Delete Base Style"),
            self.tr(
                "Delete base style “{name}”?\n"
                "No block parameters change; its blocks move to Ungrouped."
            ).format(name=base.name),
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        if base in self._proj.base_styles:
            self._proj.base_styles.remove(base)
        self._base_node = None
        self.data_committed.emit()
        self.styles_changed.emit(None)

    def _promote_to_base(self):
        """Create a base style from this ungrouped parameter set."""
        if self._entry is None or self._proj is None:
            return
        ffmt = self._entry.fontformat
        identity = (ffmt.font_family, bool(ffmt.vertical))
        if any(bs.identity == identity for bs in self._proj.base_styles):
            QMessageBox.warning(
                self,
                self.tr("Promote to Base Style"),
                self.tr("A base style with this font and orientation already exists."),
            )
            return
        from utils.base_styles import BaseStyle

        new_style = BaseStyle(ffmt.font_family, ffmt.deepcopy())
        self._proj.base_styles.append(new_style)
        self.data_committed.emit()
        self.styles_changed.emit({"type": "base", "identity": identity})

    # ── Color pickers ───────────────────────────────────────────────

    def _pick_fg(self):
        fg = self._pending_fg
        dlg = ColorPickerDialog(
            QColor(
                max(0, min(255, int(fg[0]))),
                max(0, min(255, int(fg[1]))),
                max(0, min(255, int(fg[2]))),
            ),
            self.window(),
        )
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            c = dlg.get_color()
            self._pending_fg = [c.red(), c.green(), c.blue()]
            self._fg_btn.setColor(c)
            self._fg_label.setText(f"rgb({c.red()}, {c.green()}, {c.blue()})")

    def _pick_stroke_color(self):
        sc = self._pending_stroke_color
        dlg = ColorPickerDialog(
            QColor(
                max(0, min(255, int(sc[0]))),
                max(0, min(255, int(sc[1]))),
                max(0, min(255, int(sc[2]))),
            ),
            self.window(),
        )
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            c = dlg.get_color()
            self._pending_stroke_color = [c.red(), c.green(), c.blue()]
            self._stroke_color_btn.setColor(c)
            self._stroke_color_label.setText(
                f"rgb({c.red()}, {c.green()}, {c.blue()})"
            )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _labeled_control(
    label: str, control: QWidget, action_btn: QPushButton | None = None
) -> QHBoxLayout:
    """Build a horizontal row: [label 100px] [control stretch] [action_btn]."""
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
    """Left-right split panel: style tree + style detail.

    Designed to be used inside a QDialog (see MainWindow
    on_open_fontstyle_manager). While open, any text undo-stack activity
    (single-block edits on the canvas) triggers a debounced re-discovery so
    the tree tracks parameter drift live.
    """

    # Emitted when user clicks a block in the detail panel
    navigate_to_block = Signal(str, int)  # pagename, block_idx
    pages_dirtied = Signal()  # relayed from StyleDetail after batch-apply
    data_committed = Signal()  # relayed from StyleDetail — persist JSON

    _REFRESH_DEBOUNCE_MS = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FontStyleManager")

        self._proj = None
        self._scene_manager = None
        self._tree: StyleTree | None = None
        self._node_map: Dict[tuple, BaseStyleNode] = {}
        self._sig_map: Dict[str, StyleEntry] = {}

        # ── Left: StyleTree ──────────────────────────────────────
        self.styleTree = StyleTreeWidget()
        self.styleTree.node_selected.connect(self._on_node_selected)

        # ── Right: StyleDetail ───────────────────────────────────
        self.detailContent = StyleDetail()
        self.detailContent.navigate_to_block.connect(self.navigate_to_block)
        self.detailContent.pages_dirtied.connect(self.pages_dirtied)
        self.detailContent.data_committed.connect(self.data_committed)
        self.detailContent.styles_changed.connect(self._on_styles_changed)

        # ── Layout ───────────────────────────────────────────────
        hlayout = QHBoxLayout(self)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(0)
        hlayout.addWidget(self.styleTree)
        hlayout.addWidget(self.detailContent, 1)

        self.styleTree.setFixedWidth(260)

        # ── Debounced live refresh on canvas edits ───────────────
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self._REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(lambda: self.refresh())
        self._undo_stack = None

        # ── Empty state ──────────────────────────────────────────
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

        # Live tracking: any text-stack activity (block edits, undos) →
        # debounced re-discovery. QObject teardown disconnects automatically.
        undo_stack = getattr(
            getattr(scene_manager, "canvas", None), "text_undo_stack", None
        )
        if undo_stack is not None and undo_stack is not self._undo_stack:
            if self._undo_stack is not None:
                try:
                    self._undo_stack.indexChanged.disconnect(self._schedule_refresh)
                except TypeError:
                    pass
            self._undo_stack = undo_stack
            self._undo_stack.indexChanged.connect(self._schedule_refresh)

    def _schedule_refresh(self, *_args):
        if self.isVisible():
            self._refresh_timer.start()

    def refresh(self, proj=None, scene_manager=None):
        """Re-discover styles and repopulate the tree, keeping selection."""
        if proj is not None:
            self.set_project(proj, scene_manager)
        if self._proj is None:
            self._empty_label.show()
            self.styleTree.hide()
            self.detailContent.hide()
            return

        keep = self.styleTree.current_payload()
        tree = discover_style_tree(self._proj, self._proj.base_styles)
        self._tree = tree
        self._node_map = {node.base.identity: node for node in tree.nodes}
        self._sig_map = {e.signature: e for e in tree.ungrouped}

        if not tree.nodes and not tree.ungrouped:
            self._empty_label.show()
            self.styleTree.hide()
            self.detailContent.hide()
            return

        self._empty_label.hide()
        self.styleTree.show()
        self.detailContent.show()
        self.styleTree.populate(tree)
        if keep is not None:
            self.styleTree.select_payload(keep)

    def _on_node_selected(self, payload: dict):
        if payload.get("type") == "base":
            node = self._node_map.get(payload["identity"])
            if node is not None:
                self.detailContent.show_base_style(node)
        elif payload.get("type") == "variant":
            node = self._node_map.get(payload["identity"])
            if node is None:
                return
            for var in node.variants:
                if var.key == payload.get("key"):
                    self.detailContent.show_variant(node, var)
                    return
        elif payload.get("type") == "sig":
            entry = self._sig_map.get(payload["signature"])
            if entry is not None:
                self.detailContent.show_entry(entry)

    def _on_styles_changed(self, reselect):
        """After an apply/promote/delete: re-discover and reselect."""
        self.refresh()
        if reselect is not None:
            self.styleTree.select_payload(reselect)
