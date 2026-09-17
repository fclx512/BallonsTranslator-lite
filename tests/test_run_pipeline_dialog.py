"""Offscreen tests for the run dialog (stage grid + collapsible options).

The dialog is where the pipeline's run-time options live now: keep-existing
lines (detect), skip-simple-cases (inpaint), source/target language and the
single-block strategy (translate).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_run_pipeline_dialog.py
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

from utils.config import SingleBlkTranslateMode, pcfg  # noqa: E402

PAGE_NAMES = ["%03d.jpg" % i for i in range(1, 11)]


class RunPipelineDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.run_pipeline_dialog import (
            STAGE_DETECT,
            STAGE_INPAINT,
            STAGE_OCR,
            STAGE_TRANSLATE,
            RunPipelineDialog,
        )

        cls.app = QApplication.instance() or QApplication([])
        cls.Dialog = RunPipelineDialog
        cls.STAGE_DETECT = STAGE_DETECT
        cls.STAGE_OCR = STAGE_OCR
        cls.STAGE_INPAINT = STAGE_INPAINT
        cls.STAGE_TRANSLATE = STAGE_TRANSLATE

    def setUp(self):
        # Snapshot every pcfg field the dialog writes, then restore: importing
        # utils.config loads the real config.json (see the test-pollution notes).
        cfg = pcfg.module
        keys = (
            "keep_exist_textlines",
            "check_need_inpaint",
            "single_blk_translate_mode",
            "translate_source",
            "translate_target",
            "enable_detect",
            "enable_ocr",
            "enable_translate",
            "enable_inpaint",
            "llm_translate_context",
            "llm_story_context",
            "llm_prior_context_token_budget",
            "llm_glossary_path",
            "llm_glossary_mode",
        )
        snapshot = {k: getattr(cfg, k) for k in keys}
        self.addCleanup(
            lambda: [setattr(cfg, k, v) for k, v in snapshot.items()]
        )
        # 区间记忆是类级状态：不复原就会被上一个用例的区间污染
        self.Dialog._page_range = (1, None)
        self.dialog = self.Dialog(None, page_names=PAGE_NAMES)

    # ── stage grid ───────────────────────────────────────────────────

    def test_stage_grid_has_every_stage(self):
        self.assertEqual(
            sorted(self.dialog._stage_activators),
            sorted([self.STAGE_DETECT, self.STAGE_OCR, self.STAGE_INPAINT,
                    self.STAGE_TRANSLATE]),
        )
        types = {
            stage: act.module_type
            for stage, act in self.dialog._stage_activators.items()
        }
        self.assertEqual(types[self.STAGE_DETECT], "textdetector")
        self.assertEqual(types[self.STAGE_OCR], "ocr")
        self.assertEqual(types[self.STAGE_INPAINT], "inpainter")
        self.assertEqual(types[self.STAGE_TRANSLATE], "translator")

    def test_stage_toggle_emits(self):
        seen = []
        self.dialog.stage_toggled.connect(lambda i, c: seen.append((i, c)))
        act = self.dialog._stage_activators[self.STAGE_OCR]
        act.button.setChecked(not act.button.isChecked())
        self.assertEqual(seen, [(self.STAGE_OCR, act.button.isChecked())])

    def test_module_pick_emits_with_stage_type(self):
        seen = []
        self.dialog.module_selected.connect(lambda t, n: seen.append((t, n)))
        act = self.dialog._stage_activators[self.STAGE_TRANSLATE]
        act.selector.addItem("dummy_translator")
        act.selector.setCurrentText("dummy_translator")
        self.assertIn(("translator", "dummy_translator"), seen)

    def test_section_visibility_follows_stage(self):
        act = self.dialog._stage_activators[self.STAGE_INPAINT]
        act.button.setChecked(False)
        self.assertFalse(self.dialog._stage_sections[self.STAGE_INPAINT].isVisibleTo(self.dialog))
        act.button.setChecked(True)
        self.assertTrue(self.dialog._stage_sections[self.STAGE_INPAINT].isVisibleTo(self.dialog))

    def test_section_expansion_is_remembered(self):
        self.dialog._set_section_expanded(self.STAGE_OCR, True)
        self.assertTrue(self.dialog._stage_bodies[self.STAGE_OCR].isVisibleTo(self.dialog))
        self.assertIs(
            type(self.dialog)._sections_expanded[self.STAGE_OCR], True
        )
        reopened = self.Dialog(None, page_names=PAGE_NAMES)
        self.assertTrue(
            reopened._stage_headers[self.STAGE_OCR].isChecked()
        )

    # ── stage options ────────────────────────────────────────────────

    def test_keep_existing_lines_writes_config(self):
        self.dialog.keep_lines_cb.setChecked(True)
        self.assertTrue(pcfg.module.keep_exist_textlines)
        self.dialog.keep_lines_cb.setChecked(False)
        self.assertFalse(pcfg.module.keep_exist_textlines)

    def test_skip_simple_cases_writes_config_and_class_attr(self):
        from modules.inpaint.base import InpainterBase

        original = InpainterBase.check_need_inpaint
        self.addCleanup(setattr, InpainterBase, "check_need_inpaint", original)

        self.dialog.skip_simple_cb.setChecked(False)
        self.assertFalse(pcfg.module.check_need_inpaint)
        self.assertFalse(InpainterBase.check_need_inpaint)
        self.dialog.skip_simple_cb.setChecked(True)
        self.assertTrue(InpainterBase.check_need_inpaint)

    def test_single_block_mode_writes_config(self):
        combo = self.dialog.single_blk_combo
        combo.setCurrentIndex(combo.findData(SingleBlkTranslateMode.Context))
        self.assertEqual(
            pcfg.module.single_blk_translate_mode, SingleBlkTranslateMode.Context
        )

    def test_language_change_emits(self):
        seen = []
        self.dialog.translate_source_changed.connect(seen.append)
        self.dialog.set_translator_metadata("ja", "zh", ["ja", "en"], ["zh", "en"])
        self.assertEqual(seen, [])  # quiet while mirroring
        combo = self.dialog.source_combobox
        combo.setCurrentText("en")
        self.assertEqual(seen, ["en"])

    def test_translate_labels_stay_inside_their_rows(self):
        """Regression: a stray "Source" label used to land on the page itself.

        ``_build_translate_options`` added it to the page layout instead of the
        combo's row, so it rendered as an orphan line under the whole section.
        """
        from qtpy.QtWidgets import QLabel

        row = self.dialog.source_combobox.parentWidget()
        self.assertIn(
            "Source", [label.text() for label in row.findChildren(QLabel)]
        )

        page = self.dialog.stack.widget(0)
        page_layout = page.layout()
        strays = [
            page_layout.itemAt(i).widget()
            for i in range(page_layout.count())
            if isinstance(page_layout.itemAt(i).widget(), QLabel)
        ]
        self.assertEqual(strays, [])

    # ── page range / modes ───────────────────────────────────────────

    def test_page_filter_defaults_to_all_pages(self):
        self.assertEqual(self.dialog.page_range.range_values(), (1, len(PAGE_NAMES)))
        self.assertIsNone(self.dialog.page_filter())

    def test_page_filter_range(self):
        self.dialog.page_range.set_range(2, 4)
        self.assertEqual(self.dialog.page_filter(), PAGE_NAMES[1:4])

    def test_page_filter_is_none_again_on_full_range(self):
        self.dialog.page_range.set_range(2, 4)
        self.dialog.page_range.set_range(1, len(PAGE_NAMES))
        self.assertIsNone(self.dialog.page_filter())

    def test_range_changes_emit_and_stick(self):
        seen = []
        self.dialog.page_range.range_changed.connect(
            lambda lo, hi: seen.append((lo, hi))
        )
        self.dialog.page_range.range_start.setValue(3)
        self.assertIn((3, len(PAGE_NAMES)), seen)
        self.assertEqual(type(self.dialog)._page_range, (3, len(PAGE_NAMES)))
        reopened = self.Dialog(None, page_names=PAGE_NAMES)
        self.assertEqual(reopened.page_range.range_values(), (3, len(PAGE_NAMES)))

    def test_finished_pages_feed_the_progress_track(self):
        finished = [True, False] * 5
        dialog = self.Dialog(
            None, page_names=PAGE_NAMES, finished_pages=finished
        )
        bar = dialog.page_range.range_bar
        self.assertEqual(bar.finished_pages, finished)
        self.assertEqual(bar.finished_count, 5)

    def test_render_only_tab(self):
        self.assertFalse(self.dialog.is_render_only())
        self.dialog.tab_bar.setCurrentIndex(1)
        self.assertTrue(self.dialog.is_render_only())

    def test_run_without_textstyle_checkbox(self):
        self.assertFalse(self.dialog.run_without_textstyle_update())
        self.dialog.wo_update_cb.setChecked(True)
        self.assertTrue(self.dialog.run_without_textstyle_update())


if __name__ == "__main__":
    unittest.main()
