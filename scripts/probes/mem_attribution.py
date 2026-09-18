# -*- coding: utf-8 -*-
"""内存归因探针：区域再检测一次下来，内存长在哪一步、卸得掉吗（只读）。

测的是**进程工作集**（Windows ``GetProcessMemoryInfo``，即任务管理器看到的
"内存"）；另用 torch 的 CUDA 分配器读数与 nvidia-smi 作旁证。
"""

import ctypes
import ctypes.wintypes as wt
import gc
import io
import os
import subprocess
import sys

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
    # 整数传进去（64 位下截断成 0xFFFFFFFF 以外的值）→ 调用失败返回 0。
    import ctypes.wintypes as wt

    info = _PMC()
    info.cb = ctypes.sizeof(_PMC)
    fn = ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo
    fn.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    fn.restype = wt.BOOL
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    handle = ctypes.c_void_p(handle)
    if not fn(handle, ctypes.byref(info), info.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return info.WorkingSetSize / 1024 / 1024


def torch_gpu_mb():
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return torch.cuda.memory_allocated() / 1024 / 1024
    except Exception:
        return None


def nvidia_smi():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def mark(label):
    gc.collect()
    P("  %-34s RSS=%7.1f MB" % (label, rss_mb()))


def main():
    P("== 基线 ==")
    mark("imports 完")

    import utils.shared as shared  # noqa: F401
    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(None)
    from utils.proj_imgtrans import ProjImgTrans
    from utils.registries import OCR, TEXTDETECTORS

    mark("config + 注册表")
    proj = ProjImgTrans(directory=PROJ)
    proj.set_current_img("063.jpg")
    mark("开项目 + 载图 063")
    P("  torch CUDA allocated:", torch_gpu_mb(), "| nvidia-smi:", nvidia_smi())
    P("")

    det_class = TEXTDETECTORS.resolve_module("ppocrv6_onnx")
    params = pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {}
    P("== 区域再检测用的 ppocrv6 检测器（device=%s）==" % params.get("device"))
    det = det_class(**params)
    mark("实例化（未载权重）")
    det.detect(proj.img_array[241:459, 326:714])  # 就是 063 手机屏那块裁剪
    mark("首次 detect（建 ORT 会话）")
    det.detect(proj.img_array[241:459, 326:714])
    mark("第二次 detect")
    P("  torch CUDA allocated:", torch_gpu_mb(), "| nvidia-smi:", nvidia_smi())
    P("")
    det.unload_model(empty_cache=True)
    mark("unload_model + gc")
    del det
    mark("del 实例 + gc")
    P("  torch CUDA allocated:", torch_gpu_mb(), "| nvidia-smi:", nvidia_smi())
    P("")

    P("== OCR 模块 paddleocr_v6_onnx（首次 run_ocr 会懒加载）==")
    ocr_params = pcfg.module.get_params("ocr").get("paddleocr_v6_onnx") or {}
    ocr = OCR.resolve_module("paddleocr_v6_onnx")(**ocr_params)
    mark("实例化（未载权重）")
    from utils.textblock import TextBlock

    blk = TextBlock(lines=[[[480, 295], [615, 295], [615, 341], [480, 341]]])
    blk.adjust_bbox()
    ocr.run_ocr(proj.img_array, [blk], split_textblk=True)
    mark("首次 run_ocr（建会话）")
    P("  text=%r" % blk.get_text())
    P("  torch CUDA allocated:", torch_gpu_mb(), "| nvidia-smi:", nvidia_smi())
    ocr.unload_model(empty_cache=True)
    mark("unload_model + gc")
    del ocr
    mark("del 实例 + gc")
    P("  torch CUDA allocated:", torch_gpu_mb(), "| nvidia-smi:", nvidia_smi())
    P("")
    P("脚本自身进程的绝对 RSS 只作对照——app 里还有 torch/ysgyolo 等常驻部分。")

    with io.open("tmp/mem_probe.out", "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("saved tmp/mem_probe.out")


if __name__ == "__main__":
    main()
