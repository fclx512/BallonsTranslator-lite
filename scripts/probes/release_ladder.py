# -*- coding: utf-8 -*-
"""「跑完管线后能不能把 CUDA 内存还回去」探针（只读，不改仓库行为）。

问两件事：
  1) **进程内**能不能还？——卸模型 + empty_cache + 销毁 CUDA 上下文
     （`cudaDeviceReset` / `cuDevicePrimaryCtxReset`）之后，进程工作集回到多少？
  2) 还不了的话，**重建要多久**？——新进程起到 CUDA 会话建好、首次推理完成几秒。

读数用 Windows `GetProcessMemoryInfo`（任务管理器看到的"内存"），每条立即落盘，
因为 cudaDeviceReset 有让进程直接崩掉的风险（崩了也不丢已采集的数据）。
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
OUT = os.path.join(APP, "tmp", "_rr_release_probe.out")

sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_log = open(OUT, "w", encoding="utf-8", buffering=1)
faulthandler.enable(file=open(os.path.join(APP, "tmp", "_rr_release_probe.crash"), "w"))


def P(*a):
    """每条立即写盘 + 回显，崩溃也不丢。"""
    line = " ".join(str(x) for x in a)
    _log.write(line + "\n")
    _log.flush()
    try:
        print(line, flush=True)
    except Exception:
        pass


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def rss_mb():
    # 必须显式声明 argtypes：GetCurrentProcess 的伪句柄是 -1，不声明会被当 32 位
    # 整数传进去 → 调用失败静默返回 0。
    info = _PMC()
    info.cb = ctypes.sizeof(_PMC)
    fn = ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo
    fn.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    fn.restype = wt.BOOL
    if not fn(ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess()),
              ctypes.byref(info), info.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return info.WorkingSetSize / 1024 / 1024


T0 = time.time()


def mark(label):
    gc.collect()
    P("  %-32s RSS=%7.1f MB   t=%5.1fs" % (label, rss_mb(), time.time() - T0))


def smi():
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def main():
    P("=== [0] 进程启动 ===")
    mark("python 冷启动")

    import utils.shared as shared  # noqa: F401
    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(None)
    from utils.proj_imgtrans import ProjImgTrans
    from utils.registries import OCR, TEXTDETECTORS

    mark("引擎导入完成（含 torch）")
    P("  nvidia-smi:", smi())

    proj = ProjImgTrans(directory=PROJ)
    proj.set_current_img("063.jpg")
    mark("开项目 + 载图 063")

    crop = proj.img_array[241:459, 326:714]

    # ── 1. 管线用到的两类 ONNX CUDA 会话 ─────────────────────────────
    P("")
    P("=== [1] ppocrv6_onnx 检测器（device=cuda）===")
    det_class = TEXTDETECTORS.resolve_module("ppocrv6_onnx")
    det_params = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
    P("  参数 device 原值:", det_params.get("device"))
    if "device" in det_params:
        det_params["device"] = "cuda"
    det = det_class(**det_params)
    mark("实例化（未载权重）")
    t = time.time()
    det.detect(crop)
    mark("首次 detect CUDA（%.1fs）" % (time.time() - t))
    det.detect(crop)
    mark("第二次 detect")
    P("  nvidia-smi:", smi())
    det.unload_model(empty_cache=True)
    mark("unload_model(empty_cache=True)")
    del det
    mark("del 检测器实例")
    P("  nvidia-smi:", smi())

    P("")
    P("=== [2] OCR paddleocr_v6_onnx（device=cuda）===")
    ocr_name = "paddleocr_v6_onnx"
    ocr_params = dict(pcfg.module.get_params("ocr").get(ocr_name) or {})
    P("  参数 device 原值:", ocr_params.get("device"))
    if "device" in ocr_params:
        ocr_params["device"] = "cuda"
    ocr = OCR.resolve_module(ocr_name)(**ocr_params)
    mark("实例化")
    from utils.textblock import TextBlock

    blk = TextBlock(lines=[[[480, 295], [615, 295], [615, 341], [480, 341]]])
    blk.adjust_bbox()
    t = time.time()
    ocr.run_ocr(proj.img_array, [blk], split_textblk=True)
    mark("首次 run_ocr CUDA（%.1fs）" % (time.time() - t))
    P("  text=%r" % blk.get_text())
    P("  nvidia-smi:", smi())
    ocr.unload_model(empty_cache=True)
    mark("unload_model + del")
    del ocr
    gc.collect()
    mark("全部卸完 + gc")
    P("  nvidia-smi:", smi())

    # ── 2. torch 侧上下文（管线修复阶段会起） ───────────────────────
    P("")
    P("=== [3] torch CUDA 上下文 ===")
    import torch

    mark("import torch（已导入则无变化）")
    P("  torch.cuda.is_available():", torch.cuda.is_available())
    P("  torch CUDA 版本:", getattr(torch.version, "cuda", None))
    try:
        x = torch.zeros(4 * 1024 * 1024, dtype=torch.float32, device="cuda")
        mark("cuda 上分配 4M float（建 torch 上下文）")
        del x
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
        mark("torch empty_cache + ipc_collect")
    except Exception as e:
        P("  torch CUDA 分配失败:", repr(e))
    P("  nvidia-smi:", smi())

    # ── 3. 释放尝试 ─────────────────────────────────────────────────
    P("")
    P("=== [4] 释放尝试 A：cudart!cudaDeviceReset ===")
    libdir = os.path.join(os.path.dirname(torch.__file__), "lib")
    cudarts = sorted(glob.glob(os.path.join(libdir, "cudart*")))
    P("  torch/lib 下 cudart:", [os.path.basename(c) for c in cudarts])
    reset_attempts = []
    for path in cudarts:
        if not path.lower().endswith(".dll"):
            continue
        try:
            lib = ctypes.WinDLL(path)
            fn = lib.cudaDeviceReset
            fn.argtypes = []
            fn.restype = ctypes.c_int
            rc = fn()
            P("  %s -> cudaDeviceReset() rc=%s（0=成功）" % (os.path.basename(path), rc))
            reset_attempts.append(("cudart:" + os.path.basename(path), rc))
        except Exception as e:
            P("  %s 调用失败: %r" % (os.path.basename(path), e))
    mark("cudaDeviceReset 之后")
    P("  nvidia-smi:", smi())

    P("")
    P("=== [5] 释放尝试 B：nvcuda!cuDevicePrimaryCtxReset ===")
    try:
        nv = ctypes.WinDLL("nvcuda.dll")
        fn = nv.cuDevicePrimaryCtxReset
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_int
        rc = fn(0)
        P("  cuDevicePrimaryCtxReset(0) rc=%s（0=成功）" % rc)
    except Exception as e:
        P("  调用失败: %r" % e)
    mark("cuDevicePrimaryCtxReset 之后")
    P("  nvidia-smi:", smi())

    # ── 4. 重置后还能不能用？（重建代价 + 安全性） ───────────────────
    P("")
    P("=== [6] 重置后重建（能不能继续用 + 耗时）===")
    t = time.time()
    try:
        det2_class = TEXTDETECTORS.resolve_module("ppocrv6_onnx")
        params2 = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
        if "device" in params2:
            params2["device"] = "cuda"
        det2 = det2_class(**params2)
        t_load = time.time() - t
        t2 = time.time()
        det2.detect(crop)
        P("  ORT CUDA 重建成功：实例化 %.2fs、首次推理 %.2fs" % (t_load, time.time() - t2))
        mark("重置后重新 CUDA 推理")
        det2.unload_model(empty_cache=True)
        del det2
        mark("再卸一次")
    except Exception as e:
        P("  ORT 重建失败:", repr(e))
    try:
        y = torch.zeros(1024 * 1024, device="cuda")
        P("  torch CUDA 重建成功")
        del y
        mark("重置后 torch CUDA 分配")
    except Exception as e:
        P("  torch CUDA 重建失败:", repr(e))
    P("  nvidia-smi:", smi())

    P("")
    P("=== [7] 收尾读数 ===")
    mark("最终")
    P("  对照：本进程冷启动到此处共 %.1fs" % (time.time() - T0))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        P("!! 异常:", repr(e))
        P(traceback.format_exc())
    _log.close()
