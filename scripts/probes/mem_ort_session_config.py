# -*- coding: utf-8 -*-
"""对照实验：ONNX Runtime 会话配置对「主机工作集」的影响。

跑法：``python tmp/_mem_probe2.py`` —— 每档配置在**独立子进程**里测一次
（ORT 的 arena 一旦长大就留在进程里，同进程内逐档测会互相污染）。

被测档位：CUDA 的 cudnn 卷积算法搜索模式 × CPU arena 开关 × arena 扩张策略，
另加 CPU 设备作为对照（区域再检测只跑小裁剪，CPU 会话内存代价小得多）。
"""

import ctypes
import ctypes.wintypes as wt
import gc
import json
import os
import subprocess
import sys

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
CROP = (326, 241, 714, 459)  # 063 手机屏那块裁剪
MODEL = os.path.join(APP, "data", "models", "ppocrv6_onnx", "medium", "det.onnx")


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


# ── 子进程侧：跑一档配置 ────────────────────────────────────────────

def run_case(case):
    sys.path.insert(0, APP)
    os.chdir(APP)
    import argparse

    import numpy as np
    from utils.io_utils import imread
    from utils.config import pcfg
    from utils import config as program_config

    program_config.load_config()

    import onnxruntime as ort
    from onnxocr import predict_base as pb
    from onnxocr.predict_det import TextDetector

    x1, y1, x2, y2 = CROP
    img = np.ascontiguousarray(imread(os.path.join(PROJ, "063.jpg"))[y1:y2, x1:x2])
    p = pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {}
    base = dict(
        det_algorithm="DB", det_model_dir=MODEL, det_limit_side_len=960,
        det_limit_type="max", det_box_type="quad",
        det_db_thresh=p.get("det_db_thresh", 0.2),
        det_db_box_thresh=p.get("det_db_box_thresh", 0.45),
        det_db_unclip_ratio=p.get("det_db_unclip_ratio", 1.4),
        det_db_score_mode="fast", max_candidates=3000, use_dilation=False,
        use_gpu=case["use_gpu"],
    )

    before = rss_mb()

    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = case["cpu_arena"]
    providers = []
    if case["use_gpu"]:
        gpu_opts = {"cudnn_conv_algo_search": case["cudnn"]}
        if case["arena_ext"]:
            gpu_opts["arena_extend_strategy"] = case["arena_ext"]
        gpu_opts["do_copy_in_default_stream"] = True
        providers.append(("CUDAExecutionProvider", gpu_opts))
    providers.append("CPUExecutionProvider")

    def patched(self_, model_dir, use_gpu):
        return ort.InferenceSession(model_dir, opts, providers=providers)

    pb.PredictBase.get_onnx_session = patched
    det = TextDetector(argparse.Namespace(**base))
    loaded = rss_mb()
    det(img)
    ran = rss_mb()
    for _ in range(3):
        det(img)
    ran3 = rss_mb()
    det = None
    gc.collect()
    freed = rss_mb()
    print(json.dumps({
        "case": case["name"], "before": before, "loaded": loaded,
        "ran": ran, "ran_x4": ran3, "after_del": freed,
        "delta_load": round(loaded - before, 1),
        "delta_peak": round(ran3 - before, 1),
        "recovered": round(ran3 - freed, 1),
    }, ensure_ascii=False))


CASES = [
    {"name": "现状 CUDA EXHAUSTIVE + arena", "use_gpu": True, "cudnn": "EXHAUSTIVE",
     "cpu_arena": True, "arena_ext": None},
    {"name": "CUDA DEFAULT + arena", "use_gpu": True, "cudnn": "DEFAULT",
     "cpu_arena": True, "arena_ext": None},
    {"name": "CUDA DEFAULT + 关 CPU arena", "use_gpu": True, "cudnn": "DEFAULT",
     "cpu_arena": False, "arena_ext": None},
    {"name": "CUDA HEURISTIC + kSameAsRequested + 关 arena", "use_gpu": True,
     "cudnn": "HEURISTIC", "cpu_arena": False, "arena_ext": "kSameAsRequested"},
    {"name": "纯 CPU（对照）", "use_gpu": False, "cudnn": "EXHAUSTIVE",
     "cpu_arena": True, "arena_ext": None},
    {"name": "纯 CPU + 关 arena（对照）", "use_gpu": False, "cudnn": "EXHAUSTIVE",
     "cpu_arena": False, "arena_ext": None},
]


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--case":
        run_case(json.loads(sys.argv[2]))
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = []
    for case in CASES:
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--case",
             json.dumps(case, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
        line = [l for l in r.stdout.splitlines() if l.startswith("{")]
        if line:
            rec = json.loads(line[-1])
            out.append(rec)
            print("%-46s 载入 +%7.1f | 峰值 +%7.1f | 删后回收 %7.1f MB"
                  % (rec["case"], rec["delta_load"], rec["delta_peak"],
                     rec["recovered"]))
        else:
            print("%-46s 失败：%s" % (case["name"], (r.stderr or r.stdout)[-400:]))
        sys.stdout.flush()
    with open("tmp/mem_probe2.out", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved tmp/mem_probe2.out")


if __name__ == "__main__":
    main()
