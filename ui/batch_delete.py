"""一键批量删除误框（工作台任务，设计 §4／§13＋D2／D27／D28）。

用户场景（设计 §2 的「逐页人工审校 OCR」）：检测模型常把画面元素、拟声词残片、
边框装饰切成一堆「文本框」，内容是纯数字／纯符号／一片乱码。它们在原位渲染时
是可见的垃圾，人工逐个删除又极费时。本模块把这类框（＝挂「误识别文本」标签的
框，见 ``utils/block_tags.py::iter_misread_blocks``）按队列列出来，人确认后
**一次删掉整批**。

**只删文本框层**：本任务只从 ``utils/proj_imgtrans.py::ProjImgTrans`` 的
``pages`` 里摘掉这些块，**不动遮罩与修复图**。已在 ``inpainted/`` 层留下的痕迹
（若此前跑过修复）不会因此还原——要还原请用修复侧自己的撤销。D27 的告知弹窗
应把这一点讲清楚：删的是"框与它的译文渲染"，不是"原图上的内容"。

**队列与默认口径**（D28）：队列＝带误识别标签的块（按块去重，全书范围）；
``reviewed``（人已驳回＝确认标签判错、框是正常文本）的块**默认不删**——
驳回与「勾选待删除」是两轴正交（D27）：默认口径取保守侧，显式勾选则照删。

**为什么不能复用现成的删除命令**（D27）：``ui/scenetext_manager.py``
的 ``DeleteBlkItemsCommand`` 只作用于**当前页**（按索引操作当前页的 widget 与
图像缓冲、且不写 ``proj.pages``），跨页批量用不了；它还带一条「顺手把该区域
修复掉」的支路（``mode == 1`` 会抓前图、改遮罩与 ``inpainted``），与本节只删
文本框的口径也不同。批量路径另写：写回走 ``ui/batch_ops.py`` 的 D40 五步
（页代数在此步，防端点快照落到错误块），撤销走 D35 的版本备份（D27：删除属
新出口，行为要弹窗告知）。

**取消语义**：``apply`` 先把全书改动算完、再一次性落版本写回，故取消等价于
"什么都没发生"，不留半成品。
"""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from utils.block_tags import (
    MISREAD_TAG_ID,
    get_tag,
    is_tag_reviewed,
    iter_misread_blocks,
    misread_queue_summary,
)
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock

from .batch_ops import BatchOperation

# 进版本元信息的任务名；D35 的撤销提示会读它。工作台接线时应传一个已翻译的
# 标签覆盖它（先例见 ui/batch_inpaint.py::DEFAULT_LABEL）。
DEFAULT_LABEL = "Delete misread blocks"

# 块标识：``(页名, 块在页块列表里的下标)``——plan 与 apply 之间的选取凭据。
BlockKey = Tuple[str, int]


@dataclass
class MisreadEntry:
    """队列里的一条（不带块对象——跨页回调按 ``(页名, 下标)`` 寻址）。"""

    pagename: str
    index: int
    text: str
    """该块当前原文（队列列表用它做预览）。"""

    subtypes: List[str]
    """命中的误识别子类（D38：空文本／纯数字／纯符号／无假名汉字，可多类）。"""

    reviewed: bool
    """人是否已驳回（＝标签判错、框是正常文本）。"""

    @property
    def key(self) -> BlockKey:
        return (self.pagename, self.index)

    def to_dict(self) -> dict:
        return {
            "pagename": self.pagename,
            "index": self.index,
            "text": self.text,
            "subtypes": list(self.subtypes),
            "reviewed": self.reviewed,
        }


class BatchDeleteMisread:
    """误框队列的规划（``plan``）与整批删除（``apply``）。"""

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
            should_stop: 规划与应用的页间取消口。
        """
        self.proj = proj
        self.op = op or BatchOperation(proj)
        self.on_progress = on_progress
        self.should_stop = should_stop

    # ── 规划（只读）─────────────────────────────────────────────────

    def plan(self) -> dict:
        """只读扫描全书，列出误框队列（供 D27 的告知弹窗与队列 UI）。

        只读——不写文件、不改内存数据、不加载图像。**队列扫描不支持取消**：
        它是纯内存遍历（无 I/O），而"扫到一半"的部分计数会让 D27 的告知弹窗
        说错数；取消只发生在 ``apply`` 的页间。

        Returns:
            ``{"entries", "queue", "rejected", "pending", "apply_default",
            "pages"}``：

            - ``entries``：``MisreadEntry`` 列表（按页序、页内下标升序）；
            - ``queue``／``rejected``／``pending``：队列内块数／已驳回数／待处理
              数（＝前两者之差，D28 口径，与 ``utils/block_tags.py::misread_queue_summary``
              同）；``rejected`` 的块仍在 ``entries`` 里（驳回可逆，D28）；
            - ``apply_default``：``apply`` 缺省会删的块数（＝``pending``）；
            - ``pages``：``{页名: 该页队列块数}``。
        """
        entries: List[MisreadEntry] = []
        counts: Dict[str, int] = {}
        pages = list(self.proj.pages)
        for done, pagename in enumerate(pages):
            if self.on_progress is not None:
                self.on_progress(done, len(pages), pagename)
            for name, index, blk in iter_misread_blocks(
                {pagename: self.proj.pages.get(pagename) or []}
            ):
                entries.append(self._entry(name, index, blk))
                counts[name] = counts.get(name, 0) + 1

        summary = misread_queue_summary(self.proj.pages)
        return {
            "entries": entries,
            "queue": summary["total"],
            "rejected": summary["rejected"],
            "pending": summary["pending"],
            "apply_default": summary["pending"],
            "pages": counts,
        }

    @staticmethod
    def _entry(pagename: str, index: int, blk: TextBlock) -> MisreadEntry:
        entry = get_tag(blk, MISREAD_TAG_ID) or {}
        subtypes = list(entry.get("subtypes") or [])
        return MisreadEntry(
            pagename=pagename,
            index=index,
            text=blk.get_text(),
            subtypes=subtypes,
            reviewed=is_tag_reviewed(blk, MISREAD_TAG_ID),
        )

    # ── 执行（写回）─────────────────────────────────────────────────

    def apply(
        self,
        selection: Optional[Iterable[BlockKey]] = None,
        *,
        label: Optional[str] = None,
        mark_dirty: bool = True,
    ) -> dict:
        """整批删除（重新规划 → D40 五步写回 → 落盘）。

        Args:
            selection: 要删的块标识（``(页名, 页内下标)``）。缺省＝**队列内
                未驳回**的块（保守侧；驳回＝人已确认是正常文本，不进默认口径）。
                显式勾选时不受驳回状态限制——驳回与"待删除"两轴正交（D27）。
                给的标识**必须仍是队列成员**（带误识别标签），否则不予删除并
                进报告的 ``stale``：本任务的出口只负责误框，不是通用删除。
            label: 进版本元信息的任务名；缺省 ``DEFAULT_LABEL``。
            mark_dirty: 是否把改过的页标脏（默认真——删块改的是文本层，结果图
                会过期，与人工删除一致）。

        Returns:
            ``{"started", "version", "writeback", "deleted", "pages", "stale",
            "error"}``：

            - ``started`` 为假表示没有执行（取消／队列为空／所选键都失效／备份
              版本没写成），此时数据未动；
            - ``deleted``／``pages``：删掉的块数与涉及的页；
            - ``writeback``：``ui/batch_ops.py::BatchWriteback.apply`` 的报告，
              其 ``verified`` 即 D40 的验收判据。
        """
        report: dict = {
            "started": False,
            "version": None,
            "writeback": None,
            "deleted": 0,
            "pages": [],
            "stale": [],
            "error": None,
        }
        # D40 第 ① 步前置对齐先于规划：数据层要反映面板上的编辑（否则队列里的
        # 下标可能与面板所见不一致）。``BatchWriteback`` 内会再调一次，届时
        # 判据已为假、是空操作。
        self._sync_block_data()
        plan = self.plan()

        by_key = {entry.key: entry for entry in plan["entries"]}
        if selection is None:
            chosen = [e for e in plan["entries"] if not e.reviewed]
        else:
            wanted = set(selection)
            chosen = [e for e in plan["entries"] if e.key in wanted]
            report["stale"] = sorted(wanted - set(by_key))
        if not chosen:
            report["error"] = "stale" if report["stale"] else "empty-queue"
            return report

        page_edits: Dict[str, List[TextBlock]] = {}
        deleted = 0
        applied: List[BlockKey] = []
        for pagename in dict.fromkeys(e.pagename for e in chosen):
            if self.should_stop is not None and self.should_stop():
                report["error"] = "cancelled"
                return report
            blk_list = self.proj.pages.get(pagename)
            if blk_list is None:
                continue
            drop = {
                e.index
                for e in chosen
                if e.pagename == pagename and e.index < len(blk_list)
            }
            applied.extend(
                e.key
                for e in chosen
                if e.pagename == pagename and e.index < len(blk_list)
            )
            if not drop:
                continue  # 本页的下标全部越界（块列表变过）→ 该页不动
            page_edits[pagename] = [
                blk for idx, blk in enumerate(blk_list) if idx not in drop
            ]
            deleted += len(drop)

        if not page_edits:
            report["error"] = "stale"
            report["stale"] = sorted(e.key for e in chosen)
            return report

        # 下标越界（plan 与 apply 之间块列表被改过）的块没删成 → 进 stale。
        report["stale"] = sorted(
            set(report["stale"]) | {e.key for e in chosen} - set(applied)
        )
        result = self.op.apply(label or DEFAULT_LABEL, page_edits, mark_dirty=mark_dirty)
        report.update(
            started=bool(result.get("started")),
            version=result.get("version"),
            writeback=result.get("writeback"),
            error=result.get("error"),
            deleted=deleted,
            pages=list(page_edits),
        )
        return report

    def _sync_block_data(self) -> None:
        """把面板上的未回写编辑并进数据层（D40 第 ① 步的前置对齐）。

        调用口与 ``ui/batch_ops.py::BatchWriteback`` 同一个（主窗口的
        ``_sync_block_data``），取自事务外壳；没有外壳或没接调用口时跳过。
        """
        writer = getattr(self.op, "writer", None)
        sync = getattr(writer, "sync_block_data", None)
        if sync is None:
            return
        try:
            sync()
        except Exception as e:
            LOGGER.error(f"Pre-delete sync failed: {e}")
