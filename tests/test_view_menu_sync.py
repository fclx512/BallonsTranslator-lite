"""Offscreen regression tests for the View-menu checkmark sync.

用户 2026-09-20 报告：用画布**快捷菜单**（`ui/context_menu_config.py` 的
`CAT_TOGGLE` 勾选式命令）改「过界模式 / 溢出裁剪」后，标题栏 **View 菜单**
里的勾选态不跟着变——功能本身正常，只有菜单显示停在旧状态。

回归源头：95f6ab66（2026-08-16）把快捷菜单接到 `MainWindow.on_clip_overflow_menu_toggled`
/ `MainWindow.on_overflow_triggered` 上，而这两个 handler 只**下行**写 `pcfg`
（＋设置面板 checker），没**回写** View 菜单的 QAction；菜单自己的勾选态只有
用户从菜单里点才会翻转，于是就地失真。序号徽标那条因为顺带跑了
`MainWindow._on_seq_badge_changed`（里面会回写 action）才没坏。

测试绑**真实** handler（`MethodType` 到轻量 shim，同 `tests/test_pie_menu_dismiss.py`
的做法）＋**真实** QAction，并走 `run_cmd`（快捷菜单运行时真正的入口）跑通
「快捷菜单 → pcfg → View 菜单勾选态」整条链。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_view_menu_sync.py -v
"""

import os
import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtGui import QAction, QActionGroup  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QMainWindow,
    QMenu,
)

from ui.context_menu_config import run_cmd  # noqa: E402
from ui.mainwindow import MainWindow  # noqa: E402
from utils.config import pcfg  # noqa: E402


class _EmptyLayer:
    """Stands in for canvas.textLayer（序号徽标刷新会遍历它）。"""

    def childItems(self):
        return []


class _FakeCanvas:
    """Only the overflow-mode contract: 写 pcfg（与 ui/canvas.py::Canvas.setOverflowMode 同）。"""

    textLayer = _EmptyLayer()

    def setOverflowMode(self, enabled):
        pcfg.overflow_mode = enabled


class _FakeTitleBar:
    """真 QAction + 真 viewMenu，够 `_sync_view_menu_actions` 用。

    画板 / 编辑器用同一个互斥 QActionGroup，与
    `ui/mainwindowbars.py::TitleBar` 一致——`_sync_view_mode_actions` 会同时
    设置两者，互斥组配错会在这里暴露。
    """

    def __init__(self):
        self.viewMenu = QMenu()

        def _act(text):
            action = QAction(text, self.viewMenu)
            action.setCheckable(True)
            self.viewMenu.addAction(action)
            return action

        self.darkModeAction = _act("Dark Mode")
        self.overflowAction = _act("Overflow Mode")
        self.seqBadgeAction = _act("Sequence Badge")
        self.clipOverflowAction = _act("Overflow Clip")
        # 画板 / 编辑器互斥（同 ui/mainwindowbars.py::TitleBar 的 QActionGroup）
        self.viewModeGroup = QActionGroup(self.viewMenu)
        self.viewModeGroup.setExclusive(True)
        self.drawBoardAction = _act("Drawing Board")
        self.texteditAction = _act("Text Editor")
        self.viewModeGroup.addAction(self.drawBoardAction)
        self.viewModeGroup.addAction(self.texteditAction)


class _ViewSyncShim(QMainWindow):
    """QMainWindow binding the real MainWindow sync handlers."""

    def __init__(self):
        super().__init__()
        self.save_calls = 0
        self.titleBar = _FakeTitleBar()
        self.bottomBar = SimpleNamespace(
            paintChecker=QCheckBox(), texteditChecker=QCheckBox()
        )
        self.configPanel = SimpleNamespace(
            seq_badge_checker=QCheckBox(), clip_overflow_checker=QCheckBox()
        )
        self.canvas = _FakeCanvas()

    def save_config(self):
        self.save_calls += 1


class TestViewMenuSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )

    def setUp(self):
        self.app.processEvents()
        for name in ("clip_text_overflow", "overflow_mode", "show_seq_badge", "darkmode"):
            previous = getattr(pcfg, name)
            self.addCleanup(setattr, pcfg, name, previous)

        self.shim = _ViewSyncShim()
        for name in (
            "_sync_view_menu_actions",
            "_sync_view_mode_actions",
            "_on_seq_badge_changed",
            "on_seq_badge_menu_toggled",
            "on_clip_overflow_menu_toggled",
            "on_overflow_triggered",
        ):
            setattr(self.shim, name, MethodType(getattr(MainWindow, name), self.shim))
        # MainWindow.__init__ 的接线（shim 不跑 __init__，这里照抄）
        self.shim.titleBar.viewMenu.aboutToShow.connect(
            self.shim._sync_view_menu_actions
        )

    # ── 快捷菜单 → View 菜单勾选态 ─────────────────────────────

    def test_clip_overflow_from_quick_menu_updates_view_action(self):
        pcfg.clip_text_overflow = True
        self.shim._sync_view_menu_actions()
        self.assertTrue(self.shim.titleBar.clipOverflowAction.isChecked())

        self.assertTrue(run_cmd(self.shim, "clip_overflow"))
        self.assertFalse(pcfg.clip_text_overflow)
        self.assertFalse(
            self.shim.titleBar.clipOverflowAction.isChecked(),
            "快捷菜单关掉溢出裁剪后，View 菜单勾选态必须跟着关",
        )

        self.assertTrue(run_cmd(self.shim, "clip_overflow"))
        self.assertTrue(pcfg.clip_text_overflow)
        self.assertTrue(self.shim.titleBar.clipOverflowAction.isChecked())

    def test_overflow_mode_from_quick_menu_updates_view_action(self):
        pcfg.overflow_mode = False
        self.shim._sync_view_menu_actions()
        self.assertFalse(self.shim.titleBar.overflowAction.isChecked())

        self.assertTrue(run_cmd(self.shim, "overflow_mode"))
        self.assertTrue(pcfg.overflow_mode)
        self.assertTrue(
            self.shim.titleBar.overflowAction.isChecked(),
            "快捷菜单开过界模式后，View 菜单勾选态必须跟着开",
        )

        self.assertTrue(run_cmd(self.shim, "overflow_mode"))
        self.assertFalse(pcfg.overflow_mode)
        self.assertFalse(self.shim.titleBar.overflowAction.isChecked())

    def test_seq_badge_from_quick_menu_updates_view_action(self):
        """原本就没坏的那条：作对照，避免修一处坏一处。"""
        pcfg.show_seq_badge = True
        self.shim._sync_view_menu_actions()

        self.assertTrue(run_cmd(self.shim, "seq_badge"))
        self.assertFalse(pcfg.show_seq_badge)
        self.assertFalse(self.shim.titleBar.seqBadgeAction.isChecked())

    # ── 兜底通道：菜单弹出前现读真值 ───────────────────────────

    def test_view_menu_resyncs_on_about_to_show(self):
        """任何入口只改 pcfg 却没回写 action，菜单弹出前也要被纠正回来。"""
        pcfg.clip_text_overflow = False
        pcfg.overflow_mode = True
        self.shim.titleBar.viewMenu.aboutToShow.emit()

        self.assertFalse(self.shim.titleBar.clipOverflowAction.isChecked())
        self.assertTrue(self.shim.titleBar.overflowAction.isChecked())

        pcfg.clip_text_overflow = True
        pcfg.overflow_mode = False
        self.shim.titleBar.viewMenu.aboutToShow.emit()
        self.assertTrue(self.shim.titleBar.clipOverflowAction.isChecked())
        self.assertFalse(self.shim.titleBar.overflowAction.isChecked())

    def test_view_menu_mirrors_bottom_bar_modes(self):
        """画板 / 编辑器两条的真值是底部栏 checker，同样要镜像到菜单。"""
        self.shim.bottomBar.paintChecker.setChecked(True)
        self.shim.titleBar.viewMenu.aboutToShow.emit()
        self.assertTrue(self.shim.titleBar.drawBoardAction.isChecked())
        self.assertFalse(self.shim.titleBar.texteditAction.isChecked())

        self.shim.bottomBar.paintChecker.setChecked(False)
        self.shim.bottomBar.texteditChecker.setChecked(True)
        self.shim.titleBar.viewMenu.aboutToShow.emit()
        self.assertTrue(self.shim.titleBar.texteditAction.isChecked())
        self.assertFalse(
            self.shim.titleBar.drawBoardAction.isChecked(),
            "互斥组里切到编辑器后，画板勾选态必须让位",
        )

    def test_settings_panel_checker_stays_in_sync(self):
        """设置面板侧仍是双向：快捷菜单改完，设置面板复选框跟着变。"""
        pcfg.clip_text_overflow = True
        self.assertTrue(run_cmd(self.shim, "clip_overflow"))
        self.assertFalse(self.shim.configPanel.clip_overflow_checker.isChecked())

        self.assertTrue(run_cmd(self.shim, "seq_badge"))
        self.assertFalse(self.shim.configPanel.seq_badge_checker.isChecked())


if __name__ == "__main__":
    unittest.main()
