"""QLineEdit / QTextEdit with NoArrowsSpinBox-inspired semi-transparent style.

Drop-in replacements that apply the same visual design as NoArrowsSpinBox
to single-line and multi-line text inputs across the settings panel.
"""

from qtpy.QtWidgets import QLineEdit, QTextEdit


class ConfigLineEdit(QLineEdit):
    """``QLineEdit`` with NoArrowsSpinBox semi-transparent rounded style.

    Overrides the ``ConfigContent`` underline-style border with the
    same ``@inputBackgroundColor`` rounded border look
    used by :class:`NoArrowsSpinBox``.
    Styling is handled by ``config/stylesheet.css``.

    Callers only need to swap ``QLineEdit`` → ``ConfigLineEdit``;
    all existing method calls (``setPlaceholderText``, ``setText``,
    ``setValidator``, etc.) continue to work.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)


class ConfigTextEdit(QTextEdit):
    """``QTextEdit`` with NoArrowsSpinBox-inspired style, for multi-line
    text areas such as LLM prompt templates.

    Uses slightly larger padding than :class:`ConfigLineEdit` to give
    multi-line editing comfortable whitespace inside the border.
    Styling is handled by ``config/stylesheet.css``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
