"""方向判定探针：对指定图片跑指定检测器，打印每块的 src_is_vertical。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/direction_probe.py [--detector NAME] <图片...>

detector 默认取 config 里的当前检测器（如 ysgyolo）；可显式指定，如 --detector paddleocr_v6
（键为注册名，见 pcfg.module.textdetector 的可选值）。

背景：mit_merge_textlines 曾把所有单行块判成竖排（nv >= len//2 在 len==1 时恒真），
本探针用真实检测器输出核验修复效果。结果只进终端，不写任何文件。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2


def main():
    args = sys.argv[1:]
    detector_name = None
    if args and args[0] == "--detector":
        detector_name = args[1]
        args = args[2:]
    img_paths = args
    if not img_paths:
        print("usage: direction_probe.py [--detector NAME] <image> [image ...]")
        return 1

    from modules.base import init_module_registries
    from utils.config import load_config, pcfg

    load_config()
    init_module_registries()

    from utils.registries import TEXTDETECTORS

    name = detector_name or pcfg.module.textdetector
    print(f"[probe] detector = {name}")
    det = TEXTDETECTORS.resolve_module(name)()
    det.load_model()

    for p in img_paths:
        img = cv2.imread(p)
        if img is None:
            print(f"[skip] cannot read: {p}")
            continue
        mask, blk_list = det.detect(img, proj=None)
        print(f"\n=== {Path(p).name}: {len(blk_list)} blocks ===")
        for i, blk in enumerate(blk_list):
            x1, y1, x2, y2 = blk.xyxy
            w, h = x2 - x1, y2 - y1
            shape = "tall" if h > w else "wide"
            print(
                f"  blk{i}: xyxy=({x1},{y1},{x2},{y2}) box={w}x{h}({shape})"
                f" src_is_vertical={blk.src_is_vertical}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
