"""Text engine controller package (Stage 2 port of upstream TextItemGeometryController).

Stages:
  Stage 1  model layer   — utils/fontformat.py text_transform / glyph_slant_angle fields (done)
  Stage 2  controller    — ui/text_engine/geometry.py TextItemGeometryController (this stage)
  Stage 3  effect render — nonlinear rendering effect renderer
  Stage 4  transforms    — grid / projective control points and surface rendering

Non-linear dependencies live in ``ui.text_engine._stubs`` until Stage 4.
"""
