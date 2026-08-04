"""Geometry tests for TextBlkItem after the Stage 2 controller port.

Locks the neutral-state (identity transform) geometry semantics of
TextBlkItem so the controller refactor (ui/text_engine/geometry.py)
cannot regress local behavior.

In particular this locks the local ``set_size`` alignment semantics:
Left and Center both keep the scene bounding rect *centered* (no edge
compensation), which differs from upstream v1.5.9 where Left alignment
also compensates so the top-left corner stays put.
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


def _make_blk(xyxy, alignment):
    from utils.fontformat import TextAlignment
    from utils.textblock import TextBlock

    blk = TextBlock(xyxy=list(xyxy), translation="测试文字")
    blk.fontformat.alignment = TextAlignment(alignment)
    blk._bounding_rect = list(xyxy)
    return blk


class TextBlkItemGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtCore import QPointF, QRectF, QSizeF
        from qtpy.QtGui import QColor, QImage, QPainter
        from qtpy.QtWidgets import (
            QApplication,
            QGraphicsScene,
            QStyleOptionGraphicsItem,
        )

        from ui.textitem import TextBlkItem

        cls.QPointF, cls.QRectF, cls.QSizeF = QPointF, QRectF, QSizeF
        cls.QColor, cls.QImage, cls.QPainter = QColor, QImage, QPainter
        cls.QStyleOptionGraphicsItem = QStyleOptionGraphicsItem
        cls.QApplication, cls.QGraphicsScene = QApplication, QGraphicsScene
        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _new_item(self, xyxy=(100, 100, 300, 200), alignment=1):
        """Create a TextBlkItem (blk alignment = TextAlignment(alignment))."""
        blk = _make_blk(xyxy, alignment)
        item = self.TextBlkItem(blk=blk, idx=0)
        self.scene.addItem(item)
        return item, blk

    # ── setRect → boundingRect / absBoundingRect round trip ─────────────

    def test_set_rect_round_trip(self):
        item, _ = self._new_item()
        item.setRect(self.QRectF(120, 80, 220, 140))
        # Zero padding: display rect == logical rect.
        self.assertEqual(item.boundingRect(), self.QRectF(0, 0, 220, 140))
        self.assertEqual(item.pos(), self.QPointF(120, 80))
        self.assertEqual(item.absBoundingRect(qrect=True), self.QRectF(120, 80, 220, 140))
        self.assertEqual(item.absBoundingRect(), [120, 80, 220, 140])

    # ── set_size keeps the sceneBoundingRect center (Center alignment) ──

    def test_set_size_center_keeps_center(self):
        for angle in (0.0, 30.0):
            item, _ = self._new_item()
            item.setRect(self.QRectF(100, 100, 200, 100))
            if angle:
                item.setRotation(angle)
            before = item.sceneBoundingRect().center()
            item.set_size(300, 150)
            after = item.sceneBoundingRect().center()
            self.assertAlmostEqual(before.x(), after.x(), delta=1e-6)
            self.assertAlmostEqual(before.y(), after.y(), delta=1e-6)

    # ── Left alignment must behave exactly like Center (local semantics) ──
    # Upstream v1.5.9 compensates for Left alignment so the top-left corner
    # stays fixed (center moves); local behavior keeps the center fixed for
    # both Left and Center. This test fails if upstream compensation leaks in.

    def test_set_size_left_matches_center(self):
        item_l, _ = self._new_item(alignment=0)  # Left
        item_c, _ = self._new_item(alignment=1)  # Center
        for it in (item_l, item_c):
            it.setRect(self.QRectF(100, 100, 200, 100))
        c_l0 = item_l.sceneBoundingRect().center()
        c_c0 = item_c.sceneBoundingRect().center()
        self.assertAlmostEqual(c_l0.x(), c_c0.x(), delta=1e-6)
        self.assertAlmostEqual(c_l0.y(), c_c0.y(), delta=1e-6)
        item_l.set_size(300, 150)
        item_c.set_size(300, 150)
        c_l1 = item_l.sceneBoundingRect().center()
        c_c1 = item_c.sceneBoundingRect().center()
        # Left keeps its own scene center…
        self.assertAlmostEqual(c_l1.x(), c_l0.x(), delta=1e-6)
        self.assertAlmostEqual(c_l1.y(), c_l0.y(), delta=1e-6)
        # …and is bit-identical to the Center behavior.
        self.assertAlmostEqual(c_l1.x(), c_c1.x(), delta=1e-6)
        self.assertAlmostEqual(c_l1.y(), c_c1.y(), delta=1e-6)

    # ── _text_overflows clamps set_size both ways ───────────────────────

    def test_text_overflows_clamps_resize(self):
        item, _ = self._new_item()
        item.setRect(self.QRectF(100, 100, 200, 100))
        item._text_overflows = True
        item.set_size(300, 150)  # grow → clamped
        self.assertEqual(item._display_rect.size(), self.QSizeF(200, 100))
        item.set_size(50, 50)  # shrink → clamped
        self.assertEqual(item._display_rect.size(), self.QSizeF(200, 100))

    # ── setRectFast fast path ───────────────────────────────────────────

    def test_set_rect_fast(self):
        item, _ = self._new_item()
        item.setRectFast(self.QRectF(10, 10, 150, 80))
        self.assertEqual(item.pos(), self.QPointF(10, 10))
        self.assertEqual(item._display_rect.size(), self.QSizeF(150, 80))

    # ── squeezeBoundingRect syncs blk._bounding_rect ────────────────────

    def test_squeeze_bounding_rect_syncs_model(self):
        item, blk = self._new_item()
        item.setRect(self.QRectF(100, 100, 300, 150))
        item.squeezeBoundingRect(repaint=False)
        self.assertIsNotNone(blk._bounding_rect)
        self.assertEqual(list(blk._bounding_rect), item.absBoundingRect())

    # ── padding round trip ──────────────────────────────────────────────

    def test_padding_round_trip(self):
        item, _ = self._new_item()
        item.setRect(self.QRectF(100, 100, 200, 100))
        self.assertEqual(item.padding(), 0.0)
        item.setPadding(5.0)
        self.assertEqual(item.padding(), 5.0)
        abr = item.absBoundingRect(qrect=True)
        self.assertAlmostEqual(abr.x(), 100.0, delta=1e-6)
        self.assertAlmostEqual(abr.y(), 100.0, delta=1e-6)
        self.assertAlmostEqual(abr.width(), 200.0, delta=1e-6)
        self.assertAlmostEqual(abr.height(), 100.0, delta=1e-6)

    # ── paint smoke (full paint path must not crash) ────────────────────

    def test_paint_smoke(self):
        item, _ = self._new_item()
        item.setRect(self.QRectF(50, 50, 200, 100))
        image = self.QImage(400, 300, self.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(self.QColor(255, 255, 255))
        painter = self.QPainter(image)
        try:
            self.scene.render(painter, target=self.QRectF(0, 0, 400, 300))
        finally:
            painter.end()
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
