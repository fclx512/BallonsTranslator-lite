"""气泡几何复算探针：形状网格出图 + 并集/描边环的数值判别。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe scripts/probes/bubble_geometry_probe.py

产出 tmp/bubble_probe.png（白底网格，无文字：离屏字体不可靠，位置见 stdout 图例）。
"""

import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.dirname(osp.abspath(__file__))))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QPointF, QRectF, Qt  # noqa: E402
from qtpy.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
)
from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.bubble import (  # noqa: E402
    SHAPE_ELLIPSE,
    BubbleStyle,
    BubbleTail,
    body_path,
    bubble_border_path,
    bubble_path,
    paint_bubble,
)

app = QApplication.instance() or QApplication([])

OUT = osp.join(APP_ROOT, "tmp", "bubble_probe.png")


def _legacy_add_path(rect, style):
    """反面对照：addPath 拼接（文档坑：根部留内线）。"""
    body = body_path(rect, style.shape, style.corner)
    if style.tail is None:
        return body
    outline = QPainterPath(body)
    # 直接复用几何层算出的尾巴，再 addPath 拼上去
    from ui.bubble import tail_path

    outline.addPath(tail_path(body, rect, style))
    return outline


def _ring(outline, rect, width):
    stroker = QPainterPathStroker()
    stroker.setWidth(width * 2.0)
    stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return stroker.createStroke(outline).intersected(outline)


def numeric_checks() -> int:
    problems = 0
    rect = QRectF(0, 0, 100, 100)
    style = BubbleStyle(
        border=True, border_width=4, tail=BubbleTail(angle=90, length=0.3)
    )
    union_ring = bubble_border_path(rect, style)
    legacy_ring = _ring(_legacy_add_path(rect, style), rect, 4)
    probe = QPointF(50, 101.5)

    print("[并集判别] 根部内部点 (50, 101.5)")
    print(f"  united 实现 ring.contains = {union_ring.contains(probe)}  (期望 False)")
    print(f"  addPath 对照 ring.contains = {legacy_ring.contains(probe)}  (期望 True)")
    if union_ring.contains(probe):
        problems += 1
    if not legacy_ring.contains(probe):
        print("  ! 对照组未复现内线，判别力不足")
        problems += 1

    # 描边不外溢：外轮廓外侧 1px 不应被环覆盖
    for name, pt in (
        ("left", QPointF(-1, 50)),
        ("top", QPointF(50, -1)),
        ("right", QPointF(101, 50)),
        ("bottom", QPointF(50, 131)),
    ):
        hit = union_ring.contains(pt)
        print(f"[描边外溢] {name:6s} {pt} -> {hit} (期望 False)")
        problems += int(hit)

    # 椭圆极值点描边连续（上下左右四点都在环内）
    ellipse = QRectF(0, 0, 120, 80)
    estyle = BubbleStyle(shape=SHAPE_ELLIPSE, border=True, border_width=10)
    ering = bubble_border_path(ellipse, estyle)
    for name, pt in (
        ("left", QPointF(2, 40)),
        ("right", QPointF(118, 40)),
        ("top", QPointF(60, 2)),
        ("bottom", QPointF(60, 78)),
    ):
        hit = ering.contains(pt)
        print(f"[椭圆描边] {name:6s} {pt} -> {hit} (期望 True)")
        problems += int(not hit)

    # 尾巴越界钳制
    limit = QRectF(0, 0, 200, 120)
    clipped = bubble_path(
        QRectF(40, 10, 120, 60),
        BubbleStyle(tail=BubbleTail(angle=90, length=2.0)),
        limit,
    )
    print(f"[尾巴钳制] bottom={clipped.boundingRect().bottom():.2f} (期望 <= 120)")
    problems += int(clipped.boundingRect().bottom() > 120.01)
    return problems


def render_grid() -> None:
    image = QImage(920, 620, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#2b2b2b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    cells = [
        ("1 rect corner=0", BubbleStyle(), QRectF(60, 50, 200, 110)),
        ("2 rect corner=28", BubbleStyle(corner=28), QRectF(300, 50, 200, 110)),
        ("3 ellipse", BubbleStyle(shape=SHAPE_ELLIPSE), QRectF(540, 50, 220, 110)),
        (
            "4 rect border6 tail down",
            BubbleStyle(border=True, border_width=6, tail=BubbleTail(90, 0.35)),
            QRectF(60, 230, 200, 110),
        ),
        (
            "5 ellipse border6 tail right",
            BubbleStyle(
                shape=SHAPE_ELLIPSE,
                border=True,
                border_width=6,
                tail=BubbleTail(0, 0.4),
            ),
            QRectF(330, 230, 200, 110),
        ),
        (
            "6 rect corner20 tail up-left",
            BubbleStyle(
                corner=20, border=True, border_width=4, tail=BubbleTail(225, 0.5)
            ),
            QRectF(600, 230, 200, 110),
        ),
        (
            "7 alpha=140 border2",
            BubbleStyle(alpha=140, border=True, border_width=2),
            QRectF(60, 410, 200, 110),
        ),
        (
            "8 ellipse tail down 2",
            BubbleStyle(
                shape=SHAPE_ELLIPSE, tail=BubbleTail(90, 0.6, width=0.28)
            ),
            QRectF(330, 410, 220, 110),
        ),
        (
            "9 rect border wide 16",
            BubbleStyle(border=True, border_width=16, tail=BubbleTail(90, 0.3)),
            QRectF(620, 410, 200, 110),
        ),
    ]
    for label, style, rect in cells:
        paint_bubble(painter, rect, style)
        print(f"  cell {label:32s} rect={rect.x():.0f},{rect.y():.0f} "
              f"bounds={bubble_path(rect, style).boundingRect()}")
    painter.end()
    image.save(OUT)
    print(f"\n出图: {OUT}")


if __name__ == "__main__":
    bad = numeric_checks()
    render_grid()
    print(f"\n数值问题数: {bad}")
    sys.exit(1 if bad else 0)
