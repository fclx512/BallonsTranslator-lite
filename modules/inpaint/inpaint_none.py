"""Skip inpainting module — passes image through unchanged.
注册名 ``none``，用于无模型运行模式。
"""

import numpy as np

from modules.inpaint.base import (
    InpainterBase,
    List,
    TextBlock,
    register_inpainter,
)


@register_inpainter("none")
class InpainterNone(InpainterBase):
    inpaint_by_block = False
    check_need_inpaint = False

    params = {
        "description": "Skip inpainting. No model needed.",
    }

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        return img

    def moveToDevice(self, device: str, precision: str = None):
        pass
