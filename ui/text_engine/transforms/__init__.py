"""Compiled text-transform pipeline (Stage 4 port).

``mapping`` provides the pure geometry (matrix + nonlinear mappers) while
``registry`` binds persisted transform types to stage factories and compiles
an ordered stack into one native matrix or one surface mapper.
"""
