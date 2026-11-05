"""块气泡用例：数据层往返、子项挂载与 z 序、撤销命令、面板往返。

几何本身的用例在 ``tests/test_bubble_geometry.py``；这里只钉「块气泡挂到
``TextBlkItem`` 之后的行为契约」——尤其是气泡必须画在文字**之下**，那一条
一旦回归，译文会被整块盖住而没有任何报错。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe -m unittest discover -s tests -p "test_block_bubble.py"
"""

import copy
import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QRectF  # noqa: E402
from qtpy.QtGui import QColor, QImage, QPainter  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsItem,
    QGraphicsScene,
)

try:  # PyQt6 把 QUndoStack 放在 QtGui
    from qtpy.QtWidgets import QUndoStack  # noqa: E402
except ImportError:  # pragma: no cover
    from qtpy.QtGui import QUndoStack  # noqa: E402

from ui.bubble import (  # noqa: E402
    REGION_KEY,
    SHAPE_ELLIPSE,
    SHAPE_RECT,
    BubbleSpec,
    BubbleStyle,
    BubbleTail,
    block_bubble_from_payload,
    block_bubble_region,
    block_bubble_to_payload,
)
from ui.block_bubble_panel import BlockBubblePanel  # noqa: E402
from ui.textedit_commands import SetBlockBubbleCommand  # noqa: E402
from ui.textitem import TextBlkItem  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

app = QApplication.instance() or QApplication([])

_FONT_WARNING = "Cannot find font directory"


def make_blk(xyxy=(40, 40, 240, 200), text="HELLO"):
    x1, y1, x2, y2 = xyxy
    blk = TextBlock(
        xyxy=[x1, y1, x2, y2],
        lines=[[(float(x1), float(y1)), (float(x2), float(y1)),
                (float(x2), float(y2)), (float(x1), float(y2))]],
    )
    blk.translation = text
    blk.rich_text = f"<p>{text}</p>"
    return blk


def make_item(text="HELLO"):
    blk = make_blk(text=text)
    item = TextBlkItem(blk, 0)
    scene = QGraphicsScene()
    scene.setSceneRect(QRectF(0, 0, 320, 280))
    scene.addItem(item)
    return blk, item, scene


def render_scene(scene, w=320, h=280):
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    scene.render(painter, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
    painter.end()
    return image


def count_dark(image, w=320, h=280):
    """数近黑像素——文字笔画。被气泡整块盖住时会掉到接近 0。"""
    total = 0
    for y in range(h):
        for x in range(w):
            c = image.pixelColor(x, y)
            if c.red() < 90 and c.green() < 90 and c.blue() < 90:
                total += 1
    return total


class PayloadTest(unittest.TestCase):
    """块气泡载荷：``None`` = 没气泡，``{}`` = 有气泡但全默认。"""

    def test_none_payload_means_no_bubble(self):
        self.assertIsNone(block_bubble_from_payload(None))
        self.assertIsNone(block_bubble_from_payload("garbage"))

    def test_empty_payload_is_default_style_bubble(self):
        # 空 dict 是「开了气泡但参数全默认」——必须能与「没有气泡」区分，
        # 否则用户永远画不出一个纯白矩形气泡。
        style = block_bubble_from_payload({})
        self.assertIsInstance(style, BubbleStyle)
        self.assertEqual(style.shape, SHAPE_RECT)

    def test_roundtrip_keeps_non_defaults(self):
        style = BubbleStyle(
            shape=SHAPE_ELLIPSE,
            corner=10,
            fill="#123456",
            alpha=128,
            border=True,
            border_width=6,
            border_color="#abcdef",
            tail=BubbleTail(angle=180, length=0.5, width=0.4),
        )
        again = block_bubble_from_payload(block_bubble_to_payload(style))
        self.assertEqual(again.shape, SHAPE_ELLIPSE)
        self.assertEqual(again.fill, "#123456")
        self.assertEqual(again.alpha, 128)
        self.assertTrue(again.border)
        self.assertAlmostEqual(again.border_width, 6.0)
        self.assertAlmostEqual(again.tail.angle, 180.0)

    def test_to_payload_none_for_missing_bubble(self):
        self.assertIsNone(block_bubble_to_payload(None))

    def test_region_roundtrip(self):
        """自定义覆盖区域：偏移 + 尺寸原样往返，没该键 = 跟随块。"""
        region = QRectF(-20.0, -30.0, 260.0, 180.0)
        payload = block_bubble_to_payload(BubbleStyle(fill="#123456"), region)
        self.assertEqual(payload[REGION_KEY], [-20.0, -30.0, 260.0, 180.0])
        back = block_bubble_region(payload)
        self.assertEqual(
            (back.x(), back.y(), back.width(), back.height()),
            (-20.0, -30.0, 260.0, 180.0),
        )
        # 样式没被 region 键破坏（from_payload 宽进忽略未知键）
        self.assertEqual(block_bubble_from_payload(payload).fill, "#123456")
        # 不传 region = 跟随块
        self.assertIsNone(block_bubble_region(block_bubble_to_payload(BubbleStyle())))

    def test_legacy_payload_still_follows_block(self):
        """没有 region 键的旧数据必须解析成「跟随块」，不能整条判无效。"""
        legacy = {"fill": "#ff0000", "tail": {"angle": 90.0}}
        self.assertIsNone(block_bubble_region(legacy))
        self.assertIsNotNone(block_bubble_from_payload(legacy))

    def test_bad_region_falls_back_to_follow(self):
        """坏 region（宽高非正 / 长度不对 / NaN）一律回落跟随块，不画到原点。"""
        for bad in ([1, 2, 0, 10], [1, 2, 10], "x", [1, 2, float("nan"), 10]):
            self.assertIsNone(block_bubble_region({REGION_KEY: bad}), msg=str(bad))

    def test_spec_roundtrip(self):
        region = QRectF(5.0, 6.0, 120.0, 90.0)
        spec = BubbleSpec(style=BubbleStyle(shape=SHAPE_ELLIPSE), region=region)
        back = BubbleSpec.from_payload(spec.to_payload())
        self.assertEqual(back.style.shape, SHAPE_ELLIPSE)
        self.assertEqual(back.region, region)


class TextBlockKeyTest(unittest.TestCase):
    """``TextBlock.to_dict`` 只在有气泡时写 ``bubble`` 键。"""

    def test_no_key_without_bubble(self):
        blk = make_blk()
        self.assertNotIn("bubble", blk.to_dict())

    def test_key_written_for_default_style(self):
        blk = make_blk()
        blk.bubble = {}
        self.assertEqual(blk.to_dict().get("bubble"), {})

    def test_to_dict_does_not_delete_instance_attribute(self):
        # vars() 返回实例真实 __dict__，to_dict 必须自己拷贝再 pop
        blk = make_blk()
        blk.bubble = {"fill": "#ff0000"}
        blk.to_dict()
        self.assertEqual(blk.bubble, {"fill": "#ff0000"})

    def test_glyph_metrics_still_pruned(self):
        blk = make_blk()
        blk._glyph_font_metrics = {"size": 12}
        self.assertNotIn("_glyph_font_metrics", blk.to_dict())


class MountTest(unittest.TestCase):
    """``BubbleItem`` 作为 ``TextBlkItem`` 子项的挂载契约。"""

    def test_no_item_without_bubble(self):
        _blk, item, _scene = make_item()
        self.assertIsNone(item.block_bubble())
        self.assertIsNone(item._bubble_item)

    def test_set_and_clear_bubble(self):
        blk, item, _scene = make_item()
        item.set_block_bubble(BubbleStyle(fill="#ff0000"))
        self.assertIsNotNone(item._bubble_item)
        self.assertIs(item._bubble_item.parentItem(), item)
        self.assertEqual(blk.bubble, {"fill": "#ff0000"})

        item.set_block_bubble(None)
        self.assertIsNone(item._bubble_item)
        self.assertIsNone(blk.bubble)

    def test_bubble_stacks_behind_parent(self):
        """回归钉子：子项默认画在父项**之上**，zValue(-1) 对父子无效。

        没有 ``ItemStacksBehindParent``，白底气泡会整块盖住译文，而且不报
        任何错——只有目视或像素比对能发现。
        """
        _blk, item, _scene = make_item()
        item.set_block_bubble(BubbleStyle(fill="#ff0000"))
        bubble = item._bubble_item
        self.assertTrue(
            bubble.flags() & QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent
        )

    def test_text_visible_above_full_rect_bubble(self):
        """铺满整块的红气泡下，文字像素仍在（=气泡在文字之下）。"""
        _blk, base_item, base_scene = make_item()
        baseline = count_dark(render_scene(base_scene))

        _blk2, item, scene = make_item()
        item.set_block_bubble(BubbleStyle(shape=SHAPE_RECT, fill="#ff0000"))
        with_bubble = count_dark(render_scene(scene))

        self.assertGreater(baseline, 0, "基线应有文字像素")
        # 一样多 = 气泡完全没遮住文字
        self.assertEqual(
            with_bubble,
            baseline,
            "整块气泡遮住了译文：ItemStacksBehindParent 掉了",
        )

        # 反面对照：去掉 flag 后文字被盖住（证明这条断言真的在测 z 序）
        _blk3, item3, scene3 = make_item()
        item3.set_block_bubble(BubbleStyle(shape=SHAPE_RECT, fill="#ff0000"))
        item3._bubble_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent, False
        )
        self.assertLess(count_dark(render_scene(scene3)), baseline)

    def test_geometry_follows_content_rect(self):
        """气泡框 = ``unpadRect(boundingRect())``，与选中框同口径。"""
        _blk, item, _scene = make_item()
        item.set_block_bubble(BubbleStyle())
        expected = item.unpadRect(item.boundingRect())
        got = item._bubble_item.rect()
        self.assertAlmostEqual(got.width(), expected.width(), places=2)
        self.assertAlmostEqual(got.height(), expected.height(), places=2)

    def test_refresh_uses_payload_not_item(self):
        """``refresh_block_bubble`` 只认 ``blk.bubble``（撤销走同一条路）。"""
        blk, item, _scene = make_item()
        item.set_block_bubble(BubbleStyle(fill="#ff0000"))
        blk.bubble = {"shape": SHAPE_ELLIPSE}
        item.refresh_block_bubble()
        self.assertEqual(item._bubble_item.style().shape, SHAPE_ELLIPSE)
        blk.bubble = None
        item.refresh_block_bubble()
        self.assertIsNone(item._bubble_item)


class CommandTest(unittest.TestCase):
    """``SetBlockBubbleCommand`` 的 undo/redo。"""

    def test_create_returns_none_for_noop(self):
        _blk, item, _scene = make_item()
        self.assertIsNone(SetBlockBubbleCommand.create([item], [None], [None]))

    def test_undo_redo_restores_payload_and_item(self):
        blk, item, _scene = make_item()
        after = block_bubble_to_payload(BubbleStyle(fill="#ff0000"))
        cmd = SetBlockBubbleCommand.create([item], [None], [after])
        self.assertIsNotNone(cmd)

        cmd.redo()
        self.assertEqual(blk.bubble, {"fill": "#ff0000"})
        self.assertIsNotNone(item._bubble_item)

        cmd.undo()
        self.assertIsNone(blk.bubble)
        self.assertIsNone(item._bubble_item)

        cmd.redo()
        self.assertIsNotNone(item._bubble_item)

    def test_remove_then_undo(self):
        blk, item, _scene = make_item()
        item.set_block_bubble(BubbleStyle(fill="#ff0000"))
        after = copy.deepcopy(blk.bubble)
        cmd = SetBlockBubbleCommand.create([item], [after], [None])
        cmd.redo()
        self.assertIsNone(blk.bubble)
        cmd.undo()
        self.assertEqual(blk.bubble, after)
        self.assertIsNotNone(item._bubble_item)

    def test_snapshot_isolated_from_later_mutation(self):
        """命令快照与调用方 dict 不共享引用（避免撤销回放拿到改过的值）。"""
        blk, item, _scene = make_item()
        after = {"fill": "#ff0000"}
        cmd = SetBlockBubbleCommand.create([item], [None], [after])
        after["fill"] = "#00ff00"
        cmd.redo()
        self.assertEqual(blk.bubble, {"fill": "#ff0000"})

    def test_undo_stack_sequence(self):
        """连续改参各占一步，逐步撤销能精确回退。"""
        blk, item, _scene = make_item()
        stack = QUndoStack()
        steps = [
            None,
            {},
            {"shape": SHAPE_ELLIPSE},
            {"shape": SHAPE_ELLIPSE, "alpha": 128},
        ]
        for before, after in zip(steps, steps[1:]):
            cmd = SetBlockBubbleCommand.create(
                [item], [copy.deepcopy(before)], [copy.deepcopy(after)]
            )
            stack.push(cmd)
        self.assertEqual(blk.bubble, {"shape": SHAPE_ELLIPSE, "alpha": 128})
        stack.undo()
        self.assertEqual(blk.bubble, {"shape": SHAPE_ELLIPSE})
        stack.undo()
        self.assertEqual(blk.bubble, {})
        stack.undo()
        self.assertIsNone(blk.bubble)
        self.assertIsNone(item._bubble_item)


class PanelTest(unittest.TestCase):
    """``BlockBubblePanel`` 控件 ↔ ``BubbleStyle`` 往返。"""

    def test_disabled_by_default(self):
        panel = BlockBubblePanel()
        self.assertIsNone(panel.current_style())
        self.assertFalse(panel.params_widget.isEnabled())

    def test_enable_emits_default_style(self):
        panel = BlockBubblePanel()
        got = []
        panel.bubble_changed.connect(got.append)
        panel.enable_checker.setChecked(True)
        self.assertTrue(got)
        # 信号发的是 BubbleSpec（样式 + 可选自由区域），不再是裸 BubbleStyle
        self.assertIsInstance(got[-1], BubbleSpec)
        self.assertEqual(got[-1].style.shape, SHAPE_RECT)
        self.assertIsNone(got[-1].region, "默认跟随块，不应带自定义区域")

    def test_custom_region_emits_and_roundtrips(self):
        """「自定义区域」模式：切过去带出基准尺寸，改数值即进 spec。"""
        panel = BlockBubblePanel()
        panel.seed_region(QRectF(0, 0, 300, 200))
        panel.set_from_style(BubbleStyle(fill="#ff0000"))
        panel.enable_checker.setChecked(True)
        got = []
        panel.bubble_changed.connect(got.append)
        panel.region_box.setCurrentIndex(1)  # Custom area
        self.assertTrue(panel.region_controls.isEnabled())
        spec = got[-1]
        self.assertIsNotNone(spec.region)
        # 基准尺寸必须带出来，不能是 1×1（否则气泡画成一个点）
        self.assertAlmostEqual(spec.region.width(), 300.0)
        self.assertAlmostEqual(spec.region.height(), 200.0)
        panel.region_x_spin.setValue(-25.0)
        self.assertAlmostEqual(got[-1].region.x(), -25.0)
        # 切回「跟随块」→ region 回到 None
        panel.region_box.setCurrentIndex(0)
        self.assertIsNone(got[-1].region)

    def test_roundtrip(self):
        panel = BlockBubblePanel()
        style = BubbleStyle(
            shape=SHAPE_ELLIPSE,
            fill="#123456",
            alpha=140,
            border=True,
            border_width=6,
            border_color="#abcdef",
            tail=BubbleTail(angle=180, length=0.5, width=0.4),
        )
        panel.set_from_style(style)
        got = panel.current_style()
        self.assertEqual(got.shape, SHAPE_ELLIPSE)
        self.assertEqual(got.fill, "#123456")
        self.assertEqual(got.alpha, 140)
        self.assertTrue(got.border)
        self.assertAlmostEqual(got.tail.angle, 180.0)

    def test_corner_disabled_for_ellipse(self):
        panel = BlockBubblePanel()
        panel.set_from_style(BubbleStyle(shape=SHAPE_ELLIPSE))
        self.assertFalse(panel.corner_spin.isEnabled())
        panel.set_from_style(BubbleStyle(shape=SHAPE_RECT))
        self.assertTrue(panel.corner_spin.isEnabled())

    def test_corner_and_border_capped_by_block_size(self):
        """圆角 ≤ 短边一半、描边 ≤ 短边四分之一（再大只是胶囊/实心块）。"""
        panel = BlockBubblePanel()
        panel.seed_region(QRectF(0, 0, 160, 100))
        panel.set_from_style(BubbleStyle(border=True))
        self.assertEqual(panel.corner_spin.maximum(), 50)
        self.assertAlmostEqual(panel.border_width_spin.maximum(), 25.0)
        # 自定义区域以区域尺寸为准
        panel.set_from_style(BubbleStyle(border=True), QRectF(0, 0, 60, 40))
        self.assertEqual(panel.corner_spin.maximum(), 20)
        self.assertAlmostEqual(panel.border_width_spin.maximum(), 10.0)

    def test_loaded_values_clamped_to_bubble_size(self):
        """旧数据里的超大圆角/描边回显时就被收进合理范围。"""
        panel = BlockBubblePanel()
        panel.seed_region(QRectF(0, 0, 100, 80))
        panel.set_from_style(BubbleStyle(corner=200, border=True, border_width=64))
        self.assertEqual(panel.current_style().corner, 40)
        self.assertAlmostEqual(panel.current_style().border_width, 20.0)

    def test_set_none_clears(self):
        panel = BlockBubblePanel()
        panel.set_from_style(BubbleStyle(fill="#ff0000"))
        panel.set_from_style(None)
        self.assertIsNone(panel.current_style())


if __name__ == "__main__":
    unittest.main(verbosity=2)
