"""
Pie menu (Blender-style ring menu) for the canvas.

Interaction (see ``docs/技术实现/环形菜单_Blender样式复刻方案.md``):

- Tab (held) pops the menu at the cursor; the hovered command is armed by
  mouse direction and committed on release (spring-loaded, ``>= 250 ms``).
- Short presses (``< 250 ms``) pin the menu for click selection (PIN mode):
  left-click an enabled card to trigger, left/right-click the center (or a
  disabled card) to cancel, Esc / click outside to cancel.

Rendering (Blender restyle 2026-08-11): no background disc — the window is
fully transparent except for the floating cards, the center ring and the
menu title. Cards are horizontal, axis-aligned labels with the sector
number on the right; sectors with multiple commands stack their cards
tangentially (perpendicular to the sector radius) instead of fanning them
angularly, so stacked cards never overlap. The center indicator is a thin
ring plus a hollow sector fill pointing at the hovered sector.

The widget is a separate frameless ``Qt.Tool`` window that does not take
focus, so the main window keeps receiving key events (release of the
trigger key, Esc) while it is open.
"""

from math import atan2, cos, hypot, pi, radians, sin

from qtpy.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QWidget

from utils.config import pcfg
from .context_menu_config import COMMAND_REGISTRY, cmd_enabled
from .misc import get_theme_color
from .theme_helpers import is_dark_theme

# ── Geometry (tuned to match Blender's pie menu proportions) ─
TOTAL_RADIUS = 210.0          # logical menu radius (px)
WINDOW_MARGIN = 40.0          # transparent margin so stacked cards are never clipped
WINDOW_RADIUS = TOTAL_RADIUS + WINDOW_MARGIN  # widget half-size / drawing center
CENTER_RADIUS = 42.0          # center ring radius
CENTER_INNER_RADIUS = 22.0    # inner radius of the center sector fill (hollow middle)
CARD_RADIUS = 142.0           # fixed radius of the card centers
CARD_PAD_X = 8.0              # horizontal content padding
CARD_PAD_Y = 4.0              # vertical content padding
NUM_MARGIN = 8.0              # space around the sector number
NUM_WIDTH_EXTRA = 2.0         # extra horizontal room for the number
CARD_STACK_GAP = 4.0          # tangential gap between stacked cards of one sector
SECTOR_COUNT = 8
SECTOR_MAX_CARDS = 3          # cards per sector (config load truncates to this)
SHORT_PRESS_MS = 250          # hold shorter than this -> PIN mode
DEAD_ZONE_RADIUS = 5.0        # jitter dead zone right at the center
CARD_CORNER_RADIUS = 5.0      # rounded corner radius of a card
CENTER_RING_WIDTH = 2.0       # stroke width of the center ring
TITLE_OFFSET_Y = 28.0         # title baseline distance above the center ring
HOVER_TEXT_COLOR = QColor(255, 255, 255)


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
        size = int(2 * WINDOW_RADIUS)
        self.setFixedSize(size, size)

        self._state = "hidden"   # hidden | holding | pin
        self._hover = None       # (sector, card_idx) or None (= center / disabled)
        self._press_timer = QElapsedTimer()
        self._sector_data = self._load_sectors()

    # ── State machine ──────────────────────────────────────

    def start_hold(self, global_pos: QPoint):
        """Trigger-key pressed: pop the menu at *global_pos* and begin holding."""
        if self._state != "hidden":
            self.cancel()
        self._sector_data = self._load_sectors()
        self._hover = None
        self.move(global_pos - QPoint(int(WINDOW_RADIUS), int(WINDOW_RADIUS)))
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
        """Normalize ``pcfg.pie_sectors`` to 8 lists of <= SECTOR_MAX_CARDS ids."""
        data = pcfg.pie_sectors
        if not isinstance(data, list) or len(data) != SECTOR_COUNT:
            data = []
        out = []
        for i in range(SECTOR_COUNT):
            lst = data[i] if i < len(data) and isinstance(data[i], list) else []
            out.append([cid for cid in lst[:SECTOR_MAX_CARDS] if isinstance(cid, str)])
        return out

    def _cmd_available(self, cmd_id: str) -> bool:
        return cmd_enabled(self.canvas, cmd_id)

    def _slot_at(self, sector: int, idx: int):
        """Command id at (sector, idx), or None for an empty slot."""
        lst = self._sector_data[sector] if 0 <= sector < SECTOR_COUNT else []
        if 0 <= idx < len(lst):
            return lst[idx]
        return None

    def _card_center(self, sector: int, idx: int, card_h: float):
        """Widget-local center of a card (tangential stacking for k > 1)."""
        base = radians(-90 + sector * 45)
        bx = WINDOW_RADIUS + CARD_RADIUS * cos(base)
        by = WINDOW_RADIUS + CARD_RADIUS * sin(base)
        k = len(self._sector_data[sector])
        if k > 1:
            # stack along the tangent (perpendicular to the sector radius):
            # left/right sectors stack vertically, top/bottom horizontally
            off = (idx - (k - 1) / 2.0) * (card_h + CARD_STACK_GAP)
            bx += off * -sin(base)
            by += off * cos(base)
        return bx, by

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
        cw = 2 * CARD_PAD_X + text_w + 2 * NUM_MARGIN + num_w
        ch = fm.height() + 2 * CARD_PAD_Y
        cx, cy = self._card_center(sector, idx, ch)
        # The transparent window edge clips silently — keep cards fully inside.
        cx = min(max(cx, cw / 2), 2 * WINDOW_RADIUS - cw / 2)
        cy = min(max(cy, ch / 2), 2 * WINDOW_RADIUS - ch / 2)
        return QRectF(cx - cw / 2, cy - ch / 2, cw, ch)

    def _hit_test(self, local_pos: QPointF):
        """(sector, card_idx) for a widget-local point, or None.

        The cursor angle selects the sector; inside that sector the nearest
        card wins (tangential stacking means same-angle cards are told apart
        by distance, not angle). If the cursor is not on any card, the whole
        sector wedge stays hot and the nearest card is armed — the
        Blender-style directional fallback used by release-commit flicks.
        """
        dx = local_pos.x() - WINDOW_RADIUS
        dy = local_pos.y() - WINDOW_RADIUS
        dist = hypot(dx, dy)
        if dist < DEAD_ZONE_RADIUS or dist > WINDOW_RADIUS or dist < CENTER_RADIUS:
            return None

        ang = (atan2(dy, dx) * 180.0 / pi + 450.0) % 360.0   # 0 = top, clockwise
        sector = int((ang + 22.5) / 45.0) % SECTOR_COUNT
        cards = self._sector_data[sector]
        if not cards:
            return None

        fm = QFontMetrics(self.font())

        def _nearest(idxs):
            best, best_d = None, 1e18
            for i in idxs:
                c = self._card_rect(sector, i, fm).center()
                d = (c.x() - local_pos.x()) ** 2 + (c.y() - local_pos.y()) ** 2
                if d < best_d:
                    best, best_d = i, d
            return best

        # 1. explicit card hover
        on_cards = [
            i for i in range(len(cards))
            if self._card_rect(sector, i, fm).contains(local_pos)
        ]
        if on_cards:
            return (sector, _nearest(on_cards))

        # 2. directional fallback (whole sector wedge is hot)
        return (sector, _nearest(range(len(cards))))

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
        center = QPointF(WINDOW_RADIUS, WINDOW_RADIUS)
        dark = is_dark_theme()
        fm = painter.fontMetrics()

        if dark:
            ring_c = QColor(130, 135, 150, 220)
            card_bg = QColor(35, 37, 46, 200)
            number_c = QColor(255, 255, 255, 140)
        else:
            ring_c = QColor(100, 105, 120, 160)
            card_bg = QColor(255, 255, 255, 220)
            number_c = QColor(0, 0, 0, 120)
        text_c = get_theme_color(key="@textColor")
        accent_c = get_theme_color(key="@accentPrimary")
        card_border = QColor(accent_c)
        card_border.setAlpha(160)

        # floating command cards
        self._paint_cards(painter, fm, card_bg, card_border, text_c,
                          number_c, accent_c)

        # menu title above the center ring
        self._paint_title(painter, center, fm, text_c)

        # center ring + hovered-sector fill
        self._paint_center_indicator(painter, center, ring_c, accent_c)

    def _paint_title(self, painter, center, fm, text_c):
        title = self.tr("Actions")
        title_w = fm.horizontalAdvance(title)
        x = center.x() - title_w / 2
        y = center.y() - CENTER_RADIUS - TITLE_OFFSET_Y + fm.ascent()
        c = QColor(text_c)
        c.setAlpha(204)   # slightly dimmed so it doesn't compete with cards
        painter.setPen(QPen(c))
        painter.drawText(QPointF(x, y), title)

    def _paint_cards(self, painter, fm, card_bg, card_border, text_c,
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
                    fill.setAlpha(255)
                    painter.setPen(QPen(HOVER_TEXT_COLOR, 1.5))
                    painter.setBrush(fill)
                else:
                    painter.setPen(QPen(card_border, 1.5))
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

                baseline = rect.y() + (rect.height() - fm.height()) / 2 + fm.ascent()

                # label (left-aligned)
                painter.setPen(QPen(tcolor))
                painter.drawText(QPointF(rect.x() + CARD_PAD_X, baseline), label)

                # sector number (right-aligned)
                num_w = fm.horizontalAdvance(number) + NUM_WIDTH_EXTRA
                num_x = rect.x() + rect.width() - CARD_PAD_X - num_w
                painter.setPen(QPen(ncolor))
                painter.drawText(QPointF(num_x, baseline), number)

    def _paint_center_indicator(self, painter, center, ring_c, accent_c):
        # thin outer ring
        painter.setPen(QPen(ring_c, CENTER_RING_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, CENTER_RADIUS, CENTER_RADIUS)

        if self._hover is None:
            return

        # hollow sector fill pointing at the hovered sector:
        # pie sector minus the inner disc -> annular sector
        sector = self._hover[0]
        outer_r = CENTER_RADIUS - 3
        # Qt: 0 deg = 3 o'clock, positive CCW; sector 0 is at the top (90 deg).
        start_deg = 67.5 - sector * 45
        outer = QPainterPath()
        outer.moveTo(center)
        outer.arcTo(
            QRectF(center.x() - outer_r, center.y() - outer_r,
                   2 * outer_r, 2 * outer_r),
            start_deg, 45.0,
        )
        outer.closeSubpath()
        inner = QPainterPath()
        inner.addEllipse(center, CENTER_INNER_RADIUS, CENTER_INNER_RADIUS)

        fill = QColor(accent_c)
        fill.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(outer.subtracted(inner))
