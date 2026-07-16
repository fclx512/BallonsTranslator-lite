"""QLineEdit / QTextEdit with NoArrowsSpinBox-inspired semi-transparent style.

Drop-in replacements that apply the same visual design as NoArrowsSpinBox
to single-line and multi-line text inputs across the settings panel.
"""

from qtpy.QtWidgets import QLineEdit, QTextEdit


class ConfigLineEdit(QLineEdit):
    """``QLineEdit`` with NoArrowsSpinBox semi-transparent rounded style.

    Overrides the ``ConfigContent`` underline-style border with the
    same ``rgba(128,128,128,0.13)`` background and rounded border
    used by :class:`NoArrowsSpinBox`.

    Callers only need to swap ``QLineEdit`` → ``ConfigLineEdit``;
    all existing method calls (``setPlaceholderText``, ``setText``,
    ``setValidator``, etc.) continue to work.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("""
            QLineEdit {
                background: rgba(128,128,128,0.13);
                border: 1px solid rgba(128,128,128,0.25);
                border-radius: 4px;
                padding: 2px 8px;
            }
            QLineEdit:focus {
                border: 1px solid #5DADE2;
            }
            QLineEdit:disabled {
                background: transparent;
                border: 1px solid rgba(128,128,128,0.1);
            }
        """)


class ConfigTextEdit(QTextEdit):
    """``QTextEdit`` with NoArrowsSpinBox-inspired style, for multi-line
    text areas such as LLM prompt templates.

    Uses slightly larger padding than :class:`ConfigLineEdit` to give
    multi-line editing comfortable whitespace inside the border.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QTextEdit {
                background: rgba(128,128,128,0.13);
                border: 1px solid rgba(128,128,128,0.25);
                border-radius: 4px;
                padding: 4px 8px;
            }
            QTextEdit:focus {
                border: 1px solid #5DADE2;
            }
            QTextEdit:disabled {
                background: transparent;
                border: 1px solid rgba(128,128,128,0.1);
            }
        """)
