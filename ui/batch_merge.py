"""批量合并相邻文本框（设计 §13／§15、D7／D8／D30～D33／D40）。

用户场景（设计 §2 的「逐页人工审校 OCR」）：惯用的检测模型**一行一框**（竖排
则一列一框），一个气泡内的文字被切成多个框，人工逐个合并**量极大**——实测
样本 1565 框里 1323 框（84.5%）涉及合并。本模块把「哪些框该合并」与「合并
后长什么样」交给程序算，人只看一遍审批列表（D20／D23）。

**两阶段**（D8）：

1. **规划阶段**（``plan``）：纯几何计算、只读、**不加载图像**（页尺寸优先取
   项目里已存的宽高）——同页相邻框聚成候选组 → 组级方向判定（D32）→ 产出
   组清单与审批材料（截图见 ``group_crop``）；
2. **应用阶段**（``apply``）：一次性重建整页块列表，**保留用户既有顺序**
   （D7，不自动重排），写回走 ``ui/batch_ops.py`` 的 D40 五步 + D35 版本撤销。

**分组判据**（设计 §16 的实测口径：「同页 + 行高相近 + 重叠 > 0.6」）。本
实现把它落成三件事，组 = 该判据的连通分量（§6.3 的组数即按连通分量计）：

- 同页（显然）；
- **投影重叠**：两框沿某一根轴的投影重叠率 ≥ ``MergeConfig.overlap_min``；
- **尺寸相近**：在**另一根轴**上尺寸之比 ≥ ``MergeConfig.size_ratio_min``；
- **相邻**：沿被分隔的那根轴的间隙 ≤ ``MergeConfig.max_gap_ratio`` 倍的自身
  交叉尺寸（默认 1.0＝一个行高／列宽的"相邻"）。

判据与文字方向无关：横排的「上下相邻」与竖排的「左右相邻」走同一条规则
（重叠落在哪根轴，就在另一根轴上要求尺寸相近）。**方向只影响组内拼接顺序，
不影响能否成组**——后者是 D32 的设计。

**口径提醒（2026-09-16）**：设计 §16 记录的实测脚本随 ``tmp/`` 清理已不存在，
该文只留下上面那句判据描述；本实现按之落地并把全部阈值收进 ``MergeConfig``。
在实机样本（94 页／1565 框）上跑 ``plan()`` 若组数偏离该文记录的 **435 组**
（每页约 4.6 组、每组约 3 框、最多 19 框），先调 ``overlap_min`` 与
``max_gap_ratio``；判据本身只有 ``_pair_axis`` 一处，要加条件也在这里改。

**写回契约**（D33／D40，逐条对应）：

- ``text`` 默认**逐行展开**（各成员 ``text`` 列表按组内方向顺序铺平），可切
  **按块分段**（每个成员占一个元素，即既有手动合并的形态）；
- ``rich_text`` 必须重建（契约二），规则见 ``_rebuild_rich_text``；
- ``lines`` 与 ``text`` 同序（契约一）；
- 样式来源取**该组在 ``proj.pages`` 列表中索引最小的成员**（D33b），"反转
  该组方向"不改变它，行为稳定；
- ``tags`` 取各成员并集（D33c），``reviewed`` 只在成员**全部**已驳回时保留，
  免得合并把"驳回"静默洗白；
- 合并块的 ``xyxy`` 与 ``_bounding_rect`` 都取成员并集（视觉层由数据重建，
  见 D40 第 ④ 步；``_bounding_rect`` 留旧值会让重建出的框落在错位置）；
- ``region_mask`` / ``region_inpaint_dict`` 清空（成员各自的掩码对并集框无效，
  与既有手动合并一致），``merged`` 置真。

**审批材料**：``group_crop`` 给出组并集框 + 外扩的 100% 原比例截图（D11／
D31，外扩＝组包围盒短边 50%、遇邻框即停）。裁剪**不缩放**是硬约束——缩放过的
截图不足以支撑审查。外扩后的矩形同时是"这一组要看哪块图"，不含邻框内容。

**取消语义**：``apply`` 先把全书改动算完、再一次性落版本写回，故取消（``should_stop``
在页间返回真）等价于"什么都没发生"，不会留半成品——与 ``ui/batch_inpaint.py``
的像素类任务不同，后者已涂的页要靠回滚撤掉。
"""

import copy
import os.path as osp
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image

from utils.block_geometry import expand_limited, gap_length, overlap_ratio, rect_of
from utils.block_tags import MISREAD_TAG_ID, is_tag_reviewed
from utils.io_utils import imread
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock

from .batch_ops import BatchOperation

# 进版本元信息的任务名；D35 的撤销提示会读它。工作台接线时应传一个已翻译的
# 标签覆盖它（先例见 ui/batch_inpaint.py::DEFAULT_LABEL）。
DEFAULT_LABEL = "Merge adjacent blocks"

# plan() 的跳过原因码（不含用户可见文案，工作台自行翻译）
SKIP_NO_BLOCKS = "no-blocks"
SKIP_NO_GROUPS = "no-groups"

# 组标识：``(页名, 组内最小成员索引)``——plan 与 apply 之间的选取凭据。
# 组内成员索引就是候选组在 ``proj.pages`` 页块列表里的下标（列表序）。
GroupKey = Tuple[str, int]

Rect = List[int]


@dataclass
class MergeConfig:
    """分组判据与审批材料的阈值（§6.3 实测口径 + D31／D33d）。"""

    overlap_min: float = 0.6
    """沿一根轴的投影重叠率下限（§6.3 的「重叠 > 0.6」）。"""

    size_ratio_min: float = 0.6
    """另一根轴上尺寸相近的下限（§6.3 的「行高相近」）。"""

    max_gap_ratio: float = 1.0
    """相邻判据：被分隔轴上的间隙 ≤ 该值 × 自身交叉尺寸（1.0＝一个行高／列宽）。

    这是「相邻」二字的落地——没有它，同一条带上的远隔两列会一路连通成
    整页一组。实机复算若组数远高于 435 组，先调小它。
    """

    oversize_ratio: float = 0.85
    """D33d 误聚阈值：组包围盒任一边超过页面对应边的该比例即警示。"""

    expand_ratio: float = 0.5
    """D31 审批截图的外扩量＝组包围盒短边的该比例。"""


@dataclass
class MergeGroup:
    """一个候选组（不含块对象——跨页编排的回调寻址原则，见设计 §11）。"""

    pagename: str
    indices: List[int]
    """成员在页块列表里的下标，**升序**（列表序；首项即 D33b 的样式来源块）。"""

    rects: List[Rect]
    bbox: Rect
    """成员并集框 ``xyxy``——D31 的截图基准。"""

    vertical: bool
    """组级方向判定（D32 主判）：真＝原文竖排（列自右向左）。"""

    basis: str
    """方向判定的依据：``group`` 组内投票／``order`` 块顺序／``page`` 页统计／
    ``book`` 全书统计／``default`` 全判不出（按横排）。"""

    order_suspect: bool
    """交叉验证不一致（D32）：块顺序与判定方向矛盾——只标注，不改判定。"""

    oversize: bool
    """误聚（D33d）：默认不勾选 + 警示标，不静默剔除。"""

    @property
    def key(self) -> GroupKey:
        return (self.pagename, min(self.indices))

    @property
    def size(self) -> int:
        return len(self.indices)

    def to_dict(self) -> dict:
        """不含块对象的浅描述（UI 行、日志、测试断言用）。"""
        return {
            "pagename": self.pagename,
            "indices": list(self.indices),
            "rects": [list(r) for r in self.rects],
            "bbox": list(self.bbox),
            "vertical": self.vertical,
            "basis": self.basis,
            "order_suspect": self.order_suspect,
            "oversize": self.oversize,
        }


# ── 几何判据（通用件在 utils/block_geometry.py，单点维护）──────────


def _pair_axis(ra: Rect, rb: Rect, cfg: MergeConfig) -> Optional[str]:
    """相邻判据：成组返回**重叠所在的轴**（``"x"``／``"y"``），否则 ``None``。

    重叠落在 ``x`` 上＝两框在 x 方向重叠、沿 y 排开（上下相邻），此时要求
    **高度**相近（§6.3 的「行高相近」）；反之要求**宽度**相近。间隙判据取
    被分隔轴上的距离，以自身的交叉尺寸为尺度（``max_gap_ratio``）。
    """
    ox = overlap_ratio(ra[0], ra[2], rb[0], rb[2])
    oy = overlap_ratio(ra[1], ra[3], rb[1], rb[3])
    axis = "x" if ox >= oy else "y"
    if (ox if axis == "x" else oy) < cfg.overlap_min:
        return None
    if axis == "x":
        # 上下相邻：比高度、限 y 间隙
        a_size, b_size = ra[3] - ra[1], rb[3] - rb[1]
        gap = gap_length(ra[1], ra[3], rb[1], rb[3])
    else:
        # 左右相邻：比宽度、限 x 间隙
        a_size, b_size = ra[2] - ra[0], rb[2] - rb[0]
        gap = gap_length(ra[0], ra[2], rb[0], rb[2])
    if min(a_size, b_size) / max(a_size, b_size) < cfg.size_ratio_min:
        return None
    if gap > cfg.max_gap_ratio * min(a_size, b_size):
        return None
    return axis


def _cluster(rects: Sequence[Rect], cfg: MergeConfig) -> Tuple[List[List[int]], int]:
    """连通分量分组。返回 ``(分量列表, 相邻对数)``；分量含单元素（调用方过滤）。"""
    n = len(rects)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if _pair_axis(rects[i], rects[j], cfg) is not None:
                pairs += 1
                union(i, j)

    comps: Dict[int, List[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return [sorted(members) for members in comps.values()], pairs


def _majority(values: Iterable[bool]) -> Optional[bool]:
    votes = list(values)
    if not votes:
        return None
    return sum(1 for v in votes if v) * 2 >= len(votes)


def _direction_from_order(rects: Sequence[Rect]) -> Optional[bool]:
    """按块顺序推断方向：主轴步进向左＝竖排（列自右向左），向下／向右＝横排。

    两步皆为 0（成员位置重合，如重复框）时**判不出**，返回 ``None`` 让
    ``BatchMerge._judge_direction`` 退到页／全书统计（D8 的兜底顺序）。
    """
    if len(rects) < 2:
        return None
    dx = sum(rects[i + 1][0] + rects[i + 1][2] - rects[i][0] - rects[i][2]
             for i in range(len(rects) - 1))
    dy = sum(rects[i + 1][1] + rects[i + 1][3] - rects[i][1] - rects[i][3]
             for i in range(len(rects) - 1))
    if dx == 0 and dy == 0:
        return None
    if abs(dx) > abs(dy):
        return dx < 0
    return False


def _order_key(rect: Rect, vertical: bool, idx: int):
    """组内阅读顺序的排序键（D33a 的拼接顺序依据）。

    竖排＝列自右向左（x 降序、同列按 y 升序）；横排＝行自上而下（y 升序、
    同行按 x 升序）。``idx`` 兜底保证同位置成员顺序稳定。
    """
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    if vertical:
        return (-cx, cy, idx)
    return (cy, cx, idx)


# ── 主流程 ────────────────────────────────────────────────────────


class BatchMerge:
    """把同页相邻框聚成候选组（``plan``）并按审批结果合并写回（``apply``）。"""

    def __init__(
        self,
        proj: ProjImgTrans,
        *,
        config: Optional[MergeConfig] = None,
        op: Optional[BatchOperation] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            proj: 项目实例。
            config: 分组判据阈值；缺省按设计 §16 的实测口径。
            op: 批量事务外壳（版本 + 落盘）；缺省按 ``proj`` 新建一个（没有
                UI 收尾的裸用法）。工作台应传带 ``commit``／``sync_block_data``
                的实例，好让版本快照反映当前面板编辑。
            on_progress: 规划阶段 ``(已扫页数, 总页数, 页名)``，供进度 UI。
            should_stop: 规划与应用的页间取消口；返回真即中止（``apply``
                在写版本前中止，故不留半成品）。
        """
        self.proj = proj
        self.config = config or MergeConfig()
        self.op = op or BatchOperation(proj)
        self.on_progress = on_progress
        self.should_stop = should_stop

    # ── 规划（只读）─────────────────────────────────────────────────

    def plan(self, pages: Optional[Sequence[str]] = None) -> dict:
        """只读扫描：算出可合并的候选组（供审批列表与 D27 告知）。

        只读——不写文件、不改内存数据、不加载图像（页尺寸优先取项目已存的
        宽高，其次读图片文件头）。适合在裸脚本里对实机项目做规模复算。

        Args:
            pages: 要扫的页；缺省全书页。

        Returns:
            ``{"groups", "group_count", "member_count", "pair_count",
            "oversize", "suspect", "excluded", "rotated", "apply_default",
            "pages", "skipped", "cancelled"}``：

            - ``groups``：``MergeGroup`` 列表（按页序、组内最小索引升序）；
            - ``group_count``／``member_count``：组数／参与合并的框数；
            - ``pair_count``：满足相邻判据的框对数（组数之外的口径参考）；
            - ``oversize``／``suspect``：误聚组数（D33d）／顺序存疑组数（D32）；
            - ``excluded``：带**未驳回**误识别标签、按 D30 被排除的框数；
            - ``rotated``：带旋转角（``angle != 0``）不参与合并的框数——轴对齐
              的并集框对旋转框无意义；
            - ``apply_default``：默认勾选的组数（＝组数 − 误聚组数）；
            - ``pages``：``{页名: 组数}``；
            - ``skipped``：``{页名: 原因码}``，取值见 ``SKIP_*``；
            - ``cancelled``：是否因 ``should_stop`` 提前结束。
        """
        groups: List[MergeGroup] = []
        counts: Dict[str, int] = {}
        skipped: Dict[str, str] = {}
        excluded = rotated = pairs = 0
        cancelled = False
        names = self._iter_pages(pages)

        for done, pagename in enumerate(names):
            if self.on_progress is not None:
                self.on_progress(done, len(names), pagename)
            if self.should_stop is not None and self.should_stop():
                cancelled = True
                break
            blk_list = self.proj.pages.get(pagename) or []
            if not blk_list:
                skipped[pagename] = SKIP_NO_BLOCKS
                continue
            candidates, n_excluded, n_rotated = self._candidates(blk_list)
            excluded += n_excluded
            rotated += n_rotated
            rects = [rect_of(blk) for _, blk in candidates]
            usable = [(idx, blk, rect) for (idx, blk), rect in zip(candidates, rects)
                      if rect is not None]
            comps, pair_count = _cluster([r for _, _, r in usable], self.config)
            pairs += pair_count
            page_groups = self._groups_of_page(pagename, blk_list, usable, comps)
            if not page_groups:
                skipped[pagename] = SKIP_NO_GROUPS
                continue
            groups.extend(page_groups)
            counts[pagename] = len(page_groups)

        oversize = sum(1 for g in groups if g.oversize)
        return {
            "groups": groups,
            "group_count": len(groups),
            "member_count": sum(g.size for g in groups),
            "pair_count": pairs,
            "oversize": oversize,
            "suspect": sum(1 for g in groups if g.order_suspect),
            "excluded": excluded,
            "rotated": rotated,
            "apply_default": len(groups) - oversize,
            "pages": counts,
            "skipped": skipped,
            "cancelled": cancelled,
        }

    def _iter_pages(self, pages: Optional[Sequence[str]]) -> List[str]:
        names = list(pages) if pages is not None else list(self.proj.pages)
        return [p for p in names if p in self.proj.pages]

    def _candidates(self, blk_list: Sequence[TextBlock]):
        """可参与合并的块：``[(页内索引, 块)]``，另回两处排除计数。

        - **D30**：带**未驳回**误识别标签的框整框排除（先审完清理再合并）；
          已驳回的框视同普通框参与，驳回的框不再被排除才有出口（D28／D30／
          D32 闭环）。排除范围**仅**误识别标签——``ocr_low_conf`` 不排除。
        - 旋转框（``angle != 0``）不参与：D31 的并集框与轴对齐判据对它无意义。
        """
        out, excluded, rotated = [], 0, 0
        for idx, blk in enumerate(blk_list):
            if MISREAD_TAG_ID in getattr(blk, "tags", {}) and not is_tag_reviewed(
                blk, MISREAD_TAG_ID
            ):
                excluded += 1
                continue
            if getattr(blk, "angle", 0):
                rotated += 1
                continue
            out.append((idx, blk))
        return out, excluded, rotated

    def _groups_of_page(self, pagename, blk_list, usable, comps) -> List[MergeGroup]:
        size = self._page_size(pagename)
        groups: List[MergeGroup] = []
        for comp in comps:
            if len(comp) < 2:
                continue
            members = [usable[i] for i in comp]
            rects = [rect for _, _, rect in members]
            bbox = [
                min(r[0] for r in rects),
                min(r[1] for r in rects),
                max(r[2] for r in rects),
                max(r[3] for r in rects),
            ]
            vertical, basis = self._judge_direction(members, blk_list)
            ordered = sorted(
                range(len(members)),
                key=lambda k: _order_key(rects[k], vertical, members[k][0]),
            )
            order_suspect = [members[k][0] for k in ordered] != [
                idx for idx, _, _ in members
            ]
            groups.append(
                MergeGroup(
                    pagename=pagename,
                    indices=[idx for idx, _, _ in members],
                    rects=rects,
                    bbox=bbox,
                    vertical=vertical,
                    basis=basis,
                    order_suspect=order_suspect,
                    oversize=self._is_oversize(bbox, size),
                )
            )
        groups.sort(key=lambda g: min(g.indices))
        return groups

    def _judge_direction(self, members, page_blocks) -> Tuple[bool, str]:
        """组级方向判定（D32／D8 的三级规则）。

        主判＝组内 ``src_is_vertical`` 多数；无有效值时改用块顺序；两者都判不
        出时退到页统计、再退到全书统计（D8 的"整页都判不出时才用全文投票"）。
        """
        votes = [
            blk.src_is_vertical
            for _, blk, _ in members
            if blk.src_is_vertical is not None
        ]
        voted = _majority(votes)
        if voted is not None:
            return voted, "group"
        inferred = _direction_from_order([rect for _, _, rect in members])
        if inferred is not None:
            return inferred, "order"
        page_vote = _majority(
            b.src_is_vertical
            for b in page_blocks
            if b.src_is_vertical is not None
        )
        if page_vote is not None:
            return page_vote, "page"
        book_vote = _majority(
            b.src_is_vertical
            for blks in self.proj.pages.values()
            for b in blks or []
            if b.src_is_vertical is not None
        )
        if book_vote is not None:
            return book_vote, "book"
        return False, "default"

    def _page_size(self, pagename: str) -> Optional[Tuple[int, int]]:
        """页面像素尺寸：先用项目已存的宽高，取不到再读图片文件头（不全解码）。"""
        info = getattr(self.proj, "_image_info", None) or {}
        entry = info.get(pagename) or {}
        width, height = entry.get("width"), entry.get("height")
        if width and height:
            return int(width), int(height)
        if not self.proj.directory:
            return None
        path = osp.join(self.proj.directory, pagename)
        if not osp.exists(path):
            return None
        try:
            with Image.open(path) as img:
                return tuple(int(v) for v in img.size[:2])
        except Exception:
            return None

    def _is_oversize(self, bbox: Rect, size: Optional[Tuple[int, int]]) -> bool:
        """D33d：组包围盒任一边超过页面对应边的 ``oversize_ratio``。

        页尺寸取不到（缺图）时不判误聚——那种页本就无法审批。
        """
        if not size:
            return False
        width, height = size
        ratio = self.config.oversize_ratio
        return (bbox[2] - bbox[0]) > width * ratio or (bbox[3] - bbox[1]) > height * ratio

    # ── 应用（写回）─────────────────────────────────────────────────

    def apply(
        self,
        selection: Optional[Iterable[GroupKey]] = None,
        *,
        flatten_lines: bool = True,
        reversed_groups: Optional[Iterable[GroupKey]] = None,
        label: Optional[str] = None,
        mark_dirty: bool = True,
    ) -> dict:
        """执行批量合并（重新规划 → D40 五步写回 → 落盘）。

        传入的 ``selection`` 是 **plan 阶段给出的组标识**；本方法**重新规划**
        一次（与 ``ui/batch_inpaint.py`` 同理：plan 与 apply 之间用户可能改过
        块数据，动作必须落在当前数据上），再按标识取组。

        Args:
            selection: 要合并的组标识（``(页名, 组内最小索引)``）；缺省＝全部
                **默认勾选**的组（D33d 的误聚组除外）。给了标识但已找不到的
                组进报告的 ``stale``（块列表变过，应重新规划后再执行）。
            flatten_lines: ``text`` 逐行展开（D33a 默认）／按块分段。
            reversed_groups: 审批界面「反转该组方向」按钮的产物（D32：组级
                方向判定由人拍板）。命中的组在**本次重规划的结果**上翻转
                组内阅读方向，据此重建 ``text``／``rich_text``／``lines``
                （D40 契约一：三者同序），不改判定之外的任何口径——样式
                来源仍是列表序首项（D33b），勾选范围也不变。
            label: 进版本元信息的任务名；缺省 ``DEFAULT_LABEL``。
            mark_dirty: 是否把改过的页标脏。默认真——合并改的是文本层，结果图
                会过期，与人工编辑一致（与 ``ui/batch_inpaint.py`` 的像素类任务
                相反：那里后续管线迟早覆盖，故不标脏）。

        Returns:
            ``{"started", "version", "writeback", "groups", "blocks", "pages",
            "stale", "error"}``：

            - ``started`` 为假表示没有执行（取消／无可合并的组／备份版本没写成），
              此时数据未动；
            - ``groups``／``blocks``：实际合并的组数与参与合并的**原框数**（D27
              的告知弹窗据此说"N 个框并成 M 组"）；
            - ``pages``：本次写回的页；
            - ``stale``：给了标识却已对不上的组（应重新规划后再执行）；
            - ``writeback``：``ui/batch_ops.py::BatchWriteback.apply`` 的报告，
              其 ``verified`` 即 D40 的验收判据。
        """
        report: dict = {
            "started": False,
            "version": None,
            "writeback": None,
            "groups": 0,
            "blocks": 0,
            "pages": [],
            "stale": [],
            "error": None,
        }
        # D40 第 ① 步前置对齐**必须在规划与构建之前**：合并块的 text／lines 取
        # 自数据层，若面板上还有未回写的编辑，晚对齐会把那些编辑挡在新块之外。
        # ``ui/batch_ops.py::BatchWriteback.apply`` 第 ① 步会再调一次，届时判据
        # 已为假、是空操作。
        self._sync_block_data()
        plan = self.plan()
        if plan["cancelled"]:
            report["error"] = "cancelled"
            return report
        wanted = set(selection) if selection is not None else None
        chosen: List[MergeGroup] = []
        for group in plan["groups"]:
            if wanted is None:
                if not group.oversize:
                    chosen.append(group)
            elif group.key in wanted:
                chosen.append(group)
        stale = set(report["stale"])
        if wanted is not None:
            found = {g.key for g in chosen}
            stale |= {k for k in wanted if k not in found}
        if not chosen:
            report["error"] = "stale" if stale else "no-groups"
            report["stale"] = sorted(stale)
            return report

        # D32：人拍板的"反转该组方向"。翻转只改组内拼接顺序，不改判定依据
        # 之外的任何东西——列在本次重规划的结果上翻转，故 apply 的其余口径
        # （样式来源 D33b、tags 并集 D33c、写回范围）全不受影响。
        flipped = set(reversed_groups or ())
        if flipped:
            for group in chosen:
                if group.key in flipped:
                    group.vertical = not group.vertical

        page_edits: Dict[str, List[TextBlock]] = {}
        applied: List[GroupKey] = []
        for pagename in dict.fromkeys(g.pagename for g in chosen):
            if self.should_stop is not None and self.should_stop():
                report["error"] = "cancelled"
                return report
            merged_list, applied_keys = self._rebuild_page(
                pagename, [g for g in chosen if g.pagename == pagename], flatten_lines
            )
            if merged_list is None:
                continue
            page_edits[pagename] = merged_list
            applied.extend(applied_keys)

        if not page_edits:
            report["error"] = "stale"
            report["stale"] = sorted(stale | {g.key for g in chosen})
            return report

        # 组索引失效（plan 与 apply 之间块列表被改过）的组进 stale——它们没有
        # 写进去，调用方应重新规划后再执行。
        applied_keys = set(applied)
        stale |= {g.key for g in chosen if g.key not in applied_keys}
        result = self.op.apply(label or DEFAULT_LABEL, page_edits, mark_dirty=mark_dirty)
        report.update(
            started=bool(result.get("started")),
            version=result.get("version"),
            writeback=result.get("writeback"),
            error=result.get("error"),
            groups=len(applied),
            # 参与合并的**原框数**（D27 的告知弹窗要能说"N 个框并成 M 组"）
            blocks=sum(g.size for g in chosen if g.key in applied_keys),
            pages=list(page_edits),
            stale=sorted(stale),
        )
        return report

    def _rebuild_page(self, pagename, groups, flatten_lines):
        """重建一页的块列表：合并块落在**组内最小索引**处，其余成员删除。

        返回 ``(新块列表, 已应用组的标识列表)``；页不存在或所有组都失效时返回
        ``(None, [])``。
        """
        blk_list = self.proj.pages.get(pagename)
        if blk_list is None:
            return None, []
        replacements: Dict[int, TextBlock] = {}
        consumed = set()
        applied: List[GroupKey] = []
        for group in groups:
            if any(idx >= len(blk_list) for idx in group.indices):
                continue
            merged = self.build_merged_block(pagename, group, flatten_lines=flatten_lines)
            if merged is None:
                continue
            replacements[min(group.indices)] = merged
            consumed.update(group.indices)
            applied.append(group.key)
        if not replacements:
            return None, []
        new_list: List[TextBlock] = []
        for idx, blk in enumerate(blk_list):
            if idx in replacements:
                new_list.append(replacements[idx])
            elif idx in consumed:
                continue  # 其余成员被并掉
            else:
                new_list.append(blk)
        return new_list, applied

    # ── 合并块构建（D33／D40）───────────────────────────────────────

    def _sync_block_data(self) -> None:
        """把面板上的未回写编辑并进数据层（D40 第 ① 步的前置对齐）。

        调用口与 ``ui/batch_ops.py::BatchWriteback`` 同一个（主窗口的
        ``_sync_block_data``），取自事务外壳；没有外壳或没接调用口时跳过——
        裸用法（无 UI）本就没有面板编辑。
        """
        writer = getattr(self.op, "writer", None)
        sync = getattr(writer, "sync_block_data", None)
        if sync is None:
            return
        try:
            sync()
        except Exception as e:  # 对齐失败不该阻断批量（写回处还会再试一次）
            LOGGER.error(f"Pre-merge sync failed: {e}")

    def build_merged_block(
        self, pagename: str, group: MergeGroup, *, flatten_lines: bool = True
    ) -> Optional[TextBlock]:
        """按 D33／D40 的契约构建合并块；成员索引失效时返回 ``None``。"""
        blk_list = self.proj.pages.get(pagename) or []
        if any(idx >= len(blk_list) or idx < 0 for idx in group.indices):
            return None
        members = [(idx, blk_list[idx]) for idx in group.indices]
        ordered = self._ordered_members(members, group.vertical)
        # D33b：样式来源＝页块列表中索引最小的成员（＝列表序首项），与组内
        # 阅读方向无关；"反转该组方向"不改变它。
        merged = copy.deepcopy(members[0][1])
        merged.text = self._merged_text(ordered, flatten_lines)
        merged.lines = [line for _, blk in ordered for line in (blk.lines or [])]
        translations = [blk.translation.strip() for _, blk in ordered]
        merged.translation = "\n".join(t for t in translations if t)
        # 先清空再重建（契约二）：_rebuild_rich_text 要用合并块自己的字体装配
        # 文档，留着成员旧 HTML 会让它加载旧文档。
        merged.rich_text = ""
        merged.rich_text = self._rebuild_rich_text(merged, ordered)
        merged.xyxy = list(group.bbox)
        merged._bounding_rect = [
            group.bbox[0],
            group.bbox[1],
            group.bbox[2] - group.bbox[0],
            group.bbox[3] - group.bbox[1],
        ]
        merged.tags = self._merged_tags(ordered)
        merged.region_mask = None
        merged.region_inpaint_dict = None
        merged.merged = True
        return merged

    def _ordered_members(self, members, vertical: bool):
        """成员按组内阅读方向排序（D33a 的拼接顺序）。"""
        return sorted(
            members,
            key=lambda pair: _order_key(rect_of(pair[1]) or [0, 0, 0, 0], vertical, pair[0]),
        )

    def _merged_text(self, ordered, flatten_lines: bool) -> List[str]:
        if not flatten_lines:
            return [blk.get_text() for _, blk in ordered]
        return [line for _, blk in ordered for line in (blk.text or [])]

    def _merged_tags(self, ordered) -> Dict:
        """D33c：成员标签并集；``reviewed`` 只在全部贡献者都已驳回时保留。

        子类型（``subtypes``）取并集——一个气泡里各成员命中的误识别子类不同，
        并掉后应反映"这块曾整体中招"。其余载荷取自**组内阅读顺序**中第一个带
        该标签的成员。
        """
        merged: Dict[str, dict] = {}
        for _, blk in ordered:
            for tag_id, entry in (blk.tags or {}).items():
                if not isinstance(entry, dict):
                    # 标签条目的唯一写入口是 utils/block_tags.py::set_tag，形态
                    # 恒为 dict；非字典条目属损坏数据，不参与并集。
                    continue
                payload = dict(entry)
                current = merged.get(tag_id)
                if current is None:
                    merged[tag_id] = payload
                    continue
                if payload.get("subtypes"):
                    subtypes = list(current.get("subtypes") or [])
                    for subtype in payload["subtypes"]:
                        if subtype not in subtypes:
                            subtypes.append(subtype)
                    current["subtypes"] = subtypes
                # reviewed 是"人已表态"——只要有一个贡献者未被驳回，合并块就
                # 仍是未驳回（不静默洗白，D33c）。
                if not payload.get("reviewed"):
                    current.pop("reviewed", None)
        return merged

    def _rebuild_rich_text(self, merged: TextBlock, ordered) -> str:
        """D40 契约二：重建 ``rich_text``（译文侧 HTML）。

        - 成员全无 ``rich_text`` → 留空。空值即"按合并后的译文重排"，渲染侧
          （``ui/text_engine/pipeline_formatting.py::_load_text_block_document``）
          会走 ``setPlainText(translation)`` 分支用合并块的字体排——这既是重建，
          也避免留旧成员的 HTML 造成渲染与数据不一致；
        - 有成员带注解（ruby／tate-chu-yoko 等）→ 按阅读顺序把各成员的文档片段
          插成一个文档（段落分隔对应译文的换行），再整段统一到**样式来源块的
          字体**（D33b；``mergeCharFormat`` 只并字体键，注解属性不受影响），
          最后序列化成 HTML。

        Qt 相关导入放在函数内：``plan()`` 要在无 ``QApplication`` 的裸脚本里
        可用（实机规模复算就是这么跑的）。
        """
        if not any(blk.rich_text for _, blk in ordered):
            return ""
        from qtpy.QtGui import QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentFragment

        from .text_engine.annotations import to_rich_text_html
        from .text_engine.pipeline_formatting import _load_text_block_document

        document = QTextDocument()
        document.setUndoRedoEnabled(False)
        cursor = QTextCursor(document)
        for i, (_, blk) in enumerate(ordered):
            if i:
                cursor.insertBlock()
            cursor.insertFragment(QTextDocumentFragment(_load_text_block_document(blk)))
        font = _load_text_block_document(merged).defaultFont()
        char_format = QTextCharFormat()
        char_format.setFont(font)
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(char_format)
        cursor.mergeBlockCharFormat(char_format)
        return to_rich_text_html(
            document,
            line_spacing_fallback=merged.fontformat.line_spacing,
            line_spacing_type_fallback=merged.fontformat.line_spacing_type,
        )

    # ── 审批材料（D11／D31）────────────────────────────────────────

    def group_crop(
        self,
        group: MergeGroup,
        *,
        expand_ratio: Optional[float] = None,
        image=None,
    ) -> Optional:
        """组审批截图：并集框 + 外扩，**100% 原比例**（D11），RGB ``ndarray``。

        返回 ``None`` 表示原图读不到（缺图）——调用方须兜底（``utils/io_utils.py::imread``
        对不存在的文件返回 ``None``，直接 ``.shape`` 会抛 AttributeError）。
        """
        img = image if image is not None else self._read_src(group.pagename)
        if img is None:
            return None
        height, width = img.shape[:2]
        x1, y1, x2, y2 = self.crop_rect(
            group, (width, height), expand_ratio=expand_ratio
        )
        return img[y1:y2, x1:x2]

    def crop_rect(
        self,
        group: MergeGroup,
        page_size: Tuple[int, int],
        *,
        expand_ratio: Optional[float] = None,
    ) -> Rect:
        """截图矩形：并集框外扩组包围盒短边的 ``expand_ratio``（D31），遇邻框即停。

        邻框＝同页**非本组**的框（含 D30 被排除的误识别框——它们视觉上还在，
        外扩压过去同样是越界）。外扩本身是通用件（``utils/block_geometry.py::expand_limited``），
        与 ``ui/batch_expand.py`` 的批量框扩张共用同一份"碰到邻框即停"。
        """
        ratio = self.config.expand_ratio if expand_ratio is None else expand_ratio
        x1, y1, x2, y2 = group.bbox
        amount = int(round(min(x2 - x1, y2 - y1) * ratio))
        members = set(group.indices)
        neighbors = [
            rect
            for idx, blk in enumerate(self.proj.pages.get(group.pagename) or [])
            if idx not in members
            for rect in [rect_of(blk)]
            if rect is not None
        ]
        return expand_limited(group.bbox, amount, page_size, neighbors)

    def _read_src(self, pagename: str):
        """读页面原图（RGB；缺图返回 ``None``，须兜底）。"""
        if not self.proj.directory:
            return None
        return imread(osp.join(self.proj.directory, pagename))
