"""Offscreen regression tests for the 2026-08-23 global-search rework and
font-style manager fixes.

Global search: Replace All used to run in a background thread that collected
live text-edit widget references and touched widgets/models/progress bars
from the worker thread — switching pages mid-run crashed with
``RuntimeError: wrapped C/C++ object of type TransTextEdit has been deleted``
(the replacement was then applied to deleted widgets in
``ui/textedit_commands.py::GlobalRepalceAllCommand``).  Replacement is now
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
        sceneitem, background = self._collect(w)
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
        _, background = self._collect(w)
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
        sceneitem, background = self._collect(w)
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
        _, background = self._collect(w)
        self.assertEqual(blk.translation, "X zzz")
        self.assertEqual(len(background["trans"]), 1)


# ═══════════════════════════════════════════════════════════════════════
# Global replace: undo command on live widgets
# ═══════════════════════════════════════════════════════════════════════


class GlobalReplaceCommandTest(unittest.TestCase):
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

    def test_command_apply_undo_redo_live_edits(self):
        from qtpy.QtGui import QUndoStack

        from ui.textedit_commands import GlobalRepalceAllCommand

        blk, item, src, trans = self._make_pair("foo bar")
        proj = FakeProj({"cur.png": [blk]}, "cur.png")
        sceneitem = {
            "src": [{"edit": src, "replace": "X bar"}],
            "trans": [
                {"edit": trans, "item": item, "matched_map": [[0, 3]]}
            ],
        }
        background = {"src": [], "trans": []}
        cmd = GlobalRepalceAllCommand(sceneitem, background, "X", proj)
        self.assertEqual(trans.toPlainText(), "X bar")
        self.assertEqual(item.toPlainText(), "X bar")
        self.assertEqual(src.toPlainText(), "X bar")

        # Push into a real stack to exercise the production lifecycle.
        stack = QUndoStack()
        stack.push(cmd)  # first redo() runs; edits already applied in __init__
        self.assertEqual(trans.toPlainText(), "X bar")

        stack.undo()
        self.assertEqual(trans.toPlainText(), "foo bar")
        self.assertEqual(item.toPlainText(), "foo bar")
        self.assertEqual(src.toPlainText(), "foo bar")

        stack.redo()
        self.assertEqual(trans.toPlainText(), "X bar")
        self.assertEqual(item.toPlainText(), "X bar")
        self.assertEqual(src.toPlainText(), "X bar")

    def test_background_restore_survives_vanished_page(self):
        from ui.textedit_commands import GlobalRepalceAllCommand

        blk = _make_blk(translation="foo", text="foo")
        gone = _make_blk(translation="foo", text="foo")
        proj = FakeProj({"p2.png": [blk]}, "cur.png")  # p3.png vanished
        background = {
            "src": [
                {"ori": "foo", "replace": "X", "pagename": "p2.png", "idx": 0},
                {"ori": "foo", "replace": "X", "pagename": "p3.png", "idx": 0},
            ],
            "trans": [
                {
                    "ori": "foo",
                    "replace": "X",
                    "ori_html": "",
                    "replace_html": "",
                    "pagename": "p2.png",
                    "idx": 0,
                }
            ],
        }
        cmd = GlobalRepalceAllCommand(
            {"src": [], "trans": []}, background, "X", proj
        )
        cmd.undo()
        self.assertEqual(blk.text, ["foo"])
        self.assertEqual(blk.translation, "foo")
        cmd.redo()
        self.assertEqual(blk.text, ["X"])
        self.assertEqual(blk.translation, "X")
        # Deleted-page entries were skipped without raising.
        self.assertIn("p2.png", proj.rerender_marked)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
