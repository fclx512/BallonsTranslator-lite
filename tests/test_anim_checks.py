"""演示动画校验器的负向回归：这些检查器必须真的抓得住违例。

钉两件事（都是"改个键名/挪个层级就静默失效"的契约）：

1. **节奏检查读的是有效时间轴**，不是继承来的类属性——``RHYTHM`` 档位场景
   的子类把 ``F_CLICK`` 改坏后必须报错（历史假绿灯：只读类属性会校验到
   ``CheckboxDemoScene`` 的旧时间轴）；顺序 / N_FRAMES / EMPH_SEG 越界都要报。
2. **覆盖层置顶与不被弹层裁剪**——徽标场景的单条文案建在覆盖层之后（天然压在
   其上），``AnimScene.set_state`` 每帧重顶覆盖层修掉；手绘下拉的 clip /
   opacity 残留会把随后画的涟漪/光标裁掉或调暗，``save/restore`` 隔离修掉。
   本测试在"还原旧写法"的对照组上断言检查器报错。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_anim_checks.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
if osp.join(APP_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, osp.join(APP_ROOT, "scripts"))
os.chdir(APP_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from check_anim_rhythm import verify_plan, verify_scene_timeline, verify_timeline


class RhythmCheckTest(unittest.TestCase):
    def test_catches_param_violations(self):
        good = {
            "F_PRESS": 7, "F_CLICK": 8, "F_MOVE_START": 9,
            "F_MOVE_END": 14, "F_FADE": 15, "N_FRAMES": 20,
        }
        self.assertEqual(verify_timeline(good, "Good"), [])

        out_of_order = dict(good, F_CLICK=6)
        self.assertTrue(verify_timeline(out_of_order, "Bad"))

        no_room = dict(good, F_FADE=20, N_FRAMES=20)
        self.assertTrue(verify_timeline(no_room, "Bad"))

        emph_early = dict(good, EMPH_SEG=(5, 12))
        self.assertTrue(verify_timeline(emph_early, "Bad"))

    def test_reads_effective_timeline_of_rhythm_scene(self):
        """RHYTHM 场景的子类覆写 F_* 必须被读到（否则继承旧值假绿）。"""
        from gen_help_anim import PunctSpacingScene

        self.assertEqual(verify_scene_timeline(PunctSpacingScene), [])

        class Broken(PunctSpacingScene):
            F_CLICK = 100   # 本类显式声明，应压过档位算出的 8

        errors = verify_scene_timeline(Broken)
        self.assertTrue(errors, "子类覆写的时间轴未被校验器读到")
        self.assertTrue(any("顺序" in e or "EMPH" in e for e in errors))


class PlanCheckTest(unittest.TestCase):
    def test_catches_bad_plan(self):
        from anim_kit import CursorPlan
        from qtpy.QtCore import QPointF

        good = (CursorPlan(QPointF(0, 0)).go(QPointF(10, 10), arrive=5)
                .press(6).fade(15, frames=3))
        self.assertEqual(verify_plan(good, "Good", 20), [])

        back = (CursorPlan(QPointF(0, 0)).go(QPointF(10, 10), arrive=10)
                .go(QPointF(0, 0), arrive=4))
        self.assertTrue(verify_plan(back, "Back", 20))

        fade_before_press = (CursorPlan(QPointF(0, 0))
                             .go(QPointF(1, 1), arrive=5).press(10).fade(8))
        self.assertTrue(verify_plan(fade_before_press, "Fade", 20))


class OverlayCheckTest(unittest.TestCase):
    """需要真渲染：层序与裁剪只能从实际像素/子控件顺序读出来。"""

    @classmethod
    def setUpClass(cls):
        from anim_kit import init_app
        cls.app = init_app()

    def test_stack_check_catches_covered_overlay(self):
        import anim_kit as K
        from check_anim_cursor import verify_overlay_stack
        from gen_help_anim import SeqBadgeScene

        original = K.AnimScene._raise_overlay
        try:
            K.AnimScene._raise_overlay = lambda self: None
            scene = SeqBadgeScene()
            scene.ensurePolished()
            self.app.processEvents()
            self.assertTrue(
                verify_overlay_stack(scene, "block_index", [0], self.app),
                "覆盖层被文案 label 压住却没被检查器抓到")
        finally:
            K.AnimScene._raise_overlay = original

        scene = SeqBadgeScene()
        scene.ensurePolished()
        self.app.processEvents()
        self.assertEqual(
            verify_overlay_stack(scene, "block_index", [0], self.app), [])

    def test_cursor_paint_catches_leaks(self):
        import anim_kit as K
        from check_anim_cursor import verify_cursor_paint
        from gen_readme_anim import DROPDOWN, F_FAMILY_OPEN, FormatTourScene

        def build():
            scene = FormatTourScene()
            scene._dpr = 2.0
            scene.ensurePolished()
            self.app.processEvents()
            return scene

        frame = F_FAMILY_OPEN + 1
        original = K.AnimScene._paint_overlay_event

        def leaky(self, event):
            p = K.QPainter(self._overlay)
            p.setRenderHint(K.QPainter.RenderHint.Antialiasing)
            self._paint_overlay_content(p)   # 不隔离 clip / opacity
            if self._pose is None:
                return
            if self._pose.ring_k > 0:
                K.paint_click_ring(p, self._pose.ring_point, self._pose.ring_k,
                                   self.accent)
            if self._pose.alpha > 0.01:
                K.paint_cursor(p, self._pose.point, self._pose.alpha,
                               self._pose.pressed, self.fg)

        # 真实现：光标在卡片外仍完整
        scene = build()
        fb = scene.familybox
        off = K.widget_scene_rect(scene, fb).topLeft()
        card = DROPDOWN.card_rect(off, fb.height(), fb.width())
        self.assertEqual(
            verify_cursor_paint(scene, "ft", frame, self.app,
                                [scene.plan.pose(frame).point], clip_rect=card),
            [])

        # 旧写法（残留 opacity）：光标被调暗，检查器必须报
        try:
            K.AnimScene._paint_overlay_event = leaky
            scene = build()
            fb = scene.familybox
            off = K.widget_scene_rect(scene, fb).topLeft()
            card = DROPDOWN.card_rect(off, fb.height(), fb.width())
            self.assertTrue(
                verify_cursor_paint(scene, "ft", frame, self.app,
                                    [scene.plan.pose(frame).point],
                                    clip_rect=card),
                "弹层残留 opacity 调暗光标却没被检查器抓到")
        finally:
            K.AnimScene._paint_overlay_event = original


if __name__ == "__main__":
    unittest.main()
