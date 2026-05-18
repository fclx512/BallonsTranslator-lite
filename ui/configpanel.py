from typing import List, Union, Tuple

from qtpy.QtWidgets import QPushButton, QKeySequenceEdit, QLayout, QGridLayout, QHBoxLayout, QVBoxLayout, QTreeView, QWidget, QLabel, QSpinBox, QSizePolicy, QSpacerItem, QCheckBox, QSplitter, QScrollArea, QLineEdit, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QAbstractItemView
from qtpy.QtCore import Qt, Signal, QSize, QEvent, QItemSelection
from qtpy.QtGui import QStandardItem, QStandardItemModel, QMouseEvent, QFont, QIntValidator, QValidator, QFocusEvent

from .custom_widget import ConfigComboBox, Widget
from utils.config import pcfg
from utils import shared as C
from utils.shared import CONFIG_FONTSIZE_CONTENT, CONFIG_FONTSIZE_HEADER, CONFIG_FONTSIZE_TABLE, CONFIG_COMBOBOX_SHORT, CONFIG_COMBOBOX_LONG, CONFIG_COMBOBOX_MIDEAN
from .module_parse_widgets import InpaintConfigPanel, TextDetectConfigPanel, TranslatorConfigPanel, OCRConfigPanel

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
        validator = CustomIntValidator(0, 101, 3)
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
        self.setStyleSheet("background-color:rgba(30, 147, 229, 51);")


class ConfigSubBlock(Widget):
    pressed = Signal(int, int)
    def __init__(self, widget: Union[QWidget, QLayout], name: str = None, discription: str = None, vertical_layout=True, insert_stretch: bool = False, content_margins = (24, 6, 24, 6)) -> None:
        super().__init__()
        self.idx0: int = None
        self.idx1: int = None
        if vertical_layout:
            layout = QVBoxLayout(self)
        else:
            layout = QHBoxLayout(self)
        self.name = name
        if name is not None:
            textlabel = ConfigTextLabel(name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal)
            self.name_label = textlabel
            layout.addWidget(textlabel)
        if discription is not None:
            layout.addWidget(ConfigTextLabel(discription, CONFIG_FONTSIZE_CONTENT-2))
        if insert_stretch:
            layout.insertStretch(-1)
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
        self.widget = widget
        self.setContentsMargins(*content_margins)

    def setIdx(self, idx0: int, idx1: int) -> None:
        self.idx0 = idx0
        self.idx1 = idx1

    def enterEvent(self, e: QEvent) -> None:
        self.pressed.emit(self.idx0, self.idx1)
        return super().enterEvent(e)
    

def combobox_with_label(sel: List[str], name: str, discription: str = None, vertical_layout: bool = False, target_block: QWidget = None, fix_size: bool = True, parent: QWidget = None, insert_stretch: bool = False) -> Tuple[ConfigComboBox, QWidget]:
    combox = ConfigComboBox(fix_size=fix_size, scrollWidget=parent)
    combox.addItems(sel)
    if target_block is None:
        sublock = ConfigSubBlock(combox, name, discription, vertical_layout=vertical_layout, insert_stretch=insert_stretch)
        sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        sublock.layout().setSpacing(20)
        return combox, sublock
    else:
        layout = target_block.layout()
        layout.addSpacing(20)
        layout.addWidget(ConfigTextLabel(name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal))
        layout.addWidget(combox)
        return combox, target_block
    
def checkbox_with_label(name: str, discription: str = None, target_block: QWidget = None):
    checkbox = QCheckBox()
    if discription is not None:
        font = checkbox.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        checkbox.setFont(font)
        checkbox.setText(discription)
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
    sublock_pressed = Signal(int, int)

    def __init__(self, header: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.header = ConfigTextLabel(header, CONFIG_FONTSIZE_HEADER)
        self.vlayout = QVBoxLayout(self)
        self.vlayout.addWidget(self.header)
        self.setContentsMargins(24, 24, 24, 24)
        self.label_list = []
        self.subblock_list = []
        self.index: int = 0

    def setIndex(self, index: int):
        self.index = index

    def addLineEdit(self, name: str = None, discription: str = None, vertical_layout: bool = False):
        le = QLineEdit()
        le.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        le.setFixedHeight(45)
        sublock = ConfigSubBlock(le, name, discription, vertical_layout)
        if vertical_layout is False:
            sublock.layout().addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))
        self.addSublock(sublock)
        sublock.layout().setSpacing(20)
        return le, sublock

    def addTextLabel(self, text: str = None):
        label = ConfigTextLabel(text, CONFIG_FONTSIZE_HEADER)
        self.vlayout.addWidget(label)
        self.label_list.append(label)

    def addSublock(self, sublock: ConfigSubBlock):
        self.vlayout.addWidget(sublock)
        sublock.setIdx(self.index, len(self.label_list)-1)
        sublock.pressed.connect(lambda idx0, idx1: self.sublock_pressed.emit(idx0, idx1))
        self.subblock_list.append(sublock)

    def addCombobox(self, sel: List[str], name: str, discription: str = None, vertical_layout: bool = False, target_block: QWidget = None, fix_size: bool = True) -> Tuple[ConfigComboBox, QWidget]:
        combox, sublock = combobox_with_label(sel, name, discription, vertical_layout, target_block, fix_size, parent=self)
        if target_block is None:
            self.addSublock(sublock)
        return combox, sublock

    def addBlockWidget(self, widget: Union[QWidget, QLayout], name: str = None, discription: str = None, vertical_layout: bool = False) -> ConfigSubBlock:
        sublock = ConfigSubBlock(widget, name, discription, vertical_layout)
        self.addSublock(sublock)
        return sublock

    def addCheckBox(self, name: str, discription: str = None, target_block: ConfigSubBlock = None) -> QCheckBox:
        checkbox, sublock = checkbox_with_label(name, discription, target_block)
        if target_block is None:
            self.addSublock(sublock)
        return checkbox, sublock

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
        self.active_label: ConfigTextLabel = None

    def addConfigBlock(self, block: ConfigBlock):
        self.vlayout.addWidget(block)
        self.config_block_list.append(block)

    def setActiveLabel(self, idx0: int, idx1: int):
        if self.active_label is not None:
            self.deactiveLabel()
        block = self.config_block_list[idx0]
        if idx1 >= 0:
            self.active_label = block.label_list[idx1]
        else:
            self.active_label = block.header
        self.active_label.setActiveBackground()
        if C.USE_PYSIDE6:
            self.ensureWidgetVisible(self.active_label, ymargin=self.active_label.height() * 7)
        else:
            self.ensureWidgetVisible(self.active_label, yMargin=self.active_label.height() * 7)

    def deactiveLabel(self):
        if self.active_label is not None:
            self.active_label.setStyleSheet("")
            self.active_label = None


class TableItem(QStandardItem):
    def __init__(self, text, fontsize):
        super().__init__()
        font = self.font()
        font.setPointSizeF(fontsize)
        self.setFont(font)
        self.setText(text)
        self.setEditable(False)

    def setBold(self, bold: bool):
        font = self.font()
        font.setBold(bold)
        self.setFont(font)


class TreeModel(QStandardItemModel):
    # https://stackoverflow.com/questions/32229314/pyqt-how-can-i-set-row-heights-of-qtreeview
    def data(self, index, role):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.SizeHintRole:
            size = QSize()
            item = self.itemFromIndex(index)
            size.setHeight(item.font().pointSize()+20)
            return size
        else:
            return super().data(index, role)


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


class _ShortcutPill(QWidget):
    removed = Signal(object)

    def __init__(self, key_seq: str, parent=None):
        super().__init__(parent)
        self.key_seq = key_seq
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 1, 1, 1)
        h.setSpacing(2)
        lbl = QLabel(key_seq)
        lbl.setStyleSheet("color: #d4d4d8; font-size: 11px;")
        h.addWidget(lbl)
        btn = QPushButton('×')
        btn.setFixedSize(16, 16)
        btn.setStyleSheet("QPushButton { border: none; color: #888; font-size: 12px; } QPushButton:hover { color: #f88; }")
        btn.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(btn)
        self.setStyleSheet("_ShortcutPill { background: #3a3a42; border-radius: 3px; }")


class ShortcutEditor(QWidget):
    shortcut_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(350)
        self._pills_info = {}
        self._current_action_id = None
        self._current_card = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: action list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        self.list_widget = QListWidget()
        self.list_widget.setFixedWidth(190)
        for aid in DEFAULT_SHORTCUTS.keys():
            display_name = self.tr(_ACTION_NAMES.get(aid, aid))
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, aid)
            self.list_widget.addItem(item)
        self.list_widget.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.list_widget)

        # Right: editor pane
        right = QWidget()
        self.right_layout = QVBoxLayout(right)
        self.right_layout.setContentsMargins(8, 4, 8, 4)
        self._placeholder = QLabel(self.tr('Select an action to edit shortcuts'))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #888;")
        self.right_layout.addWidget(self._placeholder)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([190, 210])
        layout.addWidget(splitter)

    def _on_select(self, row: int):
        if row < 0:
            return
        item = self.list_widget.item(row)
        action_id = item.data(Qt.ItemDataRole.UserRole)
        self._show_action_editor(action_id)

    def _show_action_editor(self, action_id: str):
        # Remove old card
        if self._current_card is not None:
            self.right_layout.removeWidget(self._current_card)
            self._current_card.deleteLater()
            self._current_card = None
        self._placeholder.setVisible(False)
        self._current_action_id = action_id
        card = self._make_card(action_id)
        self._current_card = card
        self.right_layout.addWidget(card)

    def _make_card(self, action_id: str) -> QWidget:
        card = QWidget()
        card.setStyleSheet("QWidget { background: #2a2a32; border-radius: 4px; padding: 4px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(6, 3, 4, 3)
        v.setSpacing(3)

        # Header row: name + reset
        header = QHBoxLayout()
        name = QLabel(self.tr(_ACTION_NAMES.get(action_id, action_id)))
        name.setStyleSheet("font-weight: bold; color: #ccc; border: none; background: transparent;")
        header.addWidget(name)
        header.addStretch()
        reset_btn = QPushButton('↺')
        reset_btn.setFixedSize(20, 20)
        reset_btn.setToolTip(self.tr('Reset'))
        reset_btn.setStyleSheet("QPushButton { border: none; color: #888; background: transparent; } QPushButton:hover { color: #fff; }")
        reset_btn.clicked.connect(lambda checked=False, aid=action_id: self._reset_card(aid))
        header.addWidget(reset_btn)
        v.addLayout(header)

        # Pills row
        pills_row = QHBoxLayout()
        pills_row.setSpacing(3)
        pills_widget = QWidget()
        pills_widget.setLayout(pills_row)
        pills_widget.setStyleSheet("background: transparent; border: none;")
        v.addWidget(pills_widget)

        # "+" add button
        add_btn = QPushButton('+')
        add_btn.setFixedSize(24, 20)
        add_btn.setToolTip(self.tr('Add shortcut'))
        add_btn.setStyleSheet("QPushButton { border: 1px solid #555; border-radius: 3px; color: #aaa; background: transparent; } QPushButton:hover { border-color: #88f; color: #fff; }")
        add_btn.clicked.connect(lambda checked=False, aid=action_id, pw=pills_widget: self._add_shortcut(aid, pw))
        pills_row.addWidget(add_btn)
        pills_row.addStretch()

        self._pills_info[action_id] = dict(pills_widget=pills_widget, add_btn=add_btn)
        self._rebuild_pills(action_id)
        return card

    def _rebuild_pills(self, action_id: str):
        info = self._pills_info[action_id]
        pw = info['pills_widget']
        layout = pw.layout()
        # Remove all pill widgets (keep add_btn and stretch)
        while layout.count() > 2:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        keys = pcfg.shortcuts.get(action_id, DEFAULT_SHORTCUTS.get(action_id, []))
        if not isinstance(keys, list):
            keys = [keys]
        for k in reversed(keys):
            pill = _ShortcutPill(k)
            pill.removed.connect(lambda p, aid=action_id: self._remove_shortcut(aid, p.key_seq))
            layout.insertWidget(0, pill)

    def _add_shortcut(self, action_id: str, pills_widget: QWidget):
        edit = QKeySequenceEdit()
        edit.setFixedWidth(100)
        layout = pills_widget.layout()
        layout.insertWidget(layout.count() - 2, edit)
        edit.setFocus()

        def on_finished():
            seq = edit.keySequence().toString()
            edit.deleteLater()
            if seq:
                keys = pcfg.shortcuts.get(action_id, DEFAULT_SHORTCUTS.get(action_id, []))
                if not isinstance(keys, list):
                    keys = [keys] if keys else []
                if seq not in keys:
                    keys.append(seq)
                    pcfg.shortcuts[action_id] = keys
                self._rebuild_pills(action_id)
                self.shortcut_changed.emit()
            else:
                self._rebuild_pills(action_id)

        # Use editingFinished signal
        edit.editingFinished.connect(on_finished)

    def _remove_shortcut(self, action_id: str, key_seq: str):
        keys = pcfg.shortcuts.get(action_id, [])
        if not isinstance(keys, list):
            keys = [keys] if keys else []
        if key_seq in keys:
            keys.remove(key_seq)
            if keys:
                pcfg.shortcuts[action_id] = keys
            elif action_id in pcfg.shortcuts:
                del pcfg.shortcuts[action_id]
        self._rebuild_pills(action_id)
        self.shortcut_changed.emit()

    def _reset_card(self, action_id: str):
        defaults = DEFAULT_SHORTCUTS.get(action_id, [])
        pcfg.shortcuts[action_id] = list(defaults)
        self._rebuild_pills(action_id)
        self.shortcut_changed.emit()

    def refresh(self):
        if self._current_action_id is not None:
            self._rebuild_pills(self._current_action_id)


class ConfigTable(QTreeView):
    tableitem_pressed = Signal(int, int)
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        treeModel = TreeModel()
        self.tm = treeModel
        self.setModel(treeModel)
        self.selected: TableItem = None
        self.last_selected: TableItem = None
        self.setHeaderHidden(True)
        self.setMinimumWidth(260)

    def addHeader(self, header: str) -> TableItem:
        rootNode = self.model().invisibleRootItem()
        ti = TableItem(header, CONFIG_FONTSIZE_TABLE)
        rootNode.appendRow(ti)
        return ti

    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        dis = deselected.indexes()
        sel = selected.indexes()
        model = self.model()
        self.last_selected = model.itemFromIndex(dis[0]) \
            if len(dis) > 0 else None
        
        self.selected = model.itemFromIndex(sel[0]) \
            if len(sel) > 0 else None
        for i in deselected.indexes():
            self.model().itemFromIndex(i).setBold(False)
        
        index = self.currentIndex()
        if index.isValid():
            self.model().itemFromIndex(index).setBold(True)
        super().selectionChanged(selected, deselected)

    def setCurrentItem(self, idx0, idx1):
        index = self.tm.item(idx0, 0).child(idx1).index()
        self.setCurrentIndex(index)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        if self.selected is not None:
            parent = self.selected.parent()
            if parent is None:
                idx1 = -1
                idx0 = self.selected.row()
            else:
                idx1 = self.selected.row()
                idx0 = parent.row()
            self.tableitem_pressed.emit(idx0, idx1)


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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("ConfigPanel")
        self.configTable = ConfigTable()
        self.configTable.tableitem_pressed.connect(self.onTableItemPressed)
        self.configContent = ConfigContent()
        dlConfigPanel, dltableitem = self.addConfigBlock(self.tr('DL Module'))
        generalConfigPanel, generalTableItem = self.addConfigBlock(self.tr('General'))
        
        label_text_det = self.tr('Text Detection')
        label_text_ocr = self.tr('OCR')
        label_inpaint = self.tr('Inpaint')
        label_translator = self.tr('Translator')
        label_startup = self.tr('Startup')
        label_typesetting = self.tr('Typesetting')
        label_save = self.tr('Save')

        dltableitem.appendRows([
            TableItem(label_text_det, CONFIG_FONTSIZE_TABLE),
            TableItem(label_text_ocr, CONFIG_FONTSIZE_TABLE),
            TableItem(label_inpaint, CONFIG_FONTSIZE_TABLE),
            TableItem(label_translator, CONFIG_FONTSIZE_TABLE),
        ])
        label_shortcuts = self.tr('Keyboard Shortcuts')
        generalTableItem.appendRows([
            TableItem(label_startup, CONFIG_FONTSIZE_TABLE),
            TableItem(label_typesetting, CONFIG_FONTSIZE_TABLE),
            TableItem(label_save, CONFIG_FONTSIZE_TABLE),
            TableItem(label_shortcuts, CONFIG_FONTSIZE_TABLE),
        ])
        
        self.load_model_checker, msublock = checkbox_with_label(self.tr('Load models on demand'), discription=self.tr('Load models on demand to save memory.'))
        self.load_model_checker.stateChanged.connect(self.on_load_model_changed)
        dlConfigPanel.vlayout.addWidget(msublock)
        self.empty_runcache_checker, msublock = checkbox_with_label(self.tr('Empty cache after RUN'), discription=self.tr('Empty cache after RUN to save memory.'))
        dlConfigPanel.vlayout.addWidget(msublock)
        self.empty_runcache_checker.stateChanged.connect(self.on_runcache_changed)
        self.unload_model_btn = QPushButton(parent=self)
        self.unload_model_btn.setFixedWidth(500)
        self.unload_model_btn.setText(self.tr('Unload All Models'))
        self.unload_model_btn.clicked.connect(self.unload_models)
        msublock.layout().addWidget(self.unload_model_btn)

        dlConfigPanel.addTextLabel(label_text_det)
        self.detect_config_panel = TextDetectConfigPanel(self.tr('Detector'), scrollWidget=self)
        self.detect_sub_block = dlConfigPanel.addBlockWidget(self.detect_config_panel)
        self.detect_config_panel.keep_existing_checker.clicked.connect(self.on_keepline_clicked)

        dlConfigPanel.addTextLabel(label_text_ocr)
        self.ocr_config_panel = OCRConfigPanel(self.tr('OCR'), scrollWidget=self)
        self.ocr_sub_block = dlConfigPanel.addBlockWidget(self.ocr_config_panel)

        dlConfigPanel.addTextLabel(label_inpaint)
        self.inpaint_config_panel = InpaintConfigPanel(self.tr('Inpainter'), scrollWidget=self)
        self.inpaint_sub_block = dlConfigPanel.addBlockWidget(self.inpaint_config_panel)

        dlConfigPanel.addTextLabel(label_translator)
        self.trans_config_panel = TranslatorConfigPanel(label_translator, scrollWidget=self)
        self.trans_sub_block = dlConfigPanel.addBlockWidget(self.trans_config_panel)

        generalConfigPanel.addTextLabel(label_startup)
        self.open_on_startup_checker, _ = generalConfigPanel.addCheckBox(self.tr('Reopen last project on startup'))
        self.open_on_startup_checker.stateChanged.connect(self.on_open_onstartup_changed)

        generalConfigPanel.addTextLabel(label_typesetting)
        dec_program_str = self.tr('decide by program')
        use_global_str = self.tr('use global setting')

        global_fntfmt_widget = QWidget()
        global_fntfmt_layout = QGridLayout(global_fntfmt_widget)
        global_fntfmt_layout.setSpacing(0)
        global_fntfmt_widget.setContentsMargins(0, 0, 0, 0)

        b = generalConfigPanel.addBlockWidget(global_fntfmt_widget)
        b.layout().setContentsMargins(0, 0, 0, 0)
        b.setContentsMargins(0, 0, 0, 0)
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

        self.let_autolayout_checker, sublock = generalConfigPanel.addCheckBox(self.tr('Auto layout'), 
                discription=self.tr('Split translation into multi-lines according to the extracted balloon region.'))

        self.let_autolayout_checker.stateChanged.connect(self.on_autolayout_changed)
        self.let_uppercase_checker, _ = generalConfigPanel.addCheckBox(self.tr('To uppercase'))
        self.let_uppercase_checker.stateChanged.connect(self.on_uppercase_changed)

        self.let_textstyle_indep_checker, _ = generalConfigPanel.addCheckBox(self.tr('Independent text styles for each projects'))
        self.let_textstyle_indep_checker.stateChanged.connect(self.on_textstyle_indep_changed)

        self.exclude_fonts_btn = QPushButton(self.tr('Exclude Fonts...'), parent=self)
        self.exclude_fonts_btn.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.exclude_fonts_btn.clicked.connect(self.on_exclude_fonts_clicked)
        btn_sublock = ConfigSubBlock(self.exclude_fonts_btn)
        generalConfigPanel.addSublock(btn_sublock)

        self.max_font_size_edit = QSpinBox()
        self.max_font_size_edit.setRange(10, 1000)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.max_font_size_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.max_font_size_edit.valueChanged.connect(self.on_max_font_size_changed)
        max_font_sublock = ConfigSubBlock(self.max_font_size_edit, self.tr('Max Font Size (px)'))
        generalConfigPanel.addSublock(max_font_sublock)

        generalConfigPanel.addTextLabel(label_save)
        self.rst_imgformat_combobox, imsave_sublock = generalConfigPanel.addCombobox(['PNG', 'JPG', 'WEBP', 'JXL'], self.tr('Result image format'))
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        self.rst_imgquality_edit = PercentageLineEdit('100')
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)

        sublock = ConfigSubBlock(self.rst_imgquality_edit, self.tr('Quality'), vertical_layout=False)
        sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        sublock.layout().insertStretch(-1)
        imsave_sublock.layout().addWidget(sublock)

        self.intermediate_imgformat_combobox, intermediate_imsave_sublock = generalConfigPanel.addCombobox(['PNG', 'JXL'], self.tr('Intermediate image format'))
        self.intermediate_imgformat_combobox.activated.connect(self.on_intermediate_imgformat_changed)

        generalConfigPanel.addTextLabel(label_shortcuts)
        self.shortcut_editor = ShortcutEditor(parent=self)
        shortcut_sublock = generalConfigPanel.addBlockWidget(self.shortcut_editor)
        shortcut_sublock.layout().addStretch()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.configTable)
        splitter.addWidget(self.configContent)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        hlayout = QHBoxLayout(self)

        hlayout.addWidget(splitter)
        hlayout.setSpacing(0)
        hlayout.setContentsMargins(0, 0, 0, 0)

        self.configTable.expandAll()

    def on_load_model_changed(self):
        pcfg.module.load_model_on_demand = self.load_model_checker.isChecked()

    def on_runcache_changed(self):
        pcfg.module.empty_runcache = self.empty_runcache_checker.isChecked()

    def on_keepline_clicked(self):
        pcfg.module.keep_exist_textlines = self.detect_config_panel.keep_existing_checker.isChecked()

    def addConfigBlock(self, header: str) -> Tuple[ConfigBlock, TableItem]:
        cb = ConfigBlock(header, parent=self)
        cb.sublock_pressed.connect(self.onSublockPressed)
        self.configContent.addConfigBlock(cb)
        cb.setIndex(len(self.configContent.config_block_list)-1)
        ti = self.configTable.addHeader(header)
        return cb, ti

    def onSublockPressed(self, idx0, idx1):
        self.configTable.setCurrentItem(idx0, idx1)
        self.configContent.deactiveLabel()

    def onTableItemPressed(self, idx0, idx1):
        self.configContent.setActiveLabel(idx0, idx1)

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

    def focusOnTranslator(self):
        idx0, idx1 = self.trans_sub_block.idx0, self.trans_sub_block.idx1
        self.configTable.setCurrentItem(idx0, idx1)
        self.configTable.tableitem_pressed.emit(idx0, idx1)

    def focusOnInpaint(self):
        idx0, idx1 = self.inpaint_sub_block.idx0, self.inpaint_sub_block.idx1
        self.configTable.setCurrentItem(idx0, idx1)
        self.configTable.tableitem_pressed.emit(idx0, idx1)

    def focusOnDetect(self):
        idx0, idx1 = self.detect_sub_block.idx0, self.detect_sub_block.idx1
        self.configTable.setCurrentItem(idx0, idx1)
        self.configTable.tableitem_pressed.emit(idx0, idx1)

    def focusOnOCR(self):
        idx0, idx1 = self.ocr_sub_block.idx0, self.ocr_sub_block.idx1
        self.configTable.setCurrentItem(idx0, idx1)
        self.configTable.tableitem_pressed.emit(idx0, idx1)

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
        self.intermediate_imgformat_combobox.setCurrentText(pcfg.intermediate_imgsave_ext.replace('.', '').upper())
        self.rst_imgquality_edit.setText(str(pcfg.imgsave_quality))
        self.load_model_checker.setChecked(pcfg.module.load_model_on_demand)
        self.empty_runcache_checker.setChecked(pcfg.module.empty_runcache)
        self.max_font_size_edit.setValue(pcfg.max_font_size)

        self.blockSignals(False)