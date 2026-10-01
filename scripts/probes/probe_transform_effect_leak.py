"""探针：字体变换（bend/sine/projective/slant）+ 拥有正面的滤镜特效组合下，
未处理的原生文字面是否从 base_paint 叠回（早先滤镜族"原样式挡着"同类缺陷）。

判据：同一变换下，加 noise 滤镜与不加的整帧像素差异数。若差异≈0，
说明滤镜输出被未滤镜的原生文字面精确盖回（泄漏复现）。

本探针不检查外描边与原生文字的坐标对齐；双影回归见
tests/test_textblkitem_effect.py 的源合成像素测试，以及
scripts/probes/probe_transform_effect_ui.py 的真实窗口交互演练。
"""

import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.dirname(osp.abspath(__file__))))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def build_item(TextBlkItem, TextBlock, scene, effects, transform_stack):
    blk = TextBlock(xyxy=[100, 100, 400, 220], translation="测试文字")
    blk._bounding_rect = [100, 100, 400, 220]
    blk.fontformat.font_size = 60
    blk.fontformat.frgb = [255, 255, 255]
    item = TextBlkItem(blk=blk, idx=0)
    scene.addItem(item)
    blk.fontformat.text_effects = effects
    blk.fontformat.text_transform = transform_stack
    item.repaint_background()
    # 变换在 set_fontformat 之后生效需要显式刷新编译几何
    item.set_text_transform(transform_stack)
    item.repaint_background()
    return item


def scene_bytes(QRectF, QColor, QImage, QPainter, scene):
    image = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    try:
        scene.render(painter, target=QRectF(0, 0, 600, 400))
    finally:
        painter.end()
    return bytes(image.constBits().asarray(image.sizeInBytes()))


def diff_pixels(a, b):
    return sum(
        1
        for offset in range(0, min(len(a), len(b)), 4)
        if a[offset:offset + 3] != b[offset:offset + 3]
    )


def main():
    from qtpy.QtCore import QRectF
    from qtpy.QtGui import QColor, QImage, QPainter
    from qtpy.QtWidgets import QApplication, QGraphicsScene

    from ui.textitem import TextBlkItem
    from utils.fontformat import (
        BendTextTransform,
        ProjectiveTextTransform,
        SineTextTransform,
        TextTransformStack,
        TextTransformState,
    )
    from utils.text_effects import (
        FilterEffect,
        SolidPaint,
        StrokeEffect,
        TextEffectStack,
    )
    from utils.textblock import TextBlock

    global _APP
    _APP = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()

    stroke = StrokeEffect(width=0.1, paint=SolidPaint((255, 0, 0)))
    noise = FilterEffect(
        "builtin:noise",
        params={"amount": 1.0, "mode": "monochrome", "seed": 0},
    )
    with_effects = TextEffectStack(effects=(stroke, noise))
    baseline_effects = TextEffectStack(effects=(stroke,))

    neutral = TextTransformStack()

    cases = {
        "bend": TextTransformStack(
            transforms=(BendTextTransform(bend=0.3),)
        ),
        "sine": TextTransformStack(
            transforms=(SineTextTransform(frequency_x=4, amplitude_x=0.15),)
        ),
        "projective": TextTransformStack(
            transforms=(ProjectiveTextTransform(rotation_y=40),)
        ),
    }

    base_bytes = scene_bytes(
        QRectF, QColor, QImage, QPainter, scene
    )
    del base_bytes

    print("=== 变换 + 滤镜组合泄漏探针（offscreen）===")
    for name, stack in cases.items():
        scene.clear()
        # 变换 + 无滤镜（基线）
        item_a = build_item(
            TextBlkItem, TextBlock, scene, baseline_effects, stack
        )
        bytes_a = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
        # 同变换 + noise 滤镜
        scene.clear()
        item_b = build_item(
            TextBlkItem, TextBlock, scene, with_effects, stack
        )
        bytes_b = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
        changed = diff_pixels(bytes_a, bytes_b)
        state = item_b.effect_renderer._peek_raster_state()
        ready = (
            state is not None
            and not state.cache_dirty
            and state.cache_rendered_generation == state.cache_generation
        )
        verdict = "OK（滤镜到达画面）" if changed > 1000 else "泄漏（原生面盖回）"
        print(
            f"[{name}] 变换+滤镜 vs 变换: diff={changed}px, "
            f"fg_ready={ready}, warp={item_b.geometry_controller.uses_surface_warp()} "
            f"-> {verdict}"
        )

    # 对照组：中性变换 + 滤镜（当初已修的场景）
    scene.clear()
    item_c = build_item(
        TextBlkItem, TextBlock, scene, baseline_effects, neutral
    )
    bytes_c = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    scene.clear()
    item_d = build_item(
        TextBlkItem, TextBlock, scene, with_effects, neutral
    )
    bytes_d = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_c, bytes_d)
    print(
        f"[neutral对照] diff={changed}px -> "
        f"{'OK' if changed > 1000 else '泄漏'}"
    )

    # 斜体（glyph slant）走 layout renderer 路径
    slant = TextTransformState(TextTransformStack(), 12.0)

    def build_slant_item(effects):
        item = build_item(TextBlkItem, TextBlock, scene, effects, neutral)
        item.set_text_transform(slant)
        item.repaint_background()
        return item

    scene.clear()
    item_e = build_slant_item(baseline_effects)
    bytes_e = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    scene.clear()
    item_f = build_slant_item(with_effects)
    bytes_f = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_e, bytes_f)
    state_f = item_f.effect_renderer._peek_raster_state()
    ready_f = (
        state_f is not None
        and not state_f.cache_dirty
        and state_f.cache_rendered_generation == state_f.cache_generation
    )
    print(
        f"[slant] diff={changed}px, fg_ready={ready_f}, "
        f"distortion={item_f.geometry_controller.has_layout_distortion()} -> "
        f"{'OK' if changed > 1000 else '泄漏'}"
    )

    # 序列组：先特效（渲染过）再补变换，模拟面板实际操作顺序
    scene.clear()
    blk_g = TextBlock(xyxy=[100, 100, 400, 220], translation="测试文字")
    blk_g._bounding_rect = [100, 100, 400, 220]
    blk_g.fontformat.font_size = 60
    blk_g.fontformat.frgb = [255, 255, 255]
    item_g = TextBlkItem(blk=blk_g, idx=0)
    scene.addItem(item_g)
    blk_g.fontformat.text_effects = baseline_effects
    item_g.repaint_background()
    bytes_g0 = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    del bytes_g0
    # 加变换（非 preview，模拟确认提交）
    item_g.set_text_transform(cases["bend"])
    item_g.repaint_background()
    bytes_g1 = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    # 对照：直接以「变换+滤镜」构建
    scene.clear()
    item_h = build_item(TextBlkItem, TextBlock, scene, baseline_effects, cases["bend"])
    bytes_h = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_g1, bytes_h)
    print(
        f"[先特效后变换序列] diff={changed}px -> "
        f"{'OK（与直接构建一致）' if changed < 100 else '不一致（过渡态残留?）'}"
    )

    # 预览组：变换激活时走效果参数预览再取消，检查是否回到干净基线
    scene.clear()
    item_k = build_item(TextBlkItem, TextBlock, scene, baseline_effects, cases["bend"])
    bytes_k_base = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    # 预览：叠加 noise（preview=True）
    preview_stack = TextEffectStack(effects=(stroke, noise))
    item_k.set_text_effects(preview_stack, preview=True)
    bytes_k_preview = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    del bytes_k_preview
    item_k.clear_text_effect_preview()
    item_k.repaint_background()
    bytes_k_after = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_k_after, bytes_k_base)
    print(
        f"[效果预览取消后] diff={changed}px -> "
        f"{'OK（回到基线）' if changed < 100 else '不一致（预览残留?）'}"
    )

    # 预览组2：特效先在（含滤镜），再走变换参数预览（preview=True）再取消
    scene.clear()
    item_l = build_item(TextBlkItem, TextBlock, scene, with_effects, neutral)
    bytes_l_direct = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    del bytes_l_direct
    item_l.set_text_transform(
        TextTransformState(cases["bend"], 0.0), preview=True
    )
    bytes_l_prev = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    del bytes_l_prev
    item_l.clear_text_transform_preview()
    item_l.repaint_background()
    bytes_l_after = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    scene.clear()
    item_l2 = build_item(TextBlkItem, TextBlock, scene, with_effects, neutral)
    bytes_l2 = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    # 对照：直接以「滤镜+变换」构建
    scene.clear()
    item_m = build_item(TextBlkItem, TextBlock, scene, with_effects, cases["bend"])
    bytes_m = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_l_after, bytes_m)
    print(
        f"[变换预览取消后(中性块,应回中性)] vs中性+滤镜 "
        f"diff={diff_pixels(bytes_l_after, bytes_l2)}px -> OK为预期"
    )

    # 真实流程：预览（拖动中）→ 提交（松手），与直接构建比对
    scene.clear()
    item_l4 = build_item(TextBlkItem, TextBlock, scene, with_effects, neutral)
    item_l4.set_text_transform(
        TextTransformState(cases["bend"], 0.0), preview=True
    )
    bytes_l4_prev = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    del bytes_l4_prev
    item_l4.set_text_transform(
        TextTransformState(cases["bend"], 0.0), preview=False
    )
    item_l4.repaint_background()
    bytes_l4 = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    changed = diff_pixels(bytes_l4, bytes_m)
    print(
        f"[预览→提交] vs直接构建 diff={changed}px; "
        f"fg_ready={getattr(item_l4.effect_renderer._peek_raster_state(), 'cache_dirty', None) is False} -> "
        f"{'OK' if changed < 100 else '不一致（提交后残留?）'}"
    )

    # 变换参数预览期间：确认画面里是否同时出现变换前+变换后两份文字
    scene.clear()
    item_n = build_item(TextBlkItem, TextBlock, scene, baseline_effects, neutral)
    bytes_n_neutral = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    scene.clear()
    item_o = build_item(TextBlkItem, TextBlock, scene, baseline_effects, cases["bend"])
    bytes_o_bend = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    scene.clear()
    item_p = build_item(TextBlkItem, TextBlock, scene, baseline_effects, neutral)
    item_p.set_text_transform(
        TextTransformState(cases["bend"], 0.0), preview=True
    )
    bytes_p_preview = scene_bytes(QRectF, QColor, QImage, QPainter, scene)
    diff_vs_neutral = diff_pixels(bytes_p_preview, bytes_n_neutral)
    diff_vs_bend = diff_pixels(bytes_p_preview, bytes_o_bend)
    print(
        f"[变换预览中] vs中性 diff={diff_vs_neutral}px, vs弯曲 diff={diff_vs_bend}px "
        f"（预览应接近弯曲形态且只画一份文字）"
    )


if __name__ == "__main__":
    main()
