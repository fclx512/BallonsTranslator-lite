"""Offscreen regression tests for the restored punctuation position setting.

The fork's edge-aligned stop marks (「标点靠边」, Simplified style) were
silently lost in the v1.5.12 engine port: the new layout centered
PAUSEORSTOP marks whenever ``standard_vertical_roman_alignment`` was on
(its FontFormat default), and the settings dropdown was later removed.
This pins the restored behaviour:

- ``punctuation_position`` alone decides stop-mark alignment
  (Simplified → upper-right edge, Traditional → centered),
- edge alignment covers sentence-internal pause marks (。．，、) only;
  exclamation/question marks center in both modes,
- ``standard_vertical_roman_alignment`` no longer hijacks stop marks
  (it keeps governing roman glyph orientation),
- the draw offsets really move the ink between the two modes.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_punctuation_position.py
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

from utils.fontformat import FontFormat, PunctuationPosition  # noqa: E402
from ui.text_engine.vertical_layout import (  # noqa: E402
    VerticalTextDocumentLayout,
)


def _make_layout(text):
    from qtpy.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setPlainText(text)
    fontformat = FontFormat(vertical=True)
    layout = VerticalTextDocumentLayout(doc, fontformat)
    doc.setDocumentLayout(layout)
    layout.relayout_on_changed = False
    layout.setMaxSize(200, 200, relayout=False)
    layout.reLayoutEverything()
    return doc, layout


def _stop_mark_xoff(text, position):
    """Return the x draw offset of the stop mark's own line/cell."""
    _doc, layout = _make_layout(text)
    layout.punctuation_position = position
    layout.reLayout()
    offsets = layout._draw_offset[0]
    # Vertical text breaks one character per line; "中。文" puts the mark
    # at index 1.
    return offsets[1][0]


class TestPunctuationPosition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_simplified_is_not_centered(self):
        _doc, layout = _make_layout("。")
        layout.punctuation_position = PunctuationPosition.Simplified
        self.assertFalse(layout.centers_vertical_glyph("。"))

    def test_traditional_is_centered(self):
        _doc, layout = _make_layout("。")
        layout.punctuation_position = PunctuationPosition.Traditional
        self.assertTrue(layout.centers_vertical_glyph("。"))

    def test_roman_alignment_does_not_hijack_stop_marks(self):
        _doc, layout = _make_layout("。")
        layout.punctuation_position = PunctuationPosition.Simplified
        layout.fontformat.standard_vertical_roman_alignment = True
        self.assertFalse(layout.centers_vertical_glyph("。"))
        layout.fontformat.standard_vertical_roman_alignment = False
        self.assertFalse(layout.centers_vertical_glyph("。"))

    def test_exclamation_question_center_in_both_modes(self):
        # Fork narrowing: edge alignment covers sentence-internal pause
        # marks (。．，、) only; exclamation/question marks stay centered
        # regardless of punctuation_position.
        for char in ("！", "？", "‼", "："):
            _doc, layout = _make_layout(char)
            layout.punctuation_position = PunctuationPosition.Simplified
            self.assertTrue(
                layout.centers_vertical_glyph(char),
                f"Simplified should center {char!r}",
            )
            layout.punctuation_position = PunctuationPosition.Traditional
            self.assertTrue(
                layout.centers_vertical_glyph(char),
                f"Traditional should center {char!r}",
            )

    def test_simplified_exclamation_matches_traditional_offset(self):
        # End-to-end: in Simplified mode the exclamation mark must no
        # longer be pushed to the column edge — its draw offset equals
        # the centered (Traditional) one.
        simplified = _stop_mark_xoff(
            "中！文", PunctuationPosition.Simplified
        )
        traditional = _stop_mark_xoff(
            "中！文", PunctuationPosition.Traditional
        )
        self.assertEqual(simplified, traditional)

    def test_edge_aligned_draws_right_of_centered(self):
        simplified = _stop_mark_xoff(
            "中。文", PunctuationPosition.Simplified
        )
        traditional = _stop_mark_xoff(
            "中。文", PunctuationPosition.Traditional
        )
        # Edge alignment pushes the ink to the column's right edge; the
        # centered mode sits (base_width - ink_width)/2 further left.
        self.assertGreater(simplified, traditional)

    def test_setter_triggers_relayout(self):
        _doc, layout = _make_layout("中。文")
        generation = layout.layout_generation
        layout.setPunctuationPosition(PunctuationPosition.Traditional)
        self.assertGreater(layout.layout_generation, generation)


if __name__ == "__main__":
    unittest.main()
