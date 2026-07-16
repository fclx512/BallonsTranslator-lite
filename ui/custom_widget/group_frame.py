"""Thin bordered frame for grouping controls visually.

Provides a rounded themed border (``1px solid rgba(128,128,128,0.25)``,
``border-radius: 6px`` via CSS) with transparent background.  Used to
wrap control groups in :class:`FontFormatPanel` so each functional
block has a visible container.
"""

from qtpy.QtWidgets import QFrame


class GroupFrame(QFrame):
    """``QFrame`` that renders as a rounded-border group container.

    All styling comes from the ``GroupFrame`` class selector in
    ``stylesheet.css`` — no inline styles needed.
    """

    pass
