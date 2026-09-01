"""格式化手势宏聚合回归测试（离屏）。

多选文本框在面板改字号时，每次键值对每个选中块各推一条
``TextItemEditCommand``（``ui/scenetext_manager.py::on_push_textitem_undostack``
的 is_formatting 分支）。这些命令现在并入手势宏
（``ui/canvas.py::push_formatting_command`` → ``QUndoStack.beginMacro``），
使一次格式化手势（跨多块、跨多个中间键值）整体记为一个撤销步：

- 一次 Ctrl+Z 把整批退回手势前的值（中间值不再作为撤销步暴露）；
- 选区变化 / 非格式化推送 / 撤销重做入口 / 清栈 / 空闲超时关闭手势，
  后续格式化另起新步。

命令链路复用真实组件（Canvas + TextBlkItem + TextItemEditCommand +
ffmt_change_font_size），仅 item 的 ``push_undo_stack`` 信号到 canvas 的
一段按 ``on_push_textitem_undostack`` 的分接管桥接。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_format_gesture_undo.py
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

from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.image_edit import ImageEditMode  # noqa: E402
from ui.textedit_commands import TextItemEditCommand  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


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


class FormatGestureUndoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui import shared_widget as SW
        from ui.canvas import Canvas
        from ui.textitem import TextBlkItem

        cls.SW = SW
        cls.Canvas = Canvas
        cls.TextBlkItem = TextBlkItem
        # 保持引用，防 PyQt GC 回收 C++ 实例（同 test_drawing_cursor 约定）
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        canvas = self.Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1  # text editor tab
        canvas.image_edit_mode = ImageEditMode.NONE
        canvas.txtblkShapeControl = _SpyShapeControl()
        canvas.alignment_enabled = False
        self.canvas = canvas
        self.canvas.gv.setScene(canvas)

        self.items = []
        for i in range(2):
            xyxy = [100 + i * 320, 100, 380 + i * 320, 220]
            blk = TextBlock(xyxy=xyxy, translation="测试文字")
            blk._bounding_rect = list(xyxy)
            item = self.TextBlkItem(blk=blk, idx=i)
            self.canvas.addItem(item)
            self.items.append(item)
            item.push_undo_stack.connect(self._make_handler(item))
        for item in self.items:
            item.updateUndoSteps()

        # ffmt 路径经 SW.canvas.selected_text_items() 取选中集合
        self._orig_canvas = self.SW.canvas
        self.SW.canvas = self.canvas
        self.canvas.selected_text_items = lambda: list(self.items)

        self.baseline = [self._size(item) for item in self.items]

    def tearDown(self):
        self.SW.canvas = self._orig_canvas

    # ── 链路桥接 ─────────────────────────────────────────────────────

    def _make_handler(self, bound_item):
        """按 on_push_textitem_undostack 的分接把 item 信号接到 canvas。"""

        def handler(num_steps, is_formatting):
            cmd = TextItemEditCommand(
                bound_item, None, num_steps, SimpleNamespace(textblk_item=None)
            )
            if is_formatting:
                self.canvas.push_formatting_command(cmd)
            else:
                self.canvas.push_undo_command(cmd, update_pushed_step=True)

        return handler

    def _type_size(self, value):
        """模拟面板字号键入的一次 param_changed 发射（global 分支）。"""
        from ui.fontformat_commands import ffmt_change_font_size
        from utils.fontformat import FontFormat

        ffmt_change_font_size("font_size", value, FontFormat(), is_global=True)

    def _size(self, item):
        return round(item.get_fontformat().font_size, 2)

    # ── 用例 ─────────────────────────────────────────────────────────

    def test_multi_block_two_keystrokes_single_undo(self):
        self._type_size(3.0)
        self._type_size(35.0)
        # 2 块 × 2 键值 = 4 条子命令，聚合为一个宏 = 一个撤销步
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.assertTrue(self.canvas._format_gesture_open)
        self.assertEqual([self._size(it) for it in self.items], [35.0, 35.0])

        self.canvas.undo()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertEqual([self._size(it) for it in self.items], self.baseline)

        self.canvas.redo()
        self.assertEqual([self._size(it) for it in self.items], [35.0, 35.0])

    def test_selection_change_closes_gesture(self):
        self._type_size(3.0)
        self.canvas.on_selection_changed()
        self.assertFalse(self.canvas._format_gesture_open)
        self._type_size(35.0)
        # 手势边界后：两次键值成为两个独立撤销步
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self.canvas.undo()
        self.assertEqual([self._size(it) for it in self.items], [3.0, 3.0])
        self.canvas.undo()
        self.assertEqual([self._size(it) for it in self.items], self.baseline)

    def test_idle_close_then_new_gesture(self):
        self._type_size(3.0)
        self.assertTrue(self.canvas._format_gesture_timer.isActive())
        self.canvas._close_format_gesture()
        self.assertFalse(self.canvas._format_gesture_timer.isActive())
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        # 关闭后的格式化另起新宏
        self._type_size(35.0)
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self.canvas.undo()
        self.canvas.undo()
        self.assertEqual([self._size(it) for it in self.items], self.baseline)

    def test_clear_resets_gesture_state(self):
        self._type_size(35.0)
        self.assertTrue(self.canvas._format_gesture_open)
        self.canvas.clear_text_stack()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertFalse(self.canvas.text_undo_stack.canUndo())
        # 清栈后的新手势正常开合（守护"合成中清栈"未定义边界）
        self._type_size(35.0)
        self.assertTrue(self.canvas._format_gesture_open)
        self.canvas._close_format_gesture()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)

    def test_single_block_typing_is_one_step(self):
        self.canvas.selected_text_items = lambda: [self.items[0]]
        self._type_size(3.0)
        self._type_size(35.0)
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.canvas.undo()
        self.assertEqual(self._size(self.items[0]), self.baseline[0])


if __name__ == "__main__":
    unittest.main()
