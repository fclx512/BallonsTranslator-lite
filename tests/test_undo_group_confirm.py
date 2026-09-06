"""跨页批量组化 + 撤销确认弹窗护网（离屏，撤销体系阶段 4 第二批）。

覆盖 docs/技术实现/撤销体系阶段4计划.md 四（第二批）的核心行为：

- 组化命令标记：``NormalizeBreaksCommand`` / ``_PointAlignCommand``
  暴露 ``group_undo_summary``（页名→块数）与 ``group_page_generations``
  （多页代数快照，任一涉及页过期即整组僵尸）；
- 撤销确认门：``Canvas._confirm_group_undo`` 拒绝则本步不执行；确认
  后执行并按勾选发 ``rerender_dirty_pages_requested``；历史面板跳转
  路径（auto_cross_page）不经确认门；
- 非当前页标脏：组化命令 undo/redo 后受影响页进入重渲体系；
- ``_PointAlignCommand`` 跨页锚点：item 引用改存 blk 身份，执行期
  重解析 live item（场景重建后不写隐形对象）；
- 历史面板：组化命令行追加影响面摘要。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_undo_group_confirm.py
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

from qtpy.QtCore import QPointF  # noqa: E402
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


class GroupConfirmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas
        from ui.textitem import TextBlkItem

        cls.Canvas = Canvas
        cls.TextBlkItem = TextBlkItem
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.textedit_commands import NormalizeBreaksCommand

        self.NormalizeBreaksCommand = NormalizeBreaksCommand
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
        self.sm = _StubSceneManager()

    def tearDown(self):
        from ui import shared_widget as SW

        SW.st_manager = None
        self.canvas.gv.hide()

    def _make_block(self, pagename, text=""):
        blk = TextBlock(xyxy=[100, 100, 380, 220], translation=text)
        blk._bounding_rect = [100, 100, 380, 220]
        self.proj.pages[pagename].append(blk)
        return blk

    def _make_normalize_cmd(self, per_page=2, pages=("A", "B")):
        """构造跨页整理换行命令（不 push）。当前页块序号指向空桩列表
        （item 解析 None → 跳过 live 捕获），数据层快照齐全即可。"""
        changes = []
        for pname in pages:
            for i in range(per_page):
                self._make_block(pname)
                changes.append(
                    {
                        "pagename": pname,
                        "block_idx": i,
                        "old_translation": f"{pname}{i} old",
                        "old_rich_text": "",
                        "new_text": f"{pname}{i} new",
                        "squeeze": False,
                    }
                )
        return self.NormalizeBreaksCommand(self.proj, self.sm, changes)

    # ── 组化标记：影响面摘要 + 多页代数 ─────────────────────────

    def test_normalize_group_summary_and_generations(self):
        cmd = self._make_normalize_cmd(per_page=2, pages=("A", "B"))
        self.assertEqual(cmd.group_undo_summary(), {"A": 2, "B": 2})
        self.assertEqual(
            cmd.group_page_generations,
            {"A": self.proj.page_generation("A"), "B": 0},
        )

    def test_group_stale_when_any_affected_page_bumped(self):
        from ui.textedit_commands import command_page_stale

        cmd = self._make_normalize_cmd(pages=("A", "B"))
        self.canvas.push_text_command(cmd)
        self.assertFalse(command_page_stale(cmd, self.proj))
        # 非标签页（B）被管线整体换新 → 整组僵尸
        self.proj.bump_page_generation("B")
        self.assertTrue(command_page_stale(cmd, self.proj))

    # ── 撤销确认门 ──────────────────────────────────────────────

    def test_confirm_refusal_blocks_undo(self):
        cmd = self._make_normalize_cmd(pages=("A", "B"))
        self.canvas.push_text_command(cmd)
        idx = self.canvas.text_undo_stack.index()
        self.canvas._confirm_group_undo = lambda c: False
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), idx)
        # 数据未被还原
        self.assertEqual(self.proj.pages["B"][0].translation, "B0 new")

    def test_confirm_accept_executes_and_emits_rerender(self):
        cmd = self._make_normalize_cmd(pages=("A", "B"))
        self.canvas.push_text_command(cmd)
        emitted = []
        self.canvas.rerender_dirty_pages_requested.connect(lambda: emitted.append(1))

        def fake_confirm(c):
            self.canvas._group_undo_rerender = True
            return True

        self.canvas._confirm_group_undo = fake_confirm
        self.canvas.undo()
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)
        self.assertEqual(emitted, [1])
        self.assertFalse(self.canvas._group_undo_rerender)  # 消费后复位

    def test_auto_cross_page_path_skips_confirm(self):
        """历史面板跳转路径（auto_cross_page=True）不弹确认直接执行。"""
        cmd = self._make_normalize_cmd(pages=("A", "B"))
        self.canvas.push_text_command(cmd)
        called = []
        self.canvas._confirm_group_undo = lambda c: called.append(1) or False
        self.canvas.undo(auto_cross_page=True)
        self.assertEqual(called, [])
        self.assertEqual(self.canvas.text_undo_stack.index(), 0)

    def test_single_page_group_skips_confirm(self):
        """仅影响当前页的组命令与普通撤销无异，不弹窗。"""
        cmd = self._make_normalize_cmd(pages=("A",))
        self.assertTrue(self.canvas._confirm_group_undo(cmd))
        self.assertFalse(self.canvas._group_undo_rerender)

    # ── 非当前页标脏 ────────────────────────────────────────────

    def test_undo_marks_affected_non_current_pages_dirty(self):
        cmd = self._make_normalize_cmd(pages=("A", "B"))
        self.canvas.push_text_command(cmd)  # push 即 redo：B 已标脏
        self.assertTrue(self.proj.page_needs_rerender("B"))
        self.assertFalse(self.proj.page_needs_rerender("A"))  # 当前页不标
        self.canvas._confirm_group_undo = lambda c: True
        self.canvas.undo()
        self.assertTrue(self.proj.page_needs_rerender("B"))  # undo 同样标脏

    # ── 高级对齐：blk 锚点 + 组化标记 ───────────────────────────

    def test_point_align_anchors_and_summary(self):
        from ui.mainwindow import _PointAlignCommand

        blk_a = self._make_block("A")
        blk_b = self._make_block("B")
        item_a = self.TextBlkItem(blk=blk_a, idx=0)
        self.canvas.addItem(item_a)
        # 调用方（execute_advanced_align）按 blk 身份构建 item_changes
        cmd = _PointAlignCommand(
            self.canvas,
            [
                (blk_a, [0, 0, 10, 10], [0, 5, 10, 10]),
                (blk_b, [0, 0, 10, 10], [0, 7, 10, 10]),
            ],
            [(blk_a, QPointF(0, 0), QPointF(0, 5))],
        )
        self.assertEqual(
            cmd.item_changes, [(blk_a, QPointF(0, 0), QPointF(0, 5))]
        )
        self.assertEqual(cmd.group_undo_summary(), {"A": 1, "B": 1})

    def test_point_align_reresolves_live_item(self):
        from ui import shared_widget as SW
        from ui.mainwindow import _PointAlignCommand

        blk_a = self._make_block("A")
        old_item = self.TextBlkItem(blk=blk_a, idx=0)
        self.canvas.addItem(old_item)
        cmd = _PointAlignCommand(
            self.canvas,
            [(blk_a, [0, 0, 10, 10], [0, 5, 10, 10])],
            [(blk_a, QPointF(0, 0), QPointF(0, 5))],
        )
        # 场景重建：新 item 换旧 item，命令 undo 须重放到新 item
        new_item = self.TextBlkItem(blk=blk_a, idx=0)
        self.canvas.removeItem(old_item)
        self.canvas.addItem(new_item)
        SW.st_manager = self.sm
        self.sm.textblk_item_list = [new_item]
        cmd.undo()
        self.assertEqual(new_item.pos(), QPointF(0, 0))
        self.assertEqual(blk_a._bounding_rect, [0, 0, 10, 10])
        # 旧 item（已脱离场景）不被触碰（其位置仍是构造时的 100,100）
        self.assertEqual(old_item.pos(), QPointF(100, 100))

    def test_point_align_push_undo_marks_dirty(self):
        from ui.mainwindow import _PointAlignCommand

        blk_a = self._make_block("A")
        blk_b = self._make_block("B")
        cmd = _PointAlignCommand(
            self.canvas,
            [
                (blk_a, [0, 0, 10, 10], [0, 5, 10, 10]),
                (blk_b, [0, 0, 10, 10], [0, 7, 10, 10]),
            ],
            [(blk_a, QPointF(0, 0), QPointF(0, 5))],
        )
        self.canvas.push_text_command(cmd)  # push 即 redo
        self.assertEqual(blk_b._bounding_rect, [0, 7, 10, 10])
        self.assertTrue(self.proj.page_needs_rerender("B"))
        self.assertFalse(self.proj.page_needs_rerender("A"))
        self.canvas._confirm_group_undo = lambda c: True
        self.canvas.undo()
        self.assertEqual(blk_b._bounding_rect, [0, 0, 10, 10])
        self.assertEqual(blk_a._bounding_rect, [0, 0, 10, 10])

    # ── 历史面板：组化命令行摘要 ────────────────────────────────

    def test_history_model_group_summary_suffix(self):
        from ui.history_panel import _HistoryModel, _ROLE_KIND, _ROLE_STACK_POS

        cmd = self._make_normalize_cmd(pages=("A", "B"))
        panel = SimpleNamespace(
            empty_label="Original",
            zombie_label="stale",
            image_filter=False,
            stack=self.canvas.text_undo_stack,
            _canvas=self.canvas,
        )
        model = _HistoryModel(panel, None)
        self.canvas.push_text_command(cmd)
        model.rebuild()
        texts = [
            model.index(r).data()
            for r in range(model.rowCount())
            if model.index(r).data(_ROLE_KIND) == "state"
            and model.index(r).data(_ROLE_STACK_POS) == 1
        ]
        self.assertEqual(len(texts), 1)
        self.assertIn("Normalize Line Breaks", texts[0])
        self.assertIn("2 pages / 4 blocks", texts[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
