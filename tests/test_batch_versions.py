"""批量操作版本轮转备份（规划 D35）回归：往返、轮转、LIFO 与像素前图。

覆盖 ``utils/batch_versions.py``：每个批量操作执行前写一版（项目数据 +
受影响矩形的像素前图），撤销取最新一版覆盖并消耗；版本数超限删最旧；
版本放项目目录内、跨会话仍在。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_versions.py -q
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
from PIL import Image  # noqa: E402

from utils.batch_versions import (  # noqa: E402
    MAX_VERSIONS,
    BatchVersionStore,
    version_limit,
)
from utils.config import pcfg  # noqa: E402
from utils.exceptions import ProjectLoadFailureException  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(translation):
    blk = TextBlock(xyxy=[100, 100, 300, 200], translation=translation)
    blk._bounding_rect = [100, 100, 300, 200]
    return blk


class BatchVersionStoreTest(unittest.TestCase):
    def setUp(self):
        # pcfg 是单例：别的用例可能已载入用户配置，本用例显式声明基线
        self._limit = pcfg.batch_backup_versions
        self._ext = pcfg.intermediate_imgsave_ext
        pcfg.batch_backup_versions = 1
        pcfg.intermediate_imgsave_ext = ".png"

        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        for name in ("a.png", "b.png"):
            cv2.imwrite(osp.join(self.proj_dir, name), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages["a.png"] = [_make_blk("old")]
        self.proj.pages["b.png"] = []
        self.proj.save()
        self.proj.set_current_img("b.png")
        self.store = BatchVersionStore(self.proj)

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        pcfg.intermediate_imgsave_ext = self._ext
        self._tmp.cleanup()

    def _inpainted_path(self, pagename="a.png"):
        return osp.join(self.proj.inpainted_dir(), pagename)

    def _write_inpainted(self, fill, pagename="a.png"):
        Image.fromarray(
            np.full((32, 32, 3), fill, dtype=np.uint8)
        ).save(self._inpainted_path(pagename))

    def _read_inpainted(self, pagename="a.png"):
        with Image.open(self._inpainted_path(pagename)) as im:
            return np.array(im.convert("RGB"))

    # ── 基础往返 ────────────────────────────────────────────────

    def test_begin_and_restore_roundtrip(self):
        meta = self.store.begin("t1", dirty_pages=["a.png"])
        self.assertIsNotNone(meta)
        self.assertEqual(self.store.available_steps(), 1)
        self.assertTrue(osp.isdir(self.store.root_dir()))

        self.proj.pages["a.png"][0].translation = "newer"
        dirty = self.store.restore_latest()

        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertEqual(dirty, ["a.png"])
        # 版本被消耗（同一步不可重复撤销）
        self.assertEqual(self.store.available_steps(), 0)
        self.assertFalse(osp.exists(meta.dir))

    def test_restore_keeps_current_page(self):
        self.store.begin("t1")
        self.proj.set_current_img("a.png")
        self.store.restore_latest()
        self.assertEqual(self.proj.current_img, "a.png")

    def test_versions_survive_reload(self):
        """版本放项目目录内（D35「随项目走」）：重开项目仍可撤销。"""
        self.store.begin("t1")
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertEqual(BatchVersionStore(fresh).available_steps(), 1)

    # ── 版本号校验与丢弃（查找替换与工作台共用同一套版本）────────

    def test_restore_latest_rejects_stale_seq(self):
        """指定版本号后只撤那一版：被更晚的操作顶掉即拒绝，且不动任何版本。"""
        pcfg.batch_backup_versions = 2
        first = self.store.begin("first")
        self.store.begin("second")
        self.proj.pages["a.png"][0].translation = "newer"

        with self.assertRaises(ProjectLoadFailureException):
            self.store.restore_latest(expect_seq=first.seq)

        self.assertEqual(self.proj.pages["a.png"][0].translation, "newer")
        self.assertEqual(self.store.available_steps(), 2)

    def test_restore_latest_accepts_matching_seq(self):
        meta = self.store.begin("only")
        self.proj.pages["a.png"][0].translation = "newer"
        self.store.restore_latest(expect_seq=meta.seq)
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertEqual(self.store.available_steps(), 0)

    def test_discard_latest_drops_version_without_touching_data(self):
        """空替换路径：丢掉刚写的那一版，数据保持原样（不换回、不标脏）。"""
        meta = self.store.begin("noop", dirty_pages=["a.png"])
        self.proj.pages["a.png"][0].translation = "newer"

        self.assertTrue(self.store.discard_latest(expect_seq=meta.seq))
        self.assertEqual(self.store.available_steps(), 0)
        self.assertFalse(osp.exists(meta.dir))
        self.assertEqual(self.proj.pages["a.png"][0].translation, "newer")
        self.assertFalse(self.proj.page_needs_rerender("a.png"))

    def test_discard_latest_rejects_stale_seq(self):
        pcfg.batch_backup_versions = 2
        first = self.store.begin("first")
        second = self.store.begin("second")
        self.assertFalse(self.store.discard_latest(expect_seq=first.seq))
        self.assertEqual([m.seq for m in self.store.list_versions()],
                         [first.seq, second.seq])

    def test_discard_latest_without_versions(self):
        self.assertFalse(self.store.discard_latest())
        self.assertFalse(self.store.discard_latest(expect_seq=1))

    def test_roundtrip_preserves_base_styles(self):
        """项目级大样式也在版本快照里（查找替换改格式会动它）。"""
        from utils.base_styles import BaseStyle
        from utils.fontformat import FontFormat

        self.proj.base_styles = [
            BaseStyle("TestFont", FontFormat(font_family="TestFont"))
        ]
        self.proj.save()
        self.store.begin("t1")
        self.proj.base_styles = []
        self.store.restore_latest()
        self.assertEqual(len(self.proj.base_styles), 1)
        self.assertEqual(self.proj.base_styles[0].fontformat.font_family,
                         "TestFont")

    # ── 轮转 ────────────────────────────────────────────────────

    def test_rotation_drops_oldest(self):
        pcfg.batch_backup_versions = 2
        for label in ("one", "two", "three"):
            self.assertIsNotNone(self.store.begin(label))
        self.assertEqual([m.label for m in self.store.list_versions()],
                         ["two", "three"])
        self.assertEqual(self.store.available_steps(), 2)

    def test_version_limit_clamped(self):
        for value, expect in ((99, MAX_VERSIONS), (7, 5), (0, 1), (-3, 1),
                              ("2", 2)):
            pcfg.batch_backup_versions = value
            self.assertEqual(version_limit(), expect)

    def test_lifo_undo_walks_back(self):
        pcfg.batch_backup_versions = 3
        self.store.begin("first")
        self.proj.pages["a.png"][0].translation = "v1"
        self.store.begin("second")
        self.proj.pages["a.png"][0].translation = "v2"

        self.store.restore_latest()
        self.assertEqual(self.proj.pages["a.png"][0].translation, "v1")
        self.store.restore_latest()
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        with self.assertRaises(ProjectLoadFailureException):
            self.store.restore_latest()

    def test_clear_removes_all_versions(self):
        self.store.begin("one")
        self.store.begin("two")
        self.store.clear()
        self.assertEqual(self.store.available_steps(), 0)
        self.assertFalse(osp.exists(self.store.root_dir()))

    def test_begin_refused_without_pages(self):
        self.proj.pages = {}
        self.assertIsNone(self.store.begin("x"))
        self.assertEqual(self.store.available_steps(), 0)

    # ── 像素前图 ────────────────────────────────────────────────

    def test_pixel_before_image_restored(self):
        self._write_inpainted(10)
        self.store.begin(
            "paint",
            dirty_pages=["a.png"],
            pixel_regions={"a.png": [[0, 0, 8, 8]]},
        )
        # 批量操作：把整页刷成另一色
        self._write_inpainted(200)
        self.store.restore_latest()

        arr = self._read_inpainted()
        self.assertTrue((arr[0:8, 0:8] == 10).all())   # 矩形内还原
        self.assertTrue((arr[16:, 16:] == 200).all())  # 矩形外保留操作结果

    def test_pixel_file_created_by_op_is_deleted_on_undo(self):
        self.assertFalse(osp.exists(self._inpainted_path()))
        self.store.begin("fill", pixel_regions={"a.png": [[0, 0, 8, 8]]})
        self._write_inpainted(200)  # 操作期间才生成修复图
        self.store.restore_latest()
        self.assertFalse(osp.exists(self._inpainted_path()))

    def test_rects_are_clamped_to_image(self):
        self._write_inpainted(10)
        self.store.begin(
            "clamp",
            pixel_regions={"a.png": [[-5, -5, 8, 8], [40, 40, 60, 60]]},
        )
        self._write_inpainted(200)
        self.store.restore_latest()
        arr = self._read_inpainted()
        self.assertTrue((arr[0:8, 0:8] == 10).all())
        self.assertTrue((arr[16:, 16:] == 200).all())

    # ── 损坏与半成品 ────────────────────────────────────────────

    def test_corrupt_version_raises_and_keeps_dir(self):
        meta = self.store.begin("t1")
        with open(osp.join(meta.dir, "proj.json"), "w", encoding="utf8") as f:
            f.write("{corrupted")
        with self.assertRaises(ProjectLoadFailureException):
            self.store.restore_latest()
        self.assertTrue(osp.exists(osp.join(meta.dir, "proj.json")))
        self.assertEqual(self.store.available_steps(), 1)

    def test_partial_version_without_meta_is_ignored(self):
        os.makedirs(osp.join(self.store.root_dir(), "v00009"), exist_ok=True)
        self.assertEqual(self.store.list_versions(), [])
        self.assertEqual(self.store.begin("first").seq, 1)


if __name__ == "__main__":
    unittest.main()
