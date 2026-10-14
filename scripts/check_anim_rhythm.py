"""演示动画节奏校验器 - 检查有效时间轴与光标计划的不变量。

两条校验线，都读**同一份有效解析**（渲染侧也用它，杜绝"检查读类属性、渲染
读实例覆写"的假绿灯）：

1. ``verify_timeline`` —— 一个场景的**有效时间轴**（``scripts/anim_kit.py::
   resolve_timeline``：本类手写 F_* 优先、否则由 ``RHYTHM`` 档位时长算出、
   再否则按 MRO 取继承值）：
   - ``N_FRAMES >= 10``（合理的最小动画时长），且严格大于 ``F_FADE``；
   - 存在的相邻段严格/非严格递增：
     ``F_PRESS < F_CLICK <= F_MOVE_START <= F_MOVE_END < F_FADE``
     （点击可以就地触发过渡，故 ``F_CLICK <= F_MOVE_START`` 放宽为可取等）；
   - ``EMPH_SEG`` 为 ``None``（哨兵：明暗互换跟随 MOVE 段，天然对齐）或二元组，
     显式给时必须与 ``F_MOVE_START`` 对齐。

2. ``verify_plan`` —— 一个 ``scripts/anim_kit.py::CursorPlan`` 的帧序：
   - 按下帧严格递增、落在 ``[1, N_FRAMES)``；
   - 航点到达帧严格递增、段起点不晚于到达帧、最后航点在片内；
   - 淡出帧不早于最后一次按下、落在片内。

标点场景（只声明 ``F_CLICK``/``F_MOVE_START``/``F_MOVE_END``）与 README 大画幅
场景（无 F_* 常量、节奏全在 ``CursorPlan``）分别由上面两条线覆盖。

集成到 ``scripts/verify.py`` 作为自动化检查步骤（类属性部分不需要 Qt）；
``scripts/check_anim_cursor.py`` 复用 ``verify_plan`` 校验已构造场景的光标计划。
"""

import os.path as osp
import sys

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
SCRIPT_DIR = osp.dirname(osp.abspath(__file__))
for p in (ROOT, SCRIPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


def verify_timeline(timeline: dict, name: str) -> list:
    """校验一份有效时间轴字典，返回错误列表（空 = 通过）。"""
    errors = []
    keys = ("F_PRESS", "F_CLICK", "F_MOVE_START", "F_MOVE_END", "F_FADE")

    # 不变量1: 帧数合理性
    n_frames = timeline.get("N_FRAMES")
    if n_frames is not None:
        if n_frames < 10:
            errors.append(f"{name}: N_FRAMES 过小 ({n_frames} < 10)")
        f_fade = timeline.get("F_FADE")
        if f_fade is not None and n_frames <= f_fade:
            errors.append(
                f"{name}: N_FRAMES ({n_frames}) 必须大于 F_FADE ({f_fade})")

    # 不变量2: 存在的相邻段顺序
    order = [k for k in keys if k in timeline]
    if len(order) >= 2:
        vals = [timeline[k] for k in order]
        ok = True
        for a, b, va, vb in zip(order, order[1:], vals, vals[1:]):
            relax = (a, b) == ("F_CLICK", "F_MOVE_START")
            if relax:
                ok = ok and va <= vb
            else:
                ok = ok and va < vb
        if not ok:
            errors.append(
                f"{name}: 时间轴顺序错误 - 期望 F_PRESS < F_CLICK <= "
                f"F_MOVE_START <= F_MOVE_END < F_FADE，当前 "
                + " < ".join(f"{k}={v}" for k, v in zip(order, vals)))

    # 不变量3: EMPH_SEG（None = 跟随 MOVE 段，天然对齐；显式给时必须是落在
    # 「点击之后、淡出之前」的合法窗口——三拍场景故意让它跟收缩段而非 MOVE 段）
    emph_seg = timeline.get("EMPH_SEG", None)
    if emph_seg is not None:
        if not isinstance(emph_seg, tuple) or len(emph_seg) != 2:
            errors.append(
                f"{name}: EMPH_SEG 必须是二元组或 None，"
                f"当前是 {type(emph_seg).__name__}")
        else:
            a, b = emph_seg
            if a >= b:
                errors.append(f"{name}: EMPH_SEG 区间非法（{a} → {b}）")
            f_click = timeline.get("F_CLICK")
            if f_click is not None and a < f_click:
                errors.append(
                    f"{name}: EMPH_SEG 起点 ({a}) 早于 F_CLICK ({f_click})")
            f_fade = timeline.get("F_FADE")
            if f_fade is not None and b > f_fade:
                errors.append(
                    f"{name}: EMPH_SEG 终点 ({b}) 晚于 F_FADE ({f_fade})")

    return errors


def verify_scene_timeline(scene_cls) -> list:
    """按有效解析校验场景类的时间轴（与渲染共用 ``resolve_timeline``）。"""
    from anim_kit import resolve_timeline, TIMELINE_KEYS

    timeline = resolve_timeline(scene_cls)
    errors = verify_timeline(timeline, scene_cls.__name__)

    # 不变量4: 声明了 RHYTHM 的场景，解析结果必须覆盖档位全部键（防止
    # 手写 F_* 把档位值静默覆盖成不一致的旧值却仍过顺序检查）
    rhythm = getattr(scene_cls, "RHYTHM", None)
    if rhythm is not None:
        missing = [k for k in TIMELINE_KEYS if k not in timeline]
        if missing:
            errors.append(f"{scene_cls.__name__}: 有效时间轴缺键 {missing}")
    return errors


def verify_plan(plan, name: str, n_frames: int) -> list:
    """校验 CursorPlan 的帧序（按下帧、航点、淡出）。返回错误列表。"""
    errors = []
    if plan is None:
        return errors

    presses = [f0 for f0, _at in plan._presses]
    for prev, cur in zip(presses, presses[1:]):
        if cur <= prev:
            errors.append(
                f"{name}: 按下帧未严格递增（{prev} → {cur}）")
    for f0 in presses:
        if not (1 <= f0 < n_frames):
            errors.append(f"{name}: 按下帧 {f0} 越界（应在 [1, {n_frames})）")

    arrives = [arrive for arrive, _seg0, _tgt in plan._stops]
    for prev, cur in zip(arrives, arrives[1:]):
        if cur <= prev:
            errors.append(f"{name}: 航点到达帧未严格递增（{prev} → {cur}）")
    for arrive, seg0, _tgt in plan._stops:
        if seg0 > arrive:
            errors.append(
                f"{name}: 航点段起点晚于到达帧（start={seg0} > arrive={arrive}）")
        if arrive > n_frames - 1:
            errors.append(f"{name}: 航点到达帧 {arrive} 超出片长 {n_frames}")

    if plan._fade is not None:
        fade_f, frames = plan._fade
        if frames < 1:
            errors.append(f"{name}: 淡出帧数过小（{frames}）")
        last_press = max(presses) if presses else 0
        if fade_f < last_press:
            errors.append(
                f"{name}: 淡出帧 {fade_f} 早于最后一次按下 {last_press}")
        if fade_f > n_frames - 1:
            errors.append(f"{name}: 淡出帧 {fade_f} 超出片长 {n_frames}")

    return errors


def main():
    """校验 gen_help_anim.py::SCENES 全部场景的有效时间轴。"""
    try:
        import gen_help_anim
    except ImportError as e:
        print(f"❌ 无法导入 gen_help_anim: {e}")
        sys.exit(1)

    all_errors = []
    scenes = gen_help_anim.SCENES
    for key, metadata in scenes.items():
        scene_cls = metadata.scene_class
        errors = verify_scene_timeline(scene_cls)
        all_errors.extend(errors)

    if all_errors:
        print("❌ 节奏校验失败:")
        for err in all_errors:
            print(f"  - {err}")
        sys.exit(1)
    print(f"✓ 所有 {len(scenes)} 个场景的有效时间轴校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
