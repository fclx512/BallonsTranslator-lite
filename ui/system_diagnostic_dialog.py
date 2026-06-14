"""System diagnostic dialog.

Runs ``utils.env_diagnostic.run_diagnostic()`` in a background thread and
displays the result in a read-only text area alongside a "Run" button.
"""

from qtpy.QtCore import QThread, Signal
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

# ── Worker thread ──────────────────────────────────────────────────────────


class _DiagnosticWorker(QThread):
    finished = Signal(dict)

    def run(self):
        from utils.env_diagnostic import run_diagnostic

        result = run_diagnostic()
        self.finished.emit(result)


# ── Dialog ─────────────────────────────────────────────────────────────────


class SystemDiagnosticDialog(QDialog):
    """Run a system-environment diagnostic and display the results."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("System Diagnostic"))
        self.setMinimumSize(640, 420)
        self._result = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Text output
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        self.output.setPlaceholderText(
            self.tr('Click "Run Diagnostic" to check your system.')
        )
        layout.addWidget(self.output, 1)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(0)  # indeterminate
        layout.addWidget(self.progress_bar)

        # Buttons
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton(self.tr("Run Diagnostic"))
        self.run_btn.setMinimumHeight(34)
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.setMinimumHeight(34)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _run(self):
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.output.clear()

        self._worker = _DiagnosticWorker(self)
        self._worker.finished.connect(self._on_result)
        self._worker.start()

    def _on_result(self, result: dict):
        lines = result.get("diagnostic_lines", [])
        self.output.setPlainText("\n".join(lines))
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._result = result
