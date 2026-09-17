"""区域再检测的「新块落点」回归台：真工程上核对插入顺序，并留档候选判据对比。

**为什么要常驻**（`docs/技术实现/区域再检测_设计与实现.md` §5）：落点判据是几何
启发式，改一次就得拿真实页面复核一次——合成单测只能锁住已知分支，判断"这片漫画
到底该怎么读"必须靠真检测器的输出 + 已排好的页序。起源是「新块总往最前面插」那次
排查，当时用三个一次性脚本定位（缓存检测结果 → 离线比候选判据 → 端到端核对），
本工具把它们并成一条流水。

**判据口径**：把页上**每个已有框自己**当作用户拉框区域重跑检测。前提是"该页已有
顺序正确"（本功能在误识别与顺序都处理完之后才人工触发）——所以理想结果是新块
**连续**落回被替换块原来的下标。命中数就是回归分。

**两段式**（检测要建 ONNX 会话，分钟级；判据迭代是秒级）：

1. `--detect`：真检测一遍，结果写进缓存（默认 `tmp/region_redetect_plans.json`，
   `tmp/` 被 gitignore）并跑**端到端**核对（走 `RegionRedetect.plan` +
   `RegionRedetect.build_page`，最强口径）；
2. 不带 `--detect`：读缓存**离线**核对（只走 `insert_index` / `page_direction`
   ——落点逻辑本身），不建 ONNX 会话。缓存缺失时提示加 `--detect`。

`--candidates` 额外打印候选判据的对比表（含"逐块各自算下标"与"折线投影"两个被
否掉的方案），改判据时先用它看新方案是否真的赢过现役，再动代码。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe scripts/region_redetect_order.py --detect
    ./ballontrans_pylibs_win/python.exe scripts/region_redetect_order.py --candidates

退出码：全部命中 0，有未命中 1（可挂进人工门禁）。
"""

import argparse
import json
import math
import os
import os.path as osp
import sys

_APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)

from utils.block_geometry import poly_bands, poly_center  # noqa: E402
from utils.config import load_config  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402

DEFAULT_PROJECT = osp.join("projects", "004_819b9e93")
DEFAULT_CACHE = osp.join("tmp", "region_redetect_plans.json")


class _Blk:
    """最小 ``TextBlock`` 替身：判据只读 ``lines`` 与 ``src_is_vertical``。"""

    def __init__(self, lines, vertical=True):
        self.lines = lines
        self.src_is_vertical = vertical


# ── 落点判据（现役走 ui/region_redetect.py，候选是本文件的对照实现）──


def _shipped(news, kept, rtl):
    from ui.region_redetect import insert_index

    idx, _ = insert_index(news, kept, rtl)
    return [idx + k for k in range(len(news))]


def _union_band(blocks):
    bands = [b for b in (poly_bands(b) for b in blocks) if b]
    return [
        min(b[0] for b in bands),
        min(b[1] for b in bands),
        max(b[2] for b in bands),
        max(b[3] for b in bands),
    ]


def _mean_center(blocks):
    cs = [c for c in (poly_center(b) for b in blocks) if c]
    return (sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs))


def _rule(center, other, rtl, mode):
    """``center`` 处的新块是否应排在 ``other`` 之前。"""
    band = poly_bands(other)
    if band is None:
        return None
    if mode == "rows":
        # 现役口径：先纵向排行，同一行内才比 x
        if center[1] > band[3]:
            return False
        if center[1] < band[1]:
            return True
    elif mode == "band":  # 候选：按 y 带是否重叠决定比 x 还是比 y
        if not (band[1] <= center[1] <= band[3]):
            return center[1] < band[1]
    else:
        raise ValueError(mode)
    oc = poly_center(other)
    if oc is None:
        return None
    return center[0] > oc[0] if rtl else center[0] < oc[0]


def _scan(center, kept, rtl, mode):
    for i, other in enumerate(kept):
        if _rule(center, other, rtl, mode) is True:
            return i
    return len(kept)


def _per_block(news, kept, rtl):
    """被否掉的方案：逐个新块各自算下标（会让同片区域的框散开）。"""
    blocks, out = list(kept), []
    for nb in news:
        idx = _scan(_mean_center([nb]), blocks, rtl, "rows")
        blocks.insert(idx, nb)
        out.append(idx)
    return out


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-9:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _path_proj(news, kept, rtl):
    """被否掉的方案：已有块中心连成折线，组中心投影上去取线段下标。"""
    centers = [poly_center(b) for b in kept]
    p = _mean_center(news)
    if len(centers) < 2:
        return [0 if len(centers) == 0 or p[1] < centers[0][1] else 1] * len(news)
    i = min(range(len(centers) - 1), key=lambda k: _seg_dist(p, centers[k], centers[k + 1]))
    return [i + 1] * len(news)


def _group_band(news, kept, rtl):
    """候选：整组用一个落点，但代表点取并集包围盒中心。"""
    idx = _scan(_mean_center(news), kept, rtl, "band")
    return [idx + k for k in range(len(news))]


def _shipped_mirror(news, kept, rtl):
    """候选：现役口径的镜像实现（用来分辨"改了判据"还是"改了调用方式"）。"""
    idx = _scan(_mean_center(news), kept, rtl, "rows")
    return [idx + k for k in range(len(news))]


CANDIDATES = {
    "现役（ui/region_redetect.py）": _shipped,
    "· 口径镜像": _shipped_mirror,
    "逐块各自算": _per_block,
    "折线投影": _path_proj,
    "包围盒中心 + y 带": _group_band,
}


# ── 目标枚举与期望值 ──────────────────────────────────────────────


def _expected(index, replaced, added):
    """期望落点＝被替换块在"保留块"列表里的位置（新块整组从这里开始连续排）。

    目标枚举就是"把每个已有框自己当作拉框区域"，见模块 docstring 的判据口径。
    """
    start = sum(1 for k in range(index) if k not in set(replaced))
    return list(range(start, start + added))


def _page_direction(kept):
    from ui.region_redetect import page_direction

    return page_direction(kept)


# ── 检测（慢）与缓存 ─────────────────────────────────────────────


def run_detect(proj, pages, cache_path):
    """真检测一遍，写缓存，返回端到端核对结果。"""
    load_config()
    from modules.base import init_module_registries

    init_module_registries()
    from ui.region_redetect import RegionRedetect

    task = RegionRedetect(proj)
    cache = {"pages": {}}
    rows = []
    for page in pages:
        proj.set_current_img(page)
        raw = list(proj.pages.get(page) or [])
        entries = []
        for i, old in enumerate(raw):
            rect = [int(v) for v in old.xyxy]
            plan = task.plan(page, rect)
            entries.append(
                {
                    "target": i,
                    "skip": plan.skip,
                    "replaced": plan.replaced_indices,
                    "new": [
                        {"lines": b.lines, "vertical": bool(b.src_is_vertical)}
                        for b in plan.new_blocks
                    ],
                }
            )
            if plan.skip:
                print(f"  {page} [{i}] 跳过：{plan.skip}")
                continue
            report = task.build_page(plan)
            got = [j for j, _ in report["inserted"]]
            exp = _expected(i, plan.replaced_indices, len(plan.new_blocks))
            rows.append((page, i, exp, got, len(plan.new_blocks), report["inserted"][0][1]))
        cache["pages"][page] = entries
    task.unload_detector()
    if cache_path:
        os.makedirs(osp.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        print(f"缓存写入 {cache_path}")
    return rows


def run_offline(proj, pages, cache_path, candidates=False):
    """读缓存，只跑落点逻辑（不建 ONNX 会话）。"""
    if not osp.isfile(cache_path):
        print(f"缓存不存在：{cache_path}（先跑一次 --detect）")
        return None
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)
    rows = []
    for page in pages:
        raw = list(proj.pages.get(page) or [])
        for entry in cache.get("pages", {}).get(page, []):
            if entry.get("skip"):
                continue
            i = entry["target"]
            replaced = set(entry["replaced"])
            kept = [b for k, b in enumerate(raw) if k not in replaced]
            news = [_Blk(b["lines"], b.get("vertical", True)) for b in entry["new"]]
            rtl, _ = _page_direction(kept)
            exp = _expected(i, entry["replaced"], len(news))
            got = _shipped(news, kept, rtl)
            extra = {}
            if candidates:
                extra = {
                    name: fn(news, kept, rtl) for name, fn in CANDIDATES.items()
                }
            rows.append((page, i, exp, got, len(news), rtl, extra))
    return rows


# ── 输出 ─────────────────────────────────────────────────────────


def report(rows, candidates=False):
    ok = 0
    for row in rows:
        page, i, exp, got, n, *rest = row
        hit = got == exp
        ok += hit
        mark = "OK " if hit else "X  "
        detail = rest[0] if rest else ""
        print(f"{mark}{page} [{i}] 期望={exp} 落点={got} n={n} {detail}")
    total = len(rows)
    print(f"\n落回原位 {ok}/{total}")
    if candidates and rows and rows[0][-1]:
        names = list(CANDIDATES)
        width = max(len(n) for n in names) + 2
        print("\n候选判据对比：")
        print(f"{'页 [目标]':<18}{'期望':<14}| " + " | ".join(n.rjust(width) for n in names))
        score = {n: 0 for n in names}
        for row in rows:
            page, i, exp, got, n, rtl, extra = row
            cells = []
            for name in names:
                idle = extra[name]
                hit = idle == exp
                score[name] += hit
                cells.append((str(idle) + (" OK" if hit else " X")).rjust(width))
            print(f"{page} [{i}] 期望={exp} | " + " | ".join(cells))
        print("命中:", {k: f"{v}/{total}" for k, v in score.items()})
    return ok, total


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project", metavar="DIR", default=DEFAULT_PROJECT, help="项目目录"
    )
    parser.add_argument(
        "--pages", metavar="A,B", help="只查这些页（逗号分隔页名）；缺省＝全部页"
    )
    parser.add_argument(
        "--detect", action="store_true", help="真跑检测（建 ONNX 会话，分钟级）并刷新缓存"
    )
    parser.add_argument(
        "--candidates", action="store_true", help="打印候选判据对比表（离线）"
    )
    parser.add_argument("--cache", metavar="FILE", default=DEFAULT_CACHE, help="检测结果缓存路径")
    return parser.parse_args()


def main():
    args = _parse_args()
    proj = ProjImgTrans(directory=args.project)
    proj.load(args.project)
    pages = (
        [p.strip() for p in args.pages.split(",") if p.strip()]
        if args.pages
        else list(proj.pages)
    )
    print(f"项目 {args.project}，页 {pages}")
    if args.detect:
        rows = run_detect(proj, pages, args.cache or None)
        if rows:
            report(rows)
        return 0
    rows = run_offline(proj, pages, args.cache, candidates=args.candidates)
    if rows is None:
        return 2
    ok, total = report(rows, candidates=args.candidates)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
