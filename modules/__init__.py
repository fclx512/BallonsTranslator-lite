import importlib
import os
import os.path as osp

from utils.registries import (
    MODULETYPE_TO_REGISTRIES,  # noqa: E402 — single source of truth
)

from .base import (
    DEFAULT_DEVICE,
    GPUINTENSIVE_SET,
    LOGGER,
    MissingModelFilesError,
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
# patchmatch 走 OpenCV 内置算法。（ysgyolo 有自己的 download_file_list，
# 但落盘文件名允许飘，故 GET_MISSING_MODEL_FILES 对它单独按通配判断。）
_NO_DOWNLOAD_LIST_KEYS = frozenset(
    {"none", "none_ocr", "None", "llm_ocr", "LLMInpaint",
     "LLM_API_Translator", "LLM_Agent_Translator", "patchmatch"}
)


def GET_MISSING_MODEL_FILES(module_type: str, key: str):
    """静态检查模块的模型文件是否在磁盘上（只读注册表元数据，不导入模块体）。

    返回缺失文件路径列表（相对程序根目录，每个 download 条目一行）；
    空列表＝齐备；``None``＝无法静态判定（none 系／LLM profile 类等无模型模块）。
    ``ysgyolo`` 例外：它的 ``_load_model`` 接受 ``data/models/`` 下任意
    ``ysgyolo*`` 权重，故按通配判断而不看声明文件名。

    落盘判据与 ``utils/model_files.py::missing_declared_files`` 同源
    （save_files 优先，回落 files），供选型 UI 的状态标识、运行前检查与
    「模型文件」页共用。
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

    from utils.model_files import missing_declared_files

    return missing_declared_files(getattr(spec, "download_file_list", None))


# 管线阶段顺序，「模型文件」页按它列出各阶段的权重包
_PACKAGE_STAGE_ORDER = ("textdetector", "ocr", "translator", "inpainter")


def GET_MISSING_PACKAGES(module_type: str, key: str) -> list:
    """静态检查模块声明的 pip 依赖哪些没装（只读注册表元数据，不导入模块体）。

    返回缺失的 PEP 508 requirement 字符串列表。元数据名对不上时（典型如
    ``onnxruntime`` 装成 ``onnxruntime-gpu``）退回直接 import 探测。
    """
    registry = MODULETYPE_TO_REGISTRIES.get(module_type)
    spec = registry.get(key) if registry is not None else None
    if spec is None:
        return []
    # 惰性 spec 与真实类都声明 requires_packages；dependencies 是旧名字，
    # 只在个别模块里残留，作回落读一次。
    pkgs = getattr(spec, "requires_packages", None) or getattr(
        spec, "dependencies", None
    ) or []
    if not pkgs:
        return []
    try:
        import importlib.metadata as importlib_metadata

        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
    except ImportError:
        return list(pkgs)

    missing = []
    for req_str in pkgs:
        try:
            req = Requirement(req_str)
        except Exception:
            continue
        try:
            dist = importlib_metadata.distribution(canonicalize_name(req.name))
            if not req.specifier.contains(dist.version, prereleases=True):
                missing.append(req_str)
        except importlib_metadata.PackageNotFoundError:
            try:
                importlib.import_module(req.name)
            except ImportError:
                missing.append(req_str)
    return missing


def GET_MODULE_REQUIREMENTS(module_type: str, key: str) -> dict:
    """汇总一个模块的模型文件现状与依赖缺口（面板行与下载任务共用的唯一采集口）。

    返回 ``None`` 表示该模块无本地权重（none 系 / LLM Profile 系 / 未注册）。
    字段见 :func:`GET_MODEL_PACKAGES`，另加 ``missing_packages`` 与
    ``download_file_list``（下载任务直接拿去 ``download_and_check_files(**entry)``）。
    """
    from utils.model_files import (
        declared_save_files,
        package_dir_of,
        package_size_bytes,
    )

    registry = MODULETYPE_TO_REGISTRIES.get(module_type)
    spec = registry.get(key) if registry is not None else None
    if spec is None:
        return None
    dfl = getattr(spec, "download_file_list", None) or []
    if not dfl:
        return None

    model_package = getattr(spec, "model_package", None)
    display_name = key
    size_hint = None
    if isinstance(model_package, dict):
        display_name = model_package.get("name") or key
        size_hint = model_package.get("size_hint")
    return {
        "module_type": module_type,
        "key": key,
        "display_name": display_name,
        "package_dir": package_dir_of(model_package),
        "size_hint": size_hint,
        "size_bytes": package_size_bytes(model_package, dfl),
        "missing": GET_MISSING_MODEL_FILES(module_type, key) or [],
        "files": declared_save_files(dfl),
        "missing_packages": GET_MISSING_PACKAGES(module_type, key),
        "requires_gpu": bool(getattr(spec, "requires_gpu", False)),
        "download_file_list": dfl,
    }


def GET_MODEL_PACKAGES() -> list:
    """列出所有「带本地权重」的模块，供设置页 Models →「模型文件」渲染。

    每项为 :func:`GET_MODULE_REQUIREMENTS` 的返回值：

    ================== ====================================================
    ``module_type``    阶段键（textdetector / ocr / translator / inpainter）
    ``key``            注册名
    ``display_name``   显示名：``model_package["name"]`` 优先，回落注册名
    ``package_dir``    包根目录（相对程序根）或 ``None``＝文件型包
    ``size_hint``      预期体积文本或 ``None``（未声明则不提示）
    ``size_bytes``     当前实际占用（未装为 0）
    ``missing``        缺失文件列表（齐备为 ``[]``）
    ``files``          声明的落盘路径＝**删除白名单**（不含 ``dir``，绝不按目录删）
    ``missing_packages`` 缺失的 pip 依赖
    ``requires_gpu``   模块是否要求加速设备（本机没有时下载入口拒绝）
    ``download_file_list`` 原始下载清单（仅供执行器使用）
    ================== ====================================================

    只读注册表元数据、不导入模块体；按阶段顺序输出，阶段内按注册顺序。
    """
    packages = []
    for module_type in _PACKAGE_STAGE_ORDER:
        registry = MODULETYPE_TO_REGISTRIES.get(module_type)
        if registry is None:
            continue
        for key in registry.module_dict:
            info = GET_MODULE_REQUIREMENTS(module_type, key)
            if info is not None:
                packages.append(info)
    return packages


# TODO: use manga-image-translator as backend...
