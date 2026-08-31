"""Effect-layer tests for TextBlkItem after the effects-stack renderer port.

Locks the effects-stack rendering semantics now owned by
``ui/text_engine/effects/renderer.TextEffectRenderer``:

- stroke and shadow are composited into ``effect_renderer.background_pixmap``
  (both migrated from legacy fields into the ``text_effects`` stack)
- legacy shadow/gradient fields read and write through the fontformat views
  onto stack cards, preserving the legacy render gating (radius × strength)
- ``shadow_include_stroke`` maps to card order (shadow above/below the
  primary stroke)
- neutral-state ``surface_cache_state()`` stays in the committed namespace
  until a transform becomes active
- effect padding commits leave the logical ``absBoundingRect`` untouched
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

    # ── gradient: legacy fields map onto the stack Text Fill card ─────────

    def test_gradient_view_mapping(self):
        item, blk = self._new_item(
            font_size=48,
            gradient_enabled=True,
            gradient_start_color=[10, 20, 30],
            gradient_end_color=[200, 210, 220],
            gradient_angle=30.0,
            gradient_size=1.2,
        )
        ffmat = blk.fontformat
        # Field reads resolve through the stack: one LinearGradientPaint
        # Text Fill card with legacy angle/size mapping (scale = 2 × size).
        fill = next(
            effect
            for effect in ffmat.text_effects.effects
            if effect.effect_type == "text_fill"
        )
        self.assertEqual(fill.paint.stops[0].color, (10, 20, 30))
        self.assertEqual(fill.paint.stops[-1].color, (200, 210, 220))
        self.assertAlmostEqual(fill.paint.angle, 30.0)
        self.assertAlmostEqual(fill.paint.scale, 2.4)
        self.assertEqual(ffmat.gradient_start_color, [10, 20, 30])
        self.assertEqual(ffmat.gradient_end_color, [200, 210, 220])
        self.assertAlmostEqual(ffmat.gradient_angle, 30.0)
        self.assertAlmostEqual(ffmat.gradient_size, 1.2)

        # Field writes mutate the same card (new immutable card each time).
        ffmat.gradient_angle = 95.0
        ffmat.gradient_size = 0.8
        fill = next(
            effect
            for effect in ffmat.text_effects.effects
            if effect.effect_type == "text_fill"
        )
        self.assertAlmostEqual(fill.paint.angle, 95.0)
        self.assertAlmostEqual(ffmat.gradient_size, 0.8)
        self.assertAlmostEqual(fill.paint.scale, 1.6)

        # Disabling removes the fill; reads fall back to field defaults.
        ffmat.gradient_enabled = False
        self.assertFalse(
            any(
                effect.effect_type == "text_fill"
                for effect in ffmat.text_effects.effects
            )
        )
        self.assertFalse(ffmat.gradient_enabled)
        self.assertEqual(ffmat.gradient_start_color, [0, 0, 0])

    # ── shadow: legacy fields map onto the stack Shadow card ──────────────

    def test_shadow_view_mapping(self):
        item, blk = self._new_item(
            font_size=48,
            stroke_width=0.1,
            srgb=[255, 0, 0],
            shadow_radius=0.08,
            shadow_strength=0.7,
            shadow_color=[5, 6, 7],
            shadow_offset=[0.03, 0.04],
        )
        ffmat = blk.fontformat
        shadow = next(
            effect
            for effect in ffmat.text_effects.effects
            if effect.effect_type == "shadow"
        )
        self.assertAlmostEqual(shadow.blur, 0.08)
        self.assertAlmostEqual(shadow.opacity, 0.7)
        self.assertEqual(shadow.paint.color, (5, 6, 7))
        self.assertAlmostEqual(shadow.distance, (0.05 ** 2) ** 0.5)
        self.assertAlmostEqual(ffmat.shadow_radius, 0.08)
        self.assertAlmostEqual(ffmat.shadow_strength, 0.7)
        self.assertEqual(ffmat.shadow_color, [5, 6, 7])
        self.assertAlmostEqual(ffmat.shadow_offset[0], 0.03, delta=1e-6)
        self.assertAlmostEqual(ffmat.shadow_offset[1], 0.04, delta=1e-6)
        self.assertFalse(ffmat.shadow_include_stroke)

        # include_stroke=True moves the shadow card above the primary stroke.
        ffmat.shadow_include_stroke = True

        def _shadow_index():
            return next(
                index
                for index, effect in enumerate(ffmat.text_effects.effects)
                if effect.effect_type == "shadow"
            )

        def _stroke_index():
            return next(
                index
                for index, effect in enumerate(ffmat.text_effects.effects)
                if effect.effect_type == "stroke"
            )

        self.assertLess(_shadow_index(), _stroke_index())
        ffmat.shadow_include_stroke = False
        self.assertGreater(_shadow_index(), _stroke_index())

        # Radius 0 disables rendering via the card's enabled flag.
        ffmat.shadow_radius = 0.0
        shadow = next(
            effect
            for effect in ffmat.text_effects.effects
            if effect.effect_type == "shadow"
        )
        self.assertFalse(shadow.enabled)
        self.assertEqual(ffmat.shadow_radius, 0.0)

    # ── neutral surface_cache_state stays in the committed namespace ────

    def test_surface_cache_state_neutral(self):
        item, _ = self._new_item(stroke_width=0.1, srgb=[255, 0, 0])
        self.assertEqual(
            item.effect_renderer.surface_cache_state(), (("committed", 0), False)
        )

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
        # 引擎布局把 effect padding 存于布局自身状态（不用 documentMargin），
        # 提交 padding 不再产生 documentMargin 的 undo 步（fork 布局的副作用）。
        self.assertEqual(doc.availableUndoSteps(), undo_before)
        # …and the grow-only guard makes repeated commits idempotent.
        self.assertFalse(renderer._commit_effect_padding(5.0))
        self.assertEqual(doc.availableUndoSteps(), undo_before)

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

    # ── panel decoupling: legacy setters must reach the canonical stack ──

    def test_legacy_setters_reach_canonical_stack_after_panel_decoupling(self):
        """面板深拷贝解耦后，效果 setter 仍须写透 canonical 效果栈。

        ``text_panel.py::set_textblk_item`` 惯例用 deepcopy 解耦渲染格式
        （``item.fontformat``）与模型（``blk.fontformat``），而效果渲染器
        读 canonical 栈。效果 setter 经栈属主入口 ``set_text_effects``
        提交，两份格式与渲染像素必须同时跟进。
        """
        import copy

        item, blk = self._new_item(font_size=48)
        item.fontformat = copy.deepcopy(item.fontformat)
        self.assertIsNot(item.fontformat, item.blk.fontformat)

        renderer = item.effect_renderer
        renderer.repaint_background()

        def _scene_bytes():
            image = self.QImage(
                600, 400, self.QImage.Format.Format_ARGB32_Premultiplied
            )
            image.fill(self.QColor(255, 255, 255))
            painter = self.QPainter(image)
            try:
                self.scene.render(
                    painter, target=self.QRectF(0, 0, 600, 400)
                )
            finally:
                painter.end()
            return bytes(image.constBits().asarray(image.sizeInBytes()))

        baseline = _scene_bytes()

        item.setStrokeWidth(0.25)
        self.assertEqual(renderer._stroke_width(), 0.25)
        self.assertEqual(blk.fontformat.stroke_width, 0.25)
        self.assertEqual(item.fontformat.stroke_width, 0.25)

        item.setStrokeColor([255, 0, 0])
        self.assertEqual(list(blk.fontformat.srgb), [255, 0, 0])
        self.assertEqual(item.stroke_qcolor.red(), 255)

        item.setBGAttribute("shadow_radius", 0.12)
        self.assertEqual(blk.fontformat.shadow_radius, 0.12)
        item.setBGAttribute("shadow_include_stroke", True)
        self.assertTrue(blk.fontformat.shadow_include_stroke)

        item.setGradientAttribute("gradient_enabled", True)
        self.assertTrue(blk.fontformat.gradient_enabled)
        item.setGradientEnabled(False)
        self.assertFalse(blk.fontformat.gradient_enabled)

        item.setOpacity(0.75)
        self.assertEqual(blk.fontformat.opacity, 0.75)

        fmt = blk.fontformat.deepcopy()
        fmt.shadow_strength = 0.9
        item.setShadow(fmt)
        self.assertEqual(blk.fontformat.shadow_strength, 0.9)

        # 终态：描边 + 阴影 + 渐变 + 半透明，渲染像素须偏离无效果基线。
        item.setGradientEnabled(True)
        item.setGradientAttribute("gradient_start_color", [20, 40, 160])
        item.setGradientAttribute("gradient_end_color", [220, 80, 40])
        self.assertNotEqual(_scene_bytes(), baseline)

    # ── release_caches drops every raster cache ──────────────────────────

    def test_release_clears_caches(self):
        item, _ = self._new_item(stroke_width=0.1, srgb=[255, 0, 0])
        renderer = item.effect_renderer
        self.assertIsNotNone(renderer.background_pixmap)
        renderer.release_caches()
        self.assertIsNone(renderer.background_pixmap)
        self.assertIsNone(renderer.background_pixmap_scale)
        self.assertEqual(
            renderer.surface_cache_state(), (("committed", 0), False)
        )


if __name__ == "__main__":
    unittest.main()
