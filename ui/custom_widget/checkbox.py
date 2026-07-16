from qtpy.QtGui import QMouseEvent
from qtpy.QtWidgets import QCheckBox


class ConfigCheckBox(QCheckBox):
    """QCheckBox with the ``ConfigCheckBox`` objectName pre-set.

    The theme stylesheet uses ``QCheckBox#ConfigCheckBox`` selectors
    to apply consistent indicator sizing, border, hover, and checked
    icon (see ``config/stylesheet.css``).

    Drop-in replacement for the repeated pattern::

        cb = QCheckBox()
        cb.setObjectName('ConfigCheckBox')
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName('ConfigCheckBox')


class QFontChecker(QCheckBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class AlignmentChecker(QCheckBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.isChecked():
            return event.accept()
        return super().mousePressEvent(event)
