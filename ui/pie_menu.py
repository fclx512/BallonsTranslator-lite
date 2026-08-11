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

from qtpy.QtCore import QCoreApplication, QElapsedTimer, QMimeData, QPoint, QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication, QWidget

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

# ts context used for pie-menu display names (defaults are tr keys).
PIE_MENU_TR_CONTEXT = "PieMenu"


def normalize_pie_menu(menu) -> dict:
    """Coerce a pie-menu config dict into the canonical shape.

    Guarantees: ``sectors`` in (4, 6, 8), ``slots`` has exactly ``sectors``
    lists each truncated to ``SECTOR_MAX_CARDS`` ids.  Unknown ids are kept
    (they render disabled / skipped); unknown commands never crash.
    """
    if not isinstance(menu, dict):
        menu = {}
    sectors = menu.get("sectors", SECTOR_COUNT)
    if sectors not in (4, 6, 8):
        sectors = SECTOR_COUNT
    slots = menu.get("slots", [])
    if not isinstance(slots, list):
        slots = []
    slots = slots[:sectors] + [[] for _ in range(sectors - len(slots[:sectors]))]
    slots = [
        [cid for cid in lst[:SECTOR_MAX_CARDS] if isinstance(cid, str)]
        for lst in slots
    ]
    return {
        "id": menu.get("id", ""),
        "name": menu.get("name", ""),
        "trigger": menu.get("trigger", ""),
        "sectors": sectors,
        "layout": menu.get("layout", "ring"),
        "slots": slots,
    }


def pie_menu_display_name(menu) -> str:
    """Display name of a menu: default names are tr keys, renames pass through
    unchanged (``QCoreApplication.translate`` returns the source when no
    translation exists, so free-text names are safe)."""
    name = menu.get("name", "") if isinstance(menu, dict) else ""
    return QCoreApplication.translate(PIE_MENU_TR_CONTEXT, name) if name else name


class PieMenu(QWidget):
    """Frameless always-on-top ring menu with PIN / release-commit states.

    ``preview=True`` switches to the config-editor mode: an ordinary child
    widget that never commits — mouse moves only drive the hover highlight
    (plus click-select / right-click-remove / drag-drop in edit mode), and
    all rendering stays identical to the runtime menu.
    """

    command_triggered = Signal(str)   # cmd_id
    canceled = Signal()
    # Preview-mode signals (config editor)
    slot_selected = Signal(int, int)          # (sector, idx) left-click on a card
    slot_remove_requested = Signal(int, int)  # (sector, idx) right-click on a card
    command_dropped = Signal(int, int, str, int, int)  # sector, idx, cmd_id, src_sector, src_idx

    def __init__(self, canvas, mw=None, parent=None, preview=False):
        super().__init__(parent)
        self.canvas = canvas
        self.mw = mw  # MainWindow; resolves enabled state via cmd_enabled(mw, ...)
        self._preview = preview
        if not preview:
            # Real menu: frameless always-on-top tool window that does not
            # take focus (the main window keeps receiving key events).
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
        else:
            # Config-page preview: plain child widget, hover-only (no PIN /
            # release-commit — mouse events only drive the hover highlight),
            # accepts drops from the command palette.
            self.setMouseTracking(True)
            self.setAcceptDrops(True)
        size = int(2 * WINDOW_RADIUS)
        self.setFixedSize(size, size)

        self._state = "hidden"   # hidden | holding | pin
        self._hover = None       # (sector, card_idx) or None (= center / disabled)
        self._press_timer = QElapsedTimer()
        self._menu = {}
        self._sector_count = SECTOR_COUNT
        self._sector_data = []
        # Preview-mode state (unused by the runtime menu)
        self._preview_scale = 1.0
        self._edit_mode = False
        self._drop_sector = -1
        self._drop_rejected = False
        self._selected = None    # (sector, idx) clicked card in edit mode
        self._press_pos = None
        self._press_hit = None
        self.set_menu_config(None)

    # ── State machine ──────────────────────────────────────

    def start_hold(self, global_pos: QPoint):
        """Trigger-key pressed: pop the menu at *global_pos* and begin holding."""
        if self._state != "hidden":
            self.cancel()
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

    def set_menu_config(self, menu):
        """Load a *menu* config dict (normalized) into the widget.

        ``None`` falls back to the first entry of ``pcfg.pie_menus`` so
        standalone / test usage works without an explicit menu.
        """
        if menu is None:
            menus = pcfg.pie_menus or []
            menu = menus[0] if menus else {}
        menu = normalize_pie_menu(menu)
        self._menu = menu
        self._sector_count = menu["sectors"]
        self._sector_data = menu["slots"]
        self._hover = None
        self.update()

    def _cmd_available(self, cmd_id: str) -> bool:
        if self._preview:
            return True   # editor preview: everything looks enabled
        return cmd_enabled(self.mw or self.canvas, cmd_id)

    def _label_for(self, cmd_id: str) -> str:
        """Translated label of a command (runtime uses canvas.tr, preview
        resolves the Canvas context directly)."""
        cmd = COMMAND_REGISTRY.get(cmd_id)
        if cmd is None:
            return ""
        if self.canvas is not None:
            return self.canvas.tr(cmd.label_key)
        return QCoreApplication.translate("Canvas", cmd.label_key)

    def _slot_at(self, sector: int, idx: int):
        """Command id at (sector, idx), or None for an empty slot."""
        lst = self._sector_data[sector] if 0 <= sector < self._sector_count else []
        if 0 <= idx < len(lst):
            return lst[idx]
        return None

    def _card_center(self, sector: int, idx: int, card_h: float):
        """Widget-local center of a card (tangential stacking for k > 1)."""
        span = 360.0 / self._sector_count
        base = radians(-90 + sector * span)
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
        label = self._label_for(cmd_id) if cmd_id else ""
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
        span = 360.0 / self._sector_count
        sector = int((ang + span / 2.0) / span) % self._sector_count
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

    # ── Preview mode (config editor) ─────────────────────────

    def set_preview_scale(self, scale: float):
        """Uniform scale for the embedded preview (paint transform only —
        all geometry stays in logical WINDOW_RADIUS coordinates)."""
        self._preview_scale = scale
        self.setFixedSize(int(2 * WINDOW_RADIUS * scale),
                          int(2 * WINDOW_RADIUS * scale))
        self.update()

    def set_edit_mode(self, enabled: bool):
        """Show dashed sector guides + selection ring; enables click-select /
        right-click-remove / drag-drop for the config editor."""
        self._edit_mode = enabled
        if not enabled:
            self._drop_sector = -1
            self._selected = None
        self.update()

    def set_drop_target(self, sector: int, rejected: bool = False):
        """Highlight a sector as the current drop target (red when full)."""
        self._drop_sector = sector
        self._drop_rejected = rejected
        self.update()

    def sector_at(self, local_pos) -> int:
        """Sector index under *local_pos* (logical coords), or -1 outside."""
        dx = local_pos.x() - WINDOW_RADIUS
        dy = local_pos.y() - WINDOW_RADIUS
        dist = hypot(dx, dy)
        if dist < DEAD_ZONE_RADIUS or dist > WINDOW_RADIUS or dist < CENTER_RADIUS:
            return -1
        ang = (atan2(dy, dx) * 180.0 / pi + 450.0) % 360.0
        span = 360.0 / self._sector_count
        return int((ang + span / 2.0) / span) % self._sector_count

    def _drop_insert_index(self, sector: int, local_pos) -> int:
        """Insertion index (0..k) among a sector's stacked cards for a drop
        at *local_pos*, derived from the drop's tangential coordinate."""
        cards = self._sector_data[sector]
        k = len(cards)
        if k == 0:
            return 0
        fm = QFontMetrics(self.font())
        ch = fm.height() + 2 * CARD_PAD_Y
        span = 360.0 / self._sector_count
        base = radians(-90 + sector * span)
        bx = WINDOW_RADIUS + CARD_RADIUS * cos(base)
        by = WINDOW_RADIUS + CARD_RADIUS * sin(base)
        tx, ty = -sin(base), cos(base)
        rel = (local_pos.x() - bx) * tx + (local_pos.y() - by) * ty
        idx = int(round(rel / (ch + CARD_STACK_GAP) + (k - 1) / 2.0))
        return max(0, min(idx, k))

    def _start_card_drag(self, sector: int, idx: int, cmd_id: str, pos):
        drag = QDrag(self)
        md = QMimeData()
        md.setData("application/x-pie-cmd", cmd_id.encode("utf-8"))
        md.setData("application/x-pie-src", f"{sector},{idx}".encode("utf-8"))
        drag.setMimeData(md)
        drag.exec(Qt.DropAction.MoveAction)
        # the drag ended: repaint (drop target may still be highlighted)
        self._drop_sector = -1
        self.update()

    # ── Mouse ──────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        if self._preview or self.is_open():
            self._update_hover(self._hit_test(event.position() / self._preview_scale))
        if self._preview and self._press_hit is not None and self._press_pos is not None:
            # drag threshold reached → start a card drag (move / reorder)
            if (event.position() - self._press_pos).manhattanLength() \
                    > QApplication.startDragDistance():
                sector, idx = self._press_hit
                cmd_id = self._slot_at(sector, idx)
                self._press_hit = None
                if cmd_id:
                    self._start_card_drag(sector, idx, cmd_id, event.position())
        return super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._preview:
            pos = event.position() / self._preview_scale
            hit = self._hit_test(pos)
            if event.button() == Qt.MouseButton.RightButton:
                if hit is not None:
                    sector, idx = hit
                    cmd_id = self._slot_at(sector, idx)
                    if cmd_id:
                        self.slot_remove_requested.emit(sector, idx)
                return
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_pos = event.position()
                self._press_hit = hit
            return
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

    def mouseReleaseEvent(self, event):
        if self._preview and event.button() == Qt.MouseButton.LeftButton:
            # click (no drag started) on a card → select it in edit mode
            if self._press_hit is not None:
                sector, idx = self._press_hit
                if self._slot_at(sector, idx):
                    self._selected = (sector, idx)
                    self.slot_selected.emit(sector, idx)
                    self.update()
            self._press_hit = None
            self._press_pos = None
            return
        return super().mouseReleaseEvent(event)

    # ── Drag & drop (preview mode: palette -> menu, menu -> menu, menu -> palette) ──

    def _drop_info(self, mime):
        """(cmd_id, src_sector, src_idx) from a drag mime, or (None, -1, -1)."""
        if not mime.hasFormat("application/x-pie-cmd"):
            return None, -1, -1
        cmd_id = bytes(mime.data("application/x-pie-cmd")).decode("utf-8")
        if mime.hasFormat("application/x-pie-src"):
            try:
                s, i = bytes(mime.data("application/x-pie-src")).decode("utf-8").split(",")
                return cmd_id, int(s), int(i)
            except ValueError:
                pass
        return cmd_id, -1, -1

    def _drop_ok(self, sector: int, src_sector: int) -> bool:
        """A drop into *sector* fits: internal reorders keep the count,
        additions/moves into a full sector are rejected."""
        if sector < 0:
            return False
        if src_sector == sector:
            return True
        return len(self._sector_data[sector]) < SECTOR_MAX_CARDS

    def dragEnterEvent(self, event):
        if self._preview and event.mimeData().hasFormat("application/x-pie-cmd"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._preview and event.mimeData().hasFormat("application/x-pie-cmd"):
            _, src_sector, _ = self._drop_info(event.mimeData())
            sector = self.sector_at(event.position() / self._preview_scale)
            ok = self._drop_ok(sector, src_sector)
            self.set_drop_target(sector, rejected=not ok)
            if ok:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        if self._preview:
            self.set_drop_target(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not self._preview or not event.mimeData().hasFormat("application/x-pie-cmd"):
            return super().dropEvent(event)
        cmd_id, src_sector, src_idx = self._drop_info(event.mimeData())
        pos = event.position() / self._preview_scale
        sector = self.sector_at(pos)
        self.set_drop_target(-1)
        if sector < 0 or not self._drop_ok(sector, src_sector):
            event.ignore()
            return
        idx = self._drop_insert_index(sector, pos)
        if src_sector == sector and 0 <= src_idx < idx:
            idx -= 1   # internal move: source removal shifts the target
        event.acceptProposedAction()
        self.command_dropped.emit(sector, idx, cmd_id, src_sector, src_idx)

    # ── Painting ───────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._preview_scale != 1.0:
            painter.scale(self._preview_scale, self._preview_scale)
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

        if self._edit_mode:
            self._paint_edit_guides(painter, center, text_c)

        # floating command cards
        self._paint_cards(painter, fm, card_bg, card_border, text_c,
                          number_c, accent_c)

        # drop-target highlight (config editor, dragging over the menu)
        if self._edit_mode and self._drop_sector >= 0:
            self._paint_drop_target(painter, center, accent_c)

        # menu title above the center ring
        self._paint_title(painter, center, fm, text_c)

        # center ring + hovered-sector fill
        self._paint_center_indicator(painter, center, ring_c, accent_c)

    def _paint_edit_guides(self, painter, center, text_c):
        """Dashed radial sector separators so empty sectors read as drop targets."""
        c = QColor(text_c)
        c.setAlpha(45)
        painter.setPen(QPen(c, 1.0, Qt.PenStyle.DashLine))
        span = 360.0 / self._sector_count
        for i in range(self._sector_count):
            a = radians(-90 + i * span)
            x1 = center.x() + CENTER_RADIUS * cos(a)
            y1 = center.y() + CENTER_RADIUS * sin(a)
            x2 = center.x() + (WINDOW_RADIUS - 4) * cos(a)
            y2 = center.y() + (WINDOW_RADIUS - 4) * sin(a)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _paint_drop_target(self, painter, center, accent_c):
        """Translucent wedge over the drop-target sector (red when full)."""
        span = 360.0 / self._sector_count
        start_deg = 90 - span / 2.0 - self._drop_sector * span
        if self._drop_rejected:
            fill = QColor(220, 60, 60, 90)
        else:
            fill = QColor(accent_c)
            fill.setAlpha(70)
        path = QPainterPath()
        path.moveTo(center)
        path.arcTo(
            QRectF(center.x() - (WINDOW_RADIUS - 2), center.y() - (WINDOW_RADIUS - 2),
                   2 * (WINDOW_RADIUS - 2), 2 * (WINDOW_RADIUS - 2)),
            start_deg, span,
        )
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(path)

    def _paint_title(self, painter, center, fm, text_c):
        title = pie_menu_display_name(self._menu) or self.tr("Actions")
        title_w = fm.horizontalAdvance(title)
        x = center.x() - title_w / 2
        y = center.y() - CENTER_RADIUS - TITLE_OFFSET_Y + fm.ascent()
        c = QColor(text_c)
        c.setAlpha(204)   # slightly dimmed so it doesn't compete with cards
        painter.setPen(QPen(c))
        painter.drawText(QPointF(x, y), title)

    def _paint_cards(self, painter, fm, card_bg, card_border, text_c,
                     number_c, accent_c):
        for sector in range(self._sector_count):
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

                # selection ring (config editor)
                if self._edit_mode and self._selected == (sector, idx):
                    sel_pen = QPen(QColor(accent_c), 1.2, Qt.PenStyle.DashLine)
                    painter.setPen(sel_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2),
                                            CARD_CORNER_RADIUS, CARD_CORNER_RADIUS)

                label = self._label_for(cmd_id)
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
        span = 360.0 / self._sector_count
        # Qt: 0 deg = 3 o'clock, positive CCW; sector 0 is at the top (90 deg).
        start_deg = 90 - span / 2.0 - sector * span
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
