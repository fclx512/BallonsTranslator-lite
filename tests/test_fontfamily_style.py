"""Offscreen regression tests for font-family change with style sync.

The engine ``TextBlkItem.setFontFamily`` accepts no ``style_name``, but the
format-command layer (``ui/fontformat_commands.py::ffmt_change_font_family``)
passes one — clicking the font-exclusion dialog (which refreshes the family
combo) with a selected text block crashed the app with
``TypeError: setFontFamily() got an unexpected keyword argument 'style_name'``.
The fork compat layer (``ui/textitem.py``) now adapts the engine setters back
to the legacy signatures: family switches also apply the selected style's
weight on the canvas immediately, and the spacing setters accept the legacy
``set_selected``/``restore_cursor`` kwargs they used to take.

Note: the offscreen platform exposes no system fonts, so the style-weight
sync is exercised by stubbing ``QFontDatabase.weight``.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_fontfamily_style.py
"""

import os
import os.path as osp
import sys
import unittest
from unittest.mock import patch

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字A"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class FontFamilyStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _new_item(self, translation="测试文字A"):
        item = self.TextBlkItem(blk=_make_blk(translation=translation), idx=0)
        self.scene.addItem(item)
        return item

    def test_command_layer_family_change_no_crash(self):
        # The reported crash path: ffmt_change_font_family passes style_name.
        from ui.funcmaps import handle_ffmt_change
        from utils.fontformat import FontFormat

        item = self._new_item()
        fmt = FontFormat(font_family="Arial", font_size=24)
        handle_ffmt_change["font_family"](
            "font_family", "Arial", fmt, is_global=False, blkitems=[item]
        )

    def test_style_weight_synced_to_document(self):
        # Style "Bold" (weight 700) must reach both the default font and the
        # fragment formats — the pre-fix behavior was: family changes only.
        item = self._new_item()
        with patch("ui.textitem.QFontDatabase.weight", return_value=700):
            item.setFontFamily("Arial", style_name="Bold")
        doc = item.document()
        self.assertGreaterEqual(doc.defaultFont().weight(), 700)
        frag = doc.firstBlock().begin().fragment()
        self.assertGreaterEqual(frag.charFormat().font().weight(), 700)
        self.assertTrue(frag.charFormat().font().bold())

    def test_regular_style_weight_synced(self):
        # Switching to a light style must visibly thin the text (stale-bold
        # regression guard: apply_font_change syncs the format, the canvas
        # must follow).
        item = self._new_item()
        with patch("ui.textitem.QFontDatabase.weight", return_value=300):
            item.setFontFamily("Arial", style_name="Light")
        doc = item.document()
        self.assertLessEqual(doc.defaultFont().weight(), 300)
        self.assertFalse(doc.defaultFont().bold())

    def test_empty_style_is_engine_behavior(self):
        # style_name="" (or absent) must stay a plain family change.
        item = self._new_item()
        weight_before = item.document().defaultFont().weight()
        item.setFontFamily("Times New Roman")
        self.assertEqual(item.document().defaultFont().family(), "Times New Roman")
        self.assertEqual(item.document().defaultFont().weight(), weight_before)

    def test_unknown_style_skips_weight_sync(self):
        item = self._new_item()
        with patch("ui.textitem.QFontDatabase.weight", return_value=-1):
            item.setFontFamily("Arial", style_name="NoSuchStyle")  # must not raise
        self.assertEqual(item.document().defaultFont().weight(), 400)

    def test_spacing_setters_accept_legacy_kwargs(self):
        # ffmt_change_line/letter_spacing & line_spacing_type still pass
        # set_kwargs/restore_cursor the old compat layer used to take.
        item = self._new_item()
        item.setLineSpacing(1.5, set_selected=True, restore_cursor=True)
        item.setLetterSpacing(1.0, set_selected=True, restore_cursor=True)
        item.setLineSpacingType(0, restore_cursor=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)