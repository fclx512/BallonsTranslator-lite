"""
Font Style Manager — base styles + derived variants across the project.

Left panel: two-line tree nodes (line 1 = swatch + name + block count,
line 2 = gray parameter summary; variants list only their diff fields)
painted by ``_StyleItemDelegate``.

Right panel (StyleDetail) is diff-first (2026-08-30 rework, design doc
查找替换与样式管理器重构_设计方案.md §5): preview card + key-parameter
chip row + four collapsible field groups (``ui/style_format_editor.py``,
shared with the find/replace format editor) + per-page block chips.
Modes keep their previous semantics:

* base style — batch edit flattens the *changed* parameters onto every
  block of the style (other per-block overrides survive); rename, save as
  cross-project preset, delete.
* variant — only the override fields are editable; "reset to base" writes
  the base values back (the variant then dissolves on re-discovery).
* ungrouped signature — full-parameter apply + "promote to base style".

All batch operations go through BatchFontformatCommand (undoable) and
emit pages_dirtied / data_committed like before.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from qtpy.QtCore import (
    QCoreApplication,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPalette,
    QPainter,
    QTextDocument,
)
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOption,
    QStyledItemDelegate,
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
from utils.face_resolver import sync_face
from utils.fontformat import FontFormat

from .custom_widget import ColorPickerDialog, SeparatorWidget
from .style_format_editor import FormatEditorPanel

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


# 树节点第二行参数摘要的翻译 token（模块级字面量，定义处显式标注上下文）。
_SUMMARY_TOKENS = {
    "vert": QCoreApplication.translate("StyleTreeWidget", "Vertical"),
    "horz": QCoreApplication.translate("StyleTreeWidget", "Horizontal"),
    "italic": QCoreApplication.translate("StyleTreeWidget", "Italic"),
    "underline": QCoreApplication.translate("StyleTreeWidget", "Underline"),
    "strikeout": QCoreApplication.translate("StyleTreeWidget", "Strikeout"),
}


def _base_summary(ffmt: FontFormat) -> str:
    """Line-2 parameter summary for base/ungrouped nodes (translated)."""
    tokens = [ffmt.font_family, f"{ffmt.font_size:g}px"]
    tokens.append(
        _SUMMARY_TOKENS["vert"] if ffmt.vertical else _SUMMARY_TOKENS["horz"]
    )
    for attr in ("italic", "underline", "strikeout"):
        if getattr(ffmt, attr):
            tokens.append(_SUMMARY_TOKENS[attr])
    return " · ".join(tokens)


_DISPLAY_ROLE = Qt.ItemDataRole.UserRole + 1


class _StyleItemDelegate(QStyledItemDelegate):
    """Two-line tree node: swatch + name + count / gray summary."""

    _SWATCH = 14

    def paint(self, painter: QPainter, option, index):
        data = index.data(_DISPLAY_ROLE)
        if not data or not data.get("two_line"):
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(option.palette.color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(rect, 4, 4)

        text_color = (
            option.palette.color(QPalette.ColorRole.HighlightedText)
            if selected
            else option.palette.color(QPalette.ColorRole.Text)
        )
        sub_color = QColor(text_color)
        sub_color.setAlpha(150)

        line1_h = 20.0
        x = rect.left()
        # Swatch: stroke ring outside, foreground inside (base identity).
        if data.get("fg") is not None:
            sw = self._SWATCH
            sw_rect = QRectF(x, rect.top() + (line1_h - sw) / 2, sw, sw)
            painter.setPen(Qt.PenStyle.NoPen)
            if data.get("st"):
                painter.setBrush(QColor(*data["st"]))
                painter.drawRoundedRect(sw_rect, sw / 3, sw / 3)
                inset = sw / 4
                sw_rect = sw_rect.adjusted(inset, inset, -inset, -inset)
            painter.setBrush(QColor(*data["fg"]))
            painter.drawRoundedRect(sw_rect, sw / 3, sw / 3)
            x += sw + 8

        count_txt = ""
        if data.get("count"):
            count_txt = str(data["count"])
        count_w = (
            QFontMetrics(option.font).horizontalAdvance(count_txt) + 6
            if count_txt
            else 0.0
        )

        # Line 1: name (elide before eliding — Windows GDI truncation guard).
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPixelSize(12)
        painter.setFont(title_font)
        avail = rect.right() - count_w - x
        title = data.get("title", "")
        fm = QFontMetrics(title_font)
        if fm.horizontalAdvance(title) > avail:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, int(avail))
        painter.setPen(text_color)
        painter.drawText(
            QRectF(x, rect.top(), avail, line1_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        if count_txt:
            painter.setPen(sub_color)
            painter.drawText(
                QRectF(rect.right() - count_w, rect.top(), count_w, line1_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                count_txt,
            )

        # Line 2: gray parameter summary.
        sub_font = painter.font()
        sub_font.setBold(False)
        sub_font.setPixelSize(11)
        painter.setFont(sub_font)
        painter.setPen(sub_color)
        sub = data.get("sub", "")
        fm2 = QFontMetrics(sub_font)
        avail2 = rect.width() - (x - rect.left())
        if fm2.horizontalAdvance(sub) > avail2:
            sub = fm2.elidedText(sub, Qt.TextElideMode.ElideRight, int(avail2))
        painter.drawText(
            QRectF(x, rect.top() + line1_h, avail2, rect.height() - line1_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            sub,
        )
        painter.restore()


class StyleTreeWidget(QTreeWidget):
    """Left panel: base styles → variants, plus an Ungrouped section.

    Node payloads (UserRole):
      {"type": "base",    "identity": (family, vertical)}
      {"type": "variant", "identity": (family, vertical), "key": tuple}
      {"type": "sig",     "signature": str}

    Display data for the two-line delegate lives in ``_DISPLAY_ROLE``.
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
        self.setItemDelegate(_StyleItemDelegate(self))
        self.currentItemChanged.connect(self._on_current_changed)

    def populate(self, tree: StyleTree):
        self.blockSignals(True)
        self.clear()
        for node in tree.nodes:
            base = node.base
            item = QTreeWidgetItem()
            item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "base", "identity": base.identity}
            )
            item.setData(
                0,
                _DISPLAY_ROLE,
                {
                    "two_line": True,
                    "title": base.name,
                    "sub": _base_summary(base.fontformat),
                    "count": node.total_count,
                    "fg": [int(c) for c in base.fontformat.foreground_color()],
                    "st": [int(c) for c in base.fontformat.stroke_color()]
                    if base.fontformat.stroke_width > 0
                    else None,
                },
            )
            item.setSizeHint(0, QSize(0, 42))
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
                child = QTreeWidgetItem()
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "variant", "identity": base.identity, "key": var.key},
                )
                fg = var.overrides.get("frgb")
                st = var.overrides.get("srgb")
                if fg is None:
                    fg = [int(c) for c in base.fontformat.foreground_color()]
                if st is None:
                    st = (
                        [int(c) for c in base.fontformat.stroke_color()]
                        if base.fontformat.stroke_width > 0
                        else None
                    )
                child.setData(
                    0,
                    _DISPLAY_ROLE,
                    {
                        "two_line": True,
                        "title": base.name,
                        "sub": overrides_summary(var.overrides),
                        "count": var.count,
                        "fg": [int(c) for c in fg[:3]],
                        "st": [int(c) for c in st[:3]] if st else None,
                    },
                )
                child.setSizeHint(0, QSize(0, 42))
                child.setToolTip(0, variant_display_name(base.name, var.overrides))
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
                child = QTreeWidgetItem()
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "sig", "signature": entry.signature},
                )
                child.setData(
                    0,
                    _DISPLAY_ROLE,
                    {
                        "two_line": True,
                        "title": f"{ffmt.font_family} {ffmt.font_size:.0f}px",
                        "sub": _base_summary(ffmt),
                        "count": entry.count,
                        "fg": [int(c) for c in ffmt.foreground_color()],
                        "st": [int(c) for c in ffmt.stroke_color()]
                        if ffmt.stroke_width > 0
                        else None,
                    },
                )
                child.setSizeHint(0, QSize(0, 42))
                child.setToolTip(0, _base_summary(ffmt))
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
# Reusable chips (preview parameter row + block distribution)
# ═══════════════════════════════════════════════════════════════════════


class _ChipBar(QWidget):
    """Wrapping row of small clickable chips.

    ``set_chips(chips)`` — *chips* is a list of
    ``(chip_id, label, color_hex_or_None)``; clicks re-emit ``chip_clicked``.

    Wrapping is laid out manually in ``resizeEvent`` — PyQt6 下 Python 自定义
    QLayout 子类在布局激活时会原生段错误（最小复现确认），故不走 QLayout。
    """

    chip_clicked = Signal(str)
    _SPACING = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips: List[QPushButton] = []

    def clear_chips(self):
        for btn in self._chips:
            btn.setParent(None)
            btn.deleteLater()
        self._chips = []

    def set_chips(self, chips: List[Tuple[str, str, str | None]]):
        self.clear_chips()
        for chip_id, label, color in chips:
            btn = QPushButton(label)
            btn.setObjectName("ParamChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if color:
                btn.setStyleSheet(f"ParamChip {{ background-color: {color}; }}")
            btn.clicked.connect(
                lambda _=False, cid=chip_id: self.chip_clicked.emit(cid)
            )
            btn.setParent(self)
            btn.show()
            self._chips.append(btn)
        self._relayout()

    def _relayout(self):
        x = y = 0
        row_h = 0
        avail = max(self.width(), 40)
        for btn in self._chips:
            hint = btn.sizeHint()
            w, h = hint.width(), hint.height()
            if x + w > avail and x > 0:
                x = 0
                y += row_h + self._SPACING
                row_h = 0
            btn.setGeometry(x, y, w, h)
            x += w + self._SPACING
            row_h = max(row_h, h)
        self.setMinimumHeight(y + row_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()


class StylePreviewCard(QWidget):
    """Real-font preview card (QTextDocument approximation, v1 scope:
    family/size/color/weight/style/underline/alignment/line spacing)."""

    SAMPLE = "Aa Bb Gg 123"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StylePreviewCard")
        self.setFixedHeight(64)
        self._doc = QTextDocument(self)
        self._doc.setDefaultFont(QFont())

    def set_format(self, ffmt: FontFormat):
        sample = self.SAMPLE
        if ffmt.vertical:
            sample = "\n".join(self.SAMPLE.replace(" ", ""))
        weight = "bold" if ffmt.bold or (ffmt.font_weight or 0) >= 600 else "normal"
        fg = ffmt.foreground_color()
        css = (
            f"font-family: '{ffmt.font_family}'; "
            f"font-size: {max(12, min(int(ffmt.font_size), 32))}px; "
            f"color: rgb({int(fg[0])},{int(fg[1])},{int(fg[2])}); "
            f"font-weight: {weight};"
        )
        if ffmt.italic:
            css += " font-style: italic;"
        if ffmt.underline:
            css += " text-decoration: underline;"
        self._doc.setHtml(
            f'<p style="line-height: 115%;"><span style="{css}">{sample}</span></p>'
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_Widget, opt, painter, self
        )
        self._doc.setTextWidth(self.width() - 16)
        self._doc.drawContents(
            painter, QRectF(8, 4, self.width() - 16, self.height() - 8)
        )
        painter.end()


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
        self._proj = None
        self._scene_manager = None

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(12, 8, 12, 8)

        # ── Header (rename + info) ────────────────────────────────
        self._name_edit = QLineEdit()
        self._name_edit.setToolTip(self.tr("Base style name"))
        self._name_edit.editingFinished.connect(self._on_name_edited)
        self._layout.addWidget(self._name_edit)

        self._header_info = QLabel()
        self._header_info.setWordWrap(True)
        self._layout.addWidget(self._header_info)

        # ── Preview card + key-parameter chips ────────────────────
        self._preview_card = StylePreviewCard()
        self._layout.addWidget(self._preview_card)

        self._chip_bar = _ChipBar()
        self._chip_bar.chip_clicked.connect(self._scroll_to_group)
        self._layout.addWidget(self._chip_bar)

        self._layout.addWidget(SeparatorWidget())

        # ── Four diff-first field groups ──────────────────────────
        self._panel = FormatEditorPanel()
        self._panel.field_changed.connect(self._on_field_changed)
        self._panel.setMinimumHeight(200)
        self._layout.addWidget(self._panel, 1)

        self._layout.addWidget(SeparatorWidget())

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
        self._layout.addWidget(preset_row)

        # ── Mode-specific action buttons ─────────────────────────
        self._reset_base_btn = QPushButton(self.tr("Reset to Base"))
        self._reset_base_btn.setToolTip(
            self.tr(
                "Write the base style's values back to this variant's blocks; the variant dissolves when its overrides are gone."
            )
        )
        self._reset_base_btn.clicked.connect(self._reset_variant_to_base)
        self._layout.addWidget(self._reset_base_btn)

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
        self._layout.addWidget(self._apply_all_btn)

        self._layout.addWidget(SeparatorWidget())

        # ── Block distribution chips ──────────────────────────────
        self._layout.addWidget(_SectionHeader(self.tr("Blocks Using This Style")))
        self._block_chips = _ChipBar()
        self._block_chips.chip_clicked.connect(self._on_page_chip_clicked)
        self._layout.addWidget(self._block_chips)

        self._layout.addStretch()
        self.setWidget(container)

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

        self._panel.set_format(ffmt)
        self._refresh_preview_and_chips(ffmt)

        blocks = list(node.pure.blocks)
        for var in node.variants:
            blocks.extend(var.blocks)
        self._populate_block_list(blocks)

        self._name_edit.show()
        self._reset_base_btn.hide()
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

        # 差异优先：变体只渲染 override 字段，其余组隐藏
        self._panel.set_format(rep, only_fields=set(variant.overrides))
        self._refresh_preview_and_chips(rep)

        self._populate_block_list(variant.blocks)

        self._name_edit.hide()
        self._reset_base_btn.show()
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

        self._panel.set_format(ffmt)
        self._refresh_preview_and_chips(ffmt)

        self._populate_block_list(entry.blocks)

        self._name_edit.hide()
        self._reset_base_btn.hide()
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

    # -- preview & chips ---------------------------------------------------

    def _refresh_preview_and_chips(self, ffmt: FontFormat):
        # Reload preset list so it reflects the latest saved presets
        self._load_presets()

        self._preview_card.set_format(ffmt)

        chips: List[Tuple[str, str, str | None]] = [
            ("text", ffmt.font_family, None),
            ("text", f"{ffmt.font_size:g}px", None),
        ]
        if ffmt.italic:
            chips.append(("text", self.tr("Italic"), None))
        if ffmt.underline:
            chips.append(("text", self.tr("Underline"), None))
        if ffmt.strikeout:
            chips.append(("text", self.tr("Strikeout"), None))
        fg = ffmt.foreground_color()
        fg_hex = "#{:02X}{:02X}{:02X}".format(*[int(c) for c in fg[:3]])
        chips.append(("color", fg_hex, fg_hex))
        if ffmt.stroke_width > 0:
            chips.append(
                ("color", self.tr("Stroke {n}px").format(n=f"{ffmt.stroke_width:g}"), None)
            )
        if ffmt.shadow_radius > 0:
            chips.append(("effects", self.tr("Shadow"), None))
        if ffmt.gradient_enabled:
            chips.append(("effects", self.tr("Gradient"), None))
        if ffmt.opacity < 1.0:
            chips.append(("color", self.tr("Opacity: {o}").format(o=ffmt.opacity), None))
        self._chip_bar.set_chips(chips)

    def _scroll_to_group(self, key: str):
        self._panel.scroll_to_group(key)

    def _on_field_changed(self, _fname: str):
        """Live-update the preview card while the user edits fields."""
        ffmt = self._panel.baseline_copy()
        self._panel.sync_into(ffmt)
        self._refresh_preview_and_chips(ffmt)

    # -- block distribution -------------------------------------------------

    def _populate_block_list(self, blocks: List[Tuple[str, int]]):
        """Per-page chips: ``p1.png (3)`` — click jumps to the first block."""
        self._block_chips.clear_chips()
        if self._proj is None:
            return
        page_map: Dict[str, List[int]] = {}
        for pname, bidx in blocks:
            page_map.setdefault(pname, []).append(bidx)

        chips: List[Tuple[str, str, str | None]] = []
        tooltips: List[str] = []
        for pname, bidx_list in sorted(page_map.items()):
            bidx_list = sorted(bidx_list)
            chips.append((f"{pname}\x00{bidx_list[0]}", f"{pname}  ({len(bidx_list)})", None))
            tooltips.append(self.tr("Blocks: {n}").format(
                n=", ".join(str(b) for b in bidx_list[:20])
            ))
        self._block_chips.set_chips(chips)
        chips_widgets = self._block_chips._chips
        for i, tip in enumerate(tooltips):
            if i < len(chips_widgets):
                chips_widgets[i].setToolTip(tip)

    def _on_page_chip_clicked(self, chip_id: str):
        pname, _, bidx = chip_id.partition("\x00")
        if pname:
            self.navigate_to_block.emit(pname, int(bidx))

    # ── Change collection & apply ───────────────────────────────────

    def _collect_changed(self) -> Dict:
        """Quantized diff of the field editors against the baseline format.

        Only fields the user actually changed become part of the change —
        the flatten semantics rely on this (untouched parameters must not
        clobber per-block overrides).
        """
        return self._panel.changed_values()

    def _apply_ffmt_changes(self, changes: List[Dict], reselect, description: str):
        """Shared batch-apply flow: undo command → apply → rebuild → refresh."""
        # 1. Create the command FIRST — its constructor captures the current
        #    live-item state (HTML / rect) for undo BEFORE we modify anything.
        if changes:
            if self._scene_manager is not None:
                from .fontstyle_manager_commands import BatchFontformatCommand

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
                    continue
                # 画布 item 不在（场景重建/离屏测试）→ 落数据层兜底
            page = self._proj.pages.get(pname)
            if page is None or not 0 <= bidx < len(page):
                continue
            blk = page[bidx]
            blk.fontformat = new_ffmt
            self._proj.mark_page_needs_rerender(pname)

    # -- apply dispatch ----------------------------------------------------

    def _apply_all(self):
        """Apply all field edits at once, per current mode."""
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

    def _reset_variant_to_base(self):
        """Write the base values back for every override field of the variant."""
        if (
            self._mode != self.MODE_VARIANT
            or self._variant is None
            or self._base_node is None
            or self._proj is None
        ):
            return
        base_ffmt = self._base_node.base.fontformat
        changed: Dict = {}
        for f in self._variant.overrides:
            if f not in ("font_family", "vertical"):
                changed[f] = copy_value(getattr(base_ffmt, f, None))
        if not changed:
            return
        changes = build_variant_changes(self._variant.blocks, self._proj, changed)
        if not changes:
            return
        reselect = {"type": "base", "identity": self._base_node.base.identity}
        self._apply_ffmt_changes(
            changes, reselect, self.tr("Reset variant to base")
        )

    def _apply_sig(self):
        """Full-parameter apply onto signature-matched blocks."""
        if self._entry is None or self._proj is None:
            return
        candidate = self._panel.current_values()
        new_ffmt = self._entry.fontformat.deepcopy()
        for k, v in candidate.items():
            setattr(new_ffmt, k, copy_value(v))
        # face 为派生显示缓存：整包定型后重算（快照之前）
        sync_face(new_ffmt)
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
            for k in self._panel.current_values():
                v = getattr(new_ffmt, k, None)
                old = getattr(base_style.fontformat, k, None)
                if v is None:
                    continue
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
        # face 为派生显示缓存：整包定型后重算（快照之前）
        sync_face(new_ffmt)
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
