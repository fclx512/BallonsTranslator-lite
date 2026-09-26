"""泛用工作台的**任务数据侧**（批量任务 ＋ 非删除待办队列；接线见设计 §8）。

工作台把六项任务收进同一个容器（顺序见
``ui/glossary_agent_panel.py::WORKBENCH_ORDER``）：① 可疑框清理 ② 合并相邻
文本框 ③ 原文待校对 ④ 简单背景修复 ⑤ 翻译准备（术语／剧情草稿）⑥ 译文待重译。
本模块服务其中的**数据侧**，分两层——

1. ``BatchTask`` 族（①②④）：调引擎的只读 ``plan``，把结果摆成列表行
   （``TaskRow``）；给审批预览准备 **100% 原比例**（D11／D23）的截图与叠加框；
   把用户勾选的标识交回引擎的 ``apply``（整批写回 ＋ ``utils/batch_versions.py``
   版本快照）。
2. ``ReviewQueueTask`` 族（③⑥）：**没有删除、没有整批写回、没有版本**——
   行是人工记下的待办或程序给出的建议，分区呈现（``ReviewSection``），出口
   只有「跳画布处理」与「把这条从列表移除／忽略」。不复用 ``BatchTask`` 的
   ``apply`` 语义（那是「勾选＝本次要改／要删」，见批次 C 交接 §8）。

**这里没有 QWidget**（Qt 只用于取已翻译文案），界面在
``ui/glossary_agent_panel.py``、``ui/workbench_batch_view.py`` 与
``ui/workbench_review_view.py``。之所以要这一层：批量引擎是同一个形状
（只读 ``plan`` ＋ 整批 ``apply``），界面据此只做三件事——填列表／算数字、
收勾选、把标识传回去；把"哪个引擎、哪些字段"的差异收在这里，界面就
不必为每个任务写一份。**界面不自己写几何判据、不自己改
``proj.pages``、不绕开 ``ui/batch_ops.py``**（设计 §8），本模块同样：
唯一的几何动作是审批截图的外扩，复用
``utils/block_geometry.py::expand_limited``（与引擎同一份实现）与
``block_overlays``（斜框按真实四边形画，不平成外接矩形）。

**默认值一律来自引擎的 plan**，界面不自作主张：合并的默认勾选＝非误聚组
（D33d）、误识别的默认勾选＝未驳回块（D28）。

（原第三个批量任务「框扩张」已于 2026-09-26 退役：只有机械几何写入、
没有可靠的自动排版消费，用户判定「扩了也不解决填不满／塞不下」。
登记见 ``scripts/audit_registry.json``；单块 Alt 拖拽缩放不在退役范围。）

文案一律在**字面量定义处**用 ``QCoreApplication.translate`` 显式标注上下文
（i18n 模块级翻译表规则）：本模块没有 ``self``，间接的 ``tr(variable)``
检查器看不见、必漏翻译。
"""

import os.path as osp
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from qtpy.QtCore import QCoreApplication

from utils.block_geometry import expand_limited, rect_of
from utils.block_tags import (
    MISREAD_SUBTYPE_LABELS,
    OCR_REVIEW_ID,
    TRANS_REVIEW_ID,
    has_ocr_review_pending,
    has_trans_review_pending,
    is_tag_reviewed,
    misread_queue_summary,
    remove_tag,
    set_tags_reviewed,
)
from utils.config import pcfg
from utils.io_utils import imread

from .batch_delete import BatchDeleteMisread
from .batch_merge import (
    SKIP_NO_BLOCKS as MERGE_SKIP_NO_BLOCKS,
    SKIP_NO_GROUPS as MERGE_SKIP_NO_GROUPS,
    BatchMerge,
    MergeConfig,
)
from .batch_ops import BatchOperation

# 任务 id。导航顺序定在 ``ui/glossary_agent_panel.py::WORKBENCH_ORDER``
# （扁平六项：可疑框清理 → 合并 → 原文待校对 → 简单背景修复 → 翻译准备 →
# 译文待重译）。
MISREAD = "misread"
MERGE = "merge"
SIMPLE_INPAINT = "simple_inpaint"
# 非删除待办队列：只读列表 + 「跳画布处理／从列表移除」，无批量写回、无版本
OCR_REVIEW = "ocr_review"
TRANS_REVIEW = "trans_review"
# 翻译准备＝一个导航入口下的术语／剧情两块草稿（子页 id 仍分列，见面板）
TRANSLATION_PREP = "translation_prep"
GLOSSARY = "glossary"
STORY = "story"

# 批量任务（``BatchTask`` ＋ ``ui/workbench_batch_view.py::BatchTaskView``）：
# 只读 ``plan`` ＋ 整批 ``apply`` ＋ ``utils/batch_versions.py`` 快照，**撤销池
# 只管这一族**。
CLEANUP_TASK_IDS = (MISREAD, MERGE, SIMPLE_INPAINT)

# 预览外扩量（D31：审批截图＝并集框短边的 50%、遇邻框即停）
PREVIEW_EXPAND_RATIO = 0.5

# 「无假名/汉字」这一类最容易误伤真实存在的拉丁文本、外来语与拟声词（D9），
# 故它出现的行**默认不勾选**——含多子类型时以含它为准（交接 §4.2①：先审后删，
# 绝不把"另一个检测器没框到"变成勾选依据）。其余子类型仍按 D28 的默认勾选口径。
_NO_JAPANESE = "no_japanese"

# 页面图像缓存条数——逐行翻看审批图时不必反复解码同一张大图
_PAGE_CACHE_SIZE = 2


def _clip(text: str, limit: int = 42) -> str:
    """列表单元格的单行摘要（队列列表不铺满整段原文）。"""
    flat = " ".join((text or "").split())
    if not flat:
        return "—"
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def block_row_caption(pagename: str, block_index: Optional[int]) -> str:
    """块级行的浮层标题：**说清在看哪一页的哪个框**。

    图尺寸（"150 × 60 px"）对审阅没有意义——它既不是块尺寸也不是页尺寸，
    还每行都不同；缩放与否看浮层标题条右侧的读数。组／页级任务按自己的
    行粒度覆盖（``BatchTask.row_caption``）。
    """
    if block_index is None:
        return pagename
    return QCoreApplication.translate(
        "WorkbenchTasks", "%1 · block %2"
    ).replace("%1", pagename).replace("%2", str(block_index))


_SKIP_REASON_LABELS = {
    MERGE_SKIP_NO_BLOCKS: QCoreApplication.translate(
        "WorkbenchTasks", "no text blocks"
    ),
    MERGE_SKIP_NO_GROUPS: QCoreApplication.translate(
        "WorkbenchTasks", "no merge groups"
    ),
    "no-mask": QCoreApplication.translate("WorkbenchTasks", "no mask"),
    "no-image": QCoreApplication.translate("WorkbenchTasks", "page image unavailable"),
    "mask-size-mismatch": QCoreApplication.translate(
        "WorkbenchTasks", "mask size does not match the page"
    ),
    "no-simple-blocks": QCoreApplication.translate(
        "WorkbenchTasks", "no simple-background blocks"
    ),
}


def _format_skipped(skipped: Dict[str, str]) -> str:
    """Aggregate engine skip codes into one short, user-facing detail line."""
    counts: Dict[str, int] = {}
    for reason in (skipped or {}).values():
        counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return ""
    parts = []
    for reason, count in sorted(counts.items()):
        label = _SKIP_REASON_LABELS.get(reason, reason)
        parts.append(
            QCoreApplication.translate("WorkbenchTasks", "%1 %2 page(s)")
            .replace("%1", str(count))
            .replace("%2", label)
        )
    return QCoreApplication.translate(
        "WorkbenchTasks", "Skipped: %1."
    ).replace("%1", ", ".join(parts))


# ── 叠加框形状（斜框预览）────────────────────────────────────────────


def _line_quad_points(line) -> Optional[List[Tuple[float, float]]]:
    """一行 ``lines`` 条目 → 顶点列表（兼容 4 个 ``[x, y]`` 对与扁平 8 数）；
    形态异常返回 ``None``。"""
    if line is None:
        return None
    values: List[float] = []
    try:
        for pnt in line:
            if hasattr(pnt, "__len__"):
                values.extend((float(pnt[0]), float(pnt[1])))
            else:
                values.append(float(pnt))
    except (TypeError, ValueError, IndexError):
        return None
    if len(values) < 6:
        return None
    if len(values) % 2:
        return None
    return list(zip(values[::2], values[1::2]))


def _quad_is_axis_aligned(pts, tol: float = 1.5) -> bool:
    """四边形是否轴对齐：x、y 各自的顶点值聚簇后都 ≤ 2 簇（即矩形两列／两行）。"""
    def clusters(values) -> int:
        ordered = sorted(values)
        n = 1
        for a, b in zip(ordered, ordered[1:]):
            if b - a > tol:
                n += 1
        return n

    return clusters(p[0] for p in pts) <= 2 and clusters(p[1] for p in pts) <= 2


def block_overlays(blk, role: str) -> List[dict]:
    """块 → 预览叠加框：轴对齐块给 ``rect``，斜块给 ``lines`` 的真实四边形。

    ``xyxy`` 只是轴对齐外接矩形，斜框（检测器返回的 DB 四边形）用它会被画平
    ——仅渲染问题，批处理本身不受影响。判据按四边形顶点几何算（``angle``
    在 3° 以内会被归零，不可信）；只要有一行带斜率，全部行都按 ``poly`` 画，
    多行块能看到每行的实际占位。
    """
    rect = rect_of(blk)
    if rect is None:
        return []
    quads = []
    slanted = False
    for line in getattr(blk, "lines", None) or []:
        pts = _line_quad_points(line)
        if pts is None:
            continue
        aligned = _quad_is_axis_aligned(pts)
        slanted = slanted or not aligned
        quads.append(pts)
    if not quads or not slanted:
        return [{"rect": rect, "role": role}]
    return [
        {"poly": [[round(x), round(y)] for x, y in pts], "role": role}
        for pts in quads
    ]


# ── 审批预览（D11／D23：100% 原比例）────────────────────────────────


@dataclass
class Preview:
    """一张审批预览图：**未缩放**的 RGB 图 ＋ 页面坐标系的叠加框。"""

    image: Any
    origin: Tuple[int, int]
    overlays: List[dict] = field(default_factory=list)
    """``[{"rect": [x1, y1, x2, y2], ...}, {"poly": [[x, y], ...], ...}, ...]``
    ——坐标都是**页面坐标**，视图自行减去 ``origin`` 换算到图上；``role``
    取 ``"target"|"old"|"new"``。轴对齐范围用 ``rect``，斜框（检测器返回的
    四边形带斜率）用 ``poly``——见 ``block_overlays``。"""


@dataclass
class TaskRow:
    """候选列表的一行（``key`` 是 ``apply`` 的选取凭据）。"""

    key: Any
    pagename: str
    cells: List[str]
    checked: bool = True
    block_index: Optional[int] = None
    payload: dict = field(default_factory=dict)


class _PageImageCache:
    """页面原图小缓存 ＋ 审批截图裁剪（批量任务与待办队列共用一份实现）。

    几何一律复用现成实现：外扩量＝矩形短边的 ``ratio``、上限"碰到邻框即停"
    （``utils/block_geometry.py::expand_limited``，与 ``ui/batch_merge.py``
    的审批截图同一份）。**不在这里写第二套几何判据。**
    """

    def _init_page_cache(self) -> None:
        self._pages: Dict[str, Any] = {}
        self._order: List[str] = []

    def page_image(self, pagename: str):
        """页面原图（RGB）；缺图返回 ``None``。带小缓存，见 ``_PAGE_CACHE_SIZE``。"""
        if pagename in self._pages:
            return self._pages[pagename]
        image = None
        if self.proj is not None and getattr(self.proj, "directory", None):
            image = imread(osp.join(self.proj.directory, pagename))
        self._pages[pagename] = image
        self._order.append(pagename)
        while len(self._order) > _PAGE_CACHE_SIZE:
            self._pages.pop(self._order.pop(0), None)
        return image

    def page_size(self, pagename: str) -> Optional[Tuple[int, int]]:
        """页面像素尺寸：先用项目已存的宽高，取不到再看图片（与引擎同序）。"""
        info = getattr(self.proj, "_image_info", None) or {}
        entry = info.get(pagename) or {}
        width, height = entry.get("width"), entry.get("height")
        if width and height:
            return int(width), int(height)
        image = self.page_image(pagename)
        if image is None:
            return None
        return int(image.shape[1]), int(image.shape[0])

    def block_at(self, pagename: str, block_index: Optional[int]):
        """``(块, 外接矩形)``；页／下标越界或缺坐标时给 ``(None, None)``。"""
        blk_list = self.proj.pages.get(pagename) or []
        if block_index is None or not 0 <= block_index < len(blk_list):
            return None, None
        blk = blk_list[block_index]
        return blk, rect_of(blk)

    def crop_around(
        self,
        pagename: str,
        rect: Sequence[int],
        *,
        exclude: Iterable[int] = (),
        ratio: float = PREVIEW_EXPAND_RATIO,
    ) -> Optional[Tuple[Any, Tuple[int, int]]]:
        """裁出 ``rect`` 外扩后的区域，**不缩放**（D11）。"""
        image = self.page_image(pagename)
        if image is None:
            return None
        height, width = image.shape[:2]
        skip = set(exclude)
        neighbors = [
            r
            for idx, blk in enumerate(self.proj.pages.get(pagename) or [])
            if idx not in skip
            for r in [rect_of(blk)]
            if r is not None
        ]
        amount = int(round(min(rect[2] - rect[0], rect[3] - rect[1]) * ratio))
        x1, y1, x2, y2 = expand_limited(
            list(rect), amount, (width, height), neighbors
        )
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return image[y1:y2, x1:x2], (x1, y1)

    def block_preview(self, pagename: str, block_index: Optional[int]) -> Optional["Preview"]:
        """单块的审批预览：原图裁区 ＋ 该块的真实轮廓（``block_overlays``）。"""
        blk, rect = self.block_at(pagename, block_index)
        if blk is None or rect is None:
            return None
        cropped = self.crop_around(pagename, rect, exclude={block_index})
        if cropped is None:
            return None
        image, origin = cropped
        return Preview(
            image=image, origin=origin, overlays=block_overlays(blk, "target")
        )


class BatchTask(_PageImageCache):
    """一个批量任务的数据侧适配（子类实现 plan／preview／apply）。"""

    id: str = ""
    title: str = ""
    hint: str = ""
    columns: Tuple[str, ...] = ()
    # 表格里拉伸的列号（含首列勾选框；<0 ＝取最后一列），长文本列给它
    stretch_column: int = -1
    # 候选行的呈现模式（``ui/custom_widget/row_table.py``）：``"table"``＝
    # 紧凑列对齐表格（默认，坐标/计数这类要逐位比较的数据型行）；
    # ``"card"``＝审批卡片行（主文 + 元数据 + 徽章，字段少、靠预览图判断）。
    row_view: str = "table"
    # apply 的版本元信息任务名（D35 的撤销提示会读它；子类用已翻译文案覆盖）
    label: str = ""

    def __init__(
        self,
        proj,
        op: Optional[BatchOperation] = None,
        *,
        on_changed: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            proj: 项目实例。
            op: 批量事务外壳（版本 + 落盘 + D40 前置对齐）；界面必须传带
                ``commit``／``sync_block_data`` 的实例，好让版本快照反映
                当前面板编辑（设计 §8）。
            on_changed: 任务改了项目数据（标签表态等）后通知界面置未保存位。
        """
        self.proj = proj
        self.op = op or BatchOperation(proj)
        self.on_changed = on_changed
        self._init_page_cache()
        self._last_plan: Optional[dict] = None

    # ── 子类接口 ────────────────────────────────────────────────────

    def plan(self, options: Dict[str, Any]) -> dict:
        """只读规划。

        返回 ``{"rows", "summary", "options_hint", "detail"}``；其中
        ``detail`` 是页面上可见的原因／补充说明，``options_hint`` 保留给
        兼容旧调用方的 tooltip。
        """
        raise NotImplementedError

    def preview(self, row: TaskRow) -> Optional[Preview]:
        """该行的审批预览（100% 原比例）；取不到图返回 ``None``。"""
        return None

    def row_caption(self, row: TaskRow) -> str:
        """审批浮层的标题（块级行的通用形态见 ``block_row_caption``）。"""
        return block_row_caption(row.pagename, row.block_index)

    def card_fields(self, row: TaskRow) -> Optional[dict]:
        """卡片模式（``row_view="card"``）的字段映射。

        返回 ``{"primary", "meta", "badge", "badge_tone", "rejected"}``；
        ``badge`` 为空串＝该行不画徽章。默认返回 ``None``，视图走通用兜底
        （首列当主文、其余列拼接当元数据）。
        """
        return None

    def apply(self, keys: Optional[Sequence], options: Dict[str, Any]) -> dict:
        """把勾选的标识交回引擎（``keys=None`` 表示用引擎缺省口径）。"""
        raise NotImplementedError

    def actions(self) -> List[dict]:
        """候选列表之外的额外动作按钮（``{"key", "label", "needs_rows"}``）。"""
        return []

    def options_spec(self) -> List[dict]:
        """任务参数控件的**描述**（界面据此建控件，见 ``ui/workbench_batch_view.py``）。

        ``kind`` 取值：``"int"``（``min``／``max``／``suffix``）、
        ``"choice"``（``items`` ＝ ``[(值, 已翻译标签)]``）、``"bool"``。
        参数值由界面收集进 ``options`` 字典回传 ``plan``／``apply``。
        ``suffix_map`` ＝ ``(另一个选项的键, {该选项的值: 后缀})``，用于让
        数值框的单位跟着模式走（后缀是通用单位记号，不翻译）。
        """
        return []

    def execute_label(self, count: int) -> str:
        """执行按钮文案（带勾选条数）。"""
        return QCoreApplication.translate(
            "WorkbenchTasks", "Apply to %1 row(s)"
        ).replace("%1", str(count))

    def no_candidates_label(self) -> str:
        """无候选时的禁用按钮文案，避免显示成一次真实操作。"""
        return QCoreApplication.translate("WorkbenchTasks", "No candidates")

    def count_label(self) -> str:
        """导航计数的单位说明；``pending`` 的数值口径保持不变。

        返回空串＝只报数字（块口径的队列用这个：标签已说清数的是什么，
        再缀「待审校」是重复说法，见 ``MisreadTask.count_label``）。
        """
        return QCoreApplication.translate("WorkbenchTasks", "available")

    def confirm_html(self, rows: Sequence[TaskRow], options: Dict[str, Any]) -> str:
        """D27 的告知弹窗正文：**在弹窗里说清确认后会做什么**。"""
        raise NotImplementedError

    def run_action(
        self, key: str, rows: Sequence[TaskRow], options: Dict[str, Any]
    ) -> Optional[dict]:
        """执行额外动作；返回 ``{"replan": bool, "notify": str}`` 或 ``None``。"""
        return None

    def pending(self, options: Dict[str, Any]) -> Optional[int]:
        """"还有 N 个未处理"的计数（规划 D37）；``None`` ＝本任务不参与计数。"""
        return None

    # ── 共用工具 ────────────────────────────────────────────────────

    def reset(self) -> None:
        """项目数据换过（打开项目／批量写回后），丢掉缓存与上次计划。"""
        self._pages.clear()
        self._order.clear()
        self._last_plan = None


# ── 误识别清理（ui/batch_delete.py）─────────────────────────────────


class MisreadTask(BatchTask):
    """队列＝带「误识别文本」标签的块；驳回与勾选两轴正交（D27／D28）。"""

    id = MISREAD
    stretch_column = 2  # 表格列号（含首列勾选框）：原文是该任务的宽列
    row_view = "card"  # 字段少、靠预览图判断：主文＝原文，元数据＝页·子类型
    columns = (
        QCoreApplication.translate("WorkbenchTasks", "Page"),
        QCoreApplication.translate("WorkbenchTasks", "Source text"),
        QCoreApplication.translate("WorkbenchTasks", "Subtype"),
        QCoreApplication.translate("WorkbenchTasks", "Action"),
    )

    def card_fields(self, row: TaskRow) -> Optional[dict]:
        reviewed = bool(row.payload.get("reviewed"))
        subtypes = row.cells[2] if len(row.cells) > 2 else ""
        meta = " · ".join(
            part for part in (row.pagename, subtypes) if part and part != "—"
        )
        return {
            "primary": row.cells[1] if len(row.cells) > 1 else "",
            "meta": meta,
            "badge": row.cells[3] if len(row.cells) > 3 else "",
            "badge_tone": "muted" if reviewed else "warning",
            "rejected": reviewed,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = QCoreApplication.translate(
            "WorkbenchTasks", "Delete suspicious text blocks"
        )
        self.title = QCoreApplication.translate(
            "WorkbenchTasks", "Suspicious text-block cleanup"
        )
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "OCR rules flagged these text blocks as suspicious. They are selected for deletion by default; rows whose text has no kana or kanji start unchecked, because a real Latin word or onomatopoeia can hide there. Mark a false positive to keep a block. Marking a false positive and selecting a block for deletion are separate decisions. Click a row to preview the original image above the canvas.",
        )

    def _engine(self) -> BatchDeleteMisread:
        return BatchDeleteMisread(self.proj, op=self.op)

    def plan(self, options: Dict[str, Any]) -> dict:
        report = self._engine().plan()
        rows = []
        for entry in report["entries"]:
            subtypes = ", ".join(
                MISREAD_SUBTYPE_LABELS.get(s, s) for s in entry.subtypes
            )
            if entry.reviewed:
                review = QCoreApplication.translate(
                    "WorkbenchTasks", "False positive — keep"
                )
            elif _NO_JAPANESE in entry.subtypes:
                review = QCoreApplication.translate(
                    "WorkbenchTasks", "Confirm before deleting"
                )
            else:
                review = QCoreApplication.translate(
                    "WorkbenchTasks", "Delete by default"
                )
            cells = [entry.pagename, _clip(entry.text), subtypes or "—", review]
            rows.append(
                TaskRow(
                    key=entry.key,
                    pagename=entry.pagename,
                    cells=cells,
                    checked=not entry.reviewed
                    and _NO_JAPANESE not in entry.subtypes,
                    block_index=entry.index,
                    payload={"reviewed": entry.reviewed},
                )
            )
        # 第三个数字是**实际默认勾选的行数**，不是"未驳回块数"——两者在
        # 「无假名/汉字」默认不勾选之后不再相等，摘要不能替勾选框说话。
        default_checked = sum(1 for row in rows if row.checked)
        summary = QCoreApplication.translate(
            "WorkbenchTasks",
            "%1 suspicious block(s) found; %2 marked as false positives, %3 selected for deletion by default.",
        )
        detail = QCoreApplication.translate(
            "WorkbenchTasks",
            "Source: OCR post-processing rules. If this list is empty, OCR may not have run, may be disabled, may be set to none_ocr, or may simply have found no suspicious text.",
        )
        plan = {
            "rows": rows,
            "summary": summary.replace("%1", str(report["queue"]))
            .replace("%2", str(report["rejected"]))
            .replace("%3", str(default_checked)),
            "options_hint": "",
            "detail": detail,
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        # 单块截图＝原图裁区 ＋ 该块轮廓：与待办队列共用同一份实现
        return self.block_preview(row.pagename, row.block_index)

    def apply(self, keys, options):
        return self._engine().apply(keys, label=self.label)

    def execute_label(self, count: int) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks", "Delete %1 block(s)"
        ).replace("%1", str(count))

    def confirm_html(self, rows, options) -> str:
        pages = len({row.pagename for row in rows})
        # D27 要求照实说：删的是"框与它的译文渲染"，此前跑过修复留在
        # inpainted/ 的痕迹不会因此还原（要还原请用修复侧自己的撤销）
        body = QCoreApplication.translate(
            "WorkbenchTasks",
            "Deletes %1 selected block(s) across %2 page(s): the text blocks and their rendered translation only.",
        )
        note = QCoreApplication.translate(
            "WorkbenchTasks",
            "Inpainting left in the inpainted layer (if you ran inpaint before) is not reverted — use the inpaint undo for that. The whole batch can be rolled back in one step.",
        )
        return "<p>" + body.replace("%1", str(len(rows))).replace(
            "%2", str(pages)
        ) + "</p><p>" + note + "</p>"

    def actions(self) -> List[dict]:
        return [
            {
                "key": "reject",
                "label": QCoreApplication.translate(
                    "WorkbenchTasks", "Mark / unmark false positive"
                ),
                "needs_rows": True,
            }
        ]

    def run_action(self, key, rows, options):
        if key != "reject" or not rows:
            return None
        # 与标签工具栏的翻转语义一致（utils/block_tags.py::toggle_on_blocks）：
        # 非全员已驳回 → 全部驳回，否则全部取消驳回（D28：可逆、不从队列消失）
        target = not all(r.payload.get("reviewed") for r in rows)
        changed = 0
        for row in rows:
            blk_list = self.proj.pages.get(row.pagename) or []
            if row.block_index is None or row.block_index >= len(blk_list):
                continue
            changed += set_tags_reviewed(blk_list[row.block_index], target, "ocr_misread")
            row.payload["reviewed"] = target
        if changed and self.on_changed is not None:
            self.on_changed()
        if target:
            notify = QCoreApplication.translate(
                "WorkbenchTasks", "Marked %1 block(s) as false positives."
            ).replace("%1", str(changed))
        else:
            notify = QCoreApplication.translate(
                "WorkbenchTasks", "Removed the false-positive mark from %1 block(s)."
            ).replace("%1", str(changed))
        return {"replan": True, "notify": notify}

    def count_label(self) -> str:
        # 任务名已含「可疑框」，单位「待审校」是重复说法；块口径的队列只留数字
        return ""

    def pending(self, options) -> Optional[int]:
        # 队列口径见 D28：待处理＝队列内块数 − 已驳回块数（纯内存遍历，便宜）
        return misread_queue_summary(self.proj.pages)["pending"]


# ── 批量合并相邻框（ui/batch_merge.py）──────────────────────────────


class MergeTask(BatchTask):
    """组级方向判定 + 列表级审批（D20：一组一行、默认勾选、一次执行）。"""

    id = MERGE
    stretch_column = 4  # 标记列（误聚／顺序存疑）放长文案
    row_view = "card"  # 组级行无逐位比较需求：主文＝方向·块数，徽章＝标记
    columns = (
        QCoreApplication.translate("WorkbenchTasks", "Page"),
        QCoreApplication.translate("WorkbenchTasks", "Blocks"),
        QCoreApplication.translate("WorkbenchTasks", "Direction"),
        QCoreApplication.translate("WorkbenchTasks", "Marks"),
    )

    def card_fields(self, row: TaskRow) -> Optional[dict]:
        marks = row.cells[3] if len(row.cells) > 3 else "—"
        direction = row.cells[2] if len(row.cells) > 2 else ""
        size = row.cells[1] if len(row.cells) > 1 else ""
        return {
            "primary": QCoreApplication.translate(
                "WorkbenchTasks", "%1 · %2 block(s)"
            ).replace("%1", direction).replace("%2", size),
            "meta": row.pagename,
            "badge": "" if marks == "—" else marks,
            "badge_tone": "warning" if marks != "—" else "muted",
            "rejected": False,  # 误聚组只是默认不勾选，不算已驳回
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = QCoreApplication.translate(
            "WorkbenchTasks", "Merge adjacent text blocks"
        )
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "One row per candidate group. Suspected false groupings start unchecked. Click a row to preview the original image above the canvas.",
        )

    def row_caption(self, row: TaskRow) -> str:
        group = row.payload.get("group")
        size = getattr(group, "size", None)
        if size is None:
            return row.pagename
        return QCoreApplication.translate(
            "WorkbenchTasks", "%1 · %2-block group"
        ).replace("%1", row.pagename).replace("%2", str(size))

    def _engine(self) -> BatchMerge:
        # 误聚阈值取设置里的值（D33d 的「设置内参数接口」）；引擎本身仍按
        # 调用方给的 config 工作，裸脚本复算不受设置影响。
        return BatchMerge(
            self.proj,
            config=MergeConfig(
                oversize_ratio=float(pcfg.workbench_merge_oversize_ratio)
            ),
            op=self.op,
        )

    def plan(self, options: Dict[str, Any]) -> dict:
        report = self._engine().plan()
        rows = []
        for group in report["groups"]:
            marks = []
            if group.oversize:
                marks.append(
                    QCoreApplication.translate("WorkbenchTasks", "False grouping")
                )
            if group.order_suspect:
                marks.append(
                    QCoreApplication.translate("WorkbenchTasks", "Order suspect")
                )
            direction = (
                QCoreApplication.translate("WorkbenchTasks", "Vertical")
                if group.vertical
                else QCoreApplication.translate("WorkbenchTasks", "Horizontal")
            )
            rows.append(
                TaskRow(
                    key=group.key,
                    pagename=group.pagename,
                    cells=[
                        group.pagename,
                        str(group.size),
                        direction,
                        ", ".join(marks) or "—",
                    ],
                    checked=not group.oversize,
                    block_index=min(group.indices),
                    payload={"group": group},
                )
            )
        # 排除计数按 D30 照实说：带未驳回误识别标签的框整框排除（先审完清理）
        summary = QCoreApplication.translate(
            "WorkbenchTasks",
            "%1 group(s) covering %2 block(s); %3 excluded (unrejected misread); %4 rotated skipped.",
        )
        plan = {
            "rows": rows,
            "summary": summary.replace("%1", str(report["group_count"]))
            .replace("%2", str(report["member_count"]))
            .replace("%3", str(report["excluded"]))
            .replace("%4", str(report["rotated"])),
            "options_hint": QCoreApplication.translate(
                "WorkbenchTasks",
                "%1 suspected false grouping(s), %2 group(s) with suspect order.",
            )
            .replace("%1", str(report["oversize"]))
            .replace("%2", str(report["suspect"])),
            "detail": _format_skipped(report.get("skipped"))
            or QCoreApplication.translate(
                "WorkbenchTasks", "No pages were skipped."
            ),
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        group = row.payload.get("group")
        if group is None:
            return None
        engine = self._engine()
        page = self.page_image(group.pagename)
        image = engine.group_crop(group, image=page)
        if image is None or page is None:
            return None
        # 原点＝外扩后的矩形左上角。页尺寸**取图像自己的**（与 group_crop 同源），
        # 项目里记的宽高若与磁盘图不一致，用它会算错裁剪原点、叠加框跟着错位。
        crop = engine.crop_rect(group, (page.shape[1], page.shape[0]))
        return Preview(
            image=image,
            origin=(max(0, crop[0]), max(0, crop[1])),
            overlays=[{"rect": r, "role": "target"} for r in group.rects],
        )

    def apply(self, keys, options):
        return self._engine().apply(
            keys,
            flatten_lines=bool(options.get("flatten_lines", True)),
            reversed_groups=options.get("reversed_groups") or (),
            label=self.label,
        )

    def options_spec(self) -> List[dict]:
        return [
            {
                "key": "flatten_lines",
                "kind": "bool",
                "label": QCoreApplication.translate(
                    "WorkbenchTasks", "Flatten each block's lines into one list"
                ),
                "value": True,
            }
        ]

    def execute_label(self, count: int) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks", "Merge %1 group(s)"
        ).replace("%1", str(count))

    def confirm_html(self, rows, options) -> str:
        blocks = sum(int(row.cells[1]) for row in rows)
        pages = len({row.pagename for row in rows})
        body = QCoreApplication.translate(
            "WorkbenchTasks",
            "Merges %1 group(s) — %2 block(s) become %1 block(s) across %3 page(s). Styles come from the lowest-index member, tags are unioned, and block order on the page is preserved.",
        )
        note = QCoreApplication.translate(
            "WorkbenchTasks",
            "The whole batch can be rolled back in one step.",
        )
        return (
            "<p>"
            + body.replace("%1", str(len(rows)))
            .replace("%2", str(blocks))
            .replace("%3", str(pages))
            + "</p><p>"
            + note
            + "</p>"
        )

    def actions(self) -> List[dict]:
        return [
            {
                "key": "reverse",
                "label": QCoreApplication.translate(
                    "WorkbenchTasks", "Reverse direction of selected group"
                ),
                "needs_rows": True,
            }
        ]

    def run_action(self, key, rows, options):
        if key != "reverse" or len(rows) != 1:
            return None
        flipped = options.setdefault("reversed_groups", set())
        group_key = rows[0].key
        if group_key in flipped:
            flipped.discard(group_key)
        else:
            flipped.add(group_key)
        # 只影响拼接顺序，勾选与写回范围不变（D32：判定结果由人拍板）
        return {
            "notify": QCoreApplication.translate(
                "WorkbenchTasks",
                "Direction flipped for this group; it takes effect when you merge.",
            )
        }

    def count_label(self) -> str:
        return QCoreApplication.translate("WorkbenchTasks", "groups")

    def pending(self, options) -> Optional[int]:
        # 默认勾选口径＝非误聚组（D33d），与 apply 的缺省一致
        return self._engine().plan()["apply_default"]


# ── 批量简单背景修复（ui/batch_inpaint.py）──────────────────────────


class SimpleInpaintTask(BatchTask):
    """简单块纯色覆盖、复杂块完全不动；**不改块列表**，故按页勾选（D3／D34）。"""

    id = SIMPLE_INPAINT
    stretch_column = 1  # 页名是唯一的长文本列
    columns = (
        QCoreApplication.translate("WorkbenchTasks", "Page"),
        QCoreApplication.translate("WorkbenchTasks", "Simple"),
        QCoreApplication.translate("WorkbenchTasks", "Complex"),
        QCoreApplication.translate("WorkbenchTasks", "Unknown"),
    )

    def __init__(self, proj, op=None, *, inpainter_provider=None, **kwargs):
        super().__init__(proj, op, **kwargs)
        self.inpainter_provider = inpainter_provider
        self.label = QCoreApplication.translate(
            "WorkbenchTasks", "Simple background fill"
        )
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "Fills near-flat balloon interiors with their background colour; complex backgrounds are left alone (no model is loaded). Writes the inpainted layer only. Click a row to preview the original image above the canvas.",
        )

    def row_caption(self, row: TaskRow) -> str:
        count = row.cells[1] if len(row.cells) > 1 else None
        if count is None:
            return row.pagename
        return QCoreApplication.translate(
            "WorkbenchTasks", "%1 · %2 block(s) to fill"
        ).replace("%1", row.pagename).replace("%2", count)

    def _engine(self):
        # 延迟导入：ui/batch_inpaint.py 会拉进修复模块（torch/cv2），而本模块
        # 被面板顶层导入，不该把这条重链带进启动路径与离屏测试
        from .batch_inpaint import BatchSimpleInpaint

        inpainter = self.inpainter_provider() if self.inpainter_provider else None
        return BatchSimpleInpaint(self.proj, inpainter, op=self.op)

    def plan(self, options: Dict[str, Any]) -> dict:
        report = self._engine().plan()
        rows = [
            TaskRow(
                key=pagename,
                pagename=pagename,
                cells=[
                    pagename,
                    str(entry["simple"]),
                    str(entry["complex"]),
                    str(entry["unknown"]),
                ],
                checked=True,
                payload={"rects": list(entry["rects"])},
            )
            for pagename, entry in report["pages"].items()
        ]
        summary = QCoreApplication.translate(
            "WorkbenchTasks",
            "%1 page(s) will be filled (%2 block(s) in total); %3 complex and %4 undecided block(s) across the book stay untouched.",
        )
        plan = {
            "rows": rows,
            "summary": summary.replace("%1", str(report["page_count"]))
            .replace("%2", str(report["simple"]))
            .replace("%3", str(report["complex"]))
            .replace("%4", str(report["unknown"])),
            "options_hint": "",
            "detail": _format_skipped(report.get("skipped"))
            or QCoreApplication.translate(
                "WorkbenchTasks", "No pages were skipped."
            ),
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        rects = row.payload.get("rects") or []
        if not rects:
            return None
        union = [
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        ]
        # 外扩量为 0：截图就是"会被覆盖的范围"，不借邻框语义（本任务没有
        # "扩到哪停"的问题，矩形已由判据给出）
        all_idx = set(range(len(self.proj.pages.get(row.pagename) or [])))
        cropped = self.crop_around(row.pagename, union, exclude=all_idx, ratio=0.0)
        if cropped is None:
            return None
        image, origin = cropped
        return Preview(
            image=image,
            origin=origin,
            overlays=[{"rect": r, "role": "target"} for r in rects],
        )

    def apply(self, keys, options):
        pages = list(keys) if keys is not None else None
        return self._engine().apply(pages, label=self.label)

    def execute_label(self, count: int) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks", "Fill %1 page(s)"
        ).replace("%1", str(count))

    def confirm_html(self, rows, options) -> str:
        blocks = sum(int(row.cells[1]) for row in rows)
        body = QCoreApplication.translate(
            "WorkbenchTasks",
            "Fills %1 simple-background block(s) on %2 page(s) with their background colour, writing only the inpainted layer. Complex blocks are left completely untouched.",
        )
        note = QCoreApplication.translate(
            "WorkbenchTasks",
            "Cancelling halfway rolls back what was already filled, so a cancelled run leaves nothing behind. The whole batch can be rolled back in one step.",
        )
        return (
            "<p>"
            + body.replace("%1", str(blocks)).replace("%2", str(len(rows)))
            + "</p><p>"
            + note
            + "</p>"
        )

    def count_label(self) -> str:
        return QCoreApplication.translate("WorkbenchTasks", "pages")

    def pending(self, options) -> Optional[int]:
        # plan 要把全书页图读一遍（判据算原图，D34），导航只回报已扫描的结果。
        if not self._last_plan:
            return None
        return len(self._last_plan["rows"])


# ── 工厂 ────────────────────────────────────────────────────────────


def build_batch_tasks(
    proj,
    op: Optional[BatchOperation] = None,
    *,
    inpainter_provider: Optional[Callable[[], Any]] = None,
    on_changed: Optional[Callable[[], None]] = None,
) -> Dict[str, BatchTask]:
    """按用户工作流顺序建三个批量任务（顺序见设计 §8）。"""
    tasks: List[BatchTask] = [
        MisreadTask(proj, op, on_changed=on_changed),
        MergeTask(proj, op, on_changed=on_changed),
        SimpleInpaintTask(
            proj, op, inpainter_provider=inpainter_provider, on_changed=on_changed
        ),
    ]
    return {task.id: task for task in tasks}


# ── 非删除待办队列（原文待校对／译文待重译）─────────────────────────
#
# 与批量任务的根本区别（批次 C 交接 §4.2③⑥、§8）：
#
# - **没有删除、没有整批写回、没有版本快照**：行是「我稍后处理」的记录或
#   程序给出的建议，出口只有「跳画布处理」与「把这条从列表移除／忽略」；
# - 人工记录与程序建议**分区呈现**（``ReviewSection``）：前者是用户的明确
#   待办、可取消记录，后者可安全忽略，不混成一张表、不共用勾选语义；
# - 故**不复用** ``BatchTask``（它的 ``apply`` 语义是「勾选＝本次要改／要删」），
#   只共用 ``_PageImageCache`` 的页面缓存与审批截图。
#
# "移除记录／忽略建议"只动这份记录本身：不改原文、不改译文、不清手写／拟声
# 这类**持久翻译指示**（指示与原文是否待校对是两件事，见交接 §3.2）。


@dataclass(frozen=True)
class ReviewSection:
    """待办队列的一个分区（界面按此建一段列表 ＋ 自己的处置按钮）。"""

    id: str
    title: str  # 分区标题（已翻译）
    empty: str  # 空态说明（已翻译）
    action_label: str  # 处置按钮文案（已翻译）
    notify: str  # 处置回执模板（%1 ＝ 实际移除的块数）
    badge: str  # 行的徽章文案（已翻译）
    tone: str = "muted"  # 徽章色调（``ui/custom_widget/row_table.py`` 的取值）


class ReviewQueueTask(_PageImageCache):
    """只读待办队列的数据侧（界面在 ``ui/workbench_review_view.py``）。"""

    id: str = ""
    title: str = ""
    hint: str = ""
    row_view = "card"
    # 现场处理动作 id：界面据此给出「直接开确认卡」的按钮（与「跳画布」并列
    # 的第二条出口，交接 §4.2③⑥）。空＝本队列没有现场动作。
    action_id: str = ""

    def __init__(self, proj, *, on_changed: Optional[Callable[[], None]] = None):
        self.proj = proj
        self.on_changed = on_changed
        self._init_page_cache()
        self._last_plan: Optional[dict] = None

    # ── 子类接口 ────────────────────────────────────────────────────

    def sections(self) -> Sequence[ReviewSection]:
        """本队列的分区（顺序即界面的呈现顺序）。"""
        raise NotImplementedError

    def collect(self) -> Dict[str, List[TaskRow]]:
        """各分区的行（一个块只进一个区，子类实现）。"""
        raise NotImplementedError

    def summary_text(self, counts: Dict[str, int]) -> str:
        raise NotImplementedError

    def detail_text(self) -> str:
        raise NotImplementedError

    def drop_record(self, section_id: str, blk) -> int:
        """移除这条记录／忽略这条建议；返回改动条数（0／1）。子类实现。"""
        raise NotImplementedError

    # ── 共用实现 ────────────────────────────────────────────────────

    def _make_row(
        self,
        spec: ReviewSection,
        pagename: str,
        idx: int,
        blk,
        *,
        detail: str = "",
    ) -> TaskRow:
        """块 → 列表行。``checked=False``：勾选是「选中这条待办」，不是默认口径。"""
        cells = [pagename, _clip(blk.get_text() or "")]
        if detail:
            cells.append(detail)
        return TaskRow(
            key=(pagename, idx),
            pagename=pagename,
            cells=cells,
            checked=False,
            block_index=idx,
            payload={"section": spec.id, "badge": spec.badge, "tone": spec.tone},
        )

    def _section(self, section_id: str) -> Optional[ReviewSection]:
        return next(
            (spec for spec in self.sections() if spec.id == section_id), None
        )

    def plan(self) -> dict:
        """只读规划：各分区的行 ＋ 摘要 ＋ 页面上的说明行。"""
        rows = self.collect()
        counts = {section_id: len(items) for section_id, items in rows.items()}
        plan = {
            "sections": rows,
            "summary": self.summary_text(counts),
            "detail": self.detail_text(),
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        return self.block_preview(row.pagename, row.block_index)

    def row_caption(self, row: TaskRow) -> str:
        return block_row_caption(row.pagename, row.block_index)

    def card_fields(self, row: TaskRow) -> Optional[dict]:
        """卡片行：主文＝原文摘要，次行＝页 · 块号，徽章＝这份记录的来源。"""
        meta = [row.pagename]
        if row.block_index is not None:
            meta.append(
                QCoreApplication.translate("WorkbenchTasks", "block %1").replace(
                    "%1", str(row.block_index)
                )
            )
        detail = row.cells[2] if len(row.cells) > 2 else ""
        if detail:
            meta.append(detail)
        return {
            "primary": row.cells[1] if len(row.cells) > 1 else "",
            "meta": " · ".join(part for part in meta if part),
            "badge": row.payload.get("badge", ""),
            "badge_tone": row.payload.get("tone", "muted"),
            "rejected": False,  # 待办队列没有「已驳回」这一态
        }

    def run_section_action(
        self, section_id: str, rows: Sequence[TaskRow]
    ) -> Optional[dict]:
        """处置一批行（分区动作）：只动这份记录本身。``None`` ＝ 没有可做的。"""
        spec = self._section(section_id)
        if spec is None or not rows:
            return None
        changed = 0
        for row in rows:
            blk, _rect = self.block_at(row.pagename, row.block_index)
            if blk is None:
                continue
            changed += self.drop_record(section_id, blk)
        if changed and self.on_changed is not None:
            self.on_changed()
        if not changed:
            return {
                "replan": True,
                "notify": QCoreApplication.translate(
                    "WorkbenchTasks", "Nothing to remove here."
                ),
            }
        return {"replan": True, "notify": spec.notify.replace("%1", str(changed))}

    def pending(self, options: Optional[dict] = None) -> Optional[int]:
        """导航计数；子类按"这算不算用户承诺的工作"各自给口径。"""
        return None

    def count_label(self) -> str:
        return QCoreApplication.translate("WorkbenchTasks", "items")

    def reset(self) -> None:
        self._pages.clear()
        self._order.clear()
        self._last_plan = None


class OcrReviewTask(ReviewQueueTask):
    """原文待校对：人工记录的待办在前，程序低置信度**建议**在后（不共用语义）。"""

    id = OCR_REVIEW

    action_id = "act_ocr_fix"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = QCoreApplication.translate("WorkbenchTasks", "Review source text")
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "Blocks you recorded for proofreading, plus low-confidence blocks found by OCR. Nothing here is deleted or rewritten: open a block on the canvas to proofread it, or remove the record once you are done. The suggestions below are only hints and can be ignored.",
        )

    def sections(self) -> Sequence[ReviewSection]:
        return (
            ReviewSection(
                id="manual",
                title=QCoreApplication.translate("WorkbenchTasks", "Recorded by you"),
                empty=QCoreApplication.translate(
                    "WorkbenchTasks",
                    "No block is recorded for proofreading. Select a block on the canvas and record it for later.",
                ),
                action_label=QCoreApplication.translate(
                    "WorkbenchTasks", "Remove from my list"
                ),
                notify=QCoreApplication.translate(
                    "WorkbenchTasks", "Removed %1 block(s) from the review list."
                ),
                badge=QCoreApplication.translate(
                    "WorkbenchTasks", "Recorded by you"
                ),
                tone="warning",
            ),
            ReviewSection(
                id="suggestions",
                title=QCoreApplication.translate(
                    "WorkbenchTasks", "Low-confidence suggestions"
                ),
                empty=QCoreApplication.translate(
                    "WorkbenchTasks",
                    "OCR reported no low-confidence block. Only engines that report a score produce suggestions — a missing score is not treated as a low one.",
                ),
                action_label=QCoreApplication.translate(
                    "WorkbenchTasks", "Ignore suggestion"
                ),
                notify=QCoreApplication.translate(
                    "WorkbenchTasks", "Ignored %1 suggestion(s)."
                ),
                badge=QCoreApplication.translate("WorkbenchTasks", "Low confidence"),
            ),
        )

    def collect(self) -> Dict[str, List[TaskRow]]:
        specs = {spec.id: spec for spec in self.sections()}
        rows: Dict[str, List[TaskRow]] = {section_id: [] for section_id in specs}
        for pagename, blks in (self.proj.pages or {}).items():
            for idx, blk in enumerate(blks or []):
                # 人工记录优先：同一个块不再进"建议"区（前者是用户的明确待办）
                if has_ocr_review_pending(blk):
                    rows["manual"].append(
                        self._make_row(specs["manual"], pagename, idx, blk)
                    )
                    continue
                entry = blk.tags.get("ocr_low_conf")
                if (
                    isinstance(entry, dict)
                    and entry.get("source") == "program"
                    and not is_tag_reviewed(blk, "ocr_low_conf")
                ):
                    score = entry.get("score")
                    detail = (
                        ""
                        if score is None
                        else QCoreApplication.translate(
                            "WorkbenchTasks", "score %1"
                        ).replace("%1", f"{float(score):.2f}")
                    )
                    rows["suggestions"].append(
                        self._make_row(
                            specs["suggestions"], pagename, idx, blk, detail=detail
                        )
                    )
        return rows

    def summary_text(self, counts: Dict[str, int]) -> str:
        return (
            QCoreApplication.translate(
                "WorkbenchTasks",
                "%1 block(s) recorded by you; %2 low-confidence suggestion(s).",
            )
            .replace("%1", str(counts.get("manual", 0)))
            .replace("%2", str(counts.get("suggestions", 0)))
        )

    def detail_text(self) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks",
            "Removing a record only clears your note, and ignoring a suggestion only marks it as reviewed. Neither deletes a block, changes the source text or the translation, nor clears the handwritten/onomatopoeia directives.",
        )

    def drop_record(self, section_id: str, blk) -> int:
        if section_id == "suggestions":
            return set_tags_reviewed(blk, True, "ocr_low_conf")
        changed = 0
        if OCR_REVIEW_ID in blk.tags:
            remove_tag(blk, OCR_REVIEW_ID)
            changed += 1
        entry = blk.tags.get("ocr_low_conf")
        if isinstance(entry, dict) and entry.get("source") == "manual":
            remove_tag(blk, "ocr_low_conf")
            changed += 1
        return 1 if changed else 0

    def pending(self, options: Optional[dict] = None) -> Optional[int]:
        # 只数人工记下的：程序建议不冒充用户承诺要做的工作（交接 §4.2③）
        return sum(
            1
            for blks in (self.proj.pages or {}).values()
            for blk in blks or []
            if has_ocr_review_pending(blk)
        )

    def count_label(self) -> str:
        # 标签「原文待校对」已说清口径，再缀「待审校」是重复；只留数字
        return ""


class TransReviewTask(ReviewQueueTask):
    """译文待重译：只列人工记下的待办，**不假装能自动检测词不达意**。"""

    id = TRANS_REVIEW

    action_id = "act_retranslate"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = QCoreApplication.translate("WorkbenchTasks", "Retranslate")
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "Blocks you recorded for retranslation later. Nothing here is rewritten: open a block on the canvas and use the retranslate card, then remove the record once you are done.",
        )

    def sections(self) -> Sequence[ReviewSection]:
        return (
            ReviewSection(
                id="pending",
                title=QCoreApplication.translate(
                    "WorkbenchTasks", "Waiting for retranslation"
                ),
                empty=QCoreApplication.translate(
                    "WorkbenchTasks",
                    "No block is waiting for retranslation. Select a block on the canvas after translating and record it for later.",
                ),
                action_label=QCoreApplication.translate(
                    "WorkbenchTasks", "Remove from my list"
                ),
                notify=QCoreApplication.translate(
                    "WorkbenchTasks",
                    "Removed %1 block(s) from the retranslation list.",
                ),
                badge=QCoreApplication.translate(
                    "WorkbenchTasks", "To retranslate"
                ),
            ),
        )

    def collect(self) -> Dict[str, List[TaskRow]]:
        spec = self.sections()[0]
        rows: List[TaskRow] = []
        for pagename, blks in (self.proj.pages or {}).items():
            for idx, blk in enumerate(blks or []):
                if not has_trans_review_pending(blk):
                    continue
                rows.append(
                    self._make_row(
                        spec,
                        pagename,
                        idx,
                        blk,
                        detail=_clip(blk.translation) if blk.translation else "",
                    )
                )
        return {spec.id: rows}

    def summary_text(self, counts: Dict[str, int]) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks", "%1 block(s) waiting for retranslation."
        ).replace("%1", str(counts.get("pending", 0)))

    def detail_text(self) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks",
            "Removing a record only clears your note. It changes no text and starts no retranslation — that happens on the canvas, one block at a time.",
        )

    def drop_record(self, section_id: str, blk) -> int:
        changed = 0
        for tag_id in (TRANS_REVIEW_ID, "trans_confusing", "trans_polish"):
            if tag_id in blk.tags:
                remove_tag(blk, tag_id)
                changed += 1
        return 1 if changed else 0

    def pending(self, options: Optional[dict] = None) -> Optional[int]:
        return sum(
            1
            for blks in (self.proj.pages or {}).values()
            for blk in blks or []
            if has_trans_review_pending(blk)
        )

    def count_label(self) -> str:
        # 同 OcrReviewTask：标签已含「待重译」，只留数字（组/页的单位才保留）
        return ""


def build_review_tasks(
    proj, *, on_changed: Optional[Callable[[], None]] = None
) -> Dict[str, ReviewQueueTask]:
    """按导航顺序建两个待办队列（原文待校对 → 译文待重译）。"""
    tasks: List[ReviewQueueTask] = [
        OcrReviewTask(proj, on_changed=on_changed),
        TransReviewTask(proj, on_changed=on_changed),
    ]
    return {task.id: task for task in tasks}
