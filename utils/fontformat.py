import copy
import enum
import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from typing import ClassVar, Iterator, List, Optional, Sequence, Union

import numpy as np

from . import shared
from .logger import logger as LOGGER
from .structures import Config, nested_dataclass
from .text_effects import (
    GradientStop,
    LinearGradientPaint,
    ShadowEffect,
    SolidPaint,
    TextEffectStack,
    TextFillEffect,
    coerce_text_effect_stack,
    effect_paint_fallback_color,
    primary_stroke,
    with_primary_stroke,
)


TEXT_TRANSFORM_SCALE_MIN = 0.1
TEXT_TRANSFORM_SCALE_MAX = 4.0
TEXT_TRANSFORM_PROJECTIVE_SLANT_MIN = -85.0
TEXT_TRANSFORM_PROJECTIVE_SLANT_MAX = 85.0
TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MIN = -89.0
TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MAX = 89.0
TEXT_TRANSFORM_PROJECTIVE_ROTATION_Z_MIN = -180.0
TEXT_TRANSFORM_PROJECTIVE_ROTATION_Z_MAX = 180.0
TEXT_TRANSFORM_PROJECTIVE_PERSPECTIVE_MIN = 0.0
TEXT_TRANSFORM_PROJECTIVE_PERSPECTIVE_MAX = 0.8
TEXT_TRANSFORM_GLYPH_SLANT_MIN = -45.0
TEXT_TRANSFORM_GLYPH_SLANT_MAX = 45.0
TEXT_TRANSFORM_BEND_MIN = -1.0
TEXT_TRANSFORM_BEND_MAX = 1.0
TEXT_TRANSFORM_SINE_FREQUENCY_MIN = 0
TEXT_TRANSFORM_SINE_FREQUENCY_MAX = 64
TEXT_TRANSFORM_SINE_PHASE_MIN = 0.0
TEXT_TRANSFORM_SINE_PHASE_MAX = 1.0
TEXT_TRANSFORM_SINE_AMPLITUDE_MIN = 0.0
TEXT_TRANSFORM_SINE_AMPLITUDE_MAX = 1.0
TEXT_TRANSFORM_GRID_DIVISION_MIN = 1
TEXT_TRANSFORM_GRID_DIVISION_MAX = 32
TEXT_TRANSFORM_GRID_INTERPOLATION_TYPES = ('bilinear', 'catmull_rom')
TEXT_TRANSFORM_PRECISION = 6


def _transform_value_field_names(
    transform: Union["TextTransform", type["TextTransform"]],
) -> tuple[str, ...]:
    """Return constructor fields, excluding derived fields such as the type."""
    return tuple(field.name for field in fields(transform) if field.init)


@dataclass(frozen=True)
class TextTransform:
    """Immutable base value for a persisted text-transform variant.

    Subclasses expose stable component names and normalization. Persistence
    stores ``transform_type`` with the variant-specific component payload.

    >>> ProjectiveTextTransform().transform_type
    'projective'
    """

    transform_type: str = field(init=False, default='base')
    # ``nonlinear`` means that QTransform cannot represent the operation and
    # the completed text surface must be inverse-warped instead.
    is_nonlinear: ClassVar[bool] = False

    def normalized(self) -> "TextTransform":
        raise NotImplementedError

    def with_value(self, name: str, value: float) -> "TextTransform":
        if name not in _transform_value_field_names(self):
            raise ValueError(
                f'unknown {self.transform_type} transform field {name}'
            )
        return replace(self, **{name: value}).normalized()

    def is_neutral(self) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class ProjectiveTextTransform(TextTransform):
    """One native projective stage for affine and planar 3D controls.

    X and Y stop short of edge-on because a projected flat plane is singular
    at exactly 90 degrees.

    >>> ProjectiveTextTransform(rotation_x=90).normalized().rotation_x
    89.0
    """

    horizontal_scale: float = 1.0
    vertical_scale: float = 1.0
    horizontal_slant: float = 0.0
    vertical_slant: float = 0.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0
    perspective: float = 0.0
    transform_type: str = field(init=False, default='projective')

    def normalized(self) -> "ProjectiveTextTransform":
        return ProjectiveTextTransform(
            normalize_text_transform_value(
                self.horizontal_scale,
                TEXT_TRANSFORM_SCALE_MIN,
                TEXT_TRANSFORM_SCALE_MAX,
            ),
            normalize_text_transform_value(
                self.vertical_scale,
                TEXT_TRANSFORM_SCALE_MIN,
                TEXT_TRANSFORM_SCALE_MAX,
            ),
            normalize_text_transform_value(
                self.horizontal_slant,
                TEXT_TRANSFORM_PROJECTIVE_SLANT_MIN,
                TEXT_TRANSFORM_PROJECTIVE_SLANT_MAX,
            ),
            normalize_text_transform_value(
                self.vertical_slant,
                TEXT_TRANSFORM_PROJECTIVE_SLANT_MIN,
                TEXT_TRANSFORM_PROJECTIVE_SLANT_MAX,
            ),
            normalize_text_transform_value(
                self.rotation_x,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MIN,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MAX,
            ),
            normalize_text_transform_value(
                self.rotation_y,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MIN,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_XY_MAX,
            ),
            normalize_text_transform_value(
                self.rotation_z,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_Z_MIN,
                TEXT_TRANSFORM_PROJECTIVE_ROTATION_Z_MAX,
            ),
            normalize_text_transform_value(
                self.perspective,
                TEXT_TRANSFORM_PROJECTIVE_PERSPECTIVE_MIN,
                TEXT_TRANSFORM_PROJECTIVE_PERSPECTIVE_MAX,
            ),
        )

    def is_neutral(self) -> bool:
        normalized = self.normalized()
        return (
            normalized.horizontal_scale == 1.0
            and normalized.vertical_scale == 1.0
            and normalized.horizontal_slant == 0.0
            and normalized.vertical_slant == 0.0
            and normalized.rotation_x == 0.0
            and normalized.rotation_y == 0.0
            and normalized.rotation_z == 0.0
        )


@dataclass(frozen=True)
class BendTextTransform(TextTransform):
    """Signed circular bend applied to the completed text surface."""

    bend: float = 0.0
    transform_type: str = field(init=False, default='bend')
    is_nonlinear: ClassVar[bool] = True

    def normalized(self) -> "BendTextTransform":
        return BendTextTransform(
            normalize_text_transform_value(
                self.bend,
                TEXT_TRANSFORM_BEND_MIN,
                TEXT_TRANSFORM_BEND_MAX,
            )
        )

    def is_neutral(self) -> bool:
        return self.bend == 0.0


def _normalize_sine_frequency(value: Union[int, float, np.number]) -> int:
    if isinstance(value, bool) or not isinstance(
        value, (int, float, np.number)
    ):
        raise ValueError('sine frequencies must be integers from 0 to 64')
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError('sine frequencies must be integers from 0 to 64')
    frequency = int(numeric)
    if not (
        TEXT_TRANSFORM_SINE_FREQUENCY_MIN
        <= frequency
        <= TEXT_TRANSFORM_SINE_FREQUENCY_MAX
    ):
        raise ValueError('sine frequencies must be integers from 0 to 64')
    return frequency


@dataclass(frozen=True)
class SineTextTransform(TextTransform):
    """Two ordered sine shears over the completed text surface.

    Frequencies count half-waves. The x-axis wave is applied first so the
    paired mappings remain exactly invertible at every supported value.

    >>> SineTextTransform().normalized().is_neutral()
    False
    >>> SineTextTransform(frequency_x=0).normalized().is_neutral()
    True
    """

    frequency_x: int = 2
    frequency_y: int = 0
    phase_x: float = 0.0
    phase_y: float = 0.0
    amplitude_x: float = 0.1
    amplitude_y: float = 0.1
    transform_type: str = field(init=False, default='sine')
    is_nonlinear: ClassVar[bool] = True

    def normalized(self) -> "SineTextTransform":
        return SineTextTransform(
            _normalize_sine_frequency(self.frequency_x),
            _normalize_sine_frequency(self.frequency_y),
            normalize_text_transform_value(
                self.phase_x,
                TEXT_TRANSFORM_SINE_PHASE_MIN,
                TEXT_TRANSFORM_SINE_PHASE_MAX,
            ),
            normalize_text_transform_value(
                self.phase_y,
                TEXT_TRANSFORM_SINE_PHASE_MIN,
                TEXT_TRANSFORM_SINE_PHASE_MAX,
            ),
            normalize_text_transform_value(
                self.amplitude_x,
                TEXT_TRANSFORM_SINE_AMPLITUDE_MIN,
                TEXT_TRANSFORM_SINE_AMPLITUDE_MAX,
            ),
            normalize_text_transform_value(
                self.amplitude_y,
                TEXT_TRANSFORM_SINE_AMPLITUDE_MIN,
                TEXT_TRANSFORM_SINE_AMPLITUDE_MAX,
            ),
        )

    def is_neutral(self) -> bool:
        normalized = self.normalized()
        return (
            normalized.frequency_x == 0
            or normalized.amplitude_x == 0.0
        ) and (
            normalized.frequency_y == 0
            or normalized.amplitude_y == 0.0
        )


def _normalize_grid_division(value: Union[int, float, np.number]) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError('grid divisions must be integers from 1 to 32')
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError('grid divisions must be integers from 1 to 32')
    division = int(numeric)
    if not (
        TEXT_TRANSFORM_GRID_DIVISION_MIN
        <= division
        <= TEXT_TRANSFORM_GRID_DIVISION_MAX
    ):
        raise ValueError('grid divisions must be integers from 1 to 32')
    return division


def _default_grid_control_points(horizontal: int, vertical: int) -> tuple:
    return tuple(
        (
            round(column / horizontal, TEXT_TRANSFORM_PRECISION),
            round(row / vertical, TEXT_TRANSFORM_PRECISION),
        )
        for row in range(vertical + 1)
        for column in range(horizontal + 1)
    )


def _normalize_grid_control_points(
    points: Sequence[Sequence[float]], horizontal: int, vertical: int
) -> tuple:
    expected = (horizontal + 1) * (vertical + 1)
    if not isinstance(points, (list, tuple)) or len(points) != expected:
        raise ValueError(f'grid transform requires {expected} control points')
    normalized = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError('grid control points must be finite coordinate pairs')
        coordinates = []
        for value in point:
            if isinstance(value, bool) or not isinstance(
                value, (int, float, np.number)
            ):
                raise ValueError(
                    'grid control points must be finite coordinate pairs'
                )
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(
                    'grid control points must be finite coordinate pairs'
                )
            coordinates.append(round(value, TEXT_TRANSFORM_PRECISION))
        normalized.append(tuple(coordinates))
    return tuple(normalized)


def _interpolate_grid_point_bilinear(
    points: tuple,
    horizontal: int,
    vertical: int,
    x: float,
    y: float,
) -> tuple:
    scaled_x = min(max(x, 0.0), 1.0) * horizontal
    scaled_y = min(max(y, 0.0), 1.0) * vertical
    column = min(int(scaled_x), horizontal - 1)
    row = min(int(scaled_y), vertical - 1)
    local_x = scaled_x - column
    local_y = scaled_y - row
    stride = horizontal + 1
    top_left = points[row * stride + column]
    top_right = points[row * stride + column + 1]
    bottom_left = points[(row + 1) * stride + column]
    bottom_right = points[(row + 1) * stride + column + 1]
    return tuple(
        (1.0 - local_y)
        * ((1.0 - local_x) * top_left[axis] + local_x * top_right[axis])
        + local_y
        * ((1.0 - local_x) * bottom_left[axis] + local_x * bottom_right[axis])
        for axis in range(2)
    )


def _resample_grid_control_points(
    points: tuple,
    old_horizontal: int,
    old_vertical: int,
    new_horizontal: int,
    new_vertical: int,
) -> tuple:
    return tuple(
        _interpolate_grid_point_bilinear(
            points,
            old_horizontal,
            old_vertical,
            column / new_horizontal,
            row / new_vertical,
        )
        for row in range(new_vertical + 1)
        for column in range(new_horizontal + 1)
    )


@dataclass(frozen=True)
class GridTextTransform(TextTransform):
    """Free-form grid deformation stored in normalized logical coordinates.

    Division counts describe cells, so the neutral 1 by 1 grid has four
    corner handles.

    >>> len(GridTextTransform().normalized().control_points)
    4
    >>> GridTextTransform(horizontal_divisions=2).normalized().is_neutral()
    True
    """

    horizontal_divisions: int = 1
    vertical_divisions: int = 1
    interpolation: str = 'bilinear'
    control_points: tuple = ()
    transform_type: str = field(init=False, default='grid')
    is_nonlinear: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if self.control_points:
            try:
                points = tuple(tuple(point) for point in self.control_points)
            except TypeError as error:
                raise ValueError(
                    'grid control points must be coordinate pairs'
                ) from error
            object.__setattr__(self, 'control_points', points)

    def normalized(self) -> "GridTextTransform":
        horizontal = _normalize_grid_division(self.horizontal_divisions)
        vertical = _normalize_grid_division(self.vertical_divisions)
        if self.interpolation not in TEXT_TRANSFORM_GRID_INTERPOLATION_TYPES:
            raise ValueError(
                f'unsupported grid interpolation type {self.interpolation!r}'
            )
        points = (
            _normalize_grid_control_points(
                self.control_points, horizontal, vertical
            )
            if self.control_points
            else _default_grid_control_points(horizontal, vertical)
        )
        return GridTextTransform(
            horizontal,
            vertical,
            self.interpolation,
            points,
        )

    def with_value(
        self, name: str, value: Union[int, float, str]
    ) -> "GridTextTransform":
        current = self.normalized()
        if name in {'horizontal_divisions', 'vertical_divisions'}:
            horizontal = (
                _normalize_grid_division(value)
                if name == 'horizontal_divisions'
                else current.horizontal_divisions
            )
            vertical = (
                _normalize_grid_division(value)
                if name == 'vertical_divisions'
                else current.vertical_divisions
            )
            points = _resample_grid_control_points(
                current.control_points,
                current.horizontal_divisions,
                current.vertical_divisions,
                horizontal,
                vertical,
            )
            return GridTextTransform(
                horizontal,
                vertical,
                current.interpolation,
                points,
            ).normalized()
        if name == 'interpolation':
            return replace(current, interpolation=value).normalized()
        return super().with_value(name, value)

    def with_control_points(
        self, points: Sequence[Sequence[float]]
    ) -> "GridTextTransform":
        return replace(self, control_points=tuple(points)).normalized()

    def is_neutral(self) -> bool:
        normalized = self.normalized()
        return normalized.control_points == _default_grid_control_points(
            normalized.horizontal_divisions,
            normalized.vertical_divisions,
        )


@dataclass(frozen=True)
class TextTransformStack:
    """Immutable ordered text-geometry operations.

    Empty means no geometry transform. Neutral entries remain present for the
    editor but are skipped by the runtime compiler.

    >>> stack = TextTransformStack((BendTextTransform(0.5),))
    >>> stack.has_nonlinear
    True
    """

    transforms: tuple[TextTransform, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'transforms',
            tuple(coerce_text_transform(value) for value in self.transforms),
        )

    def __iter__(self) -> Iterator[TextTransform]:
        return iter(self.transforms)

    def __len__(self) -> int:
        return len(self.transforms)

    def __getitem__(self, index: int) -> TextTransform:
        return self.transforms[index]

    def is_neutral(self) -> bool:
        return all(transform.is_neutral() for transform in self.transforms)

    @property
    def has_nonlinear(self) -> bool:
        return any(
            not transform.is_neutral() and transform.is_nonlinear
            for transform in self.transforms
        )


@dataclass(frozen=True)
class TextTransformState:
    """Complete immutable state edited by the transform undo command.

    Geometry operations stay ordered while Glyph Slant remains one layout
    effect applied before that geometry.

    >>> TextTransformState().glyph_slant_angle
    0.0
    """

    stack: TextTransformStack = field(default_factory=TextTransformStack)
    glyph_slant_angle: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'stack', coerce_text_transform_stack(self.stack)
        )
        object.__setattr__(
            self,
            'glyph_slant_angle',
            normalize_text_transform_value(
                self.glyph_slant_angle,
                TEXT_TRANSFORM_GLYPH_SLANT_MIN,
                TEXT_TRANSFORM_GLYPH_SLANT_MAX,
            ),
        )


def normalize_text_transform_value(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    """Return a finite, clamped canonical text-transform component.

    Persistence validates stored values before normalization; this pure helper
    defines the canonical value shared by the model, UI, and undo commands.

    >>> normalize_text_transform_value(4.5, 0.1, 4.0)
    4.0
    >>> normalize_text_transform_value(-0.0, -45.0, 45.0)
    0.0
    >>> normalize_text_transform_value(float("nan"), 0.1, 4.0)
    Traceback (most recent call last):
    ...
    ValueError: text transform values must be finite numbers
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError("text transform values must be finite numbers")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("text transform values must be finite numbers")
    value = round(min(max(value, minimum), maximum), TEXT_TRANSFORM_PRECISION)
    return 0.0 if value == 0.0 else value


TEXT_TRANSFORM_TYPES = {
    'projective': ProjectiveTextTransform,
    'bend': BendTextTransform,
    'sine': SineTextTransform,
    'grid': GridTextTransform,
}


def create_text_transform(transform_type: str) -> TextTransform:
    """Create the neutral initial value for a registered transform type.

    UI-selectable variants must provide constructor defaults. Persisted
    payloads may still supply required variant fields through
    :func:`coerce_text_transform`.

    >>> create_text_transform('projective')
    ProjectiveTextTransform(transform_type='projective', horizontal_scale=1.0, vertical_scale=1.0, horizontal_slant=0.0, vertical_slant=0.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0, perspective=0.0)
    """
    transform_class = TEXT_TRANSFORM_TYPES.get(transform_type)
    if transform_class is None:
        raise ValueError(f'unsupported text transform type {transform_type}')
    return transform_class().normalized()


def coerce_text_transform(value: Union[TextTransform, dict]) -> TextTransform:
    """Normalize a live value or construct a canonical persisted payload.

    >>> transform = coerce_text_transform(
    ...     {'transform_type': 'projective', 'rotation_z': 5}
    ... )
    >>> transform.rotation_z
    5.0
    >>> coerce_text_transform(
    ...     {'transform_type': 'projective', 'horizontal_scale': 5}
    ... )
    Traceback (most recent call last):
    ...
    ValueError: persisted projective transform values must be canonical
    """
    if isinstance(value, TextTransform):
        return value.normalized()
    if not isinstance(value, dict):
        raise ValueError('text transform must be a value or typed payload')
    payload = dict(value)
    if 'transform_type' not in payload:
        raise ValueError('text transform payload requires transform_type')
    transform_type = payload.pop('transform_type')
    transform_class = TEXT_TRANSFORM_TYPES.get(transform_type)
    if transform_class is None:
        raise ValueError(f'unsupported text transform type {transform_type}')
    value_fields = _transform_value_field_names(transform_class)
    unexpected = set(payload) - set(value_fields)
    if unexpected:
        raise ValueError(
            f'unsupported {transform_type} transform fields: {sorted(unexpected)}'
        )
    transform = transform_class(**payload)
    normalized = transform.normalized()
    comparison = transform
    if isinstance(transform, GridTextTransform) and not transform.control_points:
        comparison = replace(
            transform,
            control_points=normalized.control_points,
        )
    if comparison != normalized:
        raise ValueError(
            f'persisted {transform_type} transform values must be canonical'
        )
    return normalized


def coerce_text_transform_stack(
    value: Union[
        TextTransformStack,
        Sequence[Union[TextTransform, dict]],
    ],
) -> TextTransformStack:
    """Return one canonical ordered stack and reject the old single payload.

    >>> coerce_text_transform_stack([
    ...     {'transform_type': 'bend', 'bend': 0.5},
    ... ])
    TextTransformStack(transforms=(BendTextTransform(transform_type='bend', bend=0.5),))
    >>> coerce_text_transform_stack({'transform_type': 'bend'})
    Traceback (most recent call last):
    ...
    ValueError: text transform stack must be an ordered list
    """
    if isinstance(value, TextTransformStack):
        return value
    if not isinstance(value, (list, tuple)):
        raise ValueError('text transform stack must be an ordered list')
    return TextTransformStack(tuple(value))


def pt2px(pt, to_int=False) -> float:
    if to_int:
        return int(round(pt * shared.LDPI / 72.0))
    else:
        return pt * shared.LDPI / 72.0


def px2pt(px) -> float:
    return px / shared.LDPI * 72.0


class LineSpacingType(enum.IntEnum):
    Proportional = 0
    Distance = 1


class TextAlignment(enum.IntEnum):
    Left = 0
    Center = 1
    Right = 2


class PunctuationPosition(enum.IntEnum):
    Traditional = 0  # 繁体中文/日文：横排居中 / 竖排居中 (matches old Center=0)
    Simplified = 1  # 简体中文：横排底部 / 竖排右上 (matches old UpperRight=1)


# Deprecated alias — kept for backward compatibility with old serialized data
PunctuationAlignment = PunctuationPosition


fontweight_qt5_to_qt6 = {
    0: 100,
    12: 200,
    25: 300,
    50: 400,
    57: 500,
    63: 600,
    75: 700,
    81: 800,
    87: 900,
}
fontweight_qt6_to_qt5 = {
    100: 0,
    200: 12,
    300: 25,
    400: 50,
    500: 57,
    600: 63,
    700: 75,
    800: 81,
    900: 87,
}

fontweight_pattern = re.compile(r"font-weight:(\d+)", re.DOTALL)


def fix_fontweight_qt(weight: Union[str, int]):

    def _fix_html_fntweight(matched):
        weight = int(matched.group(1))
        return f"font-weight:{fix_fontweight_qt(weight)}"

    if weight is None:
        return None
    if isinstance(weight, int):
        if shared.FLAG_QT6 and weight < 100:
            if weight in fontweight_qt5_to_qt6:
                weight = fontweight_qt5_to_qt6[weight]
        if not shared.FLAG_QT6 and weight >= 100:
            if weight in fontweight_qt6_to_qt5:
                weight = fontweight_qt6_to_qt5[weight]
    if isinstance(weight, str):
        weight = fontweight_pattern.sub(
            lambda matched: _fix_html_fntweight(matched), weight
        )
    return weight


_TEXT_EFFECTS_ABSENT = object()
# 旧字段名仍被管线/既有 UI 当作读写入口，唯一活体是 text_effects 栈
# （__getattribute__/__setattr__ 提供视图）。阶段 C 起阴影/渐变同样迁入栈。
_LEGACY_EFFECT_VIEW_NAMES = {
    "opacity", "stroke_width", "srgb",
    "shadow_radius", "shadow_strength", "shadow_color", "shadow_offset",
    "shadow_include_stroke",
    "gradient_enabled", "gradient_start_color", "gradient_end_color",
    "gradient_angle", "gradient_size",
}


def _legacy_shadow_effect(
    stack: TextEffectStack,
) -> Optional[ShadowEffect]:
    """视图约定的阴影实体：栈中第一张 ShadowEffect 卡。"""
    for effect in stack.effects:
        if isinstance(effect, ShadowEffect):
            return effect
    return None


def _legacy_gradient_fill(
    stack: TextEffectStack,
) -> Optional[TextFillEffect]:
    for effect in stack.effects:
        if isinstance(effect, TextFillEffect) and isinstance(
            effect.paint, LinearGradientPaint
        ):
            return effect
    return None


def _with_shadow(
    stack: TextEffectStack, parameters: dict, *, allow_create: bool
) -> TextEffectStack:
    """按视图语义更新（或按需新建）legacy 阴影卡，返回新栈。

    无卡且写入值全为 legacy 默认（allow_create=False）时不变更，避免
    setShadow 对未配置块凭空建卡。新建卡以 legacy 字段默认值为基
    （strength=1.0、半径=0），enabled 由写入方按"半径>0 且强度>0"的
    legacy 渲染门槛推导。
    """
    shadow = _legacy_shadow_effect(stack)
    if shadow is None:
        if not allow_create:
            return stack
        shadow = ShadowEffect(distance=0.0, angle=0.0)
        effects = stack.effects + (shadow,)
    else:
        effects = stack.effects
    updated = replace(shadow, **parameters)
    if updated is shadow:
        return stack
    new_effects = tuple(
        updated if effect is shadow else effect for effect in effects
    )
    return replace(stack, effects=new_effects)


_SHADOW_DEFAULTS = {
    "shadow_radius": 0.0,
    "shadow_strength": 1.0,
    "shadow_color": [0, 0, 0],
    "shadow_offset": [0.0, 0.0],
}


def _set_shadow_radius(stack: TextEffectStack, value) -> TextEffectStack:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return stack
    if not math.isfinite(value) or value < 0.0:
        value = 0.0
    shadow = _legacy_shadow_effect(stack)
    strength = shadow.opacity if shadow is not None else 1.0
    allow_create = value > 0.0
    return _with_shadow(
        stack,
        {"blur": value, "enabled": bool(value > 0.0 and strength > 0.0)},
        allow_create=allow_create,
    )


def _set_shadow_strength(stack: TextEffectStack, value) -> TextEffectStack:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return stack
    if not math.isfinite(value):
        value = 0.0
    value = min(max(value, 0.0), 1.0)
    shadow = _legacy_shadow_effect(stack)
    blur = shadow.blur if shadow is not None else 0.0
    allow_create = value != _SHADOW_DEFAULTS["shadow_strength"]
    return _with_shadow(
        stack,
        {"opacity": value, "enabled": bool(value > 0.0 and blur > 0.0)},
        allow_create=allow_create,
    )


def _set_shadow_color(stack: TextEffectStack, value) -> TextEffectStack:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    allow_create = value != _SHADOW_DEFAULTS["shadow_color"]
    try:
        paint = SolidPaint(value)
    except (TypeError, ValueError):
        return stack
    return _with_shadow(stack, {"paint": paint}, allow_create=allow_create)


def _set_shadow_offset(stack: TextEffectStack, value) -> TextEffectStack:
    try:
        xoffset, yoffset = (float(v) for v in value)
        if not (math.isfinite(xoffset) and math.isfinite(yoffset)):
            raise ValueError
    except (TypeError, ValueError):
        return stack
    distance = math.hypot(xoffset, yoffset)
    angle = math.degrees(math.atan2(yoffset, xoffset))
    allow_create = [xoffset, yoffset] != _SHADOW_DEFAULTS["shadow_offset"]
    return _with_shadow(
        stack, {"distance": distance, "angle": angle},
        allow_create=allow_create,
    )


def _set_shadow_include_stroke(
    stack: TextEffectStack, value
) -> TextEffectStack:
    # 仅调序，不建卡（无卡时 include_stroke 无从表达）。
    """include_stroke 以卡片顺序表达：True = 阴影卡位于主描边之上。"""
    shadow = _legacy_shadow_effect(stack)
    stroke = primary_stroke(stack)
    if shadow is None or stroke is None:
        return stack
    effects = list(stack.effects)
    shadow_index = next(
        index
        for index, effect in enumerate(effects)
        if effect is shadow
    )
    stroke_index = next(
        index
        for index, effect in enumerate(effects)
        if effect is stroke
    )
    effects.pop(shadow_index)
    if shadow_index < stroke_index:
        stroke_index -= 1
    effects.insert(stroke_index if value else stroke_index + 1, shadow)
    return replace(stack, effects=tuple(effects))


def _legacy_gradient_paint(
    start_color, end_color, angle, size
) -> LinearGradientPaint:
    return LinearGradientPaint(
        stops=(
            GradientStop(0.0, tuple(start_color), 1.0),
            GradientStop(1.0, tuple(end_color), 1.0),
        ),
        angle=angle,
        # legacy gradient_size 是半径系数（跨度 = 2 × size × max(w, h)），
        # 栈 paint.scale 是全跨度系数，故 ×2。
        scale=min(max(2.0 * size, 0.1), 4.0),
    )


def _with_gradient_fill(
    stack: TextEffectStack, paint: LinearGradientPaint
) -> TextEffectStack:
    fill = _legacy_gradient_fill(stack)
    if fill is None:
        new_fill = TextFillEffect(paint=paint)
        return replace(stack, effects=(new_fill,) + stack.effects)
    updated = replace(fill, paint=paint)
    if updated == fill:
        return stack
    new_effects = tuple(
        updated if effect is fill else effect for effect in stack.effects
    )
    return replace(stack, effects=new_effects)


def _set_gradient_enabled(stack: TextEffectStack, value) -> TextEffectStack:
    if value in (True, 1):
        if _legacy_gradient_fill(stack) is not None:
            return stack
        fill = TextFillEffect(
            paint=_legacy_gradient_paint(
                (0, 0, 0), (255, 255, 255), 0.0, 1.0
            )
        )
        return replace(stack, effects=(fill,) + stack.effects)
    fills = tuple(
        effect
        for effect in stack.effects
        if not (
            isinstance(effect, TextFillEffect)
            and isinstance(effect.paint, LinearGradientPaint)
        )
    )
    if fills == stack.effects:
        return stack
    return replace(stack, effects=fills)


def _set_gradient_stop_color(
    stack: TextEffectStack, value, *, start: bool
) -> TextEffectStack:
    fill = _legacy_gradient_fill(stack)
    if fill is None:
        return stack
    if isinstance(value, np.ndarray):
        value = value.tolist()
    try:
        color = tuple(
            int(round(float(channel))) for channel in value
        )
    except (TypeError, ValueError):
        return stack
    if len(color) != 3 or not all(
        math.isfinite(channel) for channel in color
    ):
        return stack
    paint = fill.paint
    stops = list(paint.stops)
    index = 0 if start else len(stops) - 1
    stops[index] = replace(stops[index], color=color)
    return _with_gradient_fill(stack, replace(paint, stops=tuple(stops)))


def _set_gradient_angle(stack: TextEffectStack, value) -> TextEffectStack:
    fill = _legacy_gradient_fill(stack)
    if fill is None:
        return stack
    try:
        angle = float(value)
    except (TypeError, ValueError):
        return stack
    if not math.isfinite(angle):
        return stack
    return _with_gradient_fill(
        stack, replace(fill.paint, angle=angle)
    )


def _set_gradient_size(stack: TextEffectStack, value) -> TextEffectStack:
    fill = _legacy_gradient_fill(stack)
    if fill is None:
        return stack
    try:
        size = float(value)
    except (TypeError, ValueError):
        return stack
    if not math.isfinite(size) or size <= 0.0:
        return stack
    return _with_gradient_fill(
        stack, replace(fill.paint, scale=min(max(2.0 * size, 0.1), 4.0))
    )


def _migrate_legacy_text_effects(payload: dict) -> TextEffectStack:
    """旧数据（无 text_effects 载荷）→ 等价效果栈。

    迁移 opacity/stroke_width/srgb + 阴影/渐变字段本体；无效值逐项告警
    丢弃。卡片顺序（顶→底）＝渐变填充、主描边、阴影（include_stroke
    为真时阴影位于描边之上）。
    """
    stack = TextEffectStack()
    try:
        stack = replace(
            stack,
            overall_opacity=payload.get("opacity", 1.0),
        )
    except (TypeError, ValueError) as error:
        LOGGER.warning(
            "Ignoring invalid legacy text opacity (%s); using 1.0.", error
        )

    stroke_stack = stack
    width = payload.get("stroke_width", 0.0)
    if isinstance(width, bool) or not isinstance(width, (int, float)):
        LOGGER.warning("Ignoring invalid legacy Stroke width %r.", width)
    else:
        width = float(width)
        if not math.isfinite(width) or width < 0.0:
            LOGGER.warning("Ignoring invalid legacy Stroke width %r.", width)
        elif width > 0.0:
            try:
                paint = SolidPaint(payload.get("srgb", (0, 0, 0)))
            except (TypeError, ValueError) as error:
                LOGGER.warning(
                    "Ignoring invalid legacy Stroke color (%s); using black.",
                    error,
                )
                paint = SolidPaint()
            stroke_stack = with_primary_stroke(
                stroke_stack,
                width=width,
                paint=paint,
                position="outside",
            )

    effects: list = []
    gradient_stack = stroke_stack
    if payload.get("gradient_enabled", False) in (True, 1):
        try:
            paint = _legacy_gradient_paint(
                payload.get("gradient_start_color", (0, 0, 0)),
                payload.get("gradient_end_color", (255, 255, 255)),
                float(payload.get("gradient_angle", 0.0) or 0.0),
                float(payload.get("gradient_size", 1.0) or 1.0),
            )
            gradient_stack = replace(
                stroke_stack,
                effects=(TextFillEffect(paint=paint),) + stroke_stack.effects,
            )
        except (TypeError, ValueError) as error:
            LOGGER.warning(
                "Ignoring invalid legacy text Gradient (%s).", error
            )

    shadow = None
    radius = payload.get("shadow_radius", 0.0)
    strength = payload.get("shadow_strength", 1.0)
    if isinstance(radius, bool) or isinstance(strength, bool):
        LOGGER.warning("Ignoring invalid legacy text Shadow flags.")
    elif not isinstance(radius, (int, float)) or not isinstance(
        strength, (int, float)
    ):
        LOGGER.warning("Ignoring invalid legacy text Shadow values.")
    else:
        radius = float(radius)
        strength = float(strength)
        if (
            math.isfinite(radius)
            and math.isfinite(strength)
            and radius > 0.0
            and strength > 0.0
        ):
            try:
                shadow_color = payload.get("shadow_color", (0, 0, 0))
                offset = payload.get("shadow_offset", (0.0, 0.0))
                xoffset, yoffset = (float(v) for v in offset)
                shadow = ShadowEffect(
                    blur=min(max(radius, 0.0), 10.0),
                    opacity=min(max(strength, 0.0), 1.0),
                    paint=SolidPaint(shadow_color),
                    distance=math.hypot(xoffset, yoffset),
                    angle=math.degrees(math.atan2(yoffset, xoffset)),
                )
            except (TypeError, ValueError) as error:
                LOGGER.warning(
                    "Ignoring invalid legacy text Shadow (%s).", error
                )
                shadow = None

    if shadow is not None:
        include_stroke = bool(
            payload.get("shadow_include_stroke", False)
        )
        base_effects = gradient_stack.effects
        # 阴影默认置于最底（所有描边之下）；include_stroke 时置于主描边
        # 之上（描边在阴影之下参与投影源）。
        insert_at = len(base_effects)
        if include_stroke:
            primary = primary_stroke(gradient_stack)
            if primary is not None:
                insert_at = next(
                    index
                    for index, effect in enumerate(base_effects)
                    if effect is primary
                )
        shadow_stack = replace(
            gradient_stack,
            effects=(
                base_effects[:insert_at]
                + (shadow,)
                + base_effects[insert_at:]
            ),
        )
    else:
        shadow_stack = gradient_stack
    return shadow_stack


@nested_dataclass
class FontFormat(Config):
    font_family: str = (
        shared.DEFAULT_FONT_FAMILY
    )  # to always apply shared.DEFAULT_FONT_FAMILY
    font_size: float = 24
    stroke_width: float = 0.0
    frgb: List = field(default_factory=lambda: [0, 0, 0])
    srgb: List = field(default_factory=lambda: [0, 0, 0])
    # 描边色是否手动指定：False（默认）= 自动跟随文字前景色的反色（黑字白边/
    # 白字黑边）；True = 完全按 srgb 手动值渲染（用户自定义。指定轮廓颜色即置位）。
    stroke_color_custom: bool = False
    # Deprecated: 粗体样式已移除，视觉字重完全由 font_weight 承担。字段仅为
    # 旧项目数据兼容保留（text_panel/textitem 镜像写入），不参与样式 diff、
    # 查询谓词与任何 UI。
    bold: bool = False
    underline: bool = False
    strikeout: bool = False
    italic: bool = False
    alignment: int = 0
    vertical: bool = False
    standard_vertical_roman_alignment: bool = True
    font_weight: int = None
    line_spacing: float = 1.2
    letter_spacing: float = 1.15
    ligature_common: str = "default"
    ligature_discretionary: str = "enabled"
    ligature_contextual: str = "default"
    oldstyle_nums: str = "default"
    opacity: float = 1.0
    shadow_radius: float = 0.0
    shadow_strength: float = 1.0
    shadow_color: List = field(default_factory=lambda: [0, 0, 0])
    shadow_offset: List = field(default_factory=lambda: [0.0, 0.0])
    shadow_include_stroke: bool = False
    gradient_enabled: bool = False
    gradient_start_color: List = field(default_factory=lambda: [0, 0, 0])
    gradient_end_color: List = field(default_factory=lambda: [255, 255, 255])
    gradient_angle: float = 0.0
    gradient_size: float = 1.0
    _style_name: str = ""
    line_spacing_type: int = LineSpacingType.Proportional
    # Deprecated: now a global setting (ProgramConfig.punctuation_position).
    # Kept for backward compatibility with old config/textstyles files.
    punctuation_alignment: int = PunctuationPosition.Traditional  # 0 = old Center

    # Direct in-memory owner; persistence stores an ordered list of payloads.
    text_transform: Union[TextTransformStack, List] = field(
        default_factory=TextTransformStack
    )
    glyph_slant_angle: float = 0.0

    # 效果栈唯一活体：opacity/stroke_width/srgb 旧字段名经
    # __getattribute__/__setattr__ 视图直读直写栈（与上游一致）。
    text_effects: Union[TextEffectStack, dict] = _TEXT_EFFECTS_ABSENT

    deprecated_attributes: dict = field(default_factory=lambda: dict())

    def __getattribute__(self, name: str):
        # 旧字段名的活体只在 text_effects 栈；管线/旧 UI 继续按旧名读写。
        if name in _LEGACY_EFFECT_VIEW_NAMES:
            data = object.__getattribute__(self, "__dict__")
            stack = data.get("text_effects")
            if isinstance(stack, TextEffectStack):
                if name == "opacity":
                    return stack.overall_opacity
                stroke = primary_stroke(stack)
                if name == "stroke_width":
                    if (
                        stroke is None
                        or not stroke.enabled
                        or stroke.opacity == 0.0
                    ):
                        return 0.0
                    return stroke.width
                if name == "srgb":
                    return (
                        list(effect_paint_fallback_color(stroke.paint))
                        if stroke is not None
                        else [0, 0, 0]
                    )
                shadow = _legacy_shadow_effect(stack)
                if name == "shadow_radius":
                    return shadow.blur if shadow is not None else 0.0
                if name == "shadow_strength":
                    return shadow.opacity if shadow is not None else 1.0
                if name == "shadow_color":
                    return (
                        list(effect_paint_fallback_color(shadow.paint))
                        if shadow is not None
                        else [0, 0, 0]
                    )
                if name == "shadow_offset":
                    if shadow is None:
                        return [0.0, 0.0]
                    radians = math.radians(shadow.angle)
                    return [
                        math.cos(radians) * shadow.distance,
                        math.sin(radians) * shadow.distance,
                    ]
                if name == "shadow_include_stroke":
                    if shadow is None or stroke is None:
                        return False
                    effects = stack.effects
                    shadow_index = next(
                        index
                        for index, effect in enumerate(effects)
                        if effect is shadow
                    )
                    stroke_index = next(
                        index
                        for index, effect in enumerate(effects)
                        if effect is stroke
                    )
                    return shadow_index < stroke_index
                fill = _legacy_gradient_fill(stack)
                if name == "gradient_enabled":
                    return fill is not None and not fill.is_neutral()
                if name == "gradient_start_color":
                    return (
                        list(fill.paint.stops[0].color)
                        if fill is not None
                        else [0, 0, 0]
                    )
                if name == "gradient_end_color":
                    return (
                        list(fill.paint.stops[-1].color)
                        if fill is not None
                        else [255, 255, 255]
                    )
                if name == "gradient_angle":
                    return fill.paint.angle if fill is not None else 0.0
                if name == "gradient_size":
                    return (
                        fill.paint.scale / 2.0
                        if fill is not None
                        else 1.0
                    )
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        data = object.__getattribute__(self, "__dict__")
        stack = data.get("text_effects")
        if name == "text_effects" and "text_effects" in data:
            if not isinstance(value, TextEffectStack):
                raise TypeError("live text_effects requires TextEffectStack")
        if name in _LEGACY_EFFECT_VIEW_NAMES and isinstance(
            stack, TextEffectStack
        ):
            if name == "opacity":
                object.__setattr__(
                    self,
                    "text_effects",
                    replace(stack, overall_opacity=value),
                )
                return
            if name == "stroke_width":
                object.__setattr__(
                    self,
                    "text_effects",
                    with_primary_stroke(stack, width=value),
                )
                return
            if name == "srgb":
                if isinstance(value, np.ndarray):
                    value = value.tolist()
                parameters = {"paint": SolidPaint(value)}
                if primary_stroke(stack) is None:
                    # 检出/覆盖可能先写入颜色再写宽度；无描边时保持 0 宽。
                    parameters["width"] = 0.0
                object.__setattr__(
                    self,
                    "text_effects",
                    with_primary_stroke(stack, **parameters),
                )
                return
            if name == "shadow_radius":
                new_stack = _set_shadow_radius(stack, value)
            elif name == "shadow_strength":
                new_stack = _set_shadow_strength(stack, value)
            elif name == "shadow_color":
                new_stack = _set_shadow_color(stack, value)
            elif name == "shadow_offset":
                new_stack = _set_shadow_offset(stack, value)
            elif name == "shadow_include_stroke":
                new_stack = _set_shadow_include_stroke(stack, bool(value))
            elif name == "gradient_enabled":
                new_stack = _set_gradient_enabled(stack, value)
            elif name == "gradient_start_color":
                new_stack = _set_gradient_stop_color(
                    stack, value, start=True
                )
            elif name == "gradient_end_color":
                new_stack = _set_gradient_stop_color(
                    stack, value, start=False
                )
            elif name == "gradient_angle":
                new_stack = _set_gradient_angle(stack, value)
            else:
                new_stack = _set_gradient_size(stack, value)
            if new_stack is not stack:
                object.__setattr__(self, "text_effects", new_stack)
            return
        object.__setattr__(self, name, value)

    @property
    def size_pt(self):
        return max(px2pt(self.font_size), 1.0)

    def __post_init__(self):
        da = self.deprecated_attributes
        if len(da) > 0:
            if "size" in da:
                self.font_size = pt2px(da["size"])
            if "weight" in da:
                self.font_weight = da["weight"]
            if "family" in da:
                self.font_family = da["family"]

        self.font_weight = fix_fontweight_qt(self.font_weight)
        # _style_name 是派生显示缓存：历史 bug 曾把 (名,字重,斜体) 元组写进
        # 来并随项目落盘（JSON 数组），加载时归一为空串交渲染端字重匹配兜底
        if not isinstance(self._style_name, str):
            self._style_name = ""
        # bold 字段级折算：旧数据 bold=True 且字重未设/低于粗体时折算进
        # 真值 font_weight，随后 bold 复位 False（真值化后不再参与渲染）
        if self.bold:
            weight = self.font_weight if isinstance(self.font_weight, int) else 0
            self.font_weight = max(weight, 700)
            self.bold = False
        self.font_size = max(float(self.font_size), 1.0)
        if not isinstance(self.text_transform, TextTransformStack):
            if isinstance(self.text_transform, (list, tuple)):
                transforms = []
                for index, value in enumerate(self.text_transform):
                    try:
                        transforms.append(coerce_text_transform(value))
                    except (TypeError, ValueError) as error:
                        LOGGER.warning(
                            'Ignoring invalid text transform config at index '
                            '%s (%s).',
                            index,
                            error,
                        )
                self.text_transform = TextTransformStack(tuple(transforms))
            else:
                LOGGER.warning(
                    'Ignoring invalid text transform stack (%r); '
                    'using an empty transform stack.',
                    self.text_transform,
                )
                self.text_transform = TextTransformStack()
        try:
            self.glyph_slant_angle = normalize_text_transform_value(
                self.glyph_slant_angle,
                TEXT_TRANSFORM_GLYPH_SLANT_MIN,
                TEXT_TRANSFORM_GLYPH_SLANT_MAX,
            )
        except ValueError as error:
            LOGGER.warning(
                'Ignoring invalid Glyph Slant config (%s); using 0.',
                error,
            )
            self.glyph_slant_angle = 0.0

        raw_text_effects = self.__dict__.get(
            "text_effects", _TEXT_EFFECTS_ABSENT
        )
        if raw_text_effects is not _TEXT_EFFECTS_ABSENT:
            text_effects = coerce_text_effect_stack(raw_text_effects)
        else:
            # 旧载荷：从 legacy 字段迁移等价效果栈（含阴影/渐变）。
            text_effects = _migrate_legacy_text_effects(self.__dict__)
        object.__setattr__(self, "text_effects", text_effects)
        for name in _LEGACY_EFFECT_VIEW_NAMES:
            self.__dict__.pop(name, None)
        self.deprecated_attributes = {}

    def to_serializable_dict(self) -> dict:
        """Return config/project data with a typed transform payload."""
        serialized = vars(self).copy()
        serialized['text_transform'] = [
            asdict(transform) for transform in self.text_transform
        ]
        # 效果栈序列化 + 旧字段兼容视图双写（旧键仅供旧版读取；加载时
        # text_effects 载荷权威，本体的 shadow_*/gradient_* 字段不落盘）。
        serialized['text_effects'] = self.text_effects.to_serializable_dict()
        serialized['opacity'] = self.text_effects.overall_opacity
        stroke = primary_stroke(self.text_effects)
        compatible_stroke = stroke is not None and not stroke.is_neutral()
        serialized['stroke_width'] = (
            stroke.width if compatible_stroke else 0.0
        )
        serialized['srgb'] = (
            list(effect_paint_fallback_color(stroke.paint))
            if compatible_stroke
            else [0, 0, 0]
        )
        shadow = _legacy_shadow_effect(self.text_effects)
        shadow_on = shadow is not None and shadow.enabled
        serialized['shadow_radius'] = shadow.blur if shadow_on else 0.0
        serialized['shadow_strength'] = (
            shadow.opacity if shadow_on else 1.0
        )
        serialized['shadow_color'] = (
            list(effect_paint_fallback_color(shadow.paint))
            if shadow_on
            else [0, 0, 0]
        )
        radians = math.radians(shadow.angle) if shadow_on else 0.0
        distance = shadow.distance if shadow_on else 0.0
        serialized['shadow_offset'] = [
            math.cos(radians) * distance,
            math.sin(radians) * distance,
        ]
        serialized['shadow_include_stroke'] = self.shadow_include_stroke
        fill = _legacy_gradient_fill(self.text_effects)
        gradient_on = fill is not None and not fill.is_neutral()
        serialized['gradient_enabled'] = gradient_on
        serialized['gradient_start_color'] = (
            list(fill.paint.stops[0].color) if gradient_on else [0, 0, 0]
        )
        serialized['gradient_end_color'] = (
            list(fill.paint.stops[-1].color)
            if gradient_on
            else [255, 255, 255]
        )
        serialized['gradient_angle'] = fill.paint.angle if gradient_on else 0.0
        serialized['gradient_size'] = (
            fill.paint.scale / 2.0 if gradient_on else 1.0
        )
        return serialized

    def deepcopy(self):
        fmt_copyed: FontFormat = None
        fmt_copyed = copy.deepcopy(self)
        return fmt_copyed

    def merge(self, target: Config, compare: bool = False):
        if id(self) == id(target):
            return set()
        tgt_keys = target.annotations_set()
        updated_keys = set()
        has_effect_stack = isinstance(
            getattr(target, "text_effects", None), TextEffectStack
        )
        if has_effect_stack:
            old_opacity = self.opacity
            old_width = self.stroke_width
            old_color = self.srgb
            effects_changed = self.text_effects != target.text_effects
            if not compare or effects_changed:
                self.text_effects = copy.deepcopy(target.text_effects)
            if compare and effects_changed:
                updated_keys.add("text_effects")
                if old_opacity != target.opacity:
                    updated_keys.add("opacity")
                if old_width != target.stroke_width:
                    updated_keys.add("stroke_width")
                if old_color != target.srgb:
                    updated_keys.add("srgb")
            tgt_keys -= _LEGACY_EFFECT_VIEW_NAMES
        tgt_keys.discard("text_effects")
        for key in tgt_keys:
            if not hasattr(self, key):
                continue
            if compare:
                if key != "_style_name":
                    if isinstance(target[key], np.ndarray):
                        is_diff = np.any(self[key] != target[key])
                    else:
                        is_diff = self[key] != target[key]
                    if is_diff:
                        self.update(key, copy.deepcopy(target[key]))
                        updated_keys.add(key)
            else:
                self.update(key, copy.deepcopy(target[key]))
        return updated_keys

    def foreground_color(self):
        return [max(0, min(255, int(round(x)))) for x in self.frgb]

    def stroke_color(self):
        return [max(0, min(255, int(round(x)))) for x in self.srgb]

    def effective_stroke_color(self, *, auto_follow: bool = True):
        """实际渲染的描边色：自动跟随文字前景反色，手动指定则按 srgb。

        黑字→白边、白字→黑边（通道反色）；stroke_color_custom 为 True 时按
        手动 srgb 渲染。auto_follow 为全局开关（默认开启）：关闭后未手动指定
        的块也按存档 srgb 渲染，不再随字体颜色联动。描边宽为 0 时颜色无视觉
        影响，但返回值为合法色。
        """
        if self.stroke_color_custom:
            return self.stroke_color()
        if auto_follow:
            return [
                max(0, min(255, 255 - int(round(x))))
                for x in self.foreground_color()
            ]
        return self.stroke_color()
