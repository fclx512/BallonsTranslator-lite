"""
Module selection widget for BottomBar.

Replaces plain combobox selectors with QToolButton + QMenu + icons.
Simplified from upstream BallonsTranslator (no LLM modality palette).
"""

from qtpy.QtCore import QEvent, QSize, Qt, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import QHBoxLayout, QMenu, QPushButton, QToolButton

from utils import shared as C

from .custom_widget import SmallComboBox, Widget

if C.FLAG_QT6:
    from qtpy.QtGui import QAction
else:
    from qtpy.QtWidgets import QAction


def _set_bottom_aux_button_visible(button: QPushButton, visible: bool):
    """Show/hide an auxiliary button (cog, edit) and relayout parent."""
    if visible == (not button.isHidden()):
        return
    button.setVisible(visible)
    parent = button.parentWidget()
    if parent is not None:
        layout = parent.layout()
        if layout is not None:
            layout.invalidate()
        parent.updateGeometry()


def _instant_popup_mode():
    """Return InstantPopup enum, handling Qt5/Qt6 differences."""
    popup_enum = getattr(QToolButton, "ToolButtonPopupMode", QToolButton)
    return popup_enum.InstantPopup


CFG_ICON = QIcon("icons/leftbar_config_activate.svg")


class ModuleSelectionWidget(Widget):
    """BottomBar module selector: QToolButton + icon + dropdown menu.

    Parameters
    ----------
    fallback_name : str
        Display name when no module is selected (also used as tooltip).
    icon_filename : str
        SVG file name under ``icons/``, e.g. ``"textdetect.svg"``.
    """

    cfg_clicked = Signal()
    src_changed = Signal(str)
    tgt_changed = Signal(str)

    def __init__(self, fallback_name: str, icon_filename: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fallback_name = fallback_name
        self.icon_filename = icon_filename

        # Hidden combobox holds the actual value (used by ModuleManager).
        self.selector = SmallComboBox()
        self.selector.setVisible(False)
        self.selector.currentTextChanged.connect(self._on_selector_changed)

        # Hidden language selectors (used by translator for Source/Target submenus).
        self.src_selector = SmallComboBox()
        self.tgt_selector = SmallComboBox()
        self.src_selector.setVisible(False)
        self.tgt_selector.setVisible(False)
        self.src_selector.currentTextChanged.connect(self.src_changed)
        self.tgt_selector.currentTextChanged.connect(self.tgt_changed)

        # Visible tool button
        self.tool_btn = QToolButton(self)
        self.tool_btn.setObjectName("BottomBarModuleToolButton")
        self.tool_btn.setToolTip(fallback_name)
        self.tool_btn.setPopupMode(_instant_popup_mode())
        self.tool_btn.setIcon(QIcon("icons/" + icon_filename))
        self.tool_btn.setIconSize(QSize(18, 18))
        style_enum = getattr(Qt, "ToolButtonStyle", Qt)
        self.tool_btn.setToolButtonStyle(style_enum.ToolButtonTextBesideIcon)
        self.tool_btn.setText("  " + fallback_name)

        # Drop-down menu (rebuilt on open)
        self.menu = QMenu(self.tool_btn)
        self.tool_btn.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.rebuildMenu)

        # Config cog button — shown on hover
        self.cfg_btn = QPushButton()
        self.cfg_btn.clicked.connect(self.cfg_clicked)
        self.cfg_btn.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self.tool_btn)
        layout.addWidget(self.cfg_btn)
        self.updateButtonText()

    # ── Hover behaviour ─────────────────────────────────────────────

    def enterEvent(self, event: QEvent) -> None:
        _set_bottom_aux_button_visible(self.cfg_btn, True)
        self.cfg_btn.setIcon(CFG_ICON)
        return super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.cfg_btn.setIcon(QIcon())
        _set_bottom_aux_button_visible(self.cfg_btn, False)
        return super().leaveEvent(event)

    # ── Public API (mirrors the old SelectionWithConfigWidget API) ──

    def blockSignals(self, block: bool):
        self.selector.blockSignals(block)
        self.src_selector.blockSignals(block)
        self.tgt_selector.blockSignals(block)
        super().blockSignals(block)

    def setSelectedValue(self, value: str, block_signals=True):
        """Set the current module without necessarily triggering signals."""
        if block_signals:
            self.blockSignals(True)
        self.selector.setCurrentText(value)
        if block_signals:
            self.blockSignals(False)
        self.updateButtonText()

    def updateButtonText(self, *args):
        """Sync tool-button label from the hidden combobox value."""
        name = self.selector.currentText()
        if not name:
            name = self.fallback_name
        self.tool_btn.setText("  " + name)

    def setTranslatorMetadata(self, name, supported_src_list, supported_tgt_list, lang_source, lang_target):
        """Populate language selectors from a translator instance.

        Parameters
        ----------
        name : str
            Translator module name to select.
        supported_src_list : list of str
            Available source languages.
        supported_tgt_list : list of str
            Available target languages.
        lang_source : str
            Currently selected source language.
        lang_target : str
            Currently selected target language.
        """
        self.blockSignals(True)
        self.src_selector.clear()
        self.tgt_selector.clear()
        self.src_selector.addItems(supported_src_list)
        self.tgt_selector.addItems(supported_tgt_list)
        self.selector.setCurrentText(name)
        self.src_selector.setCurrentText(lang_source)
        self.tgt_selector.setCurrentText(lang_target)
        self.blockSignals(False)
        self.updateButtonText()

    # ── Menu management ─────────────────────────────────────────────

    def rebuildMenu(self):
        """Populate the drop-down menu from hidden combobox items."""
        self.menu.clear()
        current = self.selector.currentText()
        for i in range(self.selector.count()):
            text = self.selector.itemText(i)
            # Separator items (insertSeparator) have empty text.
            if not text:
                self.menu.addSeparator()
                continue
            action = QAction(text, self.menu)
            action.setCheckable(True)
            action.setChecked(text == current)
            # Capture ``text`` via default argument to avoid closure issues.
            action.triggered.connect(
                lambda checked=False, value=text: self.selector.setCurrentText(value)
            )
            self.menu.addAction(action)

        # Language submenus (used by translator).
        if self.src_selector.count() > 0:
            self.menu.addSeparator()
            self._addLanguageMenus()

    def _addLanguageMenus(self):
        """Add source/target language submenus (used by translator)."""
        # Source language submenu
        source_menu = QMenu(
            self.tr("Source - {language}").format(
                language=self.src_selector.currentText()
            ),
            self.menu,
        )
        self.menu.addMenu(source_menu)
        for i in range(self.src_selector.count()):
            lang = self.src_selector.itemText(i)
            action = QAction(lang, source_menu)
            action.setCheckable(True)
            action.setChecked(lang == self.src_selector.currentText())
            action.triggered.connect(
                lambda checked=False, value=lang: self.src_selector.setCurrentText(value)
            )
            source_menu.addAction(action)

        # Target language submenu
        target_menu = QMenu(
            self.tr("Target - {language}").format(
                language=self.tgt_selector.currentText()
            ),
            self.menu,
        )
        self.menu.addMenu(target_menu)
        for i in range(self.tgt_selector.count()):
            lang = self.tgt_selector.itemText(i)
            action = QAction(lang, target_menu)
            action.setCheckable(True)
            action.setChecked(lang == self.tgt_selector.currentText())
            action.triggered.connect(
                lambda checked=False, value=lang: self.tgt_selector.setCurrentText(value)
            )
            target_menu.addAction(action)

    # ── Internal slots ──────────────────────────────────────────────

    def _on_selector_changed(self, text: str):
        """Forward the hidden combobox change to the button text."""
        self.updateButtonText()
