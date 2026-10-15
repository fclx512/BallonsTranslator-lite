"""``utils/text_mask_refine.py`` 的契约回归（纯合成图，不依赖模型/网络/配置）。

钉住会静默失效的契约：

1. 空几何种子（fill 全零）不崩、候选全空；
2. 硬不变式：prob/color/classical 一律 ⊆ baseline（膨胀后的几何范围）；
3. prob-only 不与经典 Otsu 求并：prob 候选里不含「只有 Otsu 命中」的像素；
4. RGB 通道口径：颜色提示按 RGB 判近前景色（红字提示选红不选蓝）；
5. 颜色提示选前景、前景与背景同色时为空；
6. classical 只在 ``include_classical=True`` 时出现，且默认不产出；
7. 形状不匹配 / 非法 ``fg_rgb`` 立即报错。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_text_mask_refine.py -q
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

import numpy as np  # noqa: E402

from utils.text_mask_refine import (  # noqa: E402
    MASK_DILATE_SIZE,
    refine_text_mask,
)

WHITE = (255, 255, 255)


def _blank(h=64, w=64, bg=WHITE):
    """白底裁剪区 + 全零概率图（RGB 口径）。"""
    return np.full((h, w, 3), bg, np.uint8), np.zeros((h, w), np.float32)


def _fill(mask, x1, y1, x2, y2, val=255):
    mask[y1:y2, x1:x2] = val


def _paint(img, x1, y1, x2, y2, rgb):
    img[y1:y2, x1:x2] = rgb


class EmptySeedTest(unittest.TestCase):
    def test_zero_fill_gives_empty_candidates(self):
        img, prob = _blank()
        fill = np.zeros((64, 64), np.uint8)
        res = refine_text_mask(
            img, fill, prob_crop=prob, fg_rgb=(0, 0, 0), include_classical=True
        )
        self.assertEqual(int(res.baseline.sum()), 0)
        # 三路候选都「存在但为空」，不是 None（给了对应输入）
        for cand in (res.prob, res.color, res.classical):
            self.assertIsNotNone(cand)
            self.assertEqual(int(cand.sum()), 0)
        diag = res.diagnostics
        self.assertEqual(diag["baseline_area"], 0)
        self.assertEqual(diag["prob_reduce"], 0.0)
        self.assertTrue(diag["prob_within_baseline"])


class GeometryBoundTest(unittest.TestCase):
    """候选不得越出几何范围；baseline 由 fill 膨胀而来。"""

    def _scene(self):
        img, prob = _blank(48, 48)
        fill = np.zeros((48, 48), np.uint8)
        # 两块笔画，分别贴着左边和上边（边界情形）
        for x1, y1, x2, y2 in ((0, 0, 12, 40), (30, 0, 42, 40)):
            _fill(fill, x1, y1, x2, y2)
            _paint(img, x1, y1, x2, y2, (0, 0, 0))
            prob[y1:y2, x1:x2] = 0.9
        return img, prob, fill

    def test_candidates_stay_within_baseline(self):
        img, prob, fill = self._scene()
        res = refine_text_mask(
            img, fill, prob_crop=prob, fg_rgb=(0, 0, 0), include_classical=True
        )
        for cand in (res.prob, res.color, res.classical):
            self.assertFalse(bool((cand & ~res.baseline).any()))
        # baseline = 膨胀后的几何范围，比 fill 大
        self.assertGreater(int(res.baseline.sum()), int(fill.sum()))
        # 诊断面积与掩膜一致
        self.assertEqual(res.diagnostics["prob_area"], int((res.prob > 0).sum()))
        self.assertTrue(res.diagnostics["color_within_baseline"])
        # 缩减比落在 [0, 1]
        for key in ("prob_reduce", "color_reduce", "classical_reduce"):
            self.assertGreaterEqual(res.diagnostics[key], 0.0)
            self.assertLessEqual(res.diagnostics[key], 1.0)

    def test_zero_dilate_keeps_baseline_equal_to_fill(self):
        img, prob, fill = self._scene()
        res = refine_text_mask(img, fill, prob_crop=prob, dilate_size=0)
        self.assertTrue(np.array_equal(res.baseline, (fill > 0).astype(np.uint8) * 255))
        self.assertFalse(bool((res.prob & ~res.baseline).any()))

    def test_fill_dtype_and_range_normalized(self):
        img, prob, fill = self._scene()
        as_bool = refine_text_mask(img, fill.astype(bool), dilate_size=0)
        as_ones = refine_text_mask(img, fill.astype(np.uint8) // 255, dilate_size=0)
        self.assertTrue(np.array_equal(as_bool.baseline, as_ones.baseline))


class ProbOnlyTest(unittest.TestCase):
    """prob 候选只来自概率图，不与经典 Otsu 求并。"""

    def _scene(self):
        img, prob = _blank(96, 96)
        fill = np.zeros((96, 96), np.uint8)
        # 黑笔画：prob 高；深灰块：prob=0，但对比度足够 Otsu 会检到
        for x1, y1, x2, y2, rgb, p in (
            (10, 10, 24, 50, (0, 0, 0), 0.95),
            (44, 10, 58, 50, (20, 20, 20), 0.0),
        ):
            _fill(fill, x1, y1, x2, y2)
            _paint(img, x1, y1, x2, y2, rgb)
            prob[y1:y2, x1:x2] = p
        return img, prob, fill

    def test_prob_excludes_otsu_only_pixels(self):
        img, prob, fill = self._scene()
        res = refine_text_mask(img, fill, prob_crop=prob, include_classical=True)
        black = (slice(10, 50), slice(10, 24))
        otsu_only = (slice(10, 50), slice(44, 58))
        # prob 覆盖黑笔画
        self.assertTrue(np.all(res.prob[black] > 0))
        # Otsu 检到了深灰块，但 prob 候选里一个像素都没有（无并集）
        self.assertGreater(int(res.classical[otsu_only].sum()), 0)
        self.assertEqual(int(res.prob[otsu_only].sum()), 0)

    def test_missing_prob_returns_none(self):
        img, _, fill = self._scene()
        res = refine_text_mask(img, fill)
        self.assertIsNone(res.prob)
        self.assertFalse(res.diagnostics["prob_available"])
        self.assertGreater(int(res.baseline.sum()), 0)

    def test_classical_is_opt_in(self):
        img, prob, fill = self._scene()
        self.assertIsNone(refine_text_mask(img, fill, prob_crop=prob).classical)
        self.assertIsNotNone(
            refine_text_mask(img, fill, prob_crop=prob, include_classical=True).classical
        )


class ColorHintTest(unittest.TestCase):
    def test_hint_selects_foreground_inside_geometry(self):
        img, _ = _blank(64, 64)
        fill = np.zeros((64, 64), np.uint8)
        _fill(fill, 10, 10, 54, 54)
        _paint(img, 16, 16, 48, 48, (0, 0, 0))
        res = refine_text_mask(img, fill, fg_rgb=(0, 0, 0))
        self.assertTrue(np.all(res.color[16:48, 16:48] > 0))
        # 几何范围之外（含白底）一个像素都不选
        self.assertEqual(int(res.color[:8, :].sum()), 0)
        self.assertFalse(bool((res.color & ~res.baseline).any()))

    def test_hint_equal_to_background_is_empty(self):
        img, _ = _blank(64, 64)
        fill = np.zeros((64, 64), np.uint8)
        _fill(fill, 10, 10, 54, 54)
        _paint(img, 16, 16, 48, 48, (0, 0, 0))
        # 白底上给「白」作前景 → 前景与背景同色 → 空候选
        res = refine_text_mask(img, fill, fg_rgb=(255, 255, 255))
        self.assertEqual(int(res.color.sum()), 0)
        self.assertEqual(res.diagnostics["color_bg_rgb"], [255, 255, 255])

    def test_rgb_channel_order(self):
        """红字提示选红不选蓝；若误按 BGR 处理会选错。"""
        img, _ = _blank(64, 64)
        fill = np.zeros((64, 64), np.uint8)
        _fill(fill, 10, 10, 54, 54)
        _paint(img, 14, 14, 30, 50, (255, 0, 0))  # 红
        _paint(img, 36, 14, 52, 50, (0, 0, 255))  # 蓝
        res = refine_text_mask(img, fill, fg_rgb=(255, 0, 0))
        self.assertTrue(np.all(res.color[14:50, 14:30] > 0))
        self.assertEqual(int(res.color[14:50, 36:52].sum()), 0)


class ValidationTest(unittest.TestCase):
    def test_shape_mismatch_raises(self):
        img, _ = _blank()
        with self.assertRaises(ValueError):
            refine_text_mask(img, np.zeros((10, 10), np.uint8))
        with self.assertRaises(ValueError):
            refine_text_mask(
                img,
                np.zeros((64, 64), np.uint8),
                prob_crop=np.zeros((10, 10), np.float32),
            )

    def test_bad_fg_raises(self):
        img, _ = _blank()
        fill = np.zeros((64, 64), np.uint8)
        _fill(fill, 10, 10, 54, 54)
        with self.assertRaises(ValueError):
            refine_text_mask(img, fill, fg_rgb=(1, 2))
        with self.assertRaises(ValueError):
            refine_text_mask(img, fill, fg_rgb=("a", "b", "c"))

    def test_non_rgb_crop_raises(self):
        with self.assertRaises(ValueError):
            refine_text_mask(np.zeros((8, 8), np.uint8), np.zeros((8, 8), np.uint8))


class DefaultsTest(unittest.TestCase):
    def test_default_dilate_size(self):
        self.assertEqual(MASK_DILATE_SIZE, 2)
        img, prob = _blank(32, 32)
        fill = np.zeros((32, 32), np.uint8)
        _fill(fill, 8, 8, 24, 24)
        prob[8:24, 8:24] = 1.0
        res = refine_text_mask(img, fill, prob_crop=prob)
        self.assertEqual(res.diagnostics["dilate_size"], MASK_DILATE_SIZE)
        self.assertEqual(res.diagnostics["prob_bin_thresh"], 0.5)


if __name__ == "__main__":
    unittest.main()
