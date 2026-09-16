"""``utils/block_geometry.py`` 的几何判据回归。

这是"碰到邻框即停"的唯一实现——``ui/batch_merge.py`` 的审批截图（D31）与
``ui/batch_expand.py`` 的批量框扩张（D5）共用，故单独锁行为。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_block_geometry.py -q
"""

import os
import os.path as osp
import sys
import types
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils.block_geometry import (  # noqa: E402
    expand_limited,
    gap_length,
    overlap_ratio,
    rect_of,
)
from utils.textblock import TextBlock  # noqa: E402

PAGE = (300, 400)


class RectOfTest(unittest.TestCase):
    def test_reads_xyxy(self):
        self.assertEqual(rect_of(TextBlock(xyxy=[1, 2, 11, 22])), [1, 2, 11, 22])

    def test_rounds_floats(self):
        # TextBlock 构造期已取整，故用一个带 float xyxy 的裸对象验解析
        holder = types.SimpleNamespace(xyxy=[1.4, 2.6, 11.2, 22.8])
        self.assertEqual(rect_of(holder), [1, 3, 11, 23])

    def test_missing_rect_is_none(self):
        self.assertIsNone(rect_of(TextBlock()))

    def test_degenerate_rect_is_none(self):
        self.assertIsNone(rect_of(TextBlock(xyxy=[10, 10, 10, 40])))
        self.assertIsNone(rect_of(TextBlock(xyxy=[10, 10, 40, 10])))

    def test_short_or_broken_values_are_none(self):
        self.assertIsNone(rect_of(types.SimpleNamespace(xyxy=[1, 2, 3])))
        self.assertIsNone(rect_of(types.SimpleNamespace(xyxy=["a", 2, 3, 4])))


class ScalarHeadsTest(unittest.TestCase):
    def test_overlap_ratio_denominator_is_shorter(self):
        # 重叠 10px，较短者 20px → 0.5
        self.assertAlmostEqual(overlap_ratio(0, 100, 90, 110), 0.5)

    def test_overlap_ratio_bounds(self):
        self.assertEqual(overlap_ratio(0, 50, 50, 100), 0.0)
        self.assertEqual(overlap_ratio(0, 50, 10, 40), 1.0)

    def test_gap_length(self):
        self.assertEqual(gap_length(0, 10, 20, 30), 10)
        self.assertEqual(gap_length(0, 10, 10, 30), 0)
        self.assertEqual(gap_length(0, 10, 5, 30), 0)


class ExpandLimitedTest(unittest.TestCase):
    def test_zero_amount_is_identity(self):
        self.assertEqual(expand_limited([10, 10, 40, 50], 0, PAGE, []), [10, 10, 40, 50])
        self.assertEqual(expand_limited([10, 10, 40, 50], -5, PAGE, []), [10, 10, 40, 50])

    def test_expands_all_sides_without_neighbors(self):
        self.assertEqual(
            expand_limited([100, 100, 140, 200], 20, PAGE, []), [80, 80, 160, 220]
        )

    def test_clamped_by_page_bounds(self):
        self.assertEqual(
            expand_limited([2, 2, 40, 40], 20, PAGE, []), [0, 0, 60, 60]
        )
        self.assertEqual(
            expand_limited([280, 380, 299, 399], 20, PAGE, []), [260, 360, 300, 400]
        )  # 右／下可扩到页边界（x2 == 宽、y2 == 高）

    def test_neighbor_stops_the_expansion_and_touching_is_allowed(self):
        grown = expand_limited([100, 100, 140, 200], 20, PAGE, [[150, 100, 200, 200]])
        self.assertEqual(grown[2], 150)  # 贴住邻框左缘
        self.assertEqual(grown[0], 80)

    def test_neighbor_outside_the_band_does_not_stop(self):
        """邻框与判定带不重叠（本例在下方远处）→ 不构成阻挡。"""
        self.assertEqual(
            expand_limited([100, 100, 140, 200], 20, PAGE, [[150, 300, 200, 360]]),
            [80, 80, 160, 220],
        )

    def test_left_expansion_stops_at_neighbor_right_edge(self):
        grown = expand_limited([100, 100, 140, 200], 20, PAGE, [[50, 120, 90, 180]])
        self.assertEqual(grown[0], 90)

    def test_top_bottom_expansion_stops(self):
        grown = expand_limited(
            [100, 100, 140, 200], 20, PAGE, [[110, 60, 130, 80], [110, 220, 130, 240]]
        )
        self.assertEqual((grown[1], grown[3]), (80, 220))


if __name__ == "__main__":
    unittest.main()
