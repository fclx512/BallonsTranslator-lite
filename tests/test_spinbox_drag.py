"""Blender 式拖拽调值的按下区：数值框编辑区必须能起拖。

回归点：数值框的编辑区是一个几乎铺满控件的 ``QLineEdit`` 子控件，鼠标按下
先落到它身上、不会冒泡到 ``QAbstractSpinBox.mousePressEvent``。当年只在
``SizeComboBox`` 里挂了 lineEdit 事件代理，数值框只重载宿主鼠标事件 ——
于是「编辑区拖不动、只有边框几像素能拖」。现在代理收进
``ui/custom_widget/spinbox.py::DragAdjustMixin``，两条入口共用同一套三段式。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_spinbox_drag.py
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

from qtpy.QtCore import QEvent, QPoint, Qt  # noqa: E402
from qtpy.QtGui import QMouseEvent  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402


def send(widget, kind, pos, buttons=Qt.MouseButton.LeftButton):
    """把鼠标事件直接投给 widget（模拟"按下落在它身上"的真实路由）。"""
    point = QPoint(*pos)
    event = QMouseEvent(
        kind,
        point.toPointF(),
        widget.mapToGlobal(point).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


class DragProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _spin(self):
        from ui.custom_widget import NoArrowsSpinBox

        spin = NoArrowsSpinBox()
        spin.setRange(0, 100)
        spin.setValue(50)
        spin.resize(80, 26)
        spin.show()
        self.app.processEvents()
        self.addCleanup(spin.deleteLater)
        return spin

    def test_edit_area_press_starts_drag(self):
        """编辑区（lineEdit）上的按下必须进入 pending，而不是落在文本选区上。"""
        spin = self._spin()
        line_edit = spin.lineEdit()
        send(line_edit, QEvent.Type.MouseButtonPress, (30, 10))
        self.assertTrue(spin._drag_pending)
        self.assertFalse(spin._drag_active)

    def test_edit_area_drag_changes_value_and_commits_once(self):
        spin = self._spin()
        commits = []
        spin.drag_finished.connect(lambda: commits.append(spin.value()))
        line_edit = spin.lineEdit()
        send(line_edit, QEvent.Type.MouseButtonPress, (30, 10))
        # 30px = 6 步 × singleStep 1
        send(line_edit, QEvent.Type.MouseMove, (60, 10))
        self.assertTrue(spin._drag_active)
        self.assertEqual(spin.value(), 56)
        self.assertEqual(commits, [])  # 拖拽中不提交
        send(line_edit, QEvent.Type.MouseButtonRelease, (60, 10))
        self.assertEqual(commits, [56])  # 松手提交一次
        self.assertNotEqual(spin._drag_state, "drag")

    def test_edit_area_click_without_move_enters_edit_mode(self):
        spin = self._spin()
        line_edit = spin.lineEdit()
        send(line_edit, QEvent.Type.MouseButtonPress, (30, 10))
        send(line_edit, QEvent.Type.MouseButtonRelease, (31, 10))
        self.assertTrue(spin.hasFocus())
        self.assertEqual(line_edit.selectedText(), "50")

    def test_border_drag_still_works(self):
        """宿主自己的鼠标事件仍接管边框那几像素，行为与编辑区一致。"""
        spin = self._spin()
        send(spin, QEvent.Type.MouseButtonPress, (1, 13))
        send(spin, QEvent.Type.MouseMove, (31, 13))
        send(spin, QEvent.Type.MouseButtonRelease, (31, 13))
        self.assertEqual(spin.value(), 56)

    def test_disabled_spin_ignores_drag(self):
        spin = self._spin()
        spin.setEnabled(False)
        send(spin.lineEdit(), QEvent.Type.MouseButtonPress, (30, 10))
        self.assertFalse(spin._drag_pending)

    def test_size_combobox_shares_the_same_proxy(self):
        from ui.custom_widget import SizeComboBox

        combo = SizeComboBox(val_range=[0, 100], param_name="p", init_value=10)
        combo.resize(90, 26)
        combo.show()
        self.app.processEvents()
        self.addCleanup(combo.deleteLater)
        seen = []
        combo.param_changed.connect(lambda name, value: seen.append(value))
        line_edit = combo.lineEdit()
        send(line_edit, QEvent.Type.MouseButtonPress, (30, 10))
        send(line_edit, QEvent.Type.MouseMove, (55, 10))  # 25px = 0.25
        send(line_edit, QEvent.Type.MouseButtonRelease, (55, 10))
        self.assertEqual(seen, [10.25])


if __name__ == "__main__":
    unittest.main()
