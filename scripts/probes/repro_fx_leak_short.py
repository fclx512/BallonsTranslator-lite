"""真机演练（精简版）：短文本 + 细描边，排除粗描边的视觉干扰，
目检「描边 + 变换」是否同时画出两份文字。

复用 mw_repro 合成工程后改写为 4 字短文本、stroke_width=0.08，
抓视图后裁剪条目附近区域出 PNG：
  scripts/probes/out/fx_short_{1_neutral,2_bend,3_projective}.png
"""

import os
import os.path as osp
import sys

_APP_ROOT = osp.dirname(osp.dirname(osp.dirname(osp.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)

_RAW_ARGV = list(sys.argv)
sys.argv = [sys.argv[0]]
_SCRIPTS_DIR = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, _SCRIPTS_DIR)
import mw_repro  # noqa: E402

OUT_DIR = osp.join(_APP_ROOT, "scripts", "probes", "out")


class _Args:
    pages = 1
    blocks = 1
    project = ""
    scenario = "none"
    no_panel = True
    no_show = False
    switch_rounds = 3
    confirm_delay = 400
    watchdog = 60


def grab_item(window, app, name):
    from qtpy.QtCore import QRectF

    app.processEvents()
    view = window.canvas.gv
    item = window.st_manager.textblk_item_list[0]
    scene_rect = item.mapToScene(item.boundingRect()).boundingRect()
    viewport = view.viewport()
    tl = view.mapFromScene(scene_rect.topLeft())
    br = view.mapFromScene(scene_rect.bottomRight())
    pix = viewport.grab()
    margin = 30
    crop = pix.copy(
        max(0, tl.x() - margin),
        max(0, tl.y() - margin),
        min(pix.width(), br.x() - tl.x() + 2 * margin),
        min(pix.height(), br.y() - tl.y() + 2 * margin),
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    crop.save(osp.join(OUT_DIR, f"fx_short_{name}.png"))
    full = viewport.grab()
    full.save(osp.join(OUT_DIR, f"fx_short_full_{name}.png"))
    print(
        f"[grab] {name} -> crop ({crop.width()}x{crop.height()}) "
        f"+ full ({full.width()}x{full.height()})",
        flush=True,
    )
    del QRectF


def dump_layers(window, app, tag):
    from qtpy.QtGui import QColor, QImage, QPainter
    from qtpy.QtWidgets import QStyleOptionGraphicsItem

    item = window.st_manager.textblk_item_list[0]
    br = item.boundingRect()
    w, h = int(br.width()) + 40, int(br.height()) + 40

    def paint_to(name, fn):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(255, 255, 255))
        p = QPainter(img)
        p.translate(20, 20)
        try:
            fn(p)
        except Exception as e:
            print(f"[layer] {name} failed: {e}", flush=True)
        finally:
            p.end()
        path = osp.join(OUT_DIR, f"fx_short_layer_{tag}_{name}.png")
        img.save(path)
        print(f"[layer] {name} -> {path}", flush=True)

    opt = QStyleOptionGraphicsItem()
    paint_to("native", lambda p: item.paint(p, opt, None))
    pm = item.effect_renderer.background_pixmap
    if pm is not None:
        pm.save(osp.join(OUT_DIR, f"fx_short_layer_{tag}_surface.png"))
        print(f"[layer] surface {pm.width()}x{pm.height()}", flush=True)
    else:
        print("[layer] surface is None", flush=True)
    renderer = getattr(item.geometry_controller, "surface_renderer", None)
    wp = renderer.cached_pixmap if renderer is not None else None
    if wp is not None:
        wp.save(osp.join(OUT_DIR, f"fx_short_layer_{tag}_warped.png"))
        print(f"[layer] warped {wp.width()}x{wp.height()}", flush=True)
    else:
        print("[layer] warped cache is None", flush=True)


def dump_geometry(window, app, tag):
    item = window.st_manager.textblk_item_list[0]
    er = item.effect_renderer
    gc = item.geometry_controller
    br = item.boundingRect()
    er_br = er.boundingRect()
    pm = er.background_pixmap
    sr = gc.source_rect()
    ds = item.layout.documentSize()
    print(
        f"[geo:{tag}] item_br=({br.x():.1f},{br.y():.1f},{br.width():.1f},"
        f"{br.height():.1f}) er_br=({er_br.x():.1f},{er_br.y():.1f},"
        f"{er_br.width():.1f},{er_br.height():.1f}) "
        f"src=({sr.x():.1f},{sr.y():.1f},{sr.width():.1f},{sr.height():.1f}) "
        f"doc={ds.width():.1f}x{ds.height():.1f} "
        f"pad={item.layout.effectPadding():.2f} "
        f"pm={'None' if pm is None else f'{pm.width()}x{pm.height()}'} "
        f"warp={gc.uses_surface_warp()} "
        f"pos=({item.x():.1f},{item.y():.1f})",
        flush=True,
    )


def main():
    import faulthandler

    faulthandler.enable()
    app, _QTimer = mw_repro._setup_qt(_Args())
    window = mw_repro._open_mainwindow(app, _Args())
    mw_repro._make_synthetic(window, app, _Args(), style="vertical-stroke")

    # 精简：短文本 + 细描边，排除粗描边视觉干扰；--long 保留长文本粗描边
    item = window.st_manager.textblk_item_list[0]
    blk = item.blk
    if "--long" in _RAW_ARGV:
        if "--no-spacing" in _RAW_ARGV:
            # 去掉字距 span（克隆漂移触发源）
            blk.rich_text = (
                "<p style=\"color:#222\">竖排描边演练：这一段文本足够长，"
                "会让竖排布局在真实字体下折出多列，从而触发描边克隆文档"
                "与原布局之间的行结构漂移，用于复现快速切图时的闪退。"
                "继续补充更多文字，确保即使单列容量较大也能折出至少三列。"
                "</p>"
            )
            item.setHtml(blk.rich_text)
            window.st_manager.updateSceneTextitems()
            item = window.st_manager.textblk_item_list[0]
    elif True:
        blk.rich_text = "<p style=\"color:#222\">描边变换测试</p>"
        item.setHtml(blk.rich_text)
        blk.fontformat.stroke_width = 0.08
        window.st_manager.updateSceneTextitems()
        item = window.st_manager.textblk_item_list[0]
        item.setStrokeWidth(0.08)
    app.processEvents()

    from utils.fontformat import (
        BendTextTransform,
        ProjectiveTextTransform,
        TextTransformStack,
        TextTransformState,
    )
    from utils.text_effects import (
        SolidPaint,
        StrokeEffect,
        TextEffectStack,
    )

    if "--fx" in _RAW_ARGV:
        # 描边走效果栈（StrokeEffect），对齐真实样式系统的描边路径
        blk.fontformat.text_effects = TextEffectStack(
            effects=(StrokeEffect(width=0.08, paint=SolidPaint((255, 0, 0))),)
        )
        item.repaint_background()
        app.processEvents()

    bend = TextTransformState(
        TextTransformStack(transforms=(BendTextTransform(bend=0.3),)), 0.0
    )
    proj_tf = TextTransformState(
        TextTransformStack(transforms=(ProjectiveTextTransform(rotation_y=40),)),
        0.0,
    )

    if "--trace" in _RAW_ARGV:
        # 打点 warp 源捕获时各层的绘制坐标
        from ui.text_engine.effects.renderer import (
            TextEffectRenderer as _ER,
        )
        from ui.text_engine.rendering.surface import (
            NonlinearTextSurfaceRenderer as _SR,
        )

        orig_cap = _SR._capture_source

        @staticmethod
        def traced_capture(source_rect, scale, option, paint_source):
            def wrapped(painter, opt, widget):
                t = painter.transform()
                print(
                    f"[trace:capture] src=({source_rect.x():.1f},"
                    f"{source_rect.y():.1f},{source_rect.width():.1f},"
                    f"{source_rect.height():.1f}) scale={scale} "
                    f"m31={t.m31():.2f} m32={t.m32():.2f}",
                    flush=True,
                )
                paint_source(painter, opt, widget)

            pixmap = orig_cap(source_rect, scale, option, wrapped)
            traced_capture._n = getattr(traced_capture, "_n", 0) + 1
            if traced_capture._n <= 4:
                path = osp.join(
                    OUT_DIR, f"fx_short_src_capture_{traced_capture._n}.png"
                )
                pixmap.save(path)
                print(f"[trace:capture] saved -> {path}", flush=True)
            return pixmap

        _SR._capture_source = traced_capture

        orig_surf = _ER._draw_surface_pixmap

        @staticmethod
        def traced_surf(painter, destination, pixmap, render_scale):
            t = painter.transform()
            print(
                f"[trace:surface] dest=({destination.x():.1f},"
                f"{destination.y():.1f},{destination.width():.1f},"
                f"{destination.height():.1f}) pm={pixmap.width()}x"
                f"{pixmap.height()} m31={t.m31():.2f} m32={t.m32():.2f}",
                flush=True,
            )
            orig_surf(painter, destination, pixmap, render_scale)

        _ER._draw_surface_pixmap = traced_surf

        from ui.textitem import TextBlkItem as _TBI

        orig_native = _TBI._paint_native

        def traced_native(self, painter, option, widget):
            t = painter.transform()
            print(
                f"[trace:native] m31={t.m31():.2f} m32={t.m32():.2f} "
                f"br=({self.boundingRect().x():.1f},"
                f"{self.boundingRect().y():.1f})",
                flush=True,
            )
            orig_native(self, painter, option, widget)

        _TBI._paint_native = traced_native

    if "--split" in _RAW_ARGV:
        # 二值实验：源捕获分别只画效果面 / 只画原生面，比对谁偏移
        from ui.text_engine.rendering.surface import (
            NonlinearTextSurfaceRenderer as _SR,
        )

        orig_cap = _SR._capture_source
        _part = {"mode": "full"}

        def _partial_capture(source_rect, scale, option, paint_source):
            mode = _part["mode"]
            er = item.effect_renderer

            def wrapped(painter, opt, widget):
                if mode == "fx_only":
                    er._draw_effects(painter)
                    return
                if mode == "native_only":
                    from qtpy.QtWidgets import QGraphicsTextItem

                    item.effect_renderer.in_graphics_paint = True
                    try:
                        QGraphicsTextItem.paint(item, painter, opt, widget)
                    finally:
                        item.effect_renderer.in_graphics_paint = False
                    return
                paint_source(painter, opt, widget)

            pixmap = orig_cap(source_rect, scale, option, wrapped)
            _part["n"] = _part.get("n", 0) + 1
            if _part.get("save"):
                path = osp.join(
                    OUT_DIR, f"fx_short_src_{mode}.png"
                )
                pixmap.save(path)
                print(f"[split:{mode}] saved -> {path}", flush=True)
            return pixmap

        _SR._capture_source = staticmethod(_partial_capture)

        item.set_text_transform(bend)
        item.repaint_background()
        app.processEvents()
        renderer = item.geometry_controller.surface_renderer
        for mode in ("full", "fx_only", "native_only"):
            _part.update(mode=mode, n=0, save=True)
            if renderer is not None:
                renderer.invalidate_surface()
            item.update()
            app.processEvents()
        # Restore the real composite before the final viewport screenshot.
        _part.update(mode="full", save=False)
        if renderer is not None:
            renderer.invalidate_surface()
        item.update()
        app.processEvents()
        grab_item(window, app, "2_bend")
        dump_geometry(window, app, "bend")
        print("[done]", flush=True)
        return

    if "--fx-after" in _RAW_ARGV:
        # 用户真实顺序：先提交变换，后开描边（效果卡路径）
        item.set_text_transform(bend)
        item.repaint_background()
        app.processEvents()
        grab_item(window, app, "8_fx_after_bend")
        fx_session = window.textPanel.formatpanel.effects_editor
        fx_session.replace_targets([item])
        fx_session.add_effect("stroke")
        app.processEvents()
        grab_item(window, app, "9_fx_added")
        # 再把描边参数补齐并提交
        blk.fontformat.text_effects = TextEffectStack(
            effects=(StrokeEffect(width=0.08, paint=SolidPaint((255, 0, 0))),)
        )
        item.repaint_background()
        app.processEvents()
        grab_item(window, app, "10_fx_committed")
        dump_geometry(window, app, "fx_after")

        # 逐缓存强制失效，定位漏失效的环节
        item.effect_renderer._mark_effect_cache_dirty()
        item.effect_renderer.repaint_background()
        app.processEvents()
        grab_item(window, app, "11_fx_cache_invalidated")

        renderer = item.geometry_controller.surface_renderer
        if renderer is not None:
            renderer.invalidate_surface()
        item.repaint_background()
        app.processEvents()
        grab_item(window, app, "12_warp_cache_invalidated")

        item.setCacheMode(item.CacheMode.NoCache)
        item.update()
        app.processEvents()
        grab_item(window, app, "13_item_nocache")
        if "--layers" in _RAW_ARGV:
            dump_layers(window, app, "fx_after")
        print("[done]", flush=True)
        return

    grab_item(window, app, "1_neutral")

    item.set_text_transform(bend)
    item.repaint_background()
    app.processEvents()
    grab_item(window, app, "2_bend")
    if "--fx" in _RAW_ARGV or "--fx-after" in _RAW_ARGV:
        dump_geometry(window, app, "bend")
    if "--layers" in _RAW_ARGV:
        dump_layers(window, app, "bend")

    item.set_text_transform(proj_tf)
    item.repaint_background()
    app.processEvents()
    grab_item(window, app, "3_projective")
    if "--layers" in _RAW_ARGV:
        dump_layers(window, app, "projective")

    if "--grid" in _RAW_ARGV:
        # 用户真实路径：变换面板 add_transform('grid') → 网格编辑
        # preview → commit，复现截图里的四角手柄自由变形
        session = window.textPanel.formatpanel.text_transform_editor
        item.set_text_transform(TextTransformState(TextTransformStack(), 0.0))
        item.repaint_background()
        session.replace_targets([item])
        session.add_transform("grid")
        app.processEvents()
        grab_item(window, app, "4_grid_added")

        session.begin_grid_edit(0)
        # 拖动四角（归一化坐标，行优先：左上、右上、左下、右下）
        pts = [(0.15, 0.05), (0.95, 0.2), (0.05, 0.9), (0.9, 0.98)]
        session.preview_grid_points(0, pts)
        app.processEvents()
        grab_item(window, app, "5_grid_preview")

        session.commit_grid_points(0, pts)
        app.processEvents()
        grab_item(window, app, "6_grid_commit")

        # 重建路径：updateSceneTextitems 会按 blk 现状重建条目
        # （对应换页/重选/样式应用后的场景），检查是否出现重复条目
        from ui.textitem import TextBlkItem
        window.st_manager.updateSceneTextitems()
        app.processEvents()
        items_now = [
            it for it in window.st_manager.textblk_item_list if it.scene()
        ]
        print(
            f"[rebuild] blkitem count={len(items_now)}, "
            f"scene items={len(window.canvas.items())}",
            flush=True,
        )
        grab_item(window, app, "7_rebuild")
        if "--layers" in _RAW_ARGV:
            dump_layers(window, app, "grid")

    print("[done]", flush=True)


if __name__ == "__main__":
    main()
