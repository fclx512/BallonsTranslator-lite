"""演示动画图标落点 / 层级 / 裁剪校验器（真实构造场景后核对）。

覆盖三类会静默出错、又只能靠「渲染期真实几何」发现的契约：

1. **落点 = 实际几何**（``verify_waypoints``）：README 大画幅场景
   （``scripts/gen_readme_anim.py::FormatTourScene`` /
   ``scripts/gen_readme_showcase.py::ShowcaseScene``）的每个按下航点必须等于
   目标控件在场景坐标系的中心（``scripts/anim_kit.py::widget_scene_center``）；
   手绘色板格子 / 下拉行落点必须等于布点对象（``scripts/anim_kit.py::
   PopupPalette`` / ``scripts/anim_kit.py::PopupDropdown``）算出的位置；画布块
   落点等于 ``scripts/anim_kit.py::graphics_item_scene_rect`` 的中心。
   **先把布局推进到渲染期最终态再比对**（``ensurePolished`` + ``processEvents``
   + ``grab``），否则构造期读数自洽会把"漏激活布局"漏掉。

2. **覆盖层压在最上层**（``verify_overlay_stack``）：光标/涟漪/手绘弹层画在
   ``scripts/anim_kit.py::AnimScene`` 的透明覆盖层上，必须始终压过一切真实
   子控件。踩过的坑：徽标场景的单条文案是在 ``super().__init__()`` 之后才建
   的，天然排在覆盖层之上，会把光标盖住——``AnimScene.set_state`` 每帧重顶
   覆盖层修掉它。

3. **光标完整无缺**（``verify_cursor_paint``）：手绘下拉的 ``setClipRect``（裁
   列表项）与 ``setOpacity``（展开动画）若残留在同一个 painter 上，会把随后
   画的涟漪/光标裁进卡片或调暗；``AnimScene._paint_overlay_event`` 用
   save/restore 隔离置顶内容修掉。本检查在「下拉展开 + 光标在卡片外」的帧上
   核对卡片外仍有像素、且光标不透明达到自身应有值。

需要 QApplication 与主题（构造控件时才拿得到真实几何）；Qt 不可用时跳过，
不阻断 verify（与冒烟测试同口径）。
"""

import os
import os.path as osp
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
SCRIPT_DIR = osp.dirname(osp.abspath(__file__))
for _p in (SCRIPT_DIR, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TOL = 0.5  # 逻辑像素容差（几何都是整数/半整数的推导值，理论偏差为 0）


def _plan_points(scene) -> dict:
    """每个按下帧 → 该帧光标实际渲染坐标（等于点击落点）。"""
    pts = {}
    for f0, _at in scene.plan._presses:
        p = scene.plan.pose(f0).point
        pts[f0] = (p.x(), p.y())
    return pts


def _close(a, b):
    return abs(a[0] - b[0]) <= TOL and abs(a[1] - b[1]) <= TOL


def _resolve_layout(scene, app):
    """把控件几何推进到渲染期最终态（与 ``anim_kit.iter_frames`` 同路径）。"""
    scene.ensurePolished()
    app.processEvents()
    scene.set_state(0)
    app.processEvents()
    scene.grab()
    app.processEvents()


def _run_to(scene, frame, app):
    """从第 0 帧**顺序步进**到 ``frame``（场景状态靠精确帧号相等触发，
    跳跃式 set_state 会漏掉中间帧的开启/关闭事件）。"""
    for f in range(0, frame + 1):
        scene.set_state(f)
    app.processEvents()


def verify_waypoints(scene, tag, game, widget_beats, app) -> list:
    """落点（控件 / 手绘弹层 / 画布块）必须等于实际几何。返回错误列表。

    ``widget_beats`` = [(按下帧, 标签, 控件)]。
    **先强制布局解析到最终态再比对**：航点在构造期读几何，而控件真实几何在
    首次渲染（grab）时才由布局解析定下；只比对"同一时刻的两份读数"会把
    "构造期漏激活布局"这种偏差漏掉，必须让二者跨时刻相遇。
    """
    import anim_kit as K

    _resolve_layout(scene, app)

    errors = []
    pts = _plan_points(scene)
    for press_f, label, widget in widget_beats:
        got = pts.get(press_f)
        if got is None:
            errors.append(f"{tag}: 帧 {press_f} 无按下航点（{label}）")
            continue
        c = K.widget_scene_center(scene, widget)
        if not _close(got, (c.x(), c.y())):
            errors.append(
                f"{tag}: {label} 落点 ({got[0]:.1f},{got[1]:.1f}) != 控件中心 "
                f"({c.x():.1f},{c.y():.1f})")

    # 色板格子：落点 == 布点对象算出的中心
    cell = game.PALETTE.pick_point(scene._palette_origin)
    got = (scene._palette_cell_pos.x(), scene._palette_cell_pos.y())
    if not _close(got, (cell.x(), cell.y())):
        errors.append(
            f"{tag}: 色板格子落点 ({got[0]:.1f},{got[1]:.1f}) != 布点 "
            f"({cell.x():.1f},{cell.y():.1f})")

    # 下拉行：落点 == 行内文字左内缩处
    fbr = K.widget_scene_rect(scene, scene.familybox)
    row = game.DROPDOWN.row_point(
        fbr.topLeft(), fbr.height(), game.DROPDOWN.pick_idx,
        game.DROPDOWN.width(fbr.width()))
    got = (scene._kai_row_pos.x(), scene._kai_row_pos.y())
    if not _close(got, (row.x(), row.y())):
        errors.append(
            f"{tag}: 下拉行落点 ({got[0]:.1f},{got[1]:.1f}) != 行文字位 "
            f"({row.x():.1f},{row.y():.1f})")

    # 画布文本块：首段落点 == item 外接矩形中心
    br = K.graphics_item_scene_rect(scene, scene.view, scene.item)
    block_target = scene.plan._stops[0][2]
    block_target = block_target() if callable(block_target) else block_target
    got = (block_target.x(), block_target.y())
    if not _close(got, (br.center().x(), br.center().y())):
        errors.append(
            f"{tag}: 文本块落点 ({got[0]:.1f},{got[1]:.1f}) != 块中心 "
            f"({br.center().x():.1f},{br.center().y():.1f})")
    return errors


def verify_overlay_stack(scene, tag, frames, app) -> list:
    """覆盖层在 ``frames`` 的每一帧都必须是最上层子控件。返回错误列表。"""
    errors = []
    overlay = getattr(scene, "_overlay", None)
    if overlay is None:
        return [f"{tag}: 场景没有覆盖层"]
    for f in frames:
        scene.set_state(f)
        app.processEvents()
        kids = [c for c in scene.children() if c.isWidgetType()]
        if not kids or kids[-1] is not overlay:
            top = type(kids[-1]).__name__ if kids else "<无>"
            errors.append(
                f"{tag}: 帧 {f} 覆盖层不在最上层（{top} 压在其上）")
            break
    return errors


def verify_cursor_paint(scene, tag, frame, app, probes, *, clip_rect=None,
                        min_alpha=250) -> list:
    """光标必须画得完整：既不被手绘弹层裁掉，也不被其残留 opacity 调暗。

    覆盖层是手绘的（``scripts/anim_kit.py::AnimScene._paint_overlay_event``），
    置顶内容与光标共用一个 painter，踩过的两个坑：

    - 手绘下拉用 ``setClipRect`` 裁列表项，clip 残留会把随后画的涟漪/光标一起
      裁掉（``clip_rect`` = 弹层卡片；``probes`` 里落在卡片外的点必须有像素）；
    - 手绘下拉末尾 ``setOpacity(e)`` 残留会把光标调到 e 倍（展开动画中 e<1），
      光标在展开期变淡。

    ``AnimScene`` 用 save/restore 隔离置顶内容修掉两者。取
    ``scene._overlay.grab()``（只含覆盖层自己的像素），在每点 6×6 采样核对：
    落在 ``clip_rect`` 外的点 alpha 必须非零；``min_alpha`` 给光标体不透明度
    下限（栅格化边缘略低，故留 5 的余量）。
    """
    from qtpy.QtGui import QImage

    errors = []
    _run_to(scene, frame, app)
    img = scene._overlay.grab().toImage().convertToFormat(
        QImage.Format.Format_RGBA8888)
    scale = img.width() / scene.width()
    pose = scene.plan.pose(frame)
    expect = round(255 * pose.alpha)  # 光标本身应达到的不透明度
    for pt in probes:
        cx = int(round(pt.x() * scale))
        cy = int(round(pt.y() * scale))
        max_a = 0
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                x, y = cx + dx, cy + dy
                if 0 <= x < img.width() and 0 <= y < img.height():
                    max_a = max(max_a, (img.pixel(x, y) >> 24) & 0xFF)
        outside = clip_rect is not None and not clip_rect.contains(pt)
        if max_a == 0:
            where = "弹层矩形外" if outside else "光标处"
            errors.append(
                f"{tag}: 帧 {frame} 覆盖层在{where} ({pt.x():.0f},"
                f"{pt.y():.0f}) 无像素——光标/涟漪被手绘弹层裁掉")
        elif max_a + 5 < expect:
            errors.append(
                f"{tag}: 帧 {frame} 落在 ({pt.x():.0f},{pt.y():.0f}) 的光标不"
                f"透明度仅 {max_a}（应为 {expect}）——被弹层残留 opacity 调暗")
    return errors


def main():
    try:
        import anim_kit as K
        from anim_kit import init_app
        import gen_readme_anim as G1
        import gen_readme_showcase as G2
        from check_anim_rhythm import verify_plan
    except Exception as exc:  # noqa: BLE001 — Qt/主题不可用时跳过
        print(f"⏭ 动画光标落点: 环境不可用，跳过（{exc}）")
        return 0

    try:
        app = init_app()
    except Exception as exc:  # noqa: BLE001
        print(f"⏭ 动画光标落点: QApplication 初始化失败，跳过（{exc}）")
        return 0

    errors = []

    s1 = G1.FormatTourScene()
    s1._dpr = 2.0
    errors += verify_waypoints(s1, "format_tour", G1, [
        (G1.F_COLOR_OPEN, "colorPicker", s1.colorPicker),
        (G1.F_FAMILY_OPEN, "familybox", s1.familybox),
        (G1.F_ALIGN_PRESS, "alignCenter", s1.alignGroup.alignCenterChecker),
        (G1.F_ITALIC_PRESS, "italicBtn", s1.formatGroup.italicBtn),
        (G1.F_VERT_PRESS, "verticalChecker", s1.verticalChecker),
    ], app)
    errors += verify_plan(s1.plan, "format_tour", s1.N_FRAMES)
    # 下拉展开期间光标在卡片外，覆盖层必须有像素、且不被残留 opacity 调暗
    # （顺序步进到该帧——场景状态靠精确帧号触发，须在 stack 跳跃检查之前跑）
    fb1 = s1.familybox
    off1 = K.widget_scene_rect(s1, fb1).topLeft()
    errors += verify_cursor_paint(
        s1, "format_tour", G1.F_FAMILY_OPEN + 1, app,
        [s1.plan.pose(G1.F_FAMILY_OPEN + 1).point],
        clip_rect=G1.DROPDOWN.card_rect(off1, fb1.height(), fb1.width()))
    errors += verify_overlay_stack(
        s1, "format_tour", range(0, s1.N_FRAMES, 7), app)

    s2 = G2.ShowcaseScene()
    s2._dpr = 2.0
    errors += verify_waypoints(s2, "showcase", G2, [
        (G2.F_COLOR_OPEN, "colorPicker", s2.colorPicker),
        (G2.F_FONT_OPEN, "familybox", s2.familybox),
        (G2.F_VERT_PRESS, "verticalChecker", s2.verticalChecker),
    ], app)
    errors += verify_plan(s2.plan, "showcase", s2.N_FRAMES)
    fb2 = s2.familybox
    off2 = K.widget_scene_rect(s2, fb2).topLeft()
    errors += verify_cursor_paint(
        s2, "showcase", G2.F_FONT_OPEN + 1, app,
        [s2.plan.pose(G2.F_FONT_OPEN + 1).point],
        clip_rect=G2.DROPDOWN.card_rect(off2, fb2.height(), fb2.width()))
    errors += verify_overlay_stack(
        s2, "showcase", range(0, s2.N_FRAMES, 7), app)

    if errors:
        print("❌ 动画光标落点: 航点/层级/裁剪核对失败")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("✓ 动画图标落点: format_tour / showcase 航点对齐几何、覆盖层置顶且不被弹层裁剪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
