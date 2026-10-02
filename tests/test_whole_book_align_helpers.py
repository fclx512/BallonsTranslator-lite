"""整本对齐对话框纯函数（目标计算层）的契约。

钉的契约：``ui/point_align_dialog.py::block_edge_value`` /
``smart_default_target`` 的口径与执行端
``ui/mainwindow.py::execute_advanced_align`` 的 delta 公式一致（同一块
同一对齐边的目标值相等 ⇒ delta≈0 不动），以及众数/平票规则的确定性。
不导入 Qt，纯数据层。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.point_align_dialog import block_edge_value, smart_default_target


class _Blk:
    """duck-typed TextBlock：只带几何字段。"""

    def __init__(self, br=None, xyxy=None, angle=0):
        self._bounding_rect = br
        self.xyxy = xyxy
        self.angle = angle


class BlockEdgeValueTest(unittest.TestCase):
    def test_matches_execute_formula(self):
        # 执行端 delta 公式：y: top→y / center→y+h/2 / bottom→y+h；
        # x: left→x / center→x+w/2 / right→x+w
        br = (30, 70, 200, 60)
        self.assertEqual(block_edge_value(br, "y", "top"), 70)
        self.assertEqual(block_edge_value(br, "y", "center"), 100.0)
        self.assertEqual(block_edge_value(br, "y", "bottom"), 130)
        self.assertEqual(block_edge_value(br, "x", "left"), 30)
        self.assertEqual(block_edge_value(br, "x", "center"), 130.0)
        self.assertEqual(block_edge_value(br, "x", "right"), 230)


class SmartDefaultTargetTest(unittest.TestCase):
    def test_majority_edge_wins(self):
        blocks = [
            _Blk(br=[0, 100, 10, 10]),
            _Blk(br=[0, 100, 10, 10]),
            _Blk(br=[0, 400, 10, 10]),
        ]
        self.assertEqual(smart_default_target(blocks, "y", "top"), 100)

    def test_tie_picks_median_closest_deterministically(self):
        blocks = [_Blk(br=[0, v, 10, 10]) for v in (80, 200, 320)]
        self.assertEqual(smart_default_target(blocks, "y", "top"), 200)

    def test_skips_rotated_and_empty(self):
        blocks = [_Blk(br=[0, 50, 10, 10], angle=90)]
        self.assertEqual(smart_default_target(blocks, "y", "top"), 0)
        self.assertEqual(smart_default_target([], "x", "left"), 0)

    def test_xyxy_fallback_when_no_bounding_rect(self):
        # _bounding_rect 缺失时回退 xyxy（与执行端口径一致）
        blk = _Blk(xyxy=[10, 20, 30, 40])
        self.assertEqual(smart_default_target([blk], "y", "top"), 20)


if __name__ == "__main__":
    unittest.main()
