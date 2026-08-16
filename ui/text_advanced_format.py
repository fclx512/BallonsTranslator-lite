from qtpy.QtCore import Qt
from qtpy.QtWidgets import QPushButton


class TextStyleEntryButton(QPushButton):
    """Capsule-style entry button that opens the unified Text Style dialog.

    Replaces the old inline TextAdvancedFormatPanel: opacity, line spacing,
    shadow and gradient now live in the dialog (ui/shadow_gradient_dialog.py).
    """

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setProperty("capsule", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
