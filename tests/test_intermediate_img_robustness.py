"""截断/损坏的中间图（修复图、无字图、遮罩）不得让页面加载闪退。

背景：样式编辑器批量应用字重后 _rerender_dirty_pages 逐页 set_current_img，
读到一张此前写入中断留下的截断 inpainted PNG，PIL 抛 OSError 一路穿透
Qt 槽导致闪退。修复后这些读取按"文件缺失"语义降级并回退。

注意：测试刻意只走 ``imgname == current_img`` 分支（与页面切换的生产
路径一致）。非当前页分支会对裸 ``Image.open``（被 ultralytics 补丁）抛
错并尝试 pip 安装缺失插件，绝不能在测试中触达。
"""

import os
import os.path as osp
import sys
import tempfile
import unittest

import numpy as np

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils.config import pcfg  # noqa: E402
from utils.io_utils import imwrite  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402


def _make_png(directory: str, name: str, h=8, w=8):
    img = np.full((h, w, 3), 200, dtype=np.uint8)
    path = osp.join(directory, name)
    imwrite(path, img, ext=".png")
    return img, path


def _truncate(path: str, keep: int = 64):
    # 保留 64 字节：PIL 能识别但加载时抛 OSError("image file is truncated")，
    # 即线上闪退的实际分支；33 字节则连识别都失败（UnidentifiedImageError）。
    with open(path, "rb") as f:
        data = f.read()
    with open(path, "wb") as f:
        f.write(data[:keep])


class TestIntermediateImgRobustness(unittest.TestCase):
    def setUp(self):
        self._old_notext_cfg = pcfg.use_notext_images
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj = ProjImgTrans()
        self.proj.directory = self.tmpdir.name
        # 生产路径前提：页面是当前页且原图已加载（避免踩裸 Image.open 分支）
        self.raw, raw_path = _make_png(self.tmpdir.name, "page.png")
        self.proj.current_img = "page.png"
        self.proj.img_array = self.raw

    def tearDown(self):
        pcfg.use_notext_images = self._old_notext_cfg
        self.tmpdir.cleanup()

    def test_truncated_inpainted_returns_none(self):
        os.makedirs(self.proj.inpainted_dir(), exist_ok=True)
        _, path = _make_png(self.proj.inpainted_dir(), "page.png")
        _truncate(path)
        self.assertIsNone(self.proj.load_inpainted_by_imgname("page.png"))

    def test_valid_inpainted_still_loads(self):
        os.makedirs(self.proj.inpainted_dir(), exist_ok=True)
        img, _ = _make_png(self.proj.inpainted_dir(), "page.png")
        loaded = self.proj.load_inpainted_by_imgname("page.png")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.shape, img.shape)

    def test_truncated_notext_returns_none(self):
        os.makedirs(self.proj.notext_dir(), exist_ok=True)
        _, path = _make_png(self.proj.notext_dir(), "page.png")
        _truncate(path, keep=33)  # 识别即失败 → imread 重试后返回 None
        self.assertIsNone(self.proj.load_notext_by_imgname("page.png"))

    def test_set_current_img_falls_back_on_corrupt_intermediates(self):
        pcfg.use_notext_images = False
        os.makedirs(self.proj.inpainted_dir(), exist_ok=True)
        os.makedirs(self.proj.mask_dir(), exist_ok=True)
        _, inp_path = _make_png(self.proj.inpainted_dir(), "page.png")
        _, mask_path = _make_png(self.proj.mask_dir(), "page.png")
        _truncate(inp_path)
        _truncate(mask_path)

        self.proj.pages = {"page.png": []}
        self.proj.set_current_img("page.png")

        # 修复图损坏 → 回退原图副本；遮罩损坏 → 按无遮罩置零
        np.testing.assert_array_equal(self.proj.inpainted_array, self.raw)
        self.assertEqual(self.proj.mask_array.dtype, np.uint8)
        self.assertTrue(np.all(self.proj.mask_array == 0))


if __name__ == "__main__":
    unittest.main()
