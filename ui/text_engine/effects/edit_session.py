"""Selection/global preview and undo boundaries for text effects.

Port of upstream v1.5.13 ``edit_session.py`` with the fork scope trim:
the filter / image / texture / AI-generation families are out of scope
(效果栈移植计划 §六), so only stroke / shadow / glow / gradient fill /
hollow / overall opacity survive here.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from utils import config as C
from utils.logger import logger as LOGGER
from utils.text_effects import (
    EffectPaint,
    GeneratedEffectPaint,
    GlowEffect,
    GradientStop,
    HollowEffect,
    LinearGradientPaint,
    ShadowEffect,
    SolidPaint,
    StrokeEffect,
    TextFillEffect,
    TextEffect,
    TextEffectStack,
    effect_structure_key,
    effect_paint_fallback_color,
    without_project_raster_effects,
)

from ... import shared_widget as SW
from ..editing.commands import SetTextEffectStackCommand

if TYPE_CHECKING:
    from .panel import TextEffectPanel
    from ui.text_panel import FontFormatPanel
    from ..item import TextBlkItem


OVERALL_OPACITY_INDEX = -1


def matched_effect_occurrences(
    states: Sequence[TextEffectStack],
) -> Dict[int, Tuple[int, ...]]:
    """Map primary card indices to same-structure occurrences on every item.

    Occurrences pair in panel-visible order. Relative order among unrelated
    effect types is deliberately irrelevant.

    >>> first = TextEffectStack(effects=(StrokeEffect(), ShadowEffect()))
    >>> second = TextEffectStack(effects=(ShadowEffect(), StrokeEffect()))
    >>> matched_effect_occurrences((first, second))
    {1: (1, 0), 0: (0, 1)}
    """
    values = tuple(states)
    if len(values) < 2:
        return {}
    candidates: Dict[object, List[int]] = {}
    for index in range(len(values[0].effects) - 1, -1, -1):
        effect = values[0].effects[index]
        if isinstance(effect, HollowEffect):
            continue
        candidates.setdefault(effect_structure_key(effect), []).append(index)
    if not candidates:
        return {}

    matches = {
        index: [index]
        for indices in candidates.values()
        for index in indices
    }
    for state in values[1:]:
        available: Dict[object, List[int]] = {}
        for index in range(len(state.effects) - 1, -1, -1):
            effect = state.effects[index]
            key = effect_structure_key(effect)
            if key in candidates and not isinstance(effect, HollowEffect):
                available.setdefault(key, []).append(index)
        next_candidates = {}
        for key, primary_indices in candidates.items():
            target_indices = available.get(key, ())
            paired_primary = primary_indices[:len(target_indices)]
            if not paired_primary:
                continue
            next_candidates[key] = paired_primary
            for primary_index, target_index in zip(
                paired_primary, target_indices
            ):
                matches[primary_index].append(target_index)
        candidates = next_candidates
        if not candidates:
            return {}
    return {
        index: tuple(matches[index])
        for indices in candidates.values()
        for index in indices
    }


def effect_reorder_is_aligned(
    states: Sequence[TextEffectStack], index: int
) -> bool:
    """Return whether the card's relevant visible sequences align exactly."""
    values = tuple(states)
    if not values or not 0 <= index < len(values[0].effects):
        return False
    reference = values[0].effects[index]
    family = (
        (TextFillEffect,)
        if isinstance(reference, TextFillEffect)
        else (StrokeEffect, ShadowEffect, GlowEffect)
    )
    if not isinstance(reference, family):
        return False
    sequences = [
        tuple(
            effect_structure_key(effect)
            for effect in reversed(state.effects)
            if isinstance(effect, family)
        )
        for state in values
    ]
    return all(sequence == sequences[0] for sequence in sequences[1:])


class TextEffectEditSession:
    """Own one complete-stack preview and commit transaction."""

    def __init__(
        self,
        host: "FontFormatPanel",
        controls: Optional["TextEffectPanel"] = None,
    ) -> None:
        self.host = host
        self.controls = controls
        self.items = []
        self.preview_before = None
        self.preview_key = None
        self._matched_occurrences: Dict[int, Tuple[int, ...]] = {}
        if controls is not None:
            controls.value_commit_requested.connect(self.commit_value)
            controls.value_preview_requested.connect(self.preview_value)
            controls.parameter_preview_requested.connect(
                self.preview_parameter_delta
            )
            controls.parameter_commit_requested.connect(
                self.commit_parameter_delta
            )
            controls.preview_canceled.connect(self.cancel_preview)
            controls.add_effect_requested.connect(self.add_effect)
            controls.hollow_enabled_requested.connect(
                self.set_hollow_enabled
            )
            controls.remove_effect_requested.connect(self.remove_effect)
            controls.move_effect_requested.connect(self.move_effect)

    @staticmethod
    def _state_for_item(item: "TextBlkItem") -> TextEffectStack:
        return item.blk.fontformat.text_effects

    def _current_states(self) -> Tuple[TextEffectStack, ...]:
        if self.items:
            return tuple(self._state_for_item(item) for item in self.items)
        return (self.host.global_format.text_effects,)

    def _validate_states(
        self, states: Sequence[TextEffectStack]
    ) -> Tuple[TextEffectStack, ...]:
        values = tuple(states)
        expected = len(self.items) if self.items else 1
        if len(values) != expected:
            raise ValueError(
                'owners and effect states must have the same length'
            )
        if any(not isinstance(value, TextEffectStack) for value in values):
            raise TypeError(
                'effect edit session requires TextEffectStack values'
            )
        return values

    @staticmethod
    def _convert_effect_paint(
        paint: EffectPaint,
        paint_type: str,
    ) -> GeneratedEffectPaint:
        """Convert an effect Fill while preserving its visible color.

        >>> converted = TextEffectEditSession._convert_effect_paint(
        ...     SolidPaint((1, 2, 3)), 'linear_gradient'
        ... )
        >>> converted.stops[-1].opacity
        0.0
        """
        if paint_type not in {'solid', 'linear_gradient'}:
            raise ValueError('unsupported effect paint type')
        if paint_type == 'solid':
            if isinstance(paint, SolidPaint):
                return paint
            return SolidPaint(effect_paint_fallback_color(paint))
        if isinstance(paint, LinearGradientPaint):
            return paint
        color = effect_paint_fallback_color(paint)
        return LinearGradientPaint(stops=(
            GradientStop(0.0, color, 1.0),
            GradientStop(1.0, color, 0.0),
        ))

    @staticmethod
    def _with_value(
        state: TextEffectStack,
        index: int,
        param_name: str,
        value,
    ) -> TextEffectStack:
        if index == OVERALL_OPACITY_INDEX:
            if param_name != 'overall_opacity':
                raise ValueError('unknown overall text effect field')
            return replace(state, overall_opacity=value)
        if index < 0 or index >= len(state.effects):
            raise IndexError('text effect index is no longer current')
        effect = state.effects[index]
        parameters = {}
        if isinstance(effect, StrokeEffect):
            if param_name not in {
                'enabled', 'width', 'opacity', 'paint', 'paint_type',
                'position', 'blend_mode',
            }:
                raise ValueError('unknown Stroke field')
            if param_name == 'paint':
                if not isinstance(value, (SolidPaint, LinearGradientPaint)):
                    value = SolidPaint(value)
                parameters['paint'] = value
            elif param_name == 'paint_type':
                parameters['paint'] = (
                    TextEffectEditSession._convert_effect_paint(
                        effect.paint, value
                    )
                )
            else:
                parameters[param_name] = value
        elif isinstance(effect, ShadowEffect):
            if param_name not in {
                'enabled', 'opacity', 'shadow_type', 'paint', 'paint_type',
                'angle', 'distance', 'blur', 'spread', 'blend_mode',
            }:
                raise ValueError('unknown Shadow field')
            elif param_name == 'paint':
                if not isinstance(value, (SolidPaint, LinearGradientPaint)):
                    value = SolidPaint(value)
                parameters['paint'] = value
            elif param_name == 'paint_type':
                parameters['paint'] = (
                    TextEffectEditSession._convert_effect_paint(
                        effect.paint, value
                    )
                )
            else:
                parameters[param_name] = value
        elif isinstance(effect, GlowEffect):
            if param_name not in {
                'enabled', 'opacity', 'glow_type', 'paint', 'paint_type',
                'size', 'spread', 'blend_mode',
            }:
                raise ValueError('unknown Glow field')
            if param_name == 'paint':
                if not isinstance(value, (SolidPaint, LinearGradientPaint)):
                    value = SolidPaint(value)
                parameters['paint'] = value
            elif param_name == 'paint_type':
                parameters['paint'] = (
                    TextEffectEditSession._convert_effect_paint(
                        effect.paint, value
                    )
                )
            else:
                parameters[param_name] = value
        elif isinstance(effect, HollowEffect):
            if param_name != 'enabled':
                raise ValueError('unknown Hollow field')
            parameters['enabled'] = value
        elif isinstance(effect, TextFillEffect):
            if param_name not in {
                'enabled', 'paint', 'opacity', 'blend_mode',
            }:
                raise ValueError('unknown Text Fill field')
            if param_name == 'paint':
                if not isinstance(value, LinearGradientPaint):
                    raise TypeError(
                        'Text Fill paint must be a Gradient paint'
                    )
                parameters['paint'] = value
            else:
                parameters[param_name] = value
        else:
            raise ValueError('selected text effect type is unsupported')
        effects = list(state.effects)
        effects[index] = replace(effect, **parameters)
        return replace(state, effects=tuple(effects))

    @staticmethod
    def _value_at(
        state: TextEffectStack, index: int, param_name: str
    ):
        if index == OVERALL_OPACITY_INDEX:
            if param_name != 'overall_opacity':
                raise ValueError('unknown overall text effect field')
            return state.overall_opacity
        if index < 0 or index >= len(state.effects):
            raise IndexError('text effect index is no longer current')
        effect = state.effects[index]
        return getattr(effect, param_name)

    def _set_global_effects(self, state: TextEffectStack) -> None:
        state = without_project_raster_effects(state)
        self.host.global_format.text_effects = state
        active = C.active_format
        if active is self.host.global_format:
            active.text_effects = state

    def _apply_preview_states(
        self, states: Sequence[TextEffectStack]
    ) -> bool:
        targets = self._validate_states(states)
        if self.items:
            changed = False
            for item, state in zip(self.items, targets):
                changed = (
                    item.set_text_effects(state, preview=True) or changed
                )
            return changed
        changed = self.host.global_format.text_effects != targets[0]
        self._set_global_effects(targets[0])
        return changed

    def _sync_effect_ui(self) -> None:
        self._refresh_occurrence_mapping()
        controls = self.controls
        if self.items:
            if controls is not None:
                controls.set_effect_items(self.items)
            if len(self.items) == 1:
                item = self.items[0]
                current_item = getattr(self.host, 'textblk_item', None)
                if current_item is item and C.active_format is not None:
                    C.active_format.text_effects = self._state_for_item(item)
        elif controls is not None:
            controls.set_active_format(self.host.global_format)

    def _commit_complete_states(
        self,
        before: Sequence[TextEffectStack],
        after: Sequence[TextEffectStack],
    ) -> bool:
        before = tuple(before)
        after = tuple(after)
        if not self.items:
            after = tuple(
                without_project_raster_effects(state) for state in after
            )
            changed = before != after
            self._set_global_effects(after[0])
            if changed and hasattr(self.host, 'update_text_style_label'):
                self.host.update_text_style_label()
            self._sync_effect_ui()
            return changed
        command = SetTextEffectStackCommand.create(
            self.items, before, after, self._sync_effect_ui
        )
        if command is None:
            for item in self.items:
                item.clear_text_effect_preview()
            self._sync_effect_ui()
            return False
        SW.canvas.push_undo_command(command)
        return True

    def replace_targets(self, items: Sequence["TextBlkItem"]) -> None:
        replacements = list(items)
        changed = len(replacements) != len(self.items) or any(
            current is not replacement
            for current, replacement in zip(self.items, replacements)
        )
        if changed:
            self.cancel_preview()
        self.items = replacements
        self._refresh_occurrence_mapping()

    def _refresh_occurrence_mapping(
        self, states: Optional[Sequence[TextEffectStack]] = None
    ) -> None:
        values = self._current_states() if states is None else tuple(states)
        self._matched_occurrences = matched_effect_occurrences(values)

    def preview_states(self, states: Sequence[TextEffectStack]) -> bool:
        """Preview complete selected-item states for the item boundary API."""
        if not self.items:
            return False
        targets = self._validate_states(states)
        if self.preview_before is None:
            self.preview_before = self._current_states()
            self.preview_key = ('complete-stack',)
        return self._apply_preview_states(targets)

    def commit_states(
        self, states: Optional[Sequence[TextEffectStack]] = None
    ) -> bool:
        if not self.items and not hasattr(self.host, 'global_format'):
            self.preview_before = None
            self.preview_key = None
            return False
        before = (
            self._current_states()
            if self.preview_before is None
            else self.preview_before
        )
        if states is None:
            after = (
                tuple(item.effective_text_effects() for item in self.items)
                if self.items else self._current_states()
            )
        else:
            after = self._validate_states(states)
        self.preview_before = None
        self.preview_key = None
        return self._commit_complete_states(before, after)

    def _begin_preview(self, key: tuple) -> Tuple[TextEffectStack, ...]:
        if self.preview_before is not None and self.preview_key != key:
            self.cancel_preview()
        if self.preview_before is None:
            self.preview_before = self._current_states()
            self.preview_key = key
        return self.preview_before

    def _target_indices(
        self, states: Sequence[TextEffectStack], index: int
    ) -> Tuple[Optional[int], ...]:
        if index == OVERALL_OPACITY_INDEX:
            return (index,) * len(states)
        if len(states) <= 1:
            return (index,) * len(states)
        matched = self._matched_occurrences.get(index)
        if matched is not None:
            return matched
        return (index,) + (None,) * (len(states) - 1)

    def preview_value(
        self, index: int, param_name: str, value
    ) -> None:
        key = (int(index), str(param_name))
        before = self._begin_preview(key)
        target_indices = self._target_indices(before, index)
        try:
            after = [
                state if target_index is None else self._with_value(
                    state, target_index, param_name, value
                )
                for state, target_index in zip(before, target_indices)
            ]
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
        ):
            self.cancel_preview()
            return
        self._apply_preview_states(after)

    def preview_parameter_delta(
        self, index: int, param_name: str, canonical_delta: float
    ) -> None:
        key = (int(index), str(param_name))
        before = self._begin_preview(key)
        target_indices = self._target_indices(before, index)
        try:
            after = [
                state if target_index is None else self._with_value(
                    state,
                    target_index,
                    param_name,
                    self._value_at(state, target_index, param_name)
                    + canonical_delta,
                )
                for state, target_index in zip(before, target_indices)
            ]
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
        ):
            self.cancel_preview()
            return
        self._apply_preview_states(after)

    def commit_value(self, index: int, param_name: str, value) -> bool:
        key = (int(index), str(param_name))
        if self.preview_before is not None and self.preview_key != key:
            self.cancel_preview()
        before = self.preview_before or self._current_states()
        target_indices = self._target_indices(before, index)
        try:
            after = [
                state if target_index is None else self._with_value(
                    state, target_index, param_name, value
                )
                for state, target_index in zip(before, target_indices)
            ]
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
        ):
            self.cancel_preview()
            self._sync_effect_ui()
            return False
        self.preview_before = None
        self.preview_key = None
        return self._commit_complete_states(before, after)

    def commit_parameter_delta(
        self, index: int, param_name: str, canonical_delta: float
    ) -> bool:
        key = (int(index), str(param_name))
        if self.preview_before is None or self.preview_key != key:
            return False
        before = self.preview_before
        target_indices = self._target_indices(before, index)
        try:
            after = [
                state if target_index is None else self._with_value(
                    state,
                    target_index,
                    param_name,
                    self._value_at(state, target_index, param_name)
                    + canonical_delta,
                )
                for state, target_index in zip(before, target_indices)
            ]
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
        ):
            self.cancel_preview()
            return False
        self.preview_before = None
        self.preview_key = None
        return self._commit_complete_states(before, after)

    def _prepare_structure_change(self) -> None:
        if self.controls is not None:
            self.controls.finish_pending_effect_edits()
            self.controls.cancel_effect_previews()
        self.cancel_preview()
        self._refresh_occurrence_mapping()

    @staticmethod
    def _insertion_index(
        state: TextEffectStack, effect: TextEffect
    ) -> int:
        if isinstance(effect, HollowEffect):
            return len(state.effects)
        # Raw order is topmost-first. New movable effects and structural Fills
        # therefore land at zero so reverse/application order appends them.
        return 0

    @staticmethod
    def _matched_insertion_index(
        state: TextEffectStack,
        effect: TextEffect,
        visible_occurrence: int,
    ) -> int:
        """Insert after the occurrences already common to every target."""
        key = effect_structure_key(effect)
        visible_indices = [
            index
            for index in range(len(state.effects) - 1, -1, -1)
            if effect_structure_key(state.effects[index]) == key
        ]
        if not visible_indices:
            return TextEffectEditSession._insertion_index(state, effect)
        if visible_occurrence >= len(visible_indices):
            return TextEffectEditSession._insertion_index(state, effect)
        return visible_indices[visible_occurrence] + 1

    @staticmethod
    def _common_occurrence_budget(
        states: Sequence[TextEffectStack], effect: TextEffect
    ) -> int:
        key = effect_structure_key(effect)
        return min(
            sum(
                effect_structure_key(candidate) == key
                for candidate in state.effects
            )
            for state in states
        )

    def _insert_effect(
        self,
        before: Sequence[TextEffectStack],
        effect: TextEffect,
    ) -> bool:
        """Insert one effect across the active targets and reveal its card.

        >>> session = object.__new__(TextEffectEditSession)
        >>> session.controls = None
        >>> session._commit_complete_states = lambda before, after: True
        >>> session._insert_effect((TextEffectStack(),), StrokeEffect())
        True
        """
        common_budget = (
            self._common_occurrence_budget(before, effect)
            if len(before) > 1 else None
        )
        after = []
        primary_insert_index: Optional[int] = None
        for state in before:
            effects = list(state.effects)
            insert_index = (
                self._insertion_index(state, effect)
                if common_budget is None
                else self._matched_insertion_index(
                    state, effect, common_budget
                )
            )
            if primary_insert_index is None:
                primary_insert_index = insert_index
            effects.insert(insert_index, effect)
            after.append(replace(state, effects=tuple(effects)))
        changed = self._commit_complete_states(before, after)
        if (
            changed
            and self.controls is not None
            and primary_insert_index is not None
        ):
            self.controls.reveal_effect_card(primary_insert_index)
        return changed

    def add_effect(self, effect_type: str) -> bool:
        self._prepare_structure_change()
        before = self._current_states()
        constructors = {
            'stroke': StrokeEffect,
            'shadow': ShadowEffect,
            'glow': GlowEffect,
            'gradient': lambda: TextFillEffect(
                paint=LinearGradientPaint()
            ),
        }
        constructor = constructors.get(effect_type)
        if constructor is None:
            self._sync_effect_ui()
            return False
        effect = constructor()
        return self._insert_effect(before, effect)

    def set_hollow_enabled(self, enabled: bool) -> bool:
        """Enable the unique Hollow value, inserting it when first used.

        >>> from types import SimpleNamespace
        >>> owner = SimpleNamespace(text_effects=TextEffectStack())
        >>> session = TextEffectEditSession(
        ...     SimpleNamespace(global_format=owner)
        ... )
        >>> session.set_hollow_enabled(True)
        True
        >>> owner.text_effects.effects[0].enabled
        True
        """
        self._prepare_structure_change()
        before = self._current_states()
        after = []
        for state in before:
            effects = list(state.effects)
            index = next(
                (
                    index
                    for index, effect in enumerate(effects)
                    if isinstance(effect, HollowEffect)
                ),
                None,
            )
            if index is None:
                if enabled:
                    effect = HollowEffect()
                    effects.insert(
                        self._insertion_index(state, effect), effect
                    )
            elif effects[index].enabled != enabled:
                effects[index] = replace(effects[index], enabled=enabled)
            after.append(replace(state, effects=tuple(effects)))
        return self._commit_complete_states(before, after)

    def remove_effect(self, index: int) -> bool:
        self._prepare_structure_change()
        before = self._current_states()
        target_indices = self._target_indices(before, index)
        if index < 0:
            self._sync_effect_ui()
            return False
        after = []
        for state, target_index in zip(before, target_indices):
            if target_index is None:
                after.append(state)
                continue
            if target_index >= len(state.effects):
                self._sync_effect_ui()
                return False
            effects = list(state.effects)
            del effects[target_index]
            after.append(replace(state, effects=tuple(effects)))
        return self._commit_complete_states(before, after)

    def move_effect(self, index: int, direction: int) -> bool:
        self._prepare_structure_change()
        before = self._current_states()
        if (
            direction not in (-1, 1)
            or index < 0
            or index >= len(before[0].effects)
        ):
            self._sync_effect_ui()
            return False
        effect = before[0].effects[index]
        movable_types = (
            (TextFillEffect,)
            if isinstance(effect, TextFillEffect)
            else (StrokeEffect, ShadowEffect, GlowEffect)
        )
        if not isinstance(effect, movable_types):
            self._sync_effect_ui()
            return False
        target_indices = self._target_indices(before, index)
        if (
            len(before) > 1
            and all(target_index is not None for target_index in target_indices)
            and not effect_reorder_is_aligned(before, index)
        ):
            self._sync_effect_ui()
            return False
        after = []
        for state, target_index in zip(before, target_indices):
            if target_index is None:
                after.append(state)
                continue
            movable_indices = [
                effect_index
                for effect_index, candidate in enumerate(state.effects)
                if isinstance(candidate, movable_types)
            ]
            try:
                position = movable_indices.index(target_index)
                destination = movable_indices[position + direction]
            except (IndexError, ValueError):
                self._sync_effect_ui()
                return False
            effects = list(state.effects)
            effects[target_index], effects[destination] = (
                effects[destination], effects[target_index]
            )
            after.append(replace(state, effects=tuple(effects)))
        return self._commit_complete_states(before, after)

    def cancel_preview(self, *_key) -> bool:
        before = self.preview_before
        changed = False
        if self.items:
            for item in self.items:
                changed = item.clear_text_effect_preview() or changed
        elif before is not None:
            changed = self.host.global_format.text_effects != before[0]
            self._set_global_effects(before[0])
        self.preview_before = None
        self.preview_key = None
        if before is not None:
            self._sync_effect_ui()
        return changed

    def finish_pending_edits(self) -> None:
        if self.controls is not None:
            self.controls.finish_pending_effect_edits()

    def resolve_for_save(self) -> None:
        self.finish_pending_edits()
        if self.controls is not None:
            self.controls.cancel_effect_previews()
        self.cancel_preview()

    def resolve_for_history_change(self) -> None:
        if self.controls is not None:
            self.controls.cancel_pending_effect_edits()
            self.controls.cancel_effect_previews()
        self.cancel_preview()

    def resolve_for_page_change(self) -> None:
        self.resolve_for_save()
        self.items = []
        self._matched_occurrences = {}

    def cancel_for_scene_change(self) -> None:
        if self.controls is not None:
            self.controls.cancel_pending_effect_edits()
            self.controls.cancel_effect_previews()
        self.cancel_preview()
        self.items = []
        self._matched_occurrences = {}
