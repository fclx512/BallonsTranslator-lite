"""文本框几何判据（批量任务共用的小工具）。

「外扩到碰到邻框为止」在仓库里有两处消费点，语义相同、以前各写一份：

- ``ui/batch_merge.py`` 的审批截图（规划 D31：外扩＝组包围盒短边 50%、
  遇邻框即停）；
- ``ui/batch_expand.py`` 的批量框扩张（规划 D5：只扩渲染区域，上限
  "碰到邻框即停"）。

故把判据收在这里，两处各自只决定"扩多少"，"扩到哪停"是同一份实现。

坐标系与 ``utils/textblock.py::TextBlock`` 一致：矩形为 ``[x1, y1, x2, y2]``
（左上 + 右下，整型像素），无旋转（带 ``angle`` 的块由调用方排除——轴对齐
的并集与外扩对旋转框无意义）。
"""

from typing import List, Optional, Sequence

Rect = List[int]


def rect_of(blk) -> Optional[Rect]:
    """块的整型 ``xyxy``；缺失或退化（宽／高非正）返回 ``None``。"""
    xyxy = getattr(blk, "xyxy", None)
    if not xyxy or len(xyxy) < 4:
        return None
    try:
        x1, y1, x2, y2 = (int(round(float(v))) for v in list(xyxy)[:4])
    except (TypeError, ValueError):
        return None
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    return [x1, y1, x2, y2]


def overlap_ratio(a_lo: int, a_hi: int, b_lo: int, b_hi: int) -> float:
    """一维投影重叠率：重叠长度 / 较短者的长度（0～1）。"""
    shorter = min(a_hi - a_lo, b_hi - b_lo)
    if shorter <= 0:
        return 0.0
    return max(0, min(a_hi, b_hi) - max(a_lo, b_lo)) / shorter


def gap_length(a_lo: int, a_hi: int, b_lo: int, b_hi: int) -> int:
    """一维间隙（相离为正，重叠为 0）。"""
    return max(0, max(a_lo, b_lo) - min(a_hi, b_hi))


def expand_limited(
    rect: Rect,
    amount: int,
    page_size: Sequence[int],
    neighbors: Sequence[Rect],
) -> Rect:
    """把 ``rect`` 各边外扩 ``amount``，**碰到邻框或页边界即停**。

    逐边独立求限：邻框＝与当前边判定带（另一根轴上、按 ``rect`` 原区间取）
    有重叠的其他框；外扩后的边不得越过邻框的近侧边（可以贴住）。

    Args:
        rect: ``[x1, y1, x2, y2]``
        amount: 每边想扩的像素数（``<= 0`` 原样返回）
        page_size: ``(宽, 高)``，用于钳制在页内
        neighbors: 同页其他框的 ``[x1, y1, x2, y2]``（含不应被压到的那些）

    Returns:
        外扩后的 ``[x1, y1, x2, y2]``；四边都扩不动时与原值相同。
    """
    if amount <= 0:
        return list(rect)
    width, height = int(page_size[0]), int(page_size[1])
    x1, y1, x2, y2 = rect
    left = _edge(
        [r for r in neighbors if overlap_ratio(y1, y2, r[1], r[3]) > 0],
        x1, amount, 0, low=True, axis="x",
    )
    right = _edge(
        [r for r in neighbors if overlap_ratio(y1, y2, r[1], r[3]) > 0],
        x2, amount, width, low=False, axis="x",
    )
    top = _edge(
        [r for r in neighbors if overlap_ratio(x1, x2, r[0], r[2]) > 0],
        y1, amount, 0, low=True, axis="y",
    )
    bottom = _edge(
        [r for r in neighbors if overlap_ratio(x1, x2, r[0], r[2]) > 0],
        y2, amount, height, low=False, axis="y",
    )
    return [
        max(0, min(left, width)),
        max(0, min(top, height)),
        max(0, min(right, width)),
        max(0, min(bottom, height)),
    ]


def _edge(
    neighbors: Sequence[Rect],
    edge: int,
    amount: int,
    page_bound: int,
    *,
    low: bool,
    axis: str,
) -> int:
    """单边外扩的落点：外扩量、页边界、邻框边界三者取最保守。

    ``low`` 为真走低坐标方向（左／上）：不得越过邻框的**远侧边**（``x2``／``y2``）；
    为假走高坐标方向（右／下）：不得越过邻框的**近侧边**（``x1``／``y1``）。
    """
    if low:
        limit = max(edge - amount, page_bound)
        for rect in neighbors:
            other = rect[2] if axis == "x" else rect[3]
            if other <= edge:
                limit = max(limit, other)
        return min(limit, edge)
    limit = min(edge + amount, page_bound)
    for rect in neighbors:
        other = rect[0] if axis == "x" else rect[1]
        if other >= edge:
            limit = min(limit, other)
    return max(limit, edge)
