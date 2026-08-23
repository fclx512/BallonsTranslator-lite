"""Shadow compositing shared by the effect renderer and gradient preview.

Port of upstream v1.5.9 ``text_engine/rendering/shadow.py``.  The local
``ui/text_graphical_effect.py`` ``apply_shadow_effect`` was functionally
identical; this module is the single upstream-aligned implementation and
``ui/text_style_dock.py`` reuses it for the rail dock's shadow preview.
"""

from __future__ import annotations

from typing import Tuple, Union

import cv2
import numpy as np
from qtpy.QtGui import QColor, QPixmap, QImage

from ui.misc import pixmap2ndarray, ndarray2pixmap


def apply_shadow_effect(
    img: Union[QPixmap, QImage, np.ndarray],
    color: QColor,
    strength: float = 1.0,
    radius: int = 21,
) -> Tuple[QPixmap, np.ndarray]:
    if isinstance(color, QColor):
        color = [color.red(), color.green(), color.blue()]

    if not isinstance(img, np.ndarray):
        img = pixmap2ndarray(img, keep_alpha=True)

    mask = img[..., -1].copy()
    ksize = radius * 2 + 1
    mask = cv2.GaussianBlur(mask, (ksize, ksize), ksize / 6)
    if strength != 1:
        mask = np.clip(mask.astype(np.float32) * strength, 0, 255).astype(np.uint8)
    bg_img = np.zeros((img.shape[0], img.shape[1], 4), dtype=np.uint8)
    bg_img[..., :3] = np.array(color, np.uint8)
    bg_img[..., 3] = mask

    result = ndarray2pixmap(bg_img)
    return result, img
