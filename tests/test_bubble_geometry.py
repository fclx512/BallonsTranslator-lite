"""气泡几何层用例：形状、尾巴并集、内侧描边环、像素与落盘往返。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_bubble_geometry.py
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_bubble_geometry.py -q
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QPointF, QRectF, Qt  # noqa: E402
from qtpy.QtGui import QColor, QImage, QPainter  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsItem,
    QGraphicsScene,
)

from ui.bubble import (  # noqa: E402
    SHAPE_ELLIPSE,
    SHAPE_RECT,
    BubbleItem,
    BubbleStyle,
    BubbleTail,
    body_path,
    bubble_border_path,
    bubble_from_payload,
    bubble_image,
    bubble_path,
    bubble_to_payload,
    paint_bubble,
    tail_path,
)

app = QApplication.instance() or QApplication([])  # noqa: E402


def pixel(image: QImage, x: int, y: int) -> QColor:
    return QColor(image.pixelColor(x, y))


class BodyShapeTest(unittest.TestCase):
    def setUp(self):
        self.rect = QRectF(0, 0, 100, 100)

    def test_rect_contains_center_and_bounds(self):
        path = body_path(self.rect, SHAPE_RECT, 0)
        self.assertTrue(path.contains(QPointF(50, 50)))
        bounds = path.boundingRect()
        self.assertAlmostEqual(bounds.width(), 100.0, places=3)
        self.assertAlmostEqual(bounds.height(), 100.0, places=3)

    def test_rounded_corner_removes_corner_pixel(self):
        sharp = body_path(self.rect, SHAPE_RECT, 0)
        round_path = body_path(self.rect, SHAPE_RECT, 20)
        corner = QPointF(1, 1)
        self.assertTrue(sharp.contains(corner))
        self.assertFalse(round_path.contains(corner))
        self.assertTrue(round_path.contains(QPointF(50, 50)))

    def test_corner_clamped_to_half_short_side(self):
        rect = QRectF(0, 0, 100, 40)
        path = body_path(rect, SHAPE_RECT, 999)
        bounds = path.boundingRect()
        self.assertAlmostEqual(bounds.height(), 40.0, places=3)
        # 半径超过短边一半会翻出洞：钳成胶囊后中心仍是实心
        self.assertTrue(path.contains(QPointF(50, 20)))

    def test_ellipse_excludes_corners(self):
        path = body_path(self.rect, SHAPE_ELLIPSE, 0)
        self.assertTrue(path.contains(QPointF(50, 50)))
        self.assertFalse(path.contains(QPointF(2, 2)))
        self.assertTrue(path.contains(QPointF(50, 2)))

    def test_empty_rect_gives_empty_path(self):
        self.assertTrue(body_path(QRectF(), SHAPE_RECT, 10).isEmpty())


class BorderRingTest(unittest.TestCase):
    def setUp(self):
        self.rect = QRectF(0, 0, 100, 100)

    def test_ring_stays_inside_outline(self):
        style = BubbleStyle(border=True, border_width=8)
        ring = bubble_border_path(self.rect, style)
        self.assertFalse(ring.isEmpty())
        self.assertTrue(ring.contains(QPointF(2, 50)), "内侧 2px 应落在环内")
        self.assertFalse(ring.contains(QPointF(50, 50)), "中心不应被描边")
        self.assertFalse(ring.contains(QPointF(-2, 50)), "描边不得溢出拖拽范围")

    def test_ring_absent_without_border_flag(self):
        self.assertTrue(bubble_border_path(self.rect, BubbleStyle()).isEmpty())
        self.assertTrue(
            bubble_border_path(
                self.rect, BubbleStyle(border=False, border_width=8)
            ).isEmpty()
        )

    def test_thick_border_degrades_to_solid(self):
        style = BubbleStyle(border=True, border_width=60)
        ring = bubble_border_path(self.rect, style)
        # 宽到翻洞时会退化成整块，中心也是描边色（宁可实心也不出洞）
        self.assertTrue(ring.contains(QPointF(50, 50)))


class TailTest(unittest.TestCase):
    def setUp(self):
        self.rect = QRectF(0, 0, 100, 100)

    def test_tail_points_down_by_default(self):
        style = BubbleStyle(tail=BubbleTail(angle=90, length=0.3, width=0.18))
        path = bubble_path(self.rect, style)
        self.assertGreater(path.boundingRect().bottom(), self.rect.bottom() + 20)
        self.assertTrue(path.contains(QPointF(50, 120)), "尾巴区域应被填充")
        self.assertFalse(path.contains(QPointF(5, 120)), "尾巴外不应被填充")

    def test_tail_direction_follows_angle(self):
        for angle, probe in (
            (0, QPointF(125, 50)),
            (90, QPointF(50, 125)),
            (180, QPointF(-25, 50)),
            (270, QPointF(50, -25)),
        ):
            style = BubbleStyle(tail=BubbleTail(angle=angle, length=0.3))
            path = bubble_path(self.rect, style)
            self.assertTrue(path.contains(probe), f"angle={angle} 应在 {probe} 命中")

    def test_no_inner_line_at_tail_root(self):
        """尾巴根部的旧边线不能留下一道内侧描边（并集 vs addPath 的分水岭）。"""
        style = BubbleStyle(
            border=True, border_width=4, tail=BubbleTail(angle=90, length=0.3)
        )
        ring = bubble_border_path(self.rect, style)
        # (50, 101.5) 在尾巴内部、离并集边界 ~8px：addPath 实现会把根部那条
        # 弦当边界，这里就会变成描边色。
        self.assertFalse(ring.contains(QPointF(50, 101.5)))
        self.assertTrue(ring.contains(QPointF(2, 50)), "其余边的描边仍在")

    def test_zero_length_tail_is_skipped(self):
        style = BubbleStyle(tail=BubbleTail(length=0.0))
        self.assertAlmostEqual(
            bubble_path(self.rect, style).boundingRect().height(), 100.0, places=3
        )

    def test_tail_clipped_to_limit_rect(self):
        limit = self.rect.adjusted(-8, -8, 8, 8)
        style = BubbleStyle(tail=BubbleTail(angle=90, length=1.0))
        path = bubble_path(self.rect, style, limit)
        self.assertLessEqual(path.boundingRect().bottom(), limit.bottom() + 0.01)

    def test_tail_path_requires_body(self):
        from qtpy.QtGui import QPainterPath

        self.assertTrue(
            tail_path(
                QPainterPath(), self.rect, BubbleStyle(tail=BubbleTail())
            ).isEmpty()
        )
        self.assertTrue(
            tail_path(body_path(self.rect), self.rect, BubbleStyle()).isEmpty()
        )


class StylePayloadTest(unittest.TestCase):
    def test_default_style_writes_nothing(self):
        self.assertEqual(BubbleStyle().to_payload(), {})
        self.assertTrue(BubbleStyle().is_default())

    def test_roundtrip_keeps_non_defaults(self):
        style = BubbleStyle(
            shape=SHAPE_ELLIPSE,
            corner=18.0,
            fill="#ffe9c8",
            alpha=200,
            border=True,
            border_width=6.0,
            border_color="#333333",
            tail=BubbleTail(angle=135.0, length=0.4, width=0.2),
        )
        payload = style.to_payload()
        self.assertEqual(payload["shape"], SHAPE_ELLIPSE)
        self.assertEqual(payload["alpha"], 200)
        self.assertEqual(payload["tail"]["angle"], 135.0)
        restored = BubbleStyle.from_payload(payload)
        self.assertEqual(restored.normalized(), style.normalized())

    def test_wide_in_payload_tolerates_garbage(self):
        style = BubbleStyle.from_payload(
            {
                "shape": "star",
                "corner": -5,
                "fill": "red",
                "alpha": 999,
                "border_width": "thick",
                "tail": "none",
            }
        )
        self.assertEqual(style.shape, SHAPE_RECT)
        self.assertEqual(style.corner, 0.0)
        self.assertEqual(style.fill, "#ffffff")
        self.assertEqual(style.alpha, 255)
        self.assertEqual(style.border_width, 4.0)
        self.assertIsNone(style.tail)

    def test_tail_payload_omits_defaults(self):
        self.assertEqual(BubbleTail().to_payload(), {})
        self.assertEqual(BubbleTail(angle=45).to_payload(), {"angle": 45.0})

    def test_page_entry_roundtrip(self):
        rect = QRectF(12.5, 20, 80, 60)
        style = BubbleStyle(fill="#000000", border=True)
        payload = bubble_to_payload(rect, style)
        self.assertEqual(payload["rect"], [12.5, 20.0, 80.0, 60.0])
        restored = bubble_from_payload(payload)
        self.assertIsNotNone(restored)
        rect2, style2 = restored
        self.assertEqual(rect2, rect)
        self.assertEqual(style2.normalized(), style.normalized())

    def test_page_entry_rejects_bad_rect(self):
        self.assertIsNone(bubble_from_payload({"rect": [0, 0, 0, 10]}))
        self.assertIsNone(bubble_from_payload({"rect": [1, 2, 3]}))
        self.assertIsNone(bubble_from_payload("nope"))


class PixelTest(unittest.TestCase):
    def setUp(self):
        self.rect = QRectF(20, 20, 100, 60)
        self.bounds = QRectF(0, 0, 160, 140)

    def test_fill_color_and_outside_transparency(self):
        image = bubble_image(
            self.rect, BubbleStyle(fill="#ff0000"), bounds=self.bounds
        )
        self.assertGreater(pixel(image, 70, 50).red(), 200)
        self.assertEqual(pixel(image, 5, 5).alpha(), 0)

    def test_border_inside_and_fill_centered(self):
        image = bubble_image(
            self.rect,
            BubbleStyle(fill="#ffffff", border=True, border_width=8),
            bounds=self.bounds,
        )
        self.assertLess(pixel(image, 22, 50).red(), 60, "内侧 2px 应为描边色")
        self.assertGreater(pixel(image, 70, 50).red(), 200, "中心应为填充色")
        self.assertEqual(pixel(image, 18, 50).alpha(), 0, "描边不得外溢")

    def test_alpha_applied(self):
        image = bubble_image(
            self.rect, BubbleStyle(fill="#ff0000", alpha=128), bounds=self.bounds
        )
        alpha = pixel(image, 70, 50).alpha()
        self.assertTrue(120 <= alpha <= 136, f"alpha={alpha}")

    def test_tail_rendered(self):
        style = BubbleStyle(
            fill="#ff0000", tail=BubbleTail(angle=90, length=0.5)
        )
        image = bubble_image(self.rect, style, bounds=self.bounds)
        self.assertGreater(pixel(image, 70, 100).red(), 200, "尾巴应被填充")
        self.assertEqual(pixel(image, 10, 100).alpha(), 0)


class ItemTest(unittest.TestCase):
    def test_item_does_not_grab_mouse(self):
        item = BubbleItem(BubbleStyle(), QRectF(0, 0, 40, 40))
        self.assertEqual(item.acceptedMouseButtons(), Qt.MouseButton.NoButton)
        self.assertFalse(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.set_interactive(True)
        self.assertEqual(item.acceptedMouseButtons(), Qt.MouseButton.LeftButton)

    def test_item_tracks_rect_and_style(self):
        item = BubbleItem(BubbleStyle(), QRectF(0, 0, 40, 40))
        item.set_rect(QRectF(10, 10, 60, 30))
        self.assertEqual(item.rect(), QRectF(10, 10, 60, 30))
        self.assertTrue(item.contains_point(QPointF(40, 25)))
        item.set_style(BubbleStyle(tail=BubbleTail(angle=90, length=0.5)))
        self.assertGreater(item.boundingRect().bottom(), 40)

    def test_item_renders_in_scene_without_crashing(self):
        scene = QGraphicsScene()
        item = BubbleItem(
            BubbleStyle(fill="#ff0000"), QRectF(0, 0, 40, 40)
        )
        scene.addItem(item)
        image = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            scene.render(painter, QRectF(0, 0, 40, 40), QRectF(0, 0, 40, 40))
        finally:
            painter.end()
        self.assertGreater(pixel(image, 20, 20).red(), 180)

    def test_paint_bubble_matches_paths(self):
        rect = QRectF(0, 0, 30, 30)
        style = BubbleStyle(fill="#00ff00")
        image = QImage(30, 30, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            paint_bubble(painter, rect, style)
        finally:
            painter.end()
        self.assertGreater(pixel(image, 15, 15).green(), 180)


if __name__ == "__main__":
    unittest.main(verbosity=2)
