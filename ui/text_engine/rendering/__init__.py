"""Rendering support of the text engine.

Full port of upstream v1.5.9 ``text_engine/rendering/*`` across Stage 3 and
Stage 4:

- ``shadow``: glyph-shadow compositing (shared with the effect
  gradient-editor preview).
- ``raster``: bounded full-surface / tile raster planning policy.
- ``indexing``: Qt UTF-16 / grapheme indexing helpers.
- ``glyph``: glyph-run ink extraction, glyph-local slant shear, and
  pathless/color fallback rasterization (Stage 4 full port; the
  ``GLYPH_STROKE_FORMAT_PROPERTY`` constant and ``GlyphRasterAllocationError``
  were already consumed by the Stage 3 effect renderer).
- ``glyph_slant``: ``GlyphSlantLayoutRenderer`` — stateful glyph-slant paint
  delegate attached to ``SceneTextLayout`` (Stage 4).
- ``surface``: ``NonlinearTextSurfaceRenderer`` — cv2.remap warp of a complete
  text composite under a nonlinear mapper (Stage 4).
"""
