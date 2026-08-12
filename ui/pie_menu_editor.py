"""Config-page editor for the canvas quick menus (multi-menu, drag-config).

Layout (top → bottom):
  - menu management bar: menu tabs + "new" button
  - per-menu properties row 1: name, trigger key with conflict pills
  - per-menu properties row 2: style (Ring / List), sectors (ring only),
    direction Left/Right (list only)
  - live preview: embedded :class:`PieMenu` in edit mode — renders the ring
    or the vertical list exactly like runtime, only scaled down
  - command palette: categorized list of runnable commands; drag one onto
    the preview to place it (max 3 per sector / 24 per list), drag a card
    back to remove

Switching style converts the commands (ring -> list flattens the sectors,
list -> ring distributes the cards back into sectors).  One menu = one
function group; split into new menus when you want separation.

The editor mutates ``pcfg.pie_menus`` live (same pattern as the shortcut
editor) and saves on each committed change, so the runtime menu picks the
edit up on the next trigger press.
"""

from qtpy.QtCore import QCoreApplication, Qt, Signal
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils.config import pcfg, save_config
from utils.shortcut_conflicts import find_conflict_keys
from .context_menu_config import (
    CAT_BASIC,
    CAT_PIPELINE,
    CAT_TEXT,
    CAT_VIEW,
    COMMAND_REGISTRY,
)
from .pie_menu import (
    LIST_MAX_ITEMS,
    SECTOR_MAX_CARDS,
    WINDOW_RADIUS,
    PieMenu,
    items_to_slots,
    normalize_pie_menu,
    pie_menu_display_name,
    slots_to_items,
)
from .theme_helpers import shortcut_styles

_SECTOR_CHOICES = (4, 6, 8)
PIE_MENU_MAX = 4   # menu cap — more trigger keys become hard to remember

# Palette category order + display labels (tr keys; orphans by design —
# indirect calls, add <message> to the PieMenuEditor ts context).
_CATEGORY_LABELS = [
    (CAT_BASIC, "Basic Editing"),
    (CAT_TEXT, "Text Operations"),
    (CAT_PIPELINE, "Pipeline"),
    (CAT_VIEW, "View"),
]


class CommandPalette(QTreeWidget):
    """Categorized runnable-command list.

    Items drag out with the ``application/x-pie-cmd`` mime; dropping a menu
    card back here (``application/x-pie-src``) emits :attr:`remove_requested`.
    """

    remove_requested = Signal(int, int)   # (sector, idx) of the dropped-back card

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def mimeData(self, items):
        md = super().mimeData(items)
        if items:
            cmd_id = items[0].data(0, Qt.ItemDataRole.UserRole)
            if cmd_id:
                md.setData("application/x-pie-cmd", str(cmd_id).encode("utf-8"))
        return md

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
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Menu management bar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setExpanding(False)
        self.new_btn = QPushButton(self.tr("New Menu"))
        self.new_btn.setObjectName("ConfigButton")
        bar.addWidget(self.tabs, 1)
        bar.addWidget(self.new_btn)
        layout.addLayout(bar)

        # Per-menu properties (row 1: identity)
        props1 = QHBoxLayout()
        props1.setSpacing(6)
        props1.addWidget(QLabel(self.tr("Name:")))
        self.name_edit = QLineEdit()
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
        layout.addLayout(props1)

        # Per-menu properties (row 2: style)
        props2 = QHBoxLayout()
        props2.setSpacing(6)
        props2.addWidget(QLabel(self.tr("Style:")))
        self.style_combo = QComboBox()
        self.style_combo.addItem(self.tr("Ring"), "ring")
        self.style_combo.addItem(self.tr("List"), "list")
        props2.addWidget(self.style_combo)
        self.sectors_label = QLabel(self.tr("Sectors:"))
        self.sectors_combo = QComboBox()
        for n in _SECTOR_CHOICES:
            self.sectors_combo.addItem(str(n), n)
        props2.addWidget(self.sectors_label)
        props2.addWidget(self.sectors_combo)
        self.direction_label = QLabel(self.tr("Direction:"))
        self.direction_combo = QComboBox()
        self.direction_combo.addItem(self.tr("Right"), "right")
        self.direction_combo.addItem(self.tr("Left"), "left")
        props2.addWidget(self.direction_label)
        props2.addWidget(self.direction_combo)
        props2.addStretch()
        layout.addLayout(props2)

        # Live preview (scaled, edit-mode)
        self.preview = PieMenu(None, mw=None, parent=self, preview=True)
        self.preview.set_edit_mode(True)
        self.preview.set_preview_scale(0.72)
        pv_host = QVBoxLayout()
        pv_host.setContentsMargins(0, 0, 0, 0)
        pv_host.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(pv_host)

        # Command palette
        self.palette_hint = QLabel(
            self.tr("Commands (drag onto the menu; max %1 per sector, "
                    "right-click a card to remove):")
            .replace("%1", str(SECTOR_MAX_CARDS)))
        layout.addWidget(self.palette_hint)
        self.palette = CommandPalette(self)
        self.palette.setFixedHeight(190)
        layout.addWidget(self.palette, 1)

    def _connect_signals(self):
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(self._on_delete_menu)
        self.new_btn.clicked.connect(self._on_new_menu)
        self.name_edit.textEdited.connect(self._on_name_changed)
        self.trigger_edit.keySequenceChanged.connect(self._on_trigger_changed)
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        self.sectors_combo.currentIndexChanged.connect(self._on_sectors_changed)
        self.direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        self.preview.slot_remove_requested.connect(self._on_card_remove)
        self.preview.command_dropped.connect(self._on_command_dropped)
        self.preview.list_item_remove_requested.connect(self._on_item_remove)
        self.preview.list_command_dropped.connect(self._on_list_command_dropped)
        self.palette.remove_requested.connect(self._on_card_remove)

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
                "At most %1 quick menus are supported — one trigger key per menu keeps them memorable."
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
        """Switch the current menu between ring and list, converting its
        commands: ring -> list flattens the sectors, list -> ring distributes
        the cards back into sectors (<= 3 each)."""
        menu = self._current_menu()
        if menu is None:
            return
        new_layout = "list" if index == 1 else "ring"
        if new_layout == menu.get("layout", "ring"):
            return
        if new_layout == "list":
            menu["items"] = slots_to_items(menu.get("slots", []))
        else:
            menu["slots"] = items_to_slots(menu.get("items", []), menu.get("sectors", 8))
        menu["layout"] = new_layout
        self._load_current_props()   # sync combos + visibility (signals blocked)
        self._refresh_preview()
        self._save()

    def _on_direction_changed(self, index):
        menu = self._current_menu()
        if menu is None:
            return
        menu["direction"] = "left" if index == 1 else "right"
        self._save()

    def _on_list_command_dropped(self, idx, cmd_id, src_idx):
        menu = self._current_menu()
        if menu is None:
            return
        items = menu["items"]
        if src_idx >= 0 and 0 <= src_idx < len(items):
            items.pop(src_idx)
        items.insert(max(0, min(idx, len(items))), cmd_id)
        self._refresh_preview()
        self._save()

    def _on_item_remove(self, idx):
        menu = self._current_menu()
        if menu is None:
            return
        items = menu["items"]
        if 0 <= idx < len(items):
            items.pop(idx)
        self._refresh_preview()
        self._save()

    def _on_command_dropped(self, sector, idx, cmd_id, src_sector, src_idx):
        menu = self._current_menu()
        if menu is None:
            return
        slots = menu["slots"]
        if src_sector >= 0 and 0 <= src_sector < len(slots) \
                and 0 <= src_idx < len(slots[src_sector]):
            slots[src_sector].pop(src_idx)
        sector = max(0, min(sector, len(slots) - 1))
        slots[sector].insert(max(0, min(idx, len(slots[sector]))), cmd_id)
        self._refresh_preview()
        self._save()

    def _on_card_remove(self, sector, idx):
        """Remove a card dragged back onto the palette (the preview emits
        sector/idx; a list card carries ``x-pie-src = 0,idx``)."""
        menu = self._current_menu()
        if menu is None:
            return
        if menu.get("layout", "ring") == "list":
            items = menu["items"]
            if 0 <= idx < len(items):
                items.pop(idx)
        else:
            slots = menu["slots"]
            if 0 <= sector < len(slots) and 0 <= idx < len(slots[sector]):
                slots[sector].pop(idx)
        self._refresh_preview()
        self._save()

    # ── Refresh ────────────────────────────────────────────

    def _refresh_preview(self):
        menu = self._current_menu() or {}
        self.preview.set_menu_config(menu)
        self.preview.set_edit_mode(True)
        self._refresh_hint()

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
        from .configpanel import DEFAULT_SHORTCUTS  # lazy: avoid circular import
        for aid, defaults in DEFAULT_SHORTCUTS.items():
            keys = (pcfg.shortcuts or {}).get(aid) or defaults or []
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
        self.palette.clear()
        by_cat = {}
        for cmd in COMMAND_REGISTRY.values():
            if cmd.run_fn is not None and cmd.category:
                by_cat.setdefault(cmd.category, []).append(cmd)
        for cat, label in _CATEGORY_LABELS:
            cmds = by_cat.get(cat)
            if not cmds:
                continue
            root = QTreeWidgetItem([self.tr(label)])
            root.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for cmd in sorted(cmds, key=lambda c: self.tr(c.label_key).lower()):
                child = QTreeWidgetItem([
                    QCoreApplication.translate("Canvas", cmd.label_key)])
                child.setData(0, Qt.ItemDataRole.UserRole, cmd.id)
                child.setFlags(Qt.ItemFlag.ItemIsEnabled
                               | Qt.ItemFlag.ItemIsSelectable
                               | Qt.ItemFlag.ItemIsDragEnabled)
                root.addChild(child)
            self.palette.addTopLevelItem(root)
        self.palette.expandAll()

    def _save(self):
        pcfg.pie_menus = self._menus
        save_config()
