"""Offscreen regression tests for the grab-style row drag rework.

Covers the 2026-09-12 replacement of the native ``QDrag`` reorder in
``ui/textedit_area.py::TextEditListScrollArea`` with a mouse-grab drag:
dragged cards stay rendered in person, stacking into a folded pile that
follows the cursor while a dim mask covers the rest, the remaining rows
shift aside live (midpoint-crossing gap rule, target-based to avoid
feedback with running animations), and the block order only commits on
release via ``rearrange_blks``. Also covers the drag-time hover swallow
(editors must not light up under the pointer) and the 2026-09-14
drag-time lift of the pile to the window layer (escape the parent-bound
clip so the grab-scale effect can overflow freely, focus restored on
docking; skipped entirely when animations are disabled), the high-DPI
anchor of the grab scale (device-pixel snapshot drawn through a
logical-coordinate painter: mixing the two shifted the card in
proportion to its screen position, invisible at DPR 1), and the bare
wrapper PyQt resurrects when that effect's Python instance is gone while
its C++ half still sits on a card (state read in the virtual used to
raise AttributeError, which PyQt answers with qFatal — the full suite
aborted with exit 127).

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

from qtpy.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from qtpy.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
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
        """拖拽开始：被拖组保持真身可见并聚拢到光标（单卡纵向居中于
        光标），rest 行可见，遮罩/指示框就位，初始让位排布与其余行原位一致。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        cursor_y = pw.y() + pw.height() / 2
        area.begin_rows_drag(cursor_y)
        self.assertTrue(area._drag_active)
        self.assertTrue(pw.isVisible())
        # 居中锚：卡片中心落在光标处（旧行为是堆顶贴光标）
        self.assertEqual(pw.y(), int(cursor_y) - pw.height() // 2)
        self.assertEqual(pw.y() + pw.height() // 2, int(cursor_y))
        self.assertEqual(area._pile_grab_dy, -(pw.height() // 2))
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
        """多选聚拢锚点 = 光标内容坐标（堆顶卡纵向居中于光标）：各卡按
        PILE_PEEK 阶梯落在堆顶之下。"""
        self.area = area = self._make_area()
        d0, d2 = area.pairwidget_list[0], area.pairwidget_list[2]
        d0._set_checked_state(True)
        d2._set_checked_state(True)
        area.checked_list = [d0, d2]
        area.sel_anchor_widget = d0

        cursor_y = d0.y() + d0.height() / 2
        area.begin_rows_drag(cursor_y)
        top = int(cursor_y) - d0.height() // 2
        self.assertEqual(d0.y(), top)
        self.assertEqual(d0.y() + d0.height() // 2, int(cursor_y))
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

    def test_app_deactivate_cancels_and_restores(self):
        """应用失活（截图浮层等外部窗口接管鼠标）：拖拽取消还原，不落账。

        主路径走 applicationStateChanged 信号——Windows 上 app 级过滤器
        收 ApplicationDeactivate 事件不可靠（2026-08-18 教训）；过滤器
        分支保留为兜底。"""
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()
        self.assertEqual(area._gap_slot, 2)

        self.app.applicationStateChanged.emit(
            Qt.ApplicationState.ApplicationInactive
        )

        self.assertFalse(area._drag_active)
        self.assertEqual(area.emitted, [])
        self.assertEqual(self._vlayout_order(area), list(area.pairwidget_list))

        # 兜底路径：过滤器直收 ApplicationDeactivate 也能取消
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        area.eventFilter(self.app, QEvent(QEvent.Type.ApplicationDeactivate))
        self.assertFalse(area._drag_active)
        self.assertEqual(area.emitted, [])

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
        # 聚拢补间（目标未变被守卫保留）+ rest 行让位补间 + 指示框滑动补间
        self.assertEqual(len(area._pos_anims), 3)
        self.assertIn(area._gap_frame, area._pos_anims)
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
        # 光标偏离卡心 40px：聚拢需要真正位移（否则卡已在居中锚点原地不动）
        cursor_y = pw.y() + pw.height() / 2 + 40
        area.begin_rows_drag(cursor_y)
        area._drag_cursor_vp_y = cursor_y
        area._update_drag_frame()  # 目标与聚拢一致：守卫跳过，仍在追
        self.assertIn(pw, area._pos_anims)
        # 起飞中，未瞬移到位（目标 = 光标上方半个卡高）
        self.assertNotEqual(pw.y(), int(cursor_y) + area._pile_grab_dy)

        y2 = cursor_y + 80
        area._drag_cursor_vp_y = y2
        area._update_drag_frame()
        anim = area._pos_anims[pw]
        # 重定向到新光标位（提层后动画目标为窗口坐标，= 内容 y 减居中偏移
        # 再加 scrollContent 原点在窗口里的偏移）
        self.assertEqual(
            anim.endValue().y(),
            area._pile_org_in_parent().y() + int(y2) + area._pile_grab_dy,
        )
        area._finish_drag()

    def test_lift_docks_back_and_restores_focus(self):
        """提层（抓住缩放的防裁切配套）：动画开启时被拖组挂到窗口层，
        收尾放回 scrollContent 且登记清空；提层挤掉的卡内输入框焦点
        在落账后恢复。"""
        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        pw.e_trans.setFocus()
        self.app.processEvents()
        self.assertTrue(pw.e_trans.hasFocus())

        area.begin_rows_drag(pw.y() + pw.height() / 2)
        self.assertIs(area._pile_parent, area.window())
        self.assertIs(pw.parentWidget(), area.window())
        self.assertFalse(pw.e_trans.hasFocus())  # reparent 挤掉焦点

        area._finish_drag()
        self.app.processEvents()
        self.assertIs(pw.parentWidget(), area.scrollContent)
        self.assertIsNone(area._pile_parent)
        self.assertTrue(pw.e_trans.hasFocus())  # 焦点物归原主
        from qtpy.QtTest import QTest

        QTest.qWait(300)  # 还原缩放动画结束才摘效果
        self.assertEqual(area._scale_effects, {})

    def test_lift_off_when_animation_disabled(self):
        """关动画（animation_fps < 0）：不缩放也不提层，行为同旧版。"""
        pcfg.animation_fps = -1
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        self.assertIsNone(area._pile_parent)
        self.assertIs(pw.parentWidget(), area.scrollContent)
        self.assertIsNone(pw.graphicsEffect())
        area._cancel_drag()

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

    def test_gap_frame_slides_instead_of_jumping(self):
        """落点指示框跨行时走与行同一条补间（跟着滑过去），不是瞬移到
        目标槽位而行还在半路。"""
        from qtpy.QtTest import QTest

        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        frame = area._gap_frame
        y0 = frame.y()
        h = area.pairwidget_list[2].height()
        area._drag_cursor_vp_y = area._rest_y[area._rest[1]] + h / 2 + 4
        area._update_drag_frame()  # 光标越过邻行中点 → gap 下移一格

        _, gap_top = area._arrange_targets()
        self.assertNotEqual(y0, gap_top)
        self.assertEqual(area._pos_anims[frame].endValue().y(), gap_top)
        self.assertEqual(frame.y(), y0)  # 才起步，没瞬移到目标
        QTest.qWait(300)
        self.assertEqual(frame.y(), gap_top)

    def test_chase_settle_durations_and_stagger(self):
        """跟手时长比落位短；多选堆叠逐卡落后；抓取按下-弹起、松手还原
        比行落位快。"""
        pcfg.animation_fps = 60
        self.area = area = self._make_area()
        d0, d2 = area.pairwidget_list[0], area.pairwidget_list[2]
        for w in (d0, d2):
            w._set_checked_state(True)
        area.checked_list = [d0, d2]
        area.sel_anchor_widget = d0

        # 光标偏离首卡卡心 60px：聚拢需要真正位移，各卡才有着跟手补间
        area.begin_rows_drag(d0.y() + d0.height() / 2 + 60)
        self.assertLess(area.CHASE_MS, area.SETTLE_MS)  # 跟手更跟得住
        self.assertEqual(area._pos_anims[d0].duration(), area.CHASE_MS)
        self.assertEqual(
            area._pos_anims[d2].duration(), area.CHASE_MS + area.STAGGER_MS
        )
        eff = area._scale_effects[d0]
        self.assertEqual(eff._anim.duration(), area.SCALE_IN_MS)
        self.assertAlmostEqual(
            eff._anim.keyValueAt(area.SCALE_PRESS_AT), area.SCALE_PRESS, places=6
        )

        area._finish_drag()
        self.assertEqual(eff._anim.duration(), area.SCALE_OUT_MS)  # 还原更快
        self.assertLess(area.SCALE_OUT_MS, area.SETTLE_MS)
        # 展开逐卡落后：堆顶（rank 0）用落位时长，其后每张 +STAGGER_MS
        # （堆顶可能原地落槽、无动画，故只强断言 rank 1）
        self.assertEqual(
            area._pos_anims[d2].duration(), area.SETTLE_MS + area.STAGGER_MS
        )
        if d0 in area._pos_anims:
            self.assertEqual(area._pos_anims[d0].duration(), area.SETTLE_MS)

    def test_autoscroll_ramps_and_cools_down(self):
        """自动滚动：贴边启动定时器并给出目标速度，实际速度逐 tick 渐变
        （不猛启）。离开边缘后目标归零，速度渐降到位才停表。"""
        from qtpy.QtTest import QTest

        self.area = area = self._make_area()
        pw = area.pairwidget_list[1]
        self._check(pw)
        area.begin_rows_drag(pw.y() + pw.height() / 2)
        vp_h = area.viewport().height()
        area._drag_cursor_vp_y = vp_h - 5
        area._update_drag_frame()
        self.assertIsNotNone(area._auto_timer)
        self.assertGreater(area._auto_target, 0)
        area._auto_scroll_tick()  # 首个 tick 只爬升一段，尚未到目标速度
        self.assertLess(area._auto_speed, area._auto_target)

        area._drag_cursor_vp_y = vp_h / 2
        area._update_drag_frame()
        self.assertEqual(area._auto_target, 0.0)
        # 目标归零后不是立刻停：速度渐降，随后自行停表
        QTest.qWait(300)
        self.assertIsNone(area._auto_timer)
        self.assertEqual(area._auto_speed, 0.0)


# 缩放锚点探针：卡片几何 + 顶中红角标（锚点）/ 底中绿角标（倍率基准）
_SCALE_CARD = (100, 300, 100, 60)
_SCALE_MARK = 6
# 探针容器尺寸（逻辑像素，渲染时按 DPR 放大成设备像素）
_SCALE_CANVAS = (400, 600)


class _ScaleProbeCard(QWidget):
    """带对角标的探针卡（角标质心即锚点与底部基准）。"""

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.rect()
        m = _SCALE_MARK
        p.fillRect(r, QColor(30, 30, 30))
        p.fillRect(QRectF(r.width() / 2 - m / 2, 0, m, m), QColor(255, 0, 0))
        p.fillRect(QRectF(r.width() / 2 - m / 2, r.height() - m, m, m), QColor(0, 255, 0))


def _centroids(img, dpr):
    """图里两个角标的逻辑坐标质心 (顶中, 底中)；角标被裁掉则对应项为 None。"""

    def centroid(pred):
        xs, ys = [], []
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if pred(c):
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return (sum(xs) / len(xs) / dpr, sum(ys) / len(ys) / dpr)

    top = centroid(lambda c: c.red() > 150 and c.green() < 100 and c.blue() < 100)
    bottom = centroid(lambda c: c.green() > 150 and c.red() < 100 and c.blue() < 100)
    return top, bottom


class CardScaleAnchorTest(unittest.TestCase):
    """抓住缩放的锚点换算（2026-09-14 高 DPI 回归）。

    快照走 DeviceCoordinates（高 DPI 放大不糊），而 resetTransform 之后
    painter 与 sourcePixmap 的 offset 都回到逻辑坐标：把 deviceTransform
    映射出的设备像素锚点直接交给 painter，锚点会被再放大 DPR 倍，卡片按其
    在屏上的位置成比例偏移（越靠下偏得越多，顶行还被裁掉）。DPR=1 的机器
    上该错误恒等——当初在 100% 缩放的 Win10 上就是这么测过的，故用带 DPR
    的 QImage 离屏直渲复现（离屏屏幕本身是 DPR 1，直接 grab 看不出来）。
    """

    @classmethod
    def setUpClass(cls):
        from ui.textedit_area import _CardScaleEffect

        cls.app = QApplication.instance() or QApplication([])
        cls.cont = QWidget()
        cls.cont.resize(*_SCALE_CANVAS)
        cls.cont.setStyleSheet("background: #000000;")
        cls.card = _ScaleProbeCard(cls.cont)
        cls.card.setGeometry(*_SCALE_CARD)
        cls.eff = _CardScaleEffect(1.1)
        cls.card.setGraphicsEffect(cls.eff)

    @classmethod
    def tearDownClass(cls):
        # 先摘效果再放手：挂着图形效果的控件在 GC 回收时会硬崩（本套件
        # 已踩过的坑），故不留悬挂效果给后续用例回收
        cls.card.setGraphicsEffect(None)
        cls.cont.close()
        cls.cont = cls.card = cls.eff = None

    def _measure(self, dpr):
        """把探针卡渲进 *dpr* 倍的 QImage，返回两角标的逻辑坐标质心
        （角标被裁掉则返回 None）。"""
        img = QImage(
            round(_SCALE_CANVAS[0] * dpr),
            round(_SCALE_CANVAS[1] * dpr),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(0, 0, 0))
        self.cont.render(img)
        return _centroids(img, dpr)

    def test_anchor_holds_and_scale_applies_across_dpr(self):
        """DPR=1 / 1.25 / 1.5 / 2 下顶中锚点都不动，底中按 1.1 倍下移。"""
        self.eff.factor = 1.1
        cx = _SCALE_CARD[0] + _SCALE_CARD[2] / 2.0            # 顶中锚 x = 150
        cy = float(_SCALE_CARD[1])                            # 顶中锚 y = 300
        mark_cy = cy + _SCALE_MARK / 2.0                      # 顶中角标质心 y
        bottom_cy = cy + _SCALE_CARD[3] - _SCALE_MARK / 2.0   # 未缩放底中角标质心
        for dpr in (1.0, 1.25, 1.5, 2.0):
            with self.subTest(dpr=dpr):
                top, bottom = self._measure(dpr)
                # 顶中角标（= 锚点本身）必须还在原地：偏移会被裁掉而整个消失
                self.assertIsNotNone(top, f"dpr={dpr}: 顶中锚点漂移，角标被裁")
                self.assertIsNotNone(bottom, f"dpr={dpr}: 底部角标被裁")
                self.assertAlmostEqual(top[0], cx, delta=1.5, msg=f"dpr={dpr} 锚点 x 漂移")
                self.assertAlmostEqual(top[1], mark_cy, delta=1.5, msg=f"dpr={dpr} 锚点 y 漂移")
                # 底部角标 = 锚点 + 1.1 倍原距（倍率与锚点同时对）
                self.assertAlmostEqual(
                    bottom[1], cy + (bottom_cy - cy) * 1.1, delta=1.5, msg=f"dpr={dpr} 倍率不对"
                )
                self.assertAlmostEqual(bottom[0], cx, delta=1.5, msg=f"dpr={dpr} 卡片横向错位")

    def test_scale_below_one_shrinks_toward_anchor(self):
        """按下段（factor < 1）同样走缩放路径：锚点不动、底中向锚点收拢
        （旧实现 factor<=1 直接画原图，这一路是抓取"按下-弹起"的前提）。
        取 0.9 而非 SCALE_PRESS，让"是否真的缩了"有像素级余量。"""
        self.eff.factor = 0.9
        top, bottom = self._measure(1.0)
        self.assertIsNotNone(top, "按下态没画出卡片")
        self.assertIsNotNone(bottom, "按下态底部角标被裁")
        cy = float(_SCALE_CARD[1])
        base_bottom = cy + _SCALE_CARD[3] - _SCALE_MARK / 2.0
        self.assertAlmostEqual(top[1], cy + _SCALE_MARK / 2.0, delta=1.5)
        self.assertAlmostEqual(
            bottom[1], cy + (base_bottom - cy) * 0.9, delta=1.5, msg="按下态未收拢"
        )

    def test_bare_instance_after_resurrection_is_safe(self):
        """空壳实例（PyQt 复活路径）上虚拟方法不得抛异常。

        setGraphicsEffect 之后效果归 Qt 所有，Python 实例状态可以先没掉而
        C++ 对象仍装在被拖卡上——此后任何一次重绘都会让 PyQt 用「没走过
        __init__ 的空壳」重建包装器。此处用「真 C++ 对象 + 清空 __dict__」
        复刻该状态（套件里触发它依赖前序用例堆出的状态，不可稳定复现，但
        空壳读属性的行为一致）。曾经 boundingRectFor 读 _max_factor 直接
        AttributeError——虚拟回调里抛异常 PyQt 就 qFatal 硬崩，正是 2026-09-14
        全量套件 exit 127 的真因。空壳态须退化为不缩放、画原图。
        """
        from ui.textedit_area import _CardScaleEffect

        cont = QWidget()
        cont.resize(*_SCALE_CANVAS)
        cont.setStyleSheet("background: #000000;")
        card = _ScaleProbeCard(cont)
        card.setGeometry(*_SCALE_CARD)
        eff = _CardScaleEffect(1.1)
        card.setGraphicsEffect(eff)
        eff.factor = 1.1
        eff.__dict__.clear()  # 空壳：C++ 对象还活着，Python 侧状态没了

        src = QRectF(0, 0, _SCALE_CARD[2], _SCALE_CARD[3])
        self.assertEqual(eff.factor, 1.0, "空壳态应退化到不缩放")
        self.assertEqual(eff.boundingRectFor(src), src, "空壳态不得外扩绘制边界")
        img = QImage(*_SCALE_CANVAS, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0))
        painter = QPainter(img)
        eff.draw(painter)  # factor<=1 走 drawSource 分支，不得抛
        painter.end()
        card.setGraphicsEffect(None)  # 收尾摘除，别留给后续用例回收
        cont.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
