"""撤销步数上限（undoLimit）回归测试（离屏）。

锁定撤销体系决策 1（最大撤销步数，见
``docs/技术实现/撤销体系人工验收场景.md`` §五）的实现依据与行为：

- Qt 语义实测结论（探针固化为用例）：默认 undoLimit=0 即无限；push 超限
  立即丢最旧命令；``setUndoLimit`` 缩容不立即裁剪（惰性，等下次 push）；
  保存点幸存截断时 cleanIndex 正确平移，被截时落 -1、isClean() 恒 False
  ——不存在假「已保存」，无需自造 clean 判定；
- ``ui/canvas.py::push_draw_command`` 截断同步：绘制栈手工计数器
  （num_pushed_drawstep/saved_drawundo_step）随截断平移，保存点被截落
  -1 哨兵，脏判定不误报。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_undo_limit.py
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

from qtpy.QtGui import QUndoCommand, QUndoStack  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.config import pcfg  # noqa: E402


class Cmd(QUndoCommand):
    """空命令占位：undo/redo 无副作用，只占栈位。"""

    def __init__(self, name="cmd"):
        super().__init__(name)

    def undo(self):
        pass

    def redo(self):
        pass


class TestQtUndoLimitCleanSemantics(unittest.TestCase):
    """QUndoStack 截断 × clean 语义（Qt 升级时此用例组第一时间报警）。"""

    def test_default_limit_is_unlimited(self):
        self.assertEqual(QUndoStack().undoLimit(), 0)

    def test_push_truncates_immediately(self):
        s = QUndoStack()
        s.setUndoLimit(10)
        for i in range(15):
            s.push(Cmd())
        self.assertEqual(s.count(), 10)
        self.assertEqual(s.index(), 10)

    def test_clean_survives_truncation(self):
        s = QUndoStack()
        s.setUndoLimit(10)
        for _ in range(8):
            s.push(Cmd())
        s.setClean()  # cleanIndex = 8
        for _ in range(7):
            s.push(Cmd())  # 共 15 push，截 5
        self.assertEqual(s.count(), 10)
        self.assertEqual(s.cleanIndex(), 3)
        while s.index() > s.cleanIndex():
            s.undo()
        self.assertTrue(s.isClean())

    def test_clean_truncated_stays_dirty(self):
        s = QUndoStack()
        s.setUndoLimit(10)
        s.setClean()  # cleanIndex = 0（栈底）
        for _ in range(11):
            s.push(Cmd())  # 截掉保存点
        self.assertEqual(s.cleanIndex(), -1)
        while s.canUndo():
            s.undo()
        # 保存状态已不可达：保持「未保存」语义，绝不假报 clean
        self.assertFalse(s.isClean())

    def test_set_undo_limit_on_non_empty_stack_is_noop(self):
        s = QUndoStack()
        for _ in range(23):
            s.push(Cmd())
        s.setClean()
        for _ in range(3):
            s.push(Cmd())
        s.setUndoLimit(10)
        # Qt 文档化行为：非空栈上 setUndoLimit 只打警告并被忽略
        # （https://doc.qt.io/qt-6/qundostack.html）。改上限只能等栈清空
        # 后再设——canvas.apply_undo_limit 的空栈守卫依赖此语义。
        self.assertEqual(s.count(), 26)
        self.assertEqual(s.undoLimit(), 0)
        self.assertEqual(s.cleanIndex(), 23)


class TestDrawCounterTruncationSync(unittest.TestCase):
    """canvas.push_draw_command 截断时手工计数器同步（绘制栈脏判定）。"""

    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas

        cls.Canvas = Canvas
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self._old_limit = pcfg.undo_steps_limit
        pcfg.undo_steps_limit = 10
        self.canvas = self.Canvas()
        self.canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        self.canvas.editor_index = 0  # draw mode

    def tearDown(self):
        pcfg.undo_steps_limit = self._old_limit

    def test_limit_applied_to_both_stacks(self):
        self.assertEqual(self.canvas.text_undo_stack.undoLimit(), 10)
        self.assertEqual(self.canvas.draw_undo_stack.undoLimit(), 10)

    def test_truncation_syncs_counters(self):
        canvas = self.canvas
        for _ in range(13):
            canvas.push_draw_command(Cmd())
        # 截 3 条最旧：计数器与栈坐标同步平移（不变量 num == index）
        self.assertEqual(canvas.draw_undo_stack.count(), 10)
        self.assertEqual(canvas.num_pushed_drawstep, 10)
        self.assertEqual(canvas.draw_undo_stack.index(), 10)
        # 保存点初始在栈底（saved=0），被截 → -1 不可达哨兵，保持「未保存」
        self.assertEqual(canvas.saved_drawundo_step, -1)
        self.assertTrue(canvas.draw_change_unsaved())

    def test_saved_point_survives_and_stays_reachable(self):
        canvas = self.canvas
        for _ in range(5):
            canvas.push_draw_command(Cmd())
        canvas.update_saved_undostep()  # 保存点 index 5
        for _ in range(8):
            canvas.push_draw_command(Cmd())  # 共 13 push，截 3
        # 保存点平移 5 → 2，仍可达；撤销到保存点恢复「已保存」
        self.assertEqual(canvas.saved_drawundo_step, 2)
        for _ in range(8):
            canvas.undo()
        self.assertEqual(canvas.num_pushed_drawstep, 2)
        self.assertEqual(canvas.draw_undo_stack.index(), 2)
        self.assertFalse(canvas.draw_change_unsaved())

    def test_saved_point_truncated_keeps_dirty(self):
        canvas = self.canvas
        for _ in range(3):
            canvas.push_draw_command(Cmd())
        canvas.update_saved_undostep()  # 保存点 index 3
        for _ in range(11):
            canvas.push_draw_command(Cmd())  # 共 14 push，截 4
        self.assertEqual(canvas.saved_drawundo_step, -1)
        # 一路撤销到栈底也回不到保存点：脏判定不误报
        for _ in range(10):
            canvas.undo()
        self.assertEqual(canvas.draw_undo_stack.index(), 0)
        self.assertEqual(canvas.num_pushed_drawstep, 0)
        self.assertTrue(canvas.draw_change_unsaved())
        # 下次保存后恢复记账
        canvas.update_saved_undostep()
        self.assertFalse(canvas.draw_change_unsaved())

    def test_limit_change_applies_after_clear(self):
        # Qt 限制：非空栈上 setUndoLimit 被忽略 → 中途改设置在下次清栈
        # （切页路径）落地。旧上限 10 仍管住当前历史，切页后新值生效。
        canvas = self.canvas
        for _ in range(12):
            canvas.push_draw_command(Cmd())  # 旧上限 10 截 2
        self.assertEqual(canvas.draw_undo_stack.count(), 10)
        pcfg.undo_steps_limit = 5  # 中途改设置（栈非空，暂不生效）
        canvas.apply_undo_limit()
        self.assertEqual(canvas.draw_undo_stack.undoLimit(), 10)
        canvas.push_draw_command(Cmd())  # 继续编辑仍按旧上限
        self.assertEqual(canvas.draw_undo_stack.count(), 10)
        canvas.clear_undostack()  # 切页清栈 → 新上限落地
        self.assertEqual(canvas.draw_undo_stack.undoLimit(), 5)
        self.assertEqual(canvas.text_undo_stack.undoLimit(), 5)


if __name__ == "__main__":
    unittest.main()
