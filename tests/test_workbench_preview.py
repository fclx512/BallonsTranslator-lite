"""工作台审批预览浮层（D44）回归：滚轮缩放 / 拖拽平移 / 拉伸 / 关闭。

预览原先是任务页里固定 120~280px 高的一小块，大图看不全（用户实测 433×395
就被截）。现为工作台之外的**临时浮层**，本文件锁它的四条底线：

1. **默认 100% 原比例**（D23 的审批口径没变——不自动缩到适应窗口）；
2. 滚轮缩放**以光标为锚点**、可拖拽平移、拖不出边界；
3. 浮层能在中央区里自由拖动、拉伸，且不会被拖出宿主；
4. **尺寸随图自适应**（上限 ``MAX_SIZE``）、**点画布或 Esc 即关**（不设关闭钮，
   用户手动调过尺寸后不再自动改）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_workbench_preview.py -q
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from qtpy.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent  # noqa: E402
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402


def _mouse(widget, etype, local, button=Qt.MouseButton.LeftButton):
    global_pos = widget.mapToGlobal(QPoint(int(local.x()), int(local.y())))
    return QApplication.sendEvent(
        widget,
        QMouseEvent(
            etype,
            QPointF(local),
            QPointF(global_pos),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def _wheel(widget, local, notches):
    """滚轮事件（``notches`` 正数＝放大；每格 angleDelta 120）。"""
    global_pos = widget.mapToGlobal(QPoint(int(local.x()), int(local.y())))
    return QApplication.sendEvent(
        widget,
        QWheelEvent(
            QPointF(local),
            QPointF(global_pos),
            QPoint(0, 0),
            QPoint(0, int(notches * 120)),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )


class PreviewPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.workbench_preview import WorkbenchPreviewPanel

        self.host = QWidget()
        self.host.resize(900, 700)
        self.host.show()
        self.panel = WorkbenchPreviewPanel(self.host)
        self.canvas = self.panel.canvas
        self.addCleanup(self.host.close)

    def _big_pixmap(self, width=400, height=300):
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#808080"))
        return pixmap

    def _show(self, width=400, height=300):
        self.panel.show_content(self._big_pixmap(width, height), "caption")
        self.app.processEvents()

    # ── 默认口径 ────────────────────────────────────────────────

    def test_opens_at_actual_size_not_fitted(self):
        """D23：浮层默认 100% 原比例，不自动缩到适应窗口。"""
        self._show(400, 300)
        self.assertEqual(self.canvas.scale(), 1.0)
        self.assertEqual(self.panel._zoom_label.text(), "100%")
        self.assertTrue(self.panel.is_open())
        self.assertEqual(self.canvas._content_size().width(), 400)

    def test_small_image_is_not_blown_up_by_fit(self):
        """适应窗口对小于视口的图不放大（上限 100%）。"""
        self._show(40, 30)
        self.canvas.fit()
        self.assertEqual(self.canvas.scale(), 1.0)

    def test_fit_shrinks_only(self):
        self._show(4000, 3000)
        self.canvas.fit()
        self.assertLess(self.canvas.scale(), 1.0)
        self.assertLessEqual(self.canvas._content_size().width(), self.canvas.width())

    # ── 缩放 / 平移 ─────────────────────────────────────────────

    def test_wheel_zooms_around_cursor(self):
        """滚轮以光标为锚点：光标下的那个内容点缩放前后位置不变。"""
        self._show(4000, 3000)
        self.canvas.actual_size()
        # 先缩小到能看见边界，再放大
        self.canvas._apply_scale(0.2)
        self.app.processEvents()
        anchor = QPoint(200, 150)
        content_x = (anchor.x() - self.canvas._offset.x()) / self.canvas.scale()
        _wheel(self.canvas, anchor, 2)
        self.assertGreater(self.canvas.scale(), 0.2)
        after = (anchor.x() - self.canvas._offset.x()) / self.canvas.scale()
        self.assertAlmostEqual(after, content_x, delta=1.5)

    def test_wheel_scale_is_clamped(self):
        from ui.workbench_preview import MAX_SCALE, MIN_SCALE

        self._show(400, 300)
        for _ in range(40):
            _wheel(self.canvas, QPoint(50, 50), 3)
        self.assertLessEqual(self.canvas.scale(), MAX_SCALE)
        for _ in range(80):
            _wheel(self.canvas, QPoint(50, 50), -3)
        self.assertGreaterEqual(self.canvas.scale(), MIN_SCALE)

    def test_drag_pans_when_zoomed_in_and_clamps_at_edges(self):
        self._show(4000, 3000)
        self.canvas._apply_scale(1.5)
        self.app.processEvents()
        before = QPoint(self.canvas._offset)
        _mouse(self.canvas, QEvent.Type.MouseButtonPress, QPoint(300, 300))
        _mouse(self.canvas, QEvent.Type.MouseMove, QPoint(200, 200))
        _mouse(self.canvas, QEvent.Type.MouseButtonRelease, QPoint(200, 200))
        self.assertNotEqual(QPoint(self.canvas._offset), before)
        # 反向猛拖：被边界挡住，不会把图拖出视口
        _mouse(self.canvas, QEvent.Type.MouseButtonPress, QPoint(100, 100))
        _mouse(self.canvas, QEvent.Type.MouseMove, QPoint(5000, 5000))
        _mouse(self.canvas, QEvent.Type.MouseButtonRelease, QPoint(5000, 5000))
        self.assertLessEqual(self.canvas._content_rect().left(), self.canvas.rect().left() + 1)
        self.assertLessEqual(self.canvas._content_rect().top(), self.canvas.rect().top() + 1)

    def test_image_smaller_than_viewport_stays_centered(self):
        self._show(60, 40)
        _mouse(self.canvas, QEvent.Type.MouseButtonPress, QPoint(10, 10))
        _mouse(self.canvas, QEvent.Type.MouseMove, QPoint(300, 300))
        _mouse(self.canvas, QEvent.Type.MouseButtonRelease, QPoint(300, 300))
        rect = self.canvas._content_rect()
        self.assertAlmostEqual(
            rect.center().x(), self.canvas.rect().center().x(), delta=1
        )
        self.assertAlmostEqual(
            rect.center().y(), self.canvas.rect().center().y(), delta=1
        )

    def test_double_click_fits(self):
        self._show(4000, 3000)
        _mouse(self.canvas, QEvent.Type.MouseButtonDblClick, QPoint(100, 100))
        self.assertLess(self.canvas.scale(), 1.0)

    # ── 没有图时 ────────────────────────────────────────────────

    def test_missing_image_shows_reason_instead(self):
        self.panel.show_content(None, "该行没有可用的预览")
        self.app.processEvents()
        self.assertTrue(self.canvas._hint.isVisible())
        self.assertEqual(self.canvas._hint.text(), "该行没有可用的预览")
        self.assertEqual(self.canvas.scale(), 1.0)

    # ── 浮层窗口操作 ────────────────────────────────────────────

    def test_header_drag_moves_panel_inside_host(self):
        self._show()
        self.panel.move(20, 20)
        self.assertEqual(self.panel.pos(), QPoint(20, 20))
        _mouse(self.panel._header, QEvent.Type.MouseButtonPress, QPoint(30, 10))
        _mouse(self.panel._header, QEvent.Type.MouseMove, QPoint(120, 90))
        _mouse(self.panel._header, QEvent.Type.MouseButtonRelease, QPoint(120, 90))
        self.assertEqual(self.panel.pos(), QPoint(110, 100))
        # 拖出宿主右／下边界：钉在宿主内
        _mouse(self.panel._header, QEvent.Type.MouseButtonPress, QPoint(30, 10))
        _mouse(self.panel._header, QEvent.Type.MouseMove, QPoint(9000, 9000))
        _mouse(self.panel._header, QEvent.Type.MouseButtonRelease, QPoint(9000, 9000))
        self.assertLessEqual(self.panel.x() + self.panel.minimumWidth(), self.host.width())
        self.assertLessEqual(self.panel.y(), self.host.height())

    def test_corner_handle_resizes_and_respects_floor(self):
        self._show()
        start = self.panel.size()
        handle = self.panel._handles["bottom-right"]
        _mouse(handle, QEvent.Type.MouseButtonPress, QPoint(2, 2))
        _mouse(handle, QEvent.Type.MouseMove, QPoint(80, 60))
        _mouse(handle, QEvent.Type.MouseButtonRelease, QPoint(80, 60))
        self.assertGreater(self.panel.width(), start.width())
        self.assertGreater(self.panel.height(), start.height())
        # 反向拖过头：不缩到地板尺寸以下
        _mouse(handle, QEvent.Type.MouseButtonPress, QPoint(2, 2))
        _mouse(handle, QEvent.Type.MouseMove, QPoint(-9000, -9000))
        _mouse(handle, QEvent.Type.MouseButtonRelease, QPoint(-9000, -9000))
        self.assertGreaterEqual(self.panel.width(), self.panel.minimumWidth())
        self.assertGreaterEqual(self.panel.height(), self.panel.minimumHeight())

    def test_left_handle_moves_left_edge_only(self):
        self._show()
        self.panel.setGeometry(100, 100, 400, 300)
        handle = self.panel._handles["left"]
        _mouse(handle, QEvent.Type.MouseButtonPress, QPoint(0, 20))
        _mouse(handle, QEvent.Type.MouseMove, QPoint(-50, 20))
        _mouse(handle, QEvent.Type.MouseButtonRelease, QPoint(-50, 20))
        self.assertEqual(self.panel.x(), 50)
        self.assertEqual(self.panel.width(), 450)
        self.assertEqual(self.panel.y(), 100)

    def test_escape_closes_panel(self):
        """Esc 走应用级过滤器：焦点不在浮层里也要能关（焦点留在候选列表上）。"""
        self._show()
        seen = []
        self.panel.closed.connect(lambda: seen.append(True))
        QApplication.sendEvent(
            self.canvas,
            __import__("qtpy.QtGui", fromlist=["QKeyEvent"]).QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
            ),
        )
        self.assertFalse(self.panel.is_open())
        self.assertEqual(seen, [True])

    def test_click_on_canvas_closes_and_next_selection_reopens(self):
        """临时浮层：点画布（宿主区域）即关，不设关闭钮；再选行会重新弹出。"""
        self._show()
        self.assertFalse(hasattr(self.panel, "close_btn"))
        self.assertTrue(self.panel._filter_installed)  # 开着才挂应用级过滤器
        # 点浮层自己身上不关
        _mouse(self.panel.canvas, QEvent.Type.MouseButtonPress, QPoint(5, 5))
        self.assertTrue(self.panel.is_open())
        # 点宿主（画布区）＝关
        _mouse(self.host, QEvent.Type.MouseButtonPress, QPoint(800, 600))
        self.assertFalse(self.panel.is_open())
        self.assertFalse(self.panel._filter_installed)  # 关掉即摘，不给全局留钩子
        self._show(200, 100)
        self.assertTrue(self.panel.is_open())
        self.assertEqual(self.canvas.scale(), 1.0)  # 重新打开仍回 100%

    # ── 尺寸随图自适应 ──────────────────────────────────────────

    def test_panel_fits_the_image_and_caps_at_max_size(self):
        """浮层尺寸跟着图走，上限 MAX_SIZE（小图不泡在空底里）。"""
        self._show(120, 60)
        self.assertGreater(self.panel.width(), 120)
        self.assertLessEqual(self.panel.width(), self.panel.MAX_SIZE.width())
        self.assertLess(self.panel.height(), 200)
        # 视口至少装得下 100% 的图（不然刚打开就要滚动）
        self.assertGreaterEqual(self.canvas.width(), 120)
        self._show(2000, 1500)
        self.assertEqual(self.panel.size(), self.panel.MAX_SIZE)

    def test_manual_resize_wins_over_auto_size(self):
        """用户拉伸过之后，下一张图不再改尺寸（尊重用户摆好的窗口）。"""
        self._show(120, 60)
        handle = self.panel._handles["bottom-right"]
        _mouse(handle, QEvent.Type.MouseButtonPress, QPoint(2, 2))
        _mouse(handle, QEvent.Type.MouseMove, QPoint(120, 90))
        _mouse(handle, QEvent.Type.MouseButtonRelease, QPoint(120, 90))
        kept = self.panel.size()
        self._show(80, 40)
        self.assertEqual(self.panel.size(), kept)


if __name__ == "__main__":
    unittest.main()
