"""Offscreen regression tests for the node 2c annotation controls.

The first-pass UI (``ui/text_panel.py::AnnotationFormatGroup``) routes
emphasis / Ruby / tate-chu-yoko / ligature / oldstyle-num edits to the engine
``TextBlkItem`` setters, whose document-level undo transactions and property
properties back the readback helpers the panel restores from.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_annotation_controls.py
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

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class AnnotationItemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _new_item(self):
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        self.scene.addItem(item)
        return item

    @staticmethod
    def _select_prefix(item, length=2):
        """Enter the item's edit session and select a leading prefix."""
        from qtpy.QtGui import QTextCursor

        item.startEdit()
        cursor = item.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(
            length, QTextCursor.MoveMode.KeepAnchor
        )
        item.setTextCursor(cursor)
        return item

    def test_emphasis_round_trip(self):
        item = self._new_item()
        item.setEmphasis("filled dot", "over right")
        self.assertEqual(
            item.emphasis_values(), ("filled dot", "over right")
        )
        item.setEmphasis("none", "over right")
        self.assertEqual(item.emphasis_values()[0], "none")

    def test_tate_chu_yoko_round_trip(self):
        item = self._new_item()
        item.setTateChuYoko(True)
        self.assertTrue(item.tate_chu_yoko_enabled())
        item.setTateChuYoko(False)
        self.assertFalse(item.tate_chu_yoko_enabled())

    def test_ruby_round_trip_and_remove(self):
        item = self._select_prefix(self._new_item())
        item.setRuby("group", "か", "over")
        ruby_type, text, position, enabled = item.ruby_editor_values()
        self.assertEqual((ruby_type, text, position), ("group", "か", "over"))
        self.assertTrue(enabled)
        removed = item.removeRuby()
        self.assertTrue(removed)
        _t, _x, _p, enabled = item.ruby_editor_values()
        self.assertFalse(enabled)

    def test_ligature_axis_round_trip(self):
        item = self._new_item()
        item.setLigatureAxis("common", "enabled")
        self.assertEqual(item.ligature_axis_value("common"), "enabled")
        item.setLigatureAxis("common", "default")
        self.assertEqual(item.ligature_axis_value("common"), "default")

    def test_oldstyle_nums_round_trip(self):
        item = self._new_item()
        item.setOldstyleNums("enabled")
        self.assertEqual(item.oldstyle_nums_value(), "enabled")
        item.setOldstyleNums("default")
        self.assertEqual(item.oldstyle_nums_value(), "default")

    def test_initial_overflow_non_clip_does_not_crash(self):
        """首次布局即溢出 + 非裁剪模式：init 期间不得读未初始化属性。"""
        from utils.config import pcfg

        saved = pcfg.clip_text_overflow
        try:
            pcfg.clip_text_overflow = False
            item = self.TextBlkItem(
                blk=_make_blk(translation="很长" * 40), idx=90
            )
            self.scene.addItem(item)
            self.assertFalse(item._text_overflows)
        finally:
            pcfg.clip_text_overflow = saved

    def test_initial_overflow_clip_keeps_lock_state(self):
        """裁剪模式：init 期间溢出标记不应被 init 尾部重置。"""
        from utils.config import pcfg

        saved = pcfg.clip_text_overflow
        try:
            pcfg.clip_text_overflow = True
            item = self.TextBlkItem(
                blk=_make_blk(translation="很长" * 40), idx=91
            )
            self.scene.addItem(item)
            self.assertTrue(item._text_overflows)
        finally:
            pcfg.clip_text_overflow = saved


class AnnotationGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_group(self):
        from ui.text_panel import AnnotationFormatGroup

        return AnnotationFormatGroup()

    def test_set_helpers_restore_without_emitting(self):
        group = self._make_group()
        emissions = []
        group.annotation_changed.connect(
            lambda name, value: emissions.append(name)
        )
        group.set_emphasis("open triangle", "under left")
        group.set_tcy(True)
        group.set_ligature("contextual", "disabled")
        group.set_onum("enabled")
        group.set_ruby("mono", "よ", "under", True)
        self.assertEqual(emissions, [])
        self.assertEqual(group.emphasisBox.currentData(), "open triangle")
        self.assertEqual(group.emphasisPosBox.currentData(), "under left")
        self.assertTrue(group.tcyChecker.isChecked())
        self.assertEqual(
            group.ligatureBoxes["contextual"].currentData(), "disabled"
        )
        self.assertEqual(group.onumBox.currentData(), "enabled")
        self.assertEqual(group.rubyTypeBox.currentData(), "mono")
        self.assertEqual(group.rubyEdit.text(), "よ")
        self.assertTrue(group.rubyRemoveBtn.isEnabled())

    def test_emphasis_emits_payload(self):
        group = self._make_group()
        payloads = []
        group.annotation_changed.connect(
            lambda name, value: payloads.append((name, value))
        )
        group.emphasisBox.setCurrentIndex(
            group.emphasisBox.findData("filled circle")
        )
        self.assertEqual(
            payloads[-1], ("emphasis", ("filled circle", "over right"))
        )

    def test_tcy_and_axis_emit(self):
        group = self._make_group()
        payloads = []
        group.annotation_changed.connect(
            lambda name, value: payloads.append((name, value))
        )
        group.tcyChecker.setChecked(True)
        self.assertEqual(payloads[-1], ("tcy", True))
        group.ligatureBoxes["common"].setCurrentIndex(
            group.ligatureBoxes["common"].findData("disabled")
        )
        self.assertEqual(payloads[-1], ("ligature", ("common", "disabled")))
        group.onumBox.setCurrentIndex(group.onumBox.findData("enabled"))
        self.assertEqual(payloads[-1], ("onum", "enabled"))

    def test_ruby_buttons_emit(self):
        group = self._make_group()
        payloads = []
        remove_emissions = []
        group.annotation_changed.connect(
            lambda name, value: payloads.append((name, value))
        )
        group.ruby_remove.connect(lambda: remove_emissions.append(True))
        group.rubyEdit.setText("き")
        group.rubyTypeBox.setCurrentIndex(group.rubyTypeBox.findData("mono"))
        group.rubyApplyBtn.click()
        self.assertEqual(
            payloads[-1], ("ruby", ("mono", "き", "over"))
        )
        group.rubyRemoveBtn.click()
        self.assertEqual(remove_emissions, [True])


class PanelRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.text_panel import FontFormatPanel
        from ui.textitem import TextBlkItem

        cls.FontFormatPanel = FontFormatPanel
        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _panel_with_item(self):
        from qtpy.QtGui import QTextCursor

        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        self.scene.addItem(item)
        item.startEdit()
        cursor = item.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        item.setTextCursor(cursor)
        panel.textblk_item = item
        return panel, item

    def test_routes_to_item_setters(self):
        panel, item = self._panel_with_item()
        self.FontFormatPanel._on_annotation_changed(
            panel, "emphasis", ("open sesame", "under right")
        )
        self.assertEqual(item.emphasis_values(), ("open sesame", "under right"))
        self.FontFormatPanel._on_annotation_changed(
            panel, "ligature", ("discretionary", "enabled")
        )
        self.assertEqual(
            item.ligature_axis_value("discretionary"), "enabled"
        )
        self.FontFormatPanel._on_annotation_changed(panel, "onum", "enabled")
        self.assertEqual(item.oldstyle_nums_value(), "enabled")

        # Ruby and tate-chu-yoko are mutually exclusive spans; give each its
        # own item so the engine's overlap validation stays quiet.
        panel, item = self._panel_with_item()
        self.FontFormatPanel._on_annotation_changed(
            panel, "ruby", ("group", "め", "over")
        )
        _t, text, _p, enabled = item.ruby_editor_values()
        self.assertTrue(enabled)
        self.assertEqual(text, "め")
        self.FontFormatPanel._on_ruby_remove(panel)
        _t, _x, _p, enabled = item.ruby_editor_values()
        self.assertFalse(enabled)

        panel, item = self._panel_with_item()
        self.FontFormatPanel._on_annotation_changed(panel, "tcy", True)
        self.assertTrue(item.tate_chu_yoko_enabled())

    def test_ignores_changes_without_item(self):
        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        panel.textblk_item = None
        # Must not raise when no text item is active (global mode).
        self.FontFormatPanel._on_annotation_changed(
            panel, "emphasis", ("filled dot", "over right")
        )
        self.FontFormatPanel._on_ruby_remove(panel)

    def test_tcy_over_ruby_caught_and_reverted(self):
        """TCY 与 Ruby 互斥：面板捕获引擎校验并回滚勾选态，不得冒泡异常。"""
        from unittest.mock import patch

        from ui.text_panel import AnnotationFormatGroup

        panel, item = self._panel_with_item()
        panel.annotation_group = AnnotationFormatGroup()
        self.FontFormatPanel._on_annotation_changed(
            panel, "ruby", ("group", "か", "over")
        )
        with patch("ui.text_panel.QMessageBox.information") as info:
            self.FontFormatPanel._on_annotation_changed(panel, "tcy", True)
        info.assert_called_once()
        self.assertFalse(panel.annotation_group.tcyChecker.isChecked())

    def test_ruby_empty_reading_is_validation_not_crash(self):
        from unittest.mock import patch

        panel, item = self._panel_with_item()
        with patch("ui.text_panel.QMessageBox.information") as info:
            self.FontFormatPanel._on_annotation_changed(
                panel, "ruby", ("group", "", "over")
            )
        info.assert_called_once()

    def test_ruby_applies_to_whole_block_when_not_editing(self):
        """非编辑态无选区时 Ruby 与其余注解一致：应用到整块文本。"""
        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        self.scene.addItem(item)
        panel.textblk_item = item
        self.FontFormatPanel._on_annotation_changed(
            panel, "ruby", ("group", "か", "over")
        )
        _t, text, _p, enabled = item.ruby_editor_values()
        self.assertTrue(enabled)
        self.assertEqual(text, "か")


if __name__ == "__main__":
    unittest.main(verbosity=2)
