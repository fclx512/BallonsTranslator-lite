"""Offscreen regression tests for the PS-style rail dock launchers.

Covers the format-area rail launchers (annotation / emphasis /
appearance): icon installation order, lazy dock creation on
toggle, open-state persistence through ``pcfg``, and the corner-dot
indicators that light while the current block holds matching content.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_rail_docks.py
"""

import os
import os.path as osp
import sys
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGraphicsScene,
    QWidget,
)

from utils.textblock import TextBlock  # noqa: E402
from qtpy.QtCore import Signal  # noqa: E402


class _TransformContent(QFrame):
    content_geometry_changed = Signal()


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class RailDockLauncherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.panel_rail import PanelRail
        from ui.text_panel import FontFormatPanel
        from ui.textitem import TextBlkItem

        cls.app = QApplication.instance() or QApplication([])
        cls.PanelRail = PanelRail
        cls.FontFormatPanel = FontFormatPanel
        cls.TextBlkItem = TextBlkItem
        cls.scene = QGraphicsScene()

    def _panel_and_rail(self):
        # Keep the host alive: destroying the top-level widget destroys the
        # rail + launcher C++ objects (PyQt ownership), which would leave
        # the tests holding dangling wrappers.
        class Harness:
            pass

        harness = Harness()
        harness.host = QWidget()
        rail = self.PanelRail(harness.host)
        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        panel.annotation_launcher = panel.annotation_dock = None
        panel.emphasis_launcher = panel.emphasis_dock = None
        panel.appearance_launcher = panel.appearance_dock = None
        panel.glossary_launcher = panel.glossary_dock = None
        # history dock（一期）也在 _iter_docks 清单里，未安装时同为 None
        panel.history_launcher = panel.history_dock = None
        panel.symbol_dock = None
        panel.install_annotation_launcher(rail)
        panel.install_emphasis_launcher(rail)
        panel.install_appearance_launcher(rail)
        # Bare content stand-ins; the real groups are covered by their
        # own unit tests (annotation/emphasis sets).  The transform dock
        # takes the panel *itself* as content (not a view_widget), so a
        # plain QWidget stand-in is enough.
        panel.emphasis_group = QFrame()
        panel.effects_panel = QFrame()
        panel.texttransform_panel = _TransformContent()
        panel.appearance_panel = QFrame()
        panel.appearance_effects_button = None
        panel.textblk_item = None
        panel._active_multi_items = None
        panel.effects_editor = SimpleNamespace(resolve_for_save=Mock())
        panel.text_transform_editor = SimpleNamespace(finish_canvas_edit=Mock())
        harness.panel = panel
        harness.rail = rail
        return harness

    def test_install_adds_three_launchers_in_order(self):
        harness = self._panel_and_rail()
        panel, rail = harness.panel, harness.rail
        layout = rail.layout()
        # last item is the stretch
        count = layout.count() - 1
        self.assertEqual(count, 3)
        self.assertIs(layout.itemAt(0).widget(), panel.annotation_launcher)
        self.assertIs(layout.itemAt(1).widget(), panel.emphasis_launcher)
        self.assertIs(layout.itemAt(2).widget(), panel.appearance_launcher)

    def test_toggle_creates_dock_lazily_and_persists_state(self):
        from utils.config import pcfg

        harness = self._panel_and_rail()
        panel = harness.panel
        self.assertIsNone(panel.emphasis_dock)
        # dispatch through the real toggle handler (a __new__ harness cannot
        # run Qt's C++ signal→bound-slot path; in the app the launcher's
        # toggled is wired to the same handler like the annotation launcher).
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, True)
        dock = panel.emphasis_dock
        self.assertIsNotNone(dock)
        self.assertFalse(dock.isHidden())
        self.assertTrue(pcfg.emphasis_dock_open)
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, False)
        self.assertTrue(dock.isHidden())
        self.assertFalse(pcfg.emphasis_dock_open)

    def test_dock_close_unchecks_launcher(self):
        harness = self._panel_and_rail()
        panel = harness.panel
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, True)
        panel.emphasis_dock.close_panel()
        # the dock's closed signal is wired to this handler in production
        self.FontFormatPanel._on_emphasis_dock_closed(panel)
        self.assertFalse(panel.emphasis_launcher.isChecked())

    def test_opening_one_dock_closes_other(self):
        """Mutual exclusion: only one rail dock shows at a time."""
        harness = self._panel_and_rail()
        panel = harness.panel
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, True)
        self.assertFalse(panel.emphasis_dock.isHidden())
        self.FontFormatPanel._on_appearance_launcher_toggled(panel, True)
        self.assertTrue(panel.emphasis_dock.isHidden())
        self.assertFalse(panel.appearance_dock.isHidden())
        self.assertFalse(panel.emphasis_launcher.isChecked())
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, True)
        self.assertTrue(panel.appearance_dock.isHidden())
        self.assertFalse(panel.emphasis_dock.isHidden())
        # The __new__ harness cannot dispatch C++ signals to its bound slots.
        self.FontFormatPanel._on_appearance_dock_closed(panel)
        panel.text_transform_editor.finish_canvas_edit.assert_called_once()

    def test_appearance_dock_keeps_height_when_cards_are_added(self):
        harness = self._panel_and_rail()
        harness.host.resize(1000, 800)
        harness.host.show()
        panel = harness.panel
        panel._on_appearance_launcher_toggled(True)
        old_height = panel.appearance_dock.height()
        panel.texttransform_panel.setMinimumHeight(300)
        self.app.processEvents()
        self.assertEqual(panel.appearance_dock.height(), old_height)
        harness.host.close()

    def test_emphasis_dot_follows_block(self):
        scene = QGraphicsScene()
        harness = self._panel_and_rail()
        panel = harness.panel
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        scene.addItem(item)
        panel.textblk_item = item
        panel._update_emphasis_indicator()
        self.assertFalse(panel.emphasis_launcher._dot)
        item.setEmphasis("filled dot", "over right")
        panel._update_emphasis_indicator()
        self.assertTrue(panel.emphasis_launcher._dot)
        item.setEmphasis("none", "over right")
        panel._update_emphasis_indicator()
        self.assertFalse(panel.emphasis_launcher._dot)

    def test_appearance_summary_uses_multi_selection_instead_of_default(self):
        harness = self._panel_and_rail()
        panel = harness.panel
        def fmt(effect_count, transform_count):
            return SimpleNamespace(text_effects=SimpleNamespace(effects=[None] * effect_count),
                                   text_transform=[None] * transform_count, glyph_slant_angle=0)
        panel.global_format = fmt(0, 0)
        panel._active_multi_items = [SimpleNamespace(blk=SimpleNamespace(fontformat=fmt(1, 0))),
                                     SimpleNamespace(blk=SimpleNamespace(fontformat=fmt(3, 2)))]
        panel.appearance_effects_button = Mock()
        panel.appearance_transforms_button = Mock()
        panel.appearance_panel = Mock()
        panel.appearance_dock = Mock()
        panel._update_appearance_indicator()
        panel.appearance_panel.set_counts.assert_called_once_with('1–3', '0–2')
        self.assertTrue(panel.appearance_launcher._dot)
        self.assertIn('2', panel.appearance_dock.set_title.call_args.args[0])

    def test_hiding_text_panel_finishes_appearance_edits(self):
        harness = self._panel_and_rail()
        harness.panel.on_textpanel_visibility(False)
        harness.panel.effects_editor.resolve_for_save.assert_called_once()
        harness.panel.text_transform_editor.finish_canvas_edit.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
