from typing import List, Tuple, Union

from qtpy.QtCore import QEasingCurve, QElapsedTimer, QEvent, QItemSelection, QPoint, QSize, Qt, QTimer, Signal
from qtpy.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QGuiApplication,
    QIntValidator,
    QPainter,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
    QValidator,
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from utils.config import export_config, import_config, pcfg
from utils.message import create_error_dialog, create_info_dialog
from utils.shared import (
    CONFIG_COMBOBOX_LONG,
    CONFIG_COMBOBOX_MIDEAN,
    CONFIG_COMBOBOX_SHORT,
    CONFIG_FONTSIZE_CONTENT,
    CONFIG_FONTSIZE_HEADER,
    CONFIG_FONTSIZE_TABLE,
    CONFIG_SUBBLOCK_SPACING,
    CONFIGBLOCK_CONTENT_MARGINS,
    GROUPBOX_CONTENT_MARGINS,
    LINEEDIT_FIXHEIGHT,
    NAVLIST_WIDTH,
)

import os.path as osp

from .custom_widget import (
    ConfigCheckBox,
    ConfigComboBox,
    ConfigLineEdit,
    ConfigScrollBar,
    ConfigSectionHeader,
    ConfigTextEdit,
    NoArrowsSpinBox,
    PanelGroupBox,
    PaintQSlider,
    Widget,
)
from .module_parse_widgets import (
    InpaintConfigPanel,
    OCRConfigPanel,
    TextDetectConfigPanel,
    TranslatorConfigPanel,
)


class CustomIntValidator(QIntValidator):
    def __init__(self, bottom: int, top: int, ndigits: int = None, parent=None):
        super().__init__(bottom=bottom, top=top, parent=parent)
        self.ndigits = ndigits

    def validate(self, s: str, pos: int) -> object:
        if not s.isnumeric():
            if s != "":
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
                if s[-1] == "0":
                    d = self.top()
                else:
                    d = d % self.top()
            d = max(d, self.bottom())
            s = str(d)
        return (QValidator.State.Acceptable, s, pos)


class PercentageLineEdit(ConfigLineEdit):
    finish_edited = Signal(str)

    def __init__(self, default_value: str = "100", parent=None) -> None:
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
                text = "100"
                self.setText(text)
            self.finish_edited.emit(text)

        return super().focusOutEvent(e)


class ConfigTextLabel(QLabel):
    def __init__(
        self, text: str, fontsize: int, font_weight: int = None, *args, **kwargs
    ) -> None:
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
        self.setStyleSheet(
            f"background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 51);"
        )


class ConfigSubBlock(Widget):
    def __init__(
        self,
        widget: Union[QWidget, QLayout] = None,
        name: str = None,
        description: str = None,
        note: str = None,
        vertical_layout=True,
        insert_stretch: bool = False,
        content_margins=(24, 6, 24, 6),
    ) -> None:
        super().__init__()
        if vertical_layout:
            layout = QVBoxLayout(self)
        else:
            layout = QHBoxLayout(self)
        self.name = name
        self._note_text = note
        if name is not None and note is not None:
            # Name row: label + ? button
            name_row = QWidget()
            name_row_layout = QHBoxLayout(name_row)
            name_row_layout.setContentsMargins(0, 0, 0, 0)
            name_row_layout.setSpacing(4)
            textlabel = ConfigTextLabel(
                name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal
            )
            self.name_label = textlabel
            name_row_layout.addWidget(textlabel)
            self._note_btn = QPushButton("?")
            self._note_btn.setFixedSize(20, 20)
            self._note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._note_btn.clicked.connect(self._show_note_popup)
            self._style_note_btn()
            name_row_layout.addWidget(self._note_btn)
            name_row_layout.addStretch()
            layout.addWidget(name_row)
        elif name is not None:
            textlabel = ConfigTextLabel(
                name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal
            )
            self.name_label = textlabel
            layout.addWidget(textlabel)
        if description is not None:
            layout.addWidget(ConfigTextLabel(description, CONFIG_FONTSIZE_CONTENT - 2))
        if insert_stretch:
            layout.insertStretch(-1)
        if widget is not None:
            if isinstance(widget, QWidget):
                layout.addWidget(widget)
            else:
                layout.addLayout(widget)
        self.widget = widget
        self.setContentsMargins(*content_margins)

    def _style_note_btn(self):
        """Apply theme-accent styling to the ? note button."""
        from ui.misc import get_theme_color

        c = get_theme_color()
        r, g, b = c.red(), c.green(), c.blue()
        self._note_btn.setStyleSheet(
            f"QPushButton {{"
            f"  border: 1px solid rgba({r},{g},{b},128);"
            f"  border-radius: 10px;"
            f"  font-size: 12px; font-weight: bold; padding: 0px;"
            f"  color: rgb({r},{g},{b}); background: transparent;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background: rgba({r},{g},{b},40);"
            f"}}"
        )

    def _show_note_popup(self):
        self._note_popup = ConfigNotePopup(self._note_btn, self._note_text)
        self._note_popup.show()

    # ── Disabled-state auto-styling ────────────────────────────────
    _disabled_color = None  # class-level cache

    def changeEvent(self, e: QEvent):
        if e.type() == QEvent.Type.EnabledChange and self.name_label is not None:
            if self.isEnabled():
                self.name_label.setStyleSheet("")
            else:
                if type(self)._disabled_color is None:
                    from ui.misc import _resolve_theme
                    theme = _resolve_theme("")
                    type(self)._disabled_color = theme.get(
                        "@disabledForegroundColor", "#5d6170"
                    )
                self.name_label.setStyleSheet(
                    f"color: {type(self)._disabled_color};"
                )
        super().changeEvent(e)


def combobox_with_label(
    sel: List[str],
    name: str,
    description: str = None,
    note: str = None,
    vertical_layout: bool = False,
    target_block: QWidget = None,
    fix_size: bool = True,
    parent: QWidget = None,
    insert_stretch: bool = False,
) -> Tuple[ConfigComboBox, QWidget]:
    combox = ConfigComboBox(fix_size=fix_size, scrollWidget=parent)
    combox.addItems(sel)
    if target_block is None:
        sublock = ConfigSubBlock(
            combox,
            name,
            description,
            note=note,
            vertical_layout=vertical_layout,
            insert_stretch=insert_stretch,
        )
        sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        sublock.layout().setSpacing(CONFIG_SUBBLOCK_SPACING)
        return combox, sublock
    else:
        layout = target_block.layout()
        layout.addSpacing(CONFIG_SUBBLOCK_SPACING)
        layout.addWidget(
            ConfigTextLabel(name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal)
        )
        layout.addWidget(combox)
        return combox, target_block


def checkbox_with_label(
    name: str, description: str = None, note: str = None, target_block: QWidget = None
):
    checkbox = ConfigCheckBox()
    if description is not None:
        font = checkbox.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        checkbox.setFont(font)
        checkbox.setText(description)
        vertical_layout = True
    else:
        checkbox.setMinimumWidth(24)
        vertical_layout = False

    if target_block is None:
        sublock = ConfigSubBlock(checkbox, name, note=note, vertical_layout=vertical_layout)
        if vertical_layout is False:
            sublock.layout().addItem(
                QSpacerItem(
                    0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
                )
            )
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

    def addLineEdit(
        self, name: str = None, description: str = None, vertical_layout: bool = False
    ):
        le = ConfigLineEdit()
        le.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        le.setFixedHeight(LINEEDIT_FIXHEIGHT)
        sublock = ConfigSubBlock(le, name, description, vertical_layout)
        if vertical_layout is False:
            sublock.layout().addItem(
                QSpacerItem(
                    0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
                )
            )
        self.addSublock(sublock)
        sublock.layout().setSpacing(CONFIG_SUBBLOCK_SPACING)
        return le, sublock

    def addTextLabel(self, text: str = None):
        label = ConfigTextLabel(text, CONFIG_FONTSIZE_HEADER)
        self.vlayout.addWidget(label)

    def addSublock(self, sublock: ConfigSubBlock):
        self.vlayout.addWidget(sublock)
        self.subblock_list.append(sublock)

    def addCombobox(
        self,
        sel: List[str],
        name: str,
        description: str = None,
        vertical_layout: bool = False,
        target_block: QWidget = None,
        fix_size: bool = True,
    ) -> Tuple[ConfigComboBox, QWidget]:
        combox, sublock = combobox_with_label(
            sel, name, description, vertical_layout, target_block, fix_size, parent=self
        )
        if target_block is None:
            self.addSublock(sublock)
        return combox, sublock

    def addBlockWidget(
        self,
        widget: Union[QWidget, QLayout],
        name: str = None,
        description: str = None,
        vertical_layout: bool = False,
    ) -> ConfigSubBlock:
        sublock = ConfigSubBlock(widget, name, description, vertical_layout)
        self.addSublock(sublock)
        return sublock

    def addCheckBox(
        self, name: str, description: str = None, target_block: ConfigSubBlock = None
    ) -> QCheckBox:
        checkbox, sublock = checkbox_with_label(name, description, target_block)
        if target_block is None:
            self.addSublock(sublock)
        return checkbox, sublock

    def addGroupedBlock(
        self,
        group_title: str,
        widget: QWidget,
        object_name: str = None,
        name: str = None,
        description: str = None,
    ) -> ConfigSubBlock:
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


def _scroll_interval() -> int:
    """Determine timer interval (ms) based on animation_fps or display refresh."""
    fps = pcfg.animation_fps
    if fps > 0:
        return int(round(1000.0 / fps))
    try:
        app = QGuiApplication.instance()
        if app is None:
            return 8
        screens = app.screens()
        if not screens:
            return 8
        hz = screens[0].refreshRate()
        if hz <= 0:
            return 8
        interval = int(round(1000.0 / (hz + 10)))
        return max(4, min(interval, 16))
    except Exception:
        return 8


class ConfigNotePopup(QFrame):
    """Floating popup for ConfigSubBlock notes. Anchors to a ? button and
    auto-closes on focus loss via Qt.Popup flag."""

    _DURATION = 200

    def __init__(self, anchor: QWidget, text: str):
        super().__init__(
            None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(True)
        self.setObjectName("ConfigNotePopup")
        self._anchor = anchor
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(320)
        label.setTextFormat(Qt.TextFormat.RichText)
        font = label.font()
        font.setPointSize(max(font.pointSize() - 2, 1))
        label.setFont(font)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        layout.addWidget(label)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

    def show(self):
        # Position: to the left of anchor, vertically centered
        btn_global = self._anchor.mapToGlobal(QPoint(0, 0))
        btn_h = self._anchor.height()
        self.adjustSize()
        pw = self.sizeHint().width()
        ph = self.sizeHint().height()
        x = btn_global.x() - pw - 12
        y = btn_global.y() + (btn_h - ph) // 2
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if x < screen.left():
            x = btn_global.x() + self._anchor.width() + 12
        y = max(screen.top(), min(y, screen.bottom() - ph))
        self.move(x, y)

        super().show()
        if pcfg.animation_fps < 0:
            self._effect.setOpacity(1.0)
            return
        self._elapsed.start()
        self._anim_timer.start(_scroll_interval())

    def _tick(self):
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._DURATION, 1.0)
        eased = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(progress)
        self._effect.setOpacity(eased)
        if progress >= 1.0:
            self._anim_timer.stop()
            self._effect.setOpacity(1.0)


class TableItem(QStandardItem):
    """Item for the nav tree.  Ported from upstream with font-size support."""

    def __init__(self, text, fontsize, section_key=None, target_widget=None):
        super().__init__()
        font = self.font()
        font.setPointSizeF(fontsize)
        self.setFont(font)
        self.setText(text)
        self.setEditable(False)
        if section_key is not None:
            self.setData(section_key, Qt.ItemDataRole.UserRole)
        if target_widget is not None:
            self.setData(id(target_widget), Qt.ItemDataRole.UserRole + 1)

    def setBold(self, bold: bool):
        font = self.font()
        font.setBold(bold)
        self.setFont(font)


class TreeModel(QStandardItemModel):
    """Provides size hints matching upstream's row height calculation."""

    # https://stackoverflow.com/questions/32229314/pyqt-how-can-i-set-row-heights-of-qtreeview
    def data(self, index, role):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.SizeHintRole:
            size = QSize()
            item = self.itemFromIndex(index)
            size.setHeight(item.font().pointSize() + 14)
            return size
        else:
            return super().data(index, role)


class ConfigTable(QTreeView):
    """Upstream-style navigation tree.

    Ported from upstream's ``ConfigTable`` with native expand/collapse
    enabled on header items.  Selection is indicated by bold text.
    """

    section_pressed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        treeModel = TreeModel()
        self.setModel(treeModel)
        self.selected: TableItem = None
        self.setHeaderHidden(True)
        self.setMinimumWidth(NAVLIST_WIDTH)
        self.setMaximumWidth(NAVLIST_WIDTH)
        self.section_items = {}

        # Native expand/collapse on header items
        self.setIndentation(20)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def addHeader(self, header: str) -> TableItem:
        rootNode = self.model().invisibleRootItem()
        ti = TableItem(header, CONFIG_FONTSIZE_TABLE + 3)
        ti.setSelectable(False)
        rootNode.appendRow(ti)
        return ti

    def addSection(self, parent: TableItem, text: str, section_key: str, target_widget=None) -> TableItem:
        item = TableItem(text, CONFIG_FONTSIZE_TABLE, section_key, target_widget)
        parent.appendRow(item)
        self.section_items[section_key] = item
        return item

    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection):
        sel = selected.indexes()
        model = self.model()

        self.selected = model.itemFromIndex(sel[0]) if len(sel) > 0 else None
        for i in deselected.indexes():
            item = self.model().itemFromIndex(i)
            if item is not None:
                item.setBold(False)

        index = self.currentIndex()
        if index.isValid():
            item = self.model().itemFromIndex(index)
            if item is not None and item.isSelectable():
                item.setBold(True)
                section_key = item.data(Qt.ItemDataRole.UserRole)
                if section_key is not None:
                    self.section_pressed.emit(section_key)

        super().selectionChanged(selected, deselected)

    def setCurrentSection(self, section_key: str):
        item = self.section_items.get(section_key)
        if item is not None and self.currentIndex() != item.index():
            self.setCurrentIndex(item.index())

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self.selected is not None:
            section_key = self.selected.data(Qt.ItemDataRole.UserRole)
            if section_key is not None:
                self.section_pressed.emit(section_key)


class ConfigContent(QScrollArea):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName('ConfigContent')
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

        self.setVerticalScrollBar(ConfigScrollBar(self))

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.timeout.connect(self._update_scroll)
        self._scroll_elapsed = QElapsedTimer()
        self._scroll_start_y = 0
        self._scroll_end_y = 0
        self._scroll_duration = 350
        self._scroll_easing = QEasingCurve(QEasingCurve.Type.InOutExpo)
        self._animating_scroll = False

    def addConfigBlock(self, block: ConfigBlock):
        self.vlayout.addWidget(block)
        self.config_block_list.append(block)

    def scrollToWidget(self, widget: QWidget):
        if self._animating_scroll:
            self._scroll_timer.stop()
            self._animating_scroll = False
            self.verticalScrollBar().setValue(self._scroll_end_y)

        target = widget.mapTo(self.widget(), QPoint(0, 0)).y()
        vh = self.viewport().height()
        target -= int(vh * 0.15)
        sb = self.verticalScrollBar()
        target = max(0, min(target, sb.maximum()))

        if pcfg.animation_fps < 0:
            sb.setValue(target)
            return

        self._scroll_start_y = sb.value()
        self._scroll_end_y = target
        self._scroll_duration = 350
        self._scroll_elapsed.start()
        self._animating_scroll = True
        self._scroll_timer.start(_scroll_interval())

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return

        sb = self.verticalScrollBar()
        direction = 1 if delta > 0 else -1

        if pcfg.animation_fps < 0:
            sb.setValue(sb.value() - delta * 100 // 120)
            event.accept()
            return

        offset = direction * 100
        if self._animating_scroll:
            self._scroll_end_y = max(0, min(self._scroll_end_y - offset, sb.maximum()))
        else:
            self._scroll_easing.setType(QEasingCurve.Type.OutCubic)
            self._scroll_start_y = sb.value()
            self._scroll_end_y = max(
                0, min(self._scroll_start_y - offset, sb.maximum())
            )
            self._scroll_duration = 150
            self._scroll_elapsed.start()
            self._animating_scroll = True
            self._scroll_timer.start(_scroll_interval())

        event.accept()

    def _update_scroll(self):
        elapsed = self._scroll_elapsed.elapsed()
        progress = min(elapsed / self._scroll_duration, 1.0)
        eased = self._scroll_easing.valueForProgress(progress)

        current = int(
            round(
                self._scroll_start_y
                + (self._scroll_end_y - self._scroll_start_y) * eased
            )
        )
        self.verticalScrollBar().setValue(current)

        if progress >= 1.0:
            self._scroll_timer.stop()
            self._animating_scroll = False
            # Trigger final sync so nav matches settled scroll position
            self.verticalScrollBar().valueChanged.emit(self.verticalScrollBar().value())


DEFAULT_SHORTCUTS = {
    "prev_page": ["A"],
    "next_page": ["D"],
    "prev_page_alt": ["PgUp"],
    "next_page_alt": ["PgDown"],
    "textedit_mode": ["T"],
    "textblock_mode": ["W"],
    "drawboard_mode": ["P"],
    "zoom_in": ["Ctrl++"],
    "zoom_out": ["Ctrl+-"],
    "preview": ["Tab"],
    "delete_blks": ["Del"],
    "delete_blks_alt": ["Ctrl+D"],
    "select_all": ["Ctrl+A"],
    "bold": ["Ctrl+B"],
    "italic": ["Ctrl+I"],
    "underline": ["Ctrl+U"],
    "undo": ["Ctrl+Z"],
    "redo": ["Ctrl+Y"],
    "page_search": ["Ctrl+F"],
    "global_search": ["Ctrl+G"],
    "escape": ["Escape"],
    "space_inpaint": ["Space"],
    "hand_tool": ["H"],
    "rect_tool": ["R"],
    "inpaint_tool": ["J"],
    "pen_tool": ["B"],
    "merge_tool": ["Ctrl+Shift+M"],
    "quick_symbol": [],
    "advanced_align": [],
    "merge_blks": [],
    "toggle_original_opacity": [],
    "path_reorder": [],
    "move_up": [],
    "move_down": [],
    "move_top": [],
    "move_bottom": [],
}

_ACTION_NAMES = {
    "prev_page": "Page Up",
    "next_page": "Page Down",
    "prev_page_alt": "Page Up (alt)",
    "next_page_alt": "Page Down (alt)",
    "textedit_mode": "Text Editor",
    "textblock_mode": "Text Block",
    "drawboard_mode": "Draw Board",
    "zoom_in": "Zoom In",
    "zoom_out": "Zoom Out",
    "preview": "Preview",
    "delete_blks": "Delete",
    "delete_blks_alt": "Delete (alt)",
    "select_all": "Select All",
    "bold": "Bold",
    "italic": "Italic",
    "underline": "Underline",
    "undo": "Undo",
    "redo": "Redo",
    "page_search": "Page Search",
    "global_search": "Global Search",
    "escape": "Escape",
    "space_inpaint": "Inpaint",
    "hand_tool": "Hand Tool",
    "rect_tool": "Rect Tool",
    "inpaint_tool": "Inpaint Tool",
    "pen_tool": "Pen Tool",
    "merge_tool": "Merge Tool",
    "quick_symbol": "Quick Symbol",
    "advanced_align": "Advanced Alignment",
    "merge_blks": "Merge Text Blocks",
    "toggle_original_opacity": "Toggle Original Compare",
    "path_reorder": "Path Reorder",
    "move_up": "Move Up",
    "move_down": "Move Down",
    "move_top": "Move to Top",
    "move_bottom": "Move to Bottom",
}

# Shortcut groups for organized display
_SHORTCUT_GROUPS = [
    ("Navigation", ["prev_page", "next_page", "prev_page_alt", "next_page_alt"]),
    ("View", ["zoom_in", "zoom_out", "preview", "toggle_original_opacity"]),
    (
        "Edit",
        [
            "textedit_mode",
            "textblock_mode",
            "drawboard_mode",
            "delete_blks",
            "delete_blks_alt",
            "select_all",
            "bold",
            "italic",
            "underline",
            "undo",
            "redo",
            "merge_blks",
        ],
    ),
    (
        "Tools",
        [
            "hand_tool",
            "rect_tool",
            "inpaint_tool",
            "pen_tool",
            "merge_tool",
            "path_reorder",
            "space_inpaint",
            "quick_symbol",
            "advanced_align",
        ],
    ),
    ("Reorder", ["move_up", "move_down", "move_top", "move_bottom"]),
    ("Search", ["page_search", "global_search"]),
    ("General", ["escape"]),
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
        name.setStyleSheet(
            f"color: {s['name_clr']}; background: transparent; border: none;"
        )
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
        self._add_btn = QPushButton("+")
        self._add_btn.setFixedSize(24, 24)
        self._add_btn.setToolTip(self.tr("Add shortcut"))
        self._add_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {s['add_bdr']}; border-radius: 3px; "
            f"color: {s['add_clr']}; background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ border-color: {s['add_hvr_bdr']}; color: {s['add_hvr_clr']}; }}"
        )
        self._add_btn.clicked.connect(self._add_shortcut)
        btn_layout.addWidget(self._add_btn)

        # Clear button
        self._clear_btn = QPushButton("Del")
        self._clear_btn.setFixedSize(28, 24)
        self._clear_btn.setToolTip(self.tr("Disable this shortcut"))
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: 3px; color: {s['btn_clr']}; "
            f"background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ color: {s['close_hvr']}; }}"
        )
        self._clear_btn.clicked.connect(self._clear)
        btn_layout.addWidget(self._clear_btn)

        # Reset button
        self._reset_btn = QPushButton("Rst")
        self._reset_btn.setFixedSize(28, 24)
        self._reset_btn.setToolTip(self.tr("Reset to Default"))
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: 3px; color: {s['btn_clr']}; "
            f"background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ color: {s['reset_hvr']}; }}"
        )
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
                    f"color: {s['pill_text']}; background: transparent; border: none;"
                )
                fl.addWidget(lbl)
                close_btn = QPushButton("x")
                close_btn.setFixedSize(22, 22)
                close_btn.setStyleSheet(
                    f"QPushButton {{ border: none; border-radius: 2px; color: {s['close_clr']}; "
                    f"background: transparent; padding: 0px; }}"
                    f"QPushButton:hover {{ color: {s['close_hvr']}; "
                    f"background: rgba(200,50,50,0.2); }}"
                )
                close_btn.clicked.connect(
                    lambda checked, ks=k: self._remove_shortcut(ks)
                )
                fl.addWidget(close_btn)
                frame.setStyleSheet(
                    f"QFrame {{ background: {s['pill_bg']}; border-radius: 4px; }}"
                )
                self.shortcuts_layout.addWidget(frame)
        else:
            # Show disabled placeholder
            from .theme_helpers import shortcut_styles

            s = shortcut_styles()
            self._disabled_placeholder = QLabel(self.tr("— None —"))
            self._disabled_placeholder.setStyleSheet(
                f"color: {s['disabled_clr']}; background: transparent; font-style: italic;"
            )
            self.shortcuts_layout.addWidget(self._disabled_placeholder)

    def _add_shortcut(self):
        edit = QKeySequenceEdit()
        edit.setFixedWidth(120)
        edit.setFixedHeight(24)
        edit.setStyleSheet("QKeySequenceEdit { padding: 1px 4px; }")
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
        """)
        scroll_area.setVerticalScrollBar(ConfigScrollBar(scroll_area))

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
                    sep.setStyleSheet(f"background: {s['add_bdr']};")
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
        self.setWindowTitle(self.tr("Shortcut Editor"))
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
        self.setWindowTitle(self.tr("Font Exclusion"))
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        # Search bar
        self.search_edit = ConfigLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search fonts..."))
        self.search_edit.textChanged.connect(self._filter_lists)
        layout.addWidget(self.search_edit)

        # Side-by-side list widgets
        lists_layout = QHBoxLayout()

        # Available fonts list
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.tr("Available Fonts")))
        self.available_list = QListWidget()
        self.available_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        left_layout.addWidget(self.available_list)
        lists_layout.addLayout(left_layout)

        # Center buttons
        btn_layout = QVBoxLayout()
        btn_layout.addStretch()
        self.hide_btn = QPushButton(">")
        self.hide_btn.setFixedWidth(40)
        self.hide_btn.setToolTip(self.tr("Hide selected fonts"))
        self.hide_btn.clicked.connect(self._hide_fonts)
        btn_layout.addWidget(self.hide_btn)
        self.show_btn = QPushButton("<")
        self.show_btn.setFixedWidth(40)
        self.show_btn.setToolTip(self.tr("Show selected fonts"))
        self.show_btn.clicked.connect(self._show_fonts)
        btn_layout.addWidget(self.show_btn)
        btn_layout.addStretch()
        lists_layout.addLayout(btn_layout)

        # Excluded fonts list
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel(self.tr("Hidden Fonts")))
        self.excluded_list = QListWidget()
        self.excluded_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        right_layout.addWidget(self.excluded_list)
        lists_layout.addLayout(right_layout)

        layout.addLayout(lists_layout)

        # Legacy fonts button
        self.legacy_btn = QPushButton(self.tr("Add Legacy Fonts to Hidden List"))
        self.legacy_btn.setObjectName("ConfigButton")
        self.legacy_btn.clicked.connect(self._on_add_legacy_fonts)
        layout.addWidget(self.legacy_btn)

        # OK / Cancel buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Populate lists
        self._populate_lists()

    def _add_font_item(self, list_widget: QListWidget, font_name: str):
        """Add a font name to a list widget.
        
        Legacy fonts skip the typeface preview and get a "[Legacy]" suffix.
        The original font name is stored in ``Qt.UserRole``.
        """
        from utils.shared import LEGACY_FONTS
        from qtpy.QtCore import Qt

        is_legacy = font_name in LEGACY_FONTS
        display = f"{font_name} [{self.tr('Legacy')}]" if is_legacy else font_name
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, font_name)
        if not is_legacy:
            item.setFont(QFont(font_name, 11))
        list_widget.addItem(item)

    @staticmethod
    def _real_name(item: QListWidgetItem) -> str:
        """Return the original font name stored in UserRole."""
        name = item.data(Qt.ItemDataRole.UserRole)
        return name if name else item.text()

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
            item.setHidden(bool(text) and text not in self._real_name(item).lower())
        for i in range(self.excluded_list.count()):
            item = self.excluded_list.item(i)
            item.setHidden(bool(text) and text not in self._real_name(item).lower())

    def _hide_fonts(self):
        for item in self.available_list.selectedItems():
            self.available_list.takeItem(self.available_list.row(item))
            self._add_font_item(self.excluded_list, self._real_name(item))

    def _show_fonts(self):
        for item in self.excluded_list.selectedItems():
            self.excluded_list.takeItem(self.excluded_list.row(item))
            self._add_font_item(self.available_list, self._real_name(item))

    def _on_add_legacy_fonts(self):
        """Detect legacy Windows fonts and add them to the hidden list automatically."""
        from utils.shared import ALL_FONT_FAMILIES, LEGACY_FONTS

        # Fonts that exist on this system AND are legacy
        exist_legacy = set(ALL_FONT_FAMILIES) & LEGACY_FONTS
        already_excluded = {
            self._real_name(self.excluded_list.item(i))
            for i in range(self.excluded_list.count())
        }
        to_add = sorted(exist_legacy - already_excluded)

        if not to_add:
            QMessageBox.information(
                self,
                self.tr("Legacy Fonts"),
                self.tr("No additional legacy fonts detected on this system."),
            )
            return

        for font_name in to_add:
            self._add_font_item(self.excluded_list, font_name)
            # Remove from available list
            for i in range(self.available_list.count()):
                if self._real_name(self.available_list.item(i)) == font_name:
                    self.available_list.takeItem(i)
                    break

        QMessageBox.information(
            self,
            self.tr("Legacy Fonts"),
            self.tr(
                "Added {count} legacy font(s) to the hidden list:\n\n{fonts}"
            ).replace("{count}", str(len(to_add))).replace("{fonts}", "\n".join(to_add)),
        )

    def get_excluded_fonts(self) -> List[str]:
        return [
            self._real_name(self.excluded_list.item(i))
            for i in range(self.excluded_list.count())
        ]


class _DeadLayout:
    """No-op layout target: addWidget(head_widget) registers it as a page."""

    def addWidget(self, widget):
        ConfigPanel._active_panel._add_page(widget)


class _DeadBlock:
    """Stands in for the old ConfigBlock during paged layout. Sections built
    here are registered as pages on the active ConfigPanel's pageStack."""

    def __init__(self, header: str):
        self.header = header
        self.vlayout = _DeadLayout()
        self.subblock_list = []

    def addGroupedBlock(self, group_title, widget, object_name=None, name=None, description=None):
        return ConfigPanel._active_panel._add_grouped_page(
            group_title, widget, object_name, name, description
        )


class ConfigPanel(Widget):
    # Whitelist: widgets whose active presence prevents outside-click closing.
    # When a dialog from this set is open, clicking outside the config panel
    # won't close it.
    PRESERVE_ACTIVE_WIDGET_CLASS_NAMES = {
        'FrameLessMessageBox',
        'ImgtransProgressMessageBox',
        'KeywordSubWidget',
        'MessageBox',
        'ProgressMessageBox',
    }

    save_config = Signal()
    unload_models = Signal()
    reload_textstyle = Signal(bool)
    font_exclusion_changed = Signal()
    profiles_changed = Signal()
    shortcuts_changed = Signal()
    presets_changed = Signal()
    seq_badge_changed = Signal()
    text_rendering_changed = Signal()

    # Active instance used by _DeadBlock/_DeadLayout to find the page stack
    # during __init__ construction.
    _active_panel: "ConfigPanel | None" = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("ConfigPanel")
        # Independent OS window with standard dialog title bar, no taskbar entry.
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle(self.tr("Settings"))
        self.setMinimumSize(700, 450)
        ConfigPanel._active_panel = self
        self._modal_ref = None  # OverlayModal, injected by MainWindow
        self._outside_click_filter_installed = False

        # Right-hand side is now a page stack: each nav item switches a page
        # (no long scroll). ``configContent`` is kept as a back-compat alias
        # for ``pageStack`` to minimize external direct-access churn.
        self.pageStack = QStackedWidget()
        self.configContent = self.pageStack
        # Map: section_widget (PanelGroupBox) -> page index in pageStack
        self._page_index: dict = {}
        # Dead blocks retained for compatibility; sections live in pageStack
        dlConfigPanel = _DeadBlock(self.tr("DL Module"))
        generalConfigPanel = _DeadBlock(self.tr("General"))

        label_text_det = self.tr("Text Detection")
        label_text_ocr = self.tr("OCR")
        label_inpaint = self.tr("Inpaint")
        label_translator = self.tr("Translator")
        label_project = self.tr("Project")
        label_typesetting = self.tr("Typesetting")
        label_performance = self.tr("Performance")
        label_interface = self.tr("Interface")

        # === Models group ===
        models_group = PanelGroupBox(self.tr("Models"))
        self.models_group = models_group
        models_group.setProperty("cfgPage", True)
        models_group.setObjectName("GroupModels")
        models_vlayout = models_group.contentLayout()
        models_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        models_vlayout.setSpacing(8)

        # -- Model Loading section --
        models_vlayout.addWidget(ConfigSectionHeader(self.tr("Model Loading")))

        # Load on demand
        self.load_model_checker = ConfigCheckBox()
        font = self.load_model_checker.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        self.load_model_checker.setFont(font)
        self.load_model_checker.setText(self.tr("Load models on demand to save memory."))
        cb_block = ConfigSubBlock(
            self.load_model_checker,
            name=self.tr("Load models on demand"),
            note=self.tr("<p>When enabled, models are loaded only on <b>first use</b> instead of at startup. Reduces initial memory and launch time. Recommended for systems with limited GPU memory.</p>"),
        )
        models_vlayout.addWidget(cb_block)

        # Empty cache
        self.empty_runcache_checker = ConfigCheckBox()
        font = self.empty_runcache_checker.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        self.empty_runcache_checker.setFont(font)
        self.empty_runcache_checker.setText(self.tr("Empty cache after RUN to save memory."))
        cb_block2 = ConfigSubBlock(
            self.empty_runcache_checker,
            name=self.tr("Empty cache after RUN"),
            note=self.tr("<p>Clears intermediate inference data after each pipeline run. Frees <b>GPU/CPU memory</b> between runs. Useful when working with large projects or limited hardware.</p>"),
        )
        models_vlayout.addWidget(cb_block2)

        self.load_model_checker.stateChanged.connect(self.on_load_model_changed)
        self.empty_runcache_checker.stateChanged.connect(self.on_runcache_changed)

        # -- Management section --
        models_vlayout.addWidget(ConfigSectionHeader(self.tr("Management")))

        unload_btn = QPushButton(self.tr("Unload All Models"))
        unload_btn.setObjectName("ConfigButton")
        unload_btn.clicked.connect(self.unload_models)
        unload_sublock = ConfigSubBlock(
            unload_btn,
            name=self.tr("Unload models"),
            note=self.tr("<p>Immediately releases all loaded models from memory. Use this to free <b>GPU/CPU resources</b> without restarting the application.</p>"),
        )
        models_vlayout.addWidget(unload_sublock)

        network_btn = QPushButton(self.tr("Network & Mirror Settings..."))
        network_btn.setObjectName("ConfigButton")
        network_btn.clicked.connect(self._open_network_settings)
        network_sublock = ConfigSubBlock(
            network_btn,
            name=self.tr("Network"),
            note=self.tr("<p>Configure network proxies, mirror servers, and download sources. Useful for systems behind <b>firewalls</b> or in restricted environments.</p>"),
        )
        models_vlayout.addWidget(network_sublock)

        # Register Models as its own page
        self._add_page(models_group)

        self.detect_config_panel = TextDetectConfigPanel(
            self.tr("Detector"), scrollWidget=self
        )
        self.detect_sub_block = self._add_grouped_page(
            label_text_det, self.detect_config_panel, object_name="GroupDetect",
            note=self.tr("<p>Select the <b>text detection engine</b>. Different detectors offer varying accuracy and speed. Some engines may require additional model downloads on first use.</p>"),
        )
        detect_group = self.detect_sub_block.section_widget
        self.detect_config_panel.keep_existing_checker.clicked.connect(
            self.on_keepline_clicked
        )

        self.ocr_config_panel = OCRConfigPanel(self.tr("OCR"), scrollWidget=self)
        self.ocr_sub_block = self._add_grouped_page(
            label_text_ocr, self.ocr_config_panel, object_name="GroupOCR",
            note=self.tr("<p>Select the <b>OCR</b> (Optical Character Recognition) engine. This stage extracts text from detected text regions in the image.</p>"),
        )
        ocr_group = self.ocr_sub_block.section_widget

        self.inpaint_config_panel = InpaintConfigPanel(
            self.tr("Inpainter"), scrollWidget=self
        )
        self.inpaint_sub_block = self._add_grouped_page(
            label_inpaint, self.inpaint_config_panel, object_name="GroupInpaint",
            note=self.tr("<p>Select the <b>image inpainting engine</b>. After erasing text regions, the inpainter fills the background. Quality varies by image complexity and engine capability.</p>"),
        )
        inpaint_group = self.inpaint_sub_block.section_widget

        self.trans_config_panel = TranslatorConfigPanel(
            label_translator, scrollWidget=self
        )
        self.trans_sub_block = self._add_grouped_page(
            label_translator, self.trans_config_panel, object_name="GroupTranslate",
            note=self.tr("<p>Select the <b>translation engine</b>. Online translators require an API profile with credentials configured under <b>LLM Profile</b>.</p>"),
        )
        trans_group = self.trans_sub_block.section_widget
        self.trans_config_panel.navigate_to_llm_profile.connect(
            self.focusOnLLMProfile
        )

        # === LLM Profile page (inline profile manager) ===
        from utils.profile_manager import ProfileManagerWidget

        self.llm_profiles_panel = ProfileManagerWidget()
        self.llm_profiles_panel.profiles_changed.connect(self.profiles_changed.emit)
        self._add_page(self.llm_profiles_panel)

        # === General: Project (startup + save merged) ===
        project_widget = QWidget()
        project_layout = QVBoxLayout(project_widget)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setSpacing(0)

        # Startup
        self.open_on_startup_checker = ConfigCheckBox(
            self.tr("Reopen last project on startup")
        )
        self.open_on_startup_checker.stateChanged.connect(
            self.on_open_onstartup_changed
        )
        project_layout.addWidget(ConfigSectionHeader(self.tr("Startup")))

        startup_sublock = ConfigSubBlock(
            self.open_on_startup_checker,
            note=self.tr("<p>Reopen the last project automatically when the application starts. Saves time when continuing work on the same project.</p>"),
        )
        project_layout.addWidget(startup_sublock)

        # Output section label
        project_layout.addWidget(ConfigSectionHeader(self.tr("Output")))

        self.rst_imgformat_combobox, self.rst_imgsave_sublock = combobox_with_label(
            ["PNG", "JPG", "WEBP", "JXL"], self.tr("Result image format"),
            note=self.tr("<p>Choose the output format for translated images:</p><p><b>PNG</b> — lossless quality<br/><b>JPG / WEBP</b> — smaller files, some quality loss<br/><b>JXL</b> — high compression efficiency with lossless option</p>"),
            parent=self,
        )
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        project_layout.addWidget(self.rst_imgsave_sublock)

        self.rst_imgquality_edit = PercentageLineEdit("100")
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)
        self.rst_quality_sublock = ConfigSubBlock(
            self.rst_imgquality_edit, self.tr("Quality"),
            note=self.tr("<p>Output image quality (<code>0-100</code>). Higher values give better quality but larger file sizes. Applies to <b>JPG</b> and <b>WEBP</b> only. Also used when <b>Auto detect source format</b> matches a lossy source.</p>"),
            vertical_layout=False,
        )
        self.rst_quality_sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.rst_quality_sublock.layout().insertStretch(-1)
        project_layout.addWidget(self.rst_quality_sublock)

        self.rst_autoformat_checker, autoformat_sublock = checkbox_with_label(
            self.tr("Auto detect source format"),
            note=self.tr("<p>When enabled, the output format automatically matches the <b>source image format</b>. Overrides the format selected above. If the source uses a lossy format (<b>JPG</b>, <b>WEBP</b>), the <b>Quality</b> setting above is used.</p>"),
        )
        self.rst_autoformat_checker.stateChanged.connect(self.on_autoformat_changed)
        project_layout.addWidget(autoformat_sublock)

        self.intermediate_imgformat_combobox, intermediate_imsave_sublock = (
            combobox_with_label(
                ["PNG", "JXL"], self.tr("Intermediate image format"),
                note=self.tr("<p>Format used for intermediate processing data:</p><p><b>PNG</b> — default lossless option<br/><b>JXL</b> — better compression for mask and inpainted images</p>"),
                parent=self,
            )
        )
        self.intermediate_imgformat_combobox.activated.connect(
            self.on_intermediate_imgformat_changed
        )
        project_layout.addWidget(intermediate_imsave_sublock)

        # Temporary Projects
        project_layout.addWidget(ConfigSectionHeader(self.tr("Temporary Projects")))

        self.temp_clean_checker = ConfigCheckBox(
            self.tr("Clean up imported image projects on exit")
        )
        self.temp_clean_checker.stateChanged.connect(
            self.on_auto_clean_temp_changed
        )
        temp_clean_sublock = ConfigSubBlock(
            self.temp_clean_checker,
	            note=self.tr(
	                "<p>When enabled, projects created by importing individual images "
	                "(via drag-drop or <b>Open Image…</b>) will be "
	                "<b>automatically deleted</b> when the application closes.</p>"
	                "<p>Use <b>Save Project As…</b> to keep a project permanently.</p>"
	            ),
        )
        project_layout.addWidget(temp_clean_sublock)

        self.project_block = generalConfigPanel.addGroupedBlock(
            label_project, project_widget, object_name="GroupGeneral"
        )

        dec_program_str = self.tr("decide by program")
        use_global_str = self.tr("use global setting")

        self._preset_editors = {}

        def _make_preset_row(label: str, config_key: str, target_layout: QVBoxLayout):
            """Build a label + comma-separated QLineEdit row for a preset list."""
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFixedWidth(110)
            row.addWidget(lbl)
            edit = ConfigLineEdit()
            edit.setText(", ".join(str(v) for v in getattr(pcfg, config_key)))
            edit.setPlaceholderText(self.tr("comma-separated values"))
            row.addWidget(edit, 1)
            sublock = ConfigSubBlock(row)
            target_layout.addWidget(sublock)
            self._preset_editors[config_key] = edit
            edit.editingFinished.connect(
                lambda k=config_key, e=edit: self._on_preset_edited(k, e)
            )

        # Build typesetting wrapper widget
        ts_widget = QWidget()
        ts_layout = QVBoxLayout(ts_widget)
        ts_layout.setContentsMargins(0, 0, 0, 0)
        ts_layout.setSpacing(0)

        # Default Font Format section
        ts_layout.addWidget(ConfigSectionHeader(self.tr("Default Font Format")))

        delegation_frame = QFrame()
        delegation_frame.setObjectName("CompactDelegationFrame")
        delegation_layout = QVBoxLayout(delegation_frame)
        delegation_layout.setContentsMargins(12, 8, 12, 8)
        delegation_layout.setSpacing(4)

        delegation_label = ConfigTextLabel(
            self.tr("Default font format (when not set per-textblock):"),
            CONFIG_FONTSIZE_CONTENT - 2,
        )
        delegation_layout.addWidget(delegation_label)

        global_fntfmt_widget = QWidget()
        global_fntfmt_layout = QGridLayout(global_fntfmt_widget)
        global_fntfmt_layout.setContentsMargins(0, 0, 0, 0)
        global_fntfmt_layout.setHorizontalSpacing(16)
        global_fntfmt_layout.setVerticalSpacing(8)
        global_fntfmt_layout.setColumnStretch(2, 1)
        delegation_layout.addWidget(global_fntfmt_widget)

        DELEGATION_COMBO_WIDTH = 140

        def _add_fontfmt_cell(row, col, label, items, signal, attr_name, tooltip=None):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            lbl = ConfigTextLabel(label, CONFIG_FONTSIZE_CONTENT - 2)
            combo = ConfigComboBox(scrollWidget=self)
            combo.addItems(items)
            combo.setFixedWidth(DELEGATION_COMBO_WIDTH)
            if tooltip:
                combo.setToolTip(tooltip)
            combo.activated.connect(signal)
            cell_layout.addWidget(lbl)
            cell_layout.addWidget(combo)
            global_fntfmt_layout.addWidget(cell, row, col)
            setattr(self, attr_name, combo)

        _add_fontfmt_cell(
            0, 0, self.tr("Font Size"),
            [dec_program_str, use_global_str],
            self.on_fntsize_flag_changed, "let_fntsize_combox",
            tooltip=self.tr(
                "decide by program: Use OCR-detected font size and enable adaptive resizing to fit text regions."
            ),
        )
        _add_fontfmt_cell(
            0, 1, self.tr("Stroke Size"),
            [dec_program_str, use_global_str],
            self.on_fntstroke_flag_changed, "let_fntstroke_combox",
            tooltip=self.tr(
                "decide by program: Calculate stroke width from OCR-detected text properties."
            ),
        )
        _add_fontfmt_cell(
            0, 2, self.tr("Alignment"),
            [dec_program_str, use_global_str],
            self.on_alignment_flag_changed, "let_alignment_combox",
            tooltip=self.tr(
                "decide by program: Detect alignment (left/center/right) from the text region shape."
            ),
        )
        _add_fontfmt_cell(
            1, 0, self.tr("Writing-mode"),
            [dec_program_str, use_global_str],
            self.on_writing_mode_flag_changed, "let_writing_mode_combox",
            tooltip=self.tr(
                "decide by program: Preserve the detected writing mode (horizontal/vertical) from source text."
            ),
        )
        _add_fontfmt_cell(
            1, 1, self.tr("Font Family"),
            [self.tr("Keep existing"), self.tr("Always use global setting")],
            self.on_family_flag_changed, "let_family_combox",
            tooltip=self.tr(
                "Keep existing: Preserve each block's existing font family (if set). Always use global setting: Override all blocks with the global default font family."
            ),
        )

        ts_layout.addWidget(ConfigSubBlock(delegation_frame))

        # Text formatting section
        ts_layout.addWidget(ConfigSectionHeader(self.tr("Text formatting")))

        self.let_autolayout_checker, al_sublock = checkbox_with_label(
            self.tr("Auto layout"),
            description=self.tr(
                "Split translation into multi-lines according to the extracted balloon region."
            ),
            note=self.tr("<p>Automatically split translated text into multiple lines matching the shape of the detected balloon or text region.</p>"),
        )
        self.let_autolayout_checker.stateChanged.connect(self.on_autolayout_changed)
        ts_layout.addWidget(al_sublock)

        self.let_uppercase_checker, uc_sublock = checkbox_with_label(
            self.tr("To uppercase"),
            note=self.tr("<p>Convert all translated text to uppercase. Useful for certain <b>typographic styles</b> or all-caps conventions.</p>"),
        )
        self.let_uppercase_checker.stateChanged.connect(self.on_uppercase_changed)
        ts_layout.addWidget(uc_sublock)

        self.let_textstyle_indep_checker, ti_sublock = checkbox_with_label(
            self.tr("Independent text styles for each projects"),
            note=self.tr("<p>When enabled, each project maintains its own <b>text style settings</b> independently instead of using shared global styles.</p>"),
        )
        self.let_textstyle_indep_checker.stateChanged.connect(
            self.on_textstyle_indep_changed
        )
        ts_layout.addWidget(ti_sublock)

        # Punctuation Position
        self.punctuation_position_combo = ConfigComboBox(fix_size=False)
        self.punctuation_position_combo.addItems(
            [self.tr("Centered (Traditional Chinese / Japanese)"), self.tr("Edge-aligned (Simplified Chinese)")]
        )
        self.punctuation_position_combo.setCurrentIndex(pcfg.punctuation_position)
        self.punctuation_position_combo.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.punctuation_position_combo.currentIndexChanged.connect(
            self.on_punctuation_position_changed
        )
        punct_pos_sublock = ConfigSubBlock(
            self.punctuation_position_combo, self.tr("Punctuation Position"),
            note=self.tr("<p>Choose punctuation alignment:</p><p><b>Centered</b> — traditional CJK style (Traditional Chinese / Japanese)<br/><b>Edge-aligned</b> — modern style (Simplified Chinese)</p>"),
        )
        ts_layout.addWidget(punct_pos_sublock)

        # Vertical Latin/Digits Length (tate-chuyoko)
        self.tatechuyoko_slider = PaintQSlider()
        self.tatechuyoko_slider.setRange(0, 5)
        self.tatechuyoko_slider.setValue(pcfg.tatechuyoko_threshold)
        self.tatechuyoko_slider.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.tatechuyoko_slider.valueChanged.connect(
            self.on_tatechuyoko_threshold_changed
        )
        tatechuyoko_sublock = ConfigSubBlock(
            self.tatechuyoko_slider, self.tr("Vertical Latin/Digits Length"),
            note=self.tr("<p>In vertical text, consecutive Latin letters/digits up to this length are displayed upright (<b>tate-chuyoko</b>). <code>0</code> disables; longer runs fall back to per-character rotation.</p>"),
        )
        ts_layout.addWidget(tatechuyoko_sublock)

        self.exclude_fonts_btn = QPushButton(self.tr("Exclude Fonts..."), parent=self)
        self.exclude_fonts_btn.setObjectName("ConfigButton")
        self.exclude_fonts_btn.clicked.connect(self.on_exclude_fonts_clicked)
        btn_sublock = ConfigSubBlock(
            self.exclude_fonts_btn, name=self.tr("Font Exclusion"),
            note=self.tr("<p>Hide selected fonts from all font selection dropdowns. Useful for filtering out <b>unusable or decorative</b> fonts.</p>"),
        )
        ts_layout.addWidget(btn_sublock)

        self.max_font_size_edit = NoArrowsSpinBox()
        self.max_font_size_edit.setRange(10, 1000)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.max_font_size_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.max_font_size_edit.valueChanged.connect(self.on_max_font_size_changed)
        max_font_sublock = ConfigSubBlock(
            self.max_font_size_edit, self.tr("Max Font Size (px)"),
            note=self.tr("<p>Maximum allowed font size in pixels. Text that would render larger than this limit is <b>scaled down</b> automatically.</p>"),
        )
        ts_layout.addWidget(max_font_sublock)

        self.typesetting_block = generalConfigPanel.addGroupedBlock(
            label_typesetting, ts_widget, object_name="GroupGeneral"
        )

        # === Save controls moved into Project group above ===

        # === General: Interface (animation + shortcuts + presets) ===
        interface_widget = QWidget()
        interface_layout = QVBoxLayout(interface_widget)
        interface_layout.setContentsMargins(0, 0, 0, 0)
        interface_layout.setSpacing(8)

        # Behavior section
        interface_layout.addWidget(ConfigSectionHeader(self.tr("Behavior")))

        # Fit image to window on open
        self.fit_window_checker = ConfigCheckBox(
            self.tr("Fit image to window when opening")
        )
        self.fit_window_checker.stateChanged.connect(self.on_fit_window_changed)
        fit_win_sublock = ConfigSubBlock(
            self.fit_window_checker, name=self.tr("Window Fit"),
            note=self.tr("<p>Automatically scale the image to fit the window when opening a project. Avoids <b>manual zooming</b> on every file open.</p>"),
        )
        interface_layout.addWidget(fit_win_sublock)

        # Sub-option: also fit on page switch
        self.fit_window_page_checker = ConfigCheckBox(
            self.tr("Also fit when switching pages")
        )
        self.fit_window_page_checker.stateChanged.connect(
            self.on_fit_window_page_changed
        )
        self.fit_window_page_checker.setVisible(False)
        self._fit_page_sublock = ConfigSubBlock(self.fit_window_page_checker)
        self._fit_page_sublock.setVisible(False)
        interface_layout.addWidget(self._fit_page_sublock)

        # Shortcut button
        self.shortcut_btn = QPushButton(self.tr("Edit Shortcuts..."), parent=self)
        self.shortcut_btn.setObjectName("ConfigButton")
        self.shortcut_btn.clicked.connect(self._open_shortcut_dialog)
        shortcut_sublock = ConfigSubBlock(
            self.shortcut_btn, name=self.tr("Shortcuts"),
        )
        interface_layout.addWidget(shortcut_sublock)

        # Context menu customization button
        self.context_menu_btn = QPushButton(
            self.tr("Customize Context Menu..."), parent=self
        )
        self.context_menu_btn.setObjectName("ConfigButton")
        self.context_menu_btn.clicked.connect(self._open_context_menu_config)
        ctxmenu_sublock = ConfigSubBlock(
            self.context_menu_btn, name=self.tr("Context Menu"),
            note=self.tr("<p>Customize the right-click context menu: reorder items, add or remove commands via drag-and-drop.</p>"),
        )
        interface_layout.addWidget(ctxmenu_sublock)

        # Combo Box Presets (moved from Typesetting)
        interface_layout.addWidget(ConfigSectionHeader(self.tr("Combo Box Presets")))

        # Helper label
        preset_hint = ConfigTextLabel(
            self.tr("Comma-separated values — used in font format panel dropdowns."),
            CONFIG_FONTSIZE_CONTENT - 3,
        )
        preset_hint_sublock = ConfigSubBlock(preset_hint)
        interface_layout.addWidget(preset_hint_sublock)

        _make_preset_row(self.tr("Font Size:"), "font_size_presets", interface_layout)
        _make_preset_row(
            self.tr("Line Spacing:"), "line_spacing_presets", interface_layout
        )
        _make_preset_row(
            self.tr("Letter Spacing:"), "letter_spacing_presets", interface_layout
        )
        _make_preset_row(
            self.tr("Stroke Width:"), "stroke_width_presets", interface_layout
        )
        _make_preset_row(self.tr("Opacity:"), "opacity_presets", interface_layout)

        # ── Original Compare ───────────────────────────────────
        interface_layout.addWidget(ConfigSectionHeader(self.tr("Original Compare")))

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        toggle_lbl = QLabel(self.tr("Preset (%):"))
        toggle_lbl.setFixedWidth(110)
        toggle_row.addWidget(toggle_lbl)
        self.orig_opacity_toggle_spin = NoArrowsSpinBox()
        self.orig_opacity_toggle_spin.setRange(0, 99)
        self.orig_opacity_toggle_spin.setValue(pcfg.original_transparency_preset)
        self.orig_opacity_toggle_spin.valueChanged.connect(
            lambda v: setattr(pcfg, "original_transparency_preset", v)
        )
        toggle_row.addWidget(self.orig_opacity_toggle_spin, 1)
        toggle_row.addStretch()
        togglesublock = ConfigSubBlock(
            toggle_row, name=self.tr("Preset"),
            note=self.tr("<p>Background opacity level when using the <b>Original Compare</b> shortcut. Lower values show more of the original image beneath the translation.</p>"),
        )
        interface_layout.addWidget(togglesublock)

        # Show sequence badge on text blocks
        self.seq_badge_checker = ConfigCheckBox(
            self.tr("Show sequence number on text blocks")
        )
        self.seq_badge_checker.setChecked(pcfg.show_seq_badge)
        self.seq_badge_checker.stateChanged.connect(self.on_seq_badge_changed)
        seq_badge_sublock = ConfigSubBlock(
            self.seq_badge_checker, name=self.tr("Sequence Badge"),
            note=self.tr("<p>Displays the block <b>sequence number</b> at the top-left corner of each text block on the canvas. Disable to avoid occlusion when working with small fonts.</p>"),
        )
        interface_layout.addWidget(seq_badge_sublock)

        self.interface_block = generalConfigPanel.addGroupedBlock(
            label_interface, interface_widget, object_name="GroupGeneral"
        )

        # === General: Performance ===
        perf_widget = QWidget()
        perf_layout = QVBoxLayout(perf_widget)
        perf_layout.setContentsMargins(0, 0, 0, 0)
        perf_layout.setSpacing(8)

        # Animation mode (moved from Interface)
        anim_widget = QWidget()
        anim_row_layout = QHBoxLayout(anim_widget)
        anim_row_layout.setContentsMargins(0, 0, 0, 0)
        anim_row_layout.setSpacing(6)
        self.anim_combo = ConfigComboBox(scrollWidget=self)
        self.anim_combo.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        self.anim_combo.addItems(
            [
                self.tr("Auto (match display)"),
                "60 FPS",
                "30 FPS",
                self.tr("Off (no animation)"),
            ]
        )
        self.anim_combo.activated.connect(self._on_anim_mode_changed)
        anim_row_layout.addWidget(self.anim_combo)
        anim_row_layout.addStretch()
        anim_sublock = ConfigSubBlock(
            anim_widget, name=self.tr("Animation"),
            note=self.tr("<p>Controls UI transition smoothness:</p><p><b>Auto</b> — matches display refresh rate<br/><b>Specific FPS</b> — cap GPU usage<br/><b>Off</b> — disables all animations</p>"),
            vertical_layout=False,
        )
        perf_layout.addWidget(anim_sublock)

        # Text rendering mode: vector vs bitmap cache
        render_widget = QWidget()
        render_row_layout = QHBoxLayout(render_widget)
        render_row_layout.setContentsMargins(0, 0, 0, 0)
        render_row_layout.setSpacing(6)
        self.text_rendering_combo = ConfigComboBox(scrollWidget=self)
        self.text_rendering_combo.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        self.text_rendering_combo.addItems([
            self.tr("Crisp (always vector)"),
            self.tr("Smooth (bitmap cache)"),
        ])
        self.text_rendering_combo.activated.connect(self._on_text_rendering_changed)
        render_row_layout.addWidget(self.text_rendering_combo)
        render_row_layout.addStretch()
        render_sublock = ConfigSubBlock(
            render_widget, name=self.tr("Text Rendering"),
            note=self.tr("<p>Controls how text is drawn on the canvas:</p><p><b>Crisp (always vector)</b> — sharp at any zoom, but dragging large blocks may lag<br/><b>Smooth (bitmap cache)</b> — smooth drag/scroll; text may blur briefly after zoom until cache rebuilds</p>"),
            vertical_layout=False,
        )
        perf_layout.addWidget(render_sublock)

        # Show decorations during drag/resize
        self.drag_decorations_checker = ConfigCheckBox(self.tr("Show decorations while resizing"))
        self.drag_decorations_checker.setChecked(pcfg.show_decorations_during_drag)
        self.drag_decorations_checker.toggled.connect(self._on_decorations_during_drag_changed)
        decor_sublock = ConfigSubBlock(
            self.drag_decorations_checker, name=self.tr("Drag Decorations"),
            note=self.tr("<p>When checked, <b>text stroke and shadow</b> remain visible while dragging or resizing a text block. Uncheck for maximum frame rate during resize.</p>"),
            vertical_layout=False,
        )
        perf_layout.addWidget(decor_sublock)

        self.performance_block = generalConfigPanel.addGroupedBlock(
            label_performance, perf_widget, object_name="GroupGeneral"
        )

        # === Config Management (Import / Export) ===
        label_config_mgmt = self.tr("Config Management")
        config_mgmt_widget = QWidget()
        config_mgmt_layout = QVBoxLayout(config_mgmt_widget)
        config_mgmt_layout.setContentsMargins(0, 0, 0, 0)
        config_mgmt_layout.setSpacing(0)

        # Export section
        config_mgmt_layout.addWidget(ConfigSectionHeader(self.tr("Export Config")))

        self.export_exclude_keys = ConfigCheckBox(
            self.tr("Exclude API keys when exporting")
        )
        self.export_exclude_keys.setChecked(True)
        font = self.export_exclude_keys.font()
        font.setPointSizeF(CONFIG_FONTSIZE_CONTENT * 0.8)
        self.export_exclude_keys.setFont(font)
        config_mgmt_layout.addWidget(ConfigSubBlock(
            self.export_exclude_keys,
            note=self.tr(
                "<p>API profiles will be exported without <b>api_key</b> and "
                "<b>proxy</b> fields. Structure and all other settings remain "
                "intact. Uncheck to include credentials "
                "(not recommended for sharing).</p>"
            ),
        ))

        export_btn = QPushButton(self.tr("Export Config..."))
        export_btn.setObjectName("ConfigButton")
        export_btn.clicked.connect(self.on_export_config)
        export_sublock = ConfigSubBlock(
            export_btn,
            name=self.tr("Export"),
            note=self.tr(
                "<p>Save current settings to a <b>.json</b> file. "
                "Useful for backups or transferring configurations "
                "between machines.</p>"
            ),
        )
        config_mgmt_layout.addWidget(export_sublock)

        # Import section
        config_mgmt_layout.addWidget(ConfigSectionHeader(self.tr("Import Config")))

        import_btn = QPushButton(self.tr("Import Config..."))
        import_btn.setObjectName("ConfigButton")
        import_btn.clicked.connect(self.on_import_config)
        import_sublock = ConfigSubBlock(
            import_btn,
            name=self.tr("Import"),
            note=self.tr(
                "<p>Load settings from a previously exported <b>.json</b> file. "
                "A compatibility summary will be shown before applying.</p>"
            ),
        )
        config_mgmt_layout.addWidget(import_sublock)

        self.config_mgmt_block = generalConfigPanel.addGroupedBlock(
            label_config_mgmt, config_mgmt_widget, object_name="GroupGeneral"
        )

        # === Navigation tree (upstream-style) ===
        self.configTable = ConfigTable()
        self.configTable.setObjectName("ConfigNavList")
        self.configTable.section_pressed.connect(self._on_nav_section_pressed)

        # Build section tree with group headers
        module_header = self.configTable.addHeader(self.tr("Modules"))
        self.configTable.addSection(module_header, self.tr("Module Actions"), "models", self.models_group)
        self.configTable.addSection(module_header, label_text_det, "detect", detect_group)
        self.configTable.addSection(module_header, label_text_ocr, "ocr", ocr_group)
        self.configTable.addSection(module_header, label_inpaint, "inpaint", inpaint_group)
        self.configTable.addSection(module_header, label_translator, "trans", trans_group)
        self.configTable.addSection(module_header, self.tr("LLM Profile"), "llm_profile", self.llm_profiles_panel)

        general_header = self.configTable.addHeader(self.tr("General"))
        self.configTable.addSection(general_header, label_project, "project", self.project_block.section_widget)
        self.configTable.addSection(general_header, label_typesetting, "typesetting", self.typesetting_block.section_widget)
        self.configTable.addSection(general_header, label_performance, "performance", self.performance_block.section_widget)
        self.configTable.addSection(general_header, label_interface, "interface", self.interface_block.section_widget)
        self.configTable.addSection(
            general_header, label_config_mgmt, "config_mgmt",
            self.config_mgmt_block.section_widget,
        )

        # Expand all headers so children are visible
        self.configTable.expandAll()

        # Map: section_key -> widget for page switching
        self._nav_section_to_widget = {
            "models": self.models_group,
            "detect": detect_group,
            "ocr": ocr_group,
            "inpaint": inpaint_group,
            "trans": trans_group,
            "llm_profile": self.llm_profiles_panel,
            "project": self.project_block.section_widget,
            "typesetting": self.typesetting_block.section_widget,
            "performance": self.performance_block.section_widget,
            "interface": self.interface_block.section_widget,
            "config_mgmt": self.config_mgmt_block.section_widget,
        }

        # Select first section by default
        self.configTable.setCurrentSection("models")

        # Layout: fixed horizontal layout with nav tree | page stack
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(2)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.configTable)
        main_layout.addWidget(self.pageStack, 1)

        # Esc closes the settings window
        esc_shortcut = QShortcut(Qt.Key.Key_Escape, self)
        esc_shortcut.activated.connect(self._close_via_esc)

    def on_load_model_changed(self):
        pcfg.module.load_model_on_demand = self.load_model_checker.isChecked()

    def on_runcache_changed(self):
        pcfg.module.empty_runcache = self.empty_runcache_checker.isChecked()

    def on_fit_window_changed(self):
        checked = self.fit_window_checker.isChecked()
        pcfg.open_image_fit_window = checked
        self.fit_window_page_checker.setVisible(checked)
        self._fit_page_sublock.setVisible(checked)

    def on_fit_window_page_changed(self):
        pcfg.fit_window_on_page_switch = self.fit_window_page_checker.isChecked()

    def on_seq_badge_changed(self):
        pcfg.show_seq_badge = self.seq_badge_checker.isChecked()
        self.seq_badge_changed.emit()

    def on_keepline_clicked(self):
        pcfg.module.keep_exist_textlines = (
            self.detect_config_panel.keep_existing_checker.isChecked()
        )

    def on_export_config(self):
        """Export current configuration to a JSON file."""
        from pathlib import Path

        exclude_keys = self.export_exclude_keys.isChecked()
        ddir = osp.dirname(pcfg.text_styles_path)
        savep = QFileDialog.getSaveFileName(
            self, self.tr("Export Config"), ddir, None, "(.json)"
        )
        if not isinstance(savep, str):
            savep = savep[0]
        if savep == "":
            return
        suffix = Path(savep).suffix
        if suffix != ".json":
            if suffix == "":
                savep = savep + ".json"
            else:
                savep = savep.replace(suffix, ".json")

        if export_config(savep, exclude_api_keys=exclude_keys):
            create_info_dialog(
                self.tr("Configuration exported to ") + savep
            )
        else:
            create_error_dialog(
                self.tr("Failed to export configuration"),
                parent=self,
            )

    def on_import_config(self):
        """Import configuration from a JSON file and merge into pcfg."""
        ddir = osp.dirname(pcfg.text_styles_path)
        p = QFileDialog.getOpenFileName(
            self, self.tr("Import Config"), ddir, None, "(.json)"
        )
        if not isinstance(p, str):
            p = p[0]
        if p == "":
            return

        result = import_config(p)
        if not result["success"]:
            create_error_dialog(
                self.tr("Failed to import configuration"),
                parent=self,
            )
            return

        # Build summary message
        lines = []
        meta = result.get("export_meta", {})
        if meta.get("app_version"):
            lines.append(
                self.tr("Source version: {ver}").format(ver=meta["app_version"])
            )
        unknown = result.get("unknown_keys", [])
        missing = result.get("missing_keys", [])

        lines.append("")

        if not unknown and not missing:
            lines.append(
                self.tr("All settings imported successfully.")
            )
        else:
            lines.append(
                self.tr("Configuration imported ({n} items checked):")
                .format(n=len(unknown) + len(missing))
            )

        if unknown:
            lines.append(
                self.tr("⚠ {n} unknown setting(s) — from a newer version, will be skipped:")
                .format(n=len(unknown))
            )
            for k in unknown[:5]:
                lines.append(f"  \u2022 {k}")
            if len(unknown) > 5:
                lines.append(self.tr("  \u2026 and {n} more").format(n=len(unknown) - 5))

        if missing:
            lines.append(
                self.tr("\u2139 {n} setting(s) not in file \u2014 current values kept:")
                .format(n=len(missing))
            )
            for k in missing[:5]:
                lines.append(f"  \u2022 {k}")
            if len(missing) > 5:
                lines.append(self.tr("  \u2026 and {n} more").format(n=len(missing) - 5))

        lines.append("")
        lines.append(
            self.tr("Please close and reopen Settings to refresh the UI.")
        )

        create_info_dialog("\n".join(lines), parent=self)

    def addConfigBlock(self, header: str) -> _DeadBlock:
        # Legacy shim — sections are now pages. Returned block's
        # addGroupedBlock/vlayout route into this panel's pageStack.
        return _DeadBlock(header)

    def _wrap_page(self, content: QWidget) -> QScrollArea:
        """Wrap a section widget into a scrollable page container."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setContentsMargins(0, 0, 0, 0)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setVerticalScrollBar(ConfigScrollBar(area))
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(*CONFIGBLOCK_CONTENT_MARGINS)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(content)
        # Keep the content at its natural height instead of letting the
        # scroll area stretch a single short group to the full page height.
        content.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        area.setWidget(page)
        return area

    def _add_page(self, content: QWidget) -> int:
        """Register a bare widget (e.g. the Models group) as a page."""
        area = self._wrap_page(content)
        idx = self.pageStack.addWidget(area)
        self._page_index[id(content)] = idx
        return idx

    def _add_grouped_page(
        self, group_title, widget, object_name=None, name=None, description=None, note=None
    ) -> ConfigSubBlock:
        """Replacement for legacy ConfigBlock.addGroupedBlock: build a
        PanelGroupBox ``group`` whose section_widget becomes a page."""
        group = PanelGroupBox(group_title)
        if object_name:
            group.setObjectName(object_name)
        group.setProperty("cfgPage", True)
        group_vlayout = group.contentLayout()
        group_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        group_vlayout.setSpacing(0)

        sublock = ConfigSubBlock(widget, name=name, description=description, note=note)
        group_vlayout.addWidget(sublock)

        # Hide the panel-internal module_label — PanelGroupBox title already
        # provides the same heading, avoiding visual redundancy.
        if hasattr(widget, 'module_label') and widget.module_label is not None:
            widget.module_label.hide()

        idx = self._add_page(group)
        sublock.section_widget = group
        self.subblock_list = getattr(self, "subblock_list", [])
        # keep a reference list of sublocks for parity with legacy blocks
        if not hasattr(self, "_all_subblocks"):
            self._all_subblocks = []
        self._all_subblocks.append(sublock)
        return sublock

    def _build_grouped_widget(
        self, group_title, widget, object_name=None, name=None, description=None, note=None
    ):
        """Build a PanelGroupBox + ConfigSubBlock without registering as a
        separate page. Returns (group_box, subblock) for use in combined pages."""
        group = PanelGroupBox(group_title)
        if object_name:
            group.setObjectName(object_name)
        group.setProperty("cfgPage", True)
        group_vlayout = group.contentLayout()
        group_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        group_vlayout.setSpacing(0)
        sublock = ConfigSubBlock(widget, name=name, description=description, note=note)
        group_vlayout.addWidget(sublock)
        sublock.section_widget = group
        if not hasattr(self, "_all_subblocks"):
            self._all_subblocks = []
        self._all_subblocks.append(sublock)
        return group, sublock

    def on_open_onstartup_changed(self):
        pcfg.open_recent_on_startup = self.open_on_startup_checker.isChecked()

    def on_auto_clean_temp_changed(self):
        pcfg.auto_clean_temp_projects = self.temp_clean_checker.isChecked()

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
        if self._run_modal_dialog(dialog) == QDialog.DialogCode.Accepted:
            excluded = dialog.get_excluded_fonts()
            pcfg.excluded_fonts = excluded
            self.font_exclusion_changed.emit()
            from utils.config import save_config

            save_config()

    def _set_quality_visual_state(self, enabled: bool):
        """Toggle quality subblock + show "—" placeholder when disabled.
        
        Label dimming is handled automatically by ``ConfigSubBlock.changeEvent``.
        """
        self.rst_quality_sublock.setEnabled(enabled)
        self.rst_imgquality_edit.blockSignals(True)
        if enabled:
            self.rst_imgquality_edit.setText(str(pcfg.imgsave_quality))
        else:
            self.rst_imgquality_edit.setText("—")
        self.rst_imgquality_edit.blockSignals(False)

    def on_rst_imgformat_changed(self):
        pcfg.imgsave_ext = "." + self.rst_imgformat_combobox.currentText().lower()
        # PNG is lossless — quality setting is irrelevant
        is_png = self.rst_imgformat_combobox.currentText() == "PNG"
        self._set_quality_visual_state(not is_png)

    def on_autoformat_changed(self):
        pcfg.imgsave_auto_format = self.rst_autoformat_checker.isChecked()
        self.rst_imgsave_sublock.setEnabled(not pcfg.imgsave_auto_format)
        # When auto-format is on the actual format depends on the source,
        # which may be lossy — keep quality editable so its value can be seen.
        if pcfg.imgsave_auto_format:
            self._set_quality_visual_state(True)

    def on_max_font_size_changed(self, value: int):
        pcfg.max_font_size = value

    def on_punctuation_position_changed(self, index: int):
        pcfg.punctuation_position = index
        self._apply_punctuation_settings()

    def on_tatechuyoko_threshold_changed(self, value: int):
        pcfg.tatechuyoko_threshold = value
        self._apply_tatechuyoko_settings()

    def _apply_tatechuyoko_settings(self):
        """Apply tatechuyoko_threshold to ALL existing text items."""
        from .shared_widget import canvas as sw_canvas
        from .textitem import TextBlkItem

        if sw_canvas is None:
            return
        for item in sw_canvas.items():
            if isinstance(item, TextBlkItem):
                # 强制整体重排，不能走 setPunctuationPosition 那种增量
                layout = item.layout
                if layout is not None and hasattr(layout, "tatechuyoko_threshold"):
                    layout.tatechuyoko_threshold = pcfg.tatechuyoko_threshold
                    layout.reLayout()
                item.repaint_background()
                item.update()

    def _apply_punctuation_settings(self):
        """Apply punctuation_position to ALL existing text items."""
        from .shared_widget import canvas as sw_canvas
        from .textitem import TextBlkItem

        if sw_canvas is None:
            return
        for item in sw_canvas.items():
            if isinstance(item, TextBlkItem):
                item.layout.setPunctuationPosition(pcfg.punctuation_position)
                item.repaint_background()
                item.update()

    def _on_preset_edited(self, config_key: str, editor: QLineEdit):
        raw = editor.text()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        values = []
        for p in parts:
            try:
                values.append(float(p))
            except ValueError:
                pass
        if values:
            setattr(pcfg, config_key, values)
            from utils.config import save_config

            save_config()
            self.presets_changed.emit()
        else:
            editor.setText(", ".join(str(v) for v in getattr(pcfg, config_key)))

    def on_intermediate_imgformat_changed(self):
        pcfg.intermediate_imgsave_ext = (
            "." + self.intermediate_imgformat_combobox.currentText().lower()
        )

    def on_edit_quality_changed(self, value: str):
        pcfg.imgsave_quality = int(value)

    def on_alignment_flag_changed(self):
        pcfg.let_alignment_flag = self.let_alignment_combox.currentIndex()

    def on_writing_mode_flag_changed(self):
        pcfg.let_writing_mode_flag = self.let_writing_mode_combox.currentIndex()

    def on_family_flag_changed(self):
        pcfg.let_family_flag = self.let_family_combox.currentIndex()

    def _on_nav_section_pressed(self, section_key: str):
        """Switch page stack to the section for the selected nav item."""
        widget = self._nav_section_to_widget.get(section_key)
        if widget is None:
            return
        idx = self._page_index.get(id(widget))
        if idx is not None:
            self.pageStack.setCurrentIndex(idx)

    def _nav_select(self, section_key: str):
        """Select the nav-tree section by key."""
        self.configTable.setCurrentSection(section_key)

    def _focus_on_dl_section(self, dl_key: str):
        """Navigate directly to the DL module page."""
        self._nav_select(dl_key)

    def focusOnTranslator(self):
        self._focus_on_dl_section("trans")

    def focusOnInpaint(self):
        self._focus_on_dl_section("inpaint")

    def focusOnDetect(self):
        self._focus_on_dl_section("detect")

    def focusOnOCR(self):
        self._focus_on_dl_section("ocr")

    def focusOnLLMProfile(self, profile_id: str = ""):
        """Navigate to the LLM Profile page."""
        self._nav_select("llm_profile")

    def _run_modal_dialog(self, dialog) -> int:
        """Run a child QDialog while disabling the backdrop's click-to-close
        so the config modal isn't dismissed by stray scrim clicks."""
        if self._modal_ref is not None:
            self._modal_ref.set_backdrop_closable(False)
        result = dialog.exec()
        if self._modal_ref is not None:
            self._modal_ref.set_backdrop_closable(True)
        return result

    def _open_network_settings(self):
        from ui.network_settings_dialog import NetworkSettingsDialog

        dialog = NetworkSettingsDialog(self)
        self._run_modal_dialog(dialog)

    def _open_shortcut_dialog(self):
        dialog = ShortcutDialog(self)
        self._run_modal_dialog(dialog)
        self.shortcuts_changed.emit()

    def _open_context_menu_config(self):
        from .context_menu_config import ContextMenuCustomizeDialog

        dialog = ContextMenuCustomizeDialog(self)
        self._run_modal_dialog(dialog)

    def _on_anim_mode_changed(self):
        idx = self.anim_combo.currentIndex()
        mapping = {0: 0, 1: 60, 2: 30, 3: -1}
        pcfg.animation_fps = mapping.get(idx, 0)

    def _on_text_rendering_changed(self):
        pcfg.text_rendering = self.text_rendering_combo.currentIndex()
        self.text_rendering_changed.emit()

    def _on_decorations_during_drag_changed(self, checked: bool):
        pcfg.show_decorations_during_drag = checked

    def _close_via_esc(self) -> None:
        """Esc → delegate to modal hide."""
        if self._modal_ref is not None:
            self._modal_ref.hide()

    def closeEvent(self, e) -> None:
        """Window close (Alt+F4 / system menu) → delegate to modal hide."""
        if self._modal_ref is not None:
            self._modal_ref.hide()
        e.accept()

    def hideEvent(self, e) -> None:
        self._removeOutsideClickFilter()
        self.save_config.emit()
        return super().hideEvent(e)

    # ── Outside-click auto-hide (via global eventFilter) ──────────────────

    def _installOutsideClickFilter(self) -> None:
        """Install a global event filter to intercept clicks outside the panel."""
        if self._outside_click_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._outside_click_filter_installed = True

    def _removeOutsideClickFilter(self) -> None:
        """Remove the global click-outside filter."""
        if not self._outside_click_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._outside_click_filter_installed = False

    def eventFilter(self, watched, event) -> bool:
        """Catch MouseButtonPress outside the panel to auto-hide."""
        if not self.isVisible() or not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            if (
                QApplication.activePopupWidget() is None
                and not self._widgetInsidePanel(watched)
                and not self._activeWidgetInWhitelist()
            ):
                if self._modal_ref is not None:
                    self._modal_ref.hide()
        return super().eventFilter(watched, event)

    def _widgetInsidePanel(self, widget) -> bool:
        """True if *widget* is the config panel itself or a descendant."""
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _activeWidgetInWhitelist(self) -> bool:
        """Check whether any currently-active widget is whitelisted."""
        return any(
            self._widgetInWhitelist(w)
            for w in (
                QApplication.activeWindow(),
                QApplication.activeModalWidget(),
                QApplication.focusWidget(),
            )
        )

    def _widgetInWhitelist(self, widget) -> bool:
        """Walk parent chain of *widget* looking for a whitelisted type."""
        while widget is not None:
            if self._isWhitelistedWidget(widget):
                return True
            window = widget.window()
            if window is not widget and self._isWhitelistedWidget(window):
                return True
            widget = widget.parentWidget()
        return False

    @staticmethod
    def _isWhitelistedWidget(widget) -> bool:
        return (
            isinstance(widget, QDialog)
            or widget.__class__.__name__
            in ConfigPanel.PRESERVE_ACTIVE_WIDGET_CLASS_NAMES
        )

    def setupConfig(self):
        self.blockSignals(True)

        if pcfg.open_recent_on_startup:
            self.open_on_startup_checker.setChecked(True)

        if pcfg.auto_clean_temp_projects:
            self.temp_clean_checker.setChecked(True)

        self.detect_config_panel.keep_existing_checker.setChecked(
            pcfg.module.keep_exist_textlines
        )
        self.let_fntsize_combox.setCurrentIndex(pcfg.let_fntsize_flag)
        self.let_fntstroke_combox.setCurrentIndex(pcfg.let_fntstroke_flag)
        self.let_alignment_combox.setCurrentIndex(pcfg.let_alignment_flag)
        self.let_family_combox.setCurrentIndex(pcfg.let_family_flag)
        self.let_writing_mode_combox.setCurrentIndex(pcfg.let_writing_mode_flag)
        self.let_autolayout_checker.setChecked(pcfg.let_autolayout_flag)
        self.let_uppercase_checker.setChecked(pcfg.let_uppercase_flag)
        self.let_textstyle_indep_checker.setChecked(pcfg.let_textstyle_indep_flag)
        self.rst_imgformat_combobox.setCurrentText(
            pcfg.imgsave_ext.replace(".", "").upper()
        )
        self.rst_autoformat_checker.setChecked(pcfg.imgsave_auto_format)
        self.rst_imgsave_sublock.setEnabled(not pcfg.imgsave_auto_format)
        self.intermediate_imgformat_combobox.setCurrentText(
            pcfg.intermediate_imgsave_ext.replace(".", "").upper()
        )
        # When auto-format is on, quality may apply if source is lossy
        if pcfg.imgsave_auto_format:
            self._set_quality_visual_state(True)
        else:
            # PNG is lossless — disable quality with "—" placeholder
            is_png = self.rst_imgformat_combobox.currentText() == "PNG"
            self._set_quality_visual_state(not is_png)
        self.load_model_checker.setChecked(pcfg.module.load_model_on_demand)
        self.empty_runcache_checker.setChecked(pcfg.module.empty_runcache)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.punctuation_position_combo.setCurrentIndex(pcfg.punctuation_position)
        self.tatechuyoko_slider.setValue(pcfg.tatechuyoko_threshold)

        self.fit_window_checker.setChecked(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setVisible(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setChecked(pcfg.fit_window_on_page_switch)

        anim_idx = {0: 0, 60: 1, 30: 2, -1: 3}.get(pcfg.animation_fps, 0)
        self.anim_combo.setCurrentIndex(anim_idx)

        self.text_rendering_combo.setCurrentIndex(pcfg.text_rendering)
        self.drag_decorations_checker.setChecked(pcfg.show_decorations_during_drag)

        self.blockSignals(False)
