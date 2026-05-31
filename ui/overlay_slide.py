"""
OverlaySlider — reusable slide-in/out animation for floating overlay panels.

A single _SharedOverlay per parent widget composites all concurrently
animating panels in z-order, avoiding pixmap-capture conflicts between
sibling panels of different widths.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

from qtpy.QtCore import QEasingCurve, QElapsedTimer, QObject, QRect, Qt, QTimer
from qtpy.QtGui import QPainter, QPixmap
from qtpy.QtWidgets import QWidget

from utils.config import pcfg


# ═══════════════════════════════════════════════════════════════════
# Shared composited overlay (one per parent widget)
# ═══════════════════════════════════════════════════════════════════

class _SharedOverlay(QWidget):
    """Per-parent overlay painting background + panel layers.

    The overlay covers only the region occupied by the widest active
    panel (0 … max_panel_width × parent_height), not the entire parent,
    to keep per-frame pixmap blits cheap.  Background expands on demand
    when a wider panel joins.
    """

    _instances: dict[int, "_SharedOverlay"] = {}  # id(parent) -> overlay

    def __init__(self, parent: QWidget, bg: QPixmap):
        super().__init__(parent)
        self._bg = bg
        self._layers: dict[str, tuple[QPixmap, int, int]] = {}  # id -> (pm, x, z)
        self.setGeometry(0, 0, bg.width(), parent.height())
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # ── Factory ─────────────────────────────────────────────────

    @classmethod
    def acquire(cls, parent: QWidget, width: int) -> "_SharedOverlay":
        key = id(parent)
        inst = cls._instances.get(key)
        if inst is not None:
            inst.show()
            inst.raise_()
            if width > inst._bg.width():
                inst._expand(width)
            return inst
        bg = parent.grab(QRect(0, 0, width, parent.height()))
        inst = cls(parent, bg)
        cls._instances[key] = inst
        inst.show()
        inst.raise_()
        return inst

    # ── Layer ops ───────────────────────────────────────────────

    def add_layer(self, lid: str, pixmap: QPixmap, offset_x: int, z: int):
        self._layers[lid] = (pixmap, offset_x, z)
        self.update()

    def update_layer(self, lid: str, offset_x: int):
        entry = self._layers.get(lid)
        if entry is None:
            return
        self._layers[lid] = (entry[0], offset_x, entry[2])
        self.update()

    def remove_layer(self, lid: str):
        self._layers.pop(lid, None)
        if self._layers:
            self.update()
            return
        self.hide()
        key = id(self.parentWidget())
        _SharedOverlay._instances.pop(key, None)
        self.deleteLater()

    # ── Events ──────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._bg)
        for _, (pm, x, _) in sorted(self._layers.items(),
                                    key=lambda kv: kv[1][2]):
            painter.drawPixmap(x, 0, pm)

    def resize_for_parent(self):
        """Sync height and collapse back to tight width after parent resize."""
        p = self.parentWidget()
        if not p:
            return
        ph = p.height()
        w = self._bg.width()
        self.setGeometry(0, 0, w, ph)

    # ── Internals ───────────────────────────────────────────────

    def _expand(self, new_width: int):
        """Grow background and geometry to match a newly added wider panel."""
        p = self.parentWidget()
        ph = p.height()
        cur_w = self._bg.width()
        extra = p.grab(QRect(cur_w, 0, new_width - cur_w, ph))
        new_bg = QPixmap(new_width, ph)
        painter = QPainter(new_bg)
        painter.drawPixmap(0, 0, self._bg)
        painter.drawPixmap(cur_w, 0, extra)
        painter.end()
        self._bg = new_bg
        self.setGeometry(0, 0, new_width, ph)


# ═══════════════════════════════════════════════════════════════════
# Public slider
# ═══════════════════════════════════════════════════════════════════

class OverlaySlider(QObject):
    _next_z: int = 0

    def __init__(
        self,
        widget,
        direction: str = "left",
        duration: int = 350,
        width: Optional[Union[int, Callable[[], int]]] = None,
    ):
        super().__init__(widget)
        self._widget = widget
        self._direction = direction
        self._width = width
        self._duration = duration
        self._before_show: list[Callable] = []
        self._after_hide: list[Callable] = []

        self._easing = QEasingCurve(QEasingCurve.Type.InOutExpo)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._update_animation)

        self._elapsed = QElapsedTimer()

        # Animation segment
        self._start_x: int = 0
        self._end_x: int = 0
        self._current_x: int = 0
        # Persistent bounds for reversal
        self._anim_start_x: int = 0
        self._anim_end_x: int = 0

        self._on_finished: Optional[Callable] = None
        self._animating: bool = False
        self._hiding: bool = False

        # Shared‑overlay layer identity (None when no layer registered)
        self._shared: Optional[_SharedOverlay] = None
        self._layer_id: Optional[str] = None
        self._panel_pm: Optional[QPixmap] = None

    # ── Callback registration ───────────────────────────────────────

    def on_before_show(self, cb: Callable):
        self._before_show.append(cb)

    def on_after_hide(self, cb: Callable):
        self._after_hide.append(cb)

    # ── Public API ──────────────────────────────────────────────────

    @property
    def is_visible(self) -> bool:
        return self._widget.isVisible()

    def show(self):
        # Reverse a running hide animation
        if self._animating and self._hiding:
            self._reverse(toward_start=True)
            self._on_finished = None
            self._hiding = False
            return

        self._cancel_current()

        widget = self._widget
        pw = widget.parentWidget()
        if not pw:
            return

        ow = self._resolve_width(pw)

        # No-animation mode — jump straight to visible
        if pcfg.animation_fps < 0:
            widget.setGeometry(0, 0, ow, pw.height())
            widget.show()
            widget.raise_()
            self._hiding = False
            for cb in self._before_show:
                cb()
            return

        start_x = -ow if self._direction == "left" else pw.width()
        widget.setGeometry(start_x, 0, ow, pw.height())

        for cb in self._before_show:
            cb()

        # Show the real widget at its start position and animate it directly
        # (no SharedOverlay compositing) to avoid rendering artifacts from
        # the grab → hide → show cycle.
        widget.show()
        widget.raise_()

        self._anim_start_x = start_x
        self._anim_end_x = 0
        self._start_x = start_x
        self._end_x = 0
        self._on_finished = None
        self._hiding = False
        self._start_animation()

    def hide(self):
        # No-animation mode — immediate hide
        if pcfg.animation_fps < 0:
            self._cancel_current()
            self._widget.hide()
            for cb in self._after_hide:
                cb()
            return

        # Reverse a running show animation
        if self._animating and not self._hiding:
            self._reverse(toward_start=True)
            self._on_finished = self._on_hidden
            self._hiding = True
            return

        # Hide animation was interrupted mid-reversal — clean up immediately
        if self._animating and not self._widget.isVisible():
            self._cancel_current()
            self._widget.hide()
            for cb in self._after_hide:
                cb()
            return

        if not self._widget.isVisible():
            return

        widget = self._widget
        pw = widget.parentWidget()
        ow = self._resolve_width(pw)
        end_x = -ow if self._direction == "left" else pw.width()

        # Render visible panel, then move off-screen so bg stays clean
        self._panel_pm = widget.grab()
        widget.move(end_x, 0)

        self._shared = _SharedOverlay.acquire(pw, ow)
        OverlaySlider._next_z += 1
        self._layer_id = str(OverlaySlider._next_z)
        self._shared.add_layer(self._layer_id, self._panel_pm, 0,
                               OverlaySlider._next_z)
        widget.hide()

        self._anim_start_x = 0
        self._anim_end_x = end_x
        self._start_x = 0
        self._end_x = end_x
        self._on_finished = self._on_hidden
        self._hiding = True
        self._start_animation()

    def resize(self):
        pw = self._widget.parentWidget()
        if not pw:
            return
        if self._animating and self._shared:
            self._shared.resize_for_parent()
        elif self._widget.isVisible():
            ow = self._resolve_width(pw)
            self._widget.setGeometry(
                0 if self._direction == "left" else pw.width() - ow,
                0, ow, pw.height(),
            )

    # ── Internals ───────────────────────────────────────────────────

    def _resolve_width(self, parent_widget) -> int:
        if callable(self._width):
            return self._width()
        if self._width is not None:
            return self._width
        return parent_widget.width()

    def _reverse(self, toward_start: bool):
        """Smoothly reverse the running animation."""
        self._start_x = self._current_x
        self._end_x = self._anim_start_x if toward_start else self._anim_end_x
        self._anim_start_x, self._anim_end_x = self._anim_end_x, self._anim_start_x
        self._elapsed.start()

    def _cancel_current(self):
        """Stop animation, remove layer, discard callbacks."""
        self._timer.stop()
        self._animating = False
        self._on_finished = None

        if self._shared is not None and self._layer_id is not None:
            self._shared.remove_layer(self._layer_id)
            self._layer_id = None
            self._shared = None
            self._panel_pm = None

    def _cleanup_animation(self):
        """Remove layer and, for show animations, restore the real widget."""
        if not self._hiding:
            # Show finished — place real widget BEFORE removing the
            # overlay layer so there is no moment where neither the
            # overlay composition nor the real widget covers the area.
            self._widget.setGeometry(
                self._current_x, 0,
                self._widget.width(), self._widget.height(),
            )
            self._widget.show()
            self._widget.raise_()

        if self._shared is not None and self._layer_id is not None:
            pw = self._shared.parentWidget()
            self._shared.remove_layer(self._layer_id)
            self._layer_id = None
            self._shared = None
            self._panel_pm = None
            # Force the parent to repaint the region the overlay covered
            if pw:
                pw.update()

    def _start_animation(self):
        self._timer.stop()
        self._elapsed.start()
        self._animating = True
        self._timer.start(self._detect_interval())

    @staticmethod
    def _detect_interval() -> int:
        fps = pcfg.animation_fps
        if fps > 0:
            return int(round(1000.0 / fps))
        # Auto-detect from screen
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
            interval = int(round(1000.0 / (hz + 10)))
            return max(4, min(interval, 16))
        except Exception:
            return 8

    def _update_animation(self):
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._duration, 1.0) if self._duration > 0 else 1.0
        eased = self._easing.valueForProgress(progress)

        self._current_x = int(round(
            self._start_x + (self._end_x - self._start_x) * eased
        ))

        if self._shared is not None and self._layer_id is not None:
            self._shared.update_layer(self._layer_id, self._current_x)
        else:
            # Fallback: animate real widget directly
            self._widget.move(self._current_x, 0)
            self._widget.raise_()

        if progress >= 1.0:
            self._timer.stop()
            self._animating = False
            self._cleanup_animation()
            if self._on_finished:
                self._on_finished()

    def _on_hidden(self):
        self._widget.hide()
        for cb in self._after_hide:
            cb()
