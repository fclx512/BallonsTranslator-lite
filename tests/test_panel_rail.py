"""Offscreen regression tests for the PS-style panel rail rework.

Covers the 2026-08-23 text-panel rework and its same-day revisions: row
numbering stays inside ``TransPairWidget`` (badges / drag column /
accent bar), the annotation capsule is replaced by a rail launcher
(``ui/panel_rail.py::PanelRail``) whose panel hard-docks to the rail's
left side over the canvas area (``ui/custom_widget/rail_dock_panel.py::
RailDockPanel`` — not draggable, resizable via the bottom-left grip,
re-anchors on host resize / rail move, resize floor follows the content
layout), and the text-area toolbar (Source | Translation) sits inside
the bordered ``GroupFrame`` together with the text list. The Edit/Review
mode toggle was removed; the left number badge + drag column layout is
permanent (``ui/textedit_area.py::TransPairWidget``).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_panel_rail.py
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

from qtpy.QtCore import QEvent, QPoint, QPointF, QSize, Qt  # noqa: E402
from qtpy.QtGui import QMouseEvent  # noqa: E402
from qtpy.QtWidgets import QApplication, QCheckBox, QWidget  # noqa: E402

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


def _mouse(widget, etype, local, button=Qt.MouseButton.LeftButton):
    """Send a synthetic mouse event (global pos mirrors local for child
    widgets shown at host origin — deltas are what the handlers use)."""
    global_pos = widget.mapToGlobal(QPoint(int(local.x()), int(local.y())))
    ev = QMouseEvent(
        etype,
        QPointF(local),
        QPointF(global_pos),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    return QApplication.sendEvent(widget, ev)


class TransPairWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_pair(self):
        from ui.textedit_area import TransPairWidget

        return TransPairWidget(_make_blk(), 0)

    def test_row_chrome_restored(self):
        """编号/拖拽列/选中条与文本框一体（2026-08-23 修订回归）。"""
        pw = self._make_pair()
        for attr in ("badge", "drag_area", "accent_bar"):
            self.assertTrue(hasattr(pw, attr), attr)
        self.assertEqual(pw.badge.text(), "1")

    def test_always_full_multiline(self):
        """模式切换已删除：常驻左侧编号+拖拽列，文本框恒为整块多行。"""
        pw = self._make_pair()
        for edit in (pw.e_source, pw.e_trans):
            self.assertEqual(edit.lineWrapMode(), edit.LineWrapMode.WidgetWidth)
            self.assertEqual(edit.min_height, 45)
        # 拖拽列与编号徽章常驻可见（编辑态样式已删除）
        self.assertTrue(pw.drag_area.isVisibleTo(pw))
        self.assertTrue(pw.badge.isVisibleTo(pw.drag_area))

    def test_update_index_updates_badge(self):
        pw = self._make_pair()
        pw.updateIndex(5)
        self.assertEqual(pw.badge.text(), "6")
        self.assertEqual(pw.e_source.idx, 5)

    def test_checked_state_runs_accent_animation(self):
        pw = self._make_pair()
        pw._set_checked_state(True)
        self.assertTrue(pw.checked)
        self.assertEqual(pw.property("checked"), True)
        # the accent fade timer must be running (selection cue restored)
        self.assertIsNotNone(pw._accent_timer)
        pw._set_checked_state(False)


class PanelRailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_launchers_stacked_and_fetchable(self):
        from ui.panel_rail import PanelRail, RailLauncherButton

        rail = PanelRail()
        first = RailLauncherButton("rail_annotation")
        second = RailLauncherButton("rail_emphasis")
        rail.add_launcher(first)
        rail.add_launcher(second)
        self.assertIs(rail.launcher_at(0), first)
        self.assertIs(rail.launcher_at(1), second)
        # stretch stays last
        self.assertIsNone(rail.launcher_at(2))
        rail.resize(26, 200)
        rail.show()
        self.app.processEvents()

    def test_checked_paint_contrast(self):
        """激活态：checked 下字形翻白 + 自绘 accent 底 + 底缘状态条，
        绘制不崩（底色在 paintEvent 自绘；PyQt6 Python 子类匹配不到
        QSS 背景规则，见 ui/panel_rail.py::RailLauncherButton）。"""
        from ui.panel_rail import RailLauncherButton

        btn = RailLauncherButton("rail_annotation")
        btn.setChecked(True)
        btn.set_dot(True)
        btn.grab()
        self.assertTrue(btn.isChecked())


class RailDockPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_dock(self, open_field="test_dock_open", reset=True):
        from ui.custom_widget import RailDockPanel
        from ui.panel_rail import PanelRail

        from utils.config import pcfg

        if reset:
            setattr(pcfg, open_field, False)

        host = QWidget()
        host.resize(800, 500)
        # RailDockPanel resolves its parent via
        # rail.window().centralStackWidget — absent on plain QWidget, so
        # expose the window itself as the stack stand-in
        host.centralStackWidget = host
        # rail docked on the right half of the host, like the text panel
        rail = PanelRail(host)
        rail.setGeometry(770, 10, 26, 200)
        from qtpy.QtWidgets import QLabel, QVBoxLayout

        content = QWidget()
        content.setLayout(QVBoxLayout())
        content.layout().addWidget(QLabel("content"))
        dock = RailDockPanel(
            "Title",
            content,
            rail=rail,
            config_open=open_field,
        )
        host.show()
        self.app.processEvents()
        self.addCleanup(host.deleteLater)
        return dock, rail, host

    def test_open_close_and_flags(self):
        from utils.config import pcfg

        dock, _rail, _host = self._make_dock()
        closed_events = []
        dock.closed.connect(lambda: closed_events.append(True))
        dock.open_panel()
        self.assertTrue(dock.isVisible())
        self.assertTrue(pcfg.test_dock_open)
        dock.close_panel()
        self.assertFalse(dock.isVisible())
        self.assertFalse(pcfg.test_dock_open)
        self.assertEqual(closed_events, [True])

    def test_open_anchors_on_canvas_side(self):
        """展开锚定窄栏左侧（画布区），不覆盖右侧文本编辑区。"""
        dock, rail, host = self._make_dock()
        dock.open_panel()
        self.assertFalse(dock.isWindow())  # 窗口内浮层，非独立 OS 窗口
        # fully to the left of the rail with the anchor margin
        self.assertLessEqual(
            dock.x() + dock.width(), rail.x() - dock.ANCHOR_MARGIN + 1
        )
        # clamped inside the host
        self.assertGreaterEqual(dock.x(), 0)
        self.assertGreaterEqual(dock.y(), 0)

    def test_header_not_draggable(self):
        """标题栏不再拖拽：面板位置固定在窄栏锚点（硬连接）。"""
        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        before = dock.pos()
        header = dock._header
        _mouse(header, QEvent.Type.MouseButtonPress, QPointF(10, 10))
        _mouse(header, QEvent.Type.MouseMove, QPointF(60, 40))
        _mouse(header, QEvent.Type.MouseButtonRelease, QPointF(60, 40))
        self.assertEqual(dock.pos(), before)
        # 仍锚定窄栏左侧（画布区）
        self.assertGreaterEqual(dock.x(), 0)
        self.assertGreaterEqual(dock.y(), 0)

    def test_grip_at_bottom_left(self):
        """面板从锚定右缘向左展开，缩放手柄在左下角（镜像斜纹+斜向光标）。"""
        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        grip = dock._grip
        self.assertEqual(grip.x(), 0)
        self.assertEqual(grip.y(), dock.height() - grip.height())
        self.assertEqual(
            grip.cursor().shape(), Qt.CursorShape.SizeBDiagCursor
        )

    def test_grip_resizes_panel(self):
        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        before = dock.size()
        grip = dock._grip
        # 左下角自由角：向左/下拖 = 变大（右缘+顶部保持锚定）
        _mouse(grip, QEvent.Type.MouseButtonPress, QPointF(7, 7))
        _mouse(grip, QEvent.Type.MouseMove, QPointF(-33, 32))
        _mouse(grip, QEvent.Type.MouseButtonRelease, QPointF(-33, 32))
        self.assertEqual(dock.size() - before, QSize(40, 25))

    def test_grip_cannot_shrink_below_floor(self):
        """太小拖不进去：尺寸下限随内容布局（功能项不会被压成一线）。"""
        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        before = dock.size()
        _mouse(dock._grip, QEvent.Type.MouseButtonPress, QPointF(7, 7))
        _mouse(dock._grip, QEvent.Type.MouseMove, QPointF(500, 7))
        _mouse(dock._grip, QEvent.Type.MouseButtonRelease, QPointF(500, 7))
        self.assertEqual(dock.size(), before)

    def test_grip_resize_clamped_to_host(self):
        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        # 向左猛拉：宽度受宿主左缘（x>=0）约束，右缘仍贴住窄栏锚点
        _mouse(dock._grip, QEvent.Type.MouseButtonPress, QPointF(7, 7))
        _mouse(dock._grip, QEvent.Type.MouseMove, QPointF(-1000, 30))
        _mouse(dock._grip, QEvent.Type.MouseButtonRelease, QPointF(-1000, 30))
        self.assertEqual(dock.x(), 0)
        self.assertLessEqual(dock.x() + dock.width(), _host.width())

    def test_host_resize_reanchors_to_rail(self):
        """宿主缩放/窄栏移动后面板自动重锚（位置补偿，无需重开）。"""
        dock, rail, host = self._make_dock()
        dock.open_panel()
        host.resize(900, 600)
        rail.move(870, 10)  # 窄栏随宿主右缘右移
        self.app.processEvents()
        self.assertLessEqual(
            dock.x() + dock.width(), rail.x() - dock.ANCHOR_MARGIN + 1
        )
        # 宿主缩小时面板被夹回宿主范围内
        host.resize(700, 400)
        self.app.processEvents()
        self.assertGreaterEqual(dock.x(), 0)

    def test_reopen_reanchors_to_rail(self):
        dock, rail, _host = self._make_dock()
        dock.open_panel()
        dock.move(120, 80)  # 尝试移走——重开必须回锚点
        dock.close_panel()
        dock.open_panel()
        # 右缘紧贴窄栏左缘（硬连接，不保留自由位置）
        self.assertLessEqual(
            dock.x() + dock.width(), rail.x() - dock.ANCHOR_MARGIN + 1
        )
        self.assertGreaterEqual(dock.x(), 0)

    def test_hide_keep_state(self):
        from utils.config import pcfg

        dock, _rail, _host = self._make_dock()
        dock.open_panel()
        dock.hide_keep_state()
        self.assertFalse(dock.isVisible())
        self.assertTrue(pcfg.test_dock_open)

    def test_set_title(self):
        dock, _rail, _host = self._make_dock()
        dock.set_title("注解 •")
        self.assertEqual(dock._title_label.text(), "注解 •")


class AnnotationLauncherLogicTest(unittest.TestCase):
    """FontFormatPanel cannot be fully constructed offscreen (its ViewWidget
    registration depends on mainwindow-populated ``utils.shared`` state), so
    drive the launcher logic through a ``__new__`` instance like
    ``tests/test_annotation_controls.py::PanelRoutingTest``."""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.text_panel import FontFormatPanel
        from ui.textitem import TextBlkItem

        cls.FontFormatPanel = FontFormatPanel
        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _make_panel(self):
        from ui.panel_rail import RailLauncherButton
        from ui.text_panel import (
            AnnotationFormatGroup,
            EmphasisFormatGroup,
            FormatGroupBtn,
        )

        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        panel.textblk_item = None
        panel.annotation_group = AnnotationFormatGroup()
        panel.emphasis_group = EmphasisFormatGroup()
        panel.textstyle_group = QCheckBox()  # only setEnabled is touched here
        panel.tcyChecker = QCheckBox()
        panel.formatBtnGroup = FormatGroupBtn()
        panel.annotation_launcher = RailLauncherButton("rail_annotation")
        panel.annotation_dock = None
        panel.emphasis_launcher = RailLauncherButton("rail_emphasis")
        panel.emphasis_dock = None
        panel.transform_launcher = RailLauncherButton("rail_transform")
        panel.transform_dock = None
        panel.textstyle_launcher = RailLauncherButton("rail_effects")
        panel.textstyle_dock = None
        return panel

    def test_global_mode_disables_launcher(self):
        panel = self._make_panel()
        panel._sync_annotation_controls()
        self.assertFalse(panel.annotation_launcher.isEnabled())
        self.assertFalse(panel.emphasis_launcher.isEnabled())
        self.assertFalse(panel.annotation_launcher._dot)
        self.assertFalse(panel.emphasis_launcher._dot)

    def test_item_with_annotation_sets_dot(self):
        """强调角标独立：增删强调只影响 emphasis launcher。

        注：注解角标对干净块也可能恒亮（引擎默认 Discretionary 连字为
        'enabled'，与 LIGATURE_DEFAULT 比较视为非默认）——这是既有行为，
        本测试只验证强调角标自身跟随强调的增删。
        """
        panel = self._make_panel()
        item = self.TextBlkItem(blk=_make_blk(), idx=0)
        self.scene.addItem(item)
        panel.textblk_item = item
        panel._sync_annotation_controls()
        self.assertFalse(panel.emphasis_launcher._dot)
        item.setEmphasis("filled dot", "over right")
        panel._sync_annotation_controls()
        self.assertTrue(panel.annotation_launcher.isEnabled())
        self.assertTrue(panel.emphasis_launcher.isEnabled())
        self.assertTrue(panel.emphasis_launcher._dot)
        item.setEmphasis("none", "over right")
        panel._sync_annotation_controls()
        self.assertFalse(panel.emphasis_launcher._dot)

    def test_indicator_without_launcher_installed(self):
        panel = self.FontFormatPanel.__new__(self.FontFormatPanel)
        panel.textblk_item = None
        panel.annotation_launcher = None
        panel.annotation_dock = None
        panel.emphasis_launcher = None
        panel.emphasis_dock = None
        panel.transform_launcher = None
        panel.transform_dock = None
        panel.textstyle_launcher = None
        panel.textstyle_dock = None
        # must not raise before install_*_launcher ran
        panel._update_annotation_indicator()
        panel._update_emphasis_indicator()
        panel._update_textstyle_indicator()
        panel._update_transform_indicator()


if __name__ == "__main__":
    unittest.main(verbosity=2)
