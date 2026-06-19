"""Advanced point alignment — compute offsets for batch text-block alignment.

Pure computation, no Qt GUI dependencies.
Supports X-axis (left/center/right) and Y-axis (top/center/bottom) alignment.
"""

from typing import Dict, List

from utils.textblock import TextBlock


def _blk_y_bounds(blk: TextBlock):
    """Return (top, bottom, center_y) of a TextBlock's vertical extent."""
    if blk._bounding_rect is not None:
        _, y, _, h = blk._bounding_rect  # xywh
        top = y
        bottom = y + h
        center = y + h / 2.0
    else:
        _, y1, _, y2 = blk.xyxy  # x1y1x2y2
        top = y1
        bottom = y2
        center = (y1 + y2) / 2.0
    return top, bottom, center


def _blk_x_bounds(blk: TextBlock):
    """Return (left, right, center_x) of a TextBlock's horizontal extent."""
    if blk._bounding_rect is not None:
        x, _, w, _ = blk._bounding_rect  # xywh
        left = x
        right = x + w
        center = x + w / 2.0
    else:
        x1, _, x2, _ = blk.xyxy  # x1y1x2y2
        left = x1
        right = x2
        center = (x1 + x2) / 2.0
    return left, right, center


def compute_offsets(
    blk_list: List[TextBlock],
    axis: str,
    mode: str,
    target: float,
) -> Dict[TextBlock, float]:
    """Compute offset for each non-rotated TextBlock along the given axis.

    Rotated blocks (``angle != 0``) are skipped.

    Args:
        blk_list: Text blocks to align.
        axis: ``"x"`` or ``"y"``.
        mode:
            - For ``axis="y"``: ``"top"`` | ``"center"`` | ``"bottom"``.
            - For ``axis="x"``: ``"left"`` | ``"center"`` | ``"right"``.
        target: Target coordinate in scene space (X or Y).

    Returns:
        Mapping from TextBlock → offset to apply (int/float).
        Empty dict when no block qualifies.
    """
    offsets: Dict[TextBlock, float] = {}

    for blk in blk_list:
        if blk.angle != 0:
            continue

        if axis == "y":
            top, bottom, center = _blk_y_bounds(blk)
            if mode == "top":
                delta = target - top
            elif mode == "center":
                delta = target - center
            elif mode == "bottom":
                delta = target - bottom
            else:
                continue
        elif axis == "x":
            left, right, center = _blk_x_bounds(blk)
            if mode == "left":
                delta = target - left
            elif mode == "center":
                delta = target - center
            elif mode == "right":
                delta = target - right
            else:
                continue
        else:
            continue

        if abs(delta) < 0.5:
            continue

        offsets[blk] = delta

    return offsets
