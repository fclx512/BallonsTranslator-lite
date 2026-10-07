"""块级字体颜色自动提取（纯 numpy/cv2，无模型依赖）。

思路：文本检测/OCR 模型本身不出颜色，但「文字掩膜 → 前景/背景取色」这一步
几乎全是代码逻辑，掩膜来源可以与检测模型解耦：

- 首选 DBNet 概率图（PP-OCRv6 检测前向的原始输出），阈值化即得笔画级掩膜；
- 无概率图时退化为经典二值化：以块内非文字像素的中位色为背景基准，
  对色距图做 Otsu 阈值分离前景。

前景取掩膜内像素中位数（对边缘反锯齿混合像素与少量背景串色稳健），
背景取掩膜外像素中位数。置信度综合三项：fg/bg 色距（LAB CIE76，同
``utils/imgproc_utils.py::color_difference`` 口径）、前景色纯度、掩膜覆盖率。
置信度不足时调用方应放弃该结果（回落全局样式色），不硬填。

**通道口径是 RGB**（与 ``utils/io_utils.py::imread`` 一致——项目管线
``utils/proj_imgtrans.py::read_img`` 交给检测器的就是它）。输入/输出均为
RGB 列表，与 ``utils/fontformat.py::FontFormat`` 的 frgb 字段同口径；
给 cv2 显示/写图的调用方自行[::-1]转 BGR。（2026-10-03 教训：首版按 BGR
设计、探针用 cv2.imread 掩盖了口径差，应用管线里颜色整体反转。）

可选 ``palette``（项目人工取样调板，``utils/proj_imgtrans.py::ProjImgTrans 的人工取样调板 fg_palette``,
条目为 ``{"rgb": [r,g,b], "role": str}``、兼容旧裸三元组）：
启发式挑簇后按最近原则吸附到调板色——最近候选在吸附距离内时直接取调板色
（人工采样的权威性高于启发式），专治「误取背景色/描边色」类系统性偏差。
"""

from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

# ── 可调参数（经样张实测调定；调参跑 scripts/probes/probe_color_extract.py）──

# 概率图二值化阈值。DBNet soft mask 在笔画核心接近 1.0、边缘渐变，
# 0.5 折中：过高丢细笔画、过低把背景串进来。
PROB_BIN_THRESH = 0.5
# 前景像素数下限：低于此值统计不稳定，直接放弃。
MIN_TEXT_PIXELS = 24
# 掩膜覆盖率（文字像素 / 块外接矩形面积）的合理区间；过低=掩膜没盖住文字，
# 过高=掩膜漫出文字区（常见于背景复杂时概率图成片）。
COVERAGE_LOW = 0.01
COVERAGE_HIGH = 0.75
# 经典二值化的背景基准带宽（外接矩形四边各占该比例的像素）。
BORDER_BAND_RATIO = 0.08
# 两簇色距低于该值（LAB）视为单色文本，直接取整体中位数。
CLUSTER_MERGE_DIST = 30.0
# 彩度低于此值（cv2 LAB a/b 距 128 的模）视为「无彩」。
CHROMA_LOW = 14.0
# 两簇彩度差低于此值视为「彩度相当」，改用明度判（更暗者为填充）。
CHROMA_MARGIN = 10.0
# 前景与背景色距低于此值（LAB）视为「同色」：掩膜只圈到背景，结果作废。
FG_BG_MIN_SEP = 20.0
# 簇像素占掩膜像素的最低占比：低于此的簇不参与填充/描边判断。
CLUSTER_MIN_SHARE = 0.08
# 调板吸附距离（LAB）：启发式首选色（或其余候选色）与调板最近色距小于此值
# 才吸附。过大→把背景色也错吸到调板上；过小→吸附形同虚设。
PALETTE_SNAP_DIST = 45.0
# 调板内近重判定（LAB）：画布取样落点与已有调板色距小于此值视为同色，
# 不重复入板（用户连点同一字的多帧像素不会灌出一串近重色）。
PALETTE_DUP_DIST = 10.0
# 全书扫描聚类的合并半径（LAB CIE76）。**必须 ≤ PALETTE_SNAP_DIST**：候选色
# 人工勾选入库后跑一次「重新取色（整本）」，整簇块会被吸附成调板原值，
# ``utils/proj_imgtrans.py::speaker_hints_for_blocks`` 的精确相等注入才命中
# （半径超过吸附距离时，离代表色较远的块吸附后仍是别的值，收敛路径漏块）。
CLUSTER_RADIUS = 27.0


@dataclass
class ExtractedBlockColors:
    frgb: list  # 前景（文字填充）色 RGB
    # 背景色 RGB。**不要**写进 FontFormat.srgb——那是描边色视图；此处仅供
    # 置信判据/后续用途（如描边宽判定、纯色修复取色）。
    srgb: list
    confidence: float  # 0~1
    coverage: float  # 文字像素占外接矩形比例（诊断用）


@dataclass
class ColorSample:
    """候选色命中的单个块：样本缩略图与「跳到该块」所需的定位信息。"""

    pagename: str
    block_index: int
    rect: List[int]  # 页像素 xyxy（utils/block_geometry.py::rect_of 口径）


@dataclass
class ColorCluster:
    """一个候选文字色：代表色 + 命中块数 + 少量样本（纯数据，不落项目 JSON）。

    代表色是**众数（真实观测值）**而非均值/中位数：注入判据是
    ``utils/proj_imgtrans.py::speaker_hints_for_blocks`` 的**精确相等**，
    均值往往不对应任何块的真实颜色，写进调板后一个块都不命中且静默失效。
    """

    rgb: List[int]  # 众数（真实观测值）
    count: int  # 命中块数（不是不同色数）
    samples: List[ColorSample]


def _lab(img_rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)


def _lab_dist(lab: np.ndarray, color_lab: np.ndarray) -> np.ndarray:
    """逐像素到参考色的近似 CIE76 色距（L 通道同 color_difference 折权）。"""
    diff = lab - color_lab.reshape(1, 1, 3)
    diff[..., 0] *= 0.392
    return np.linalg.norm(diff, axis=2)


def _median_rgb(px: np.ndarray) -> Optional[list]:
    if px.shape[0] == 0:
        return None
    med = np.median(px, axis=0).astype(int)
    return [int(med[0]), int(med[1]), int(med[2])]


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(N,3) RGB float → LAB float32（cv2 口径：L/a/b 各 0~255 域）。"""
    u8 = np.clip(rgb, 0, 255).astype(np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(u8, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)


def _palette_rgbs(palette) -> list:
    """调板 → RGB 列表（兼容裸三元组与 {"rgb": …, "role": …} 条目）。"""
    rgbs = []
    for entry in palette or []:
        value = entry.get("rgb") if isinstance(entry, dict) else entry
        if value is None:
            continue
        try:
            rgb = [int(v) for v in list(value)[:3]]
        except (TypeError, ValueError):
            continue
        if len(rgb) == 3:
            rgbs.append(rgb)
    return rgbs


def nearest_palette_entry(frgb: list, palette) -> Optional[tuple]:
    """frgb 到调板的最近条目，返回 (距离, 调板下标)；调板为空返回 None。"""
    rgbs = _palette_rgbs(palette)
    if not rgbs:
        return None
    dists = [color_difference_lab(frgb, c) for c in rgbs]
    idx = int(np.argmin(dists))
    return float(dists[idx]), idx


def _snap_to_palette(frgb: list, palette) -> Optional[list]:
    entry = nearest_palette_entry(frgb, palette)
    if entry is None or entry[0] > PALETTE_SNAP_DIST:
        return None
    return [int(v) for v in _palette_rgbs(palette)[entry[1]]]


def _two_color_split(px_rgb: np.ndarray):
    """把掩膜像素确定性地分成两个颜色簇，返回 (labels, centers_rgb)。

    不用 ``cv2.kmeans``：kmeans++ 初始化带随机性，固定种子后结果又取决于
    种子运气（实测同一块会聚成「白+粉」或「白粉混合+背景」两种）。改为取
    颜色直方图最高峰，再取与之色距足够远的次高峰作两个中心，各像素就近
    归属后重算两轮——无随机、贴合「文字=填充+描边两色」的结构。

    找不到第二个足够远的峰（单色文本）时返回 None。
    """
    quant = (px_rgb // 16).astype(np.uint8)
    keys, counts = np.unique(quant, axis=0, return_counts=True)
    reps = keys.astype(np.float32) * 16.0 + 8.0
    reps_lab = _rgb_to_lab(reps)
    order = np.argsort(-counts)

    c1 = reps_lab[order[0]]
    chosen = None
    for idx in order[1:]:
        d = reps_lab[idx] - c1
        d[0] *= 0.392
        if float(np.linalg.norm(d)) >= CLUSTER_MERGE_DIST:
            chosen = idx
            break
    if chosen is None:
        return None

    centers_lab = np.stack([c1, reps_lab[chosen]])
    centers_rgb = np.stack([reps[order[0]], reps[chosen]])
    labels = np.zeros(px_rgb.shape[0], np.int32)
    for _ in range(2):
        lab_px = _rgb_to_lab(px_rgb)
        d0 = lab_px - centers_lab[0]
        d0[:, 0] *= 0.392
        d1 = lab_px - centers_lab[1]
        d1[:, 0] *= 0.392
        labels = (np.linalg.norm(d1, axis=1) < np.linalg.norm(d0, axis=1)).astype(
            np.int32
        )
        for c in (0, 1):
            sel = px_rgb[labels == c]
            if sel.shape[0]:
                centers_rgb[c] = np.median(sel, axis=0)
        centers_lab = _rgb_to_lab(centers_rgb)
    return labels, centers_rgb


def _pick_fill_cluster(crop_rgb: np.ndarray, mask: np.ndarray, palette=None):
    """在掩膜像素里分离「文字填充色」与「描边色」，返回 (fg_rgb, pick_conf)。

    掩膜（DBNet 概率图）给的是**文本区域**（填充+描边+晕）而非字形掩膜，
    几何判据（厚度/贴边/芯部）在本内容上实测均不可靠，因此按颜色本身判：

    - 漫画惯例：白（低彩度）多用于描边/光晕，**彩度高的那簇为填充**；
    - 两簇都无彩（黑字白边这类）时取**更暗**的一簇；
    - 空心字（描边内露出页面底色）天然落进第一条：底色彩度低、描边彩度高，
      选出的是描边色——正是要渲染的文字色。

    两簇色距过近（CLUSTER_MERGE_DIST）视为单色文本，直接取整体中位数。

    有调板（palette）时按最近原则校正：启发式首选色在吸附距离内→取调板色；
    首选色不在且首选簇**低彩**（疑似气泡底/描边被误挑为填充）时，改试有彩
    的候选簇吸附调板色——首选簇有彩（ heuristic 挑到了真颜色）时信任启发式，
    防止调板反把黑字白边这类正确判定吸错。
    """
    px = crop_rgb[mask > 0]

    split = _two_color_split(px)
    if split is None:
        fg, conf = _median_rgb(px), 1.0
        snapped = _snap_to_palette(fg, palette) if palette else None
        return (snapped, max(conf, 0.9)) if snapped is not None else (fg, conf)

    labels, centers_rgb = split
    centers_lab = _rgb_to_lab(centers_rgb)
    share = np.bincount(labels, minlength=2) / max(1, len(labels))

    # 彩度（cv2 LAB 域 a/b 以 128 为中心）与明度
    chroma = np.linalg.norm(centers_lab[:, 1:] - 128.0, axis=1)
    lightness = centers_lab[:, 0]

    valid = [c for c in range(2) if share[c] >= CLUSTER_MIN_SHARE]
    if not valid:
        fg, conf = _median_rgb(px), 1.0
        snapped = _snap_to_palette(fg, palette) if palette else None
        return (snapped, max(conf, 0.9)) if snapped is not None else (fg, conf)
    if len(valid) == 1:
        best = runner = valid[0]
        pick_conf = min(1.0, 0.5 + share[best] / 2.0)
    else:
        ranked = sorted(valid, key=lambda c: chroma[c])
        low, high = ranked[0], ranked[1]
        chroma_gap = chroma[high] - chroma[low]
        if chroma[high] < CHROMA_LOW or chroma_gap < CHROMA_MARGIN:
            # 都无彩或彩度接近：取更暗的一簇（黑字白边）
            if lightness[low] <= lightness[high]:
                best, runner = low, high
            else:
                best, runner = high, low
            pick_conf = min(1.0, abs(lightness[runner] - lightness[best]) / 60.0)
        else:
            best, runner = high, low  # 彩度高者为填充
            pick_conf = min(1.0, 0.4 + chroma_gap / 60.0)

    r, g, b = (int(v) for v in centers_rgb[best])
    fg = [r, g, b]
    if palette:
        snapped = _snap_to_palette(fg, palette)
        if snapped is not None:
            return snapped, float(max(pick_conf, 0.9))
        # 启发式首选离调板远时，只在「首选簇低彩（多半是气泡底/描边被误当
        # 填充）」且候选簇有彩（palette 里的白/黑不会反吸走黑字白边的正确
        # 判定）时改选候选簇——防止调板反把对的结果吸错。
        if chroma[best] < CHROMA_LOW:
            for c in valid:
                if c == best or chroma[c] < CHROMA_LOW:
                    continue
                snapped = _snap_to_palette(
                    [int(v) for v in centers_rgb[c]], palette
                )
                if snapped is not None:
                    return snapped, 0.9

    return fg, float(min(1.0, max(0.0, pick_conf)))


def _glyph_mask_from_prob(prob_crop: np.ndarray) -> Optional[np.ndarray]:
    if prob_crop is None or prob_crop.size == 0:
        return None
    mask = (prob_crop >= PROB_BIN_THRESH).astype(np.uint8)
    if mask.sum() == 0:
        return None
    # 收缩 1px：概率图边缘是渐变带，阈值化后最外圈像素多为混合色
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)
    return mask if mask.sum() > 0 else None


def _glyph_mask_classical(img_rgb: np.ndarray) -> Optional[np.ndarray]:
    """无概率图时的兜底：背景基准取外接矩形边带中位色，色距图 Otsu 分离。"""
    h, w = img_rgb.shape[:2]
    band = max(2, int(round(min(h, w) * BORDER_BAND_RATIO)))
    border = np.zeros((h, w), bool)
    border[:band, :] = True
    border[-band:, :] = True
    border[:, :band] = True
    border[:, -band:] = True
    bg = np.median(img_rgb[border], axis=0)
    lab = _lab(img_rgb)
    bg_lab = cv2.cvtColor(bg.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB)
    dist = _lab_dist(lab, bg_lab.astype(np.float32))
    dist_u8 = np.clip(dist / 255.0 * 255, 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = (mask > 0).astype(np.uint8)  # 统一成 0/1，下游按 1 计数
    if mask.sum() == 0:
        return None
    return mask


def extract_block_colors(
    img_rgb: np.ndarray,
    xyxy,
    prob: Optional[np.ndarray] = None,
    palette=None,
) -> Optional[ExtractedBlockColors]:
    """对单个块的外接矩形提取前景/背景色。

    Args:
        img_rgb: 整页 **RGB** 图（``utils/io_utils.py::imread`` 口径）。
        xyxy: 块外接矩形 ``(x1, y1, x2, y2)``（图像坐标系，自动裁剪到图内）。
        prob: 整页分辨率的对齐概率图（0~1 float，如 DBNet 前向输出上采样）；
            None 时走经典二值化兜底。
        palette: 项目人工取样调板（条目 ``{"rgb": [r,g,b], "role": str}``，
            兼容旧裸三元组；``utils/proj_imgtrans.py::ProjImgTrans`` 的
            ``fg_palette`` 字段），最近原则吸附。

    Returns:
        提取结果；统计不稳定（文字像素过少等）时返回 None。
    """
    im_h, im_w = img_rgb.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in xyxy]
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, im_w), min(y2, im_h)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    crop = img_rgb[y1:y2, x1:x2]

    prob_crop = prob[y1:y2, x1:x2] if prob is not None else None
    mask = _glyph_mask_from_prob(prob_crop)
    if mask is None:
        mask = _glyph_mask_classical(crop)
    if mask is None:
        return None

    n_text = int(mask.sum())
    if n_text < MIN_TEXT_PIXELS:
        return None

    bg_mask = mask == 0
    if not bg_mask.any():
        return None

    frgb, pick_conf = _pick_fill_cluster(crop, mask, palette=palette)
    srgb = _median_rgb(crop[bg_mask])
    if frgb is None or srgb is None:
        return None

    # 前景与所在区域背景几乎同色 = 掩膜只圈到了背景（小碎块常见），
    # 真文字不会与背景同色 → 直接否决，别落一个看不见的颜色。
    if color_difference_lab(frgb, srgb) < FG_BG_MIN_SEP:
        return None

    coverage = n_text / float(mask.size)
    if coverage < COVERAGE_LOW:
        cov_factor = coverage / COVERAGE_LOW * 0.5
    elif coverage > COVERAGE_HIGH:
        cov_factor = max(0.2, 1.0 - (coverage - COVERAGE_HIGH))
    else:
        cov_factor = 1.0

    confidence = cov_factor * pick_conf
    return ExtractedBlockColors(
        frgb=frgb, srgb=srgb, confidence=float(confidence), coverage=float(coverage)
    )


def color_difference_lab(rgb1: list, rgb2: list) -> float:
    """LAB CIE76 色距（RGB 输入），与 utils/imgproc_utils.py::color_difference 同口径。"""
    c1 = np.array(rgb1, dtype=np.uint8).reshape(1, 1, 3)
    c2 = np.array(rgb2, dtype=np.uint8).reshape(1, 1, 3)
    lab1 = cv2.cvtColor(c1, cv2.COLOR_RGB2LAB).astype(np.float64)
    lab2 = cv2.cvtColor(c2, cv2.COLOR_RGB2LAB).astype(np.float64)
    diff = lab1 - lab2
    diff[..., 0] *= 0.392
    return float(np.linalg.norm(diff))


def cluster_font_colors(
    entries, radius=CLUSTER_RADIUS, max_samples=4
) -> List[ColorCluster]:
    """把 ``(rgb_like, ColorSample)`` 流贪心聚成候选色簇（纯函数、确定性）。

    ``entries`` 为 ``Iterable[Tuple[rgb_like, ColorSample]]``。规则：

    - rgb 过 ``utils/proj_imgtrans.py::normalize_rgb`` 失败（长度非 3、越界、
      非数值）→ 丢弃该条；float numpy 数组按 ``int()`` 截断后参与比较
      （与 ``speaker_hints_for_blocks`` 的取整口径一致）；
    - 先按「同一 rgb 的块数」降序处理，同数按 rgb 值元组升序 → 处理顺序稳定，
      输入顺序不影响结果；
    - 每条归入**第一个**代表色与之 LAB CIE76 距离 ≤ ``radius`` 的簇，
      否则自建新簇（距离一律复用 ``color_difference_lab``，不另立口径）；
    - 簇代表色 = 建簇时的那条 rgb。按块数降序处理保证了它必是该簇内出现次数
      最多的真实色（众数）——均值不是任何块的真实颜色，写进调板不命中
      精确相等注入，故不用；
    - ``count`` = 簇内块数（不是不同色数）；
    - ``samples`` 按 (页名, 块下标) 升序取前 ``max_samples`` 条。

    半径默认 ≤ ``PALETTE_SNAP_DIST``：候选入库后跑「重新取色（整本）」时整簇块
    会被吸附成调板原值，精确相等注入才收敛命中（见模块常量处说明）。

    返回按 ``count`` 降序（同数按 rgb 升序），顺序确定。
    """
    from utils.proj_imgtrans import normalize_rgb  # 延迟导入保持本模块轻量

    grouped = {}  # rgb 元组 -> [ColorSample, ...]
    for rgb_like, sample in entries:
        rgb = normalize_rgb(rgb_like)
        if rgb is None or sample is None:
            continue
        grouped.setdefault(tuple(rgb), []).append(sample)

    # [代表色 list, 样本 list]；建簇顺序即「第一个命中」的查找顺序
    clusters = []
    for rgb_tuple, samples in sorted(
        grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])
    ):
        rgb = list(rgb_tuple)
        members = None
        for rep, cluster_samples in clusters:
            if color_difference_lab(rgb, rep) <= radius:
                members = cluster_samples
                break
        if members is None:
            clusters.append([rgb, list(samples)])
        else:
            members.extend(samples)

    cap = max(0, int(max_samples))
    result = [
        ColorCluster(
            rgb=list(rep),
            count=len(members),
            samples=sorted(members, key=lambda s: (s.pagename, s.block_index))[:cap],
        )
        for rep, members in clusters
    ]
    result.sort(key=lambda c: (-c.count, tuple(c.rgb)))
    return result


def scan_project_colors(proj, radius=CLUSTER_RADIUS, max_samples=4) -> dict:
    """扫全书块上已提取的字体色 → 候选色簇（不重跑检测、不重跑管线）。

    数据源是 ``proj.pages`` 里每块的 ``fg_colors``（检测阶段
    ``modules/textdetector/base.py::extract_colors`` 写入；可能是 list，也可能是
    float numpy 数组，统一走 ``utils/proj_imgtrans.py::normalize_rgb`` 校验 +
    int 截断）。页按 ``proj.pages`` 自身顺序遍历，块按列表先后稳定。

    返回 ``{"clusters": List[ColorCluster], "total_blocks": int,
    "colored_blocks": int, "uncolored_blocks": int}``：``total_blocks`` 为所有块；
    ``colored_blocks`` 为 rgb 合法**且** ``utils/block_geometry.py::rect_of``
    成功的块（缺坐标的块做不了样本缩略图/跳转）；其余都算 ``uncolored_blocks``
    （UI 文案按「未取到颜色/无坐标」笼统说即可）。

    结果只是**草稿候选**，绝不自动写入调板；人工勾选后才入库。
    """
    from utils.block_geometry import rect_of  # shapely 较重，用到才导入
    from utils.proj_imgtrans import normalize_rgb

    pages = getattr(proj, "pages", None) or {}
    entries = []
    total_blocks = 0
    colored_blocks = 0
    for pagename, blocks in pages.items():
        for block_index, blk in enumerate(blocks or []):
            total_blocks += 1
            rgb = normalize_rgb(getattr(blk, "fg_colors", None))
            rect = rect_of(blk)
            if rgb is None or rect is None:
                continue
            colored_blocks += 1
            entries.append(
                (
                    rgb,
                    ColorSample(
                        pagename=pagename, block_index=block_index, rect=rect
                    ),
                )
            )

    return {
        "clusters": cluster_font_colors(
            entries, radius=radius, max_samples=max_samples
        ),
        "total_blocks": total_blocks,
        "colored_blocks": colored_blocks,
        "uncolored_blocks": total_blocks - colored_blocks,
    }
