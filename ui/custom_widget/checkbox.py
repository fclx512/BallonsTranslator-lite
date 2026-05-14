from qtpy.QtWidgets import QCheckBox
from qtpy.QtGui import QMouseEvent

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
