"""Compact disclosure rows shared by the effects and transform editors.

Only reorganizes widgets: existing cards, controls and edit-session signals
remain the owners of parameter values and transactions.
"""

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class AppearanceEntry(QObject):
    expanded_requested = Signal(bool)

    def __init__(self, card, changed):
        super().__init__(card)
        self.card = card
        self.expanded = True
        self._changed = changed
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = card.layout()
        header = layout.itemAt(0).layout()
        title = card.title_label
        position = header.indexOf(title)
        header.removeWidget(title)
        title.hide()
        self.toggle = QToolButton(card)
        self.toggle.setObjectName('AppearanceEntryToggle')
        self.toggle.setText(title.text())
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.clicked.connect(self.expanded_requested.emit)
        header.insertWidget(position, self.toggle)
        self.summary = QLabel(card)
        self.summary.setObjectName('AppearanceEntrySummary')
        self.summary.setMaximumWidth(110)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.insertWidget(position + 1, self.summary, 1)
        self.body = QWidget(card)
        self.body.setObjectName('AppearanceEntryBody')
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(6)
        while layout.count() > 1:
            item = layout.takeAt(1)
            if item.widget() is not None:
                body_layout.addWidget(item.widget())
            elif item.layout() is not None:
                child = item.layout()
                child.setParent(None)
                body_layout.addLayout(child)
            else:
                body_layout.addItem(item)
        layout.addWidget(self.body)
        layout.setContentsMargins(2, 5, 2, 6)
        layout.setSpacing(0)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.toggle.setChecked(self.expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if self.expanded else Qt.ArrowType.RightArrow)
        self.body.setVisible(self.expanded)
        sync = getattr(self.card, '_sync_action_icons', None) or getattr(self.card, '_sync_action_visibility', None)
        if sync is not None:
            sync()
        self.card.layout().invalidate()
        self.card.updateGeometry()
        self._changed()

    def set_summary(self, text):
        self.summary.setText(text)
        self.summary.setToolTip(text)
