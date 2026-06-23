"""
OverlayModal — fade-in/pop-up modal for the config panel.

Unlike OverlaySlider (which slides children in from a screen edge),
OverlayModal shows ``panel`` centered over ``cover_widget`` behind a
darkened scrim.  Both the scrim and the panel animate their opacity
(alphanumericfa 0→target) via QGraphicsOpacityEffect + QPropertyAnimation.
Animations are skipped when ``pcfg.animation_fps < 0`` (project convention)
or when the configured duration is <= 0.

The scrim only covers ``cover_widget``; siblings of ``cover_widget``
(left bar, bottom bar, title bar) remain interactive.  Clicking the scrim
outside the panel rectangle closes the modal.  Parent hooks mirror
OverlaySlider: ``on_before_show`` / ``on_after_hide`` callbacks, and
``set_backdrop_closable(False)`` disables scrim clicks (used while a
child QDialog is open so the modal isn't dismissed by misclicks).
"""

from __future__ import annotations

from typing import Callable, Optional

from qtpy.QtCore import QEasingCurve, QElapsedTimer, QObject, Qt, QTimer
from qtpy.QtGui import QColor, QPainter
from qtpy.QtWidgets import QGraphicsOpacityEffect, QWidget

from utils.config import pcfg

# ── Fine detail ────────────────────────────────────────────────────
# Panel uses a fixed size (capped to cover_widget if smaller), centered
# over cover_widget. Wide elements like the font-format delegation grid
# drive 1000px; revisit if individual pages overflow.
_PANEL_W = 1000
_PANEL_H = 700

# Hard‑coded fine detail.
_SCRIM_ALPHA = 0.55
_DEFAULT_DURATION = 350


def _detect_interval() -> int:
    """Timer interval (ms) honoring pcfg.animation_fps, else screen refresh."""
    fps = pcfg.animation_fps
    if fps > 0:
        return int(round(1000.0 / fps))
    try:
        from qtpy.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return 8
        screens = app.screens()
        if not screens:
            return 8
        hz = screens[0].refreshRate()
        if hz <= 0:
            return 8
        return max(4, min(int(round(1000.0 / (hz + 10))), 16))
    except Exception:
        return 8


class _Scrim(QWidget):
    """Semi‑transparent overlay that fills cover_widget and captures clicks."""

    def __init__(self, parent: QWidget, alpha: float, on_click_outside: Callable):
        super().__init__(parent)
        self._color = QColor(0, 0, 0, int(round(alpha * 255)))
        self._on_click_outside = on_click_outside
        self._closable = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

    def set_closable(self, closable: bool) -> None:
        self._closable = closable

    def set_alpha_target(self, target: float) -> None:
        self._effect.setOpacity(target)

    def alpha(self) -> float:
        return self._effect.opacity()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Fill the parent (cover_widget) entirely.
        self.setGeometry(self.parentWidget().rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._closable:
            return
        # Always treat scrim clicks as "outside" — the panel sits above the
        # scrim and intercepts its own clicks, so any press reaching us is
        # on exposed scrim area.
        self._on_click_outside()


class OverlayModal(QObject):
    """Fade modal hosting ``panel`` over ``cover_widget``."""

    def __init__(self, panel: QWidget, cover_widget: QWidget, duration: int = _DEFAULT_DURATION):
        super().__init__(panel)
        self._panel = panel
        self._cover = cover_widget
        self._duration = duration

        self._before_show: list[Callable] = []
        self._after_hide: list[Callable] = []
        self._scrim: Optional[_Scrim] = None

        self._panel_effect = QGraphicsOpacityEffect(panel)
        self._panel_effect.setOpacity(0.0)
        panel.setGraphicsEffect(self._panel_effect)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()
        self._easing = QEasingCurve(QEasingCurve.Type.InOutExpo)

        self._showing: bool = False   # animating/open toward shown
        self._hiding: bool = False    # animating toward hidden
        self._busy: bool = False      # a timer‑driven frame loop is running
        self._alpha_start: float = 0.0
        self._alpha_end: float = 0.0
        self._on_finished: Optional[Callable] = None

    # ── Callback registration (mirror OverlaySlider) ──────────────────

    def on_before_show(self, cb: Callable) -> None:
        self._before_show.append(cb)

    def on_after_hide(self, cb: Callable) -> None:
        self._after_hide.append(cb)

    def set_backdrop_closable(self, closable: bool) -> None:
        if self._scrim is not None:
            self._scrim.set_closable(closable)

    def resize(self) -> None:
        """Recenter panel and refit scrim after cover_widget resize."""
        if self._scrim is not None:
            self._scrim.setGeometry(self._cover.rect())
        if self._panel.isVisible():
            pw = self._cover.width()
            ph = self._cover.height()
            w = min(_PANEL_W, pw)
            h = min(_PANEL_H, ph)
            self._panel.setFixedSize(w, h)
            self._panel.setGeometry(pw // 2 - w // 2, ph // 2 - h // 2, w, h)

    # ── Public API ────────────────────────────────────────────────────

    @property
    def is_visible(self) -> bool:
        return self._panel.isVisible()

    def show(self) -> None:
        if self._busy and not self._hiding:
            return
        if self._busy and self._hiding:
            # Reverse a running hide into a show.
            self._reverse(toward_hidden=False)
            self._on_finished = None
            self._hiding = False
            self._showing = True
            return

        self._cancel()
        for cb in self._before_show:
            cb()

        self._scrim = _Scrim(self._cover, _SCRIM_ALPHA, self.hide)
        self._scrim.setGeometry(self._cover.rect())
        self._scrim.set_closable(True)

        # Panel above scrim; centered in cover_widget. Fixed size (capped to cover).
        pw = self._cover.width()
        ph = self._cover.height()
        w = min(_PANEL_W, pw)
        h = min(_PANEL_H, ph)
        self._panel.setParent(self._cover)
        self._panel.setFixedSize(w, h)
        self._panel.setGeometry(
            pw // 2 - w // 2, ph // 2 - h // 2, w, h
        )
        self._panel.show()
        self._panel.raise_()
        self._scrim.show()
        self._scrim.raise_()
        self._panel.raise_()
        self._panel.setFocus()

        no_anim = pcfg.animation_fps < 0 or self._duration <= 0
        if no_anim:
            self._panel_effect.setOpacity(1.0)
            self._scrim.set_alpha_target(_SCRIM_ALPHA)
            self._showing = True
            self._hiding = False
            return

        self._alpha_start = self._panel_effect.opacity()
        self._alpha_end = 1.0
        self._scrim_start = self._scrim.alpha()
        self._scrim_end = _SCRIM_ALPHA
        self._showing = True
        self._hiding = False
        self._on_finished = None
        self._start_timer()

    def hide(self) -> None:
        if self._busy and self._hiding:
            if not self._panel.isVisible():
                self._cancel()
                self._do_after_hide()
            return
        no_anim = pcfg.animation_fps < 0 or self._duration <= 0
        if no_anim:
            self._cancel()
            self._panel.hide()
            self._teardown_scrim()
            self._showing = False
            self._hiding = False
            self._do_after_hide()
            return
        if self._busy and not self._hiding:
            self._reverse(toward_hidden=True)
            self._on_finished = self._on_hidden
            self._hiding = True
            self._showing = False
            return
        if not self._panel.isVisible():
            return

        self._alpha_start = self._panel_effect.opacity()
        self._alpha_end = 0.0
        self._scrim_start = self._scrim.alpha() if self._scrim else 0.0
        self._scrim_end = 0.0
        self._on_finished = self._on_hidden
        self._hiding = True
        self._showing = False
        self._start_timer()

    # ── Internals ──────────────────────────────────────────────────────

    def _start_timer(self) -> None:
        self._timer.stop()
        self._elapsed.start()
        self._busy = True
        self._timer.start(_detect_interval())

    def _reverse(self, toward_hidden: bool) -> None:
        self._alpha_start = self._panel_effect.opacity()
        if toward_hidden:
            self._alpha_end = 0.0
        else:
            self._alpha_end = 1.0
        if self._scrim is not None:
            self._scrim_start = self._scrim.alpha()
            self._scrim_end = 0.0 if toward_hidden else _SCRIM_ALPHA
        self._elapsed.start()

    def _cancel(self) -> None:
        self._timer.stop()
        self._busy = False
        self._on_finished = None

    def _teardown_scrim(self) -> None:
        if self._scrim is not None:
            self._scrim.setParent(None)
            self._scrim.deleteLater()
            self._scrim = None

    def _on_hidden(self) -> None:
        self._panel.hide()
        self._teardown_scrim()
        self._do_after_hide()

    def _do_after_hide(self) -> None:
        for cb in self._after_hide:
            cb()

    def _tick(self) -> None:
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._duration, 1.0) if self._duration > 0 else 1.0
        eased = self._easing.valueForProgress(progress)

        panel_alpha = self._alpha_start + (self._alpha_end - self._alpha_start) * eased
        self._panel_effect.setOpacity(panel_alpha)
        if self._scrim is not None:
            scrim_alpha = self._scrim_start + (self._scrim_end - self._scrim_start) * eased
            self._scrim.set_alpha_target(scrim_alpha)

        if progress >= 1.0:
            self._timer.stop()
            self._busy = False
            self._panel_effect.setOpacity(self._alpha_end)
            finished = self._on_finished
            self._on_finished = None
            if finished:
                finished()