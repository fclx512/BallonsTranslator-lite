"""区域再检测（人工拉框 → 只在框内跑「检测 + OCR」）回归。

覆盖 ``ui/region_redetect.py``（任务层）与 ``ui/region_redetect_tool.py``
（UI 层的撤销命令与控制器）：

- 裁剪外扩与钳制、坐标回映射（``lines`` 每个顶点平移）、区域外过滤（按四边形
  顶点中心）、``det_model``／检测器名；
- 重叠替换判据（决策 4：分母取较小者，"区域级大框被逐行小框压住"也算替换）；
- 掩码贴回页级（决策 6：并集 + 落盘 + 图像代数）；
- 样式继承与字号（决策 7 + 例外：字号按实际检出框量出来，不继承邻居的）；
- 阅读顺序坐标插入（方案 §八）与其三重兜底（块太少／全判不出／异常）；
- 一次拉框 = 一步撤销（``RedetectCommand`` 的 redo／undo 两端snapshot）。

几何判据本身的行为在 ``tests/test_block_geometry.py``；实机样本的验收（063.jpg
手机屏 5~6 块、047.jpeg 两组旁白）见 ``tmp/区域再检测_方案_2026-09-17.md``
第七节，本文件只用合成项目锁行为。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_region_redetect.py -q
"""

import copy
import os
import os.path as osp
import sys
import tempfile
import types
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import ui.region_redetect as redetect_module  # noqa: E402
from ui.region_redetect import (  # noqa: E402
    INSERT_APPEND,
    INSERT_BEFORE,
    INSERT_ERROR,
    INSERT_FEW_BLOCKS,
    SKIP_NO_DETECTION,
    SKIP_NO_IMAGE,
    SKIP_TOO_SMALL,
    RedetectConfig,
    RegionRedetect,
    group_center,
    insert_index,
    new_precedes,
    page_direction,
)
from ui.region_redetect_tool import RedetectCommand, RegionRedetectTool  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

PAGE = "p1.png"
PAGE_W, PAGE_H = 300, 400
USER_RECT = [100, 100, 200, 180]
EXPAND = 24
# USER_RECT 外扩 24 且未触页边界 → 裁剪矩形
CROP_RECT = [76, 76, 224, 204]


# ── 合成件 ────────────────────────────────────────────────────────


def _quad(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _blk(x1, y1, x2, y2, *, vertical=False, text=None, family=None, font_size=None):
    """**页级坐标**的块（假检测器会按裁剪偏移换算再交回被测代码）。"""
    blk = TextBlock(lines=[_quad(x1, y1, x2, y2)])
    blk.src_is_vertical = vertical
    blk.vertical = vertical
    blk.adjust_bbox()
    if text:
        blk.text = list(text)
    if family is not None:
        blk.fontformat.font_family = family
    if font_size is not None:
        blk.fontformat.font_size = font_size
    return blk


class _FakeDetector:
    """按 ``TextDetectorBase.detect`` 的契约产出 ``(mask, blk_list)``。

    构造时给**页级坐标**的块，``detect`` 把它们换算到裁剪坐标系（模拟真实
    检测器只看得到裁剪图），被测代码再回映射到页级——这条往返正是要锁的行为。
    """

    def __init__(self, page_blocks, crop_xy, name="fake_det"):
        self.name = name
        self._blocks = list(page_blocks)
        self._crop_xy = tuple(crop_xy)

    def detect(self, img, proj=None):
        dx, dy = self._crop_xy
        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        out = []
        for src in self._blocks:
            blk = copy.deepcopy(src)
            blk.lines = [
                [[float(p[0]) - dx, float(p[1]) - dy] for p in line]
                for line in src.lines
            ]
            for line in blk.lines:
                cv2.fillPoly(mask, [np.array(line, np.int32)], 255)
            blk.det_model = self.name
            out.append(blk)
        return mask, out


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


class _FakeCanvas:
    def __init__(self, proj):
        self.imgtrans_proj = proj
        self.pushed = []
        self.layer_updates = 0
        self.mode = False

    def push_undo_command(self, command, update_pushed_step=True):
        self.pushed.append(command)
        command.redo()

    def updateLayers(self):
        self.layer_updates += 1

    def setRegionRedetectMode(self, enabled):
        self.mode = bool(enabled)


class _FakeRunningThread:
    def __init__(self, running=False):
        self._running = running

    def isRunning(self):
        return self._running


class _FakeModuleManager:
    def __init__(self, ocr_module=None, pipeline_running=False):
        self.imgtrans_thread = _FakeRunningThread(pipeline_running)
        self.ocr_thread = types.SimpleNamespace(module=ocr_module)


class _RedetectTestCase(unittest.TestCase):
    """合成项目底座：一张 300×400 的页 + 一个假检测器。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        cv2.imwrite(
            osp.join(self.proj_dir, PAGE),
            np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8),
        )
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages[PAGE] = []
        self.proj.save()
        self.proj.set_current_img(PAGE)
        os.makedirs(self.proj.mask_dir(), exist_ok=True)
        self.scene = _FakeSceneManager(self.proj)
        self.canvas = _FakeCanvas(self.proj)

    def tearDown(self):
        self._tmp.cleanup()

    def _set_blocks(self, blocks):
        self.proj.pages[PAGE] = list(blocks)
        self.scene.rebuild()

    def _task(self, detector_blocks=None, *, detector_name="fake_det", **cfg):
        detector = (
            None
            if detector_blocks is None
            else _FakeDetector(
                detector_blocks,
                (CROP_RECT[0], CROP_RECT[1]),
                name=detector_name or "fake_det",
            )
        )
        return RegionRedetect(
            self.proj,
            config=RedetectConfig(**cfg) if cfg else None,
            detector=detector,
            detector_name=detector_name,
        )

    def _plan(self, detector_blocks, rect=None, **cfg):
        task = self._task(detector_blocks, **cfg)
        return task, task.plan(PAGE, USER_RECT if rect is None else rect)


# ── plan：裁剪 / 回映射 / 过滤 / 替换 / 掩码 ──────────────────────


class PlanTest(_RedetectTestCase):
    def test_remaps_lines_to_page_coordinates(self):
        """回映射：裁剪坐标 + 偏移 = 页级坐标，xyxy 同步。"""
        found = _blk(120, 120, 180, 140)
        _, plan = self._plan([found])
        self.assertIsNone(plan.skip)
        self.assertEqual(len(plan.new_blocks), 1)
        blk = plan.new_blocks[0]
        self.assertEqual(blk.xyxy, [120, 120, 180, 140])
        self.assertEqual(blk.lines[0][0], [120.0, 120.0])
        self.assertEqual(blk.det_model, "fake_det")
        self.assertEqual(plan.detector, "fake_det")
        self.assertEqual(plan.crop_rect, CROP_RECT)

    def test_filters_blocks_outside_the_user_rect(self):
        """区域外过滤（决策 3）：中心点落在用户框外的块被剔除并计数。"""
        inside = _blk(120, 120, 180, 140)
        outside = _blk(80, 80, 96, 96)  # 在裁剪内、在用户框外
        _, plan = self._plan([inside, outside])
        self.assertEqual(len(plan.new_blocks), 1)
        self.assertEqual(plan.dropped_outside, 1)

    def test_skip_when_box_too_small(self):
        _, plan = self._plan([_blk(120, 120, 180, 140)], rect=[100, 100, 105, 104])
        self.assertEqual(plan.skip, SKIP_TOO_SMALL)
        self.assertFalse(plan.ok)

    def test_skip_when_no_detection(self):
        _, plan = self._plan([])
        self.assertEqual(plan.skip, SKIP_NO_DETECTION)
        self.assertFalse(plan.ok)

    def test_skip_when_everything_is_filtered_out(self):
        _, plan = self._plan([_blk(80, 80, 96, 96)])
        self.assertEqual(plan.skip, SKIP_NO_DETECTION)
        self.assertEqual(plan.dropped_outside, 1)

    def test_skip_when_page_image_missing(self):
        task = self._task([_blk(120, 120, 180, 140)])
        plan = task.plan("missing.png", USER_RECT)
        self.assertEqual(plan.skip, SKIP_NO_IMAGE)

    def test_detects_on_source_image_not_inpainted(self):
        """检测必须用原图：把修复图换成尺寸不对的图，链路仍应正常。"""
        self.proj.inpainted_array = np.zeros((10, 10, 3), dtype=np.uint8)
        _, plan = self._plan([_blk(120, 120, 180, 140)])
        self.assertEqual(len(plan.new_blocks), 1)

    def test_replaces_overlapping_existing_block(self):
        """决策 4：新块压住旧块过半 → 替换；只是擦边的旧块保留。"""
        old_big = _blk(110, 110, 190, 150)  # 新块几乎整块落在它里面
        old_far = _blk(250, 300, 290, 340)
        self._set_blocks([old_big, old_far])
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        self.assertEqual(plan.replaced_indices, [0])
        report = task.build_page(plan)
        self.assertEqual(report["replaced"], [0])
        self.assertEqual(len(report["blocks"]), 2)  # 1 保留 + 1 新增

    def test_rotated_box_uses_real_polygon_for_replacement(self):
        """倾斜旧块：按外接矩形会误判替换，按真实四边形不会。"""
        tilted = _blk(118, 105, 200, 123)
        tilted.lines = [[[200, 115], [120, 105], [118, 113], [198, 123]]]
        tilted.adjust_bbox()
        self._set_blocks([tilted])
        _, plan = self._plan([_blk(118, 117, 138, 125)])
        self.assertEqual(plan.replaced_indices, [])

    def test_crop_mask_matches_crop_rect(self):
        _, plan = self._plan([_blk(120, 120, 180, 140)])
        self.assertEqual(
            plan.mask.shape, (CROP_RECT[3] - CROP_RECT[1], CROP_RECT[2] - CROP_RECT[0])
        )
        self.assertEqual(int(plan.mask.sum() > 0), 1)


# ── 样式继承与字号 ────────────────────────────────────────────────


class StyleTest(_RedetectTestCase):
    def test_inherits_fontformat_but_measures_font_size(self):
        source = _blk(
            250, 300, 290, 340, vertical=True, family="SrcFamily", font_size=40
        )
        self._set_blocks([source])
        _, plan = self._plan([_blk(120, 120, 180, 140)])
        new = plan.new_blocks[0]
        self.assertEqual(new.fontformat.font_family, "SrcFamily")
        # 字号按实际检出框量出来（高 20px），不是继承来的 40
        self.assertEqual(new.font_size, 20)
        self.assertEqual(new._detected_font_size, 20)
        # 渲染方向取原文方向，不继承邻居的
        self.assertFalse(new.vertical)
        self.assertFalse(new.src_is_vertical)

    def test_vertical_block_font_size_is_width(self):
        _, plan = self._plan([_blk(120, 120, 140, 180, vertical=True)])
        new = plan.new_blocks[0]
        self.assertEqual(new.font_size, 20)
        self.assertTrue(new.vertical)

    def test_no_existing_block_keeps_default_style(self):
        _, plan = self._plan([_blk(120, 120, 180, 140)])
        self.assertEqual(len(plan.new_blocks), 1)


# ── 掩码贴回 ──────────────────────────────────────────────────────


class MaskTest(_RedetectTestCase):
    def test_paste_mask_unions_and_saves(self):
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        before_gen = self.proj.page_image_generation(PAGE)
        self.assertTrue(task.paste_mask(plan))
        mask = self.proj.mask_array
        self.assertGreater(int(mask[120:140, 120:180].sum()), 0)
        self.assertEqual(int(mask[0:50, 0:50].sum()), 0)
        self.assertGreater(self.proj.page_image_generation(PAGE), before_gen)
        self.assertTrue(osp.exists(self.proj.get_mask_path(PAGE)))

    def test_paste_mask_keeps_existing_pixels(self):
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        self.proj.mask_array[10:20, 10:20] = 255
        self.assertTrue(task.paste_mask(plan))
        self.assertEqual(int(self.proj.mask_array[10:20, 10:20].sum()), 255 * 100)

    def test_paste_mask_skips_non_current_page(self):
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        plan.page_key = "other.png"
        self.assertFalse(task.paste_mask(plan))


# ── 阅读顺序：整组坐标插入与兜底 ──────────────────────────────────


class InsertionTest(unittest.TestCase):
    def test_inserts_between_rows(self):
        blocks = [_blk(100, 10, 200, 30), _blk(100, 50, 200, 70), _blk(100, 90, 200, 110)]
        idx, why = insert_index([_blk(100, 35, 200, 45)], blocks, rtl=True)
        self.assertEqual((idx, why), (1, INSERT_BEFORE))

    def test_before_first_and_after_last(self):
        blocks = [_blk(100, 50, 200, 70), _blk(100, 90, 200, 110)]
        self.assertEqual(
            insert_index([_blk(100, 10, 200, 30)], blocks, rtl=True)[0], 0
        )
        self.assertEqual(
            insert_index([_blk(100, 200, 200, 220)], blocks, rtl=True)[0], len(blocks)
        )

    def test_same_row_follows_page_direction(self):
        """同一行内比 x：已排好的两块分别是右到左／左到右，新块都落在中间。"""
        rtl_blocks = [_blk(200, 50, 240, 70), _blk(100, 50, 140, 70)]
        self.assertEqual(
            insert_index([_blk(150, 50, 190, 70)], rtl_blocks, rtl=True),
            (1, INSERT_BEFORE),
        )
        ltr_blocks = [_blk(100, 50, 140, 70), _blk(200, 50, 240, 70)]
        self.assertEqual(
            insert_index([_blk(150, 50, 190, 70)], ltr_blocks, rtl=False),
            (1, INSERT_BEFORE),
        )

    def test_lower_row_block_does_not_jump_to_the_front(self):
        """跨行不比 x：右下角的框属于最后一行的队尾，不该插到最前面。

        回归实测（``projects/004_819b9e93/004.jpeg``）：旧判据只比 x，右下角的
        框比右上角的更靠右，于是被判成"排在已有块之前"，插到了第 1 位。
        """
        blocks = [
            _blk(800, 130, 890, 280),  # 第一行（右）
            _blk(620, 150, 670, 250),  # 第一行（左）
            _blk(170, 970, 240, 1160),  # 第二行（左）
        ]
        idx, why = insert_index([_blk(760, 970, 860, 1170)], blocks, rtl=True)
        self.assertEqual((idx, why), (2, INSERT_BEFORE))

    def test_columns_follow_page_direction(self):
        blocks = [_blk(220, 10, 250, 120, vertical=True),
                  _blk(150, 10, 180, 120, vertical=True),
                  _blk(80, 10, 110, 120, vertical=True)]
        idx, why = insert_index([_blk(185, 10, 215, 120, vertical=True)], blocks, rtl=True)
        self.assertEqual((idx, why), (1, INSERT_BEFORE))

    def test_ltr_flips_the_column_order(self):
        blocks = [_blk(80, 10, 110, 120, vertical=True),
                  _blk(150, 10, 180, 120, vertical=True)]
        idx, _ = insert_index([_blk(185, 10, 215, 120, vertical=True)], blocks, rtl=False)
        self.assertEqual(idx, len(blocks))
        idx, _ = insert_index([_blk(120, 10, 145, 120, vertical=True)], blocks, rtl=False)
        self.assertEqual(idx, 1)

    def test_same_column_uses_y(self):
        """同一列内的两段（纵向跨度不重叠）按 y 自上而下。"""
        blocks = [
            _blk(100, 10, 130, 50, vertical=True),
            _blk(100, 90, 130, 130, vertical=True),
        ]
        idx, why = insert_index([_blk(100, 55, 130, 85, vertical=True)], blocks, rtl=True)
        self.assertEqual((idx, why), (1, INSERT_BEFORE))

    def test_same_row_and_position_is_undecidable(self):
        """同一行、x 也分不出先后（新块整段落在一段更长的旧块里）→ 追加到末尾。"""
        blocks = [
            _blk(100, 10, 130, 60, vertical=True),
            _blk(100, 60, 130, 160, vertical=True),
        ]
        idx, why = insert_index([_blk(100, 70, 130, 100, vertical=True)], blocks, rtl=True)
        self.assertEqual((idx, why), (2, INSERT_APPEND))

    def test_group_shares_one_index_and_stays_contiguous(self):
        """一次手势的新块整组共用一个落点（组内保持检测器给的顺序）。"""
        blocks = [
            _blk(800, 130, 890, 280),  # 第一行
            _blk(170, 970, 240, 1160),  # 第二行
            _blk(80, 1130, 120, 1270),  # 第三行
        ]
        group = [_blk(840, 970, 880, 1160), _blk(760, 990, 800, 1150)]
        self.assertEqual(insert_index(group, blocks, rtl=True), (1, INSERT_BEFORE))

    def test_group_center_is_the_mean_of_member_centers(self):
        group = [_blk(100, 100, 140, 140), _blk(200, 200, 240, 260)]
        self.assertEqual(group_center(group), [170.0, 175.0])
        self.assertIsNone(group_center([]))

    def test_falls_back_when_no_reference_block(self):
        idx, why = insert_index([_blk(100, 10, 200, 30)], [], rtl=True)
        self.assertEqual((idx, why), (0, INSERT_FEW_BLOCKS))

    def test_single_reference_block_is_enough(self):
        """1 个参照块也够用：判据只在同行内才需要页方向。"""
        blocks = [_blk(250, 300, 290, 340)]
        self.assertEqual(
            insert_index([_blk(120, 120, 180, 140)], blocks, rtl=True),
            (0, INSERT_BEFORE),
        )
        self.assertEqual(
            insert_index([_blk(120, 350, 180, 370)], blocks, rtl=True),
            (1, INSERT_APPEND),
        )

    def test_falls_back_when_undecidable(self):
        """位置完全重合（同行且 x 相同）→ 追加到末尾。"""
        blocks = [_blk(100, 50, 200, 70), _blk(100, 50, 200, 70)]
        idx, why = insert_index([_blk(100, 50, 200, 70)], blocks, rtl=True)
        self.assertEqual((idx, why), (2, INSERT_APPEND))

    def test_falls_back_when_comparison_raises(self):
        blocks = [_blk(100, 50, 200, 70), _blk(100, 90, 200, 110)]

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        original = redetect_module.new_precedes
        redetect_module.new_precedes = _boom
        try:
            idx, why = insert_index([_blk(100, 65, 200, 80)], blocks, rtl=True)
        finally:
            redetect_module.new_precedes = original
        self.assertEqual((idx, why), (2, INSERT_ERROR))

    def test_page_direction_votes_from_block_order(self):
        rtl_blocks = [_blk(200, 10, 230, 100, vertical=True),
                      _blk(100, 10, 130, 100, vertical=True)]
        self.assertEqual(page_direction(rtl_blocks), (True, "vote"))
        ltr_blocks = list(reversed(rtl_blocks))
        self.assertEqual(page_direction(ltr_blocks), (False, "vote"))

    def test_page_direction_ignores_cross_row_pairs(self):
        """跨行的相邻对不投票：只有第一行的两块同行，投出右到左。"""
        blocks = [
            _blk(200, 10, 230, 100, vertical=True),
            _blk(100, 10, 130, 100, vertical=True),
            _blk(300, 400, 330, 500, vertical=True),  # 下一行，与上面两块都不同行
        ]
        self.assertEqual(page_direction(blocks), (True, "vote"))

    def test_page_direction_defaults_without_samples(self):
        self.assertEqual(page_direction([]), (True, "default"))
        self.assertEqual(page_direction([_blk(100, 10, 130, 100)]), (True, "default"))

    def test_new_precedes_returns_none_without_geometry(self):
        blk = _blk(100, 50, 200, 70)
        blk.lines = []
        self.assertIsNone(new_precedes([150.0, 60.0], blk, rtl=True))


# ── build_page / apply ────────────────────────────────────────────


class BuildPageTest(_RedetectTestCase):
    def test_keeps_unspecified_blocks_in_order(self):
        # 三行都在用户框内（决策 3 的中心点判据要求），新块插在第 1、2 行之间
        a = _blk(100, 105, 200, 125)
        b = _blk(100, 140, 200, 160)
        c = _blk(100, 170, 200, 190)
        self._set_blocks([a, b, c])
        task, plan = self._plan([_blk(100, 128, 200, 136)])
        report = task.build_page(plan)
        self.assertEqual(report["kept"], 3)
        self.assertEqual(report["blocks"][1].xyxy, [100, 128, 200, 136])
        self.assertIs(report["blocks"][0], a)
        self.assertIs(report["blocks"][2], b)
        self.assertIs(report["blocks"][3], c)
        self.assertEqual(report["inserted"], [(1, INSERT_BEFORE)])

    def test_build_page_places_above_a_single_neighbor(self):
        only = _blk(250, 300, 290, 340)
        self._set_blocks([only])
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        report = task.build_page(plan)
        self.assertEqual(len(report["blocks"]), 2)
        self.assertIs(report["blocks"][1], only)
        self.assertEqual(report["inserted"], [(0, INSERT_BEFORE)])

    def test_build_page_keeps_a_group_together(self):
        a = _blk(100, 105, 200, 125)
        b = _blk(100, 170, 200, 190)
        self._set_blocks([a, b])
        task, plan = self._plan([_blk(120, 128, 160, 148), _blk(165, 128, 200, 148)])
        report = task.build_page(plan)
        self.assertEqual(report["inserted"], [(1, INSERT_BEFORE), (2, INSERT_BEFORE)])
        self.assertIs(report["blocks"][0], a)
        self.assertIs(report["blocks"][3], b)

    def test_apply_writes_pages(self):
        old = _blk(110, 110, 190, 150)
        self._set_blocks([old])
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        report = task.apply(plan)
        self.assertTrue(report["applied"])
        self.assertEqual(len(self.proj.pages[PAGE]), 1)
        self.assertIsNot(self.proj.pages[PAGE][0], old)
        self.assertTrue(report["mask_written"])

    def test_apply_skips_when_plan_is_empty(self):
        task = self._task([])
        _, plan = self._plan([])
        self.assertFalse(task.apply(plan)["applied"])

    def test_apply_persists_through_save_and_reload(self):
        """落盘后重开项目：新块的几何／字号／``det_model`` 仍在。"""
        self._set_blocks([_blk(250, 300, 290, 340)])
        task, plan = self._plan([_blk(120, 120, 180, 140)])
        task.apply(plan)
        self.proj.save()

        reloaded = ProjImgTrans(directory=self.proj_dir)
        blocks = reloaded.pages[PAGE]
        self.assertEqual(len(blocks), 2)
        added = [b for b in blocks if b.det_model == "fake_det"]
        self.assertEqual(len(added), 1)
        new = added[0]
        self.assertEqual(new.xyxy, [120, 120, 180, 140])
        self.assertEqual(new.lines[0][0], [120, 120])
        self.assertEqual(int(new.font_size), 20)


# ── 撤销命令（一次拉框 = 一步撤销）───────────────────────────────


class CommandTest(_RedetectTestCase):
    def _push(self, detector_blocks, existing):
        self._set_blocks(existing)
        task, plan = self._plan(detector_blocks)
        command = RedetectCommand(self.canvas, self.scene, task, plan)
        return task, plan, command

    def test_constructor_writes_nothing(self):
        existing = [_blk(110, 110, 190, 150), _blk(250, 300, 290, 340)]
        task, plan, command = self._push([_blk(120, 120, 180, 140)], existing)
        self.assertEqual(len(self.proj.pages[PAGE]), 2)
        self.assertIs(self.proj.pages[PAGE][0], existing[0])
        self.assertEqual(command.report["replaced"], [0])

    def test_redo_applies_and_undo_restores_everything(self):
        existing = [_blk(110, 110, 190, 150), _blk(250, 300, 290, 340)]
        task, plan, command = self._push([_blk(120, 120, 180, 140)], existing)

        self.canvas.push_undo_command(command)
        applied = list(self.proj.pages[PAGE])
        # 被替换的 existing[0] 出局；新块在保留块之上 → 插到它前面
        self.assertEqual(len(applied), 2)
        self.assertEqual(applied[0].xyxy, [120, 120, 180, 140])
        self.assertIs(applied[1], existing[1])
        self.assertGreater(int(self.proj.mask_array[120:140, 120:180].sum()), 0)
        self.assertEqual(self.scene.rebuilds, 1)

        command.undo()
        restored = list(self.proj.pages[PAGE])
        self.assertEqual(len(restored), 2)
        self.assertIs(restored[0], existing[0])
        self.assertIs(restored[1], existing[1])
        self.assertEqual(self.scene.rebuilds, 2)

        command.redo()
        self.assertEqual(
            [b.xyxy for b in self.proj.pages[PAGE]], [b.xyxy for b in applied]
        )
        self.assertEqual(self.scene.rebuilds, 3)
        self.assertGreaterEqual(self.canvas.layer_updates, 3)

    def test_command_keeps_mask_on_undo(self):
        """掩码是派生的像素数据，撤销块的写入不回退它（方案未列入撤销范围）。"""
        existing = [_blk(250, 300, 290, 340)]
        task, plan, command = self._push([_blk(120, 120, 180, 140)], existing)
        self.canvas.push_undo_command(command)
        command.undo()
        self.assertGreater(int(self.proj.mask_array[120:140, 120:180].sum()), 0)


# ── 控制器接线 ────────────────────────────────────────────────────


class ToolTest(_RedetectTestCase):
    def _tool(self, ocr_module=None, pipeline_running=False, detector_blocks=None):
        task = self._task(detector_blocks or [])
        tool = RegionRedetectTool(
            self.proj,
            self.canvas,
            self.scene,
            _FakeModuleManager(ocr_module, pipeline_running),
        )
        tool.task = task
        return tool

    def test_mode_toggles_canvas(self):
        tool = self._tool()
        tool.set_mode(True)
        self.assertTrue(self.canvas.mode)
        tool.set_mode(False)
        self.assertFalse(self.canvas.mode)

    def test_on_plan_ready_pushes_one_command(self):
        self._set_blocks([_blk(250, 300, 290, 340)])
        tool = self._tool(detector_blocks=[_blk(120, 120, 180, 140)])
        reports = []
        tool.completed.connect(reports.append)
        task = tool.task
        plan = task.plan(PAGE, USER_RECT)
        tool._on_plan_ready(plan)
        self.assertEqual(len(self.canvas.pushed), 1)
        self.assertTrue(reports[0]["applied"])
        self.assertEqual(reports[0]["added"], 1)
        self.assertEqual(reports[0]["replaced"], [])

    def test_on_plan_ready_drops_result_when_page_changed(self):
        tool = self._tool(detector_blocks=[_blk(120, 120, 180, 140)])
        reports = []
        tool.completed.connect(reports.append)
        plan = tool.task.plan(PAGE, USER_RECT)
        plan.page_key = "another.png"
        tool._on_plan_ready(plan)
        self.assertEqual(self.canvas.pushed, [])
        self.assertFalse(reports[0]["applied"])

    def test_on_plan_ready_skips_empty_plan(self):
        tool = self._tool()
        reports = []
        tool.completed.connect(reports.append)
        plan = tool.task.plan(PAGE, USER_RECT)
        tool._on_plan_ready(plan)
        self.assertEqual(self.canvas.pushed, [])
        self.assertEqual(reports[0]["error"], SKIP_NO_DETECTION)

    def test_request_rejects_running_pipeline(self):
        tool = self._tool(pipeline_running=True)
        from qtpy.QtCore import QRectF

        self.assertFalse(tool.request(QRectF(100, 100, 100, 80)))
        self.assertEqual(self.canvas.pushed, [])

    def test_request_rejects_tiny_box(self):
        tool = self._tool()
        from qtpy.QtCore import QRectF

        self.assertFalse(tool.request(QRectF(100, 100, 3, 3)))
        self.assertEqual(self.canvas.pushed, [])

    def test_releases_detector_after_each_gesture(self):
        """用完即卸：成功、空结果、失败三条路径都不把会话留成常驻内存。"""
        tool = self._tool(detector_blocks=[_blk(120, 120, 180, 140)])
        released = []
        tool.task.unload_detector = lambda *a, **k: released.append(1)
        tool._on_plan_ready(tool.task.plan(PAGE, USER_RECT))
        self.assertEqual(len(released), 1)
        tool._on_plan_ready(tool.task.plan(PAGE, [10, 10, 12, 12]))  # 框太小
        self.assertEqual(len(released), 2)
        tool._on_plan_ready(None)
        self.assertEqual(len(released), 3)


class GestureTest(unittest.TestCase):
    """画布手势的落点分流（``ui/canvas.py`` 的两处判断）。

    区域再检测复用"拉框"手势，只在结束的那一刻改发
    ``region_redetect_rect``；正常模式下仍是"建一个空文本框"。
    """

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        from ui.canvas import Canvas

        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.canvas = Canvas()
        cls.canvas.setSceneRect(0, 0, 300, 300)
        cls.canvas.baseLayer.setRect(0, 0, 300, 300)

    def setUp(self):
        self.canvas.editor_index = 1  # 文本框编辑页
        self.canvas.setRegionRedetectMode(False)
        self.redetects = []
        self.creates = []
        self.canvas.region_redetect_rect.connect(self.redetects.append)
        self.canvas.end_create_textblock.connect(self.creates.append)

    def _drag(self, rect=(20, 20, 100, 60)):
        """``rect`` ＝ ``QRectF`` 的 ``(x, y, w, h)``。"""
        from qtpy.QtCore import QPointF, QRectF

        self.canvas.startCreateTextblock(QPointF(rect[0], rect[1]), hide_control=True)
        self.canvas.txtblkShapeControl.setRect(QRectF(*rect))
        self.canvas.endCreateTextblock(btn=0)

    def test_region_mode_routes_to_redetect(self):
        self.canvas.setRegionRedetectMode(True)
        self._drag()
        self.assertEqual(len(self.redetects), 1)
        self.assertEqual(self.creates, [])
        self.assertEqual(
            [self.redetects[0].x(), self.redetects[0].y()], [20.0, 20.0]
        )

    def test_normal_mode_creates_block(self):
        self._drag()
        self.assertEqual(self.creates and len(self.creates), 1)
        self.assertEqual(self.redetects, [])

    def test_region_mode_needs_text_edit_page(self):
        """绘图页（editor_index=0）不接管——那里文本框层是隐藏的。"""
        self.canvas.editor_index = 0
        self.canvas.setRegionRedetectMode(True)
        self._drag()
        self.assertEqual(self.redetects, [])
        self.assertEqual(len(self.creates), 1)

    def test_tiny_drag_emits_nothing(self):
        self.canvas.setRegionRedetectMode(True)
        self._drag(rect=(20, 20, 0.5, 0.5))
        self.assertEqual(self.redetects, [])
        self.assertEqual(self.creates, [])

    def test_switching_mode_aborts_inflight_drag(self):
        from qtpy.QtCore import QPointF

        self.canvas.startCreateTextblock(QPointF(20, 20), hide_control=True)
        self.assertTrue(self.canvas.creating_textblock)
        self.canvas.setRegionRedetectMode(True)
        self.assertFalse(self.canvas.creating_textblock)


class DeviceAndReleaseTest(_RedetectTestCase):
    """内存策略：检测器默认跑 CPU、且用完即卸（实测见模块 docstring 的「内存」一节）。

    CUDA 会话一次吃 +835MB 主机工作集且 ``unload_model`` 只回收 50MB；CPU 只
    +89~134MB 且卸载可回收，单次推理只慢约 20ms。
    """

    def setUp(self):
        super().setUp()
        from utils.config import pcfg as _pcfg

        self._saved = (
            _pcfg.region_redetect_detector,
            _pcfg.region_redetect_device,
            _pcfg.module.textdetector_params,
        )
        self.built = []

    def tearDown(self):
        from utils.config import pcfg as _pcfg

        (
            _pcfg.region_redetect_detector,
            _pcfg.region_redetect_device,
            _pcfg.module.textdetector_params,
        ) = self._saved
        super().tearDown()

    def _stub_registry(self):
        """把注册表解析换成一个假模块类，记录构造参数。返回原函数以便还原。"""
        from utils import registries

        built = self.built

        class _StubDetector:
            name = "stub_det"

            def __init__(self, **params):
                built.append(params)

            def detect(self, img, proj=None):
                return np.zeros(img.shape[:2], dtype=np.uint8), []

            def unload_model(self, empty_cache=False):
                return None

        original = registries.TEXTDETECTORS.resolve_module
        registries.TEXTDETECTORS.resolve_module = lambda _name: _StubDetector
        return registries, original

    def test_detector_runs_on_cpu_by_default(self):
        from utils.config import pcfg as _pcfg

        _pcfg.region_redetect_detector = "stub"
        _pcfg.region_redetect_device = "cpu"
        _pcfg.module.textdetector_params = {"stub": {"device": "cuda", "other": 7}}
        registries, original = self._stub_registry()
        try:
            task = RegionRedetect(self.proj)
            task._ensure_detector()
        finally:
            registries.TEXTDETECTORS.resolve_module = original
        # 只覆盖 device，其余参数原样带过去
        self.assertEqual(self.built, [{"device": "cpu", "other": 7}])

    def test_device_follows_config_and_rebuilds_on_change(self):
        from utils.config import pcfg as _pcfg

        _pcfg.region_redetect_detector = "stub"
        _pcfg.module.textdetector_params = {"stub": {"device": "cpu"}}
        registries, original = self._stub_registry()
        try:
            task = RegionRedetect(self.proj)
            _pcfg.region_redetect_device = "cuda"
            task._ensure_detector()
            self.assertTrue(task.detector_loaded())
            # 设备改了：旧实例作废，下次调用重建
            _pcfg.region_redetect_device = "cpu"
            self.assertFalse(task.detector_loaded())
            task._ensure_detector()
        finally:
            registries.TEXTDETECTORS.resolve_module = original
        self.assertEqual([p["device"] for p in self.built], ["cuda", "cpu"])

    def test_release_clears_cache(self):
        task = self._task([_blk(120, 120, 180, 140)])
        self.assertTrue(task.detector_loaded())
        task.unload_detector()
        self.assertFalse(task.detector_loaded())


if __name__ == "__main__":
    unittest.main()
