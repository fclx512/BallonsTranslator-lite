"""Combined tools dialog with tabs for dependency check and model file check."""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QTabWidget,
    QVBoxLayout,
)

from ui.dependency_dialog import DependencyPanel
from ui.model_check_dialog import ModelCheckPanel


class ToolsDialog(QDialog):
    """Tabbed dialog: Check Dependencies | Check Model Files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Tools"))
        self.setMinimumSize(800, 550)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)  # Chrome style

        # Tab 1 — Dependency Check
        self.dep_panel = DependencyPanel(self.tabs)
        self.tabs.addTab(self.dep_panel, self.tr("Check Dependencies"))

        # Tab 2 — Model File Check
        self.model_panel = ModelCheckPanel(self.tabs)
        self.tabs.addTab(self.model_panel, self.tr("Check Model Files"))

        layout.addWidget(self.tabs)
