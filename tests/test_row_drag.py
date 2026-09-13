"""Offscreen regression tests for the grab-style row drag rework.

Covers the 2026-09-12 replacement of the native ``QDrag`` reorder in
``ui/textedit_area.py::TextEditListScrollArea`` with a mouse-grab drag:
dragged cards stay rendered in person, stacking into a folded pile that
follows the cursor while a dim mask covers the rest, the remaining rows
shift aside live (midpoint-crossing gap rule, target-based to avoid
feedback with running animations), and the block order only commits on
release via ``rearrange_blks``. Also covers the drag-time hover swallow
(editors must not light up under the pointer).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_row_drag.py
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

from qtpy.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from qtpy.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402

from utils.config import pcfg  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(idx=0):
    blk = TextBlock(xyxy=[100, 100 + idx * 50, 300, 150 + idx * 50])
    blk._bounding_rect = [100, 100 + idx * 50, 300, 150 + idx * 50]
    return blk


def _mouse(widget, etype, local, button=Qt.MouseButton.LeftButton):
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


class RowDragTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._saved_fps = pcfg.animation_fps
        pcfg.animation_fps = -1  # 默认关动画，让位直接就位便于断言

    def tearDown(self):
        if getattr(self, "area", None) is not None:
            self.area.clearDrag()
        pcfg.animation_fps = self._saved_fps

    def _make_area(self, n=4):
        from ui.textedit_area import TextEditListScrollArea, TransPairWidget

        area = TextEditListScrollArea()
        pws = []
        for i in range(n):
            pw = TransPairWidget(_make_blk(i), i)
            pws.append(pw)
        area.pairwidget_list = pws
        for pw in pws:
            area.addPairWidget(pw)
        area.resize(400, 600)
        area.show()
        self.app.processEvents()
        self.app.processEvents()
        area.pairs = pws
        area.emitted = []
        area.rearrange_blks.connect(lambda m: area.emitted.append(m))
        return area

    def _check(self, pw):
        pw._set_checked_state(True)
        self.area.checked_list = [pw]
        self.area.sel_anchor_widget = pw

    def _vlayout_order(self, area):
        items = []
        for i in range(area.vlayout.count()):
            w = area.vlayout.itemAt(i).widget()
            if w is not None:
                items.append(w)
        return items

    # ── 基础拖拽流程 ─────────────────────────────────────────

    def test_begin_pile_visible_and_rest_arranged(self):
        """拖拽开始：被拖组保持真身可见并聚拢到光标（单卡偏移 0），
        rest 行可见，遮罩/指示框就位，初始让位排布与其余行原位一致。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        cursor_y = pw.y() + pw.height() / 2
        area.begin_rows_drag(cursor_y)
        self.assertTrue(area._drag_active)
        self.assertTrue(pw.isVisible())
        self.assertEqual(pw.y(), int(cursor_y))  # 堆顶聚拢到光标位置
        for w in area.pairwidget_list:
            if w is not pw:
                self.assertTrue(w.isVisible())
        self.assertIsNotNone(area._drag_dim)
        self.assertIsNotNone(area._gap_frame)
        self.assertEqual(area._gap_slot, 1)
        # 单卡拖拽：初始让位排布与其余行原位一致
        for w, ty in area._rest_y.items():
            self.assertEqual(w.y(), ty)

    def test_multi_select_gather_to_cursor(self):
        """多选聚拢锚点 = 光标内容坐标：各卡按 PILE_PEEK 阶梯落到光标处。"""
        self.area = area = self._make_area()
        d0, d2 = area.pairwidget_list[0], area.pairwidget_list[2]
        d0._set_checked_state(True)
        d2._set_checked_state(True)
        area.checked_list = [d0, d2]
        area.sel_anchor_widget = d0

        cursor_y = d0.y() + d0.height() / 2
        area.begin_rows_drag(cursor_y)
        top = int(cursor_y)
        self.assertEqual(d0.y(), top)
        self.assertEqual(d2.y(), top + area.PILE_PEEK)
        self.assertTrue(d0.isVisible() and d2.isVisible())

    def test_gap_midpoint_crossing(self):
        """让位判定：光标越过下邻行中点 gap 才下移，越过上邻行中点上移。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        orig_y = pw.y()  # 真身拖拽：卡随光标移动，原槽位需提前捕获
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        below_mid = area._rest_y[area._rest[1]] + h / 2

        # 中点以内：gap 不动
        area._drag_cursor_vp_y = below_mid - 2
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 1)
        # 越过中点：gap 下移一格
        area._drag_cursor_vp_y = below_mid + 2
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 2)
        # 卡 2 上移让位（目标 y = 原 pw1 的槽位）
        card2 = area.pairwidget_list[2]
        self.assertEqual(area._rest_y[card2], orig_y)
        # 再上移越过上一行（原卡 0）中点：gap 回到 0
        above_mid = area._rest_y[area._rest[0]] + area.pairwidget_list[0].height() / 2
        area._drag_cursor_vp_y = above_mid - 2
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 0)

    def test_finish_commits_permutation_and_restores(self):
        """松手落账：经 rearrange_blks 发出置换，行恢复可见且布局按新序。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        area._finish_drag()

        self.assertFalse(area._drag_active)
        self.assertEqual(area.emitted, [([2, 1], [1, 2])])
        self.assertEqual(
            self._vlayout_order(area),
            [area.pairwidget_list[0], area.pairwidget_list[2],
             area.pairwidget_list[1], area.pairwidget_list[3]],
        )
        for w in area.pairwidget_list:
            self.assertTrue(w.isVisible())
        self.assertIsNone(area._drag_dim)
        self.assertIsNone(area._gap_frame)
        # 布局归还后自然排布与松手终态一致（无缝）
        h0 = area.pairwidget_list[0].height()
        s = area.vlayout.spacing()
        self.assertEqual(
            area.pairwidget_list[2].y(),
            area.pairwidget_list[0].y() + h0 + s,
        )

    def test_finish_without_move_emits_nothing(self):
        """拖出去又拖回原位：置换无变化不发信号。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        area._finish_drag()
        self.assertEqual(area.emitted, [])
        self.assertEqual(
            self._vlayout_order(area), list(area.pairwidget_list)
        )

    def test_esc_cancels_and_restores(self):
        """Esc 取消：行回原位、不发信号。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 2)

        ev = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(area.viewport(), ev)

        self.assertFalse(area._drag_active)
        self.assertEqual(area.emitted, [])
        self.assertEqual(
            self._vlayout_order(area), list(area.pairwidget_list)
        )
        for w in area.pairwidget_list:
            self.assertTrue(w.isVisible())

    # ── 多选组拖拽 ───────────────────────────────────────────

    def test_multi_select_group_drag(self):
        """非连续多选拖为折叠堆：gap 高度取堆叠后的堆高（PILE_PEEK +
        末卡高），落点为整组连续插入。"""
        self.area = area = self._make_area()
        d0, d2 = area.pairwidget_list[0], area.pairwidget_list[2]
        d0._set_checked_state(True)
        d2._set_checked_state(True)
        area.checked_list = [d0, d2]
        area.sel_anchor_widget = d0

        area.begin_rows_drag(d0.y() + d0.height() / 2)
        expected_gap_h = area.PILE_PEEK + d2.height()
        self.assertEqual(area._pile_offsets, [0, area.PILE_PEEK])
        self.assertEqual(area._gap_h, expected_gap_h)
        self.assertEqual(area._gap_slot, 0)
        # 卡 1 让位到堆槽之下
        self.assertEqual(
            area._rest_y[area.pairwidget_list[1]],
            area._base_y + expected_gap_h + area._spacing,
        )

        # 拖到最底：组插在卡 1（唯一 rest 行）之后
        area._drag_cursor_vp_y = area._rest_y[area._rest[0]] + area.pairwidget_list[1].height()
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 1)
        area._finish_drag()
        self.assertEqual(area.emitted, [([1, 0], [0, 1])])
        self.assertEqual(
            self._vlayout_order(area),
            [area.pairwidget_list[1], area.pairwidget_list[0],
             area.pairwidget_list[2], area.pairwidget_list[3]],
        )

    # ── 触发路径与守卫 ───────────────────────────────────────

    def test_press_move_triggers_drag(self):
        """按压后移动超阈值触发拖拽（保留原触发管线）。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        _mouse(area.viewport(), QEvent.Type.MouseButtonPress, QPoint(30, pw.y() + 10))
        _mouse(
            area.viewport(),
            QEvent.Type.MouseMove,
            QPoint(30 + QApplication.startDragDistance() + 5, pw.y() + 10),
        )
        self.assertTrue(area._drag_active)
        area._finish_drag()

    def test_guards(self):
        """少于 2 行 / 全选时拖拽不启动。"""
        area = self._make_area(1)
        self.area = area
        self._check(area.pairwidget_list[0])
        area.begin_rows_drag(10)
        self.assertFalse(area._drag_active)

        area2 = self._make_area(3)
        for pw in area2.pairwidget_list:
            pw._set_checked_state(True)
        area2.checked_list = list(area2.pairwidget_list)
        area2.sel_anchor_widget = area2.pairwidget_list[0]
        area2.begin_rows_drag(10)
        self.assertFalse(area2._drag_active)

    def test_clear_drag_cancels_active(self):
        """外部 clearDrag（焦点切走）取消进行中的拖拽。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        self.assertTrue(area._drag_active)
        area.clearDrag()
        self.assertFalse(area._drag_active)
        self.assertEqual(
            self._vlayout_order(area), list(area.pairwidget_list)
        )

    # ── hover 吞噬与动画门控 ─────────────────────────────────

    def test_hover_swallowed_inside_list_only(self):
        """拖拽期间：列表内部目标 hover 被吞，列表外不受影响。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)

        ev = QEvent(QEvent.Type.HoverEnter)
        self.assertTrue(area.eventFilter(pw.e_source, ev))
        self.assertTrue(area.eventFilter(area.pairwidget_list[0], ev))
        outside = QWidget()
        self.assertFalse(area.eventFilter(outside, ev))
        area._cancel_drag()

    def test_animation_disabled_snaps(self):
        """animation_fps < 0：让位无动画直接就位。"""
        self.area = area = self._make_area()  # setUp 已关动画
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        self.assertEqual(area._pos_anims, {})
        self.assertEqual(
            area.pairwidget_list[2].y(), area._rest_y[area.pairwidget_list[2]]
        )
        area._finish_drag()

    def test_animation_enabled_runs(self):
        """animation_fps >= 0：gap 变化触发位移动画，动画结束后到位。"""
        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        # 聚拢补间（目标未变被守卫保留）+ rest 行让位补间
        self.assertEqual(len(area._pos_anims), 2)
        from qtpy.QtTest import QTest

        QTest.qWait(300)
        self.assertEqual(area._pos_anims, {})
        self.assertEqual(
            area.pairwidget_list[2].y(), area._rest_y[area.pairwidget_list[2]]
        )
        area._finish_drag()
        self.assertEqual(area.emitted, [([2, 1], [1, 2])])

    def test_chase_follow_no_teleport(self):
        """追随式跟随：光标移动后拖拽组经补间咬合（不瞬移），
        动画目标更新为光标新位置。"""
        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        cursor_y = pw.y() + pw.height() / 2
        area.begin_rows_drag(cursor_y)
        area._drag_cursor_vp_y = cursor_y
        area._update_drag_frame()  # 目标与聚拢一致：守卫跳过，仍在追
        self.assertIn(pw, area._pos_anims)
        self.assertNotEqual(pw.y(), int(cursor_y))  # 起飞中，未瞬移到位

        y2 = cursor_y + 80
        area._drag_cursor_vp_y = y2
        area._update_drag_frame()
        anim = area._pos_anims[pw]
        self.assertEqual(anim.endValue().y(), int(y2))  # 重定向到新光标位
        area._finish_drag()

    def test_finish_settle_animation(self):
        """松手退应：数据即时落账，行从快照位置（拖拽组仍堆叠在
        光标处）补间飞向布局终态，动画结束后 _pos_anims 清空。"""
        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        area._finish_drag()

        # 数据已落账，几何处于退应途中
        self.assertEqual(area.emitted, [([2, 1], [1, 2])])
        self.assertTrue(area._pos_anims)
        from qtpy.QtTest import QTest

        QTest.qWait(300)
        self.assertEqual(area._pos_anims, {})
        # 终态与布局一致（卡 1 落到卡 0+卡 2 之后的槽位）
        h0 = area.pairwidget_list[0].height()
        s = area.vlayout.spacing()
        self.assertEqual(
            area.pairwidget_list[2].y(),
            area.pairwidget_list[0].y() + h0 + s,
        )

    def test_autoscroll_timer_lifecycle(self):
        """光标贴近视口下缘启动自动滚动定时器，离开后停止。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        vp_h = area.viewport().height()
        area._drag_cursor_vp_y = vp_h - 5
        area._update_drag_frame()
        self.assertIsNotNone(area._auto_timer)
        area._drag_cursor_vp_y = vp_h / 2
        area._update_drag_frame()
        self.assertIsNone(area._auto_timer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
