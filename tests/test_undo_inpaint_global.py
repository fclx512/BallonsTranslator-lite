"""修复命令并入全局栈护网（离屏，撤销体系阶段 4-3b）。

覆盖 docs/技术实现/撤销体系阶段4计划.md 五（第三批 3b）的核心行为：

- 路由：``InpaintUndoCommand`` 经 push_undo_command 入全局文本栈
  （image_history 标记），打页标签 + 图像代数；绘制栈不再承载修复
  历史；
- 图像脏记账：num_imgstep/saved_imgstep 维护不变量 = 全局栈
  [0, index) 区间图像命令数，驱动 draw_change_unsaved（修复图/遮罩
  落盘门）与保存点；
- 绘制模式撤销回退：涂鸦栈空则回退全局栈（跨模态回退）；
- 跨页 armed 门与图像代数僵尸对修复命令生效；
- invalidate_text_history_for_page（文本原地重写）不作废修复命令；
- undoLimit 截断的图像计数器平移；
- 修复区过滤视图（HistoryPanel image_filter）：只显示当前页修复行，
  行号 = 全局栈位置。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_undo_inpaint_global.py
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
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402

RECT = [0, 0, 30, 20]
H, W = 20, 30


class _SpyShapeControl:
    def isVisible(self):
        return False

    def setBlkItem(self, item):
        pass

    def updateBoundingRect(self):
        pass


class TxtCmd(QUndoCommand):
    """无 image_history 的占位文本命令（占全局栈位，测截断平移）。"""

    def __init__(self):
        super().__init__("txt")

    def undo(self):
        pass

    def redo(self):
        pass


class InpaintGlobalTest(unittest.TestCase):
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
        self.proj = ProjImgTrans()
        self.proj.pages = {"A": [], "B": []}
        self.proj.current_img = "A"
        # img_array 置 None：img_valid False → updateLayers 直接返回，
        # 测试只看数组内容
        self.img = np.zeros((H, W, 3), dtype=np.uint8)
        self.mask = np.zeros((H, W), dtype=np.uint8)
        self.proj.inpainted_array = self.img
        self.proj.mask_array = self.mask
        self.canvas.imgtrans_proj = self.proj
        self.canvas.editor_index = 0  # draw mode（修复区）
        self.canvas.txtblkShapeControl = _SpyShapeControl()
        self.canvas.alignment_enabled = False
        self.canvas.gv.setScene(self.canvas)
        self._jumps = []
        self.canvas.page_jump_requested.connect(self._on_jump_requested)

    def tearDown(self):
        from ui import shared_widget as SW

        pcfg.undo_steps_limit = self._old_limit
        SW.st_manager = None

    def _on_jump_requested(self, pagename):
        self._jumps.append(pagename)
        self.proj.current_img = pagename

    def make_cmd(self, after_val, before_val, rect=RECT):
        """构造修复命令：调用前把缓冲写成 before_val（构造期抓「修复前」
        快照），after_val 为「修复后」端点。"""
        self.proj.inpainted_array[:] = before_val
        self.proj.mask_array[:] = 0
        return self.InpaintUndoCommand(
            self.canvas,
            np.full((rect[3] - rect[1], rect[2] - rect[0], 3), after_val, np.uint8),
            np.ones((rect[3] - rect[1], rect[2] - rect[0]), np.uint8),
            list(rect),
        )

    # ── 路由与标签 ──────────────────────────────────────────────

    def test_push_routes_to_global_stack(self):
        cmd = self.make_cmd(1, 0)
        self.canvas.push_undo_command(cmd)
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.assertEqual(self.canvas.draw_undo_stack.count(), 0)
        self.assertEqual(cmd.pagename, "A")
        self.assertEqual(cmd.page_image_generation, 0)
        self.assertFalse(hasattr(cmd, "page_generation"))
        self.assertEqual(self.canvas.num_imgstep, 1)
        np.testing.assert_array_equal(self.img, 1)

    def test_draw_mode_undo_falls_back_to_global(self):
        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.canvas.undo()  # 涂鸦栈空 → 回退全局栈
        np.testing.assert_array_equal(self.img, 0)
        self.assertEqual(self.canvas.num_imgstep, 0)
        self.canvas.redo()
        np.testing.assert_array_equal(self.img, 1)
        self.assertEqual(self.canvas.num_imgstep, 1)

    # ── 脏记账与保存点 ──────────────────────────────────────────

    def test_dirty_and_save_flags(self):
        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.assertTrue(self.canvas.draw_change_unsaved())
        self.canvas.update_saved_undostep()
        self.assertFalse(self.canvas.draw_change_unsaved())
        self.canvas.undo()  # 撤离保存点 → 图像与已保存态不一致
        self.assertTrue(self.canvas.draw_change_unsaved())
        self.canvas.redo()
        self.assertFalse(self.canvas.draw_change_unsaved())

    def test_truncation_shifts_img_counters(self):
        pcfg.undo_steps_limit = 2
        self.canvas.apply_undo_limit()  # Qt 限制：仅空栈可落地
        # 异矩形：同矩形会被 3a 聚合吸收，截断场景须各自独立成步
        rects = ([0, 0, 30, 20], [5, 5, 25, 15], [2, 2, 28, 18])
        for i, rect in enumerate(rects):
            self.canvas.push_undo_command(self.make_cmd(i + 1, i, rect=list(rect)))
        # 截掉最旧 1 条图像命令：num 与栈内区间一致，保存点落 -1 哨兵
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self.assertEqual(self.canvas.num_imgstep, 2)
        self.assertEqual(self.canvas.saved_imgstep, -1)
        self.assertTrue(self.canvas.draw_change_unsaved())

    def test_truncation_ignores_text_commands(self):
        pcfg.undo_steps_limit = 2
        self.canvas.apply_undo_limit()  # Qt 限制：仅空栈可落地
        self.canvas.push_text_command(TxtCmd())
        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.canvas.push_undo_command(
            self.make_cmd(2, 1, rect=[5, 5, 25, 15])
        )  # 截掉 TxtCmd
        self.assertEqual(self.canvas.num_imgstep, 2)
        self.assertEqual(self.canvas.saved_imgstep, 0)  # 非图像截断不平移

    # ── 页屏障：armed 门与图像代数 ──────────────────────────────

    def test_cross_page_gate_arms_for_image_cmd(self):
        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.proj.current_img = "B"  # 模拟已切到他页
        self.canvas.undo()  # 第一按：只提示不执行
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        np.testing.assert_array_equal(self.img, 1)
        self.canvas.undo()  # 第二按：跳页 + 执行
        self.assertEqual(self._jumps, ["A"])
        np.testing.assert_array_equal(self.img, 0)
        self.assertEqual(self.canvas.num_imgstep, 0)

    def test_image_gen_bump_zombies(self):
        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.proj.bump_page_image_generation("A")
        self.canvas.undo()  # 僵尸：无操作消费，位置照走
        np.testing.assert_array_equal(self.img, 1)  # 内容未变
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)

    def test_invalidate_text_history_skips_image_cmd(self):
        cmd = self.make_cmd(1, 0)
        self.canvas.push_undo_command(cmd)
        self.canvas.invalidate_text_history_for_page("A")
        self.assertFalse(getattr(cmd, "_page_stale", False))
        from ui.textedit_commands import command_page_stale

        self.assertFalse(command_page_stale(cmd, self.proj))

    # ── 修复区过滤视图 ──────────────────────────────────────────

    def test_filtered_history_model(self):
        from ui.history_panel import HistoryPanel, _ROLE_STACK_POS

        self.canvas.push_undo_command(self.make_cmd(1, 0))  # A 页修复
        self.canvas.push_text_command(TxtCmd())  # 文本命令不入过滤视图
        self.proj.current_img = "B"
        self.canvas.push_undo_command(self.make_cmd(2, 1))  # B 页修复
        self.proj.current_img = "A"

        panel = HistoryPanel(image_filter=True)
        panel._canvas = self.canvas
        panel.stack = self.canvas.text_undo_stack
        panel.model.rebuild()
        pos_rows = [
            panel.model.index(r).data(_ROLE_STACK_POS)
            for r in range(panel.model.rowCount())
        ]
        # 首行 = 首条 A 页修复前的状态（栈位 0），其后仅 A 页修复行
        self.assertEqual(pos_rows, [0, 1])

    def test_filtered_model_empty_on_other_page(self):
        from ui.history_panel import HistoryPanel, _ROLE_STACK_POS

        self.canvas.push_undo_command(self.make_cmd(1, 0))
        self.proj.current_img = "B"
        panel = HistoryPanel(image_filter=True)
        panel._canvas = self.canvas
        panel.stack = self.canvas.text_undo_stack
        panel.model.rebuild()
        pos_rows = [
            panel.model.index(r).data(_ROLE_STACK_POS)
            for r in range(panel.model.rowCount())
        ]
        # 切页即空：仅剩首行，点击为无操作（pos = 当前栈位）
        self.assertEqual(pos_rows, [1])


if __name__ == "__main__":
    unittest.main()
