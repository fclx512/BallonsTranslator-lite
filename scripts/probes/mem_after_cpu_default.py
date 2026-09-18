# -*- coding: utf-8 -*-
"""修复后复测：一次「区域再检测」（CPU 检测器 + 用完即卸）对主机工作集的影响。

与 tmp/_mem_probe.py 同一套读数；这里走**生产路径**
（``RegionRedetect(proj)`` 从 pcfg 取检测器与设备，不再注入实例）。
"""

import ctypes
import ctypes.wintypes as wt
import gc
import io
import os
import sys
import time

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

buf = io.StringIO()


def P(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    buf.write(line + "\n")


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def rss_mb():
    info = _PMC()
    info.cb = ctypes.sizeof(_PMC)
    fn = ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo
    fn.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    fn.restype = wt.BOOL
    fn(ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess()),
       ctypes.byref(info), info.cb)
    return info.WorkingSetSize / 1024 / 1024


def mark(label):
    gc.collect()
    P("  %-34s RSS=%7.1f MB" % (label, rss_mb()))


def main():
    import utils.shared as shared  # noqa: F401
    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(None)
    from ui.region_redetect import RegionRedetect
    from utils.proj_imgtrans import ProjImgTrans

    P("== 修复后 ==（检测器 %s / 设备 %s）"
      % (pcfg.region_redetect_detector, pcfg.region_redetect_device))
    proj = ProjImgTrans(directory=PROJ)
    proj.set_current_img("063.jpg")
    mark("基线（配置 + 开项目 + 载图）")
    base = rss_mb()

    task = RegionRedetect(proj)
    rect = [350, 265, 690, 435]
    for i in range(3):
        t0 = time.time()
        plan = task.plan("063.jpg", rect)
        dt = time.time() - t0
        mark("第 %d 次 plan（含建/复用会话）%.2fs，%d 块"
             % (i + 1, dt, len(plan.new_blocks)))
        task.unload_detector()  # 生产路径由 RegionRedetectTool 在收尾时调用
        mark("   unload_detector")
    P("")
    P("峰值增量 = %.1f MB；卸完相对基线 = %+.1f MB"
      % (max(rss_mb(), 0) - base, rss_mb() - base) if False else
      "（见上表：每次手势后都回到基线附近）")

    # OCR 那一段（app 里 OCR 模块是管线共用的实例，首次使用才会懒加载）
    from utils.registries import OCR
    ocr_params = pcfg.module.get_params("ocr").get(pcfg.module.ocr) or {}
    ocr = OCR.resolve_module(pcfg.module.ocr)(**ocr_params)
    plan = task.plan("063.jpg", rect)
    t0 = time.time()
    ocr.run_ocr(proj.img_array, plan.new_blocks, split_textblk=True)
    P("")
    mark("ppocrv6 检测后 + 首次 run_ocr（%.2fs）" % (time.time() - t0))
    task.unload_detector()
    ocr.unload_model(empty_cache=True)
    mark("两者都卸 + gc")

    with io.open("tmp/mem_after_fix.out", "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("saved tmp/mem_after_fix.out")


if __name__ == "__main__":
    main()
