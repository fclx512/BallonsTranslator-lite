"""一键批量删除误框（规划 D2／D27／D28）回归。

覆盖 ``ui/batch_delete.py``：队列口径（带误识别标签的块、按块去重）、驳回与
「待删除」两轴正交、只删文本层不碰修复图、跨页批量与页代数、D40 验收判据、
D35 整批撤回、取消不留半成品、非队列成员不可删。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_delete.py -q
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

from ui.batch_delete import BatchDeleteMisread  # noqa: E402
from ui.batch_ops import BatchOperation  # noqa: E402
from utils.block_actions import page_data_needs_sync  # noqa: E402
from utils.block_tags import MISREAD_TAG_ID, set_tag, set_tags_reviewed  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

PAGE_A = "a.png"
PAGE_B = "b.png"


def _blk(text, xyxy=(10, 10, 60, 40)):
    blk = TextBlock(xyxy=list(xyxy), text=[text])
    blk._bounding_rect = list(xyxy)
    return blk


def _misread(text, subtypes=("numeric",), *, reviewed=False, xyxy=(10, 10, 60, 40)):
    blk = _blk(text, xyxy)
    set_tag(blk, MISREAD_TAG_ID, "program", subtypes=list(subtypes))
    if reviewed:
        set_tags_reviewed(blk, True)
    return blk


class _FakeItem:
    def __init__(self, blk):
        self.blk = blk


class _FakeSceneManager:
    def __init__(self, proj):
        self.proj = proj
        self.textblk_item_list = []
        self.rebuilds = 0

    def rebuild(self):
        self.textblk_item_list = [_FakeItem(b) for b in self.proj.current_block_list()]

    def updateSceneTextitems(self):
        self.rebuilds += 1
        self.rebuild()


class _DeleteTestCase(unittest.TestCase):
    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 1
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        for name in (PAGE_A, PAGE_B):
            cv2.imwrite(osp.join(self.proj_dir, name), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages[PAGE_A] = [_blk("keep-a")]
        self.proj.pages[PAGE_B] = [_blk("keep-b")]
        self.proj.save()
        self.proj.set_current_img(PAGE_A)
        self.scene = _FakeSceneManager(self.proj)
        self.scene.rebuild()

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _task(self, **kwargs):
        return BatchDeleteMisread(self.proj, **kwargs)

    def _op(self):
        return BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=lambda: None
        )

    def _apply(self, selection=None, **kwargs):
        return BatchDeleteMisread(self.proj, op=self._op()).apply(selection, **kwargs)

    def _texts(self, pagename):
        return [b.get_text() for b in self.proj.pages[pagename]]


# ── 队列规划 ──────────────────────────────────────────────────────


class PlanTest(_DeleteTestCase):
    def test_queue_counts_and_entries(self):
        self.proj.pages[PAGE_A] = [
            _blk("keep-a"),
            _misread("8", ("numeric",)),
            _misread("……", ("symbolic", "no_japanese"), reviewed=True),
            _blk("keep-a2"),
        ]
        self.proj.pages[PAGE_B] = [_misread("H1")]
        plan = self._task().plan()
        self.assertEqual(plan["queue"], 3)
        self.assertEqual(plan["rejected"], 1)
        self.assertEqual(plan["pending"], 2)
        self.assertEqual(plan["apply_default"], 2)
        self.assertEqual(plan["pages"], {PAGE_A: 2, PAGE_B: 1})
        first = plan["entries"][0]
        self.assertEqual((first.pagename, first.index), (PAGE_A, 1))
        self.assertEqual(first.subtypes, ["numeric"])
        self.assertFalse(first.reviewed)
        rejected = plan["entries"][1]
        self.assertTrue(rejected.reviewed)
        self.assertEqual(sorted(rejected.subtypes), ["no_japanese", "symbolic"])

    def test_empty_queue(self):
        plan = self._task().plan()
        self.assertEqual(
            (plan["queue"], plan["rejected"], plan["pending"], plan["entries"]),
            (0, 0, 0, []),
        )

    def test_plan_is_read_only(self):
        self.proj.pages[PAGE_A] = [_blk("keep-a"), _misread("8")]
        before = len(self.proj.pages[PAGE_A])
        gen = self.proj.page_generation(PAGE_A)
        self._task().plan()
        self.assertEqual(len(self.proj.pages[PAGE_A]), before)
        self.assertEqual(self.proj.page_generation(PAGE_A), gen)
        self.assertFalse(osp.exists(osp.join(self.proj_dir, ".bt_batch_backup")))

    def test_entry_to_dict_is_json_friendly(self):
        self.proj.pages[PAGE_A] = [_misread("8")]
        entry = self._task().plan()["entries"][0]
        self.assertEqual(
            entry.to_dict(),
            {
                "pagename": PAGE_A,
                "index": 0,
                "text": "8",
                "subtypes": ["numeric"],
                "reviewed": False,
            },
        )


# ── 整批删除 ──────────────────────────────────────────────────────


class ApplyTest(_DeleteTestCase):
    def _queue(self):
        self.proj.pages[PAGE_A] = [
            _blk("keep-a"),
            _misread("8"),
            _misread("11", reviewed=True),
            _blk("keep-a2"),
        ]
        self.proj.pages[PAGE_B] = [_misread("H1"), _blk("keep-b")]

    def test_default_deletes_pending_only(self):
        self._queue()
        report = self._apply()
        self.assertTrue(report["started"])
        self.assertEqual(report["deleted"], 2)          # a.png 的 "8" + b.png 的 "H1"
        self.assertEqual(sorted(report["pages"]), sorted([PAGE_A, PAGE_B]))
        self.assertEqual(self._texts(PAGE_A), ["keep-a", "11", "keep-a2"])
        self.assertEqual(self._texts(PAGE_B), ["keep-b"])

    def test_default_keeps_rejected_and_non_queue_blocks(self):
        self._queue()
        self._apply()
        self.assertEqual(self._texts(PAGE_A)[1], "11")  # 已驳回：默认不删

    def test_explicit_selection_can_delete_rejected(self):
        """驳回与「勾选待删除」两轴正交（D27）：显式勾选照删。"""
        self._queue()
        report = self._apply([(PAGE_A, 2)])
        self.assertTrue(report["started"])
        self.assertEqual(report["deleted"], 1)
        self.assertEqual(self._texts(PAGE_A), ["keep-a", "8", "keep-a2"])

    def test_non_queue_key_is_stale_and_writes_nothing(self):
        self._queue()
        report = self._apply([(PAGE_A, 0)])  # 0 号块没有误识别标签
        self.assertFalse(report["started"])
        self.assertEqual(report["stale"], [(PAGE_A, 0)])
        self.assertEqual(self._texts(PAGE_A), ["keep-a", "8", "11", "keep-a2"])

    def test_empty_queue_reports_error(self):
        report = self._apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "empty-queue")

    def test_page_generation_and_d40_acceptance(self):
        self._queue()
        report = self._apply()
        self.assertEqual(self.proj.page_generation(PAGE_A), 1)
        self.assertEqual(self.proj.page_generation(PAGE_B), 1)
        self.assertEqual(self.proj.page_image_generation(PAGE_A), 0)  # 不动图像
        self.assertTrue(report["writeback"]["verified"])
        self.assertFalse(
            page_data_needs_sync(
                self.proj.current_block_list(), self.scene.textblk_item_list
            )
        )

    def test_undo_restores_whole_batch(self):
        """D27／D35：整批一条撤回。"""
        self._queue()
        task = BatchDeleteMisread(self.proj, op=self._op())
        report = task.apply()
        self.assertTrue(report["started"])
        task.op.undo_last(expect_seq=report["version"].seq)
        self.assertEqual(
            self._texts(PAGE_A), ["keep-a", "8", "11", "keep-a2"]
        )
        self.assertEqual(self._texts(PAGE_B), ["H1", "keep-b"])

    def test_cancel_before_write_changes_nothing(self):
        self._queue()
        task = BatchDeleteMisread(self.proj, op=self._op(), should_stop=lambda: True)
        report = task.apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "cancelled")
        self.assertEqual(len(self.proj.pages[PAGE_A]), 4)
        self.assertEqual(self.proj.page_generation(PAGE_A), 0)

    def test_marks_page_dirty_by_default(self):
        """删块改的是文本层；当前页不进脏页表，故把当前页挪开再验。"""
        self._queue()
        self.proj.set_current_img(PAGE_B)
        self.scene.rebuild()
        self._apply()
        self.assertTrue(self.proj.page_needs_rerender(PAGE_A))
        self.assertFalse(self.proj.page_needs_rerender(PAGE_B))  # 当前页自身排除

    def test_can_skip_dirty_marking(self):
        self._queue()
        self.proj.set_current_img(PAGE_B)
        self.scene.rebuild()
        self._apply(mark_dirty=False)
        self.assertFalse(self.proj.page_needs_rerender(PAGE_A))

    def test_index_shift_keeps_remaining_order(self):
        """一页删多块后，剩下的块按原顺序、原对象留在列表里。"""
        self.proj.pages[PAGE_A] = [
            _misread("m0"),
            _blk("s1"),
            _misread("m2"),
            _blk("s3"),
            _misread("m4"),
        ]
        survivors = [self.proj.pages[PAGE_A][i] for i in (1, 3)]
        report = self._apply()
        self.assertEqual(report["deleted"], 3)
        self.assertEqual(self._texts(PAGE_A), ["s1", "s3"])
        self.assertIs(self.proj.pages[PAGE_A][0], survivors[0])
        self.assertIs(self.proj.pages[PAGE_A][1], survivors[1])

    def test_pre_align_runs_before_queue_scan(self):
        """D40 第 ① 步先于规划：面板未回写的编辑不能让队列下标错位。

        构造使两种顺序结果不同：回写把误识别框从 0 号挤到 1 号——先对齐则删
        1 号（留 "pad"），晚对齐会照旧删 0 号（把 "pad" 删掉、误识别框留下）。
        """
        self.proj.pages[PAGE_A] = [_misread("8")]

        def fake_sync():
            # 面板在 0 号位置插入了一个新块（未回写）
            self.proj.pages[PAGE_A] = [_blk("pad"), _misread("8")]

        op = BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=fake_sync
        )
        report = BatchDeleteMisread(self.proj, op=op).apply([(PAGE_A, 1)])
        self.assertTrue(report["started"])
        self.assertEqual(self._texts(PAGE_A), ["pad"])

    def test_images_untouched(self):
        """只删文本层：遮罩与修复图不被本任务写入。"""
        self._queue()
        self.proj.save_mask(PAGE_A, np.zeros((64, 64), dtype=np.uint8))
        mask_path = self.proj.get_mask_path(PAGE_A)
        stamp = osp.getmtime(mask_path)
        self._apply()
        self.assertEqual(osp.getmtime(mask_path), stamp)


if __name__ == "__main__":
    unittest.main()
