from collections import OrderedDict
from typing import List, Tuple

import cv2
import numpy as np

from utils.proj_imgtrans import ProjImgTrans
from utils.registries import TEXTDETECTORS
from utils.textblock import TextBlock

from ..base import (
    DEFAULT_DEVICE,  # noqa: F401 — re-exported for detector modules
    DEVICE_SELECTOR,  # noqa: F401 — re-exported for detector modules
    BaseModule,
)

register_textdetectors = TEXTDETECTORS.register_module


class TextDetectorBase(BaseModule):
    _postprocess_hooks = OrderedDict()
    _preprocess_hooks = OrderedDict()

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.name = ""
        for key in TEXTDETECTORS.module_dict:
            if TEXTDETECTORS.module_dict[key] == self.__class__:
                self.name = key
                break

    def _detect(
        self, img: np.ndarray, proj: ProjImgTrans
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        """
        The proj context can be accessed via ```proj```
        """
        raise NotImplementedError

    def setup_detector(self):
        raise NotImplementedError

    def detect(
        self, img: np.ndarray, proj: ProjImgTrans = None
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        # TODO: allow processing proj entirely in _detect and yield progress
        if not self.all_model_loaded():
            self.load_model()

        # All text detectors only support 3 channels input
        if img.ndim == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)

        mask, blk_list = self._detect(img, proj)
        for blk in blk_list:
            blk.det_model = self.name
        return mask, blk_list
