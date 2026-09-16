"""批量「简单背景」纯色修复（规划 D3／D34）回归。

覆盖 ``modules/inpaint/base.py`` 的 ``classify_simple`` 与
``InpainterBase.inpaint`` 的 ``only_simple`` 开关，以及
``ui/batch_inpaint.py::BatchSimpleInpaint``：判据一律算原图、只写
``inpainted/`` 层、复杂块完全不动（``only_simple`` 不加载模型）、既有修复图
上复杂块成果与矩形外像素保留、整批走版本仓库可撤回、原图文件字节不变。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_simple_inpaint.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from modules.inpaint.base import InpainterBase, classify_simple  # noqa: E402
from ui.batch_inpaint import (  # noqa: E402
    SKIP_NO_MASK,
    SKIP_NO_SIMPLE,
    BatchSimpleInpaint,
)
from ui.batch_ops import BatchOperation  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.io_utils import imread  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

SIZE = 200
# 简单块：气泡内近乎纯白（判据 std≈0）；复杂块：气泡内横向渐变（std 明显超阈值）
SIMPLE_XYXY = [20, 20, 80, 80]
COMPLEX_XYXY = [110, 20, 170, 80]
GHOST_XYXY = [150, 150, 190, 190]  # 落在掩码之外 → 判不出来
SIMPLE_ENLARGED = [11, 11, 89, 89]  # enlarge_window(ratio=1.7) 的结果
# 既有修复图上的标记块：落在两个外扩矩形之外
MARKER = (60, 150, 90, 180)
MARKER_VALUE = 128


def _page_image(spec):
    """按 ``[(kind, xyxy)]`` 造一页：``simple``／``complex``／``ghost``。

    ``ghost`` 的框不落任何掩码像素（对应"判不出来"的块）。
    """
    img = np.full((SIZE, SIZE, 3), 255, np.uint8)
    mask = np.zeros((SIZE, SIZE), np.uint8)
    for kind, (x1, y1, x2, y2) in spec:
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 0), 2)
        if kind == "simple":
            cv2.rectangle(img, (x1 + 20, y1 + 20), (x2 - 20, y2 - 20), (0, 0, 0), -1)
        elif kind == "complex":
            grad = np.tile(
                np.linspace(0, 255, x2 - x1 - 4, dtype=np.uint8), (y2 - y1 - 4, 1)
            )
            img[y1 + 2 : y2 - 2, x1 + 2 : x2 - 2] = grad[:, :, None]
            cv2.rectangle(img, (x1 + 20, y1 + 20), (x2 - 20, y2 - 20), (0, 0, 0), -1)
        if kind != "ghost":
            # 掩码比文字块大 3px：盖住文字边缘（与真实检测遮罩一致）
            mask[y1 + 17 : y2 - 17, x1 + 17 : x2 - 17] = 255
    return img, mask


def _make_blk(xyxy):
    blk = TextBlock(xyxy=list(xyxy), translation="t")
    blk._bounding_rect = list(xyxy)
    return blk


class _FakeInpainter(InpainterBase):
    """只暴露开关语义：模型一律不该被调用（``only_simple`` 路径）。"""

    inpaint_by_block = True
    check_need_inpaint = False
    _load_model_keys = {"model"}
    model = None
    params = {}

    def __init__(self):
        super().__init__()
        self.loads = 0
        self.model_calls = 0
        self.model_result = None

    def load_model(self):
        self.loads += 1
        self.model = object()

    def _load_model(self):
        pass

    def _inpaint(self, img, mask, textblock_list=None):
        self.model_calls += 1
        if self.model_result is None:
            raise AssertionError("only_simple 不应调用修复模型")
        return self.model_result

    def moveToDevice(self, device, precision: str = None):
        pass


class InpaintOnlySimpleTest(unittest.TestCase):
    """``classify_simple`` 判据与 ``InpainterBase.inpaint`` 的开关语义。"""

    def setUp(self):
        self.img, self.mask = _page_image(
            [("simple", SIMPLE_XYXY), ("complex", COMPLEX_XYXY)]
        )
        self.blks = [_make_blk(SIMPLE_XYXY), _make_blk(COMPLEX_XYXY)]

    def test_classify_judges_simple_and_complex(self):
        im = self.img[11:89, 11:89]
        need, ballon, bg = classify_simple(im, self.mask[11:89, 11:89])
        self.assertFalse(need)
        self.assertIsNotNone(ballon)
        self.assertTrue(np.allclose(bg, 255))

        x1, y1, x2, y2 = 101, 11, 179, 89
        need, ballon, _bg = classify_simple(
            self.img[y1:y2, x1:x2], self.mask[y1:y2, x1:x2]
        )
        self.assertTrue(need)
        self.assertIsNotNone(ballon)

    def test_classify_returns_none_when_no_text_mask(self):
        """裁剪区内没有掩码像素：判不出来（既不覆盖也不炸）。"""
        need, ballon, bg = classify_simple(self.img[:50, :50], self.mask[:50, :50])
        self.assertTrue(need)
        self.assertIsNone(ballon)
        self.assertIsNone(bg)

    def test_default_keeps_model_for_complex_blocks(self):
        """默认行为不变：简单块纯色覆盖、复杂块走模型。"""
        fake = _FakeInpainter()
        fake.model_result = np.zeros((78, 78, 3), np.uint8)
        out = fake.inpaint(self.img, self.mask.copy(), self.blks, check_need_inpaint=True)
        self.assertEqual(fake.model_calls, 1)
        self.assertEqual(fake.loads, 1)  # 默认路径照旧加载模型
        # 简单块的文字被纯色覆盖
        self.assertEqual(out[40, 40].tolist(), [255, 255, 255])
        # 该调用不改动传入的图像本身（结果在副本上）
        self.assertEqual(self.img[40, 40].tolist(), [0, 0, 0])

    def test_only_simple_skips_model_and_complex_blocks(self):
        fake = _FakeInpainter()
        out = fake.inpaint(self.img.copy(), self.mask.copy(), self.blks, only_simple=True)
        self.assertEqual(fake.model_calls, 0)
        self.assertEqual(fake.loads, 0)  # 纯色覆盖不需要模型
        self.assertEqual(out[40, 40].tolist(), [255, 255, 255])  # 简单块被覆盖
        self.assertEqual(out[50, 140].tolist(), self.img[50, 140].tolist())  # 复杂块不动

    def test_judgement_off_means_no_fill(self):
        """``check_need_inpaint=False`` 且非 only_simple：判据不跑，两块都走模型。"""
        fake = _FakeInpainter()
        fake.model_result = np.zeros((78, 78, 3), np.uint8)
        fake.inpaint(self.img.copy(), self.mask.copy(), self.blks)
        self.assertEqual(fake.model_calls, 2)


class _FailStore:
    """版本写不成的仓库替身。"""

    def begin(self, label, **kwargs):
        return None

    def latest(self):
        return None


class BatchSimpleInpaintTest(unittest.TestCase):
    """批量任务本体：扫描、覆盖、版本撤回。"""

    def setUp(self):
        self._ext = pcfg.intermediate_imgsave_ext
        self._limit = pcfg.batch_backup_versions
        pcfg.intermediate_imgsave_ext = ".png"
        pcfg.batch_backup_versions = 1

        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.pages = {
            "01.png": [("simple", SIMPLE_XYXY), ("complex", COMPLEX_XYXY)],
            "02.png": [("complex", COMPLEX_XYXY)],  # 无简单块 → 跳过
            "03.png": [("simple", SIMPLE_XYXY)],  # 无遮罩 → 跳过
            "05.png": [("simple", SIMPLE_XYXY)],  # 无既有修复图 → 撤销即删
        }
        self.raw = {}
        for name, spec in self.pages.items():
            img, mask = _page_image(spec)
            path = osp.join(self.dir, name)
            cv2.imwrite(path, img)
            self.raw[name] = open(path, "rb").read()

        self.proj = ProjImgTrans(directory=self.dir)
        for name, spec in self.pages.items():
            if name != "03.png":
                self.proj.save_mask(name, _page_image(spec)[1])
            self.proj.pages[name] = [
                _make_blk(xyxy) for _kind, xyxy in spec
            ]
        self.proj.save()

        # 01 页的既有修复图：标记块（矩形外）+ 原内容（复杂块成果的替身）
        base = imread(osp.join(self.dir, "01.png"))
        x1, y1, x2, y2 = MARKER
        base[y1:y2, x1:x2] = MARKER_VALUE
        self.proj.save_inpainted("01.png", base)
        self.base_bytes = open(
            osp.join(self.proj.inpainted_dir(), "01.png"), "rb"
        ).read()
        # 还原是按像素贴回的（重新编码后字节不等于原文件），故另存一份数组
        self.base_array = imread(
            self.proj.get_inpainted_path("01.png", get_last_modified=True)
        )

        self.fake = _FakeInpainter()
        self.task = BatchSimpleInpaint(self.proj, self.fake)

    def tearDown(self):
        pcfg.intermediate_imgsave_ext = self._ext
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _inpainted(self, name):
        return imread(self.proj.get_inpainted_path(name, get_last_modified=True))

    # ── 扫描 ────────────────────────────────────────────────────────

    def test_plan_counts_and_skips(self):
        plan = self.task.plan()
        self.assertEqual(sorted(plan["pages"]), ["01.png", "05.png"])
        self.assertEqual(plan["simple"], 2)  # 01 与 05 各一块
        self.assertEqual(plan["complex"], 2)  # 01 与 02 各一块（02 整页不处理）
        self.assertEqual(plan["page_count"], 2)
        self.assertEqual(plan["pages"]["01.png"]["rects"], [SIMPLE_ENLARGED])
        self.assertEqual(plan["skipped"]["02.png"], SKIP_NO_SIMPLE)
        self.assertEqual(plan["skipped"]["03.png"], SKIP_NO_MASK)

    def test_plan_totals_cover_whole_book(self):
        """复杂／判不出的块数按全书统计（含不处理的页），供 D27 说明"其余不动"。"""
        plan = self.task.plan()
        # 02 页只有复杂块（不处理），但它的复杂块要记进总数
        self.assertEqual(plan["complex"], 2)
        self.assertEqual(plan["skipped"]["02.png"], SKIP_NO_SIMPLE)

    def test_plan_counts_unknown_blocks(self):
        """框不落掩码像素的块计入 unknown，不进 rects、不被覆盖。"""
        img, mask = _page_image([("ghost", GHOST_XYXY), ("simple", SIMPLE_XYXY)])
        cv2.imwrite(osp.join(self.dir, "06.png"), img)
        self.proj.save_mask("06.png", mask)
        self.proj.pages["06.png"] = [_make_blk(GHOST_XYXY), _make_blk(SIMPLE_XYXY)]
        plan = self.task.plan(["06.png"])
        self.assertEqual(plan["pages"]["06.png"]["unknown"], 1)
        self.assertEqual(plan["pages"]["06.png"]["simple"], 1)
        self.assertEqual(plan["pages"]["06.png"]["rects"], [SIMPLE_ENLARGED])

    def test_plan_is_read_only(self):
        before = open(osp.join(self.dir, "01.png"), "rb").read()
        self.task.plan()
        self.assertEqual(open(osp.join(self.dir, "01.png"), "rb").read(), before)
        self.assertEqual(self.fake.model_calls, 0)
        self.assertEqual(self.fake.loads, 0)

    # ── 执行 ────────────────────────────────────────────────────────

    def test_apply_fills_simple_blocks_only(self):
        report = self.task.apply()
        self.assertTrue(report["started"], report)
        self.assertIsNotNone(report["version"])
        self.assertEqual(report["pages"], ["01.png", "05.png"])
        self.assertEqual(report["blocks"], 2)
        self.assertTrue(report["committed"])
        self.assertEqual(report["errors"], {})
        # 模型全程没被碰过
        self.assertEqual(self.fake.model_calls, 0)
        self.assertEqual(self.fake.loads, 0)

        after = self._inpainted("01.png")
        self.assertEqual(after[40, 40].tolist(), [255, 255, 255])  # 简单块被覆盖
        self.assertEqual(after[50, 140].tolist(), [0, 0, 0])  # 复杂块文字保留
        x1, y1, x2, y2 = MARKER
        self.assertTrue((after[y1:y2, x1:x2] == MARKER_VALUE).all())  # 矩形外不动

    def test_apply_does_not_touch_original_images(self):
        self.task.apply()
        for name in self.pages:
            path = osp.join(self.dir, name)
            self.assertEqual(open(path, "rb").read(), self.raw[name], name)

    def test_apply_bumps_image_generation_but_not_dirty(self):
        """图像代数 +1（栈外写入），但不标脏（D27／§8）。"""
        before = self.proj.page_image_generation("01.png")
        self.task.apply()
        self.assertEqual(self.proj.page_image_generation("01.png"), before + 1)
        self.assertEqual(self.proj.page_generation("01.png"), 0)  # 文本侧不动
        self.assertFalse(self.proj.page_needs_rerender("01.png"))

    def test_apply_creates_pixels_for_page_without_inpainted(self):
        self.task.apply()
        self.assertTrue(
            osp.exists(self.proj.get_inpainted_path("05.png", get_last_modified=True))
        )

    def test_apply_reloads_current_page(self):
        self.proj.set_current_img("01.png")
        report = self.task.apply()
        self.assertTrue(report["reloaded"])
        self.assertTrue(self.proj.inpainted_valid)  # 缓冲已换上
        self.assertEqual(
            self.proj.inpainted_array[40, 40].tolist(), [255, 255, 255]
        )

    def test_undo_restores_pixels(self):
        report = self.task.apply()
        gone = self.proj.get_inpainted_path("05.png", get_last_modified=True)
        self.assertFalse(np.array_equal(self._inpainted("01.png"), self.base_array))

        dirty = self.task.op.undo_last(expect_seq=report["version"].seq)

        self.assertTrue(np.array_equal(self._inpainted("01.png"), self.base_array))
        self.assertFalse(osp.exists(gone))  # 操作前没有的那张被删掉
        self.assertIsInstance(dirty, list)

    # ── 守卫 ────────────────────────────────────────────────────────

    def test_apply_refuses_non_block_inpainter(self):
        self.fake.inpaint_by_block = False
        report = self.task.apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "inpainter-not-block-capable")
        self.assertEqual(
            open(osp.join(self.proj.inpainted_dir(), "01.png"), "rb").read(),
            self.base_bytes,
        )

    def test_apply_aborts_when_version_not_written(self):
        task = BatchSimpleInpaint(
            self.proj,
            self.fake,
            op=BatchOperation(self.proj, store=_FailStore()),
        )
        report = task.apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "version-not-written")
        # 没写成版本就绝不落写：像素保持原样
        self.assertEqual(
            open(osp.join(self.proj.inpainted_dir(), "01.png"), "rb").read(),
            self.base_bytes,
        )
        self.assertFalse(
            osp.exists(self.proj.get_inpainted_path("05.png", get_last_modified=True))
        )

    def test_stop_before_first_page_discards_version(self):
        task = BatchSimpleInpaint(
            self.proj, self.fake, should_stop=lambda: True
        )
        report = task.apply()
        self.assertTrue(report["stopped"])
        self.assertFalse(report["started"])
        self.assertIsNone(report["error"])  # 取消不是错误
        self.assertFalse(report["rolled_back"])  # 一页都没涂过，无需回滚
        # 刚写的版本就地丢弃，不污染「可撤销步数」
        self.assertEqual(self.task.op.available_steps(), 0)

    def test_stop_midway_rolls_back(self):
        """取消即回滚：中途取消时已涂的页撤回原样，不留半成品。"""
        calls = {"n": 0}

        def should_stop():
            calls["n"] += 1
            return calls["n"] > 1  # 第二页之前取消

        task = BatchSimpleInpaint(self.proj, self.fake, should_stop=should_stop)
        report = task.apply()

        self.assertTrue(report["stopped"])
        self.assertTrue(report["rolled_back"])
        self.assertFalse(report["started"])
        self.assertIsNone(report["version"])  # 版本被回滚消耗
        self.assertEqual(report["pages"], [])
        self.assertEqual(report["blocks"], 0)
        self.assertEqual(report["rolled_back_pages"], ["01.png"])
        # 01 页的像素回到操作前，05 页那张也没留下来
        self.assertTrue(np.array_equal(self._inpainted("01.png"), self.base_array))
        self.assertFalse(
            osp.exists(self.proj.get_inpainted_path("05.png", get_last_modified=True))
        )
        self.assertEqual(self.task.op.available_steps(), 0)

    def test_progress_callback_reports_every_page(self):
        seen = []
        task = BatchSimpleInpaint(
            self.proj, self.fake, on_progress=lambda i, n, p: seen.append((i, n, p))
        )
        task.apply()
        self.assertEqual([(i, n) for i, n, _ in seen], [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
