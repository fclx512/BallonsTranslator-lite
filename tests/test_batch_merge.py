"""批量合并相邻文本框（设计 §16、D7／D8／D30～D33／D40）回归。

覆盖 ``ui/batch_merge.py``：分组判据（同页 + 投影重叠 + 尺寸相近 + 相邻间隙）、
方向判定的三级与交叉验证、D30 的带标框排除、D33d 误聚标记、合并块的字段契约
（``text``／``lines``／``rich_text``／``tags``／``xyxy``）、D40 的写回顺序与验收
判据、D35 的整批撤回、D11／D31 的审批截图。

样本与阈值口径见模块 docstring——真机样本（94 页／1565 框）的规模复算在主机上
跑 ``plan()``，本文件只用合成项目锁行为。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_merge.py -q
"""

import copy
import os
import os.path as osp
import sys
import tempfile
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from ui.batch_merge import (  # noqa: E402
    SKIP_NO_BLOCKS,
    BatchMerge,
    MergeConfig,
    MergeGroup,
    _cluster,
    _direction_from_order,
    _pair_axis,
)
from ui.batch_ops import BatchOperation  # noqa: E402
from utils.block_actions import page_data_needs_sync  # noqa: E402
from utils.block_tags import MISREAD_TAG_ID, set_tag, set_tags_reviewed  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

PAGE = "p1.png"
PAGE_SIZE = (300, 400)  # (宽, 高)


def _line_rect(xyxy):
    x1, y1, x2, y2 = xyxy
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _blk(xyxy, text, *, vertical=None, translation="", rich_text="", tags=None, angle=0):
    blk = TextBlock(xyxy=list(xyxy), text=list(text))
    blk.src_is_vertical = vertical
    blk.translation = translation
    blk.rich_text = rich_text
    blk.angle = angle
    blk.lines = [_line_rect(xyxy)]
    if tags:
        blk.tags = tags
    return blk


def _row(y, text, x1=100, x2=200, height=30, **kw):
    """横排一行（高 30，宽度默认 100）。"""
    return _blk([x1, y, x2, y + height], text, **kw)


def _column(x, text, y1=10, y2=110, width=30, **kw):
    """竖排一列（宽 30，高度默认 100）。"""
    return _blk([x, y1, x + width, y2], text, **kw)


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


class _MergeTestCase(unittest.TestCase):
    """合成项目底座：一张 300×400 的页，块由各用例自行摆放。"""

    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 1
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        cv2.imwrite(
            osp.join(self.proj_dir, PAGE),
            np.zeros((PAGE_SIZE[1], PAGE_SIZE[0], 3), dtype=np.uint8),
        )
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages[PAGE] = []
        self.proj.save()
        self.proj.set_current_img(PAGE)
        self.scene = _FakeSceneManager(self.proj)

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self._tmp.cleanup()

    def _set_blocks(self, blocks):
        self.proj.pages[PAGE] = blocks
        self.scene.rebuild()

    def _task(self, **cfg):
        return BatchMerge(self.proj, config=MergeConfig(**cfg) if cfg else None)

    def _op(self):
        return BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=lambda: None
        )

    def _apply(self, selection=None, **kwargs):
        task = BatchMerge(self.proj, op=self._op())
        return task.apply(selection, **kwargs)


# ── 几何判据 ──────────────────────────────────────────────────────


class PairAxisTest(unittest.TestCase):
    def setUp(self):
        self.cfg = MergeConfig()

    def test_stacked_rows_pair_on_x(self):
        """上下相邻（共享 x 投影）→ 成组，返回 x。"""
        self.assertEqual(
            _pair_axis([10, 10, 110, 40], [10, 50, 110, 80], self.cfg), "x"
        )

    def test_side_by_side_columns_pair_on_y(self):
        self.assertEqual(
            _pair_axis([10, 10, 40, 110], [50, 10, 80, 110], self.cfg), "y"
        )

    def test_size_mismatch_rejected(self):
        """行高相近不成立（30 vs 90 → 0.33 < 0.6）→ 不成组。"""
        self.assertIsNone(_pair_axis([10, 10, 110, 40], [10, 50, 110, 140], self.cfg))

    def test_overlap_below_threshold_rejected(self):
        """交叉轴重叠只有 30%（< 0.6）→ 不成组。"""
        self.assertIsNone(
            _pair_axis([10, 10, 110, 40], [80, 50, 180, 80], self.cfg)
        )

    def test_gap_beyond_one_size_rejected(self):
        """间隙 > 一个行高 → 不算相邻（没有这条，整页同带框会一路连通）。"""
        self.assertIsNone(_pair_axis([10, 10, 110, 40], [10, 75, 110, 105], self.cfg))

    def test_touching_gap_accepted(self):
        """贴住（间隙 0）→ 相邻。"""
        self.assertEqual(
            _pair_axis([10, 10, 110, 40], [10, 40, 110, 70], self.cfg), "x"
        )

    def test_disjoint_rejected(self):
        self.assertIsNone(_pair_axis([10, 10, 110, 40], [200, 60, 300, 90], self.cfg))

    def test_cluster_chains_into_one_component(self):
        rects = [[10, 10, 110, 40], [10, 45, 110, 75], [10, 80, 110, 110]]
        comps, pairs = _cluster(rects, self.cfg)
        self.assertEqual(comps, [[0, 1, 2]])
        # 连通链上只有相邻的两对满足判据，首尾相隔 40 > 一个行高
        self.assertEqual(pairs, 2)

    def test_cluster_keeps_far_blocks_separate(self):
        rects = [[10, 10, 110, 40], [10, 45, 110, 75], [10, 300, 110, 330]]
        comps, _ = _cluster(rects, self.cfg)
        self.assertEqual(sorted(len(c) for c in comps), [1, 2])


class DirectionFromOrderTest(unittest.TestCase):
    def test_top_down_is_horizontal(self):
        self.assertFalse(_direction_from_order([[0, 0, 10, 10], [0, 20, 10, 30]]))

    def test_right_to_left_is_vertical(self):
        self.assertTrue(_direction_from_order([[30, 0, 40, 10], [0, 0, 10, 10]]))

    def test_single_member_is_undecidable(self):
        self.assertIsNone(_direction_from_order([[0, 0, 10, 10]]))

    def test_overlapping_members_are_undecidable(self):
        """位置重合（重复框）→ 判不出，交页／全书统计。"""
        self.assertIsNone(
            _direction_from_order([[0, 0, 10, 10], [0, 0, 10, 10]])
        )


# ── 规划 ──────────────────────────────────────────────────────────


class PlanTest(_MergeTestCase):
    def test_groups_bubble_rows_and_skips_isolated(self):
        self._set_blocks([
            _row(10, ["r1"], vertical=False),
            _row(45, ["r2"], vertical=False),
            _row(300, ["solo"], vertical=False),
        ])
        plan = self._task().plan()
        self.assertEqual(plan["group_count"], 1)
        self.assertEqual(plan["groups"][0].indices, [0, 1])
        self.assertEqual(plan["member_count"], 2)
        self.assertEqual(plan["apply_default"], 1)
        self.assertEqual(plan["pages"], {PAGE: 1})

    def test_vertical_columns_group_right_to_left(self):
        self._set_blocks([
            _column(10, ["L"], vertical=True),
            _column(50, ["R"], vertical=True),
        ])
        group = self._task().plan()["groups"][0]
        self.assertTrue(group.vertical)
        self.assertTrue(group.order_suspect)  # 列表序是左→右，与竖排阅读序相反

    def test_plan_is_read_only(self):
        self._set_blocks([_row(10, ["r1"]), _row(45, ["r2"])])
        before = copy.deepcopy([b.to_dict() for b in self.proj.pages[PAGE]])
        gen_before = self.proj.page_generation(PAGE)
        self._task().plan()
        self.assertEqual(
            [b.to_dict() for b in self.proj.pages[PAGE]], before
        )
        self.assertEqual(self.proj.page_generation(PAGE), gen_before)
        self.assertFalse(osp.exists(osp.join(self.proj_dir, ".bt_batch_backup")))

    def test_unreviewed_misread_block_is_excluded(self):
        """D30：带未驳回误识别标签的框整框排除，并计数上报。"""
        tagged = _row(45, ["r2"], vertical=False)
        set_tag(tagged, MISREAD_TAG_ID, "program", subtypes=["numeric"])
        self._set_blocks([_row(10, ["r1"], vertical=False), tagged, _row(80, ["r3"], vertical=False)])
        plan = self._task().plan()
        self.assertEqual(plan["excluded"], 1)
        self.assertEqual([g.indices for g in plan["groups"]], [])  # 剩下两块隔太远

    def test_reviewed_misread_block_participates(self):
        """驳回（reviewed）＝人已确认标签判错 → 视同普通框参与合并（D30）。"""
        tagged = _row(45, ["r2"], vertical=False)
        set_tag(tagged, MISREAD_TAG_ID, "program", subtypes=["numeric"])
        set_tags_reviewed(tagged, True)
        self._set_blocks([_row(10, ["r1"], vertical=False), tagged])
        plan = self._task().plan()
        self.assertEqual(plan["excluded"], 0)
        self.assertEqual(plan["groups"][0].indices, [0, 1])

    def test_rotated_block_is_not_merged(self):
        self._set_blocks([
            _row(10, ["r1"], vertical=False),
            _row(45, ["r2"], vertical=False, angle=15),
        ])
        plan = self._task().plan()
        self.assertEqual(plan["rotated"], 1)
        self.assertEqual(plan["group_count"], 0)
        self.assertEqual(plan["skipped"], {PAGE: "no-groups"})

    def test_oversize_group_flagged_and_not_default_checked(self):
        """D33d：组包围盒超页面 85% → 误聚标 + 不进默认勾选，但不剔除。"""
        self._set_blocks([
            _blk([10, 10, 150, 390], ["a"], vertical=False),
            _blk([160, 10, 300, 390], ["b"], vertical=False),
        ])
        plan = self._task().plan()
        self.assertEqual(plan["group_count"], 1)
        self.assertTrue(plan["groups"][0].oversize)
        self.assertEqual(plan["oversize"], 1)
        self.assertEqual(plan["apply_default"], 0)

    def test_group_direction_basis_group_vote(self):
        self._set_blocks([
            _row(10, ["r1"], vertical=False),
            _row(45, ["r2"], vertical=False),
        ])
        group = self._task().plan()["groups"][0]
        self.assertEqual((group.vertical, group.basis), (False, "group"))

    def test_group_direction_basis_order_when_no_src_direction(self):
        """组内无有效 src_is_vertical → 退到块顺序（D8 兜底第一级）。"""
        self._set_blocks([_row(10, ["r1"]), _row(45, ["r2"])])
        group = self._task().plan()["groups"][0]
        self.assertEqual((group.vertical, group.basis), (False, "order"))

    def test_group_direction_basis_page_when_order_undecidable(self):
        """块顺序也判不出（成员位置重合）→ 退到页统计（D8 兜底第二级）。"""
        dup = [10, 10, 40, 110]
        self._set_blocks([
            _blk(dup, ["a"]),          # src_is_vertical 全为 None
            _blk(dup, ["b"]),
            _column(250, ["vote"], vertical=True),  # 页内票源（距组太远，不进组）
        ])
        plan = self._task().plan()
        self.assertEqual(len(plan["groups"]), 1)
        group = plan["groups"][0]
        self.assertEqual((group.vertical, group.basis), (True, "page"))

    def test_group_direction_basis_book_when_page_has_no_vote(self):
        """页内也没票 → 退到全书统计（D8 兜底第三级）。"""
        dup = [10, 10, 40, 110]
        self._set_blocks([_blk(dup, ["a"]), _blk(dup, ["b"])])
        self.proj.pages["p2.png"] = [_column(10, ["x"], vertical=True)]
        self.proj.save()
        group = self._task().plan()["groups"][0]
        self.assertEqual((group.vertical, group.basis), (True, "book"))

    def test_direction_defaults_horizontal_when_nothing_votes(self):
        """页与全书都无票 → 按横排（default），不阻断合并。"""
        dup = [10, 10, 40, 110]
        self._set_blocks([_blk(dup, ["a"]), _blk(dup, ["b"])])
        group = self._task().plan()["groups"][0]
        self.assertEqual((group.vertical, group.basis), (False, "default"))

    def test_empty_page_reported_as_skipped(self):
        self._set_blocks([])
        plan = self._task().plan()
        self.assertEqual(plan["skipped"], {PAGE: SKIP_NO_BLOCKS})
        self.assertEqual(plan["groups"], [])

    def test_should_stop_cancels_plan(self):
        self._set_blocks([_row(10, ["r1"]), _row(45, ["r2"])])
        task = BatchMerge(self.proj, should_stop=lambda: True)
        plan = task.plan()
        self.assertTrue(plan["cancelled"])
        self.assertEqual(plan["group_count"], 0)

    def test_page_size_falls_back_to_image_header(self):
        """页尺寸优先取项目已存宽高；缺了就读图片文件头。"""
        self._set_blocks([_row(10, ["r1"]), _row(45, ["r2"])])
        self.proj._image_info[PAGE].pop("width", None)
        self.proj._image_info[PAGE].pop("height", None)
        self.assertEqual(self._task()._page_size(PAGE), PAGE_SIZE)


# ── 合并块字段契约 ────────────────────────────────────────────────


class MergedBlockTest(_MergeTestCase):
    def _merged(self, blocks, flatten_lines=True):
        self._set_blocks(blocks)
        task = self._task()
        group = task.plan()["groups"][0]
        return task.build_merged_block(PAGE, group, flatten_lines=flatten_lines)

    def _merged_from_group(self, blocks, indices, *, vertical=False, **kwargs):
        """直接构造组（绕开 D30 的排除——带未驳回误识别标签的框进不了 plan）。"""
        self._set_blocks(blocks)
        task = self._task()
        rects = [list(blk.xyxy) for blk in blocks]
        x1 = min(r[0] for r in rects)
        y1 = min(r[1] for r in rects)
        x2 = max(r[2] for r in rects)
        y2 = max(r[3] for r in rects)
        group = MergeGroup(
            pagename=PAGE,
            indices=list(indices),
            rects=rects,
            bbox=[x1, y1, x2, y2],
            vertical=vertical,
            basis="group",
            order_suspect=False,
            oversize=False,
        )
        return task.build_merged_block(PAGE, group, **kwargs)

    def test_flatten_lines_default_order_and_lines_same_order(self):
        merged = self._merged([
            _row(10, ["top", "line"], vertical=False),
            _row(45, ["bottom"], vertical=False),
        ])
        self.assertEqual(merged.text, ["top", "line", "bottom"])
        self.assertEqual(len(merged.lines), 2)  # 与 text 同序（契约一）

    def test_segment_mode_keeps_one_element_per_member(self):
        merged = self._merged(
            [
                _row(10, ["top", "line"], vertical=False),
                _row(45, ["bottom"], vertical=False),
            ],
            flatten_lines=False,
        )
        self.assertEqual(len(merged.text), 2)
        self.assertIn("top", merged.text[0])

    def test_vertical_group_concatenates_right_to_left(self):
        merged = self._merged([
            _column(10, ["L"], vertical=True),
            _column(50, ["R"], vertical=True),
        ])
        self.assertEqual(merged.text, ["R", "L"])

    def test_bbox_and_bounding_rect_take_union(self):
        merged = self._merged([_row(10, ["a"], vertical=False), _row(45, ["b"], vertical=False)])
        self.assertEqual(merged.xyxy, [100, 10, 200, 75])
        self.assertEqual(merged._bounding_rect, [100, 10, 100, 65])
        self.assertTrue(merged.merged)
        self.assertIsNone(merged.region_mask)
        self.assertIsNone(merged.region_inpaint_dict)

    def test_style_source_is_min_index_member(self):
        """D33b：样式来源＝页块列表中索引最小的成员，与阅读方向无关。"""
        first = _row(10, ["a"], vertical=False)
        first.fontformat.font_size = 40.0
        second = _row(45, ["b"], vertical=False)
        second.fontformat.font_size = 10.0
        merged = self._merged([first, second])
        self.assertEqual(merged.fontformat.font_size, 40.0)

    def test_translations_joined_in_reading_order(self):
        merged = self._merged([
            _row(10, ["a"], vertical=False, translation="一号"),
            _row(45, ["b"], vertical=False, translation=""),
            _row(80, ["c"], vertical=False, translation="三号"),
        ])
        self.assertEqual(merged.translation, "一号\n三号")

    def test_tags_union_with_subtypes(self):
        first = _row(10, ["a"], vertical=False)
        set_tag(first, MISREAD_TAG_ID, "program", subtypes=["numeric"])
        set_tag(first, "ocr_low_conf", "program", score=0.4)
        second = _row(45, ["b"], vertical=False)
        set_tag(second, MISREAD_TAG_ID, "program", subtypes=["symbolic"])
        merged = self._merged_from_group([first, second], [0, 1])
        self.assertEqual(
            sorted(merged.tags[MISREAD_TAG_ID]["subtypes"]), ["numeric", "symbolic"]
        )
        self.assertIn("ocr_low_conf", merged.tags)
        self.assertEqual(merged.tags["ocr_low_conf"]["score"], 0.4)

    def test_reviewed_survives_only_when_all_members_reviewed(self):
        """D33c：不静默洗白——有一个成员未驳回，合并块就仍是未驳回。"""
        mixed_a = _row(10, ["a"], vertical=False)
        set_tag(mixed_a, MISREAD_TAG_ID, "program", subtypes=["numeric"])
        set_tags_reviewed(mixed_a, True)
        mixed_b = _row(45, ["b"], vertical=False)
        set_tag(mixed_b, MISREAD_TAG_ID, "program", subtypes=["symbolic"])
        merged = self._merged_from_group([mixed_a, mixed_b], [0, 1])
        self.assertNotIn("reviewed", merged.tags[MISREAD_TAG_ID])

        both_a = _row(10, ["a"], vertical=False)
        set_tag(both_a, MISREAD_TAG_ID, "program", subtypes=["numeric"])
        set_tags_reviewed(both_a, True)
        both_b = _row(45, ["b"], vertical=False)
        set_tag(both_b, MISREAD_TAG_ID, "program", subtypes=["symbolic"])
        set_tags_reviewed(both_b, True)
        merged = self._merged_from_group([both_a, both_b], [0, 1])
        self.assertTrue(merged.tags[MISREAD_TAG_ID]["reviewed"])

    def test_rich_text_empty_when_no_member_has_annotations(self):
        """成员都无注解 → 留空＝按合并后的译文重排（渲染侧兜底分支）。"""
        merged = self._merged([
            _row(10, ["a"], vertical=False, translation="AA"),
            _row(45, ["b"], vertical=False, translation="BB"),
        ])
        self.assertEqual(merged.rich_text, "")

    def test_rich_text_rebuilt_from_member_annotations(self):
        """有成员带注解（译文 HTML）→ 重建成一份，含两段文字（契约二）。"""
        from qtpy.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        merged = self._merged([
            _row(10, ["a"], vertical=False, translation="AA", rich_text="<p>AA</p>"),
            _row(45, ["b"], vertical=False, translation="BB", rich_text="<p>BB</p>"),
        ])
        self.assertNotEqual(merged.rich_text, "")
        self.assertIn("AA", merged.rich_text)
        self.assertIn("BB", merged.rich_text)

    def test_build_returns_none_for_stale_indices(self):
        self._set_blocks([_row(10, ["a"], vertical=False), _row(45, ["b"], vertical=False)])
        group = MergeGroup(
            pagename=PAGE,
            indices=[0, 9],
            rects=[],
            bbox=[0, 0, 1, 1],
            vertical=False,
            basis="group",
            order_suspect=False,
            oversize=False,
        )
        self.assertIsNone(self._task().build_merged_block(PAGE, group))


# ── 写回与撤销 ────────────────────────────────────────────────────


class ApplyTest(_MergeTestCase):
    def _bubble(self):
        self._set_blocks([
            _row(10, ["a1"], vertical=False, translation="A1"),
            _row(45, ["a2"], vertical=False, translation="A2"),
            _row(200, ["b1"], vertical=False, translation="B1"),
            _row(235, ["b2"], vertical=False, translation="B2"),
            _row(320, ["solo"], vertical=False, translation="S"),
        ])

    def _open_other_page(self):
        """把当前页挪到别处：``mark_page_needs_rerender`` 自身排除当前页。"""
        other = "p2.png"
        cv2.imwrite(
            osp.join(self.proj_dir, other),
            np.zeros((PAGE_SIZE[1], PAGE_SIZE[0], 3), dtype=np.uint8),
        )
        self.proj.pages[other] = []
        self.proj.save()
        self.proj.set_current_img(other)
        self.scene.rebuild()  # 真场景管理器切页时同样会重建视觉层
        return other

    def test_apply_merges_default_groups_and_keeps_order(self):
        self._bubble()
        report = self._apply()
        self.assertTrue(report["started"])
        self.assertEqual(report["groups"], 2)
        self.assertEqual(report["blocks"], 4)  # 参与合并的原框数（两个气泡各两行）
        self.assertEqual(report["pages"], [PAGE])
        blocks = self.proj.pages[PAGE]
        self.assertEqual(len(blocks), 3)  # 两组各留一块 + 孤立块
        self.assertEqual(blocks[0].text, ["a1", "a2"])   # 落在组内最小索引处
        self.assertEqual(blocks[1].text, ["b1", "b2"])
        self.assertEqual(blocks[2].text, ["solo"])       # 未参与的块原样保留
        # D40 验收判据：数据层与视觉层不脱节
        self.assertTrue(report["writeback"]["verified"])
        self.assertFalse(
            page_data_needs_sync(self.proj.current_block_list(), self.scene.textblk_item_list)
        )
        # ③ 页代数（栈外整体换新）
        self.assertEqual(self.proj.page_generation(PAGE), 1)

    def test_apply_marks_page_dirty_by_default(self):
        """合并改的是文本层 → 结果图过期（与像素类批量任务相反）。

        当前页不进脏页表（``utils/proj_imgtrans.py::ProjImgTrans::mark_page_needs_rerender``
        自身排除当前页），故这里把当前页挪开再验。
        """
        self._bubble()
        self._open_other_page()
        self._apply()
        self.assertTrue(self.proj.page_needs_rerender(PAGE))

    def test_apply_can_skip_dirty_marking(self):
        self._bubble()
        self._open_other_page()
        self._apply(mark_dirty=False)
        self.assertFalse(self.proj.page_needs_rerender(PAGE))

    def test_apply_selection_limits_groups(self):
        self._bubble()
        self._apply([(PAGE, 2)])  # 只合并第二组
        blocks = self.proj.pages[PAGE]
        self.assertEqual(len(blocks), 4)
        self.assertIn("b1", [t for b in blocks for t in b.text])

    def test_apply_skips_oversize_group_by_default(self):
        self._set_blocks([
            _blk([10, 10, 150, 390], ["wide1"], vertical=False),
            _blk([160, 10, 300, 390], ["wide2"], vertical=False),
        ])
        report = self._apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "no-groups")
        self.assertEqual(len(self.proj.pages[PAGE]), 2)  # 误聚组不动

    def test_apply_unknown_selection_is_stale(self):
        self._bubble()
        report = self._apply([(PAGE, 99)])
        self.assertFalse(report["started"])
        self.assertEqual(report["stale"], [(PAGE, 99)])
        self.assertEqual(len(self.proj.pages[PAGE]), 5)

    def test_apply_cancel_before_write_changes_nothing(self):
        self._bubble()
        task = BatchMerge(self.proj, op=self._op(), should_stop=lambda: True)
        report = task.apply()
        self.assertFalse(report["started"])
        self.assertEqual(report["error"], "cancelled")
        self.assertEqual(len(self.proj.pages[PAGE]), 5)
        self.assertEqual(self.proj.page_generation(PAGE), 0)

    def test_pre_align_runs_before_building_merged_blocks(self):
        """D40 第 ① 步必须先于合并块构建：面板未回写的编辑不能被挡在新块之外。"""
        self._bubble()

        def fake_sync():
            self.proj.pages[PAGE][2].text = ["b1-edited"]

        op = BatchOperation(
            self.proj, self.scene, commit=self.proj.save, sync_block_data=fake_sync
        )
        report = BatchMerge(self.proj, op=op).apply([(PAGE, 2)])
        self.assertTrue(report["started"])
        self.assertEqual(self.proj.pages[PAGE][2].text, ["b1-edited", "b2"])

    def test_undo_restores_whole_batch(self):
        """D4／D35：整批一条撤回。"""
        self._bubble()
        task = BatchMerge(self.proj, op=self._op())
        report = task.apply()
        self.assertTrue(report["started"])
        task.op.undo_last(expect_seq=report["version"].seq)
        blocks = self.proj.pages[PAGE]
        self.assertEqual(len(blocks), 5)
        self.assertEqual(
            [t for b in blocks for t in b.text], ["a1", "a2", "b1", "b2", "solo"]
        )


# ── 审批截图（D11／D31）────────────────────────────────────────────


class CropTest(_MergeTestCase):
    def _group(self, indices, bbox):
        return MergeGroup(
            pagename=PAGE,
            indices=indices,
            rects=[],
            bbox=bbox,
            vertical=False,
            basis="group",
            order_suspect=False,
            oversize=False,
        )

    def test_crop_is_full_scale_and_expanded_by_half_short_edge(self):
        self._set_blocks([_blk([100, 150, 200, 180], ["a"], vertical=False)])
        group = self._group([0], [100, 150, 200, 180])  # 短边 30 → 外扩 15
        crop = self._task().group_crop(group)
        self.assertEqual(crop.shape[:2], (60, 130))  # 100% 原比例，未缩放
        self.assertEqual(self._task().crop_rect(group, PAGE_SIZE), [85, 135, 215, 195])

    def test_expansion_stops_at_neighbor(self):
        """外扩碰到邻框即停（D31）：右侧被邻框左缘截住。"""
        self._set_blocks([
            _blk([100, 150, 200, 180], ["a"], vertical=False),
            _blk([240, 150, 280, 200], ["nb"], vertical=False),
        ])
        group = self._group([0], [100, 150, 200, 180])
        # 邻框在 240：外扩 15 到 215 够不着它，照常外扩
        self.assertEqual(self._task().crop_rect(group, PAGE_SIZE)[2], 215)
        # 邻框挪进外扩路径（210）→ 停在邻框左缘
        self.proj.pages[PAGE][1].xyxy = [210, 150, 280, 200]
        self.assertEqual(self._task().crop_rect(group, PAGE_SIZE)[2], 210)

    def test_crop_clamped_to_page(self):
        self._set_blocks([_blk([2, 2, 62, 62], ["a"], vertical=False)])
        rect = self._task().crop_rect(self._group([0], [2, 2, 62, 62]), PAGE_SIZE)
        self.assertEqual(rect, [0, 0, 92, 92])

    def test_crop_returns_none_for_missing_image(self):
        self._set_blocks([_row(10, ["a"], vertical=False)])
        os.remove(osp.join(self.proj_dir, PAGE))
        self.assertIsNone(self._task().group_crop(self._group([0], [100, 10, 200, 40])))


if __name__ == "__main__":
    unittest.main()
