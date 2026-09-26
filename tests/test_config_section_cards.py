"""Offscreen tests for the settings section cards.

Every in-page section is a card (``PanelGroupBox`` with the ``compact``
property) that physically holds its own rows — before 2026-09-20 they were a
bare bold label plus siblings in one flat page layout, which is why section
boundaries were hard to see.  Built by ``ui/configpanel.py::_section_body``.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_config_section_cards.py
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


class SectionCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.configpanel import ConfigPanel

        cls.app = QApplication.instance() or QApplication([])
        cls.panel = ConfigPanel()

    # ── helpers ───────────────────────────────────────────────────────

    def _cards(self, page_widget):
        """页面布局里按顺序排好的分节卡（compact PanelGroupBox）。

        模型管理页与工作台页的页面容器本身就是 ``PanelGroupBox``
        （cfgPage），卡片在它的内容布局里；其余页面是普通 QWidget。
        """
        from ui.custom_widget import PanelGroupBox

        if isinstance(page_widget, PanelGroupBox):
            layout = page_widget.contentLayout()
        else:
            layout = page_widget.layout()
        cards = []
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if isinstance(widget, PanelGroupBox) and widget.property("compact"):
                cards.append(widget)
        return cards

    def _card(self, page_widget, title):
        for card in self._cards(page_widget):
            if card.title_label.text() == title:
                return card
        self.fail(f"页面里没有标题为 {title!r} 的分节卡")

    # ── structure ─────────────────────────────────────────────────────

    def test_every_form_page_is_boxed(self):
        expected = {
            "project": 4,
            "typesetting": 6,
            "interface": 3,
            "app": 4,
            "models": 3,
            "workbench_temp": 1,
        }
        pages = {
            "project": self.panel.project_block.widget,
            "typesetting": self.panel.typesetting_block.widget,
            "interface": self.panel.interface_block.widget,
            "app": self.panel.config_mgmt_block.widget,
            "models": self.panel.models_group,
            "workbench_temp": self.panel.workbench_settings_group,
        }
        for key, page in pages.items():
            cards = self._cards(page)
            self.assertEqual(len(cards), expected[key], f"{key} 的分节卡数量")
            for card in cards:
                self.assertTrue(card.title_label.text(), f"{key} 有卡片没有标题")

    def test_cards_hold_their_rows(self):
        """行必须在卡内（不是页面布局的平级兄弟）——这正是卡片化的目的。"""
        for card in self._cards(self.panel.project_block.widget):
            self.assertGreater(
                card.contentLayout().count(), 0, f"{card.title_label.text()} 卡是空的"
            )

    def test_spot_check_membership(self):
        project = self.panel.project_block.widget
        typesetting = self.panel.typesetting_block.widget
        interface = self.panel.interface_block.widget
        app_page = self.panel.config_mgmt_block.widget

        cases = [
            (project, "Output", self.panel.rst_quality_sublock),
            (project, "Backup", self.panel.batch_versions_spin),
            (typesetting, "Fonts", self.panel.max_font_size_edit),
            (typesetting, "Vertical Text", self.panel.auto_tcy_options_widget),
            (interface, "Appearance", self.panel.anim_combo),
            (interface, "Canvas", self.panel.undo_limit_spin),
            (app_page, "External Editor", self.panel.ps_path_edit),
            (app_page, "Workbench Prompts", self.panel.confirm_costly_checker),
        ]
        for page, title, control in cases:
            card = self._card(page, title)
            self.assertTrue(
                card.isAncestorOf(control),
                f"{control} 不在 {title} 卡内",
            )

    def test_halfwidth_sublock_still_follows_parent(self):
        """子行随父项显隐的语义不因卡片化改变（用 isHidden，页面被栈隐藏）。"""
        wrapper = self.panel._halfwidth_horizontal_sublock_wrapper
        checker = self.panel.halfwidth_corner_bracket_checker
        snapshot = checker.isChecked()
        self.addCleanup(checker.setChecked, snapshot)

        checker.setChecked(False)
        self.assertTrue(wrapper.isHidden())
        checker.setChecked(True)
        self.assertFalse(wrapper.isHidden())

    def test_pipeline_tabs_are_boxed_like_the_rest(self):
        """管线标签页也走分节卡（2026-09-21）：页体凹陷，参数表在「Parameters」卡里。

        之前这条断言的是反例（``recessed=False`` + 不套卡）；现在四个阶段的参数
        表都是卡，翻译器多一张「API Profile」卡且在参数卡之前。
        """
        area = self.panel.pipeline_stack.widget(0)
        page_body = area.widget() if hasattr(area, "widget") else area
        self.assertEqual(page_body.objectName(), "ConfigPageBody")

        for panel in (
            self.panel.detect_config_panel,
            self.panel.ocr_config_panel,
            self.panel.inpaint_config_panel,
            self.panel.trans_config_panel,
        ):
            card = panel.params_card
            self.assertTrue(card.property("compact"))
            self.assertEqual(card.title_label.text(), "Parameters")
            self.assertIs(
                card.contentLayout().itemAt(0).layout(), panel.params_layout
            )

        trans = self.panel.trans_config_panel
        self.assertEqual(
            trans.vlayout.indexOf(trans._profile_section),
            trans.vlayout.indexOf(trans.params_card) - 1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
