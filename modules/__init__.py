import os
import os.path as osp

from utils.registries import (
    MODULETYPE_TO_REGISTRIES,  # noqa: E402 — single source of truth
)

from .base import (
    DEFAULT_DEVICE,
    GPUINTENSIVE_SET,
    LOGGER,
    init_inpainter_registries,
    init_module_registries,
    init_ocr_registries,
    init_textdetector_registries,
    init_translator_registries,
    merge_config_module_params,
)
from .inpaint import INPAINTERS, InpainterBase
from .ocr import OCR, OCRBase
from .textdetector import TEXTDETECTORS, TextDetectorBase
from .translators import TRANSLATORS, BaseTranslator


def GET_VALID_TEXTDETECTORS() -> list:
    return list(TEXTDETECTORS.module_dict.keys())


def GET_VALID_TRANSLATORS() -> list:
    return list(TRANSLATORS.module_dict.keys())


def GET_VALID_INPAINTERS() -> list:
    return list(INPAINTERS.module_dict.keys())


def GET_VALID_OCR() -> list:
    return list(OCR.module_dict.keys())


# 不进选型界面（运行对话框 / 底部栏）的修复器，注册保留：
# LLMInpaint 只在模块参数页配置；patchmatch 仅适合涂简单背景，
# 藏起来避免被当成常规修复模型选用。
HIDDEN_INPAINTERS = frozenset({"LLMInpaint", "patchmatch"})

# 模型文件与注册名解耦的模块：none 系无需模型；LLM 系依赖 Profile；
# ysgyolo 权重不走 download_file_list（手工/HF 下载放进 data/models）。
_NO_DOWNLOAD_LIST_KEYS = frozenset(
    {"none", "none_ocr", "None", "llm_ocr", "LLMInpaint",
     "LLM_API_Translator", "LLM_Agent_Translator", "patchmatch"}
)


def GET_MISSING_MODEL_FILES(module_type: str, key: str):
    """静态检查模块的模型文件是否在磁盘上（只读注册表元数据，不导入模块体）。

    返回缺失文件路径列表（相对程序根目录，每个 download 条目一行）；
    空列表＝齐备；``None``＝无法静态判定（none 系／LLM profile 类等无模型模块）。
    供选型 UI 的状态标识与运行前检查共用，与
    ui/module_manager.py::_ensure_module_deps 的落盘判据同源（save_files 优先）。
    """
    registry = MODULETYPE_TO_REGISTRIES.get(module_type)
    spec = registry.get(key) if registry is not None else None
    if spec is None:
        return None
    if key in _NO_DOWNLOAD_LIST_KEYS:
        return None
    if key == "ysgyolo":
        model_dir = osp.join(osp.dirname(osp.dirname(osp.abspath(__file__))), "data", "models")
        if osp.isdir(model_dir) and any(
            name.startswith(("ysgyolo", "ultralyticsyolo"))
            for name in os.listdir(model_dir)
        ):
            return []
        return ["data/models/ysgyolo_*.pt"]
    from utils.shared import PROGRAM_PATH

    missing = []
    for entry in getattr(spec, "download_file_list", None) or []:
        paths = entry.get("save_files") or entry.get("files") or []
        if isinstance(paths, str):
            paths = [paths]
        for fpath in paths:
            full = fpath if osp.isabs(fpath) else osp.join(PROGRAM_PATH, fpath)
            if not osp.exists(full):
                missing.append(fpath)
                break
    return missing


# TODO: use manga-image-translator as backend...
