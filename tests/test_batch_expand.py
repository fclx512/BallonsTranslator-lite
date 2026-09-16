"""批量框扩张（规划 D5／D36／§6.4）回归。

覆盖 ``ui/batch_expand.py``：扩张量两种给法（px／短边比例）、"碰到邻框即停"
的截断统计、旋转框与退化矩形不参与、只动渲染矩形（掩码与修复数据原样保留）、
写回走 D40 五步与 D35 版本撤回、**扩张后写回副本**（原地改会让版本快照记成
改动后状态、撤销失效）、取消不留半成品。

几何判据本身的行为在 ``tests/test_block_geometry.py``。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_expand.py -q
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

from ui.batch_expand import MODE_PX, MODE_RATIO, BatchExpand  # noqa: E402
from ui.batch_ops import BatchOperation  # noqa: E402
from utils.block_actions import page_data_needs_sync  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

PAGE = "p1.png"
PAGE_B = "p2.png"
PAGE_SIZE = (300, 400)


def _blk(xyxy, text="t", *, angle=0):
    blk = TextBlock(xyxy=list(xyxy), text=[text])
    blk._bounding_rect = [xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]]
    blk.angle = angle
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


class _ExpandTestCase(unittest.TestCase):
    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 1
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        for name in (PAGE, PAGE_B):
            cv2.imwrite(
                osp.join(self.proj_dir, name),
                np.zeros((PAGE_SIZE[1], PAGE_SIZE[0], 3), dtype=np.uint8),
            )
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages[PAGE] = []
        self.proj.pages[PAGE_B] = []
        self.proj.save()
        self.proj.set_current_img(PAGE)
        self.scene = _FakeSceneManager(self.proj)
        self.scene.rebuild()

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _set_blocks(self, blocks, pagename=PAGE):
        self.proj.pages[pagename] = blocks
        self.scene.rebuild()

    def _task(self):
        return BatchExpand(self.proj)

    def _op(self):
        return BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=lambda: None
        )

    def _apply(self, amount, mode=MODE_PX, selection=None, **kwargs):
        return BatchExpand(self.proj, op=self._op()).apply(
            amount, mode, selection, **kwargs
        )

    def _rect(self, pagename=PAGE, index=0):
        blk = self.proj.pages[pagename][index]
        return list(blk.xyxy)


# ── 规划 ──────────────────────────────────────────────────────────


class PlanTest(_ExpandTestCase):
    def test_counts_free_and_clamped_blocks(self):
        # 两个框左右相邻（间隔 10）：先者把空档吃满、后者左侧已贴住
        self._set_blocks([_blk([100, 100, 140, 200]), _blk([150, 100, 190, 200])])
        plan = self._task().plan(20)
        self.assertEqual(plan["blocks"], 2)
        self.assertEqual((plan["changed"], plan["unchanged"]), (2, 0))
        self.assertEqual(plan["clamped"], 2)  # 两边都被对方截住
        entries = {e["index"]: e for e in plan["entries"]}
        self.assertEqual(entries[0]["old"], [100, 100, 140, 200])
        self.assertEqual(entries[0]["new"], [80, 80, 150, 220])   # 右边停在邻框左缘
        self.assertEqual(entries[1]["new"], [150, 80, 210, 220])  # 左边保持贴住
        self.assertEqual(plan["pages"], {PAGE: 2})

    def test_enclosed_block_is_unchanged(self):
        """四个方向都贴住邻框的框扩不动，已在里面的邻框仍可往外扩。"""
        self._set_blocks([
            _blk([100, 100, 140, 200]),  # 0：被四面包围
            _blk([80, 100, 100, 200]),   # 1 左侧贴住
            _blk([140, 100, 160, 200]),  # 2 右侧贴住
            _blk([100, 60, 140, 100]),   # 3 上方贴住
            _blk([100, 200, 140, 240]),  # 4 下方贴住
        ])
        plan = self._task().plan(20)
        self.assertNotIn(0, [e["index"] for e in plan["entries"]])
        self.assertEqual(plan["changed"], 4)
        self.assertEqual(plan["unchanged"], 1)

    def test_ratio_mode_scales_with_short_edge(self):
        self._set_blocks([_blk([100, 100, 140, 200]), _blk([100, 250, 200, 350])])
        plan = self._task().plan(0.5, MODE_RATIO)
        grows = {e["index"]: (e["new"][0] - e["old"][0]) for e in plan["entries"]}
        self.assertEqual(grows[0], -20)  # 短边 40 → 每边 20
        self.assertEqual(grows[1], -50)  # 短边 100 → 每边 50

    def test_rotated_and_degenerate_blocks_are_left_alone(self):
        self._set_blocks([
            _blk([100, 100, 140, 200], angle=15),
            TextBlock(xyxy=[10, 10, 10, 40], text=["degen"]),
            _blk([100, 250, 140, 350]),
        ])
        plan = self._task().plan(10)
        self.assertEqual(plan["blocks"], 1)
        self.assertEqual([e["index"] for e in plan["entries"]], [2])

    def test_unknown_mode_raises(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        with self.assertRaises(ValueError):
            self._task().plan(10, "percent")

    def test_empty_page_is_skipped(self):
        plan = self._task().plan(10)
        self.assertEqual(plan["skipped"], {PAGE: "no-blocks", PAGE_B: "no-blocks"})
        self.assertEqual(plan["changed"], 0)

    def test_missing_page_size_is_skipped(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        os.remove(osp.join(self.proj_dir, PAGE))
        self.proj._image_info[PAGE].pop("width", None)
        self.proj._image_info[PAGE].pop("height", None)
        self.assertEqual(self._task().plan(10)["skipped"][PAGE], "no-page-size")

    def test_plan_is_read_only(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        self._task().plan(20)
        self.assertEqual(self._rect(), [100, 100, 140, 200])
        self.assertFalse(osp.exists(osp.join(self.proj_dir, ".bt_batch_backup")))


# ── 写回 ──────────────────────────────────────────────────────────


class ApplyTest(_ExpandTestCase):
    def test_expands_both_render_fields(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        report = self._apply(20)
        self.assertTrue(report["started"])
        self.assertEqual(report["blocks"], 1)
        blk = self.proj.pages[PAGE][0]
        self.assertEqual(list(blk.xyxy), [80, 80, 160, 220])
        self.assertEqual(blk._bounding_rect, [80, 80, 80, 140])  # D36 同步变动

    def test_masks_and_inpaint_data_are_kept(self):
        """只扩渲染区域、不动擦除区域（D5）：掩码与修复数据原样保留。"""
        blk = _blk([100, 100, 140, 200])
        blk.region_mask = np.ones((100, 40), dtype=np.uint8)
        blk.region_inpaint_dict = {"rgb": (1, 2, 3)}
        self._set_blocks([blk])
        self._apply(20)
        grown = self.proj.pages[PAGE][0]
        self.assertIsNotNone(grown.region_mask)
        self.assertEqual(grown.region_inpaint_dict, {"rgb": (1, 2, 3)})

    def test_expanded_rects_do_not_overlap(self):
        """串行扩张：先者吃掉空档、后者停在其边缘——两两不重叠（C3 验收点）。"""
        self._set_blocks([_blk([100, 100, 140, 200]), _blk([150, 100, 190, 200])])
        self._apply(20)
        left = self.proj.pages[PAGE][0].xyxy
        right = self.proj.pages[PAGE][1].xyxy
        self.assertEqual(list(left), [80, 80, 150, 220])
        self.assertEqual(list(right), [150, 80, 210, 220])
        self.assertLessEqual(left[2], right[0])  # 贴住而不重叠

    def test_expansion_never_overlaps_any_pair(self):
        """性质：无论怎么排，扩张后的任意两框都不重叠（C3 验收点）。"""
        self._set_blocks([
            _blk([100, 100, 140, 200]),
            _blk([160, 100, 200, 200]),
            _blk([220, 90, 260, 210]),
            _blk([110, 230, 150, 330]),
        ])
        self._apply(30)
        rects = [list(blk.xyxy) for blk in self.proj.pages[PAGE]]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                overlaps = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                self.assertFalse(overlaps, f"{a} 与 {b} 重叠")

    def test_non_selected_blocks_are_untouched(self):
        self._set_blocks([_blk([100, 100, 140, 200]), _blk([100, 250, 140, 350])])
        report = self._apply(20, selection=[(PAGE, 1)])
        self.assertEqual(report["blocks"], 1)
        self.assertEqual(self._rect(index=0), [100, 100, 140, 200])
        self.assertEqual(self._rect(index=1), [80, 230, 160, 370])

    def test_stale_selection_changes_nothing(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        report = self._apply(20, selection=[(PAGE, 7)])
        self.assertFalse(report["started"])
        self.assertEqual(report["stale"], [(PAGE, 7)])
        self.assertEqual(self._rect(), [100, 100, 140, 200])

    def test_nothing_to_expand_reports_error(self):
        # 两框各占半页：四边分别是邻框与页边界 → 谁都扩不动
        self._set_blocks([_blk([0, 0, 150, 400]), _blk([150, 0, 300, 400])])
        report = self._apply(20)
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "nothing-to-expand")

    def test_page_generation_and_d40_acceptance(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        report = self._apply(20)
        self.assertEqual(self.proj.page_generation(PAGE), 1)
        self.assertEqual(self.proj.page_image_generation(PAGE), 0)
        self.assertTrue(report["writeback"]["verified"])
        self.assertFalse(
            page_data_needs_sync(
                self.proj.current_block_list(), self.scene.textblk_item_list
            )
        )

    def test_undo_restores_original_rects(self):
        """写回的是副本：原地改会让版本快照记成扩张后的状态，撤销等于没撤。"""
        self._set_blocks([_blk([100, 100, 140, 200]), _blk([100, 250, 140, 350])])
        task = BatchExpand(self.proj, op=self._op())
        report = task.apply(20)
        self.assertEqual(self._rect(), [80, 80, 160, 220])
        task.op.undo_last(expect_seq=report["version"].seq)
        self.assertEqual(self._rect(index=0), [100, 100, 140, 200])
        self.assertEqual(self._rect(index=1), [100, 250, 140, 350])
        self.assertEqual(
            self.proj.pages[PAGE][0]._bounding_rect, [100, 100, 40, 100]
        )

    def test_cancel_before_write_changes_nothing(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        task = BatchExpand(self.proj, op=self._op(), should_stop=lambda: True)
        report = task.apply(20)
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "cancelled")
        self.assertEqual(self._rect(), [100, 100, 140, 200])
        self.assertEqual(self.proj.page_generation(PAGE), 0)

    def test_marks_page_dirty_by_default(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        self.proj.set_current_img(PAGE_B)
        self.scene.rebuild()
        self._apply(20)
        self.assertTrue(self.proj.page_needs_rerender(PAGE))

    def test_can_skip_dirty_marking(self):
        self._set_blocks([_blk([100, 100, 140, 200])])
        self.proj.set_current_img(PAGE_B)
        self.scene.rebuild()
        self._apply(20, mark_dirty=False)
        self.assertFalse(self.proj.page_needs_rerender(PAGE))

    def test_multi_page_apply(self):
        self._set_blocks([_blk([100, 100, 140, 200])], PAGE)
        self._set_blocks([_blk([100, 100, 140, 200])], PAGE_B)
        report = self._apply(10)
        self.assertEqual(sorted(report["pages"]), sorted([PAGE, PAGE_B]))
        self.assertEqual(report["blocks"], 2)
        self.assertEqual(self.proj.page_generation(PAGE_B), 1)

    def test_pre_align_runs_before_plan(self):
        """D40 第 ① 步先于规划：面板刚拖过的矩形才是扩张的基准。"""
        self._set_blocks([_blk([100, 100, 140, 200])])

        def fake_sync():
            self.proj.pages[PAGE][0].xyxy = [120, 120, 160, 220]

        op = BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=fake_sync
        )
        BatchExpand(self.proj, op=op).apply(10)
        self.assertEqual(self._rect(), [110, 110, 170, 230])


if __name__ == "__main__":
    unittest.main()
