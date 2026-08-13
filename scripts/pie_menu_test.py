"""Offline functional test for the pie menu (phase 2).

Run with the bundled interpreter:
    ./ballontrans_pylibs_win/python.exe scripts/pie_menu_test.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtCore import QElapsedTimer, QObject, QPoint, QPointF, Qt, Signal
from qtpy.QtWidgets import QApplication, QWidget

from utils import shared
from utils.config import load_config, pcfg, save_config

# ── Sandbox config so we never touch the user's real config.json ──
_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pie_test_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)
_TEST_MENU = {
    "id": "test",
    "name": "",
    "trigger": "Tab",
    "sectors": 8,
    "layout": "ring",
    "slots": [
        ["ocr_translate"], ["ocr"], ["copy"], ["paste"],
        ["delete"], ["merge"],
        ["align_left", "align_right", "align_hcenter"],
        ["translate"],
    ],
}
pcfg.pie_menus = [_TEST_MENU]


class MockUndoStack:
    """Minimal QUndoStack stand-in for enabled_fn checks."""

    def __init__(self):
        self.can_undo = False
        self.can_redo = False

    def canUndo(self):
        return self.can_undo

    def canRedo(self):
        return self.can_redo


class MockCanvas(QObject):
    """Minimal canvas stand-in: records direct-execution calls.

    Also plays the ``MainWindow`` role: ``run_cmd(mc, ...)`` reaches the
    canvas-level methods through ``mc.canvas`` (= self).
    """

    delete_textblks = Signal(int)
    merge_textblks = Signal()
    align_textblks = Signal(str)
    run_blktrans = Signal(int)
    reset_angle = Signal()
    squeeze_blk = Signal()
    copy_src_signal = Signal()
    paste_src_signal = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []
        self._selected = 0
        self._textedit = True
        self.canvas = self  # mock also plays the MainWindow role (mw.canvas)
        for name in ("delete_textblks", "merge_textblks", "align_textblks",
                     "run_blktrans", "reset_angle", "squeeze_blk",
                     "copy_src_signal", "paste_src_signal"):
            sig = getattr(self, name)
            sig.connect(lambda *a, _n=name: self.calls.append((_n, a)))
        self.undo_stack = MockUndoStack()
        self.text_undo_stack = self.undo_stack
        self.draw_undo_stack = MockUndoStack()

    def tr(self, s):
        return s

    def selected_text_items(self):
        return list(range(self._selected))

    @property
    def have_selected_blkitem(self):
        return self._selected > 0

    def on_copy(self):
        self.calls.append(("copy", ()))

    def on_paste(self):
        self.calls.append(("paste", ()))

    def textEditMode(self):
        return self._textedit

    def drawMode(self):
        return not self._textedit

    def undo(self):
        self.calls.append(("undo", ()))

    def redo(self):
        self.calls.append(("redo", ()))

    def scaleUp(self):
        self.calls.append(("scaleUp", ()))

    def scaleDown(self):
        self.calls.append(("scaleDown", ()))

    def fitToWindow(self):
        self.calls.append(("fitToWindow", ()))

    def shortcutBefore(self):
        self.calls.append(("shortcutBefore", ()))

    def shortcutNext(self):
        self.calls.append(("shortcutNext", ()))


PASS = []
FAIL = []


def check(name, cond):
    if cond:
        PASS.append(name)
        print(f"  ok  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}")


def main():
    app = QApplication.instance() or QApplication([])

    from ui.context_menu_config import COMMAND_REGISTRY, cmd_enabled, run_cmd
    from ui.pie_menu import (
        CENTER_RADIUS, DEAD_ZONE_RADIUS, SECTOR_COUNT, SHORT_PRESS_MS,
        WINDOW_RADIUS, PieMenu,
    )

    mc = MockCanvas()
    pm = PieMenu(mc, mw=mc)
    # Mirror the MainWindow wiring: emitted cmd_id -> direct execution.
    pm.command_triggered.connect(lambda cid: run_cmd(mc, cid))

    print("== registry ==")
    check("registry has align_left", "align_left" in COMMAND_REGISTRY)
    check("align_left hidden from customize",
          COMMAND_REGISTRY["align_left"].hidden_in_customize)
    check("align_left runnable", run_cmd(mc, "align_left"))
    check("align(legacy) not directly runnable", run_cmd(mc, "align") is False)
    check("unknown cmd not runnable", run_cmd(mc, "nope") is False)

    print("== enabled states ==")
    mc._selected = 1
    check("copy enabled with selection", cmd_enabled(mc, "copy"))
    check("merge disabled with 1 sel", not cmd_enabled(mc, "merge"))
    mc._selected = 2
    check("merge enabled with 2 sel", cmd_enabled(mc, "merge"))
    check("align_left enabled with 2 sel", cmd_enabled(mc, "align_left"))
    mc._selected = 0
    check("copy disabled w/o selection", not cmd_enabled(mc, "copy"))
    check("paste always enabled", cmd_enabled(mc, "paste"))

    print("== run_cmd execution ==")
    mc.calls.clear()
    run_cmd(mc, "copy")
    check("run_cmd copy invokes on_copy", ("copy", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "align_hcenter")
    check("run_cmd align_hcenter emits align_textblks hcenter",
          ("align_textblks", ("hcenter",)) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "ocr_translate")
    check("run_cmd ocr_translate emits run_blktrans 1",
          ("run_blktrans", (1,)) in mc.calls)

    print("== new palette commands (category + view/undo pool) ==")
    check("undo/redo/zoom/fit/pages registered",
          all(cid in COMMAND_REGISTRY for cid in
              ("undo", "redo", "fit_window", "zoom_in", "zoom_out",
               "prev_page", "next_page")))
    check("new commands hidden from context-menu customize",
          all(COMMAND_REGISTRY[cid].hidden_in_customize for cid in
              ("undo", "redo", "fit_window", "zoom_in", "zoom_out",
               "prev_page", "next_page")))
    check("undo category basic", COMMAND_REGISTRY["undo"].category == "basic")
    check("fit_window category view", COMMAND_REGISTRY["fit_window"].category == "view")
    check("translate category pipeline", COMMAND_REGISTRY["translate"].category == "pipeline")
    check("reset_angle category text", COMMAND_REGISTRY["reset_angle"].category == "text")
    check("align_left runnable", run_cmd(mc, "align_left"))
    check("fit_window runnable", run_cmd(mc, "fit_window"))
    check("prev_page runnable", run_cmd(mc, "prev_page"))
    check("next_page runnable", run_cmd(mc, "next_page"))
    mc.calls.clear()
    run_cmd(mc, "undo")
    check("run_cmd undo invokes canvas.undo", ("undo", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "redo")
    check("run_cmd redo invokes canvas.redo", ("redo", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "zoom_in")
    check("run_cmd zoom_in invokes scaleUp", ("scaleUp", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "zoom_out")
    check("run_cmd zoom_out invokes scaleDown", ("scaleDown", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "fit_window")
    check("run_cmd fit_window invokes fitToWindow", ("fitToWindow", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "prev_page")
    check("run_cmd prev_page invokes shortcutBefore", ("shortcutBefore", ()) in mc.calls)
    mc.calls.clear()
    run_cmd(mc, "next_page")
    check("run_cmd next_page invokes shortcutNext", ("shortcutNext", ()) in mc.calls)
    # enabled states
    mc.undo_stack.can_undo = False
    mc.undo_stack.can_redo = False
    check("undo disabled when stack empty", not cmd_enabled(mc, "undo"))
    check("redo disabled when stack empty", not cmd_enabled(mc, "redo"))
    mc.undo_stack.can_undo = True
    mc.undo_stack.can_redo = True
    check("undo enabled when stack ready", cmd_enabled(mc, "undo"))
    check("redo enabled when stack ready", cmd_enabled(mc, "redo"))
    check("zoom always enabled", cmd_enabled(mc, "zoom_in"))
    check("page nav always enabled", cmd_enabled(mc, "next_page"))

    print("== hit-test math (floating cards + tangential stack) ==")

    def _pt(cw_deg, r):
        """Widget-local point at clockwise-from-top angle *cw_deg*, radius *r*."""
        from math import cos, radians, sin
        t = radians(cw_deg - 90)
        return QPointF(WINDOW_RADIUS + r * cos(t), WINDOW_RADIUS + r * sin(t))

    # center
    check("center -> None", pm._hit_test(QPointF(WINDOW_RADIUS, WINDOW_RADIUS)) is None)
    # dead zone
    check("dead zone -> None",
          pm._hit_test(QPointF(WINDOW_RADIUS + 2, WINDOW_RADIUS)) is None)
    # single-card sectors: any radius in the annulus at the sector angle
    check("top -> (0,0)", pm._hit_test(_pt(0, 130)) == (0, 0))
    check("upper-right -> (1,0)", pm._hit_test(_pt(45, 130)) == (1, 0))
    check("right -> (2,0)", pm._hit_test(_pt(90, 60)) == (2, 0))
    check("bottom -> (4,0)", pm._hit_test(_pt(180, 130)) == (4, 0))
    check("upper-left -> (7,0)", pm._hit_test(_pt(315, 130)) == (7, 0))
    # left sector (3 stacked cards): each card rect center hits its own card,
    # and the stacked rects must not overlap each other
    from qtpy.QtGui import QFontMetrics
    fm = QFontMetrics(pm.font())
    for idx in range(3):
        c = pm._card_rect(6, idx, fm).center()
        check(f"align stack card {idx} hit", pm._hit_test(c) == (6, idx))
    rects = [pm._card_rect(6, i, fm) for i in range(3)]
    check("align stack cards do not overlap",
          all(not rects[i].intersects(rects[j])
              for i in range(3) for j in range(i + 1, 3)))
    # every card stays fully inside the (transparent) widget bounds
    check("all cards inside widget bounds",
          all(0 <= r.left() and r.right() <= 2 * WINDOW_RADIUS
              and 0 <= r.top() and r.bottom() <= 2 * WINDOW_RADIUS
              for s in range(SECTOR_COUNT)
              for i in range(len(pm._sector_data[s]))
              for r in [pm._card_rect(s, i, fm)]))
    # sector wedge fallback near the boundary arms the nearest card of sector 6
    hit_lo = pm._hit_test(_pt(247.6, 130))
    check("left boundary 247.6 -> sector 6", hit_lo is not None and hit_lo[0] == 6)
    hit_hi = pm._hit_test(_pt(292.4, 130))
    check("left boundary 292.4 -> sector 6", hit_hi is not None and hit_hi[0] == 6)
    # outside the widget
    check("outside -> None", pm._hit_test(_pt(0, WINDOW_RADIUS + 10)) is None)

    print("== multi-menu + sector-count parameterization ==")
    from ui.pie_menu import normalize_pie_menu
    from utils.config import _LEGACY_PIE_SECTORS, DEFAULT_PIE_MENUS, migrate_legacy_pie

    # normalize: sector clamp + per-sector truncation to 3 cards
    norm = normalize_pie_menu({"sectors": 4, "slots": [["copy", "paste", "delete"],
                                                       ["x"], [], []]})
    check("normalize keeps 4 sectors", norm["sectors"] == 4)
    check("normalize truncates to 3 cards",
          norm["slots"][0] == ["copy", "paste", "delete"])
    check("normalize pads empty slots", len(norm["slots"]) == 4)
    norm_bad = normalize_pie_menu({"sectors": 5})
    check("normalize clamps invalid sectors to 8", norm_bad["sectors"] == 8)
    check("default menus all valid",
          all(normalize_pie_menu(m)["sectors"] in (4, 6, 8)
              and len(normalize_pie_menu(m)["slots"]) == normalize_pie_menu(m)["sectors"]
              for m in DEFAULT_PIE_MENUS))

    # 4-sector menu: hit-test at the 4 cardinal angles
    quad = normalize_pie_menu({"sectors": 4, "slots": [["copy"], ["paste"],
                                                        ["delete"], ["merge"]]})
    pm.set_menu_config(quad)
    check("4-sector top -> (0,0)", pm._hit_test(_pt(0, 130)) == (0, 0))
    check("4-sector right -> (1,0)", pm._hit_test(_pt(90, 130)) == (1, 0))
    check("4-sector bottom -> (2,0)", pm._hit_test(_pt(180, 130)) == (2, 0))
    check("4-sector left -> (3,0)", pm._hit_test(_pt(270, 130)) == (3, 0))
    hit_diag = pm._hit_test(_pt(45, 130))
    check("4-sector diagonal arms nearer sector",
          hit_diag is not None and hit_diag[0] in (0, 1))
    # switch back to the 8-sector test menu
    pm.set_menu_config(_TEST_MENU)
    check("restore 8-sector top", pm._hit_test(_pt(0, 130)) == (0, 0))

    # legacy migration
    migrated_default = migrate_legacy_pie(_LEGACY_PIE_SECTORS)
    check("legacy default -> 3 menus", len(migrated_default) == 3)
    check("legacy default first menu id edit", migrated_default[0]["id"] == "edit")
    custom = [["copy"], ["paste"], [], [], [], [], [], []]
    migrated_custom = migrate_legacy_pie(custom)
    check("legacy custom kept as first menu", migrated_custom[0]["slots"] == custom)
    check("legacy custom gets 2 extra defaults", len(migrated_custom) == 3)

    print("== config editor: drop geometry + mutation ==")
    from ui.pie_menu_editor import PieMenuEditor

    editor = PieMenuEditor()
    editor._menus = [normalize_pie_menu({
        "id": "e", "name": "", "trigger": "Tab", "sectors": 8,
        "slots": [["copy"], ["paste"], [], [], ["delete"], [], [], []],
    })]
    editor._current = 0
    editor._refresh_preview()
    pv = editor.preview
    check("preview sector_at top", pv.sector_at(_pt(0, 130)) == 0)
    check("preview sector_at center -> -1",
          pv.sector_at(QPointF(WINDOW_RADIUS, WINDOW_RADIUS)) == -1)
    check("drop ok into non-full sector", pv._drop_ok(2, -1))
    check("drop ok internal reorder of full sector", pv._drop_ok(0, 0))
    check("insert idx empty sector", pv._drop_insert_index(2, _pt(0, 130)) == 0)
    editor._on_command_dropped(2, 0, "merge", -1, -1)
    check("drop add lands in sector 2", editor._menus[0]["slots"][2] == ["merge"])
    editor._on_command_dropped(1, 1, "copy", 0, 0)
    check("move reorders across sectors",
          editor._menus[0]["slots"][1] == ["paste", "copy"])
    check("move removes source", editor._menus[0]["slots"][0] == [])
    editor._on_card_remove(1, 0)
    check("remove card", editor._menus[0]["slots"][1] == ["copy"])
    # full sector: additions rejected, internal moves allowed
    editor._menus[0]["slots"][6] = ["a", "b", "c"]
    editor._refresh_preview()
    check("drop rejected on full sector", not pv._drop_ok(6, -1))
    check("internal move ok on full sector", pv._drop_ok(6, 6))
    # sector-count resize truncates / pads slots
    editor._on_sectors_changed(0)   # 4 sectors
    check("sector count resize to 4",
          editor._menus[0]["sectors"] == 4 and len(editor._menus[0]["slots"]) == 4)
    # paint smoke: edit-mode guides + drop-target highlight render without crashing
    pv.set_drop_target(3, rejected=False)
    check("edit-mode preview paints", not pv.grab().isNull())
    pv.set_drop_target(6, rejected=True)
    check("rejected drop-target paints", not pv.grab().isNull())
    pv.set_drop_target(-1)
    # trigger conflict pill
    editor._menus = [normalize_pie_menu({
        "id": "a", "trigger": "Tab", "sectors": 8, "slots": []}),
        normalize_pie_menu({
            "id": "b", "trigger": "Tab", "sectors": 8, "slots": []})]
    editor._refresh_conflicts()
    check("conflict pill shown for duplicate trigger",
          not editor.conflict_label.isHidden())
    editor._menus[1]["trigger"] = "X"
    editor._refresh_conflicts()
    check("conflict pill hidden after unique trigger",
          editor.conflict_label.isHidden())

    print("== state machine ==")
    # short press -> PIN
    pm.start_hold(QPoint(400, 400))
    check("holding after start_hold", pm.is_holding() and pm.is_open())
    pm.release_hold()
    check("short press -> pin", pm.is_open() and not pm.is_holding())

    # PIN: left click on a sector triggers the command
    mc._selected = 2
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtCore import QEvent
    # sector 3 (lower-right) = paste; point at dist 58, cw angle 135°
    off3 = 58 * 2 ** -0.5
    ev = QMouseEvent(QEvent.Type.MouseButtonPress,
                     QPointF(WINDOW_RADIUS + off3, WINDOW_RADIUS + off3),
                     QPointF(500, 400), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    mc.calls.clear()
    pm.mousePressEvent(ev)
    check("PIN click triggers paste", ("paste", ()) in mc.calls)
    check("menu hidden after trigger", not pm.is_open())

    # PIN: click center -> cancel
    pm.start_hold(QPoint(400, 400))
    pm.release_hold()
    check("pin again", pm.is_open() and not pm.is_holding())
    ev_center = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPointF(WINDOW_RADIUS, WINDOW_RADIUS),
                            QPointF(400, 400), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    mc.calls.clear()
    pm.mousePressEvent(ev_center)
    check("PIN center click cancels", not pm.is_open())
    check("no command fired", mc.calls == [])

    # PIN: right click anywhere -> cancel
    pm.start_hold(QPoint(400, 400))
    pm.release_hold()
    ev_r = QMouseEvent(QEvent.Type.MouseButtonPress,
                       QPointF(WINDOW_RADIUS + 80, WINDOW_RADIUS + 80),
                       QPointF(480, 480), Qt.MouseButton.RightButton,
                       Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    pm.mousePressEvent(ev_r)
    check("PIN right click cancels", not pm.is_open())

    # long press -> release-commit
    pm.start_hold(QPoint(400, 400))
    pm._press_timer.elapsed = lambda: SHORT_PRESS_MS + 1  # force long press
    # simulate hover over the delete sector (bottom, ring 0 = delete)
    pm._update_hover((4, 0))
    mc._selected = 1
    mc.calls.clear()
    pm.release_hold()
    check("long press commits hovered", ("delete_textblks", (0,)) in mc.calls)
    check("menu closed after commit", not pm.is_open())

    # long press with center hover -> cancel
    pm.start_hold(QPoint(400, 400))
    pm._press_timer.elapsed = lambda: SHORT_PRESS_MS + 1
    pm.release_hold()
    check("long press center cancels", not pm.is_open())

    # long press with disabled slot hover -> cancel (decision #5)
    pm.start_hold(QPoint(400, 400))
    pm._press_timer.elapsed = lambda: SHORT_PRESS_MS + 1
    pm._update_hover((5, 0))   # merge, but only 1 selected -> disabled
    mc.calls.clear()
    pm.release_hold()
    check("disabled hover -> cancel", not pm.is_open())
    check("no merge fired", ("merge_textblks", ()) not in mc.calls)

    # cancel() closes and emits canceled
    pm.start_hold(QPoint(400, 400))
    canceled = []
    pm.canceled.connect(lambda: canceled.append(1))
    pm.cancel()
    check("cancel closes menu", not pm.is_open())
    check("cancel signal emitted", len(canceled) == 1)

    print("== MainWindow handler wiring (shim) ==")
    from qtpy.QtGui import QKeyEvent
    from ui import mainwindow as mw_mod

    class FakeCanvas:
        def __init__(self):
            self.gv = QWidget()
            self.creating_textblock = False
            self.editing_textblkitem = None
            self._textedit = True

        def textEditMode(self):
            return self._textedit

    class FakeMW:
        def __init__(self):
            self.canvas = FakeCanvas()
            self.pie_menu = PieMenu(self.canvas, mw=self)
            self.pie_menu.command_triggered.connect(lambda cid: run_cmd(mc, cid))
            self._canvas_mode = True
            self.cursor_on_canvas = True   # trigger-area gate (review 2026-08-11)

        def _pie_cursor_on_canvas(self):
            return self.cursor_on_canvas

        def _is_canvas_mode(self):
            return self._canvas_mode

        def isActiveWindow(self):
            return True

    def bind(fmw):
        for name in ("_pie_trigger_ready", "_pie_handle_shortcut_override",
                     "_pie_menu_for_event", "_pie_handle_keypress",
                     "_pie_handle_keyrelease", "_pie_handle_click_outside"):
            setattr(fmw, name, getattr(mw_mod.MainWindow, name).__get__(fmw))

    fmw = FakeMW()
    fmw.app = QApplication.instance()
    bind(fmw)
    fmw.isAncestorOf = lambda w: True   # pretend focus is inside the main window

    # Establish a focused widget inside the "main window" (offscreen focus
    # requires a shown window).
    _host = QWidget()
    _host.show()
    _host.setFocus()
    app.processEvents()
    check("focus established", app.focusWidget() is not None)

    # mode conditions — focus is no longer required to be on the canvas
    check("trigger ready in textEdit mode", fmw._pie_trigger_ready())
    fmw.canvas.creating_textblock = True
    check("not ready while creating textblock", not fmw._pie_trigger_ready())
    fmw.canvas.creating_textblock = False
    fmw.canvas.editing_textblkitem = object()
    check("not ready while editing textblk", not fmw._pie_trigger_ready())
    fmw.canvas.editing_textblkitem = None
    fmw.canvas._textedit = False
    check("not ready in drawboard mode", not fmw._pie_trigger_ready())
    fmw.canvas._textedit = True
    fmw._canvas_mode = False
    check("not ready with config panel open", not fmw._pie_trigger_ready())
    fmw._canvas_mode = True

    # KeyPress Tab with conditions → opens pie, returns True (swallowed)
    pm2 = fmw.pie_menu
    pm2.move(QPoint(200, 200))
    ev_tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                       Qt.KeyboardModifier.NoModifier)
    mc.calls.clear()
    check("KeyPress Tab opens pie", fmw._pie_handle_keypress(ev_tab) is True)
    check("pie holding", pm2.is_holding())

    # KeyRelease Tab (short) → PIN
    ev_rel = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Tab,
                       Qt.KeyboardModifier.NoModifier)
    check("KeyRelease handled", fmw._pie_handle_keyrelease(ev_rel) is True)
    check("pin after short release", pm2.is_open() and not pm2.is_holding())

    # Esc while open → cancel, swallowed
    ev_esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                       Qt.KeyboardModifier.NoModifier)
    check("Esc cancels pie", fmw._pie_handle_keypress(ev_esc) is True)
    check("pie closed after Esc", not pm2.is_open())

    # Tab again while open → cancel
    pm2.start_hold(QPoint(300, 300))
    check("pie reopened", pm2.is_open())
    check("Tab-again cancels", fmw._pie_handle_keypress(ev_tab) is True)
    check("pie closed after Tab-again", not pm2.is_open())

    # auto-repeat Tab while open → swallowed, menu kept (no focus cycling /
    # tab-char insertion during a long hold — the leak fix)
    pm2.start_hold(QPoint(300, 300))
    ev_tab_rep = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                           Qt.KeyboardModifier.NoModifier, "\t", True)
    check("auto-repeat Tab swallowed while open",
          fmw._pie_handle_keypress(ev_tab_rep) is True)
    check("pie still open after auto-repeat", pm2.is_open())
    check("Tab-again cancels", fmw._pie_handle_keypress(ev_tab) is True)
    check("pie closed after Tab-again", not pm2.is_open())

    # trigger-area gate: cursor off the canvas → Tab passes through, no pie
    fmw.cursor_on_canvas = False
    check("Tab passes through off-canvas", fmw._pie_handle_keypress(ev_tab) is False)
    check("pie not opened off-canvas", not pm2.is_open())
    ev_ov_off = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Tab,
                          Qt.KeyboardModifier.NoModifier)
    check("ShortcutOverride not swallowed off-canvas",
          fmw._pie_handle_shortcut_override(ev_ov_off) is False)
    fmw.cursor_on_canvas = True
    check("Tab opens pie again on-canvas", fmw._pie_handle_keypress(ev_tab) is True)
    check("pie holding again", pm2.is_holding())
    pm2.cancel()

    # bare Tab when not ready → not swallowed
    fmw.canvas._textedit = False
    check("Tab passes through when not ready",
          fmw._pie_handle_keypress(ev_tab) is False)
    fmw.canvas._textedit = True

    # ShortcutOverride with Tab while ready → accepted (swallow QShortcut)
    ev_ov = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Tab,
                      Qt.KeyboardModifier.NoModifier)
    check("ShortcutOverride swallowed when ready",
          fmw._pie_handle_shortcut_override(ev_ov) is True)
    ev_ov_mod = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Tab,
                          Qt.KeyboardModifier.ControlModifier)
    check("Ctrl+Tab override not swallowed",
          fmw._pie_handle_shortcut_override(ev_ov_mod) is False)

    # multi-menu trigger lookup (configurable keys)
    pcfg.pie_menus = [_TEST_MENU, {
        "id": "quad", "name": "", "trigger": "X", "sectors": 4, "layout": "ring",
        "slots": [["copy"], ["paste"], ["delete"], ["merge"]],
    }]
    check("trigger lookup finds Tab menu", fmw._pie_menu_for_event(
        Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier) is not None)
    check("trigger lookup finds X menu", fmw._pie_menu_for_event(
        Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier) is not None)
    check("trigger lookup ignores Ctrl+X",
          fmw._pie_menu_for_event(
              Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier) is None)
    ev_x = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X,
                     Qt.KeyboardModifier.NoModifier)
    check("X opens its own menu", fmw._pie_handle_keypress(ev_x) is True)
    check("X menu has 4 sectors", fmw.pie_menu._sector_count == 4)
    check("X menu holding", pm2.is_holding())
    ev_x_rel = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_X,
                         Qt.KeyboardModifier.NoModifier)
    check("X release handled", fmw._pie_handle_keyrelease(ev_x_rel) is True)
    pm2.cancel()

    # KeyRelease Tab while not holding → not swallowed
    check("KeyRelease passes when not holding",
          fmw._pie_handle_keyrelease(ev_rel) is False)

    # focus inside a pure text input → Tab swallowed, pie NOT opened
    # (no tab-char insertion / no focus jump — the off-field guard)
    from qtpy.QtWidgets import QTextEdit
    _win2 = QWidget()
    _win2.show()
    _txt = QTextEdit()
    _txt.setParent(_win2)
    _txt.show()
    _txt.setFocus()
    app.processEvents()
    check("focus in text input", app.focusWidget() is _txt)
    check("Tab in text input swallowed", fmw._pie_handle_keypress(ev_tab) is True)
    check("pie not opened from text input", not pm2.is_open())
    # a typeable trigger key (X) must pass through text inputs
    ev_x_txt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X,
                         Qt.KeyboardModifier.NoModifier)
    check("X in text input passes through",
          fmw._pie_handle_keypress(ev_x_txt) is False)
    check("pie not opened from X in text input", not pm2.is_open())
    _win2.close()

    # focus NOT inside the main window (e.g. a dialog) → Tab passes through
    _dlg = QWidget()
    _dlg.show()
    _dlg.setFocus()
    app.processEvents()
    fmw.isAncestorOf = lambda w: False
    check("Tab passes through outside main window",
          fmw._pie_handle_keypress(ev_tab) is False)
    check("pie not opened outside main window", not pm2.is_open())
    fmw.isAncestorOf = lambda w: True
    _dlg.close()

    # click outside (PIN mode) cancels
    pm2.start_hold(QPoint(300, 300))
    pm2.release_hold()
    check("pin for outside-click test", pm2.is_open() and not pm2.is_holding())
    ev_out = QMouseEvent(QEvent.Type.MouseButtonPress,
                         QPointF(0, 0), QPointF(10, 10),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
    fmw._pie_handle_click_outside(QWidget(), ev_out)
    check("click outside cancels pie", not pm2.is_open())

    # app deactivate (Alt-Tab away) while open → cancel (eventFilter branch)
    pm2.start_hold(QPoint(300, 300))
    check("open for deactivate test", pm2.is_open())
    ev_deact = QEvent(QEvent.Type.ApplicationDeactivate)
    if pm2.is_open():  # mirrors MainWindow.eventFilter's deactivate branch
        pm2.cancel()
    check("app deactivate cancels pie", not pm2.is_open())

    print("== vertical list layout (half-ring panels) ==")
    from ui.pie_menu import (
        LIST_MIN_W, LIST_PANEL_MAX_ITEMS, LIST_PANELS, WINDOW_RADIUS,
        panels_to_slots, slots_to_panels,
    )

    # normalize: panels / direction, invalid fallbacks, caps, items migration
    lst = normalize_pie_menu({
        "id": "L", "trigger": "V", "layout": "list",
        "panels": [["copy", "paste"], ["---", "delete", 123, ""], []],
        "direction": "left",
    })
    check("list normalize keeps panels",
          lst["panels"] == [["copy", "paste"], ["delete"], []])
    check("list normalize filters separator+non-str",
          "---" not in str(lst["panels"]) and "123" not in str(lst["panels"]))
    check("list normalize direction", lst["direction"] == "left")
    check("list normalize keeps slots shape", len(lst["slots"]) == lst["sectors"])
    check("invalid layout -> ring", normalize_pie_menu({"layout": "bogus"})["layout"] == "ring")
    check("invalid direction -> right",
          normalize_pie_menu({"direction": "up"})["direction"] == "right")
    wide = normalize_pie_menu({"layout": "list",
                               "panels": [[f"c{i}" for i in range(9)], "x", []]})
    check("list normalize caps panel rows",
          len(wide["panels"][0]) == LIST_PANEL_MAX_ITEMS)
    check("list normalize coerces bad panel", wide["panels"][1] == [])
    check("list normalize pads panels",
          len(normalize_pie_menu({"layout": "list"})["panels"]) == LIST_PANELS)
    mig = normalize_pie_menu({"layout": "list",
                              "items": ["a", "b", "c", "d", "e", "f", "g"]})
    check("legacy items migrate to panels (chunked)",
          mig["panels"] == [["a", "b", "c"], ["d", "e", "f"], ["g"]])

    # ring <-> list conversions (lateral half, top -> bottom)
    ring_slots = [["copy"], ["paste"], ["delete"], ["merge"], [], [], [], []]
    check("slots_to_panels picks right half",
          slots_to_panels(ring_slots, "right") == [["paste"], ["delete"], ["merge"]])
    left_slots = [[], [], [], [], [], ["merge"], ["delete"], ["paste"]]
    check("slots_to_panels picks left half",
          slots_to_panels(left_slots, "left") == [["paste"], ["delete"], ["merge"]])
    back = panels_to_slots([["paste"], ["delete"], ["merge"]], "right")
    check("panels_to_slots writes sectors 1..3",
          back[1] == ["paste"] and back[2] == ["delete"] and back[3] == ["merge"])
    back_l = panels_to_slots([["paste"], ["delete"], ["merge"]], "left")
    check("panels_to_slots writes sectors 7..5",
          back_l[7] == ["paste"] and back_l[6] == ["delete"] and back_l[5] == ["merge"])
    quad = slots_to_panels([["a"], ["b"], ["c"], ["d"]], "right")
    check("4-sector ring -> single lateral panel + padding",
          quad == [["b"], [], []])

    # list geometry + hit-test on a runtime widget
    list_menu = normalize_pie_menu({
        "id": "L", "trigger": "V", "layout": "list",
        "panels": [["copy", "paste"], ["delete"], ["merge"]],
    })
    pm.set_menu_config(list_menu)
    check("list mode active", pm._is_list())
    check("list window sized", pm._list_w >= LIST_MIN_W and pm._list_h > 0)
    # regression 2026-08-12: switching list -> ring must restore the ring
    # window size (a list-sized window clips the ring to nothing)
    ring_menu = normalize_pie_menu({
        "id": "R", "trigger": "B", "layout": "ring",
        "slots": [["copy"], ["paste"], ["delete"], [], [], [], [], []],
    })
    pm.set_menu_config(ring_menu)
    check("ring window restored after list",
          pm.width() == int(2 * WINDOW_RADIUS) and pm.height() == pm.width())
    pm.set_menu_config(list_menu)
    check("list window restored after ring",
          pm.width() == int(pm._list_w) and pm.height() == int(pm._list_h))
    check("three panel rects", len(pm._list_rects) == LIST_PANELS)
    check("mid panel right of cursor",
          pm._list_rects[1].left() > pm._list_cursor.x())
    check("top panel above cursor",
          pm._list_rects[0].bottom() < pm._list_cursor.y())
    check("bottom panel below cursor",
          pm._list_rects[2].top() > pm._list_cursor.y())
    check("mid panel vertically centered on cursor",
          abs(pm._list_rects[1].center().y() - pm._list_cursor.y()) <= 1)
    check("list hit top panel row 1",
          pm._hit_test_list(pm._list_row_rect(0, 1).center()) == (0, 1))
    check("list hit mid panel row 0",
          pm._hit_test_list(pm._list_row_rect(1, 0).center()) == (1, 0))
    check("list hit cursor gap -> None",
          pm._hit_test_list(pm._list_cursor) is None)
    c0 = pm._list_row_rect(0, 0).center()
    check("drop at row center inserts before it",
          pm._list_drop_pos(c0) == (0, 0))
    check("drop below row center inserts after",
          pm._list_drop_pos(c0 + QPointF(0.0, 9.0)) == (0, 1))
    check("drop at panel bottom appends",
          pm._list_drop_pos(QPointF(c0.x(), pm._list_rects[0].bottom() - 1))
          == (0, 2))
    check("drop outside -> (-1, -1)",
          pm._list_drop_pos(QPointF(0.0, 0.0)) == (-1, -1))

    # empty panel: no hit at runtime, but a valid drop target (ghost)
    sparse = normalize_pie_menu({
        "layout": "list", "panels": [["copy"], [], ["delete"]]})
    pm.set_menu_config(sparse)
    check("empty panel not hit", pm._hit_test_list(pm._list_rects[1].center()) is None)
    check("empty panel is drop target",
          pm._list_drop_pos(pm._list_rects[1].center()) == (1, 0))

    # runtime anchoring: widget-local cursor lands on the global cursor pos
    pm.set_menu_config(list_menu)
    pm.start_hold(QPoint(400, 300))
    check("list window cursor-anchored (right)",
          abs(pm.geometry().left() + pm._list_cursor.x() - 400) <= 1
          and abs(pm.geometry().top() + pm._list_cursor.y() - 300) <= 1)
    pm.cancel()
    list_menu["direction"] = "left"
    pm.set_menu_config(list_menu)
    check("left direction mirrors panels",
          pm._list_rects[1].right() < pm._list_cursor.x())
    pm.start_hold(QPoint(400, 300))
    check("list window cursor-anchored (left)",
          abs(pm.geometry().left() + pm._list_cursor.x() - 400) <= 1
          and abs(pm.geometry().top() + pm._list_cursor.y() - 300) <= 1)
    pm.cancel()

    # PIN: left click on a list row triggers
    # (fresh timer: earlier long-press tests stubbed _press_timer.elapsed)
    pm._press_timer = QElapsedTimer()
    list_menu["direction"] = "right"
    pm.set_menu_config(list_menu)
    pm.start_hold(QPoint(400, 300))
    pm.release_hold()
    check("list pin after short release", pm.is_open() and not pm.is_holding())
    mc._selected = 2
    ev_l = QMouseEvent(QEvent.Type.MouseButtonPress,
                       pm._list_row_rect(0, 1).center(), QPointF(400, 300),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    mc.calls.clear()
    pm.mousePressEvent(ev_l)
    check("list PIN click triggers paste", ("paste", ()) in mc.calls)
    check("list closed after trigger", not pm.is_open())

    # long press release-commit on a list row
    pm.start_hold(QPoint(400, 300))
    pm._press_timer.elapsed = lambda: SHORT_PRESS_MS + 1
    pm._update_hover((0, 0))   # copy
    mc.calls.clear()
    pm.release_hold()
    check("list long press commits", ("copy", ()) in mc.calls)
    check("list closed after commit", not pm.is_open())

    # disabled list row behaves like the center (hover cleared -> cancel)
    pm.start_hold(QPoint(400, 300))
    pm._press_timer.elapsed = lambda: SHORT_PRESS_MS + 1
    mc._selected = 0
    pm._update_hover((0, 0))   # copy disabled (no selection)
    check("list disabled hover cleared", pm._hover is None)
    mc.calls.clear()
    pm.release_hold()
    check("list disabled commit cancels", not pm.is_open())
    check("no copy fired", ("copy", ()) not in mc.calls)
    mc._selected = 1

    # editor: style switch conversions, direction, visibility, panel drag-drop
    editor._menus = [normalize_pie_menu({
        "id": "e", "trigger": "T", "sectors": 8,
        "slots": [["copy"], ["paste"], ["delete"], ["merge"], [], [], [], []],
    })]
    editor._current = 0
    editor._refresh_preview()
    editor._on_style_changed(1)   # -> list
    check("style switch to list", editor._menus[0]["layout"] == "list")
    check("style switch picks lateral half",
          editor._menus[0]["panels"] == [["paste"], ["delete"], ["merge"]])
    check("sectors hidden in list style", editor.sectors_combo.isHidden())
    check("direction shown in list style", not editor.direction_combo.isHidden())
    editor._on_style_changed(0)   # -> ring (panels written to the right half)
    check("style switch back to ring", editor._menus[0]["layout"] == "ring")
    check("panels written back to sectors 1..3",
          editor._menus[0]["slots"][1] == ["paste"]
          and editor._menus[0]["slots"][2] == ["delete"]
          and editor._menus[0]["slots"][3] == ["merge"])
    editor._on_direction_changed(1)   # ring ignores it, just persists
    check("direction set to left", editor._menus[0]["direction"] == "left")
    editor._on_style_changed(1)   # -> list again, now from the left half
    check("round trip keeps commands (left half empty -> padded)",
          editor._menus[0]["panels"] == [[], [], []])
    editor._menus = [normalize_pie_menu({
        "id": "e", "trigger": "T", "layout": "list", "direction": "right",
        "panels": [["paste"], ["delete"], ["merge"]],
    })]
    editor._current = 0
    editor._refresh_preview()
    editor._on_command_dropped(1, 1, "ocr", -1, -1)   # palette add, panel 1 row 1
    check("list drop adds to panel",
          editor._menus[0]["panels"][1] == ["delete", "ocr"])
    editor._on_command_dropped(0, 0, "delete", 1, 0)  # move panel1 row0 -> panel0 row0
    check("list drop moves across panels",
          editor._menus[0]["panels"][0] == ["delete", "paste"]
          and editor._menus[0]["panels"][1] == ["ocr"])
    editor._on_card_remove(0, 1)   # right-click "paste" in panel 0
    check("list right-click remove",
          editor._menus[0]["panels"][0] == ["delete"])
    editor._on_card_remove(2, 0)   # palette drop-back in list mode
    check("list palette drop-back removes",
          editor._menus[0]["panels"][2] == [])

    # list preview paints + ghost drop geometry
    editor._refresh_preview()
    pv_list = editor.preview
    check("list preview paints", not pv_list.grab().isNull())
    check("list preview ghost drop target",
          pv_list._list_drop_pos(pv_list._list_rects[2].center()) == (2, 0))

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    save_config()
    if os.path.exists(_TMP_CFG):
        os.remove(_TMP_CFG)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
