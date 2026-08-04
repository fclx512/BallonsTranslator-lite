"""Stage 4 dependency stubs for the text engine controller port.

Stage 2 ports ``TextItemGeometryController`` (``ui/text_engine/geometry.py``)
with every non-linear branch routed through this module:

- Neutral state (empty transform stack + ``glyph_slant_angle == 0``) returns
  identity / no-ops, so the ported controller behaves identically to the
  pre-port local implementation (zero regression).
- Any non-linear branch (Grid/Projective transforms, surface rendering,
  Glyph Slant) either warns and falls back to identity (recoverable) or
  raises ``NotImplementedError``. None of these branches are reachable in
  Stage 2's neutral runtime.

Stage 4 replaces this module with the upstream ``transforms/*`` /
``rendering/*`` implementations; ``geometry.py`` only needs its import
block updated.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from qtpy.QtGui import QTransform

from utils.fontformat import (
    ProjectiveTextTransform,
    TextTransformStack,
    coerce_text_transform_stack,
)

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transforms: mapping / registry（阶段 4 移植）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledTextTransform:
    """Aligned with upstream ``transforms/mapping.CompiledTextTransform``.

    Stage 2 only ever holds the identity compilation; ``geometry_key`` is
    compared in ``refresh_compiled_geometry`` to detect geometry changes.
    """

    stack: TextTransformStack
    native_matrix: QTransform
    surface_mapper: Optional["CompositeTextTransformMapper"] = None
    stages: tuple = ()

    @property
    def geometry_key(self):
        if self.is_identity:
            return None
        matrix = self.native_matrix
        coefficients = (
            matrix.m11(), matrix.m12(), matrix.m13(),
            matrix.m21(), matrix.m22(), matrix.m23(),
            matrix.m31(), matrix.m32(), matrix.m33(),
        )
        mapper_key = (
            self.surface_mapper.geometry_key
            if self.surface_mapper is not None
            else None
        )
        return (coefficients, mapper_key)

    @property
    def is_identity(self) -> bool:
        return self.native_matrix.isIdentity() and self.surface_mapper is None

    @property
    def has_projective_mapping(self) -> bool:
        return any(
            isinstance(getattr(stage, 'transform', None), ProjectiveTextTransform)
            and not stage.transform.is_neutral()
            for stage in self.stages
        )

    @property
    def needs_local_handle_frames(self) -> bool:
        return self.has_projective_mapping

    @property
    def requires_no_cache(self) -> bool:
        return self.has_projective_mapping

    @property
    def requires_custom_resize(self) -> bool:
        return self.has_projective_mapping or self.surface_mapper is not None


def compile_text_transform_stack(stack, logical_rect, source_rect, vertical):
    """Compile a transform stack (neutral-only until Stage 4)."""
    if not isinstance(stack, TextTransformStack):
        stack = coerce_text_transform_stack(stack)
    if stack.is_neutral():
        return CompiledTextTransform(stack, QTransform())
    LOGGER.warning(
        'Non-neutral text transform stack cannot be compiled until '
        'Stage 4; falling back to identity (%r).',
        stack,
    )
    return CompiledTextTransform(stack, QTransform())


def compensated_native_transform_matrix(
    native_transform,
    transform_pivot,
    rotation_angle,
    rotation_pivot=None,
):
    """Return the compensated native matrix (identity until Stage 4)."""
    if native_transform.isIdentity():
        return QTransform()
    LOGGER.warning(
        'compensated_native_transform_matrix is a Stage 4 stub; '
        'returning identity for %r.',
        native_transform,
    )
    return QTransform()


def grid_transform_stage(*args, **kwargs):
    raise NotImplementedError(
        'grid_transform_stage is ported in Stage 4 '
        '(grid control-point editing).'
    )


class CompositeTextTransformMapper:
    """Placeholder: upstream ``transforms/mapping.CompositeTextTransformMapper``."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'CompositeTextTransformMapper is ported in Stage 4.'
        )


# ---------------------------------------------------------------------------
# Rendering（阶段 4 移植）
# ---------------------------------------------------------------------------


class NonlinearTextSurfaceRenderer:
    """Placeholder: upstream ``rendering/surface.NonlinearTextSurfaceRenderer``."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'NonlinearTextSurfaceRenderer is ported in Stage 4.'
        )


class GlyphSlantLayoutRenderer:
    """Placeholder: upstream ``rendering/glyph_slant.GlyphSlantLayoutRenderer``."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'GlyphSlantLayoutRenderer is ported in Stage 4.'
        )


class EffectRasterAllocationError(RuntimeError):
    """Surface raster allocation failure (upstream ``rendering/raster``)."""


# Raster boundary failures retried by paint_item (empty until Stage 4).
RASTER_BOUNDARY_FAILURES = ()


# ---------------------------------------------------------------------------
# TextEffectRenderer 占位（阶段 3 移植）
# ---------------------------------------------------------------------------


class TextEffectRendererStub:
    """Minimal effect renderer stub.

    ``geometry.paint_item`` always routes the neutral path through
    ``item.effect_renderer.paint_item(...)``; this stub forwards directly to
    the base paint. Replaced by the upstream effect renderer in Stage 3.
    """

    def __init__(self, item):
        self.item = item
        self.export_render = False
        self.export_error = None
        self.background_pixmap = None

    def paint_item(self, painter, option, widget, base_paint):
        base_paint(painter, option, widget)

    def release_caches(self):
        pass

    def _warn_effect_allocation_once(self, failure):
        LOGGER.warning('Effect allocation failure (Stage 3 stub): %s', failure)

    def _effect_flags(self):
        return ()

    def _mark_effect_cache_dirty(self):
        pass

    def _update_effect_padding(self):
        return False

    def _refresh_gradient_geometry(self):
        pass

    def _text_transform_is_neutral(self):
        return True

    def finalize_neutral_cache(self):
        pass

    def surface_cache_state(self):
        return (0, False)

    _on_glyph_raster_failure = None
