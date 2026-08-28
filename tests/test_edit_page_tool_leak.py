"""Regression test: repair tools must not leak onto the text-edit page.

``_apply_canvas_mode`` used to re-arm the persisted tool's painting mode
unconditionally — ``hideEvent`` / ``set_config`` / ``handle_page_changed``
all land there — so with the AI brush active a left-drag on the edit page
painted repair strokes instead of box-selecting text blocks.  Only the
crop frame was visibility-gated; the mode itself stayed armed (2026-08-27).

While the DrawingPanel is hidden the canvas must park at ``NONE``; showing
the panel re-checks the current tool via ``showEvent`` and re-applies the
real mode.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_edit_page_tool_leak.py
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

from qtpy.QtWidgets import QApplication

from ui.canvas import Canvas
from ui.drawingpanel import DrawingPanel
from ui.image_edit import ImageEditMode
from ui.module_parse_widgets import InpaintConfigPanel


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


_APP = qapp()


class EditPageToolLeakTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canvas = Canvas()
        cls.canvas.setSceneRect(0, 0, 300, 300)
        cls.canvas.baseLayer.setRect(0, 0, 300, 300)
        cls.panel = DrawingPanel(cls.canvas, InpaintConfigPanel(""))

    def setUp(self) -> None:
        self.panel.show()
        _APP.processEvents()
        self.panel.on_use_aitool()
        _APP.processEvents()
        # Simulate the text-edit page: the panel (stack index 0) is hidden.
        self.panel.hide()
        _APP.processEvents()

    def test_hidden_panel_parks_canvas_mode_at_none(self) -> None:
        for activate in (
            self.panel.on_use_aitool,
            self.panel.on_use_inpainttool,
            self.panel.on_use_recttool,
        ):
            with self.subTest(activate=activate.__name__):
                activate()
                _APP.processEvents()
                self.assertEqual(
                    self.canvas.image_edit_mode,
                    ImageEditMode.NONE,
                    "hidden panel must not arm any canvas edit mode",
                )

    def test_show_reapplies_tool_mode(self) -> None:
        self.panel.show()
        _APP.processEvents()
        self.assertEqual(
            self.canvas.image_edit_mode,
            self.panel._tool_natural_mode(),
            "showEvent re-check must restore the current tool's mode",
        )

    def test_hide_clears_armed_mode(self) -> None:
        self.panel.show()
        _APP.processEvents()
        self.panel.on_use_aitool()
        _APP.processEvents()
        self.assertEqual(
            self.canvas.image_edit_mode, self.panel._tool_natural_mode()
        )
        self.panel.hide()
        _APP.processEvents()
        self.assertEqual(self.canvas.image_edit_mode, ImageEditMode.NONE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
