"""Config-page editor for the canvas quick menus (multi-menu, drag-config).

Layout (top → bottom):
  - toolbar card (``#PieMenuToolbar``): menu tabs + "New Menu" button,
    per-menu props row 1 (name + trigger key with conflict pill),
    per-menu props row 2 (style Ring / List, sectors for ring, direction
    Left/Right for list) — packaged custom controls (ConfigLineEdit /
    ConfigComboBox) for a consistent look with the rest of the settings
  - separator hairline
  - live preview: embedded :class:`PieMenu` in edit mode inside a
    ``GroupFrame`` — renders the ring or the half-ring list panels exactly
    like runtime, only scaled down
  - command palette: flow grid of draggable command cards (with category
    badges); drag one onto the preview to place it (max 3 per sector /
    panel), drag a card back to remove
  - canvas-options palette (second section, magenta badges): checkbox
    toggles (Snap Alignment ...) — same drag & drop, rendered as checkboxes
    in the menu; kept apart from regular commands on purpose

Ring and List are two independent arrangements of the same command group:
switching the style only shows the other arrangement — it never converts,
merges or overwrites the other layout (decision 2026-08-16).  One menu =
one function group; split into new menus when you want separation.

The editor mutates ``pcfg.pie_menus`` live (same pattern as the shortcut
editor) and saves on each committed change, so the runtime menu picks the
edit up on the next trigger press.
"""

import copy
import json
import re
from pathlib import Path

import utils.config

from qtpy.QtCore import QCoreApplication, QMimeData, Qt, Signal
from qtpy.QtGui import QDrag, QKeySequence
from qtpy.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from utils.config import pcfg, save_config
from utils.shortcut_conflicts import find_conflict_keys
from .context_menu_config import (
    CAT_BASIC,
    CAT_PIPELINE,
    CAT_TEXT,
    CAT_TOGGLE,
    CAT_VIEW,
    COMMAND_REGISTRY,
)
from .pie_menu import (
    SECTOR_MAX_CARDS,
    PieMenu,
    normalize_pie_menu,
    pie_menu_display_name,
)
from .custom_widget import (
    ConfigComboBox,
    ConfigLineEdit,
    FlowLayout,
    GroupFrame,
)
from .misc import get_theme_color
from .theme_helpers import shortcut_styles

_SECTOR_CHOICES = (4, 6, 8)
PIE_MENU_MAX = 4   # menu cap — more trigger keys become hard to remember

# Palette category order + display labels + badge colors (tr keys; orphans
# by design — indirect calls, add <message> to the PieMenuEditor ts context).
_CATEGORY_LABELS = [
    (CAT_BASIC, "Basic Editing", "#1e93e5"),
    (CAT_TEXT, "Text Operations", "#27ae60"),
    (CAT_PIPELINE, "Pipeline", "#e67e22"),
    (CAT_VIEW, "View", "#8e44ad"),
]

# Checkbox toggles live in a separate palette section below the regular
# commands (decision 2026-08-14) — magenta badge sets them apart.
_TOGGLE_LABEL = "Canvas Options"
_TOGGLE_COLOR = "#e84393"

_CARD_H = 44
_CARD_W_MAX = 400         # defensive width cap — labels elide only past this
_DRAG_SCALE = 0.55   # drag-ghost shrinks so the small (0.72-scaled) preview
                     # stays visible while dragging a card in


class _CommandCard(QFrame):
    """One draggable command chip in the palette grid (name + category badge)."""

    def __init__(self, cmd_id, name, cat_label, cat_color, parent=None):
        super().__init__(parent)
        self.cmd_id = cmd_id
        self._drag_start = None
        self.setObjectName("PieCmdCard")
        self.setToolTip(name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        self.name_label = QLabel(name)
        badge = QLabel(cat_label)
        badge.setStyleSheet(f"color: {cat_color}; font-size: 10px;")
        # Card width follows its longest line (name or category badge) so
        # long command names show in full while the grid has room — the
        # tooltip always carries the full name anyway (2026-08-16).
        name_w = self.name_label.fontMetrics().horizontalAdvance(name)
        badge_w = badge.fontMetrics().horizontalAdvance(cat_label)
        text_w = max(name_w, badge_w)
        w = min(text_w + 2 * 8 + 12, _CARD_W_MAX)   # margins (8+8) + padding
        self.setFixedSize(w, _CARD_H)
        if text_w + 2 * 8 + 12 > _CARD_W_MAX:
            self.name_label.setText(self.name_label.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, _CARD_W_MAX - 2 * 8 - 12))
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for lbl in (self.name_label, badge):
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.name_label)
        lay.addWidget(badge)

    def set_used(self, used: bool):
        """Track "already in this menu (current layout)" — the property stays
        for the duplicate-add guard parity, but the *visual* dimming is
        disabled for now (2026-08-16): on the user's machine the property
        updates live yet the QSS never re-renders, root cause unknown —
        parked until there is time to debug; keep this property mechanism
        so restoring the look is a one-line stylesheet change."""
        if self.property("used") == used:
            return
        self.setProperty("used", used)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None \
                and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position() - self._drag_start).manhattanLength() \
                    >= QApplication.startDragDistance():
                self._drag_start = None
                drag = QDrag(self)
                md = QMimeData()
                md.setData("application/x-pie-cmd", self.cmd_id.encode("utf-8"))
                drag.setMimeData(md)
                # Scale the ghost down (and the hotspot with it) — a full-size
                # 128px card hides the small preview's drop slots.
                pix = self.grab()
                pix = pix.scaled(
                    max(1, int(pix.width() * _DRAG_SCALE)),
                    max(1, int(pix.height() * _DRAG_SCALE)),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                drag.setPixmap(pix)
                drag.setHotSpot((event.position() * _DRAG_SCALE).toPoint())
                drag.exec(Qt.DropAction.MoveAction)
                return
        super().mouseMoveEvent(event)


class CommandPalette(QWidget):
    """Flow grid of draggable command cards.

    Cards drag out with the ``application/x-pie-cmd`` mime; dropping a menu
    card back here (``application/x-pie-src``) emits :attr:`remove_requested`.
    """

    remove_requested = Signal(int, int)   # (sector, idx) of the dropped-back card

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.flow = FlowLayout(self)
        self.flow.setContentsMargins(0, 2, 0, 2)
        self.flow.setHorizontalSpacing(8)
        self.flow.setVerticalSpacing(8)
        self._cards = {}

    def set_commands(self, commands):
        """Rebuild the grid. *commands*: (cmd_id, name, cat_label, cat_color)."""
        self.flow.takeAllWidgets()
        self._cards.clear()
        s = shortcut_styles()
        accent = get_theme_color(key="@accentPrimary").name()
        border = get_theme_color(key="@borderColor").name()
        self.setStyleSheet(
            f"#PieCmdCard {{ background: {s['card_bg']};"
            f" border: 1px solid {border}; border-radius: 6px; }}"
            f"#PieCmdCard:hover {{ border-color: {accent}; }}"
            f"#PieCmdCard QLabel {{ color: {s['name_clr']};"
            f" background: transparent; border: none; }}"
        )
        for cmd_id, name, cat_label, cat_color in commands:
            card = _CommandCard(cmd_id, name, cat_label, cat_color, self)
            self.flow.addWidget(card)
            self._cards[cmd_id] = card
        self.updateGeometry()

    def set_used(self, used_ids):
        """Dim cards already in the current menu's current layout (hint only —
        the drag guard rejects a duplicate add anyway)."""
        for cmd_id, card in self._cards.items():
            card.set_used(cmd_id in used_ids)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-pie-src"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-pie-src"):
            try:
                s, i = bytes(md.data("application/x-pie-src")).decode("utf-8").split(",")
            except ValueError:
                return super().dropEvent(event)
            self.remove_requested.emit(int(s), int(i))
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class PieMenuEditor(QWidget):
    """Multi-menu drag-config page (registered in the ConfigPanel)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._menus = [normalize_pie_menu(m) for m in (pcfg.pie_menus or [])]
        if not self._menus:
            self._menus = [normalize_pie_menu({})]
        self._current = 0
        self._build_ui()
        self._connect_signals()
        self._reload()

    # ── UI construction ────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)   # page provides the margins
        layout.setSpacing(8)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        # ── Menu management card (tabs + per-menu props) ─────
        card = QFrame()
        card.setObjectName("PieMenuToolbar")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 10)
        card_layout.setSpacing(6)

        # Row 1: menu tabs + new button
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setExpanding(False)
        self.new_btn = QPushButton(self.tr("New Menu"))
        self.new_btn.setObjectName("ConfigButton")
        self.new_btn.setFixedHeight(30)   # match the tab row height
        bar.addWidget(self.tabs, 1)
        bar.addWidget(self.new_btn)
        self.reset_btn = QPushButton(self.tr("Reset to Defaults"))
        self.reset_btn.setObjectName("ConfigButton")
        self.reset_btn.setFixedHeight(30)
        bar.addWidget(self.reset_btn)
        # Dev tool (2026-08-16): persist the arranged layout into
        # utils/config.py::DEFAULT_PIE_MENUS so future defaults match it.
        self.save_default_btn = QPushButton(self.tr("Save as Default"))
        self.save_default_btn.setObjectName("ConfigButton")
        self.save_default_btn.setFixedHeight(30)
        bar.addWidget(self.save_default_btn)
        card_layout.addLayout(bar)

        # Row 2: name + trigger key
        props1 = QHBoxLayout()
        props1.setSpacing(6)
        props1.addWidget(QLabel(self.tr("Name:")))
        self.name_edit = ConfigLineEdit()
        self.name_edit.setFixedHeight(29)   # match the row's other fields
        props1.addWidget(self.name_edit, 1)
        props1.addSpacing(8)
        props1.addWidget(QLabel(self.tr("Trigger Key:")))
        self.trigger_edit = QKeySequenceEdit()
        self.trigger_edit.setClearButtonEnabled(True)
        self.trigger_edit.setFixedWidth(130)
        props1.addWidget(self.trigger_edit)
        self.conflict_label = QLabel()
        self.conflict_label.hide()
        props1.addWidget(self.conflict_label)
        card_layout.addLayout(props1)

        # Row 3: style + sectors / direction
        props2 = QHBoxLayout()
        props2.setSpacing(6)
        props2.addWidget(QLabel(self.tr("Style:")))
        self.style_combo = ConfigComboBox(fix_size=False)
        self.style_combo.addItems([self.tr("Ring"), self.tr("List")])
        # CJK items ("环形"/"竖排") are wider than the combos' English-derived
        # sizeHint — give the text room so it isn't clipped by the dropdown.
        self.style_combo.setMinimumWidth(110)
        props2.addWidget(self.style_combo)
        self.sectors_label = QLabel(self.tr("Sectors:"))
        self.sectors_combo = ConfigComboBox(fix_size=False)
        self.sectors_combo.addItems([str(n) for n in _SECTOR_CHOICES])
        props2.addWidget(self.sectors_label)
        props2.addWidget(self.sectors_combo)
        self.direction_label = QLabel(self.tr("Direction:"))
        self.direction_combo = ConfigComboBox(fix_size=False)
        self.direction_combo.addItems([self.tr("Right"), self.tr("Left")])
        self.direction_combo.setMinimumWidth(110)
        props2.addWidget(self.direction_label)
        props2.addWidget(self.direction_combo)
        props2.addStretch()
        card_layout.addLayout(props2)

        layout.addWidget(card)

        # Separator between the toolbar card and the preview
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: @borderColor;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Live preview (scaled, edit-mode) inside a group frame so the
        # floating canvas reads as a deliberate block (ring layout) instead
        # of a lone circle on the page.  The widget is Fixed-size so the
        # layout can never squeeze it; the surrounding ConfigPanel page
        # scrolls instead.  (A nested QScrollArea here collapsed to a thin
        # strip whenever vertical space ran short — removed 2026-08-12.)
        self.preview = PieMenu(None, mw=None, parent=self, preview=True)
        self.preview.set_edit_mode(True)
        self.preview.set_preview_scale(0.72)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pv_frame = GroupFrame()
        pv_lay = QVBoxLayout(pv_frame)
        pv_lay.setContentsMargins(8, 8, 8, 8)
        pv_lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(pv_frame)

        # Command palette
        self.palette_hint = QLabel(
            self.tr("Commands (drag onto the menu; max %1 per sector, "
                    "right-click a card to remove):")
            .replace("%1", str(SECTOR_MAX_CARDS)))
        self.palette_hint.setWordWrap(True)   # a single long line would
        # force the whole page wider than the ConfigPanel viewport
        layout.addWidget(self.palette_hint)
        self.palette = CommandPalette(self)
        layout.addWidget(self.palette, 1)

        # Checkbox toggles: a separate section (they render differently in
        # the menu, so they get their own area and badge — 2026-08-14).
        self.toggle_hint = QLabel(self.tr(
            "Canvas Options (checkbox commands — click a card in the menu to switch it on/off):"))
        self.toggle_hint.setWordWrap(True)
        layout.addWidget(self.toggle_hint)
        self.toggle_palette = CommandPalette(self)
        layout.addWidget(self.toggle_palette)

    def _connect_signals(self):
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(self._on_delete_menu)
        self.new_btn.clicked.connect(self._on_new_menu)
        self.reset_btn.clicked.connect(self._on_reset_defaults)
        self.save_default_btn.clicked.connect(self._save_as_default)
        self.name_edit.textEdited.connect(self._on_name_changed)
        self.trigger_edit.keySequenceChanged.connect(self._on_trigger_changed)
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        self.sectors_combo.currentIndexChanged.connect(self._on_sectors_changed)
        self.direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        self.preview.slot_remove_requested.connect(self._on_card_remove)
        self.preview.command_dropped.connect(self._on_command_dropped)
        self.palette.remove_requested.connect(self._on_card_remove)
        self.toggle_palette.remove_requested.connect(self._on_card_remove)

    # ── Current menu helpers ───────────────────────────────

    def _current_menu(self):
        if not self._menus or not (0 <= self._current < len(self._menus)):
            return None
        return self._menus[self._current]

    def _reload(self):
        """Rebuild tabs + props + preview from the working copy."""
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for menu in self._menus:
            self.tabs.addTab(pie_menu_display_name(menu))
        if self._menus:
            self.tabs.setCurrentIndex(min(self._current, len(self._menus) - 1))
        self.tabs.blockSignals(False)
        self._populate_palette()
        self._load_current_props()
        self._refresh_preview()
        self._refresh_conflicts()

    def _load_current_props(self):
        menu = self._current_menu()
        enabled = menu is not None
        for w in (self.name_edit, self.trigger_edit, self.sectors_combo,
                  self.style_combo, self.direction_combo):
            w.blockSignals(True)
        if menu is None:
            self.name_edit.clear()
            self.trigger_edit.clear()
        else:
            self.name_edit.setText(menu.get("name", ""))
            self.trigger_edit.setKeySequence(QKeySequence(menu.get("trigger", "")))
            is_list = menu.get("layout", "ring") == "list"
            self.style_combo.setCurrentIndex(1 if is_list else 0)
            self.direction_combo.setCurrentIndex(
                1 if menu.get("direction", "right") == "left" else 0)
            sectors = menu.get("sectors", 8)
            self.sectors_combo.setCurrentIndex(
                _SECTOR_CHOICES.index(sectors) if sectors in _SECTOR_CHOICES else 2)
        for w in (self.name_edit, self.trigger_edit, self.sectors_combo,
                  self.style_combo, self.direction_combo):
            w.blockSignals(False)
        is_list = bool(menu) and menu.get("layout", "ring") == "list"
        self.sectors_label.setVisible(enabled and not is_list)
        self.sectors_combo.setVisible(enabled and not is_list)
        self.direction_label.setVisible(enabled and is_list)
        self.direction_combo.setVisible(enabled and is_list)
        for w in (self.name_edit, self.trigger_edit, self.sectors_combo,
                  self.style_combo, self.direction_combo):
            w.setEnabled(enabled)
        self._refresh_hint()

    # ── Slots ──────────────────────────────────────────────

    def _on_tab_changed(self, index):
        if 0 <= index < len(self._menus):
            self._current = index
        self._load_current_props()
        self._refresh_preview()
        self._refresh_conflicts()

    def _on_new_menu(self):
        if len(self._menus) >= PIE_MENU_MAX:
            from utils.message import create_info_dialog
            create_info_dialog(self.tr(
                "At most %1 quick menus are supported."
            ).replace("%1", str(PIE_MENU_MAX)))
            return
        n = len(self._menus) + 1
        self._menus.append(normalize_pie_menu({
            "id": f"menu{n}",
            "name": self.tr("New Menu"),
            "trigger": "",
            "sectors": 8,
            "layout": "ring",
            "slots": [[] for _ in range(8)],
        }))
        self.tabs.addTab(pie_menu_display_name(self._menus[-1]))
        self.tabs.setCurrentIndex(len(self._menus) - 1)
        # The new menu is empty: clear the used-grey even when the tab index
        # did not move (currentChanged then never fires — 2026-08-16).
        self._refresh_preview()
        self._save()

    def _on_delete_menu(self, index):
        if not (0 <= index < len(self._menus)):
            return
        self._menus.pop(index)
        if self._current > index:
            self._current -= 1
        self._current = min(self._current, max(0, len(self._menus) - 1))
        self._reload()
        self._save()

    def _on_reset_defaults(self):
        """Restore every quick menu to the built-in default template."""
        reply = QMessageBox.question(
            self,
            self.tr("Reset"),
            self.tr("Reset all quick menus to the default layout?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._menus = [normalize_pie_menu(m)
                       for m in utils.config.DEFAULT_PIE_MENUS]
        self._current = 0
        self._reload()
        self._save()

    def _on_name_changed(self, text):
        menu = self._current_menu()
        if menu is None:
            return
        menu["name"] = text
        self.tabs.setTabText(self._current, text or self.tr("New Menu"))
        self._save()

    def _on_trigger_changed(self, ks: QKeySequence):
        menu = self._current_menu()
        if menu is None:
            return
        menu["trigger"] = ks.toString(QKeySequence.SequenceFormat.PortableText)
        self._refresh_conflicts()
        self._save()

    def _on_sectors_changed(self, index):
        menu = self._current_menu()
        if menu is None:
            return
        n = _SECTOR_CHOICES[index] if 0 <= index < len(_SECTOR_CHOICES) else 8
        menu["sectors"] = n
        slots = menu["slots"]
        slots = slots[:n] + [[] for _ in range(n - len(slots[:n]))]
        menu["slots"] = slots
        self._refresh_preview()
        self._save()

    def _on_style_changed(self, index):
        """Switch which arrangement the menu shows.  Ring ``slots`` and list
        ``panels`` are independent layouts, each edited on its own — a style
        switch never converts, merges or overwrites the other one (decision
        2026-08-16).
        """
        menu = self._current_menu()
        if menu is None:
            return
        new_layout = "list" if index == 1 else "ring"
        if new_layout == menu.get("layout", "ring"):
            return
        menu["layout"] = new_layout
        self._load_current_props()   # sync combos + visibility (signals blocked)
        self._refresh_preview()
        self._save()

    def _on_direction_changed(self, index):
        """Side of the cursor the list panels sit on — a pure render mirror
        of the same five panels, never touches the ring slots."""
        menu = self._current_menu()
        if menu is None:
            return
        new_dir = "left" if index == 1 else "right"
        if new_dir == menu.get("direction", "right"):
            return
        menu["direction"] = new_dir
        self._refresh_preview()   # panel anchors mirror to the other side
        self._save()

    def _on_command_dropped(self, sector, idx, cmd_id, src_sector, src_idx):
        """Drop onto the preview.  For the list layout the sector argument
        carries the panel index; the storage group switches accordingly."""
        menu = self._current_menu()
        if menu is None:
            return
        key = "panels" if menu.get("layout", "ring") == "list" else "slots"
        groups = menu[key]
        if src_sector < 0 and any(cmd_id in g for g in groups):
            return   # one copy per command per menu (adds only — internal
                     # drags carry the source slot and relocate the same card)
        if src_sector >= 0 and 0 <= src_sector < len(groups) \
                and 0 <= src_idx < len(groups[src_sector]):
            groups[src_sector].pop(src_idx)
        sector = max(0, min(sector, len(groups) - 1))
        groups[sector].insert(max(0, min(idx, len(groups[sector]))), cmd_id)
        self._refresh_preview()
        self._save()

    def _on_card_remove(self, sector, idx):
        """Remove a card (right-click in the preview, or dragged back onto
        the palette).  For the list layout the sector argument is the panel
        index."""
        menu = self._current_menu()
        if menu is None:
            return
        key = "panels" if menu.get("layout", "ring") == "list" else "slots"
        groups = menu[key]
        if 0 <= sector < len(groups) and 0 <= idx < len(groups[sector]):
            groups[sector].pop(idx)
        self._refresh_preview()
        self._save()

    # ── Refresh ────────────────────────────────────────────

    def _refresh_preview(self):
        menu = self._current_menu() or {}
        self.preview.set_menu_config(menu)
        self.preview.set_edit_mode(True)
        self._refresh_hint()
        self._refresh_used()

    def _refresh_used(self):
        """Dim palette cards already placed in the current menu's *current*
        layout (ring reads ``slots``, list reads ``panels`` — each menu and
        each style is checked independently, mirroring the duplicate-add
        guard)."""
        menu = self._current_menu() or {}
        key = "panels" if menu.get("layout", "ring") == "list" else "slots"
        used = {cid for group in menu.get(key, []) for cid in group}
        self.palette.set_used(used)
        self.toggle_palette.set_used(used)

    def _refresh_hint(self):
        """Palette hint text follows the current menu's style."""
        menu = self._current_menu() or {}
        if menu.get("layout") == "list":
            text = self.tr(
                "Commands (drag onto the list; drag cards to reorder, right-click a card to remove):"
            )
        else:
            text = self.tr(
                "Commands (drag onto the menu; max %1 per sector, right-click a card to remove):"
            ).replace("%1", str(SECTOR_MAX_CARDS))
        self.palette_hint.setText(text)

    def _refresh_conflicts(self):
        """Trigger keys must not collide with each other or with shortcuts."""
        mapping = {}
        for i, m in enumerate(self._menus):
            t = m.get("trigger", "")
            if t:
                mapping[f"pie_menu_{i}"] = [t]
        from .configpanel import DEFAULT_SHORTCUTS, default_keys_for  # lazy: avoid circular import
        for aid in DEFAULT_SHORTCUTS:
            keys = (pcfg.shortcuts or {}).get(aid) or default_keys_for(aid) or []
            mapping[aid] = [k for k in keys if k]
        conflicts = find_conflict_keys(mapping)
        menu = self._current_menu()
        t = menu.get("trigger", "") if menu else ""
        if t and t in conflicts:
            s = shortcut_styles()
            self.conflict_label.setText(self.tr("Conflict: already in use"))
            self.conflict_label.setStyleSheet(
                f"background:{s['conflict_pill_bg']};"
                f"color:{s['conflict_pill_text']};"
                "border-radius:9px; padding:2px 10px;")
            self.conflict_label.show()
        else:
            self.conflict_label.hide()

    def _populate_palette(self):
        """Regular command grid + a separate section for checkbox toggles."""
        commands = []
        for cat, label, color in _CATEGORY_LABELS:
            cmds = [c for c in COMMAND_REGISTRY.values()
                    if c.run_fn is not None and c.category == cat]
            for cmd in sorted(cmds, key=lambda c: self.tr(c.label_key).lower()):
                commands.append((
                    cmd.id,
                    QCoreApplication.translate("Canvas", cmd.label_key),
                    self.tr(label),
                    color,
                ))
        self.palette.set_commands(commands)

        toggle_cmds = [c for c in COMMAND_REGISTRY.values()
                       if c.run_fn is not None and c.category == CAT_TOGGLE]
        toggle_cards = [
            (cmd.id,
             QCoreApplication.translate("Canvas", cmd.label_key),
             self.tr(_TOGGLE_LABEL),
             _TOGGLE_COLOR)
            for cmd in sorted(toggle_cmds,
                              key=lambda c: self.tr(c.label_key).lower())
        ]
        self.toggle_palette.set_commands(toggle_cards)

    def _save(self):
        pcfg.pie_menus = self._menus
        save_config()

    def _save_as_default(self):
        """Dev tool: overwrite ``utils/config.py::DEFAULT_PIE_MENUS`` with the
        current layout, so fresh configs and "Reset to Defaults" start from
        what was arranged here (newly added commands land in a sane default
        without hand-editing the source)."""

        def _fmt(v, depth):
            """Source-format a value: dicts expand per key, str lists stay
            inline, nested grids (slots/panels) expand one row per line."""
            pad = "    " * depth
            if isinstance(v, dict):
                items = ",\n".join(
                    f"{pad}    {json.dumps(k, ensure_ascii=False)}: "
                    f"{_fmt(x, depth + 1)}"
                    for k, x in v.items())
                return "{\n" + items + "\n" + pad + "}"
            if isinstance(v, list):
                if not v:
                    return "[]"
                if all(isinstance(x, str) for x in v):
                    return "[" + ", ".join(
                        json.dumps(x, ensure_ascii=False) for x in v) + "]"
                items = ",\n".join(
                    f"{pad}    " + _fmt(x, depth + 1) for x in v)
                return "[\n" + items + "\n" + pad + "]"
            return json.dumps(v, ensure_ascii=False)

        try:
            cfg_path = Path(__file__).resolve().parents[1] / "utils" / "config.py"
            text = cfg_path.read_text(encoding="utf-8")
            block = "DEFAULT_PIE_MENUS = " + _fmt(self._menus, 0)
            new_text, n = re.subn(
                r"DEFAULT_PIE_MENUS = \[.*?\n\]",
                block, text, count=1, flags=re.S)
            if n == 0:
                raise RuntimeError("DEFAULT_PIE_MENUS block not found")
            cfg_path.write_text(new_text, encoding="utf-8")
            # The file write alone would not affect this process — the
            # module constant was bound at import time — so update it in
            # memory too, making "Reset to Defaults" use the new layout
            # immediately (dev-stage priority over the old defaults).
            utils.config.DEFAULT_PIE_MENUS = copy.deepcopy(self._menus)
            QMessageBox.information(
                self, self.tr("Save as Default"),
                self.tr("Default menu layout saved to utils/config.py."))
        except Exception as exc:
            QMessageBox.warning(
                self, self.tr("Save as Default"),
                self.tr("Failed to save default layout: %1").replace(
                    "%1", str(exc)))
