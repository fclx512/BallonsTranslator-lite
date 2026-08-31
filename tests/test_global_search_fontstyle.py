"""Offscreen regression tests for the 2026-08-23 global-search rework and
font-style manager fixes.

Global search: Replace All used to run in a background thread that collected
live text-edit widget references and touched widgets/models/progress bars
from the worker thread — switching pages mid-run crashed with
``RuntimeError: wrapped C/C++ object of type TransTextEdit has been deleted``
(the replacement was then applied to deleted widgets).  Replacement is now
fully synchronous on the GUI thread and re-matches the pattern against the
live text of every block, so stale search spans can never corrupt edited
text.  This suite also pins the smaller fixes: whole-word searches escape
the query when regex mode is off, an invalid regex resets the search state,
and clicking a stale result is bounds-checked.

Font style manager: ``compute_signature`` quantizes float fields so
OCR-derived continuous sizes no longer fragment styles into one entry per
block; batch apply re-derives its target blocks by signature at apply time
(healing stale indices); undo captures per-block formats and always rides
the text undo stack.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_global_search_fontstyle.py
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

from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字", text=None):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    if text is not None:
        blk.text = [text]
    return blk


class FakeProj:
    """Minimal ProjImgTrans stand-in: pages, current page, rerender flags."""

    def __init__(self, pages=None, current_img=None):
        self.pages = pages or {}
        self.current_img = current_img
        self.rerender_marked = set()
        self.base_styles = []

    def mark_page_needs_rerender(self, pagename):
        self.rerender_marked.add(pagename)


# ═══════════════════════════════════════════════════════════════════════
# Global search: regex building + stale-state reset
# ═══════════════════════════════════════════════════════════════════════


class GlobalSearchRegexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self):
        from ui.global_search_widget import GlobalSearchWidget

        w = GlobalSearchWidget()
        w.imgtrans_proj = FakeProj()
        return w

    def test_whole_word_escapes_non_regex_query(self):
        w = self._widget()
        w.search_editor.setPlainText("3.5")
        w.whole_word_toggle.setChecked(True)
        w.regex_toggle.setChecked(False)
        pattern = w.get_regex_pattern()
        self.assertIsNotNone(pattern)
        self.assertIsNotNone(pattern.search("say 3.5 now"))
        # "." must not act as a regex wildcard: "345" is not a match.
        self.assertIsNone(pattern.search("id 345 ok"))

    def test_whole_word_still_works_for_plain_words(self):
        w = self._widget()
        w.search_editor.setPlainText("foo")
        w.whole_word_toggle.setChecked(True)
        w.regex_toggle.setChecked(False)
        pattern = w.get_regex_pattern()
        self.assertIsNotNone(pattern.search("a foo b"))
        self.assertIsNone(pattern.search("afoob"))

    def test_regex_mode_keeps_raw_query(self):
        w = self._widget()
        w.search_editor.setPlainText(r"\d+")
        w.regex_toggle.setChecked(True)
        pattern = w.get_regex_pattern()
        self.assertIsNotNone(pattern.search("abc 123"))

    def test_invalid_regex_resets_state(self):
        w = self._widget()
        w.imgtrans_proj.pages = {"p.png": [_make_blk(translation="foo")]}
        w.search_editor.setPlainText("foo")
        w.commit_search()
        self.assertGreater(w.counter_sum, 0)
        self.assertIsNotNone(w.searched_pattern)

        # Now type a broken regex and re-search: state must reset so a
        # subsequent Replace All can never run on a stale pattern.
        w.search_editor.setPlainText("[")
        w.regex_toggle.setChecked(True)
        w.commit_search()
        self.assertEqual(w.counter_sum, 0)
        self.assertIsNone(w.searched_pattern)
        self.assertEqual(w.result_label.text(), w.invalid_regex_str)
        w.deleteLater()

    def test_empty_query_resets_state(self):
        w = self._widget()
        w.imgtrans_proj.pages = {"p.png": [_make_blk(translation="foo")]}
        w.search_editor.setPlainText("foo")
        w.commit_search()
        w.search_editor.setPlainText("")
        w.commit_search()
        self.assertEqual(w.counter_sum, 0)
        self.assertIsNone(w.searched_pattern)
        w.deleteLater()

    def test_set_document_edited_invalidates_pattern(self):
        w = self._widget()
        w.imgtrans_proj.pages = {"p.png": [_make_blk(translation="foo")]}
        w.search_editor.setPlainText("foo")
        w.commit_search()
        w.set_document_edited()
        self.assertEqual(w.counter_sum, 0)
        self.assertIsNone(w.searched_pattern)
        w.deleteLater()


# ═══════════════════════════════════════════════════════════════════════
# Global replace: synchronous collector
# ═══════════════════════════════════════════════════════════════════════


class GlobalReplaceCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self, pages, current_img):
        from ui.global_search_widget import GlobalSearchWidget

        w = GlobalSearchWidget()
        w.imgtrans_proj = FakeProj(pages, current_img)
        w.pairwidget_list = []
        w.textblk_item_list = []
        w.range_combobox.setCurrentIndex(2)  # All: search src + translation
        w.search_editor.setPlainText("foo")
        w.commit_search()
        return w

    def _collect(self, w, target="X"):
        return w._collect_replace_targets(target)

    def test_off_page_plain_text_replaced(self):
        blk = _make_blk(translation="foo啊foo", text="foo bar")
        w = self._widget(
            {"cur.png": [], "p2.png": [blk]}, current_img="cur.png"
        )
        sceneitem, background, _ = self._collect(w)
        self.assertEqual(blk.text, ["X bar"])
        self.assertEqual(blk.translation, "X啊X")
        self.assertEqual(len(background["src"]), 1)
        self.assertEqual(len(background["trans"]), 1)
        self.assertEqual(background["src"][0]["ori"], "foo bar")
        self.assertEqual(background["trans"][0]["ori"], "foo啊foo")
        self.assertIn("p2.png", w.imgtrans_proj.rerender_marked)
        self.assertEqual(sceneitem["src"], [])
        self.assertEqual(sceneitem["trans"], [])

    def test_off_page_rich_text_replaced(self):
        blk = _make_blk(translation="aa foo bb")
        blk.rich_text = "<p>aa foo bb</p>"
        w = self._widget(
            {"cur.png": [], "p2.png": [blk]}, current_img="cur.png"
        )
        _, background, _ = self._collect(w)
        self.assertEqual(blk.translation, "aa X bb")
        self.assertIn("X", blk.rich_text)
        self.assertNotIn("foo", blk.rich_text)
        self.assertEqual(background["trans"][0]["ori"], "aa foo bb")
        self.assertEqual(background["trans"][0]["replace"], "aa X bb")

    def test_no_match_leaves_block_untouched(self):
        blk = _make_blk(translation="bar", text="bar")
        w = self._widget(
            {"cur.png": [], "p2.png": [blk]}, current_img="cur.png"
        )
        sceneitem, background, _ = self._collect(w)
        self.assertEqual(blk.text, ["bar"])
        self.assertEqual(blk.translation, "bar")
        self.assertEqual(background["src"], [])
        self.assertEqual(background["trans"], [])
        self.assertNotIn("p2.png", w.imgtrans_proj.rerender_marked)

    def test_stale_search_spans_never_apply(self):
        # Blocks whose text changed after the search must be re-matched, not
        # replaced at the stale captured positions.
        blk = _make_blk(translation="zzz foo", text="zzz foo")
        w = self._widget(
            {"cur.png": [], "p2.png": [blk]}, current_img="cur.png"
        )
        blk.translation = "foo zzz"  # simulate an edit after the search
        _, background, _ = self._collect(w)
        self.assertEqual(blk.translation, "X zzz")
        self.assertEqual(len(background["trans"]), 1)


# ═══════════════════════════════════════════════════════════════════════
# Global replace: applier on live widgets
# ═══════════════════════════════════════════════════════════════════════


class GlobalReplaceApplierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()
        cls.TextBlkItem = TextBlkItem

    def _make_pair(self, text="foo bar"):
        from ui.textedit_area import SourceTextEdit, TransTextEdit

        blk = _make_blk(translation=text, text=text)
        item = self.TextBlkItem(blk=blk, idx=0)
        self.scene.addItem(item)
        item.setPlainText(text)
        src = SourceTextEdit(0, None)
        src.setPlainText(text)
        trans = TransTextEdit(0, None)
        trans.setPlainText(text)
        return blk, item, src, trans

    def test_applier_applies_live_edits(self):
        from ui.textedit_commands import GlobalReplaceApplier

        blk, item, src, trans = self._make_pair("foo bar")
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sceneitem = {
            "src": [{"edit": src, "replace": "X bar"}],
            "trans": [
                {"edit": trans, "item": item, "matched_map": [[0, 3]]}
            ],
        }
        GlobalReplaceApplier(sceneitem, "X", proj)
        self.assertEqual(trans.toPlainText(), "X bar")
        self.assertEqual(item.toPlainText(), "X bar")
        self.assertEqual(src.toPlainText(), "X bar")
        # 批量替换不依赖文档撤销栈（回滚走项目快照）
        self.assertFalse(trans.document().isUndoAvailable())
        self.assertFalse(item.document().isUndoAvailable())
        self.assertFalse(src.document().isUndoAvailable())


# ═══════════════════════════════════════════════════════════════════════
# Font style manager: signature quantization + live re-match
# ═══════════════════════════════════════════════════════════════════════


class FontStyleSignatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_nearby_float_sizes_share_signature(self):
        from ui.fontstyle_manager import compute_signature

        b1 = _make_blk(translation="a")
        b2 = _make_blk(translation="b")
        b1.font_size, b2.font_size = 24.1, 24.2
        self.assertEqual(
            compute_signature(b1.fontformat), compute_signature(b2.fontformat)
        )
        b2.font_size = 24.7
        self.assertNotEqual(
            compute_signature(b1.fontformat), compute_signature(b2.fontformat)
        )

    def test_discover_styles_groups_fragments(self):
        from ui.fontstyle_manager import discover_styles

        pages = {
            "p.png": [
                _make_blk(translation=str(i)) for i in range(6)
            ]
        }
        for i, blk in enumerate(pages["p.png"]):
            blk.font_size = 24.0 + 0.05 * i  # all quantize to 24.0
        entries = discover_styles(FakeProj(pages, "p.png"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].count, 6)


class StyleLiveMatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _detail(self, pages, current_img="cur.png"):
        from ui.fontstyle_manager import StyleDetail, discover_styles

        proj = FakeProj(pages, current_img)
        detail = StyleDetail()
        detail.set_project(proj, None)
        entries = discover_styles(proj)
        detail._entry = entries[0]
        return detail, proj

    def test_collect_live_blocks_heals_stale_indices(self):
        pages = {
            "cur.png": [],
            "p2.png": [
                _make_blk(translation="a"),
                _make_blk(translation="b"),
                _make_blk(translation="c"),
            ],
        }
        for blk in pages["p2.png"]:
            blk.font_size = 24.0
        pages["p2.png"][1].font_size = 40.0  # different style
        detail, proj = self._detail(pages)

        # Simulate the user deleting a block while the dialog is open.
        del pages["p2.png"][0]  # remaining: [b(40.0) at 0, c(24.0) at 1]
        live = detail._collect_live_blocks()
        pnames = [(p, i) for p, i, _ in live]
        # Only the 24.0-style block matches — found by signature at its NEW
        # index, not by the stale captured index.
        self.assertEqual(pnames, [("p2.png", 1)])
        self.assertTrue(all(blk.font_size != 40.0 for _, _, blk in live))

    def test_changes_capture_per_block_old_format(self):
        pages = {
            "cur.png": [],
            "p2.png": [_make_blk(translation="a"), _make_blk(translation="b")],
        }
        pages["p2.png"][0].font_size = 24.0
        pages["p2.png"][1].font_size = 24.0
        detail, proj = self._detail(pages)
        new_ffmt = pages["p2.png"][0].fontformat.deepcopy()
        new_ffmt.font_size = 32.0
        changes = detail._changes_for_targets(new_ffmt)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["old_ffmt"].font_size, 24.0)
        self.assertEqual(changes[0]["new_ffmt"].font_size, 32.0)


class BatchFontformatCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_off_page_undo_redo_restores_formats(self):
        from qtpy.QtGui import QUndoStack

        from ui.fontstyle_manager_commands import BatchFontformatCommand

        blk = _make_blk(translation="a")
        blk.font_size = 24.0
        old_ffmt = blk.fontformat.deepcopy()
        new_ffmt = blk.fontformat.deepcopy()
        new_ffmt.font_size = 32.0
        proj = FakeProj({"p2.png": [blk]}, "cur.png")
        changes = [
            {
                "pagename": "p2.png",
                "block_idx": 0,
                "old_ffmt": old_ffmt,
                "new_ffmt": new_ffmt,
            }
        ]
        cmd = BatchFontformatCommand(proj, None, changes, "test")

        # Real lifecycle: push consumes _first_redo, apply happens outside.
        stack = QUndoStack()
        stack.push(cmd)
        blk.fontformat = new_ffmt.deepcopy()  # simulate _apply_changes_to_blocks

        stack.undo()
        self.assertEqual(blk.fontformat.font_size, 24.0)
        self.assertIn("p2.png", proj.rerender_marked)

        stack.redo()
        self.assertEqual(blk.fontformat.font_size, 32.0)

    def test_vanished_block_does_not_crash(self):
        from ui.fontstyle_manager_commands import BatchFontformatCommand

        blk = _make_blk(translation="a")
        old_ffmt = blk.fontformat.deepcopy()
        new_ffmt = blk.fontformat.deepcopy()
        new_ffmt.font_size = 32.0
        proj = FakeProj({}, "cur.png")  # page deleted since collect
        changes = [
            {
                "pagename": "gone.png",
                "block_idx": 0,
                "old_ffmt": old_ffmt,
                "new_ffmt": new_ffmt,
            }
        ]
        cmd = BatchFontformatCommand(proj, None, changes, "test")
        cmd.undo()  # must not raise
        cmd.redo()


# ═══════════════════════════════════════════════════════════════════════
# 阶段 4：格式条件搜索 / 格式替换（文本 × 格式四象限）
# ═══════════════════════════════════════════════════════════════════════


class FormatConditionSearchTest(unittest.TestCase):
    """格式条件接入 GlobalSearchWidget：搜索 AND 语义 + format-only 命中。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self, pages, current_img=None):
        from ui.global_search_widget import GlobalSearchWidget

        w = GlobalSearchWidget()
        w.imgtrans_proj = FakeProj(pages, current_img)
        return w

    def _enable_find_format(self, w, fname="font_size", value=32.0):
        w.find_format_btn.setChecked(True)
        w._find_format_panel.set_field_value(fname, value)
        w._update_format_btn_texts()

    def test_format_only_search_finds_blocks(self):
        big = _make_blk(translation="big")
        big.fontformat.font_size = 32.0
        small = _make_blk(translation="small")
        w = self._widget({"p.png": [big, small]})
        self._enable_find_format(w)
        w.commit_search()
        self.assertEqual(w.counter_sum, 1)
        self.assertIsNone(w.searched_pattern)
        w.deleteLater()

    def test_format_condition_gates_text_search(self):
        # AND 语义：文本都命中，但只有大字号块通过格式过滤
        big = _make_blk(translation="foo big", text="foo")
        big.fontformat.font_size = 32.0
        small = _make_blk(translation="foo small", text="foo")
        w = self._widget({"p.png": [big, small]})
        w.range_combobox.setCurrentIndex(2)
        w.search_editor.setPlainText("foo")
        self._enable_find_format(w)
        w.commit_search()
        self.assertEqual(w.counter_sum, 2)  # big 的 src+trans 各一条
        w.deleteLater()

    def test_empty_query_with_no_format_conditions_resets(self):
        w = self._widget({"p.png": [_make_blk()]})
        w.commit_search()
        self.assertEqual(w.counter_sum, 0)
        w.deleteLater()

    def test_format_only_replace_patches_off_page_blocks(self):
        from ui.textedit_commands import GlobalReplaceApplier

        blk = _make_blk(translation="t", text="s")
        w = self._widget({"cur.png": [], "p2.png": [blk]}, current_img="cur.png")
        w.replace_format_btn.setChecked(True)
        w._replace_format_panel.set_field_value("font_size", 32.0)
        sceneitem, background, fmt_changes = w._collect_replace_targets("X")
        self.assertEqual(sceneitem["src"], [])
        self.assertEqual(background["src"], [])
        self.assertEqual(len(fmt_changes), 1)
        self.assertEqual(fmt_changes[0]["block_idx"], 0)
        self.assertEqual(fmt_changes[0]["new_ffmt"].font_size, 32.0)
        # 收集期不写数据：由施加器统一落点
        self.assertEqual(blk.fontformat.font_size, 24)
        GlobalReplaceApplier(
            sceneitem, "", w.imgtrans_proj, format_changes=fmt_changes
        )
        self.assertEqual(blk.fontformat.font_size, 32.0)
        self.assertIn("p2.png", w.imgtrans_proj.rerender_marked)
        w.deleteLater()

    def test_apply_base_style_mode_replaces_whole_ffmt(self):
        from utils.base_styles import BaseStyle

        blk = _make_blk(translation="t")
        style_ffmt = blk.fontformat.deepcopy()
        style_ffmt.font_size = 40.0
        style_ffmt.italic = True
        w = self._widget({"cur.png": [], "p2.png": [blk]}, current_img="cur.png")
        w.imgtrans_proj.base_styles = [BaseStyle("Big", style_ffmt)]
        w.replace_format_btn.setChecked(True)
        w._refresh_style_combo()
        w.replace_mode_combo.setCurrentIndex(1)
        sceneitem, _, fmt_changes = w._collect_replace_targets("")
        self.assertEqual(len(fmt_changes), 1)
        new_ffmt = fmt_changes[0]["new_ffmt"]
        self.assertEqual(new_ffmt.font_size, 40.0)
        self.assertTrue(new_ffmt.italic)
        w.deleteLater()

    def test_format_gate_skips_nonmatching_text_targets(self):
        # 文本命中但格式不中的块：既不替换文本也不进 format_changes
        blk = _make_blk(translation="foo", text="foo")
        w = self._widget({"cur.png": [], "p2.png": [blk]}, current_img="cur.png")
        w.search_editor.setPlainText("foo")
        w.commit_search()
        self.assertGreater(w.counter_sum, 0)
        self._enable_find_format(w)
        sceneitem, background, fmt_changes = w._collect_replace_targets("X")
        self.assertEqual(sceneitem["src"], [])
        self.assertEqual(background["src"], [])
        self.assertEqual(background["trans"], [])
        self.assertEqual(fmt_changes, [])
        self.assertEqual(blk.translation, "foo")
        w.deleteLater()

    def test_current_page_format_gate_uses_data_block(self):
        class _FakeEdit:
            def __init__(self, text):
                self._text = text

            def toPlainText(self):
                return self._text

        class _FakePW:
            def __init__(self, src, trans):
                self.e_source = src
                self.e_trans = trans

        class _FakeItem:
            def __init__(self, idx):
                self.idx = idx

        big = _make_blk(translation="foo big", text="foo")
        big.fontformat.font_size = 32.0
        small = _make_blk(translation="foo small", text="foo")
        w = self._widget({"cur.png": [big, small]}, current_img="cur.png")
        w.pairwidget_list = [
            _FakePW(_FakeEdit("foo"), _FakeEdit("foo big")),
            _FakePW(_FakeEdit("foo"), _FakeEdit("foo small")),
        ]
        w.textblk_item_list = [_FakeItem(0), _FakeItem(1)]
        w.range_combobox.setCurrentIndex(2)  # All
        w.search_editor.setPlainText("foo")
        self._enable_find_format(w)
        w.commit_search()
        sceneitem, background, fmt_changes = w._collect_replace_targets("X")
        # 只有 big（idx 0）通过格式门；src 暂存须带 idx 供回滚条计数
        self.assertEqual(len(sceneitem["src"]), 1)
        self.assertEqual(sceneitem["src"][0]["idx"], 0)
        self.assertEqual(background["src"], [])
        self.assertEqual(fmt_changes, [])
        w.deleteLater()


class FormatPanelFontPopulationTest(unittest.TestCase):
    """字体下拉不得因字体库未枚举而只剩补插的当前字体一项。

    GlobalSearchWidget 启动即 ``set_format(FontFormat())``，早于旧时序中
    MainWindow 的 ``init_font_list()``——``ALL_FONT_FAMILIES`` 为空时
    ``get_filtered_font_list`` 返回空，下拉经"当前字体补插"兜底后只剩
    默认字体（Microsoft YaHei UI）一项，用户无法选其他字体。修复：
    ``FormatEditorPanel.set_format`` 在列表为空时惰性触发字体枚举。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_set_format_lazily_enumerates_fonts(self):
        from ui.style_format_editor import FormatEditorPanel

        from utils import shared
        from utils.fontformat import FontFormat

        orig_families, orig_styles = shared.ALL_FONT_FAMILIES, shared.FONT_STYLES
        orig_init = shared.init_font_list
        calls = []
        shared.ALL_FONT_FAMILIES = []
        shared.FONT_STYLES = {}
        shared.init_font_list = lambda: calls.append(True)
        try:
            panel = FormatEditorPanel()
            panel.set_format(FontFormat())
            # 字体库为空时必须惰性补枚举，而不是静默只填补插项
            self.assertTrue(calls)
            combo = panel._editors["font_family"]._control
            self.assertGreaterEqual(combo.count(), 1)
            panel.deleteLater()
        finally:
            shared.ALL_FONT_FAMILIES = orig_families
            shared.FONT_STYLES = orig_styles
            shared.init_font_list = orig_init

    def test_current_family_always_selectable(self):
        """补插兜底保持有效：当前字体不在过滤列表时下拉仍含它并可选中。"""
        from ui.style_format_editor import FormatEditorPanel

        from utils import shared
        from utils.config import pcfg
        from utils.fontformat import FontFormat

        # 本机 config.json 可能排除了大量字体（实测 312 个），测试字体名
        # 必须临时解除排除才有意义
        orig_families = shared.ALL_FONT_FAMILIES
        orig_excluded = pcfg.excluded_fonts
        shared.ALL_FONT_FAMILIES = ["Arial", "Segoe UI"]
        pcfg.excluded_fonts = []
        try:
            panel = FormatEditorPanel()
            ffmt = FontFormat()
            ffmt.font_family = "Missing Font"
            panel.set_format(ffmt)
            combo = panel._editors["font_family"]._control
            self.assertEqual(combo.currentText(), "Missing Font")
            self.assertIn("Arial", [combo.itemText(i) for i in range(combo.count())])
            panel.deleteLater()
        finally:
            shared.ALL_FONT_FAMILIES = orig_families
            pcfg.excluded_fonts = orig_excluded


if __name__ == "__main__":
    unittest.main(verbosity=2)
