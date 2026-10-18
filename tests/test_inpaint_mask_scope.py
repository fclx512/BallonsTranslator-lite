"""「跳过简单情况」的纯色快捷填充只能覆盖修复掩膜内的像素（#1364）。

按气泡轮廓填充会把半透明气泡整只涂平：气泡轮廓通常大于修复掩膜，判据
又容易把低对比度的半透明背景判成「简单」。
"""
import unittest
from unittest import mock

import numpy as np

from modules.inpaint.base import InpainterBase
from utils.textblock import TextBlock


def _balloon_and_non_text(img, mask):
    balloon = np.full(img.shape[:2], 255, np.uint8)
    non_text = balloon.copy()
    non_text[mask > 127] = 0
    return balloon, non_text


class DummyInpainter(InpainterBase):
    inpaint_by_block = True
    _load_model_keys = None

    def __init__(self):
        self.name = "dummy"
        self.params = None
        self.calls = []

    def _inpaint(self, img, mask, textblock_list=None, **kwargs):
        self.calls.append(np.array(mask, copy=True))
        result = np.copy(img)
        result[mask > 127] = 255
        return result


class FlatFillMaskScopeTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(InpainterBase, "check_need_inpaint", True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_full_image_shortcut_only_fills_mask_pixels(self):
        inpainter = DummyInpainter()
        inpainter.inpaint_by_block = False
        img = np.full((12, 12, 3), 120, np.uint8)
        mask = np.zeros((12, 12), np.uint8)
        mask[4:7, 4:7] = 255
        img[4:7, 4:7] = 10
        with mock.patch(
            "modules.inpaint.base.extract_ballon_mask",
            side_effect=_balloon_and_non_text,
        ):
            result = inpainter.inpaint(img, mask, None, check_need_inpaint=True)

        self.assertEqual(inpainter.calls, [])
        expected = img.copy()
        expected[mask > 127] = 120
        np.testing.assert_array_equal(result, expected)

    def test_block_shortcut_only_fills_mask_pixels(self):
        inpainter = DummyInpainter()
        img = np.full((20, 20, 3), 120, np.uint8)
        mask = np.zeros((20, 20), np.uint8)
        mask[8:12, 8:12] = 255
        img[8:12, 8:12] = 10
        blocks = [TextBlock(xyxy=[6, 6, 14, 14])]
        with mock.patch(
            "modules.inpaint.base.extract_ballon_mask",
            side_effect=_balloon_and_non_text,
        ):
            result = inpainter.inpaint(img, mask.copy(), blocks)

        self.assertEqual(inpainter.calls, [])
        expected = img.copy()
        expected[mask > 127] = 120
        np.testing.assert_array_equal(result, expected)

    def test_block_shortcut_does_not_touch_neighbour_mask(self):
        """1.7 倍裁剪窗会带进邻块文字，块局部掩膜必须把它们挡在外面。"""
        inpainter = DummyInpainter()
        img = np.full((30, 30, 3), 120, np.uint8)
        mask = np.zeros((30, 30), np.uint8)
        mask[6:9, 6:9] = 255
        mask[20:23, 20:23] = 255
        img[mask > 0] = 10
        blocks = [TextBlock(xyxy=[4, 4, 11, 11])]
        with mock.patch(
            "modules.inpaint.base.extract_ballon_mask",
            side_effect=_balloon_and_non_text,
        ):
            result = inpainter.inpaint(img, mask.copy(), blocks)

        self.assertEqual(inpainter.calls, [])
        self.assertTrue(np.all(result[6:9, 6:9] == 120))
        # 邻块遮罩不在本块矩形里，保持原样。
        np.testing.assert_array_equal(result[20:23, 20:23], img[20:23, 20:23])

    def test_nonuniform_background_uses_the_model_path(self):
        inpainter = DummyInpainter()
        img = np.zeros((16, 16, 3), np.uint8)
        img[:] = np.arange(16, dtype=np.uint8)[None, :, None] * 15
        mask = np.zeros((16, 16), np.uint8)
        mask[6:10, 6:10] = 255
        blocks = [TextBlock(xyxy=[5, 5, 11, 11])]
        with mock.patch(
            "modules.inpaint.base.extract_ballon_mask",
            side_effect=_balloon_and_non_text,
        ):
            inpainter.inpaint(img, mask.copy(), blocks)

        self.assertEqual(len(inpainter.calls), 1)


if __name__ == "__main__":
    unittest.main()
