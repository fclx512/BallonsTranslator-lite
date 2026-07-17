"""Custom glossary dialog for context translation.

Opened from the Run dialog when the user clicks "Custom Terms...".
Allows manual entry of source→target term pairs.
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

    Each line should be: source → target
    Supported separators: →  ->  :
    """

    def __init__(self, parent=None, existing_terms: Dict[str, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Custom Glossary"))
        self.setMinimumSize(420, 320)
        self.resize(480, 400)
        self._existing = dict(existing_terms) if existing_terms else {}
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        hint = QLabel(
            self.tr(
                "Enter one term per line: source → target\n"
                "Supported separators:   →   |   ->   |   :"
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            self.tr("e.g. 天馬 → 天马\n氷の女王 → 冰之女王")
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
        """Pre-fill editor with existing terms."""
        if not self._existing:
            return
        lines = [f"{s} → {t}" for s, t in self._existing.items()]
        self.editor.setPlainText("\n".join(lines))

    def get_terms(self) -> Dict[str, str]:
        """Parse editor text and return {source: target} dict.

        Lines that don't match the expected format are silently skipped.
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
