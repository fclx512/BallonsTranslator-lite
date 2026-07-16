"""ConfigScrollBar — theme-aware QScrollBar subclass with rounded corners.

Usage
-----
    scroll_area.setVerticalScrollBar(ConfigScrollBar(scroll_area))
    scroll_area.setHorizontalScrollBar(ConfigScrollBar(scroll_area))
"""

from qtpy.QtCore import QEasingCurve, QElapsedTimer, Qt, QTimer, QTimer
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QScrollBar

from utils.config import pcfg


def _scroll_interval():
    """Return timer interval (ms) based on configured animation FPS."""
    fps = pcfg.animation_fps
    return max(int(1000 / max(fps, 1)), 16)  # floor at ~60fps


class ConfigScrollBar(QScrollBar):
    """Theme‑aware scrollbar with rounded corners and hover animation.

    Handle colour darkens slightly on hover (no colour inversion).
    Style is consistent with the ``NoArrowsSpinBox`` / ``ConfigLineEdit``
    semi‑transparent rounded aesthetic.

    Supports both vertical and horizontal orientations.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._factor = 0.0        # 0 = normal, 1 = fully hovered
        self._factor_start = 0.0
        self._factor_end = 0.0
        self._animating = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()
        self._duration = 150
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)

        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Cache theme colour so we don't hit disk on every animation tick
        self._cached_base = QColor()
        self._sync_style()

    # ── Hover ────────────────────────────────────────────────────────

    def enterEvent(self, event):
        self._start_anim(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._start_anim(0.0)
        super().leaveEvent(event)

    # ── Orientation ──────────────────────────────────────────────────

    def setOrientation(self, orientation: Qt.Orientation):
        super().setOrientation(orientation)
        self._sync_style()

    # ── Animation ────────────────────────────────────────────────────

    def _start_anim(self, target: float):
        if pcfg.animation_fps < 0:
            self._factor = target
            self._sync_style()
            return
        self._factor_start = self._factor
        self._factor_end = target
        self._elapsed.start()
        if not self._animating:
            self._animating = True
            self._timer.start(_scroll_interval())

    def _tick(self):
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._duration, 1.0)
        eased = self._easing.valueForProgress(progress)
        self._factor = (
            self._factor_start + (self._factor_end - self._factor_start) * eased
        )
        self._sync_style()
        if progress >= 1.0:
            self._timer.stop()
            self._animating = False

    def _sync_style(self):
        """Rebuild inline stylesheet with slightly darker handle on hover."""
        if not self._cached_base.isValid():
            from ui.misc import get_theme_color

            self._cached_base = get_theme_color(key="@scrollBarColor")

        base = self._cached_base
        f = self._factor  # 0 = normal, 1 = fully hovered

        # Darken by reducing brightness + increasing opacity for
        # semi‑transparent colours.  No colour inversion — just a
        # subtle visual weight shift.
        darken = 1.0 - 0.3 * f  # 1.0 → 0.7
        r = int(base.red() * darken)
        g = int(base.green() * darken)
        b = int(base.blue() * darken)
        if base.alpha() < 255:
            a = min(255, int(base.alpha() + (255 - base.alpha()) * 0.5 * f))
        else:
            a = base.alpha()

        if self.orientation() == Qt.Orientation.Vertical:
            self.setStyleSheet(
                "QScrollBar:vertical {"
                "  width: 8px; background: transparent; margin: 0; }"
                "QScrollBar:vertical:hover {"
                "  background: transparent; }"
                "QScrollBar::handle:vertical {"
                f"  background: rgba({r},{g},{b},{a});"
                "  border-radius: 4px; min-height: 20px; }"
                "QScrollBar::add-line:vertical,"
                "QScrollBar::sub-line:vertical { height: 0; }"
            )
        else:
            self.setStyleSheet(
                "QScrollBar:horizontal {"
                "  height: 8px; background: transparent; margin: 0; }"
                "QScrollBar:horizontal:hover {"
                "  background: transparent; }"
                "QScrollBar::handle:horizontal {"
                f"  background: rgba({r},{g},{b},{a});"
                "  border-radius: 4px; min-width: 20px; }"
                "QScrollBar::add-line:horizontal,"
                "QScrollBar::sub-line:horizontal { width: 0; }"
            )
