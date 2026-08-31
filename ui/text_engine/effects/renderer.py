"""Typed effect-stack rendering for the engine text item.

Port of upstream ``ballontranslator/ui/text_engine/effects/renderer.py``
(``TextEffectRenderer``) onto the fork's tier raster pipeline.  Untouched
methods stay byte-identical to upstream; the deviations are:

- Postponed card kinds are not ported (计划第六节"后续待办"): Filter, Image/
  texture, AI generation and the block alpha mask.  Their methods and cache
  namespaces are removed; ``_ordered_surface_nodes`` skips those effect
  kinds defensively should data ever contain them.  Re-port together with
  the matching panel cards.
- ``paint_item`` keeps the fork's neutral composition order: a neutral
  transform without a completed foreground forwards to the host paint and
  the host (``textitem._paint_native``) consumes ``background_pixmap``
  before text via SourceOver (page-switch stale-clip workaround); the
  renderer must not composite that surface itself or the host order would
  double-draw.  ``ensure_host_background`` refreshes the host-consumed cache
  at the active device scale, preserving the unified tier pipeline for
  neutral blocks (blurry-outline fix, 5a13c1f).
- ``repaint_background`` resolves ``render_scale=None`` through the first
  visible host view so bare-callers stay crisp on HiDPI/zoomed canvases, and
  honours ``pcfg.show_decorations_during_drag`` during reshape drags.
- ``_paint_cloned_document_stroke`` rebuilds the clone through HTML and
  re-applies the pcfg punctuation layout parameters (fork layouts carry
  them as plain attributes) plus the legacy letter-spacing view.
- ``boundingRect`` uses ``geometry_controller.source_rect()``: the fork has
  no source ink-overhang plumbing (upstream ``source_paint_rect``).
- ``get_text_gradient``/``_refresh_gradient_geometry`` and the transient
  gradient FormatRange mechanism are gone: gradients are TextFillEffects in
  the stack and render through the completed foreground.
- Legacy field reads go through the ``fontformat`` views; ``_stroke_width``
  and friends read the stack, never ``fontformat.stroke_width`` directly.
"""

import math
from typing import Callable, Dict, Optional, Tuple

import cv2
import numpy as np
from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextLayout,
)
from qtpy.QtWidgets import QStyle, QStyleOptionGraphicsItem, QWidget

from utils.fontformat import pt2px
from utils.logger import logger as LOGGER
from utils.text_effects import (
    FilterEffect,
    GlowEffect,
    HollowEffect,
    ImageEffect,
    LinearGradientPaint,
    ShadowEffect,
    StrokeEffect,
    TextEffect,
    TextEffectStack,
    TextFillEffect,
    effect_phase,
    effect_paint_fallback_color,
    hollow_effect,
    primary_stroke,
)
from ...misc import ndarray2pixmap, pixmap2ndarray
from ..horizontal_layout import HorizontalTextDocumentLayout
from ..vertical_layout import (
    VerticalTextDocumentLayout as EngineVerticalTextDocumentLayout,
)
from .blend import CUSTOM_BLEND_MODES, composite_custom_blend_rgba
from .paint import colorize_effect_paint_rgba
from ..rendering.glyph import (
    GLYPH_DILATED_STROKE_FORMAT_PROPERTY,
    GLYPH_FEEDBACK_ONLY_FORMAT_PROPERTY,
    GLYPH_STROKE_FORMAT_PROPERTY,
)
from .shadow import render_glow_alpha, render_shadow_alpha
from ..rendering.raster import (
    EFFECT_CACHE_MAX_BYTES,
    EFFECT_CACHE_MAX_DIMENSION,
    EFFECT_CACHE_MAX_PIXELS,
    EFFECT_CACHE_MAX_SCALE,
    EFFECT_RASTER_FAILURES,
    EFFECT_RASTER_GUARD,
    EFFECT_TILE_MAX_EDGE,
    RASTER_BOUNDARY_FAILURES,
    EffectRasterAllocationError,
    EffectRasterPlan,
    plan_effect_raster,
    quality_raster_request,
)


STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY = 0x100000 + 1241
_STROKE_ALIGNMENT_RANGE_LENGTH = 0x7FFFFFFF
# Glyph Slant writes vector paths into effect pixmaps, not native text.
_VECTOR_EFFECT_RENDER_HINTS = (
    QPainter.RenderHint.Antialiasing
    | QPainter.RenderHint.TextAntialiasing
)
_BLEND_COMPOSITION_MODES = {
    'normal': QPainter.CompositionMode.CompositionMode_SourceOver,
    'darken': QPainter.CompositionMode.CompositionMode_Darken,
    'multiply': QPainter.CompositionMode.CompositionMode_Multiply,
    'color_burn': QPainter.CompositionMode.CompositionMode_ColorBurn,
    'lighten': QPainter.CompositionMode.CompositionMode_Lighten,
    'screen': QPainter.CompositionMode.CompositionMode_Screen,
    'color_dodge': QPainter.CompositionMode.CompositionMode_ColorDodge,
}





def _decorations_visible_during_drag() -> bool:
    """Whether stroke/shadow stay visible while dragging a text block.

    Hidden by default: decorations reappear on release, and drags stay at
    native-text frame rate.
    """
    from utils.config import pcfg

    return pcfg.show_decorations_during_drag



class _EffectRasterState:
    """Allocate raster/cache state only after an effect needs it.

    >>> _EffectRasterState().cache_generation
    0
    """

    def __init__(self) -> None:
        self.cache_generation = 0
        self.cache_rendered_generation = -1
        self.cache_dirty = False
        self.tile_cache = {}
        self.allocation_warning_generation = -1
        self.export_error = None
        self.in_graphics_paint = False
        self.capturing_surface = False
        self.surface_raster_error = None
        self.force_tiles = False
        self.direct_stroke = False
        self.background_pixmap = None
        self.background_pixmap_scale = None
        self.cache_input_key = None
        # Effect paint does not change the canonical glyph pixels. Retain at
        # most the same two full/tile source captures across paint previews.
        self.effect_source_cache = {}
        # Stroke paint and opacity consume, but do not change, native outline
        # coverage. Keep the same bounded full/tile working set.
        self.positioned_stroke_coverage_cache: Dict[
            tuple, np.ndarray
        ] = {}



class _EffectRasterField:
    """Descriptor keeping raster-only fields lazy at existing call sites."""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return getattr(instance._raster_state(), self.name)

    def __set__(self, instance, value):
        setattr(instance._raster_state(), self.name, value)



class TextEffectRenderer:
    """Own all effect cache state and transformed effect rendering.

    >>> hasattr(TextEffectRenderer, 'repaint_background')
    True
    """

    cache_generation = _EffectRasterField()
    cache_rendered_generation = _EffectRasterField()
    cache_dirty = _EffectRasterField()
    tile_cache = _EffectRasterField()
    allocation_warning_generation = _EffectRasterField()
    export_error = _EffectRasterField()
    in_graphics_paint = _EffectRasterField()
    capturing_surface = _EffectRasterField()
    surface_raster_error = _EffectRasterField()
    force_tiles = _EffectRasterField()
    direct_stroke = _EffectRasterField()

    def __init__(self, item) -> None:
        self.item = item
        self._effect_raster_state = None
        self._preview_effect_raster_state = None
        self._export_effect_raster_state = None
        self._export_active = False
        self.preview = None
        self.faster_preview = False
        self._render_stroke = None
        self._outline_only_stroke = False
        self._native_stroke_alignment = False
        self.refreshing_effect_padding = False

    def _raster_state(self) -> _EffectRasterState:
        if self._export_active:
            state = self._export_effect_raster_state
            if state is None:
                state = _EffectRasterState()
                self._export_effect_raster_state = state
            return state
        preview = self._uses_preview_cache_namespace()
        state = (
            self._preview_effect_raster_state
            if preview
            else self._effect_raster_state
        )
        if state is None:
            state = _EffectRasterState()
            if preview:
                self._preview_effect_raster_state = state
            else:
                self._effect_raster_state = state
        return state

    def _peek_raster_state(self) -> Optional[_EffectRasterState]:
        if self._export_active:
            return self._export_effect_raster_state
        if self._uses_preview_cache_namespace():
            return self._preview_effect_raster_state
        return self._effect_raster_state

    def _drop_active_raster_state(self) -> None:
        if self._export_active:
            self._export_effect_raster_state = None
            return
        if self._uses_preview_cache_namespace():
            self._preview_effect_raster_state = None
        else:
            self._effect_raster_state = None

    @property
    def background_pixmap(self):
        state = self._peek_raster_state()
        return None if state is None else state.background_pixmap

    @background_pixmap.setter
    def background_pixmap(self, pixmap) -> None:
        state = self._peek_raster_state()
        if state is None and pixmap is None:
            return
        self._raster_state().background_pixmap = pixmap

    @property
    def background_pixmap_scale(self):
        state = self._peek_raster_state()
        return None if state is None else state.background_pixmap_scale

    @background_pixmap_scale.setter
    def background_pixmap_scale(self, scale) -> None:
        state = self._peek_raster_state()
        if state is None and scale is None:
            return
        self._raster_state().background_pixmap_scale = scale

    def surface_cache_state(self) -> Tuple[Tuple[str, int], bool]:
        """Return settled final-warp inputs without allocating effect state."""
        stale = self._invalidate_stale_active_raster_state()
        if stale and not self._export_active and any(self._effect_flags()):
            # The nonlinear cache key includes the completed effect pixmap.
            # Settle it before geometry snapshots that key.
            self.repaint_background()
        export = self._export_active
        preview = self._uses_preview_cache_namespace()
        state = self._peek_raster_state()
        if state is None:
            namespace = 'export' if export else (
                'preview' if preview else 'committed'
            )
            return (namespace, 0), export
        return (
            (
                'export'
                if export
                else ('preview' if preview else 'committed'),
                state.cache_generation,
            ),
            export,
        )

    @property
    def export_render(self) -> bool:
        return self._export_active

    def canonical_text_effects(self) -> TextEffectStack:
        return self.item.blk.fontformat.text_effects

    def effective_text_effects(self) -> TextEffectStack:
        return (
            self.preview
            if self.preview is not None
            else self.canonical_text_effects()
        )

    def has_preview(self) -> bool:
        return self.preview is not None

    def uses_preview_surface(self) -> bool:
        """Return whether preview changes source-surface pixels or geometry."""
        return self._uses_preview_cache_namespace()

    def uses_faster_preview_surface(self) -> bool:
        """Return whether an effect-stack preview selected the 0.5x path."""
        return self.faster_preview and self._effect_preview_changes_pixels()

    def has_active_effects(self) -> bool:
        return self.effective_text_effects().has_active_effects

    def has_raster_effects(self) -> bool:
        """Return whether strict export must own the complete effect output."""
        return (
            any(self._effect_flags())
            or self._renders_completed_foreground()
        )

    def has_generated_effect_layers(self) -> bool:
        """Return whether font/geometry changes invalidate generated layers."""
        return any(self._effect_flags())

    def surface_semantic_state(self) -> tuple:
        """Return effect values that change completed source-surface pixels."""
        return (self._surface_effect_values(self.effective_text_effects()),)

    def _surface_effect_values(
        self, stack: TextEffectStack
    ) -> Tuple[TextEffect, ...]:
        """Return the stack values that own completed-surface pixels."""
        return stack.effects

    def _effect_preview_changes_pixels(self) -> bool:
        return bool(
            self.preview is not None
            and self.preview.effects
            != self.canonical_text_effects().effects
        )

    def _uses_preview_cache_namespace(self) -> bool:
        return self._effect_preview_changes_pixels()

    def _active_strokes(
        self, stack: Optional[TextEffectStack] = None
    ) -> Tuple[StrokeEffect, ...]:
        active = self.effective_text_effects() if stack is None else stack
        return tuple(
            effect
            for effect in active.effects
            if isinstance(effect, StrokeEffect) and not effect.is_neutral()
        )

    def _active_text_fills(
        self, stack: Optional[TextEffectStack] = None
    ) -> Tuple[TextFillEffect, ...]:
        active = self.effective_text_effects() if stack is None else stack
        return tuple(
            effect
            for effect in reversed(active.effects)
            if isinstance(effect, TextFillEffect)
            and not effect.is_neutral()
        )

    def _ordered_surface_nodes(
        self, *, target_stroke: bool = True
    ) -> Tuple[Tuple[int, TextEffect], ...]:
        """Return visible stack nodes in bottom-to-top execution order.

        Text Fill is the permanent canonical face and Hollow is a structural
        modifier. Generated layers retain their canonical geometry/source;
        only their composition relative to Filters follows global card order.

        >>> hasattr(TextEffectRenderer, '_ordered_surface_nodes')
        True
        """
        hollow = self._hollow_enabled()
        nodes = []
        for index, effect in reversed(tuple(enumerate(
            self.effective_text_effects().effects
        ))):
            if isinstance(effect, (ImageEffect, FilterEffect)):
                # Postponed card kinds (计划第六节): no data path creates
                # them yet; skip defensively instead of resolving assets.
                continue
            if isinstance(effect, StrokeEffect):
                if (
                    target_stroke
                    and not effect.is_neutral()
                ):
                    nodes.append((index, effect))
                continue
            if isinstance(effect, (ShadowEffect, GlowEffect)):
                if effect.is_neutral():
                    continue
                if hollow and effect_phase(effect) == 'interior':
                    continue
                nodes.append((index, effect))
        return tuple(nodes)

    def _retained_strokes(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> Tuple[StrokeEffect, ...]:
        retained = (
            self._ordered_surface_nodes(strict_assets=False)
            if nodes is None
            else nodes
        )
        return tuple(
            effect
            for _index, effect in retained
            if isinstance(effect, StrokeEffect)
        )

    def _retained_phase_effects(
        self,
        phase: str,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> Tuple[TextEffect, ...]:
        if phase not in {'exterior', 'interior'}:
            raise ValueError('generated phase must be exterior or interior')
        retained = (
            self._ordered_surface_nodes(strict_assets=False)
            if nodes is None
            else nodes
        )
        return tuple(
            effect
            for _index, effect in retained
            if isinstance(effect, (ShadowEffect, GlowEffect))
            and effect_phase(effect) == phase
        )

    def _stroke_sources_for_nodes(
        self,
        nodes: Tuple[Tuple[int, TextEffect], ...],
    ) -> Tuple[StrokeEffect, ...]:
        """Return painted Strokes plus canonical exterior dependencies."""
        if self._retained_phase_effects('exterior', nodes):
            return self._active_strokes()
        return self._retained_strokes(nodes)

    def _hollow_enabled(
        self, stack: Optional[TextEffectStack] = None
    ) -> bool:
        active = self.effective_text_effects() if stack is None else stack
        hollow = hollow_effect(active)
        return hollow is not None and not hollow.is_neutral()

    def _effect_cache_input_key(
        self, stack: Optional[TextEffectStack] = None
    ) -> tuple:
        active = self.effective_text_effects() if stack is None else stack
        rect = self.boundingRect()
        layout_generation = getattr(self.layout, 'layout_generation', 0)
        layout_render_key = (
            None
            if self.geometry_controller.layout_renderer is None
            else self.geometry_controller.layout_renderer.render_cache_key()
        )
        return (
            self._surface_effect_values(active),
            self.document().revision(),
            layout_generation,
            layout_render_key,
            self.geometry_controller.effective(),
            self.fontformat.vertical,
            (
                rect.x(), rect.y(), rect.width(), rect.height()
            ),
        )

    @staticmethod
    def _effect_cache_semantic_key(cache_key: tuple) -> tuple:
        layout_render_key = cache_key[4]
        if isinstance(layout_render_key, tuple) and layout_render_key:
            layout_render_key = layout_render_key[1:]
        return (
            cache_key[0],
            cache_key[1],
            cache_key[2],
            layout_render_key,
        ) + cache_key[5:]

    def _effect_source_cache_key(
        self,
        surface_rect: QRectF,
        render_scale: float,
    ) -> tuple:
        """Describe only inputs that can change canonical source pixels.

        >>> callable(TextEffectRenderer._effect_source_cache_key)
        True
        """
        document = self.document()
        layout = self.layout
        layout_renderer = self.geometry_controller.layout_renderer
        layout_render_key = (
            None
            if layout_renderer is None
            else layout_renderer.render_cache_key()
        )
        logical_rect = self.logical_unpadded_rect()
        source_rect = self.boundingRect()
        return (
            document.revision(),
            getattr(layout, 'layout_generation', 0),
            layout_render_key,
            self.geometry_controller.effective(),
            self.fontformat.vertical,
            self._native_stroke_alignment,
            (
                logical_rect.x(), logical_rect.y(),
                logical_rect.width(), logical_rect.height(),
            ),
            (
                source_rect.x(), source_rect.y(),
                source_rect.width(), source_rect.height(),
            ),
            (
                surface_rect.x(), surface_rect.y(),
                surface_rect.width(), surface_rect.height(),
            ),
            float(render_scale),
        )

    @staticmethod
    def _copy_source_caches(
        source: Optional[_EffectRasterState],
        target: _EffectRasterState,
    ) -> None:
        if source is not None:
            target.effect_source_cache.update(source.effect_source_cache)
            target.positioned_stroke_coverage_cache.update(
                source.positioned_stroke_coverage_cache
            )

    def _promotable_preview_state(
        self, stack: TextEffectStack
    ) -> Optional[_EffectRasterState]:
        state = self._preview_effect_raster_state
        if (
            self.faster_preview
            or state is None
            or state.background_pixmap is None
            or state.cache_dirty
            or state.cache_rendered_generation != state.cache_generation
            or state.cache_input_key != self._effect_cache_input_key(stack)
        ):
            return None
        rect = self.boundingRect()
        plan = plan_effect_raster(
            rect.width(), rect.height(), quality_raster_request(1.0)
        )
        if (
            plan.mode != 'full'
            or state.background_pixmap_scale < plan.tier
        ):
            return None
        return state

    def _raster_request(self, requested_scale: float) -> float:
        if (
            self.faster_preview
            and not self._export_active
            and self._effect_preview_changes_pixels()
        ):
            return 0.5
        return quality_raster_request(requested_scale)

    def set_faster_preview(self, enabled: bool) -> bool:
        """Choose the existing half-resolution live effect preview path."""
        enabled = bool(enabled)
        if self.faster_preview == enabled:
            return False
        self.faster_preview = enabled
        if self._effect_preview_changes_pixels():
            self._mark_effect_cache_dirty()
            self.item.update()
        return True

    def _invalidate_stale_active_raster_state(self) -> bool:
        state = self._peek_raster_state()
        if (
            state is not None
            and (not self.pre_editing or self._export_active)
            and not state.cache_dirty
            and state.cache_rendered_generation == state.cache_generation
            and state.cache_input_key != self._effect_cache_input_key()
        ):
            self._mark_effect_cache_dirty()
            return True
        return False

    def _current_stroke(self) -> Optional[StrokeEffect]:
        if self._render_stroke is not None:
            return self._render_stroke
        return primary_stroke(self.effective_text_effects())

    def _stroke_width(self) -> float:
        stroke = self._current_stroke()
        if stroke is None:
            return 0.0
        # Position clips the same historical native outline; it does not
        # redefine the saved width as an outside-only radius.
        return stroke.width

    def _all_strokes_vector_compatible(
        self,
        strokes: Optional[Tuple[StrokeEffect, ...]] = None,
    ) -> bool:
        active = self._retained_strokes() if strokes is None else strokes
        return all(
            stroke.position == 'center'
            and stroke.blend_mode == 'normal'
            and not isinstance(stroke.paint, LinearGradientPaint)
            for stroke in active
        )

    def _has_inside_strokes(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> bool:
        return any(
            stroke.position == 'inside'
            for stroke in self._retained_strokes(nodes)
        )

    def _paint_strokes(
        self, painter: QPainter, paint: Callable[[], None]
    ) -> None:
        previous = self._render_stroke
        try:
            # The first card is topmost, so paint semantic order back-to-front.
            for stroke in reversed(self._active_strokes()):
                self._render_stroke = stroke
                painter.save()
                try:
                    painter.setOpacity(painter.opacity() * stroke.opacity)
                    paint()
                finally:
                    painter.restore()
        finally:
            self._render_stroke = previous

    @property
    def fontformat(self):
        return self.item.fontformat

    @property
    def layout(self):
        return self.item.layout

    @property
    def geometry_controller(self):
        return self.item.geometry_controller

    @property
    def repainting(self):
        return self.item.repainting

    @repainting.setter
    def repainting(self, value):
        # Formatting and effect rendering share this reentrancy guard.
        self.item.repainting = value

    @property
    def reshaping(self):
        return self.item.reshaping

    @property
    def pre_editing(self):
        return self.item.pre_editing

    @property
    def stroke_qcolor(self):
        stroke = self._current_stroke()
        if stroke is None:
            return self.item.stroke_qcolor
        return QColor(*effect_paint_fallback_color(stroke.paint))

    @property
    def idx(self):
        return self.item.idx

    def document(self):
        return self.item.document()

    def boundingRect(self):
        # Fork: no source ink-overhang plumbing (upstream source_paint_rect).
        if self.geometry_controller.uses_surface_warp():
            return self.geometry_controller.source_rect()
        return self.item.boundingRect()

    def logical_unpadded_rect(self):
        return self.item.logical_unpadded_rect()

    def padding(self):
        return self.item.padding()

    def setPadding(self, padding):
        return self.item.setPadding(padding)

    def update(self):
        self.item.update()

    def _text_transform_is_neutral(self):
        # A final surface warp still consumes source-local effects exactly
        # once. Active effects around Glyph Slant must keep the
        # transform-aware source path so their silhouette stays slanted.
        if self.geometry_controller.uses_surface_warp():
            return not (
                self._has_layout_distortion()
                and any(self._effect_flags())
            )
        return self.item._text_transform_is_neutral()

    def _has_layout_distortion(self) -> bool:
        return self.geometry_controller.has_layout_distortion()

    def clear_cached_surface(self) -> None:
        self.background_pixmap = None
        self.background_pixmap_scale = None

    def requires_no_item_cache(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> bool:
        """Let the effect raster cache see the actual paint-device scale."""
        return any(self._effect_flags(nodes))

    def release_caches(self) -> None:
        """Release every item-owned raster cache before page removal."""
        for state in (
            self._effect_raster_state,
            self._preview_effect_raster_state,
            self._export_effect_raster_state,
        ):
            if state is not None:
                state.tile_cache.clear()
                state.effect_source_cache.clear()
                state.positioned_stroke_coverage_cache.clear()
        self._effect_raster_state = None
        self._preview_effect_raster_state = None
        self._export_effect_raster_state = None
        self._export_active = False

    def _apply_effective_opacity(self) -> None:
        self.item._set_effective_opacity(
            self.effective_text_effects().overall_opacity
        )

    def _sync_legacy_primary_stroke_view(self) -> None:
        stroke = primary_stroke(self.effective_text_effects())
        if stroke is not None:
            self.item.stroke_qcolor = QColor(
                *effect_paint_fallback_color(stroke.paint)
            )

    @staticmethod
    def _invalidate_raster_state(state: Optional[_EffectRasterState]) -> None:
        if state is None:
            return
        state.cache_generation += 1
        state.cache_dirty = True
        state.cache_rendered_generation = -1
        state.cache_input_key = None
        state.tile_cache.clear()
        state.effect_source_cache.clear()
        state.positioned_stroke_coverage_cache.clear()
        state.background_pixmap = None
        state.background_pixmap_scale = None

    def _finish_effect_transition(self, repaint: bool) -> None:
        self._apply_effective_opacity()
        self._sync_legacy_primary_stroke_view()
        nodes = self._ordered_surface_nodes()
        was_repainting = self.repainting
        self.repainting = True
        try:
            self._sync_native_stroke_alignment(nodes)
        finally:
            self.repainting = was_repainting
        self._update_effect_padding(nodes)
        self.item.refresh_cache_policy(nodes)
        if repaint and not self.reshaping:
            self.repaint_background(nodes=nodes, geometry_prepared=True)
        self.item.update()

    def set_text_effects(
        self, stack: TextEffectStack, preview: bool = False
    ) -> bool:
        """Apply a complete preview or committed stack at the item boundary.

        >>> isinstance(TextEffectStack(), TextEffectStack)
        True
        """
        if not isinstance(stack, TextEffectStack):
            raise TypeError('live text effects require TextEffectStack')
        canonical = self.canonical_text_effects()
        effective_before = self.effective_text_effects()
        preview_before = self.preview

        if preview:
            if stack == canonical:
                return self.clear_text_effect_preview()
            if preview_before == stack:
                return False
            had_pixel_preview = self._uses_preview_cache_namespace()
            source_state = (
                None if self._export_active else self._peek_raster_state()
            )
            self.preview = stack
            effects_changed = effective_before.effects != stack.effects
            if effects_changed:
                if stack.effects != canonical.effects:
                    if not had_pixel_preview:
                        preview_state = _EffectRasterState()
                        self._copy_source_caches(
                            source_state, preview_state
                        )
                        self._preview_effect_raster_state = preview_state
                        self.geometry_controller.retain_effect_preview_surface()
                    self._mark_effect_cache_dirty()
                else:
                    # Returning to canonical effect pixels keeps the complete
                    # preview alive only for its native overall opacity.
                    self._preview_effect_raster_state = None
                    self._finish_effect_transition(False)
                    self.geometry_controller.restore_effect_preview_surface()
                    return True
            self._finish_effect_transition(
                effects_changed and self.faster_preview
            )
            return True

        model_format = self.item.blk.fontformat
        render_format = self.item.fontformat
        canonical_changed = canonical != stack
        render_format_changed = (
            render_format is not model_format
            and render_format.text_effects != stack
        )
        if (
            not canonical_changed
            and not render_format_changed
            and preview_before is None
        ):
            self._apply_effective_opacity()
            return False
        effects_changed = canonical.effects != stack.effects
        promoted_state = (
            self._promotable_preview_state(stack)
            if (
                effects_changed
                and preview_before == stack
            )
            else None
        )
        committed_generation = (
            0
            if self._effect_raster_state is None
            else self._effect_raster_state.cache_generation
        )
        if canonical_changed:
            model_format.text_effects = stack
        if render_format_changed:
            render_format.text_effects = stack
        self.preview = None
        self._preview_effect_raster_state = None
        self.geometry_controller.invalidate_effect_preview_surface()
        if effects_changed:
            if promoted_state is None:
                self._mark_effect_cache_dirty()
            else:
                promoted_state.cache_generation = committed_generation + 1
                promoted_state.cache_rendered_generation = (
                    promoted_state.cache_generation
                )
                promoted_state.tile_cache.clear()
                self._effect_raster_state = promoted_state
        self._finish_effect_transition(
            effects_changed
            and promoted_state is None
            and preview_before != stack
        )
        if promoted_state is not None:
            current_key = self._effect_cache_input_key(stack)
            if promoted_state.cache_input_key != current_key:
                self._mark_effect_cache_dirty()
                self.repaint_background()
            else:
                promoted_state.cache_input_key = current_key
        return (
            canonical_changed
            or render_format_changed
            or effective_before != stack
        )

    def clear_text_effect_preview(self) -> bool:
        if self.preview is None:
            return False
        preview = self.preview
        self.preview = None
        effects_changed = preview.effects != self.canonical_text_effects().effects
        self._preview_effect_raster_state = None
        self._finish_effect_transition(False)
        state = self._effect_raster_state
        current_key = self._effect_cache_input_key()
        if (
            state is not None
            and not state.cache_dirty
            and state.cache_rendered_generation == state.cache_generation
            and state.cache_input_key is not None
            and self._effect_cache_semantic_key(state.cache_input_key)
            == self._effect_cache_semantic_key(current_key)
        ):
            # Preview padding advances layout-only generations. Re-key only
            # after all pixel-bearing inputs return to the canonical values.
            state.cache_input_key = current_key
        needs_repaint = bool(
            effects_changed
            and any(self._effect_flags())
            and (
                state is None
                or state.cache_dirty
                or state.cache_rendered_generation
                != state.cache_generation
                or state.cache_input_key != current_key
            )
        )
        if needs_repaint and not self.reshaping:
            self.repaint_background()
        self.geometry_controller.restore_effect_preview_surface()
        return True

    def begin_reshape(self) -> None:
        """Omit effects during pointer motion and retire old geometry caches."""
        self._invalidate_raster_state(self._effect_raster_state)
        self._invalidate_raster_state(self._preview_effect_raster_state)
        self._invalidate_raster_state(self._export_effect_raster_state)
        self.geometry_controller.invalidate_effect_preview_surface()

    def end_reshape(self) -> None:
        """Rebuild only the effective namespace after geometry settles."""
        self.repaint_background()

    def paint_item(self, painter: QPainter, option, widget: QWidget, base_paint) -> None:
        """Paint effects around the host item's normal text pass."""
        if self.reshaping and not self._export_active:
            option.state = QStyle.State_None
            base_paint(painter, option, widget)
            return
        if not self.has_active_effects():
            option.state = QStyle.State_None
            base_paint(painter, option, widget)
            return

        # Fork: neutral blocks keep the host composition order — the host
        # (_paint_native) draws background_pixmap before text via SourceOver
        # and owns border/badge. Only a completed foreground (fills/hollow/
        # interior phases/inside strokes) must replace the native text pass,
        # which the host order cannot express.
        if self._text_transform_is_neutral() and (
            not self._renders_completed_foreground()
        ):
            option.state = QStyle.State_None
            base_paint(painter, option, widget)
            return

        # Effects must be composited before the normal fill. DestinationOver
        # against an already opaque scene would discard them.
        was_in_graphics_paint = self.in_graphics_paint
        self.in_graphics_paint = True
        try:
            nodes = self._ordered_surface_nodes()
            flags = self._effect_flags(nodes)
            renders_foreground = self._renders_completed_foreground(nodes)
            if not any(flags) and not renders_foreground:
                option.state = QStyle.State_None
                base_paint(painter, option, widget)
                return
            interaction_option = QStyleOptionGraphicsItem(option)
            if any(flags):
                self._draw_effects(
                    painter,
                    option.exposedRect,
                    nodes=nodes,
                    flags=flags,
                )
            replace_foreground = self._hollow_enabled() or (
                renders_foreground
                and (
                    self.export_render
                    or self._completed_foreground_ready()
                )
            )
            if replace_foreground:
                self._paint_effect_interaction(
                    painter, interaction_option, widget, base_paint
                )
            else:
                option.state = QStyle.State_None
                base_paint(painter, option, widget)
        finally:
            self.in_graphics_paint = was_in_graphics_paint

    def _renders_completed_foreground(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> bool:
        retained = (
            self._ordered_surface_nodes()
            if nodes is None
            else nodes
        )
        return (
            self._hollow_enabled()
            or bool(self._retained_phase_effects('interior', retained))
            or self._has_inside_strokes(retained)
            or bool(self._active_text_fills())
        )

    def _completed_foreground_ready(self) -> bool:
        state = self._peek_raster_state()
        return bool(
            state is not None
            and not state.cache_dirty
            and state.cache_rendered_generation == state.cache_generation
        )

    def _paint_effect_interaction(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget,
        base_paint,
    ) -> None:
        """Paint only selection/caret feedback over a completed foreground.

        A zero-opacity native pass keeps Qt's caret/IME state current and
        captures its selection formats. Ordinary foreground is then muted
        while those selections are replayed over the completed surface; the
        geometry owner paints the deferred caret last.

        >>> hasattr(TextEffectRenderer, '_paint_effect_interaction')
        True
        """
        if self.export_render or not self.item.isEditing():
            return
        layout = self.item.layout
        previous_defer_cursor = layout.defer_cursor_paint
        previous_observer = layout.paint_context_observer
        deferred_cursor_position = -1
        captured_context: Optional[
            QAbstractTextDocumentLayout.PaintContext
        ] = None

        def capture_context(
            context: QAbstractTextDocumentLayout.PaintContext,
        ) -> None:
            nonlocal captured_context
            if previous_observer is not None:
                previous_observer(context)
            # A caret is painted separately. Avoid a second full layout pass
            # unless Qt supplied selection feedback or active IME preedit ink.
            if context.selections or self.pre_editing:
                captured_context = self._editing_feedback_context(context)

        layout.defer_cursor_paint = True
        layout.paint_context_observer = capture_context
        try:
            painter.save()
            try:
                painter.setOpacity(0.0)
                base_paint(painter, option, widget)
                deferred_cursor_position = layout.deferred_cursor_position
            finally:
                painter.restore()
            layout.paint_context_observer = None
            if captured_context is not None:
                self._paint_live_layout(painter, captured_context)
        finally:
            layout.deferred_cursor_position = deferred_cursor_position
            layout.defer_cursor_paint = previous_defer_cursor
            layout.paint_context_observer = previous_observer
        if not self.geometry_controller.uses_surface_warp():
            self.geometry_controller.paint_deferred_cursor(
                painter, None, export_render=False
            )

    def _editing_feedback_context(
        self,
        context: QAbstractTextDocumentLayout.PaintContext,
    ) -> QAbstractTextDocumentLayout.PaintContext:
        """Keep Qt selections while suppressing ordinary foreground paint.

        >>> callable(TextEffectRenderer._editing_feedback_context)
        True
        """
        feedback = QAbstractTextDocumentLayout.PaintContext()
        feedback.clip = QRectF(context.clip)
        feedback.cursorPosition = -1
        feedback.palette = context.palette

        muted = QAbstractTextDocumentLayout.Selection()
        muted.cursor = QTextCursor(self.document())
        muted.cursor.select(QTextCursor.SelectionType.Document)
        muted_format = QTextCharFormat()
        transparent = QColor(0, 0, 0, 0)
        muted_format.setForeground(transparent)
        muted_format.setBackground(transparent)
        muted_format.setTextOutline(QPen(Qt.PenStyle.NoPen))
        muted_format.setUnderlineColor(transparent)
        feedback_base_format = QTextCharFormat(muted_format)
        muted_format.setProperty(GLYPH_FEEDBACK_ONLY_FORMAT_PROPERTY, True)
        muted.format = muted_format
        feedback_selections = [muted]
        for selection in context.selections:
            char_format = selection.format
            copied = QAbstractTextDocumentLayout.Selection()
            copied.cursor = QTextCursor(selection.cursor)
            feedback_format = QTextCharFormat(feedback_base_format)
            feedback_format.merge(char_format)
            if (
                char_format.foreground().style()
                != Qt.BrushStyle.NoBrush
                and not char_format.underlineColor().isValid()
            ):
                feedback_format.setUnderlineColor(
                    char_format.foreground().color()
                )
            copied.format = feedback_format
            feedback_selections.append(copied)
        feedback.selections = feedback_selections
        return feedback

    def finalize_neutral_cache(self) -> None:
        """Invalidate transformed pixels after neutral restoration."""
        state = self._peek_raster_state()
        if state is not None:
            state.tile_cache.clear()
            state.force_tiles = False
            state.direct_stroke = False
            state.cache_dirty = True
            state.cache_rendered_generation = -1
        self.clear_cached_surface()
        self.item.update()
        if not any(self._effect_flags()):
            self._drop_active_raster_state()

    def _effect_paint_context(self):
        context = QAbstractTextDocumentLayout.PaintContext()
        context.cursorPosition = -1
        context.selections = []
        return context

    def _paint_live_layout(self, painter: QPainter, context=None):
        layout = self.document().documentLayout()
        if context is None:
            context = self._effect_paint_context()
        layout.draw(painter, context)

    def _stroke_paint_context(self):
        context = self._effect_paint_context()
        doc = self.document()
        selections = []
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                char_format = fragment.charFormat()
                point_size = char_format.fontPointSize()
                if point_size <= 0:
                    point_size = char_format.font().pointSizeF()
                if point_size <= 0:
                    point_size = doc.defaultFont().pointSizeF()

                pen = QPen(
                    self.stroke_qcolor,
                    pt2px(point_size) * self._stroke_width(),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
                effect_format = QTextCharFormat()
                effect_format.setProperty(
                    GLYPH_STROKE_FORMAT_PROPERTY, True
                )
                # The later normal fill restores glyph interiors. Keeping this
                # pass opaque also avoids bindings that suppress textOutline
                # when the selection foreground itself is transparent.
                foreground = QColor(self.stroke_qcolor)
                if self._outline_only_stroke:
                    foreground.setAlpha(1)
                effect_format.setForeground(foreground)
                effect_format.setTextOutline(pen)

                selection = QAbstractTextDocumentLayout.Selection()
                selection.cursor = QTextCursor(doc)
                selection.cursor.setPosition(fragment.position())
                selection.cursor.setPosition(
                    fragment.position() + fragment.length(),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                selection.format = effect_format
                selections.append(selection)
                it += 1
            block = block.next()
        context.selections = selections
        return context

    def _stroke_outset(
        self,
        strokes: Optional[Tuple[StrokeEffect, ...]] = None,
    ) -> float:
        """Return the maximum visible Stroke reach outside glyph alpha."""
        strokes = self._retained_strokes() if strokes is None else strokes
        if not strokes:
            return 0.0
        font_size = self.layout.max_font_size(to_px=True)
        return max(
            font_size
            * stroke.width
            * (
                0.0
                if stroke.position == 'inside'
                else 0.5
            )
            for stroke in strokes
        )

    def _stroke_generation_reach(
        self,
        strokes: Optional[Tuple[StrokeEffect, ...]] = None,
    ) -> float:
        """Return the halo needed to generate positioned Stroke tiles."""
        strokes = self._retained_strokes() if strokes is None else strokes
        if not strokes:
            return 0.0
        font_size = self.layout.max_font_size(to_px=True)
        return max(
            font_size
            * stroke.width
            * 0.5
            for stroke in strokes
        )

    def _sync_native_stroke_alignment(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> None:
        """Keep fill and stroke on Qt's same native glyph raster path."""
        if self.layout is None:
            self._native_stroke_alignment = False
            return
        retained = (
            self._ordered_surface_nodes(strict_assets=False)
            if nodes is None
            else nodes
        )
        enabled = bool(self._stroke_sources_for_nodes(retained))
        self._native_stroke_alignment = enabled
        changed = False
        alignment_format = None
        block = self.document().firstBlock()
        while block.isValid():
            layout = block.layout()
            formats = list(layout.formats())
            tagged = [
                entry
                for entry in formats
                if bool(entry.format.property(
                    STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY
                ))
            ]
            if enabled == bool(tagged):
                block = block.next()
                continue
            formats = [
                entry
                for entry in formats
                if not bool(entry.format.property(
                    STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY
                ))
            ]
            if enabled:
                if alignment_format is None:
                    alignment_format = QTextCharFormat()
                    alignment_format.setProperty(
                        STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY, True
                    )
                    # A styled outline selects Qt's path-backed glyph
                    # rasterizer; transparent zero width paints no pixels.
                    alignment_format.setTextOutline(QPen(
                        QColor(0, 0, 0, 0),
                        0.0,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    ))
                entry = QTextLayout.FormatRange()
                entry.start = 0
                entry.length = _STROKE_ALIGNMENT_RANGE_LENGTH
                entry.format = alignment_format
                formats.append(entry)
            layout.setFormats(formats)
            changed = True
            block = block.next()
        if changed:
            # setFormats invalidates QTextLine objects but changes no document
            # content or geometry; rebuild once after all blocks are updated.
            self.layout.reLayout()

    def _new_effect_pixmap(
        self,
        render_scale: float = 1.0,
        surface_rect: QRectF = None,
    ) -> QPixmap:
        rect = self.boundingRect() if surface_rect is None else surface_rect
        pixel_width = max(1, math.ceil(rect.width() * render_scale))
        pixel_height = max(1, math.ceil(rect.height() * render_scale))
        if (
            pixel_width > EFFECT_CACHE_MAX_DIMENSION
            or pixel_height > EFFECT_CACHE_MAX_DIMENSION
            or pixel_width * pixel_height > EFFECT_CACHE_MAX_PIXELS
            or pixel_width * pixel_height * 4 > EFFECT_CACHE_MAX_BYTES
        ):
            raise EffectRasterAllocationError(
                f'effect surface {pixel_width}x{pixel_height} exceeds policy'
            )
        try:
            pixmap = QPixmap(pixel_width, pixel_height)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                f'unable to allocate effect surface '
                f'{pixel_width}x{pixel_height}'
            ) from error
        if pixmap.isNull():
            raise EffectRasterAllocationError(
                f'unable to allocate effect surface {pixel_width}x{pixel_height}'
            )
        try:
            if render_scale >= 1.0:
                pixmap.setDevicePixelRatio(render_scale)
            pixmap.fill(Qt.GlobalColor.transparent)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                f'unable to initialize effect surface '
                f'{pixel_width}x{pixel_height}'
            ) from error
        return pixmap

    @staticmethod
    def _prepare_effect_surface_painter(
        painter: QPainter, render_scale: float
    ) -> None:
        """Map logical item coordinates onto a sub-unit preview surface."""
        if render_scale < 1.0:
            painter.scale(render_scale, render_scale)

    def _begin_effect_layer_painter(
        self,
        target: QPixmap,
        surface_rect: QRectF,
        render_scale: float,
    ) -> QPainter:
        """Begin one painter in the shared item-local surface space."""
        painter: Optional[QPainter] = None
        try:
            painter = QPainter(target)
            if not painter.isActive():
                raise EffectRasterAllocationError(
                    'unable to begin text-effect layer painter'
                )
            painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
            self._prepare_effect_surface_painter(painter, render_scale)
            painter.translate(-surface_rect.topLeft())
            return painter
        except RASTER_BOUNDARY_FAILURES as error:
            if painter is not None and painter.isActive():
                try:
                    painter.end()
                except RASTER_BOUNDARY_FAILURES:
                    pass
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to prepare text-effect layer painter'
            ) from error

    @staticmethod
    def _custom_blend_surface_pixmaps(
        destination: QPixmap,
        source: QPixmap,
        blend_mode: str,
        render_scale: float,
    ) -> QPixmap:
        """Bridge one non-native blend without changing surface coordinates."""
        destination_rgba = pixmap2ndarray(destination, keep_alpha=True)
        source_rgba = pixmap2ndarray(source, keep_alpha=True)
        if destination_rgba is None or source_rgba is None:
            raise EffectRasterAllocationError(
                'unable to read text-effect blend layers'
            )
        result = ndarray2pixmap(composite_custom_blend_rgba(
            destination_rgba, source_rgba, blend_mode
        ))
        if result is None or result.isNull():
            raise EffectRasterAllocationError(
                'unable to allocate blended text-effect surface'
            )
        if render_scale >= 1.0:
            result.setDevicePixelRatio(render_scale)
        return result

    @staticmethod
    def _draw_surface_pixmap(
        painter: QPainter,
        destination: QRectF,
        pixmap: QPixmap,
        render_scale: float,
    ) -> None:
        """Draw physical surface pixels into their explicit logical bounds."""
        if render_scale < 1.0:
            painter.drawPixmap(destination, pixmap, QRectF(pixmap.rect()))
        else:
            painter.drawPixmap(destination.topLeft(), pixmap)

    def _paint_cloned_document_stroke(self, painter: QPainter) -> None:
        """Paint stroke through the BASE cloned-document path.

        Fork: the clone is rebuilt through HTML and the engine layouts are
        instantiated with the pcfg punctuation parameters (matching
        ``item.py`` construction); ``QTextDocument.clone`` would drop both.
        """
        doc = QTextDocument()
        doc.setUndoRedoEnabled(False)
        doc.setDocumentMargin(self.layout.effectPadding())
        doc.setDefaultFont(self.document().defaultFont())
        doc.setHtml(self.document().toHtml())
        doc.setDefaultTextOption(self.document().defaultTextOption())
        cursor = QTextCursor(doc)
        block = doc.firstBlock()
        stroke_pen = QPen(
            self.stroke_qcolor,
            0,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        letter_spacing = self.fontformat.letter_spacing * 100
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                char_format = fragment.charFormat()
                stroke_pen.setWidthF(
                    pt2px(char_format.fontPointSize())
                    * self._stroke_width()
                )
                cursor.setPosition(fragment.position())
                cursor.setPosition(
                    fragment.position() + fragment.length(),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                char_format.setTextOutline(stroke_pen)
                if self._outline_only_stroke:
                    foreground = QColor(self.stroke_qcolor)
                    foreground.setAlpha(1)
                    char_format.setForeground(foreground)
                # Path-painted glyph extensions consume this flag. Ruby and
                # emphasis derive half-width native outlines in temporary docs.
                char_format.setProperty(
                    GLYPH_DILATED_STROKE_FORMAT_PROPERTY, True
                )
                if letter_spacing != 100 and not self.fontformat.vertical:
                    char_format.setFontLetterSpacingType(
                        QFont.SpacingType.PercentageSpacing
                    )
                    char_format.setFontLetterSpacing(letter_spacing)
                cursor.mergeCharFormat(char_format)
                it += 1
            block = block.next()

        from utils.config import pcfg

        if self.fontformat.vertical:
            layout = EngineVerticalTextDocumentLayout(doc, self.fontformat)
            # Fork compatibility: the fork layout accepted these as constructor
            # arguments; the engine layout exposes the same pcfg members, so
            # reapply them after construction.
            layout.punctuation_position = pcfg.punctuation_position
            layout.halfwidth_jp_corner_brackets = (
                pcfg.halfwidth_jp_corner_brackets
            )
        else:
            layout = HorizontalTextDocumentLayout(doc, self.fontformat)
        layout._draw_offset = self.layout._draw_offset
        layout._is_painting_stroke = True
        layout.setMaxSize(self.layout.max_width, self.layout.max_height, False)
        doc.setDocumentLayout(layout)
        layout.relayout_on_changed = False
        doc.drawContents(painter)

    def _paint_vertical_stroke(
        self,
        painter: QPainter,
        render_scale: float = 1.0,
        surface_rect: QRectF = None,
    ):
        """Stroke vertical glyphs per rich-text fragment on every binding."""
        stroke_alpha = None
        rgba = None
        stroke_context = self._stroke_paint_context()
        selections_by_radius = {}
        for selection in stroke_context.selections:
            logical_radius = selection.format.textOutline().widthF() / 2
            selections_by_radius.setdefault(logical_radius, []).append(selection)

        for logical_radius, selections in selections_by_radius.items():
            rect = self.boundingRect() if surface_rect is None else surface_rect
            source = self._new_effect_pixmap(render_scale, rect)
            source_painter = QPainter(source)
            if not source_painter.isActive():
                raise EffectRasterAllocationError(
                    'unable to begin vertical stroke source painter'
                )
            try:
                source_painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
                self._prepare_effect_surface_painter(
                    source_painter, render_scale
                )
                source_painter.translate(-rect.topLeft())
                fragment_context = self._effect_paint_context()
                fragment_context.selections = selections
                self.geometry_controller.draw_layout_selection_mask(
                    source_painter,
                    fragment_context,
                )
            finally:
                source_painter.end()

            try:
                rgba = pixmap2ndarray(source, keep_alpha=True)
            except RASTER_BOUNDARY_FAILURES as error:
                raise EffectRasterAllocationError(
                    'unable to access vertical stroke source pixels'
                ) from error
            if rgba is None:
                raise EffectRasterAllocationError(
                    'unable to access vertical stroke source pixels'
                )
            alpha = rgba[..., 3]
            radius = math.ceil(logical_radius * render_scale)
            if radius > 0:
                diameter = radius * 2 + 1
                kernel = cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE, (diameter, diameter)
                )
                alpha = cv2.dilate(alpha, kernel)
            if stroke_alpha is None:
                stroke_alpha = alpha
            else:
                np.maximum(stroke_alpha, alpha, out=stroke_alpha)

        if stroke_alpha is None or rgba is None:
            return
        stroke = np.empty_like(rgba)
        stroke[..., 0] = self.stroke_qcolor.red()
        stroke[..., 1] = self.stroke_qcolor.green()
        stroke[..., 2] = self.stroke_qcolor.blue()
        stroke[..., 3] = stroke_alpha
        try:
            stroke_pixmap = ndarray2pixmap(stroke)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                'unable to allocate vertical stroke result'
            ) from error
        if stroke_pixmap is None or stroke_pixmap.isNull():
            raise EffectRasterAllocationError(
                'unable to allocate vertical stroke result'
            )
        if render_scale >= 1.0:
            stroke_pixmap.setDevicePixelRatio(render_scale)
        self._draw_surface_pixmap(
            painter, rect, stroke_pixmap, render_scale
        )
        # Fork: the engine layout paints annotation (ruby/emphasis) ink in
        # its own stroke pass; upstream re-draws half-font annotation
        # outlines here via draw_layout_annotations, which the fork's
        # glyph_slant does not expose.

    def paint_stroke(
        self,
        painter: QPainter,
        render_scale: float = 1.0,
        surface_rect: QRectF = None,
    ):
        if self._text_transform_is_neutral():
            self._paint_cloned_document_stroke(painter)
            return
        active_layout = self.document().documentLayout()
        if (
            isinstance(active_layout, EngineVerticalTextDocumentLayout)
            and self._has_layout_distortion()
        ):
            self._paint_vertical_stroke(painter, render_scale, surface_rect)
            return
        self._paint_source_local_stroke(painter)

    def _paint_source_local_stroke(self, painter: QPainter):
        # Native box transforms map the completed source surface. Only an
        # attached glyph renderer changes the source glyph geometry itself.
        if self._has_layout_distortion():
            self._paint_live_layout(painter, self._stroke_paint_context())
            return
        self._paint_cloned_document_stroke(painter)

    def _shadow_metrics(
        self, shadow: ShadowEffect
    ) -> Tuple[float, float, float, float]:
        font_size = self.layout.max_font_size(to_px=True)
        distance = shadow.distance * font_size
        radians = math.radians(shadow.angle)
        return (
            shadow.blur * font_size,
            shadow.spread * font_size,
            math.cos(radians) * distance,
            math.sin(radians) * distance,
        )

    def _shadowed_bounds(
        self, source_bounds: QRectF, shadow: ShadowEffect
    ) -> QRectF:
        blur, spread, xoffset, yoffset = self._shadow_metrics(shadow)
        if shadow.shadow_type == 'long':
            return source_bounds.united(
                source_bounds.translated(xoffset, yoffset)
            )
        return source_bounds.translated(xoffset, yoffset).adjusted(
            -blur - spread,
            -blur - spread,
            blur + spread,
            blur + spread,
        )

    def _glow_metrics(self, glow: GlowEffect) -> Tuple[float, float]:
        font_size = self.layout.max_font_size(to_px=True)
        return glow.size * font_size, glow.spread * font_size

    def _exterior_effect_bounds(
        self, source_bounds: QRectF, effect: TextEffect
    ) -> QRectF:
        if isinstance(effect, ShadowEffect):
            return self._shadowed_bounds(source_bounds, effect)
        if isinstance(effect, GlowEffect) and effect.glow_type == 'outer':
            size, spread = self._glow_metrics(effect)
            return source_bounds.adjusted(
                -size - spread,
                -size - spread,
                size + spread,
                size + spread,
            )
        raise TypeError('exterior bounds require Shadow or Outer Glow')

    def _logical_ink_bounds(self) -> QRectF:
        if self.document().isEmpty() or not self._has_layout_distortion():
            return QRectF()
        return self.geometry_controller.layout_ink_bounds()

    def _effect_padding(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> float:
        retained = (
            self._ordered_surface_nodes()
            if nodes is None
            else nodes
        )
        layout_distorted = self._has_layout_distortion()
        if not layout_distorted:
            return self._conservative_effect_padding(retained)
        ink_bounds = self._logical_ink_bounds()
        logical_rect = self.logical_unpadded_rect()
        retained_strokes = self._retained_strokes(retained)
        painted_stroke_outset = self._stroke_outset(retained_strokes)
        source_stroke_outset = self._stroke_outset(
            self._stroke_sources_for_nodes(retained)
        )
        painted_stroke_bounds = ink_bounds.adjusted(
            -painted_stroke_outset,
            -painted_stroke_outset,
            painted_stroke_outset,
            painted_stroke_outset,
        )
        exterior_source_bounds = ink_bounds.adjusted(
            -source_stroke_outset,
            -source_stroke_outset,
            source_stroke_outset,
            source_stroke_outset,
        )
        effect_bounds = QRectF(ink_bounds)
        exterior = False
        for index, effect in retained:
            if isinstance(effect, StrokeEffect):
                if not ink_bounds.isEmpty():
                    effect_bounds = effect_bounds.united(
                        painted_stroke_bounds
                    )
            elif (
                isinstance(effect, (ShadowEffect, GlowEffect))
                and effect_phase(effect) == 'exterior'
            ):
                if not ink_bounds.isEmpty():
                    exterior = True
                    effect_bounds = effect_bounds.united(
                        self._exterior_effect_bounds(
                            exterior_source_bounds, effect
                        )
                    )
        if effect_bounds.isEmpty():
            return 0.0
        if painted_stroke_outset > 0.0 or exterior:
            effect_bounds = effect_bounds.adjusted(
                -EFFECT_RASTER_GUARD,
                -EFFECT_RASTER_GUARD,
                EFFECT_RASTER_GUARD,
                EFFECT_RASTER_GUARD,
            )
        return max(
            0.0,
            logical_rect.left() - effect_bounds.left(),
            effect_bounds.right() - logical_rect.right(),
            logical_rect.top() - effect_bounds.top(),
            effect_bounds.bottom() - logical_rect.bottom(),
        )

    def _conservative_effect_padding(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> float:
        """Return cheap symmetric padding for non-distorting glyph paths."""
        if self.layout is None:
            return 0.0
        retained = (
            self._ordered_surface_nodes()
            if nodes is None
            else nodes
        )
        max_font_size = max(0.0, self.layout.max_font_size(to_px=True))
        stroke_outset = 0.0
        active_strokes = self._stroke_sources_for_nodes(retained)
        if active_strokes:
            stroke_outset = max(
                max_font_size
                * (stroke.width + 0.05)
                * (
                    0.0
                    if stroke.position == 'inside'
                    else 0.5
                )
                for stroke in active_strokes
            )
        padding = stroke_outset
        exterior_padding = None
        for effect in self._retained_phase_effects('exterior', retained):
            if isinstance(effect, ShadowEffect):
                blur, spread, xoffset, yoffset = self._shadow_metrics(effect)
                effect_padding = (
                    stroke_outset
                    + (
                        0.0
                        if effect.shadow_type == 'long'
                        else blur + spread
                    )
                    + max(abs(xoffset), abs(yoffset))
                )
            else:
                size, spread = self._glow_metrics(effect)
                effect_padding = stroke_outset + size + spread
            exterior_padding = (
                effect_padding
                if exterior_padding is None
                else max(exterior_padding, effect_padding)
            )
        if exterior_padding is not None:
            padding = max(
                padding, exterior_padding + EFFECT_RASTER_GUARD
            )
        return padding

    def _commit_effect_padding(
        self,
        padding: float,
    ) -> bool:
        return (
            self.setPadding(padding)
            if self.padding() != padding
            else False
        )

    def _update_effect_padding(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ):
        if self.refreshing_effect_padding or self.layout is None:
            return False
        self.refreshing_effect_padding = True
        try:
            padding = self._effect_padding(nodes)
            # QTextLayout stores coordinates at 26.6 fixed-point precision.
            # Round outward so relayout and undo cycles converge.
            if padding > 0.0:
                layout_units = math.nextafter(padding * 64.0, -math.inf)
                padding = math.ceil(layout_units) / 64.0
            return self._commit_effect_padding(padding)
        finally:
            self.refreshing_effect_padding = False

    def _effect_flags(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> Tuple[bool, bool]:
        """Return active Stroke and generated completed-surface flags."""
        retained = (
            self._ordered_surface_nodes()
            if nodes is None
            else nodes
        )
        strokes = self._retained_strokes(retained)
        exterior = self._retained_phase_effects('exterior', retained)
        interior = self._retained_phase_effects('interior', retained)
        return (
            bool(strokes),
            bool(exterior)
            or bool(interior)
            or (
                not self._hollow_enabled()
                and bool(self._active_text_fills())
            )
            or any(
                stroke.position != 'center'
                or stroke.blend_mode != 'normal'
                or isinstance(stroke.paint, LinearGradientPaint)
                for stroke in strokes
            ),
        )

    def _effect_tile_overlap(
        self,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> float:
        retained = (
            self._ordered_surface_nodes(strict_assets=False)
            if nodes is None
            else nodes
        )
        stroke_reach = self._stroke_generation_reach(
            self._stroke_sources_for_nodes(retained)
        )
        overlap = stroke_reach + EFFECT_RASTER_GUARD
        for effect in (
            self._retained_phase_effects('exterior', retained)
            + self._retained_phase_effects('interior', retained)
        ):
            if isinstance(effect, ShadowEffect):
                blur, spread, xoffset, yoffset = self._shadow_metrics(effect)
                if effect.shadow_type == 'long':
                    reach = max(abs(xoffset), abs(yoffset))
                else:
                    reach = (
                        blur + spread + max(abs(xoffset), abs(yoffset))
                    )
                source_reach = (
                    stroke_reach
                    if effect.shadow_type != 'inner'
                    else 0.0
                )
            else:
                size, spread = self._glow_metrics(effect)
                reach = size + spread
                source_reach = (
                    stroke_reach if effect.glow_type == 'outer' else 0.0
                )
            overlap = max(
                overlap,
                reach + source_reach + EFFECT_RASTER_GUARD,
            )
        return overlap

    def _warn_effect_allocation_once(self, error: Exception):
        if self.allocation_warning_generation == self.cache_generation:
            return
        self.allocation_warning_generation = self.cache_generation
        LOGGER.warning(
            'Text effect raster allocation failed for item %s; '
            'using the bounded interactive fallback for this frame: %s',
            self.idx,
            error,
        )

    def _on_glyph_raster_failure(
        self, error: Exception, effect_pass: bool = False
    ):
        """Bridge renderer degradation into item/export failure policy."""
        failure = EffectRasterAllocationError(str(error))
        self._warn_effect_allocation_once(failure)
        if self.capturing_surface:
            self.surface_raster_error = failure
        if effect_pass:
            self.cache_dirty = True
        if self.capturing_surface:
            return
        if self.export_render:
            if self.in_graphics_paint:
                self.export_error = failure
            else:
                raise failure from error

    def set_export_effect_render(self, enabled: bool):
        """Make effect allocation failures fatal during a render transaction."""
        enabled = bool(enabled)
        self._verified_export_assets.clear()
        if enabled:
            self._export_active = True
            self._export_effect_raster_state = _EffectRasterState()
            return
        self._export_active = False
        self._export_effect_raster_state = None

    def _raise_or_defer_export_effect_error(self, error: Exception) -> bool:
        """Raise at a Python boundary or defer across Qt's paint callback.

        PyQt treats an exception escaping a virtual ``QGraphicsItem.paint``
        callback as fatal. Canvas checks the deferred error immediately after
        ``QGraphicsScene.render`` and raises before returning its image.
        """
        if not self.export_render:
            return False
        failure = EffectRasterAllocationError(str(error))
        if self.in_graphics_paint:
            self.export_error = failure
            return True
        raise failure from error

    def _render_effect_surface(
        self,
        surface_rect: QRectF,
        render_scale: float,
        *,
        target_stroke: bool = True,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> QPixmap:
        """Render the ordered stack: canonical base plus generated layers.

        >>> hasattr(TextEffectRenderer, '_render_effect_surface')
        True
        """
        if nodes is None:
            nodes = self._ordered_surface_nodes(target_stroke=target_stroke)
        target = self._render_effect_base(surface_rect, render_scale)
        return self._composite_generated_layer_batch(
            target,
            nodes,
            surface_rect,
            render_scale,
            _source_is_fresh_base=True,
        )

    def _render_effect_base(
        self,
        surface_rect: QRectF,
        render_scale: float,
    ) -> QPixmap:
        """Render the structural canonical Text Fill base.

        >>> hasattr(TextEffectRenderer, '_render_effect_base')
        True
        """
        target = self._new_effect_pixmap(render_scale, surface_rect)
        hollow = self._hollow_enabled()
        canonical = None
        if not hollow:
            canonical, _canonical_alpha = self._cached_effect_source(
                surface_rect, render_scale, needs_alpha=False
            )

        painter = QPainter(target)
        if not painter.isActive():
            raise EffectRasterAllocationError(
                'unable to begin effect base painter'
            )
        previous_capture = self.capturing_surface
        previous_raster_error = self.surface_raster_error
        self.capturing_surface = True
        self.surface_raster_error = None
        try:
            painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
            self._prepare_effect_surface_painter(painter, render_scale)
            painter.translate(-surface_rect.topLeft())
            if canonical is not None:
                text_fill_group = self._text_fill_group_pixmap(
                    canonical,
                    surface_rect,
                    render_scale,
                    self._active_text_fills(),
                )
                self._draw_surface_pixmap(
                    painter,
                    surface_rect,
                    canonical if text_fill_group is None else text_fill_group,
                    render_scale,
                )
            if self.surface_raster_error is not None:
                raise self.surface_raster_error
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render effect base surface'
            ) from error
        finally:
            end_error = None
            try:
                painter.end()
            except RASTER_BOUNDARY_FAILURES as error:
                end_error = error
            self.capturing_surface = previous_capture
            self.surface_raster_error = previous_raster_error
            if end_error is not None:
                raise EffectRasterAllocationError(
                    'unable to finish effect base painter'
                ) from end_error
        return target

    def _composite_generated_layer_batch(
        self,
        source: QPixmap,
        nodes: Tuple[Tuple[int, TextEffect], ...],
        surface_rect: QRectF,
        render_scale: float,
        *,
        _source_is_fresh_base: bool = False,
    ) -> QPixmap:
        """Source-over one contiguous canonical generated-layer batch.

        >>> hasattr(TextEffectRenderer, '_composite_generated_layer_batch')
        True
        """
        if not nodes:
            return source
        generated_nodes = tuple(
            (index, effect)
            for index, effect in nodes
            if isinstance(effect, (StrokeEffect, ShadowEffect, GlowEffect))
        )
        needs_canonical_alpha = bool(generated_nodes)
        if generated_nodes:
            canonical, canonical_alpha = self._cached_effect_source(
                surface_rect,
                render_scale,
                needs_alpha=needs_canonical_alpha,
            )
        else:
            canonical = None
            canonical_alpha = None
        positioned_stroke_bands: Dict[StrokeEffect, QPixmap] = {}
        try:
            exterior_alphas = (
                self._ordered_exterior_source_alphas(
                    canonical,
                    canonical_alpha,
                    nodes,
                    surface_rect,
                    render_scale,
                    positioned_stroke_bands,
                )
                if canonical is not None and canonical_alpha is not None
                else {}
            )
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render ordered Stroke source silhouette'
            ) from error

        # The prefix owner just allocated this base and no cache observes it
        # yet. Upper batches may receive cached/filter output and must detach.
        target = source if _source_is_fresh_base else QPixmap(source)
        painter: Optional[QPainter] = None
        previous_capture = self.capturing_surface
        previous_raster_error = self.surface_raster_error
        self.capturing_surface = True
        self.surface_raster_error = None
        try:
            painter = self._begin_effect_layer_painter(
                target, surface_rect, render_scale
            )
            for index, effect in nodes:
                if isinstance(effect, StrokeEffect):
                    assert canonical is not None
                    layer = self._stroke_layer_pixmap(
                        effect,
                        surface_rect,
                        render_scale,
                        canonical_alpha,
                        positioned_stroke_bands,
                    )
                else:
                    assert canonical is not None
                    source_alpha = (
                        exterior_alphas[index]
                        if effect_phase(effect) == 'exterior'
                        else canonical_alpha
                    )
                    assert source_alpha is not None
                    layer = self._generated_effect_pixmap(
                        source_alpha,
                        effect,
                        surface_rect,
                        render_scale,
                        canonical_alpha,
                    )
                if effect.blend_mode in CUSTOM_BLEND_MODES:
                    painter.end()
                    target = self._custom_blend_surface_pixmaps(
                        target, layer, effect.blend_mode, render_scale
                    )
                    painter = self._begin_effect_layer_painter(
                        target, surface_rect, render_scale
                    )
                else:
                    painter.setCompositionMode(
                        _BLEND_COMPOSITION_MODES[effect.blend_mode]
                    )
                    self._draw_surface_pixmap(
                        painter, surface_rect, layer, render_scale
                    )
            if self.surface_raster_error is not None:
                raise self.surface_raster_error
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to composite generated text-effect layers'
            ) from error
        finally:
            end_error = None
            try:
                if painter is not None and painter.isActive():
                    painter.end()
            except RASTER_BOUNDARY_FAILURES as error:
                end_error = error
            self.capturing_surface = previous_capture
            self.surface_raster_error = previous_raster_error
            if end_error is not None:
                raise EffectRasterAllocationError(
                    'unable to finish generated-layer painter'
                ) from end_error
        return target

    def _stroke_layer_pixmap(
        self,
        stroke: StrokeEffect,
        surface_rect: QRectF,
        render_scale: float,
        canonical_alpha: Optional[np.ndarray],
        positioned_stroke_bands: Dict[StrokeEffect, QPixmap],
    ) -> QPixmap:
        """Return one Stroke layer without consulting sibling visual order.

        >>> hasattr(TextEffectRenderer, '_stroke_layer_pixmap')
        True
        """
        previous = self._render_stroke
        self._render_stroke = stroke
        try:
            band = positioned_stroke_bands.get(stroke)
            if band is None:
                band = self._positioned_stroke_band(
                    surface_rect,
                    render_scale,
                    stroke,
                    canonical_alpha,
                )
                positioned_stroke_bands[stroke] = band
            return band
        finally:
            self._render_stroke = previous

    @staticmethod
    def _positioned_stroke_coverage_cache_key(
        source_key: tuple,
        stroke: StrokeEffect,
    ) -> tuple:
        """Key Stroke geometry without its downstream paint or opacity.

        >>> first = StrokeEffect(width=0.2, opacity=0.25)
        >>> second = StrokeEffect(
        ...     width=0.2, opacity=0.75,
        ...     paint=LinearGradientPaint(angle=95.0)
        ... )
        >>> TextEffectRenderer._positioned_stroke_coverage_cache_key(
        ...     ('source',), first
        ... ) == TextEffectRenderer._positioned_stroke_coverage_cache_key(
        ...     ('source',), second
        ... )
        True
        """
        return source_key, float(stroke.width), stroke.position

    def _positioned_stroke_coverage(
        self,
        surface_rect: QRectF,
        render_scale: float,
        stroke: StrokeEffect,
        canonical_alpha: Optional[np.ndarray],
    ) -> np.ndarray:
        """Return immutable native outline alpha clipped to its position.

        >>> hasattr(TextEffectRenderer, '_positioned_stroke_coverage')
        True
        """
        state = self._raster_state()
        key = self._positioned_stroke_coverage_cache_key(
            self._effect_source_cache_key(surface_rect, render_scale),
            stroke,
        )
        cached = state.positioned_stroke_coverage_cache.get(key)
        if cached is not None:
            return cached

        previous = self._render_stroke
        previous_outline_only = self._outline_only_stroke
        self._render_stroke = stroke
        self._outline_only_stroke = True
        try:
            layer = self._new_effect_pixmap(render_scale, surface_rect)
            layer_painter = QPainter(layer)
            if not layer_painter.isActive():
                raise EffectRasterAllocationError(
                    'unable to begin positioned Stroke painter'
                )
            try:
                layer_painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
                self._prepare_effect_surface_painter(
                    layer_painter, render_scale
                )
                layer_painter.translate(-surface_rect.topLeft())
                self.paint_stroke(
                    layer_painter, render_scale, surface_rect
                )
            finally:
                layer_painter.end()

            rgba = pixmap2ndarray(layer, keep_alpha=True)
            if rgba is None:
                raise EffectRasterAllocationError(
                    'unable to access positioned Stroke pixels'
                )
            # Alpha 1 keeps Qt from suppressing textOutline. It is a capture
            # sentinel, not visible foreground in the persistent band.
            alpha = rgba[..., 3]
            alpha[alpha <= 1] = 0
            if stroke.position != 'center':
                if canonical_alpha is None:
                    raise EffectRasterAllocationError(
                        'positioned Stroke requires canonical glyph alpha'
                    )
                coverage = (
                    canonical_alpha
                    if stroke.position == 'inside'
                    else 255 - canonical_alpha
                )
                product = alpha.astype(np.uint16)
                product *= coverage.astype(np.uint16)
                product += 127
                product //= 255
                alpha = product.astype(np.uint8)
            alpha = np.ascontiguousarray(alpha)
            alpha.setflags(write=False)
            state.positioned_stroke_coverage_cache[key] = alpha
            while len(state.positioned_stroke_coverage_cache) > 2:
                state.positioned_stroke_coverage_cache.pop(
                    next(iter(state.positioned_stroke_coverage_cache))
                )
            return alpha
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render positioned Stroke coverage'
            ) from error
        finally:
            self._outline_only_stroke = previous_outline_only
            self._render_stroke = previous

    def _positioned_stroke_band(
        self,
        surface_rect: QRectF,
        render_scale: float,
        stroke: StrokeEffect,
        canonical_alpha: Optional[np.ndarray],
    ) -> QPixmap:
        """Apply one Stroke's paint and opacity to geometric coverage.

        >>> hasattr(TextEffectRenderer, '_positioned_stroke_band')
        True
        """
        try:
            alpha = self._positioned_stroke_coverage(
                surface_rect,
                render_scale,
                stroke,
                canonical_alpha,
            )
            if stroke.position == 'center' and not self._hollow_enabled():
                if canonical_alpha is None:
                    raise EffectRasterAllocationError(
                        'Center Stroke requires canonical glyph alpha'
                    )
                product = alpha.astype(np.uint16)
                product *= (255 - canonical_alpha).astype(np.uint16)
                product += 127
                product //= 255
                alpha = product.astype(np.uint8)
            if stroke.opacity != 1.0:
                product = alpha.astype(np.uint16)
                product *= int(round(stroke.opacity * 255))
                product += 127
                product //= 255
                alpha = product.astype(np.uint8)
            rgba = np.empty(alpha.shape + (4,), dtype=np.uint8)
            rgba[..., 3] = alpha
            colorize_effect_paint_rgba(
                stroke.paint,
                rgba,
                surface_rect,
                self.logical_unpadded_rect(),
                render_scale,
            )
            band = ndarray2pixmap(rgba)
            if band is None or band.isNull():
                raise EffectRasterAllocationError(
                    'unable to allocate positioned Stroke band'
                )
            if render_scale >= 1.0:
                band.setDevicePixelRatio(render_scale)
            return band
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render positioned Stroke band'
            ) from error

    def _paint_positioned_strokes(
        self,
        painter: QPainter,
        surface_rect: QRectF,
        render_scale: float,
        canonical_alpha: Optional[np.ndarray],
        positions: Tuple[str, ...],
        positioned_stroke_bands: Optional[
            Dict[StrokeEffect, QPixmap]
        ] = None,
        strokes: Optional[Tuple[StrokeEffect, ...]] = None,
    ) -> None:
        """Paint selected Stroke positions back-to-front.

        >>> hasattr(TextEffectRenderer, '_paint_positioned_strokes')
        True
        """
        previous = self._render_stroke
        try:
            paint_order = (
                tuple(reversed(self._active_strokes()))
                if strokes is None
                else strokes
            )
            for stroke in paint_order:
                if stroke.position not in positions:
                    continue
                self._render_stroke = stroke
                band = (
                    None
                    if positioned_stroke_bands is None
                    else positioned_stroke_bands.get(stroke)
                )
                if band is None:
                    band = self._positioned_stroke_band(
                        surface_rect,
                        render_scale,
                        stroke,
                        canonical_alpha,
                    )
                    if positioned_stroke_bands is not None:
                        positioned_stroke_bands[stroke] = band
                self._draw_surface_pixmap(
                    painter,
                    surface_rect,
                    band,
                    render_scale,
                )
        finally:
            self._render_stroke = previous

    def _paint_stroke_silhouette(
        self,
        silhouette: QPixmap,
        canonical_alpha: np.ndarray,
        strokes: Tuple[StrokeEffect, ...],
        surface_rect: QRectF,
        render_scale: float,
        positioned_stroke_bands: Dict[StrokeEffect, QPixmap],
    ) -> None:
        """Extend a canonical silhouette with Strokes in application order."""
        if not strokes:
            return
        painter = QPainter(silhouette)
        if not painter.isActive():
            raise EffectRasterAllocationError(
                'unable to begin Stroke silhouette painter'
            )
        try:
            painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
            self._prepare_effect_surface_painter(painter, render_scale)
            painter.translate(-surface_rect.topLeft())
            self._paint_positioned_strokes(
                painter,
                surface_rect,
                render_scale,
                canonical_alpha,
                ('center', 'inside', 'outside'),
                positioned_stroke_bands,
                strokes,
            )
        finally:
            painter.end()

    def _capture_effect_source(
        self,
        surface_rect: QRectF,
        render_scale: float,
    ) -> QPixmap:
        """Capture the canonical glyph pixels once for compiled phases.

        >>> hasattr(TextEffectRenderer, '_capture_effect_source')
        True
        """
        source = self._new_effect_pixmap(render_scale, surface_rect)
        try:
            painter = QPainter(source)
            if not painter.isActive():
                raise EffectRasterAllocationError(
                    'unable to begin effect source painter'
                )
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to begin effect source painter'
            ) from error
        previous_capture = self.capturing_surface
        previous_raster_error = self.surface_raster_error
        self.capturing_surface = True
        self.surface_raster_error = None
        try:
            painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
            self._prepare_effect_surface_painter(painter, render_scale)
            painter.translate(-surface_rect.topLeft())
            self._paint_live_layout(painter, self._effect_paint_context())
            if self.surface_raster_error is not None:
                raise self.surface_raster_error
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render effect source surface'
            ) from error
        finally:
            end_error = None
            try:
                painter.end()
            except RASTER_BOUNDARY_FAILURES as error:
                end_error = error
            self.capturing_surface = previous_capture
            self.surface_raster_error = previous_raster_error
            if end_error is not None:
                raise EffectRasterAllocationError(
                    'unable to finish effect source painter'
                ) from end_error
        return source

    def capture_plain_logical_rgba(
        self,
        width: int,
        height: int,
        offset_x: float,
        offset_y: float,
    ) -> np.ndarray:
        """Render the untransformed document foreground without effects.

        ``offset`` places the logical rectangle inside an integer page crop.
        The active Glyph Slant delegate and transient effect foreground state
        are restored even when Qt painting fails.

        >>> callable(TextEffectRenderer.capture_plain_logical_rgba)
        True
        """
        if width <= 0 or height <= 0:
            raise ValueError('plain text capture requires a positive size')
        image = QImage(
            width, height, QImage.Format.Format_ARGB32_Premultiplied
        )
        if image.isNull():
            raise EffectRasterAllocationError(
                'unable to allocate plain text capture'
            )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        if not painter.isActive():
            raise EffectRasterAllocationError(
                'unable to begin plain text capture'
            )
        logical = self.logical_unpadded_rect()
        layout = self.item.layout
        previous_delegate = layout.render_delegate
        previous_stroke = self._render_stroke
        previous_outline = self._outline_only_stroke
        previous_alignment = self._native_stroke_alignment
        previous_deferred_cursor = layout.deferred_cursor_position
        try:
            layout.render_delegate = None
            self._render_stroke = None
            self._outline_only_stroke = False
            self._native_stroke_alignment = False
            painter.setRenderHints(_VECTOR_EFFECT_RENDER_HINTS)
            painter.translate(
                float(offset_x) - logical.x(),
                float(offset_y) - logical.y(),
            )
            self._paint_live_layout(painter, self._effect_paint_context())
        finally:
            layout.render_delegate = previous_delegate
            self._render_stroke = previous_stroke
            self._outline_only_stroke = previous_outline
            self._native_stroke_alignment = previous_alignment
            layout.deferred_cursor_position = previous_deferred_cursor
            painter.end()
        rgba = pixmap2ndarray(image, keep_alpha=True)
        if rgba is None:
            raise EffectRasterAllocationError(
                'unable to read plain text capture'
            )
        return rgba

    def _cached_effect_source(
        self,
        surface_rect: QRectF,
        render_scale: float,
        *,
        needs_alpha: bool,
    ) -> Tuple[QPixmap, Optional[np.ndarray]]:
        """Reuse paint-independent canonical glyph pixels and alpha.

        >>> hasattr(TextEffectRenderer, '_cached_effect_source')
        True
        """
        state = self._raster_state()
        key = self._effect_source_cache_key(surface_rect, render_scale)
        cached = state.effect_source_cache.get(key)
        if cached is None:
            canonical = self._capture_effect_source(
                surface_rect, render_scale
            )
            canonical_alpha = (
                self._pixmap_alpha(canonical) if needs_alpha else None
            )
            cached = (canonical, canonical_alpha)
            state.effect_source_cache[key] = cached
            while len(state.effect_source_cache) > 2:
                state.effect_source_cache.pop(
                    next(iter(state.effect_source_cache))
                )
        elif needs_alpha and cached[1] is None:
            cached = (cached[0], self._pixmap_alpha(cached[0]))
            state.effect_source_cache[key] = cached
        return cached

    def _text_fill_group_pixmap(
        self,
        canonical: QPixmap,
        surface_rect: QRectF,
        render_scale: float,
        text_fills: Tuple[TextFillEffect, ...],
    ) -> Optional[QPixmap]:
        """Compose renderable Text Fills over a transparent face surface.

        >>> hasattr(TextEffectRenderer, '_text_fill_group_pixmap')
        True
        """
        if not text_fills:
            return None
        painter = None
        try:
            target = None
            rgba = None
            for text_fill in text_fills:
                # Compose paint alpha first, then apply glyph coverage once so
                # repeated Fills cannot thicken antialiased face edges.
                if rgba is None:
                    rgba = np.empty(
                        (canonical.height(), canonical.width(), 4),
                        dtype=np.uint8,
                    )
                rgba[..., 3] = 255
                colorize_effect_paint_rgba(
                    text_fill.paint,
                    rgba,
                    surface_rect,
                    self.logical_unpadded_rect(),
                    render_scale,
                )
                if text_fill.opacity != 1.0:
                    product = rgba[..., 3].astype(np.uint16)
                    product *= int(round(text_fill.opacity * 255.0))
                    product += 127
                    product //= 255
                    rgba[..., 3] = product.astype(np.uint8)
                layer = ndarray2pixmap(rgba)
                if layer is None or layer.isNull():
                    raise EffectRasterAllocationError(
                        'unable to allocate Text Fill layer'
                    )
                if render_scale >= 1.0:
                    layer.setDevicePixelRatio(render_scale)
                if target is None:
                    # Every blend mode is source identity over transparency.
                    # Source-copy into an alpha-capable surface so the final
                    # canonical clip can still reduce an opaque first layer.
                    target = self._new_effect_pixmap(
                        render_scale, surface_rect
                    )
                    painter = self._begin_effect_layer_painter(
                        target, surface_rect, render_scale
                    )
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_Source
                    )
                    self._draw_surface_pixmap(
                        painter, surface_rect, layer, render_scale
                    )
                    continue
                if text_fill.blend_mode in CUSTOM_BLEND_MODES:
                    painter.end()
                    target = self._custom_blend_surface_pixmaps(
                        target, layer, text_fill.blend_mode, render_scale
                    )
                    painter = self._begin_effect_layer_painter(
                        target, surface_rect, render_scale
                    )
                else:
                    painter.setCompositionMode(
                        _BLEND_COMPOSITION_MODES[text_fill.blend_mode]
                    )
                    self._draw_surface_pixmap(
                        painter, surface_rect, layer, render_scale
                    )
            if target is not None:
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_DestinationIn
                )
                self._draw_surface_pixmap(
                    painter, surface_rect, canonical, render_scale
                )
            return target
        except RASTER_BOUNDARY_FAILURES as error:
            if isinstance(error, EffectRasterAllocationError):
                raise
            raise EffectRasterAllocationError(
                'unable to render Text Fill'
            ) from error
        finally:
            if painter is not None and painter.isActive():
                painter.end()

    def _ordered_exterior_source_alphas(
        self,
        canonical: QPixmap,
        canonical_alpha: np.ndarray,
        nodes: Tuple[Tuple[int, TextEffect], ...],
        surface_rect: QRectF,
        render_scale: float,
        positioned_stroke_bands: Dict[StrokeEffect, QPixmap],
    ) -> Dict[int, np.ndarray]:
        """Map exterior nodes to canonical plus preceding Stroke alpha.

        The working silhouette grows monotonically through the card order, so
        each Stroke band is composited at most once per generated-layer batch.

        >>> hasattr(TextEffectRenderer, '_ordered_exterior_source_alphas')
        True
        """
        active_effects = self.effective_text_effects().effects
        ordered_strokes = tuple(
            (index, effect)
            for index, effect in reversed(tuple(enumerate(active_effects)))
            if isinstance(effect, StrokeEffect) and not effect.is_neutral()
        )
        sources: Dict[int, np.ndarray] = {}
        silhouette: Optional[QPixmap] = None
        painted_count = 0
        source_alpha = canonical_alpha
        for index, effect in nodes:
            if not isinstance(effect, (ShadowEffect, GlowEffect)) or (
                effect_phase(effect) != 'exterior'
            ):
                continue
            previous_count = painted_count
            while (
                painted_count < len(ordered_strokes)
                and ordered_strokes[painted_count][0] > index
            ):
                painted_count += 1
            stroke_slice = ordered_strokes[previous_count:painted_count]
            new_strokes = tuple(
                stroke for _stroke_index, stroke in stroke_slice
            )
            if new_strokes:
                if silhouette is None:
                    silhouette = QPixmap(canonical)
                self._paint_stroke_silhouette(
                    silhouette,
                    canonical_alpha,
                    new_strokes,
                    surface_rect,
                    render_scale,
                    positioned_stroke_bands,
                )
                source_alpha = self._pixmap_alpha(silhouette)
            sources[index] = source_alpha
        return sources

    @staticmethod
    def _pixmap_alpha(pixmap: QPixmap) -> np.ndarray:
        try:
            rgba = pixmap2ndarray(pixmap, keep_alpha=True)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                'unable to access text effect source pixels'
            ) from error
        if rgba is None:
            raise EffectRasterAllocationError(
                'unable to access text effect source pixels'
            )
        return rgba[..., 3].copy()

    def _shadow_pixmap(
        self,
        source_alpha: np.ndarray,
        shadow: ShadowEffect,
        surface_rect: QRectF,
        render_scale: float,
        canonical_alpha: Optional[np.ndarray] = None,
    ) -> QPixmap:
        """Render Shadow alpha while protecting only the canonical face.

        >>> hasattr(TextEffectRenderer, '_shadow_pixmap')
        True
        """
        blur, spread, xoffset, yoffset = self._shadow_metrics(shadow)
        if canonical_alpha is None:
            canonical_alpha = source_alpha
        try:
            alpha = render_shadow_alpha(
                source_alpha,
                shadow.shadow_type,
                shadow.opacity,
                (
                    xoffset * render_scale,
                    yoffset * render_scale,
                ),
                max(0, int(round(blur * render_scale))),
                max(0, int(round(spread * render_scale))),
                canonical_alpha,
            )
            rgba = np.empty(source_alpha.shape + (4,), dtype=np.uint8)
            rgba[..., 3] = alpha
            colorize_effect_paint_rgba(
                shadow.paint,
                rgba,
                surface_rect,
                self.logical_unpadded_rect(),
                render_scale,
            )
            pixmap = ndarray2pixmap(rgba)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                f'unable to allocate typed shadow surface: {error}'
            ) from error
        if pixmap is None or pixmap.isNull():
            raise EffectRasterAllocationError(
                'unable to allocate typed shadow surface'
            )
        if render_scale >= 1.0:
            pixmap.setDevicePixelRatio(render_scale)
        return pixmap

    def _glow_pixmap(
        self,
        source_alpha: np.ndarray,
        glow: GlowEffect,
        surface_rect: QRectF,
        render_scale: float,
    ) -> QPixmap:
        """Render one typed Glow from the phase's shared source alpha.

        >>> hasattr(TextEffectRenderer, '_glow_pixmap')
        True
        """
        size, spread = self._glow_metrics(glow)
        try:
            alpha = render_glow_alpha(
                source_alpha,
                glow.glow_type,
                max(0, int(round(size * render_scale))),
                max(0, int(round(spread * render_scale))),
            )
            if glow.opacity != 1.0:
                product = alpha.astype(np.uint16)
                product *= int(round(glow.opacity * 255.0))
                product += 127
                product //= 255
                alpha = product.astype(np.uint8)
            rgba = np.empty(source_alpha.shape + (4,), dtype=np.uint8)
            rgba[..., 3] = alpha
            colorize_effect_paint_rgba(
                glow.paint,
                rgba,
                surface_rect,
                self.logical_unpadded_rect(),
                render_scale,
            )
            pixmap = ndarray2pixmap(rgba)
        except RASTER_BOUNDARY_FAILURES as error:
            raise EffectRasterAllocationError(
                f'unable to allocate typed Glow surface: {error}'
            ) from error
        if pixmap is None or pixmap.isNull():
            raise EffectRasterAllocationError(
                'unable to allocate typed Glow surface'
            )
        if render_scale >= 1.0:
            pixmap.setDevicePixelRatio(render_scale)
        return pixmap

    def _generated_effect_pixmap(
        self,
        source_alpha: np.ndarray,
        effect: TextEffect,
        surface_rect: QRectF,
        render_scale: float,
        canonical_alpha: Optional[np.ndarray],
    ) -> QPixmap:
        """Render one generated node from canonical geometry inputs.

        >>> hasattr(TextEffectRenderer, '_generated_effect_pixmap')
        True
        """
        if isinstance(effect, ShadowEffect):
            return self._shadow_pixmap(
                source_alpha,
                effect,
                surface_rect,
                render_scale,
                canonical_alpha,
            )
        if isinstance(effect, GlowEffect):
            return self._glow_pixmap(
                source_alpha, effect, surface_rect, render_scale
            )
        raise TypeError('generated effect must be Shadow or Glow')

    def repaint_background(
        self,
        render_scale: float = None,
        *,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
        geometry_prepared: bool = False,
    ) -> None:
        """Rebuild the effect surface cache at an effective device scale.

        Fork: ``None`` (the common bare-caller form) resolves the host view's
        current target scale so caches rebuilt outside a paint event stay
        crisp on HiDPI or zoomed canvases; reshape drags honour
        ``pcfg.show_decorations_during_drag`` instead of always clearing.
        """
        if self.repainting or (self.pre_editing and not self._export_active):
            # During IME, reuse the preedit-free cache because PaintContext
            # cannot exclude active preedit glyphs.
            return
        if (
            self.reshaping
            and not _decorations_visible_during_drag()
            and not self._export_active
        ):
            return

        planned_here = nodes is None
        retained = (
            self._ordered_surface_nodes()
            if planned_here
            else nodes
        )
        if planned_here:
            self.item.refresh_cache_policy(retained)
        empty = self.document().isEmpty()

        # Immediate transitions already prepared this exact immutable plan.
        if not geometry_prepared:
            self.repainting = True
            try:
                self._sync_native_stroke_alignment(retained)
            finally:
                self.repainting = False
            self._update_effect_padding(retained)

        paint_stroke, paint_non_stroke = self._effect_flags(retained)
        if not paint_non_stroke and not paint_stroke or empty:
            changed = self.background_pixmap is not None
            self.background_pixmap = None
            self.background_pixmap_scale = None
            state = self._peek_raster_state()
            if state is not None:
                state.tile_cache.clear()
            self._drop_active_raster_state()
            if changed:
                self.item.update()
            return

        self.tile_cache.clear()
        self.repainting = True
        try:
            if render_scale is None:
                render_scale = self._host_target_scale()
            br = self.boundingRect()
            plan = plan_effect_raster(
                br.width(),
                br.height(),
                self._raster_request(render_scale),
            )
            try:
                target_map = self._render_effect_surface(
                    br, plan.tier, nodes=retained
                )
            except EFFECT_RASTER_FAILURES as error:
                # A higher tier may fail despite satisfying the deterministic
                # caps. Retry the smallest full tier before degrading.
                retry = plan_effect_raster(br.width(), br.height(), 1.0)
                if plan.tier > 1.0 and retry.mode == 'full':
                    try:
                        target_map = self._render_effect_surface(
                            br, 1.0, nodes=retained
                        )
                        plan = retry
                    except EFFECT_RASTER_FAILURES as retry_error:
                        error = retry_error
                        target_map = None
                else:
                    target_map = None
                if target_map is None:
                    self.background_pixmap = None
                    self.background_pixmap_scale = None
                    self.cache_dirty = True
                    self.cache_rendered_generation = -1
                    if self.export_render:
                        # A policy-valid full allocation can still fail at
                        # runtime. Export gets one bounded visible-tile retry
                        # before the transaction is failed.
                        self.direct_stroke = False
                        self.force_tiles = True
                        return
                    self.direct_stroke = (
                        paint_stroke
                        and self._all_strokes_vector_compatible(
                            self._retained_strokes(retained)
                        )
                    )
                    self._warn_effect_allocation_once(error)
                    return

            self.background_pixmap = target_map
            self.background_pixmap_scale = plan.tier
            self.direct_stroke = False
            self.force_tiles = False
            self.cache_dirty = False
            self.cache_rendered_generation = self.cache_generation
            self._raster_state().cache_input_key = (
                self._effect_cache_input_key()
            )
        finally:
            self.repainting = False
        self.item.update()

    def _mark_effect_cache_dirty(self) -> None:
        state = self._raster_state()
        state.cache_generation += 1
        state.cache_dirty = True
        state.cache_input_key = None
        state.tile_cache.clear()
        # Completed effect pixels contain the previous paint parameters.
        self.background_pixmap = None
        self.background_pixmap_scale = None

    def _visible_effect_rect(
        self, painter: QPainter, exposed_rect: QRectF = None
    ) -> QRectF:
        visible = QRectF(self.boundingRect())
        if exposed_rect is not None and not exposed_rect.isEmpty():
            visible = visible.intersected(exposed_rect)
        if painter.hasClipping():
            clip = painter.clipBoundingRect()
            if not clip.isEmpty():
                visible = visible.intersected(clip)
        return visible

    def _draw_tiled_effects(
        self,
        painter: QPainter,
        plan: EffectRasterPlan,
        exposed_rect: QRectF = None,
        *,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
    ) -> None:
        br = self.boundingRect()
        visible = self._visible_effect_rect(painter, exposed_rect)
        if visible.isEmpty():
            return

        if nodes is None:
            nodes = self._ordered_surface_nodes()
        retained_strokes = self._retained_strokes(nodes)
        paint_stroke, paint_non_stroke = self._effect_flags(nodes)
        stroke_overlap = (
            self._stroke_generation_reach(retained_strokes)
            + EFFECT_RASTER_GUARD
        )
        vector_stroke_direct = (
            paint_stroke
            and not paint_non_stroke
            and self._all_strokes_vector_compatible(retained_strokes)
            and 2 * math.ceil(stroke_overlap * plan.tier)
            >= plan.tile_edge
        )
        target_overlap = (
            EFFECT_RASTER_GUARD
            if vector_stroke_direct
            else self._effect_tile_overlap(nodes)
        )
        if vector_stroke_direct:
            self.tile_cache.clear()
            self.direct_stroke = True
            self.cache_dirty = False
            self.cache_rendered_generation = self.cache_generation
            self._raster_state().cache_input_key = (
                self._effect_cache_input_key()
            )
            self.force_tiles = False
            return
        overlap_px = math.ceil(target_overlap * plan.tier)
        core_edge_px = plan.tile_edge - 2 * overlap_px
        if core_edge_px < 1:
            error = EffectRasterAllocationError(
                'effect overlap exceeds bounded tile surface'
            )
            if self._raise_or_defer_export_effect_error(error):
                return
            self._warn_effect_allocation_once(error)
            self.direct_stroke = (
                paint_stroke
                and self._all_strokes_vector_compatible(retained_strokes)
            )
            return
        core_edge = core_edge_px / plan.tier
        surface_overlap = overlap_px / plan.tier

        first_x = max(
            0, int(math.floor((visible.left() - br.left()) / core_edge))
        )
        first_y = max(
            0, int(math.floor((visible.top() - br.top()) / core_edge))
        )
        last_x = max(
            first_x,
            int(
                math.floor(
                    (math.nextafter(visible.right(), -math.inf) - br.left())
                    / core_edge
                )
            ),
        )
        last_y = max(
            first_y,
            int(
                math.floor(
                    (math.nextafter(visible.bottom(), -math.inf) - br.top())
                    / core_edge
                )
            ),
        )

        active_keys = set()
        staging_pixmap = None
        staging_painter = None
        tile_painter = painter
        raster_failure = None
        try:
            if not self.export_render:
                staging_plan = plan_effect_raster(
                    visible.width(), visible.height(), plan.tier
                )
                if (
                    staging_plan.mode != 'full'
                    or staging_plan.tier != plan.tier
                ):
                    raise EffectRasterAllocationError(
                        'visible effect staging surface exceeds policy'
                    )
                staging_pixmap = self._new_effect_pixmap(
                    plan.tier, visible
                )
                staging_painter = QPainter(staging_pixmap)
                if not staging_painter.isActive():
                    raise EffectRasterAllocationError(
                        'unable to begin visible effect staging painter'
                    )
                self._prepare_effect_surface_painter(
                    staging_painter, plan.tier
                )
                staging_painter.translate(-visible.topLeft())
                # Each core is a complete surface region. Source-copying it
                # preserves the premultiplied pixels produced by every tile.
                staging_painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                tile_painter = staging_painter
            tile_painter.setRenderHint(
                QPainter.RenderHint.SmoothPixmapTransform
            )
            for tile_y in range(first_y, last_y + 1):
                for tile_x in range(first_x, last_x + 1):
                    core = QRectF(
                        br.left() + tile_x * core_edge,
                        br.top() + tile_y * core_edge,
                        core_edge,
                        core_edge,
                    ).intersected(br)
                    if core.isEmpty():
                        continue
                    surface = core.adjusted(
                        -surface_overlap,
                        -surface_overlap,
                        surface_overlap,
                        surface_overlap,
                    ).intersected(br)
                    key = (
                        self.cache_generation,
                        plan.tier,
                        tile_x,
                        tile_y,
                        round(surface.left(), 6),
                        round(surface.top(), 6),
                        round(surface.width(), 6),
                        round(surface.height(), 6),
                        vector_stroke_direct,
                    )
                    active_keys.add(key)
                    cached = self.tile_cache.get(key)
                    if cached is None:
                        pixmap = self._render_effect_surface(
                            surface,
                            plan.tier,
                            target_stroke=not vector_stroke_direct,
                            nodes=nodes,
                        )
                        cached = (QRectF(surface), pixmap)
                        self.tile_cache[key] = cached
                        while len(self.tile_cache) > 2:
                            oldest = next(iter(self.tile_cache))
                            if oldest == key and len(self.tile_cache) > 1:
                                oldest = next(
                                    candidate
                                    for candidate in self.tile_cache
                                    if candidate != key
                                )
                            self.tile_cache.pop(oldest, None)
                    tile_painter.save()
                    try:
                        tile_painter.setClipRect(
                            core, Qt.ClipOperation.IntersectClip
                        )
                        self._draw_surface_pixmap(
                            tile_painter, cached[0], cached[1], plan.tier
                        )
                    finally:
                        tile_painter.restore()
        except RASTER_BOUNDARY_FAILURES as error:
            raster_failure = (
                error
                if isinstance(error, EFFECT_RASTER_FAILURES)
                else EffectRasterAllocationError(
                    'unable to render tiled effect surface'
                )
            )
            if raster_failure is not error:
                raster_failure.__cause__ = error
        finally:
            if staging_painter is not None:
                try:
                    if staging_painter.isActive():
                        staging_painter.end()
                except RASTER_BOUNDARY_FAILURES as error:
                    if raster_failure is None:
                        raster_failure = EffectRasterAllocationError(
                            'unable to finish tiled effect painter'
                        )
                        raster_failure.__cause__ = error

        if raster_failure is not None:
            self.tile_cache.clear()
            self.direct_stroke = (
                paint_stroke
                and self._all_strokes_vector_compatible(retained_strokes)
            )
            self.cache_dirty = True
            self.cache_rendered_generation = -1
            if self._raise_or_defer_export_effect_error(raster_failure):
                return
            self._warn_effect_allocation_once(raster_failure)
            return

        if staging_pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._draw_surface_pixmap(
                painter, visible, staging_pixmap, plan.tier
            )

        # Retain no cache from a viewport that is no longer exposed.
        for key in list(self.tile_cache):
            if key not in active_keys:
                self.tile_cache.pop(key, None)

        self.direct_stroke = vector_stroke_direct
        self.cache_dirty = False
        self.cache_rendered_generation = self.cache_generation
        self._raster_state().cache_input_key = (
            self._effect_cache_input_key()
        )
        self.force_tiles = False

    def _draw_direct_stroke(self, painter: QPainter) -> None:
        if (
            not self._effect_flags()[0]
            or not self._all_strokes_vector_compatible()
        ):
            return
        # This path intentionally avoids every intermediate raster allocation.
        # The custom glyph renderer still consumes outline selections, while a
        # native box transform keeps the unclipped cloned-document stroke.
        previous = self._outline_only_stroke
        self._outline_only_stroke = self._hollow_enabled()
        try:
            self._paint_strokes(
                painter, lambda: self._paint_source_local_stroke(painter)
            )
        finally:
            self._outline_only_stroke = previous

    def _draw_effects(
        self,
        painter: QPainter,
        exposed_rect: QRectF = None,
        *,
        nodes: Optional[Tuple[Tuple[int, TextEffect], ...]] = None,
        flags: Optional[Tuple[bool, bool]] = None,
    ) -> None:
        painter.save()
        try:
            retained = (
                self._ordered_surface_nodes()
                if nodes is None
                else nodes
            )
            paint_stroke, paint_non_stroke = (
                self._effect_flags(retained) if flags is None else flags
            )
            if not paint_stroke and not paint_non_stroke:
                return
            # A preview can park committed pixels while content or another
            # effect changes. Validate semantics at the final reuse boundary
            # so cancellation cannot revive that stale surface.
            self._invalidate_stale_active_raster_state()
            br = self.boundingRect()
            requested_scale = self._paint_device_scale(painter)
            plan = plan_effect_raster(
                br.width(),
                br.height(),
                self._raster_request(requested_scale),
            )
            if self.force_tiles:
                plan = EffectRasterPlan(
                    'tiles', min(1.0, plan.tier), 0, 0,
                    EFFECT_TILE_MAX_EDGE,
                )
            stale = (
                self.cache_rendered_generation
                != self.cache_generation
            )
            if plan.mode == 'full':
                if (
                    (not self.pre_editing or self._export_active)
                    and (
                        self.background_pixmap is None
                        or self.background_pixmap_scale != plan.tier
                        or self.cache_dirty
                        or stale
                    )
                ):
                    self.repaint_background(
                        requested_scale,
                        nodes=retained,
                    )
                if self.force_tiles:
                    tile_plan = EffectRasterPlan(
                        'tiles', min(1.0, plan.tier), 0, 0,
                        EFFECT_TILE_MAX_EDGE,
                    )
                    self._draw_tiled_effects(
                        painter,
                        tile_plan,
                        exposed_rect,
                        nodes=retained,
                    )
                    if self.direct_stroke:
                        self._draw_direct_stroke(painter)
                    return
                if (
                    self.background_pixmap is not None
                    and self.background_pixmap_scale == plan.tier
                    and self.cache_rendered_generation
                    == self.cache_generation
                ):
                    painter.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform
                    )
                    self._draw_surface_pixmap(
                        painter, br, self.background_pixmap, plan.tier
                    )
                elif self.direct_stroke:
                    self._draw_direct_stroke(painter)
            else:
                # A previous ordinary-size fast cache must never be stretched
                # over a new huge local surface.
                self.background_pixmap = None
                self.background_pixmap_scale = None
                self._draw_tiled_effects(
                    painter,
                    plan,
                    exposed_rect,
                    nodes=retained,
                )
                if self.direct_stroke:
                    self._draw_direct_stroke(painter)
        finally:
            painter.restore()

    @staticmethod
    def _paint_device_scale(painter: QPainter) -> float:
        transform = painter.deviceTransform()
        a, b = transform.m11(), transform.m21()
        c, d = transform.m12(), transform.m22()
        trace = a * a + b * b + c * c + d * d
        determinant_squared = (a * d - b * c) ** 2
        discriminant = max(0.0, trace * trace - 4 * determinant_squared)
        scale = math.sqrt((trace + math.sqrt(discriminant)) / 2)
        if scale <= 0:
            return 1.0
        return min(max(1.0, scale), EFFECT_CACHE_MAX_SCALE)


    def _host_target_scale(self) -> float:
        """Best-effort target scale from the first visible host view."""
        try:
            scene = self.item.scene()
            views = scene.views() if scene is not None else []
        except RuntimeError:
            return 1.0
        for view in views:
            if view.isVisible():
                scale = abs(view.transform().m11()) * max(
                    1.0, view.viewport().devicePixelRatioF()
                )
                return min(max(1.0, scale), EFFECT_CACHE_MAX_SCALE)
        return 1.0

    def ensure_host_background(self, painter: QPainter) -> None:
        """Refresh the host-consumed cache at the active device scale.

        Neutral blocks composite ``background_pixmap`` through the host item
        instead of ``_draw_effects``, so a zoom/DPI change or an input-key
        drift must rebuild the cached raster here before ``drawPixmap``
        consumes it.
        """
        if not any(self._effect_flags()):
            return
        requested_scale = quality_raster_request(
            self._paint_device_scale(painter)
        )
        br = self.boundingRect()
        plan = plan_effect_raster(br.width(), br.height(), requested_scale)
        state = self._peek_raster_state()
        stale = (
            state is not None
            and state.cache_rendered_generation != state.cache_generation
        )
        drifted = (
            state is not None
            and not stale
            and state.cache_input_key is not None
            and state.cache_input_key != self._effect_cache_input_key()
        )
        if (
            self.background_pixmap is None
            or self.background_pixmap_scale != plan.tier
            or stale
            or drifted
        ):
            self.repaint_background(plan.tier)
