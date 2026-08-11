"""Config-page editor for the canvas ring menus (multi-menu, drag-config).

Layout (top → bottom):
  - menu management bar: menu tabs + "new" button, per-menu properties
    (name, trigger key with conflict pills, sector count)
  - live preview: embedded :class:`PieMenu` in edit mode (dashed sector
    guides, drop-target highlights, hover preview) — the exact rendering
    code used at runtime, only scaled down
  - command palette: categorized list of runnable commands; drag one onto
    the preview to place it (max 3 per sector), drag a card back to remove

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
    SECTOR_MAX_CARDS,
    WINDOW_RADIUS,
    PieMenu,
    normalize_pie_menu,
    pie_menu_display_name,
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

        # Per-menu properties
        props = QHBoxLayout()
        props.setSpacing(6)
        props.addWidget(QLabel(self.tr("Name:")))
        self.name_edit = QLineEdit()
        props.addWidget(self.name_edit, 1)
        props.addSpacing(8)
        props.addWidget(QLabel(self.tr("Trigger Key:")))
        self.trigger_edit = QKeySequenceEdit()
        self.trigger_edit.setClearButtonEnabled(True)
        self.trigger_edit.setFixedWidth(130)
        props.addWidget(self.trigger_edit)
        self.conflict_label = QLabel()
        self.conflict_label.hide()
        props.addWidget(self.conflict_label)
        props.addSpacing(8)
        props.addWidget(QLabel(self.tr("Sectors:")))
        self.sectors_combo = QComboBox()
        for n in _SECTOR_CHOICES:
            self.sectors_combo.addItem(str(n), n)
        props.addWidget(self.sectors_combo)
        props.addStretch()
        layout.addLayout(props)

        # Live preview (scaled, edit-mode)
        self.preview = PieMenu(None, mw=None, parent=self, preview=True)
        self.preview.set_edit_mode(True)
        self.preview.set_preview_scale(0.72)
        pv_host = QVBoxLayout()
        pv_host.setContentsMargins(0, 0, 0, 0)
        pv_host.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(pv_host)

        # Command palette
        layout.addWidget(QLabel(
            self.tr("Commands (drag onto the menu; max %1 per sector, "
                    "right-click a card to remove):")
            .replace("%1", str(SECTOR_MAX_CARDS))))
        self.palette = CommandPalette(self)
        self.palette.setFixedHeight(190)
        layout.addWidget(self.palette, 1)

    def _connect_signals(self):
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(self._on_delete_menu)
        self.new_btn.clicked.connect(self._on_new_menu)
        self.name_edit.textEdited.connect(self._on_name_changed)
        self.trigger_edit.keySequenceChanged.connect(self._on_trigger_changed)
        self.sectors_combo.currentIndexChanged.connect(self._on_sectors_changed)
        self.preview.slot_remove_requested.connect(self._on_card_remove)
        self.preview.command_dropped.connect(self._on_command_dropped)
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
        for w in (self.name_edit, self.trigger_edit, self.sectors_combo):
            w.blockSignals(True)
        if menu is None:
            self.name_edit.clear()
            self.trigger_edit.clear()
        else:
            self.name_edit.setText(menu.get("name", ""))
            self.trigger_edit.setKeySequence(QKeySequence(menu.get("trigger", "")))
            sectors = menu.get("sectors", 8)
            self.sectors_combo.setCurrentIndex(
                _SECTOR_CHOICES.index(sectors) if sectors in _SECTOR_CHOICES else 2)
        for w in (self.name_edit, self.trigger_edit, self.sectors_combo):
            w.blockSignals(False)
        self.name_edit.setEnabled(enabled)
        self.trigger_edit.setEnabled(enabled)
        self.sectors_combo.setEnabled(enabled)

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
                "At most %1 pie menus are supported — one trigger key per "
                "menu keeps them memorable.").replace("%1", str(PIE_MENU_MAX)))
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
        menu = self._current_menu()
        if menu is None:
            return
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
