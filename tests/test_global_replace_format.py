"""GlobalReplaceApplier 格式 patch / 施加语义回归。

阶段 2（查找替换重构）：统一施加器在文本路径之外支持 ``format_changes``
（``utils.style_query.build_query_changes`` 契约的
``{pagename, block_idx, old_ffmt, new_ffmt}`` 列表）。当前页格式命中块
经 live item 施加整块快照外的格式写入，非当前页走数据直写
+ ``mark_page_needs_rerender``。

阶段 2.5（批量快照回滚）：施加器不再继承 QUndoCommand、不进任何撤销栈
——批量替换的撤销 = 替换前项目快照整体回滚
（``utils/proj_imgtrans.py::restore_batch_backup``），构造期施加后清空
涉及文档的撤销栈（文档栈不再参与批量语义）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_global_replace_format.py -q
"""

import copy
import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="foo bar", text=None):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    if text is not None:
        blk.text = [text]
    return blk


class FakeProj:
    def __init__(self, pages=None, current_img=None):
        self.pages = pages or {}
        self.current_img = current_img
        self.rerender_marked = set()

    def mark_page_needs_rerender(self, pagename):
        self.rerender_marked.add(pagename)


class FakePair:
    def __init__(self, e_trans):
        self.e_trans = e_trans


class FakeSceneManager:
    def __init__(self, items, pairs):
        self.textblk_item_list = items
        self.pairwidget_list = pairs


class GlobalReplaceApplierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.textitem import TextBlkItem

        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()
        cls.TextBlkItem = TextBlkItem

    def _make_live(self, idx=0, text="foo bar"):
        from ui.textedit_area import TransTextEdit

        blk = _make_blk(translation=text)
        item = self.TextBlkItem(blk=blk, idx=idx)
        self.scene.addItem(item)
        item.setPlainText(text)
        trans = TransTextEdit(idx, None)
        trans.setPlainText(text)
        return blk, item, trans

    def _background(self):
        return {"src": [], "trans": []}

    def test_format_only_applied_to_item_and_data(self):
        from ui.textedit_commands import GlobalReplaceApplier

        blk, item, trans = self._make_live()
        old_size = copy.deepcopy(item.get_fontformat().font_size)
        new_ffmt = copy.deepcopy(item.get_fontformat())
        new_ffmt.font_size = old_size + 4
        old_ffmt = copy.deepcopy(item.get_fontformat())
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sm = FakeSceneManager([item], [FakePair(trans)])

        changes = [
            {"pagename": "cur.png", "block_idx": 0, "old_ffmt": old_ffmt,
             "new_ffmt": new_ffmt}
        ]
        GlobalReplaceApplier(
            {"src": [], "trans": []}, "X", proj,
            scene_manager=sm, format_changes=changes,
        )
        self.assertEqual(item.get_fontformat().font_size, old_size + 4)
        self.assertEqual(blk.fontformat.font_size, old_size + 4)

    def test_combined_text_and_format_applied(self):
        from ui.textedit_commands import GlobalReplaceApplier

        blk, item, trans = self._make_live(text="foo bar")
        old_ffmt = copy.deepcopy(item.get_fontformat())
        new_ffmt = copy.deepcopy(old_ffmt)
        new_ffmt.opacity = 0.5
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sm = FakeSceneManager([item], [FakePair(trans)])

        sceneitem = {
            "src": [],
            "trans": [{"edit": trans, "item": item, "matched_map": [[0, 3]]}],
        }
        changes = [
            {"pagename": "cur.png", "block_idx": 0, "old_ffmt": old_ffmt,
             "new_ffmt": new_ffmt}
        ]
        GlobalReplaceApplier(
            sceneitem, "X", proj,
            scene_manager=sm, format_changes=changes,
        )
        self.assertEqual(item.toPlainText(), "X bar")
        self.assertEqual(trans.toPlainText(), "X bar")
        self.assertEqual(item.get_fontformat().opacity, 0.5)

    def test_apply_clears_document_undo_stacks(self):
        """批量语义不依赖文档栈：施加后涉及文档的撤销栈必须清空。"""
        from ui.textedit_commands import GlobalReplaceApplier

        blk, item, trans = self._make_live(text="foo bar")
        # 制造可撤销的手动编辑步
        trans.textCursor().insertText("!")
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sm = FakeSceneManager([item], [FakePair(trans)])
        sceneitem = {
            "src": [],
            "trans": [{"edit": trans, "item": item, "matched_map": [[0, 3]]}],
        }
        GlobalReplaceApplier(sceneitem, "X", proj, scene_manager=sm)
        self.assertFalse(trans.document().isUndoAvailable())
        self.assertFalse(item.document().isUndoAvailable())

    def test_format_patch_background_page_data_only(self):
        from ui.textedit_commands import GlobalReplaceApplier

        blk = _make_blk(translation="foo")
        old_ffmt = copy.deepcopy(blk.fontformat)
        new_ffmt = copy.deepcopy(old_ffmt)
        new_ffmt.font_size = old_ffmt.font_size + 2
        proj = FakeProj({"cur.png": [], "p2.png": [blk]}, "cur.png")

        changes = [
            {"pagename": "p2.png", "block_idx": 0, "old_ffmt": old_ffmt,
             "new_ffmt": new_ffmt}
        ]
        GlobalReplaceApplier(
            {"src": [], "trans": []}, "X", proj,
            format_changes=changes,
        )
        self.assertEqual(blk.fontformat.font_size, old_ffmt.font_size + 2)
        self.assertIn("p2.png", proj.rerender_marked)

    def test_format_change_missing_page_guarded(self):
        from ui.textedit_commands import GlobalReplaceApplier

        proj = FakeProj({"cur.png": []}, "cur.png")
        changes = [
            {"pagename": "gone.png", "block_idx": 0,
             "old_ffmt": None, "new_ffmt": None}
        ]
        # 已删除页的格式条目必须被静默跳过
        GlobalReplaceApplier(
            {"src": [], "trans": []}, "X", proj,
            format_changes=changes,
        )

    def test_manual_edit_before_replace_stays_consistent(self):
        """替换前面板有手动输入：构造期不得双重替换、edit/item 不得失步。

        对应实机缺陷：构造期 doc_replace 经同步链先改写 item 文档，
        doc_replace_no_shift 再套用一次 span（双重替换）。
        """
        from qtpy.QtGui import QTextCursor

        from ui.textedit_commands import GlobalReplaceApplier, sync_text_by_diff

        blk, item, trans = self._make_live(text="foo bar")
        # 接上真实 app 的 edit→item 同步链（on_propagate_transwidget_edit 同款）
        trans.propagate_user_edited.connect(
            lambda joint: sync_text_by_diff(item, trans.toPlainText(), joint)
        )
        cur = trans.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText("!")
        self.assertEqual(item.toPlainText(), "foo bar!")

        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sm = FakeSceneManager([item], [FakePair(trans)])
        GlobalReplaceApplier(
            {
                "src": [],
                "trans": [{"edit": trans, "item": item, "matched_map": [[0, 3]]}],
            },
            "X", proj, scene_manager=sm,
        )
        self.assertEqual(item.toPlainText(), "X bar!")
        self.assertEqual(trans.toPlainText(), "X bar!")

    def test_format_changes_deepcopied_from_caller(self):
        """施加器须深拷贝 format_changes：调用方/blk 侧的原地改写不得串改目标值。

        对应实机缺陷：FontFormat 与 blk.fontformat 共享对象，场景重建时
        排版回写原地改写字号，记录值随之损坏。
        """
        from ui.textedit_commands import GlobalReplaceApplier

        blk, item, trans = self._make_live()
        old_ffmt = copy.deepcopy(item.get_fontformat())
        new_ffmt = copy.deepcopy(old_ffmt)
        new_ffmt.font_size = old_ffmt.font_size + 4
        orig_size = old_ffmt.font_size
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sm = FakeSceneManager([item], [FakePair(trans)])
        changes = [
            {"pagename": "cur.png", "block_idx": 0,
             "old_ffmt": old_ffmt, "new_ffmt": new_ffmt}
        ]
        GlobalReplaceApplier(
            {"src": [], "trans": []}, "X", proj,
            scene_manager=sm, format_changes=changes,
        )
        # blk 数据层拿到的是独立副本
        self.assertIsNot(blk.fontformat, changes[0]["new_ffmt"])
        self.assertEqual(blk.fontformat.font_size, orig_size + 4)
        # 模拟外部原地改写调用方对象（如排版回写）不串改已写入的值
        new_ffmt.font_size = 0.0
        self.assertEqual(blk.fontformat.font_size, orig_size + 4)

    def test_blk_fontformat_writeback_is_private_copy(self):
        """数据层写回 blk.fontformat 用独立副本，不与调用方共享对象。"""
        from ui.textedit_commands import GlobalReplaceApplier

        blk = _make_blk(translation="foo")
        old_ffmt = copy.deepcopy(blk.fontformat)
        new_ffmt = copy.deepcopy(old_ffmt)
        new_ffmt.font_size = old_ffmt.font_size + 2
        proj = FakeProj({"cur.png": [], "p2.png": [blk]}, "cur.png")
        changes = [
            {"pagename": "p2.png", "block_idx": 0,
             "old_ffmt": old_ffmt, "new_ffmt": new_ffmt}
        ]
        GlobalReplaceApplier({"src": [], "trans": []}, "X", proj,
                             format_changes=changes)
        self.assertIsNot(blk.fontformat, changes[0]["new_ffmt"])
        # 外部改写 blk 数据（如排版回写）不影响施加器持有记录
        blk.fontformat.font_size = 0.0
        self.assertEqual(changes[0]["new_ffmt"].font_size,
                         old_ffmt.font_size + 2)


if __name__ == "__main__":
    unittest.main()
