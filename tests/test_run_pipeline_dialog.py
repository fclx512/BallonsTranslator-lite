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
        self.assertEqual(strays, [self.dialog.task_hint])

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

    def test_task_modes_limit_stages_and_filter_progress(self):
        from utils.config import RunStatus
        dialog = self.Dialog(None, page_names=PAGE_NAMES,
                             progress_codes=[RunStatus.FIN_TRANSLATE] * len(PAGE_NAMES))
        for mode, expected in (("translate", {self.STAGE_TRANSLATE}),
                               ("inpaint", {self.STAGE_INPAINT}),
                               ("recognize", {self.STAGE_DETECT, self.STAGE_OCR})):
            dialog.task_combo.setCurrentIndex(dialog.task_combo.findData(mode))
            self.assertEqual(dialog.task_mode(), mode)
            for stage, activator in dialog._stage_activators.items():
                self.assertEqual(activator.isVisibleTo(dialog), stage in expected)
                self.assertEqual(activator.button.isChecked(), stage in expected)
            self.assertEqual(dialog.overwrite_cb.isVisibleTo(dialog), mode == "translate")
            self.assertEqual(dialog.page_range.range_bar.finished_count,
                             len(PAGE_NAMES) if mode == "translate" else 0)
        dialog._stage_activators[self.STAGE_DETECT].button.setChecked(False)
        self.assertTrue(dialog.run_btn.isEnabled())
        dialog._stage_activators[self.STAGE_OCR].button.setChecked(False)
        self.assertFalse(dialog.run_btn.isEnabled())
        dialog.tab_bar.setCurrentIndex(1)
        self.assertTrue(dialog.run_btn.isEnabled())

    def test_workbench_refreshes_raw_stage_codes(self):
        from types import SimpleNamespace
        from ui.run_pipeline_dialog import PipelineWorkbench
        from utils.config import RunStatus
        project = SimpleNamespace(directory="project", current_img="a.png",
                                  pages={"a.png": [], "b.png": []},
                                  _image_info={"a.png": {"finish_code": RunStatus.FIN_ALL}})
        panel = PipelineWorkbench(project)
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel._progress_codes, [RunStatus.FIN_ALL, 0])
        project._image_info["b.png"] = {"finish_code": RunStatus.FIN_ALL}
        panel.refresh_project_state()
        self.assertEqual(panel.page_range.range_bar.finished_count, 2)

    def test_workbench_scope_and_project_switch_do_not_reuse_old_pages(self):
        from types import SimpleNamespace
        from ui.run_pipeline_dialog import PipelineWorkbench
        project = SimpleNamespace(directory="first", current_img="002.jpg",
                                  pages=dict.fromkeys(PAGE_NAMES, []), _image_info={})
        panel = PipelineWorkbench(project)
        self.addCleanup(panel.deleteLater)
        panel.set_scope("current")
        self.assertEqual(panel.page_filter(), ["002.jpg"])
        project.current_img = "003.jpg"
        self.assertEqual(panel.page_filter(), ["003.jpg"])
        panel.set_scope("range")
        panel.page_range.set_range(2, 4)
        panel.refresh_project_state()
        self.assertEqual(panel.page_filter(), PAGE_NAMES[1:4])
        project.directory, project.pages, project.current_img = "second", {"new.jpg": []}, "new.jpg"
        panel.refresh_project_state()
        self.assertIsNone(panel.page_filter())
        self.assertEqual(panel._page_names, ["new.jpg"])

    def test_workbench_run_stays_embedded_and_render_task_remains_available(self):
        from types import SimpleNamespace
        from qtpy.QtWidgets import QWidget
        from ui.run_pipeline_dialog import PipelineWorkbench
        parent = QWidget()
        project = SimpleNamespace(directory="project", current_img="a.jpg",
                                  pages={"a.jpg": []}, _image_info={})
        panel = PipelineWorkbench(project, parent)
        self.addCleanup(parent.deleteLater)
        seen = []
        panel.run_requested.connect(lambda: seen.append(panel.task_mode()))
        panel.task_combo.setCurrentIndex(panel.task_combo.findData("render"))
        panel.run_btn.click()
        self.assertEqual(seen, ["render"])
        self.assertFalse(panel.isWindow())
        self.assertFalse(panel.isModal())
        self.assertTrue(panel.is_render_only())

    def test_mainwindow_run_opens_the_existing_workbench(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.mainwindow import MainWindow
        window = SimpleNamespace(refresh_module_selector_status=Mock(),
                                 leftBar=SimpleNamespace(workbenchChecker=Mock()),
                                 on_set_workbench_widget=Mock())
        MainWindow.run_imgtrans(window)
        window.leftBar.workbenchChecker.setChecked.assert_called_once_with(True)
        window.on_set_workbench_widget.assert_called_once_with()

    def test_workbench_engine_selector_fits_inside_its_stage_row(self):
        from qtpy.QtWidgets import QWidget, QVBoxLayout
        from ui.run_pipeline_dialog import WorkbenchStage
        host = QWidget()
        host.resize(340, 100)
        row = WorkbenchStage("textdetector", "Detect text")
        QVBoxLayout(host).addWidget(row)
        self.addCleanup(host.deleteLater)
        host.show()
        self.app.processEvents()
        self.assertLess(row.button.geometry().right(), row.selector.geometry().left())
        self.assertLessEqual(row.selector.geometry().right(), row.width())

    def test_render_only_tab(self):
        self.assertFalse(self.dialog.is_render_only())
        self.dialog.tab_bar.setCurrentIndex(1)
        self.assertTrue(self.dialog.is_render_only())

    def test_run_without_textstyle_checkbox(self):
        self.assertFalse(self.dialog.run_without_textstyle_update())
        self.dialog.wo_update_cb.setChecked(True)
        self.assertTrue(self.dialog.run_without_textstyle_update())

    def test_render_option_follows_saved_preference_without_writing_on_cancel(self):
        from unittest.mock import patch
        for enabled in (True, False):
            with patch.object(pcfg.module, "render_after_pipeline", enabled):
                dialog = self.Dialog(page_names=PAGE_NAMES)
                self.assertEqual(dialog.render_after_cb.isChecked(), enabled)
                dialog.render_after_cb.setChecked(not enabled)
                dialog.reject()
                self.assertEqual(pcfg.module.render_after_pipeline, enabled)
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
