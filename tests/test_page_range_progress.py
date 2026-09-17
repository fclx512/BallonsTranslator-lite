"""页码区间控件的交互与数据口径（复刻上游三件套）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_page_range_progress.py
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

from qtpy.QtCore import QEvent, QPointF, Qt  # noqa: E402
from qtpy.QtGui import QMouseEvent  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

PAGES = ["%03d.jpg" % i for i in range(1, 21)]


def _mouse(widget, kind, pos):
    """把鼠标事件直接投给 widget（模拟"按下落在它身上"的真实路由）。"""
    event = QMouseEvent(
        kind,
        pos,
        widget.mapToGlobal(pos.toPoint()).toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _press(widget, pos):
    _mouse(widget, QEvent.Type.MouseButtonPress, pos)


def _release(widget, pos):
    _mouse(widget, QEvent.Type.MouseButtonRelease, pos)


class PageRangeSpinBoxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _spin(self):
        from ui.custom_widget import PageRangeSpinBox

        spin = PageRangeSpinBox()
        spin.setRange(1, 20)
        spin.setValue(7)
        spin.setFixedWidth(82)
        spin.show()
        self.app.processEvents()
        self.addCleanup(spin.deleteLater)
        return spin

    def test_chevron_buttons_step_in_place(self):
        spin = self._spin()
        up_rect, down_rect = spin._button_rects()
        self.assertGreater(up_rect.left(), down_rect.right())  # 上箭头在右、下箭头在左
        _press(spin, up_rect.center().toPointF())
        self.assertEqual(spin.value(), 8)
        _press(spin, down_rect.center().toPointF())
        self.assertEqual(spin.value(), 7)

    def test_clicks_elsewhere_reach_the_native_editor(self):
        spin = self._spin()
        before = spin.value()
        _press(spin, QPointF(10, 12))
        self.assertEqual(spin.value(), before)
        self.assertTrue(spin.lineEdit().hasFocus())


class PageProgressRangeBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _bar(self):
        from ui.custom_widget import PageProgressRangeBar

        bar = PageProgressRangeBar(PAGES)
        bar.resize(400, bar.HEIGHT)
        self.addCleanup(bar.deleteLater)
        return bar

    def test_set_range_clamps_and_emits_once(self):
        bar = self._bar()
        seen = []
        bar.range_changed.connect(lambda lo, hi: seen.append((lo, hi)))
        bar.set_range(3, 9)
        self.assertEqual((bar.start_index, bar.end_index), (2, 8))
        self.assertEqual(seen, [(3, 9)])
        bar.set_range(3, 9)  # 同值不再发
        self.assertEqual(seen, [(3, 9)])
        bar.set_range(15, 4)  # 倒序收口成单页
        self.assertEqual((bar.start_index, bar.end_index), (14, 14))
        bar.set_range(-5, 99)  # 越界夹到全书
        self.assertEqual((bar.start_index, bar.end_index), (0, 19))

    def test_click_on_track_moves_nearest_handle(self):
        bar = self._bar()
        bar.set_range(1, 20, emit=False)
        seen = []
        bar.range_changed.connect(lambda lo, hi: seen.append((lo, hi)))
        track_y = bar._track_rect().center().y()
        # 满区间时点在左半边 → 就近取起页手柄，起页落到第 6 页
        _press(bar, QPointF(bar._page_x(5), track_y))
        self.assertEqual((bar.start_index, bar.end_index), (5, 19))
        self.assertEqual(seen, [(6, 20)])
        # 再点靠近末页处 → 这次离末页手柄更近，末页被拉过来
        _press(bar, QPointF(bar._page_x(15), track_y))
        self.assertEqual((bar.start_index, bar.end_index), (5, 15))
        self.assertEqual(seen[-1], (6, 16))

    def test_overlapping_handles_split_by_drag_direction(self):
        bar = self._bar()
        bar.set_range(8, 8, emit=False)
        _press(bar, QPointF(bar._page_x(2), bar._track_rect().center().y()))
        self.assertEqual((bar.start_index, bar.end_index), (2, 7))
        bar.set_range(8, 8, emit=False)
        _press(bar, QPointF(bar._page_x(15), bar._track_rect().center().y()))
        self.assertEqual((bar.start_index, bar.end_index), (7, 15))

    def test_finished_pages_pad_and_truncate(self):
        bar = self._bar()
        bar.set_finished_pages([True] * 3)
        self.assertEqual(bar.finished_count, 3)
        self.assertEqual(len(bar.finished_pages), len(PAGES))
        bar.set_finished_pages([True] * 100)
        self.assertEqual(bar.finished_count, len(PAGES))


class PageRangeProgressWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self, **kwargs):
        from ui.custom_widget import PageRangeProgressWidget

        widget = PageRangeProgressWidget(PAGES, **kwargs)
        widget.show()
        self.app.processEvents()
        self.addCleanup(widget.deleteLater)
        return widget

    def test_default_range_is_everything(self):
        widget = self._widget()
        self.assertEqual(widget.range_values(), (1, len(PAGES)))
        self.assertEqual(widget.range_bar.finished_count, 0)

    def test_spin_boxes_and_bar_stay_in_sync(self):
        widget = self._widget()
        seen = []
        widget.range_changed.connect(lambda lo, hi: seen.append((lo, hi)))
        widget.range_start.setValue(4)
        self.assertEqual(widget.range_values(), (4, len(PAGES)))
        self.assertEqual(
            (widget.range_bar.start_index, widget.range_bar.end_index),
            (3, len(PAGES) - 1),
        )
        self.assertEqual(seen[-1], (4, len(PAGES)))
        # 起止不会交叉：起页推过末页时末页被顶走
        widget.range_end.setValue(18)
        widget.range_start.setValue(19)
        self.assertEqual(widget.range_values(), (19, 19))
        self.assertEqual(widget.range_bar.start_index, widget.range_bar.end_index)

    def test_bar_drag_writes_back_to_spin_boxes(self):
        widget = self._widget()
        bar = widget.range_bar
        bar.resize(400, bar.HEIGHT)
        track_y = bar._track_rect().center().y()
        _press(bar, QPointF(bar._page_x(4), track_y))
        _release(bar, QPointF(bar._page_x(4), track_y))
        self.assertEqual(
            (widget.range_start.value(), widget.range_end.value()),
            (5, len(PAGES)),
        )

    def test_no_pages_disables_selectors(self):
        from ui.custom_widget import PageRangeProgressWidget

        widget = PageRangeProgressWidget([])
        self.addCleanup(widget.deleteLater)
        self.assertFalse(widget.range_start.isEnabled())
        self.assertFalse(widget.range_end.isEnabled())
        self.assertEqual(widget.range_bar.page_count, 0)


if __name__ == "__main__":
    unittest.main()
