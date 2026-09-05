"""格式化手势快照命令回归测试（离屏，3a 会话制）。

多选文本框在面板改字号时，每次键值对每个选中块发射一次
``push_undo_stack(is_formatting=True)``，经
``ui/scenetext_manager.py::on_push_textitem_undostack`` 分接到
``ui/canvas.py::note_formatting_edit`` 并入当前格式化手势。手势期间的
预览中间值不入栈；边界（选区变化/键入/失焦/idle 超时/清栈）闭合时以
「基线↔终值」落一条 ``ui/textedit_commands.py::FormatGestureCommand``：

- 闭合后一次 Ctrl+Z 把整批退回手势前的基线值；
- 预览悬开期间按 Ctrl+Z = 取消手势、恢复基线，不落命令（目标行为 7）；
- 边界后的后续格式化另起新手势，各落一条命令；
- 脏状态走 QUndoStack clean 机制 + 会话脏标记（num_pushed_textstep 已删）。

命令链路复用真实组件（Canvas + TextBlkItem + ffmt_change_font_size），
仅 item 的 ``push_undo_stack`` 信号到 canvas 的一段按
``on_push_textitem_undostack`` 的分接管桥接。

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
        """按 on_push_textitem_undostack 的分接把 item 信号接到 canvas。

        3a：键入已由 propagate 登记（本测试不覆盖），此处只接管格式化 →
        并入 canvas 的格式化手势。
        """

        def handler(num_steps, is_formatting):
            if is_formatting:
                self.canvas.note_formatting_edit(
                    bound_item, SimpleNamespace(textblk_item=None)
                )

        return handler

    def _type_size(self, value):
        """模拟面板字号键入的一次 param_changed 发射（global 分支）。"""
        from ui.fontformat_commands import ffmt_change_font_size
        from utils.fontformat import FontFormat

        ffmt_change_font_size("font_size", value, FontFormat(), is_global=True)

    def _size(self, item):
        return round(item.get_fontformat().font_size, 2)

    def _sizes(self):
        return [self._size(it) for it in self.items]

    # ── 用例 ─────────────────────────────────────────────────────────

    def test_committed_gesture_single_undo_redo(self):
        self._type_size(3.0)
        self._type_size(35.0)
        # 预览中间值不入栈：手势悬开期间栈上无命令
        self.assertTrue(self.canvas._format_gesture_open)
        self.assertEqual(self.canvas.text_undo_stack.count(), 0)
        self.assertEqual(self._sizes(), [35.0, 35.0])

        # 边界闭合：2 块 × 2 键值并入同一手势，落一条快照命令
        self.canvas.commit_edit_sessions()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)

        self.canvas.undo()
        self.assertEqual(self._sizes(), self.baseline)

        self.canvas.redo()
        self.assertEqual(self._sizes(), [35.0, 35.0])

    def test_preview_open_ctrlz_cancels_gesture(self):
        """目标行为 7：预览悬开期间 Ctrl+Z = 取消手势、恢复基线，不落命令。"""
        self._type_size(3.0)
        self._type_size(35.0)
        self.assertTrue(self.canvas._format_gesture_open)

        self.canvas.undo()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertEqual(self._sizes(), self.baseline)
        self.assertEqual(self.canvas.text_undo_stack.count(), 0)

        # 取消不落命令：redo 无东西可做
        self.canvas.redo()
        self.assertEqual(self._sizes(), self.baseline)

    def test_selection_change_closes_gesture(self):
        self._type_size(3.0)
        self.canvas.on_selection_changed()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)

        self._type_size(35.0)
        self.canvas.commit_edit_sessions()
        # 手势边界后：两次键值成为两个独立撤销步
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self.canvas.undo()
        self.assertEqual(self._sizes(), [3.0, 3.0])
        self.canvas.undo()
        self.assertEqual(self._sizes(), self.baseline)

    def test_idle_close_then_new_gesture(self):
        self._type_size(3.0)
        self.assertTrue(self.canvas._edit_session_timer.isActive())
        self.canvas._commit_format_gesture()
        self.assertFalse(self.canvas._edit_session_timer.isActive())
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        # 关闭后的格式化另起新手势
        self._type_size(35.0)
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self.canvas.undo()
        self.canvas.undo()
        self.assertEqual(self._sizes(), self.baseline)

    def test_clear_resets_gesture_state(self):
        self._type_size(35.0)
        self.assertTrue(self.canvas._format_gesture_open)
        self.canvas.clear_text_stack()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertFalse(self.canvas.text_undo_stack.canUndo())
        # 清栈后的新手势正常开合（守护"合成中清栈"未定义边界）
        self._type_size(35.0)
        self.assertTrue(self.canvas._format_gesture_open)
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)

    def test_single_block_gesture_is_one_step(self):
        self.canvas.selected_text_items = lambda: [self.items[0]]
        self._type_size(3.0)
        self._type_size(35.0)
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.canvas.undo()
        self.assertEqual(self._size(self.items[0]), self.baseline[0])

    def _strokes(self, item):
        return round(item.get_fontformat().stroke_width, 2)

    def _line_spacing(self, item):
        return round(item.get_fontformat().line_spacing, 3)

    def _letter_spacing(self, item):
        return round(item.get_fontformat().letter_spacing, 3)

    def _change_param(self, func, param_name, value):
        from utils.fontformat import FontFormat

        func(param_name, value, FontFormat(), is_global=True)

    def test_effect_param_one_step_per_gesture(self):
        """描边/行距/字距等效果类参数也应并入格式化手势：一次调整=一步撤销。

        撤销体系 3a 护网只测过 font_size（内容类，靠 on_content_changed 自登记）；
        效果类 setter（描边/阴影/透明/行距/字距）不触发 on_content_changed，
        靠 font_formating wrapper 显式登记。回归锁定：多次发射仍聚合成一条
        FormatGestureCommand、单步撤销还原基线（修复前每次发射压一条命令，
        撤销要多按几次、有的无视觉变化）。
        """
        from ui.fontformat_commands import (
            ffmt_change_letter_spacing,
            ffmt_change_line_spacing,
            ffmt_change_stroke_width,
        )

        cases = [
            (ffmt_change_stroke_width, "stroke_width", self._strokes, 1.5, 2.6),
            (ffmt_change_line_spacing, "line_spacing", self._line_spacing, 1.4, 1.6),
            (ffmt_change_letter_spacing, "letter_spacing", self._letter_spacing, 1.2, 1.3),
        ]
        for func, param_name, read, v1, v2 in cases:
            canvas = self._fresh_single_item_canvas()
            orig = self.SW.canvas
            self.SW.canvas = canvas
            try:
                item = canvas._probe_item
                baseline = read(item)
                self._change_param(func, param_name, v1)
                self._change_param(func, param_name, v2)
                # 两次发射入同一手势：悬开时栈上无独立命令
                self.assertTrue(canvas._format_gesture_open)
                self.assertEqual(canvas.text_undo_stack.count(), 0)
                canvas.commit_edit_sessions()
                # 一次手势 = 恰好一条快照命令
                self.assertEqual(canvas.text_undo_stack.count(), 1)
                self.assertAlmostEqual(read(item), v2, places=2)
                # 单步撤回到手势前基线
                canvas.undo()
                self.assertAlmostEqual(read(item), baseline, places=2)
            finally:
                self.SW.canvas = orig

    def _fresh_single_item_canvas(self):
        """independent canvas + 1 textblkitem，绑定 note_formatting_edit 分接。

        每参数一独立画布，屏蔽 count()/手势状态跨 case 污染（undo 只回移
        undo 索引、不清栈，QUndoStack.count() 不改）。
        """
        from ui.textitem import TextBlkItem as _TB

        canvas = self.Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1
        canvas.image_edit_mode = ImageEditMode.NONE
        canvas.txtblkShapeControl = _SpyShapeControl()
        canvas.alignment_enabled = False
        canvas.gv.setScene(canvas)
        xyxy = [100, 100, 380, 220]
        blk = TextBlock(xyxy=xyxy, translation="测试文字")
        blk._bounding_rect = list(xyxy)
        item = _TB(blk=blk, idx=0)
        canvas.addItem(item)
        item.updateUndoSteps()
        item.push_undo_stack.connect(
            lambda steps, fmt, b=item: (
                canvas.note_formatting_edit(b, None) if fmt else None
            )
        )
        canvas.selected_text_items = lambda: [item]
        canvas._probe_item = item
        return canvas

    def test_gesture_dirty_state_clean_mechanism(self):
        """脏状态 = QUndoStack clean 机制 + 会话脏标记。

        手势悬开（未落账）即脏；落账后未保存仍脏；update_saved_undostep
        标干净；undo 偏离 clean 索引变脏，redo 回到 clean 索引恢复干净。
        """
        self.assertFalse(self.canvas.text_change_unsaved())

        self._type_size(3.0)
        self._type_size(35.0)
        # 手势悬开：栈上无命令但持有未落账改动 → 脏
        self.assertTrue(self.canvas.text_change_unsaved())

        self.canvas.commit_edit_sessions()
        self.assertTrue(self.canvas.text_change_unsaved())

        self.canvas.update_saved_undostep()
        self.assertFalse(self.canvas.text_change_unsaved())

        self.canvas.undo()
        self.assertTrue(self.canvas.text_change_unsaved())
        self.canvas.redo()
        self.assertFalse(self.canvas.text_change_unsaved())

    def test_update_saved_undostep_does_not_emit_textstack_changed(self):
        """保存路径（update_saved_undostep）不得广播 textstack_changed。

        保存不改文档内容，广播会被 mainwindow.on_textstack_changed 当作
        文档变更而清空全局搜索结果——全局替换 prepare 阶段的同步落盘
        恰好走此路径，在收集前抹掉 searched_pattern，替换静默空转
        （实机验收 S18-3 抽查发现）。锁定：保存只刷新保存状态，信号仅在
        真实栈变更（如 undo）时发射。
        """
        emissions = []
        self.canvas.textstack_changed.connect(lambda: emissions.append(1))

        self._type_size(35.0)
        self.canvas.commit_edit_sessions()
        self.assertTrue(self.canvas.text_change_unsaved())
        emissions.clear()

        self.canvas.update_saved_undostep()
        self.assertFalse(self.canvas.text_change_unsaved())
        self.assertEqual(emissions, [])

        # 对照：真实栈变更仍广播
        self.canvas.undo()
        self.assertEqual(len(emissions), 1)


if __name__ == "__main__":
    unittest.main()
