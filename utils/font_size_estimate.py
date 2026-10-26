"""从字形精细掩膜估计原文字号，并按页做规范字号吸附。

设计约束（与检测器/UI 解耦）：
- 本模块只依赖 numpy/cv2 和 block 的鸭子类型属性（lines/text/src_is_vertical/
  font_size/_detected_font_size），不导入 Qt、pcfg、modules 或 ui。
- 只有声明了 ``provides_glyph_mask = True`` 的检测器才会被调用；其它检测器
  走过原流程，不改变任何行为。
- 估算失败（掩膜为空、整行是填充块、无文本）时返回 None，由调用方保留原有
  字号；本模块不会把字号写成 0 或负数。

算法：
1. 每个检测行内取掩膜墨迹的短边（竖排取列宽、横排取行高）和沿阅读方向的
   长度。掩膜来自管线时已膨胀 ``mask dilate size`` 像素，测量前每侧减掉
   该膨胀量。
2. ``em_short = median(墨迹短边) * 1.103``：字形本身的大小，不受字间距影响。
3. ``em_len = Σ(墨迹长度) / (加权字符数 - 0.1 * 有墨行数)``：字距/空格会把它
   抬高；全角空格计 1.0、半角空格计 0.5，首尾空白不计。
4. 取 ``min(em_short, em_len)``：装饰字体/粗描边抬高 em_short，字距/OCR 缺字
   抬高 em_len，两者方向相反，取小最稳；em_len 低到 em_short 的一半以下时
   视为文本计数异常，回退 em_short。
5. 逐页用长文本块（加权字符数 >= 8）做加权 KDE，得到 1~3 个规范字号；短块
   在 ±12%、长块在 ±9% 内吸附，长块离群保留原值（文本量多而有意缩小）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

import cv2
import numpy as np

INK_TO_EM = 1.103
FULL_WIDTH_SPACE = "\u3000"
LONG_BLOCK_CHARS = 8.0
NORM_BANDWIDTH_RATIO = 0.06
NORM_PEAK_RATIO = 0.18
NORM_PEAK_SHARE = 0.15
NORM_MAX_MODES = 3
LONG_SNAP_TOL = 0.09
SHORT_SNAP_TOL = 0.12
LOW_CONFIDENCE_SNAP_TOL = 0.15
FILL_RATIO_LIMIT = 0.75
MIN_INK_PIXELS = 10


@dataclass
class LineMeasure:
    ink_short: float
    ink_long: float
    fill_ratio: float
    ink_pixels: int


@dataclass
class BlockEstimate:
    block: Any
    em: Optional[float] = None
    em_short: float = 0.0
    em_len: float = 0.0
    chars: float = 0.0
    line_count: int = 0
    measured_lines: int = 0
    confidence: str = "low"
    reason: str = ""
    final_em: Optional[float] = None
    snapped: bool = False


@dataclass
class PageNorm:
    modes: list = field(default_factory=list)
    bandwidth: float = 0.0
    anchors: int = 0


def block_text(block: Any) -> str:
    text = getattr(block, "text", "")
    if isinstance(text, (list, tuple)):
        text = "".join(str(part) for part in text)
    return str(text).replace("\r", "").replace("\n", "")


def weighted_char_count(text: str) -> float:
    """空格按排版宽度计：全角 1.0、半角 0.5；首尾空白不进入墨迹长度。"""
    text = str(text).strip(" \t\u3000")
    count = 0.0
    for ch in text:
        if ch in "\r\n":
            continue
        if ch == FULL_WIDTH_SPACE:
            count += 1.0
        elif ch in " \t":
            count += 0.5
        else:
            count += 1.0
    return count


def _line_bbox(line: Sequence[Sequence[float]]):
    pts = np.asarray(line, np.float32)
    return (
        int(np.floor(pts[:, 0].min())),
        int(np.floor(pts[:, 1].min())),
        int(np.ceil(pts[:, 0].max())),
        int(np.ceil(pts[:, 1].max())),
    )


def _block_vertical(block: Any, lines: Sequence[Sequence[Sequence[float]]]) -> bool:
    vertical = getattr(block, "src_is_vertical", None)
    if vertical is not None:
        return bool(vertical)
    if not lines:
        return False
    x1, y1, x2, y2 = _line_bbox(lines[0])
    return (y2 - y1) > (x2 - x1)


def _measure_line(
    mask: np.ndarray,
    line: Sequence[Sequence[float]],
    vertical: bool,
    dilate: int,
    pad: int = 2,
) -> Optional[LineMeasure]:
    x1, y1, x2, y2 = _line_bbox(line)
    height, width = mask.shape[:2]
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(width, x2 + pad), min(height, y2 + pad)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = mask[y1:y2, x1:x2]
    poly = np.asarray(line, np.int32) - np.array([x1, y1], np.int32)
    roi = np.zeros(crop.shape[:2], np.uint8)
    cv2.fillPoly(roi, [poly], 255)
    ink = cv2.bitwise_and((crop > 0).astype(np.uint8) * 255, roi)
    ink_pixels = int(cv2.countNonZero(ink))
    roi_pixels = int(cv2.countNonZero(roi))
    if ink_pixels < MIN_INK_PIXELS or roi_pixels <= 0:
        return None
    fill_ratio = ink_pixels / roi_pixels
    # CTD 精修行是稀疏字形；整行多边形被填充说明该行未精修（或来自矩形检测器），
    # 不能拿它当字形尺寸。
    if fill_ratio > FILL_RATIO_LIMIT:
        return None
    profile_short = (ink > 0).sum(axis=0 if vertical else 1)
    short_idx = np.flatnonzero(profile_short > 0)
    profile_long = (ink > 0).sum(axis=1 if vertical else 0)
    long_idx = np.flatnonzero(profile_long > 0)
    if not len(short_idx) or not len(long_idx):
        return None
    ink_short = float(short_idx[-1] - short_idx[0] + 1)
    ink_long = float(long_idx[-1] - long_idx[0] + 1)
    if dilate > 0:
        shrink = 2.0 * dilate
        ink_short = max(1.0, ink_short - shrink)
        ink_long = max(1.0, ink_long - shrink)
    return LineMeasure(ink_short=ink_short, ink_long=ink_long,
                       fill_ratio=fill_ratio, ink_pixels=ink_pixels)


def _lines_bbox(lines: Sequence[Sequence[Sequence[float]]]):
    points = np.asarray([p for line in lines for p in line], np.float32)
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def _metrics_valid(block: Any, metrics: Any) -> bool:
    if not isinstance(metrics, dict):
        return False
    try:
        short = float(metrics["short"])
        long_sum = float(metrics["long_sum"])
        measured = int(metrics["measured"])
        total = int(metrics["total"])
        bbox = [float(value) for value in metrics["bbox"]]
    except (KeyError, TypeError, ValueError):
        return False
    if short <= 0 or long_sum <= 0 or measured <= 0:
        return False
    lines = list(getattr(block, "lines", None) or [])
    if len(lines) != total:
        return False
    current = _lines_bbox(lines)
    tolerance = max(4.0, 0.05 * max(current[2] - current[0], current[3] - current[1]))
    return all(abs(current[i] - bbox[i]) <= tolerance for i in range(4))


def attach_glyph_font_metrics(blocks: Iterable[Any], mask: np.ndarray,
                              dilate: int = 0) -> int:
    """检测阶段用未合并的 CTD 精细掩膜量出每块墨迹指标。

    指标只挂在 block 的内存属性上，``utils/textblock.py::TextBlock.to_dict``
    会把它排除在工程存档之外。部分行没有 CTD 字形时整块不挂指标，保留旧指标
    或回退到页级掩膜，避免用部分长度去除全部字符数。
    """
    if mask is None or getattr(mask, "ndim", 0) != 2 or mask.size == 0:
        return 0
    attached = 0
    for block in blocks:
        lines = list(getattr(block, "lines", None) or [])
        if not lines:
            continue
        vertical = _block_vertical(block, lines)
        measures = [
            measure
            for measure in (
                _measure_line(mask, line, vertical, max(0, int(dilate)))
                for line in lines
            )
            if measure is not None
        ]
        if len(measures) != len(lines):
            continue
        setattr(block, "_glyph_font_metrics", {
            "short": float(np.median([m.ink_short for m in measures])),
            "long_sum": float(sum(m.ink_long for m in measures)),
            "measured": len(measures),
            "total": len(lines),
            "bbox": [int(round(value)) for value in _lines_bbox(lines)],
        })
        attached += 1
    return attached


def _finalize_estimate(
    estimate: BlockEstimate,
    short: float,
    long_sum: float,
    measured: int,
    chars: float,
) -> BlockEstimate:
    estimate.measured_lines = measured
    estimate.em_short = float(short * INK_TO_EM)
    denominator = max(chars - 0.1 * measured, 1.0)
    estimate.em_len = float(long_sum / denominator)
    ratio = estimate.em_len / estimate.em_short if estimate.em_short > 0 else 0.0
    if ratio < 0.5:
        # OCR 多给了字符或掩膜长度被截断；短边比长度更不容易被文本计数带偏。
        estimate.em = estimate.em_short
        estimate.confidence = "low"
        estimate.reason = "text/mask mismatch"
    else:
        estimate.em = min(estimate.em_short, estimate.em_len)
        if (
            chars >= LONG_BLOCK_CHARS
            and 0.75 <= ratio <= 1.15
            and measured == estimate.line_count
        ):
            estimate.confidence = "high"
        elif 0.55 <= ratio <= 1.6:
            estimate.confidence = "medium"
        else:
            estimate.confidence = "low"
    estimate.final_em = estimate.em
    return estimate


def estimate_block(block: Any, mask: Optional[np.ndarray],
                   dilate: int = 0) -> BlockEstimate:
    text = block_text(block)
    chars = weighted_char_count(text)
    lines = list(getattr(block, "lines", None) or [])
    estimate = BlockEstimate(block=block, chars=chars, line_count=len(lines))
    if chars < 1.0 or not lines:
        estimate.reason = "no text"
        return estimate
    # 检测阶段挂上的原始 CTD 指标优先：页级合并掩膜可能把未参与合并的行整行填充。
    metrics = getattr(block, "_glyph_font_metrics", None)
    if _metrics_valid(block, metrics):
        return _finalize_estimate(
            estimate,
            float(metrics["short"]),
            float(metrics["long_sum"]),
            int(metrics["measured"]),
            chars,
        )
    if mask is None or getattr(mask, "ndim", 0) != 2 or mask.size == 0:
        estimate.reason = "no mask"
        return estimate
    vertical = _block_vertical(block, lines)
    measures = [
        measure
        for measure in (
            _measure_line(mask, line, vertical, max(0, int(dilate)))
            for line in lines
        )
        if measure is not None
    ]
    if not measures:
        estimate.reason = "mask missing or filled"
        return estimate
    return _finalize_estimate(
        estimate,
        float(np.median([m.ink_short for m in measures])),
        float(sum(m.ink_long for m in measures)),
        len(measures),
        chars,
    )


def estimate_page(blocks: Iterable[Any], mask: Optional[np.ndarray],
                  dilate: int = 0) -> list:
    return [estimate_block(block, mask, dilate) for block in blocks]


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    return float(values[np.searchsorted(cumulative, cumulative[-1] / 2.0)])


def _kde_modes(values: np.ndarray, weights: np.ndarray, bandwidth: float):
    if len(values) < 2 or bandwidth <= 0:
        return []
    low, high = values.min() - 3 * bandwidth, values.max() + 3 * bandwidth
    grid = np.arange(low, high + 0.25, 0.25)
    density = np.zeros_like(grid)
    for value, weight in zip(values, weights):
        density += weight * np.exp(-0.5 * ((grid - value) / bandwidth) ** 2)
    if density.max() <= 0:
        return []
    peaks = []
    for index in range(1, len(grid) - 1):
        if (
            density[index] >= density[index - 1]
            and density[index] > density[index + 1]
            and density[index] >= density.max() * NORM_PEAK_RATIO
        ):
            peaks.append((float(grid[index]), float(density[index])))
    peaks.sort(key=lambda item: -item[1])
    merged = []
    for peak, score in peaks:
        if all(abs(peak - other) > bandwidth * 1.2 for other, _ in merged):
            merged.append((peak, score))
    return merged


def page_norm(
    estimates: Sequence[BlockEstimate],
    long_chars: float = LONG_BLOCK_CHARS,
    bandwidth_ratio: float = NORM_BANDWIDTH_RATIO,
    peak_share: float = NORM_PEAK_SHARE,
    max_modes: int = NORM_MAX_MODES,
) -> PageNorm:
    anchors = [e for e in estimates if e.em and e.chars >= long_chars]
    if len(anchors) < 2:
        anchors = [e for e in estimates if e.em]
    if len(anchors) < 2:
        return PageNorm()
    values = np.array([e.em for e in anchors], float)
    weights = np.array([e.chars for e in anchors], float)
    bandwidth = max(2.0, float(np.median(values)) * bandwidth_ratio)
    peaks = _kde_modes(values, weights, bandwidth)
    modes = []
    for peak, _score in peaks:
        members = np.abs(values - peak) <= bandwidth
        if not members.any():
            continue
        share = float(weights[members].sum() / weights.sum())
        if share < peak_share:
            continue
        center = _weighted_median(values[members], weights[members])
        if all(abs(center - existing) > bandwidth * 1.2 for existing in modes):
            modes.append(center)
    if not modes:
        modes = [_weighted_median(values, weights)]
    modes.sort()
    return PageNorm(modes=modes[:max_modes], bandwidth=bandwidth, anchors=len(anchors))


def snap_estimates(
    estimates: Sequence[BlockEstimate],
    norm: PageNorm,
    long_chars: float = LONG_BLOCK_CHARS,
    long_tol: float = LONG_SNAP_TOL,
    short_tol: float = SHORT_SNAP_TOL,
    low_confidence_tol: float = LOW_CONFIDENCE_SNAP_TOL,
) -> None:
    if not norm.modes:
        for estimate in estimates:
            estimate.final_em = estimate.em
            estimate.snapped = False
        return
    for estimate in estimates:
        if not estimate.em:
            continue
        nearest = min(norm.modes, key=lambda mode: abs(mode - estimate.em))
        relative = abs(nearest - estimate.em) / estimate.em
        tolerance = long_tol if estimate.chars >= long_chars else short_tol
        if estimate.confidence == "low":
            tolerance = max(tolerance, low_confidence_tol)
        if relative <= tolerance:
            estimate.final_em = float(nearest)
            estimate.snapped = True
        else:
            # 长文本块离群通常是气泡有意缩小；保留原始估计，不强行拉回。
            estimate.final_em = estimate.em
            estimate.snapped = False


def apply_font_sizes(
    blocks: Iterable[Any],
    mask: Optional[np.ndarray],
    *,
    dilate: int = 0,
    scale: float = 1.0,
    min_size: float = 0.0,
    max_size: float = 0.0,
    per_page_norm: bool = True,
    long_chars: float = LONG_BLOCK_CHARS,
) -> list:
    estimates = estimate_page(blocks, mask, dilate)
    if per_page_norm:
        norm = page_norm(estimates, long_chars=long_chars)
        snap_estimates(estimates, norm, long_chars=long_chars)
    for estimate in estimates:
        if not estimate.final_em or estimate.final_em <= 0:
            continue
        size = float(estimate.final_em) * float(scale or 1.0)
        if max_size and max_size > 0:
            size = min(size, float(max_size))
        if min_size and min_size > 0:
            size = max(size, float(min_size))
        if size <= 0:
            continue
        estimate.block.font_size = size
        estimate.block._detected_font_size = size
    return estimates


def _param_value(params: dict, key: str, default: Any) -> Any:
    item = params.get(key)
    if isinstance(item, dict):
        return item.get("value", default)
    return default


def params_from_detector(detector: Any) -> dict:
    """从检测器的 params 声明中读取膨胀量与字号参数；不依赖具体类。"""
    params = getattr(detector, "params", None) or {}
    try:
        dilate = int(_param_value(params, "mask dilate size", 0) or 0)
    except (TypeError, ValueError):
        dilate = 0
    try:
        scale = float(_param_value(params, "font size multiplier", 1.0) or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    try:
        min_size = float(_param_value(params, "font size min", 0.0) or 0.0)
    except (TypeError, ValueError):
        min_size = 0.0
    try:
        max_size = float(_param_value(params, "font size max", 0.0) or 0.0)
    except (TypeError, ValueError):
        max_size = 0.0
    return {
        "dilate": max(0, dilate),
        "scale": scale,
        "min_size": min_size,
        "max_size": max_size,
    }


def apply_mask_font_sizes_from_detector(
    blocks: Iterable[Any], mask: Optional[np.ndarray], detector: Any
) -> list:
    """仅对声明了字形掩膜的检测器生效；其它情况原样返回空列表。"""
    if detector is None:
        return []
    if not getattr(detector, "provides_glyph_mask", False):
        return []
    return apply_font_sizes(blocks, mask, **params_from_detector(detector))
