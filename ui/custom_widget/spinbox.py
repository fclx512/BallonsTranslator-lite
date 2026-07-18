"""QSpinBox / QDoubleSpinBox with up/down arrow buttons hidden.

Drop-in replacements that hide the native arrow buttons.
Styling is handled by ``config/stylesheet.css`` (NoArrowsSpinBox /
NoArrowsDoubleSpinBox selectors with ``@inputBackgroundColor``).

Two classes are provided:
  - :class:`NoArrowsSpinBox`  — for ``QSpinBox`` (integer)
  - :class:`NoArrowsDoubleSpinBox` — for ``QDoubleSpinBox`` (float)
"""

from qtpy.QtWidgets import QDoubleSpinBox, QSpinBox


class NoArrowsSpinBox(QSpinBox):
    """QSpinBox with hidden arrow buttons and theme-aware background.

    Arrow buttons are hidden via CSS (``::up-button`` / ``::down-button``
    width:0) in the global stylesheet.  No inline style needed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)


class NoArrowsDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox with hidden arrow buttons and theme-aware background.

    Identical visual style to :class:`NoArrowsSpinBox`, but supports
    floating-point values via ``QDoubleSpinBox``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
