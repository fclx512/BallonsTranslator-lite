"""Offscreen regression guard for the upstream-compat notice dialog (2026-10-01).

背景（用户实测缺陷）：打开无 ``base_styles`` 的上游/旧版工程时，兼容差异
提示窗把**无父**的 ``QCheckBox`` 临时对象交给 ``QMessageBox.setCheckBox``
（``ui/mainwindow.py``）。PyQt6 认定该对象归 Python 所有，语句结束即回收
C++ 对象；``QMessageBox`` 内部的复选框指针随之悬空——弹窗布局/绘制或
``checkBox()`` 访问就是 access violation（症状：弹出兼容提示后卡死闪退、
终端无 Python 报错）。复选框必须构造期挂父，与
``ui/canvas.py::Canvas._confirm_group_undo`` 的写法一致。

判据取「exec 时刻复选框仍是 box 的活子对象」：无父写法在对象回收后
``findChildren`` 为空（红），构造期挂父写法恒为 1（绿）。这里只检查存活
与父子关系，不碰悬空指针本身，因此红态是断言失败而不是进程崩溃。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_upstream_notice_dialog.py
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QMessageBox,
    QWidget,
)


class _StubWindow(QWidget):
    """只提供 ``_maybe_seed_upstream_styles`` 需要的成员（工程/管理器入口/tr）。

    必须是 QWidget：真实调用点把主窗口当 ``parent`` 传进 ``QMessageBox``，
    弹窗构造这一环正是缺陷所在，不能绕过。
    """

    def __init__(self):
        super().__init__()
        self.imgtrans_proj = SimpleNamespace(
            pages={}, loaded_without_base_styles=True
        )
        self._styleMgrDialog = None
        self.textPanel = None

    def tr(self, text: str) -> str:
        return text

    def _upstream_diff_notice(self) -> str:
        return self.tr("notice")


class UpstreamNoticeDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.mainwindow import MainWindow

        cls.MainWindow = MainWindow
        cls._APP = QApplication.instance() or QApplication([])

    def setUp(self):
        from utils.config import pcfg

        # pcfg 是单例：别的用例 load_config 会灌入用户配置，逐条显式赋值
        self._saved_dismissed = pcfg.upstream_diff_notice_dismissed
        pcfg.upstream_diff_notice_dismissed = False
        self.addCleanup(
            setattr, pcfg, "upstream_diff_notice_dismissed", self._saved_dismissed
        )

    def _assert_checkbox_live(self, box) -> QCheckBox:
        """box 的复选框必须是活子对象；无父临时对象被回收后指针悬空。

        先查 ``findChildren`` 再返回：红态（无父写法）在这里断言失败而不是
        去碰悬空指针——直接访问 ``checkBox()`` 是进程级 access violation
        （实测 exit 139、无 Python 报错，与用户症状同源）。
        """
        children = box.findChildren(QCheckBox)
        self.assertTrue(
            children,
            "兼容提示窗的 QCheckBox 已不在 box 子对象里——无父临时对象被回收"
            "会让 box 内部指针悬空（access violation）",
        )
        self.assertIs(children[0].parent(), box)
        return children[0]

    def test_notice_box_checkbox_is_live_child_at_exec(self):
        """无命名样式的上游项目：提示窗复选框必须是活子对象（悬空即 AV）。"""
        seen = {}

        def fake_exec(box):
            seen["checkbox"] = self._assert_checkbox_live(box)
            return QMessageBox.StandardButton.Ok

        window = _StubWindow()
        with mock.patch.object(QMessageBox, "exec", fake_exec):
            self.MainWindow._maybe_seed_upstream_styles(window)

        self.assertIsNotNone(seen.get("checkbox"))
        self.assertFalse(window.imgtrans_proj.loaded_without_base_styles)

    def test_notice_checkbox_writes_dismiss_flag(self):
        """勾选「不再提示」走 ``pcfg.upstream_diff_notice_dismissed`` 契约。"""
        from utils.config import pcfg

        def fake_exec(box):
            self._assert_checkbox_live(box).setChecked(True)
            return QMessageBox.StandardButton.Ok

        window = _StubWindow()
        with mock.patch.object(QMessageBox, "exec", fake_exec), mock.patch(
            "ui.mainwindow.save_config"
        ) as save:
            self.MainWindow._maybe_seed_upstream_styles(window)

        self.assertTrue(pcfg.upstream_diff_notice_dismissed)
        save.assert_called_once()

    def test_dismissed_flag_silences_notice(self):
        """已勾选「不再提示」：不再弹窗，也不越权播种。"""
        from utils.config import pcfg

        pcfg.upstream_diff_notice_dismissed = True
        window = _StubWindow()
        with mock.patch.object(QMessageBox, "exec") as exec_mock:
            self.MainWindow._maybe_seed_upstream_styles(window)

        exec_mock.assert_not_called()
        self.assertFalse(window.imgtrans_proj.loaded_without_base_styles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
