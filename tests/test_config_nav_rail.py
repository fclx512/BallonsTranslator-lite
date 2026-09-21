"""Offscreen tests for the card-style settings navigation rail.

The nav was a ``QTreeView`` (``ConfigTable``) until 2026-09-20; it is now a
column of group cards holding checkable item chips (``ConfigNavRail`` in
``ui/configpanel.py``).  The rail deliberately keeps the old wiring API —
``section_pressed`` / ``addHeader`` / ``addSection`` / ``section_items`` /
``setCurrentSection`` — so page switching, ``_nav_select`` and the ``focusOn*``
entries did not change.  These tests pin that contract.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_config_nav_rail.py
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

from qtpy.QtWidgets import QApplication  # noqa: E402


class ConfigNavRailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.configpanel import ConfigPanel

        cls.app = QApplication.instance() or QApplication([])
        cls.panel = ConfigPanel()

    def test_rail_replaces_the_tree(self):
        # 树控件整体退役：属性改名 + 旧类不再存在
        self.assertFalse(hasattr(self.panel, "configTable"))
        self.assertTrue(hasattr(self.panel, "configNav"))

    def test_two_group_cards_with_all_items(self):
        from ui.configpanel import ConfigNavGroup, ConfigNavItem

        nav = self.panel.configNav
        self.assertEqual(
            set(nav.section_items), set(self.panel._nav_section_to_widget)
        )
        self.assertEqual(len(nav.section_items), 10)

        groups = [
            w
            for w in nav.findChildren(ConfigNavGroup)
        ]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].title_label.text(), "Modules")
        self.assertEqual(groups[1].title_label.text(), "General")
        self.assertEqual(len(groups[0].findChildren(ConfigNavItem)), 3)
        self.assertEqual(len(groups[1].findChildren(ConfigNavItem)), 7)

    def test_click_switches_page(self):
        nav = self.panel.configNav
        received = []
        nav.section_pressed.connect(received.append)

        nav.section_items["typesetting"].click()

        self.assertEqual(received, ["typesetting"])
        expected = self.panel._page_index[
            id(self.panel.typesetting_block.section_widget)
        ]
        self.assertEqual(self.panel.pageStack.currentIndex(), expected)

    def test_exactly_one_item_stays_checked(self):
        nav = self.panel.configNav
        nav.setCurrentSection("quick_menus")
        checked = [k for k, item in nav.section_items.items() if item.isChecked()]
        self.assertEqual(checked, ["quick_menus"])

    def test_setcurrent_is_idempotent(self):
        """已是当前项时重复设置不发信号（与旧 setCurrentIndex 的守卫一致）。"""
        nav = self.panel.configNav
        nav.setCurrentSection("shortcuts")
        emitted = []
        nav.section_pressed.connect(emitted.append)
        nav.setCurrentSection("shortcuts")
        self.assertEqual(emitted, [])

    def test_clicking_current_item_re_emits(self):
        """点已选中的项仍重发——旧树靠 mousePressEvent 做到，底部栏齿轮依赖它。"""
        nav = self.panel.configNav
        nav.setCurrentSection("interface")
        emitted = []
        nav.section_pressed.connect(emitted.append)

        nav.section_items["interface"].click()

        self.assertEqual(emitted, ["interface"])

    def test_focus_entries_still_switch_stage(self):
        self.panel.focusOnOCR()
        self.assertTrue(self.panel.configNav.section_items["pipeline"].isChecked())
        self.assertEqual(
            self.panel.pipeline_tab_bar.currentIndex(),
            self.panel.PIPELINE_STAGE_OCR,
        )
        self.panel.focusOnInpaint()
        self.assertEqual(
            self.panel.pipeline_tab_bar.currentIndex(),
            self.panel.PIPELINE_STAGE_INPAINT,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
