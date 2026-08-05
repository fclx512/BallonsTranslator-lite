"""Text engine controller package (Stage 2-4 port of upstream TextItemGeometryController).

Stages:
  Stage 1  model layer   — utils/fontformat.py text_transform / glyph_slant_angle fields (done)
  Stage 2  controller    — ui/text_engine/geometry.py TextItemGeometryController
  Stage 3  effect render — nonlinear rendering effect renderer
  Stage 4  transforms    — transforms/ (mapping+registry+bend/sine/grid) and
                           rendering/ (glyph, glyph_slant, surface), wired into
                           geometry.py (the former ``_stubs`` module is gone)
"""
