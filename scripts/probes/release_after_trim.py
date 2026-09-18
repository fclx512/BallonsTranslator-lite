# -*- coding: utf-8 -*-
"""「交回工作集」之后的可用性与代价（只读）。

链路：CUDA 跑一轮 → 全卸 → cudaDeviceReset → EmptyWorkingSet → **再用一次 CUDA**
看三件事：还能不能用、要多久（页被换出去后重新 fault in 的代价）、内存是否涨回来。
"""

import ctypes
import ctypes.wintypes as wt
import faulthandler
import gc
import glob
import os
import subprocess
import sys
import time

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
OUT = os.path.join(APP, "tmp", "_rr_after_trim_probe.out")

sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_log = open(OUT, "w", encoding="utf-8", buffering=1)
faulthandler.enable(file=open(os.path.join(APP, "tmp", "_rr_after_trim_probe.crash"), "w"))

k32 = ctypes.WinDLL("kernel32.dll")
psapi = ctypes.WinDLL("psapi.dll")
HANDLE = wt.HANDLE


def P(*a):
    line = " ".join(str(x) for x in a)
    _log.write(line + "\n")
    _log.flush()
    try:
        print(line, flush=True)
    except Exception:
        pass


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


k32.GetCurrentProcess.restype = HANDLE


def rss_mb():
    info = _PMC()
    info.cb = ctypes.sizeof(_PMC)
    fn = psapi.GetProcessMemoryInfo
    fn.argtypes = [HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    fn.restype = wt.BOOL
    fn(k32.GetCurrentProcess(), ctypes.byref(info), info.cb)
    return info.WorkingSetSize / 1024 / 1024


def trim():
    psapi.EmptyWorkingSet.argtypes = [HANDLE]
    psapi.EmptyWorkingSet.restype = wt.BOOL
    return bool(psapi.EmptyWorkingSet(k32.GetCurrentProcess()))


def smi():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


T0 = time.time()


def mark(label):
    gc.collect()
    P("  %-40s RSS=%7.1f MB  t=%5.1fs" % (label, rss_mb(), time.time() - T0))


def main():
    import utils.shared as shared  # noqa: F401
    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(None)
    from utils.proj_imgtrans import ProjImgTrans
    from utils.registries import OCR, TEXTDETECTORS

    mark("引擎导入完成")
    proj = ProjImgTrans(directory=PROJ)
    proj.set_current_img("063.jpg")
    crop = proj.img_array[241:459, 326:714]
    mark("开项目 + 载图")

    P("")
    P("=== [1] CUDA 跑一轮（检测 + OCR）再全卸 ===")
    det_params = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
    det_params["device"] = "cuda"
    det = TEXTDETECTORS.resolve_module("ppocrv6_onnx")(**det_params)
    det.detect(crop)
    mark("首次 detect CUDA（冷）")
    det.detect(crop)
    mark("第二次 detect（热）")
    det.unload_model(empty_cache=True)
    del det

    ocr_params = dict(pcfg.module.get_params("ocr").get("paddleocr_v6_onnx") or {})
    ocr_params["device"] = "cuda"
    ocr = OCR.resolve_module("paddleocr_v6_onnx")(**ocr_params)
    from utils.textblock import TextBlock

    blk = TextBlock(lines=[[[480, 295], [615, 295], [615, 341], [480, 341]]])
    blk.adjust_bbox()
    ocr.run_ocr(proj.img_array, [blk], split_textblk=True)
    mark("首次 run_ocr CUDA")
    ocr.unload_model(empty_cache=True)
    del ocr
    gc.collect()
    mark("全部卸完 + gc")
    P("  nvidia-smi:", smi())

    P("")
    P("=== [2] 释放动作 ===")
    import torch

    try:
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
        P("  torch.cuda.empty_cache + ipc_collect 完成")
    except Exception as e:
        P("  torch empty_cache 失败:", repr(e))
    mark("empty_cache 后")

    p = sorted(glob.glob(os.path.join(os.path.dirname(torch.__file__), "lib", "cudart*.dll")))[0]
    lib = ctypes.WinDLL(p)
    lib.cudaDeviceReset.argtypes = []
    lib.cudaDeviceReset.restype = ctypes.c_int
    P("  cudaDeviceReset rc=%s" % lib.cudaDeviceReset())
    mark("cudaDeviceReset 后")

    P("  EmptyWorkingSet:", trim())
    time.sleep(0.3)
    mark("EmptyWorkingSet 后")
    P("  nvidia-smi:", smi())

    P("")
    P("=== [3] 交回之后再跑一次 CUDA（能用吗 / 多久 / 涨回来吗）===")
    t = time.time()
    det2_params = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
    det2_params["device"] = "cuda"
    det2 = TEXTDETECTORS.resolve_module("ppocrv6_onnx")(**det2_params)
    P("  实例化 %.2fs" % (time.time() - t))
    t = time.time()
    try:
        det2.detect(crop)
        P("  首次 detect %.2fs（冷，页被换出去过）" % (time.time() - t))
    except Exception as e:
        P("  detect 失败:", repr(e))
    mark("交回后 detect")
    t = time.time()
    det2.detect(crop)
    P("  第二次 detect %.2fs（热）" % (time.time() - t))
    mark("交回后第二次 detect")
    P("  nvidia-smi:", smi())

    P("")
    P("=== [4] 再交回一次 ===")
    det2.unload_model(empty_cache=True)
    del det2
    gc.collect()
    try:
        torch.cuda.empty_cache()
        lib.cudaDeviceReset()
    except Exception as e:
        P("  reset 失败:", repr(e))
    P("  EmptyWorkingSet:", trim())
    time.sleep(0.3)
    mark("第二轮释放后")
    P("  nvidia-smi:", smi())
    P("")
    P("  总耗时 %.1fs" % (time.time() - T0))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        P("!! 异常:", repr(e))
        P(traceback.format_exc())
    _log.close()
