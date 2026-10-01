"""真机演练：字体变换 + 特效组合的「双文本」复现抓屏。

复用 scripts/mw_repro.py 的合成工程，逐形态对真实画布截图：
  1) 中性块（基线）
  2) 弯曲变换
  3) 弯曲 + 噪点滤镜（拥有文字正面的效果）
  4) 3 的状态下缩放 200% / 50%
  5) 预览拖动（preview=True）→ 提交
  6) 撤销（Ctrl+Z 走画布栈）

产物：scripts/probes/out/fx_leak_*.png，供目检是否同时出现两份文字。
"""

import os
import os.path as osp
import sys

_APP_ROOT = osp.dirname(osp.dirname(osp.dirname(osp.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)

_RAW_ARGV = list(sys.argv)
sys.argv = [sys.argv[0]]  # 防止 mw_repro 的 argparse 吃到本脚本参数
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


def QGraphicsTextItem_paint(item, painter):
    from qtpy.QtWidgets import QStyleOptionGraphicsItem
    from qtpy.QtGui import QTextDocument
    opt = QStyleOptionGraphicsItem()
    # 直绘文档层，绕过 item.paint 的组合包装
    QTextDocument_painter = painter
    doc = item.document()
    doc.documentLayout().draw(QTextDocument_painter, None) if False else None
    from qtpy.QtGui import QAbstractTextDocumentLayout
    ctx = QAbstractTextDocumentLayout.PaintContext()
    item.document().documentLayout().draw(painter, ctx)


def grab_view(window, app, name, tag=""):
    from qtpy.QtWidgets import QGraphicsView

    app.processEvents()
    view = window.canvas.gv
    assert isinstance(view, QGraphicsView)
    pix = view.grab()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = osp.join(OUT_DIR, f"fx_leak{tag}_{name}.png")
    pix.save(path)
    print(f"[grab] {name} -> {path} ({pix.width()}x{pix.height()})", flush=True)


def main():
    import faulthandler

    tag = "_stroke" if "--stroke" in _RAW_ARGV else "_nostroke"

    faulthandler.enable()
    app, _QTimer = mw_repro._setup_qt(_Args())
    window = mw_repro._open_mainwindow(app, _Args())
    vertical = "--vertical" in _RAW_ARGV
    mw_repro._make_synthetic(
        window, app, _Args(),
        style="vertical-stroke" if vertical else "plain",
    )
    if "--no-spacing" in _RAW_ARGV:
        # 去掉竖排富文本里的字距 span（克隆漂移触发源）
        for blk2 in window.imgtrans_proj.pages[
            window.imgtrans_proj.current_img
        ]:
            blk2.rich_text = "<p style=\"color:#222\">竖排描边演练：这一段文本足够长，会让竖排布局在真实字体下折出多列，从而触发描边文档与原布局之间的行结构漂移，用于复现快速切图时的闪退。继续补充更多文字，确保即使单列容量较大也能折出至少三列。</p>"
        window.st_manager.updateSceneTextitems()
    app.processEvents()

    from utils.fontformat import (
        BendTextTransform,
        TextTransformStack,
        TextTransformState,
    )
    from utils.text_effects import (
        FilterEffect,
        SolidPaint,
        StrokeEffect,
        TextEffectStack,
    )

    item = window.st_manager.textblk_item_list[0]
    blk = item.blk
    if "--no-stroke" in _RAW_ARGV:
        # 隔离变量：无描边的竖排块是否也重影
        blk.fontformat.stroke_width = 0.0
        item.setStrokeWidth(0.0)
        item.repaint_background()
    stroke = StrokeEffect(width=0.1, paint=SolidPaint((255, 0, 0)))
    noise = FilterEffect(
        "builtin:noise",
        params={"amount": 1.0, "mode": "monochrome", "seed": 0},
    )
    bend = TextTransformState(
        TextTransformStack(transforms=(BendTextTransform(bend=0.3),)), 0.0
    )

    def layout_metrics(label):
        lay = item.layout
        ds = lay.documentSize()
        print(
            f"[metrics:{label}] doc={ds.width():.1f}x{ds.height():.1f} "
            f"max={lay.max_width:.1f}x{lay.max_height:.1f} "
            f"avail={lay.available_width:.1f}x{lay.available_height:.1f} "
            f"pad={lay._effect_padding:.2f} "
            f"lines={getattr(lay, 'lineCount', lambda: -1)()}",
            flush=True,
        )
        return ds

    def clone_metrics(label):
        from qtpy.QtGui import QTextDocument, QTextCursor
        from ui.text_engine.vertical_layout import (
            VerticalTextDocumentLayout as EngineVerticalTextDocumentLayout,
        )
        from ui.text_engine.horizontal_layout import (
            HorizontalTextDocumentLayout,
        )
        from utils.config import pcfg as _pcfg

        doc = QTextDocument()
        doc.setUndoRedoEnabled(False)
        doc.setDocumentMargin(item.layout.effectPadding())
        doc.setDefaultFont(item.document().defaultFont())
        doc.setHtml(item.document().toHtml())
        doc.setDefaultTextOption(item.document().defaultTextOption())
        if blk.fontformat.vertical:
            lay = EngineVerticalTextDocumentLayout(doc, blk.fontformat)
            lay.punctuation_position = _pcfg.punctuation_position
            lay.halfwidth_jp_corner_brackets = _pcfg.halfwidth_jp_corner_brackets
        else:
            lay = HorizontalTextDocumentLayout(doc, blk.fontformat)
        lay._draw_offset = item.layout._draw_offset
        lay.setMaxSize(item.layout.max_width, item.layout.max_height, False)
        doc.setDocumentLayout(lay)
        lay.reLayout() if hasattr(lay, 'reLayout') else None
        ds = lay.documentSize()
        print(
            f"[clone:{label}] doc={ds.width():.1f}x{ds.height():.1f}",
            flush=True,
        )
        return ds

    layout_metrics("neutral")
    clone_metrics("neutral")

    grab_view(window, app, "1_neutral", tag)

    item.set_text_transform(bend)
    item.repaint_background()
    layout_metrics("bend")
    clone_metrics("bend")
    grab_view(window, app, "2_bend", tag)
    if "--subtract" in _RAW_ARGV:
        # 屏蔽效果面后单画正面（warp 仍生效），再与完整帧对齐比较
        from qtpy.QtCore import QRectF as _QRF2
        from qtpy.QtGui import QImage as _QI2, QColor as _QC2, QPainter as _QP2
        from qtpy.QtWidgets import QStyleOptionGraphicsItem as _QO2
        orig_draw = item.effect_renderer._draw_effects
        item.effect_renderer._draw_effects = lambda *a, **k: None
        try:
            br3 = item.boundingRect()
            img3 = _QI2(int(br3.width()) + 80, int(br3.height()) + 80,
                        _QI2.Format.Format_ARGB32_Premultiplied)
            img3.fill(_QC2(255, 255, 255))
            p3 = _QP2(img3)
            p3.translate(40, 40)
            opt3 = _QO2()
            item.paint(p3, opt3, None)
            p3.end()
            img3.save(osp.join(OUT_DIR, f"fx_leak{tag}_face_only.png"))
            print("[sub] face_only saved", flush=True)
        finally:
            item.effect_renderer._draw_effects = orig_draw
        # 位移测量：把完整帧与 face_only 帧二值化后找最大互相关偏移
        import numpy as np
        a = _QI2(osp.join(OUT_DIR, f"fx_leak{tag}_2_bend.png" if False else "x") ) if False else None
        full = _QI2(int(item.boundingRect().width()) + 80, int(item.boundingRect().height()) + 80,
                    _QI2.Format.Format_ARGB32_Premultiplied)
        full.fill(_QC2(255, 255, 255))
        p4 = _QP2(full)
        p4.translate(40, 40)
        item.paint(p4, _QO2(), None)
        p4.end()
        def _gray(img):
            g = img.convertToFormat(_QI2.Format.Format_Grayscale8)
            b = bytes(g.constBits().asarray(g.sizeInBytes()))
            return np.frombuffer(b, dtype=np.uint8).reshape(
                g.height(), g.bytesPerLine()
            )[:, : g.width()]
        arr_full = _gray(full)
        arr_face = _gray(img3)
        ink_full = arr_full < 128
        ink_face = arr_face < 128
        best = (1e18, 0, 0)
        for dy in range(-30, 31, 2):
            for dx in range(-30, 31, 2):
                shifted = np.roll(np.roll(ink_face, dy, axis=0), dx, axis=1)
                xor = np.logical_xor(ink_full, shifted).sum()
                if xor < best[0]:
                    best = (int(xor), dx, dy)
        print(f"[sub] best alignment shift dx={best[1]} dy={best[2]} xor={best[0]} "
              f"(no-shift xor={np.logical_xor(ink_full, ink_face).sum()})", flush=True)
    if "--layers" in _RAW_ARGV:
        # 分层取证：效果面光栅单独存图 + 不经扭曲的 item 直绘
        pm = item.effect_renderer.background_pixmap
        if pm is not None:
            pm.save(osp.join(OUT_DIR, f"fx_leak{tag}_layer_surface.png"))
            print(f"[layer] surface {pm.width()}x{pm.height()}", flush=True)
        else:
            print("[layer] surface is None", flush=True)
        from qtpy.QtCore import QRectF as _QRF
        from qtpy.QtGui import QImage as _QImg, QColor as _QColor, QPainter as _QP
        from qtpy.QtWidgets import QStyleOptionGraphicsItem as _QOpt
        br2 = item.boundingRect()
        _opt = _QOpt()
        for lname, fn in (
            ("native", lambda p2: item.paint(p2, _opt, None)),
            ("clone", item.effect_renderer._paint_cloned_document_stroke),
        ):
            img2 = _QImg(int(br2.width()) + 40, int(br2.height()) + 40,
                         _QImg.Format.Format_ARGB32_Premultiplied)
            img2.fill(_QColor(255, 255, 255))
            p2 = _QP(img2)
            p2.translate(20, 20)
            try:
                fn(p2)
            except Exception as e:
                print(f"[layer] {lname} failed: {e}", flush=True)
            finally:
                p2.end()
            img2.save(osp.join(OUT_DIR, f"fx_leak{tag}_layer_{lname}.png"))
            print(f"[layer] {lname} saved", flush=True)
        wp = item.geometry_controller.surface_renderer.cached_pixmap
        if wp is not None:
            wp.save(osp.join(OUT_DIR, f"fx_leak{tag}_layer_warped.png"))
            print(f"[layer] warped cache {wp.width()}x{wp.height()}", flush=True)
        else:
            print("[layer] warped cache is None", flush=True)
        img = _QImg(item.boundingRect().width() * 2 + 80,
                    item.boundingRect().height() * 2 + 80,
                    _QImg.Format.Format_ARGB32_Premultiplied)
        img.fill(_QColor(255, 255, 255))
        p2 = _QP(img)
        p2.scale(2, 2)
        item.paint(p2, None, None)
        p2.end()
        img.save(osp.join(OUT_DIR, f"fx_leak{tag}_layer_itempaint.png"))
        print("[layer] item.paint saved", flush=True)

    if "--fx" in _RAW_ARGV:
        blk.fontformat.text_effects = TextEffectStack(effects=(stroke, noise))
        item.repaint_background()
        grab_view(window, app, "3_bend_noise", tag)

    window.canvas._set_scene_scale(2.0)
    grab_view(window, app, "4_zoom200")
    window.canvas._set_scene_scale(0.5)
    grab_view(window, app, "5_zoom50")
    window.canvas._set_scene_scale(1.0)
    grab_view(window, app, "6_back100")

    # 预览拖动（等于面板参数 preview 路径）→ 提交
    item.set_text_transform(bend, preview=True)
    grab_view(window, app, "7_preview")
    item.set_text_transform(bend, preview=False)
    item.repaint_background()
    grab_view(window, app, "8_commit")

    # 撤销一次（画布撤销栈）
    window.canvas.undo_textedit()
    app.processEvents()
    grab_view(window, app, "9_undo")

    print("[done]", flush=True)
    app.processEvents()


if __name__ == "__main__":
    main()
