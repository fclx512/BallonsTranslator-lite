"""
Right-click context menu configuration system.

Provides:
- ``COMMAND_REGISTRY`` — all available context menu commands
- ``build_context_menu()`` — build and execute the menu from saved config
- ``ContextMenuCustomizeDialog`` — drag-drop reorder dialog for user customization

Usage (from ``Canvas.on_create_contextmenu``)::

    if self.textEditMode() and not self.creating_textblock:
        from .context_menu_config import build_context_menu
        build_context_menu(self, pos)
"""

from dataclasses import dataclass
from functools import partial
from typing import Callable, Dict, List, Optional

from qtpy.QtCore import QPoint, QSize, Qt
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from utils.config import pcfg, save_config

# ── Default layout (mirrors the current hardcoded order) ────
# This is the fallback used when ``pcfg.context_menu_order`` is empty / reset.
DEFAULT_ORDER: List[str] = [
    "copy", "paste", "delete",
    "copy_src", "paste_src",
    "---",
    "reset_angle", "squeeze",
    "---",
    "align",
    "merge",
    "behavior",
    "---",
    "translate", "ocr", "ocr_translate", "ocr_translate_inpaint",
]

SEPARATOR_SENTINEL = "---"


# ── Command definition ──────────────────────────────────────

@dataclass
class CmdDef:
    """Descriptor for a single context-menu command.

    ``build_fn(menu, canvas)`` must add the relevant ``QAction``(s) to
    *menu* and connect their ``triggered`` signals.

    ``run_fn(canvas)`` (optional) executes the command directly without
    building a QMenu — used by the pie menu (:func:`run_cmd`).
    ``enabled_fn(canvas)`` (optional) reports whether the command can run
    right now (pie menu gray-out / unselectable).
    ``icon`` (optional) is the filename of an SVG in ``icons/`` shown next
    to the label in the pie menu.
    """

    id: str
    label_key: str = ""
    build_fn: Optional[Callable] = None
    hidden_in_customize: bool = False  # submenu leaf items
    run_fn: Optional[Callable] = None
    enabled_fn: Optional[Callable] = None
    icon: str = ""


# ── Registry ────────────────────────────────────────────────

COMMAND_REGISTRY: Dict[str, CmdDef] = {}


def _reg(cmd: CmdDef) -> CmdDef:
    COMMAND_REGISTRY[cmd.id] = cmd
    return cmd


# ── Helpers ─────────────────────────────────────────────────

def _act(menu, canvas, label_key, shortcut=None, checkable=False,
         checked=False, enabled=True, connect=None):
    """Add a ``QAction`` to *menu* with standard setup.

    NOTE: ``menu.addAction(str)`` is used instead of ``QAction(str)`` +
    ``menu.addAction(act)`` because PyQt6 will garbage-collect the
    ``QAction`` when the Python reference is lost, even though
    ``addAction(act)`` was called.  ``addAction(str)`` returns an action
    that stays alive correctly.
    """
    act = menu.addAction(canvas.tr(label_key))
    if shortcut is not None:
        act.setShortcut(shortcut)
    if checkable:
        act.setCheckable(True)
        act.setChecked(checked)
    act.setEnabled(enabled)
    if connect is not None:
        if checkable:
            # connect receives the checked bool directly
            act.triggered.connect(connect)
        else:
            # ignore the ``triggered(bool)`` argument
            act.triggered.connect(lambda _checked, fn=connect: fn())
    return act


def _emit(menu, canvas, label_key, signal, *args, shortcut=None, enabled=True):
    """Shorthand for an action that emits a signal with positional args."""
    return _act(menu, canvas, label_key, shortcut=shortcut, enabled=enabled,
                connect=partial(signal.emit, *args))


# ── Submenu builders ────────────────────────────────────────

def _build_align(menu: QMenu, canvas):
    """Build the **Align** submenu contents."""
    n_selected = len(canvas.selected_text_items())
    enabled = n_selected >= 2

    sub = menu.addMenu(canvas.tr("Align"))
    sub.setEnabled(enabled)

    _act(sub, canvas, "Align Left Edges", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("left"))
    _act(sub, canvas, "Align Right Edges", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("right"))
    _act(sub, canvas, "Align Top Edges", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("top"))
    _act(sub, canvas, "Align Bottom Edges", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("bottom"))
    sub.addSeparator()
    _act(sub, canvas, "Align Horizontal Centers", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("hcenter"))
    _act(sub, canvas, "Align Vertical Centers", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("vcenter"))
    sub.addSeparator()
    _act(sub, canvas, "Distribute Horizontally", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("dist_h"))
    _act(sub, canvas, "Distribute Vertically", enabled=enabled,
         connect=lambda: canvas.align_textblks.emit("dist_v"))


def _build_merge(menu: QMenu, canvas):
    """Merge selected text blocks in list order (by idx)."""
    n_selected = len(canvas.selected_text_items())
    _act(menu, canvas, "Merge", enabled=n_selected >= 2,
         connect=canvas.merge_textblks.emit)


def _build_behavior(menu: QMenu, canvas):
    """Build the **Behavior** submenu — snap alignment."""
    sub = menu.addMenu(canvas.tr("Behavior"))

    # Snap Alignment (checkable toggle)
    _act(sub, canvas, "Snap Alignment", checkable=True,
         checked=canvas.alignment_enabled,
         connect=lambda checked: setattr(canvas, "alignment_enabled", checked))


# ── Register all built-in commands ──────────────────────────

def _selected_count(canvas) -> int:
    return len(canvas.selected_text_items())


# --- Basic editing ---
_reg(CmdDef("copy", "Copy",
    build_fn=lambda m, c: _act(m, c, "Copy",
        shortcut=QKeySequence.StandardKey.Copy,
        connect=c.on_copy),
    run_fn=lambda c: c.on_copy(),
    enabled_fn=lambda c: c.have_selected_blkitem))

_reg(CmdDef("paste", "Paste",
    build_fn=lambda m, c: _act(m, c, "Paste",
        shortcut=QKeySequence.StandardKey.Paste,
        connect=c.on_paste),
    run_fn=lambda c: c.on_paste()))

_reg(CmdDef("delete", "Delete",
    build_fn=lambda m, c: _act(m, c, "Delete",
        shortcut=QKeySequence("Ctrl+D"),
        connect=lambda: c.delete_textblks.emit(0)),
    run_fn=lambda c: c.delete_textblks.emit(0),
    enabled_fn=lambda c: c.have_selected_blkitem,
    icon="chrome-close.svg"))

_reg(CmdDef("copy_src", "Copy source text",
    build_fn=lambda m, c: _act(m, c, "Copy source text",
        shortcut=QKeySequence("Ctrl+Shift+C"),
        connect=c.copy_src_signal.emit),
    run_fn=lambda c: c.copy_src_signal.emit()))

_reg(CmdDef("paste_src", "Paste source text",
    build_fn=lambda m, c: _act(m, c, "Paste source text",
        shortcut=QKeySequence("Ctrl+Shift+V"),
        connect=c.paste_src_signal.emit),
    run_fn=lambda c: c.paste_src_signal.emit()))

# --- Text manipulation ---
_reg(CmdDef("reset_angle", "Reset Angle",
    build_fn=lambda m, c: _act(m, c, "Reset Angle",
        connect=lambda: c.reset_angle.emit()),
    run_fn=lambda c: c.reset_angle.emit(),
    enabled_fn=lambda c: c.have_selected_blkitem))

_reg(CmdDef("squeeze", "Squeeze",
    build_fn=lambda m, c: _act(m, c, "Squeeze",
        connect=lambda: c.squeeze_blk.emit()),
    run_fn=lambda c: c.squeeze_blk.emit(),
    enabled_fn=lambda c: c.have_selected_blkitem))

# --- Align submenu ---
_reg(CmdDef("align", "Align",
    build_fn=_build_align))

# --- Align direction leaves (pie menu direct commands) -------
# Single-direction actions usable by the pie menu; hidden from the
# customize dialog (the right-click "Align" submenu remains the entry
# point there).
_ALIGN_DIRECTIONS = [
    ("align_left", "Align Left Edges", "left", "fontfmt_alignl.svg"),
    ("align_right", "Align Right Edges", "right", "fontfmt_alignr.svg"),
    ("align_top", "Align Top Edges", "top", ""),
    ("align_bottom", "Align Bottom Edges", "bottom", ""),
    ("align_hcenter", "Align Horizontal Centers", "hcenter", "fontfmt_alignc.svg"),
    ("align_vcenter", "Align Vertical Centers", "vcenter", ""),
]

for _cid, _label, _op, _icon in _ALIGN_DIRECTIONS:

    def _align_cmd(cid, label, op, icon):
        def _build(m, c):
            _act(m, c, label, enabled=_selected_count(c) >= 2,
                 connect=lambda: c.align_textblks.emit(op))

        def _run(c):
            c.align_textblks.emit(op)

        return CmdDef(cid, label, build_fn=_build, hidden_in_customize=True,
                      run_fn=_run,
                      enabled_fn=lambda c: _selected_count(c) >= 2,
                      icon=icon)

    _reg(_align_cmd(_cid, _label, _op, _icon))

# --- Merge action (single click, respects global direction) ---
_reg(CmdDef("merge", "Merge",
    build_fn=_build_merge,
    run_fn=lambda c: c.merge_textblks.emit(),
    enabled_fn=lambda c: _selected_count(c) >= 2))

# --- Behavior submenu (snap alignment + merge direction) ---
_reg(CmdDef("behavior", "Behavior",
    build_fn=_build_behavior))

# --- Pipeline actions ---
_reg(CmdDef("translate", "translate",
    build_fn=lambda m, c: _act(m, c, "translate",
        connect=lambda: c.run_blktrans.emit(-1)),
    run_fn=lambda c: c.run_blktrans.emit(-1),
    icon="bottombar_translate.svg"))

_reg(CmdDef("ocr", "OCR",
    build_fn=lambda m, c: _act(m, c, "OCR",
        connect=lambda: c.run_blktrans.emit(0)),
    run_fn=lambda c: c.run_blktrans.emit(0),
    icon="bottombar_ocr.svg"))

_reg(CmdDef("ocr_translate", "OCR and translate",
    build_fn=lambda m, c: _act(m, c, "OCR and translate",
        connect=lambda: c.run_blktrans.emit(1)),
    run_fn=lambda c: c.run_blktrans.emit(1),
    icon="bottombar_ocr.svg"))

_reg(CmdDef("ocr_translate_inpaint", "OCR, translate and inpaint",
    build_fn=lambda m, c: _act(m, c, "OCR, translate and inpaint",
        connect=lambda: c.run_blktrans.emit(2)),
    run_fn=lambda c: c.run_blktrans.emit(2)))


# ── Menu builder ────────────────────────────────────────────

def _merge_default_order(saved: List[str]) -> List[str]:
    """Merge items from ``DEFAULT_ORDER`` missing in *saved*.

    Each missing item is inserted after its predecessor in ``DEFAULT_ORDER``
    (or at the end if the predecessor is not found).  This preserves the
    user's custom order while keeping related items grouped correctly.
    """
    known = set(cmd_id for cmd_id in saved if cmd_id != SEPARATOR_SENTINEL)
    new_items = [
        cmd_id for cmd_id in DEFAULT_ORDER
        if cmd_id != SEPARATOR_SENTINEL and cmd_id not in known
    ]
    if not new_items:
        return saved

    merged = list(saved)
    # Build a lookup: predecessor -> position in DEFAULT_ORDER
    prev = None
    pred_of = {}
    for cmd_id in DEFAULT_ORDER:
        if cmd_id == SEPARATOR_SENTINEL:
            continue
        pred_of[cmd_id] = prev
        prev = cmd_id

    for new_id in new_items:
        pred = pred_of.get(new_id)
        if pred is not None and pred in known:
            # Insert after the predecessor's last occurrence in saved
            try:
                pos = merged.index(pred) + 1
            except ValueError:
                pos = len(merged)
        else:
            # Predecessor not in saved order — append before pipeline
            try:
                pos = merged.index("translate")
            except ValueError:
                pos = len(merged)
        merged.insert(pos, new_id)
    return merged


def build_context_menu(canvas, pos: QPoint):
    """Build and execute the right-click context menu.

    Reads ``pcfg.context_menu_order`` for item order, builds a ``QMenu``
    using the ``COMMAND_REGISTRY``, and executes it.  No return value;
    actions are wired directly via their ``build_fn``.
    """
    order: List[str] = pcfg.context_menu_order
    if not order:
        order = DEFAULT_ORDER
    else:
        order = _merge_default_order(order)

    menu = QMenu(canvas.gv)
    prev_separator = True  # suppress leading separator

    for cmd_id in order:
        if cmd_id == SEPARATOR_SENTINEL:
            if not prev_separator:
                menu.addSeparator()
                prev_separator = True
            continue

        cmd = COMMAND_REGISTRY.get(cmd_id)
        if cmd is None:
            continue

        if cmd.build_fn is not None:
            cmd.build_fn(menu, canvas)
        prev_separator = False

    # Strip trailing separator if the menu ended with one
    if prev_separator:
        actions = menu.actions()
        if actions and actions[-1].isSeparator():
            menu.removeAction(actions[-1])

    menu.exec_(pos)


def run_cmd(canvas, cmd_id: str) -> bool:
    """Directly execute a context-menu command by id (no QMenu built).

    Used by the pie menu.  Returns False if the command is unknown or has
    no direct-execution hook (e.g. submenu-only commands like ``align``).
    """
    cmd = COMMAND_REGISTRY.get(cmd_id)
    if cmd is None or cmd.run_fn is None:
        return False
    cmd.run_fn(canvas)
    return True


def cmd_enabled(canvas, cmd_id: str) -> bool:
    """Whether *cmd_id* can run right now (pie-menu gray-out / unselectable)."""
    cmd = COMMAND_REGISTRY.get(cmd_id)
    if cmd is None or cmd.run_fn is None:
        return False
    if cmd.enabled_fn is None:
        return True
    return cmd.enabled_fn(canvas)


# ── Customization dialog ────────────────────────────────────

class ContextMenuCustomizeDialog(QDialog):
    """Drag-drop reorder dialog for the right-click context menu.

    Uses standard ``QListWidget`` rendering — inherits the global
    stylesheet so themes (light / dark) are applied correctly.
    Separators render as thin ``QFrame`` lines; commands show their
    translated label.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Customize Context Menu"))
        self.setMinimumSize(400, 460)

        self._build_ui()
        self._connect_signals()
        self._populate_from_config()

    # - UI construction ---------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

	# -- Menu preview list --------------------------------
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setSpacing(0)
        # No custom delegate — standard QListWidget rendering
        # inherits the global stylesheet (background, color,
        # border) directly, adapting correctly to dark mode.
        self.list_widget.setStyleSheet("""
            QListWidget#ContextMenuPreview {
                border-radius: 6px;
                padding: 4px 0;
                outline: none;
            }
            QListWidget#ContextMenuPreview::item:hover {
                background: palette(midlight);
            }
            QListWidget#ContextMenuPreview::drop-indicator {
                width: 2px;
                background: palette(highlight);
            }
            QListWidget#ContextMenuPreview QScrollBar:horizontal { height: 0; }
        """)
        layout.addWidget(self.list_widget, 1)

        # -- Button bar ----------------------------------------
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(12, 8, 12, 4)
        btn_bar.setSpacing(4)

        self.add_btn = QPushButton(self.tr("+ Add"))
        self.add_btn.setObjectName("ConfigButton")
        self.add_sep_btn = QPushButton(self.tr("Add Separator"))
        self.add_sep_btn.setObjectName("ConfigButton")
        self.remove_btn = QPushButton(self.tr("Remove"))
        self.remove_btn.setObjectName("ConfigButton")

        btn_bar.addWidget(self.add_btn)
        btn_bar.addWidget(self.add_sep_btn)
        btn_bar.addWidget(self.remove_btn)
        btn_bar.addSpacing(8)

        # Move buttons for keyboard/precision reorder
        self.move_up_btn = QPushButton("↑")
        self.move_up_btn.setObjectName("ConfigButton")
        self.move_up_btn.setFixedWidth(32)
        self.move_up_btn.setToolTip(self.tr("Move up"))
        self.move_down_btn = QPushButton("↓")
        self.move_down_btn.setObjectName("ConfigButton")
        self.move_down_btn.setFixedWidth(32)
        self.move_down_btn.setToolTip(self.tr("Move down"))
        btn_bar.addWidget(self.move_up_btn)
        btn_bar.addWidget(self.move_down_btn)

        layout.addLayout(btn_bar)

        # -- Bottom bar: Reset + OK/Cancel ----------------------
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(12, 4, 12, 8)
        bottom_bar.setSpacing(6)

        self.reset_btn = QPushButton(self.tr("Reset to Default"))
        self.reset_btn.setObjectName("ConfigButton")

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )

        bottom_bar.addWidget(self.reset_btn)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.button_box)

        layout.addLayout(bottom_bar)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self._on_add)
        self.add_sep_btn.clicked.connect(self._on_add_separator)
        self.remove_btn.clicked.connect(self._on_remove)
        self.move_up_btn.clicked.connect(self._on_move_up)
        self.move_down_btn.clicked.connect(self._on_move_down)
        self.reset_btn.clicked.connect(self._on_reset)
        self.button_box.accepted.connect(self._on_ok)
        self.button_box.rejected.connect(self.reject)

    # - Population --------------------------------------------

    def _populate_from_config(self):
        self.list_widget.clear()
        order = pcfg.context_menu_order
        if not order:
            order = DEFAULT_ORDER
        for cmd_id in order:
            self._add_list_item(cmd_id)

    def _add_list_item(self, cmd_id: str):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, cmd_id)
        if cmd_id == SEPARATOR_SENTINEL:
            item.setText("")
            item.setSizeHint(QSize(0, 12))
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, sep)
        else:
            cmd = COMMAND_REGISTRY.get(cmd_id)
            if cmd is None:
                return
            # Drag-handle visual cue (⠿) lets users know items are
            # reorderable before they try to drag.
            item.setText("⠿ " + self.tr(cmd.label_key))
            fm = self.list_widget.fontMetrics()
            item.setSizeHint(QSize(0, fm.height() + 12))
            self.list_widget.addItem(item)

    # - Slots -------------------------------------------------

    def _on_add(self):
        """Popup menu of available commands to add."""
        current_ids = {
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
        }

        menu = QMenu(self)
        available: List[tuple] = []
        for cmd_id, cmd in COMMAND_REGISTRY.items():
            if cmd_id not in current_ids and not cmd.hidden_in_customize:
                available.append((cmd_id, cmd.label_key))
        available.sort(key=lambda x: self.tr(x[1]).lower())

        if not available:
            act = menu.addAction(self.tr("(all commands added)"))
            act.setEnabled(False)

        for cmd_id, label_key in available:
            act = menu.addAction(self.tr(label_key))
            act.setData(cmd_id)

        chosen = menu.exec(
            self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft())
        )
        if chosen:
            self._add_list_item(chosen.data())

    def _on_add_separator(self):
        """Insert a separator after the current selection, or at the end."""
        row = self.list_widget.currentRow()
        if row < 0:
            row = self.list_widget.count()  # append
        else:
            row += 1  # after selection
        item = QListWidgetItem()
        item.setText("")
        item.setData(Qt.ItemDataRole.UserRole, SEPARATOR_SENTINEL)
        item.setSizeHint(QSize(0, 12))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        self.list_widget.insertItem(row, item)
        self.list_widget.setItemWidget(item, sep)

    def _on_remove(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)

    def _on_move_up(self):
        row = self.list_widget.currentRow()
        if row > 0:
            item = self.list_widget.item(row)
            widget = self.list_widget.itemWidget(item)
            if widget:
                widget.setParent(None)  # detach so takeItem won't delete it
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row - 1, item)
            if widget:
                widget.setParent(self.list_widget)
                self.list_widget.setItemWidget(item, widget)
            self.list_widget.setCurrentRow(row - 1)

    def _on_move_down(self):
        row = self.list_widget.currentRow()
        count = self.list_widget.count()
        if 0 <= row < count - 1:
            item = self.list_widget.item(row)
            widget = self.list_widget.itemWidget(item)
            if widget:
                widget.setParent(None)
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + 1, item)
            if widget:
                widget.setParent(self.list_widget)
                self.list_widget.setItemWidget(item, widget)
            self.list_widget.setCurrentRow(row + 1)

    def _on_reset(self):
        reply = QMessageBox.question(
            self,
            self.tr("Reset"),
            self.tr("Reset context menu to default layout?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.list_widget.clear()
            for cmd_id in DEFAULT_ORDER:
                self._add_list_item(cmd_id)

    def _on_ok(self):
        """Save order to config and close."""
        order: List[str] = []
        for i in range(self.list_widget.count()):
            cmd_id = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            order.append(cmd_id)
        pcfg.context_menu_order = order
        save_config()
        self.accept()
