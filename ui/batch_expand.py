"""批量框扩张（工作台任务，规划 D5／D36／§6.4）。

用户场景（规划 §4「逐页人工审校 OCR」）：检测框紧贴文字，译文渲染时常觉得
"框不够大"——需要**给渲染留出空间**。本模块把全书（或选定）的框一次性外扩，
上限是"碰到邻框即停"。

**只扩渲染区域，不动擦除区域**（D5）：落到数据上就是只改
``utils/textblock.py::TextBlock`` 的 ``_bounding_rect`` 与 ``xyxy``（D36 接受
两者同步变动，与手动拖拽同一条 sync 路径），而**掩码与修复数据原样保留**
（``region_mask``／``region_inpaint_dict`` 不动——它们是擦除侧的像素产物）。
注意由此带出的既有效应：下次跑修复管线时，若管线按新 ``xyxy`` 重算掩码，擦除
范围会随之变化；那是 D36 已接受的取舍，本模块不另做补偿。

**扩多少由参数给定，不设默认值**（规划未定义这个量）：``apply`` 要求显式的
``amount`` 与 ``mode``——``"px"``（每边固定像素）或 ``"ratio"``（每边＝框短边
的该比例，跨分辨率一致）。调用方（工作台的输入控件）决定数值，``plan`` 顺带
回报"多少框已经贴住邻框／页边、扩不动"，供挑数值时参考。

**其余口径**：

- **同页内串行扩张**：邻框取"当前已扩后"的矩形，故扩张后**两两不重叠**
  （C3 的验收点）；代价是顺序相关——页块列表靠前的框先占空档，后面的停在它
  边缘。若按原始矩形各自求限，两个相邻框会互相穿过（双边都越界）。
- 旋转框（``angle != 0``）与退化矩形不参与（轴对齐的外扩对旋转框无意义；
  ``ui/mainwindow.py`` 的既有批量对齐 ``execute_advanced_align`` 同样跳过旋转框）；
- 写回走 ``ui/batch_ops.py`` 的 D40 五步（页代数在此步）与 D35 版本撤回；
- **扩张后的块是副本**：版本快照在写回前从内存取，若原地改块对象，快照会记成
  改动后的状态、撤销等于没撤——故新列表里放的是 ``copy.deepcopy`` 后改过矩形的
  块，原对象不动；
- 取消（``should_stop``）发生在落版本之前，等价于"什么都没发生"。
"""

import copy
import os.path as osp
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PIL import Image

from utils.block_geometry import expand_limited, rect_of
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock

from .batch_ops import BatchOperation

# 进版本元信息的任务名；D35 的撤销提示会读它。工作台接线时应传一个已翻译的
# 标签覆盖它（先例见 ui/batch_inpaint.py::DEFAULT_LABEL）。
DEFAULT_LABEL = "Expand blocks"

# plan() 的跳过原因码（不含用户可见文案，工作台自行翻译）
SKIP_NO_BLOCKS = "no-blocks"
SKIP_NO_SIZE = "no-page-size"

# 扩张量的两种给法（见模块 docstring）
MODE_PX = "px"
MODE_RATIO = "ratio"
MODES = (MODE_PX, MODE_RATIO)

# 块标识：``(页名, 块在页块列表里的下标)``
BlockKey = Tuple[str, int]

Rect = List[int]


class BatchExpand:
    """框扩张的规划（``plan``）与整批写回（``apply``）。"""

    def __init__(
        self,
        proj: ProjImgTrans,
        *,
        op: Optional[BatchOperation] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            proj: 项目实例。
            op: 批量事务外壳（版本 + 落盘）；缺省按 ``proj`` 新建一个（没有
                UI 收尾的裸用法）。工作台应传带 ``commit``／``sync_block_data``
                的实例，好让版本快照反映当前面板编辑。
            on_progress: 规划阶段 ``(已扫页数, 总页数, 页名)``，供进度 UI。
            should_stop: 应用的页间取消口。
        """
        self.proj = proj
        self.op = op or BatchOperation(proj)
        self.on_progress = on_progress
        self.should_stop = should_stop

    # ── 规划（只读）─────────────────────────────────────────────────

    def plan(
        self,
        amount: float,
        mode: str = MODE_PX,
        pages: Optional[Iterable[str]] = None,
    ) -> dict:
        """只读算一遍"这批框会变成什么样"（不写任何数据、不加载图像）。

        Args:
            amount: 每边的扩张量（``mode="ratio"`` 时为短边比例，如 ``0.1``）。
            mode: ``"px"`` 或 ``"ratio"``。
            pages: 只扫这些页；缺省全书。

        Returns:
            ``{"amount", "mode", "blocks", "changed", "unchanged", "clamped",
            "entries", "pages", "skipped"}``：

            - ``blocks``／``changed``：参与判定的框数／其中会变大的框数；
            - ``unchanged``：算完与原值相同的框数（已贴住邻框或页边）；
            - ``clamped``：至少有**一条边**被邻框截住的框数（挑数值时的参考）；
            - ``entries``：会变大的框，``{"pagename", "index", "old", "new"}``；
            - ``pages``：``{页名: 改动框数}``；
            - ``skipped``：``{页名: 原因码}``（见模块常量 ``SKIP_*``）。

        **同页内按页块列表顺序串行扩张**：每个框的邻框取"当前已扩后"的矩形，
        故扩张结果**两两不重叠**（C3 的验收点），代价是顺序相关——靠前的框先
        占空档、后面的停在它边缘。
        """
        if mode not in MODES:
            raise ValueError(f"unknown expand mode: {mode!r}")
        names = [p for p in (pages if pages is not None else self.proj.pages)
                 if p in self.proj.pages]
        entries: List[dict] = []
        counts: Dict[str, int] = {}
        skipped: Dict[str, str] = {}
        blocks = 0
        for done, pagename in enumerate(names):
            if self.on_progress is not None:
                self.on_progress(done, len(names), pagename)
            blk_list = self.proj.pages.get(pagename) or []
            if not blk_list:
                skipped[pagename] = SKIP_NO_BLOCKS
                continue
            rects = [rect_of(blk) for blk in blk_list]
            page_size = self._page_size(pagename)
            if page_size is None:
                skipped[pagename] = SKIP_NO_SIZE
                continue
            # 串行扩张：邻框取**当前已扩后**的矩形，故两个相邻框不会对穿
            # （各按原始矩形求限时，双边都会越界——拆分表 C3 的验收点要求
            # "扩张后无重叠"）。代价是顺序相关：页块列表靠前的框先占空档，
            # 后面的停在它边缘；块的顺序本就是人工为准（D7）。
            current = list(rects)
            for index, rect in enumerate(rects):
                if rect is None or getattr(blk_list[index], "angle", 0):
                    continue
                blocks += 1
                neighbors = [r for i, r in enumerate(current) if i != index and r]
                grown = expand_limited(
                    rect, self._amount_for(rect, amount, mode), page_size, neighbors
                )
                current[index] = grown
                if grown == rect:
                    continue
                counts[pagename] = counts.get(pagename, 0) + 1
                entries.append(
                    {"pagename": pagename, "index": index, "old": list(rect), "new": grown}
                )
        changed = len(entries)
        return {
            "amount": amount,
            "mode": mode,
            "blocks": blocks,
            "changed": changed,
            "unchanged": blocks - changed,
            "clamped": sum(
                1 for e in entries if self._clamped(e["old"], e["new"], amount, mode)
            ),
            "entries": entries,
            "pages": counts,
            "skipped": skipped,
        }

    # ── 执行（写回）─────────────────────────────────────────────────

    def apply(
        self,
        amount: float,
        mode: str = MODE_PX,
        selection: Optional[Iterable[BlockKey]] = None,
        *,
        label: Optional[str] = None,
        mark_dirty: bool = True,
    ) -> dict:
        """整批扩张并写回（重新规划 → D40 五步 → 落盘）。

        Args:
            amount: 每边的扩张量（``mode="ratio"`` 时为短边比例）。
            mode: ``"px"`` 或 ``"ratio"``。
            selection: 只扩这些块（``(页名, 页内下标)``）；缺省＝全书可扩张的框
                （旋转框与退化矩形本就不参与）。标识已失效的块进 ``stale``。
            label: 进版本元信息的任务名；缺省 ``DEFAULT_LABEL``。
            mark_dirty: 是否把改过的页标脏（默认真——渲染矩形变了，结果图会过期）。

        Returns:
            ``{"started", "version", "writeback", "blocks", "pages", "stale",
            "error"}``；``started`` 为假表示没有执行（取消／没有可扩张的框／
            备份版本没写成），此时数据未动。
        """
        report: dict = {
            "started": False,
            "version": None,
            "writeback": None,
            "blocks": 0,
            "pages": [],
            "stale": [],
            "error": None,
        }
        # D40 第 ① 步前置对齐先于规划：块矩形可能刚被面板改过（``BatchWriteback``
        # 内会再调一次，届时判据已为假、是空操作）。
        self._sync_block_data()
        plan = self.plan(amount, mode)

        wanted = set(selection) if selection is not None else None
        if wanted is not None:
            report["stale"] = sorted(wanted - {self._key(e) for e in plan["entries"]})
        page_edits: Dict[str, List[TextBlock]] = {}
        blocks = 0
        for pagename in dict.fromkeys(e["pagename"] for e in plan["entries"]):
            if self.should_stop is not None and self.should_stop():
                report["error"] = "cancelled"
                return report
            blk_list = self.proj.pages.get(pagename)
            if blk_list is None:
                continue
            edits = {
                e["index"]: e["new"]
                for e in plan["entries"]
                if e["pagename"] == pagename
                and (wanted is None or self._key(e) in wanted)
                and e["index"] < len(blk_list)
            }
            if not edits:
                continue
            new_list: List[TextBlock] = []
            for index, blk in enumerate(blk_list):
                if index not in edits:
                    new_list.append(blk)
                    continue
                grown = copy.deepcopy(blk)  # 副本：原对象留给版本快照
                grown.xyxy = list(edits[index])
                grown._bounding_rect = _rect_to_xywh(edits[index])
                new_list.append(grown)
                blocks += 1
            page_edits[pagename] = new_list

        if not page_edits:
            report["error"] = "stale" if report["stale"] else "nothing-to-expand"
            return report

        result = self.op.apply(label or DEFAULT_LABEL, page_edits, mark_dirty=mark_dirty)
        report.update(
            started=bool(result.get("started")),
            version=result.get("version"),
            writeback=result.get("writeback"),
            error=result.get("error"),
            blocks=blocks,
            pages=list(page_edits),
        )
        return report

    # ── 工具 ────────────────────────────────────────────────────────

    @staticmethod
    def _key(entry: dict) -> BlockKey:
        return (entry["pagename"], entry["index"])

    @staticmethod
    def _amount_for(rect: Rect, amount: float, mode: str) -> int:
        """把扩张量换算成该框每边的像素数（``ratio`` 取短边比例）。"""
        if mode == MODE_RATIO:
            short = min(rect[2] - rect[0], rect[3] - rect[1])
            return int(round(short * amount))
        return int(round(amount))

    @staticmethod
    def _clamped(old: Rect, new: Rect, amount: float, mode: str) -> bool:
        """是否至少一条边没扩够（被邻框或页边截住）。"""
        want = BatchExpand._amount_for(old, amount, mode)
        return (
            new[0] > old[0] - want
            or new[1] > old[1] - want
            or new[2] < old[2] + want
            or new[3] < old[3] + want
        )

    def _page_size(self, pagename: str) -> Optional[Tuple[int, int]]:
        """页面像素尺寸：先用项目已存的宽高，取不到再读图片文件头。

        两个来源都取不到（缺图且无记录）时该页跳过——没有页边界就没有"扩到哪
        为止"的依据。
        """
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

    def _sync_block_data(self) -> None:
        """把面板上的未回写编辑并进数据层（D40 第 ① 步的前置对齐）。"""
        writer = getattr(self.op, "writer", None)
        sync = getattr(writer, "sync_block_data", None)
        if sync is None:
            return
        try:
            sync()
        except Exception as e:
            LOGGER.error(f"Pre-expand sync failed: {e}")


def _rect_to_xywh(rect: Rect) -> List[int]:
    """``xyxy`` → ``_bounding_rect`` 的 ``[x, y, w, h]``。"""
    return [rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]]
