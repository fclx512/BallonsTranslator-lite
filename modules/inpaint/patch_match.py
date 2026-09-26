#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# File   : patch_match.py
# Author : Jiayuan Mao
# Email  : maojiayuan@gmail.com
# Date   : 01/09/2020
#
# Distributed under terms of the MIT license.
"""PyPatchMatch 的 ctypes 绑定。

本 fork 只改了**原生库的加载时机**（2026-09-26，精简包发行）：上游在模块导入期
就 ``ctypes.CDLL(...)``，精简包缺 ``data/libs`` 附件时导入这个模块直接抛裸
``OSError``——选型扫描、注册表刷新只要碰到它就炸，且错误只有
``Could not find module ... (or one of its dependencies)``，指不出真正缺哪个文件
（Windows 上 ``patchmatch_inpaint.dll`` 依赖 ``opencv_world455.dll``，缺的是后者时
报的却是前者）。

现在导入本模块永远安全（不碰 DLL），缺库的分辨交给 :func:`native_lib_status`
（可读原因，不抛异常）与 :func:`load_native_lib`（抛
:class:`PatchMatchUnavailableError`）。算法与函数签名与上游一致。
"""

import ctypes
import os.path as osp
import sys
from glob import glob
from typing import List, Optional, Tuple, Union

import numpy as np
from PIL import Image


__all__ = [
    'set_random_seed',
    'set_verbose',
    'inpaint',
    'inpaint_regularity',
    'PatchMatchUnavailableError',
    'native_lib_status',
    'load_native_lib',
    'required_native_files',
]


class PatchMatchUnavailableError(RuntimeError):
    """原生 PatchMatch 库缺失或加载失败。

    ``str(exc)`` 是给用户看的一段话（缺哪个文件、去哪儿补），可直接进错误
    弹窗与日志。继承 ``RuntimeError`` 而非 ``OSError``：``ui/module_manager.py``
    的模块加载失败弹窗与 InpaintThread 的失败提示都按 ``Exception`` 兜底，
    调用点不用改。
    """


#: 原生库目录（仓库相对路径）。精简包随包携带这几个文件；目录不入 git
#: （``*.dll`` 在 .gitignore 内），源码运行需自备。
LIB_DIR = 'data/libs'

#: 各平台的 patchmatch 原生库文件名（其余平台按 Linux 名）
_PATCHMATCH_FILENAMES = {
    'win32': 'patchmatch_inpaint.dll',
    'darwin': 'macos_libpatchmatch_inpaint.dylib',
}
_FALLBACK_PATCHMATCH_FILENAME = 'libpatchmatch.so'

#: Windows 的 patchmatch DLL 依赖它：实测缺了就直接加载失败。精简包两个一起带。
_WINDOWS_OPENCV_WORLD = 'opencv_world455.dll'

#: 已加载的原生库句柄（惰性填充；None＝还没加载，或上次加载失败）
PMLIB: Optional[ctypes.CDLL] = None


class CShapeT(ctypes.Structure):
    _fields_ = [
        ('width', ctypes.c_int),
        ('height', ctypes.c_int),
        ('channels', ctypes.c_int),
    ]

class CMatT(ctypes.Structure):
    _fields_ = [
        ('data_ptr', ctypes.c_void_p),
        ('shape', CShapeT),
        ('dtype', ctypes.c_int)
    ]


def patchmatch_filename() -> str:
    """本平台的 patchmatch 原生库文件名。"""
    return _PATCHMATCH_FILENAMES.get(sys.platform, _FALLBACK_PATCHMATCH_FILENAME)


def required_native_files(lib_dir: str = LIB_DIR) -> List[str]:
    """本平台需要的原生文件（仓库相对路径，按加载顺序）。

    Windows 上是两个：``patchmatch_inpaint.dll`` 以及被它依赖的
    ``opencv_world455.dll``。只做**存在性**判据、不加载——选型 UI 与启动期
    扫描可以放心调用。
    """
    names = [patchmatch_filename()]
    if sys.platform == 'win32':
        names.append(_WINDOWS_OPENCV_WORLD)
    return ['%s/%s' % (lib_dir, name) for name in names]


def _missing_files() -> List[str]:
    return [path for path in required_native_files() if not osp.isfile(path)]


def _missing_message(missing: List[str]) -> str:
    return (
        'PatchMatch native files are missing: %s. They ship inside the '
        'minimal package (data/libs) and are not tracked by git - re-extract '
        'the package, or copy its data/libs folder back.' % ', '.join(missing)
    )


def _load_failed_message(exc: OSError) -> str:
    return (
        'PatchMatch native library failed to load: %s (%s). Re-extract the '
        'minimal package so that %s are the packaged files.'
        % (patchmatch_filename(), exc, ', '.join(required_native_files()))
    )


def native_lib_status(try_load: bool = False) -> Tuple[bool, str]:
    """原生库可用性：``(可用, 可读原因)``。**不抛异常**，原因串可直接展示。

    缺省只查文件在不在、**不加载 DLL**：Windows 上这两个文件合起来 55MB，
    选型 UI 与启动扫描不该为了查一下就把它们映射进进程。``try_load=True``
    时顺带真加载一次（成功即缓存），用来分辨"文件都在但加载失败"（系统缺
    VC 运行库、附件版本不匹配等）。
    """
    missing = _missing_files()
    if missing:
        return False, _missing_message(missing)
    if try_load:
        try:
            _ensure_loaded()
        except OSError as exc:
            return False, _load_failed_message(exc)
    return True, ''


def load_native_lib() -> ctypes.CDLL:
    """取原生库句柄（幂等，首次调用时加载并声明函数原型）。

    不可用时抛 :class:`PatchMatchUnavailableError`，消息即
    :func:`native_lib_status` 的原因——用户需要的是"重新解压精简包"，不是
    ``Could not find module ...`` 这种加载器原话。
    """
    ok, reason = native_lib_status(try_load=True)
    if not ok:
        raise PatchMatchUnavailableError(reason)
    return _ensure_loaded()


def _ensure_loaded() -> ctypes.CDLL:
    """加载并缓存原生库；失败抛 ``OSError``（加载器原始错误）。"""
    global PMLIB
    if PMLIB is None:
        PMLIB = _load()
    return PMLIB


def _load() -> ctypes.CDLL:
    """加载原生库并按上游签名声明函数原型。"""
    if sys.platform == 'darwin':
        # macOS 的 dylib 需要同目录的 opencv_world dylib，版本号写在文件名里，
        # 故按 glob 找。尽力而为：找不到就让主库加载自己去报错。
        for path in sorted(glob('%s/macos_libopencv_world.*.dylib' % LIB_DIR)):
            try:
                ctypes.CDLL(path)
            except OSError:
                pass
    lib = ctypes.CDLL('%s/%s' % (LIB_DIR, patchmatch_filename()))
    lib.PM_set_random_seed.argtypes = [ctypes.c_uint]
    lib.PM_set_verbose.argtypes = [ctypes.c_int]
    lib.PM_free_pymat.argtypes = [CMatT]
    lib.PM_inpaint.argtypes = [CMatT, CMatT, ctypes.c_int]
    lib.PM_inpaint.restype = CMatT
    lib.PM_inpaint_regularity.argtypes = [CMatT, CMatT, CMatT, ctypes.c_int, ctypes.c_float]
    lib.PM_inpaint_regularity.restype = CMatT
    lib.PM_inpaint2.argtypes = [CMatT, CMatT, CMatT, ctypes.c_int]
    lib.PM_inpaint2.restype = CMatT
    lib.PM_inpaint2_regularity.argtypes = [CMatT, CMatT, CMatT, CMatT, ctypes.c_int, ctypes.c_float]
    lib.PM_inpaint2_regularity.restype = CMatT
    return lib


def set_random_seed(seed: int):
    lib = load_native_lib()
    lib.PM_set_random_seed(ctypes.c_uint(seed))


def set_verbose(verbose: bool):
    lib = load_native_lib()
    lib.PM_set_verbose(ctypes.c_int(verbose))


def inpaint(
    image: Union[np.ndarray, Image.Image],
    mask: Optional[Union[np.ndarray, Image.Image]] = None,
    *,
    global_mask: Optional[Union[np.ndarray, Image.Image]] = None,
    patch_size: int = 15
) -> np.ndarray:
    """
    PatchMatch based inpainting proposed in:

        PatchMatch : A Randomized Correspondence Algorithm for Structural Image Editing
        C.Barnes, E.Shechtman, A.Finkelstein and Dan B.Goldman
        SIGGRAPH 2009

    Args:
        image (Union[np.ndarray, Image.Image]): the input image, should be 3-channel RGB/BGR.
        mask (Union[np.array, Image.Image], optional): the mask of the hole(s) to be filled, should be 1-channel.
        If not provided (None), the algorithm will treat all purely white pixels as the holes (255, 255, 255).
        global_mask (Union[np.array, Image.Image], optional): the target mask of the output image.
        patch_size (int): the patch size for the inpainting algorithm.

    Return:
        result (np.ndarray): the repaired image, of the same size as the input image.
    """

    if isinstance(image, Image.Image):
        image = np.array(image)
    image = np.ascontiguousarray(image)
    assert image.ndim == 3 and image.shape[2] == 3 and image.dtype == 'uint8'

    if mask is None:
        mask = (image == (255, 255, 255)).all(axis=2, keepdims=True).astype('uint8')
        mask = np.ascontiguousarray(mask)
    else:
        mask = _canonize_mask_array(mask)

    # 参数校验放在加载之前：坏输入抛自己的 assert，不必先拉进原生库
    lib = load_native_lib()

    if global_mask is None:
        ret_pymat = lib.PM_inpaint(np_to_pymat(image), np_to_pymat(mask), ctypes.c_int(patch_size))
    else:
        global_mask = _canonize_mask_array(global_mask)
        ret_pymat = lib.PM_inpaint2(np_to_pymat(image), np_to_pymat(mask), np_to_pymat(global_mask), ctypes.c_int(patch_size))

    ret_npmat = pymat_to_np(ret_pymat)
    lib.PM_free_pymat(ret_pymat)

    return ret_npmat


def inpaint_regularity(
    image: Union[np.ndarray, Image.Image],
    mask: Optional[Union[np.ndarray, Image.Image]],
    ijmap: np.ndarray,
    *,
    global_mask: Optional[Union[np.ndarray, Image.Image]] = None,
    patch_size: int = 15, guide_weight: float = 0.25
) -> np.ndarray:
    if isinstance(image, Image.Image):
        image = np.array(image)
    image = np.ascontiguousarray(image)

    assert isinstance(ijmap, np.ndarray) and ijmap.ndim == 3 and ijmap.shape[2] == 3 and ijmap.dtype == 'float32'
    ijmap = np.ascontiguousarray(ijmap)

    assert image.ndim == 3 and image.shape[2] == 3 and image.dtype == 'uint8'
    if mask is None:
        mask = (image == (255, 255, 255)).all(axis=2, keepdims=True).astype('uint8')
        mask = np.ascontiguousarray(mask)
    else:
        mask = _canonize_mask_array(mask)

    lib = load_native_lib()

    if global_mask is None:
        ret_pymat = lib.PM_inpaint_regularity(np_to_pymat(image), np_to_pymat(mask), np_to_pymat(ijmap), ctypes.c_int(patch_size), ctypes.c_float(guide_weight))
    else:
        global_mask = _canonize_mask_array(global_mask)
        ret_pymat = lib.PM_inpaint2_regularity(np_to_pymat(image), np_to_pymat(mask), np_to_pymat(global_mask), np_to_pymat(ijmap), ctypes.c_int(patch_size), ctypes.c_float(guide_weight))

    ret_npmat = pymat_to_np(ret_pymat)
    lib.PM_free_pymat(ret_pymat)

    return ret_npmat


def _canonize_mask_array(mask):
    if isinstance(mask, Image.Image):
        mask = np.array(mask)
    if mask.ndim == 2 and mask.dtype == 'uint8':
        mask = mask[..., np.newaxis]
    assert mask.ndim == 3 and mask.shape[2] == 1 and mask.dtype == 'uint8'
    return np.ascontiguousarray(mask)


dtype_pymat_to_ctypes = [
    ctypes.c_uint8,
    ctypes.c_int8,
    ctypes.c_uint16,
    ctypes.c_int16,
    ctypes.c_int32,
    ctypes.c_float,
    ctypes.c_double,
]


dtype_np_to_pymat = {
    'uint8': 0,
    'int8': 1,
    'uint16': 2,
    'int16': 3,
    'int32': 4,
    'float32': 5,
    'float64': 6,
}


def np_to_pymat(npmat):
    assert npmat.ndim == 3
    return CMatT(
        ctypes.cast(npmat.ctypes.data, ctypes.c_void_p),
        CShapeT(npmat.shape[1], npmat.shape[0], npmat.shape[2]),
        dtype_np_to_pymat[str(npmat.dtype)]
    )


def pymat_to_np(pymat):
    npmat = np.ctypeslib.as_array(
        ctypes.cast(pymat.data_ptr, ctypes.POINTER(dtype_pymat_to_ctypes[pymat.dtype])),
        (pymat.shape.height, pymat.shape.width, pymat.shape.channels)
    )
    ret = np.empty(npmat.shape, npmat.dtype)
    ret[:] = npmat
    return ret
