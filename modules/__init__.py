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

GET_VALID_TEXTDETECTORS = lambda: list(TEXTDETECTORS.module_dict.keys())
GET_VALID_TRANSLATORS = lambda: list(TRANSLATORS.module_dict.keys())
GET_VALID_INPAINTERS = lambda: list(INPAINTERS.module_dict.keys())
GET_VALID_OCR = lambda: (
    [k for k in list(OCR.module_dict.keys()) if k != "none_ocr"] + ["none_ocr"]
)


MODULETYPE_TO_REGISTRIES = {
    "textdetector": TEXTDETECTORS,
    "ocr": OCR,
    "inpainter": INPAINTERS,
    "translator": TRANSLATORS,
}

# TODO: use manga-image-translator as backend...
