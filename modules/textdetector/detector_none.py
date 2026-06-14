"""Skip text detection module — returns empty mask and block list.
注册名 ``none``，用于无模型运行模式。
"""

import numpy as np

from modules.textdetector.base import (
    List,
    ProjImgTrans,
    TextBlock,
    TextDetectorBase,
    Tuple,
    register_textdetectors,
)


@register_textdetectors("none")
class TextDetectorNone(TextDetectorBase):
    params = {
        "description": "Skip text detection. No model needed.",
    }

    def _detect(
        self, img: np.ndarray, proj: ProjImgTrans
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        return np.zeros(img.shape[:2], dtype=np.uint8), []

    def setup_detector(self):
        pass
