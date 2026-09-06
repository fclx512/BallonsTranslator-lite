"""Selection and stack orchestration for item-wide text effects.

Port of upstream v1.5.13 ``panel.py`` with the fork scope trim: mask /
texture / image / filter families are out of scope (效果栈移植计划 §六).
Layout follows the transform dock hosting contract: the panel is a
``PanelArea`` passed directly to ``RailDockPanel``.
"""

from typing import Iterator, Optional, Sequence, Tuple, TYPE_CHECKING

from qtpy.QtCore import QSignalBlocker, QTimer, Signal, QSize, Qt
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from utils.fontformat import FontFormat
from utils.text_effects import (
    GlowEffect,
    HollowEffect,
    ShadowEffect,
    StrokeEffect,
    TextEffectStack,
    TextFillEffect,
    effect_structure_key,
)

from ... import shared_widget as SW
from ui.custom_widget import ComboBox, PanelArea
from ui.misc import themed_icon_path
from .cards import (
    EffectNumericControl,
    GlowEffectCard,
    ShadowEffectCard,
    StrokeEffectCard,
    TextFillEffectCard,
    _labeled_effect_editor,
)
from .edit_session import (
    effect_reorder_is_aligned,
    matched_effect_occurrences,
)
from .gradient_editor import InlineLinearGradientEditor

if TYPE_CHECKING:
    from ..item import TextBlkItem


class TextEffectPanel(PanelArea):
    """Own Overall Opacity and typed effect cards."""

    value_commit_requested = Signal(int, str, object)
    value_preview_requested = Signal(int, str, object)
    parameter_preview_requested = Signal(int, str, object)
    parameter_commit_requested = Signal(int, str, object)
    preview_canceled = Signal(int, str)
    add_effect_requested = Signal(str)
    hollow_enabled_requested = Signal(bool)
    remove_effect_requested = Signal(int)
    move_effect_requested = Signal(int, int)
    color_dialog_active_changed = Signal(bool)
    line_spacing_type_requested = Signal(int)

    MAX_CONTENT_HEIGHT = 480

    def __init__(
        self,
        panel_name: str,
        config_name: str,
        config_expand_name: str,
    ) -> None:
        super().__init__(panel_name, config_name, config_expand_name)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.scrollContent.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.setMaximumHeight(self.MAX_CONTENT_HEIGHT)

        self.overall_opacity_control = EffectNumericControl(
            self.tr('Opacity'),
            'overall_opacity',
            100.0,
            0.0,
            1.0,
            '%',
            1.0,
            self.scrollContent,
            decimals=1,
        )
        self.overall_opacity_control.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        overall_opacity_hint = self.tr(
            'Overall opacity of the text and all effects'
        )
        self.overall_opacity_control.label.setToolTip(overall_opacity_hint)
        self.overall_opacity_control.editor.setToolTip(overall_opacity_hint)
        self.overall_opacity_control.commit_requested.connect(
            self._on_overall_commit
        )
        self.overall_opacity_control.value_preview_requested.connect(
            self._on_overall_value_preview
        )
        self.overall_opacity_control.preview_requested.connect(
            self._on_overall_parameter_preview
        )
        self.overall_opacity_control.drag_commit_requested.connect(
            self._on_overall_parameter_commit
        )
        self.overall_opacity_control.preview_canceled.connect(
            self._on_overall_preview_canceled
        )
        self.overall_opacity_control.value_preview_canceled.connect(
            self._on_overall_preview_canceled
        )

        self.hollow_toggle_button = QToolButton(self.scrollContent)
        self.hollow_toggle_button.setObjectName('TextEffectHollowButton')
        self.hollow_toggle_button.setIcon(
            QIcon(themed_icon_path('text-effect-hollow.svg'))
        )
        self.hollow_toggle_button.setIconSize(QSize(16, 16))
        self.hollow_toggle_button.setFixedSize(26, 26)
        self.hollow_toggle_button.setCheckable(True)
        self.hollow_toggle_button.setProperty('mixed', False)
        self.hollow_toggle_button.clicked.connect(
            self._on_hollow_toggled
        )
        self._set_hollow_toggle_state(False)

        self.add_effect_button = QToolButton(self.scrollContent)
        self.add_effect_button.setObjectName('AddTextEffectButton')
        self.add_effect_button.setText(self.tr('Add'))
        self.add_effect_button.setToolTip(self.tr('Add Effect'))
        self.add_effect_button.setAccessibleName(self.tr('Add Effect'))
        self.add_effect_button.setFixedSize(72, 26)
        self.add_effect_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self.add_effect_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        add_menu = QMenu(self.add_effect_button)
        add_menu.setObjectName('TextEffectAddMenu')
        self.add_effect_actions = {}
        for label, effect_type, icon_name in (
            (self.tr('Stroke'), 'stroke', 'text-effect-stroke.svg'),
            (self.tr('Shadow'), 'shadow', 'text-effect-shadow.svg'),
            (self.tr('Glow'), 'glow', 'text-effect-glow.svg'),
            (self.tr('Gradient'), 'gradient', 'text-effect-gradient.svg'),
        ):
            action = add_menu.addAction(QIcon(
                themed_icon_path(icon_name)
            ), label)
            action.setData(effect_type)
            action.triggered.connect(self._on_add_effect_triggered)
            self.add_effect_actions[effect_type] = action
        # 漏挂 setMenu 是纯静默失效（菜单建好但点击无响应）——InstantPopup
        # 按钮必须有这一行（2026-09-03 首版验收教训）
        self.add_effect_button.setMenu(add_menu)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        top_row.addWidget(self.add_effect_button)
        top_row.addWidget(self.hollow_toggle_button)
        top_row.addStretch()
        top_row.addWidget(self.overall_opacity_control)

        # 行距类型：低频项，从旧 ◐ 浮层迁来（浮层宽度容得下英文长文案，
        # 2026-09-06 用户拍板：不放右栏）
        self.line_spacing_type_box = ComboBox(self.scrollContent)
        self.line_spacing_type_box.setObjectName("EffectLineSpacingBox")
        self.line_spacing_type_box.addItem(self.tr("Proportional"), 0)
        self.line_spacing_type_box.addItem(self.tr("Distance"), 1)
        self.line_spacing_type_box.currentIndexChanged.connect(
            self._on_line_spacing_type_changed
        )
        self.line_spacing_row = _labeled_effect_editor(
            self.scrollContent,
            self.tr("Line Spacing Type"),
            self.line_spacing_type_box,
        )

        self.cards_layout = QVBoxLayout()
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.effect_cards = []
        self._effect_types = None
        self._pending_visible_effect_index: Optional[int] = None
        self._reveal_effect_timer = QTimer(self)
        self._reveal_effect_timer.setSingleShot(True)
        self._reveal_effect_timer.timeout.connect(
            self._reveal_pending_effect_card
        )
        self._block_items = ()
        self.base_card_layout = QVBoxLayout()
        self.base_card_layout.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)
        layout.addWidget(self.line_spacing_row)
        layout.addLayout(self.base_card_layout)
        layout.addLayout(self.cards_layout)
        self.setContentLayout(layout)
        self.content_layout = layout
        self.scrollContent.after_resized.connect(self._sync_content_height)
        self._sync_content_height()
        QTimer.singleShot(0, self._sync_content_height)

    def _text_fill_cards(self) -> Iterator[TextFillEffectCard]:
        return (
            card for card in self.effect_cards
            if isinstance(card, TextFillEffectCard)
        )

    def _clear_effect_cards(self) -> None:
        for card in self.effect_cards:
            (
                self.base_card_layout
                if isinstance(card, TextFillEffectCard)
                else self.cards_layout
            ).removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.effect_cards = []

    def _rebuild_effect_cards(
        self,
        effect_keys: Sequence[object],
        seed: Optional[TextEffectStack] = None,
    ) -> None:
        effect_keys = tuple(effect_keys)
        if effect_keys == self._effect_types:
            return
        self._clear_effect_cards()
        self._effect_types = effect_keys
        # The model stays topmost-first, while the panel shows the renderer's
        # bottom-to-top application order.
        for index, effect_key in reversed(tuple(enumerate(effect_keys))):
            effect_type = (
                effect_key[0]
                if isinstance(effect_key, tuple)
                else effect_key
            )
            if effect_type == 'stroke':
                card = StrokeEffectCard(index, self.scrollContent)
            elif effect_type == 'shadow':
                card = ShadowEffectCard(index, self.scrollContent)
            elif effect_type == 'glow':
                card = GlowEffectCard(index, self.scrollContent)
            elif effect_type == 'hollow':
                continue
            elif effect_type == 'text_fill':
                fill_effect = (
                    None if seed is None else seed.effects[index]
                )
                if not isinstance(fill_effect, TextFillEffect):
                    continue
                card = TextFillEffectCard(
                    index, fill_effect.paint.paint_type, self.scrollContent
                )
            else:
                continue
            card.value_commit_requested.connect(
                self.value_commit_requested.emit
            )
            card.value_preview_requested.connect(
                self.value_preview_requested.emit
            )
            card.parameter_preview_requested.connect(
                self.parameter_preview_requested.emit
            )
            card.parameter_commit_requested.connect(
                self.parameter_commit_requested.emit
            )
            card.preview_canceled.connect(self.preview_canceled.emit)
            card.color_dialog_active_changed.connect(
                self.color_dialog_active_changed.emit
            )
            card.move_requested.connect(self._move_visual_effect)
            card.remove_requested.connect(self.remove_effect_requested.emit)
            (
                self.base_card_layout
                if isinstance(card, TextFillEffectCard)
                else self.cards_layout
            ).addWidget(card)
            card.show()
            self.effect_cards.append(card)

    def _move_visual_effect(self, index: int, direction: int) -> None:
        self.move_effect_requested.emit(index, -direction)

    @staticmethod
    def _effect_sequence(stack: TextEffectStack) -> Tuple[object, ...]:
        return tuple(
            effect_structure_key(effect) for effect in stack.effects
        )

    def _set_effect_states(
        self, states: Sequence[TextEffectStack]
    ) -> None:
        states = tuple(states)
        if not states or any(
            not isinstance(state, TextEffectStack) for state in states
        ):
            raise TypeError('effect panel requires TextEffectStack values')

        opacity_values = [state.overall_opacity for state in states]
        common_opacity = (
            opacity_values[0]
            if all(value == opacity_values[0] for value in opacity_values)
            else None
        )
        self.overall_opacity_control.set_model_value(
            common_opacity, opacity_values
        )

        hollow_values = [
            next(
                (
                    effect.enabled
                    for effect in state.effects
                    if isinstance(effect, HollowEffect)
                ),
                False,
            )
            for state in states
        ]
        common_hollow = (
            hollow_values[0]
            if all(value == hollow_values[0] for value in hollow_values)
            else None
        )
        self._set_hollow_toggle_state(common_hollow)

        reference = states[0]
        self.add_effect_button.setEnabled(True)
        self._rebuild_effect_cards(
            self._effect_sequence(reference), reference
        )
        matched = matched_effect_occurrences(states)
        movable_types = (StrokeEffect, ShadowEffect, GlowEffect)
        movable_indices = [
            index
            for index, effect in enumerate(reference.effects)
            if isinstance(effect, movable_types)
        ]
        fill_indices = [
            index
            for index, effect in enumerate(reference.effects)
            if isinstance(effect, TextFillEffect)
        ]
        movable_aligned = (
            effect_reorder_is_aligned(states, movable_indices[0])
            if movable_indices else False
        )
        fill_aligned = (
            effect_reorder_is_aligned(states, fill_indices[0])
            if fill_indices else False
        )
        for card in self.effect_cards:
            # Cards always expose the primary item's exact values. Matching is
            # derived only for fan-out and never creates a synthetic stack.
            value = reference.effects[card.index]
            card_matched = len(states) > 1 and card.index in matched
            card.set_matched(card_matched)
            card.set_value(value)
            if isinstance(card, TextFillEffectCard):
                position = fill_indices.index(card.index)
                reorder_enabled = not card_matched or fill_aligned
                card.set_move_enabled(
                    reorder_enabled and position + 1 < len(fill_indices),
                    reorder_enabled and position > 0,
                )
            else:
                position = movable_indices.index(card.index)
                reorder_enabled = not card_matched or movable_aligned
                card.set_move_enabled(
                    reorder_enabled and position + 1 < len(movable_indices),
                    reorder_enabled and position > 0,
                )
        self._sync_content_height()

    def set_active_format(self, font_format: FontFormat) -> None:
        self._block_items = ()
        self._set_effect_states([font_format.text_effects])

    def set_effect_items(self, items: Sequence["TextBlkItem"]) -> None:
        self._block_items = tuple(items)
        self._set_effect_states(
            [item.blk.fontformat.text_effects for item in items]
        )

    def set_line_spacing_type(self, value: int) -> None:
        with QSignalBlocker(self.line_spacing_type_box):
            index = self.line_spacing_type_box.findData(int(value))
            if index >= 0:
                self.line_spacing_type_box.setCurrentIndex(index)

    def _on_line_spacing_type_changed(self, index: int) -> None:
        if index >= 0:
            self.line_spacing_type_requested.emit(
                int(self.line_spacing_type_box.itemData(index))
            )

    def iter_controls(self) -> Iterator[EffectNumericControl]:
        yield self.overall_opacity_control
        for card in self.effect_cards:
            yield from card.iter_controls()

    def iter_gradient_editors(self) -> Iterator[InlineLinearGradientEditor]:
        for card in self.effect_cards:
            editor = getattr(card, 'gradient_editor', None)
            if isinstance(editor, InlineLinearGradientEditor):
                yield editor

    def finish_pending_effect_edits(self) -> None:
        for control in self.iter_controls():
            control.commit_pending()
        for editor in tuple(self.iter_gradient_editors()):
            editor.commit_pending()

    def cancel_pending_effect_edits(self) -> None:
        for control in self.iter_controls():
            control.cancel_pending()
        for editor in tuple(self.iter_gradient_editors()):
            editor.cancel_pending()

    def cancel_effect_previews(self) -> None:
        for control in self.iter_controls():
            control.cancel_preview()
        for editor in tuple(self.iter_gradient_editors()):
            editor.cancel_pending()

    def _sync_content_height(self) -> None:
        if not hasattr(self, 'content_layout'):
            return
        self._sync_scroll_content_height(self.content_layout)

    def reveal_effect_card(self, index: int) -> None:
        """Scroll a newly inserted effect card into the viewport."""
        self._pending_visible_effect_index = int(index)
        self._reveal_effect_timer.start(0)

    def _reveal_pending_effect_card(self) -> None:
        index = self._pending_visible_effect_index
        self._pending_visible_effect_index = None
        if index is None:
            return
        self._sync_content_height()
        card = next(
            (card for card in self.effect_cards if card.index == index),
            None,
        )
        if card is not None:
            self.ensureWidgetVisible(card, 0, self.cards_layout.spacing())

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if not hasattr(self, 'content_layout'):
            return hint
        hint.setHeight(min(
            self.content_layout.sizeHint().height(),
            self.MAX_CONTENT_HEIGHT,
        ))
        return hint

    def _on_overall_commit(self, name: str, value) -> None:
        self.value_commit_requested.emit(-1, name, value)

    def _on_overall_value_preview(self, name: str, value) -> None:
        self.value_preview_requested.emit(-1, name, value)

    def _on_overall_parameter_preview(self, name: str, delta) -> None:
        self.parameter_preview_requested.emit(-1, name, delta)

    def _on_overall_parameter_commit(self, name: str, delta) -> None:
        self.parameter_commit_requested.emit(-1, name, delta)

    def _on_overall_preview_canceled(self, name: str) -> None:
        self.preview_canceled.emit(-1, name)

    def _on_add_effect_triggered(self, _checked: bool = False) -> None:
        action = self.sender()
        if action is not None and action.data() in {
            'stroke', 'shadow', 'glow', 'gradient',
        }:
            self.add_effect_requested.emit(action.data())

    def _set_hollow_toggle_state(
        self, enabled: Optional[bool]
    ) -> None:
        mixed = enabled is None
        blocker = QSignalBlocker(self.hollow_toggle_button)
        self.hollow_toggle_button.setChecked(enabled is True)
        del blocker
        if self.hollow_toggle_button.property('mixed') != mixed:
            self.hollow_toggle_button.setProperty('mixed', mixed)
            style = self.hollow_toggle_button.style()
            style.unpolish(self.hollow_toggle_button)
            style.polish(self.hollow_toggle_button)
        if mixed:
            description = self.tr('Enable Hollow for All Selected Text')
        elif enabled is True:
            description = self.tr('Disable Hollow')
        else:
            description = self.tr('Enable Hollow')
        self.hollow_toggle_button.setToolTip(description)
        self.hollow_toggle_button.setAccessibleName(description)

    def _on_hollow_toggled(self, enabled: bool) -> None:
        self.hollow_enabled_requested.emit(enabled)
