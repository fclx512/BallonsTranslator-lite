"""气泡图元几何层：形状 + 尾巴 → 一条 QPainterPath。

设计见 docs/技术实现/气泡绘制_设计方案.md。本模块**不依赖画布、撤销栈与工程**，
只做「(rect, style) → 路径 / 像素」的纯操作，因此可离屏复算
（tests/test_bubble_geometry.py）。拖拽预览与最终导出必须共用 `bubble_path()`，
否则预览和结果会差几个像素。

坐标系：rect 用「父项坐标系」。独立气泡挂在页面图层时父项原点即页面左上角；
块气泡挂在 `TextBlkItem` 下时 rect 取 `unpadRect(boundingRect())`，同一坐标系。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from qtpy.QtWidgets import QGraphicsItem, QGraphicsPathItem

SHAPE_RECT = "rect"
SHAPE_ELLIPSE = "ellipse"
SHAPES = (SHAPE_RECT, SHAPE_ELLIPSE)

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}$")
_BISECT_STEPS = 26
# 嘴宽二分：每次迭代两次 pointAtPercent（圆角矩形约 35µs），22 步约 1.5ms，
# 精度 < 0.5px，足够。调参是拖拽手势，不需要更快。
_MOUTH_BISECT_STEPS = 22
# 锚点定位先粗采样再三分收敛：圆角矩形 64 个采样点约 2ms，加收敛共约 3ms
_RAY_SAMPLES = 64


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _as_color(value, default: str) -> str:
    if isinstance(value, str) and _HEX_COLOR.match(value):
        return value
    return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class BubbleTail:
    """单条直边三角尾巴。

    - ``angle``：0 = 正右，顺时针（屏幕 y 轴向下），90 = 正下，270 = 正上
    - ``length``：尖点外推量，按 ``min(rect.w, rect.h)`` 的比例
    - ``width``：根部宽度，同样按 ``min(w, h)`` 的比例

    用短边做基准而不是「所在边的长度」，是为了同一套参数在圆/扁气泡上
    的观感一致，也不会因为方向转到长边上突然变粗。
    """

    angle: float = 90.0
    length: float = 0.35
    width: float = 0.32

    def normalized(self) -> "BubbleTail":
        angle = _as_float(self.angle, 90.0) % 360.0
        return BubbleTail(
            angle=angle,
            length=_clamp(_as_float(self.length, 0.35), 0.0, 2.0),
            width=_clamp(_as_float(self.width, 0.32), 0.02, 1.0),
        )

    def to_payload(self) -> dict:
        tail = self.normalized()
        payload = {}
        if tail.angle != 90.0:
            payload["angle"] = tail.angle
        if tail.length != 0.35:
            payload["length"] = tail.length
        if tail.width != 0.32:
            payload["width"] = tail.width
        return payload

    @classmethod
    def from_payload(cls, payload) -> "BubbleTail | None":
        if isinstance(payload, BubbleTail):
            return payload.normalized()
        if not isinstance(payload, dict):
            return None
        return cls(
            angle=_as_float(payload.get("angle"), 90.0),
            length=_as_float(payload.get("length"), 0.35),
            width=_as_float(payload.get("width"), 0.32),
        ).normalized()


@dataclass
class BubbleStyle:
    """一套气泡参数，独立气泡与块气泡共用同一份（落盘只写非默认值）。"""

    shape: str = SHAPE_RECT
    corner: float = 0.0
    fill: str = "#ffffff"
    alpha: int = 255
    border: bool = False
    border_width: float = 4.0
    border_color: str = "#000000"
    tail: BubbleTail | None = None

    def normalized(self) -> "BubbleStyle":
        shape = self.shape if self.shape in SHAPES else SHAPE_RECT
        return BubbleStyle(
            shape=shape,
            corner=max(0.0, _as_float(self.corner, 0.0)),
            fill=_as_color(self.fill, "#ffffff"),
            alpha=int(_clamp(_as_int(self.alpha, 255), 0, 255)),
            border=bool(self.border),
            border_width=max(0.0, _as_float(self.border_width, 4.0)),
            border_color=_as_color(self.border_color, "#000000"),
            tail=BubbleTail.from_payload(self.tail),
        )

    def to_payload(self) -> dict:
        """只写与默认值不同的键；冲突/默认值不落盘（上游兼容纪律）。"""
        style = self.normalized()
        payload = {}
        if style.shape != SHAPE_RECT:
            payload["shape"] = style.shape
        if style.corner:
            payload["corner"] = style.corner
        if style.fill != "#ffffff":
            payload["fill"] = style.fill
        if style.alpha != 255:
            payload["alpha"] = style.alpha
        if style.border:
            payload["border"] = True
        if style.border_width != 4.0:
            payload["border_width"] = style.border_width
        if style.border_color != "#000000":
            payload["border_color"] = style.border_color
        if style.tail is not None:
            payload["tail"] = style.tail.to_payload()
        return payload

    @classmethod
    def from_payload(cls, payload) -> "BubbleStyle":
        """宽进：未知键忽略、类型错回默认、数值钳到合法区间。"""
        if isinstance(payload, BubbleStyle):
            return payload.normalized()
        if not isinstance(payload, dict):
            return cls()
        default = cls()
        shape = payload.get("shape", default.shape)
        return cls(
            shape=shape if shape in SHAPES else default.shape,
            corner=_as_float(payload.get("corner"), default.corner),
            fill=_as_color(payload.get("fill"), default.fill),
            alpha=_as_int(payload.get("alpha"), default.alpha),
            border=bool(payload.get("border", default.border)),
            border_width=_as_float(
                payload.get("border_width"), default.border_width
            ),
            border_color=_as_color(
                payload.get("border_color"), default.border_color
            ),
            tail=BubbleTail.from_payload(payload.get("tail")),
        ).normalized()

    def fill_color(self) -> QColor:
        color = QColor(self.fill)
        color.setAlpha(int(_clamp(self.alpha, 0, 255)))
        return color

    def border_qcolor(self) -> QColor:
        return QColor(self.border_color)

    def is_default(self) -> bool:
        return not self.to_payload()


def body_path(
    rect: QRectF, shape: str = SHAPE_RECT, corner: float = 0.0
) -> QPainterPath:
    """主体路径：矩形（可圆角）或椭圆。圆角钳到短边一半，超过退化成胶囊。"""
    path = QPainterPath()
    rect = QRectF(rect).normalized()
    if rect.isEmpty():
        return path
    if shape == SHAPE_ELLIPSE:
        path.addEllipse(rect)
        return path
    radius = max(0.0, _as_float(corner, 0.0))
    radius = min(radius, min(rect.width(), rect.height()) / 2.0)
    if radius > 0.0:
        path.addRoundedRect(rect, radius, radius)
    else:
        path.addRect(rect)
    return path


def _ray_distance(
    path: QPainterPath, center: QPointF, dx: float, dy: float, max_r: float
) -> float:
    """从 center 沿 (dx, dy) 走到 path 边界的距离（二分；对凸/圆角稳定）。"""
    if not path.contains(QPointF(center.x() + dx * max_r, center.y() + dy * max_r)):
        lo, hi = 0.0, max_r
        for _ in range(_BISECT_STEPS):
            mid = (lo + hi) / 2.0
            if path.contains(QPointF(center.x() + dx * mid, center.y() + dy * mid)):
                lo = mid
            else:
                hi = mid
        return lo
    return max_r


def _pull_toward(point: QPointF, target: QPointF, distance: float) -> QPointF:
    dx, dy = target.x() - point.x(), target.y() - point.y()
    norm = math.hypot(dx, dy)
    if norm <= 1e-9:
        return point
    return QPointF(
        point.x() + dx / norm * distance, point.y() + dy / norm * distance
    )


def _curve_mouth(
    body: QPainterPath,
    center: QPointF,
    anchor_percent: float,
    width: float,
    inset: float,
) -> tuple[QPointF, QPointF] | None:
    """以轮廓上 ``anchor_percent`` 处为中心，取一段弦宽 ``width``，两端各向心收 ``inset``。

    矩形（含圆角）与椭圆共用。不能沿切线直接偏移——边界向外弯时偏出来的点都在
    体外，收回去后嘴只剩几像素，尾巴会变成一根针。凸形体的内接弦必在体内，所以
    先找弦，再从锚点沿轮廓向两侧对称放出端点。

    **沿轮廓量弧长距离，而不是按半径找边界点**（2026-11-04 重写）：圆角矩形的
    角点附近，沿半径找出的两点会落到相邻两条边上，测出来的宽度骤变，嘴算不出宽
    度就直接不画——这正是"尾巴转到四个直角区就消失"的成因（corner=20 时
    40°~50° 全灭）。改为沿周长对称外扩并按**欧氏弦长**二分，角上取到的仍是等宽
    的一段弧，尾巴在各方向粗细一致，不会压扁。
    """
    total = body.length()
    if total <= 0.0:
        return None
    lo, hi = 0.0, total / 4.0

    def chord(offset: float) -> tuple[QPointF, QPointF, float]:
        a = body.pointAtPercent(((anchor_percent * total - offset) % total) / total)
        b = body.pointAtPercent(((anchor_percent * total + offset) % total) / total)
        return a, b, math.hypot(b.x() - a.x(), b.y() - a.y())

    # 极端情形（嘴巴比轮廓半周还宽）：直接用半周那一对点，不再二分
    edge_a, edge_b, edge_w = chord(hi)
    if edge_w <= width:
        left, right = edge_a, edge_b
    else:
        for _ in range(_MOUTH_BISECT_STEPS):
            mid = (lo + hi) / 2.0
            if chord(mid)[2] < width:
                lo = mid
            else:
                hi = mid
        a, b, _ = chord(lo)
        left, right = a, b
    if math.hypot(right.x() - left.x(), right.y() - left.y()) < 3.0:
        return None
    return (
        _pull_toward(left, center, inset),
        _pull_toward(right, center, inset),
    )


def tail_path(
    body: QPainterPath,
    rect: QRectF,
    style: "BubbleStyle",
    limit_rect: QRectF | None = None,
) -> QPainterPath:
    """三角尾巴。根部落在轮廓上并向体内收 2px，保证与主体并集相连。

    根部内收是必要的：轮廓处处是曲线，根点直接在轮廓上时，弦的中段可能落在
    体外，并集只会得到一条细缝。内收后嘴巴在体内，并集出来的 V 形干净。

    矩形与椭圆走**同一条**取值路径：从中心沿尾巴方向打到轮廓得到锚点，再沿
    轮廓对称放出嘴宽（``_curve_mouth``）。旧实现给矩形单独走「沿边展开」，
    在四个角上会把嘴压塌到不画；统一后各方向粗细一致。
    """
    path = QPainterPath()
    rect = QRectF(rect).normalized()
    tail = style.tail
    if body.isEmpty() or rect.isEmpty() or tail is None:
        return path
    tail = tail.normalized()
    unit = min(rect.width(), rect.height())
    length = tail.length * unit
    if length <= 0.0:
        return path

    angle = math.radians(tail.angle)
    dx, dy = math.cos(angle), math.sin(angle)
    center = rect.center()
    max_r = math.hypot(rect.width(), rect.height()) / 2.0 + 1.0

    anchor_percent = _ray_hit_percent(body, center, dx, dy, max_r)
    if anchor_percent is None:
        return path
    anchor = body.pointAtPercent(anchor_percent)

    tip = QPointF(anchor.x() + dx * length, anchor.y() + dy * length)
    if path_is_inside(body, tip):
        return path

    if limit_rect is not None:
        tip = _clamp_tip(anchor, tip, QRectF(limit_rect).normalized())

    inset = min(2.0, 0.06 * unit)
    half = max(1.0, tail.width * unit / 2.0)
    mouth = _curve_mouth(body, center, anchor_percent, half * 2.0, inset)
    if mouth is None:
        return path
    left, right = mouth

    if math.hypot(left.x() - right.x(), left.y() - right.y()) < 3.0:
        # 根点被收得太窄：宁可不要，也不画一根细刺
        return path

    path.moveTo(left)
    path.lineTo(tip)
    path.lineTo(right)
    path.closeSubpath()
    return path


def _ray_hit_percent(
    body: QPainterPath, center: QPointF, dx: float, dy: float, max_r: float
) -> float | None:
    """从中心沿方向打到轮廓，返回命中点的百分比位置。

    先按半径二分求交点，再在交点附近取最近的轮廓采样点换算成百分比——轮廓是
    由 ``addRect``/``addEllipse`` 生成的，百分比与几何位置不是线性对应，直接拿
    ``hit / 半周长`` 当百分比会偏。
    """
    t = _ray_distance(body, center, dx, dy, max_r)
    hit = QPointF(center.x() + dx * t, center.y() + dy * t)
    total = body.length()
    if total <= 0.0:
        return None
    best_p, best_d = None, math.inf
    samples = _RAY_SAMPLES
    for i in range(samples):
        p = i / samples
        q = body.pointAtPercent(p)
        d = (q.x() - hit.x()) ** 2 + (q.y() - hit.y()) ** 2
        if d < best_d:
            best_d, best_p = d, p
    if best_p is None:
        return None
    # 粗采样定位后，在邻域内再收一轮，避免角点附近落偏到相邻边
    step = 1.0 / samples
    lo, hi = best_p - step, best_p + step
    for _ in range(18):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        q1, q2 = body.pointAtPercent(m1 % 1.0), body.pointAtPercent(m2 % 1.0)
        d1 = (q1.x() - hit.x()) ** 2 + (q1.y() - hit.y()) ** 2
        d2 = (q2.x() - hit.x()) ** 2 + (q2.y() - hit.y()) ** 2
        if d1 < d2:
            hi = m2
        else:
            lo = m1
    return (((lo + hi) / 2.0) % 1.0)


def path_is_inside(path: QPainterPath, point: QPointF) -> bool:
    return path.contains(point)


def _clamp_tip(base: QPointF, tip: QPointF, limit: QRectF) -> QPointF:
    """尖点越出页面时沿 base→tip 回退到页面内 1px。"""
    if limit.contains(tip):
        return tip
    lo, hi = 0.0, 1.0
    for _ in range(_BISECT_STEPS):
        mid = (lo + hi) / 2.0
        probe = QPointF(
            base.x() + (tip.x() - base.x()) * mid,
            base.y() + (tip.y() - base.y()) * mid,
        )
        if limit.contains(probe):
            lo = mid
        else:
            hi = mid
    probe = QPointF(
        base.x() + (tip.x() - base.x()) * lo,
        base.y() + (tip.y() - base.y()) * lo,
    )
    if probe == base:
        return base
    return probe


def bubble_paths(
    rect: QRectF, style: BubbleStyle, limit_rect: QRectF | None = None
) -> tuple[QPainterPath, QPainterPath]:
    """返回 (外轮廓, 描边环)。描边环只在 ``border`` 开启且宽度 > 0 时非空。

    主体与尾巴必须 ``united()`` 而不是 ``addPath()``：后者会在尾巴根部留一条
    内部边线，描边环沿它画出来就是「气泡上多一道横线」。
    """
    style = style.normalized()
    body = body_path(rect, style.shape, style.corner)
    if body.isEmpty():
        return QPainterPath(), QPainterPath()
    outline = body
    if style.tail is not None:
        tail = tail_path(body, rect, style, limit_rect)
        if not tail.isEmpty():
            outline = body.united(tail)
    return outline, _border_ring(outline, rect, style)


def _border_ring(
    outline: QPainterPath, rect: QRectF, style: BubbleStyle
) -> QPainterPath:
    if not style.border or style.border_width <= 0:
        return QPainterPath()
    rect = QRectF(rect).normalized()
    if outline.isEmpty() or rect.isEmpty():
        return QPainterPath()
    # 描边到中心时内部已无空间，再描会翻出洞：退化成实心。
    if style.border_width * 2.0 >= min(rect.width(), rect.height()):
        return outline
    stroker = QPainterPathStroker()
    stroker.setWidth(style.border_width * 2.0)  # 只保留内侧一半
    stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return stroker.createStroke(outline).intersected(outline)


def bubble_path(
    rect: QRectF, style: BubbleStyle, limit_rect: QRectF | None = None
) -> QPainterPath:
    """外出轮廓（预览与最终像素共用这一个函数）。"""
    return bubble_paths(rect, style, limit_rect)[0]


def bubble_border_path(
    rect: QRectF, style: BubbleStyle, limit_rect: QRectF | None = None
) -> QPainterPath:
    return bubble_paths(rect, style, limit_rect)[1]


def paint_bubble(
    painter: QPainter,
    rect: QRectF,
    style: BubbleStyle,
    limit_rect: QRectF | None = None,
) -> None:
    """把气泡画到 painter 上（坐标已由调用方对好，不做 translate）。"""
    style = style.normalized()
    outline, border = bubble_paths(rect, style, limit_rect)
    if outline.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill = style.fill_color()
    if fill.alpha() > 0:
        painter.fillPath(outline, fill)
    if not border.isEmpty():
        painter.fillPath(border, style.border_qcolor())
    painter.restore()


def bubble_image(
    rect: QRectF,
    style: BubbleStyle,
    bounds: QRectF | None = None,
    limit_rect: QRectF | None = None,
) -> QImage:
    """离屏渲一张（探针/用例用）。实机走 BubbleItem 矢量绘制，不调这里。"""
    rect = QRectF(rect).normalized()
    if bounds is None:
        pad = 4.0 + (style.tail.length if style.tail else 0.0) * min(
            rect.width(), rect.height()
        )
        bounds = rect.adjusted(-pad, -pad, pad, pad)
    bounds = QRectF(bounds).normalized().toAlignedRect()
    image = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        return image
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.translate(-bounds.x(), -bounds.y())
        paint_bubble(painter, rect, style, limit_rect)
    finally:
        painter.end()
    return image


def bubble_to_payload(rect: QRectF, style: BubbleStyle) -> dict:
    """页面级气泡条目的落盘形态：``{'rect': [x, y, w, h], 'style': {...}}``。"""
    rect = QRectF(rect).normalized()
    payload = {
        "rect": [
            round(rect.x(), 2),
            round(rect.y(), 2),
            round(rect.width(), 2),
            round(rect.height(), 2),
        ]
    }
    style_payload = style.normalized().to_payload()
    if style_payload:
        payload["style"] = style_payload
    return payload


def bubble_from_payload(payload) -> tuple[QRectF, BubbleStyle] | None:
    """宽进解析；rect 非法直接判整条无效（返回 None），避免把气泡画到 (0,0)。"""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("rect")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values = [_as_float(v, 0.0) for v in raw]
    rect = QRectF(values[0], values[1], values[2], values[3]).normalized()
    if rect.width() < 1.0 or rect.height() < 1.0:
        return None
    return rect, BubbleStyle.from_payload(payload.get("style"))


# ── 块气泡的存取（TextBlock.bubble 是「只有非默认值才写」的 style 载荷）──────
#
# 块气泡不带 rect：几何由块自身的 ``unpadRect(boundingRect())`` 现算，随块
# 缩放/改字号自动跟随（设计 §3.2）。因此 ``bubble`` 直接存 ``BubbleStyle``
# 的载荷，而不是 ``bubble_to_payload`` 那种带 rect 的页面条目形态。
#
# **「键存在」= 有气泡**，而不是「载荷非空」：默认样式（白色矩形、无描边）
# 本身就是一个有效可见的气泡，``to_payload()`` 对它返回 ``{}``。若拿
# ``is_default()`` 判存在性，用户就永远画不出一个纯白矩形气泡。所以
# ``None`` = 没气泡，``{}`` = 有气泡但全用默认值。
#
# **自定义覆盖区域**（2026-11-04）：同一份载荷里可带 ``"region": [dx, dy, w, h]``
# ——相对块内容区左上角的偏移与尺寸（页面像素）。缺这个键 = 跟随文本块轮廓。
# 它和样式放在同一个 dict 而不是包一层外壳，是刻意的：``BubbleStyle.from_payload``
# 对未知键「宽进忽略」，所以旧数据（纯样式载荷）照原样解析，不需要迁移。

REGION_KEY = "region"


def _as_region(value) -> list | None:
    """校验 ``[dx, dy, w, h]``；宽高必须为正，否则判无效（回落到跟随块）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        dx, dy, w, h = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    # NaN/Inf 会让 QRectF 进半死状态（normalized 不报错但画不出东西），
    # `v != v` 是 NaN 的标准判据
    if any(v != v or v in (float("inf"), float("-inf")) for v in (dx, dy, w, h)):
        return None
    if w < 1.0 or h < 1.0:
        return None
    return [round(dx, 2), round(dy, 2), round(w, 2), round(h, 2)]


def block_bubble_from_payload(payload) -> BubbleStyle | None:
    """解析块气泡样式。``None`` 表示没有气泡；``{}`` 是默认样式的气泡。"""
    if not isinstance(payload, dict):
        return None
    return BubbleStyle.from_payload(payload)


def block_bubble_region(payload) -> QRectF | None:
    """解析自定义覆盖区域（相对块内容区的偏移 + 尺寸）；无该键返回 ``None``。"""
    if not isinstance(payload, dict):
        return None
    region = _as_region(payload.get(REGION_KEY))
    if region is None:
        return None
    return QRectF(region[0], region[1], region[2], region[3])


def block_bubble_to_payload(
    style: BubbleStyle | None, region: QRectF | None = None
) -> dict | None:
    """把块气泡转成落盘形态；没有气泡（``None``）才返回 ``None``。

    默认样式写成 ``{}``——它仍表示「有气泡」，只是参数全默认。*region* 为
    ``None`` 时不写覆盖区域（跟随文本块轮廓）。
    """
    if style is None:
        return None
    payload = BubbleStyle.from_payload(style).to_payload()
    if region is not None:
        rect = QRectF(region).normalized()
        if rect.width() >= 1.0 and rect.height() >= 1.0:
            payload[REGION_KEY] = [
                round(rect.x(), 2),
                round(rect.y(), 2),
                round(rect.width(), 2),
                round(rect.height(), 2),
            ]
    return payload


@dataclass
class BubbleSpec:
    """面板 → 命令的完整意图：样式 + 可选的自由覆盖区域。

    ``region`` 是相对块内容区左上角的矩形；``None`` = 跟随文本块轮廓。
    面板只产出它，不碰撤销栈与工程数据（与改造前一致的分工）。
    """

    style: BubbleStyle
    region: QRectF | None = None

    def to_payload(self) -> dict:
        return block_bubble_to_payload(self.style, self.region) or {}

    @classmethod
    def from_payload(cls, payload) -> "BubbleSpec | None":
        if not isinstance(payload, dict):
            return None
        return cls(
            style=BubbleStyle.from_payload(payload),
            region=block_bubble_region(payload),
        )


class BubbleItem(QGraphicsPathItem):
    """矢量气泡图元。两个宿主共用：页面图层子项 / ``TextBlkItem`` 子项。

    - 不接管鼠标（``NoButton``），命中由所在工具显式开启，避免抢文字块的点选
    - 描边环需要「填充 + 内侧环」两次绘制，``QGraphicsPathItem`` 的 pen/brush
      表达不了，因此覆写 ``paint()``；不调 ``super().paint()``，也不碰 ``scene()``
      （离屏无场景时不会踩 ``scene().update()`` 的空指针）
    """

    def __init__(
        self,
        style: BubbleStyle | None = None,
        rect: QRectF | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._style = (style or BubbleStyle()).normalized()
        self._rect = QRectF(rect) if rect is not None else QRectF()
        self._limit_rect: QRectF | None = None
        self._outline = QPainterPath()
        self._border = QPainterPath()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._rebuild()

    # ── 数据 ────────────────────────────────────────────────────────
    def style(self) -> BubbleStyle:
        return self._style

    def set_style(self, style: BubbleStyle) -> None:
        self._style = BubbleStyle.from_payload(style)
        self._rebuild()

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def set_rect(self, rect: QRectF) -> None:
        self._rect = QRectF(rect).normalized()
        self._rebuild()

    def set_limit_rect(self, limit_rect: QRectF | None) -> None:
        self._limit_rect = QRectF(limit_rect) if limit_rect is not None else None
        self._rebuild()

    def outline(self) -> QPainterPath:
        return QPainterPath(self._outline)

    def border(self) -> QPainterPath:
        return QPainterPath(self._border)

    # ── 交互开关（由宿主工具调用，图元自身不做模式判断）────────────
    def set_interactive(self, enabled: bool) -> None:
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton if enabled else Qt.MouseButton.NoButton
        )
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, enabled)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, enabled)

    def contains_point(self, pos: QPointF) -> bool:
        return self._outline.contains(pos)

    # ── 绘制 ────────────────────────────────────────────────────────
    def _rebuild(self) -> None:
        self._outline, self._border = bubble_paths(
            self._rect, self._style, self._limit_rect
        )
        self.setPath(self._outline)
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._outline.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = self._style.fill_color()
        if fill.alpha() > 0:
            painter.fillPath(self._outline, fill)
        if not self._border.isEmpty():
            painter.fillPath(self._border, self._style.border_qcolor())
        painter.restore()
