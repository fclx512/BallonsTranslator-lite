"""同区域连续修复聚合回归测试（离屏）。

锁定撤销体系阶段4-3a（决策 5「仅存首前末后，不记中间态」）的行为：

- 同页同矩形连续修复：栈顶命令吸收新「修复后」端点，不新增栈步；
  一次 undo 回到首前状态（不是中间态），一次 redo 回到末后状态；
- 矩形不同 / 中间隔其他命令 / 处于干净保存点 / 非栈顶：不聚合，
  各自成步；
- 聚合不碰手工计数器（num_pushed_drawstep 与栈坐标保持一致），
  保存点不被污染。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_inpaint_undo_merge.py
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np  # noqa: E402
from qtpy.QtWidgets import QApplication, QUndoCommand  # noqa: E402

from utils.config import pcfg  # noqa: E402

RECT = [0, 0, 30, 20]
H, W = 20, 30


class OtherCmd(QUndoCommand):
    """无 try_absorb 的占位命令：模拟修复之间夹杂的其他绘制操作。"""

    def __init__(self):
        super().__init__("other")

    def undo(self):
        pass

    def redo(self):
        pass


class TestInpaintMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas
        from ui.drawing_commands import InpaintUndoCommand

        cls.Canvas = Canvas
        cls.InpaintUndoCommand = InpaintUndoCommand
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self._old_limit = pcfg.undo_steps_limit
        pcfg.undo_steps_limit = 0
        self.canvas = self.Canvas()
        self.img = np.zeros((H, W, 3), dtype=np.uint8)
        self.mask = np.zeros((H, W), dtype=np.uint8)
        # img_valid=False：updateLayers 直接返回，测试只看数组内容
        self.canvas.imgtrans_proj = SimpleNamespace(
            img_valid=False,
            inpainted_valid=False,
            inpainted_array=self.img,
            mask_array=self.mask,
        )

    def tearDown(self):
        pcfg.undo_steps_limit = self._old_limit

    def make_cmd(self, after_val, before_val, rect=RECT, canvas=None):
        """构造一条修复命令：调用前先把缓冲写成 before_val（命令构造期
        抓「修复前」快照），after_val 作为「修复后」端点。"""
        buf = (canvas or self.canvas).imgtrans_proj
        buf.inpainted_array[:] = before_val
        buf.mask_array[:] = 0
        return self.InpaintUndoCommand(
            canvas or self.canvas,
            np.full((rect[3] - rect[1], rect[2] - rect[0], 3), after_val, np.uint8),
            np.ones((rect[3] - rect[1], rect[2] - rect[0]), np.uint8),
            list(rect),
        )

    def push(self, cmd):
        # 3b 起修复命令经 push_undo_command 路由入全局栈（image_history）
        self.canvas.push_undo_command(cmd)

    def test_same_rect_merges_to_single_step(self):
        self.push(self.make_cmd(1, 0))  # 0 → 1
        self.push(self.make_cmd(2, 1))  # 1 → 2，吸收
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.assertEqual(self.canvas.num_imgstep, 1)
        np.testing.assert_array_equal(self.img, 2)  # 末后端点已生效

    def test_absorb_keeps_first_before_not_intermediate(self):
        self.push(self.make_cmd(1, 0))
        self.push(self.make_cmd(2, 1))
        self.canvas.undo()
        np.testing.assert_array_equal(self.img, 0)  # 首前，不是中间态 1
        self.canvas.redo()
        np.testing.assert_array_equal(self.img, 2)  # 末后

    def test_absorb_takes_redo_endpoint_only(self):
        a = self.make_cmd(1, 0)
        b = self.make_cmd(2, 1)
        self.assertTrue(a.try_absorb(b))
        np.testing.assert_array_equal(a.redo_img, 2)
        np.testing.assert_array_equal(a.undo_img, 0)  # 首前快照不被触碰

    def test_different_rect_no_merge(self):
        self.push(self.make_cmd(1, 0))
        self.push(self.make_cmd(2, 1, rect=[5, 5, 20, 15]))
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)

    def test_doodle_between_does_not_break_image_merge(self):
        # 涂鸦走绘制层、不碰 inpainted_array：两修复在全局栈中相邻且
        # 图像域连续，聚合仍成立（打断聚合的是全局栈顶为其他命令）
        self.push(self.make_cmd(1, 0))
        self.push(OtherCmd())  # 无 image_history → 绘制模式路由入涂鸦栈
        self.push(self.make_cmd(2, 1))
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.assertEqual(self.canvas.draw_undo_stack.count(), 1)
        # 撤销路由：涂鸦栈优先先撤涂鸦，第二步才回退全局栈撤修复
        self.canvas.undo()
        np.testing.assert_array_equal(self.img, 2)
        self.canvas.undo()
        np.testing.assert_array_equal(self.img, 0)  # 撤销回首前

    def test_clean_state_no_merge(self):
        self.push(self.make_cmd(1, 0))
        self.canvas.update_saved_undostep()
        self.push(self.make_cmd(2, 1))  # 干净保存点上不聚合
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)

    def test_not_at_top_no_merge(self):
        self.push(self.make_cmd(1, 0))
        self.canvas.undo()  # 栈顶不再是 c1（有 redo 残尾）
        self.push(self.make_cmd(2, 9))  # 非栈顶不聚合，push 截掉残尾正常入栈
        np.testing.assert_array_equal(self.img, 2)
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.canvas.undo()
        np.testing.assert_array_equal(self.img, 9)  # 新命令自己的首前，非被吸收

    def test_dirty_accounting_survives_merge(self):
        self.push(self.make_cmd(1, 0))
        self.push(self.make_cmd(2, 1))  # 吸收：计数器与栈坐标都不动
        self.assertEqual(self.canvas.num_imgstep, 1)
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        self.assertTrue(self.canvas.draw_change_unsaved())
        self.canvas.update_saved_undostep()
        self.assertFalse(self.canvas.draw_change_unsaved())
        self.canvas.undo()
        self.assertTrue(self.canvas.draw_change_unsaved())  # 撤离保存点即脏
        self.canvas.redo()
        self.assertFalse(self.canvas.draw_change_unsaved())

    def test_absorb_requires_same_canvas(self):
        other_canvas = self.Canvas()
        other_img = np.zeros((H, W, 3), dtype=np.uint8)
        other_canvas.imgtrans_proj = SimpleNamespace(
            img_valid=False,
            inpainted_valid=False,
            inpainted_array=other_img,
            mask_array=np.zeros((H, W), dtype=np.uint8),
        )
        a = self.make_cmd(1, 0)
        b = self.make_cmd(2, 1, canvas=other_canvas)
        self.assertFalse(a.try_absorb(b))
        np.testing.assert_array_equal(a.redo_img, 1)  # 拒绝时不接管端点


if __name__ == "__main__":
    unittest.main()
