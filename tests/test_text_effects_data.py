"""Data-layer regression tests for the text effect stack (port phase B).

Pins the ``text_effects`` integration in ``utils/fontformat.py``:

- legacy payloads (no ``text_effects`` key) migrate into an equivalent
  stack; shadow/gradient stay field-owned until the phase C renderer,
- legacy view names (``opacity``/``stroke_width``/``srgb``) read and
  write through the stack transparently,
- serialization roundtrips carry both the stack and compat fields,
- an explicit ``text_effects`` payload is authoritative over legacy
  fields,
- ``utils/base_styles.py`` variant diff keeps working through the
  legacy views (``text_effects`` itself joins DIFF_FIELDS only in
  phase D, together with the effect panel that can edit it).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_text_effects_data.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils.base_styles import compute_override  # noqa: E402
from utils.fontformat import FontFormat, TextEffectStack  # noqa: E402
from utils.text_effects import primary_stroke  # noqa: E402


class TextEffectsDataTest(unittest.TestCase):
    def test_default_is_neutral_stack(self):
        f = FontFormat()
        self.assertIsInstance(f.text_effects, TextEffectStack)
        self.assertTrue(f.text_effects.is_neutral())
        self.assertEqual(f.opacity, 1.0)
        self.assertEqual(f.stroke_width, 0.0)
        self.assertEqual(f.srgb, [0, 0, 0])

    def test_legacy_payload_migrates(self):
        f = FontFormat(
            opacity=0.7,
            stroke_width=0.3,
            srgb=[255, 0, 0],
            shadow_radius=4.0,
        )
        self.assertEqual(f.opacity, 0.7)
        self.assertEqual(f.stroke_width, 0.3)
        self.assertEqual(f.srgb, [255, 0, 0])
        # 阴影在阶段 C 前保持字段本体
        self.assertEqual(f.shadow_radius, 4.0)
        stroke = primary_stroke(f.text_effects)
        self.assertIsNotNone(stroke)
        self.assertEqual(stroke.width, 0.3)
        self.assertEqual(list(stroke.paint.color), [255, 0, 0])

    def test_legacy_views_write_through(self):
        f = FontFormat()
        f.stroke_width = 0.6
        f.opacity = 0.5
        f.srgb = [0, 255, 0]
        self.assertEqual(f.text_effects.overall_opacity, 0.5)
        stroke = primary_stroke(f.text_effects)
        self.assertEqual(stroke.width, 0.6)
        self.assertEqual(list(stroke.paint.color), [0, 255, 0])

    def test_serialization_roundtrip(self):
        f = FontFormat(opacity=0.7, stroke_width=0.3, srgb=[255, 0, 0])
        d = f.to_serializable_dict()
        self.assertEqual(d["text_effects"]["overall_opacity"], 0.7)
        self.assertEqual(d["stroke_width"], 0.3)
        self.assertEqual(d["opacity"], 0.7)
        g = FontFormat(**d)
        self.assertEqual(g.text_effects, f.text_effects)
        self.assertEqual(g.stroke_width, 0.3)
        self.assertEqual(g.srgb, [255, 0, 0])

    def test_explicit_stack_is_authoritative(self):
        f = FontFormat(
            opacity=0.1,
            stroke_width=0.9,
            text_effects={"overall_opacity": 0.9, "effects": []},
        )
        self.assertEqual(f.opacity, 0.9)

    def test_merge_and_deepcopy(self):
        a = FontFormat(opacity=0.7, stroke_width=0.2)
        b = FontFormat(opacity=0.4, stroke_width=0.8)
        changed = a.merge(b, compare=True)
        self.assertIn("text_effects", changed)
        self.assertIn("opacity", changed)
        self.assertEqual(a.opacity, 0.4)
        self.assertEqual(a.stroke_width, 0.8)
        c = a.deepcopy()
        c.stroke_width = 0.99
        self.assertEqual(a.stroke_width, 0.8)
        self.assertEqual(c.stroke_width, 0.99)

    def test_base_styles_diff_via_legacy_views(self):
        # 阶段 B：diff/聚类仍走 opacity/stroke_width/srgb 视图，
        # text_effects 本身不进 DIFF_FIELDS（避免与视图关联重复）。
        from utils.base_styles import (
            DIFF_FIELDS,
            compute_override,
        )

        self.assertNotIn("text_effects", DIFF_FIELDS)
        base = FontFormat()
        self.assertEqual(compute_override(FontFormat(), base), {})
        overrides = compute_override(
            FontFormat(opacity=0.5, stroke_width=0.2), base
        )
        self.assertIn("opacity", overrides)
        self.assertIn("stroke_width", overrides)
        self.assertNotIn("text_effects", overrides)


if __name__ == "__main__":
    unittest.main()
