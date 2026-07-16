"""QSpinBox / QDoubleSpinBox with up/down arrow buttons hidden.

Drop-in replacements that hide the native arrow buttons and apply
a semi-transparent rounded style matching NoArrowsSpinBox.

Two classes are provided:
  - :class:`NoArrowsSpinBox`  — for ``QSpinBox`` (integer)
  - :class:`NoArrowsDoubleSpinBox` — for ``QDoubleSpinBox`` (float)
"""

from qtpy.QtWidgets import QDoubleSpinBox, QSpinBox

# Shared stylesheet template — ``{selector}`` is substituted with
# the Qt class name (``QSpinBox`` or ``QDoubleSpinBox``) at runtime.
_SPIN_STYLE = """
{selector} {{
    background: rgba(128,128,128,0.13);
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 4px;
    padding: 2px 4px;
}}
{selector}:focus {{
    border: 1px solid #5DADE2;
}}
{selector}::up-button, {selector}::down-button {{
    width: 0px;
    height: 0px;
}}
{selector}:disabled {{
    background: transparent;
    border: 1px solid rgba(128,128,128,0.1);
}}
"""


class NoArrowsSpinBox(QSpinBox):
    """QSpinBox with hidden arrow buttons and transparent-ish background.

    All style is applied once in __init__; callers only need to set
    range, value, and fixedWidth as usual.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_SPIN_STYLE.format(selector="QSpinBox"))


class NoArrowsDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox with hidden arrow buttons and transparent-ish background.

    Identical visual style to :class:`NoArrowsSpinBox`, but supports
    floating-point values via ``QDoubleSpinBox``.

    Callers only need to set range, singleStep, decimals, value, and
    fixedWidth as usual.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_SPIN_STYLE.format(selector="QDoubleSpinBox"))
