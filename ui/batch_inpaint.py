"""批量「简单背景」纯色修复（规划 D3／D34）。

用户场景（设计 §2 的「清理原图嵌字」）：修复管线不可控，复杂背景一律手工处理，
而「简单背景」（气泡内近乎纯色）用纯色覆盖就够——走模型反而可能把气泡边框
一并抹掉。本模块把这条判据跑遍全书页：**简单块纯色覆盖、复杂块完全不动**
（不调修复模型），只写 ``inpainted/`` 层。

四条口径：

- **判据一律算原图**（D34）：判定取页面原图，不取可能已被模型修过或手工改
  过的 ``inpainted/``——否则手工成果会被判成"简单"而遭纯色覆盖。判据是
  ``modules/inpaint/base.py::classify_simple``（与管线逐块路径同一个函数，
  单点维护），覆盖执行走 ``modules/inpaint/base.py::InpainterBase`` 的
  ``inpaint`` 加 ``only_simple=True``（该模式不加载模型）。
- **覆盖落在既有修复图上**：页面已有修复图时，只把"本次被覆盖的像素"贴到
  那张图上（按矩形逐像素比对得出），复杂块既有的修复成果不丢；没有修复图
  时结果就是「原图 + 简单块被覆盖」。
- **不标脏**（D27／设计 §14）：与管线路径一致——``ui/module_manager.py``
  的修复阶段是 ``inpaint`` → ``save_inpainted`` → ``bump_page_image_generation``，
  全程不调 ``utils/proj_imgtrans.py::ProjImgTrans`` 的 ``mark_page_needs_rerender``；
  跑完只**重载当前页**显示，其余页照旧惰性。
- **整批一条撤回**（D21／D35）：执行前经 ``ui/batch_ops.py::BatchOperation``
  写一版（项目数据 + 受影响矩形的前图），撤销＝取最新一版覆盖并消耗。
- **取消即回滚**（2026-09-16 定）：中途取消时把本次已涂的页一并撤回
  （``apply`` 的 ``stopped`` / ``rolled_back``），取消不留半成品；因此 D27 的
  告知弹窗必须写明"中途取消会一并撤回本次已处理的部分"。

典型用法（工作台侧；D27 的告知弹窗读 ``plan`` 的页数／块数）::

    task = BatchSimpleInpaint(proj, inpainter, op=op)
    plan = task.plan()                  # 只读：N 页 / M 块
    report = task.apply()
    op.undo_last(expect_seq=report["version"].seq)   # 整批撤回

代价（已知并接受，D34）：``plan`` 与 ``apply`` 各自把原图读一遍（判据要像素），
故两遍 I/O；``apply`` 内部**重新扫描**而不复用外部 ``plan``，因为两次之间
用户可能改过块数据，前图矩形必须与真正要动的那份计划一致。
"""

import os.path as osp
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from modules.inpaint.base import InpainterBase, classify_simple
from utils.io_utils import imread
from utils.imgproc_utils import enlarge_window
from utils.logger import logger as LOGGER
from utils.proj_imgtrans import ProjImgTrans

from .batch_ops import BatchOperation

# 进版本元信息的任务名；D35 的撤销提示会读它。工作台接线时应传一个已翻译的
# 标签覆盖它（先例见 ui/mainwindow.py 的批量替换入口——它传的是 tr 的结果）。
# 注意：本文件别写出「tr 调用」的字面形态（含注释），i18n 检查器不剥注释，
# 会把注释里的调用当成真实条目去 ts 里找。
DEFAULT_LABEL = "Simple background fill"

# plan() 的跳过原因码（不含用户可见文案，工作台自行翻译）
SKIP_NO_BLOCKS = "no-blocks"
SKIP_NO_MASK = "no-mask"
SKIP_NO_IMAGE = "no-image"
SKIP_MASK_MISMATCH = "mask-size-mismatch"
SKIP_NO_SIMPLE = "no-simple-blocks"


class BatchSimpleInpaint:
    """把「简单背景」判据跑遍全书页并纯色覆盖（只写 ``inpainted/`` 层）。"""

    def __init__(
        self,
        proj: ProjImgTrans,
        inpainter: InpainterBase,
        *,
        op: Optional[BatchOperation] = None,
        reload_current: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            proj: 项目实例。
            inpainter: 当前修复模块实例（只用其逐块路径与判据；本任务**不
                加载模型**）。``ui/module_manager.py`` 的 ``inpainter`` 属性
                即取其来源；模块不支持逐块（如 ``none``）时 ``apply`` 拒绝执行。
            op: 批量事务外壳（版本 + 落盘）；缺省按 ``proj`` 新建一个
                （没有 UI 收尾的裸用法）。工作台应传带 ``commit``／
                ``sync_block_data`` 的实例，好让版本快照反映当前面板编辑。
            reload_current: 跑完重载当前页的额外收尾（画布刷新等）；缺省只
                调 ``utils/proj_imgtrans.py::ProjImgTrans`` 的
                ``set_current_img`` 把内存缓冲换成新图。
            on_progress: ``(已完成页数, 总页数, 页名)``，供进度 UI。
            should_stop: 页间检查的取消口；返回 True 即**取消并回滚**——已
                覆盖的页由版本仓库撤回原样（报告里 ``rolled_back`` 为真），
                取消等价于"这次没发生过"。
        """
        self.proj = proj
        self.inpainter = inpainter
        self.op = op or BatchOperation(proj)
        self.reload_current = reload_current
        self.on_progress = on_progress
        self.should_stop = should_stop

    # ── 扫描（只读）─────────────────────────────────────────────────

    def plan(self, pages: Optional[Sequence[str]] = None) -> dict:
        """只读扫描：算出会被纯色覆盖的页与块（供 D27 告知与进度预估）。

        只读——不写任何文件、不改内存数据。

        Args:
            pages: 要扫的页；缺省全书页（设计 §13：批量是程序遍历全书页）。

        Returns:
            ``{"pages": {页名: {"simple", "complex", "unknown", "rects"}},
            "simple", "complex", "unknown", "page_count", "skipped"}``：

            - ``pages``／``page_count``：**会被处理的页**与其明细；``rects``
              是会被覆盖的外扩矩形（即版本仓库要存前图的范围）。一个简单块
              都没有的页不进 ``pages``（它没有可覆盖的东西）；
            - ``simple``：将被覆盖的块数（＝各处理页 ``rects`` 之和）；
            - ``complex``／``unknown``：**全书**（含不处理的页）判为复杂／判
              不出来的块数——D27 弹窗要能说清"其余多少块保持原样"，故这两个
              数不按处理页截断；
            - ``skipped``：``{页名: 原因码}``，取值见模块常量 ``SKIP_*``。
        """
        pages_report: Dict[str, dict] = {}
        skipped: Dict[str, str] = {}
        totals = {"simple": 0, "complex": 0, "unknown": 0}
        for pagename in self._iter_pages(pages):
            entry, reason = self._scan_page(pagename)
            if entry is None:
                skipped[pagename] = reason
                continue
            for kind in totals:
                totals[kind] += entry[kind]
            if reason is not None:
                skipped[pagename] = reason
            else:
                pages_report[pagename] = entry
        return {
            "pages": pages_report,
            "simple": totals["simple"],
            "complex": totals["complex"],
            "unknown": totals["unknown"],
            "page_count": len(pages_report),
            "skipped": skipped,
        }

    def _iter_pages(self, pages: Optional[Sequence[str]]) -> List[str]:
        names = list(pages) if pages is not None else list(self.proj.pages)
        return [p for p in names if p in self.proj.pages]

    def _scan_page(self, pagename: str):
        """扫一页：返回 ``(计划项, None)`` 或 ``(None, 跳过原因码)``。"""
        blk_list = self.proj.pages.get(pagename) or []
        if not blk_list:
            return None, SKIP_NO_BLOCKS
        mask = self.proj.load_mask_by_imgname(pagename)
        if mask is None:
            return None, SKIP_NO_MASK
        img = self._read_src(pagename)
        if img is None:
            return None, SKIP_NO_IMAGE
        if mask.shape[:2] != img.shape[:2]:
            return None, SKIP_MASK_MISMATCH

        im_h, im_w = img.shape[:2]
        entry = {"simple": 0, "complex": 0, "unknown": 0, "rects": []}
        for blk in blk_list:
            x1, y1, x2, y2 = enlarge_window(blk.xyxy, im_w, im_h, ratio=1.7)
            im = img[y1:y2, x1:x2]
            msk = mask[y1:y2, x1:x2]
            if im.size == 0 or msk.size == 0:
                entry["unknown"] += 1
                continue
            need_inpaint, ballon_msk, _bg = classify_simple(
                im,
                msk,
                # 本块 xyxy 在裁剪区坐标下（块局部遮罩，D45）：窗口是 1.7 倍
                # 外扩，邻块遮罩落进来会把包围盒撑大 → 围不出气泡 → 判不出
                (blk.xyxy[0] - x1, blk.xyxy[1] - y1, blk.xyxy[2] - x1, blk.xyxy[3] - y1),
            )
            if ballon_msk is None:
                # 判不出来（裁剪区内无文本像素／围不出气泡）：不动它
                entry["unknown"] += 1
            elif need_inpaint:
                entry["complex"] += 1
            else:
                entry["simple"] += 1
                entry["rects"].append([int(x1), int(y1), int(x2), int(y2)])
        if not entry["rects"]:
            # 有判据结果但没有可覆盖的块 → 整页不处理（计数仍进 plan 的总数）
            return entry, SKIP_NO_SIMPLE
        return entry, None

    # ── 执行 ────────────────────────────────────────────────────────

    def apply(
        self, pages: Optional[Sequence[str]] = None, *, label: Optional[str] = None
    ) -> dict:
        """执行批量纯色覆盖（写版本 → 逐页覆盖 → 重载当前页 → 落盘）。

        Args:
            pages: 要处理的页；缺省全书页。
            label: 进版本元信息的任务名；缺省 ``DEFAULT_LABEL``。

        **取消即回滚**（2026-09-16 定）：``should_stop`` 触发时本次已涂的页与版本
        一并撤回，取消等价于"这次没发生过"；D27 的告知弹窗须写明这一点。

        Returns:
            ``{"started", "error", "version", "pages", "blocks", "errors",
            "skipped", "plan", "committed", "stopped", "rolled_back",
            "rolled_back_pages", "reloaded"}``：

            - ``started`` 为假表示**没有留下任何改动**（没有可覆盖的块、版本没
              写成、或中途取消后已回滚）——``error`` 带原因码
              ``version-not-written`` / ``inpainter-not-block-capable``；
            - ``pages`` 是实际留下改动的页、``blocks`` 是覆盖的块数；
            - ``errors`` 是 ``{页名: 错误文本}``（单页失败不中断整批）；
            - ``stopped`` 为真＝用户取消；``rolled_back`` 为真表示已把本次改动
              撤回，``rolled_back_pages`` 是撤回前已写过的页（供提示"已取消并
              回滚 N 页"）；
            - ``committed`` 为假表示收尾落盘失败（数据已改、版本已在，撤销仍有效）。
        """
        plan = self.plan(pages)
        report: dict = {
            "started": False,
            "error": None,
            "version": None,
            "pages": [],
            "blocks": 0,
            "errors": {},
            "skipped": plan["skipped"],
            "plan": plan,
            "committed": False,
            "stopped": False,
            "rolled_back": False,
            "rolled_back_pages": [],
            "reloaded": False,
        }
        if not plan["pages"]:
            return report
        if self.inpainter is None or not getattr(
            self.inpainter, "inpaint_by_block", False
        ):
            # 判据与覆盖都写在逐块路径里（如 none 模块是整图语义），
            # 这里明确拒绝而不是静默什么都不做
            report["error"] = "inpainter-not-block-capable"
            return report

        meta = self.op.begin_pixels(
            label or DEFAULT_LABEL,
            dirty_pages=[],  # D27／§8：不标脏，照搬管线路径
            pixel_regions={p: e["rects"] for p, e in plan["pages"].items()},
        )
        if meta is None:
            report["error"] = "version-not-written"
            return report
        report["version"] = meta
        report["started"] = True

        names = list(plan["pages"])
        stopped = False
        for i, pagename in enumerate(names):
            if self.should_stop is not None and self.should_stop():
                stopped = True
                break
            try:
                filled = self._fill_page(pagename, plan["pages"][pagename]["rects"])
            except Exception as e:
                LOGGER.error(f"Batch simple inpaint failed on {pagename}: {e}")
                report["errors"][pagename] = str(e)
                filled = 0
            if filled:
                report["pages"].append(pagename)
                report["blocks"] += filled
            if self.on_progress is not None:
                self.on_progress(i + 1, len(names), pagename)

        if stopped:
            # 取消即回滚：把本次已涂的页撤回原样，取消等价于"这次没发生过"
            report["stopped"] = True
            report["started"] = False
            report["version"] = None
            if report["pages"] or report["errors"]:
                report["rolled_back_pages"] = list(report["pages"])
                # 版本必须还是刚写的那一版：中途出现更晚的批量操作时宁可不撤
                self.op.undo_last(expect_seq=meta.seq)
                report["rolled_back"] = True
                self._reload_current(names)
            else:
                # 一页都没动过（在第一页之前就取消）：刚写的版本只镜像当前
                # 状态，留着会污染「可撤销步数」，就地丢弃（与空替换同一处置）
                self.op.store.discard_latest(expect_seq=meta.seq)
            report["pages"] = []
            report["blocks"] = 0
            report["committed"] = self.op.finish()
            return report

        if not report["pages"] and not report["errors"]:
            # plan 里有页却一个像素都没写成、也没报错（理论上不该发生）：
            # 同样丢弃那版，避免留下一个"什么都没变"的版本
            self.op.store.discard_latest(expect_seq=meta.seq)
            report["version"] = None
            report["started"] = False
            report["error"] = "no-pages-processed"
            return report

        report["reloaded"] = self._reload_current(report["pages"])
        report["committed"] = self.op.finish()
        return report

    def _fill_page(self, pagename: str, rects: Sequence) -> int:
        """覆盖一页的简单块；返回覆盖的块数（0 表示这页没动）。"""
        blk_list = self.proj.pages[pagename]
        mask = self.proj.load_mask_by_imgname(pagename)
        img = self._read_src(pagename)
        if img is None or mask is None or mask.shape[:2] != img.shape[:2]:
            return 0
        # 既有修复图要先读（下面就要覆盖这个文件）
        base = self.proj.load_inpainted_by_imgname(pagename)
        filled = self.inpainter.inpaint(img, mask, blk_list, only_simple=True)
        if base is not None and base.shape == filled.shape:
            filled = self._transfer_covered(base, img, filled, rects)
        self.proj.save_inpainted(pagename, filled)
        self.proj.bump_page_image_generation(pagename)
        return len(rects)

    @staticmethod
    def _transfer_covered(base: np.ndarray, ref: np.ndarray, filled: np.ndarray, rects):
        """把 ``filled`` 相对 ``ref`` 被改动的像素贴到 ``base`` 上（矩形外不动）。

        ``filled`` 只在被覆盖的像素上与 ``ref`` 不同（``only_simple`` 不跑
        模型），故逐像素比对即得覆盖掩码，无需再算一次气泡掩码。
        """
        h, w = base.shape[:2]
        for x1, y1, x2, y2 in rects:
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            new = filled[y1:y2, x1:x2]
            old = ref[y1:y2, x1:x2]
            target = base[y1:y2, x1:x2]
            if new.shape != old.shape or target.shape != new.shape:
                continue
            changed = np.any(new != old, axis=-1) if new.ndim == 3 else new != old
            if changed.any():
                target[changed] = new[changed]
        return base

    def _reload_current(self, pages: Sequence[str]) -> bool:
        """重载当前页显示（D34）；当前页不在本次范围内则不动。"""
        cur = self.proj.current_img
        if not cur or cur not in pages:
            return False
        if self.reload_current is not None:
            self.reload_current(cur)
        else:
            self.proj.set_current_img(cur)
        return True

    # ── 工具 ────────────────────────────────────────────────────────

    def _read_src(self, pagename: str) -> Optional[np.ndarray]:
        """读页面**原图**（D34 的判据来源）。

        ``utils/io_utils.py::imread`` 对不存在的路径返回 ``None``（随后
        ``.shape`` 会抛 AttributeError），故这里显式兜底。
        """
        if not self.proj.directory:
            return None
        return imread(osp.join(self.proj.directory, pagename))
