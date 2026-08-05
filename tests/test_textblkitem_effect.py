"""Effect-layer tests for TextBlkItem after the Stage 3 renderer port.

Locks the neutral-state (identity transform) effect semantics now owned by
``ui/text_engine/effect_renderer.TextEffectRenderer``:

- stroke and shadow are composited into ``effect_renderer.background_pixmap``
- ``shadow_include_stroke`` switches the shadow source between glyph-only and
  glyph+stroke (PS drop-shadow behavior)
- the persistent gradient path (document foreground) and the transient
  FormatRange injection path both resolve the same 2-stop gradient
- neutral-state ``surface_cache_state()`` stays ``(0, False)`` until a
  transform becomes active
- effect padding commits leave the logical ``absBoundingRect`` untouched and
  add at most one document undo step per value increase (Qt records
  ``setDocumentMargin`` as undoable; the grow-only guard keeps repeated
  commits idempotent)
- ``release_caches()`` drops every item-owned raster cache

Pixel assertions use ``ui.misc.pixmap2ndarray`` (RGBA8888, shape ``(h, w, 4)``,
channel 0 = red).
"""

import os
import os.path as osp
import sys
import unittest

import numpy as np

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"


class TextBlkItemEffectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtCore import QRectF
        from qtpy.QtGui import QColor, QImage, QPainter
        from qtpy.QtWidgets import QApplication, QGraphicsScene

        from ui import misc
        from ui.textitem import TextBlkItem

        cls.QRectF = QRectF
        cls.QColor, cls.QImage, cls.QPainter = QColor, QImage, QPainter
        cls.QApplication, cls.QGraphicsScene = QApplication, QGraphicsScene
        cls.TextBlkItem = TextBlkItem
        cls.misc = misc
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def setUp(self):
        self.scene.clear()

    def _new_item(self, xyxy=(100, 100, 400, 220), translation="测试文字", **ff):
        """Build a TextBlkItem whose fontformat carries the given overrides.

        The overrides are applied to ``blk.fontformat`` *before* the item is
        constructed so the constructor's stroke/shadow/gradient wiring sees
        them (e.g. ``stroke_qcolor`` is snapshotted from ``srgb``).
        """
        from utils.textblock import TextBlock

        blk = TextBlock(xyxy=list(xyxy), translation=translation)
        blk._bounding_rect = list(xyxy)
        for key, value in ff.items():
            setattr(blk.fontformat, key, value)
        item = self.TextBlkItem(blk=blk, idx=0)
        self.scene.addItem(item)
        return item, blk

    def _bg_array(self, item):
        """Return the renderer's neutral background as a uint8 RGBA array."""
        pm = item.effect_renderer.background_pixmap
        self.assertIsNotNone(pm, "background_pixmap was not produced")
        return self.misc.pixmap2ndarray(pm)

    # ── stroke lands in background_pixmap ────────────────────────────────

    def test_stroke_renders_into_background_pixmap(self):
        item, _ = self._new_item(
            font_size=60, stroke_width=0.1, srgb=[255, 0, 0], frgb=[255, 255, 255]
        )
        arr = self._bg_array(item)
        # RGBA8888: channel 0 = red, 1 = green, 2 = blue. Glyphs are painted
        # by the item's own text pass, so red pixels here can only be stroke.
        red = (arr[..., 0] > 200) & (arr[..., 1] < 100) & (arr[..., 2] < 100)
        self.assertGreater(int(red.sum()), 100, "expected red stroke pixels")

    # ── shadow renders and shadow_include_stroke changes the source ──────

    def test_shadow_renders_and_include_stroke_differs(self):
        kwargs = dict(
            font_size=48,
            stroke_width=0.1,
            srgb=[255, 0, 0],
            frgb=[255, 255, 255],
            shadow_radius=0.1,
            shadow_strength=1.0,
            shadow_color=[0, 0, 0],
            shadow_offset=[0.1, 0.1],
        )
        # shadow_include_stroke defaults to False → glyph-only shadow source.
        item, blk = self._new_item(**kwargs)
        arr_glyph_only = self._bg_array(item)

        def _dark(arr):
            dark_rgb = (arr[..., 0] < 60) & (arr[..., 1] < 60) & (arr[..., 2] < 60)
            return dark_rgb & (arr[..., 3] > 30)

        # Glyphs are white and stroke is red, so near-black pixels can only
        # come from the black drop shadow.
        self.assertGreater(
            int(_dark(arr_glyph_only).sum()), 200, "expected shadow pixels"
        )

        # shadow_include_stroke=True → shadow source is glyph+stroke.
        blk.fontformat.shadow_include_stroke = True
        item.repaint_background()
        arr_stroke_included = self._bg_array(item)
        # The stroke ring now casts shadow, so the composites must differ.
        self.assertFalse(np.array_equal(arr_glyph_only, arr_stroke_included))

    # ── gradient: persistent + transient paths ────────────────────────────

    def test_gradient_persistent_and_transient(self):
        from ui.text_engine.effect_renderer import GRADIENT_LAYOUT_FORMAT_PROPERTY

        item, _ = self._new_item(
            font_size=48,
            gradient_enabled=True,
            gradient_start_color=[10, 20, 30],
            gradient_end_color=[200, 210, 220],
        )
        grad = item.get_text_gradient()
        # PyQt6 QLinearGradient has no colorAt(); stops() returns [(pos, QColor)].
        stops = grad.stops()
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0], (0.0, self.QColor(10, 20, 30)))
        self.assertEqual(stops[1], (1.0, self.QColor(200, 210, 220)))

        renderer = item.effect_renderer
        # Neutral state never installs transient ranges.
        renderer._refresh_gradient_geometry()
        self.assertFalse(renderer.has_transient_gradient_ranges)

        # Non-neutral state injects the gradient as a transient FormatRange.
        original = renderer._text_transform_is_neutral
        renderer._text_transform_is_neutral = lambda: False
        try:
            renderer._refresh_gradient_geometry()
        finally:
            renderer._text_transform_is_neutral = original
        self.assertTrue(renderer.has_transient_gradient_ranges)
        block = item.document().firstBlock()
        self.assertTrue(
            any(
                fr.format.property(GRADIENT_LAYOUT_FORMAT_PROPERTY)
                for fr in block.layout().formats()
            )
        )

    # ── neutral surface_cache_state stays (0, False) ─────────────────────

    def test_surface_cache_state_neutral(self):
        item, _ = self._new_item(stroke_width=0.1, srgb=[255, 0, 0])
        self.assertEqual(item.effect_renderer.surface_cache_state(), (0, False))

    # ── effect padding: grow-only, single undo step, rect stable ─────────

    def test_effect_padding_grow_only_rect_stable(self):
        item, _ = self._new_item()
        renderer = item.effect_renderer
        doc = item.document()
        abr_before = item.absBoundingRect(qrect=True)
        padding_before = item.padding()
        undo_before = doc.availableUndoSteps()

        # Neutral repaint never commits effect padding.
        item.repaint_background()
        self.assertEqual(item.padding(), padding_before)
        self.assertEqual(item.absBoundingRect(qrect=True), abr_before)
        self.assertEqual(doc.availableUndoSteps(), undo_before)

        # _commit_effect_padding is grow-only and leaves the logical rect alone.
        self.assertTrue(renderer._commit_effect_padding(5.0))
        self.assertAlmostEqual(item.padding(), 5.0, delta=1e-6)
        self.assertEqual(item.absBoundingRect(qrect=True), abr_before)
        # Qt records the documentMargin change as exactly one undo step…
        self.assertEqual(doc.availableUndoSteps(), undo_before + 1)
        # …and the grow-only guard makes repeated commits idempotent.
        self.assertFalse(renderer._commit_effect_padding(5.0))
        self.assertEqual(doc.availableUndoSteps(), undo_before + 1)

    # ── fontformat combo paint smoke ─────────────────────────────────────

    def test_fontformat_combo_paint_smoke(self):
        item, _ = self._new_item(
            font_size=48,
            stroke_width=0.1,
            srgb=[255, 0, 0],
            frgb=[255, 255, 255],
            shadow_radius=0.08,
            shadow_strength=1.0,
            shadow_color=[0, 0, 0],
            gradient_enabled=True,
            gradient_start_color=[255, 0, 0],
            gradient_end_color=[0, 0, 255],
        )
        item.repaint_background()
        arr = self._bg_array(item)
        self.assertGreater(int((arr[..., 3] > 0).sum()), 1000)

        image = self.QImage(600, 400, self.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(self.QColor(255, 255, 255))
        painter = self.QPainter(image)
        try:
            self.scene.render(painter, target=self.QRectF(0, 0, 600, 400))
        finally:
            painter.end()
        self.assertFalse(image.isNull())

    # ── release_caches drops every raster cache ──────────────────────────

    def test_release_clears_caches(self):
        item, _ = self._new_item(stroke_width=0.1, srgb=[255, 0, 0])
        renderer = item.effect_renderer
        self.assertIsNotNone(renderer.background_pixmap)
        renderer.release_caches()
        self.assertIsNone(renderer.background_pixmap)
        self.assertIsNone(renderer.background_pixmap_scale)
        self.assertEqual(renderer.surface_cache_state(), (0, False))


if __name__ == "__main__":
    unittest.main()
