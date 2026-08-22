"""Floating dialog with common manga/comic symbols for quick insertion."""
from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class QuickSymbolDialog(QDialog):
    """Always-on-top symbol palette. Click a symbol to insert it at cursor."""

    # Symbols grouped by category
    _GROUPS = [
        ("Quotes", [
            "「", "」", "『", "』", "〝", "〟", "【", "】", "（", "）",
        ]),
        ("Punctuation", [
            "！", "？", "…", "——", "～", "〜", "〰", "·", "‼", "⁉",
        ]),
        ("Decoratives", [
            "※", "♥", "♡", "●", "○", "■", "□", "◆", "◇", "♪", "♫", "♬",
        ]),
        ("Other", [
            "　",  # full-width space
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Quick Symbol"))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)

        # Track the last known text-edit focus so we can insert
        # even after the dialog or its buttons steal focus on click.
        self._last_text_focus: Optional[QWidget] = None
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        from utils.config import pcfg

        groups = list(self._GROUPS)
        custom_chars = (
            getattr(pcfg, "quick_insert_characters", "") or ""
        ).strip()
        if custom_chars:
            groups.append(
                (
                    "Custom",
                    [ch for ch in custom_chars if not ch.isspace()],
                )
            )

        for group_name, symbols in groups:
            label = QLabel(self.tr(group_name))
            label.setStyleSheet("font-weight: bold; font-size: 11px;")
            layout.addWidget(label)

            grid = QGridLayout()
            grid.setSpacing(4)
            row, col = 0, 0
            max_cols = 5
            for sym in symbols:
                btn = QPushButton(sym)
                btn.setFixedSize(48, 32)
                btn.setToolTip(sym)
                # Don't steal focus from the text editor
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.clicked.connect(self._on_symbol_clicked)
                grid.addWidget(btn, row, col)
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1
            layout.addLayout(grid)

        self.setLayout(layout)

    def _on_focus_changed(self, old: Optional[QWidget], new: Optional[QWidget]):
        """Track the last focused text-edit widget for symbol insertion."""
        if isinstance(new, (QTextEdit, QPlainTextEdit)):
            self._last_text_focus = new

    def _on_symbol_clicked(self):
        """Insert the clicked symbol into the last known text editor."""
        btn = self.sender()
        if not isinstance(btn, QPushButton):
            return
        symbol = btn.text()

        target = self._last_text_focus or QApplication.focusWidget()
        if isinstance(target, (QTextEdit, QPlainTextEdit)):
            # Use SourceTextEdit's dedicated method if available, so the canvas
            # text item updates in real-time even without widget focus.
            if hasattr(target, "insert_external_text"):
                target.insert_external_text(symbol)
            else:
                cursor = target.textCursor()
                cursor.insertText(symbol)
                target.setTextCursor(cursor)
