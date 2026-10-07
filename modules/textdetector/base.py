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

    # 颜色提取对「掩膜出处」不敏感：有概率图就用概率图（笔画级更准），
    # 没有就走经典二值化兜底。置信度闸门在检测器里，低于门槛不落块、
    # 让块保持原色由全局样式兜底。详见
    # docs/技术实现/字体自动取色_设计与实现.md。
    _COLOR_CONF_MIN = 0.45
    # 各检测器在 params 里自带同名开关（声明在子类的 params 字典，默认开）
    _COLOR_PARAM_KEY = "extract colors"

    def _color_extract_enabled(self) -> bool:
        """读本检测器的「提取颜色」开关（未声明该参数的检测器＝不取色）。

        不走 ``get_param_value``：它对未声明的键会 assert（``detector_none``
        就没这个参数）。
        """
        entry = (self.params or {}).get(self._COLOR_PARAM_KEY)
        value = entry.get("value") if isinstance(entry, dict) else entry
        return bool(value)

    def extract_colors(
        self,
        img: np.ndarray,
        blk_list: List[TextBlock],
        prob: np.ndarray = None,
        palette=None,
    ) -> None:
        """逐块提取字体前景色并写到块上（``utils/color_extract``）。

        只写前景色：``TextBlock.bg_colors`` 落在 ``FontFormat.srgb``，那是
        **描边色**视图（见 ``utils/fontformat.py::FontFormat``），写进去会把
        已有描边的块涂成背景色；提取出的背景色只随返回值给上层。

        palette（项目人工取样调板 ``utils/proj_imgtrans.py::ProjImgTrans`` 的 ``fg_palette`` 字段）
        非空时按最近原则吸附，见 ``utils/color_extract.py::PALETTE_SNAP_DIST``。
        """
        if not self._color_extract_enabled():
            return
        from utils.color_extract import extract_block_colors

        for blk in blk_list:
            try:
                res = extract_block_colors(img, blk.xyxy, prob=prob, palette=palette)
            except Exception as e:  # 单块取色失败不拖垮检测
                self.logger.warning("color extract failed: %s", e)
                continue
            if res is None or res.confidence < self._COLOR_CONF_MIN:
                continue
            blk.fg_colors = res.frgb

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
