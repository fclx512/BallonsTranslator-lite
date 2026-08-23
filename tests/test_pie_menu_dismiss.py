"""Offscreen regression tests for quick-menu (pie menu) dismissal.

Covers the two dismissal paths fixed 2026-08-18:

1. Click-outside — must close the menu in BOTH states (PIN and the
   spring-loaded HOLDING state; holding used to be skipped, stranding the
   menu above the canvas).
2. Focus loss — main-window deactivation (``_pie_cancel_if_inactive``) and
   app-wide deactivation (``_pie_on_app_state_changed``) must close the menu.

The tests drive the REAL ``MainWindow`` dismissal handlers bound to a
lightweight QMainWindow shim and a REAL ``PieMenu`` widget.  The menu window
itself is never shown: showing a frameless Tool window offscreen under pytest
hard-crashes this PyQt build, and the handlers under test only need the menu's
state machine and geometry, not a visible window.

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_pie_menu_dismiss.py -v
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Unconditional insert: under pytest, tests/ sits at sys.path[0]; the repo
# previously had a tests/ui fixture dir that shadowed the repo-root ui package
# (renamed to tests/offscreen_ui on 2026-08-23), keep the insert as belt-and-braces.
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import MethodType

from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
from qtpy.QtWidgets import QApplication, QMainWindow, QWidget

from ui.mainwindow import MainWindow
from ui.pie_menu import PieMenu


class _PieShim(QMainWindow):
    """QMainWindow binding the real MainWindow dismissal handlers."""

    def isActiveWindow(self):  # controllable by tests
        return self._active

    _active = True


class TestPieMenuDismiss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )

    def setUp(self):
        self.app.processEvents()
        self.shim = _PieShim()
        self.shim.pie_menu = PieMenu(canvas=None, mw=None, parent=None)
        # Bind the REAL dismissal handlers from MainWindow.
        for name in (
            "_pie_handle_click_outside",
            "_pie_maybe_cancel_on_activation",
            "_pie_cancel_if_inactive",
            "_pie_on_app_state_changed",
        ):
            setattr(self.shim, name, MethodType(getattr(MainWindow, name), self.shim))
        self.proxy = QWidget()  # click target standing in for the canvas viewport

    def tearDown(self):
        self.shim.pie_menu.cancel()
        self.app.processEvents()

    # ── helpers ────────────────────────────────────────────

    def _open_menu(self, state):
        """Drive the menu into *state* without showing the window.

        ``start_hold`` shows the frameless Tool window and starts the open
        animation; a shown menu left open hard-crashes offscreen under pytest
        on the next ``processEvents``.  The dismissal handlers under test only
        consume the state machine and geometry, so drive those directly —
        the start_hold/release_hold transitions themselves are covered by the
        pie_menu state-machine suite.
        """
        pm = self.shim.pie_menu
        pm.set_menu_config(None)  # normalize config + fix window size
        pm._anim_timer.stop()     # no animation timer ticking during events
        pm._open_anim = None
        pm.move(250, -190)        # ring menu centered at (500, 60)
        pm._state = "pin" if state == "pin" else "holding"
        self.assertTrue(pm.is_open())
        self.assertEqual(pm._state, state)

    def _press_at(self, x, y):
        """Build a left-button press event at global (x, y) — what the app
        event filter would hand to ``_pie_handle_click_outside``."""
        from qtpy.QtGui import QMouseEvent, QPointingDevice

        dev = QPointingDevice.primaryPointingDevice()
        return QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            dev,
        )

    def _press_event(self, widget):
        """Deliver a left-button press straight to a widget (the window's own
        mousePressEvent), no top-level show required."""
        QApplication.sendEvent(widget, self._press_at(100, 100))

    # ── click-outside decision logic ───────────────────────

    def test_pin_click_outside_closes(self):
        self._open_menu("pin")
        self.shim._pie_handle_click_outside(self.proxy, self._press_at(900, 700))
        self.assertFalse(self.shim.pie_menu.is_open())

    def test_holding_click_outside_closes(self):
        # Was broken: holding state skipped the outside-click check, so the
        # spring-loaded menu could only be closed by pressing the trigger again.
        self._open_menu("holding")
        self.shim._pie_handle_click_outside(self.proxy, self._press_at(900, 700))
        self.assertFalse(self.shim.pie_menu.is_open())

    def test_click_inside_keeps_pin_open(self):
        self._open_menu("pin")
        self.shim._pie_handle_click_outside(self.proxy, self._press_at(500, 60))
        self.assertTrue(self.shim.pie_menu.is_open())

    def test_click_inside_keeps_holding_open(self):
        self._open_menu("holding")
        self.shim._pie_handle_click_outside(self.proxy, self._press_at(500, 60))
        self.assertTrue(self.shim.pie_menu.is_open())

    # ── main-window deactivation ───────────────────────────

    def test_deactivation_cancels_when_inactive(self):
        self._open_menu("pin")
        self.shim._active = False
        self.shim._pie_maybe_cancel_on_activation()
        self.app.processEvents()  # deferred QTimer.singleShot(0, ...)
        self.assertFalse(self.shim.pie_menu.is_open())

    def test_deactivation_keeps_open_when_active(self):
        self._open_menu("pin")
        self.shim._active = True
        self.shim._pie_maybe_cancel_on_activation()
        self.app.processEvents()
        self.assertTrue(self.shim.pie_menu.is_open())

    # ── app-wide deactivation ──────────────────────────────

    def test_app_state_inactive_closes(self):
        self._open_menu("pin")
        self.shim._pie_on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(self.shim.pie_menu.is_open())

    def test_app_state_inactive_closes_holding(self):
        self._open_menu("holding")
        self.shim._pie_on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(self.shim.pie_menu.is_open())

    def test_app_state_active_keeps_open(self):
        self._open_menu("pin")
        self.shim._pie_on_app_state_changed(Qt.ApplicationState.ApplicationActive)
        self.assertTrue(self.shim.pie_menu.is_open())

    # ── menu-window press while HOLDING (spring-loaded) ────

    def test_holding_press_on_menu_cancels(self):
        # While holding, no mouse press on the menu itself is meaningful
        # (commands fire on key release) — a press on the transparent window
        # area / ring gap used to fall through to super() and do nothing,
        # stranding the menu.  Any press must cancel it.
        pm = self.shim.pie_menu
        pm.set_menu_config(None)
        pm._anim_timer.stop()
        pm._state = "holding"
        self._press_event(pm)
        self.assertEqual(pm._state, "hidden")
        self.assertFalse(pm.is_open())

    # ── cancel is idempotent (multi-signal focus loss) ─────

    def test_double_cancel_is_safe(self):
        self._open_menu("pin")
        self.shim._pie_on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.shim._pie_on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.shim._pie_maybe_cancel_on_activation()
        self.app.processEvents()
        self.assertFalse(self.shim.pie_menu.is_open())


if __name__ == "__main__":
    unittest.main(verbosity=2)