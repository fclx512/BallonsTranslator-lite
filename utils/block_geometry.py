"""文本框几何判据（批量任务与区域再检测共用的小工具）。

**矩形部分**：「外扩到碰到邻框为止」的唯一实现，消费点：

- ``ui/batch_merge.py`` 的审批截图（规划 D31：外扩＝组包围盒短边 50%、
  遇邻框即停）；
- ``ui/workbench_tasks.py`` 的待办队列审批截图（外扩＝块矩形短边 50%）。

（批量框扩张这个消费点已于 2026-09-26 退役，见 ``scripts/audit_registry.json``。）
两处各自只决定"扩多少"，"扩到哪停"是同一份实现。

坐标系与 ``utils/textblock.py::TextBlock`` 一致：矩形为 ``[x1, y1, x2, y2]``
（左上 + 右下，整型像素），无旋转（带 ``angle`` 的块由调用方排除——轴对齐
的并集与外扩对旋转框无意义）。

**四边形部分**（``poly_of`` 一族）：``ui/region_redetect.py`` 的判据一律按
``TextBlock.lines`` 的**真实四边形**算，不能用 ``xyxy``——后者只是轴对齐
外接矩形，倾斜框（检测器返回的 DB 四边形，实测 11.9°~16.7°）用外接矩形会
误判邻居与重叠。返回 ``shapely`` 几何体（仓库既有依赖，
``utils/textblock.py`` 已 import），面积类判据走它；轴对齐的"带重叠"仍用
上面的 1-D ``overlap_ratio``，两处口径保持一致。
"""

from typing import List, Optional, Sequence

from shapely.geometry import MultiPoint
from shapely.geometry.base import BaseGeometry

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


# ── 四边形判据（区域再检测用）─────────────────────────────────────

Point = List[float]


def poly_of(blk) -> Optional[BaseGeometry]:
    """块占位的真实多边形：``lines`` 全部顶点的凸包；取不到返回 ``None``。

    - ``lines`` 是文字行四边形列表（检测器返回的 DB 四边形；合并块是多行），
      凸包即该块在页面上实际覆盖的区域；
    - 顶点全共线（退化框）时凸包退化为线段、面积为 0 → 返回 ``None``，调用方
      按"判不出"处理，不要拿它去算重叠。
    """
    pts = quad_points(blk)
    if len(pts) < 3:
        return None
    try:
        hull = MultiPoint(pts).convex_hull
    except Exception:
        return None
    if hull.is_empty or hull.area <= 0:
        return None
    return hull


def quad_points(blk) -> List[Point]:
    """``lines`` 的全部顶点（``[(x, y), ...]``）；缺失或形态异常时返回空表。"""
    out: List[Point] = []
    for line in getattr(blk, "lines", None) or []:
        try:
            for pnt in line:
                x, y = float(pnt[0]), float(pnt[1])
                out.append([x, y])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def poly_center(blk) -> Optional[Point]:
    """块中心＝四边形**顶点均值**（规划 §八.1 的口径，不是形心）。

    取不到顶点时退回 ``xyxy`` 的几何中心；两者都没有返回 ``None``。
    """
    pts = quad_points(blk)
    if pts:
        return [
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        ]
    rect = rect_of(blk)
    if rect is None:
        return None
    return [(rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0]


def poly_bands(blk) -> Optional[Rect]:
    """块的轴对齐范围（由四边形顶点取，非 ``xyxy``）；取不到返回 ``None``。

    只用于"带重叠"这类粗判（沿一根轴比较），精细判据一律用多边形本身。
    """
    pts = quad_points(blk)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]


def poly_overlap_ratio(a, b, base: BaseGeometry = None) -> float:
    """两个块（或现成形状）的重叠率：交集面积 / ``base`` 的面积。

    ``base`` 缺省取**较小者**，即区域再检测的替换判据口径（决策 4）：无论
    "新块落在已有块里"（区域级大框吃掉了整块文字）还是"已有块落在新块里"，
    都算同一片文字被重新检出。任一边取不到有效多边形即返回 0.0（不替换）。
    """
    pa, pb = shape_of(a), shape_of(b)
    if pa is None or pb is None:
        return 0.0
    if base is None or base.area <= 0:
        base = pa if pa.area <= pb.area else pb
    try:
        return float(pa.intersection(pb).area) / float(base.area)
    except Exception:
        return 0.0


def shape_of(obj) -> Optional[BaseGeometry]:
    """块 → 多边形；已是有效几何体则原样返回；无效（空/退化）返回 ``None``。"""
    if isinstance(obj, BaseGeometry):
        return None if (obj.is_empty or obj.area <= 0) else obj
    return poly_of(obj)
