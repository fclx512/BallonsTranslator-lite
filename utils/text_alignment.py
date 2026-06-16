"""Photoshop-style text block alignment utilities.

Pure computation functions — no Qt GUI dependencies beyond QPointF/QLineF.
All alignment operates on content rects (absBoundingRect / no padding).
"""

from typing import Dict, List

from qtpy.QtCore import QLineF, QPointF

# Snapping threshold in screen pixels (converted to scene coords via scale_factor)
SNAP_THRESHOLD = 5


def compute_snap(
    moving_rect: list,
    target_rects: List[list],
    threshold: float,
) -> tuple:
    """Compute snap position and guide lines for a moving text block.

    Checks the moving rect's edges (left, right) and horizontal center
    against every target rect's edges and center. The same independently
    for vertical (top, bottom, center Y).

    Args:
        moving_rect: [x, y, w, h] content rect of the dragged item.
        target_rects: List of [x, y, w, h] content rects of other items.
        threshold: Snap distance in scene coordinates.

    Returns:
        (adjusted_x, adjusted_y, guides) where guides is a list of QLineF.
    """
    mx, my, mw, mh = moving_rect
    m_left = mx
    m_right = mx + mw
    m_top = my
    m_bottom = my + mh
    m_cx = mx + mw / 2.0
    m_cy = my + mh / 2.0

    adjusted_x = mx
    adjusted_y = my
    guides: List[QLineF] = []

    moving_h = [m_left, m_right, m_cx]
    moving_v = [m_top, m_bottom, m_cy]

    # --- Horizontal snap ---
    best_h_dist = threshold + 1.0
    best_h_target = 0.0
    best_h_y1 = 0.0
    best_h_y2 = 0.0

    for tx, ty, tw, th in target_rects:
        t_right = tx + tw
        t_cx = tx + tw / 2.0
        t_bottom = ty + th
        target_h = [tx, t_right, t_cx]

        for mh_val in moving_h:
            for th_val in target_h:
                dist = abs(mh_val - th_val)
                if dist < best_h_dist:
                    best_h_dist = dist
                    adjusted_x = mx + (th_val - mh_val)
                    best_h_target = th_val
                    best_h_y1 = min(my, ty)
                    best_h_y2 = max(m_bottom, t_bottom)

    if best_h_dist <= threshold:
        guides.append(QLineF(best_h_target, best_h_y1, best_h_target, best_h_y2))

    # --- Vertical snap ---
    best_v_dist = threshold + 1.0
    best_v_target = 0.0
    best_v_x1 = 0.0
    best_v_x2 = 0.0

    for tx, ty, tw, th in target_rects:
        t_bottom = ty + th
        t_cy = ty + th / 2.0
        t_right = tx + tw
        target_v = [ty, t_bottom, t_cy]

        for mv_val in moving_v:
            for tv_val in target_v:
                dist = abs(mv_val - tv_val)
                if dist < best_v_dist:
                    best_v_dist = dist
                    adjusted_y = my + (tv_val - mv_val)
                    best_v_target = tv_val
                    best_v_x1 = min(mx, tx)
                    best_v_x2 = max(m_right, t_right)

    if best_v_dist <= threshold:
        guides.append(QLineF(best_v_x1, best_v_target, best_v_x2, best_v_target))

    return adjusted_x, adjusted_y, guides


# ---------------------------------------------------------------------------
# Batch alignment — each returns {TextBlkItem: QPointF} for items that move
# ---------------------------------------------------------------------------


def _content_rects(items) -> List[tuple]:
    """Return list of (item, x, y, w, h) for each item's content rect."""
    result = []
    for it in items:
        r = it.absBoundingRect()
        result.append((it, r[0], r[1], r[2], r[3]))
    return result


def _to_padded_pos(item, target_cx, target_cy):
    """Convert content-rect top-left to the padded pos() that setPos expects."""
    pad = item.padding()
    return QPointF(target_cx - pad, target_cy - pad)


def align_left(items) -> Dict:
    """Align content left edges to the leftmost edge."""
    data = _content_rects(items)
    target = min(x for _, x, y, w, h in data)
    result = {}
    for it, x, y, w, h in data:
        if abs(x - target) > 0.5:
            result[it] = _to_padded_pos(it, target, y)
    return result


def align_right(items) -> Dict:
    """Align content right edges to the rightmost edge."""
    data = _content_rects(items)
    rightmost = max(x + w for _, x, y, w, h in data)
    result = {}
    for it, x, y, w, h in data:
        target_x = rightmost - w
        if abs(x - target_x) > 0.5:
            result[it] = _to_padded_pos(it, target_x, y)
    return result


def align_top(items) -> Dict:
    """Align content top edges to the topmost edge."""
    data = _content_rects(items)
    target = min(y for _, x, y, w, h in data)
    result = {}
    for it, x, y, w, h in data:
        if abs(y - target) > 0.5:
            result[it] = _to_padded_pos(it, x, target)
    return result


def align_bottom(items) -> Dict:
    """Align content bottom edges to the bottommost edge."""
    data = _content_rects(items)
    bottommost = max(y + h for _, x, y, w, h in data)
    result = {}
    for it, x, y, w, h in data:
        target_y = bottommost - h
        if abs(y - target_y) > 0.5:
            result[it] = _to_padded_pos(it, x, target_y)
    return result


def align_horizontal_center(items) -> Dict:
    """Align horizontal centres to the average centre X of all items."""
    data = _content_rects(items)
    centres = [x + w / 2.0 for _, x, y, w, h in data]
    target_cx = sum(centres) / len(centres)
    result = {}
    for it, x, y, w, h in data:
        target_x = target_cx - w / 2.0
        if abs(x - target_x) > 0.5:
            result[it] = _to_padded_pos(it, target_x, y)
    return result


def align_vertical_center(items) -> Dict:
    """Align vertical centres to the average centre Y of all items."""
    data = _content_rects(items)
    centres = [y + h / 2.0 for _, x, y, w, h in data]
    target_cy = sum(centres) / len(centres)
    result = {}
    for it, x, y, w, h in data:
        target_y = target_cy - h / 2.0
        if abs(y - target_y) > 0.5:
            result[it] = _to_padded_pos(it, x, target_y)
    return result


def distribute_horizontal(items) -> Dict:
    """Equal horizontal spacing. First and last items anchor the range.

    Requires ≥ 3 items; returns empty dict otherwise.
    """
    if len(items) < 3:
        return {}

    paired = [(it, it.absBoundingRect()) for it in items]
    paired.sort(key=lambda p: p[1][0])

    total_w = sum(r[2] for _, r in paired)
    leftmost = paired[0][1][0]
    rightmost = paired[-1][1][0] + paired[-1][1][2]
    available = rightmost - leftmost - total_w
    gap = available / (len(items) - 1)

    result = {}
    cur_x = float(leftmost)
    for it, (x, y, w, h) in paired:
        if abs(x - cur_x) > 0.5:
            result[it] = _to_padded_pos(it, cur_x, y)
        cur_x += w + gap
    return result


def distribute_vertical(items) -> Dict:
    """Equal vertical spacing. First and last items anchor the range.

    Requires ≥ 3 items; returns empty dict otherwise.
    """
    if len(items) < 3:
        return {}

    paired = [(it, it.absBoundingRect()) for it in items]
    paired.sort(key=lambda p: p[1][1])

    total_h = sum(r[3] for _, r in paired)
    topmost = paired[0][1][1]
    bottommost = paired[-1][1][1] + paired[-1][1][3]
    available = bottommost - topmost - total_h
    gap = available / (len(items) - 1)

    result = {}
    cur_y = float(topmost)
    for it, (x, y, w, h) in paired:
        if abs(y - cur_y) > 0.5:
            result[it] = _to_padded_pos(it, x, cur_y)
        cur_y += h + gap
    return result
