"""PatchMatch 图像修复模块 — 基于 C 扩展的快速修复算法。

PatchMatch: A Randomized Correspondence Algorithm for Structural Image Editing
(C) Barnes, Shechtman, Finkelstein & Goldman, SIGGRAPH 2009

依赖随包携带的原生库：``data/libs/patchmatch_inpaint.dll``（Windows，该 DLL
还依赖同目录的 ``opencv_world455.dll``）／``libpatchmatch.so``（Linux）。

三条边界别改坏：

- **不隐藏**：PatchMatch 是精简包随包携带的**非模型基础能力**（无 torch、无
  权重、无下载），精简包用户必须能从 GUI 直接选到它。它不在
  ``modules/__init__.py::HIDDEN_INPAINTERS`` 里（那个集合只留 ``LLMInpaint``）。
- **不该被 torch 降级**：基础包不带 torch，但 PatchMatch 不需要 torch——
  ``launch.py::_ensure_module_fallback`` 的"没 torch 就换 none"不能作用到它
  （换掉等于把本来能用的功能关死）。它也不进模型下载清单
  （``modules/__init__.py`` 的 ``_NO_DOWNLOAD_LIST_KEYS``）。
- **原生库惰性加载**：构造实例与选型扫描都不碰 DLL（缺附件不该影响启动）；
  真跑修复时才经 ``.patch_match`` 加载，缺库抛带提示语的
  ``modules/inpaint/patch_match.py::PatchMatchUnavailableError`` 而不是裸
  ``OSError``（用户要的是"重新解压精简包"，不是加载器错误码）。
"""

from typing import List, Tuple

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

    逐块能力：``inpaint_by_block`` 继承基类的 True，因此它也能当
    ``ui/batch_inpaint.py::BatchSimpleInpaint`` 的载体——那个任务只走逐块
    路径的判据与纯色覆盖（``only_simple=True``：简单块纯色覆盖、复杂块完全
    不动），连原生库和模型都不会碰。
    """

    def __init__(self, **params) -> None:
        super().__init__(**params)
        from . import patch_match

        # 只取函数引用，不加载原生库（缺 data/libs 附件时构造仍成功）
        self.inpaint_method = lambda img, mask, *args, **kwargs: patch_match.inpaint(
            img, mask, patch_size=3
        )

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        return self.inpaint_method(img, mask)

    @staticmethod
    def native_lib_status() -> Tuple[bool, str]:
        """原生库可用性 ``(可用, 可读原因)``；原因可直接展示给用户。

        只查文件在不在（默认不加载 DLL），可安全地用于选型 UI 与运行前检查。
        """
        from . import patch_match

        return patch_match.native_lib_status()

    def is_computational_intensive(self) -> bool:
        return True

    def is_cpu_intensive(self) -> bool:
        return True

    def moveToDevice(self, device: str, precision: str = None):
        pass
