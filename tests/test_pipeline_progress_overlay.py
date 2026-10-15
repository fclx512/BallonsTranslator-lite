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
from qtpy.QtCore import QObject, Signal, QEvent
from qtpy.QtWidgets import QApplication, QWidget

from ui.pipeline_progress_overlay import PipelineProgressOverlay
from ui.pipeline_tasks import PipelineTasks, STOP_GRACE_SECONDS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_overlay_stages_and_input_boundary(app):
    host = QWidget()
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
    overlay.close_overlay()
    host.close()


def test_translation_details_are_collapsible_and_trimmed(app):
    host = QWidget()
    host.show()
    overlay = PipelineProgressOverlay(host)
    overlay.show()
    overlay.panel.show()
    card = overlay.panel.stage_cards["translate"]
    card.set_summary("Round 2 · check_next_block")
    card.set_details("Round 2<br>Tools: check_next_block<br><i>Reasoning hidden</i>")

    assert card.expand_btn.isVisible()
    assert not card.is_expanded()
    assert card.summary.isVisible()
    assert not card.details.isVisible()
    assert "translation" not in card.details.text().lower()

    card.expand_btn.click()
    assert card.is_expanded()
    assert not card.summary.isVisible()
    assert card.details.isVisible()
    card.expand_btn.click()
    assert not card.is_expanded()
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
