"""字体自动取色探针：对样张跑 ppocrv6 检测 + 颜色提取，输出可视化与色值表。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/probe_color_extract.py <图片或目录> ... [--page N]

- 目录时取其中图片（按名排序），`--page N` 只跑第 N 张（0 基）。
- 结果写 `scripts/probes/out/color_extract/<页名>.png`：每块裁剪成小图拼成
  一页（只含 OCR 文本区，不含整页画面）+ 前景/背景色条 + 置信度标注，目视验收用。
- 终端同时打印每块 (fg, bg, conf, coverage) 表。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2
import numpy as np

OUT_DIR = Path(__file__).resolve().parent / "out" / "color_extract"

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def collect_images(args):
    imgs = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            imgs.extend(
                sorted(
                    f for f in p.iterdir() if f.suffix.lower() in IMG_EXTS
                )
            )
        elif p.suffix.lower() in IMG_EXTS:
            imgs.append(p)
        else:
            print(f"[skip] not an image: {p}")
    return imgs


def draw_montage(img, blk_list, colors, max_w=760, max_h=280, bar_h=18):
    """把每块裁剪成小图拼成一页图（只含文本区，不含整页画面）。

    竖排长条旋转 90° 成横条；每块下方是提取的前景/背景色条 + 置信度。
    缩放取「不超过 max_w × max_h」，保持长条仍可读。
    """
    tiles = []
    for blk, res in zip(blk_list, colors):
        x1, y1, x2, y2 = [int(v) for v in blk.xyxy]
        pad = 6
        im_h, im_w = img.shape[:2]
        crop = img[
            max(0, y1 - pad) : min(im_h, y2 + pad),
            max(0, x1 - pad) : min(im_w, x2 + pad),
        ].copy()
        if crop.size == 0:
            continue
        h, w = crop.shape[:2]
        if h > w * 2.5:
            crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
            h, w = crop.shape[:2]
        scale = min(max_w / w, max_h / h, 1.0)
        tw, th = max(24, int(w * scale)), max(16, int(h * scale))
        crop = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
        tile = np.full((th + bar_h, tw, 3), 40, np.uint8)
        tile[:th] = crop
        if res is not None:
            tile[th:, : max(4, tw // 2)] = res.frgb[::-1]
            tile[th:, max(4, tw // 2) :] = res.srgb[::-1]
            label = f"{res.confidence:.2f}"
        else:
            label = "--"
        cv2.putText(
            tile, label, (4, th + 13), cv2.FONT_HERSHEY_SIMPLEX,
            0.45, (255, 255, 255), 1, cv2.LINE_AA,
        )
        tiles.append(tile)
    if not tiles:
        return None
    cw = max(t.shape[1] for t in tiles) + 8
    ch = max(t.shape[0] for t in tiles) + 8
    cols = max(1, min(len(tiles), 2400 // cw))
    rows = (len(tiles) + cols - 1) // cols
    canvas = np.full((rows * ch, cols * cw, 3), 25, np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        canvas[r * ch : r * ch + t.shape[0], c * cw : c * cw + t.shape[1]] = t
    return canvas


def parse_palette(text):
    """--palette "#1b19d6,#ffffff" → RGB 三元组列表（模拟项目人工调板）。"""
    if not text:
        return None
    palette = []
    for part in text.split(","):
        part = part.strip().lstrip("#")
        if len(part) != 6:
            print(f"[skip] bad palette color: {part}")
            continue
        palette.append([int(part[i : i + 2], 16) for i in (0, 2, 4)])
    return palette or None


def main():
    args = sys.argv[1:]
    use_prob = "--no-prob" not in args
    if not use_prob:
        args.remove("--no-prob")
    detector_name = "ppocrv6_onnx"
    if "--detector" in args:
        i = args.index("--detector")
        detector_name = args[i + 1]
        del args[i : i + 2]
    palette = None
    if "--palette" in args:
        i = args.index("--palette")
        palette = parse_palette(args[i + 1])
        del args[i : i + 2]
        print(f"[probe] palette = {palette}")
    page_filter = None
    if "--page" in args:
        i = args.index("--page")
        page_filter = int(args[i + 1])
        del args[i : i + 2]
    elif "--pages" in args:
        # 0基闭区间，如 --pages 0:11
        i = args.index("--pages")
        lo, hi = (int(v) for v in args[i + 1].split(":"))
        del args[i : i + 2]
        page_filter = (lo, hi)
    if not args:
        print("usage: probe_color_extract.py <image or dir> [--page N]")
        return 1

    from modules.base import init_module_registries
    from utils.config import load_config

    load_config()
    init_module_registries()

    from utils.registries import TEXTDETECTORS

    det = TEXTDETECTORS.resolve_module(detector_name)()
    print(f"[probe] detector = {detector_name}")
    det.load_model()

    imgs = collect_images(args)
    if page_filter is not None:
        if isinstance(page_filter, tuple):
            lo, hi = page_filter
            imgs = imgs[lo : hi + 1]
        else:
            imgs = [imgs[page_filter]] if page_filter < len(imgs) else []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    from utils.color_extract import extract_block_colors

    for p in imgs:
        # 用仓库的 imread（PIL，RGB）：与项目管线 ``read_img`` 完全同口径。
        # 2026-10-03 教训：早先用 cv2.imread（BGR）掩盖了提取器与管线的
        # 通道口径差，探针全对、应用里颜色整体反转。
        from utils.io_utils import imread

        img = imread(str(p))
        if img is None:
            print(f"[skip] cannot read: {p}")
            continue
        mask, blk_list = det.detect(img, proj=None)

        # 探针侧独立复算每块的提取结果（检测器只落高置信块，这里全打出来）
        prob_full = getattr(det, "_last_probmap", None) if use_prob else None
        colors = []
        for blk in blk_list:
            res = extract_block_colors(img, blk.xyxy, prob=prob_full, palette=palette)
            colors.append(res)

        print(f"\n=== {p.name}: {len(blk_list)} blocks ===")
        for i, (blk, res) in enumerate(zip(blk_list, colors)):
            info = "None" if res is None else (
                f"fg=#{res.frgb[0]:02x}{res.frgb[1]:02x}{res.frgb[2]:02x} "
                f"bg=#{res.srgb[0]:02x}{res.srgb[1]:02x}{res.srgb[2]:02x} "
                f"conf={res.confidence:.2f} cov={res.coverage:.2f}"
            )
            print(f"  blk{i}: xyxy={[int(v) for v in blk.xyxy]} {info}")

        vis = draw_montage(img, blk_list, colors)
        if vis is None:
            continue
        cv2.imwrite(str(OUT_DIR / f"{p.stem}.png"), vis)

    print(f"\n[done] visualizations -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
