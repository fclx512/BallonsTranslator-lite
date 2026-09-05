import json
import os
import os.path as osp
import re
from typing import Dict, List, Set

import cv2
import numpy as np

from . import shared
from .config import RunStatus, pcfg
from .exceptions import (
    ImgnameNotInProjectException,
    ProjectDirNotExistException,
    ProjectLoadFailureException,
    ProjectNotSupportedException,
)
from .io_utils import NumpyEncoder, find_all_imgs, imread, imwrite
from .logger import logger as LOGGER
from .base_styles import BaseStyle, ensure_default_base_styles
from .textblock import FontFormat, TextBlock


def get_last_modified_file(file_prefix, exts, ext_fallback=None):
    """
    get last modified file from files sharing same prefix
    """
    latest_time = -1
    latest_f = None
    for ext in exts:
        tmp_p = file_prefix + ext
        if osp.exists(tmp_p) and osp.getmtime(tmp_p) > latest_time:
            latest_time = osp.getmtime(tmp_p)
            latest_f = tmp_p
    if latest_f is None:
        if ext_fallback is not None:
            latest_f = file_prefix + ext_fallback
        else:
            latest_f = file_prefix + exts[0]
    return latest_f


page_start_pattern = re.compile(r"^###\s+", re.MULTILINE)
text_blkid_start_pattern = re.compile(r"^\d+\.", re.MULTILINE)


def parse_txt_translation(file_path: str):
    with open(file_path, "r", encoding="utf8") as f:
        content = f.read()
    page_start = None
    page_list = []
    for matched in page_start_pattern.finditer(content):
        start, end = matched.span()
        if page_start is not None:
            page_list.append({"page_content": content[page_start:start]})
        page_start = start
    if page_start is not None:
        page_list.append({"page_content": content[page_start:]})

    for page_dict in page_list:
        page_content = page_dict["page_content"]
        page_dict["page_name"] = page_start_pattern.sub(
            "", page_content.split("\n")[0]
        ).strip()
        blkid_start = blkid_end = None
        blk_list = []
        for matched in text_blkid_start_pattern.finditer(page_content):
            start, end = matched.span()
            if blkid_start is not None:
                blk_list.append(page_content[blkid_end:start].strip())
            blkid_start = start
            blkid_end = end
        if blkid_start is not None:
            blk_list.append(page_content[blkid_end:].strip())
        page_dict["blk_list"] = blk_list

    return page_list


class TextBlkEncoder(NumpyEncoder):
    def default(self, obj):
        if isinstance(obj, TextBlock):
            return obj.to_dict()
        elif isinstance(obj, FontFormat):
            return obj.to_serializable_dict()
        return NumpyEncoder.default(self, obj)


class ProjImgTrans:
    def __init__(self, directory: str = None):
        self.type = "imgtrans"
        self.directory: str = None
        self.pages: Dict[str, List[TextBlock]] = {}
        self._pagename2idx = {}
        self._idx2pagename = {}
        self._image_info = {}

        self._fuzzy_inpainted_list = None

        # Pages whose TextBlock data has been modified by batch ops
        # (font-style manager, global replace, txt import) but whose
        # result image has not yet been re-rendered.
        self._pages_needing_rerender: Set[str] = set()

        # Session validity flag for the single-slot batch backup; the file
        # may survive on disk across sessions but is not offerable then.
        self._batch_backup_valid = False

        # 页代数计数（撤销体系阶段 4 跨页历史的页屏障）：检测管线等栈外
        # 写入整体换新 blk_list 时对应页 +1；撤销命令入栈时捕获当时的
        # 代数值，执行期不一致即判定为僵尸命令（所属页历史已过期）。
        # 纯会话内属性，不持久化。
        self._page_generations: Dict[str, int] = {}

        self.not_found_pages: Dict[str, List[TextBlock]] = {}
        self.new_pages: List[str] = []
        self.proj_path: str = None
        # Project-level base styles (identity: font_family + vertical).
        self.base_styles: List[BaseStyle] = []

        # Project-level story synopsis (upstream vision_context key); the
        # glossary workbench "apply" writes it, agent translation injects it.
        self.llm_compact_memory: str = ""

        self.current_img: str = None
        self.img_array: np.ndarray = None
        self.mask_array: np.ndarray = None
        self.inpainted_array: np.ndarray = None
        self.notext_array: np.ndarray = None
        if directory is not None:
            self.load(directory)

    def idx2pagename(self, idx: int) -> str:
        return self._idx2pagename[idx]

    def pagename2idx(self, pagename: str) -> int:
        if pagename in self.pages:
            return self._pagename2idx[pagename]
        return -1

    def proj_name(self) -> str:
        return self.type + "_" + osp.basename(self.directory)

    def mark_page_needs_rerender(self, pagename: str):
        """Mark a page as modified by a batch operation; result image is stale."""
        if pagename != self.current_img:
            self._pages_needing_rerender.add(pagename)

    def clear_page_needs_rerender(self, pagename: str):
        """Clear the stale-result-image flag after re-rendering."""
        self._pages_needing_rerender.discard(pagename)

    def page_needs_rerender(self, pagename: str) -> bool:
        return pagename in self._pages_needing_rerender

    # ── 页代数（跨页历史的页屏障）──────────────────────────────────

    def page_generation(self, pagename: str) -> int:
        gens = getattr(self, "_page_generations", None)
        return gens.get(pagename, 0) if gens else 0

    def bump_page_generation(self, pagename: str):
        """栈外写入整体换新某页 blk_list 后调用：该页既有撤销历史过期。"""
        gens = getattr(self, "_page_generations", None)
        if gens is None:
            gens = self._page_generations = {}
        gens[pagename] = gens.get(pagename, 0) + 1

    # ── 批量操作快照（单槽）─────────────────────────────────────────
    # 批量替换等批量操作的撤销不进逐命令撤销栈，而是操作前整项目
    # 快照、一键整体回滚，与逐块编辑的文档撤销栈严格分治（语义定稿
    # 见 docs/技术实现/查找替换与样式管理器重构_设计方案.md §4.4）。

    @property
    def batch_backup_path(self):
        if not self.proj_path:
            return None
        return osp.splitext(self.proj_path)[0] + ".batch_backup.json"

    def has_batch_backup(self) -> bool:
        """本会话内存在可回滚的批量快照；磁盘残留的跨会话文件不视为可用。"""
        return (
            self._batch_backup_valid
            and self.batch_backup_path is not None
            and osp.exists(self.batch_backup_path)
        )

    def write_batch_backup(self, dirty_pages=None) -> bool:
        """把当前已落盘的项目 JSON 整体快照到单槽备份文件（覆盖旧槽）。

        Args:
            dirty_pages: 快照时刻已带脏标记的页面（含当前页），回滚后
                据此整体重标脏——其中若已被重渲过，结果图此刻必然过期。
        """
        if not self.proj_path or not osp.exists(self.proj_path):
            return False
        try:
            with open(self.proj_path, "r", encoding="utf8") as f:
                proj_dict = json.load(f)
            payload = {
                "version": 1,
                "dirty_pages": list(dirty_pages or []),
                "proj": proj_dict,
            }
            tmp_path = self.batch_backup_path + ".tmp"
            with open(tmp_path, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self.batch_backup_path)
        except Exception as e:
            LOGGER.error(f"Failed to write batch backup: {e}")
            return False
        self._batch_backup_valid = True
        return True

    def restore_batch_backup(self) -> List[str]:
        """用快照整体换回项目数据，返回快照记录的脏页清单。

        各页数据全部换回，但保持用户当前所在页不跳转；成功后消费
        快照（删除备份文件并置会话标记失效）。快照缺失/损坏时抛
        ``ProjectLoadFailureException``，备份文件保留不动。
        """
        if not self.has_batch_backup():
            raise ProjectLoadFailureException("No batch backup available")
        try:
            with open(self.batch_backup_path, "r", encoding="utf8") as f:
                payload = json.load(f)
        except Exception as e:
            raise ProjectLoadFailureException(e)
        orig_page = self.current_img
        self.load_from_dict(payload["proj"])
        self._pages_needing_rerender = set(payload.get("dirty_pages", []))
        if orig_page and orig_page in self.pages and orig_page != self.current_img:
            self.set_current_img(orig_page)
        self.clear_batch_backup()
        return list(self._pages_needing_rerender)

    def clear_batch_backup(self):
        if self.batch_backup_path and osp.exists(self.batch_backup_path):
            try:
                os.remove(self.batch_backup_path)
            except OSError as e:
                LOGGER.error(f"Failed to remove batch backup: {e}")
        self._batch_backup_valid = False

    def load(self, directory: str, json_path: str = None) -> bool:
        self.directory = directory
        self._batch_backup_valid = False
        if json_path is None:
            self.proj_path = osp.join(self.directory, self.proj_name() + ".json")
        else:
            self.proj_path = json_path
        new_proj = False
        if not osp.exists(self.proj_path):
            new_proj = True
            self.new_project()
        else:
            try:
                with open(self.proj_path, "r", encoding="utf8") as f:
                    proj_dict = json.loads(f.read())
            except Exception as e:
                raise ProjectLoadFailureException(e)
            self.load_from_dict(proj_dict)
        if not osp.exists(self.inpainted_dir()):
            os.makedirs(self.inpainted_dir())
        if not osp.exists(self.mask_dir()):
            os.makedirs(self.mask_dir())

        return new_proj

    def mask_dir(self):
        return osp.join(self.directory, "mask")

    def inpainted_dir(self):
        return osp.join(self.directory, "inpainted")

    def notext_dir(self):
        return osp.join(self.directory, "notext")

    def result_dir(self):
        return osp.join(self.directory, "result")

    def load_from_dict(self, proj_dict: dict):
        self.set_current_img(None)
        try:
            self.pages = {}
            self._pagename2idx = {}
            self._idx2pagename = {}
            self.not_found_pages = {}
            page_dict = proj_dict["pages"]
            not_found_pages = list(page_dict.keys())
            found_pages = find_all_imgs(
                img_dir=self.directory, abs_path=False, sort=True
            )
            for ii, imname in enumerate(found_pages):
                if imname in page_dict:
                    self.pages[imname] = [
                        TextBlock(**blk_dict) for blk_dict in page_dict[imname]
                    ]
                    not_found_pages.remove(imname)
                else:
                    self.pages[imname] = []
                    self.new_pages.append(imname)
                self._pagename2idx[imname] = ii
                self._idx2pagename[ii] = imname
            for imname in not_found_pages:
                self.not_found_pages[imname] = [
                    TextBlock(**blk_dict) for blk_dict in page_dict[imname]
                ]
        except Exception as e:
            raise ProjectNotSupportedException(e)

        if "image_info" in proj_dict:
            self._image_info = proj_dict["image_info"]
        else:
            self._image_info = {}

        # Project-level story synopsis; legacy projects carry none.
        memory = proj_dict.get("llm_compact_memory", "")
        self.llm_compact_memory = memory if isinstance(memory, str) else ""

        # Project-level base styles; legacy projects carry none → register a
        # default one seeded from the global format (see ensure_default_base_styles).
        self.base_styles = []
        for bs_dict in proj_dict.get("base_styles", []):
            try:
                self.base_styles.append(BaseStyle.from_dict(bs_dict))
            except Exception as e:
                LOGGER.warning(f"Ignoring invalid base style entry: {e}")
        if ensure_default_base_styles(self.base_styles, pcfg.global_fontformat):
            LOGGER.debug("Registered default base style for legacy project")

        for p in self.pages:
            if p not in self._image_info:
                self._image_info[p] = {}
            img_info = self._image_info[p]
            if "finish_code" not in img_info:
                page_blklist = self.pages[p]
                has_empty_blk = len(page_blklist) == 0 or any(
                    not blk.text or len(blk.text) == 0 for blk in page_blklist
                )
                if has_empty_blk:
                    img_info["finish_code"] = 0
                else:
                    img_info["finish_code"] = RunStatus.FIN_ALL

        set_img_failed = False
        if "current_img" in proj_dict:
            current_img = proj_dict["current_img"]
            try:
                self.set_current_img(current_img)
            except ImgnameNotInProjectException:
                set_img_failed = True
        else:
            set_img_failed = True

        if set_img_failed:
            if len(self.pages) > 0:
                self.set_current_img_byidx(0)

    def get_page_progress(self, pagename: str):
        fin_code = self._image_info[pagename]["finish_code"]
        return (fin_code & pcfg.module.finish_code) == pcfg.module.finish_code

    def set_page_progress(self, pagename, code):
        self._image_info[pagename]["finish_code"] = code

    def update_page_progress(self, pagename, code):
        self._image_info[pagename]["finish_code"] |= code

    def load_translation_from_txt(self, file_path: str):
        page_list = parse_txt_translation(file_path)
        missing_pages = []
        unmatched_pages = []
        unexpected_pages = []
        matched_pages = []
        for page_dict in page_list:
            page_name = page_dict["page_name"]
            if page_name in self.pages:
                matched_pages.append(page_name)
            else:
                unexpected_pages.append(page_name)
                continue
            blklist = self.pages[page_name]
            n_blk = len(blklist)
            src_blk_list = page_dict["blk_list"]
            n_src_blk = len(src_blk_list)
            if n_src_blk != n_blk:
                LOGGER.warning(
                    f"Unmatched text blocks in {page_name}, number of text blocks in this page vs source file: {n_blk}-{n_src_blk}"
                )
                unmatched_pages.append(page_name)
            for blkid in range(min(n_blk, n_src_blk)):
                blk = blklist[blkid]
                blk.rich_text = ""
                blk.translation = src_blk_list[blkid]

        matched_pages = set(matched_pages)
        if len(matched_pages) != self.num_pages:
            for page_name in self.pages:
                if page_name not in matched_pages:
                    missing_pages.append(page_name)

        all_matched = (
            len(missing_pages) == 0
            and len(unmatched_pages) == 0
            and len(unexpected_pages) == 0
        )
        return all_matched, {
            "missing_pages": missing_pages,
            "unmatched_pages": unmatched_pages,
            "unexpected_pages": unexpected_pages,
            "matched_pages": matched_pages,
        }

    def load_from_json(self, json_path: str):
        old_dir = self.directory
        directory = osp.dirname(json_path)
        try:
            self.load(directory, json_path=json_path)
        except Exception as e:
            self.load(old_dir)
            raise ProjectLoadFailureException(e)

    def set_current_img(self, imgname: str):
        if imgname is not None:
            if imgname not in self.pages:
                raise ImgnameNotInProjectException
            self.current_img = imgname
            img_path = self.current_img_path()
            mask_path = self.get_mask_path(get_last_modified=True)
            self.img_array = imread(img_path)
            im_h, im_w = self.img_array.shape[:2]
            self.mask_array = None
            if osp.exists(mask_path):
                try:
                    self.mask_array = imread(mask_path, cv2.IMREAD_GRAYSCALE)
                except Exception as e:
                    # 损坏遮罩按"无遮罩"降级（与文件缺失同语义），不让页面加载崩掉
                    LOGGER.warning(f"Failed to read mask {mask_path}: {e}")
            if self.mask_array is None:
                self.mask_array = np.zeros((im_h, im_w), dtype=np.uint8)
            self.inpainted_array = self.load_inpainted_by_imgname(imgname)
            if self.inpainted_array is None:
                self.inpainted_array = np.copy(self.img_array)
            if pcfg.use_notext_images:
                self.notext_array = self.load_notext_by_imgname(imgname)
            else:
                self.notext_array = None
        else:
            self.current_img = None
            self.img_array = None
            self.mask_array = None
            self.inpainted_array = None
            self.notext_array = None

    def current_has_alpha(self):
        if self.current_img is None:
            return False
        return len(self.img_array.shape) and self.img_array.shape[-1] == 4

    def set_current_img_byidx(self, idx: int):
        num_pages = self.num_pages
        if idx < 0:
            idx = idx + self.num_pages
        if idx < 0 or idx > num_pages - 1:
            self.set_current_img(None)
        else:
            self.set_current_img(self.idx2pagename(idx))

    def get_blklist_byidx(self, idx: int) -> List[TextBlock]:
        return self.pages[self.idx2pagename(idx)]

    @property
    def num_pages(self) -> int:
        return len(self.pages)

    @property
    def current_idx(self) -> int:
        return self.pagename2idx(self.current_img)

    def new_project(self):
        if not osp.exists(self.directory):
            raise ProjectDirNotExistException
        self.set_current_img(None)
        imglist = find_all_imgs(self.directory, abs_path=False, sort=True)
        self.pages = {}
        self._pagename2idx = {}
        self._idx2pagename = {}
        self._image_info = {}
        for ii, imgname in enumerate(imglist):
            self.pages[imgname] = []
            self._pagename2idx[imgname] = ii
            self._idx2pagename[ii] = imgname
            self._image_info[imgname] = {"finish_code": 0}
        self.set_current_img_byidx(0)
        self.save()

    def save(self, keep_exist_as_backup=False):
        if not osp.exists(self.directory):
            raise ProjectDirNotExistException
        tmp_save_tgt = self.proj_path + ".tmp"
        try:
            with open(tmp_save_tgt, "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(self.to_dict(), ensure_ascii=False, cls=TextBlkEncoder)
                )
        except Exception:
            raise Exception(f"Failed to write {self.to_dict()}")
        if osp.exists(self.proj_path) and keep_exist_as_backup:
            os.replace(self.proj_path, self.proj_path + ".backup")
            os.replace(tmp_save_tgt, self.proj_path)
        else:
            os.replace(tmp_save_tgt, self.proj_path)
        LOGGER.debug(f"project saved to {self.proj_path}")

    def to_dict(self) -> Dict:
        pages = self.pages.copy()
        pages.update(self.not_found_pages)
        image_info = self._image_info.copy()
        proj_dict = {
            "directory": self.directory,
            "pages": pages,
            "current_img": self.current_img,
            "image_info": image_info,
            "base_styles": [bs.to_dict() for bs in self.base_styles],
            "llm_compact_memory": self.llm_compact_memory,
        }
        return proj_dict

    def read_img(self, imgname: str) -> np.ndarray:
        if imgname not in self.pages:
            raise ImgnameNotInProjectException
        img_path = osp.join(self.directory, imgname)
        img = imread(img_path)
        h, w = img.shape[:2]
        self._image_info[imgname].update({"width": w, "height": h})
        return img

    def save_mask(self, img_name, mask: np.ndarray):
        imwrite(self.get_mask_path(img_name), mask, ext=pcfg.intermediate_imgsave_ext)

    def save_inpainted(self, img_name, inpainted: np.ndarray):
        imwrite(
            self.get_inpainted_path(img_name),
            inpainted,
            ext=pcfg.intermediate_imgsave_ext,
        )

    def current_img_path(self) -> str:
        if self.current_img is None:
            return None
        return osp.join(self.directory, self.current_img)

    def get_mask_path(self, imgname: str = None, get_last_modified=False) -> str:
        if imgname is None:
            imgname = self.current_img

        fileprefix = osp.join(self.mask_dir(), osp.splitext(imgname)[0])
        if get_last_modified:
            p = get_last_modified_file(
                fileprefix, [".jxl", ".png"], ext_fallback=pcfg.intermediate_imgsave_ext
            )
        else:
            p = fileprefix + pcfg.intermediate_imgsave_ext

        return p

    def load_mask_by_imgname(self, imgname: str) -> np.ndarray:
        mask = None
        mp = self.get_mask_path(imgname, get_last_modified=True)
        if osp.exists(mp):
            mask = imread(mp, cv2.IMREAD_GRAYSCALE)
        return mask

    def get_inpainted_path(self, imgname: str = None, get_last_modified=False) -> str:
        if imgname is None:
            imgname = self.current_img

        fileprefix = osp.join(self.inpainted_dir(), osp.splitext(imgname)[0])
        if get_last_modified:
            p = get_last_modified_file(
                fileprefix, [".jxl", ".png"], ext_fallback=pcfg.intermediate_imgsave_ext
            )
        else:
            p = fileprefix + pcfg.intermediate_imgsave_ext

        if not osp.exists(p) and shared.FUZZY_MATCH_IMAGE_NAME:
            if self._fuzzy_inpainted_list is None:
                if osp.exists(self.inpainted_dir()):
                    self._fuzzy_inpainted_list = find_all_imgs(
                        self.inpainted_dir(), sort=True
                    )
                else:
                    self._fuzzy_inpainted_list = []
            pidx = self.pagename2idx(imgname)
            if pidx < len(self._fuzzy_inpainted_list):
                return osp.join(self.inpainted_dir(), self._fuzzy_inpainted_list[pidx])
        return p

    def load_inpainted_by_imgname(
        self, imgname: str, scale_to_src: bool = True
    ) -> np.ndarray:
        inpainted = None
        mp = self.get_inpainted_path(imgname, get_last_modified=True)
        if mp is not None and osp.exists(mp):
            try:
                inpainted = imread(mp)
            except Exception as e:
                # 截断/损坏的中间修复图不致命：告警后按"无修复图"处理，
                # 调用方回退原图，避免页面切换/批量重渲染时整个应用闪退
                LOGGER.warning(f"Failed to read inpainted image {mp}: {e}")
                inpainted = None
            if inpainted is not None:
                if imgname == self.current_img and self.img_array is not None:
                    h, w = self.img_array.shape[:2]
                else:
                    from PIL import Image
                    i = Image.open(osp.join(self.directory, imgname))
                    h, w = i.height, i.width
                ih, iw = inpainted.shape[:2]
                if ih != h or iw != w:
                    inpainted = Image.fromarray(inpainted).resize(
                        (w, h), resample=Image.Resampling.LANCZOS
                    )
                    inpainted = np.array(inpainted)
        return inpainted

    def get_notext_path(self, imgname: str = None) -> str:
        """Return path to the no-text clean background image, or None if unavailable."""
        if imgname is None:
            imgname = self.current_img
        ndir = self.notext_dir()
        if not osp.exists(ndir):
            return None
        direct = osp.join(ndir, imgname)
        if osp.exists(direct):
            return direct
        base = osp.splitext(imgname)[0]
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            candidate = osp.join(ndir, base + ext)
            if osp.exists(candidate):
                return candidate
        return None

    def load_notext_by_imgname(self, imgname: str) -> np.ndarray:
        """Load the no-text clean image, resizing to match the original dimensions."""
        path = self.get_notext_path(imgname)
        if path is None:
            return None
        try:
            notext = imread(path)
        except Exception as e:
            LOGGER.warning(f"Failed to read no-text image {path}: {e}")
            return None
        if notext is None:
            return None
        h, w = self.img_array.shape[:2]
        ih, iw = notext.shape[:2]
        if ih != h or iw != w:
            from PIL import Image
            notext = Image.fromarray(notext).resize(
                (w, h), resample=Image.Resampling.LANCZOS
            )
            notext = np.array(notext)
        return notext

    def get_result_ext(self, imgname: str) -> str:
        if pcfg is not None and pcfg.imgsave_auto_format:
            src_ext = osp.splitext(imgname)[1].lower()
            # .jxl kept in auto-format list for backward compat with existing source images
            if src_ext in {".jpg", ".jpeg", ".png", ".webp", ".jxl", ".bmp"}:
                return src_ext
        ext = ".png"
        if pcfg is not None:
            # .jxl format support (requires pillow-jxl-plugin with Pillow <11)
            if pcfg.imgsave_ext in {".jpg", ".png", ".webp", ".jxl"}:
                ext = pcfg.imgsave_ext
            else:
                LOGGER.warning("invalid image saving ext in config.json")
        return ext

    def get_result_path(self, imgname: str) -> str:
        ext = self.get_result_ext(imgname)
        return osp.join(self.result_dir(), osp.splitext(imgname)[0] + ext)

    def backup(self):
        raise NotImplementedError

    @property
    def is_empty(self):
        return len(self.pages) == 0

    @property
    def is_all_pages_no_text(self):
        return all([len(blklist) == 0 for blklist in self.pages.values()])

    @property
    def img_valid(self):
        return self.img_array is not None

    @property
    def mask_valid(self):
        return self.mask_array is not None

    @property
    def inpainted_valid(self):
        return self.inpainted_array is not None

    def set_next_img(self):
        if self.current_img is not None:
            next_idx = (self.current_idx + 1) % self.num_pages
            self.set_current_img(self.idx2pagename(next_idx))

    def set_prev_img(self):
        if self.current_img is not None:
            next_idx = (self.current_idx - 1 + self.num_pages) % self.num_pages
            self.set_current_img(self.idx2pagename(next_idx))

    def current_block_list(self) -> List[TextBlock]:
        if self.current_img is not None:
            assert self.current_img in self.pages
            return self.pages[self.current_img]
        else:
            return None

    def dump_txt_path(self, dump_target, suffix):
        save_path = osp.join(
            self.directory, self.proj_name() + f"_{dump_target}{suffix}"
        )
        return save_path

    def dump_txt(self, dump_target: str, suffix=".txt"):
        save_path = self.dump_txt_path(dump_target, suffix=suffix)
        text_all = []
        assert dump_target in {"source", "translation"}
        assert suffix in {".txt", ".md"}
        for page_name, blk_list in self.pages.items():
            text_in_page = ["### " + page_name]
            for ii, blk in enumerate(blk_list):
                if dump_target == "translation":
                    text = (
                        blk.translation.strip()
                        .replace("\r\n", " ")
                        .replace("\n", " ")
                        .replace("\r", " ")
                    )
                elif dump_target == "source":
                    text = blk.get_text().strip()
                text_in_page.append(f"{ii + 1}. {text}")
            text_all.append("\n\n".join(text_in_page))
        with open(save_path, "w", encoding="utf8") as f:
            f.write("\n\n\n".join(text_all))

    def merge_from_proj_dict(self, tgt_dict: Dict) -> Dict:
        if self.pages is None:
            self.pages = {}
        src_dict = self.pages if self.pages is not None else {}
        key_lst = list(dict.fromkeys(list(src_dict.keys()) + list(tgt_dict.keys())))
        key_lst.sort()
        rst_dict = {}
        pagename2idx = {}
        idx2pagename = {}
        page_counter = 0
        for key in key_lst:
            if key in src_dict and key not in tgt_dict:
                rst_dict[key] = src_dict[key]
            else:
                rst_dict[key] = tgt_dict[key]
            pagename2idx[key] = page_counter
            idx2pagename[page_counter] = key
            page_counter += 1
        self.pages.clear()
        self.pages.update(rst_dict)
        self._pagename2idx = pagename2idx
        self._idx2pagename = idx2pagename

    def dump_compact_index(self, include_global_font: bool = True) -> Dict:
        from .proj_compact import build_index

        return build_index(self, include_global_font=include_global_font)

    def dump_compact_detail(
        self, page_indices: List[int], fields_whitelist=None
    ) -> Dict:
        from .proj_compact import build_detail

        return build_detail(self, page_indices, fields_whitelist=fields_whitelist)

    def apply_compact_modifications(self, modifications: Dict, metadata: Dict = None):
        from .proj_compact import apply_modifications

        return apply_modifications(self, modifications, metadata)
