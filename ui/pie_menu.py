"""
Quick menu (ring + vertical list) for the canvas.

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
perpendicular to the sector radius.  Left/right sectors fan tangentially
(vertically); top/bottom sectors would fan horizontally and bury the wide
cards' text, so they stack screen-vertically instead (2026-08-14) — a
stack never overlaps itself.  The center indicator is a thin ring plus a
hollow sector fill pointing at the hovered sector.

Vertical list style (2026-08-12, half-ring redesign): the ring is cut
vertically and each lateral sector position hosts one small continuous
context-menu panel (touching rows, no card gaps) — five fixed anchor
positions per side (top / upper-diagonal / lateral / lower-diagonal /
bottom; the poles were added 2026-08-14 so ring -> list keeps the top and
bottom sectors).  Data model: ``panels`` — 5 groups x up to 3 commands.
Shares the whole state machine with the ring.  See
``docs/技术实现/快捷菜单_竖排样式_设计与交接.md``.

The widget is a separate frameless ``Qt.Tool`` window that does not take
focus, so the main window keeps receiving key events (release of the
trigger key, Esc) while it is open.
"""

from math import atan2, cos, hypot, pi, radians, sin

from qtpy.QtCore import (QCoreApplication, QEasingCurve, QElapsedTimer,
                         QEvent, QMimeData, QPoint, QPointF, QRectF, Qt,
                         QTimer, Signal)
from qtpy.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication, QWidget

from utils.config import pcfg
from .context_menu_config import (
    COMMAND_REGISTRY,
    SEPARATOR_SENTINEL,
    cmd_checked,
    cmd_enabled,
)
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
CARD_STACK_GAP = 4.0          # gap between stacked cards of one sector
CHECKBOX_SIZE = 12.0          # toggle-command checkbox side length
CHECKBOX_GAP = 6.0            # checkbox -> label gap
CHECKBOX_RADIUS = 3.0         # checkbox corner radius
SECTOR_COUNT = 8
SECTOR_MAX_CARDS = 3          # cards per sector (config load truncates to this)
SHORT_PRESS_MS = 250          # hold shorter than this -> PIN mode
DEAD_ZONE_RADIUS = 5.0        # jitter dead zone right at the center
CARD_CORNER_RADIUS = 8.0      # rounded corner radius of a card
CENTER_RING_WIDTH = 2.0       # stroke width of the center ring
TITLE_OFFSET_Y = 28.0         # title baseline distance above the center ring
HOVER_TEXT_COLOR = QColor(255, 255, 255)

# ── Vertical list layout (quick-menu "List" style) ─────────
# Half-ring of context-menu panels: the ring is cut vertically and each
# lateral sector position hosts one small continuous panel (standard
# context-menu look — touching rows, no card gaps).  Grouping happens at
# the menu level: one menu = one function group, split into new menus when
# you want separation (no separators inside a panel — decision 2026-08-12).
# 5 anchors per side since 2026-08-14: the top/bottom poles were added so
# ring -> list no longer drops the top/bottom sectors (the conversion picks
# the whole lateral half including the poles).
LIST_PANELS = 5               # anchor positions per side (top / upper-diag / lateral / lower-diag / bottom)
LIST_PANEL_MAX_ITEMS = 3      # rows per panel (matches SECTOR_MAX_CARDS)
# The cluster hugs the cursor (2026-08-13): the ring's 120px radius left the
# panels far from the pointer — a ring can be aimed by direction, a vertical
# list needs direct pointer travel.  Lateral panel left edge sits
# LIST_ANCHOR_GAP_X right of the cursor (vertically centered); the diagonal
# panels inset LIST_DIAG_INSET further left and the poles LIST_POLE_INSET,
# so the five panels sweep a ring-like arc (10 / 5 / 0 px from the cursor).
# The diagonal panels clear the lateral panel vertically by LIST_ANCHOR_GAP_Y
# (computed from its actual height, so the panels never overlap whatever
# their row counts).
LIST_ANCHOR_GAP_X = 10.0      # cursor -> lateral panel left edge
LIST_ANCHOR_GAP_Y = 6.0       # vertical clearance: diagonal vs lateral panel
LIST_DIAG_INSET = 5.0         # diagonal panels inset from the lateral one
LIST_POLE_INSET = 10.0        # pole panels inset further (ring-like arc)
LIST_PANEL_RADIUS = 6.0       # outer corner radius of a panel
LIST_ROW_H = 26.0             # single command row height
LIST_PAD_X = 14.0             # horizontal padding inside a panel
LIST_PAD_Y = 4.0              # top/bottom padding inside a panel
LIST_MIN_W = 120.0            # panel width floor (short labels)
LIST_MAX_W = 240.0            # panel width cap (longer labels elide)
LIST_MARGIN = 8.0             # transparent margin around the panel bbox

# ── Ring-card text width & menu pop-in animation ────────────
# Long labels (English UI) cap the ring-card width at the same value as the
# list panels (LIST_MAX_W) so pipeline names like "OCR and translate" stay
# fully visible; only longer labels elide, and the hovered card expands
# with a short animation to its full width (drawn last, on top — decision
# 2026-08-14).  The menu itself pops in with a fade + slight scale.  Both
# animations skip when ``pcfg.animation_fps < 0`` (project convention).
CARD_MAX_W = 240.0      # ring-card width cap (== list panel cap); longer labels elide until hovered
CARD_ANIM_MS = 140      # hover expand / collapse duration (ms)
OPEN_ANIM_MS = 140      # menu pop-in duration (ms)
OPEN_SCALE_FROM = 0.92  # pop-in starts slightly scaled down

# ts context used for pie-menu display names (defaults are tr keys).
PIE_MENU_TR_CONTEXT = "PieMenu"


def _anim_interval() -> int:
    """Frame interval (ms) honoring ``pcfg.animation_fps``, else 16 ms
    (60 fps) — same convention as overlay_modal._detect_interval."""
    fps = pcfg.animation_fps
    if fps > 0:
        return int(round(1000.0 / fps))
    try:
        from qtpy.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return 16
        screens = app.screens()
        if not screens:
            return 16
        hz = screens[0].refreshRate()
        if hz <= 0:
            return 16
        return max(16, int(round(1000.0 / (hz + 10))))
    except Exception:
        return 16


def _elide_fitting(fm: QFontMetrics, label: str, avail: float) -> str:
    """Return *label* unchanged when it fits within *avail*, else an
    ElideRight copy.  Eliding at exactly the measured width is not safe:
    on fonts whose real (fractional) advance rounds down (e.g. 9pt
    Microsoft YaHei) ``elidedText`` truncates a label that fits — "OCR"
    became "O…" — so the fit check uses the same integer advance and only
    elides on a genuine overflow.
    """
    if not label:
        return label
    if fm.horizontalAdvance(label) <= avail:
        return label
    return fm.elidedText(label, Qt.TextElideMode.ElideRight,
                         max(0, int(avail)))


def normalize_pie_menu(menu) -> dict:
    """Coerce a quick-menu config dict into the canonical shape.

    Ring layout: ``sectors`` in (4, 6, 8), ``slots`` has exactly ``sectors``
    lists each truncated to ``SECTOR_MAX_CARDS`` ids.  List layout:
    ``panels`` has exactly ``LIST_PANELS`` lists each truncated to
    ``LIST_PANEL_MAX_ITEMS`` ids and ``direction`` is ``"left"`` /
    ``"right"``.  Both representations are kept and normalized so switching
    styles is cheap.  Unknown ids are kept (they render disabled /
    skipped); unknown commands never crash.
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
    layout = menu.get("layout", "ring")
    if layout not in ("ring", "list"):
        layout = "ring"
    direction = menu.get("direction", "right")
    if direction not in ("left", "right"):
        direction = "right"
    panels = menu.get("panels", [])
    if not isinstance(panels, list):
        panels = []
    if not panels:
        # migrate the retired flat ``items`` list (chunked by panel capacity)
        legacy = menu.get("items", [])
        if isinstance(legacy, list) and legacy:
            panels = [legacy[i:i + LIST_PANEL_MAX_ITEMS]
                      for i in range(0, len(legacy), LIST_PANEL_MAX_ITEMS)]
    panels = (panels + [[] for _ in range(LIST_PANELS)])[:LIST_PANELS]
    panels = [
        [cid for cid in lst[:LIST_PANEL_MAX_ITEMS]
         if isinstance(cid, str) and cid and cid != SEPARATOR_SENTINEL]
        if isinstance(lst, list) else []
        for lst in panels
    ]
    return {
        "id": menu.get("id", ""),
        "name": menu.get("name", ""),
        "trigger": menu.get("trigger", ""),
        "sectors": sectors,
        "layout": layout,
        "slots": slots,
        "direction": direction,
        "panels": panels,
    }


def half_ring_sector_idxs(n: int, direction: str = "right") -> list:
    """Ring sector indices of the lateral half, ordered top -> bottom.

    Includes the top/bottom poles (they belong to both halves; the
    ``direction`` only picks which diagonal/left-right sectors sit between
    them).  Used by both conversion functions so ring <-> list round-trips
    map the same sectors.
    """
    if direction == "left":
        return [0] + list(range(n - 1, n // 2 - 1, -1))
    return list(range(0, n // 2 + 1))


def slots_to_panels(slots, direction="right") -> list:
    """Pick the lateral half of ring slots as list panels (top -> bottom)."""
    n = len(slots)
    idxs = half_ring_sector_idxs(n, direction) if n else []
    picked = [list(slots[i]) if 0 <= i < n and isinstance(slots[i], list)
              else [] for i in idxs]
    picked = (picked + [[] for _ in range(LIST_PANELS)])[:LIST_PANELS]
    return [lst[:LIST_PANEL_MAX_ITEMS] for lst in picked]


def panels_to_slots(panels, direction="right", sectors=SECTOR_COUNT) -> list:
    """Write list panels back into a ring slot layout (lateral half)."""
    slots = [[] for _ in range(sectors)]
    for panel, slot_i in zip(panels, half_ring_sector_idxs(sectors, direction)):
        if isinstance(panel, list):
            slots[slot_i] = list(panel)[:SECTOR_MAX_CARDS]
    return slots


def pie_menu_display_name(menu) -> str:
    """Display name of a menu: default names are tr keys, renames pass through
    unchanged (``QCoreApplication.translate`` returns the source when no
    translation exists, so free-text names are safe)."""
    name = menu.get("name", "") if isinstance(menu, dict) else ""
    return QCoreApplication.translate(PIE_MENU_TR_CONTEXT, name) if name else name


class PieMenu(QWidget):
    """Frameless always-on-top quick menu with PIN / release-commit states.

    Two layouts, selected per menu via ``layout``: ``"ring"`` (Blender-style
    sectors around the cursor) and ``"list"`` (a vertical card column beside
    the cursor, direction left/right).  The state machine, window flags and
    signals are shared — only geometry / hit-testing / painting branch.

    ``preview=True`` switches to the config-editor mode: an ordinary child
    widget that never commits — mouse moves only drive the hover highlight
    (plus click-select / right-click-remove / drag-drop in edit mode), and
    all rendering stays identical to the runtime menu.
    """

    command_triggered = Signal(str)   # cmd_id
    canceled = Signal()
    # Preview-mode signals (config editor).  For the list layout the
    # "sector" argument carries the panel index (0..LIST_PANELS-1).
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
        self._hover = None       # (sector, card_idx) / (panel, row) or None
        self._press_timer = QElapsedTimer()
        self._menu = {}
        self._sector_count = SECTOR_COUNT
        self._sector_data = []
        self._list_panels = []   # list-layout command id groups (3 panels)
        self._list_rects = []    # per-panel QRectF (widget-local, logical)
        self._list_cursor = QPointF(0.0, 0.0)  # widget-local cursor position
        self._list_panel_w = LIST_MIN_W        # uniform panel width
        self._list_w = LIST_MIN_W  # list-layout logical window width
        self._list_h = 0.0         # list-layout logical window height
        # Preview-mode state (unused by the runtime menu)
        self._preview_scale = 1.0
        self._edit_mode = False
        self._drop_sector = -1
        self._drop_row = -1      # list-layout drop insertion row
        self._drop_rejected = False
        self._selected = None    # (sector, idx) clicked card in edit mode
        self._press_pos = None
        self._press_hit = None
        # Animation state — hover card expand/collapse + menu pop-in
        # (skipped when ``pcfg.animation_fps < 0``, see module constants).
        self._card_progress: dict = {}   # (sector, idx) -> 0..1 expand progress
        self._card_anim: dict = {}       # (sector, idx) -> (start, target, t0)
        self._open_progress = 1.0        # 0..1 pop-in (runtime menus only)
        self._open_anim = None           # (start, target, t0) or None
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_elapsed = QElapsedTimer()
        self._card_easing = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_easing = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_interval = _anim_interval()
        self.set_menu_config(None)

    # ── State machine ──────────────────────────────────────

    def start_hold(self, global_pos: QPoint):
        """Trigger-key pressed: pop the menu at *global_pos* and begin holding."""
        if self._state != "hidden":
            self.cancel()
        self._hover = None
        self._reset_anim()
        if self._is_list():
            self._move_list_at(global_pos)
        else:
            self.move(global_pos - QPoint(int(WINDOW_RADIUS), int(WINDOW_RADIUS)))
        self.show()
        self.raise_()
        self._state = "holding"
        self._press_timer.start()
        self._start_open_anim()

    def _move_list_at(self, global_pos: QPoint):
        """Place the panel window so the cursor lands on its anchor point,
        then clamp the widget inside the screen's available geometry."""
        x = global_pos.x() - self._list_cursor.x()
        y = global_pos.y() - self._list_cursor.y()
        w, h = self.width(), self.height()
        scr = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            x = min(max(x, g.left() + 4.0), max(g.left() + 4.0, g.right() - w - 4.0))
            y = min(max(y, g.top() + 4.0), max(g.top() + 4.0, g.bottom() - h - 4.0))
        self.move(QPoint(int(x), int(y)))

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
        self._anim_timer.stop()
        self._card_anim.clear()
        self._open_anim = None
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
        self._list_panels = menu["panels"]
        self._hover = None
        self._card_progress.clear()
        self._card_anim.clear()
        if self._is_list():
            self._relayout_list()
        self._sync_fixed_size()
        self.updateGeometry()
        self.update()

    def _sync_fixed_size(self):
        """Resize the widget to the current layout's logical size (x preview
        scale).  Must run on every layout switch — a list-sized window would
        otherwise clip the ring (its center sits outside the small rect,
        so a previously-list runtime menu rendered fully transparent)."""
        if self._is_list():
            self.setFixedSize(int(self._list_w * self._preview_scale),
                              int(self._list_h * self._preview_scale))
        else:
            size = int(2 * WINDOW_RADIUS * self._preview_scale)
            self.setFixedSize(size, size)

    # ── List layout helpers ────────────────────────────────

    def _is_list(self) -> bool:
        return self._menu.get("layout") == "list"

    def _relayout_list(self):
        """Recompute panel rects / window size from the current font.

        Five anchor positions on the configured side of the cursor
        (top / upper-diagonal / lateral / lower-diagonal / bottom); each
        panel is a small continuous context menu.  All five panels always
        get a rect (empty ones collapse to a one-row ghost) so the window
        geometry is stable while the editor fills panels; the runtime menu
        simply never paints or hit-tests empty panels.
        """
        fm = QFontMetrics(self.font())
        w = LIST_MIN_W
        for panel in self._list_panels:
            for cid in panel:
                if cid in COMMAND_REGISTRY:
                    toggle_w = int(CHECKBOX_SIZE + CHECKBOX_GAP) \
                        if COMMAND_REGISTRY[cid].is_toggle else 0
                    w = max(w, fm.horizontalAdvance(self._label_for(cid))
                            + 2 * LIST_PAD_X + toggle_w)
        w = min(w, LIST_MAX_W)
        self._list_panel_w = w
        left_dir = self._menu.get("direction") == "left"
        # Heights first: every panel clears the neighbouring one vertically
        # by LIST_ANCHOR_GAP_Y; the lateral panel is centered on the cursor.
        heights = [2 * LIST_PAD_Y + max(1, len(p)) * LIST_ROW_H
                   for p in self._list_panels]
        h_lat = heights[2]
        x_lat = LIST_ANCHOR_GAP_X
        x_diag = max(0.0, LIST_ANCHOR_GAP_X - LIST_DIAG_INSET)
        x_pole = max(0.0, LIST_ANCHOR_GAP_X - LIST_POLE_INSET)
        y_ud = -h_lat / 2.0 - LIST_ANCHOR_GAP_Y - heights[1]   # upper-diag top
        y_tp = y_ud - LIST_ANCHOR_GAP_Y - heights[0]           # top top
        y_ld = h_lat / 2.0 + LIST_ANCHOR_GAP_Y                 # lower-diag top
        y_bt = y_ld + heights[3] + LIST_ANCHOR_GAP_Y           # bottom top
        rects = [
            QRectF(x_pole, y_tp, w, heights[0]),               # 0 top pole
            QRectF(x_diag, y_ud, w, heights[1]),               # 1 upper-diagonal
            QRectF(x_lat, -h_lat / 2.0, w, h_lat),             # 2 lateral
            QRectF(x_diag, y_ld, w, heights[3]),               # 3 lower-diagonal
            QRectF(x_pole, y_bt, w, heights[4]),               # 4 bottom pole
        ]
        if left_dir:
            rects = [QRectF(-r.x() - r.width(), r.y(), r.width(), r.height())
                     for r in rects]
        left = min(r.left() for r in rects)
        top = min(r.top() for r in rects)
        right = max(r.right() for r in rects)
        bottom = max(r.bottom() for r in rects)
        off_x, off_y = LIST_MARGIN - left, LIST_MARGIN - top
        self._list_rects = [r.translated(off_x, off_y) for r in rects]
        self._list_cursor = QPointF(off_x, off_y)
        self._list_w = (right - left) + 2 * LIST_MARGIN
        self._list_h = (bottom - top) + 2 * LIST_MARGIN

    def changeEvent(self, event):
        # Font changes re-flow the list panels (width derives from labels).
        if event.type() == QEvent.Type.FontChange and self._is_list():
            self._relayout_list()
            self._sync_fixed_size()
        super().changeEvent(event)

    def _list_row_rect(self, panel: int, row: int) -> QRectF:
        """Widget-local rect of a command row (logical coordinates)."""
        rect = self._list_rects[panel]
        return QRectF(rect.x(), rect.y() + LIST_PAD_Y + row * LIST_ROW_H,
                      rect.width(), LIST_ROW_H)

    def _hit_test_list(self, local_pos: QPointF):
        """(panel, row) under *local_pos*, or None (gap / ghost / outside)."""
        for i, rect in enumerate(self._list_rects):
            if not self._list_panels[i]:
                continue
            if rect.contains(local_pos):
                row = int((local_pos.y() - rect.top() - LIST_PAD_Y)
                          // LIST_ROW_H)
                row = max(0, min(row, len(self._list_panels[i]) - 1))
                return (i, row)
        return None

    def _list_drop_pos(self, local_pos: QPointF):
        """(panel, insert_row) for a drop at *local_pos*, or (-1, -1).
        Empty panels (ghost rects) are valid drop targets in edit mode."""
        for i, rect in enumerate(self._list_rects):
            if rect.contains(local_pos):
                n = len(self._list_panels[i])
                rel = local_pos.y() - rect.top() - LIST_PAD_Y
                idx = int(rel // LIST_ROW_H)
                if rel - idx * LIST_ROW_H > LIST_ROW_H / 2.0:
                    idx += 1   # past the row's midpoint -> insert after it
                return i, max(0, min(idx, n))
        return -1, -1

    def _cmd_available(self, cmd_id: str) -> bool:
        if self._preview:
            return True   # editor preview: everything looks enabled
        return cmd_enabled(self.mw or self.canvas, cmd_id)

    def _is_toggle_cmd(self, cmd_id: str) -> bool:
        cmd = COMMAND_REGISTRY.get(cmd_id)
        return bool(cmd is not None and cmd.is_toggle)

    def _cmd_checked(self, cmd_id: str) -> bool:
        """Current checked state of a toggle command (checkbox rendering)."""
        if self._preview:
            return False   # editor preview has no live canvas state
        return cmd_checked(self.mw or self.canvas, cmd_id)

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
        """Command id at (sector, idx) — for the list layout the "sector" is
        the panel index.  Returns None for an empty slot."""
        data = self._list_panels if self._is_list() else self._sector_data
        lst = data[sector] if 0 <= sector < len(data) else []
        if 0 <= idx < len(lst):
            return lst[idx]
        return None

    def _stack_vertical(self, sector: int) -> bool:
        """Whether *sector* stacks its cards screen-vertically.

        Cards are wider than tall, so a tangential fan only keeps them from
        overlapping when the tangent is (near-)vertical (left/right
        sectors).  Top/bottom sectors fan horizontally and the wide cards
        would bury each other's text — they stack straight up/down instead
        (decision 2026-08-14).
        """
        span = 360.0 / self._sector_count
        base = radians(-90 + sector * span)
        return abs(-sin(base)) > abs(cos(base))

    def _card_center(self, sector: int, idx: int, card_h: float):
        """Widget-local center of a card (stacked along the stack axis for
        k > 1).  See :meth:`_stack_vertical` for the axis rule."""
        span = 360.0 / self._sector_count
        base = radians(-90 + sector * span)
        bx = WINDOW_RADIUS + CARD_RADIUS * cos(base)
        by = WINDOW_RADIUS + CARD_RADIUS * sin(base)
        k = len(self._sector_data[sector])
        if k > 1:
            off = (idx - (k - 1) / 2.0) * (card_h + CARD_STACK_GAP)
            if self._stack_vertical(sector):
                by += off
            else:
                # tangential (perpendicular to the sector radius)
                bx += off * -sin(base)
                by += off * cos(base)
        return bx, by

    def _card_rect(self, sector: int, idx: int, fm: QFontMetrics) -> QRectF:
        """Axis-aligned bounding rect of a card in widget-local coordinates.

        Width follows the hover expand animation: capped at ``CARD_MAX_W``,
        then interpolated toward the full label width as the progress goes
        to 1.0 (short labels never exceed the cap, so they never animate).
        """
        cmd_id = self._slot_at(sector, idx)
        label = self._label_for(cmd_id) if cmd_id else ""
        text_w = fm.horizontalAdvance(label)
        num_w = fm.horizontalAdvance(str(sector + 1)) + NUM_WIDTH_EXTRA
        toggle_w = CHECKBOX_SIZE + CHECKBOX_GAP if self._is_toggle_cmd(cmd_id) else 0.0
        full_w = 2 * CARD_PAD_X + text_w + 2 * NUM_MARGIN + num_w + toggle_w
        capped = min(full_w, CARD_MAX_W)
        p = self._card_progress.get((sector, idx), 0.0)
        cw = capped + (full_w - capped) * p
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
            old = self._hover
            self._hover = hit
            # Hover expand animation: the old card collapses, the new one
            # expands to its full width (see CARD_MAX_W).
            if old is not None:
                self._set_card_progress(old, 0.0)
            if hit is not None:
                self._set_card_progress(hit, 1.0)
            self.update()

    # ── Animation (hover expand + pop-in; ``pcfg.animation_fps < 0`` skips) ──

    def _reset_anim(self):
        """Clear all animation state on a menu (re)open."""
        self._anim_timer.stop()
        self._card_progress.clear()
        self._card_anim.clear()
        self._open_progress = 1.0
        self._open_anim = None

    def _start_open_anim(self):
        """Pop the menu in from transparent / slightly scaled (runtime only)."""
        if pcfg.animation_fps < 0:
            self._open_progress = 1.0
            return
        self._open_progress = 0.0
        self._ensure_anim_timer()
        self._open_anim = (0.0, 1.0, self._anim_elapsed.elapsed())

    def _set_card_progress(self, key, target):
        """Animate a card's expand progress toward *target* (0 = capped, 1 = full)."""
        cur = self._card_progress.get(key, 0.0)
        if cur == target:
            return
        if pcfg.animation_fps < 0:
            self._card_progress[key] = target
            return
        self._ensure_anim_timer()
        self._card_anim[key] = (cur, target, self._anim_elapsed.elapsed())

    def _ensure_anim_timer(self):
        """Start the shared frame driver unless something already runs it."""
        if not self._anim_timer.isActive():
            self._anim_elapsed.start()
            self._anim_timer.start(self._anim_interval)

    def _tick_anim(self):
        """Advance every running animation by elapsed time, then repaint."""
        now = self._anim_elapsed.elapsed()
        animating = False
        for key, (start, target, t0) in list(self._card_anim.items()):
            p = min((now - t0) / CARD_ANIM_MS, 1.0)
            if p >= 1.0:
                self._card_progress[key] = target
                del self._card_anim[key]
            else:
                self._card_progress[key] = start + (target - start) \
                    * self._card_easing.valueForProgress(p)
                animating = True
        if self._open_anim is not None:
            start, target, t0 = self._open_anim
            p = min((now - t0) / OPEN_ANIM_MS, 1.0)
            if p >= 1.0:
                self._open_progress = target
                self._open_anim = None
            else:
                self._open_progress = start + (target - start) \
                    * self._open_easing.valueForProgress(p)
                animating = True
        self.update()
        if not animating:
            self._anim_timer.stop()

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
        self._anim_timer.stop()
        self._card_anim.clear()
        self._open_anim = None
        self.command_triggered.emit(cmd_id)

    # ── Preview mode (config editor) ─────────────────────────

    def set_preview_scale(self, scale: float):
        """Uniform scale for the embedded preview (paint transform only —
        geometry stays in logical coordinates)."""
        self._preview_scale = scale
        self._sync_fixed_size()
        self.update()

    def set_edit_mode(self, enabled: bool):
        """Show dashed sector guides + selection ring; enables click-select /
        right-click-remove / drag-drop for the config editor."""
        self._edit_mode = enabled
        if not enabled:
            self._drop_sector = -1
            self._drop_row = -1
            self._selected = None
        self.update()

    def set_drop_target(self, sector: int, rejected: bool = False):
        """Highlight a sector as the current drop target (red when full)."""
        self._drop_sector = sector
        self._drop_rejected = rejected
        self.update()

    def set_list_drop_target(self, panel: int, row: int, rejected: bool = False):
        """Highlight a list insertion slot as the current drop target."""
        self._drop_sector = panel
        self._drop_row = row
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
        at *local_pos*, measured along the sector's stack axis (see
        :meth:`_stack_vertical`)."""
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
        if self._stack_vertical(sector):
            rel = local_pos.y() - by
        else:
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
        self._drop_row = -1
        self.update()

    # ── Mouse ──────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        if self._preview or self.is_open():
            pos = event.position() / self._preview_scale
            hit = self._hit_test_list(pos) if self._is_list() else self._hit_test(pos)
            self._update_hover(hit)
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
            hit = self._hit_test_list(pos) if self._is_list() else self._hit_test(pos)
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
        if self._is_list():
            hit = self._hit_test_list(event.position())
            if hit is None:  # gap / ghost / outside -> cancel
                self.cancel()
                return
            cmd_id = self._slot_at(*hit)
            if cmd_id and self._cmd_available(cmd_id):
                self._trigger(cmd_id)
            else:
                self.cancel()   # disabled card -> same as center (decision #5)
            return
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
            cmd_id, src_sector, _ = self._drop_info(event.mimeData())
            if cmd_id == SEPARATOR_SENTINEL:   # defensive: no separators in menus
                self.set_drop_target(-1)
                self.set_list_drop_target(-1, -1)
                event.ignore()
                return
            if self._is_list():
                panel, row = self._list_drop_pos(
                    event.position() / self._preview_scale)
                ok = panel >= 0 and (src_sector == panel
                                     or len(self._list_panels[panel])
                                     < LIST_PANEL_MAX_ITEMS)
                self.set_list_drop_target(panel, row, rejected=not ok)
                if ok:
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return
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
            self.set_list_drop_target(-1, -1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not self._preview or not event.mimeData().hasFormat("application/x-pie-cmd"):
            return super().dropEvent(event)
        cmd_id, src_sector, src_idx = self._drop_info(event.mimeData())
        pos = event.position() / self._preview_scale
        if cmd_id == SEPARATOR_SENTINEL:
            self.set_drop_target(-1)
            self.set_list_drop_target(-1, -1)
            event.ignore()
            return
        if self._is_list():
            panel, row = self._list_drop_pos(pos)
            self.set_list_drop_target(-1, -1)
            if panel < 0:
                event.ignore()
                return
            if src_sector == panel and 0 <= src_idx < row:
                row -= 1   # internal move: source removal shifts the target
            if src_sector != panel \
                    and len(self._list_panels[panel]) >= LIST_PANEL_MAX_ITEMS:
                event.ignore()
                return
            event.acceptProposedAction()
            self.command_dropped.emit(panel, row, cmd_id, src_sector, src_idx)
            return
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

    def _card_palette(self, dark):
        """Shared card colors for both layouts (dark/light theme).

        Cards are fully opaque (2026-08-16): the menu is a transient
        overlay, so letting the canvas bleed through only hurt label
        readability on light backgrounds.  The center ring and numbers
        stay translucent.
        """
        if dark:
            ring_c = QColor(130, 135, 150, 220)
            card_bg = QColor(55, 58, 70, 255)
            number_c = QColor(255, 255, 255, 140)
            border_c = QColor(255, 255, 255, 70)
        else:
            ring_c = QColor(100, 105, 120, 160)
            card_bg = QColor(255, 255, 255, 255)
            number_c = QColor(0, 0, 0, 120)
            border_c = QColor(0, 0, 0, 55)
        text_c = get_theme_color(key="@textColor")
        accent_c = get_theme_color(key="@accentPrimary")
        return ring_c, card_bg, number_c, text_c, accent_c, border_c

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Pop-in animation: fade + slight scale from the window center
        # (runtime menus only — the editor preview never runs it).
        if self._open_progress < 1.0:
            painter.setOpacity(self._open_progress)
            s = OPEN_SCALE_FROM + (1.0 - OPEN_SCALE_FROM) * self._open_progress
            wc = (self._list_w if self._is_list() else 2 * WINDOW_RADIUS) / 2.0
            hc = (self._list_h if self._is_list() else 2 * WINDOW_RADIUS) / 2.0
            painter.translate(wc, hc)
            painter.scale(s, s)
            painter.translate(-wc, -hc)
        if self._preview_scale != 1.0:
            painter.scale(self._preview_scale, self._preview_scale)
        if self._is_list():
            self._paint_list(painter)
            return
        center = QPointF(WINDOW_RADIUS, WINDOW_RADIUS)
        dark = is_dark_theme()
        fm = painter.fontMetrics()

        ring_c, card_bg, number_c, text_c, accent_c, border_c = \
            self._card_palette(dark)

        if self._edit_mode:
            self._paint_edit_guides(painter, center, text_c)

        # floating command cards
        self._paint_cards(painter, fm, card_bg, border_c, text_c,
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
        """Draw all sector cards.

        Layering follows the card index: a lower index sits on top of a
        higher one when cards overlap (layer 1 > layer 2, decision
        2026-08-16), so each sector stack draws from the last card up.
        The hovered card is drawn *last* so it always stays fully
        readable while the cursor is on it (decision 2026-08-14).
        """
        hovered = self._hover
        for sector in range(self._sector_count):
            for idx in range(len(self._sector_data[sector]) - 1, -1, -1):
                if (sector, idx) != hovered:
                    self._paint_card(painter, sector, idx, fm, card_bg,
                                     card_border, text_c, number_c, accent_c)
        if hovered is not None:
            sector, idx = hovered
            if (0 <= sector < self._sector_count
                    and 0 <= idx < len(self._sector_data[sector])):
                self._paint_card(painter, sector, idx, fm, card_bg,
                                 card_border, text_c, number_c, accent_c)

    def _paint_card(self, painter, sector, idx, fm, card_bg, card_border,
                    text_c, number_c, accent_c):
        cmd_id = self._slot_at(sector, idx)
        cmd = COMMAND_REGISTRY.get(cmd_id)
        if cmd is None:
            return
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
        num_w = fm.horizontalAdvance(number) + NUM_WIDTH_EXTRA
        toggle_w = CHECKBOX_SIZE + CHECKBOX_GAP if self._is_toggle_cmd(cmd_id) else 0.0
        # Elide only when the label really overflows the card's content
        # width (see _elide_fitting); the cap then only cuts labels that
        # are genuinely longer than CARD_MAX_W, and the fully-expanded
        # hovered card keeps its whole label.
        label = _elide_fitting(
            fm, label,
            rect.width() - 2 * CARD_PAD_X - 2 * NUM_MARGIN - num_w - toggle_w)

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

        # label (left-aligned; toggle commands lead with a checkbox)
        label_x = rect.x() + CARD_PAD_X
        if self._is_toggle_cmd(cmd_id):
            cy = rect.y() + (rect.height() - CHECKBOX_SIZE) / 2
            self._paint_checkbox(painter, label_x, cy,
                                 self._cmd_checked(cmd_id),
                                 hovered, enabled, accent_c, card_border)
            label_x += CHECKBOX_SIZE + CHECKBOX_GAP
        painter.setPen(QPen(tcolor))
        painter.drawText(QPointF(label_x, baseline), label)

        # sector number (right-aligned)
        num_x = rect.x() + rect.width() - CARD_PAD_X - num_w
        painter.setPen(QPen(ncolor))
        painter.drawText(QPointF(num_x, baseline), number)

    def _paint_checkbox(self, painter, x, y, checked, hovered, enabled,
                        accent_c, border_c):
        """Small rounded checkbox for a toggle command (checked = accent
        fill + white tick; unchecked = outline that follows the card text
        color on hover)."""
        box = QRectF(x, y, CHECKBOX_SIZE, CHECKBOX_SIZE)
        if checked:
            fill = QColor(accent_c)
            if not enabled:
                fill.setAlpha(140)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, CHECKBOX_RADIUS, CHECKBOX_RADIUS)
            mark = QColor(HOVER_TEXT_COLOR)
            if not enabled:
                mark.setAlpha(140)
            painter.setPen(QPen(mark, 1.6))
            painter.drawLine(QPointF(x + 2.2, y + CHECKBOX_SIZE * 0.52),
                             QPointF(x + CHECKBOX_SIZE * 0.44,
                                     y + CHECKBOX_SIZE - 2.4))
            painter.drawLine(QPointF(x + CHECKBOX_SIZE * 0.44,
                                     y + CHECKBOX_SIZE - 2.4),
                             QPointF(x + CHECKBOX_SIZE - 1.6, y + 2.2))
        else:
            if hovered:
                pen_c = QColor(HOVER_TEXT_COLOR)
            else:
                pen_c = QColor(border_c)
                if not enabled:
                    pen_c.setAlpha(110)
            painter.setPen(QPen(pen_c, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, CHECKBOX_RADIUS, CHECKBOX_RADIUS)

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
            start_deg, span,   # sweep follows the sector count (4/6/8)
        )
        outer.closeSubpath()
        inner = QPainterPath()
        inner.addEllipse(center, CENTER_INNER_RADIUS, CENTER_INNER_RADIUS)

        fill = QColor(accent_c)
        fill.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(outer.subtracted(inner))

    # ── List layout painting ───────────────────────────────

    def _paint_list(self, painter):
        """Half-ring of context-menu panels: each non-empty panel is one
        continuous rounded menu; empty panels only show as dashed ghosts in
        the config editor (drop targets)."""
        dark = is_dark_theme()
        _, panel_bg, _, text_c, accent_c, border_c = self._card_palette(dark)
        fm = painter.fontMetrics()
        max_text_w = int(self._list_panel_w - 2 * LIST_PAD_X)

        for i, rect in enumerate(self._list_rects):
            panel = self._list_panels[i]
            if not panel:
                if self._edit_mode:
                    ghost = QColor(text_c)
                    ghost.setAlpha(45)
                    painter.setPen(QPen(ghost, 1.0, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect, LIST_PANEL_RADIUS,
                                            LIST_PANEL_RADIUS)
                continue

            # Panel background + border
            panel_path = QPainterPath()
            panel_path.addRoundedRect(rect, LIST_PANEL_RADIUS,
                                      LIST_PANEL_RADIUS)
            painter.setPen(QPen(border_c, 1.0))
            painter.setBrush(panel_bg)
            painter.drawPath(panel_path)

            # Clip rows to the panel so hover fills respect the corners
            painter.save()
            painter.setClipPath(panel_path)
            for row, cmd_id in enumerate(panel):
                cmd = COMMAND_REGISTRY.get(cmd_id)
                if cmd is None:
                    continue
                row_rect = self._list_row_rect(i, row)
                hovered = self._hover == (i, row)
                enabled = self._cmd_available(cmd_id)
                toggle_w = int(CHECKBOX_SIZE + CHECKBOX_GAP) if cmd.is_toggle else 0

                if hovered:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(accent_c))
                    painter.drawRect(row_rect)
                    tcolor = HOVER_TEXT_COLOR
                else:
                    tcolor = QColor(text_c)
                    if not enabled:
                        tcolor.setAlpha(110)

                # selection ring (config editor)
                if self._edit_mode and self._selected == (i, row):
                    sel_pen = QPen(QColor(accent_c), 1.2, Qt.PenStyle.DashLine)
                    painter.setPen(sel_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(
                        row_rect.adjusted(2, 2, -2, -2), 3.0, 3.0)

                label = _elide_fitting(fm, self._label_for(cmd_id),
                                       max_text_w - toggle_w)
                baseline = (row_rect.y() + (row_rect.height() - fm.height())
                            / 2 + fm.ascent())
                label_x = row_rect.x() + LIST_PAD_X
                if toggle_w:
                    cy = row_rect.y() + (row_rect.height() - CHECKBOX_SIZE) / 2
                    self._paint_checkbox(painter, label_x, cy,
                                         self._cmd_checked(cmd_id),
                                         hovered, enabled, accent_c, border_c)
                    label_x += CHECKBOX_SIZE + CHECKBOX_GAP
                painter.setPen(QPen(tcolor))
                painter.drawText(QPointF(label_x, baseline), label)
            painter.restore()

        # Drop insertion line (config editor, dragging over a panel)
        if self._edit_mode and 0 <= self._drop_sector < len(self._list_rects):
            self._paint_list_drop_line(painter, accent_c)

    def _paint_list_drop_line(self, painter, accent_c):
        """Horizontal accent line inside the target panel marking the
        insertion slot (red when the panel is full)."""
        rect = self._list_rects[self._drop_sector]
        n = len(self._list_panels[self._drop_sector])
        idx = max(0, min(self._drop_row, n))
        y = rect.top() + LIST_PAD_Y + idx * LIST_ROW_H
        if self._drop_rejected:
            color = QColor(220, 60, 60, 200)
        else:
            color = QColor(accent_c)
            color.setAlpha(220)
        painter.setPen(QPen(color, 2.0))
        painter.drawLine(QPointF(rect.left() + 4.0, y),
                         QPointF(rect.right() - 4.0, y))
