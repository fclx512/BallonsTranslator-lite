"""Regression test: repair-tool cursors live on canvas.baseLayer (upstream
9ea9795 semantics).  A partial port left setInpaintCursor writing
to the QGraphicsView cursor, which child items override and mode switches
destroy — the fixed path sets baseLayer and guards on visibility + tool.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.config import load_config

load_config()

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication

from ui.canvas import Canvas
from ui.drawingpanel import DrawingPanel
from ui.module_parse_widgets import InpaintConfigPanel


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


_APP = qapp()


class DrawingCursorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canvas = Canvas()
        cls.canvas.setSceneRect(0, 0, 300, 300)
        cls.canvas.baseLayer.setRect(0, 0, 300, 300)
        cls.panel = DrawingPanel(
            cls.canvas, InpaintConfigPanel("")
        )
        cls.panel.show()
        _APP.processEvents()

    def setUp(self) -> None:
        self.canvas.clearToolStates()
        self.canvas.clear_states()
        self.panel.hide()
        _APP.processEvents()

    def _base_cursor(self):
        return self.canvas.baseLayer.cursor()

    def test_inpaint_cursor_lands_on_base_layer(self) -> None:
        for use, tool in (
            (self.panel.on_use_inpainttool, self.panel.inpaintTool),
        ):
            with self.subTest(tool=tool.objectName()):
                self.panel.show()
                _APP.processEvents()
                use()
                _APP.processEvents()
                self.assertIs(self.panel.currentTool, tool)
                self.assertTrue(
                    self.canvas.baseLayer.hasCursor(),
                    "tool cursor must live on baseLayer, not the view",
                )
                cur = self._base_cursor()
                self.assertEqual(cur.shape(), Qt.CursorShape.BitmapCursor)
                self.assertFalse(cur.pixmap().isNull())

    def test_guards_do_not_touch_cursor(self) -> None:
        self.panel.show()
        _APP.processEvents()
        self.panel.on_use_inpainttool()
        _APP.processEvents()
        before = self._base_cursor().pixmap().cacheKey()

        # Wrong tool: nothing may change.
        self.panel.currentTool = self.panel.handTool
        self.panel.setInpaintCursor()
        self.assertEqual(
            before, self._base_cursor().pixmap().cacheKey(),
            "setInpaintCursor must no-op while hand tool is current",
        )

        # Hidden panel: nothing may be set.
        self.panel.hide()
        _APP.processEvents()
        self.canvas.clear_canvas_cursor()
        self.panel.setInpaintCursor()
        self.assertFalse(
            self.canvas.baseLayer.hasCursor(),
            "hidden panel must not install a baseLayer cursor",
        )

    def test_hand_tool_clears_base_layer_cursor(self) -> None:
        self.panel.show()
        _APP.processEvents()
        self.panel.on_use_inpainttool()
        _APP.processEvents()
        self.assertTrue(self.canvas.baseLayer.hasCursor())
        self.panel.on_use_handtool()
        _APP.processEvents()
        self.assertFalse(self.canvas.baseLayer.hasCursor())


if __name__ == "__main__":
    unittest.main(verbosity=2)