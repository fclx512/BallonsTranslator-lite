"""撤销体系行为特征护网（离屏）。

把撤销专项排查的离线复现台固化为常驻测试（撤销体系重构阶段 0-b）：
真实 Canvas +
TextBlkItem + **真实 TransTextEdit**（面板侧用真身不用替身——真身发射
语义漂移时替身发现不了），按 ``ui/scenetext_manager.py`` 的四个 handler
分接布线。

锁定目标行为（计划第二节，3a 快照命令制落地后全部转正）：

- 纯 canvas / 纯面板键入连续聚合为一个撤销步（键入会话制）；undo/redo
  后双端文本一致；
- 键入↔格式化交错序列：键入会话与格式化手势互为边界，各落一条快照
  命令，undo 逐层回退且双端一致；
- 管线运行命令（``ui/drawing_commands.py::RunBlkTransCommand``）快照
  重放回归：构造期前后快照 + undo/redo 内容重放；
- 多块格式化手势 undo/redo 性能粗断言（防终局「正确但卡」）。

注意：键入会话/格式化手势在边界（选区变化、失焦、idle 超时、push 其它
命令、undo/redo）才落账，计数断言前需显式 ``canvas.commit_edit_sessions()``
（生产上由 scenetext_manager 的 focus/selection 钩子驱动）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_undo_safety_net.py
"""

import os
import os.path as osp
import sys
import time
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QEvent, Qt  # noqa: E402
from qtpy.QtGui import QKeyEvent, QTextCursor  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.image_edit import ImageEditMode  # noqa: E402
from ui.textedit_commands import sync_text_by_diff  # noqa: E402
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


def _key_event(ch: str) -> QKeyEvent:
    key = getattr(Qt.Key, "Key_" + ch.upper())
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, ch)


class UndoSafetyNetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui import shared_widget as SW
        from ui.canvas import Canvas
        from ui.textedit_area import SourceTextEdit, TransTextEdit
        from ui.textitem import TextBlkItem

        cls.SW = SW
        cls.Canvas = Canvas
        cls.TextBlkItem = TextBlkItem
        cls.TransTextEdit = TransTextEdit
        cls.SourceTextEdit = SourceTextEdit
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
        # offscreen 下 item 的 hasFocus() 需要 gv.show() + 事件循环后才成立，
        # 否则 ui/text_engine/item.py::on_content_changed 的发射门整体跳过
        self.canvas.gv.show()
        self._APP.processEvents()

        blk = TextBlock(xyxy=[100, 100, 380, 220], translation="")
        blk._bounding_rect = [100, 100, 380, 220]
        self.item = self.TextBlkItem(blk=blk, idx=0)
        self.canvas.addItem(self.item)

        # 面板真身。初始 setPlainText 必须在挂接信号之前，否则初始化同步
        # 会触发对账（文本不一致时会真的去改 item 文档）
        self.edit = self.TransTextEdit(0, None)
        self.edit.setPlainText("")

        self._wire()

        # ffmt 路径经 SW.canvas.selected_text_items() 取选中集合
        self._orig_canvas = self.SW.canvas
        self.SW.canvas = self.canvas
        self.canvas.selected_text_items = lambda: [self.item]

    def tearDown(self):
        self.SW.canvas = self._orig_canvas
        self.edit.deleteLater()
        self.canvas.gv.hide()

    # ── 布线：按 ui/scenetext_manager.py 的四个 handler 分接 ─────────

    def _wire(self):
        item, edit = self.item, self.edit
        item.push_undo_stack.connect(
            lambda fmt: self._on_item_push_undo_stack(fmt)
        )
        edit.push_undo_stack.connect(self._on_edit_push_undo_stack)
        item.propagate_user_edited.connect(self._on_propagate_item_edit)
        edit.propagate_user_edited.connect(self._on_propagate_edit)

    def _on_item_push_undo_stack(self, is_formatting):
        # 对应 ui/scenetext_manager.py::on_push_textitem_undostack
        # 3a：键入已由 propagate 登记，此处只接管格式化 → 并入格式化手势
        if is_formatting:
            self.canvas.note_formatting_edit(
                self.item, SimpleNamespace(textblk_item=None)
            )

    def _on_edit_push_undo_stack(self):
        # 对应 ui/scenetext_manager.py::on_push_edit_stack
        # 3a：译文侧键入已由 propagate 登记；仅原文编辑器走此路登记
        if type(self.edit) is self.SourceTextEdit:
            self.canvas.note_source_edit(
                self.edit,
                self.edit.change_from,
                self.edit.change_removed,
                self.edit.change_added,
            )

    def _on_propagate_item_edit(self, pos, removed, added_text):
        # 对应 ui/scenetext_manager.py::on_propagate_textitem_edit
        # before 快照在镜像同步前抓：e_trans 此时尚持旧文
        edit = self.edit
        if not edit.in_acts:
            before_text = edit.toPlainText()
            edit.in_acts = True
            try:
                changed = sync_text_by_diff(edit, self.item.toPlainText())
            finally:
                edit.in_acts = False
            if changed:
                self.canvas.note_typing_edit(
                    self.item, edit, before_text, pos, removed, len(added_text)
                )

    def _on_propagate_edit(self):
        # 对应 ui/scenetext_manager.py::on_propagate_transwidget_edit
        # before 快照在镜像同步前抓：item 此时尚持旧文
        if self.item.isEditing():
            self.item.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
            )
        before_text = self.item.toPlainText()
        if sync_text_by_diff(self.item, self.edit.toPlainText()):
            self.canvas.note_typing_edit(
                self.item, self.edit, before_text,
                self.edit.change_from,
                self.edit.change_removed,
                self.edit.change_added,
            )

    # ── 驱动助手 ─────────────────────────────────────────────────────

    def _type_item(self, text: str):
        """画布键入：真实 keyPressEvent 路径（Qt 自动合并语义）。"""
        if not self.item.isEditing():
            self.item.startEdit()
            self._APP.processEvents()
        cursor = self.item.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.item.setTextCursor(cursor)
        for ch in text:
            self.item.keyPressEvent(_key_event(ch))
        self._APP.processEvents()

    def _type_edit(self, text: str, at_end: bool = True):
        """面板键入：真实 keyPressEvent 路径。at_end=False 时在当前光标处插入。"""
        if at_end:
            cursor = self.edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.edit.setTextCursor(cursor)
        for ch in text:
            self.edit.keyPressEvent(_key_event(ch))
        self._APP.processEvents()

    def _type_size(self, value: float):
        """模拟面板字号键入的一次 param_changed 发射（global 分支）。"""
        from ui.fontformat_commands import ffmt_change_font_size
        from utils.fontformat import FontFormat

        ffmt_change_font_size("font_size", value, FontFormat(), is_global=True)
        self._APP.processEvents()

    def _font_size(self) -> float:
        return round(self.item.get_fontformat().font_size, 2)

    def _assert_consistent(self, expected=None):
        itxt = self.item.toPlainText()
        etxt = self.edit.toPlainText()
        self.assertEqual(
            itxt, etxt, f"画布/面板失同步: item={itxt!r} edit={etxt!r}"
        )
        if expected is not None:
            self.assertEqual(itxt, expected)

    # ── 目标行为：纯键入 ─────────────────────────────────────────────

    def test_canvas_typing_aggregates_single_step(self):
        """目标行为 1：画布连续键入聚合为一个撤销步（键入会话制）。"""
        self._type_item("AB")
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self._assert_consistent("AB")

        self.canvas.undo()
        self._assert_consistent("")

        self.canvas.redo()
        self._assert_consistent("AB")

    def test_panel_typing_aggregates_single_step(self):
        self._type_edit("AB")
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self._assert_consistent("AB")

        self.canvas.undo()
        self._assert_consistent("")

        self.canvas.redo()
        self._assert_consistent("AB")

    def test_panel_typing_burst_two_commands(self):
        """burst：非相邻位置插入打断会话相邻性，两次键入各成一个命令。

        会话相邻性判定与 Qt 合并同语义：插入点不接前次变更末尾 → 闭合旧
        会话另起新会话——这是生产上 burst 的真实成因。
        """
        self._type_edit("A")
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.edit.setTextCursor(cursor)
        self._type_edit("B", at_end=False)  # 插入点 0 ≠ 前次末尾 1 → 新会话
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)
        self._assert_consistent("BA")

        self.canvas.undo()
        self._assert_consistent("A")
        self.canvas.undo()
        self._assert_consistent("")
        self.canvas.redo()
        self.canvas.redo()
        self._assert_consistent("BA")

    # ── 目标行为：键入↔格式化交错 ───────────────────────────────────

    def test_interleaved_formatting_then_typing(self):
        """计划 1.3 的交错序列：面板键入 A → 改字号 30 → 面板键入 B → Z×3。

        目标行为：键入会话与格式化手势互为边界——B 有自己的命令；
        Z① 只退 B、字号保持；Z② 退字号；Z③ 退 A；每步双端一致。
        """
        baseline_size = self._font_size()
        self._type_edit("A")
        self.canvas.commit_edit_sessions()
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)

        self._type_size(30.0)
        self._type_edit("B")
        self.canvas.commit_edit_sessions()
        # B 应有自己的命令：A 会话 + 格式化手势 + B 会话 = 3 步
        self.assertEqual(self.canvas.text_undo_stack.count(), 3)
        self._assert_consistent("AB")

        self.canvas.undo()  # 只退 B
        self._assert_consistent("A")
        self.assertEqual(self._font_size(), 30.0)

        self.canvas.undo()  # 退字号
        self._assert_consistent("A")
        self.assertEqual(self._font_size(), baseline_size)

        self.canvas.undo()  # 退 A
        self._assert_consistent("")

    # ── 目标行为：非交错混合序列双端一致 ─────────────────────────────

    def test_mixed_sequence_consistency(self):
        baseline_size = self._font_size()
        self._type_edit("AB")
        self._assert_consistent("AB")

        self._type_size(30.0)
        self.assertEqual(self._font_size(), 30.0)
        # 干净的边界闭合法（选区变化），不触发交错纠缠
        self.canvas.on_selection_changed()
        self.assertFalse(self.canvas._format_gesture_open)
        self.assertEqual(self.canvas.text_undo_stack.count(), 2)

        self.canvas.undo()  # 退字号
        self.assertEqual(self._font_size(), baseline_size)
        self._assert_consistent("AB")

        self.canvas.undo()  # 退键入
        self._assert_consistent("")

        self.canvas.redo()
        self._assert_consistent("AB")
        self.canvas.redo()
        self.assertEqual(self._font_size(), 30.0)
        self._assert_consistent("AB")


class RunBlkTransCommandUndoTest(unittest.TestCase):
    """管线运行命令撤销回归（``ui/drawing_commands.py::RunBlkTransCommand``）。

    3a 起该命令改为快照重放：构造期抓 item/e_trans/e_source 前后快照
    （item 侧 HTML 全保真），undo/redo = 内容重放，不再借道文本文档
    私有 undo 栈排水。本测试锁住文字部分的回退/重做行为。
    """

    @classmethod
    def setUpClass(cls):
        from ui.textedit_area import SourceTextEdit, TransTextEdit
        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.TransTextEdit = TransTextEdit
        cls.SourceTextEdit = SourceTextEdit
        cls._APP = QApplication.instance() or QApplication([])

    def test_mode1_text_undo_redo(self):
        from ui.drawing_commands import RunBlkTransCommand

        blk = TextBlock(
            xyxy=[100, 100, 380, 220], text=["原文"], translation="旧译"
        )
        blk._bounding_rect = [100, 100, 380, 220]
        item = self.TextBlkItem(blk=blk, idx=0)

        e_trans = self.TransTextEdit(0, None)
        e_trans.setPlainText("旧译")
        e_source = self.SourceTextEdit(0, None)
        e_source.setPlainText("旧文")

        blk.translation = "新译"
        pairw = SimpleNamespace(e_trans=e_trans, e_source=e_source)
        # mode=1：写译文+原文、不碰修复图像（不依赖 canvas/imgtrans_proj）
        cmd = RunBlkTransCommand(None, [item], [pairw], mode=1)
        cmd.redo()  # 模拟 QUndoStack.push 的自动 redo（op_counter 消费）

        self.assertEqual(item.toPlainText(), "新译")
        self.assertEqual(e_trans.toPlainText(), "新译")
        self.assertEqual(e_source.toPlainText(), "原文")

        cmd.undo()
        self.assertEqual(item.toPlainText(), "旧译")
        self.assertEqual(e_trans.toPlainText(), "旧译")
        self.assertEqual(e_source.toPlainText(), "旧文")

        cmd.redo()
        self.assertEqual(item.toPlainText(), "新译")
        self.assertEqual(e_trans.toPlainText(), "新译")
        self.assertEqual(e_source.toPlainText(), "原文")

        e_trans.deleteLater()
        e_source.deleteLater()


class FormatMacroPerfTest(unittest.TestCase):
    """多块格式化宏 undo/redo 性能粗断言（防终局「正确但卡」）。

    阈值取宽松上限，只挡灾难性回归；实测值打印出来供阶段 3 校准。
    """

    PERF_BUDGET_SECONDS = 5.0
    NUM_ITEMS = 50

    @classmethod
    def setUpClass(cls):
        from ui import shared_widget as SW
        from ui.canvas import Canvas
        from ui.textitem import TextBlkItem

        cls.SW = SW
        cls.Canvas = Canvas
        cls.TextBlkItem = TextBlkItem
        cls._APP = QApplication.instance() or QApplication([])

    def test_macro_undo_redo_perf(self):
        from ui.fontformat_commands import ffmt_change_font_size
        from utils.fontformat import FontFormat

        canvas = self.Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1
        canvas.image_edit_mode = ImageEditMode.NONE
        canvas.txtblkShapeControl = _SpyShapeControl()
        canvas.alignment_enabled = False
        canvas.gv.setScene(canvas)

        items = []
        for i in range(self.NUM_ITEMS):
            xyxy = [100 + (i % 10) * 320, 100 + (i // 10) * 160,
                    380 + (i % 10) * 320, 220 + (i // 10) * 160]
            blk = TextBlock(xyxy=xyxy, translation="测试文字")
            blk._bounding_rect = list(xyxy)
            item = self.TextBlkItem(blk=blk, idx=i)
            canvas.addItem(item)
            item.push_undo_stack.connect(
                lambda fmt, _it=item: fmt
                and canvas.note_formatting_edit(
                    _it, SimpleNamespace(textblk_item=None)
                )
            )
            items.append(item)

        orig_canvas = self.SW.canvas
        self.SW.canvas = canvas
        canvas.selected_text_items = lambda: list(items)
        try:
            ffmt_change_font_size("font_size", 3.0, FontFormat(), is_global=True)
            ffmt_change_font_size("font_size", 35.0, FontFormat(), is_global=True)
            canvas.commit_edit_sessions()
            # 50 块 × 2 键值并入同一格式化手势，闭合落一条快照命令
            self.assertEqual(canvas.text_undo_stack.count(), 1)

            t0 = time.perf_counter()
            canvas.undo()
            canvas.redo()
            elapsed = time.perf_counter() - t0
            print(
                f"\n[perf] {self.NUM_ITEMS} 块格式化宏 undo+redo: "
                f"{elapsed * 1000:.1f} ms"
            )
            self.assertLess(elapsed, self.PERF_BUDGET_SECONDS)
        finally:
            self.SW.canvas = orig_canvas


if __name__ == "__main__":
    unittest.main(verbosity=2)
