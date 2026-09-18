# -*- coding: utf-8 -*-
"""对照实验二：区域再检测用的 ppocrv6 检测器，CPU vs CUDA 的**耗时**（内存见
tmp/_mem_probe*.py）。

被测：① 会话创建（＝改用"用完即卸"时每次手势要付的加载代价）② 单次小裁剪推
理（区域再检测的真实负载：063 手机屏 388×218、047 底部旁白 388×173、以及一个
较大的 500×600 裁剪）。
"""

import argparse
import json
import os
import subprocess
import sys
import time

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
MODEL = os.path.join(APP, "data", "models", "ppocrv6_onnx", "medium", "det.onnx")
CROPS = [
    ("063 手机屏 388x218", "063.jpg", (326, 241, 714, 459)),
    ("047 底部旁白 388x173", "047.jpeg", (536, 1176, 924, 1349)),
    ("较大区域 500x600", "063.jpg", (300, 200, 800, 800)),
]


def run_case(use_gpu):
    sys.path.insert(0, APP)
    os.chdir(APP)
    import numpy as np

    from utils import config as program_config

    program_config.load_config()
    from utils.config import pcfg
    from utils.io_utils import imread

    from onnxocr.predict_det import TextDetector

    p = pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {}
    args = argparse.Namespace(
        det_algorithm="DB", det_model_dir=MODEL, det_limit_side_len=960,
        det_limit_type="max", det_box_type="quad",
        det_db_thresh=p.get("det_db_thresh", 0.2),
        det_db_box_thresh=p.get("det_db_box_thresh", 0.45),
        det_db_unclip_ratio=p.get("det_db_unclip_ratio", 1.4),
        det_db_score_mode="fast", max_candidates=3000, use_dilation=False,
        use_gpu=use_gpu,
    )
    t0 = time.time()
    det = TextDetector(args)
    build = time.time() - t0

    out = {"device": "cuda" if use_gpu else "cpu", "build_s": round(build, 2),
           "crops": []}
    for label, name, (x1, y1, x2, y2) in CROPS:
        img = np.ascontiguousarray(
            imread(os.path.join(PROJ, name))[y1:y2, x1:x2]
        )
        det(img)  # 预热一次（首次含显存/线程池初始化）
        t0 = time.time()
        n = 3
        for _ in range(n):
            boxes = det(img)
        dt = (time.time() - t0) / n
        out["crops"].append(
            {"label": label, "in": list(img.shape[:2]), "boxes":
             (0 if boxes is None else len(boxes)), "sec": round(dt, 3)}
        )
    print(json.dumps(out, ensure_ascii=False))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--case":
        run_case(sys.argv[2] == "cuda")
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for mode in ("cuda", "cpu"):
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--case", mode],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900,
        )
        lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
        if not lines:
            print(mode, "失败：", (r.stderr or r.stdout)[-600:])
            continue
        rec = json.loads(lines[-1])
        print("== %s ==  会话创建 %.2fs" % (rec["device"].upper(), rec["build_s"]))
        for c in rec["crops"]:
            print("   %-24s 输入 %s  %d 框  %.3fs/次"
                  % (c["label"], c["in"], c["boxes"], c["sec"]))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
