"""Context Translation Log Dialog — non-modal log window for batch translation detail."""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ContextLogDialog(QDialog):
    """Non-modal log window showing per-batch translation detail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Context Translation Log"))
        self.setMinimumSize(520, 360)
        self.resize(560, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet(
            "font-family: Consolas, 'Noto Sans Mono', 'Courier New', monospace; "
            "font-size: 13px;"
        )
        layout.addWidget(self.output, 1)

        btn_row = QVBoxLayout()
        clear_btn = QPushButton(self.tr("Clear"))
        clear_btn.clicked.connect(self.output.clear)
        btn_row.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(btn_row)

    def append(self, text: str):
        """Append one line of text and auto-scroll."""
        self.output.appendPlainText(text)
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())
