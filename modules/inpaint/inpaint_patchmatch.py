"""PatchMatch 图像修复模块 — 基于 C 扩展的快速修复算法。

PatchMatch: A Randomized Correspondence Algorithm for Structural Image Editing
(C) Barnes, Shechtman, Finkelstein & Goldman, SIGGRAPH 2009

依赖 data/libs/patchmatch_inpaint.dll (Windows) / libpatchmatch.so (Linux)
"""

from typing import List

import numpy as np

from modules.inpaint.base import (
    InpainterBase,
    TextBlock,
    register_inpainter,
)


@register_inpainter("patchmatch")
class PatchmatchInpainter(InpainterBase):
    """PatchMatch 非学习式图像修复器。

    基于像素块匹配的快速修复，无需 GPU / 模型加载。
    ``patch_size=3`` 适用于漫画文字擦除场景。
    """

    def __init__(self, **params) -> None:
        super().__init__(**params)
        from . import patch_match

        self.inpaint_method = lambda img, mask, *args, **kwargs: patch_match.inpaint(
            img, mask, patch_size=3
        )

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        return self.inpaint_method(img, mask)

    def is_computational_intensive(self) -> bool:
        return True

    def is_cpu_intensive(self) -> bool:
        return True

    def moveToDevice(self, device: str, precision: str = None):
        pass
