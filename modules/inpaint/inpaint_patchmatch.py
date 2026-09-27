"""PatchMatch 图像修复模块 — 基于 C 扩展的快速修复算法。

PatchMatch: A Randomized Correspondence Algorithm for Structural Image Editing
(C) Barnes, Shechtman, Finkelstein & Goldman, SIGGRAPH 2009

依赖 ``data/libs`` 下的原生库：``patchmatch_inpaint.dll``（Windows，该 DLL
还依赖同目录的 ``opencv_world455.dll``）／``libpatchmatch.so``（Linux）。
Windows 两个文件以 GitHub Release 资产分发（不进小包，保持 2-30MB 体积），
缺失时选中本模块即由后台下载补齐——声明见本类 ``download_file_list``。

三条边界别改坏：

- **不隐藏**：PatchMatch 是**非模型基础能力**（无 torch、无权重），用户必须
  能从 GUI 直接选到它。它不在 ``modules/__init__.py::HIDDEN_INPAINTERS`` 里
  （那个集合只留 ``LLMInpaint``）。
- **不该被 torch 降级**：基础包不带 torch，但 PatchMatch 不需要 torch——
  ``launch.py::_ensure_module_fallback`` 的"没 torch 就换 none"不能作用到它
  （换掉等于把本来能用的功能关死）。原生库缺口则走**正常下载清单**：选中即
  ``ui/model_downloads.py`` 后台下载，缺文件标识、运行前警告与「模型文件」页
  都认这份声明。
- **原生库惰性加载**：构造实例与选型扫描都不碰 DLL（缺附件不该影响启动）；
  真跑修复时才经 ``.patch_match`` 加载，缺库抛带提示语的
  ``modules/inpaint/patch_match.py::PatchMatchUnavailableError`` 而不是裸
  ``OSError``（用户要的是"去哪儿补齐"的指路，不是加载器错误码）。
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

    # 原生库＝Release 资产（发版时与小包一起上传，资产名＝文件名，
    # releases/latest/download 直链恒指向最新版）。选中本模块即后台下载；
    # 缺文件检查、「模型文件」页下载按钮与删除白名单共用这份声明。
    # sha256 钉死资产内容——DLL 不该漂移，漂了就按哈希不匹配强制重下。
    download_file_list = [
        {
            "url": "https://github.com/fclx512/BallonsTranslator-lite/releases/latest/download/patchmatch_inpaint.dll",
            "files": "data/libs/patchmatch_inpaint.dll",
            "sha256_pre_calculated": "0ba60cfe664c97629daa7e4d05c0888ebfe3edcb3feaf1ed5a14544079c6d7af",
        },
        {
            "url": "https://github.com/fclx512/BallonsTranslator-lite/releases/latest/download/opencv_world455.dll",
            "files": "data/libs/opencv_world455.dll",
            "sha256_pre_calculated": "3b7619caa29dc3352b939de4e9981217a9585a13a756e1101a50c90c100acd8d",
        },
    ]
    model_package = {
        "name": "PatchMatch",
        "size_hint": "53 MB",
    }

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
