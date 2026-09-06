"""跨页撤销历史护网（离屏，撤销体系阶段 4 第一批）。

覆盖撤销体系阶段 4 第一批（跨页历史）的核心行为：

- 命令页标签/页代数捕获（canvas._tag_text_command 经 push_text_command）；
- 跨页撤销门：跨页第一按只提示（armed），第二次按跳页后执行；
  僵尸命令（页屏障过期/显式失效）不拦、无操作消费；
- 命令锚点重解析：场景重建（新 item/pairwidget）后 undo 仍回放到
  新场景的 live widget 上；
- 历史面板模型：页头分组行 + 栈位置映射。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_undo_cross_page.py
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

from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


class _SpyShapeControl:

    def __init__(self):
        self.blk_item = None

    def isVisible(self):
        return False

    def setBlkItem(self, item):
        self.blk_item = item

    def updateBoundingRect(self):
        pass


class _StubSceneManager:
    """resolve_blk_entry 的场景管理器桩：只暴露两个列表。"""

    def __init__(self):
        self.textblk_item_list = []
        self.pairwidget_list = []


class CrossPageUndoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas
        from ui.textedit_area import TransTextEdit
        from ui.textitem import TextBlkItem

        cls.Canvas = Canvas
        cls.TransTextEdit = TransTextEdit
        cls.TextBlkItem = TextBlkItem
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.textedit_commands import TypingSessionCommand

        self.TypingSessionCommand = TypingSessionCommand
        self.canvas = self.Canvas()
        self.proj = ProjImgTrans()
        self.proj.pages = {"A": [], "B": []}
        self.proj.current_img = "A"
        self.canvas.imgtrans_proj = self.proj
        self.canvas.editor_index = 1
        self.canvas.image_edit_mode = SimpleNamespace()
        self.canvas.txtblkShapeControl = _SpyShapeControl()
        self.canvas.alignment_enabled = False
        self.canvas.gv.setScene(self.canvas)
        self.canvas.gv.show()
        self._APP.processEvents()
        self._jumps = []
        self.canvas.page_jump_requested.connect(self._on_jump_requested)

    def tearDown(self):
        from ui import shared_widget as SW

        SW.st_manager = None
        self.canvas.gv.hide()

    def _on_jump_requested(self, pagename):
        # 模拟 mainwindow 跳页槽：同步切当前页
        self._jumps.append(pagename)
        self.proj.current_img = pagename

    def _make_block(self, pagename, text=""):
        blk = TextBlock(xyxy=[100, 100, 380, 220], translation=text)
        blk._bounding_rect = [100, 100, 380, 220]
        self.proj.pages[pagename].append(blk)
        item = self.TextBlkItem(blk=blk, idx=len(self.proj.pages[pagename]) - 1)
        self.canvas.addItem(item)
        edit = self.TransTextEdit(item.idx, None)
        edit.setPlainText(text)
        return blk, item, edit

    def _push_typing(self, item, edit, before, after):
        # 复现真实键入：会话闭合前内容已施加到 widget，命令 redo 首跳
        from ui.textedit_commands import replay_guard, sync_text_by_diff

        with replay_guard(item, edit):
            if item is not None:
                sync_text_by_diff(item, after)
            sync_text_by_diff(edit, after)
        cmd = self.TypingSessionCommand(item, edit, before, after)
        self.canvas.push_text_command(cmd)
        return cmd

    # ── 页标签与代数捕获 ────────────────────────────────────────

    def test_command_tagged_with_page_and_generation(self):
        blk, item, edit = self._make_block("A")
        self.proj.bump_page_generation("A")
        cmd = self._push_typing(item, edit, "", "hello")
        self.assertEqual(cmd.pagename, "A")
        self.assertEqual(cmd.page_generation, 1)
        self.assertTrue(self.canvas.text_undo_stack.canUndo())

    # ── 跨页撤销门 ──────────────────────────────────────────────

    def test_cross_page_undo_requires_second_press(self):
        blk_a, item_a, edit_a = self._make_block("A", "AAA")
        self._push_typing(item_a, edit_a, "AAA", "AAAB")
        self.proj.current_img = "B"  # 切页后再产生 B 页命令（标签随当前页）
        blk_b, item_b, edit_b = self._make_block("B", "BBB")
        self._push_typing(item_b, edit_b, "BBB", "BBBC")
        self.assertEqual(self.canvas.text_undo_stack.index(), 2)

        # 撤销页 B 命令：当前页就是 B，直接执行
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        self.assertEqual(edit_b.toPlainText(), "BBB")

        # 下一步是页 A 的命令：第一按被拦截（armed），不执行
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        self.assertEqual(edit_a.toPlainText(), "AAAB")  # 未回退

        # 第二按：跳页（信号模拟切换）后执行
        self.canvas.undo()
        self.assertEqual(self._jumps, ["A"])
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)
        self.assertEqual(edit_a.toPlainText(), "AAA")

    def test_cross_page_armed_disarmed_by_other_operation(self):
        blk_a, item_a, edit_a = self._make_block("A", "AAA")
        self._push_typing(item_a, edit_a, "AAA", "AAAB")
        self.proj.current_img = "B"
        blk_b, item_b, edit_b = self._make_block("B", "BBB")
        self._push_typing(item_b, edit_b, "BBB", "BBBC")

        # 撤 B（当前页）
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        # 第一按（跨页 A）：armed
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        # 其他操作（保存点）解除 armed
        self.canvas.update_saved_undostep()
        # 再按：重新 armed，仍不执行
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        # 第二按（armed 存续）：跳页执行
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)

    def test_cross_page_redo_symmetric(self):
        blk_a, item_a, edit_a = self._make_block("A", "AAA")
        self._push_typing(item_a, edit_a, "AAA", "AAAB")
        self.proj.current_img = "B"
        blk_b, item_b, edit_b = self._make_block("B", "BBB")
        self._push_typing(item_b, edit_b, "BBB", "BBBC")

        self.canvas.undo()  # 撤 B（当前页）
        self.canvas.undo()  # 跨页 A：armed
        self.canvas.undo()  # 跳页 A + 撤 A
        self.assertEqual(self._jumps, ["A"])
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)

        # redo：栈顶下一命令是 A 的（跳页后当前页已是 A），直接重做
        self.canvas.redo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        self.assertEqual(edit_a.toPlainText(), "AAAB")

        # 下一命令是 B 的（跨页）：armed → 二按跳页执行
        self.canvas.redo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 1)
        self.canvas.redo()
        self.assertEqual(self._jumps, ["A", "B"])
        self.assertEqual(self.canvas.text_undo_stack.index(), 2)
        self.assertEqual(edit_b.toPlainText(), "BBBC")
        self.assertEqual(edit_b.toPlainText(), "BBBC")

    # ── 页屏障：僵尸命令 ────────────────────────────────────────

    def test_stale_page_command_is_noop_skip(self):
        blk_a, item_a, edit_a = self._make_block("A", "AAA")
        self._push_typing(item_a, edit_a, "AAA", "AAAB")
        self.canvas.invalidate_text_history_for_page("A")
        self.proj.current_img = "B"
        # 僵尸命令：不拦、不跳页、无内容变更，栈位置照常消费
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)
        self.assertEqual(edit_a.toPlainText(), "AAAB")
        self.assertEqual(self._jumps, [])

    def test_generation_bump_makes_command_stale(self):
        blk, item, edit = self._make_block("A", "AAA")
        self._push_typing(item, edit, "AAA", "AAAB")
        # 模拟检测管线整体换新 blk_list
        self.proj.bump_page_generation("A")
        self.canvas.undo()
        # 命令已消费（无操作），文本未被回退
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)
        self.assertEqual(edit.toPlainText(), "AAAB")

    def test_prepare_page_switch_keeps_text_stack(self):
        blk, item, edit = self._make_block("A", "AAA")
        self._push_typing(item, edit, "AAA", "AAAB")
        before_index = self.canvas.text_undo_stack.index()
        self.canvas.prepare_page_switch()
        self.assertEqual(self.canvas.text_undo_stack.index(), before_index)
        self.assertTrue(self.canvas.text_undo_stack.canUndo())
        self.assertEqual(self.canvas.saved_drawundo_step, 0)
        self.assertEqual(self.canvas.num_pushed_drawstep, 0)

    # ── 锚点重解析（场景重建）───────────────────────────────────

    def test_undo_after_scene_rebuild_resolves_new_widgets(self):
        from ui import shared_widget as SW

        blk, item, edit = self._make_block("A", "AAA")
        self._push_typing(item, edit, "AAA", "AAAB")

        sm = _StubSceneManager()
        SW.st_manager = sm
        # 模拟切页重建：旧 item/edit 脱离场景，blk 重建出全新 item/edit
        item_new = self.TextBlkItem(blk=blk, idx=0)
        self.canvas.addItem(item_new)
        edit_new = self.TransTextEdit(0, None)
        edit_new.setPlainText("AAAB")
        sm.textblk_item_list = [item_new]
        pw = SimpleNamespace(e_trans=edit_new, e_source=None)
        sm.pairwidget_list = [pw]
        self.canvas.textblk_item_list = sm.textblk_item_list

        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)
        # 重放落在重建后的 live widget 上
        self.assertEqual(edit_new.toPlainText(), "AAA")
        # 旧（已脱离场景的）引用未被误写
        self.assertEqual(edit.toPlainText(), "AAAB")

    # ── 历史面板模型 ────────────────────────────────────────────

    def test_history_model_groups_by_page(self):
        from ui.history_panel import (
            _ROLE_KIND,
            _ROLE_STACK_POS,
            _HistoryModel,
            HistoryPanel,
        )

        blk_a, item_a, edit_a = self._make_block("A", "AAA")
        self._push_typing(item_a, edit_a, "AAA", "AAAB")
        self.proj.current_img = "B"
        blk_b, item_b, edit_b = self._make_block("B", "BBB")
        self._push_typing(item_b, edit_b, "BBB", "BBBC")

        panel = HistoryPanel()
        panel._canvas = self.canvas
        panel.stack = self.canvas.text_undo_stack
        model = panel.model
        model.rebuild()

        # 行序：原始状态、页头 A、A 命令、页头 B、B 命令
        self.assertEqual(model.rowCount(), 5)
        self.assertEqual(model.index(0, 0).data(_ROLE_STACK_POS), 0)
        self.assertEqual(model.index(1, 0).data(_ROLE_KIND), "header")
        self.assertEqual(model.index(1, 0).data(), "A")
        self.assertEqual(model.index(2, 0).data(_ROLE_STACK_POS), 1)
        self.assertEqual(model.index(3, 0).data(_ROLE_KIND), "header")
        self.assertEqual(model.index(4, 0).data(_ROLE_STACK_POS), 2)

        # 连续同页命令不重复插页头
        self._push_typing(item_a, edit_a, "AAAB", "AAAC")
        model.rebuild()
        self.assertEqual(model.rowCount(), 6)
        kinds = [
            model.index(r, 0).data(_ROLE_KIND) for r in range(model.rowCount())
        ]
        self.assertEqual(kinds.count("header"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
