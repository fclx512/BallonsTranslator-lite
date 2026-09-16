"""原文编辑清程序标签（D29）的接线测试。

覆盖两处落点：
- ``ui/scenetext_manager.py::SceneTextManager`` 的原文面板变更链路
  （信号 → handle_source_panel_edit → 判据）：面板原文偏离数据层才清，
  程序性面板回写（合并式同步）不清；译文编辑不清。
- ``ui/textedit_commands.py::ApplyBlockTextCommand`` 写原文分支：人点了
  应用即视为原文被改定，清程序标签；写译文分支不清。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_tag_prune_on_source_edit.py
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

from qtpy.QtCore import QObject  # noqa: E402
from qtpy.QtGui import QTextCursor  # noqa: E402
from qtpy.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from utils.block_tags import (  # noqa: E402
    MISREAD_TAG_ID,
    apply_misread_tag,
    classify_misread_text,
    has_tag,
    set_tag,
)
from utils.textblock import TextBlock  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

from ui.scenetext_manager import SceneTextManager  # noqa: E402
from ui.textedit_area import SourceTextEdit, TransTextEdit  # noqa: E402
from ui.textedit_commands import ApplyBlockTextCommand  # noqa: E402
from ui.textitem import TextBlkItem  # noqa: E402


class _FakeCanvas:
    def __init__(self):
        self.notes = []
        self.saves = []

    def note_source_edit(self, edit, change_from, removed, added):
        self.notes.append((edit, change_from, removed, added))

    def setProjSaveState(self, unsaved):
        self.saves.append(unsaved)


class _StManagerShim(QObject):
    """承载 SceneTextManager 原文编辑三个方法的真实现（QObject 便于 sender()）。"""

    _METHODS = (
        "on_push_edit_stack",
        "handle_source_panel_edit",
        "_prune_program_tags_after_source_edit",
    )

    def __init__(self, canvas, items, pairs):
        super().__init__()
        self.canvas = canvas
        self.textblk_item_list = items
        self.pairwidget_list = pairs
        for name in self._METHODS:
            setattr(self, name, getattr(SceneTextManager, name).__get__(self))


class TestPruneOnSourceEdit(unittest.TestCase):
    def setUp(self):
        # pcfg 是单例：别的用例可能已 load_config() 载入用户开关，
        # 本用例显式声明依赖的基线，跑完还原。
        import utils.config as cfg

        self._badge_flag = cfg.pcfg.show_tag_badge
        cfg.pcfg.show_tag_badge = True

        self.blk = TextBlock(
            text=["166"],
            translation="t",
            lines=[[[0, 0], [100, 0], [100, 30], [0, 30]]],
        )
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1, reviewed=True)
        apply_misread_tag(self.blk, classify_misread_text(self.blk.get_text()))
        set_tag(self.blk, "handwritten", "manual")

        self.scene = QGraphicsScene()
        self.item = TextBlkItem(self.blk, 0)
        self.scene.addItem(self.item)
        self.badge_calls = 0
        real_refresh = self.item.refresh_tag_badge

        def counting_refresh():
            self.badge_calls += 1
            real_refresh()

        self.item.refresh_tag_badge = counting_refresh

        self.src = SourceTextEdit(0, None)
        self.trans = TransTextEdit(0, None)
        self.src.setPlainText(self.blk.get_text())
        self.trans.setPlainText(self.blk.translation)

        self.canvas = _FakeCanvas()
        self.sm = _StManagerShim(
            self.canvas,
            [self.item],
            [SimpleNamespace(e_source=self.src, e_trans=self.trans)],
        )
        # 与 addTextBlock 一致：初始 setPlainText 完成后再接信号
        self.src.push_undo_stack.connect(self.sm.on_push_edit_stack)
        self.trans.push_undo_stack.connect(self.sm.on_push_edit_stack)

    def tearDown(self):
        import utils.config as cfg

        cfg.pcfg.show_tag_badge = self._badge_flag
        self.scene.removeItem(self.item)

    def _type_into(self, edit, text):
        cursor = edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        edit.setTextCursor(cursor)
        edit.insertPlainText(text)

    def test_source_typing_clears_program_tags(self):
        self._type_into(self.src, "0")
        # 只清标签，不动数据层原文（面板 → 数据的写回仍归 updateTextBlkList）
        self.assertEqual(self.blk.text, ["166"])
        self.assertEqual(self.src.toPlainText(), "1660")
        # 程序标签全清（含已驳回条目），人工标签保留
        self.assertNotIn("ocr_low_conf", self.blk.tags)
        self.assertNotIn(MISREAD_TAG_ID, self.blk.tags)
        self.assertTrue(has_tag(self.blk, "handwritten"))
        # 画布徽标刷新一次、项目置未保存；原文键入会话照常登记
        self.assertEqual(self.badge_calls, 1)
        self.assertEqual(self.canvas.saves, [True])
        self.assertEqual(len(self.canvas.notes), 1)

        # 继续键入：已无可清条目 → 不再重复刷徽标 / 置脏
        self._type_into(self.src, "1")
        self.assertEqual(self.badge_calls, 1)
        self.assertEqual(self.canvas.saves, [True])

    def test_translation_typing_keeps_program_tags(self):
        self._type_into(self.trans, "x")
        self.assertIn("ocr_low_conf", self.blk.tags)
        self.assertIn(MISREAD_TAG_ID, self.blk.tags)
        self.assertEqual(self.badge_calls, 0)
        self.assertEqual(self.canvas.saves, [])
        self.assertEqual(self.canvas.notes, [])

    def test_programmatic_source_sync_keeps_tags(self):
        """合并式回写：数据层换新块后把面板同步成一致 → 不算人工编辑。"""
        self.blk.text = ["1660"]
        self.src.setPlainText(self.blk.get_text())
        self.assertIn("ocr_low_conf", self.blk.tags)
        self.assertIn(MISREAD_TAG_ID, self.blk.tags)
        self.assertEqual(self.canvas.saves, [])

    def test_paste_source_write_is_an_edit(self):
        """粘贴原文（全量替换面板文本）走同一条信号链 → 清标签。"""
        self.src.setPlainText("160")
        self.assertNotIn("ocr_low_conf", self.blk.tags)
        self.assertNotIn(MISREAD_TAG_ID, self.blk.tags)
        self.assertEqual(self.canvas.saves, [True])

    def test_mismatched_widget_is_ignored(self):
        """面板与块列表错位（合并/删除进行中）时不动数据。"""
        other = SourceTextEdit(0, None)
        other.setPlainText("999")
        # ① 传进来的编辑器不是该 idx 对应的面板
        self.sm.handle_source_panel_edit(other)
        self.assertIn("ocr_low_conf", self.blk.tags)
        # ② idx 越界（列表已被合并命令截短）
        self.sm.pairwidget_list = []
        self.sm.handle_source_panel_edit(self.src)
        self.assertIn("ocr_low_conf", self.blk.tags)
        self.assertEqual(self.canvas.saves, [])


class TestApplyBlockTextCommandPrune(unittest.TestCase):
    def setUp(self):
        import utils.config as cfg

        self._badge_flag = cfg.pcfg.show_tag_badge
        cfg.pcfg.show_tag_badge = True

        self.blk = TextBlock(
            text=["166"],
            translation="t",
            lines=[[[0, 0], [100, 0], [100, 30], [0, 30]]],
        )
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1)
        apply_misread_tag(self.blk, classify_misread_text(self.blk.get_text()))

        self.scene = QGraphicsScene()
        self.item = TextBlkItem(self.blk, 0)
        self.scene.addItem(self.item)
        self.src = SourceTextEdit(0, None)
        self.trans = TransTextEdit(0, None)
        self.src.setPlainText(self.blk.get_text())
        self.trans.setPlainText(self.blk.translation)
        self.pairw = SimpleNamespace(e_source=self.src, e_trans=self.trans)

    def tearDown(self):
        import utils.config as cfg

        cfg.pcfg.show_tag_badge = self._badge_flag
        self.scene.removeItem(self.item)

    def test_apply_source_clears_program_tags(self):
        cmd = ApplyBlockTextCommand(self.item, self.pairw, "source", "160")
        self.assertEqual(self.blk.get_text(), "160")
        self.assertEqual(self.blk.tags, {})
        # redo 幂等：undo 不回滚标签，redo 也不复活
        cmd.redo()
        self.assertEqual(self.blk.tags, {})

    def test_apply_translation_keeps_program_tags(self):
        ApplyBlockTextCommand(self.item, self.pairw, "translation", "訳文")
        self.assertEqual(self.blk.translation, "訳文")
        self.assertIn("ocr_low_conf", self.blk.tags)
        self.assertIn(MISREAD_TAG_ID, self.blk.tags)


if __name__ == "__main__":
    unittest.main()
