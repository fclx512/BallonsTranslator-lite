from typing import List, Tuple, Union

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QFocusEvent, QFont, QIntValidator, QValidator
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils import shared as C
from utils.config import pcfg
from utils.shared import (
    CONFIG_COMBOBOX_LONG,
    CONFIG_COMBOBOX_MIDEAN,
    CONFIG_COMBOBOX_SHORT,
    CONFIG_FONTSIZE_CONTENT,
    CONFIG_FONTSIZE_HEADER,
    CONFIG_SUBBLOCK_SPACING,
    CONFIGBLOCK_CONTENT_MARGINS,
    GROUPBOX_CONTENT_MARGINS,
    LINEEDIT_FIXHEIGHT,
    NAVLIST_HEADER_FONTSIZE,
    NAVLIST_WIDTH,
)

from .custom_widget import ConfigComboBox, PanelGroupBox, Widget
from .module_parse_widgets import (
    InpaintConfigPanel,
    OCRConfigPanel,
    TextDetectConfigPanel,
    TranslatorConfigPanel,
)


class CustomIntValidator(QIntValidator):

    def __init__(self, bottom: int, top: int, ndigits: int = None, parent = None):
        super().__init__(bottom=bottom, top=top, parent=parent)
        self.ndigits = ndigits

    def validate(self, s: str, pos: int) -> object:
        if not s.isnumeric():
            if s != '':
                return (QValidator.State.Invalid, s, pos)
            else:
                return (QValidator.State.Intermediate, s, pos)

        s_ori = s
        d = int(s)
        s = str(d)
        if len(s) != len(s_ori):
            pos -= len(s_ori) - len(s)
        if len(s) > self.ndigits:
            ndel = len(s) - self.ndigits
            s = s[ndel:]
            pos -= ndel
        else:
            if d > self.top():
                if s[-1] == '0':
                    d = self.top()
                else:
                    d = d % self.top()
            d = max(d, self.bottom())
            s = str(d)
        return (QValidator.State.Acceptable, s, pos)


class PercentageLineEdit(QLineEdit):

    finish_edited = Signal(str)

    def __init__(self, default_value: str = '100', parent=None) -> None:
        super().__init__(default_value, parent=parent)
        validator = CustomIntValidator(0, 100, 3)
        self.setValidator(validator)
        self.textEdited.connect(self.on_text_edited)
        self._edited = False

    def on_text_edited(self):
        self._edited = True

    def focusOutEvent(self, e: QFocusEvent) -> None:
        if self._edited:
            text = self.text()
            if not text.isnumeric():
                text = '100'
                self.setText(text)
            self.finish_edited.emit(text)

        return super().focusOutEvent(e)


class ConfigTextLabel(QLabel):
    def __init__(self, text: str, fontsize: int, font_weight: int = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setText(text)
        font = self.font()
        if font_weight is not None:
            font.setWeight(font_weight)
        font.setPointSizeF(fontsize)
        self.setFont(font)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setOpenExternalLinks(True)

    def setActiveBackground(self):
        from ui.misc import get_theme_color
        c = get_theme_color()
        self.setStyleSheet(f"background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 51);")


class ConfigSubBlock(Widget):
    def __init__(self, widget: Union[QWidget, QLayout], name: str = None, description: str = None, vertical_layout=True, insert_stretch: bool = False, content_margins = (24, 6, 24, 6)) -> None:
        super().__init__()
        if vertical_layout:
            layout = QVBoxLayout(self)
        else:
            layout = QHBoxLayout(self)
        self.name = name
        if name is not None:
            textlabel = ConfigTextLabel(name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal)
            self.name_label = textlabel
            layout.addWidget(textlabel)
        if description is not None:
            layout.addWidget(ConfigTextLabel(description, CONFIG_FONTSIZE_CONTENT-2))
        if insert_stretch:
            layout.insertStretch(-1)
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
        self.widget = widget
        self.setContentsMargins(*content_margins)


def combobox_with_label(sel: List[str], name: str, description: str = None, vertical_layout: bool = False, target_block: QWidget = None, fix_size: bool = True, parent: QWidget = None, insert_stretch: bool = False) -> Tuple[ConfigComboBox, QWidget]:
    combox = ConfigComboBox(fix_size=fix_size, scrollWidget=parent)
    combox.addItems(sel)
    if target_block is None:
        sublock = ConfigSubBlock(combox, name, description, vertical_layout=vertical_layout, insert_stretch=insert_stretch)
        sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        sublock.layout().setSpacing(CONFIG_SUBBLOCK_SPACING)
        return combox, sublock
    else:
        layout = target_block.layout()
        layout.addSpacing(CONFIG_SUBBLOCK_SPACING)
        layout.addWidget(ConfigTextLabel(name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal))
        layout.addWidget(combox)
        return combox, target_block

def checkbox_with_label(name: str, description: str = None, target_block: QWidget = None):
    checkbox = QCheckBox()
    if description is not None:
        font = checkbox.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        checkbox.setFont(font)
        checkbox.setText(description)
        vertical_layout = True
    else:
        vertical_layout = False

    if target_block is None:
        sublock = ConfigSubBlock(checkbox, name, vertical_layout=vertical_layout)
        if vertical_layout is False:
            sublock.layout().addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))
        target_block = sublock
    return checkbox, target_block



class ConfigBlock(Widget):

    def __init__(self, header: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.header = ConfigTextLabel(header, CONFIG_FONTSIZE_HEADER)
        self.vlayout = QVBoxLayout(self)
        self.vlayout.addWidget(self.header)
        self.setContentsMargins(*CONFIGBLOCK_CONTENT_MARGINS)
        self.subblock_list = []
        self.index: int = 0

    def setIndex(self, index: int):
        self.index = index

    def addLineEdit(self, name: str = None, description: str = None, vertical_layout: bool = False):
        le = QLineEdit()
        le.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        le.setFixedHeight(LINEEDIT_FIXHEIGHT)
        sublock = ConfigSubBlock(le, name, description, vertical_layout)
        if vertical_layout is False:
            sublock.layout().addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))
        self.addSublock(sublock)
        sublock.layout().setSpacing(CONFIG_SUBBLOCK_SPACING)
        return le, sublock

    def addTextLabel(self, text: str = None):
        label = ConfigTextLabel(text, CONFIG_FONTSIZE_HEADER)
        self.vlayout.addWidget(label)

    def addSublock(self, sublock: ConfigSubBlock):
        self.vlayout.addWidget(sublock)
        self.subblock_list.append(sublock)

    def addCombobox(self, sel: List[str], name: str, description: str = None, vertical_layout: bool = False, target_block: QWidget = None, fix_size: bool = True) -> Tuple[ConfigComboBox, QWidget]:
        combox, sublock = combobox_with_label(sel, name, description, vertical_layout, target_block, fix_size, parent=self)
        if target_block is None:
            self.addSublock(sublock)
        return combox, sublock

    def addBlockWidget(self, widget: Union[QWidget, QLayout], name: str = None, description: str = None, vertical_layout: bool = False) -> ConfigSubBlock:
        sublock = ConfigSubBlock(widget, name, description, vertical_layout)
        self.addSublock(sublock)
        return sublock

    def addCheckBox(self, name: str, description: str = None, target_block: ConfigSubBlock = None) -> QCheckBox:
        checkbox, sublock = checkbox_with_label(name, description, target_block)
        if target_block is None:
            self.addSublock(sublock)
        return checkbox, sublock

    def addGroupedBlock(self, group_title: str, widget: QWidget, object_name: str = None, name: str = None, description: str = None) -> ConfigSubBlock:
        group = PanelGroupBox(group_title)
        if object_name:
            group.setObjectName(object_name)
        group_vlayout = group.contentLayout()
        group_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        group_vlayout.setSpacing(0)

        sublock = ConfigSubBlock(widget, name=name, description=description)
        group_vlayout.addWidget(sublock)

        self.vlayout.addWidget(group)
        sublock.section_widget = group
        self.subblock_list.append(sublock)
        return sublock

    def getSubBlockbyIdx(self, idx: int) -> ConfigSubBlock:
        return self.subblock_list[idx]


class ConfigContent(QScrollArea):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.config_block_list: List[ConfigBlock] = []
        self.scrollContent = Widget()
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.scrollContent)
        vlayout = QVBoxLayout()
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scrollContent.setLayout(vlayout)
        self.setWidgetResizable(True)
        self.setContentsMargins(0, 0, 0, 0)
        self.vlayout = vlayout

    def addConfigBlock(self, block: ConfigBlock):
        self.vlayout.addWidget(block)
        self.config_block_list.append(block)

    def scrollToWidget(self, widget: QWidget):
        if C.USE_PYSIDE6:
            self.ensureWidgetVisible(widget, ymargin=widget.height() * 7)
        else:
            self.ensureWidgetVisible(widget, yMargin=widget.height() * 7)


DEFAULT_SHORTCUTS = {
    'prev_page': ['A'],
    'next_page': ['D'],
    'prev_page_alt': ['PgUp'],
    'next_page_alt': ['PgDown'],
    'textedit_mode': ['T'],
    'textblock_mode': ['W'],
    'drawboard_mode': ['P'],
    'zoom_in': ['Ctrl++'],
    'zoom_out': ['Ctrl+-'],
    'preview': ['Tab'],
    'delete_blks': ['Del'],
    'delete_blks_alt': ['Ctrl+D'],
    'select_all': ['Ctrl+A'],
    'bold': ['Ctrl+B'],
    'italic': ['Ctrl+I'],
    'underline': ['Ctrl+U'],
    'undo': ['Ctrl+Z'],
    'redo': ['Ctrl+Y'],
    'page_search': ['Ctrl+F'],
    'global_search': ['Ctrl+G'],
    'escape': ['Escape'],
    'space_inpaint': ['Space'],
    'hand_tool': ['H'],
    'rect_tool': ['R'],
    'inpaint_tool': ['J'],
    'pen_tool': ['B'],
    'merge_tool': ['Ctrl+Shift+M'],
}

_ACTION_NAMES = {
    'prev_page': 'Page Up', 'next_page': 'Page Down', 'prev_page_alt': 'Page Up (alt)',
    'next_page_alt': 'Page Down (alt)', 'textedit_mode': 'Text Editor', 'textblock_mode': 'Text Block',
    'drawboard_mode': 'Draw Board', 'zoom_in': 'Zoom In', 'zoom_out': 'Zoom Out',
    'preview': 'Preview', 'delete_blks': 'Delete', 'delete_blks_alt': 'Delete (alt)',
    'select_all': 'Select All', 'bold': 'Bold', 'italic': 'Italic', 'underline': 'Underline',
    'undo': 'Undo', 'redo': 'Redo', 'page_search': 'Page Search', 'global_search': 'Global Search',
    'escape': 'Escape', 'space_inpaint': 'Inpaint', 'hand_tool': 'Hand Tool',
    'rect_tool': 'Rect Tool', 'inpaint_tool': 'Inpaint Tool', 'pen_tool': 'Pen Tool',
    'merge_tool': 'Merge Tool',
}

# Shortcut groups for organized display
_SHORTCUT_GROUPS = [
    ('Navigation', ['prev_page', 'next_page', 'prev_page_alt', 'next_page_alt']),
    ('View', ['zoom_in', 'zoom_out', 'preview']),
    ('Edit', ['textedit_mode', 'textblock_mode', 'drawboard_mode', 'delete_blks', 'delete_blks_alt',
              'select_all', 'bold', 'italic', 'underline', 'undo', 'redo']),
    ('Tools', ['hand_tool', 'rect_tool', 'inpaint_tool', 'pen_tool', 'merge_tool', 'space_inpaint']),
    ('Search', ['page_search', 'global_search']),
    ('General', ['escape']),
]


class _ShortcutRow(QWidget):
    """A row for editing shortcuts of a single action."""
    shortcut_changed = Signal()

    def __init__(self, action_id: str, parent=None):
        super().__init__(parent)
        self.action_id = action_id
        self._disabled_placeholder = None

        from .theme_helpers import shortcut_styles
        s = shortcut_styles()

        h = QHBoxLayout(self)
        h.setContentsMargins(2, 6, 2, 6)
        h.setSpacing(6)

        # Action name — left column
        name = QLabel(self.tr(_ACTION_NAMES.get(action_id, action_id)))
        name.setStyleSheet(f"color: {s['name_clr']}; background: transparent; border: none;")
        name.setFixedWidth(140)
        h.addWidget(name)

        # Shortcuts pills — middle column (stretches)
        self.shortcuts_widget = QWidget()
        self.shortcuts_widget.setStyleSheet("background: transparent; border: none;")
        self.shortcuts_layout = QHBoxLayout(self.shortcuts_widget)
        self.shortcuts_layout.setContentsMargins(0, 0, 0, 0)
        self.shortcuts_layout.setSpacing(4)
        self.shortcuts_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        h.addWidget(self.shortcuts_widget, 1)

        # Buttons — right column
        btn_container = QWidget()
        btn_container.setFixedWidth(86)
        btn_container.setStyleSheet("background: transparent; border: none;")
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(2)

        # Add button
        self._add_btn = QPushButton('+')
        self._add_btn.setFixedSize(24, 24)
        self._add_btn.setToolTip(self.tr('Add shortcut'))
        self._add_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {s['add_bdr']}; border-radius: 3px; "
            f"color: {s['add_clr']}; background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ border-color: {s['add_hvr_bdr']}; color: {s['add_hvr_clr']}; }}")
        self._add_btn.clicked.connect(self._add_shortcut)
        btn_layout.addWidget(self._add_btn)

        # Clear button
        self._clear_btn = QPushButton('Del')
        self._clear_btn.setFixedSize(28, 24)
        self._clear_btn.setToolTip(self.tr('Disable this shortcut'))
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: 3px; color: {s['btn_clr']}; "
            f"background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ color: {s['close_hvr']}; }}")
        self._clear_btn.clicked.connect(self._clear)
        btn_layout.addWidget(self._clear_btn)

        # Reset button
        self._reset_btn = QPushButton('Rst')
        self._reset_btn.setFixedSize(28, 24)
        self._reset_btn.setToolTip(self.tr('Reset to Default'))
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: 3px; color: {s['btn_clr']}; "
            f"background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ color: {s['reset_hvr']}; }}")
        self._reset_btn.clicked.connect(self._reset)
        btn_layout.addWidget(self._reset_btn)

        h.addWidget(btn_container)

        self._rebuild_pills()

    def _get_keys(self) -> list:
        """Get current shortcut keys respecting explicit empty-list (disabled)."""
        if self.action_id in pcfg.shortcuts:
            keys = pcfg.shortcuts[self.action_id]
            if not isinstance(keys, list):
                keys = [keys] if keys else []
            return keys
        return list(DEFAULT_SHORTCUTS.get(self.action_id, []))

    def _rebuild_pills(self):
        # Clear existing pills and placeholder
        while self.shortcuts_layout.count():
            item = self.shortcuts_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._disabled_placeholder = None

        keys = self._get_keys()
        if keys:
            from .theme_helpers import shortcut_styles
            s = shortcut_styles()
            for k in keys:
                # Pill: QFrame container — QFrame selector works reliably in PyQt6
                frame = QFrame()
                frame.setFrameShape(QFrame.Shape.NoFrame)
                fl = QHBoxLayout(frame)
                fl.setContentsMargins(8, 1, 4, 1)
                fl.setSpacing(2)
                lbl = QLabel(k)
                lbl.setStyleSheet(
                    f"color: {s['pill_text']}; background: transparent; border: none;")
                fl.addWidget(lbl)
                close_btn = QPushButton('x')
                close_btn.setFixedSize(22, 22)
                close_btn.setStyleSheet(
                    f"QPushButton {{ border: none; border-radius: 2px; color: {s['close_clr']}; "
                    f"background: transparent; padding: 0px; }}"
                    f"QPushButton:hover {{ color: {s['close_hvr']}; "
                    f"background: rgba(200,50,50,0.2); }}")
                close_btn.clicked.connect(lambda checked, ks=k: self._remove_shortcut(ks))
                fl.addWidget(close_btn)
                frame.setStyleSheet(
                    f"QFrame {{ background: {s['pill_bg']}; border-radius: 4px; }}")
                self.shortcuts_layout.addWidget(frame)
        else:
            # Show disabled placeholder
            from .theme_helpers import shortcut_styles
            s = shortcut_styles()
            self._disabled_placeholder = QLabel(self.tr('— None —'))
            self._disabled_placeholder.setStyleSheet(
                f"color: {s['disabled_clr']}; background: transparent; font-style: italic;")
            self.shortcuts_layout.addWidget(self._disabled_placeholder)

    def _add_shortcut(self):
        edit = QKeySequenceEdit()
        edit.setFixedWidth(120)
        edit.setFixedHeight(24)
        edit.setStyleSheet(
            "QKeySequenceEdit { padding: 1px 4px; }")
        self.shortcuts_layout.addWidget(edit)
        edit.setFocus()

        def on_finished():
            seq = edit.keySequence().toString()
            edit.deleteLater()
            if seq:
                keys = self._get_keys()
                if seq not in keys:
                    keys.append(seq)
                    pcfg.shortcuts[self.action_id] = keys
                self._rebuild_pills()
                self.shortcut_changed.emit()
            else:
                self._rebuild_pills()

        edit.editingFinished.connect(on_finished)

    def _remove_shortcut(self, key_seq: str):
        keys = self._get_keys()
        if key_seq in keys:
            keys.remove(key_seq)
            pcfg.shortcuts[self.action_id] = keys
        self._rebuild_pills()
        self.shortcut_changed.emit()

    def _clear(self):
        pcfg.shortcuts[self.action_id] = []
        self._rebuild_pills()
        self.shortcut_changed.emit()

    def _reset(self):
        defaults = DEFAULT_SHORTCUTS.get(self.action_id, [])
        if defaults:
            pcfg.shortcuts[self.action_id] = list(defaults)
        elif self.action_id in pcfg.shortcuts:
            del pcfg.shortcuts[self.action_id]
        self._rebuild_pills()
        self.shortcut_changed.emit()

    def refresh(self):
        self._rebuild_pills()


class ShortcutEditor(QWidget):
    shortcut_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = {}
        self.setMinimumHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)

        # Create scroll area for grouped shortcuts
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.viewport().setContentsMargins(0, 0, 0, 0)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #666;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        scroll_content = QWidget()
        self._content_layout = QVBoxLayout(scroll_content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)

        # Build grouped layout
        from .theme_helpers import shortcut_styles
        s = shortcut_styles()

        for group_name, action_ids in _SHORTCUT_GROUPS:
            group_box = PanelGroupBox(self.tr(group_name))
            group_layout = group_box.contentLayout()

            for idx, action_id in enumerate(action_ids):
                if idx > 0:
                    sep = QWidget()
                    sep.setFixedHeight(1)
                    sep.setStyleSheet(
                        f"background: {s['add_bdr']};")
                    group_layout.addWidget(sep)
                row = _ShortcutRow(action_id)
                row.shortcut_changed.connect(self.shortcut_changed)
                self._rows[action_id] = row
                group_layout.addWidget(row)

            self._content_layout.addWidget(group_box)
            self._content_layout.addSpacing(6)

        self._content_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

    def refresh(self):
        for row in self._rows.values():
            row.refresh()


class ShortcutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr('Shortcut Editor'))
        self.setMinimumSize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shortcut_editor = ShortcutEditor()
        self.shortcut_editor.shortcut_changed.connect(self._on_shortcut_changed)
        layout.addWidget(self.shortcut_editor)

    def _on_shortcut_changed(self):
        from utils.config import save_config
        save_config()


class FontExcludeDialog(QDialog):
    """Dialog for selecting which fonts to exclude from the font list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr('Font Exclusion'))
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        # Search bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr('Search fonts...'))
        self.search_edit.textChanged.connect(self._filter_lists)
        layout.addWidget(self.search_edit)

        # Side-by-side list widgets
        lists_layout = QHBoxLayout()

        # Available fonts list
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.tr('Available Fonts')))
        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        left_layout.addWidget(self.available_list)
        lists_layout.addLayout(left_layout)

        # Center buttons
        btn_layout = QVBoxLayout()
        btn_layout.addStretch()
        self.hide_btn = QPushButton('>')
        self.hide_btn.setFixedWidth(40)
        self.hide_btn.setToolTip(self.tr('Hide selected fonts'))
        self.hide_btn.clicked.connect(self._hide_fonts)
        btn_layout.addWidget(self.hide_btn)
        self.show_btn = QPushButton('<')
        self.show_btn.setFixedWidth(40)
        self.show_btn.setToolTip(self.tr('Show selected fonts'))
        self.show_btn.clicked.connect(self._show_fonts)
        btn_layout.addWidget(self.show_btn)
        btn_layout.addStretch()
        lists_layout.addLayout(btn_layout)

        # Excluded fonts list
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel(self.tr('Hidden Fonts')))
        self.excluded_list = QListWidget()
        self.excluded_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        right_layout.addWidget(self.excluded_list)
        lists_layout.addLayout(right_layout)

        layout.addLayout(lists_layout)

        # OK / Cancel buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Populate lists
        self._populate_lists()

    def _add_font_item(self, list_widget: QListWidget, font_name: str):
        """Add a font name to a list widget with its own typeface as preview."""
        item = QListWidgetItem(font_name)
        item.setFont(QFont(font_name, 11))
        list_widget.addItem(item)

    def _populate_lists(self):
        from utils import shared
        from utils.config import pcfg
        self.available_list.clear()
        self.excluded_list.clear()

        for font in shared.get_filtered_font_list(pcfg.excluded_fonts):
            self._add_font_item(self.available_list, font)

        for font in pcfg.excluded_fonts:
            self._add_font_item(self.excluded_list, font)

    def _filter_lists(self):
        text = self.search_edit.text().lower()
        for i in range(self.available_list.count()):
            item = self.available_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())
        for i in range(self.excluded_list.count()):
            item = self.excluded_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _hide_fonts(self):
        for item in self.available_list.selectedItems():
            self.available_list.takeItem(self.available_list.row(item))
            self._add_font_item(self.excluded_list, item.text())

    def _show_fonts(self):
        for item in self.excluded_list.selectedItems():
            self.excluded_list.takeItem(self.excluded_list.row(item))
            self._add_font_item(self.available_list, item.text())

    def get_excluded_fonts(self) -> List[str]:
        return [self.excluded_list.item(i).text() for i in range(self.excluded_list.count())]


class ConfigPanel(Widget):

    save_config = Signal()
    unload_models = Signal()
    reload_textstyle = Signal(bool)
    font_exclusion_changed = Signal()
    profiles_changed = Signal()
    theme_changed = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("ConfigPanel")

        self.configContent = ConfigContent()

        dlConfigPanel = self.addConfigBlock(self.tr('DL Module'))
        generalConfigPanel = self.addConfigBlock(self.tr('General'))

        label_text_det = self.tr('Text Detection')
        label_text_ocr = self.tr('OCR')
        label_inpaint = self.tr('Inpaint')
        label_translator = self.tr('Translator')
        label_startup = self.tr('Startup')
        label_typesetting = self.tr('Typesetting')
        label_save = self.tr('Save')
        label_shortcuts = self.tr('Miscellaneous')

        # === Model management ===
        model_group = PanelGroupBox(self.tr('Models'))
        model_vlayout = model_group.contentLayout()
        model_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        model_vlayout.setSpacing(0)
        self.load_model_checker, msublock = checkbox_with_label(self.tr('Load models on demand'), description=self.tr('Load models on demand to save memory.'))
        self.load_model_checker.stateChanged.connect(self.on_load_model_changed)
        model_vlayout.addWidget(msublock)
        self.empty_runcache_checker, msublock = checkbox_with_label(self.tr('Empty cache after RUN'), description=self.tr('Empty cache after RUN to save memory.'))
        self.empty_runcache_checker.stateChanged.connect(self.on_runcache_changed)
        model_vlayout.addWidget(msublock)
        self.unload_model_btn = QPushButton(parent=self)
        self.unload_model_btn.setFixedWidth(CONFIG_COMBOBOX_LONG + 32)
        self.unload_model_btn.setText(self.tr('Unload All Models'))
        self.unload_model_btn.clicked.connect(self.unload_models)
        msublock.layout().addWidget(self.unload_model_btn)
        self.manage_profiles_btn = QPushButton(self.tr('Manage API Profiles...'))
        self.manage_profiles_btn.setFixedWidth(CONFIG_COMBOBOX_LONG + 32)
        self.manage_profiles_btn.clicked.connect(self._open_profile_manager)
        msublock.layout().addWidget(self.manage_profiles_btn)
        dlConfigPanel.vlayout.addWidget(model_group)

        self.detect_config_panel = TextDetectConfigPanel(self.tr('Detector'), scrollWidget=self)
        self.detect_sub_block = dlConfigPanel.addGroupedBlock(label_text_det, self.detect_config_panel, object_name="GroupDetect")
        self.detect_config_panel.keep_existing_checker.clicked.connect(self.on_keepline_clicked)

        self.ocr_config_panel = OCRConfigPanel(self.tr('OCR'), scrollWidget=self)
        self.ocr_sub_block = dlConfigPanel.addGroupedBlock(label_text_ocr, self.ocr_config_panel, object_name="GroupOCR")

        self.inpaint_config_panel = InpaintConfigPanel(self.tr('Inpainter'), scrollWidget=self)
        self.inpaint_sub_block = dlConfigPanel.addGroupedBlock(label_inpaint, self.inpaint_config_panel, object_name="GroupInpaint")

        self.trans_config_panel = TranslatorConfigPanel(label_translator, scrollWidget=self)
        self.trans_sub_block = dlConfigPanel.addGroupedBlock(label_translator, self.trans_config_panel, object_name="GroupTranslate")

        # === General: Startup ===
        startup_widget = QWidget()
        startup_layout = QVBoxLayout(startup_widget)
        startup_layout.setContentsMargins(0, 0, 0, 0)
        self.open_on_startup_checker = QCheckBox(self.tr('Reopen last project on startup'))
        self.open_on_startup_checker.stateChanged.connect(self.on_open_onstartup_changed)
        startup_layout.addWidget(self.open_on_startup_checker)
        self.startup_block = generalConfigPanel.addGroupedBlock(label_startup, startup_widget, object_name="GroupGeneral")

        dec_program_str = self.tr('decide by program')
        use_global_str = self.tr('use global setting')

        # Build typesetting wrapper widget
        ts_widget = QWidget()
        ts_layout = QVBoxLayout(ts_widget)
        ts_layout.setContentsMargins(0, 0, 0, 0)
        ts_layout.setSpacing(0)

        global_fntfmt_widget = QWidget()
        global_fntfmt_layout = QGridLayout(global_fntfmt_widget)
        global_fntfmt_layout.setSpacing(0)
        global_fntfmt_widget.setContentsMargins(0, 0, 0, 0)

        b = ConfigSubBlock(global_fntfmt_widget)
        b.layout().setContentsMargins(0, 0, 0, 0)
        b.setContentsMargins(0, 0, 0, 0)
        ts_layout.addWidget(b)
        self.let_fntsize_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Font Size'), parent=self, insert_stretch=True)
        global_fntfmt_layout.addWidget(sublock, 0, 0)

        self.let_fntsize_combox.activated.connect(self.on_fntsize_flag_changed)
        self.let_fntstroke_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Stroke Size'), parent=self, insert_stretch=True)
        self.let_fntstroke_combox.activated.connect(self.on_fntstroke_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 0, 1)

        self.let_fntcolor_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Font Color'), parent=self, insert_stretch=True)
        self.let_fntcolor_combox.activated.connect(self.on_fontcolor_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 1, 0)
        self.let_fnt_scolor_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Stroke Color'), parent=self, insert_stretch=True)
        self.let_fnt_scolor_combox.activated.connect(self.on_font_scolor_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 1, 1)

        self.let_effect_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Effect'), parent=self, insert_stretch=True)
        self.let_effect_combox.activated.connect(self.on_effect_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 2, 0)
        self.let_alignment_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Alignment'), parent=self, insert_stretch=True)
        self.let_alignment_combox.activated.connect(self.on_alignment_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 2, 1)

        self.let_writing_mode_combox, sublock = combobox_with_label([dec_program_str, use_global_str], self.tr('Writing-mode'), parent=self, insert_stretch=True)
        self.let_writing_mode_combox.activated.connect(self.on_writing_mode_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 3, 0)
        self.let_family_combox, sublock = combobox_with_label([self.tr('Keep existing'), self.tr('Always use global setting')], self.tr('Font Family'), parent=self, insert_stretch=True)
        self.let_family_combox.activated.connect(self.on_family_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 3, 1)

        global_fntfmt_layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding), 0, 2)

        self.let_autolayout_checker, al_sublock = checkbox_with_label(
                self.tr('Auto layout'),
                description=self.tr('Split translation into multi-lines according to the extracted balloon region.'))
        self.let_autolayout_checker.stateChanged.connect(self.on_autolayout_changed)
        ts_layout.addWidget(al_sublock)

        self.let_uppercase_checker, uc_sublock = checkbox_with_label(self.tr('To uppercase'))
        self.let_uppercase_checker.stateChanged.connect(self.on_uppercase_changed)
        ts_layout.addWidget(uc_sublock)

        self.let_textstyle_indep_checker, ti_sublock = checkbox_with_label(
                self.tr('Independent text styles for each projects'))
        self.let_textstyle_indep_checker.stateChanged.connect(self.on_textstyle_indep_changed)
        ts_layout.addWidget(ti_sublock)

        self.exclude_fonts_btn = QPushButton(self.tr('Exclude Fonts...'), parent=self)
        self.exclude_fonts_btn.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.exclude_fonts_btn.clicked.connect(self.on_exclude_fonts_clicked)
        btn_sublock = ConfigSubBlock(self.exclude_fonts_btn)
        ts_layout.addWidget(btn_sublock)

        self.max_font_size_edit = QSpinBox()
        self.max_font_size_edit.setRange(10, 1000)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.max_font_size_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.max_font_size_edit.valueChanged.connect(self.on_max_font_size_changed)
        max_font_sublock = ConfigSubBlock(self.max_font_size_edit, self.tr('Max Font Size (px)'))
        ts_layout.addWidget(max_font_sublock)

        self.typesetting_block = generalConfigPanel.addGroupedBlock(label_typesetting, ts_widget, object_name="GroupGeneral")

        # === General: Save ===
        save_widget = QWidget()
        save_layout = QVBoxLayout(save_widget)
        save_layout.setContentsMargins(0, 0, 0, 0)
        save_layout.setSpacing(0)

        self.rst_imgformat_combobox, imsave_sublock = combobox_with_label(
                ['PNG', 'JPG', 'WEBP', 'JXL'], self.tr('Result image format'), parent=self)
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        save_layout.addWidget(imsave_sublock)

        self.rst_autoformat_checker, autoformat_sublock = checkbox_with_label(
            self.tr('Auto detect source format'))
        self.rst_autoformat_checker.stateChanged.connect(self.on_autoformat_changed)
        save_layout.addWidget(autoformat_sublock)

        self.rst_imgquality_edit = PercentageLineEdit('100')
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)

        quality_sublock = ConfigSubBlock(self.rst_imgquality_edit, self.tr('Quality'), vertical_layout=False)
        quality_sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        quality_sublock.layout().insertStretch(-1)
        imsave_sublock.layout().addWidget(quality_sublock)

        self.intermediate_imgformat_combobox, intermediate_imsave_sublock = combobox_with_label(
                ['PNG', 'JXL'], self.tr('Intermediate image format'), parent=self)
        self.intermediate_imgformat_combobox.activated.connect(self.on_intermediate_imgformat_changed)
        save_layout.addWidget(intermediate_imsave_sublock)

        self.save_block = generalConfigPanel.addGroupedBlock(label_save, save_widget, object_name="GroupSave")

        # === General: Miscellaneous (theme + shortcut editor) ===
        misc_widget = QWidget()
        misc_layout = QVBoxLayout(misc_widget)
        misc_layout.setContentsMargins(0, 0, 0, 0)
        misc_layout.setSpacing(8)

        # Theme selector row
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        self.theme_combo = ConfigComboBox()
        self.theme_combo.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        self.theme_combo.activated.connect(self._on_theme_selected)
        theme_row.addWidget(self.theme_combo)

        self.edit_theme_btn = QPushButton(self.tr('Edit...'), parent=self)
        self.edit_theme_btn.setFixedWidth(80)
        self.edit_theme_btn.clicked.connect(self._on_edit_theme)
        theme_row.addWidget(self.edit_theme_btn)
        theme_row.addStretch()

        misc_layout.addLayout(theme_row)

        # Shortcut button
        self.shortcut_btn = QPushButton(self.tr('Edit Shortcuts...'), parent=self)
        self.shortcut_btn.setFixedWidth(CONFIG_COMBOBOX_LONG + 32)
        self.shortcut_btn.clicked.connect(self._open_shortcut_dialog)
        misc_layout.addWidget(self.shortcut_btn)

        self.shortcut_block = generalConfigPanel.addGroupedBlock(label_shortcuts, misc_widget)
        self._refresh_theme_combo()

        # === Navigation list (replaces horizontal nav bar) ===
        self.navList = QListWidget()
        self.navList.setFixedWidth(NAVLIST_WIDTH)
        self.navList.setSpacing(2)
        self.navList.setFrameShape(QListWidget.NoFrame)

        # Build section list with group headers
        sections = [
            ("_header", self.tr("DL Module")),
            (self.detect_sub_block.section_widget, label_text_det),
            (self.ocr_sub_block.section_widget,    label_text_ocr),
            (self.inpaint_sub_block.section_widget, label_inpaint),
            (self.trans_sub_block.section_widget,  label_translator),
            ("_sep", None),
            ("_header", self.tr("General")),
            (self.startup_block.section_widget,    label_startup),
            (self.typesetting_block.section_widget, label_typesetting),
            (self.save_block.section_widget,       label_save),
            (self.shortcut_block.section_widget,   label_shortcuts),
        ]
        self._nav_items = []  # (widget_or_None, row)
        for target, text in sections:
            if target == "_header":
                item = QListWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                f = item.font()
                f.setPointSize(NAVLIST_HEADER_FONTSIZE)
                f.setBold(True)
                item.setFont(f)
                self.navList.addItem(item)
                self._nav_items.append((None, self.navList.count() - 1))
            elif target == "_sep":
                item = QListWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setSizeHint(QSize(0, 4))
                self.navList.addItem(item)
                self._nav_items.append((None, self.navList.count() - 1))
            else:
                item = QListWidgetItem(text)
                self.navList.addItem(item)
                self._nav_items.append((target, self.navList.count() - 1))

        self.navList.currentRowChanged.connect(self._on_nav_row_changed)

        # Layout: fixed horizontal layout with nav list | content (no resize)
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(2)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.navList)
        main_layout.addWidget(self.configContent, 1)

    def on_load_model_changed(self):
        pcfg.module.load_model_on_demand = self.load_model_checker.isChecked()

    def on_runcache_changed(self):
        pcfg.module.empty_runcache = self.empty_runcache_checker.isChecked()

    def on_keepline_clicked(self):
        pcfg.module.keep_exist_textlines = self.detect_config_panel.keep_existing_checker.isChecked()

    def addConfigBlock(self, header: str) -> ConfigBlock:
        cb = ConfigBlock(header, parent=self)
        self.configContent.addConfigBlock(cb)
        cb.setIndex(len(self.configContent.config_block_list) - 1)
        return cb

    def on_open_onstartup_changed(self):
        pcfg.open_recent_on_startup = self.open_on_startup_checker.isChecked()

    def on_fntsize_flag_changed(self):
        pcfg.let_fntsize_flag = self.let_fntsize_combox.currentIndex()

    def on_fntstroke_flag_changed(self):
        pcfg.let_fntstroke_flag = self.let_fntstroke_combox.currentIndex()

    def on_autolayout_changed(self):
        pcfg.let_autolayout_flag = self.let_autolayout_checker.isChecked()

    def on_uppercase_changed(self):
        pcfg.let_uppercase_flag = self.let_uppercase_checker.isChecked()

    def on_textstyle_indep_changed(self):
        pcfg.let_textstyle_indep_flag = self.let_textstyle_indep_checker.isChecked()
        self.reload_textstyle.emit(pcfg.let_textstyle_indep_flag)

    def on_exclude_fonts_clicked(self):
        dialog = FontExcludeDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            excluded = dialog.get_excluded_fonts()
            pcfg.excluded_fonts = excluded
            self.font_exclusion_changed.emit()
            from utils.config import save_config
            save_config()

    def on_rst_imgformat_changed(self):
        pcfg.imgsave_ext = '.' + self.rst_imgformat_combobox.currentText().lower()

    def on_autoformat_changed(self):
        pcfg.imgsave_auto_format = self.rst_autoformat_checker.isChecked()
        self.rst_imgformat_combobox.setEnabled(not pcfg.imgsave_auto_format)

    def on_max_font_size_changed(self, value: int):
        pcfg.max_font_size = value

    def on_intermediate_imgformat_changed(self):
        pcfg.intermediate_imgsave_ext = '.' + self.intermediate_imgformat_combobox.currentText().lower()

    def on_edit_quality_changed(self, value: str):
        pcfg.imgsave_quality = int(value)

    def on_fontcolor_flag_changed(self):
        pcfg.let_fntcolor_flag = self.let_fntcolor_combox.currentIndex()

    def on_font_scolor_flag_changed(self):
        pcfg.let_fnt_scolor_flag = self.let_fnt_scolor_combox.currentIndex()

    def on_alignment_flag_changed(self):
        pcfg.let_alignment_flag = self.let_alignment_combox.currentIndex()

    def on_writing_mode_flag_changed(self):
        pcfg.let_writing_mode_flag = self.let_writing_mode_combox.currentIndex()

    def on_family_flag_changed(self):
        pcfg.let_family_flag = self.let_family_combox.currentIndex()

    def on_effect_flag_changed(self):
        pcfg.let_fnteffect_flag = self.let_effect_combox.currentIndex()

    def _on_nav_row_changed(self, row: int):
        """Scroll config content to the section widget for the selected nav row."""
        if row < 0 or row >= len(self._nav_items):
            return
        target, _ = self._nav_items[row]
        if target is not None:
            self.configContent.scrollToWidget(target)

    def _nav_select(self, section_widget) -> int:
        """Select the nav-list row whose target matches *section_widget*.
        Returns the row index, or -1 if not found.
        """
        for idx, (target, _) in enumerate(self._nav_items):
            if target is section_widget:
                self.navList.setCurrentRow(idx)
                return idx
        return -1

    def focusOnTranslator(self):
        target = self.trans_sub_block.section_widget
        self._nav_select(target)
        self.configContent.scrollToWidget(target)

    def focusOnInpaint(self):
        target = self.inpaint_sub_block.section_widget
        self._nav_select(target)
        self.configContent.scrollToWidget(target)

    def focusOnDetect(self):
        target = self.detect_sub_block.section_widget
        self._nav_select(target)
        self.configContent.scrollToWidget(target)

    def focusOnOCR(self):
        target = self.ocr_sub_block.section_widget
        self._nav_select(target)
        self.configContent.scrollToWidget(target)

    def _open_profile_manager(self):
        from utils.profile_manager import (
            ProfileManagerDialog,
            load_profiles,
            save_all_profiles,
        )
        profiles = load_profiles()
        dialog = ProfileManagerDialog(self, profiles, on_changed=lambda: None)
        dialog.exec()
        save_all_profiles(profiles)
        self.profiles_changed.emit()

    def _open_shortcut_dialog(self):
        dialog = ShortcutDialog(self)
        dialog.exec()

    # ── Theme management ──

    def _refresh_theme_combo(self):
        """Rebuild the theme combo box with all available themes."""
        from .misc import load_all_themes, load_custom_themes
        all_themes = load_all_themes()
        custom_themes = load_custom_themes()
        current = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme

        self.theme_combo.clear()
        for name in all_themes:
            is_custom = name in custom_themes
            label = name if is_custom else f'{name} ({self.tr("built-in")})'
            self.theme_combo.addItem(label, name)

        idx = self.theme_combo.findData(current)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)

        self._update_theme_buttons()

    def _update_theme_buttons(self):
        """Enable/disable Edit/Delete based on whether the selected theme is custom."""
        from .misc import load_custom_themes
        theme_name = self.theme_combo.currentData()
        if not theme_name:
            return
        is_custom = theme_name in load_custom_themes()
        self.edit_theme_btn.setEnabled(True)

    def _on_theme_selected(self):
        """Apply the selected theme for the current mode."""
        from .misc import load_all_themes
        theme_name = self.theme_combo.currentData()
        if not theme_name:
            return
        all_themes = load_all_themes()
        if theme_name not in all_themes:
            return

        base = all_themes[theme_name].get('_base', theme_name)
        is_dark = 'dark' in base.lower()
        if is_dark:
            pcfg.dark_theme = theme_name
        else:
            pcfg.light_theme = theme_name
        self.save_config.emit()
        self._update_theme_buttons()
        self.theme_changed.emit()

    def _on_new_theme(self):
        """Clone the currently selected theme and open the editor."""
        from .misc import load_all_themes
        from .theme_editor import ThemeEditorDialog
        theme_name = self.theme_combo.currentData()
        if not theme_name:
            return

        all_themes = load_all_themes()
        source = all_themes.get(theme_name)
        if not source:
            return

        base = source.get('_base', theme_name)
        import random
        new_name = f'{base}-custom-{random.randint(100, 999)}'

        from .misc import load_custom_themes
        custom = load_custom_themes()
        clone = dict(source)
        clone['_base'] = base
        custom[new_name] = clone
        try:
            import json
            import os
            os.makedirs(os.path.dirname(C.CUSTOM_THEME_PATH), exist_ok=True)
            with open(C.CUSTOM_THEME_PATH, 'w', encoding='utf-8') as f:
                json.dump(custom, f, indent=4, ensure_ascii=False)
        except Exception:
            return

        dlg = ThemeEditorDialog(new_name, self.window(), parent=self)
        dlg.themeSaved.connect(self._refresh_theme_combo)
        dlg.themeSaved.connect(self.theme_changed.emit)
        dlg.exec()

    def _on_edit_theme(self):
        """Open the theme editor for the selected theme."""
        from .theme_editor import ThemeEditorDialog

        theme_name = self.theme_combo.currentData()
        if not theme_name:
            return

        dlg = ThemeEditorDialog(theme_name, self.window(), parent=self)
        dlg.themeSaved.connect(self._refresh_theme_combo)
        dlg.themeSaved.connect(self.theme_changed.emit)
        dlg.exec()

    def _on_delete_theme(self):
        """Delete the selected custom theme."""
        from qtpy.QtWidgets import QMessageBox

        from .misc import load_custom_themes

        theme_name = self.theme_combo.currentData()
        if not theme_name:
            return

        custom = load_custom_themes()
        if theme_name not in custom:
            return

        reply = QMessageBox.question(
            self, self.tr('Delete Theme'),
            self.tr('Delete theme "%s"? This cannot be undone.') % theme_name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        del custom[theme_name]
        try:
            import json
            with open(C.CUSTOM_THEME_PATH, 'w', encoding='utf-8') as f:
                json.dump(custom, f, indent=4, ensure_ascii=False)
        except Exception:
            return

        # Fall back to built-in if the deleted theme was active
        if pcfg.dark_theme == theme_name:
            pcfg.dark_theme = 'eva-dark'
        if pcfg.light_theme == theme_name:
            pcfg.light_theme = 'eva-light'
        self.save_config.emit()
        self._refresh_theme_combo()
        self.theme_changed.emit()

    def refresh_theme_ui(self):
        """Called from outside (e.g. darkmode toggle) to refresh theme combo."""
        self._refresh_theme_combo()

    def hideEvent(self, e) -> None:
        self.save_config.emit()
        return super().hideEvent(e)

    def setupConfig(self):
        self.blockSignals(True)

        if pcfg.open_recent_on_startup:
            self.open_on_startup_checker.setChecked(True)

        self.detect_config_panel.keep_existing_checker.setChecked(pcfg.module.keep_exist_textlines)
        self.let_effect_combox.setCurrentIndex(pcfg.let_fnteffect_flag)
        self.let_fntsize_combox.setCurrentIndex(pcfg.let_fntsize_flag)
        self.let_fntstroke_combox.setCurrentIndex(pcfg.let_fntstroke_flag)
        self.let_fntcolor_combox.setCurrentIndex(pcfg.let_fntcolor_flag)
        self.let_fnt_scolor_combox.setCurrentIndex(pcfg.let_fnt_scolor_flag)
        self.let_alignment_combox.setCurrentIndex(pcfg.let_alignment_flag)
        self.let_family_combox.setCurrentIndex(pcfg.let_family_flag)
        self.let_writing_mode_combox.setCurrentIndex(pcfg.let_writing_mode_flag)
        self.let_autolayout_checker.setChecked(pcfg.let_autolayout_flag)
        self.let_uppercase_checker.setChecked(pcfg.let_uppercase_flag)
        self.let_textstyle_indep_checker.setChecked(pcfg.let_textstyle_indep_flag)
        self.rst_imgformat_combobox.setCurrentText(pcfg.imgsave_ext.replace('.', '').upper())
        self.rst_autoformat_checker.setChecked(pcfg.imgsave_auto_format)
        self.rst_imgformat_combobox.setEnabled(not pcfg.imgsave_auto_format)
        self.intermediate_imgformat_combobox.setCurrentText(pcfg.intermediate_imgsave_ext.replace('.', '').upper())
        self.rst_imgquality_edit.setText(str(pcfg.imgsave_quality))
        self.load_model_checker.setChecked(pcfg.module.load_model_on_demand)
        self.empty_runcache_checker.setChecked(pcfg.module.empty_runcache)
        self.max_font_size_edit.setValue(pcfg.max_font_size)

        self.blockSignals(False)
