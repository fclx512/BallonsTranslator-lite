"""文字擦除掩膜精化：把「擦整块几何范围」收窄成「只擦笔画」（纯 numpy/cv2）。

一个块的擦除范围有两个来源，本模块只负责在**几何范围之内**算候选，不做
模型调用、不读配置、不碰 Qt：

- **几何范围（baseline）**：块 lines 填充多边形 + ``mask dilate size`` 膨胀，
  即现有「整块擦除」行为的边界。它是**硬上限**——任何候选掩膜都由构造保证
  落在它里面（先与 fill 相交、膨胀后再与 baseline 相交，见 ``_finalize``），
  不是靠口头断言。
- **笔画候选**：优先用检测器概率图（prob-only）；给定了前景色时可加一路
  颜色提示；经典 Otsu 二值化只在显式要求时作为对照输出，**默认不参与**，
  也**不做 prob∪Otsu 的并集**（并集只会把 Otsu 自身的误检一起带进来）。

候选逻辑与真实样本探针保持同一口径：
``utils/color_extract.py::PROB_BIN_THRESH`` 二值化、椭圆核膨胀、膨胀后与
膨胀几何相交；本模块目前只供隔离验证使用，不接生产管线。

**通道口径是 RGB**（与 ``utils/io_utils.py::imread`` 一致），颜色提示的
LAB 距离复用 ``utils/color_extract.py`` 的 ``_lab``/``_lab_dist``/``_rgb_to_lab``，
避免另立一套色距口径。

典型用法::

    from utils.text_mask_refine import refine_text_mask
    res = refine_text_mask(crop_rgb, fill_mask, prob_crop=prob[y1:y2, x1:x2],
                           fg_rgb=[0, 0, 0])
    erase = res.prob if res.prob is not None else res.baseline
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2
import numpy as np

# 二值化阈值与色距口径都复用取色模块，避免两处漂移（探针同款）。
from utils.color_extract import (
    PROB_BIN_THRESH,
    _glyph_mask_classical,
    _lab,
    _lab_dist,
    _rgb_to_lab,
)

# 膨胀核半径：与检测器参数 ``mask dilate size`` 默认值同值（核边长 = 2k+1）。
# 0 表示不膨胀（核为 1x1，等价恒等），便于对照与测试。
MASK_DILATE_SIZE = 2


@dataclass
class TextMaskCandidates:
    """一个块的擦除掩膜候选（均为裁剪区坐标、uint8 0/255）。

    - ``baseline``：几何范围本身（膨胀后的框掩膜），永远存在；无一路候选
      可用时就直接擦它。
    - ``prob``：概率图二值化后的**纯 prob 候选**，未给 ``prob_crop`` 时为 None。
    - ``color``：给定 ``fg_rgb`` 时的颜色提示候选（近前景色像素），未给时为 None。
    - ``classical``：经典 Otsu 对照，仅 ``include_classical=True`` 时非 None；
      **只是对照，不参与上面的候选，也不与它们求并**。
    - ``diagnostics``：面积/缩减/相交前越界像素等客观诊断量（见
      ``refine_text_mask`` 的 Returns 说明）。

    硬不变式：``prob``/``color``/``classical`` 非 None 时都满足
    ``候选 & ~baseline == 0``。
    """

    baseline: np.ndarray
    prob: Optional[np.ndarray]
    color: Optional[np.ndarray]
    classical: Optional[np.ndarray]
    diagnostics: Dict


def _ellipse_kernel(dilate_size: int) -> np.ndarray:
    size = max(0, int(dilate_size))
    return cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * size + 1, 2 * size + 1), (size, size)
    )


def _bin255(mask: np.ndarray) -> np.ndarray:
    """任意掩膜 → uint8 0/255（下游统一按 ``>0`` 计数）。"""
    return ((np.asarray(mask) > 0).astype(np.uint8)) * 255


def _as_binary_mask(mask, shape, name: str) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2 or arr.shape[:2] != shape:
        raise ValueError(f"{name} 形状 {arr.shape} 与裁剪区 {shape} 不一致")
    return _bin255(arr)


def _as_rgb_image(crop_rgb) -> np.ndarray:
    arr = np.asarray(crop_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"crop_rgb 必须是 HxWx3 的 RGB 图，收到 {arr.shape}")
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr)


def _as_rgb(rgb) -> List[int]:
    try:
        vals = [int(round(float(v))) for v in rgb]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"fg_rgb 必须是 3 个数值，收到 {rgb!r}") from exc
    if len(vals) != 3:
        raise ValueError(f"fg_rgb 必须是 3 个数值，收到 {rgb!r}")
    return [min(255, max(0, v)) for v in vals]


def _background_rgb(crop_rgb: np.ndarray, bound: np.ndarray) -> List[int]:
    """几何范围外的像素中位色作背景基准；范围盖满裁剪区时退回全图中位色。"""
    outside = bound == 0
    px = crop_rgb[outside] if outside.any() else crop_rgb.reshape(-1, 3)
    med = np.median(px, axis=0).astype(int)
    return [int(med[0]), int(med[1]), int(med[2])]


def _color_hint_mask(crop_rgb: np.ndarray, fg_rgb, bg_rgb) -> np.ndarray:
    """逐像素判给更近前景色的那批（LAB 近邻）；前景=背景时返回全零。"""
    lab = _lab(crop_rgb)
    fg_lab = _rgb_to_lab(np.array(fg_rgb, np.float32).reshape(1, 3)).reshape(1, 1, 3)
    bg_lab = _rgb_to_lab(np.array(bg_rgb, np.float32).reshape(1, 3)).reshape(1, 1, 3)
    return ((_lab_dist(lab, fg_lab) < _lab_dist(lab, bg_lab)).astype(np.uint8)) * 255


def _finalize(cand_u8: np.ndarray, kernel: np.ndarray, bound_u8: np.ndarray):
    """膨胀候选后与同样膨胀的几何范围相交。

    返回 ``(erase, leak_area)``：``erase`` 必然 ⊆ ``bound_u8``（相交是执行
    保证，不靠膨胀单调性口头断言）；``leak_area`` 是相交前落在几何范围外的
    像素数，仅作诊断。
    """
    dilated = cv2.dilate(cand_u8, kernel)
    leak_area = int(((dilated > 0) & (bound_u8 == 0)).sum())
    erase = cv2.bitwise_and(dilated, bound_u8)
    return erase, leak_area


def refine_text_mask(
    crop_rgb,
    fill_mask,
    prob_crop=None,
    fg_rgb=None,
    dilate_size: int = MASK_DILATE_SIZE,
    include_classical: bool = False,
) -> TextMaskCandidates:
    """在几何范围内算文字擦除掩膜候选。

    Args:
        crop_rgb: 块裁剪区 **RGB** 图（``utils/io_utils.py::imread`` 口径，HxWx3）。
        fill_mask: 块几何掩膜（lines 填充多边形，任意非零即有效），形状同裁剪区。
        prob_crop: 同分辨率概率图 crop（0~1 float，如检测器 DBNet 前向输出）；
            None 时 ``prob`` 候选为 None。
        fg_rgb: 可选前景（文字）色 RGB。给定后才产生 ``color`` 候选；背景
            基准取几何范围外像素中位色。
        dilate_size: 膨胀核半径（默认 ``MASK_DILATE_SIZE``，与检测器
            ``mask dilate size`` 同口径）；0 表示不膨胀。
        include_classical: 是否额外产出经典 Otsu 对照候选（默认 False）。
            它**只是对照**：不参与 ``prob``/``color``，也不与它们求并。

    Returns:
        ``TextMaskCandidates``；``diagnostics`` 键（面积单位＝裁剪区像素）：

        - ``fill_area``/``baseline_area``：几何范围面积（膨胀前/后）；
        - ``{prob,color,classical}_available``：该路候选是否存在；
        - ``{...}_area``/``{...}_reduce``：候选面积及相对 baseline 的缩减比；
        - ``{...}_leak_before_intersect``：相交前越出几何范围的像素数
          （贪心膨胀留下的，会被相交裁掉，只报告不沉默）；
        - ``{...}_within_baseline``：相交后是否完全落在几何范围内（恒为 True，
          作硬不变式的自检证据）；
        - ``color_overlap_with_prob``：有 color 且有 prob 时，二者重合面积
          （描述量，用于人工判断两路口径是否打架，不作为可靠性证据）。

    Raises:
        ValueError: 形状不匹配（fill/prob 与裁剪区）或 ``fg_rgb`` 非 3 数值。
    """
    crop = _as_rgb_image(crop_rgb)
    h, w = crop.shape[:2]
    if h < 1 or w < 1:
        raise ValueError(f"crop_rgb 不能为空图，收到 {crop.shape}")
    fill = _as_binary_mask(fill_mask, (h, w), "fill_mask")

    prob_src = None
    if prob_crop is not None:
        prob_src = np.asarray(prob_crop)
        if prob_src.shape[:2] != (h, w):
            raise ValueError(
                f"prob_crop 形状 {prob_src.shape} 与裁剪区 {(h, w)} 不一致"
            )

    kernel = _ellipse_kernel(dilate_size)
    baseline = _bin255(cv2.dilate(fill, kernel))
    base_area = int((baseline > 0).sum())

    diagnostics: Dict = {
        "crop_hw": [h, w],
        "dilate_size": int(dilate_size),
        "prob_bin_thresh": float(PROB_BIN_THRESH),
        "fill_area": int((fill > 0).sum()),
        "baseline_area": base_area,
        "prob_available": False,
        "color_available": False,
        "classical_available": False,
    }

    def _reduce_of(area: int) -> float:
        # 空几何范围无「缩减」可言，报 0 而不是用 max(1,·) 兜出一个假的 1.0
        if base_area == 0:
            return 0.0
        return round(1.0 - area / base_area, 4)

    def _record(name: str, erase: np.ndarray, leak_area: int) -> None:
        area = int((erase > 0).sum())
        diagnostics[f"{name}_available"] = True
        diagnostics[f"{name}_area"] = area
        diagnostics[f"{name}_reduce"] = _reduce_of(area)
        diagnostics[f"{name}_leak_before_intersect"] = leak_area
        diagnostics[f"{name}_within_baseline"] = not bool((erase & ~baseline).any())

    # ── prob-only：只来自概率图，不掺 Otsu ──────────────────────────────────
    prob_cand = None
    if prob_src is not None:
        prob_bin = _bin255(prob_src >= PROB_BIN_THRESH)
        prob_cand, leak = _finalize(
            cv2.bitwise_and(prob_bin, fill), kernel, baseline
        )
        _record("prob", prob_cand, leak)

    # ── color hint：只来自给定前景色 ────────────────────────────────────────
    color_cand = None
    if fg_rgb is not None:
        fg = _as_rgb(fg_rgb)
        bg = _background_rgb(crop, baseline)
        diagnostics["color_fg_rgb"] = fg
        diagnostics["color_bg_rgb"] = bg
        color_raw = cv2.bitwise_and(_color_hint_mask(crop, fg, bg), fill)
        color_cand, leak = _finalize(color_raw, kernel, baseline)
        _record("color", color_cand, leak)

    # ── classical：显式要求的对照，绝不并进上面的候选 ───────────────────────
    classical_cand = None
    if include_classical:
        ink = _glyph_mask_classical(crop)
        ink_u8 = _bin255(ink) if ink is not None else np.zeros((h, w), np.uint8)
        classical_cand, leak = _finalize(
            cv2.bitwise_and(ink_u8, fill), kernel, baseline
        )
        _record("classical", classical_cand, leak)

    # ── 描述量：两路口径是否打架（不作可靠性证据） ──────────────────────────
    if prob_cand is not None and color_cand is not None:
        diagnostics["color_overlap_with_prob"] = int(
            ((color_cand > 0) & (prob_cand > 0)).sum()
        )

    return TextMaskCandidates(
        baseline=baseline,
        prob=prob_cand,
        color=color_cand,
        classical=classical_cand,
        diagnostics=diagnostics,
    )
