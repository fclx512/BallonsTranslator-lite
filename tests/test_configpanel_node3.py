"""Offscreen tests for the node 3 settings-panel entries.

Covers the three new Text-formatting settings — automatic Tate-chu-yoko
(toggle + Apply button + options), compact punctuation spacing, and quick
insert characters — plus the QuickSymbolPanel custom group fed by
``pcfg.quick_insert_characters``.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_configpanel_node3.py
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

from qtpy.QtWidgets import QApplication, QPushButton, QToolButton  # noqa: E402

from utils.config import pcfg  # noqa: E402


class ConfigPanelNode3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.configpanel import ConfigPanel

        cls.app = QApplication.instance() or QApplication([])
        cls.ConfigPanel = ConfigPanel
        cls.panel = ConfigPanel()

    def _set_auto_tcy(self, **kwargs):
        """Mutate pcfg.auto_tate_chu_yoko in place and restore afterwards."""
        cfg = pcfg.auto_tate_chu_yoko
        snapshot = {
            "enabled": cfg.enabled,
            "max_length": cfg.max_length,
            "include_numbers": cfg.include_numbers,
            "include_letters": cfg.include_letters,
            "additional_chars": cfg.additional_chars,
        }
        for key, value in kwargs.items():
            setattr(cfg, key, value)
        self.addCleanup(
            lambda: [
                setattr(pcfg.auto_tate_chu_yoko, key, value)
                for key, value in snapshot.items()
            ]
        )

    def test_controls_exist(self):
        self.assertTrue(
            hasattr(self.panel, "auto_tate_chu_yoko_checker")
        )
        self.assertTrue(hasattr(self.panel, "auto_tate_chu_yoko_apply_btn"))
        self.assertTrue(hasattr(self.panel, "auto_tate_chu_yoko_max_length"))
        self.assertTrue(hasattr(self.panel, "auto_tate_chu_yoko_numbers"))
        self.assertTrue(hasattr(self.panel, "auto_tate_chu_yoko_letters"))
        self.assertTrue(
            hasattr(self.panel, "auto_tate_chu_yoko_additional_chars")
        )
        self.assertTrue(hasattr(self.panel, "compact_punctuation_checker"))
        self.assertTrue(hasattr(self.panel, "quick_insert_characters_edit"))

    def test_auto_tcy_toggle_updates_pcfg_and_visibility(self):
        self._set_auto_tcy(enabled=False)
        self.panel.setupConfig()
        self.assertFalse(self.panel.auto_tate_chu_yoko_checker.isChecked())
        # isHidden() reflects the explicit setVisible() state; the panel is
        # never actually shown in offscreen tests.
        self.assertTrue(self.panel.auto_tcy_options_widget.isHidden())
        self.panel.auto_tate_chu_yoko_checker.setChecked(True)
        self.assertTrue(pcfg.auto_tate_chu_yoko.enabled)
        self.assertFalse(self.panel.auto_tcy_options_widget.isHidden())
        self.assertFalse(self.panel.auto_tate_chu_yoko_apply_btn.isHidden())

    def test_auto_tcy_options_update_pcfg(self):
        self._set_auto_tcy(
            max_length=3,
            include_numbers=True,
            include_letters=True,
            additional_chars="§",
        )
        self.panel.setupConfig()
        self.assertEqual(self.panel.auto_tate_chu_yoko_max_length.value(), 3)
        self.panel.auto_tate_chu_yoko_max_length.setValue(5)
        self.assertEqual(pcfg.auto_tate_chu_yoko.max_length, 5)
        self.panel.auto_tate_chu_yoko_numbers.setChecked(False)
        self.assertFalse(pcfg.auto_tate_chu_yoko.include_numbers)
        self.panel.auto_tate_chu_yoko_letters.setChecked(False)
        self.assertFalse(pcfg.auto_tate_chu_yoko.include_letters)
        self.panel.auto_tate_chu_yoko_additional_chars.setText("§†")
        self.assertEqual(pcfg.auto_tate_chu_yoko.additional_chars, "§†")

    def test_apply_button_emits_request(self):
        self._set_auto_tcy(enabled=True)
        self.panel.setupConfig()
        emitted = []

        def on_requested():
            emitted.append(True)

        self.panel.apply_auto_tate_chu_yoko_requested.connect(on_requested)
        self.panel.auto_tate_chu_yoko_apply_btn.click()
        self.assertEqual(len(emitted), 1)

    def test_compact_punctuation_toggle_updates_pcfg(self):
        old = pcfg.compact_vertical_punctuation_spacing
        self.addCleanup(
            setattr, pcfg, "compact_vertical_punctuation_spacing", old
        )
        self.panel.setupConfig()
        self.assertEqual(
            self.panel.compact_punctuation_checker.isChecked(), old
        )
        self.panel.compact_punctuation_checker.setChecked(not old)
        self.assertEqual(
            pcfg.compact_vertical_punctuation_spacing, not old
        )

    def test_quick_insert_characters_updates_pcfg(self):
        old = pcfg.quick_insert_characters
        self.addCleanup(setattr, pcfg, "quick_insert_characters", old)
        self.panel.setupConfig()
        self.panel.quick_insert_characters_edit.setText("♥♡")
        self.assertEqual(pcfg.quick_insert_characters, "♥♡")

    def test_typesetting_section_order(self):
        """竖排设置归入 Vertical Text 分节卡，quick insert 前置不混排；
        字体管理项归 Fonts 卡并前移（不再悬挂在 Vertical Text 之后）。

        卡片化（2026-09-20）后行为断言按「这一行属于哪张卡」判：先在页面
        布局里按顺序取出分节卡，再用 ``isAncestorOf`` 判定归属、用卡内布局
        下标判定同卡内的先后。
        """
        from ui.custom_widget import PanelGroupBox

        page = self.panel.typesetting_block.widget
        layout = page.layout()
        cards = []
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if isinstance(widget, PanelGroupBox) and widget.property("compact"):
                cards.append(widget)
        titles = [card.title_label.text() for card in cards]

        def card(title):
            self.assertIn(title, titles)
            return cards[titles.index(title)]

        def row_index(card_widget, control):
            """该控件所在行在卡内的下标（行 = 卡内容布局的直接子项）。"""
            body = card_widget.contentLayout()
            for i in range(body.count()):
                widget = body.itemAt(i).widget()
                if widget is not None and (
                    widget is control or widget.isAncestorOf(control)
                ):
                    return i
            self.fail(
                f"{control} 不在 {card_widget.title_label.text()} 卡内"
            )

        fonts = card("Fonts")
        vertical = card("Vertical Text")
        quick = card("Quick Symbol Palette")

        # Edge-aligned punctuation setting was restored (fork「标点靠边」)
        self.assertTrue(vertical.isAncestorOf(self.panel.punctuation_position_combo))
        self.assertTrue(
            vertical.isAncestorOf(self.panel.compact_punctuation_checker),
            "Compact punctuation must sit inside the Vertical Text card",
        )
        self.assertLess(
            row_index(vertical, self.panel.punctuation_position_combo),
            row_index(vertical, self.panel.compact_punctuation_checker),
            "Punctuation Position must precede the compact punctuation row",
        )

        self.assertTrue(
            fonts.isAncestorOf(self.panel.exclude_fonts_btn),
            "Font Exclusion must sit inside the Fonts card",
        )
        self.assertLess(
            titles.index("Fonts"),
            titles.index("Vertical Text"),
            "Fonts section must precede the Vertical Text section",
        )

        self.assertTrue(
            quick.isAncestorOf(self.panel.quick_insert_characters_edit),
            "Quick insert characters must sit inside the Quick Symbol Palette card",
        )
        self.assertLess(
            titles.index("Quick Symbol Palette"),
            titles.index("Vertical Text"),
            "Quick insert characters must sit before the Vertical Text section",
        )


class QuickSymbolCustomGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _buttons(self, panel):
        return [
            btn
            for btn in panel.findChildren(QToolButton)
            if len(btn.text()) == 1
        ]

    def test_custom_group_renders_pcfg_chars(self):
        old = pcfg.quick_insert_characters
        self.addCleanup(setattr, pcfg, "quick_insert_characters", old)
        pcfg.quick_insert_characters = "♥♡★"
        from ui.quick_symbol_panel import QuickSymbolPanel

        panel = QuickSymbolPanel()
        try:
            texts = {btn.text() for btn in self._buttons(panel)}
            self.assertIn("♥", texts)
            self.assertIn("♡", texts)
            self.assertIn("★", texts)
        finally:
            panel.deleteLater()

    def test_empty_custom_chars_no_crash(self):
        old = pcfg.quick_insert_characters
        self.addCleanup(setattr, pcfg, "quick_insert_characters", old)
        pcfg.quick_insert_characters = ""
        from ui.quick_symbol_panel import QuickSymbolPanel

        panel = QuickSymbolPanel()
        try:
            # Fixed groups still render without the custom section.
            texts = {btn.text() for btn in self._buttons(panel)}
            self.assertIn("「", texts)
        finally:
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
