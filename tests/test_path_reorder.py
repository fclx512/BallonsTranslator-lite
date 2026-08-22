"""Offscreen regression tests for the canvas path-reorder stroke.

The first port of the reorder gesture (upstream v1.5.12, node 4b) crashed on
the very first mouse move after a click:

    QGraphicsScene::addItem: item has already been added to this scene
    ...
    TypeError: unhashable type: 'TextBlock'          # ui/canvas.py:1227

Two root causes, both fixed on 2026-08-21:

- touched-blocks bookkeeping stored ``TextBlock`` objects (a dataclass that
  defines ``__eq__`` but no ``__hash__``) inside a ``set`` — every stroke
  blew up on membership.  It now stores ``TextBlkItem`` instances, which are
  hashable by identity (same as upstream's ``_path_reorder_touched``).
- the stroked-preview ``QGraphicsPathItem`` was added to the scene twice
  (``setParentItem(textLayer)`` already inserts it) — the redundant
  ``addItem`` drew the duplicate-add warning.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_path_reorder.py
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from qtpy.QtGui import QMouseEvent, QPointingDevice  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy, translation="测试文字"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class TestPathReorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas

        cls.Canvas = Canvas
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.textitem import TextBlkItem

        canvas = self.Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1  # text editor tab
        canvas.alignment_enabled = False
        self.canvas = canvas
        self.gv = canvas.gv
        self.gv.setScene(canvas)
        canvas.setSceneRect(QRectF(0, 0, 800, 800))
        self.gv.setSceneRect(canvas.sceneRect())
        self.gv.resize(820, 820)
        self.TextBlkItem = TextBlkItem

        # Two blocks that a straight stroke from A's centre to B's centre
        # crosses in that order.
        self.rects = [(50, 50, 150, 150), (220, 50, 320, 150)]
        self.items = []
        for i, xyxy in enumerate(self.rects):
            item = TextBlkItem(blk=_make_blk(xyxy), idx=i)
            canvas.addItem(item)
            item.setParentItem(canvas.textLayer)
            self.items.append(item)
        self.a, self.b = self.items

        self.emitted = []
        canvas.reorder_path_finished.connect(self.emitted.append)
        canvas.enterReorderMode()

    def tearDown(self):
        if self.canvas._reorder_mode:
            self.canvas.exitReorderMode()

    # ── event helpers (delivered through the real view) ─────

    def _send(self, typ, scene_pos, button, buttons):
        vp = self.gv.mapFromScene(QPointF(*scene_pos))
        lx, ly = vp.x(), vp.y()
        ev = QMouseEvent(
            typ,
            QPointF(lx, ly),
            QPointF(lx + 50, ly + 50),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )
        if typ == QEvent.Type.MouseButtonPress:
            self.gv.mousePressEvent(ev)
        elif typ == QEvent.Type.MouseMove:
            self.gv.mouseMoveEvent(ev)
        elif typ == QEvent.Type.MouseButtonRelease:
            self.gv.mouseReleaseEvent(ev)

    def _stroke(self, points):
        """Press->moves->release along *points*, all with the left button."""
        h = Qt.MouseButton.LeftButton
        self._send(QEvent.Type.MouseButtonPress, points[0], h, h)
        for p in points[1:]:
            self._send(QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, h)
        self._send(
            QEvent.Type.MouseButtonRelease, points[-1], h, Qt.MouseButton.NoButton
        )

    # ── tests ────────────────────────────────────────────────

    def test_stroke_numbers_blocks_in_travel_order(self):
        # A slow drag through A then B: both must be touched, in stroke order.
        self._stroke([(100, 45), (100, 100), (270, 100), (270, 130)])
        self.assertEqual(self.canvas._reorder_touched_blocks, [self.a, self.b])
        self.assertEqual(self.a._reorder_seq, 0)
        self.assertEqual(self.b._reorder_seq, 1)
        self.assertEqual(self.emitted, [[0, 1]])  # canvas id order == idx

    def test_fast_single_frame_drag_crosses_both_blocks(self):
        # One move event from A's centre to B's centre: the segment crosses
        # both; entry-order scoring must still number them A(0) B(1).
        self._stroke([(100, 100), (270, 100)])
        self.assertEqual(self.canvas._reorder_touched_blocks, [self.a, self.b])
        self.assertEqual(self.a._reorder_seq, 0)
        self.assertEqual(self.b._reorder_seq, 1)

    def test_block_not_touched_twice_by_revisit(self):
        # Dragging through A twice in one stroke must not renumber it.
        self._stroke([(100, 45), (100, 100), (80, 100), (100, 100), (270, 100)])
        self.assertEqual(self.canvas._reorder_touched_blocks, [self.a, self.b])
        self.assertEqual(self.a._reorder_seq, 0)

    def test_preview_path_item_added_exactly_once(self):
        # The dashed preview path is a child of textLayer and must appear in
        # the scene exactly once (regression: double add drew the
        # "item has already been added to this scene" warning).
        item = self.canvas._reorder_path_item
        self.assertIsNotNone(item)
        self.assertIs(item.parentItem(), self.canvas.textLayer)
        self.assertIs(item.scene(), self.canvas)
        matches = [i for i in self.canvas.items() if i is item]
        self.assertEqual(len(matches), 1)

    def test_exit_reorder_cleans_up(self):
        self._stroke([(100, 100), (270, 100)])
        self.canvas.exitReorderMode()
        self.assertFalse(self.canvas._reorder_mode)
        self.assertIsNone(self.canvas._reorder_path_item)
        self.assertEqual(self.canvas._reorder_touched_blocks, [])
        for item in (self.a, self.b):
            self.assertEqual(item._reorder_seq, -1)
            self.assertFalse(item.isSelected())


if __name__ == "__main__":
    unittest.main(verbosity=2)