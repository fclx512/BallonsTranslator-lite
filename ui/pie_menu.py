"""
Pie menu (Blender-style ring menu) for the canvas.

Interaction (see ``docs/技术实现/环形菜单_实施方案.md``):

- Tab (held) pops the menu at the cursor; the hovered command is armed by
  mouse direction and committed on release (spring-loaded, ``>= 250 ms``).
- Short presses (``< 250 ms``) pin the menu for click selection (PIN mode):
  left-click an enabled card to trigger, left/right-click the center (or a
  disabled card) to cancel, Esc / click outside to cancel.

Rendering (review 2026-08-10): function labels are drawn as horizontal,
axis-aligned cards at the eight sector directions, matching Blender's pie
menu visual style. Each card reserves space for an optional icon, shows the
command label, and displays the sector number on the right. Sectors with more
than one command fan their cards angularly inside the sector while keeping
each card itself axis-aligned.

The widget is a separate frameless ``Qt.Tool`` window that does not take
focus, so the main window keeps receiving key events (release of the
trigger key, Esc) while it is open.
"""

import os
from math import atan2, cos, degrees, pi, radians, sin

from qtpy.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap, QPolygonF
from qtpy.QtSvg import QSvgRenderer
from qtpy.QtWidgets import QWidget

from utils.config import pcfg
from .context_menu_config import COMMAND_REGISTRY, cmd_enabled
from .misc import get_theme_color
from .theme_helpers import is_dark_theme

# ── Geometry (tuned to match Blender's pie menu proportions) ─
TOTAL_RADIUS = 210.0          # menu outer radius (px)
CENTER_RADIUS = 42.0          # center ring radius
CENTER_DOT_RADIUS = 4.5       # inner dot radius when idle
CARD_R_IN = 84.0              # inner edge of the card selection ring
CARD_R_OUT = 192.0            # outer edge of the card selection ring
CARD_PAD_X = 10.0             # horizontal content padding
CARD_PAD_Y = 6.0              # vertical content padding
ICON_SIZE = 13.0              # icon side length
ICON_MARGIN = 5.0             # space around the icon area
NUM_MARGIN = 6.0              # space around the sector number
NUM_WIDTH_EXTRA = 2.0         # extra horizontal room for the number
FAN_SPACING = 16.0            # degrees between fanned cards of one sector
SECTOR_COUNT = 8
SHORT_PRESS_MS = 250          # hold shorter than this -> PIN mode
DEAD_ZONE_RADIUS = 5.0        # jitter dead zone right at the center
CARD_CORNER_RADIUS = 8.0      # rounded corner radius of a card
CENTER_RING_WIDTH = 1.5       # stroke width of the center ring
SECTOR_WEDGE_ALPHA = 22       # alpha of the hover sector wedge
HOVER_TEXT_COLOR = QColor(255, 255, 255)

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons"
)


def _card_radius() -> float:
    """Radius of the card centers (single radial row for all cards)."""
    return (CARD_R_IN + CARD_R_OUT) / 2.0


def _fan_offsets(k: int) -> list:
    """Angular offsets (clockwise-from-top degrees) for *k* fanned cards."""
    return [(i - (k - 1) / 2.0) * FAN_SPACING for i in range(k)]


class PieMenu(QWidget):
    """Frameless always-on-top ring menu with PIN / release-commit states."""

    command_triggered = Signal(str)   # cmd_id
    canceled = Signal()

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        size = int(2 * TOTAL_RADIUS)
        self.setFixedSize(size, size)

        self._state = "hidden"   # hidden | holding | pin
        self._hover = None       # (sector, card_idx) or None (= center / disabled)
        self._press_timer = QElapsedTimer()
        self._sector_data = self._load_sectors()
        self._icon_cache = {}    # (cmd_id, color_name, alpha, size) -> QPixmap

    # ── State machine ──────────────────────────────────────

    def start_hold(self, global_pos: QPoint):
        """Trigger-key pressed: pop the menu at *global_pos* and begin holding."""
        if self._state != "hidden":
            self.cancel()
        self._sector_data = self._load_sectors()
        self._icon_cache.clear()
        self._hover = None
        self.move(global_pos - QPoint(int(TOTAL_RADIUS), int(TOTAL_RADIUS)))
        self.show()
        self.raise_()
        self._state = "holding"
        self._press_timer.start()

    def release_hold(self):
        """Trigger-key released: spring-loaded commit, or switch to PIN mode."""
        if self._state != "holding":
            return
        if self._press_timer.elapsed() < SHORT_PRESS_MS:
            self._state = "pin"
        else:
            self._commit()

    def cancel(self):
        """Close the menu without triggering (public; also used by MainWindow)."""
        if self._state == "hidden":
            return
        self._state = "hidden"
        self.hide()
        self.canceled.emit()

    def is_open(self) -> bool:
        return self._state in ("holding", "pin")

    def is_holding(self) -> bool:
        return self._state == "holding"

    # ── Internals ──────────────────────────────────────────

    def _load_sectors(self):
        """Normalize ``pcfg.pie_sectors`` to 8 lists of <= 3 ids."""
        data = pcfg.pie_sectors
        if not isinstance(data, list) or len(data) != SECTOR_COUNT:
            data = []
        out = []
        for i in range(SECTOR_COUNT):
            lst = data[i] if i < len(data) and isinstance(data[i], list) else []
            out.append([cid for cid in lst[:3] if isinstance(cid, str)])
        return out

    def _cmd_available(self, cmd_id: str) -> bool:
        return cmd_enabled(self.canvas, cmd_id)

    def _slot_at(self, sector: int, idx: int):
        """Command id at (sector, idx), or None for an empty slot."""
        lst = self._sector_data[sector] if 0 <= sector < SECTOR_COUNT else []
        if 0 <= idx < len(lst):
            return lst[idx]
        return None

    def _card_angle(self, sector: int, idx: int) -> float:
        """Widget-convention angle (radians, 0=right / y-down) of a card center."""
        k = len(self._sector_data[sector])
        off = (idx - (k - 1) / 2.0) * FAN_SPACING
        return radians(-90 + sector * 45 + off)

    def _card_rect(self, sector: int, idx: int, fm: QFontMetrics) -> QRectF:
        """Axis-aligned bounding rect of a card in widget-local coordinates."""
        cmd_id = self._slot_at(sector, idx)
        label = ""
        if cmd_id:
            cmd = COMMAND_REGISTRY.get(cmd_id)
            if cmd is not None:
                label = self.canvas.tr(cmd.label_key)
        text_w = fm.horizontalAdvance(label)
        num_w = fm.horizontalAdvance(str(sector + 1)) + NUM_WIDTH_EXTRA
        cw = (
            2 * CARD_PAD_X
            + 2 * ICON_MARGIN
            + ICON_SIZE
            + text_w
            + 2 * NUM_MARGIN
            + num_w
        )
        ch = max(fm.height(), ICON_SIZE) + 2 * CARD_PAD_Y
        r = _card_radius()
        a = self._card_angle(sector, idx)
        cx = TOTAL_RADIUS + r * cos(a)
        cy = TOTAL_RADIUS + r * sin(a)
        return QRectF(cx - cw / 2, cy - ch / 2, cw, ch)

    def _hit_test(self, local_pos: QPointF):
        """(sector, card_idx) for a widget-local point, or None.

        First check the axis-aligned card rectangles; if the cursor is not
        inside any card, fall back to the angular sector selection used by
        Blender-style pie menus.
        """
        dx = local_pos.x() - TOTAL_RADIUS
        dy = local_pos.y() - TOTAL_RADIUS
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < DEAD_ZONE_RADIUS or dist > TOTAL_RADIUS or dist < CENTER_RADIUS:
            return None

        fm = QFontMetrics(self.font())

        # 1. explicit card hover (axis-aligned cards)
        # Compute cursor angle first; only cards inside the cursor's sector
        # wedge are considered, and the closest card by angle wins. This
        # prevents long cards from neighbouring sectors from stealing hits.
        ang = (atan2(dy, dx) * 180.0 / pi + 450.0) % 360.0   # 0 = top, clockwise
        cursor_sector = int((ang + 22.5) / 45.0) % SECTOR_COUNT
        hits = []
        for sector in range(SECTOR_COUNT):
            if sector != cursor_sector:
                continue
            for idx, _ in enumerate(self._sector_data[sector]):
                rect = self._card_rect(sector, idx, fm)
                if not rect.contains(local_pos):
                    continue
                a = (sector * 45 + _fan_offsets(len(self._sector_data[sector]))[idx]) % 360.0
                d = abs((ang - a + 540.0) % 360.0 - 180.0)
                hits.append((d, sector, idx))
        if hits:
            hits.sort()
            return hits[0][1], hits[0][2]

        # 2. angular fallback (whole sector wedge is hot)
        cards = self._sector_data[cursor_sector]
        if not cards:
            return None
        center_ang = (cursor_sector * 45) % 360.0
        best, best_d = 0, 1e9
        for i, _ in enumerate(cards):
            a = (center_ang + _fan_offsets(len(cards))[i]) % 360.0
            d = abs((ang - a + 540.0) % 360.0 - 180.0)   # circular distance
            if d < best_d:
                best, best_d = i, d
        return (cursor_sector, best)

    def _update_hover(self, hit):
        # Disabled / empty slots behave like the center (unselectable).
        if hit is not None:
            sector, idx = hit
            cmd_id = self._slot_at(sector, idx)
            if not cmd_id or not self._cmd_available(cmd_id):
                hit = None
        if hit != self._hover:
            self._hover = hit
            self.update()

    def _commit(self):
        """Release-commit: trigger the hovered item, or cancel (center/disabled)."""
        hover = self._hover
        if hover is not None:
            sector, idx = hover
            cmd_id = self._slot_at(sector, idx)
            if cmd_id and self._cmd_available(cmd_id):
                self._trigger(cmd_id)
                return
        self.cancel()

    def _trigger(self, cmd_id: str):
        self._state = "hidden"
        self.hide()
        self.command_triggered.emit(cmd_id)

    # ── Mouse ──────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        if self.is_open():
            self._update_hover(self._hit_test(event.position()))
        return super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._state != "pin":
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        hover = self._hit_test(event.position())
        if hover is None:  # center / dead zone -> cancel
            self.cancel()
            return
        sector, idx = hover
        cmd_id = self._slot_at(sector, idx)
        if cmd_id and self._cmd_available(cmd_id):
            self._trigger(cmd_id)
        else:
            self.cancel()   # disabled slot -> same as center (decision #5)

    # ── Painting ───────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(TOTAL_RADIUS, TOTAL_RADIUS)
        dark = is_dark_theme()
        fm = painter.fontMetrics()

        if dark:
            bg = QColor(35, 37, 46, 235)
            ring_c = QColor(130, 135, 150, 220)
            card_bg = QColor(55, 58, 70, 200)
            card_border = QColor(255, 255, 255, 70)
            number_c = QColor(255, 255, 255, 140)
            text_c = get_theme_color(key="@textColor")
        else:
            bg = QColor(245, 246, 250, 250)
            ring_c = QColor(100, 105, 120, 160)
            card_bg = QColor(255, 255, 255, 235)
            card_border = QColor(0, 0, 0, 55)
            number_c = QColor(0, 0, 0, 120)
            text_c = get_theme_color(key="@textColor")
        accent_c = get_theme_color(key="@accentPrimary")

        # menu disc
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(center, TOTAL_RADIUS, TOTAL_RADIUS)

        # hover sector wedge (subtle background highlight)
        if self._hover is not None:
            self._paint_sector_wedge(painter, center, self._hover[0], accent_c)

        # floating command cards
        self._paint_cards(painter, center, fm, card_bg, card_border, text_c,
                          number_c, accent_c)

        # center ring
        pen = QPen(ring_c, CENTER_RING_WIDTH)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, CENTER_RADIUS, CENTER_RADIUS)

        # center dot or direction arrow
        self._paint_center_indicator(painter, center, accent_c)

    def _paint_sector_wedge(self, painter, center, sector, accent_c):
        """Draw a low-alpha pie slice behind the hovered sector."""
        fill = QColor(accent_c)
        fill.setAlpha(SECTOR_WEDGE_ALPHA)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        rect = QRectF(
            center.x() - TOTAL_RADIUS, center.y() - TOTAL_RADIUS,
            2 * TOTAL_RADIUS, 2 * TOTAL_RADIUS,
        )
        # Qt drawPie: start/spam in 1/16 degree, positive = counter-clockwise.
        start_qt = (-112.5 - sector * 45) * 16
        painter.drawPie(rect, int(start_qt), 16 * 45)

    def _load_icon(self, cmd_id: str, color: QColor, size: int) -> QPixmap:
        """Render an SVG icon tinted to *color* (cached)."""
        key = (cmd_id, color.name(), color.alpha(), size)
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        cmd = COMMAND_REGISTRY.get(cmd_id)
        if cmd is None or not cmd.icon:
            return None
        path = os.path.join(_ICON_DIR, cmd.icon)
        if not os.path.exists(path):
            return None
        renderer = QSvgRenderer(path)
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(pm.rect())
        p.end()
        self._icon_cache[key] = pm
        return pm

    def _paint_cards(self, painter, center, fm, card_bg, card_border, text_c,
                     number_c, accent_c):
        for sector in range(SECTOR_COUNT):
            for idx, cmd_id in enumerate(self._sector_data[sector]):
                cmd = COMMAND_REGISTRY.get(cmd_id)
                if cmd is None:
                    continue
                rect = self._card_rect(sector, idx, fm)
                hovered = self._hover == (sector, idx)
                enabled = self._cmd_available(cmd_id)

                # card background / hover highlight
                if hovered:
                    fill = QColor(accent_c)
                    fill.setAlpha(230)
                    painter.setPen(QPen(HOVER_TEXT_COLOR, 1.2))
                    painter.setBrush(fill)
                else:
                    painter.setPen(QPen(card_border, 1.0))
                    painter.setBrush(card_bg)
                painter.drawRoundedRect(rect, CARD_CORNER_RADIUS, CARD_CORNER_RADIUS)

                label = self.canvas.tr(cmd.label_key)
                number = str(sector + 1)

                if hovered:
                    tcolor = HOVER_TEXT_COLOR
                    ncolor = HOVER_TEXT_COLOR
                else:
                    tcolor = QColor(text_c)
                    ncolor = QColor(number_c)
                    if not enabled:
                        tcolor.setAlpha(110)
                        ncolor.setAlpha(110)

                # icon (optional)
                icon = self._load_icon(cmd_id, tcolor, int(ICON_SIZE))
                if icon is not None:
                    ix = rect.x() + CARD_PAD_X + ICON_MARGIN
                    iy = rect.y() + (rect.height() - ICON_SIZE) / 2
                    painter.drawPixmap(int(ix), int(iy), icon)

                # label
                text_x = rect.x() + CARD_PAD_X + ICON_MARGIN + ICON_SIZE + ICON_MARGIN
                baseline = rect.y() + (rect.height() - fm.height()) / 2 + fm.ascent()
                painter.setPen(QPen(tcolor))
                painter.drawText(
                    QPointF(text_x, baseline),
                    label,
                )

                # sector number
                num_w = fm.horizontalAdvance(number) + NUM_WIDTH_EXTRA
                num_x = rect.x() + rect.width() - CARD_PAD_X - NUM_MARGIN - num_w
                painter.setPen(QPen(ncolor))
                painter.drawText(
                    QPointF(num_x, baseline),
                    number,
                )

    def _paint_center_indicator(self, painter, center, accent_c):
        if self._hover is None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(accent_c))
            painter.drawEllipse(center, CENTER_DOT_RADIUS, CENTER_DOT_RADIUS)
            return
        a = self._card_angle(self._hover[0], self._hover[1])
        ux, uy = cos(a), sin(a)

        # directional arrow inside the center ring
        tip = center + QPointF(ux, uy) * (CENTER_RADIUS - 6)
        base = center + QPointF(ux, uy) * (CENTER_RADIUS - 14)
        side = QPointF(-uy, ux) * 5
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent_c))
        painter.drawPolygon(QPolygonF([tip, base + side, base - side]))

        # faint radial line connecting center to the hovered card
        accent_line = QColor(accent_c)
        accent_line.setAlpha(90)
        painter.setPen(QPen(accent_line, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        end = center + QPointF(ux, uy) * _card_radius()
        painter.drawLine(center, end)
