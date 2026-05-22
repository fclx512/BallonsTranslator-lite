"""Python-QML communication bridge for curtain animations.

A single QObject instance exposed to QML as ``pyBridge``.  QML curtains call
back through this bridge when animations finish, so MainWindow can sync widget
visibility.
"""

from qtpy.QtCore import QObject, Signal, Slot


class QmlBridge(QObject):
    """Bridge object shared across all QML curtain overlays."""

    # QML -> Python: emitted when a curtain slide animation completes
    slideComplete = Signal(str, bool)   # panelId ("config"|"search"), showing

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

    @Slot(str, bool)
    def onSlideComplete(self, panel_id: str, showing: bool):
        """Called by QML curtain when slide animation finishes."""
        self.slideComplete.emit(panel_id, showing)
