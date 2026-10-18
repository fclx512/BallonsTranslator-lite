"""Contract tests for the in-window pipeline progress overlay."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from qtpy.QtCore import QObject, Signal, QEvent, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QPushButton, QWidget

from ui.pipeline_progress_overlay import PipelineProgressOverlay
from ui.pipeline_tasks import PipelineTasks, STOP_GRACE_SECONDS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_overlay_stages_and_input_boundary(app):
    host = QWidget()
    outside = QPushButton("Outside", host)
    invoked = Mock()
    outside.clicked.connect(invoked)
    host.resize(900, 700)
    host.show()
    overlay = PipelineProgressOverlay(host)
    overlay.panel.set_stages({"translate", "render"})
    overlay.show()
    overlay.panel.show()
    app.processEvents()

    assert overlay.panel.stage_cards["translate"].isVisible()
    assert overlay.panel.stage_cards["render"].isVisible()
    assert not overlay.panel.stage_cards["detect"].isVisible()
    assert not overlay.panel.stage_cards["ocr"].isVisible()
    assert not overlay.panel.stage_cards["inpaint"].isVisible()

    event = QEvent(QEvent.Type.KeyPress)
    assert overlay.event(event)
    assert event.isAccepted()
    overlay.panel.activity_log.setFocus()
    for _ in range(6):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert overlay.panel.isAncestorOf(app.focusWidget())
    for _ in range(6):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Backtab)
        assert overlay.panel.isAncestorOf(app.focusWidget())
    invoked.assert_not_called()
    overlay.close_overlay()
    host.close()


def test_page_change_resets_stage_counts_and_keeps_plain_activity(app):
    import time
    host = QWidget()
    host.canvas = _Canvas()
    host.resize(900, 700)
    host.show()
    controller = PipelineTasks(host)
    overlay = controller.overlay = PipelineProgressOverlay(host)
    overlay.show()
    overlay.panel.show()
    controller.runner = SimpleNamespace(pages=["a.png", "b.png"], mode="translate",
                                        report={"skipped": [], "failed": {}},
                                        total_blocks=8, known_tokens=120, unknown_usage=1)
    controller.started = time.monotonic()
    controller.applied = controller.conflicts = controller.blocks = 0
    controller.errors = []
    controller.activity({"waiting": True, "page": "a.png", "number": 1, "total": 2})
    controller.activity({"stage": "translate", "state": "running", "total": 7})
    controller.activity({"turn": 2, "tools": ["<b>literal</b>"], "covered": 3, "covered_total": 7})
    controller.activity({"stage": "translate", "state": "done", "total": 7, "seconds": 3.0})
    panel = overlay.panel
    assert panel.stage_cards["translate"].property("state") == "done"
    assert "<b>literal</b>" in panel.activity_log.toPlainText()
    controller.activity({"waiting": True, "page": "b.png", "number": 2, "total": 2})
    assert panel.stage_cards["translate"].property("state") == "waiting"
    assert panel.stage_cards["translate"].bar.isHidden()
    assert panel.page_facts["target"][1].text() == "—"
    assert panel.page_facts["round"][1].text() == "—"
    assert "total" not in controller.state
    assert panel.progress.value() == 1
    assert panel.progress.maximum() == 2
    # 小宿主中正文滚动，操作区仍在面板内。
    host.resize(480, 340)
    app.processEvents()
    assert overlay.rect().contains(panel.geometry())
    assert panel.rect().contains(panel.stop_graceful.mapTo(panel, panel.stop_graceful.rect().bottomRight()))
    overlay.close_overlay()
    host.close()


class _Canvas(QObject):
    content_modified = Signal()

    def __init__(self):
        super().__init__()
        self.draw_change_unsaved = lambda: False


def _controller(app):
    window = QWidget()
    window.canvas = _Canvas()
    controller = PipelineTasks(window)
    controller.overlay = PipelineProgressOverlay(window)
    controller.stop_state = "graceful"
    controller.stop_requested_at = __import__("time").monotonic() - STOP_GRACE_SECONDS - 1
    return controller, window


def test_watchdog_enters_unresponsive_without_force_exit(app):
    controller, window = _controller(app)
    controller._check_stop_timeout()
    assert controller.stop_state == "unresponsive"
    assert controller.overlay.panel.title.text() == controller.tr("No response")
    assert controller.overlay.panel.stop_force.isEnabled()
    controller._teardown_overlay()
    window.close()


def test_start_failure_close_does_not_force_exit(app):
    controller, window = _controller(app)
    controller._controls = Mock()
    force = Mock()
    controller.overlay.stop_force_requested.connect(force)
    controller._fail_to_start("<b>save failed</b>")
    panel = controller.overlay.panel
    assert panel.notice.text() == "<b>save failed</b>"
    assert panel.progress.maximum() == 1
    panel.stop_force.click()
    assert controller.overlay is None
    force.assert_not_called()
    controller._teardown_overlay()
    window.close()


def test_force_exit_saves_then_exits(monkeypatch, app):
    window = QWidget()
    window.canvas = _Canvas()
    window.conditional_save = Mock()
    window.imsave_thread = SimpleNamespace(isRunning=lambda: False)
    controller = PipelineTasks(window)
    exited = []
    monkeypatch.setattr(os, "_exit", exited.append)
    controller._force_exit()
    window.conditional_save.assert_called_once_with()
    assert exited == [1]
    window.close()
