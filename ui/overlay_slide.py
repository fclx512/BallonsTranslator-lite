"""
OverlaySlider — reusable slide-in/out animation for floating overlay panels.

Eliminates the duplicated show/hide/resize pattern that previously
lived in MainWindow for ConfigPanel, GlobalSearchWidget, and PageList.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

from qtpy.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, Qt


class OverlaySlider(QObject):
    """Manages slide-in/out animation for a floating overlay widget.

    Parameters
    ----------
    widget:
        The QWidget to animate. Must already be parented.
    direction:
        ``'left'`` — slides in from the left edge.
        ``'right'`` — slides in from the right edge.
    duration:
        Animation duration in milliseconds (default 350).
    width:
        Overlay width policy:
        - ``None`` → fill parent width (for right-slide panels).
        - ``int`` → fixed width (for left-slide panels with known width).
        - ``callable`` → invoked to get desired width (e.g. ``lambda: w.sizeHint().width()``).
    """

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
        self._before_show: list[Callable] = []
        self._after_hide: list[Callable] = []

        self._anim = QPropertyAnimation(widget, b"pos")
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutExpo)

    # ── Callback registration ───────────────────────────────────────

    def on_before_show(self, cb: Callable):
        """Register a callable invoked just before ``raise_()`` + ``show()``."""
        self._before_show.append(cb)

    def on_after_hide(self, cb: Callable):
        """Register a callable invoked right after ``hide()`` on the widget."""
        self._after_hide.append(cb)

    # ── Public API ──────────────────────────────────────────────────

    @property
    def is_visible(self) -> bool:
        return self._widget.isVisible()

    def show(self):
        """Slide the overlay into view."""
        widget = self._widget
        pw = widget.parentWidget()
        if not pw:
            return

        ow = self._resolve_width(pw)
        if self._direction == "left":
            start_x = -ow
            widget.setGeometry(0, 0, ow, pw.height())
        else:
            start_x = pw.width()
            widget.setGeometry(pw.width(), 0, ow, pw.height())

        for cb in self._before_show:
            cb()
        widget.raise_()
        widget.show()

        self._disconnect_finished()
        self._anim.setStartValue(QPoint(start_x, 0))
        self._anim.setEndValue(QPoint(0, 0))
        self._anim.start()

    def hide(self):
        """Slide the overlay out of view."""
        if not self._widget.isVisible():
            return
        widget = self._widget
        pw = widget.parentWidget()
        end_x = (
            -widget.width() if self._direction == "left" else (pw.width() if pw else 0)
        )

        self._disconnect_finished()
        self._anim.finished.connect(
            self._on_hidden, Qt.ConnectionType.SingleShotConnection
        )
        self._anim.setStartValue(widget.pos())
        self._anim.setEndValue(QPoint(end_x, 0))
        self._anim.start()

    def resize(self):
        """Sync overlay geometry after parent resize."""
        if not self._widget.isVisible():
            return
        pw = self._widget.parentWidget()
        if not pw:
            return
        ow = self._resolve_width(pw)
        self._widget.setGeometry(
            0 if self._direction == "left" else pw.width() - ow,
            0,
            ow,
            pw.height(),
        )

    # ── Internals ───────────────────────────────────────────────────

    def _resolve_width(self, parent_widget) -> int:
        if callable(self._width):
            return self._width()
        if self._width is not None:
            return self._width
        return parent_widget.width()

    def _disconnect_finished(self):
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass

    def _on_hidden(self):
        self._widget.hide()
        for cb in self._after_hide:
            cb()
