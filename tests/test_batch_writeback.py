"""批量写回范式（规划 D40）与批量操作事务（D35 + D40）回归。

覆盖 ``ui/batch_ops.py``：五步顺序（前置对齐 → 只改数据层 → 页代数 →
当前页重建视觉层 → 不再回写）、D40 验收判据（``page_data_needs_sync``
收尾为假）、版本写入与 LIFO 撤销、版本写不成时中止不写。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_writeback.py -q
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

from ui.batch_ops import BatchOperation, BatchWriteback  # noqa: E402
from utils.block_actions import page_data_needs_sync  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(translation, xyxy=(10, 10, 60, 40)):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class _FakeItem:
    def __init__(self, blk):
        self.blk = blk


class _FakeSceneManager:
    """只保留写回范式用到的两件东西：块项列表与整页重建。"""

    def __init__(self, proj):
        self.proj = proj
        self.textblk_item_list = []
        self.rebuilds = 0

    def rebuild(self):
        self.textblk_item_list = [
            _FakeItem(b) for b in self.proj.current_block_list()
        ]

    def updateSceneTextitems(self):
        self.rebuilds += 1
        self.rebuild()


class BatchWritebackTest(unittest.TestCase):
    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 1

        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        for name in ("a.png", "b.png"):
            cv2.imwrite(osp.join(self.proj_dir, name), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages["a.png"] = [_make_blk("old")]
        self.proj.pages["b.png"] = [_make_blk("bee")]
        self.proj.save()
        self.scene = _FakeSceneManager(self.proj)
        self.scene.rebuild()

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _new_blocks(self, tag):
        return [_make_blk(tag, (10, 10, 60, 40)), _make_blk("extra")]

    # ── 五步写回 ────────────────────────────────────────────────

    def test_apply_writes_data_scene_and_verifies(self):
        new_a = self._new_blocks("merged")
        report = BatchWriteback(self.proj, self.scene).apply(
            {"a.png": new_a, "b.png": [_make_blk("new-b")]}
        )
        # ② 数据层换新（当前页与非当前页都写）
        self.assertEqual(self.proj.pages["a.png"], new_a)
        self.assertEqual(self.proj.pages["b.png"][0].translation, "new-b")
        # ③ 页代数 +1（文本侧）
        self.assertEqual(self.proj.page_generation("a.png"), 1)
        self.assertEqual(self.proj.page_generation("b.png"), 1)
        self.assertEqual(self.proj.page_image_generation("a.png"), 0)
        # ④ 当前页重建视觉层一次
        self.assertTrue(report["ui_rebuilt"])
        self.assertEqual(self.scene.rebuilds, 1)
        # 验收：判据为假（D40）
        self.assertTrue(report["verified"])
        self.assertFalse(
            page_data_needs_sync(
                self.proj.current_block_list(), self.scene.textblk_item_list
            )
        )

    def test_image_changed_bumps_image_generation(self):
        BatchWriteback(self.proj, self.scene).apply(
            {"a.png": self._new_blocks("x")}, image_changed=True
        )
        self.assertEqual(self.proj.page_image_generation("a.png"), 1)

    def test_dirty_marking_skips_current_page(self):
        self.proj.set_current_img("b.png")
        BatchWriteback(self.proj, self.scene).apply(
            {"a.png": self._new_blocks("x"), "b.png": [_make_blk("y")]}
        )
        self.assertTrue(self.proj.page_needs_rerender("a.png"))
        self.assertFalse(self.proj.page_needs_rerender("b.png"))

    def test_sync_step_runs_before_data_write(self):
        """① 前置对齐必须先于 ② 改数据层（否则会把面板旧值覆盖新数据）。"""
        seen = {}

        def fake_sync():
            seen["a_translation"] = self.proj.pages["a.png"][0].translation

        report = BatchWriteback(
            self.proj, self.scene, sync_block_data=fake_sync
        ).apply({"a.png": self._new_blocks("x")})
        self.assertTrue(report["synced"])
        self.assertEqual(seen["a_translation"], "old")

    def test_unknown_page_is_skipped(self):
        report = BatchWriteback(self.proj, self.scene).apply(
            {"ghost.png": self._new_blocks("x"), "a.png": self._new_blocks("x")}
        )
        self.assertEqual(report["skipped"], ["ghost.png"])
        self.assertEqual(report["pages"], ["a.png"])
        self.assertTrue(report["verified"])

    def test_verify_true_without_scene_manager(self):
        report = BatchWriteback(self.proj).apply({"a.png": self._new_blocks("x")})
        self.assertFalse(report["ui_rebuilt"])
        self.assertTrue(report["verified"])

    def test_verify_false_when_scene_left_stale(self):
        """反例：只改数据层不重建视觉层 → 判据必须报脱节。"""
        report = BatchWriteback(self.proj).apply({"a.png": self._new_blocks("x")})
        self.assertTrue(report["verified"])  # 无视觉层时视为通过
        # 挂上视觉层后再验一次：数据已换新、项仍是旧块 → 脱节
        writer = BatchWriteback(self.proj, self.scene)
        self.assertFalse(writer.verify())


class BatchOperationTest(unittest.TestCase):
    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 2

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
        self.scene = _FakeSceneManager(self.proj)
        self.scene.rebuild()
        self.commits = []

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _commit(self):
        self.commits.append(True)
        self.proj.save()

    def _op(self):
        return BatchOperation(self.proj, self.scene, commit=self._commit)

    def test_apply_writes_version_and_commits_twice(self):
        op = self._op()
        report = op.apply("合并相邻框", {"a.png": [_make_blk("merged")]})
        self.assertTrue(report["started"])
        self.assertEqual(report["version"].label, "合并相邻框")
        self.assertEqual(op.available_steps(), 1)
        self.assertEqual(len(self.commits), 2)  # 快照前 + 写回后
        self.assertTrue(report["writeback"]["verified"])

    def test_undo_last_restores_and_consumes(self):
        op = self._op()
        op.apply("合并相邻框", {"a.png": [_make_blk("merged")]})
        self.assertEqual(self.proj.pages["a.png"][0].translation, "merged")

        dirty = op.undo_last()
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertIn("a.png", dirty)
        self.assertEqual(op.available_steps(), 0)

    def test_aborts_when_version_not_written(self):
        class _RefusingStore:
            def begin(self, *args, **kwargs):
                return None

            def available_steps(self):
                return 0

        op = BatchOperation(
            self.proj, self.scene, commit=self._commit, store=_RefusingStore()
        )
        report = op.apply("合并相邻框", {"a.png": [_make_blk("merged")]})
        self.assertFalse(report["started"])
        self.assertIsNotNone(report["error"])
        # 没写成版本就绝不落写：数据保持原样
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")


if __name__ == "__main__":
    unittest.main()
