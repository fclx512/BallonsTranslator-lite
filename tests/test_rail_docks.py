"""Offscreen regression tests for the PS-style rail dock launchers.

Covers the four format-area rail launchers (annotation / emphasis /
transform / text style): icon installation order, lazy dock creation on
toggle, open-state persistence through ``pcfg``, and the corner-dot
indicators that light while the current block holds matching content.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_rail_docks.py
"""

import os
import os.path as osp
import sys
import unittest

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
        panel.transform_launcher = panel.transform_dock = None
        panel.textstyle_launcher = panel.textstyle_dock = None
        panel.install_annotation_launcher(rail)
        panel.install_emphasis_launcher(rail)
        panel.install_transform_launcher(rail)
        panel.install_textstyle_launcher(rail)
        # Bare content stand-ins; the real groups are covered by their
        # own unit tests (annotation/emphasis/text style sets).  The
        # transform dock takes the panel *itself* as content (not a
        # view_widget), so a plain QWidget stand-in is enough.
        panel.emphasis_group = QFrame()
        panel.textstyle_group = QFrame()
        panel.texttransform_panel = QFrame()
        harness.panel = panel
        harness.rail = rail
        return harness

    def test_install_adds_four_launchers_in_order(self):
        harness = self._panel_and_rail()
        panel, rail = harness.panel, harness.rail
        layout = rail.layout()
        # last item is the stretch
        count = layout.count() - 1
        self.assertEqual(count, 4)
        self.assertIs(layout.itemAt(0).widget(), panel.annotation_launcher)
        self.assertIs(layout.itemAt(1).widget(), panel.emphasis_launcher)
        self.assertIs(layout.itemAt(2).widget(), panel.transform_launcher)
        self.assertIs(layout.itemAt(3).widget(), panel.textstyle_launcher)

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
        self.FontFormatPanel._on_transform_launcher_toggled(panel, True)
        # the previously-open emphasis dock closed and its launcher unchecked
        self.assertTrue(panel.emphasis_dock.isHidden())
        self.assertFalse(panel.transform_dock.isHidden())
        self.assertFalse(panel.emphasis_launcher.isChecked())
        # and reopening emphasis closes the transform dock again
        self.FontFormatPanel._on_emphasis_launcher_toggled(panel, True)
        self.assertTrue(panel.transform_dock.isHidden())
        self.assertFalse(panel.emphasis_dock.isHidden())

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

    def test_textstyle_dot_follows_block(self):
        scene = QGraphicsScene()
        harness = self._panel_and_rail()
        panel = harness.panel
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        scene.addItem(item)
        panel.textblk_item = item
        panel._update_textstyle_indicator()
        self.assertFalse(panel.textstyle_launcher._dot)
        item.blk.fontformat.shadow_radius = 0.25
        panel._update_textstyle_indicator()
        self.assertTrue(panel.textstyle_launcher._dot)
        item.blk.fontformat.shadow_radius = 0.0
        item.blk.fontformat.gradient_enabled = True
        panel._update_textstyle_indicator()
        self.assertTrue(panel.textstyle_launcher._dot)


class TextStyleGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_group(self):
        from ui.text_style_dock import TextStyleGroup

        return TextStyleGroup()

    def test_preview_and_commit_are_split(self):
        group = self._make_group()
        previews, commits = [], []
        group.preview_changed.connect(lambda n, v: previews.append((n, v)))
        group.commit_changed.connect(lambda n, v: commits.append((n, v)))
        # slider drag ticks → preview only on valueChanged
        group.strength_slider.setValue(40)
        self.assertEqual(previews[-1], ("shadow_strength", 0.4))
        self.assertEqual(commits, [])
        # release → one commit
        group.strength_slider.sliderReleased.emit()
        self.assertEqual(commits, [("shadow_strength", 0.4)])

    def test_set_from_format_restores_without_emitting(self):
        from utils.fontformat import FontFormat

        group = self._make_group()
        emissions = []
        group.preview_changed.connect(lambda *v: emissions.append(v))
        group.commit_changed.connect(lambda *v: emissions.append(v))
        fmt = FontFormat()
        fmt.shadow_radius = 0.5
        fmt.shadow_strength = 0.75
        fmt.shadow_color = [20, 30, 40]
        fmt.gradient_enabled = True
        fmt.gradient_angle = 30.0
        fmt.gradient_start_color = [1, 2, 3]
        fmt.gradient_size = 1.5
        group.set_from_format(fmt)
        self.assertEqual(emissions, [])
        self.assertEqual(group.radius_slider.value(), 50)
        self.assertEqual(group.strength_slider.value(), 75)
        self.assertEqual(group.shadow_color_btn.color(), [20, 30, 40])
        self.assertTrue(group.gradient_enable_cb.isChecked())
        self.assertEqual(round(group.gradient_dial.angle()), 30)
        self.assertEqual(group.scale_slider.value(), 150)

    def test_include_stroke_projects(self):
        group = self._make_group()
        toggles = []
        group.shadow_include_stroke_changed.connect(toggles.append)
        group.include_stroke_cb.setChecked(True)
        self.assertEqual(toggles, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)