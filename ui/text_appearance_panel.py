"""One canvas-side inspector hosting both existing text appearance editors."""

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class TextAppearancePanel(QWidget):
    page_changing = Signal()

    def __init__(self, effects, transforms, parent=None):
        super().__init__(parent)
        self.setObjectName('TextAppearancePanel')
        self.effects = effects
        self.transforms = transforms
        self.buttons = []
        tabs = QHBoxLayout()
        tabs.setContentsMargins(6, 0, 6, 0)
        tabs.setSpacing(6)
        self.stack = QStackedWidget(self)
        for index, (title, panel) in enumerate(((self.tr('Effects'), effects), (self.tr('Transforms'), transforms))):
            button = QToolButton(self)
            button.setObjectName('AppearanceTab')
            button.setText(title)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setFixedHeight(30)
            button.clicked.connect(lambda _checked, index=index: self.show_page(index))
            tabs.addWidget(button)
            self.buttons.append(button)
            self.stack.addWidget(panel)
            panel.setMinimumHeight(0)
            panel.setMaximumHeight(16777215)
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            panel.inspector_hosted = True
        # QStackedWidget otherwise takes the hidden page's natural height too.
        self.stack.setMinimumSize(0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(tabs)
        layout.addWidget(self.stack, 1)
        self.show_page(0, settle=False)

    def show_page(self, index, *, settle=True):
        index = int(index)
        if index != self.stack.currentIndex() and settle:
            self.page_changing.emit()
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.buttons):
            button.setChecked(i == index)

    def set_counts(self, effects, transforms):
        self.buttons[0].setText(self.tr('Effects') + f' · {effects}')
        self.buttons[1].setText(self.tr('Transforms') + f' · {transforms}')
