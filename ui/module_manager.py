import importlib
import os.path as osp
import sys
import threading
import time
from typing import List, Union

import numpy as np
from qtpy.QtCore import QLocale, QObject, QThread, QTimer, Signal
from qtpy.QtWidgets import QFileDialog

import modules
from modules import (
    GET_VALID_INPAINTERS,
    GET_VALID_OCR,
    GET_VALID_TEXTDETECTORS,
    GET_VALID_TRANSLATORS,
    INPAINTERS,
    OCR,
    TEXTDETECTORS,
    TRANSLATORS,
    BaseTranslator,
    InpainterBase,
    OCRBase,
    TextDetectorBase,
    merge_config_module_params,
)
from modules.base import soft_empty_cache
from modules.translators import MissingTranslatorParams
from utils import shared
from utils.config import RunStatus, pcfg
from utils.imgproc_utils import enlarge_window
from utils.logger import logger as LOGGER
from utils.message import create_error_dialog, create_info_dialog
from utils.proj_imgtrans import ProjImgTrans
from utils.registry import Registry
from utils.textblock import TextBlock, sort_regions

from .configpanel import ConfigPanel
from .custom_widget import ImgtransProgressMessageBox, ParamComboBox, ProgressMessageBox
from .funcmaps import get_maskseg_method
from .misc import get_theme_color

modules.translators.SYSTEM_LANG = QLocale.system().name()
cfg_module = pcfg.module


def _build_dep_notes(registry: Registry) -> dict[str, str]:
    """Build ``{module_name: dependency_hint_or_None}`` for ComboBox grouping.

    Checks each registered module's ``requires_packages`` and
    ``download_file_list``.  Returns a dict where:
    - ``None`` / missing means no extra dependencies
    - a non-empty string describes what's needed (shown as tooltip)
    """
    notes: dict[str, str] = {}
    for name in registry.module_dict:
        cls = registry.get(name)
        parts: list[str] = []
        pkgs = getattr(cls, "requires_packages", None) or []
        for pkg in pkgs:
            if "torch" in pkg.lower():
                parts.append("PyTorch")
            elif "paddle" in pkg.lower():
                parts.append("PaddlePaddle")
            else:
                # Strip version specifiers for display
                pkg_name = (
                    pkg.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
                )
                if pkg_name:
                    parts.append(pkg_name)
        dfl = getattr(cls, "download_file_list", None) or []
        n_models = 0
        for dl_entry in dfl:
            raw = dl_entry.get("files") or []
            if isinstance(raw, str):
                raw = [raw]
            n_models += len(raw)
        if n_models:
            parts.append(f"{n_models} model file{'s' if n_models > 1 else ''}")
        notes[name] = ", ".join(parts) if parts else None
    return notes


class ModuleThread(QThread):
    finish_set_module = Signal()
    module_prepare_progress = Signal(dict)
    _failed_set_module_msg = "Failed to set module."
    module_thread_stopped = Signal()

    def __init__(
        self, module_key: str, MODULE_REGISTER: Registry, *args, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.job = None
        self.module: Union[TextDetectorBase, BaseTranslator, InpainterBase, OCRBase] = (
            None
        )
        self.module_register = MODULE_REGISTER
        self.module_key = module_key

        self.pipeline_pagekey_queue = []
        self.finished_counter = 0
        self.num_process_pages = 0
        self.imgtrans_proj: ProjImgTrans = None
        self.stop_requested = False
        # Thread-safe cancel for module preparation (downloads, imports).
        self.cancel_event = threading.Event()
        self.last_set_success = False
        self.last_set_module_name = ""
        self.last_error = None
        self.last_missing_requirements = []

    def _emit_prepare_progress(self, payload: dict):
        """Emit preparation progress with module_key context."""
        payload = dict(payload)
        payload.setdefault("module_key", self.module_key)
        payload.setdefault("module", self.last_set_module_name)
        self.module_prepare_progress.emit(payload)

    def _prepare_module_class(self, module_name: str):
        """Resolve module class after dependency checks (override for richer flow)."""
        self._emit_prepare_progress({
            "event": "importing",
            "message": self.tr("Importing module"),
        })
        if self.cancel_event.is_set():
            return None
        spec = self.module_register.get_spec(module_name)
        if spec is None and module_name not in self.module_register.module_dict:
            raise KeyError(f"Unknown {self.module_key} module: {module_name}")
        module_class = self.module_register.resolve_module(module_name)
        if module_class is None:
            raise KeyError(f"Failed to resolve {self.module_key} module: {module_name}")
        return module_class

    def _discard_module_instance(self, module):
        """Safely unload a module that won't be used."""
        if module is None:
            return
        try:
            module.unload_model(empty_cache=True)
        except Exception as e:
            LOGGER.warning(
                f"Failed to unload {self.module_key} module after failed preparation: {e}"
            )

    def _set_module(self, module_name: str):
        old_module = self.module
        new_module = None
        self.last_set_module_name = module_name
        self.last_set_success = False
        self.last_error = None
        self.last_missing_requirements = []
        self.cancel_event.clear()

        try:
            if module_name not in self.module_register.module_dict:
                available = list(self.module_register.module_dict.keys())
                if available:
                    fallback = available[0]
                    LOGGER.warning(
                        f"Module '{module_name}' not available, falling back to '{fallback}'"
                    )
                    module_name = fallback
                else:
                    LOGGER.warning(
                        f"No modules available for '{self.module_key}', skipping."
                    )
                    self.finish_set_module.emit()
                    return

            # Skip re-load if already loaded and complete
            same_module = old_module is not None and old_module.name == module_name
            if same_module and old_module.all_model_loaded():
                self.last_set_success = True
                self.finish_set_module.emit()
                return

            module_class = self._prepare_module_class(module_name)
            if self.cancel_event.is_set():
                raise RuntimeError("Module preparation cancelled by user.")

            if same_module:
                new_module = old_module
                old_module = None
            else:
                self._emit_prepare_progress({
                    "event": "instantiating",
                    "message": self.tr("Creating module"),
                })
                params = cfg_module.get_params(self.module_key).get(module_name)
                if params is not None:
                    new_module = module_class(**params)
                else:
                    new_module = module_class()

            if self.cancel_event.is_set():
                raise RuntimeError("Module preparation cancelled by user.")

            if not pcfg.module.load_model_on_demand:
                self._emit_prepare_progress({
                    "event": "loading_model",
                    "message": self.tr("Loading model"),
                })
                new_module.load_model()

            if self.cancel_event.is_set():
                raise RuntimeError("Module preparation cancelled by user.")

            if old_module is not None:
                old_module.unload_model(empty_cache=True)
                old_module = None

            self.module = new_module
            new_module = None
            self.last_set_success = True

        except RuntimeError as e:
            if "cancelled" in str(e).lower():
                LOGGER.info(f"Cancelled preparing {self.module_key} module {module_name}.")
            self.module = None
            self._discard_module_instance(new_module)
            self._discard_module_instance(old_module)
            self.last_error = e
        except Exception as e:
            self.module = None
            self._discard_module_instance(new_module)
            self._discard_module_instance(old_module)
            self.last_error = e
            create_error_dialog(e, self._failed_set_module_msg)

        self.finish_set_module.emit()

    def installMissingPackagesAndSetModule(
        self,
        module_name: str,
        requirements: List[str],
    ):
        """Install missing packages then retry _set_module in thread."""
        self.job = lambda: self._install_missing_then_set(module_name, requirements)
        self.start()

    def _install_missing_then_set(self, module_name: str, requirements: List[str]):
        self.last_set_module_name = module_name
        self.last_set_success = False
        self.last_error = None
        self.last_missing_requirements = []
        self.cancel_event.clear()
        self._emit_prepare_progress({
            "event": "installing_packages",
            "message": self.tr("Installing packages"),
        })
        try:
            import subprocess
            python = sys.executable
            for req in requirements:
                subprocess.run(
                    [python, "-m", "pip", "install", req, "--prefer-binary"],
                    capture_output=True, timeout=300, check=True,
                )
        except Exception as e:
            self.last_error = e
            self.finish_set_module.emit()
            return
        self._set_module(module_name)

    def pipeline_finished(self):
        if self.imgtrans_proj is None:
            return True
        elif self.finished_counter >= self.num_process_pages:
            return True
        return False

    def initImgtransPipeline(self, proj: ProjImgTrans):
        if self.isRunning():
            self.terminate()
        self.imgtrans_proj = proj
        self.finished_counter = 0
        self.pipeline_pagekey_queue.clear()

    def requestStop(self):
        self.stop_requested = True

    def requestCancelModuleInit(self):
        """Request cooperative cancellation of module preparation."""
        self.cancel_event.set()

    def run(self):
        if self.job is not None:
            try:
                self.job()
            except Exception as e:
                create_error_dialog(
                    e, self.tr("Module task failed."), f"ModuleThreadFailed:{self.module_key}"
                )
        self.job = None


class InpaintThread(ModuleThread):
    finish_inpaint = Signal(dict)
    inpainting = False
    inpaint_failed = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("inpainter", INPAINTERS, *args, **kwargs)

    @property
    def inpainter(self) -> InpainterBase:
        return self.module

    def setInpainter(self, inpainter: str):
        self.job = lambda: self._set_module(inpainter)
        self.start()

    def inpaint(
        self, img: np.ndarray, mask: np.ndarray, img_key: str = None, inpaint_rect=None
    ):
        self.job = lambda: self._inpaint(img, mask, img_key, inpaint_rect)
        self.start()

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, img_key: str = None, inpaint_rect=None
    ):
        inpaint_dict = {}
        self.inpainting = True
        try:
            inpainted = self.inpainter.inpaint(img, mask)
            inpaint_dict = {
                "inpainted": inpainted,
                "img": img,
                "mask": mask,
                "img_key": img_key,
                "inpaint_rect": inpaint_rect,
            }
            self.finish_inpaint.emit(inpaint_dict)
        except Exception as e:
            create_error_dialog(e, self.tr("Inpainting Failed."), "InpaintFailed")
            self.inpainting = False
            self.inpaint_failed.emit()
        self.inpainting = False


class TextDetectThread(ModuleThread):
    finish_detect_page = Signal(str)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("textdetector", TEXTDETECTORS, *args, **kwargs)

    def setTextDetector(self, textdetector: str):
        self.job = lambda: self._set_module(textdetector)
        self.start()

    @property
    def textdetector(self) -> TextDetectorBase:
        return self.module


class OCRThread(ModuleThread):
    finish_ocr_page = Signal(str)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("ocr", OCR, *args, **kwargs)

    def setOCR(self, ocr: str):
        self.job = lambda: self._set_module(ocr)
        self.start()

    @property
    def ocr(self) -> OCRBase:
        return self.module


class TranslateThread(ModuleThread):
    finish_translate_page = Signal(str)
    progress_changed = Signal(int)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("translator", TRANSLATORS, *args, **kwargs)
        self.translator: BaseTranslator = self.module

    def _set_translator(self, translator: str):

        old_translator = self.translator
        source, target = cfg_module.translate_source, cfg_module.translate_target
        if self.translator is not None:
            if getattr(self.translator, "name", None) == translator:
                return

        try:
            params = cfg_module.translator_params[translator]
            translator_module: BaseTranslator = TRANSLATORS.resolve_module(translator)
            if params is not None:
                self.translator = translator_module(
                    source, target, raise_unsupported_lang=False, **params
                )
            else:
                self.translator = translator_module(
                    source, target, raise_unsupported_lang=False
                )
            cfg_module.translate_source = self.translator.lang_source
            cfg_module.translate_target = self.translator.lang_target
            cfg_module.translator = self.translator.name
        except Exception as e:
            if old_translator is None:
                fallback_name = next(iter(TRANSLATORS.module_dict))
                fallback_cls = TRANSLATORS.resolve_module(fallback_name)
                old_translator = fallback_cls(
                    "简体中文", "English", raise_unsupported_lang=False
                )
            self.translator = old_translator
            msg = self.tr("Failed to set translator ") + translator
            create_error_dialog(e, msg, "FailedSetTranslator")

        self.module = self.translator
        self.finish_set_module.emit()

    def setTranslator(self, translator: str):
        self.job = lambda: self._set_translator(translator)
        self.start()

    def _translate_page(self, page_dict, page_key: str, emit_finished=True, project=None):
        page = page_dict[page_key]
        try:
            self.translator.translate_textblk_lst(
                page,
                project=project or getattr(self, 'imgtrans_proj', None),
                page_key=page_key,
            )
        except Exception as e:
            create_error_dialog(e, self.tr("Translation Failed."), "TranslationFailed")
        if emit_finished:
            self.finish_translate_page.emit(page_key)

    def translatePage(self, page_dict, page_key: str):
        self.job = lambda: self._translate_page(page_dict, page_key)
        self.start()

    def push_pagekey_queue(self, page_key: str):
        self.pipeline_pagekey_queue.append(page_key)

    def runTranslatePipeline(self, imgtrans_proj: ProjImgTrans):
        self.initImgtransPipeline(imgtrans_proj)
        if hasattr(self.translator, "set_project"):
            self.translator.set_project(imgtrans_proj)
        self.job = self._run_translate_pipeline
        self.start()

    def _run_translate_pipeline(self):
        delay = self.translator.delay()

        while not self.pipeline_finished():
            if self.stop_requested:
                self.module_thread_stopped.emit()
                self.stop_requested = False
                break

            if len(self.pipeline_pagekey_queue) == 0:
                time.sleep(0.1)
                continue

            page_key = self.pipeline_pagekey_queue.pop(0)
            self.blockSignals(True)
            trans_success = True
            try:
                self._translate_page(
                    self.imgtrans_proj.pages, page_key,
                    emit_finished=False, project=self.imgtrans_proj,
                )
            except Exception as e:
                # TODO: allowing retry/skip/terminate
                trans_success = False
                msg = self.tr("Translation Failed.")
                if isinstance(e, MissingTranslatorParams):
                    msg = (
                        msg
                        + "\n"
                        + str(e)
                        + self.tr(" is required for " + self.translator.name)
                    )

                self.blockSignals(False)
                create_error_dialog(e, msg, "TranslationFailed")
                # self.imgtrans_proj = None
                # self.finished_counter = 0
                # self.pipeline_pagekey_queue = []
                # return
            self.blockSignals(False)
            self.finished_counter += 1
            if trans_success:
                self.imgtrans_proj.update_page_progress(
                    page_key, RunStatus.FIN_TRANSLATE
                )
            self.progress_changed.emit(self.finished_counter)

            if not self.pipeline_finished() and delay > 0:
                time.sleep(delay)


class ImgtransThread(QThread):
    pipeline_stopped = Signal()
    update_detect_progress = Signal(int)
    update_ocr_progress = Signal(int)
    update_translate_progress = Signal(int)
    update_inpaint_progress = Signal(int)

    finish_blktrans_stage = Signal(str, int)
    finish_blktrans = Signal(int, list)
    unload_modules = Signal(list)

    detect_counter = 0
    ocr_counter = 0
    translate_counter = 0
    inpaint_counter = 0

    def __init__(
        self,
        textdetect_thread: TextDetectThread,
        ocr_thread: OCRThread,
        translate_thread: TranslateThread,
        inpaint_thread: InpaintThread,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.textdetect_thread = textdetect_thread
        self.ocr_thread = ocr_thread
        self.translate_thread = translate_thread
        self.translate_thread.module_thread_stopped.connect(
            self.on_module_thread_stopped
        )
        self.inpaint_thread = inpaint_thread
        self.job = None
        self.imgtrans_proj: ProjImgTrans = None
        self.stop_requested = False
        self.pages_to_process = None  # 需要处理的页面列表（用于继续运行模式）

    def on_module_thread_stopped(self):
        while True:
            # might freeze UI
            if (
                self.translate_thread.isRunning()
                or self.inpaint_thread.isRunning()
                or self.ocr_thread.isRunning()
                or self.textdetect_thread.isRunning()
            ):
                time.sleep(0.05)
                continue
            break

        self.pipeline_stopped.emit()

    @property
    def textdetector(self) -> TextDetectorBase:
        return self.textdetect_thread.textdetector

    @property
    def ocr(self) -> OCRBase:
        return self.ocr_thread.ocr

    @property
    def translator(self) -> BaseTranslator:
        return self.translate_thread.translator

    @property
    def inpainter(self) -> InpainterBase:
        return self.inpaint_thread.inpainter

    def runImgtransPipeline(self, imgtrans_proj: ProjImgTrans, pages_to_process=None):
        self.imgtrans_proj = imgtrans_proj
        self.pages_to_process = pages_to_process  # 保存需要处理的页面列表
        self.num_pages = len(self.imgtrans_proj.pages)
        self.stop_requested = False
        # 创建处理索引到实际页面索引的映射
        self.process_idx_to_page_idx = {}
        self.job = self._imgtrans_pipeline
        self.start()

    def requestStop(self):
        """Request stop current task"""
        if self.isRunning():
            self.stop_requested = True
        # 同时停止翻译线程
        if self.translate_thread.isRunning():
            self.translate_thread.requestStop()

    def runBlktransPipeline(
        self,
        blk_list: List[TextBlock],
        tgt_img: np.ndarray,
        mode: int,
        blk_ids: List[int],
        tgt_mask,
    ):
        self.job = lambda: self._blktrans_pipeline(
            blk_list, tgt_img, mode, blk_ids, tgt_mask
        )
        self.start()

    def _blktrans_pipeline(
        self,
        blk_list: List[TextBlock],
        tgt_img: np.ndarray,
        mode: int,
        blk_ids: List[int],
        tgt_mask,
    ):
        if mode >= 0 and mode < 3:
            try:
                ocr_mod = self.ocr_thread.module
                if ocr_mod is None:
                    LOGGER.warning(
                        "OCR module not loaded, skipping OCR in blktrans"
                    )
                else:
                    ocr_mod.run_ocr(tgt_img, blk_list, split_textblk=True)
            except Exception as e:
                create_error_dialog(e, self.tr("OCR Failed."), "OCRFailed")

        if mode != 0 and mode < 3:
            try:
                trans_mod = self.translate_thread.module
                if trans_mod is None:
                    LOGGER.warning(
                        "Translator module not loaded, "
                        "skipping translate in blktrans"
                    )
                else:
                    trans_mod.translate_textblk_lst(
                        blk_list,
                        project=getattr(self, 'imgtrans_proj', None),
                        page_key=getattr(self, 'current_page_key', None),
                    )
            except Exception as e:
                create_error_dialog(
                    e, self.tr("Translation Failed."), "TranslationFailed"
                )
        if mode > 1:
            im_h, im_w = tgt_img.shape[:2]
            progress_prod = 100.0 / len(blk_list) if len(blk_list) > 0 else 0
            try:
                for ii, blk in enumerate(blk_list):
                    xyxy = enlarge_window(blk.xyxy, im_w, im_h)
                    xyxy = np.array(xyxy)
                    x1, y1, x2, y2 = xyxy.astype(np.int64)
                    blk.region_inpaint_dict = None
                    if y2 - y1 > 2 and x2 - x1 > 2:
                        im = np.copy(tgt_img[y1:y2, x1:x2])
                        maskseg_method = get_maskseg_method()
                        inpaint_mask_array, ballon_mask, bub_dict = maskseg_method(
                            im, mask=tgt_mask[y1:y2, x1:x2]
                        )
                        mask = self.post_process_mask(inpaint_mask_array)
                        if mask.sum() > 0:
                            if self.inpaint_thread.inpainter is None:
                                LOGGER.warning(
                                    "Inpainter not loaded, "
                                    "skipping inpaint in blktrans"
                                )
                            else:
                                inpainted = (
                                    self.inpaint_thread.inpainter.inpaint(im, mask)
                                )
                                blk.region_inpaint_dict = {
                                    "img": im,
                                    "mask": mask,
                                    "inpaint_rect": [x1, y1, x2, y2],
                                    "inpainted": inpainted,
                                }
                    self.finish_blktrans_stage.emit(
                        "inpaint", int((ii + 1) * progress_prod)
                    )
            except Exception as e:
                create_error_dialog(
                    e, self.tr("Inpaint Failed."), "InpaintFailed"
                )
        self.finish_blktrans.emit(mode, blk_ids)

    def _imgtrans_pipeline(self):
        self.detect_counter = 0
        self.ocr_counter = 0
        self.translate_counter = 0
        self.inpaint_counter = 0

        # 如果指定了pages_to_process，只处理这些页面
        all_pages = list(self.imgtrans_proj.pages.keys())
        if self.pages_to_process is not None and len(self.pages_to_process) > 0:
            pages_to_iterate = self.pages_to_process
            self.num_pages = num_pages = len(self.pages_to_process)
            # 建立处理索引到实际页面索引的映射
            for process_idx, page_name in enumerate(pages_to_iterate):
                if page_name in all_pages:
                    self.process_idx_to_page_idx[process_idx] = all_pages.index(
                        page_name
                    )
            LOGGER.info(f"Processing specific pages: {len(pages_to_iterate)} pages")
        else:
            pages_to_iterate = all_pages
            self.num_pages = num_pages = len(self.imgtrans_proj.pages)
            # 处理索引等于实际页面索引
            for i in range(num_pages):
                self.process_idx_to_page_idx[i] = i
            LOGGER.info(f"Processing all {num_pages} pages")
        self.textdetect_thread.num_process_pages = self.num_pages
        self.ocr_thread.num_process_pages = self.num_pages
        self.inpaint_thread.num_process_pages = self.num_pages
        self.translate_thread.num_process_pages = self.num_pages

        low_vram_trans = False
        if self.translator is not None:
            low_vram_trans = self.translator.low_vram_mode
            self.parallel_trans = (
                not self.translator.is_computational_intensive() and not low_vram_trans
            )
        else:
            self.parallel_trans = False
        if self.parallel_trans and cfg_module.enable_translate:
            self.translate_thread.runTranslatePipeline(self.imgtrans_proj)

        if cfg_module.enable_translate and hasattr(self.translator, "set_project"):
            self.translator.set_project(self.imgtrans_proj)

        for imgname in pages_to_iterate:
            # 检查是否请求停止
            if self.stop_requested:
                LOGGER.info("Image translation pipeline stopped by user")
                break

            img = self.imgtrans_proj.read_img(imgname)
            mask = blk_list = None
            need_save_mask = False
            if cfg_module.enable_detect:
                try:
                    mask, blk_list = self.textdetector.detect(img, self.imgtrans_proj)
                    need_save_mask = True
                except Exception as e:
                    create_error_dialog(
                        e, self.tr("Text Detection Failed."), "TextDetectFailed"
                    )
                    blk_list = []
                self.detect_counter += 1
                if pcfg.module.keep_exist_textlines:
                    blk_list = self.imgtrans_proj.pages[imgname] + blk_list
                    blk_list = sort_regions(blk_list)
                    existed_mask = self.imgtrans_proj.load_mask_by_imgname(imgname)
                    if existed_mask is not None:
                        mask = np.bitwise_or(mask, existed_mask)
                self.imgtrans_proj.pages[imgname] = blk_list

                if mask is not None and not cfg_module.enable_ocr:
                    self.imgtrans_proj.save_mask(imgname, mask)
                    need_save_mask = False

                self.imgtrans_proj.update_page_progress(imgname, RunStatus.FIN_DET)
                self.update_detect_progress.emit(self.detect_counter)

            if blk_list is None:
                blk_list = (
                    self.imgtrans_proj.pages[imgname]
                    if imgname in self.imgtrans_proj.pages
                    else []
                )

            if cfg_module.enable_ocr:
                if self.ocr is None:
                    LOGGER.warning("OCR module not loaded, skipping OCR stage")
                else:
                    try:
                        self.ocr.run_ocr(img, blk_list)
                    except Exception as e:
                        create_error_dialog(
                            e, self.tr("OCR Failed."), "OCRFailed"
                        )
                self.ocr_counter += 1

                self.imgtrans_proj.update_page_progress(imgname, RunStatus.FIN_OCR)
                self.update_ocr_progress.emit(self.ocr_counter)

            if need_save_mask and mask is not None:
                self.imgtrans_proj.save_mask(imgname, mask)
                need_save_mask = False

            if cfg_module.enable_translate:
                if self.parallel_trans:
                    self.translate_thread.push_pagekey_queue(imgname)
                elif not low_vram_trans:
                    if self.translator is None:
                        LOGGER.warning(
                            "Translator not loaded, skipping translate stage"
                        )
                    else:
                        self.translator.translate_textblk_lst(
                            blk_list,
                            project=self.imgtrans_proj,
                            page_key=imgname,
                        )
                        self.translate_counter += 1
                        self.update_translate_progress.emit(self.translate_counter)

            if cfg_module.enable_inpaint:
                if mask is None:
                    mask = self.imgtrans_proj.load_mask_by_imgname(imgname)

                if mask is not None:
                    if self.inpainter is None:
                        LOGGER.warning(
                            "Inpainter not loaded, skipping inpaint stage"
                        )
                    else:
                        try:
                            inpainted = self.inpainter.inpaint(
                                img, mask, blk_list
                            )
                            self.imgtrans_proj.save_inpainted(
                                imgname, inpainted
                            )
                        except Exception as e:
                            create_error_dialog(
                                e,
                                self.tr("Inpainting Failed."),
                                "InpaintFailed",
                            )

                self.inpaint_counter += 1
                self.imgtrans_proj.update_page_progress(imgname, RunStatus.FIN_INPAINT)
                self.update_inpaint_progress.emit(self.inpaint_counter)
        if cfg_module.enable_translate and low_vram_trans:
            unload_modules(self, ["textdetector", "inpainter", "ocr"])
            for imgname in pages_to_iterate:
                # 检查是否请求停止
                if self.stop_requested:
                    LOGGER.info("Translation stopped by user")
                    break

                blk_list = self.imgtrans_proj.pages[imgname]
                if self.translator is None:
                    LOGGER.warning(
                        "Translator not loaded, skipping low-vram translate"
                    )
                else:
                    self.translator.translate_textblk_lst(
                        blk_list,
                        project=self.imgtrans_proj,
                        page_key=imgname,
                    )
                    self.translate_counter += 1
                    self.imgtrans_proj.update_page_progress(
                        imgname, RunStatus.FIN_TRANSLATE
                    )
                    self.update_translate_progress.emit(
                        self.translate_counter
                    )

        if cfg_module.enable_translate and hasattr(self.translator, "finalize"):
            self.translator.finalize()

        if self.stop_requested and (
            not cfg_module.enable_translate or not self.parallel_trans
        ):
            self.pipeline_stopped.emit()

    def detect_finished(self) -> bool:
        if self.imgtrans_proj is None:
            return True
        return self.detect_counter == self.num_pages or not cfg_module.enable_detect

    def ocr_finished(self) -> bool:
        if self.imgtrans_proj is None:
            return True
        return self.ocr_counter == self.num_pages or not cfg_module.enable_ocr

    def translate_finished(self) -> bool:
        if (
            self.imgtrans_proj is None
            or not cfg_module.enable_ocr
            or not cfg_module.enable_translate
        ):
            return True
        if self.parallel_trans:
            # 检查翻译计数器是否达到需要处理的页面数
            return self.translate_thread.finished_counter >= self.num_pages
        return (
            self.translate_counter == self.num_pages or not cfg_module.enable_translate
        )

    def inpaint_finished(self) -> bool:
        if self.imgtrans_proj is None or not cfg_module.enable_inpaint:
            return True
        return self.inpaint_counter == self.num_pages or not cfg_module.enable_inpaint

    def run(self):
        if self.job is not None:
            self.job()
        self.job = None

    def recent_finished_index(self, ref_counter: int) -> int:
        if cfg_module.enable_detect:
            ref_counter = min(ref_counter, self.detect_counter)
        if cfg_module.enable_ocr:
            ref_counter = min(ref_counter, self.ocr_counter)
        if cfg_module.enable_inpaint:
            ref_counter = min(ref_counter, self.inpaint_counter)
        if cfg_module.enable_translate:
            if self.parallel_trans:
                ref_counter = min(ref_counter, self.translate_thread.finished_counter)
            else:
                ref_counter = min(ref_counter, self.translate_counter)

        process_idx = ref_counter - 1
        # 将处理索引转换为实际页面索引
        if (
            hasattr(self, "process_idx_to_page_idx")
            and process_idx in self.process_idx_to_page_idx
        ):
            return self.process_idx_to_page_idx[process_idx]
        return process_idx


def unload_modules(self, module_names):
    model_deleted = False
    for module in module_names:
        module = getattr(self, module)
        if hasattr(module, "unload_model"):
            model_deleted = model_deleted or module.unload_model()
    if model_deleted:
        soft_empty_cache()


def _ensure_module_deps(
    module_cls,
    parent_widget,
) -> bool:
    """Check module dependencies and show an install dialog if anything is
    missing.  Returns ``True`` if all deps are satisfied (or the user chose to
    skip), ``False`` if the module class is invalid.

    ``module_cls`` may be a class or a ``ModuleSpec`` — both are handled.

    The dialog shows:
    - Python packages that need installing (from ``requires_packages``)
    - Model files that need downloading (from ``download_file_list``)
    - "Install all" / "Later" buttons

    IMPORTANT: ``ModuleSpec`` metadata (``download_file_list``,
    ``dependencies``) is used directly **without** resolving/importing the
    module.  Resolving would trigger heavy C-extension imports (torch, cv2)
    on the **main thread** and freeze the UI for several seconds — only
    resolve if the dialog actually needs to install something.
    """
    from utils.registry import ModuleSpec

    is_spec = isinstance(module_cls, ModuleSpec)

    # Keep ModuleSpec name for the dialog so the user sees the registration key.
    mod_name = module_cls.key if is_spec else getattr(module_cls, "__name__", "?")

    # ── Read metadata without resolving ──────────────────────────────
    # ModuleSpec stores ``dependencies`` (like ``requires_packages``)
    # and ``download_file_list`` from the AST scan.
    if is_spec:
        pkgs = getattr(module_cls, "dependencies", None) or []
        dfl = getattr(module_cls, "download_file_list", None) or []
        actual_cls = None  # resolved later only if needed
    else:
        pkgs = getattr(module_cls, "requires_packages", None) or []
        dfl = getattr(module_cls, "download_file_list", None) or []
        actual_cls = module_cls

    # Check which packages are already installed
    missing_pkgs: list[str] = []
    if pkgs:
        try:
            import importlib.metadata as importlib_metadata

            from packaging.requirements import Requirement
            from packaging.utils import canonicalize_name
        except ImportError:
            missing_pkgs = list(pkgs)
        else:
            for req_str in pkgs:
                try:
                    req = Requirement(req_str)
                    dist = importlib_metadata.distribution(canonicalize_name(req.name))
                    if not req.specifier.contains(dist.version, prereleases=True):
                        missing_pkgs.append(req_str)
                except importlib_metadata.PackageNotFoundError:
                    # Metadata name mismatch (e.g. onnxruntime installed as
                    # onnxruntime-gpu).  Try a direct import as last resort —
                    # if the top-level module can be loaded the dependency is
                    # actually satisfied.
                    try:
                        importlib.import_module(req.name)
                    except ImportError:
                        missing_pkgs.append(req_str)

    # Check which model files are already on disk.
    # Use ``save_files`` (actual on-disk paths) when available, falling
    # back to ``files`` (archive-internal names).
    missing_model_labels: list[str] = []
    for dl_entry in dfl:
        check_paths = dl_entry.get("save_files") or dl_entry.get("files") or []
        if isinstance(check_paths, str):
            check_paths = [check_paths]
        for fpath in check_paths:
            if not osp.isabs(fpath):
                fpath = osp.join(shared.PROGRAM_PATH, fpath)
            if not osp.exists(fpath):
                label = dl_entry.get("url", osp.basename(fpath))
                missing_model_labels.append(label)
                break  # one line per entry

    if not missing_pkgs and not missing_model_labels:
        return True  # everything already present

    # --- Lazy resolve: only import the module if we actually need to
    # install something.  The resolve() call does a real Python import
    # (torch, cv2 etc.) --- keep it off the happy path. ----------
    if is_spec and actual_cls is None:
        try:
            actual_cls = module_cls.resolve()
        except Exception as e:
            LOGGER.error(
                "Failed to resolve module '%s' (import_path=%s): %s",
                mod_name,
                getattr(module_cls, "import_path", "?"),
                e,
            )
            create_error_dialog(
                e,
                (
                    f"Failed to load module '{mod_name}'.\n"
                    "The module file may be corrupted or missing critical dependencies.\n"
                    "Please check the log for details."
                ),
            )
            return False

    # ── Build and show the install dialog ──
    from qtpy.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
    )

    # ── Background install worker ──
    class _InstallWorker(QThread):
        """Install Python packages and download model files in a background thread."""
        status = Signal(str)         # current operation text
        log_line = Signal(str)       # log message
        finished_with_result = Signal(bool)  # overall success

        def __init__(self, mod_name, missing_pkgs, dfl, parent=None):
            super().__init__(parent)
            self.mod_name = mod_name
            self.missing_pkgs = missing_pkgs
            self.dfl = dfl  # raw download_file_list from the module

        def run(self):
            import shutil
            import subprocess

            success = True
            failure_code = None
            has_hf_no_mirror = self._check_hf_no_mirror()
            # 1. Install Python packages
            if self.missing_pkgs:
                self.status.emit(
                    "Step 1/2: Installing Python packages…"
                )
                self.log_line.emit(
                    ">> Packages: " + ", ".join(self.missing_pkgs)
                )

                python = sys.executable
                _uv_avail = (
                    subprocess.run(
                        [python, "-m", "uv", "--version"],
                        capture_output=True, check=False,
                    ).returncode == 0
                )
                _runners = []
                if _uv_avail:
                    _runners.append([python, "-m", "uv", "pip", "install"])
                _runners.append([python, "-m", "pip", "install"])

                def _pip_install(pkgs, *, no_deps=False):
                    bases = _runners if not no_deps else _runners[::-1]
                    for runner in bases:
                        is_uv = "uv" in runner
                        extra = []
                        if no_deps:
                            extra = ["--no-deps"]
                        elif not is_uv:
                            # pip supports --prefer-binary / --timeout; uv does not
                            extra = ["--prefer-binary", "--timeout", "30"]
                        try:
                            subprocess.run(
                                [*runner, *pkgs, *extra],
                                timeout=300, check=True,
                            )
                            return True
                        except Exception:
                            continue
                    sys_py = shutil.which("python")
                    if sys_py and osp.realpath(sys_py) != osp.realpath(python):
                        try:
                            subprocess.run(
                                [sys_py, "-m", "pip", "install",
                                 *pkgs, "--prefer-binary", "--timeout", "30"],
                                timeout=300, check=True,
                            )
                            return True
                        except Exception:
                            pass
                    return False

                for pkg in self.missing_pkgs:
                    self.status.emit(f"Installing {pkg}…")
                    self.log_line.emit(f">> Installing {pkg} …")
                    if not _pip_install([pkg]):
                        self.log_line.emit(
                            f">> Package '{pkg}' failed with deps, retrying --no-deps …"
                        )
                        if not _pip_install([pkg], no_deps=True):
                            self.log_line.emit(f">> FAILED: {pkg}")
                            success = False
                            failure_code = f"pip_failed:{pkg}"
                            break

                if success:
                    self.log_line.emit(">> Package installation complete.")

            # 2. Download model files
            if success and self.dfl:
                self.status.emit("Step 2/2: Downloading model files…")
                for dl_entry in self.dfl:
                    url = dl_entry.get("url", "?")
                    fname = osp.basename(url) or url
                    self.status.emit(f"Downloading {fname}…")
                    self.log_line.emit(f">> Downloading: {fname}")
                    self.log_line.emit(f"   from: {url}")
                    try:
                        from utils.download_util import download_and_check_files

                        ok = download_and_check_files(**dl_entry)
                    except Exception as e:
                        self.log_line.emit(f">> Error: {e}")
                        ok = False
                    if ok:
                        self.log_line.emit(f">> Downloaded: {fname}")
                    else:
                        self.log_line.emit(f">> FAILED: {fname}")
                        success = False
                        failure_code = self._classify_network_error(url, has_hf_no_mirror)

            self.finished_with_result.emit(success)
            # Emit any failure code after finished_with_result so the dialog
            # can display it on the next signal dispatch. We store it on self.
            self._failure_code = failure_code

        # ── helpers run in the worker thread (no Qt calls) ──

        def _check_hf_no_mirror(self):
            """Return True if there are HF URLs but no mirror configured."""
            if not self.dfl:
                return False
            try:
                from utils.config import pcfg

                if pcfg.mirror.hf_endpoint:
                    return False
                for dl_entry in self.dfl:
                    url = dl_entry.get("url", "")
                    if "huggingface.co" in url:
                        return True
            except Exception:
                pass
            return False

        def _classify_network_error(self, url, has_hf_no_mirror):
            """Return a short error code for the dialog to translate."""
            if "huggingface.co" in url and has_hf_no_mirror:
                return "network_hf_no_mirror"
            if "huggingface.co" in url:
                return "network_hf"
            if "github.com" in url or "github" in url:
                return "network_github"
            return "network_other"

    class _InstallDialog(QDialog):
        def __init__(self, mod_name, pkgs, models, dfl, parent=None):
            super().__init__(parent)
            self._installed = False
            self._worker = None
            self._dfl = dfl
            self.setWindowTitle(
                self.tr("Install Dependencies")
            )
            self.setMinimumWidth(520)
            self.setMinimumHeight(400)
            layout = QVBoxLayout(self)

            # ── Info section ──
            layout.addWidget(
                QLabel(
                    self.tr('Module "{name}" needs extra dependencies:').format(
                        name=mod_name
                    )
                )
            )

            if pkgs:
                pkg_label = QLabel(self.tr("Python packages:"))
                pkg_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
                layout.addWidget(pkg_label)
                for p in pkgs:
                    layout.addWidget(QLabel(f"  • {p}"))

            if models:
                model_label = QLabel(self.tr("Model files to download:"))
                model_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
                layout.addWidget(model_label)
                for m in models:
                    layout.addWidget(QLabel(f"  • {m}"))

            layout.addSpacing(12)

            # Detect HuggingFace URLs with no mirror configured → show warning
            _has_hf_no_mirror = False
            if models:
                try:
                    if not pcfg.mirror.hf_endpoint:
                        for m in models:
                            if "huggingface.co" in m:
                                _has_hf_no_mirror = True
                                break
                except Exception:
                    pass

            if _has_hf_no_mirror:
                _hf_warn = QLabel(
                    self.tr(
                        '⚠ HuggingFace model detected but <b>no mirror configured</b>.<br>Open <b>Settings → Mirror Config</b> and set <tt>hf_endpoint</tt> to <tt>https://hf-mirror.com</tt>.<br>Without a mirror, downloads will likely fail from China.'
                    )
                )
                _hf_warn.setWordWrap(True)
                wc = get_theme_color(key="@warningColor")
                _hf_warn.setStyleSheet(
                    f"color: {wc.name()}; "
                    f"background: rgba({wc.red()},{wc.green()},{wc.blue()},30); "
                    f"border: 1px solid {wc.name()}; "
                    "border-radius: 4px; padding: 8px; margin-top: 4px;"
                )
                layout.addWidget(_hf_warn)
            else:
                layout.addWidget(
                    QLabel(
                        self.tr(
                            "Network restricted? Open Settings → Mirror Config to configure download sources."
                        )
                    )
                )

            # ── Status label ──
            self._status_label = QLabel()
            self._status_label.setVisible(False)
            self._status_label.setWordWrap(True)
            layout.addWidget(self._status_label)

            # ── Progress bar (indeterminate during work) ──
            self._progress = QProgressBar()
            self._progress.setVisible(False)
            layout.addWidget(self._progress)

            # ── Log area (scrollable) ──
            self._log_area = QPlainTextEdit()
            self._log_area.setVisible(False)
            self._log_area.setReadOnly(True)
            self._log_area.setMaximumBlockCount(200)
            lfg = get_theme_color(key="@qwidgetForegroundColor").name()
            lbg = get_theme_color(key="@transtexteditBackgroundColor").name()
            lbrd = get_theme_color(key="@borderColor").name()
            self._log_area.setStyleSheet(
                f"color: {lfg}; font-size: 11px; background: {lbg}; "
                f"border: 1px solid {lbrd}; border-radius: 3px; padding: 4px;"
            )
            self._log_area.setFixedHeight(120)
            layout.addWidget(self._log_area)

            # ── Error hint label (shown on failure) ──
            self._error_hint = QLabel()
            self._error_hint.setVisible(False)
            self._error_hint.setWordWrap(True)
            ec = get_theme_color(key="@dangerColor")
            self._error_hint.setStyleSheet(
                f"color: {ec.name()}; font-size: 12px; "
                f"background: rgba({ec.red()},{ec.green()},{ec.blue()},20); "
                f"border: 1px solid {ec.name()}; border-radius: 4px; padding: 8px;"
            )
            layout.addWidget(self._error_hint)

            # ── Buttons ──
            btn_row = QHBoxLayout()
            self._install_btn = QPushButton(
                self.tr("Install All")
            )
            self._install_btn.clicked.connect(self._do_install)
            btn_row.addWidget(self._install_btn)

            self._skip_btn = QPushButton(self.tr("Later"))
            self._skip_btn.clicked.connect(self.reject)
            btn_row.addWidget(self._skip_btn)
            layout.addLayout(btn_row)

        def closeEvent(self, event):
            """Prevent accidental close during installation."""
            if self._worker and self._worker.isRunning():
                event.ignore()
                return
            event.accept()

        def _do_install(self):
            self._install_btn.setEnabled(False)
            self._skip_btn.setEnabled(False)
            self._error_hint.setVisible(False)

            # Indeterminate progress bar — we show what's happening via status label
            self._progress.setVisible(True)
            self._progress.setRange(0, 0)
            self._status_label.setVisible(True)
            self._status_label.setText(
                self.tr("Starting…")
            )
            self._log_area.setVisible(True)
            self._log_area.clear()

            self._worker = _InstallWorker(
                mod_name, missing_pkgs, self._dfl, self,
            )
            self._worker.status.connect(self._on_worker_status)
            self._worker.log_line.connect(self._on_worker_log)
            self._worker.finished_with_result.connect(self._on_worker_finished)
            self._worker.start()

        def _on_worker_status(self, text):
            # Translate known fixed-status strings
            _map = {
                "Step 1/2: Installing Python packages…": self.tr(
                    "Step 1/2: Installing Python packages…"
                ),
                "Step 2/2: Downloading model files…": self.tr(
                    "Step 2/2: Downloading model files…"
                ),
            }
            self._status_label.setText(_map.get(text, text))

        def _on_worker_log(self, line):
            self._log_area.appendPlainText(line)
            # Auto-scroll to bottom
            sb = self._log_area.verticalScrollBar()
            sb.setValue(sb.maximum())
            LOGGER.info(line)

        def _on_worker_finished(self, success):
            if success:
                self._installed = True
                self.accept()
            else:
                self._progress.setVisible(False)
                self._log_area.setStyleSheet(
                    "color: #a00; font-size: 11px; background: #fff0f0; "
                    "border: 1px solid #e88; border-radius: 3px; padding: 4px;"
                )
                # Show user-friendly error hint (translated)
                code = getattr(self._worker, "_failure_code", "")
                hint = self._format_error_hint(code)
                if hint:
                    self._error_hint.setText(hint)
                    self._error_hint.setVisible(True)
                self._install_btn.setText(self.tr("Retry"))
                self._install_btn.setEnabled(True)
                self._skip_btn.setEnabled(True)

        def _format_error_hint(self, code):
            """Return a translated, user-friendly error message for *code*."""
            if code.startswith("pip_failed:"):
                pkg = code.split(":", 1)[1]
                return self.tr(
                    'Failed to install Python package "{pkg}".\nCheck the log above for details.'
                ).format(pkg=pkg)
            return {
                "network_hf_no_mirror": self.tr(
                    "Download failed — HuggingFace is not accessible from your network.\nGo to Settings → Mirror Config, set hf_endpoint to https://hf-mirror.com,\nthen click Retry."
                ),
                "network_hf": self.tr(
                    "Download failed — HuggingFace may be blocked in your region.\nGo to Settings → Mirror Config to configure a mirror, then Retry."
                ),
                "network_github": self.tr(
                    "Download failed — GitHub may not be reachable.\nGo to Settings → Mirror Config to set up a mirror, then Retry."
                ),
                "network_other": self.tr(
                    "Download failed — check your network connection.\nIf you are in a restricted region, try setting up a download mirror\nin Settings → Mirror Config, then click Retry."
                ),
            }.get(code, "")

    dlg = _InstallDialog(
        mod_name,
        missing_pkgs,
        missing_model_labels,
        dfl,  # raw download_file_list for the worker
        parent_widget,
    )
    return dlg.exec() == QDialog.DialogCode.Accepted and dlg._installed


class ModuleManager(QObject):
    imgtrans_proj: ProjImgTrans = None

    finish_translate_page = Signal(str)
    canvas_inpaint_finished = Signal(dict)
    inpaint_th_finished = Signal()

    imgtrans_pipeline_finished = Signal()
    blktrans_pipeline_finished = Signal(int, list)
    page_trans_finished = Signal(int)

    run_canvas_inpaint = False
    is_waiting_th = False
    block_set_inpainter = False

    def __init__(self, imgtrans_proj: ProjImgTrans, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.imgtrans_proj = imgtrans_proj
        self.check_inpaint_fin_timer = QTimer(self)
        self.check_inpaint_fin_timer.timeout.connect(self.check_inpaint_th_finished)

    def setupThread(
        self,
        config_panel: ConfigPanel,
        imgtrans_progress_msgbox: ImgtransProgressMessageBox,
    ):
        self.config_panel = config_panel
        self.textdetect_thread = TextDetectThread()

        self.ocr_thread = OCRThread()

        self.translate_thread = TranslateThread()
        self.translate_thread.progress_changed.connect(
            self.on_update_translate_progress
        )
        self.translate_thread.finish_translate_page.connect(
            self.on_finish_translate_page
        )

        self.inpaint_thread = InpaintThread()
        self.inpaint_thread.finish_inpaint.connect(self.on_finish_inpaint)

        # ── Module preparation progress dialog ─────────────────────
        self.prepare_msgbox = ProgressMessageBox(
            "", True, None
        )
        self.prepare_msgbox.stop_clicked.connect(self.cancelModulePreparation)

        for module_thread in [
            self.textdetect_thread,
            self.ocr_thread,
            self.translate_thread,
            self.inpaint_thread,
        ]:
            module_thread.module_prepare_progress.connect(
                self.on_module_prepare_progress
            )
            module_thread.finish_set_module.connect(
                lambda th=module_thread: self.on_module_prepare_finished(th)
            )

        self.progress_msgbox = imgtrans_progress_msgbox
        self.progress_msgbox.stop_clicked.connect(self.stopImgtransPipeline)

        self.imgtrans_thread = ImgtransThread(
            self.textdetect_thread,
            self.ocr_thread,
            self.translate_thread,
            self.inpaint_thread,
        )
        self.imgtrans_thread.update_detect_progress.connect(
            self.on_update_detect_progress
        )
        self.imgtrans_thread.update_ocr_progress.connect(self.on_update_ocr_progress)
        self.imgtrans_thread.update_translate_progress.connect(
            self.on_update_translate_progress
        )
        self.imgtrans_thread.update_inpaint_progress.connect(
            self.on_update_inpaint_progress
        )
        self.imgtrans_thread.finish_blktrans_stage.connect(
            self.on_finish_blktrans_stage
        )
        self.imgtrans_thread.finish_blktrans.connect(self.on_finish_blktrans)
        self.imgtrans_thread.pipeline_stopped.connect(self.on_imgtrans_thread_stopped)

        self.translator_panel = translator_panel = config_panel.trans_config_panel
        translator_params = merge_config_module_params(
            cfg_module.translator_params, GET_VALID_TRANSLATORS(), TRANSLATORS.get
        )
        translator_panel.addModulesParamWidgets(
            translator_params, _build_dep_notes(TRANSLATORS)
        )
        translator_panel.translator_changed.connect(self.setTranslator)
        translator_panel.paramwidget_edited.connect(self.on_translatorparam_edited)
        from modules.translators.hooks import chs2cht

        BaseTranslator.register_postprocess_hooks({"chs2cht": chs2cht})

        self.inpaint_panel = inpainter_panel = config_panel.inpaint_config_panel
        inpainter_params = merge_config_module_params(
            cfg_module.inpainter_params, GET_VALID_INPAINTERS(), INPAINTERS.get
        )
        inpainter_panel.addModulesParamWidgets(
            inpainter_params, _build_dep_notes(INPAINTERS)
        )
        inpainter_panel.paramwidget_edited.connect(self.on_inpainterparam_edited)
        inpainter_panel.inpainter_changed.connect(self.setInpainter)
        inpainter_panel.needInpaintChecker.checker_changed.connect(
            self.on_inpainter_checker_changed
        )
        inpainter_panel.needInpaintChecker.checker.setChecked(
            cfg_module.check_need_inpaint
        )

        self.textdetect_panel = textdetector_panel = config_panel.detect_config_panel
        textdetector_params = merge_config_module_params(
            cfg_module.textdetector_params, GET_VALID_TEXTDETECTORS(), TEXTDETECTORS.get
        )
        textdetector_panel.addModulesParamWidgets(
            textdetector_params, _build_dep_notes(TEXTDETECTORS)
        )
        textdetector_panel.paramwidget_edited.connect(self.on_textdetectorparam_edited)
        textdetector_panel.detector_changed.connect(self.setTextDetector)

        self.ocr_panel = ocr_panel = config_panel.ocr_config_panel
        ocr_params = merge_config_module_params(
            cfg_module.ocr_params, GET_VALID_OCR(), OCR.get
        )
        # Populate vision profile options for LLM OCR
        from utils.profile_manager import get_vision_profile_names

        for mod_key in ("llm_ocr",):
            if mod_key in ocr_params and isinstance(ocr_params[mod_key], dict):
                profile_cfg = ocr_params[mod_key].get("profile")
                if isinstance(profile_cfg, dict):
                    profile_cfg["options"] = get_vision_profile_names()
        ocr_panel.addModulesParamWidgets(ocr_params, _build_dep_notes(OCR))
        ocr_panel.paramwidget_edited.connect(self.on_ocrparam_edited)
        ocr_panel.ocr_changed.connect(self.setOCR)
        config_panel.profiles_changed.connect(self._on_profiles_changed)
        config_panel.unload_models.connect(self.unload_all_models)

    def unload_all_models(self):
        unload_modules(self, {"textdetector", "inpainter", "ocr", "translator"})

    def on_module_prepare_progress(self, payload: dict):
        """Update prepare progress dialog with current step."""
        event = payload.get("event", "")
        message = payload.get("message", "")
        module_key = payload.get("module_key", "")
        module_name = payload.get("module", "")
        if event == "installing_packages":
            self.prepare_msgbox.updateTaskProgress(0, 
                self.tr("Installing packages for {module}...").format(module=module_name or module_key)
            )
        elif event == "checking_dependencies":
            self.prepare_msgbox.updateTaskProgress(0, 
                self.tr("Checking dependencies for {module}...").format(module=module_name or module_key)
            )
        elif event == "downloading":
            self.prepare_msgbox.updateTaskProgress(0, 
                self.tr("Downloading files for {module}...").format(module=module_name or module_key)
            )
        elif event == "importing":
            self.prepare_msgbox.updateTaskProgress(0, 
                self.tr("Importing {module}...").format(module=module_name or module_key)
            )
        elif event == "loading_model":
            self.prepare_msgbox.updateTaskProgress(0, 
                self.tr("Loading model for {module}...").format(module=module_name or module_key)
            )
        else:
            self.prepare_msgbox.updateTaskProgress(0, message or module_name or module_key)

    def on_module_prepare_finished(self, thread: ModuleThread):
        """Check if thread succeeded; close progress if done, show error otherwise."""
        self.prepare_msgbox.hide()
        if not thread.last_set_success and thread.last_error is not None:
            LOGGER.error(
                f"Failed to set {thread.module_key} module '{thread.last_set_module_name}': {thread.last_error}"
            )

    def cancelModulePreparation(self):
        """Cancel any in-progress module preparation."""
        for thread in [
            self.textdetect_thread,
            self.ocr_thread,
            self.translate_thread,
            self.inpaint_thread,
        ]:
            if thread.isRunning():
                thread.requestCancelModuleInit()
        self.prepare_msgbox.hide()

    @property
    def translator(self) -> BaseTranslator:
        return self.translate_thread.translator

    @property
    def inpainter(self) -> InpainterBase:
        return self.inpaint_thread.inpainter

    @property
    def textdetector(self) -> TextDetectorBase:
        return self.textdetect_thread.textdetector

    @property
    def ocr(self) -> OCRBase:
        return self.ocr_thread.ocr

    def translatePage(self, run_target: bool, page_key: str):
        if not run_target:
            if self.translate_thread.isRunning():
                LOGGER.warning("Terminating a running translation thread.")
                self.translate_thread.terminate()
            return
        self.translate_thread.translatePage(self.imgtrans_proj.pages, page_key)

    def inpainterBusy(self):
        return self.inpaint_thread.isRunning()

    def inpaint(
        self,
        img: np.ndarray,
        mask: np.ndarray,
        img_key: str = None,
        inpaint_rect=None,
        **kwargs,
    ):
        if self.inpaint_thread.isRunning():
            LOGGER.warning("Waiting for inpainting to finish")
            return
        self.inpaint_thread.inpaint(img, mask, img_key, inpaint_rect)

    def terminateRunningThread(self):
        if self.textdetect_thread.isRunning():
            self.textdetect_thread.quit()
        if self.ocr_thread.isRunning():
            self.ocr_thread.quit()
        if self.inpaint_thread.isRunning():
            self.inpaint_thread.quit()
        if self.translate_thread.isRunning():
            self.translate_thread.quit()

    def check_inpaint_th_finished(self):
        if self.inpaint_thread.isRunning():
            return
        self.block_set_inpainter = False
        self.check_inpaint_fin_timer.stop()
        self.inpaint_th_finished.emit()

    def runImgtransPipeline(self, pages_to_process=None):
        if self.imgtrans_proj.is_empty:
            LOGGER.info("proj file is empty, nothing to do")
            self.progress_msgbox.hide()
            return
        self.last_finished_index = -1
        self.terminateRunningThread()

        if (
            cfg_module.all_stages_disabled()
            and self.imgtrans_proj is not None
            and self.imgtrans_proj.num_pages > 0
        ):
            for ii in range(self.imgtrans_proj.num_pages):
                self.page_trans_finished.emit(ii)
            self.imgtrans_pipeline_finished.emit()
            return

        self.progress_msgbox.detect_bar.setVisible(cfg_module.enable_detect)
        self.progress_msgbox.ocr_bar.setVisible(cfg_module.enable_ocr)
        self.progress_msgbox.translate_bar.setVisible(cfg_module.enable_translate)
        self.progress_msgbox.inpaint_bar.setVisible(cfg_module.enable_inpaint)
        self.progress_msgbox.zero_progress()
        self.progress_msgbox.show()
        self.imgtrans_thread.runImgtransPipeline(self.imgtrans_proj, pages_to_process)

    def stopImgtransPipeline(self):
        """Stop image translation pipeline"""
        LOGGER.info("Stopping image translation pipeline...")
        self.imgtrans_thread.requestStop()

    def runBlktransPipeline(
        self,
        blk_list: List[TextBlock],
        tgt_img: np.ndarray,
        mode: int,
        blk_ids: List[int],
        tgt_mask,
    ):
        self.terminateRunningThread()
        self.progress_msgbox.hide_all_bars()
        if mode >= 0 and mode < 3:
            self.progress_msgbox.ocr_bar.show()
        if mode >= 2:
            self.progress_msgbox.inpaint_bar.show()
        if mode != 0 and mode < 3:
            self.progress_msgbox.translate_bar.show()
        self.progress_msgbox.zero_progress()
        self.progress_msgbox.show()
        self.imgtrans_thread.runBlktransPipeline(
            blk_list, tgt_img, mode, blk_ids, tgt_mask
        )

    def on_finish_blktrans_stage(self, stage: str, progress: int):
        if stage == "ocr":
            self.progress_msgbox.updateOCRProgress(progress)
        elif stage == "translate":
            self.progress_msgbox.updateTranslateProgress(progress)
        elif stage == "inpaint":
            self.progress_msgbox.updateInpaintProgress(progress)
        else:
            raise NotImplementedError(f"Unknown stage: {stage}")

    def on_finish_blktrans(self, mode: int, blk_ids: List):
        self.blktrans_pipeline_finished.emit(mode, blk_ids)
        self.progress_msgbox.hide()

    def on_update_detect_progress(self, progress: int):
        ri = self.imgtrans_thread.recent_finished_index(progress)
        if "detect" in shared.pbar:
            shared.pbar["detect"].update(1)
        progress = int(progress / self.imgtrans_thread.num_pages * 100)
        self.progress_msgbox.updateDetectProgress(progress)
        if ri != self.last_finished_index:
            self.last_finished_index = ri
            self.page_trans_finished.emit(ri)
        if progress == 100:
            self.finishImgtransPipeline()

    def on_update_ocr_progress(self, progress: int):
        ri = self.imgtrans_thread.recent_finished_index(progress)
        if "ocr" in shared.pbar:
            shared.pbar["ocr"].update(1)
        progress = int(progress / self.imgtrans_thread.num_pages * 100)
        self.progress_msgbox.updateOCRProgress(progress)
        if ri != self.last_finished_index:
            self.last_finished_index = ri
            self.page_trans_finished.emit(ri)
        if progress == 100:
            self.finishImgtransPipeline()

    def on_update_translate_progress(self, progress: int):
        ri = self.imgtrans_thread.recent_finished_index(progress)
        if "translate" in shared.pbar:
            shared.pbar["translate"].update(1)
        progress = int(progress / self.imgtrans_thread.num_pages * 100)
        self.progress_msgbox.updateTranslateProgress(progress)
        if ri != self.last_finished_index:
            self.last_finished_index = ri
            self.page_trans_finished.emit(ri)
        if progress == 100:
            self.finishImgtransPipeline()

    def on_update_inpaint_progress(self, progress: int):
        ri = self.imgtrans_thread.recent_finished_index(progress)
        if "inpaint" in shared.pbar:
            shared.pbar["inpaint"].update(1)
        progress = int(progress / self.imgtrans_thread.num_pages * 100)
        self.progress_msgbox.updateInpaintProgress(progress)
        if ri != self.last_finished_index:
            self.last_finished_index = ri
            self.page_trans_finished.emit(ri)
        if progress == 100:
            self.finishImgtransPipeline()

    def progress(self):
        progress = {}
        num_pages = self.imgtrans_thread.num_pages
        if cfg_module.enable_detect:
            progress["detect"] = self.imgtrans_thread.detect_counter / num_pages
        if cfg_module.enable_ocr:
            progress["ocr"] = self.imgtrans_thread.ocr_counter / num_pages
        if cfg_module.enable_inpaint:
            progress["inpaint"] = self.imgtrans_thread.inpaint_counter / num_pages
        if cfg_module.enable_translate:
            progress["translate"] = self.imgtrans_thread.translate_counter / num_pages
        return progress

    def proj_finished(self):
        if (
            self.imgtrans_thread.detect_finished()
            and self.imgtrans_thread.ocr_finished()
            and self.imgtrans_thread.translate_finished()
            and self.imgtrans_thread.inpaint_finished()
        ):
            return True
        return False

    def finishImgtransPipeline(self):
        if self.proj_finished():
            self.progress_msgbox.hide()
            self.imgtrans_pipeline_finished.emit()

    def on_imgtrans_thread_stopped(self):
        """Thread finished [] ensure progress dialog is closed"""
        # 线程完成了，直接关闭窗口
        self.progress_msgbox.hide()
        self.imgtrans_pipeline_finished.emit()

    def setTranslator(self, translator: str = None):
        if translator is None:
            translator = cfg_module.translator
        cls = TRANSLATORS.get(translator)
        if cls and not _ensure_module_deps(cls, self.parent()):
            if self.translator is not None:
                self.config_panel.trans_config_panel.setModule(
                    self.translator.name
                )
            return
        if self.translate_thread.isRunning():
            LOGGER.warning("Terminating a running translation thread.")
            self.translate_thread.terminate()
        self.prepare_msgbox.updateTaskProgress(0, 
            self.tr("Preparing module: {module}...").format(module=translator)
        )
        self.prepare_msgbox.show()
        self.translate_thread.setTranslator(translator)

    def setInpainter(self, inpainter: str = None):

        if self.block_set_inpainter:
            return

        if inpainter is None:
            inpainter = cfg_module.inpainter

        if self.inpaint_thread.isRunning():
            self.block_set_inpainter = True
            create_info_dialog(
                self.tr("Set Inpainter..."),
                modal=True,
                signal_slot_map_list=[
                    {"signal": self.inpaint_th_finished, "slot": "done"}
                ],
            )
            self.check_inpaint_fin_timer.start(300)
            return

        cls = INPAINTERS.get(inpainter)
        if cls and not _ensure_module_deps(cls, self.parent()):
            if self.inpainter is not None:
                self.config_panel.inpaint_config_panel.setModule(
                    self.inpainter.name
                )
            return

        self.prepare_msgbox.updateTaskProgress(0, 
            self.tr("Preparing module: {module}...").format(module=inpainter)
        )
        self.prepare_msgbox.show()
        self.inpaint_thread.setInpainter(inpainter)

    def setTextDetector(self, textdetector: str = None):
        if textdetector is None:
            textdetector = cfg_module.textdetector
        cls = TEXTDETECTORS.get(textdetector)
        if cls and not _ensure_module_deps(cls, self.parent()):
            if self.textdetector is not None:
                self.config_panel.detect_config_panel.setModule(
                    self.textdetector.name
                )
            return
        # Refresh param widget after dep install so dynamic model lists
        # (e.g. ysgyolo's CKPT_LIST) are picked up.
        self._refresh_module_widget(
            self.config_panel.detect_config_panel, textdetector, cls
        )
        if self.textdetect_thread.isRunning():
            LOGGER.warning("Terminating a running text detection thread.")
            self.textdetect_thread.terminate()
        self.prepare_msgbox.updateTaskProgress(0, 
            self.tr("Preparing module: {module}...").format(module=textdetector)
        )
        self.prepare_msgbox.show()
        self.textdetect_thread.setTextDetector(textdetector)

    def setOCR(self, ocr: str = None):
        if ocr is None:
            ocr = cfg_module.ocr
        cls = OCR.get(ocr)
        if cls and not _ensure_module_deps(cls, self.parent()):
            if self.ocr is not None:
                self.config_panel.ocr_config_panel.setModule(self.ocr.name)
            return
        if self.ocr_thread.isRunning():
            LOGGER.warning("Terminating a running OCR thread.")
            self.ocr_thread.terminate()
        self.prepare_msgbox.updateTaskProgress(0, 
            self.tr("Preparing module: {module}...").format(module=ocr)
        )
        self.prepare_msgbox.show()
        self.ocr_thread.setOCR(ocr)

    def on_finish_translate_page(self, page_key: str):
        self.finish_translate_page.emit(page_key)

    def on_finish_inpaint(self, inpaint_dict: dict):
        if self.run_canvas_inpaint:
            self.canvas_inpaint_finished.emit(inpaint_dict)
            self.run_canvas_inpaint = False

    def canvas_inpaint(self, inpaint_dict):
        self.run_canvas_inpaint = True
        self.inpaint(**inpaint_dict)

    def on_translatorparam_edited(self, param_key: str, param_content: dict):
        if self.translator is not None:
            self.updateModuleSetupParam(self.translator, param_key, param_content)
            cfg_module.translator_params[self.translator.name] = self.translator.params

    def on_inpainterparam_edited(self, param_key: str, param_content: dict):
        if self.inpainter is not None:
            self.updateModuleSetupParam(self.inpainter, param_key, param_content)
            cfg_module.inpainter_params[self.inpainter.name] = self.inpainter.params

    def on_textdetectorparam_edited(self, param_key: str, param_content: dict):
        if self.textdetector is not None:
            self.updateModuleSetupParam(self.textdetector, param_key, param_content)
            cfg_module.textdetector_params[self.textdetector.name] = (
                self.textdetector.params
            )

    def on_ocrparam_edited(self, param_key: str, param_content: dict):
        if self.ocr is not None:
            self.updateModuleSetupParam(self.ocr, param_key, param_content)
            cfg_module.ocr_params[self.ocr.name] = self.ocr.params

    def _on_profiles_changed(self):
        """Refresh profile-dependent selectors after profiles are edited."""
        # Refresh OCR vision profile options (class-level params)
        from modules import OCR as _OCR
        from utils.profile_manager import get_profile_names, get_vision_profile_names

        ocr_cls = _OCR.module_dict.get("llm_ocr")
        if ocr_cls and hasattr(ocr_cls, "params"):
            profile_cfg = ocr_cls.params.get("profile")
            if isinstance(profile_cfg, dict):
                vision_names = get_vision_profile_names()
                profile_cfg["options"] = vision_names
                if profile_cfg.get("value", "") not in vision_names:
                    profile_cfg["value"] = vision_names[0] if vision_names else ""
        # Refresh translator active_profile options (class-level params)
        from modules import TRANSLATORS as _TRANS

        trans_cls = _TRANS.module_dict.get("LLM_API_Translator")
        if trans_cls and hasattr(trans_cls, "params"):
            active_cfg = trans_cls.params.get("active_profile")
            if isinstance(active_cfg, dict):
                all_names = get_profile_names()
                active_cfg["options"] = all_names
                if active_cfg.get("value", "") not in all_names:
                    active_cfg["value"] = all_names[0] if all_names else ""
        # Invalidate cached param widgets so they get rebuilt with new options
        for panel, module_key in [
            (self.config_panel.ocr_config_panel, "llm_ocr"),
            (self.config_panel.trans_config_panel, "LLM_API_Translator"),
        ]:
            if module_key in panel.param_widget_map:
                old_widget = panel.param_widget_map[module_key]
                if old_widget is not None:
                    old_widget.deleteLater()
                panel.param_widget_map[module_key] = None
        # Rebuild param widgets to reflect new options
        self.config_panel.ocr_config_panel.updateModuleParamWidget()
        self.config_panel.trans_config_panel.updateModuleParamWidget()

    def updateModuleSetupParam(
        self,
        module: Union[InpainterBase, BaseTranslator],
        param_key: str,
        param_content: dict,
    ):

        if param_content.get("flush", False):
            param_widget: ParamComboBox = param_content["widget"]
            result = module.flush(param_key)
            if result is None:
                return
            if not result:
                from qtpy.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self.parent(),
                    self.tr("Refresh failed"),
                    self.tr(
                        "Failed to fetch model list. Please check your API key and host configuration."
                    ),
                )
                return
            param_widget.blockSignals(True)
            current_item = param_widget.currentText()
            param_widget.clear()
            param_widget.addItems(result)
            param_widget.setCurrentText(current_item)
            param_widget.blockSignals(False)
        elif param_content.get("select_path", False):
            dialog = QFileDialog()
            f = module.params[param_key].get("path_filter", None)
            p = dialog.getOpenFileUrl(self.parent(), filter=f)[0].toLocalFile()
            if osp.exists(p):
                param_widget: ParamComboBox = param_content["widget"]
                param_widget.setCurrentText(p)
        else:
            module.updateParam(param_key, param_content["content"])

    def _refresh_module_widget(self, panel, module_name, cls):
        """Refresh a module's param widget after its dependencies were installed.

        Calls module-level refresh hooks (e.g. ``update_ckpt_list()`` for
        ysgyolo) then invalidates the cached ``ParamWidget`` so it is rebuilt
        with current options on next render.
        """
        # 1. Call module-level refresh hooks if they exist
        mod_name = getattr(cls, "__module__", None)
        if mod_name:
            try:
                py_mod = importlib.import_module(mod_name)
                if hasattr(py_mod, "update_ckpt_list"):
                    py_mod.update_ckpt_list()
            except Exception:
                pass
        # 2. Invalidate cached ParamWidget so it gets rebuilt
        if module_name in panel.param_widget_map:
            old = panel.param_widget_map[module_name]
            if old is not None:
                old.deleteLater()
            panel.param_widget_map[module_name] = None
        panel.updateModuleParamWidget()

    def handle_page_changed(self):
        if not self.imgtrans_thread.isRunning():
            if self.inpaint_thread.inpainting:
                self.run_canvas_inpaint = False
                self.inpaint_thread.terminate()

    def on_inpainter_checker_changed(self, is_checked: bool):
        cfg_module.check_need_inpaint = is_checked
        InpainterBase.check_need_inpaint = is_checked
