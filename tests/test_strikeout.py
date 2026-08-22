"""Offscreen regression tests for the strike-through format control.

The bold toggle was removed from the format button row (font weight stays
reachable via the family/style selector); a strike-through toggle
(``FontStrikeChecker``) takes its slot. These tests pin the data path:
``FontFormat.strikeout`` → engine char format → readback, plus the panel
restore and the shortcut table swap (bold action out, strike action in).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_strikeout.py
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


class StrikeoutItemTest(unittest.TestCase):
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

    def test_setter_and_readback(self):
        item = self._new_item()
        self.assertFalse(item.get_fontformat().strikeout)
        item.setFontStrikeOut(True)
        self.assertTrue(item.get_fontformat().strikeout)
        item.setFontStrikeOut(False)
        self.assertFalse(item.get_fontformat().strikeout)

    def test_set_fontformat_applies_strikeout(self):
        # set_char_format=False is the rich-HTML path where existing text
        # keeps its own formats; the whole-style apply must carry strikeout.
        item = self._new_item()
        fmt = item.fontformat.deepcopy()
        fmt.strikeout = True
        item.set_fontformat(fmt, set_char_format=True)
        self.assertTrue(item.get_fontformat().strikeout)

    def test_html_round_trip(self):
        # Qt serializes FontStrikeOut char formats as <s>…</s>; the rich-text
        # reload path must therefore preserve the attribute.
        item = self._new_item()
        item.setFontStrikeOut(True)
        html = item.document().toHtml()
        self.assertIn("line-through", html)
        item2 = self._new_item()
        item2.load_rich_text_html(html)
        self.assertTrue(item2.get_fontformat().strikeout)


class FormatButtonsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_button_row_composition(self):
        from ui.text_panel import FormatGroupBtn

        group = FormatGroupBtn()
        self.assertFalse(hasattr(group, "boldBtn"))
        for attr in ("strikeBtn", "italicBtn", "underlineBtn", "emphasisBtn"):
            self.assertTrue(hasattr(group, attr), attr)
        # strike replaces bold in the leftmost slot
        self.assertIs(group.layout().itemAt(0).widget(), group.strikeBtn)

    def test_param_signal_payload(self):
        from ui.text_panel import FormatGroupBtn

        group = FormatGroupBtn()
        received = []
        group.param_changed.connect(lambda name, val: received.append((name, val)))
        group.strikeBtn.setChecked(True)
        group.setStrikeout()
        self.assertEqual(received, [("strikeout", True)])


class ShortcutTableTest(unittest.TestCase):
    def test_bold_action_removed_strike_added(self):
        from ui.configpanel import _ACTION_NAMES, DEFAULT_SHORTCUTS

        self.assertNotIn("bold", DEFAULT_SHORTCUTS)
        self.assertNotIn("bold", _ACTION_NAMES)
        self.assertIn("strike", DEFAULT_SHORTCUTS)
        self.assertEqual(DEFAULT_SHORTCUTS["strike"], [])
        self.assertEqual(_ACTION_NAMES["strike"], "Strike-through")


if __name__ == "__main__":
    unittest.main(verbosity=2)
