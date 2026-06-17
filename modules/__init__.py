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


# TODO: use manga-image-translator as backend...
