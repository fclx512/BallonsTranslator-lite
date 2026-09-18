# -*- coding: utf-8 -*-
"""CUDA 残留归因：那 ~690MB 到底是什么、还能不能交回给系统（只读）。

在同一进程里做三件事并逐点读工作集：
  A. 跑一次 CUDA 检测 + OCR 后全部卸掉          → 剩下多少
  B. 销毁 CUDA 上下文（cudaDeviceReset）        → 再还多少
  C. EmptyWorkingSet（把工作集还给系统备用链表） → 再还多少
  D. 再用一次 CUDA                              → 会不会又涨回来

外加每个阶段的**模块级工作集明细**（哪个 DLL 在占），用于判断残留是
"堆"还是"驱动/cuDNN DLL 的文件映射"——这决定进程内能不能救。
"""

import ctypes
import ctypes.wintypes as wt
import faulthandler
import gc
import os
import subprocess
import sys
import time

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
OUT = os.path.join(APP, "tmp", "_rr_modules_probe.out")

sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_log = open(OUT, "w", encoding="utf-8", buffering=1)
faulthandler.enable(file=open(os.path.join(APP, "tmp", "_rr_modules_probe.crash"), "w"))

k32 = ctypes.WinDLL("kernel32.dll")
psapi = ctypes.WinDLL("psapi.dll")

HANDLE = wt.HANDLE
HMODULE = wt.HMODULE


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


def _proc_handle():
    k32.GetCurrentProcess.restype = HANDLE
    return k32.GetCurrentProcess()


def rss_mb():
    info = _PMC()
    info.cb = ctypes.sizeof(_PMC)
    fn = psapi.GetProcessMemoryInfo
    fn.argtypes = [HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    fn.restype = wt.BOOL
    if not fn(_proc_handle(), ctypes.byref(info), info.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return info.WorkingSetSize / 1024 / 1024


class _MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wt.DWORD),
                ("EntryPoint", ctypes.c_void_p)]


psapi.EnumProcessModules.argtypes = [HANDLE, ctypes.POINTER(HMODULE), wt.DWORD,
                                    ctypes.POINTER(wt.DWORD)]
psapi.EnumProcessModules.restype = wt.BOOL
psapi.GetModuleInformation.argtypes = [HANDLE, HMODULE, ctypes.POINTER(_MODULEINFO), wt.DWORD]
psapi.GetModuleInformation.restype = wt.BOOL
k32.GetModuleFileNameW.argtypes = [HMODULE, wt.LPWSTR, wt.DWORD]
k32.GetModuleFileNameW.restype = wt.DWORD


def modules():
    """{dll 短名: 工作集 MB}——工作集里含文件映射页，DLL 一加载就不会自己走。"""
    h = _proc_handle()
    needed = wt.DWORD()
    if not psapi.EnumProcessModules(h, None, 0, ctypes.byref(needed)):
        return {}
    n = needed.value // ctypes.sizeof(HMODULE)
    arr = (HMODULE * n)()
    if not psapi.EnumProcessModules(h, arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return {}
    out = {}
    for i in range(needed.value // ctypes.sizeof(HMODULE)):
        mi = _MODULEINFO()
        if not psapi.GetModuleInformation(h, arr[i], ctypes.byref(mi), ctypes.sizeof(mi)):
            continue
        buf = ctypes.create_unicode_buffer(512)
        if k32.GetModuleFileNameW(arr[i], buf, 512) == 0:
            continue
        name = os.path.basename(buf.value)
        if mi.SizeOfImage <= 0:
            continue
        # 每模块的工作集：用 QueryWorkingSet 太啰嗦，这里用 SizeOfImage 的量级排序 +
        # 进程总工作集已经足够判断"DLL 常驻"是不是大头。
        out[name] = mi.SizeOfImage / 1024 / 1024
    return out


def query_working_set_by_module():
    """精确一点：用 QueryWorkingSetEx 统计每个模块页的工作集。"""
    h = _proc_handle()
    needed = wt.DWORD()
    if not psapi.EnumProcessModules(h, None, 0, ctypes.byref(needed)):
        return {}
    n = needed.value // ctypes.sizeof(HMODULE)
    arr = (HMODULE * n)()
    if not psapi.EnumProcessModules(h, arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return {}

    class _PSAPI_WORKING_SET_BLOCK(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_size_t)]

    class _PSAPI_WORKING_SET_INFORMATION(ctypes.Structure):
        _fields_ = [("NumberOfEntries", ctypes.c_size_t),
                    ("WorkingSetInfo", _PSAPI_WORKING_SET_BLOCK * 1)]

    QueryWorkingSet = psapi.QueryWorkingSet
    QueryWorkingSet.argtypes = [HANDLE, ctypes.c_void_p, wt.DWORD]
    QueryWorkingSet.restype = wt.BOOL

    pages = 0
    buf_size = 4096
    while True:
        buf = ctypes.create_string_buffer(buf_size)
        if QueryWorkingSet(h, buf, buf_size):
            info = ctypes.cast(buf, ctypes.POINTER(_PSAPI_WORKING_SET_INFORMATION))
            pages = info.contents.NumberOfEntries
            raw = ctypes.addressof(buf)
            stride = ctypes.sizeof(ctypes.c_size_t)
            entries = []
            for i in range(pages):
                v = ctypes.c_size_t.from_address(raw + 8 + i * stride).value
                entries.append(v)
            break
        if buf_size > 64 * 1024 * 1024:
            return {}
        buf_size *= 2
    res = {}
    for m in arr:
        mi = _MODULEINFO()
        if not psapi.GetModuleInformation(h, m, ctypes.byref(mi), ctypes.sizeof(mi)):
            continue
        buf = ctypes.create_unicode_buffer(512)
        if k32.GetModuleFileNameW(m, buf, 512) == 0:
            continue
        base = mi.lpBaseOfDll or 0
        end = base + mi.SizeOfImage
        cnt = 0
        for v in entries:
            addr = (v >> 12) << 12
            if base <= addr < end:
                cnt += 1
        res[os.path.basename(buf.value)] = cnt * 4096 / 1024 / 1024
    return res


def dump(label, ws):
    tot = sum(ws.values())
    P("  -- %s：模块工作集合计 %.0f MB（%d 个模块）" % (label, tot, len(ws)))
    for name, mb in sorted(ws.items(), key=lambda kv: -kv[1])[:12]:
        P("       %-46s %7.1f MB" % (name, mb))


T0 = time.time()


def mark(label):
    gc.collect()
    P("  %-34s RSS=%7.1f MB  t=%5.1fs" % (label, rss_mb(), time.time() - T0))


def main():
    P("=== [0] 导入 ===")
    mark("冷启动")
    import utils.shared as shared  # noqa: F401
    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(None)
    from utils.proj_imgtrans import ProjImgTrans
    from utils.registries import OCR, TEXTDETECTORS

    mark("引擎导入完成")
    base_ws = query_working_set_by_module()
    dump("CUDA 之前", base_ws)

    proj = ProjImgTrans(directory=PROJ)
    proj.set_current_img("063.jpg")
    crop = proj.img_array[241:459, 326:714]
    mark("开项目 + 载图")

    P("")
    P("=== [1] CUDA 检测 + OCR，然后全部卸掉 ===")
    det_params = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
    det_params["device"] = "cuda"
    det = TEXTDETECTORS.resolve_module("ppocrv6_onnx")(**det_params)
    det.detect(crop)
    mark("首次 detect CUDA")
    det.unload_model(empty_cache=True)
    del det
    mark("卸检测器")

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
    after_unload_ws = query_working_set_by_module()
    dump("卸完后", after_unload_ws)

    P("")
    P("=== [2] 销毁 CUDA 上下文 ===")
    import torch

    import glob

    import torch as _torch  # noqa: F811

    found = sorted(glob.glob(os.path.join(os.path.dirname(_torch.__file__), "lib", "cudart*.dll")))
    P("  cudart:", [os.path.basename(p) for p in found])
    lib = ctypes.WinDLL(found[0])
    lib.cudaDeviceReset.argtypes = []
    lib.cudaDeviceReset.restype = ctypes.c_int
    P("  cudaDeviceReset rc=%s" % lib.cudaDeviceReset())
    mark("cudaDeviceReset 后")
    nv = ctypes.WinDLL("nvcuda.dll")
    nv.cuDevicePrimaryCtxReset.argtypes = [ctypes.c_int]
    nv.cuDevicePrimaryCtxReset.restype = ctypes.c_int
    P("  cuDevicePrimaryCtxReset rc=%s" % nv.cuDevicePrimaryCtxReset(0))
    mark("再 reset 一次后")

    P("")
    P("=== [3] EmptyWorkingSet（把工作集交回系统备用链表）===")
    psapi.EmptyWorkingSet.argtypes = [HANDLE]
    psapi.EmptyWorkingSet.restype = wt.BOOL
    P("  EmptyWorkingSet:", bool(psapi.EmptyWorkingSet(_proc_handle())))
    time.sleep(0.5)
    mark("EmptyWorkingSet 后")
    P("  SetProcessWorkingSetSize(-1,-1):",
      bool(k32.SetProcessWorkingSetSize(_proc_handle(), ctypes.c_size_t(-1).value,
                                        ctypes.c_size_t(-1).value)))
    time.sleep(0.5)
    mark("SetProcessWorkingSetSize 后")

    P("")
    P("=== [4] 再用一次 CUDA：会涨回来吗 ===")
    det2_params = dict(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {})
    det2_params["device"] = "cuda"
    det2 = TEXTDETECTORS.resolve_module("ppocrv6_onnx")(**det2_params)
    det2.detect(crop)
    mark("重置后再跑一次 CUDA detect")
    det2.unload_model(empty_cache=True)
    del det2
    mark("再卸掉")
    final_ws = query_working_set_by_module()
    dump("最终", final_ws)

    P("")
    P("=== 模块级增量（最终 vs CUDA 之前）===")
    names = set(base_ws) | set(final_ws)
    deltas = sorted(((n, final_ws.get(n, 0) - base_ws.get(n, 0)) for n in names),
                    key=lambda kv: -kv[1])
    for n, d in deltas[:18]:
        if abs(d) >= 1.0:
            P("       %-46s %+7.1f MB" % (n, d))
    P("  合计增量: %+.0f MB" % sum(d for _, d in deltas))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        P("!! 异常:", repr(e))
        P(traceback.format_exc())
    _log.close()
