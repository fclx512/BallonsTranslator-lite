"""泛用工作台的**批量任务适配层**（D20／D27；接线清单见设计 §8）。

工作台把六个任务放进同一个容器：前四项是「问题清理」任务族的批量任务
（误识别清理／合并相邻框／框扩张／简单背景修复），后两项是既有的
术语提取／剧情摘要。本模块只服务前四项的**数据侧**——

- 调引擎的只读 ``plan``，把结果摆成列表行（``TaskRow``）；
- 给审批预览准备 **100% 原比例**（D11／D23）的截图与叠加框；
- 把用户勾选的标识交回引擎的 ``apply``。

**这里没有 QWidget**（Qt 只用于取已翻译文案），界面在
``ui/glossary_agent_panel.py``。之所以要这一层：四个引擎是同一个形状
（只读 ``plan`` ＋ 整批 ``apply``），界面据此只做三件事——填列表／算数字、
收勾选、把标识传回去；把"哪个引擎、哪些字段"的差异收在这里，界面就
不必为每个任务写一份。**界面不自己写几何判据、不自己改
``proj.pages``、不绕开 ``ui/batch_ops.py``**（设计 §8），本模块同样：
唯一的几何动作是审批截图的外扩，复用
``utils/block_geometry.py::expand_limited``（与引擎同一份实现）。

**默认值一律来自引擎的 plan**，界面不自作主张：合并的默认勾选＝非误聚组
（D33d）、误识别的默认勾选＝未驳回块（D28）。扩张量在**引擎侧没有默认值**
（D5：必须由调用方显式给），工作台的输入框初值则取自设置里的
``utils/config.py::ProgramConfig`` 的 ``workbench_expand_px``（2026-09-18
拍板定值 10px）——用户把该值改成 0 时列表为空、执行按钮保持禁用。

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
    misread_queue_summary,
    set_tags_reviewed,
)
from utils.config import pcfg
from utils.io_utils import imread

from .batch_delete import BatchDeleteMisread
from .batch_expand import MODE_PX, MODE_RATIO, BatchExpand
from .batch_merge import BatchMerge, MergeConfig
from .batch_ops import BatchOperation

# 任务 id 即导航顺序的依据（设计 §8／D16：误识别清理 → 合并 → 扩张 → 背景修复
# → 术语提取 → 剧情摘要）。前四项属「问题清理」任务族，参与跳步提示计数。
MISREAD = "misread"
MERGE = "merge"
EXPAND = "expand"
SIMPLE_INPAINT = "simple_inpaint"
GLOSSARY = "glossary"
STORY = "story"

CLEANUP_TASK_IDS = (MISREAD, MERGE, EXPAND, SIMPLE_INPAINT)

# 扩张量的单位（与 ui/batch_expand.py 的模式常量一一对应）
AMOUNT_MODE_KEYS = (MODE_PX, MODE_RATIO)

# 预览外扩量（D31：审批截图＝并集框短边的 50%、遇邻框即停）
PREVIEW_EXPAND_RATIO = 0.5

# 页面图像缓存条数——逐行翻看审批图时不必反复解码同一张大图
_PAGE_CACHE_SIZE = 2


def _clip(text: str, limit: int = 42) -> str:
    """列表单元格的单行摘要（队列列表不铺满整段原文）。"""
    flat = " ".join((text or "").split())
    if not flat:
        return "—"
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── 审批预览（D11／D23：100% 原比例）────────────────────────────────


@dataclass
class Preview:
    """一张审批预览图：**未缩放**的 RGB 图 ＋ 页面坐标系的叠加框。"""

    image: Any
    origin: Tuple[int, int]
    overlays: List[dict] = field(default_factory=list)
    """``[{"rect": [x1, y1, x2, y2], "role": "target"|"old"|"new"}, ...]``
    ——``rect`` 是**页面坐标**，视图自行减去 ``origin`` 换算到图上。"""


@dataclass
class TaskRow:
    """候选列表的一行（``key`` 是 ``apply`` 的选取凭据）。"""

    key: Any
    pagename: str
    cells: List[str]
    checked: bool = True
    block_index: Optional[int] = None
    payload: dict = field(default_factory=dict)


class BatchTask:
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
        self._pages: Dict[str, Any] = {}
        self._order: List[str] = []
        self._last_plan: Optional[dict] = None

    # ── 子类接口 ────────────────────────────────────────────────────

    def plan(self, options: Dict[str, Any]) -> dict:
        """只读规划。返回 ``{"rows", "summary", "options_hint"}``。"""
        raise NotImplementedError

    def preview(self, row: TaskRow) -> Optional[Preview]:
        """该行的审批预览（100% 原比例）；取不到图返回 ``None``。"""
        return None

    def row_caption(self, row: TaskRow) -> str:
        """审批浮层的标题：**说清在看哪一页的哪个框**。

        图尺寸（"150 × 60 px"）对审阅没有意义——它既不是块尺寸也不是页尺寸，
        还每行都不同；缩放与否看浮层标题条右侧的读数。子类按自己的行粒度
        覆盖（组／页级任务给条数）。
        """
        if row.block_index is None:
            return row.pagename
        return QCoreApplication.translate(
            "WorkbenchTasks", "%1 · block %2"
        ).replace("%1", row.pagename).replace("%2", str(row.block_index))

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

    def crop_around(
        self,
        pagename: str,
        rect: Sequence[int],
        *,
        exclude: Iterable[int] = (),
        ratio: float = PREVIEW_EXPAND_RATIO,
    ) -> Optional[Tuple[Any, Tuple[int, int]]]:
        """裁出 ``rect`` 外扩后的区域，**不缩放**（D11）。

        外扩量＝矩形短边的 ``ratio``，上限"碰到邻框即停"——与
        ``ui/batch_merge.py`` 的审批截图共用
        ``utils/block_geometry.py::expand_limited``。
        """
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
        QCoreApplication.translate("WorkbenchTasks", "Review"),
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
            "WorkbenchTasks", "Delete misread blocks"
        )
        self.title = QCoreApplication.translate(
            "WorkbenchTasks", "Misread cleanup"
        )
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "Blocks whose OCR text looks like noise. Reject the false positives first, then delete the rest in one go.",
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
            review = (
                QCoreApplication.translate("WorkbenchTasks", "Rejected")
                if entry.reviewed
                else QCoreApplication.translate("WorkbenchTasks", "Pending")
            )
            cells = [entry.pagename, _clip(entry.text), subtypes or "—", review]
            rows.append(
                TaskRow(
                    key=entry.key,
                    pagename=entry.pagename,
                    cells=cells,
                    checked=not entry.reviewed,
                    block_index=entry.index,
                    payload={"reviewed": entry.reviewed},
                )
            )
        summary = QCoreApplication.translate(
            "WorkbenchTasks",
            "%1 in the queue, %2 rejected, %3 to delete by default.",
        )
        plan = {
            "rows": rows,
            "summary": summary.replace("%1", str(report["queue"]))
            .replace("%2", str(report["rejected"]))
            .replace("%3", str(report["pending"])),
            "options_hint": "",
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        blk_list = self.proj.pages.get(row.pagename) or []
        if row.block_index is None or row.block_index >= len(blk_list):
            return None
        rect = rect_of(blk_list[row.block_index])
        if rect is None:
            return None
        cropped = self.crop_around(row.pagename, rect, exclude={row.block_index})
        if cropped is None:
            return None
        image, origin = cropped
        return Preview(
            image=image, origin=origin, overlays=[{"rect": rect, "role": "target"}]
        )

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
                    "WorkbenchTasks", "Reject / un-reject selection"
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
            changed += set_tags_reviewed(blk_list[row.block_index], target)
            row.payload["reviewed"] = target
        if changed and self.on_changed is not None:
            self.on_changed()
        if target:
            notify = QCoreApplication.translate(
                "WorkbenchTasks", "Rejected %1 block(s)."
            ).replace("%1", str(changed))
        else:
            notify = QCoreApplication.translate(
                "WorkbenchTasks", "Un-rejected %1 block(s)."
            ).replace("%1", str(changed))
        return {"replan": True, "notify": notify}

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
            "WorkbenchTasks", "Merge adjacent blocks"
        )
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "One row per candidate group. Click a row to preview it; suspected false groupings start unchecked.",
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

    def pending(self, options) -> Optional[int]:
        # 默认勾选口径＝非误聚组（D33d），与 apply 的缺省一致
        return self._engine().plan()["apply_default"]


# ── 批量框扩张（ui/batch_expand.py）─────────────────────────────────


class ExpandTask(BatchTask):
    """只扩渲染区域（D5）；引擎侧无默认扩张量，输入框初值取设置里的默认值。"""

    id = EXPAND
    stretch_column = 3  # 增长描述是唯一的长文本列
    columns = (
        QCoreApplication.translate("WorkbenchTasks", "Page"),
        QCoreApplication.translate("WorkbenchTasks", "Block"),
        QCoreApplication.translate("WorkbenchTasks", "Growth"),
    )

    # 各边的短名（增长列的「受限边」描述用；字面量定义处标注翻译上下文）
    _SIDE_KEYS = ("top", "bottom", "left", "right")
    _SIDE_LABELS = {
        "top": QCoreApplication.translate("WorkbenchTasks", "Top"),
        "bottom": QCoreApplication.translate("WorkbenchTasks", "Bottom"),
        "left": QCoreApplication.translate("WorkbenchTasks", "Left"),
        "right": QCoreApplication.translate("WorkbenchTasks", "Right"),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = QCoreApplication.translate("WorkbenchTasks", "Expand blocks")
        self.title = self.label
        self.hint = QCoreApplication.translate(
            "WorkbenchTasks",
            "Makes room for typesetting: grows each text block's rect so the translated text has more room. Only the rect changes (masks and inpainted pixels are untouched); the text re-flows inside the new rect, centered if the block's alignment is set to centered.",
        )

    @staticmethod
    def _growth_text(entry: dict, want: int) -> str:
        """一行的「增长」描述：只说每边长多少、哪边受限，不铺坐标数字。"""
        old, new = entry["old"], entry["new"]
        deltas = {
            "top": old[1] - new[1],
            "bottom": new[3] - old[3],
            "left": old[0] - new[0],
            "right": new[2] - old[2],
        }
        limited = [name for name in ExpandTask._SIDE_KEYS if deltas[name] < want]
        if not limited:
            return QCoreApplication.translate(
                "WorkbenchTasks", "+%1 px on all sides"
            ).replace("%1", str(want))
        # _SIDE_LABELS 的值已在定义处翻译（i18n 模块级翻译表规则），直接用
        names = "、".join(ExpandTask._SIDE_LABELS[name] for name in limited)
        return QCoreApplication.translate(
            "WorkbenchTasks", "+%1 px, %2 limited"
        ).replace("%1", str(want)).replace("%2", names)

    def _engine(self) -> BatchExpand:
        return BatchExpand(self.proj, op=self.op)

    @staticmethod
    def _raw_amount(options: Dict[str, Any]) -> float:
        """界面控件里的原始数值（``ratio`` 模式下是百分数）。"""
        try:
            return float(options.get("amount") or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _mode(options: Dict[str, Any]) -> str:
        mode = options.get("mode") or MODE_PX
        return mode if mode in AMOUNT_MODE_KEYS else MODE_PX

    def _amount(self, options: Dict[str, Any]) -> float:
        """给引擎的扩张量：``ratio`` 模式下把百分数换成短边比例。"""
        raw = self._raw_amount(options)
        return raw / 100.0 if self._mode(options) == MODE_RATIO else raw

    def plan(self, options: Dict[str, Any]) -> dict:
        amount = self._amount(options)
        if amount <= 0:
            self._last_plan = None
            return {
                "rows": [],
                "summary": QCoreApplication.translate(
                    "WorkbenchTasks", "Set an amount to preview the expansion."
                ),
                "options_hint": "",
            }
        mode = self._mode(options)
        report = self._engine().plan(amount, mode)
        # 每边请求量：px 模式全表一个值；ratio 模式按各框短边算（同引擎口径）
        want_px = int(round(amount)) if mode == MODE_PX else 0
        rows = []
        for entry in report["entries"]:
            if mode == MODE_PX:
                want = want_px
            else:
                short = min(
                    entry["old"][2] - entry["old"][0],
                    entry["old"][3] - entry["old"][1],
                )
                want = int(round(short * amount))
            rows.append(
                TaskRow(
                    key=(entry["pagename"], entry["index"]),
                    pagename=entry["pagename"],
                    cells=[
                        entry["pagename"],
                        str(entry["index"]),
                        self._growth_text(entry, want),
                    ],
                    checked=True,
                    block_index=entry["index"],
                    payload={"old": entry["old"], "new": entry["new"]},
                )
            )
        summary = QCoreApplication.translate(
            "WorkbenchTasks",
            "%1 block(s) can grow, %2 blocked already, %3 with at least one side clamped.",
        )
        plan = {
            "rows": rows,
            "summary": summary.replace("%1", str(report["changed"]))
            .replace("%2", str(report["unchanged"]))
            .replace("%3", str(report["clamped"])),
            "options_hint": "",
        }
        self._last_plan = plan
        return plan

    def preview(self, row: TaskRow) -> Optional[Preview]:
        old = row.payload.get("old")
        new = row.payload.get("new")
        if not old or not new:
            return None
        # 预览框取新旧并集再外扩，好让"扩到哪"看得见（D5 的数值就靠这个挑）
        union = [
            min(old[0], new[0]),
            min(old[1], new[1]),
            max(old[2], new[2]),
            max(old[3], new[3]),
        ]
        cropped = self.crop_around(row.pagename, union, exclude={row.block_index})
        if cropped is None:
            return None
        image, origin = cropped
        return Preview(
            image=image,
            origin=origin,
            overlays=[
                {"rect": old, "role": "old"},
                {"rect": new, "role": "new"},
            ],
        )

    def apply(self, keys, options):
        return self._engine().apply(
            self._amount(options), self._mode(options), keys, label=self.label
        )

    def options_spec(self) -> List[dict]:
        return [
            {
                "key": "amount",
                "kind": "int",
                "label": QCoreApplication.translate(
                    "WorkbenchTasks", "Grow each side by"
                ),
                "min": 0,
                "max": 500,
                # 初值取设置里的默认扩张量（D5／C3，2026-09-18 定值 10px）。
                # 引擎仍要求显式给 amount，这里只是输入框的起点，用户可改。
                "value": max(0, int(pcfg.workbench_expand_px)),
                # 后缀跟着「单位」走，且**不翻译**：px／% 是通用单位记号，
                # 中文语境下同样一眼看懂（写死"像素"反而不通用）。
                "suffix_map": ("mode", {MODE_PX: " px", MODE_RATIO: " %"}),
            },
            {
                "key": "mode",
                "kind": "choice",
                "label": QCoreApplication.translate("WorkbenchTasks", "Unit"),
                "items": [
                    (MODE_PX, "px"),
                    (
                        MODE_RATIO,
                        QCoreApplication.translate(
                            "WorkbenchTasks", "percent of the short side"
                        ),
                    ),
                ],
                "value": MODE_PX,
            },
        ]

    def execute_label(self, count: int) -> str:
        return QCoreApplication.translate(
            "WorkbenchTasks", "Expand %1 block(s)"
        ).replace("%1", str(count))

    def confirm_html(self, rows, options) -> str:
        amount = self._amount(options)
        if self._mode(options) == MODE_RATIO:
            size = QCoreApplication.translate(
                "WorkbenchTasks", "%1% of each block's short side per side"
            ).replace("%1", str(round(amount * 100, 1)))
        else:
            size = QCoreApplication.translate(
                "WorkbenchTasks", "%1 px per side"
            ).replace("%1", str(round(amount)))
        pages = len({row.pagename for row in rows})
        body = QCoreApplication.translate(
            "WorkbenchTasks",
            "Grows %1 block(s) across %2 page(s) by %3, stopping at a neighbouring block or the page edge.",
        )
        note = QCoreApplication.translate(
            "WorkbenchTasks",
            "Only the rendering rectangle changes (mask and inpainted data are kept). The whole batch can be rolled back in one step.",
        )
        return (
            "<p>"
            + body.replace("%1", str(len(rows)))
            .replace("%2", str(pages))
            .replace("%3", size)
            + "</p><p>"
            + note
            + "</p>"
        )

    def pending(self, options) -> Optional[int]:
        if self._amount(options) <= 0:
            return None
        return self._engine().plan(
            self._amount(options), self._mode(options)
        )["changed"]


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
            "Fills near-flat balloon interiors with their background colour; complex backgrounds are left alone (no model is loaded). Writes the inpainted layer only.",
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

    def pending(self, options) -> Optional[int]:
        # plan 要把全书页图读一遍（判据算原图，D34），跳步提示不值得为它重跑
        # 一遍——只回报**已经扫过**的那次结果
        if not self._last_plan:
            return None
        return sum(int(row.cells[1]) for row in self._last_plan["rows"])


# ── 工厂 ────────────────────────────────────────────────────────────


def build_batch_tasks(
    proj,
    op: Optional[BatchOperation] = None,
    *,
    inpainter_provider: Optional[Callable[[], Any]] = None,
    on_changed: Optional[Callable[[], None]] = None,
) -> Dict[str, BatchTask]:
    """按导航顺序建四个批量任务（D16 的默认顺序见 ``CLEANUP_TASK_IDS``）。"""
    tasks: List[BatchTask] = [
        MisreadTask(proj, op, on_changed=on_changed),
        MergeTask(proj, op, on_changed=on_changed),
        ExpandTask(proj, op, on_changed=on_changed),
        SimpleInpaintTask(
            proj, op, inpainter_provider=inpainter_provider, on_changed=on_changed
        ),
    ]
    return {task.id: task for task in tasks}
