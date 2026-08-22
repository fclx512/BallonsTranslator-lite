"""Offscreen tests for the node 3 settings-panel entries.

Covers the three new Text-formatting settings — automatic Tate-chu-yoko
(toggle + Apply button + options), compact punctuation spacing, and quick
insert characters — plus the QuickSymbolDialog custom group fed by
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

from qtpy.QtWidgets import QApplication, QPushButton  # noqa: E402

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
        """竖排相关设置归入 Vertical Text 分组，quick insert 前置不混排。"""
        from qtpy.QtWidgets import QComboBox, QLabel, QLineEdit

        layout = self.panel.typesetting_block.widget.layout()
        widgets = [
            layout.itemAt(i).widget() for i in range(layout.count())
        ]
        edit = self.panel.quick_insert_characters_edit
        combo = self.panel.punctuation_position_combo
        quick_row = next(
            w
            for w in widgets
            if w is not None
            and w is not edit
            and edit in w.findChildren(QLineEdit)
        )
        header = next(
            w
            for w in widgets
            if w is not None
            and w.objectName() == "ConfigSectionHeader"
            and any(
                isinstance(c, QLabel) and c.text() == "Vertical Text"
                for c in w.findChildren(QLabel)
            )
        )
        punct_row = next(
            w
            for w in widgets
            if w is not None
            and w is not combo
            and combo in w.findChildren(QComboBox)
        )
        self.assertLess(
            widgets.index(quick_row),
            widgets.index(header),
            "Quick insert characters must sit before the Vertical Text header",
        )
        self.assertLess(
            widgets.index(header),
            widgets.index(punct_row),
            "Punctuation Position must sit inside the Vertical Text section",
        )


class QuickSymbolCustomGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _buttons(self, dialog):
        return [
            btn
            for btn in dialog.findChildren(QPushButton)
            if len(btn.text()) == 1
        ]

    def test_custom_group_renders_pcfg_chars(self):
        old = pcfg.quick_insert_characters
        self.addCleanup(setattr, pcfg, "quick_insert_characters", old)
        pcfg.quick_insert_characters = "♥♡★"
        from ui.quick_symbol_dialog import QuickSymbolDialog

        dialog = QuickSymbolDialog()
        try:
            texts = {btn.text() for btn in self._buttons(dialog)}
            self.assertIn("♥", texts)
            self.assertIn("♡", texts)
            self.assertIn("★", texts)
        finally:
            dialog.deleteLater()

    def test_empty_custom_chars_no_crash(self):
        old = pcfg.quick_insert_characters
        self.addCleanup(setattr, pcfg, "quick_insert_characters", old)
        pcfg.quick_insert_characters = ""
        from ui.quick_symbol_dialog import QuickSymbolDialog

        dialog = QuickSymbolDialog()
        try:
            # Fixed groups still render without the custom section.
            texts = {btn.text() for btn in self._buttons(dialog)}
            self.assertIn("「", texts)
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
