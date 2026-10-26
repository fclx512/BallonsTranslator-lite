"""字形掩膜字号估计：字距、空格、膨胀修正、填充行跳过与页内规范字号。"""
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from utils.font_size_estimate import (
    BlockEstimate,
    apply_font_sizes,
    apply_mask_font_sizes_from_detector,
    attach_glyph_font_metrics,
    estimate_block,
    page_norm,
    params_from_detector,
    snap_estimates,
    weighted_char_count,
)


def _make_mask(em, chars, vertical=True, spacing=0.0, ink_ratio=0.9, line_scale=1.6):
    advances = []
    for ch in chars:
        if ch == "\u3000":
            advances.append(float(em))
        elif ch == " ":
            advances.append(em * 0.5)
        else:
            advances.append(em * (1.0 + spacing))
    total = sum(advances)
    pad = int(em * 0.2)
    ink_w = max(1, int(em * ink_ratio))
    if vertical:
        line_w = int(em * line_scale)
        line_h = int(total + 2 * pad)
        mask = np.zeros((line_h, line_w), np.uint8)
        pos = pad
        for ch, advance in zip(chars, advances):
            if ch not in " \u3000":
                x = (line_w - ink_w) // 2
                y = int(pos + (advance - ink_w) / 2)
                cv2.rectangle(mask, (x, y), (x + ink_w - 1, y + ink_w - 1), 255, -1)
            pos += advance
        line = [[0, 0], [line_w - 1, 0], [line_w - 1, line_h - 1], [0, line_h - 1]]
    else:
        line_w = int(total + 2 * pad)
        line_h = int(em * line_scale)
        mask = np.zeros((line_h, line_w), np.uint8)
        pos = pad
        for ch, advance in zip(chars, advances):
            if ch not in " \u3000":
                y = (line_h - ink_w) // 2
                x = int(pos + (advance - ink_w) / 2)
                cv2.rectangle(mask, (x, y), (x + ink_w - 1, y + ink_w - 1), 255, -1)
            pos += advance
        line = [[0, 0], [line_w - 1, 0], [line_w - 1, line_h - 1], [0, line_h - 1]]
    return mask, line


def _block(line, text, vertical=True):
    return SimpleNamespace(
        lines=[line], text=[text], src_is_vertical=vertical,
        font_size=0.0, _detected_font_size=0.0,
    )


class WeightedCharCountTest(unittest.TestCase):
    def test_space_widths(self):
        self.assertEqual(weighted_char_count("あ\u3000い"), 3.0)
        self.assertEqual(weighted_char_count("あ い"), 2.5)
        self.assertEqual(weighted_char_count("\u3000あ い\u3000"), 2.5)

    def test_newlines_are_not_chars(self):
        self.assertEqual(weighted_char_count("あ\nい"), 2.0)


class EstimateBlockTest(unittest.TestCase):
    def test_vertical_glyph_size(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        estimate = estimate_block(_block(line, "あいうえお"), mask)
        self.assertIsNotNone(estimate.em)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_horizontal_glyph_size(self):
        em = 48
        mask, line = _make_mask(em, "あいうえお", vertical=False)
        estimate = estimate_block(_block(line, "あいうえお", vertical=False), mask)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_letter_spacing_does_not_inflate(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお", spacing=0.5)
        estimate = estimate_block(_block(line, "あいうえお"), mask)
        # 0.5em 字距会把长度口径抬高到 ~56px；短边口径仍应给出 ~40px。
        self.assertGreater(estimate.em_len, estimate.em_short * 1.15)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_full_width_space_counts_as_one(self):
        em = 40
        # 把字形画得比 em 宽，让 em_short 偏大，才能看出空格计数的效果。
        mask, line = _make_mask(em, "あ\u3000い", ink_ratio=1.05, line_scale=2.2)
        estimate = estimate_block(_block(line, "あ\u3000い"), mask)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_half_width_space_counts_as_half(self):
        em = 40
        mask, line = _make_mask(em, "あ い", ink_ratio=1.05, line_scale=2.2)
        estimate = estimate_block(_block(line, "あ い"), mask)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_dilation_is_removed(self):
        em = 60
        mask, line = _make_mask(em, "あいうえお", line_scale=2.2)
        dilated = cv2.dilate(mask, np.ones((17, 17), np.uint8))
        raw_estimate = estimate_block(_block(line, "あいうえお"), mask, dilate=0)
        dilated_estimate = estimate_block(
            _block(line, "あいうえお"), dilated, dilate=8
        )
        self.assertIsNotNone(dilated_estimate.em)
        self.assertLess(
            abs(dilated_estimate.em - raw_estimate.em) / raw_estimate.em, 0.08
        )

    def test_filled_line_is_rejected(self):
        mask, line = _make_mask(40, "あいうえお")
        filled = np.zeros_like(mask)
        filled[:, :] = 255
        estimate = estimate_block(_block(line, "あいうえお"), filled)
        self.assertIsNone(estimate.em)

    def test_empty_mask_is_rejected(self):
        _, line = _make_mask(40, "あいうえお")
        estimate = estimate_block(_block(line, "あいうえお"), np.zeros((100, 100), np.uint8))
        self.assertIsNone(estimate.em)


class GlyphMetricsTest(unittest.TestCase):
    def test_attached_metrics_work_without_mask(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        self.assertEqual(attach_glyph_font_metrics([block], mask), 1)
        estimate = estimate_block(block, None)
        self.assertIsNotNone(estimate.em)
        self.assertLess(abs(estimate.em - em) / em, 0.10)

    def test_metrics_invalidated_when_lines_move(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        attach_glyph_font_metrics([block], mask)
        moved = [[x + 60, y] for x, y in line]
        block.lines = [moved]
        self.assertIsNone(estimate_block(block, None).em)

    def test_partial_lines_do_not_attach(self):
        mask, line = _make_mask(40, "あいうえお")
        block = _block(line, "あいうえお")
        block.lines = [line, [[500, 500], [510, 500], [510, 510], [500, 510]]]
        self.assertEqual(attach_glyph_font_metrics([block], mask), 0)
        self.assertFalse(hasattr(block, "_glyph_font_metrics"))

    def test_metrics_not_serialized(self):
        from utils.textblock import TextBlock

        mask, line = _make_mask(40, "あいうえお")
        block = TextBlock(lines=[line], text=["あいうえお"])
        attach_glyph_font_metrics([block], mask)
        self.assertNotIn("_glyph_font_metrics", block.to_dict())


class PageNormTest(unittest.TestCase):
    def _est(self, em, chars):
        return BlockEstimate(block=None, em=em, em_short=em, em_len=em, chars=chars)

    def test_long_small_block_is_preserved(self):
        estimates = [
            self._est(50, 10), self._est(50, 11), self._est(50, 9),
            self._est(34, 12), self._est(47, 4),
        ]
        norm = page_norm(estimates)
        snap_estimates(estimates, norm)
        self.assertAlmostEqual(estimates[0].final_em, 50, delta=1)
        self.assertAlmostEqual(estimates[3].final_em, 34, delta=1)  # 长文本有意缩小
        self.assertAlmostEqual(estimates[4].final_em, 50, delta=1)  # 短块吸附
        self.assertTrue(estimates[4].snapped)

    def test_two_tiers_are_kept(self):
        estimates = [
            self._est(33, 10), self._est(33, 9),
            self._est(55, 12), self._est(55, 11),
            self._est(54, 4),
        ]
        norm = page_norm(estimates)
        snap_estimates(estimates, norm)
        self.assertTrue(any(abs(mode - 33) < 2 for mode in norm.modes))
        self.assertTrue(any(abs(mode - 55) < 2 for mode in norm.modes))
        self.assertAlmostEqual(estimates[2].final_em, 55, delta=1)
        self.assertAlmostEqual(estimates[4].final_em, 55, delta=1)

    def test_too_few_blocks_has_no_norm(self):
        estimates = [self._est(50, 10)]
        self.assertFalse(page_norm(estimates).modes)


class ApplyTest(unittest.TestCase):
    def test_apply_writes_size_and_respects_limits(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        apply_font_sizes([block], mask, scale=1.1, min_size=30, max_size=45,
                         per_page_norm=False)
        self.assertGreater(block.font_size, 0)
        self.assertLessEqual(block.font_size, 45)
        self.assertEqual(block.font_size, block._detected_font_size)

    def test_no_mask_is_noop(self):
        em = 40
        _, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        apply_font_sizes([block], None)
        self.assertEqual(block.font_size, 0)

    def test_detector_capability_gate(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        plain = SimpleNamespace(params={})
        advanced = SimpleNamespace(
            provides_glyph_mask=True,
            params={
                "mask dilate size": {"value": 0},
                "font size multiplier": {"value": 1.0},
                "font size min": {"value": 0.0},
                "font size max": {"value": 0.0},
            },
        )
        self.assertEqual(apply_mask_font_sizes_from_detector([block], mask, plain), [])
        self.assertEqual(block.font_size, 0)
        self.assertTrue(apply_mask_font_sizes_from_detector([block], mask, advanced))
        self.assertGreater(block.font_size, 0)

    def test_metrics_allow_none_mask(self):
        em = 40
        mask, line = _make_mask(em, "あいうえお")
        block = _block(line, "あいうえお")
        attach_glyph_font_metrics([block], mask)
        advanced = SimpleNamespace(
            provides_glyph_mask=True,
            params={
                "mask dilate size": {"value": 0},
                "font size multiplier": {"value": 1.0},
                "font size min": {"value": 0.0},
                "font size max": {"value": 0.0},
            },
        )
        self.assertTrue(apply_mask_font_sizes_from_detector([block], None, advanced))
        self.assertGreater(block.font_size, 0)

    def test_params_from_detector(self):
        detector = SimpleNamespace(params={
            "mask dilate size": {"value": 8},
            "font size multiplier": {"value": 1.2},
            "font size min": {"value": 20},
            "font size max": {"value": 80},
        })
        self.assertEqual(params_from_detector(detector), {
            "dilate": 8, "scale": 1.2, "min_size": 20.0, "max_size": 80.0,
        })


if __name__ == "__main__":
    unittest.main()
