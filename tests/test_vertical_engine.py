"""Offscreen regression tests for the engine vertical layout (port node 2b).

The engine ``ui/text_engine/vertical_layout.py`` gained three fork-compatible
typography members (``punctuation_position``, ``tatechuyoko_threshold``,
``halfwidth_jp_corner_brackets``) that the settings panel drives through
hasattr-guarded calls.  This test pins:

- constructor defaults read from ``pcfg``,
- ``setPunctuationPosition`` re-layout behaviour,
- tate-chu-yoko run injection from the fork ``find_tatechuyoko_runs``
  detector into the annotation-driven ``text_combine_ranges``,
- ``centers_vertical_glyph`` honouring Simplified/Traditional.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_vertical_engine.py
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

from utils.config import pcfg  # noqa: E402
from utils.fontformat import FontFormat, PunctuationPosition  # noqa: E402
from ui.text_engine.vertical_layout import (  # noqa: E402
    PUNSET_ALIGNCENTER,
    PUNSET_CORNER_BRACKET,
    VerticalTextDocumentLayout,
    find_tatechuyoko_runs,
)


def _make_layout(text, **layout_kwargs):
    from qtpy.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setPlainText(text)
    fontformat = FontFormat(vertical=True)
    layout = VerticalTextDocumentLayout(doc, fontformat, **layout_kwargs)
    doc.setDocumentLayout(layout)
    layout.relayout_on_changed = False
    layout.setMaxSize(200, 200, relayout=False)
    layout.reLayoutEverything()
    return doc, layout


class TestTatechuyokoRuns(unittest.TestCase):
    def test_disabled_below_threshold(self):
        self.assertEqual(find_tatechuyoko_runs("ABC abc", 0), {})
        self.assertEqual(find_tatechuyoko_runs("ABC abc", -1), {})

    def test_short_runs_only(self):
        self.assertEqual(
            find_tatechuyoko_runs("ABC 中文 abc", 3), {0: 3, 7: 3}
        )

    def test_long_runs_excluded(self):
        self.assertEqual(find_tatechuyoko_runs("ABCDEF", 3), {})

    def test_exact_threshold_included(self):
        # The detector matches the whole contiguous [A-Za-z0-9] run; a
        # separated "12" run of length 2 is inside the threshold.
        self.assertEqual(find_tatechuyoko_runs("ab 12", 2), {0: 2, 3: 2})
        self.assertEqual(find_tatechuyoko_runs("ab12", 4), {0: 4})


class TestVerticalEngineLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_constructor_reads_pcfg_defaults(self):
        _doc, layout = _make_layout("测试")
        self.assertEqual(
            layout.punctuation_position, int(pcfg.punctuation_position)
        )
        self.assertEqual(
            layout.tatechuyoko_threshold, int(pcfg.tatechuyoko_threshold)
        )
        self.assertEqual(
            layout.halfwidth_jp_corner_brackets,
            bool(pcfg.halfwidth_jp_corner_brackets),
        )

    def test_set_punctuation_position_relayouts(self):
        _doc, layout = _make_layout("测试")
        generation = layout.layout_generation
        layout.setPunctuationPosition(
            PunctuationPosition.Traditional
        )
        self.assertGreater(layout.layout_generation, generation)
        # Same value is a no-op.
        generation = layout.layout_generation
        layout.setPunctuationPosition(PunctuationPosition.Traditional)
        self.assertEqual(layout.layout_generation, generation)

    def test_centers_vertical_glyph_stop_marks(self):
        _doc, layout = _make_layout("。")
        # Stop marks stay centered under the default standard vertical roman
        # alignment; punctuation_position never right-aligns them (fork also
        # centers PAUSEORSTOP regardless of the position setting).
        self.assertTrue(layout.centers_vertical_glyph("。"))
        layout.punctuation_position = PunctuationPosition.Simplified
        self.assertTrue(layout.centers_vertical_glyph("。"))
        layout.punctuation_position = PunctuationPosition.Traditional
        self.assertTrue(layout.centers_vertical_glyph("。"))
        # Non-standard vertical roman alignment sends stop marks upper-right.
        layout.fontformat.standard_vertical_roman_alignment = False
        self.assertFalse(layout.centers_vertical_glyph("。"))
        # Alignment-center marks always center regardless of position.
        self.assertTrue(layout.centers_vertical_glyph("·"))

    def test_tate_chu_yoko_injection(self):
        _doc, layout = _make_layout("ABC 中文 abc")
        layout.tatechuyoko_threshold = 3
        layout.reLayout()
        ranges = layout.text_combine_ranges[0]
        lengths = {
            start: length for start, length, _group_id in ranges
        }
        self.assertIn(0, lengths)
        self.assertIn(7, lengths)
        self.assertEqual(lengths[0], 3)
        self.assertEqual(lengths[7], 3)

    def test_tate_chu_yoko_disabled(self):
        _doc, layout = _make_layout("ABC 中文 abc")
        layout.tatechuyoko_threshold = 0
        layout.reLayout()
        ranges = layout.text_combine_ranges[0]
        self.assertEqual(ranges, ())

    def test_auto_runs_do_not_overlap_annotations(self):
        # An explicit <tcy> annotation over the same span must win over the
        # automatic detector's identical run.
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QTextCursor, QTextDocument

        from ui.text_engine.annotations import AnnotationProperty

        doc = QTextDocument()
        doc.setPlainText("AB 测试")
        cursor = QTextCursor(doc)
        cursor.setPosition(0)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        fmt = cursor.charFormat()
        fmt.setProperty(
            int(AnnotationProperty.TEXT_COMBINE_UPRIGHT), "all"
        )
        cursor.mergeCharFormat(fmt)
        fontformat = FontFormat(vertical=True)
        layout = VerticalTextDocumentLayout(doc, fontformat)
        doc.setDocumentLayout(layout)
        layout.relayout_on_changed = False
        layout.setMaxSize(200, 200, relayout=False)
        layout.reLayoutEverything()
        layout.tatechuyoko_threshold = 2
        layout.reLayout()
        ranges = layout.text_combine_ranges[0]
        # Only one range owns [0, 2); the auto detector must not double-book.
        owners = [
            group_id
            for start, length, group_id in ranges
            if start == 0
        ]
        self.assertEqual(len(owners), 1)

    def test_corner_bracket_member_default(self):
        _doc, layout = _make_layout("「」")
        self.assertIsInstance(
            layout.halfwidth_jp_corner_brackets, bool
        )

    def test_punset_corner_bracket_constants(self):
        self.assertTrue("「" in PUNSET_CORNER_BRACKET)
        self.assertTrue("』" in PUNSET_CORNER_BRACKET)
        self.assertTrue("·" in PUNSET_ALIGNCENTER)

    def test_full_feature_vertical_render(self):
        """Render a vertical document with TCY + punctuation + corner
        brackets in every position mode without raising."""
        from qtpy.QtCore import QRectF
        from qtpy.QtGui import (
            QAbstractTextDocumentLayout,
            QImage,
            QPainter,
            QTextDocument,
        )

        doc = QTextDocument()
        doc.setPlainText("「AB」テスト。！abc")
        fontformat = FontFormat(vertical=True)
        layout = VerticalTextDocumentLayout(doc, fontformat)
        doc.setDocumentLayout(layout)
        layout.relayout_on_changed = False
        layout.setMaxSize(160, 160, relayout=False)
        layout.tatechuyoko_threshold = 3
        layout.halfwidth_jp_corner_brackets = True
        layout.reLayoutEverything()
        for position in (
            PunctuationPosition.Simplified,
            PunctuationPosition.Traditional,
        ):
            layout.punctuation_position = position
            image = QImage(160, 160, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            painter = QPainter(image)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.clip = QRectF(0, 0, 160, 160)
            layout.draw(painter, context)
            painter.end()
            self.assertFalse(image.isNull())


class TestVerticalEditSelectionRender(unittest.TestCase):
    """双击编辑 + 选区时竖排绘制路径不得崩溃。

    钉住 v1.5.12 移植缺口：``draw_slanted_line`` 的
    ``background_overlays``/``horizontal_shifts`` 参数曾缺失，竖排选区
    背景经 ``_vertical_selection_backgrounds`` 传入时抛 TypeError。
    """

    @classmethod
    def setUpClass(cls):
        from qtpy.QtGui import QImage, QPainter, QTextCursor
        from qtpy.QtWidgets import QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.QImage = QImage
        cls.QPainter = QPainter
        cls.QTextCursor = QTextCursor
        cls.QGraphicsScene = QGraphicsScene
        cls.app = QApplication.instance() or QApplication([])

    def _render_editing_item(self, vertical: bool) -> None:
        from utils.textblock import TextBlock

        xyxy = [100, 100, 260, 420] if vertical else [100, 100, 500, 220]
        blk = TextBlock(xyxy=list(xyxy), translation="縦書きテスト文字列")
        blk._bounding_rect = list(xyxy)
        blk.vertical = vertical
        blk.fontformat.vertical = vertical
        scene = self.QGraphicsScene()
        item = self.TextBlkItem(blk=blk, idx=0)
        scene.addItem(item)
        item.startEdit()
        cursor = item.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(4, self.QTextCursor.MoveMode.KeepAnchor)
        item.setTextCursor(cursor)
        image = self.QImage(600, 700, self.QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        painter = self.QPainter(image)
        try:
            scene.render(painter)
        finally:
            painter.end()
        self.assertFalse(image.isNull())

    def test_vertical_selection_render(self):
        self._render_editing_item(vertical=True)

    def test_horizontal_selection_render(self):
        self._render_editing_item(vertical=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
