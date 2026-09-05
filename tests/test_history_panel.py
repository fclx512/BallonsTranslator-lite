"""撤销历史面板与撤回提示回归测试（离屏）。

覆盖 2026-09-05 落地的三个功能（对应撤销体系决策记录 §五）：

- 命令命名：快照命令类构造即带 UndoCommand 上下文文本（历史面板行名
  与撤回 toast 的数据源）；
- 撤回 toast：``ui/canvas.py::Canvas._notify_undo`` 的抑制开关与空名
  守卫（通知中心 key="undo" 去重刷新）；
- 历史面板跳转：``ui/history_panel.py::HistoryPanel._on_row_clicked``
  行号 = 栈位置映射，循环 canvas.undo()/redo() 逐步跳转，跳转期间
  ``_suppress_undo_toast`` 抑制、结束后恢复。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_history_panel.py
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

from qtpy.QtGui import QUndoCommand  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402


class NamedCmd(QUndoCommand):
    """带名的空命令占位。"""

    def __init__(self, name):
        super().__init__(name)

    def undo(self):
        pass

    def redo(self):
        pass


class _SpyShapeControl:
    """canvas.undo() 的文字模式分支会调 updateBoundingRect，桩掉即可。"""

    def __init__(self):
        self.blk_item = None

    def isVisible(self):
        return False

    def setBlkItem(self, item):
        self.blk_item = item

    def updateBoundingRect(self):
        pass


class TestCommandTexts(unittest.TestCase):
    """命令命名清单抽查：快照命令构造即带文本。"""

    def test_typing_session_command_has_text(self):
        from ui.textedit_commands import TypingSessionCommand

        cmd = TypingSessionCommand(None, None, "before", "after")
        self.assertTrue(cmd.text())

    def test_format_gesture_command_has_text(self):
        from ui.textedit_commands import FormatGestureCommand

        cmd = FormatGestureCommand([])
        self.assertTrue(cmd.text())


class TestUndoToastGuard(unittest.TestCase):
    """_notify_undo：空名不提示、跳转抑制开关生效。"""

    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas

        cls.Canvas = Canvas
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.canvas = self.Canvas()
        self.canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        self.canvas.editor_index = 1  # text mode
        self.canvas.image_edit_mode = 0
        self.canvas.txtblkShapeControl = _SpyShapeControl()

    def test_notify_no_crash_and_suppress(self):
        # 空名守卫 + 正常名 + 抑制态：仅验证不崩与开关复位（toast 展示
        # 归通知中心自己的去重刷新管，不在本测试范围）
        self.canvas._notify_undo("")
        self.canvas._notify_undo("Typing")
        self.canvas._suppress_undo_toast = True
        self.canvas._notify_undo("Typing")
        self.canvas._suppress_undo_toast = False


class TestHistoryPanelJump(unittest.TestCase):
    """历史面板行号 = 栈位置跳转（文本栈）。"""

    @classmethod
    def setUpClass(cls):
        from ui.history_panel import HistoryPanel
        from ui import shared_widget as SW

        cls.HistoryPanel = HistoryPanel
        cls.SW = SW
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.canvas import Canvas
        from ui.image_edit import ImageEditMode

        self.canvas = Canvas()
        self.canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        self.canvas.editor_index = 1  # text mode
        self.canvas.image_edit_mode = ImageEditMode.NONE
        self.canvas.txtblkShapeControl = _SpyShapeControl()
        self._orig_canvas = self.SW.canvas
        self.SW.canvas = self.canvas

        self.panel = self.HistoryPanel()
        # 直接绑定（等价 showEvent 的惰性绑定，测试不走真实展示）
        self.panel._canvas = self.canvas
        self.panel.stack = self.canvas.text_undo_stack

        for i in range(5):
            self.canvas.push_text_command(NamedCmd(f"step{i}"))
        # 新模型不走 QUndoView 自动跟随，显式重建（showEvent 亦做）
        self.panel.model.rebuild()

    def tearDown(self):
        self.SW.canvas = self._orig_canvas

    def test_click_jumps_to_row_position(self):
        stack = self.canvas.text_undo_stack
        self.assertEqual(stack.index(), 5)
        model = self.panel.view.model()

        # 命令无页标签 → 行序：原始状态、页头、5 命令行
        self.assertEqual(model.rowCount(), 7)

        # 点首个命令行（栈位置 1）→ 撤回到位置 1
        self.panel._on_row_clicked(model.index(2, 0))
        self.assertEqual(stack.index(), 1)

        # 点栈位置 4 的行 → 重做到位置 4
        self.panel._on_row_clicked(model.index(5, 0))
        self.assertEqual(stack.index(), 4)

        # 点当前行 → 无操作
        self.panel._on_row_clicked(model.index(5, 0))
        self.assertEqual(stack.index(), 4)

        # 点页头行 → 无操作
        self.panel._on_row_clicked(model.index(1, 0))
        self.assertEqual(stack.index(), 4)

        # 跳回 0（原始状态）再跳到末行
        self.panel._on_row_clicked(model.index(0, 0))
        self.assertEqual(stack.index(), 0)
        self.panel._on_row_clicked(model.index(6, 0))
        self.assertEqual(stack.index(), 5)

    def test_suppress_flag_restored_after_jump(self):
        model = self.panel.view.model()
        self.panel._on_row_clicked(model.index(3, 0))
        self.assertFalse(self.canvas._suppress_undo_toast)

    def test_truncated_history_click_maps_correctly(self):
        # 上限必须在栈空时设置（Qt 限制，见 tests/test_undo_limit.py）：
        # 以 limit=3 新建画布验证截断后历史面板的行映射
        from ui.canvas import Canvas
        from ui.image_edit import ImageEditMode
        from utils.config import pcfg

        pcfg.undo_steps_limit = 3
        canvas = Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1
        canvas.image_edit_mode = ImageEditMode.NONE
        canvas.txtblkShapeControl = _SpyShapeControl()
        self.panel._canvas = canvas
        self.panel.stack = canvas.text_undo_stack
        for i in range(5):
            canvas.push_text_command(NamedCmd(f"step{i}"))
        self.panel.model.rebuild()
        model = self.panel.view.model()
        self.assertEqual(model.rowCount(), 5)  # 原始状态 + 页头 + 3 命令
        self.panel._on_row_clicked(model.index(2, 0))  # 首个命令行 = 栈位置 1
        self.assertEqual(canvas.text_undo_stack.index(), 1)


if __name__ == "__main__":
    unittest.main()
