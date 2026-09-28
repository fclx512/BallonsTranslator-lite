"""mit_merge_textlines 方向判定合成矩阵（ysgyolo「Merge Text Lines」路径的复算台）。

修复背景：投票阈值 nv >= len(txtlns)//2 在单行块（len//2==0）时恒真，横排单行块
被误判竖排；修后为 nv >= max(len//2, 1)。本矩阵覆盖修复点及多行/混合/斜行场景，
钉住多行组的既有投票口径不被将来改动波及。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/direction_matrix.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from utils.textblock import mit_merge_textlines, sort_pnts

W, H = 1200, 1600


def hline(x, y, w=300, h=50, tilt_deg=0.0):
    """横排行四边形，可绕中心倾斜。"""
    pts = np.array(
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=float
    )
    if tilt_deg:
        c, s = np.cos(np.radians(tilt_deg)), np.sin(np.radians(tilt_deg))
        center = pts.mean(axis=0)
        pts = (pts - center) @ np.array([[c, s], [-s, c]]).T + center
    return pts


def vline(x, y, w=50, h=300):
    """竖排列四边形（一列）。"""
    return np.array(
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=float
    )


CASES = [
    # (说明, 行四边形列表, 期望 is_vertical)
    ("单行·横排",            [hline(100, 100)],                        False),
    ("单行·横排·斜8°",       [hline(100, 200, tilt_deg=8)],            False),
    ("单行·竖排",            [vline(100, 100)],                        True),
    ("单行·竖排·斜8°",       [hline(100, 100, w=50, h=300, tilt_deg=8)], True),
    ("两行·全横排",          [hline(100, 100), hline(100, 170)],       False),
    ("三行·全横排",          [hline(100, 100), hline(100, 170), hline(100, 240)], False),
    ("两列·全竖排",          [vline(100, 100), vline(170, 100)],       True),
    ("三列·全竖排",          [vline(100, 100), vline(170, 100), vline(240, 100)], True),
    # 混合组：钉住既有投票口径（len=2 平票判竖、len=3 一票即竖），修复不改变它们
    ("两行·混1竖1横(平票)",  [vline(100, 100), hline(200, 120)],       True),
    ("三行·1竖2横",          [vline(100, 100), hline(200, 120), hline(200, 190)], True),
]

fails = 0
for name, lines, expected in CASES:
    regions = mit_merge_textlines(lines, W, H)
    got = regions[0].src_is_vertical if regions else None
    ok = got == expected
    fails += not ok
    print(f"{'✅' if ok else '❌'} {name}: n_lines={len(lines)} -> {got} (期望 {expected})")

# sort_pnts 直测（ysgyolo 非合并路径与 ppocrv6 的逐框判定都走它）
for name, pts, expected in [
    ("sort_pnts·横排矩形", hline(0, 0), False),
    ("sort_pnts·竖排矩形", vline(0, 0), True),
    ("sort_pnts·横排斜8°", hline(0, 0, tilt_deg=8), False),
    ("sort_pnts·竖排斜8°", hline(0, 0, w=50, h=300, tilt_deg=8), True),
]:
    _, got = sort_pnts(pts)
    ok = got == expected
    fails += not ok
    print(f"{'✅' if ok else '❌'} {name}: -> {got} (期望 {expected})")

print(f"\n{'✅ 全部通过' if fails == 0 else f'❌ {fails} 项失败'}")
sys.exit(1 if fails else 0)
