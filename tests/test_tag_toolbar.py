"""选中跟随标签工具栏测试（批次 B）：显隐、打标联动、展开面板、快捷键翻转语义。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_tag_toolbar.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication, QGraphicsScene, QGraphicsView  # noqa: E402
from qtpy.QtWidgets import QHBoxLayout, QWidget  # noqa: E402

from utils.block_tags import (  # noqa: E402
    has_tag,
    toggle_on_blocks,
)
from utils.textblock import TextBlock  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

from ui.textitem import TextBlkItem  # noqa: E402
from ui.tag_toolbar import TagToolbar  # noqa: E402


class FakeCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self._items = []
        self.saved = False
        self.gv = QGraphicsView(self)
        layout = QHBoxLayout(self)
        layout.addWidget(self.gv)

    def textEditMode(self):
        return True

    def selected_text_items(self):
        return self._items

    def setProjSaveState(self, v):
        self.saved = v


def _make_blk():
    return TextBlock(
        translation="t", lines=[[[0, 0], [100, 0], [100, 30], [0, 30]]]
    )


class TestToggleOnBlocks(unittest.TestCase):
    def test_flip_semantics(self):
        a, b = TextBlock(), TextBlock()
        set_res = toggle_on_blocks([a, b], "handwritten")
        self.assertTrue(set_res)
        self.assertTrue(has_tag(a, "handwritten") and has_tag(b, "handwritten"))
        set_res = toggle_on_blocks([a, b], "handwritten")
        self.assertFalse(set_res)
        self.assertFalse(has_tag(a, "handwritten"))

    def test_flip_with_partial(self):
        a, b = TextBlock(), TextBlock()
        from utils.block_tags import set_tag

        set_tag(a, "handwritten", "manual")
        # 非全员带 → 全挂
        self.assertTrue(toggle_on_blocks([a, b], "handwritten"))
        self.assertTrue(has_tag(b, "handwritten"))


class TestTagToolbar(unittest.TestCase):
    def setUp(self):
        # pcfg 是单例：先跑的用例（如 tests/test_drawing_cursor.py）会
        # load_config() 把真实 config.json 载入 pcfg，本文件的显隐断言
        # 就会随用户手上的开关漂移（实测 show_tag_toolbar=False 时三个
        # 用例齐挂）。显式声明本用例依赖的基线，跑完还原。
        import ui.tag_toolbar as tt

        self._saved_flags = (
            tt.pcfg.show_tag_toolbar,
            tt.pcfg.show_tag_badge,
        )
        tt.pcfg.show_tag_toolbar = True
        tt.pcfg.show_tag_badge = True
        self.host = QWidget()
        self.host.resize(900, 700)
        self.canvas = FakeCanvas()
        self.canvas.resize(800, 600)
        self.canvas.show()
        self.toolbar = TagToolbar(self.host, self.canvas)
        self.host.show()
        self.scene = QGraphicsScene()
        self.canvas.gv.setScene(self.scene)
        self.canvas.gv.setSceneRect(0, 0, 800, 600)

    def tearDown(self):
        import ui.tag_toolbar as tt

        (
            tt.pcfg.show_tag_toolbar,
            tt.pcfg.show_tag_badge,
        ) = self._saved_flags
        self.toolbar.hide()
        self.host.hide()
        self.host.deleteLater()

    def _add_item(self):
        item = TextBlkItem(_make_blk(), 0)
        self.scene.addItem(item)
        return item

    def test_show_on_selection(self):
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        self.assertTrue(self.toolbar.isVisible())

    def test_hide_without_selection(self):
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        self.canvas._items = []
        self.toolbar.sync_from_canvas()
        self.assertFalse(self.toolbar.isVisible())

    def test_toggle_updates_badge_and_dirty(self):
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        btn = self.toolbar._compact_buttons["handwritten"]
        btn.setChecked(True)
        self.assertTrue(has_tag(item.blk, "handwritten"))
        self.assertEqual(item._tag_badge_item._tags[0].id, "handwritten")
        self.assertTrue(self.canvas.saved)
        btn.setChecked(False)
        self.assertFalse(has_tag(item.blk, "handwritten"))

    def test_expand_panel(self):
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        self.toolbar.toggle_expanded()
        self.assertTrue(self.toolbar._panel.isVisible())
        # 行数 = 前台任务数（两个人工待办 + 两个持久翻译指示）：
        # 程序问题与旧 ID 都不提供人工打标途径
        from utils.block_tags import MANUAL_TAG_DEFS

        self.assertEqual(len(self.toolbar._panel_rows), len(MANUAL_TAG_DEFS))
        self.assertEqual(len(MANUAL_TAG_DEFS), 4)
        # 收回
        self.toolbar.toggle_expanded()
        self.assertFalse(self.toolbar._panel.isVisible())

    def test_program_only_tag_not_offered(self):
        """D2／D38：程序专用标签（误识别文本）不提供人工打标途径。"""
        from utils.block_tags import MANUAL_TAG_DEFS, MISREAD_TAG_ID

        self.assertNotIn(MISREAD_TAG_ID, self.toolbar._compact_buttons)
        self.assertNotIn(MISREAD_TAG_ID, self.toolbar._panel_rows)
        self.assertEqual(len(self.toolbar._compact_buttons), len(MANUAL_TAG_DEFS))
        # 选中块上带该标签时也不应变出按钮（_update_checks 遍历的是
        # MANUAL_TAG_DEFS，不会因缺键抛 KeyError）
        from utils.block_tags import apply_misread_tag

        item = self._add_item()
        apply_misread_tag(item.blk, ["empty"])
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        self.assertNotIn(MISREAD_TAG_ID, self.toolbar._compact_buttons)

    def test_clamped_in_host(self):
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        geo = self.toolbar.geometry()
        self.assertGreaterEqual(geo.x(), 0)
        self.assertGreaterEqual(geo.y(), 0)
        self.assertLessEqual(geo.right(), self.host.width())
        self.assertLessEqual(geo.bottom(), self.host.height())

    def test_show_toggle_gate(self):
        """show_tag_toolbar 关闭时即使有选中也不出现。"""
        import ui.tag_toolbar as tt

        item = self._add_item()
        self.canvas._items = [item]
        tt.pcfg.show_tag_toolbar = False
        try:
            self.toolbar.sync_from_canvas()
            self.assertFalse(self.toolbar.isVisible())
        finally:
            tt.pcfg.show_tag_toolbar = True
        self.toolbar.sync_from_canvas()
        self.assertTrue(self.toolbar.isVisible())

    def test_action_buttons_open_on_single_selection(self):
        """「处理」钮：单选一块即出现，**不需要预先挂标签**（交接 §4.1）。

        现场看到可疑就走「校对原文／重译」，AI 结果仍只落确认卡；
        标签只决定应用后清掉哪几条问题记录（``BlockActionDef.consumes``）。
        """
        item = self._add_item()
        self.canvas._items = [item]
        self.toolbar.sync_from_canvas()
        # 未挂任何标签也是两块可用的现场入口
        self.assertEqual(item.blk.tags, {})
        for btn in self.toolbar._action_buttons.values():
            self.assertTrue(btn.isVisible())
        seen = []
        self.toolbar.action_requested.connect(seen.append)
        self.toolbar._action_buttons["act_ocr_fix"].click()
        self.assertEqual(seen, ["act_ocr_fix"])
        # 多选 → 不显示（批量动作不在 v1 范围）
        item2 = TextBlkItem(_make_blk(), 1)
        self.scene.addItem(item2)
        self.canvas._items = [item, item2]
        self.toolbar.sync_from_canvas()
        for btn in self.toolbar._action_buttons.values():
            self.assertFalse(btn.isVisible())


class TestBlockActionCard(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.resize(900, 700)
        self.canvas = FakeCanvas()
        self.canvas.resize(800, 600)
        self.canvas.show()
        self.host.show()
        from ui.block_action_card import BlockActionCard

        self.card = BlockActionCard(self.host, self.canvas)
        self.item = TextBlkItem(_make_blk(), 0)

    def test_busy_then_proposal(self):
        applied = []
        self.card.apply_clicked.connect(applied.append)
        self.card.begin("Fix OCR", self.item, "orig")
        self.assertTrue(self.card.isVisible())
        self.assertTrue(self.card.is_busy())
        self.card.show_proposal("draft text")
        self.assertFalse(self.card.is_busy())
        self.card._editor.setPlainText("edited")
        self.card._on_apply()
        self.assertEqual(applied, ["edited"])

    def test_error_state_allows_manual_entry(self):
        """失败也留人工填写路径：对着切图自己敲（手写体靠人最稳）。"""
        applied = []
        self.card.apply_clicked.connect(applied.append)
        self.card.begin("Fix OCR", self.item, "orig")
        self.card.show_error("timeout")
        self.assertTrue(self.card._error_label.isVisibleTo(self.card))
        self.assertTrue(self.card._apply_btn.isVisibleTo(self.card))
        self.card._editor.setPlainText("manual text")
        self.card._on_apply()
        self.assertEqual(applied, ["manual text"])

    def test_cancel_signal(self):
        got = []
        self.card.cancelled.connect(lambda: got.append(1))
        self.card.begin("Fix OCR", self.item, "orig")
        self.card._on_cancel()
        self.assertEqual(got, [1])
        self.assertFalse(self.card.isVisible())

    def test_toggle_form(self):
        """双形态：展开变大面板，收回恢复紧凑卡片。"""
        from ui.block_action_card import CARD_WIDTH, EXPANDED_SIZE

        self.card.begin("Fix OCR", self.item, "orig")
        self.card.toggle_form()
        self.assertEqual(self.card.width(), EXPANDED_SIZE[0])
        self.assertEqual(self.card.height(), EXPANDED_SIZE[1])
        self.card.toggle_form()
        self.assertEqual(self.card.width(), CARD_WIDTH)

    def test_per_line_rows_keep_unchecked_originals(self):
        """逐行接受：勾选=采纳模型行，取消勾选=保留原行。"""
        self.card.begin(
            "Fix OCR",
            self.item,
            "orig",
            line_texts=["もと1", "もと2", "もと3"],
            per_line=True,
        )
        self.card.show_proposal("新1\n新2\n新3", ["新1", "新2", "新3"])
        self.assertTrue(self.card._rows_scroll.isVisibleTo(self.card))
        self.assertFalse(self.card._editor.isVisibleTo(self.card))
        self.card._rows[1][0].setChecked(False)
        self.assertEqual(self.card.result_text(), "新1\nもと2\n新3")
        # 行内也能直接改写
        self.card._rows[0][1].setText("手改")
        self.assertEqual(self.card.result_text(), "手改\nもと2\n新3")

    def test_line_count_mismatch_falls_back_to_blob(self):
        """回复行数对不上 → 不逐行，整块草稿（调用方不传 line_corrections）。"""
        self.card.begin(
            "Fix OCR", self.item, "orig", line_texts=["a", "b"], per_line=True
        )
        self.card.show_proposal("ab")
        self.assertTrue(self.card._editor.isVisibleTo(self.card))
        self.assertFalse(self.card._rows_scroll.isVisibleTo(self.card))
        self.assertEqual(self.card.result_text(), "ab")

    def test_error_state_prefills_rows_from_original(self):
        self.card.begin(
            "Fix OCR", self.item, "orig", line_texts=["a", "b"], per_line=True
        )
        self.card.show_error("timeout")
        self.assertTrue(self.card._rows_scroll.isVisibleTo(self.card))
        self.assertEqual(self.card.result_text(), "a\nb")

    def test_context_pack_shown_only_when_expanded(self):
        self.card.begin(
            "Fix OCR",
            self.item,
            "orig",
            context_items=[("Source sent", "こんにちは")],
        )
        self.card.show_proposal("draft")
        self.assertFalse(self.card._context_label.isVisibleTo(self.card))
        self.card.toggle_form()
        self.assertTrue(self.card._context_label.isVisibleTo(self.card))
        self.assertIn("こんにちは", self.card._context_label.text())

    def test_preview_shown_and_zoom_opens(self):
        import base64

        import cv2
        import numpy as np

        ok, buf = cv2.imencode(
            ".jpg", np.full((40, 120, 3), 255, np.uint8)
        )
        self.assertTrue(ok)
        b64 = base64.b64encode(buf).decode("utf-8")
        self.card.begin("Fix OCR", self.item, "orig", preview_b64=b64)
        self.assertTrue(self.card._preview_label.isVisibleTo(self.card))
        self.card.show_proposal("draft")
        self.card._open_zoom()
        self.assertIsNotNone(self.card._zoom_dialog)
        self.assertTrue(self.card._zoom_dialog.isVisible())
        self.card.close_card()
        self.assertFalse(self.card._zoom_dialog.isVisible())

    def test_preview_garbage_ignored(self):
        self.card.begin("Fix OCR", self.item, "orig", preview_b64="not-base64!!")
        self.assertFalse(self.card._preview_label.isVisibleTo(self.card))

    def test_retry_emits_hint(self):
        got = []
        self.card.retry_requested.connect(got.append)
        self.card.begin("Fix OCR", self.item, "orig")
        self.card.show_proposal("draft")
        self.card._hint_edit.setText("  语气更冲  ")
        self.card._on_retry()
        self.assertEqual(got, ["语气更冲"])


class TestProgramOnlyTagEntries(unittest.TestCase):
    """程序专用标签不进右键菜单，也不进自定义菜单的可选列表（D2／D38）。"""

    def test_not_in_context_menu(self):
        from ui.context_menu_config import COMMAND_REGISTRY, DEFAULT_ORDER
        from utils.block_tags import MISREAD_TAG_ID

        cmd_id = f"tag_{MISREAD_TAG_ID}"
        self.assertNotIn(cmd_id, COMMAND_REGISTRY)
        self.assertNotIn(cmd_id, DEFAULT_ORDER)

    def test_manual_tags_still_registered(self):
        """人工标签的**命令**仍注册，但只作为「打标」子菜单的构建源。

        `ui/context_menu_config.py::DEFAULT_ORDER` 里平铺的是 ``tags`` 一项，
        具体标签由 `ui/context_menu_config.py::_build_tags` 按 ``tag_<id>`` 从
        ``COMMAND_REGISTRY`` 取出来挂进子菜单——所以断言两件事：命令在注册表里、
        顶层顺序里没有它（2026-09-20 修：原断言写的是收纳前的平铺形态）。
        """
        from ui.context_menu_config import COMMAND_REGISTRY, DEFAULT_ORDER
        from utils.block_tags import MANUAL_TAG_DEFS

        for tag in MANUAL_TAG_DEFS:
            self.assertIn(f"tag_{tag.id}", COMMAND_REGISTRY)
            self.assertNotIn(f"tag_{tag.id}", DEFAULT_ORDER)
        self.assertIn("tags", DEFAULT_ORDER)


if __name__ == "__main__":
    unittest.main()
