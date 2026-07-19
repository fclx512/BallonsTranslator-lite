"""Custom glossary dialog for context translation.

Opened from the Run dialog when the user clicks "Custom...".
Allows manual entry or natural-language description of
source→target term pairs.  The raw text is later sent to the
LLM to produce a structured glossary before translation begins.
"""

import re
from typing import Dict

from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

_SEP = re.compile(r"\s*(→|->|:)\s*")


class CustomGlossaryDialog(QDialog):
    """Dialog for entering custom translation terms.

    Supports two input styles:
    • Structured: one term per line,  source → target
    • Natural language: plain descriptions the LLM will parse later.

    Use get_raw_text() to retrieve the editor content for AI processing,
    or get_terms() for structured-parsed results.
    """

    def __init__(self, parent=None, initial_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Custom Glossary"))
        self.setMinimumSize(420, 320)
        self.resize(480, 400)
        self._initial_text = initial_text
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        hint = QLabel(
            self.tr('Enter one term per line: source → target\n\nYou can also use natural language, e.g.:\n  The protagonist is Goku, the villain is Frieza')
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            self.tr("e.g.  Dragon Ball → 龙珠\nOne Piece → 海贼王\n\nOr: 主角叫鸣人，反派叫佐助")
        )
        layout.addWidget(self.editor, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate(self):
        """Pre-fill editor with initial text."""
        if self._initial_text:
            self.editor.setPlainText(self._initial_text)

    def get_raw_text(self) -> str:
        """Return the raw editor content for AI processing."""
        return self.editor.toPlainText()

    def get_terms(self) -> Dict[str, str]:
        """Parse editor text and return {source: target} dict.

        Lines that don't match the expected separator pattern
        are silently skipped — they will be handled later by the
        AI glossary generator.
        """
        result: Dict[str, str] = {}
        for line in self.editor.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            m = _SEP.split(line, maxsplit=1)
            if len(m) == 3:
                src, _, tgt = m
                src = src.strip()
                tgt = tgt.strip()
                if src and tgt:
                    result[src] = tgt
        return result
