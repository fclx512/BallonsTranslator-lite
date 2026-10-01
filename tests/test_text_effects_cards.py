"""Card-layer regression tests for the effects panel (2026-09-08 batch).

Pins the batch that retires the main-panel stroke row and restores the
standalone stroke card with progressive disclosure:

- ``StrokeEffectCard`` is built again for a stack holding a Stroke,
- low-frequency parameters (stroke position/opacity/blend, shadow and glow
  opacity/spread/blend, gradient opacity/blend) live inside a collapsed
  「高级」 container while high-frequency parameters stay outside it,
- the disclosure row toggles that container and reports a geometry change,
- the disclosure dot marks non-default advanced values while collapsed,
- ``add_effect('stroke')`` constructs a StrokeEffect again,
- committing a stroke paint marks the block stroke-color-custom so the
  auto-follow inverse cannot overwrite the picked color.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_text_effects_cards.py
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


class EffectsCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.text_engine.effects.panel import TextEffectPanel

        self.panel = TextEffectPanel(
            'Text Effects',
            config_name='show_text_effect_panel',
            config_expand_name='expand_teffect_panel',
        )

    def tearDown(self):
        self.panel.deleteLater()

    def _card_of(self, card_type):
        return next(
            card
            for card in self.panel.effect_cards
            if isinstance(card, card_type)
        )

    def _set_stack(self, stack):
        from utils.fontformat import FontFormat

        fmt = FontFormat()
        fmt.text_effects = stack
        self.panel.set_active_format(fmt)
        return fmt

    def _make_session(self):
        from ui.text_engine.effects.edit_session import TextEffectEditSession
        from utils.fontformat import FontFormat

        host = type('EffectHost', (), {})()
        host.global_format = FontFormat()
        host.update_text_style_label = lambda: None
        return host, TextEffectEditSession(host, self.panel)

    def test_disabling_hollow_removes_its_hidden_stack_entry(self):
        from utils.text_effects import HollowEffect, StrokeEffect, TextEffectStack

        host, session = self._make_session()
        stroke = StrokeEffect(width=0.2)
        host.global_format.text_effects = TextEffectStack(effects=(stroke,))
        self.assertTrue(session.set_hollow_enabled(True))
        self.assertEqual(len(host.global_format.text_effects.effects), 2)
        self.assertTrue(session.set_hollow_enabled(False))
        self.assertEqual(host.global_format.text_effects.effects, (stroke,))
        # Legacy disabled entries must also disappear when switched off.
        host.global_format.text_effects = TextEffectStack(effects=(HollowEffect(enabled=False), stroke))
        self.assertTrue(session.set_hollow_enabled(False))
        self.assertEqual(host.global_format.text_effects.effects, (stroke,))
        self.assertFalse(session.set_hollow_enabled(False))

    def test_stroke_card_returns_with_collapsed_advanced(self):
        from ui.text_engine.effects.cards import StrokeEffectCard
        from utils.text_effects import StrokeEffect, TextEffectStack

        self._set_stack(TextEffectStack(effects=(StrokeEffect(width=0.2),)))
        card = self._card_of(StrokeEffectCard)
        advanced = card._advanced_container
        # 宽度/位置/颜色常显（位置与宽度同行是上游布局），不透明度/混合收进「高级」
        self.assertFalse(advanced.isAncestorOf(card.width_control))
        self.assertFalse(advanced.isAncestorOf(card.position_selector))
        self.assertFalse(advanced.isAncestorOf(card.paint_button))
        self.assertTrue(advanced.isAncestorOf(card.opacity_control))
        self.assertTrue(advanced.isAncestorOf(card.blend_selector))
        self.assertTrue(advanced.isHidden())

    def test_shadow_card_keeps_geometry_visible(self):
        from ui.text_engine.effects.cards import ShadowEffectCard
        from utils.text_effects import ShadowEffect, TextEffectStack

        self._set_stack(
            TextEffectStack(effects=(ShadowEffect(),))
        )
        card = self._card_of(ShadowEffectCard)
        advanced = card._advanced_container
        for visible in (
            card.type_selector,
            card.angle_control,
            card.distance_control,
            card.blur_control,
            card.paint_button,
        ):
            self.assertFalse(advanced.isAncestorOf(visible))
        for collapsed in (
            card.opacity_control,
            card.spread_control,
            card.blend_selector,
        ):
            self.assertTrue(advanced.isAncestorOf(collapsed))

    def test_advanced_toggle_reveals_container_and_signals(self):
        from utils.text_effects import StrokeEffect, TextEffectStack

        self._set_stack(TextEffectStack(effects=(StrokeEffect(width=0.2),)))
        card = self.panel.effect_cards[0]
        seen = []
        card.geometry_changed.connect(lambda: seen.append(True))
        card.advanced_disclosure._expanded = True
        card.advanced_disclosure.toggled.emit(True)
        self.assertFalse(card._advanced_container.isHidden())
        self.assertEqual(seen, [True])

    def test_advanced_dot_marks_non_default_values(self):
        from utils.text_effects import StrokeEffect, TextEffectStack

        self._set_stack(TextEffectStack(effects=(StrokeEffect(width=0.2),)))
        card = self.panel.effect_cards[0]
        self.assertTrue(card.advanced_disclosure._modified_dot.isHidden())
        self._set_stack(
            TextEffectStack(effects=(StrokeEffect(width=0.2, opacity=0.5),))
        )
        self.assertFalse(card.advanced_disclosure._modified_dot.isHidden())

    def test_add_stroke_effect_constructs_stroke(self):
        from utils.text_effects import StrokeEffect

        host, session = self._make_session()
        self.assertTrue(session.add_effect('stroke'))
        self.assertTrue(
            any(
                isinstance(effect, StrokeEffect)
                for effect in host.global_format.text_effects.effects
            )
        )

    def test_stroke_paint_commit_marks_color_manual(self):
        from utils.text_effects import SolidPaint, StrokeEffect

        host, session = self._make_session()
        session.add_effect('stroke')
        # 添加描边时即按当前文字色取一次反色并标为手动（2026-09-08 二轮拍板）
        self.assertTrue(host.global_format.stroke_color_custom)
        stack = host.global_format.text_effects
        index = next(
            position
            for position, effect in enumerate(stack.effects)
            if isinstance(effect, StrokeEffect)
        )
        self.assertTrue(
            session.commit_value(index, 'paint', SolidPaint((255, 0, 0)))
        )
        self.assertTrue(host.global_format.stroke_color_custom)
        stroke = next(
            effect
            for effect in host.global_format.text_effects.effects
            if isinstance(effect, StrokeEffect)
        )
        self.assertEqual(tuple(stroke.paint.color), (255, 0, 0))

    def test_fill_row_selector_not_overlapped_by_swatch(self):
        """Ignored 策略下布局把下拉算成 0 宽，色块排在它头上（2026-09-08 实机）。"""
        from utils.text_effects import GlowEffect, SolidPaint, TextEffectStack

        self._set_stack(
            TextEffectStack(effects=(GlowEffect(paint=SolidPaint((255, 255, 255))),))
        )
        card = self.panel.effect_cards[0]
        card.grab()  # 触发布局
        selector = card.fill_type_selector
        swatch = card.paint_button
        self.assertIs(selector.parent(), swatch.parent())
        self.assertGreaterEqual(selector.geometry().width(), 72)
        self.assertGreaterEqual(
            swatch.geometry().left(), selector.geometry().right() + 1
        )

    def test_bottom_border_combo_hides_native_arrow(self):
        """自绘 chevron 的选择器要带 bottomBorderSelector，否则双箭头。"""
        from ui.custom_widget.combobox import BottomBorderComboBox

        selector = BottomBorderComboBox()
        self.assertTrue(selector.property('bottomBorderSelector'))

    def test_gradient_stop_color_preview_replaces_selected_stop(self):
        """取色器实时预览走 _replaced_selected_stop（移植时漏掉该方法）。"""
        from qtpy.QtGui import QColor

        from ui.text_engine.effects.gradient_editor import (
            InlineLinearGradientEditor,
        )
        from utils.text_effects import GradientStop, LinearGradientPaint

        editor = InlineLinearGradientEditor(
            LinearGradientPaint(
                stops=(
                    GradientStop(0.0, (255, 0, 0)),
                    GradientStop(1.0, (0, 0, 255)),
                )
            )
        )
        editor._on_stop_color_preview(QColor(0, 255, 0))
        self.assertEqual(editor.paint.stops[0].color, (0, 255, 0))
        self.assertEqual(editor.paint.stops[1].color, (0, 0, 255))

    def test_gradient_stop_opacity_comes_from_picker_alpha(self):
        """停点不透明度没有独立数值框，改由取色器 alpha 通道承担。"""
        from qtpy.QtGui import QColor

        from ui.text_engine.effects.gradient_editor import (
            InlineLinearGradientEditor,
        )
        from utils.text_effects import GradientStop, LinearGradientPaint

        editor = InlineLinearGradientEditor(
            LinearGradientPaint(
                stops=(
                    GradientStop(0.0, (255, 0, 0)),
                    GradientStop(1.0, (0, 0, 255)),
                )
            )
        )
        editor._on_stop_color_preview(QColor(0, 255, 0, 128))
        stop = editor.paint.stops[0]
        self.assertEqual(stop.color, (0, 255, 0))
        self.assertAlmostEqual(stop.opacity, 128 / 255, places=3)

    def test_effect_panel_header_has_no_hide_button(self):
        """文本效果区折叠栏不再带（无实际行为的）隐藏面板按钮。"""
        self.assertIsNone(self.panel.view_widget.title_label.hidelabel)

    def test_add_menu_creates_stroke(self):
        """Add → 描边 必须真的发请求（此前 allow-list 漏了 stroke）。"""
        seen = []
        self.panel.add_effect_requested.connect(seen.append)
        self.panel.add_effect_actions['stroke'].trigger()
        self.assertEqual(seen, ['stroke'])

    def test_legacy_writes_do_not_create_phantom_stroke(self):
        """自动反色写 srgb / 写 0 宽都不该凭空建描边卡。"""
        from utils.fontformat import FontFormat
        from utils.text_effects import TextEffectStack, primary_stroke

        fmt = FontFormat()
        fmt.text_effects = TextEffectStack()
        fmt.srgb = [255, 255, 255]
        self.assertIsNone(primary_stroke(fmt.text_effects))
        fmt.stroke_width = 0.0
        self.assertIsNone(primary_stroke(fmt.text_effects))
        fmt.stroke_width = 0.2
        stroke = primary_stroke(fmt.text_effects)
        self.assertIsNotNone(stroke)
        self.assertEqual(stroke.width, 0.2)
        fmt.srgb = [10, 20, 30]
        self.assertEqual(
            tuple(primary_stroke(fmt.text_effects).paint.color), (10, 20, 30)
        )

    def test_editor_box_drag_previews_then_commits_once(self):
        """数值框横向拖拽：拖动中实时预览、松手提交一次（Blender 式）。"""
        from qtpy.QtCore import QPoint, Qt
        from qtpy.QtTest import QTest

        from utils.text_effects import StrokeEffect, TextEffectStack

        self._set_stack(TextEffectStack(effects=(StrokeEffect(width=0.2),)))
        control = self.panel.effect_cards[0].width_control
        self.assertFalse(control.label.drag_enabled)
        previews, commits = [], []
        control.preview_requested.connect(
            lambda _name, delta: previews.append(delta)
        )
        control.drag_commit_requested.connect(
            lambda _name, delta: commits.append(delta)
        )
        editor = control.editor
        QTest.mousePress(
            editor, Qt.MouseButton.LeftButton, pos=QPoint(10, 10)
        )
        QTest.mouseMove(editor, QPoint(70, 10))
        QTest.mouseMove(editor, QPoint(120, 10))
        self.assertEqual(previews[-1], 0.5)
        self.assertEqual(editor.text(), '0.70')
        QTest.mouseRelease(
            editor, Qt.MouseButton.LeftButton, pos=QPoint(120, 10)
        )
        self.assertEqual(commits, [0.5])

    def test_unitless_effect_params_capped_at_one(self):
        """无单位（非 %/px/°）的效果参数上限统一为 1。"""
        from ui.text_engine.effects.filters import get_filter_registry
        from utils.text_effects import (
            EFFECT_MAGNITUDE_LIMIT,
            GlowEffect,
            ShadowEffect,
            StrokeEffect,
            TextEffectStack,
        )

        exempt = {'%', ' px', '°'}
        for effect in (StrokeEffect(width=0.2), ShadowEffect(), GlowEffect()):
            fmt = self._set_stack(TextEffectStack(effects=(effect,)))
            self.assertIsNotNone(fmt)
            for control in self.panel.effect_cards[0].iter_controls():
                if control.suffix in exempt:
                    continue
                self.assertLessEqual(
                    control.canonical_maximum,
                    EFFECT_MAGNITUDE_LIMIT,
                    f'{control.param_name} 上限超过 1',
                )
        for spec in get_filter_registry().specs:
            for parameter in spec.params:
                if parameter.kind != 'float' or parameter.suffix in exempt:
                    continue
                self.assertLessEqual(
                    parameter.maximum,
                    EFFECT_MAGNITUDE_LIMIT,
                    f'{spec.filter_id}:{parameter.key} 上限超过 1',
                )

    def test_clear_effect_cards_hides_before_detaching(self):
        """清理卡片必须先 hide() 再 setParent(None)。

        卡片处于「已 show」状态（``isHidden()`` 为 False）时脱离父级会变成
        顶层窗口，Qt 随后把它当独立窗口显示出来——画布框选时每次重建都会
        闪出一排秒关的原生小窗（2026-09-08 实机复现）。
        """
        from utils.text_effects import StrokeEffect, TextEffectStack

        self._set_stack(TextEffectStack(effects=(StrokeEffect(width=0.2),)))
        card = self.panel.effect_cards[0]
        self.assertFalse(card.isHidden())

        hidden_at_detach = []
        original_set_parent = card.setParent

        def probe_set_parent(parent, *args):
            if parent is None:
                hidden_at_detach.append(card.isHidden())
            return original_set_parent(parent, *args)

        card.setParent = probe_set_parent
        self.panel._clear_effect_cards()
        self.assertEqual(
            hidden_at_detach,
            [True],
            'setParent(None) 之前卡片必须已 hide()，否则会闪成原生小窗',
        )


if __name__ == '__main__':
    unittest.main()
