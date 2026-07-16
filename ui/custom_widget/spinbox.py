"""QSpinBox with up/down arrow buttons hidden.

Drop-in replacement for the repeated no-arrow spinbox style
that previously existed as inline QSS in several files.
"""

from qtpy.QtWidgets import QSpinBox


class NoArrowsSpinBox(QSpinBox):
    """QSpinBox with hidden arrow buttons and transparent-ish background.

    All style is applied once in __init__; callers only need to set
    range, value, and fixedWidth as usual.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QSpinBox {
                background: rgba(128,128,128,0.13);
                border: 1px solid rgba(128,128,128,0.25);
                border-radius: 4px;
                padding: 2px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px;
                height: 0px;
            }
            QSpinBox::disabled {
                background: transparent;
                border: 1px solid rgba(128,128,128,0.1);
            }
        """)
