from qtpy.QtCore import Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from utils.shared import CONFIG_FONTSIZE_CONTENT


class ConfigSectionHeader(QWidget):
    """Reusable section header for ConfigPanel pages.

    A plain left-aligned bold label with uniform top/bottom margins.  It is
    intentionally lightweight (no background, no border) so that pages which
    use it stay visually consistent with the rest of the settings UI.
    """

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ConfigSectionHeader")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 6)
        layout.setSpacing(0)

        self._label = QLabel(text)
        self._label.setObjectName("ConfigSectionHeaderLabel")
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        font = self._label.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT)
        font.setWeight(QFont.Weight.Bold)
        self._label.setFont(font)

        layout.addWidget(self._label)
        layout.addStretch()

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()
