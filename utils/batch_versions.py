"""项目目录内的批量操作版本轮转备份（规划 D35）。

批量操作（工作台的任务：简单背景修复／合并相邻框／框扩张／删误框）不进
逐命令撤销栈，而是**执行前写一版、撤销时取最新一版覆盖并消耗**：

- 一版 ＝ 项目数据（``utils/proj_imgtrans.py::ProjImgTrans`` 的 ``to_dict``
  内存快照，故调用方须先让数据层追上 UI，见 ``ui/batch_ops.py``）＋ 该操作
  将要改动页面的**像素前图**（只存矩形区域，D35 的「只存前图」）。
- 版本放**项目目录内子目录**（``.bt_batch_backup/``，随项目一起搬走，
  也不会被 ``utils/io_utils.py::find_all_imgs`` 当成页面——它只列目录里
  带图片扩展名的文件）。
- 版本数取 ``utils/config.py::ProgramConfig`` 的 ``batch_backup_versions``
  （默认 1、上限 5）：每次 ``begin`` 写新版、超出即删最旧；``restore_latest``
  覆盖后**消耗**该版本（同一步不可重复撤销，N=1 即「只能撤销最近一次」）。
- **版本跨会话有效**：文件就在项目目录里（D35「随项目走」），重开项目仍可
  撤销上次会话的批量操作。撤销会丢弃其后的一切改动，故该动作须由调用方
  弹窗告知（D27）。
- **本机制是仓库里唯一的批量备份口**（2026-09-16 归并）：查找替换
  （``ui/global_search_widget.py::GlobalSearchWidget``）与工作台批量任务
  共用它——替换只改文本/样式，故 ``pixel_regions`` 传空（不产像素前图），
  其余（版本、脏页清单、LIFO、消耗语义）完全一致。原先
  ``utils/proj_imgtrans.py::ProjImgTrans`` 的单槽会话备份（``*.batch_backup.json``）
  已删除。
- 调用方若需「撤销的必须是我刚写的那一版」，把 ``begin`` 返回的
  ``VersionMeta.seq`` 交给 ``restore_latest`` / ``discard_latest`` 的
  ``expect_seq`` 校验（查找替换的回滚条据此判断自己的版本是否已被
  更晚的批量操作顶掉）。

用户在设置面板改「备份版本数」即时生效（每次 ``begin`` 现取）。
"""

import json
import os
import os.path as osp
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
from PIL import Image

from .config import pcfg
from .exceptions import ProjectLoadFailureException
from .io_utils import imread
from .logger import logger as LOGGER
from .proj_imgtrans import (
    ProjImgTrans,
    TextBlkEncoder,
    get_last_modified_file,
)

BACKUP_DIR_NAME = ".bt_batch_backup"
META_NAME = "meta.json"
PROJ_NAME = "proj.json"
PIXEL_DIR_NAME = "px"

MIN_VERSIONS = 1
MAX_VERSIONS = 5
DEFAULT_VERSIONS = 1


@dataclass
class VersionMeta:
    """一版的元信息（``meta.json`` 的读写载体）。"""

    seq: int
    label: str
    created: float
    dirty_pages: List[str] = field(default_factory=list)
    pixels: Dict[str, dict] = field(default_factory=dict)
    dir: str = ""

    @property
    def pixel_pages(self) -> List[str]:
        return list(self.pixels.keys())

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "seq": self.seq,
            "label": self.label,
            "created": self.created,
            "dirty_pages": list(self.dirty_pages),
            "pixels": self.pixels,
        }

    @classmethod
    def from_dict(cls, d: dict, vdir: str) -> "VersionMeta":
        return cls(
            seq=int(d.get("seq", 0)),
            label=str(d.get("label", "")),
            created=float(d.get("created", 0.0)),
            dirty_pages=list(d.get("dirty_pages") or []),
            pixels=dict(d.get("pixels") or {}),
            dir=vdir,
        )


def version_limit() -> int:
    """当前设置下的版本数上限（夹在 1–5，防手工改配置写进越界值）。"""
    try:
        n = int(getattr(pcfg, "batch_backup_versions", DEFAULT_VERSIONS))
    except (TypeError, ValueError):
        n = DEFAULT_VERSIONS
    return max(MIN_VERSIONS, min(MAX_VERSIONS, n))


def _exact_inpainted_path(proj: ProjImgTrans, pagename: str) -> str:
    """该页修复图的**精确**路径（不走 ``ProjImgTrans.get_inpainted_path``
    的按页序模糊兜底——那会把别页的修复图当成这一页的，撤销时删错文件）。"""
    prefix = osp.join(proj.inpainted_dir(), osp.splitext(pagename)[0])
    return get_last_modified_file(
        prefix, [".jxl", ".png"], ext_fallback=pcfg.intermediate_imgsave_ext
    )


def _normalize_rects(rects: Sequence, w: int, h: int) -> List[List[int]]:
    """矩形统一成 ``[x1,y1,x2,y2]`` 并夹进图像范围；退化矩形直接丢。"""
    out: List[List[int]] = []
    for r in rects or []:
        try:
            x1, y1, x2, y2 = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        except (TypeError, ValueError, IndexError):
            continue
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        out.append([x1, y1, x2, y2])
    return out


def _stack_crops(crops: Sequence[np.ndarray]) -> np.ndarray:
    """把一页的各矩形裁片竖着拼成一条带（还原时按 ``rects`` 顺序取回）。

    **各裁片宽度不等**（这正是「简单背景修复」的常态：一页有好几条不同宽的
    纯色带）时不能直接 ``np.concatenate``——它会抛
    "all the input array dimensions except for the concatenation axis must
    match exactly"（2026-09-18 的 `Failed to write batch version` 就是这么来的，
    整批操作因此中止）。故窄的裁片**左侧对齐、右边补零**到最大宽度：
    还原侧逐条取 ``strip[y:y+h, :w]``，多出来的右边距是死区、不会写回图像。
    """
    width = max(crop.shape[1] for crop in crops)
    rows = []
    for crop in crops:
        if crop.shape[1] < width:
            pad = [(0, 0), (0, width - crop.shape[1])] + [(0, 0)] * (crop.ndim - 2)
            crop = np.pad(crop, pad, mode="constant")
        rows.append(crop)
    return np.concatenate(rows, axis=0)


def _match_channels(crop: np.ndarray, base: np.ndarray) -> np.ndarray:
    """裁片与底图通道数对齐（修复图可能带 alpha，来源图不带）。"""
    if crop.ndim == 2 and base.ndim == 3:
        crop = np.repeat(crop[:, :, None], base.shape[2], axis=2)
    elif crop.ndim == 3 and base.ndim == 2:
        crop = crop[:, :, 0]
    elif crop.ndim == 3 and base.ndim == 3 and crop.shape[2] != base.shape[2]:
        if crop.shape[2] == 4 and base.shape[2] == 3:
            crop = crop[:, :, :3]
        elif crop.shape[2] == 3 and base.shape[2] == 4:
            crop = np.concatenate(
                [crop, np.full(crop.shape[:2] + (1,), 255, np.uint8)], axis=2
            )
    return crop


class BatchVersionStore:
    """批量操作的版本轮转备份（把一版写进项目目录、按 LIFO 撤销）。"""

    def __init__(self, proj: ProjImgTrans):
        self.proj = proj

    # ── 位置 ────────────────────────────────────────────────────────

    def root_dir(self) -> Optional[str]:
        if not self.proj or not self.proj.directory:
            return None
        return osp.join(self.proj.directory, BACKUP_DIR_NAME)

    def _version_dir(self, seq: int) -> str:
        return osp.join(self.root_dir(), f"v{int(seq):05d}")

    def _usable(self) -> bool:
        """备份目录可写的前提：项目有目录且数据层是完整项目。"""
        root = self.root_dir()
        if not root or not osp.isdir(self.proj.directory):
            return False
        return bool(self.proj.pages)

    # ── 查询 ────────────────────────────────────────────────────────

    def list_versions(self) -> List[VersionMeta]:
        """磁盘上全部有效版本（按 seq 升序；缺 ``meta.json`` 的半成品不算）。"""
        root = self.root_dir()
        if not root or not osp.isdir(root):
            return []
        out: List[VersionMeta] = []
        for name in sorted(os.listdir(root)):
            vdir = osp.join(root, name)
            meta_path = osp.join(vdir, META_NAME)
            if not osp.isdir(vdir) or not osp.exists(meta_path):
                continue
            try:
                with open(meta_path, "r", encoding="utf8") as f:
                    out.append(VersionMeta.from_dict(json.load(f), vdir))
            except Exception as e:
                LOGGER.warning(f"Ignoring unreadable batch version {vdir}: {e}")
        out.sort(key=lambda m: m.seq)
        return out

    def available_steps(self) -> int:
        """可连续撤销的批量操作步数（＝现存版本数）。"""
        return len(self.list_versions())

    def latest(self) -> Optional[VersionMeta]:
        versions = self.list_versions()
        return versions[-1] if versions else None

    # ── 写版本 ──────────────────────────────────────────────────────

    def begin(
        self,
        label: str,
        *,
        dirty_pages: Optional[Sequence[str]] = None,
        pixel_regions: Optional[Dict[str, Sequence]] = None,
    ) -> Optional[VersionMeta]:
        """批量操作**执行前**调用：把当前项目状态写成一个新版本。

        Args:
            label: 任务名（设置面板与未来工作台的「可撤销」提示都读它）。
            dirty_pages: 操作后结果图会过期的页（回滚时据此重标脏）。
            pixel_regions: ``{页名: [[x1,y1,x2,y2], ...]}``——本次会改动像素的
                矩形；只存这些矩形的前图。页面此时**没有**修复图时只登记
                ``existed=False``（不占磁盘），撤销即删除该文件。

        Returns:
            写入的版本元信息；项目不可快照（无目录/无页面）或写入失败时
            返回 ``None``（调用方据此中止本次批量操作并告知用户）。
        """
        if not self._usable():
            LOGGER.error("Batch version store unusable (no project dir or pages)")
            return None
        root = self.root_dir()
        versions = self.list_versions()
        seq = versions[-1].seq + 1 if versions else 1
        vdir = self._version_dir(seq)
        try:
            os.makedirs(vdir, exist_ok=True)
            with open(osp.join(vdir, PROJ_NAME), "w", encoding="utf8") as f:
                f.write(
                    json.dumps(
                        self.proj.to_dict(),
                        ensure_ascii=False,
                        cls=TextBlkEncoder,
                    )
                )
            pixels = self._capture_pixels(vdir, pixel_regions or {})
            meta = VersionMeta(
                seq=seq,
                label=str(label),
                created=time.time(),
                dirty_pages=list(dirty_pages or []),
                pixels=pixels,
                dir=vdir,
            )
            # meta.json 最后写：它存在即视为版本有效（半成品会被忽略）
            with open(osp.join(vdir, META_NAME), "w", encoding="utf8") as f:
                json.dump(meta.to_dict(), f, ensure_ascii=False)
        except Exception as e:
            LOGGER.error(f"Failed to write batch version: {e}")
            shutil.rmtree(vdir, ignore_errors=True)
            return None
        self._rotate()
        return meta

    def _capture_pixels(self, vdir: str, pixel_regions: Dict[str, Sequence]) -> dict:
        """按页存「前图」：受影响矩形的裁片拼成一条竖带（PNG，无损）。"""
        pixels: Dict[str, dict] = {}
        if not pixel_regions:
            return pixels
        px_dir = osp.join(vdir, PIXEL_DIR_NAME)
        for i, (pagename, rects) in enumerate(pixel_regions.items()):
            src_path = _exact_inpainted_path(self.proj, pagename)
            if not src_path or not osp.exists(src_path):
                # 操作前没有修复图：撤销＝把操作期间生成的这张图删掉
                pixels[pagename] = {"file": src_path, "existed": False}
                continue
            try:
                img = imread(src_path)
            except Exception as e:
                LOGGER.warning(f"Failed to read inpainted image {src_path}: {e}")
                img = None
            if img is None:
                # 修复图存在但读不出来：不记裁剪（还原时不动这个文件，避免
                # 把用户既有的修复成果删掉）
                pixels[pagename] = {"file": src_path, "existed": True, "rects": []}
                continue
            h, w = img.shape[:2]
            norm = _normalize_rects(rects, w, h)
            if not norm:
                pixels[pagename] = {"file": src_path, "existed": True, "rects": []}
                continue
            strip = _stack_crops(
                [img[y1:y2, x1:x2] for x1, y1, x2, y2 in norm]
            )
            os.makedirs(px_dir, exist_ok=True)
            rel = f"{PIXEL_DIR_NAME}/{i:04d}.png"
            Image.fromarray(strip.astype(np.uint8, copy=False)).save(
                osp.join(vdir, rel)
            )
            pixels[pagename] = {
                "file": src_path,
                "existed": True,
                "rects": norm,
                "strip": rel,
            }
        return pixels

    def _rotate(self) -> None:
        """超出设置版本数时删最旧（版本目录整目录删）。"""
        limit = version_limit()
        versions = self.list_versions()
        for meta in versions[:-limit]:
            shutil.rmtree(meta.dir, ignore_errors=True)

    # ── 撤销（LIFO）────────────────────────────────────────────────

    def restore_latest(self, expect_seq: Optional[int] = None) -> List[str]:
        """用最新版本覆盖当前数据与像素，返回需要重渲的页，然后消耗该版本。

        保持用户当前所在页不跳转；成功后消费版本；版本缺失、损坏或
        ``expect_seq`` 与最新一版不符时抛
        ``utils/exceptions.py::ProjectLoadFailureException`` 且**不动**版本目录。

        Args:
            expect_seq: 期望撤销的版本号（``begin`` 的返回值）。不符即拒绝，
                避免撤销掉此后新写的另一批操作——调用方据此提示用户
                「该操作之后还有更晚的批量操作」。
        """
        meta = self.latest()
        if meta is None:
            raise ProjectLoadFailureException("No batch version available")
        if expect_seq is not None and meta.seq != expect_seq:
            raise ProjectLoadFailureException(
                f"Latest batch version is v{meta.seq}, expected v{expect_seq}"
            )
        try:
            with open(osp.join(meta.dir, PROJ_NAME), "r", encoding="utf8") as f:
                payload = json.load(f)
        except Exception as e:
            raise ProjectLoadFailureException(e)

        orig_page = self.proj.current_img
        self.proj.load_from_dict(payload)
        if orig_page and orig_page in self.proj.pages:
            self.proj.set_current_img(orig_page)

        pixel_pages = self._restore_pixels(meta)
        if (
            pixel_pages
            and self.proj.current_img in pixel_pages
            and self.proj.current_img in self.proj.pages
        ):
            # 当前页像素被换回 → 内存缓冲必须重载，否则画布显示的还是撤销前
            self.proj.set_current_img(self.proj.current_img)

        for pagename in list(meta.dirty_pages) + pixel_pages:
            self.proj.mark_page_needs_rerender(pagename)
        dirty = [
            p for p in (list(meta.dirty_pages) + pixel_pages)
            if self.proj.page_needs_rerender(p)
        ]

        shutil.rmtree(meta.dir, ignore_errors=True)
        return dirty

    def _restore_pixels(self, meta: VersionMeta) -> List[str]:
        """把版本里的像素前图贴回磁盘；返回实际动过的页。"""
        done: List[str] = []
        for pagename, rec in (meta.pixels or {}).items():
            target = rec.get("file")
            rects = rec.get("rects") or []
            if not target:
                continue
            try:
                if not rec.get("existed", True):
                    # 操作前没有修复图：删掉操作期间生成的那张即还原
                    if osp.exists(target):
                        os.remove(target)
                    done.append(pagename)
                    continue
                if not rects:
                    continue
                base = imread(target) if osp.exists(target) else None
                if base is None:
                    base = self.proj.read_img(pagename)
                strip_path = osp.join(meta.dir, rec.get("strip") or "")
                if base is None or not osp.exists(strip_path):
                    LOGGER.warning(
                        f"Skipping pixel restore for {pagename}: no base image"
                    )
                    continue
                with Image.open(strip_path) as im:
                    strip = np.array(im.convert("RGB"))
                y = 0
                for x1, y1, x2, y2 in rects:
                    h, w = y2 - y1, x2 - x1
                    crop = _match_channels(strip[y : y + h, :w], base)
                    base[y1:y2, x1:x2] = crop
                    y += h
                try:
                    Image.fromarray(base.astype(np.uint8, copy=False)).save(target)
                except Exception:
                    # 修复图可能是 PIL 写不了的后缀（如 .jxl）：换 PNG 落盘，
                    # 应用侧的 get_inpainted_path(get_last_modified=True) 会
                    # 取到这张更新的 PNG
                    alt = osp.splitext(target)[0] + ".png"
                    Image.fromarray(base.astype(np.uint8, copy=False)).save(alt)
                    LOGGER.warning(f"Could not write {target}; wrote {alt} instead")
                done.append(pagename)
            except Exception as e:
                LOGGER.error(f"Failed to restore pixels for {pagename}: {e}")
        return done

    def discard_latest(self, expect_seq: Optional[int] = None) -> bool:
        """丢弃最新一版、**不换数据**：给「已写版本、但最终没有改动」的路径用。

        查找替换先写版本、再收集改动；收集结果为空（空替换）时那版只镜像
        当前状态、留着会污染「可撤销步数」与后来的回滚，故就地丢弃。

        Args:
            expect_seq: 期望丢弃的版本号；不符或没有版本时什么都不动
                （防止丢掉别的操作写的版本）。返回是否真的丢弃了。
        """
        meta = self.latest()
        if meta is None:
            return False
        if expect_seq is not None and meta.seq != expect_seq:
            return False
        shutil.rmtree(meta.dir, ignore_errors=True)
        return True

    def clear(self) -> None:
        """清空全部版本（项目关闭/换目录时的清理口）。"""
        root = self.root_dir()
        if root and osp.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
