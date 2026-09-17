"""区域再检测：人工拉一个矩形 → 只在矩形内跑一次「文本检测 + OCR」。

**要解决的问题**（2026-09-17 实测，见 ``tmp/区域再检测_方案_2026-09-17.md``）：
用户实际工作流是 ``ysgyolo`` 检测 + ``paddleocr_v6`` 识别。ysgyolo 干净但会成片
漏检**横排文本**（标题、旁白、手机屏内的字）——实测漏检率横排 20.5% vs 竖排
7.7%；而全量双跑复核没有油水（两模型一致率 88%~92%）。**人工指定区域的局部
重跑实测 3/3 全部补回**，机制是裁剪本身带来的"相对放大"红利：
``ppocrv6_onnx`` 的参数是 ``det_limit_side_len=960`` / ``limit_type='max'``，而
``ballontrans_pylibs_win/Lib/site-packages/onnxocr/operators.py`` 的
``DetResizeForTest`` **max 模式只缩小不放大** → 全页 1032×1457 被压到 0.659×，
裁剪图按原尺寸送检，等效比全页大 1.5 倍。**主动再放大 2×/3× 实测零增益，故
本模块不提供放大参数**（避免没有实测收益的复杂度）。

**两阶段**（与 ``ui/batch_*.py`` 同形）：

1. ``plan(page_key, rect)``——**只读**：裁剪（外扩 ``RedetectConfig.expand``，
   实测 24px 够用）→ 跑检测器 → 坐标回映射 → 区域外过滤 → 替换决策 →
   产出 ``RedetectPlan``（可核对、将来工作台可直接复用为审批材料）；
2. ``build_page(plan)``——**纯函数**：把"新增 + 替换"算进页块列表（含阅读
   顺序插入），不改任何东西，便于单测与离线复算；
   ``apply(plan)``＝``build_page`` + 数据层写回（``proj.pages`` + 页级掩码）。

**它不是 UI 逻辑**：本模块不 import 任何 Qt widget，工作台将来的"对选中区域
批量重检测"应直接复用 ``RegionRedetect``；手势、撤销、通知在
``ui/region_redetect_tool.py``。

**七项已拍板的设计**（见方案文档第三节，逐条对应）：

- 区域外过滤：**按四边形中心点**是否落在用户框内收块（外扩区实测会带进 1~2
  个无关框）；
- 与已有块重叠 **> 50%** 的已有块**替换**，其余新增；判据的分子是交集面积、
  分母取**两者较小者**（``utils/block_geometry.py::poly_overlap_ratio``）——
  这样"新块落在已有块里"（ysgyolo 的区域级大框吃掉了整块文字）与"已有块落在
  新块里"都能识破；不弹窗，靠"结果可见 + 一步撤销"；
- 掩码：检测时顺手把裁剪图 mask 贴回页级（决策 6），否则后续修复不会清掉原文，
  译文会叠在原文上；
- 新块样式：**继承同页最近的已有块**的 ``fontformat``（决策 7，必需而非锦上
  添花——``modules/textdetector/detector_paddlev6.py::PPOCRv6Detector`` 的
  第 5 步 ``sz = blk._detected_font_size * fnt_rsz`` 会把 ``examine_textblk``
  刚算好的字号覆盖成 ``-1``，新块从检测器拿不到可用字号）；
  **字号例外**：回映射后**重跑一遍** ``utils/textblock.py::examine_textblk``
  （它在裁剪坐标系里算出的字号/角度/距离对页级坐标同样有效，只是被检测器覆盖
  掉了），拿到按实际检出框量出来的字号，再写回 ``_detected_font_size``——
  继承字号只是碰运气，量出来的更准；
- 阅读顺序：**不整页重排**，按坐标插入（见 ``insert_index``）；失误一律兜底为
  追加到末尾，语义等同"新增文本框"；
- 检测器：由 ``pcfg.region_redetect_detector`` 指定（默认 ``ppocrv6_onnx``），
  不频繁修改；**不预热**，只要 UI 层给明确的加载提示。

**几何判据一律按 ``lines`` 四边形算，不用 ``xyxy``**——后者只是轴对齐外接
矩形，倾斜框（DB 四边形实测 11.9°~16.7°）用它做区域过滤与重叠判据会误判邻居。

**内存（2026-09-17 实测；改之前一次手势能让 app 从 600MB 涨到 1700MB 且不回落）**：

- 元凶是**给这条路径新建的 ONNX Runtime CUDA 会话**：同一个小裁剪，
  ``CUDAExecutionProvider`` 一次 ``detect`` 就让主机工作集 **+835MB**（会话建立
  +165MB，首次推理再 +670MB —— cuDNN 的卷积 workspace 与 arena），而
  ``unload_model()`` 实测**只回收 50MB**（GPU 侧倒是能还：``nvidia-smi``
  692→434MiB）。cudnn 搜索模式（EXHAUSTIVE→DEFAULT/HEURISTIC）、
  ``enable_cpu_mem_arena``、``arena_extend_strategy`` 三个开关都试过，**只把峰值
  从 835 压到 730，量级不变** ⇒ 事后"清缓存"救不回来，唯一有效的手段是别用 CUDA
  建它。
- 故本模块**默认让检测器跑 CPU**（``RedetectConfig.device`` /
  ``pcfg.region_redetect_device``）：同一裁剪 **+89~134MB** 且卸载能回收 76MB，
  单次推理 19~93ms（CUDA 7~21ms，一次手势只差约 20ms），会话重建仅 0.17s。
- 会话存活策略＝**用完即卸**：``ui/region_redetect_tool.py::RegionRedetectTool``
  在每次手势收尾时调 ``unload_detector``，不把会话留成常驻内存。
- 实测脚本：``tmp/_mem_probe.py``（归因）、``tmp/_mem_probe2.py``（会话配置对照）、
  ``tmp/_mem_probe3b.py``（CPU/CUDA 耗时对照）。

**与批量基础设施的关系**：本功能是**单页单手势**，撤销走画布撤销栈，
**不碰** ``utils/batch_versions.py``（那是批量的唯一备份口，2026-09-16 拍板
归并）。
"""

import copy
import os
import os.path as osp
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from utils.block_geometry import (
    overlap_ratio,
    poly_bands,
    poly_center,
    poly_of,
    poly_overlap_ratio,
)
from utils.config import pcfg
from utils.io_utils import imread
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock, examine_textblk

Rect = List[int]


class RegionRedetectError(RuntimeError):
    """区域再检测的前置条件不成立（检测器取不到、页面无图等）。"""


# plan() 的跳过原因码（不含用户可见文案，UI 层自行翻译）
SKIP_TOO_SMALL = "too-small"
"""用户框太小（任一边 < ``RedetectConfig.min_side``）——拖框误触。"""

SKIP_NO_IMAGE = "no-image"
"""页面原图读不到（缺图／项目目录失效）。"""

SKIP_NO_DETECTION = "no-detection"
"""检测器没检出任何块，或检出的块全被区域外过滤剔除。"""

# insert_index 的落点依据（进报告，便于复算与调试）
INSERT_BEFORE = "before"
"""插到了某个已有块之前（坐标插入成功，正常路径）。"""

INSERT_FEW_BLOCKS = "few-blocks"
"""页上已有块 < ``RedetectConfig.min_neighbors``：没有可参照的邻居，追加到末尾。"""

INSERT_APPEND = "append"
"""扫完全表都判不出先后（位置重合／方向矛盾），追加到末尾。"""

INSERT_ERROR = "error"
"""插入点计算抛异常，追加到末尾（宁可不理想也不插错位置）。"""


@dataclass
class RedetectConfig:
    """区域再检测的阈值（方案文档的实测口径，全部集中在此）。"""

    expand: int = 24
    """裁剪外扩像素数（实测 24px 够用：外扩区带进的无关框由区域过滤剔除）。"""

    min_side: int = 8
    """用户框任一边小于该值即当作误触，不做任何事。"""

    replace_overlap: float = 0.5
    """替换阈值（决策 4）：与已有块重叠率 **> 该值** 的已有块被替换。"""

    min_neighbors: int = 2
    """插入点算法的下限：页上已有块少于该数时一律追加到末尾。"""

    device: Optional[str] = None
    """检测器跑在哪个设备；``None``＝读 ``pcfg.region_redetect_device``。

    **默认解析为 ``"cpu"``**：实测（``tmp/_mem_probe2.py``／``tmp/_mem_probe3b.py``）
    同一个小裁剪，ONNX Runtime 的 CUDA 会话一次就吃 +835MB 主机工作集、且
    ``unload_model`` 只回收 50MB（cuDNN workspace／arena 已长在进程里），CPU 会话
    只 +89~134MB 且卸载可回收；单次推理 CPU 19~93ms vs CUDA 7~21ms——一次手势
    只差约 20ms。实测依据见模块 docstring 的「内存」一节。
    """


@dataclass
class RedetectPlan:
    """一次区域再检测的只读产物（``build_page`` 的输入）。"""

    page_key: str
    rect: Rect
    """用户框（页级像素，已钳制到页内）。"""

    crop_rect: Rect
    """实际送检的裁剪矩形（用户框外扩 ``expand`` 后钳制到页内）。"""

    new_blocks: List[TextBlock] = field(default_factory=list)
    """检出的新块（坐标已回映射到页级；掩码见 ``mask``）。"""

    replaced_indices: List[int] = field(default_factory=list)
    """被判为"同一片文字、应当替换"的已有块下标（升序）。"""

    mask: Optional[np.ndarray] = None
    """裁剪区尺寸的**页级掩码增量**（``crop_rect`` 大小），缺省无检出。"""

    detector: str = ""
    """本次使用的检测器名（进报告，便于归因）。"""

    dropped_outside: int = 0
    """被区域外过滤剔除的块数（外扩区的无关框）。"""

    skip: Optional[str] = None
    """跳过原因码（``SKIP_*``）；为 ``None`` 表示有可用产出。"""

    @property
    def ok(self) -> bool:
        return self.skip is None and bool(self.new_blocks)


# ── 阅读顺序：坐标插入（方案 §八，含三重兜底）───────────────────────


def page_direction(blocks: Sequence[TextBlock]) -> Tuple[bool, str]:
    """用**当前页已有块的成对先后投票**推断页方向；返回 ``(rtl, basis)``。

    ``ui/region_redetect.py`` 的插入判据不用 ``utils/textblock.py::sort_regions``
    的 ``right_to_left`` 推断——它的判据有已知缺陷（分母是总块数，1 个竖排块
    即判右到左）。这里按块列表序取相邻成对样本：后一块在更左边即投"右到左"。

    ``basis`` 为 ``"vote"``（有成对样本）或 ``"default"``（样本不足——本仓库
    没有独立的阅读方向设置项，回落漫画竖排的通行约定：右到左）。
    """
    votes: List[bool] = []
    prev = None
    for blk in blocks:
        center = poly_center(blk)
        if center is None:
            continue
        if prev is not None and abs(center[0] - prev[0]) > 1e-6:
            votes.append(center[0] < prev[0])
        prev = center
    if not votes:
        return True, "default"
    return sum(1 for v in votes if v) * 2 >= len(votes), "vote"


def _band_overlaps(blk_a, blk_b, axis: str) -> bool:
    """两块在 ``axis`` 上的轴对齐带是否重叠（由四边形顶点取带，见 block_geometry）。"""
    bands_a, bands_b = poly_bands(blk_a), poly_bands(blk_b)
    if bands_a is None or bands_b is None:
        return False
    if axis == "x":
        return overlap_ratio(bands_a[0], bands_a[2], bands_b[0], bands_b[2]) > 0
    return overlap_ratio(bands_a[1], bands_a[3], bands_b[1], bands_b[3]) > 0


def new_precedes(
    new_blk: TextBlock, other: TextBlock, vertical: bool, rtl: bool
) -> Optional[bool]:
    """新块是否应排在 ``other`` **之前**；判不出返回 ``None``。

    成对判据（方案 §八.3，方向一律取**新块**的方向——被摆的是它）：

    - **主方向带不重叠**（竖排看 x 带、横排看 y 带）＝ 两块分处不同列／行：
      竖排按页方向比 x（``rtl`` 大者在前、否则小者在前），横排恒为 y 小者在前；
    - **主方向带重叠**（同一列／同一行内）：竖排比 y 小者在前（列内自上而下），
      横排比 x 小者在前（行内自左而右——页方向只决定列的先后，与行内无关）；
    - 两个方向都重叠（位置几乎重合）→ ``None``，插不下就交给兜底。
    """
    center_new, center_other = poly_center(new_blk), poly_center(other)
    if center_new is None or center_other is None:
        return None
    if vertical:
        if _band_overlaps(new_blk, other, "x"):
            if _band_overlaps(new_blk, other, "y"):
                return None
            return center_new[1] < center_other[1]
        return (
            center_new[0] > center_other[0]
            if rtl
            else center_new[0] < center_other[0]
        )
    if _band_overlaps(new_blk, other, "y"):
        if _band_overlaps(new_blk, other, "x"):
            return None
        return center_new[0] < center_other[0]
    return center_new[1] < center_other[1]


def insert_index(
    new_blk: TextBlock,
    blocks: Sequence[TextBlock],
    rtl: bool,
    *,
    config: Optional[RedetectConfig] = None,
) -> Tuple[int, str]:
    """新块在 ``blocks`` 里的插入下标与依据（方案 §八.2 的扫描 + 三重兜底）。

    ``blocks`` 是**当前**块列表（替换掉旧块的成员已不在其中）；返回值可直接
    交给 ``list.insert``。**宁可顺序不理想，也不能插错位置**：页上块太少、
    全表都判不出先后、计算抛异常，一律追加到末尾（依据码见 ``INSERT_*``）。
    """
    cfg = config or RedetectConfig()
    if len(blocks) < cfg.min_neighbors:
        return len(blocks), INSERT_FEW_BLOCKS
    vertical = bool(getattr(new_blk, "src_is_vertical", False))
    try:
        for idx, other in enumerate(blocks):
            if new_precedes(new_blk, other, vertical, rtl) is True:
                return idx, INSERT_BEFORE
    except Exception as e:  # 单块几何异常不该放大成「整批不做」
        LOGGER.warning(f"Region redetect insertion failed, appending: {e}")
        return len(blocks), INSERT_ERROR
    return len(blocks), INSERT_APPEND


# ── 主流程 ────────────────────────────────────────────────────────


class RegionRedetect:
    """把"用户框内重跑检测"落成"待新增／待替换清单"，并按清单重建页块列表。

    Args:
        proj: 项目实例（只读页面图像与块列表；``apply`` 才写 ``pages``）。
        config: 阈值集合；缺省按方案文档的实测口径。
        detector: 现成的检测器实例（单测注入假检测器用）；缺省按
            ``detector_name``／``pcfg.region_redetect_detector`` 懒建。
        detector_name: 检测器模块名；缺省读配置。
    """

    def __init__(
        self,
        proj: ProjImgTrans,
        *,
        config: Optional[RedetectConfig] = None,
        detector=None,
        detector_name: Optional[str] = None,
    ):
        self.proj = proj
        self.config = config or RedetectConfig()
        # 注入了现成检测器（单测／上层复用同一个实例）时名字以它为准，免得
        # "配置里的名字一对不上就把它卸掉重建"。
        if detector is not None and not detector_name:
            detector_name = getattr(detector, "name", "") or None
        self._explicit_name = detector_name
        self._detector = detector
        self._detector_for = detector_name if detector is not None else None
        self._device_for = None
        # 外部注入的实例（单测／上层复用）由调用方负责生命周期：配置里的检测器
        # 名与设备变化都不该把它丢掉重建。
        self._pinned = detector is not None

    # ── 检测器 ──────────────────────────────────────────────────────

    @property
    def effective_detector_name(self) -> str:
        """当前该用的检测器名（显式给的优先，否则读 ``pcfg`` 的实时值）。"""
        return self._explicit_name or getattr(
            pcfg, "region_redetect_detector", ""
        )

    @property
    def effective_device(self) -> str:
        """检测器该跑在哪个设备（``RedetectConfig.device`` 优先，其次 ``pcfg``）。"""
        device = self.config.device or getattr(
            pcfg, "region_redetect_device", "cpu"
        )
        return str(device or "cpu").strip().lower()

    def detector_loaded(self) -> bool:
        """**当前设置对应的**检测器实例是否已建（未建＝首次触发要建 ONNX 会话，
        UI 须给出「正在加载哪个模型」的明确提示）。检测器或设备改了都会重新变假。"""
        if self._detector is None:
            return False
        if self._detector_for != self.effective_detector_name:
            return False
        return self._pinned or self._device_for == self.effective_device

    def _ensure_detector(self):
        """取检测器实例（不加载权重；``detect`` 自己按需 ``load_model``）。

        ``utils/registries.py::TEXTDETECTORS`` 是惰性注册表（AST 扫描出的
        ``ModuleSpec``），``resolve_module`` 才真正 import 模块。设置里的检测器
        换了就把旧实例卸干净再建（ONNX 会话占显存，不能只丢引用）。

        ``device`` 一律按 ``effective_device`` 覆盖（默认 ``cpu``）——理由与实测
        数字见 ``RedetectConfig.device``；只覆盖本条路径，不动管线检测器与用户配置。
        """
        name = self.effective_detector_name
        device = self.effective_device
        if name and self._detector is not None and self._detector_for == name:
            if self._pinned or self._device_for == device:
                return self._detector
        if self._detector is not None:
            self.unload_detector()
        if not name:
            raise RegionRedetectError("no region-redetect detector configured")
        from utils.registries import TEXTDETECTORS

        module_class = TEXTDETECTORS.resolve_module(name)
        if module_class is None:
            raise RegionRedetectError(f"unknown text detector: {name}")
        params = dict(
            (pcfg.module.get_params("textdetector") or {}).get(name) or {}
        )
        if "device" in params:
            params["device"] = device
        self._detector = module_class(**params)
        self._detector_for = name
        self._device_for = device
        self._pinned = False
        return self._detector

    def unload_detector(self, empty_cache: bool = True) -> None:
        """释放本任务持有的检测器（每次手势收尾、换检测器、关窗时调用）。

        注意**主机工作集回收有限**（实测 CUDA 会话即便卸掉也只还 50MB，见模块
        docstring 的「内存」一节）——不要指望"卸载"能救回 CUDA 会话的开销，
        那条路只能靠"一开始就别用 CUDA"（本模块默认 CPU）。
        """
        detector, self._detector = self._detector, None
        self._detector_for = None
        self._device_for = None
        if detector is None:
            return
        try:
            detector.unload_model(empty_cache=empty_cache)
        except Exception as e:
            LOGGER.warning(f"Failed to unload region-redetect detector: {e}")

    # ── 页面图像 ────────────────────────────────────────────────────

    def page_image(self, page_key: str):
        """页面**原图**（RGB；缺图返回 ``None``，调用方必须兜底）。

        **必须用原图，不能用 ``inpainted_array``**——修复图上原文可能已被清掉，
        再检测什么也检不出来。当前页直接取内存里的 ``img_array``；其他页从磁盘
        读（工作台的批量重检测会走这条路径）。
        """
        if page_key == self.proj.current_img and self.proj.img_valid:
            return self.proj.img_array
        if not self.proj.directory:
            return None
        return imread(osp.join(self.proj.directory, page_key))

    def _page_blocks(self, page_key: str) -> List[TextBlock]:
        return list(self.proj.pages.get(page_key) or [])

    # ── 规划（只读）─────────────────────────────────────────────────

    def plan(self, page_key: str, rect: Sequence[int]) -> RedetectPlan:
        """只读：裁剪 → 检测 → 回映射 → 区域过滤 → 替换决策。

        Args:
            page_key: 页名（项目 ``pages`` 的键）。
            rect: 用户框 ``[x1, y1, x2, y2]``（页级像素）。

        Returns:
            ``RedetectPlan``；``skip`` 非空表示没有可用产出（框太小／缺图／
            零检出／全被区域过滤），此时 ``new_blocks`` 为空。
        """
        try:
            raw = [int(round(float(v))) for v in list(rect)[:4]]
        except (TypeError, ValueError):
            raw = [0, 0, 0, 0]
        normalized = self._normalize_rect(raw)
        if normalized is None:
            return RedetectPlan(page_key, raw, raw, skip=SKIP_TOO_SMALL)
        x1, y1, x2, y2 = normalized
        img = self.page_image(page_key)
        if img is None:
            return RedetectPlan(
                page_key, normalized, normalized, skip=SKIP_NO_IMAGE
            )

        page_h, page_w = img.shape[:2]
        crop_rect = self._crop_rect(normalized, page_w, page_h)
        cx1, cy1, cx2, cy2 = crop_rect
        detector = self._ensure_detector()
        crop = np.ascontiguousarray(img[cy1:cy2, cx1:cx2])

        crop_mask, blk_list = detector.detect(crop, self.proj)
        page_blocks = self._page_blocks(page_key)
        new_blocks: List[TextBlock] = []
        dropped = 0
        for blk in blk_list or []:
            if not self._remap_lines(blk, cx1, cy1):
                continue
            if not self._inside(blk, normalized):
                dropped += 1
                continue
            # 顺序要紧：先继承样式（整份 fontformat 覆盖），再算派生量——
            # 派生量里的字号/竖排必须压过继承来的那份（见模块 docstring）。
            self._inherit_style(blk, page_blocks)
            self._derive(blk, page_w, page_h)
            new_blocks.append(blk)

        plan = RedetectPlan(
            page_key=page_key,
            rect=normalized,
            crop_rect=crop_rect,
            new_blocks=new_blocks,
            replaced_indices=self._replaced_indices(page_blocks, new_blocks),
            mask=self._crop_mask(crop_mask, crop_rect),
            detector=getattr(detector, "name", "") or self.effective_detector_name,
            dropped_outside=dropped,
        )
        if not new_blocks:
            plan.skip = SKIP_NO_DETECTION
        return plan

    def _normalize_rect(self, rect: Sequence[int]) -> Optional[Rect]:
        """把用户框规整成整型左上／右下；太小（误触）返回 ``None``。"""
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in list(rect)[:4])
        except (TypeError, ValueError):
            return None
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        if x2 - x1 < self.config.min_side or y2 - y1 < self.config.min_side:
            return None
        return [x1, y1, x2, y2]

    def _crop_rect(self, rect: Rect, page_w: int, page_h: int) -> Rect:
        """外扩 ``expand`` 并钳制到页内（决策 3 的实测口径：外扩 24px）。"""
        amount = max(int(self.config.expand), 0)
        x1, y1, x2, y2 = rect
        return [
            max(0, x1 - amount),
            max(0, y1 - amount),
            min(int(page_w), x2 + amount),
            min(int(page_h), y2 + amount),
        ]

    def _remap_lines(self, blk: TextBlock, dx: int, dy: int) -> bool:
        """把裁剪坐标系里的块平移到页级坐标系：``lines`` 的每个顶点加 ``(dx, dy)``。"""
        lines: List[List[List[float]]] = []
        for line in getattr(blk, "lines", None) or []:
            try:
                lines.append(
                    [[float(p[0]) + dx, float(p[1]) + dy] for p in line]
                )
            except (TypeError, ValueError, IndexError):
                return False
        if not lines:
            return False
        blk.lines = lines
        return True

    def _derive(self, blk: TextBlock, page_w: int, page_h: int) -> None:
        """按页级坐标补齐派生量：字号／角度／距离／``xyxy``／渲染方向。

        ``examine_textblk`` 的角度是平移不变量，字号与距离在页级坐标下才有意义。
        检测器（``modules/textdetector/detector_paddlev6.py::PPOCRv6Detector``）在
        裁剪坐标系里已经算过一次字号，又在第 5 步 ``sz = blk._detected_font_size
        * fnt_rsz`` 里把它覆盖成 ``-1``（ppocrv6 从不设 ``_detected_font_size``）；
        这里重算并写回 ``_detected_font_size``，新块才有可用字号（决策 7 的例外，
        见模块 docstring）。
        """
        try:
            examine_textblk(blk, page_w, page_h)
            blk._detected_font_size = blk.font_size
        except Exception as e:
            LOGGER.warning(f"Region redetect: failed to re-examine block: {e}")
        blk.adjust_bbox()
        blk.vertical = bool(blk.src_is_vertical)

    def _inside(self, blk: TextBlock, rect: Rect) -> bool:
        """区域外过滤（决策 3）：**四边形顶点中心**落在用户框内才收。"""
        center = poly_center(blk)
        if center is None:
            return False
        return rect[0] <= center[0] <= rect[2] and rect[1] <= center[1] <= rect[3]

    def _inherit_style(
        self, blk: TextBlock, page_blocks: Sequence[TextBlock]
    ) -> None:
        """新块样式继承同页**最近**的已有块（决策 7）。

        不继承 ``translation``／``text``／``rich_text``／``tags``／掩码——新块
        只有几何与样式；字号的覆盖见 ``_remap``。
        """
        source = self._nearest_block(blk, page_blocks)
        if source is None:
            return
        try:
            blk.fontformat = copy.deepcopy(source.fontformat)
        except Exception as e:
            LOGGER.warning(f"Region redetect: failed to inherit style: {e}")

    @staticmethod
    def _nearest_block(
        blk: TextBlock, page_blocks: Sequence[TextBlock]
    ) -> Optional[TextBlock]:
        center = poly_center(blk)
        if center is None:
            return None
        best, best_dist = None, None
        for other in page_blocks:
            other_center = poly_center(other)
            if other_center is None:
                continue
            dist = (other_center[0] - center[0]) ** 2 + (
                other_center[1] - center[1]
            ) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = other, dist
        return best

    def _replaced_indices(
        self, page_blocks: Sequence[TextBlock], new_blocks: Sequence[TextBlock]
    ) -> List[int]:
        """重叠率超 ``replace_overlap`` 的已有块下标（决策 4）。

        分母取两者较小者（``utils/block_geometry.py::poly_overlap_ratio`` 缺省
        口径）：ysgyolo 的区域级大框（短边中位 152px，整个气泡）被 ppocrv6 的
        逐行小框压住时，按"旧块面积"算永远到不了 50%，那正是最该被替换的一种。
        """
        threshold = self.config.replace_overlap
        replaced: List[int] = []
        for idx, old in enumerate(page_blocks):
            old_shape = poly_of(old)
            if old_shape is None:
                continue
            if any(
                poly_overlap_ratio(new, old_shape) > threshold
                for new in new_blocks
            ):
                replaced.append(idx)
        return replaced

    @staticmethod
    def _crop_mask(crop_mask, crop_rect: Rect) -> Optional[np.ndarray]:
        """裁剪掩码按裁剪矩形尺寸对齐（检测器返回的可能是整幅或裁剪尺寸）。"""
        if crop_mask is None:
            return None
        x1, y1, x2, y2 = crop_rect
        want_h, want_w = y2 - y1, x2 - x1
        mask = np.asarray(crop_mask)
        if mask.ndim > 2:
            mask = mask[..., 0]
        if mask.shape[:2] == (want_h, want_w):
            return mask.astype(np.uint8, copy=False)
        clipped = np.zeros((want_h, want_w), dtype=np.uint8)
        h = min(want_h, mask.shape[0])
        w = min(want_w, mask.shape[1])
        clipped[:h, :w] = mask[:h, :w]
        return clipped

    # ── 重建（纯函数）──────────────────────────────────────────────

    def build_page(self, plan: RedetectPlan) -> dict:
        """把"替换 + 新增"算进页块列表，**不改任何东西**。

        替换＝按下标剔除；新增＝按坐标逐个插入（``insert_index``），插入顺序取
        检测器给的阅读顺序（``modules/textdetector/detector_paddlev6.py`` 内部
        已按 ``reading_order`` 排过）。**替换掉旧块的成员也走坐标插入**而不钉在
        原下标：它就在原地附近，坐标判据会把它放回同一相对位置，同时避免"钉住
        旧下标"与"插入"两套索引记账互相打架。

        Returns:
            ``{"blocks", "kept", "replaced", "added", "inserted",
            "direction"}``——``inserted`` 是 ``(下标, 依据码)`` 列表。
        """
        page_blocks = self._page_blocks(plan.page_key)
        replaced = sorted(set(plan.replaced_indices))
        replaced_set = set(replaced)
        kept = [b for i, b in enumerate(page_blocks) if i not in replaced_set]
        rtl, basis = page_direction(kept)

        blocks = list(kept)
        inserted: List[Tuple[int, str]] = []
        for blk in plan.new_blocks:
            idx, why = insert_index(blk, blocks, rtl, config=self.config)
            blocks.insert(idx, blk)
            inserted.append((idx, why))
        return {
            "blocks": blocks,
            "kept": len(kept),
            "replaced": replaced,
            "added": len(plan.new_blocks),
            "inserted": inserted,
            "direction": (rtl, basis),
        }

    # ── 写回 ────────────────────────────────────────────────────────

    def apply(self, plan: RedetectPlan) -> dict:
        """数据层写回：``proj.pages`` + 页级掩码（+ 掩码落盘）。

        **视觉层重建由调用方负责**（UI 层的撤销命令 ``redo`` 里做，它必须同时
        拥有"改前／改后"两个端点才能一步撤销）；**页代数不在这里加**——本功能
        是栈内写入（与 ``ui/scenetext_manager.py::DeleteBlkItemsCommand``、
        ``RearrangeBlksCommand`` 同性质），``bump_page_generation`` 会把调用方
        自己的撤销命令一并僵尸化，那是给栈外整体换新用的（规划 D40 第 ③ 步）。
        """
        if plan.skip is not None or not plan.new_blocks:
            return {"applied": False, "error": plan.skip or SKIP_NO_DETECTION}
        if plan.page_key not in self.proj.pages:
            return {"applied": False, "error": "unknown-page"}
        report = self.build_page(plan)
        self.proj.pages[plan.page_key] = list(report["blocks"])
        report["applied"] = True
        report["page"] = plan.page_key
        report["rect"] = list(plan.rect)
        report["mask_written"] = self.paste_mask(plan)
        return report

    def paste_mask(self, plan: RedetectPlan) -> bool:
        """把裁剪掩码**并进**页级掩码并落盘（决策 6）；只在当前页生效。

        ``np.bitwise_or`` 并集：这块区域之后仍可被手工涂抹或管线覆盖，撤销本
        次区域再检测**不**回退掩码——掩码是派生的像素数据，撤销范围以方案为准
        （"新增 + OCR 文本 + 标签 + 可能的替换删除"，掩码不在其中），且它的语义
        与管线在修复阶段的写法一致（``ui/module_manager.py`` 里 ``save_mask`` +
        ``bump_page_image_generation``，同样是栈外图像写入）。
        """
        if plan.mask is None or plan.page_key != self.proj.current_img:
            return False
        mask_array = self.proj.mask_array
        if mask_array is None or mask_array.size == 0:
            return False
        x1, y1, x2, y2 = plan.crop_rect
        height = min(plan.mask.shape[0], mask_array.shape[0] - y1, y2 - y1)
        width = min(plan.mask.shape[1], mask_array.shape[1] - x1, x2 - x1)
        if height <= 0 or width <= 0:
            return False
        region = mask_array[y1 : y1 + height, x1 : x1 + width]
        np.bitwise_or(region, plan.mask[:height, :width], out=region)
        self.proj.bump_page_image_generation(plan.page_key)
        try:
            mask_dir = self.proj.mask_dir()
            if mask_dir:
                os.makedirs(mask_dir, exist_ok=True)
            self.proj.save_mask(plan.page_key, mask_array)
            return True
        except Exception as e:
            # 掩码只落盘失败：数据层已写、内存掩码已并，不该让整次操作失败
            LOGGER.error(f"Region redetect: failed to save mask: {e}")
            return False


__all__ = [
    "INSERT_APPEND",
    "INSERT_BEFORE",
    "INSERT_ERROR",
    "INSERT_FEW_BLOCKS",
    "SKIP_NO_DETECTION",
    "SKIP_NO_IMAGE",
    "SKIP_TOO_SMALL",
    "RedetectConfig",
    "RedetectPlan",
    "RegionRedetect",
    "RegionRedetectError",
    "insert_index",
    "new_precedes",
    "page_direction",
]
