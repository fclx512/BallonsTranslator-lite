"""Frozen task inputs, cooperative cancellation and main-thread conflicts.

All engines are fakes; no API, model load or foreground window is used.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import pytest
from qtpy.QtCore import QObject, Signal
from qtpy.QtWidgets import QApplication, QWidget

from ui.pipeline_runner import PipelineRunner, page_signature
from ui.pipeline_tasks import PipelineTasks
from utils.config import pcfg, RunStatus
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock
from modules.translators.agent.validator import validate_submission


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path):
    for name in ("a.png", "b.png"):
        cv2.imwrite(str(tmp_path / name), np.zeros((40, 40, 3), np.uint8))
    project = ProjImgTrans(directory=str(tmp_path))
    project.pages["a.png"] = [TextBlock(xyxy=[1, 1, 10, 10], text=["hello"], translation=""),
                              TextBlock(xyxy=[12, 1, 20, 10], text=["world"], translation="existing")]
    project.pages["b.png"] = [TextBlock(xyxy=[1, 1, 10, 10], text=["next"], translation="")]
    project.save()
    return project


class Translator:
    def __init__(self):
        self.calls = []
        self._status_cb = None

    def set_status_callback(self, callback):
        self._status_cb = callback

    def translate_selection_with_context(self, blocks, **kwargs):
        self.calls.append((blocks, kwargs))
        return ["translated " + block.get_text() for block in blocks]


def make_runner(project, **kwargs):
    translator = kwargs.pop("translator", Translator())
    return PipelineRunner(project, list(project.pages), kwargs.pop("mode", "translate"),
                          pcfg.module, {"translator": translator}, **kwargs)


def test_default_fills_only_empty_and_freezes_input(app, project):
    runner = make_runner(project)
    ready = []
    runner.page_ready.connect(ready.append)
    runner.run()
    assert project.pages["a.png"][0].translation == ""
    assert ready[0]["indexes"] == [0]
    assert ready[0]["blocks"][1].translation == "existing"
    assert len(runner.report["completed"]) == 2
    assert runner.modules["translator"]._status_cb is None


def test_revision_only_targets_and_does_not_pollute_context(app, project):
    runner = make_runner(project, mode="revise", targets={"a.png": [1], "b.png": [0]}, hint="concise")
    ready = []
    runner.page_ready.connect(ready.append)
    runner.run()
    assert ready[0]["proposals"][0]["index"] == 1
    assert ready[0]["proposals"][0]["before"] == "existing"
    assert runner.project.pages["a.png"][1].translation == "existing"
    assert project.pages["a.png"][1].translation == "existing"
    assert "concise" in runner.modules["translator"].calls[0][1]["hint"]


def test_cancel_in_flight_discards_late_result_and_next_page(app, project):
    translator = Translator()
    runner = make_runner(project, translator=translator)
    def request(blocks, **kwargs):
        translator.calls.append(kwargs)
        runner.cancel()
        return ["late"] * len(blocks)
    translator.translate_selection_with_context = request
    ready = []
    runner.page_ready.connect(ready.append)
    runner.run()
    assert ready == []
    assert len(translator.calls) == 1
    assert runner.report["cancelled"]
    assert runner.report["completed"] == []


def test_incomplete_submission_is_failure_not_completion(app, project):
    translator = Translator()
    translator.translate_selection_with_context = lambda *args, **kwargs: []
    runner = make_runner(project, translator=translator)
    ready = []
    runner.page_ready.connect(ready.append)
    runner.run()
    assert not ready
    assert set(runner.report["failed"]) == {"a.png", "b.png"}
    assert not runner.report["completed"]


def test_usage_distinguishes_unknown(app, project):
    runner = make_runner(project)
    runner._status("a.png", 1, [], 10)
    runner._status("a.png", 2, [], None)
    assert (runner.known_tokens, runner.unknown_usage) == (10, 1)


@pytest.mark.parametrize("change", ["text", "translation", "delete", "generation", "image"])
def test_page_signature_rejects_stale_input(project, change):
    before = page_signature(project, "a.png")
    if change == "text":
        project.pages["a.png"][0].text = ["edited"]
    elif change == "translation":
        project.pages["a.png"][0].translation = "edited"
    elif change == "delete":
        project.pages["a.png"].pop(0)
    elif change == "generation":
        project.bump_page_generation("a.png")
    else:
        project.bump_page_image_generation("a.png")
    assert page_signature(project, "a.png") != before


def test_page_signature_allows_identical_image_save_but_rejects_changes(project):
    path = Path(project.get_mask_path("a.png"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"same image")
    before = page_signature(project, "a.png")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
    assert page_signature(project, "a.png") == before
    path.write_bytes(b"new pixels")
    assert page_signature(project, "a.png") != before


@pytest.mark.parametrize("payload", [
    {"1": "ok", "3": "foreign"},
    [{"id": 1, "translation": "first"}, {"id": 1, "translation": "second"}],
    {"1": None},
])
def test_strict_submission_rejects_mixed_invalid_or_duplicate_ids(payload):
    accepted, _, _, _, rejected = validate_submission({"translations": payload}, frozenset({1}), strict_ids=True)
    assert accepted is None
    assert rejected == []


class Canvas(QObject):
    content_modified = Signal()
    num_imgstep = saved_imgstep = 0
    def __init__(self):
        super().__init__()
        self.invalidate_text_history_for_page = Mock()
        self.setProjSaveState = Mock()
        self.updateCanvas = Mock()
    def draw_change_unsaved(self):
        return False


def fake_window(project):
    w = QWidget()
    w.imgtrans_proj = project
    w.canvas = Canvas()
    w._sync_block_data = Mock()
    w.imsave_thread = SimpleNamespace(isRunning=lambda: False)
    w.postprocess_translations = Mock()
    def rebuild():
        w.st_manager.textblk_item_list = [SimpleNamespace(blk=block) for block in project.current_block_list()]
    w.st_manager = SimpleNamespace(updateSceneTextitems=Mock(side_effect=rebuild))
    rebuild()
    w.glossary_workbench = SimpleNamespace(mark_content_changed=Mock())
    w.pageList = SimpleNamespace(setCurrentRow=Mock())
    return w


def test_settlement_preserves_manual_edits_and_does_not_switch_page(app, project):
    w = fake_window(project)
    controller = PipelineTasks(w)
    runner = controller.runner = make_runner(project)
    controller.project = project
    controller.applied = controller.conflicts = controller.blocks = 0
    controller.errors = []
    controller.refresh = lambda: None
    result = runner._process_page("a.png")
    # User types after the snapshot; sync must happen before comparison.
    w._sync_block_data.side_effect = lambda: setattr(project.pages["a.png"][0], "translation", "human")
    controller.receive(result)
    assert project.pages["a.png"][0].translation == "human"
    assert controller.conflicts == 1
    assert controller.applied == 0
    assert runner._settled_page.is_set()
    w.pageList.setCurrentRow.assert_not_called()
    w.st_manager.updateSceneTextitems.assert_not_called()


def test_settlement_saves_only_matching_page_and_acknowledges_context(app, project):
    w = fake_window(project)
    controller = PipelineTasks(w)
    runner = controller.runner = make_runner(project)
    controller.project = project
    controller.applied = controller.conflicts = controller.blocks = 0
    controller.errors = []
    controller.refresh = lambda: None
    result = runner._process_page("b.png")
    controller.receive(result)
    assert not controller.errors
    assert controller.applied == 1
    assert project.pages["b.png"][0].translation == "translated next"
    assert runner._accepted_blocks[0].translation == "translated next"
    w.pageList.setCurrentRow.assert_not_called()


@pytest.mark.parametrize("outcome", ["saved", "edited", "cancelled", "replaced"])
def test_settlement_waits_for_save_without_false_conflict(app, project, outcome):
    from qtpy.QtTest import QTest
    w = fake_window(project)
    busy = {"value": True}
    w.imsave_thread.isRunning = lambda: busy["value"]
    controller = PipelineTasks(w)
    runner = controller.runner = make_runner(project)
    controller.project = project
    controller.applied = controller.conflicts = controller.blocks = 0
    controller.errors = []
    controller.refresh = lambda: None
    controller.receive(runner._process_page("a.png"))
    assert controller.conflicts == controller.applied == 0
    assert not runner._settled_page.is_set()
    if outcome == "edited":
        project.pages["a.png"][0].translation = "human"
    elif outcome == "cancelled":
        runner.cancel()
    elif outcome == "replaced":
        controller.runner = make_runner(project)
    busy["value"] = False
    QTest.qWait(100)
    app.processEvents()
    assert controller.applied == (1 if outcome == "saved" else 0)
    assert controller.conflicts == (1 if outcome == "edited" else 0)
    assert runner._settled_page.is_set() == (outcome != "replaced")
    if outcome == "saved":
        assert project.pages["a.png"][0].translation == "translated hello"
        w.st_manager.updateSceneTextitems.assert_called_once()
    else:
        assert project.pages["a.png"][0].translation == ("human" if outcome == "edited" else "")


@pytest.mark.parametrize("detect", [False, True])
def test_recognition_settlement_preserves_valid_progress(app, project, detect):
    w = fake_window(project)
    project._image_info["a.png"]["finish_code"] = RunStatus.FIN_ALL
    controller = PipelineTasks(w)
    runner = controller.runner = make_runner(project, mode="recognize")
    runner.config.enable_detect = detect
    controller.project = project
    controller.applied = controller.conflicts = controller.blocks = 0
    controller.errors = []
    controller.refresh = lambda: None
    result = {"page": "a.png", "blocks": runner.project.pages["a.png"],
              "mask": None, "inpainted": None,
              "bits": RunStatus.FIN_DET if detect else RunStatus.FIN_OCR}
    controller.receive(result)
    assert not controller.errors
    assert controller.applied == 1
    expected = RunStatus.FIN_DET | RunStatus.FIN_INPAINT if detect else RunStatus.FIN_ALL
    assert project._image_info["a.png"]["finish_code"] == expected
    assert runner._settled_page.is_set()


def test_cancelled_save_wait_cannot_start_over_new_task(app, project, monkeypatch):
    from qtpy.QtCore import QTimer
    from qtpy.QtTest import QTest
    w = fake_window(project)
    w.configPanel = QWidget()
    w.glossary_workbench = QWidget()
    w.bottomBar = SimpleNamespace(**{name: QWidget() for name in (
        "textdet_selector", "ocr_selector", "inpaint_selector", "trans_selector")})
    busy = {"value": True}
    w.imsave_thread.isRunning = lambda: busy["value"]
    w.imsave_thread.failure_count = 0
    w.conditional_save = Mock()
    w._cuda_work_in_progress = lambda: False
    w.module_manager = SimpleNamespace(translator=Translator())
    starts = []
    monkeypatch.setattr(PipelineRunner, "start", lambda runner: starts.append(runner.mode))
    controller = PipelineTasks(w)
    controller.refresh = lambda: None
    controller.start("translate")
    assert controller.pending
    controller.cancel()
    assert not w.module_manager.background_task_active
    controller.start("revise", targets={"a.png": [0]})
    QTimer.singleShot(10, lambda: busy.update(value=False))
    QTest.qWait(160)
    app.processEvents()
    assert starts == ["revise"]
    assert not controller.pending
    assert controller.runner.mode == "revise"
    controller.timer.stop()
    controller.runner = None
    controller._controls(True)


def test_real_worker_lifecycle_waits_for_ui_and_releases_ownership(app, project):
    import time
    from qtpy.QtTest import QTest
    w = fake_window(project)
    w.configPanel = QWidget()
    w.glossary_workbench = QWidget()
    w.glossary_workbench.mark_content_changed = Mock()
    w.bottomBar = SimpleNamespace(**{name: QWidget() for name in (
        "textdet_selector", "ocr_selector", "inpaint_selector", "trans_selector")})
    w.imsave_thread.failure_count = 0
    w.conditional_save = Mock()
    w._cuda_work_in_progress = lambda: False
    w.updatePageList = Mock()
    translator = Translator()
    w.module_manager = SimpleNamespace(
        translator=translator,
        unload_all_models=Mock(),
    )
    controller = PipelineTasks(w)
    controller.start("translate")
    assert controller.busy()
    assert w.module_manager.background_task_active
    deadline = time.monotonic() + 5
    while controller.busy() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    if controller.runner is not None:
        controller.cancel()
        controller.runner.wait(1000)
        pytest.fail("Worker did not settle through the main-thread event loop")
    assert not controller.errors
    assert controller.applied == 2
    assert not w.module_manager.background_task_active
    assert project.pages["a.png"][0].translation == "translated hello"
    assert project.pages["a.png"][1].translation == "existing"
    w.pageList.setCurrentRow.assert_not_called()


@pytest.mark.parametrize("jump_ok", [True, False])
def test_revision_apply_and_undo_restore_text_and_pending_tag(app, project, jump_ok):
    from qtpy.QtGui import QUndoStack
    from qtpy.QtWidgets import QGraphicsScene, QPushButton
    from ui.textitem import TextBlkItem
    from ui.textedit_area import SourceTextEdit, TransTextEdit
    from utils.block_tags import set_manual_tag, TRANS_REVIEW_ID
    from ui.pipeline_runner import block_signature
    w = fake_window(project)
    block = project.pages["a.png"][0]
    block.translation = "before"
    block.lines = np.array([[[1, 1], [20, 1], [20, 20], [1, 20]]])
    set_manual_tag(block, TRANS_REVIEW_ID, True)
    scene = QGraphicsScene()
    item = TextBlkItem(block, 0)
    scene.addItem(item)
    source, translation = SourceTextEdit(0, None), TransTextEdit(0, None)
    source.setPlainText(block.get_text())
    translation.setPlainText(block.translation)
    w.st_manager.textblk_item_list = [item]
    w.st_manager.pairwidget_list = [SimpleNamespace(e_source=source, e_trans=translation)]
    stack = QUndoStack()
    w.canvas.push_undo_command = stack.push
    w.on_workbench_jump = Mock()
    controller = PipelineTasks(w)
    controller.project = project
    controller.candidates = [{"project": project, "page": "a.png", "index": 0,
                              "signature": block_signature(block), "source": "hello",
                              "before": "before", "after": "after"}]
    controller.review_next()
    apply = next(b for b in controller.dialog.findChildren(QPushButton) if b.text() == "Apply revision")
    if not jump_ok:
        project.current_img = "b.png"
    apply.click()
    if not jump_ok:
        assert block.translation == "before"
        assert TRANS_REVIEW_ID in block.tags
        assert not apply.isEnabled()
        assert stack.count() == 0
        controller.dialog.close()
        scene.removeItem(item)
        return
    assert block.translation == "after"
    assert TRANS_REVIEW_ID not in block.tags
    stack.undo()
    assert block.translation == "before"
    assert TRANS_REVIEW_ID in block.tags
    stack.redo()
    assert block.translation == "after"
    assert TRANS_REVIEW_ID not in block.tags
    scene.removeItem(item)
