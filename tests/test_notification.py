"""NotificationCenter 单元回归：锚点定位、keyed toast 刷新去重、
activity 开关、status 置空移除。离屏运行，不碰真实 UI。
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.config import load_config

load_config()

from qtpy.QtWidgets import QApplication, QGraphicsScene, QGraphicsView, QWidget

from ui.custom_widget.notification import (
    ActivityLabel,
    NotificationCenter,
    StatusBadge,
    ToastLabel,
)


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


# 持有引用防止 GC 回收 QApplication（PyQt6 引用计数，失去 Python 引用即销毁
# C++ 对象，随后任何 QWidget 操作都会触发 Windows fail-fast 0xC0000409）。
_APP = qapp()


class NotificationCenterTest(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.resize(800, 600)
        self.host.show()  # viewport 在真实应用中可见；isVisible 依赖祖先可见性
        self.center = NotificationCenter()
        self.center.attach(self.host)

    def tearDown(self):
        self.center.detach()
        self.host.deleteLater()

    def test_toast_anchored_and_positioned(self):
        self.center.toast("hello", anchor="top-left")
        items = self.center._items
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ToastLabel)
        self.assertTrue(items[0].isVisible())
        self.assertGreaterEqual(items[0].pos().x(), 0)
        self.assertGreaterEqual(items[0].pos().y(), 0)

    def test_keyed_toast_replaces_not_stacks(self):
        self.center.toast("a", key="zoom", anchor="bottom-center")
        self.center.toast("b", key="zoom", anchor="bottom-center")
        self.assertEqual(len(self.center._items), 1)
        self.assertEqual(self.center._items[0].text(), "b")

    def test_activity_toggle(self):
        self.center.activity("job", True, "working")
        self.assertTrue(self.center.is_active("job"))
        self.assertIsInstance(self.center._items[0], ActivityLabel)
        self.center.activity("job", False)
        self.assertFalse(self.center.is_active("job"))
        self.assertEqual(len(self.center._items), 0)

    def test_status_none_removes(self):
        self.center.status("notext", "No-text BG")
        self.assertEqual(len(self.center._items), 1)
        self.assertIsInstance(self.center._items[0], StatusBadge)
        self.center.status("notext", None)
        self.assertEqual(len(self.center._items), 0)

    def test_bottom_stack_flows_upward(self):
        self.center.toast("one", anchor="bottom-center")
        self.center.toast("two", anchor="bottom-center")
        a, b = self.center._items[0], self.center._items[1]
        # 后添加的条目应排在先添加的之上（y 更小）
        self.assertLess(b.pos().y(), a.pos().y())

    def test_no_host_is_silent_noop(self):
        detached = NotificationCenter()
        detached.toast("dropped")  # 未 attach 时应静默忽略，不抛异常
        self.assertEqual(len(detached._items), 0)

    def test_gv_host_immune_to_scroll_drift(self):
        """宿主必须挂 QGraphicsView 本身而非 viewport：QAbstractScrollArea
        滚动时对 viewport 做像素级 scroll（子控件一并平移），缩放调整滚动条
        会把挂 viewport 的 toast 漂走。"""
        scene = QGraphicsScene(0, 0, 4000, 6000)
        scene.addRect(0, 0, 4000, 6000)
        view = QGraphicsView(scene)
        view.resize(800, 580)
        view.show()
        center = NotificationCenter()
        center.attach(view)  # 回归点：不得改回 attach(view.viewport())
        try:
            center.toast("pinned", anchor="top-center", duration=900000)
            item = center._items[0]
            base_pos = item.pos()
            for factor in (1.25, 1.5625, 0.8):
                scene.setSceneRect(scene.sceneRect().adjusted(0, 0, 1000 * factor, 1000 * factor))
                for bar in (view.verticalScrollBar(), view.horizontalScrollBar()):
                    bar.setValue(int(bar.maximum() * 0.1))
                QApplication.processEvents()
                self.assertEqual(item.pos(), base_pos)
        finally:
            center.detach()
            view.deleteLater()

    def test_reattach_clears_old_items(self):
        self.center.toast("one", anchor="top-left")
        self.assertEqual(len(self.center._items), 1)
        new_host = QWidget()
        new_host.resize(640, 480)
        new_host.show()
        self.center.attach(new_host)
        self.assertEqual(len(self.center._items), 0)
        self.center.toast("two", anchor="top-left")
        self.assertEqual(len(self.center._items), 1)
        new_host.deleteLater()


if __name__ == "__main__":
    unittest.main()