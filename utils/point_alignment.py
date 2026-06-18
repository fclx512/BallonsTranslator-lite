"""Advanced point alignment — compute vertical offsets for batch text-block alignment.

Pure computation, no Qt GUI dependencies.
"""

from typing import Dict, List

from utils.textblock import TextBlock


def _blk_y_bounds(blk: TextBlock):
    """Return (top, bottom, center_y) of a TextBlock's current vertical extent.

    Uses ``_bounding_rect`` (synced from canvas) when available, falling
    back to ``xyxy`` which stores the original detection box.
    """
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


def compute_offsets(
    blk_list: List[TextBlock],
    mode: str,
    target_y: float,
) -> Dict[TextBlock, float]:
    """Compute vertical offset ``dy`` for each non-rotated TextBlock.

    Rotated blocks (``angle != 0``) are skipped — they are not included
    in the returned dict.

    Args:
        blk_list: Text blocks to align.
        mode: ``"top"`` | ``"center"`` | ``"bottom"``.
        target_y: Target Y coordinate in scene space.

    Returns:
        Mapping from TextBlock → vertical offset to apply (int/float).
        Empty dict when no block qualifies.
    """
    offsets: Dict[TextBlock, float] = {}

    for blk in blk_list:
        if blk.angle != 0:
            continue

        top, bottom, center = _blk_y_bounds(blk)

        if mode == "top":
            dy = target_y - top
        elif mode == "center":
            dy = target_y - center
        elif mode == "bottom":
            dy = target_y - bottom
        else:
            continue  # unknown mode

        if abs(dy) < 0.5:
            continue  # already aligned, skip

        offsets[blk] = dy

    return offsets
