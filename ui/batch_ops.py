"""批量操作的写回范式（规划 D40）与事务外壳（D35 + D40）。

工作台的批量任务（简单背景修复／合并相邻框／框扩张／删误框）都走这里，
以避免每处各写一遍"先改数据、再重建视觉层"的顺序而踩到脱节陷阱。

**D40 五步（不可交错）**：``BatchWriteback.apply`` 内逐步执行——

1. 前置对齐：调 ``ui/mainwindow.py::MainWindow`` 的 ``_sync_block_data``
   （它内部按 ``utils/block_actions.py::page_data_needs_sync`` 判据决定是否
   写回，不必重复判断）；
2. 只改 ``utils/proj_imgtrans.py::ProjImgTrans`` 的 ``pages`` 块列表；
3. 调 ``utils/proj_imgtrans.py::ProjImgTrans`` 的 ``bump_page_generation``
   （动图像时再加 ``bump_page_image_generation``）；
4. 当前页调 ``ui/scenetext_manager.py::SceneTextManager`` 的
   ``updateSceneTextitems`` 重建视觉层（数据 → UI 方向）；
5. **此后禁止**再调 ``updateTextBlkList``（UI → 数据方向：会用旧面板值覆盖
   新数据，并把 ``blk.text`` 折叠成单元素列表）。

**验收判据**：完成后 ``page_data_needs_sync(current_block_list(),
textblk_item_list)`` 必须为 ``False``——``apply`` 会现算一次放进返回值，
失败即记错误日志（见 ``apply`` 的 ``verified`` 字段）。

**一次批量操作的完整事务**：先落盘当前状态 → 写一个备份版本（D35）→
执行改动 → 再落盘；撤销走 ``BatchOperation.undo_last``（取最新版本覆盖并
消耗），撤销之后的 UI 收尾（清撤销栈、重画布、重建场景、跳页、弹窗告知
D27）由工作台负责，先例见 ``ui/mainwindow.py::MainWindow`` 的
``on_batch_rollback``。

事务有两半，按任务改的是数据还是像素分开：

- ``BatchOperation.apply``：**数据类任务**（合并相邻框／框扩张／删误框）
  的整段事务，改动经 D40 五步写回；
- ``BatchOperation.begin_pixels`` + ``BatchOperation.finish``：**像素类任务**
  （简单背景修复，见 ``ui/batch_inpaint.py::BatchSimpleInpaint``）的两端——
  它不改块列表，故不套 D40 五步（不动页代数、不重建视觉层），只在两端之间
  由任务自己写 ``inpainted/`` 层并 ``bump_page_image_generation``。
"""

from typing import Callable, Dict, List, Optional, Sequence

from utils.batch_versions import BatchVersionStore, VersionMeta
from utils.block_actions import page_data_needs_sync
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import TextBlock


class BatchWriteback:
    """按 D40 五步把批量编辑写回项目数据层与视觉层。"""

    def __init__(
        self,
        proj: ProjImgTrans,
        scene_manager=None,
        *,
        sync_block_data: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            proj: 项目实例（只改 ``pages`` / 页代数 / 脏页标记）。
            scene_manager: ``ui/scenetext_manager.py::SceneTextManager``，用于
                第 ④ 步重建视觉层；``None`` 时不重建（无 UI 的批处理场景）。
            sync_block_data: 第 ① 步的调用口（通常是主窗口的 ``_sync_block_data``）；
                ``None`` 时跳过前置对齐。
        """
        self.proj = proj
        self.scene_manager = scene_manager
        self.sync_block_data = sync_block_data

    def apply(
        self,
        page_edits: Dict[str, Sequence[TextBlock]],
        *,
        image_changed: bool = False,
        mark_dirty: bool = True,
    ) -> dict:
        """把 ``{页名: 新块列表}`` 按五步写回。

        Args:
            page_edits: 只写这些页；页面不在项目里则跳过（列在 ``skipped``）。
            image_changed: 本次是否改写了页面的图像缓冲（遮罩/修复图），
                为真时加 ``bump_page_image_generation``。
            mark_dirty: 是否把改过的页标为"结果图过期"（当前页由
                ``mark_page_needs_rerender`` 自身排除）。

        Returns:
            ``{"pages": [...], "skipped": [...], "synced": bool,
            "ui_rebuilt": bool, "verified": bool}``——``verified`` 即 D40 的
            验收判据，为假说明视觉层与数据层仍脱节，属缺陷。
        """
        report = {
            "pages": [],
            "skipped": [],
            "synced": False,
            "ui_rebuilt": False,
            "verified": False,
        }
        if page_edits:
            # ① 前置对齐（写回前让数据层追上当前页的面板编辑）
            if self.sync_block_data is not None:
                try:
                    self.sync_block_data()
                    report["synced"] = True
                except Exception as e:
                    LOGGER.error(f"Pre-writeback sync failed: {e}")

            # ② 只改数据层的块列表 ／ ③ 页代数
            for pagename, blk_list in page_edits.items():
                if pagename not in self.proj.pages:
                    report["skipped"].append(pagename)
                    continue
                self.proj.pages[pagename] = list(blk_list)
                self.proj.bump_page_generation(pagename)
                if image_changed:
                    self.proj.bump_page_image_generation(pagename)
                if mark_dirty:
                    self.proj.mark_page_needs_rerender(pagename)
                report["pages"].append(pagename)

            # ④ 当前页重建视觉层（数据 → UI）；非当前页切页时自然走这条路
            if (
                self.scene_manager is not None
                and self.proj.current_img in page_edits
            ):
                self.scene_manager.updateSceneTextitems()
                report["ui_rebuilt"] = True

        report["verified"] = self.verify()
        if not report["verified"]:
            LOGGER.error(
                "Batch writeback left data and scene out of sync "
                "(D40 acceptance judgment is True)"
            )
        return report

    def verify(self) -> bool:
        """D40 验收判据：数据层与视觉层未脱节（无视觉层时恒为真）。"""
        if self.scene_manager is None:
            return True
        try:
            return not page_data_needs_sync(
                self.proj.current_block_list(),
                self.scene_manager.textblk_item_list,
            )
        except Exception as e:
            LOGGER.error(f"Failed to verify writeback state: {e}")
            return False


class BatchOperation:
    """一次批量操作的事务外壳：落盘 → 写版本 → 写回 → 落盘。"""

    def __init__(
        self,
        proj: ProjImgTrans,
        scene_manager=None,
        *,
        commit: Optional[Callable[[], None]] = None,
        sync_block_data: Optional[Callable[[], None]] = None,
        store: Optional[BatchVersionStore] = None,
    ):
        """
        Args:
            commit: "把当前状态落盘"的调用口（主窗口传 ``_sync_and_commit_project``，
                它含 UI → 数据的同步）；``None`` 时退化为 ``proj.save()``。
            store: 备份版本仓库；``None`` 时按 ``proj`` 新建。
        """
        self.proj = proj
        self.commit = commit
        self.store = store or BatchVersionStore(proj)
        self.writer = BatchWriteback(
            proj, scene_manager, sync_block_data=sync_block_data
        )

    def _commit(self) -> None:
        if self.commit is not None:
            self.commit()
        elif self.proj.proj_path:
            self.proj.save()

    def available_steps(self) -> int:
        """当前可连续撤销的批量操作步数。"""
        return self.store.available_steps()

    def apply(
        self,
        label: str,
        page_edits: Dict[str, Sequence[TextBlock]],
        *,
        dirty_pages: Optional[Sequence[str]] = None,
        pixel_regions: Optional[Dict[str, Sequence]] = None,
        image_changed: bool = False,
        mark_dirty: bool = True,
    ) -> dict:
        """执行一次批量操作（写版本 → D40 写回 → 落盘）。

        Args:
            label: 任务名，进版本元信息（撤销提示会读它）。
            dirty_pages: 结果图会过期的页（缺省时取 ``page_edits`` 的键）。
            pixel_regions: ``{页名: [xyxy, ...]}``——本次会改像素的矩形，
                用于存前图（D35）；不改像素的任务传 ``None``。

        Returns:
            ``{"started": bool, "version": VersionMeta|None,
            "writeback": dict|None, "error": str|None}``；``started`` 为假表示
            备份版本没写成（磁盘不可写等），**本次批量操作应中止**并告知用户
            （否则失败后没有可回退的版本）。
        """
        report: dict = {"started": False, "version": None, "writeback": None,
                        "error": None}
        # 快照前落盘：版本取的是数据层的内存快照，须先让 UI 的编辑进数据层；
        # 这一步失败就中止——不落版本就写数据，失败后没有任何可回退的东西
        try:
            self._commit()
        except Exception as e:
            LOGGER.error(f"Batch commit before snapshot failed: {e}")
            report["error"] = "commit before snapshot failed"
            return report
        meta = self.store.begin(
            label,
            dirty_pages=list(dirty_pages) if dirty_pages is not None
            else list(page_edits.keys()),
            pixel_regions=pixel_regions,
        )
        if meta is None:
            report["error"] = "batch version not written"
            return report
        report["version"] = meta
        report["writeback"] = self.writer.apply(
            page_edits, image_changed=image_changed, mark_dirty=mark_dirty
        )
        try:
            self._commit()
        except Exception as e:
            # 数据已改、版本已在：撤销路径仍然有效，只记错误（调用方提示用户）
            LOGGER.error(f"Batch commit after writeback failed: {e}")
            report["error"] = "commit after writeback failed"
        report["started"] = True
        return report

    def begin_pixels(
        self,
        label: str,
        *,
        dirty_pages: Optional[Sequence[str]] = None,
        pixel_regions: Optional[Dict[str, Sequence]] = None,
    ) -> Optional[VersionMeta]:
        """像素类批量任务的第 1–2 步：落盘 → 写版本；失败返回 ``None``。

        与 ``apply`` 的差别：像素任务（简单背景修复）**不改块列表**，故不走
        D40 的五步写回，也不标脏（D27／§8：照搬管线路径）；调用方用
        ``finish`` 收尾，退回 ``undo_last`` 撤销。

        Args:
            dirty_pages: 结果图会过期的页；**像素任务按 D27 传空**（管线路径
                同样不标脏），缺省取空列表。
            pixel_regions: ``{页名: [[x1,y1,x2,y2], ...]}``——本次会改的像素
                矩形，用于存前图（D35）。
        """
        try:
            self._commit()
        except Exception as e:
            LOGGER.error(f"Batch commit before pixel snapshot failed: {e}")
            return None
        return self.store.begin(
            label,
            dirty_pages=list(dirty_pages or []),
            pixel_regions=pixel_regions,
        )

    def finish(self) -> bool:
        """像素类批量任务的落盘收尾；返回是否成功（失败时数据已在、版本已在）。"""
        try:
            self._commit()
            return True
        except Exception as e:
            LOGGER.error(f"Batch commit after pixel task failed: {e}")
            return False

    def undo_last(self, expect_seq: Optional[int] = None) -> List[str]:
        """撤销最近一次批量操作：取最新版本覆盖数据与像素并消耗它。

        返回需要重渲的页清单；调用方随后须做 UI 收尾（清撤销栈、重画布、
        重建场景、落盘、刷页列表、弹窗告知），先例见 ``ui/mainwindow.py``
        的 ``on_batch_rollback``。没有版本可撤时抛
        ``utils/exceptions.py::ProjectLoadFailureException``。

        Args:
            expect_seq: 期望撤销的版本号（``apply`` 里那次 ``begin`` 的
                返回值）。版本目录是共用池，若期间已有更晚的批量操作写
                新版，带上它即可让本调用**拒绝执行**而不是撤错对象。
        """
        return self.store.restore_latest(expect_seq=expect_seq)
