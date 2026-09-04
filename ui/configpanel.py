import os.path as osp
from functools import partial
from typing import Dict, List, Tuple, Union

from qtpy.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QItemSelection,
    QPoint,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import (
    QFocusEvent,
    QFont,
    QGuiApplication,
    QIntValidator,
    QKeySequence,
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
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from utils.config import export_config, import_config, pcfg
from utils.message import create_error_dialog, create_info_dialog
from utils.shortcut_conflicts import find_conflict_keys
from utils.version import APP_VERSION
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

from .custom_widget import (
    ConfigCheckBox,
    ConfigComboBox,
    ConfigLineEdit,
    ConfigScrollBar,
    ConfigSectionHeader,
    NoArrowsSpinBox,
    PanelGroupBox,
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


def _make_note_btn(note_text: str) -> QPushButton:
    """Build a themed ``?`` button that pops up ``note_text`` on click.

    The note text is captured in the click closure so the popup never loses
    it (the old per-instance ``_note_text`` attribute was clobbered by
    ConfigSubBlock.__init__ when a subclass passed ``name=None``).
    """
    btn = QPushButton("?")
    btn.setFixedSize(20, 20)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    from ui.misc import get_theme_color

    c = get_theme_color()
    r, g, b = c.red(), c.green(), c.blue()
    btn.setStyleSheet(
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

    def _show_note_popup(checked: bool = False):
        popup = ConfigNotePopup(btn, note_text)
        popup.show()
        btn._note_popup = popup  # keep alive: popup has no parent

    btn.clicked.connect(_show_note_popup)
    return btn


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
            self._note_btn = _make_note_btn(note)
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

    # ── Disabled-state auto-styling ────────────────────────────────
    _disabled_color = None  # class-level cache

    def changeEvent(self, e: QEvent):
        # name_label only exists for instances built with a name; guard with
        # getattr so name-less sublocks (e.g. module pages) don't crash on
        # enabled-state changes.
        if (
            e.type() == QEvent.Type.EnabledChange
            and getattr(self, "name_label", None) is not None
        ):
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


class ConfigFormRow(ConfigSubBlock):
    """Compact label-control row for settings forms.

    Left side: a fixed-width, right-aligned label (with optional ? note button).
    Right side: the actual control widget.
    Use this to align controls vertically in a two-column layout.
    """

    def __init__(
        self,
        label: str,
        widget: Union[QWidget, QLayout],
        note: str = None,
        label_width: int = 110,
        parent: QWidget = None,
    ) -> None:
        row_widget = QWidget(parent)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self.name_label = QLabel(label)
        self.name_label.setFixedWidth(label_width)
        self.name_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row_layout.addWidget(self.name_label)

        if isinstance(widget, QWidget):
            row_layout.addWidget(widget)
        else:
            row_layout.addLayout(widget)

        if note is not None:
            self._note_btn = _make_note_btn(note)
            row_layout.addWidget(self._note_btn)
        else:
            self._note_btn = None

        row_layout.addStretch()

        # content_margins tuned for dense form pages
        super().__init__(
            row_widget,
            name=None,
            vertical_layout=False,
            content_margins=(16, 4, 16, 4),
        )
        self.layout().setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )


def _section_header(text: str) -> ConfigSectionHeader:
    """Compact section header for dense form pages."""
    header = ConfigSectionHeader(text)
    header.layout().setContentsMargins(16, 8, 16, 4)
    return header


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
    "delete_blks": ["Del"],
    "delete_blks_alt": ["Ctrl+D"],
    "select_all": ["Ctrl+A"],
    "strike": [],
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
    # Temporarily unbound — the previous "A" collided with prev_page (also "A").
    "ai_tool": [],
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

# Display names are translated at table definition (QCoreApplication.translate
# with an explicit context) so the strings are plain literals — i18n_check can
# extract them without the orphan whitelist.  Context names match the existing
# zh_CN.ts contexts; launch.py installs the translator before this module is
# imported.
_ACTION_NAMES = {
    "prev_page": QCoreApplication.translate("_ShortcutRow", "Page Up"),
    "next_page": QCoreApplication.translate("_ShortcutRow", "Page Down"),
    "prev_page_alt": QCoreApplication.translate("_ShortcutRow", "Page Up (alt)"),
    "next_page_alt": QCoreApplication.translate("_ShortcutRow", "Page Down (alt)"),
    "textedit_mode": QCoreApplication.translate("_ShortcutRow", "Text Editor"),
    "textblock_mode": QCoreApplication.translate("_ShortcutRow", "Text Block"),
    "drawboard_mode": QCoreApplication.translate("_ShortcutRow", "Draw Board"),
    "zoom_in": QCoreApplication.translate("_ShortcutRow", "Zoom In"),
    "zoom_out": QCoreApplication.translate("_ShortcutRow", "Zoom Out"),
    "delete_blks": QCoreApplication.translate("_ShortcutRow", "Delete"),
    "delete_blks_alt": QCoreApplication.translate("_ShortcutRow", "Delete (alt)"),
    "select_all": QCoreApplication.translate("_ShortcutRow", "Select All"),
    "strike": QCoreApplication.translate("_ShortcutRow", "Strike-through"),
    "italic": QCoreApplication.translate("_ShortcutRow", "Italic"),
    "underline": QCoreApplication.translate("_ShortcutRow", "Underline"),
    "undo": QCoreApplication.translate("_ShortcutRow", "Undo"),
    "redo": QCoreApplication.translate("_ShortcutRow", "Redo"),
    "page_search": QCoreApplication.translate("_ShortcutRow", "Page Search"),
    "global_search": QCoreApplication.translate("_ShortcutRow", "Global Search"),
    "escape": QCoreApplication.translate("_ShortcutRow", "Escape"),
    "space_inpaint": QCoreApplication.translate("_ShortcutRow", "Inpaint"),
    "hand_tool": QCoreApplication.translate("_ShortcutRow", "Hand Tool"),
    "rect_tool": QCoreApplication.translate("_ShortcutRow", "Rect Tool"),
    "inpaint_tool": QCoreApplication.translate("_ShortcutRow", "Inpaint Tool"),
    "ai_tool": QCoreApplication.translate("_ShortcutRow", "AI Inpaint"),
    "merge_tool": QCoreApplication.translate("_ShortcutRow", "Merge Tool"),
    "quick_symbol": QCoreApplication.translate("_ShortcutRow", "Quick Symbol"),
    "advanced_align": QCoreApplication.translate("_ShortcutRow", "Advanced Alignment"),
    "merge_blks": QCoreApplication.translate("_ShortcutRow", "Merge Text Blocks"),
    "toggle_original_opacity": QCoreApplication.translate("_ShortcutRow", "Toggle Original Compare"),
    "path_reorder": QCoreApplication.translate("_ShortcutRow", "Path Reorder"),
    "move_up": QCoreApplication.translate("_ShortcutRow", "Move Up"),
    "move_down": QCoreApplication.translate("_ShortcutRow", "Move Down"),
    "move_top": QCoreApplication.translate("_ShortcutRow", "Move to Top"),
    "move_bottom": QCoreApplication.translate("_ShortcutRow", "Move to Bottom"),
}

# Actions whose factory default resolves through a Qt StandardKey so macOS
# users get native Command-based bindings instead of literal Ctrl+... .
# Windows is unaffected — for this set, QKeySequence(StandardKey).toString(
# PortableText) equals the literal in DEFAULT_SHORTCUTS above, so behavior is
# byte-identical there (guarded by tests/test_shortcut_system.py).
_STANDARD_DEFAULT_KEYS = {
    "undo": QKeySequence.StandardKey.Undo,
    "redo": QKeySequence.StandardKey.Redo,
    "select_all": QKeySequence.StandardKey.SelectAll,
    "italic": QKeySequence.StandardKey.Italic,
    "underline": QKeySequence.StandardKey.Underline,
    "page_search": QKeySequence.StandardKey.Find,
    "delete_blks": QKeySequence.StandardKey.Delete,
    "zoom_in": QKeySequence.StandardKey.ZoomIn,
    "zoom_out": QKeySequence.StandardKey.ZoomOut,
}


def default_keys_for(action_id: str) -> List[str]:
    """Factory-default key sequences (PortableText) for ``action_id``.

    Standard-keyed actions resolve through Qt so macOS picks up Command-based
    bindings; the rest fall back to the literal ``DEFAULT_SHORTCUTS``.
    """
    std = _STANDARD_DEFAULT_KEYS.get(action_id)
    if std is not None:
        return [
            QKeySequence(std).toString(QKeySequence.SequenceFormat.PortableText)
        ]
    return list(DEFAULT_SHORTCUTS.get(action_id, []))


def native_key_display(seq: str) -> str:
    """Render a PortableText key sequence in the current platform's native form."""
    try:
        return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)
    except Exception:
        return seq


# Shortcut groups for organized display (titles translated at definition, see
# _ACTION_NAMES note above)
_SHORTCUT_GROUPS = [
    (
        QCoreApplication.translate("ShortcutEditor", "Navigation"),
        ["prev_page", "next_page", "prev_page_alt", "next_page_alt"],
    ),
    (
        QCoreApplication.translate("ShortcutEditor", "View"),
        ["zoom_in", "zoom_out", "toggle_original_opacity"],
    ),
    (
        QCoreApplication.translate("ShortcutEditor", "Edit"),
        [
            "textedit_mode",
            "textblock_mode",
            "drawboard_mode",
            "delete_blks",
            "delete_blks_alt",
            "select_all",
            "strike",
            "italic",
            "underline",
            "undo",
            "redo",
            "merge_blks",
        ],
    ),
    (
        QCoreApplication.translate("ShortcutEditor", "Tools"),
        [
            "hand_tool",
            "rect_tool",
            "inpaint_tool",
            "ai_tool",
            "merge_tool",
            "path_reorder",
            "space_inpaint",
            "quick_symbol",
            "advanced_align",
        ],
    ),
    (
        QCoreApplication.translate("ShortcutEditor", "Reorder"),
        ["move_up", "move_down", "move_top", "move_bottom"],
    ),
    (
        QCoreApplication.translate("ShortcutEditor", "Search"),
        ["page_search", "global_search"],
    ),
    (QCoreApplication.translate("ShortcutEditor", "General"), ["escape"]),
]


class _ShortcutRow(QWidget):
    """A row for editing shortcuts of a single action."""

    shortcut_changed = Signal()

    def __init__(self, action_id: str, parent=None):
        super().__init__(parent)
        self.action_id = action_id
        self._disabled_placeholder = None
        self._conflict_keys: set = set()

        from .theme_helpers import shortcut_styles

        s = shortcut_styles()

        h = QHBoxLayout(self)
        h.setContentsMargins(2, 6, 2, 6)
        h.setSpacing(6)

        # Action name — left column (already translated, see _ACTION_NAMES)
        name = QLabel(_ACTION_NAMES.get(action_id, action_id))
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
        self._add_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {s['add_bdr']}; border-radius: 3px; "
            f"color: {s['add_clr']}; background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ border-color: {s['add_hvr_bdr']}; color: {s['add_hvr_clr']}; }}"
        )
        self._add_btn.clicked.connect(self._add_shortcut)
        btn_layout.addWidget(self._add_btn)

        # Clear button
        self._clear_btn = QPushButton(self.tr("Del"))
        self._clear_btn.setFixedSize(28, 24)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: 3px; color: {s['btn_clr']}; "
            f"background: transparent; padding: 0px; }}"
            f"QPushButton:hover {{ color: {s['close_hvr']}; }}"
        )
        self._clear_btn.clicked.connect(self._clear)
        btn_layout.addWidget(self._clear_btn)

        # Reset button
        self._reset_btn = QPushButton(self.tr("Rst"))
        self._reset_btn.setFixedSize(28, 24)
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
            return [k for k in keys if isinstance(k, str) and k]
        return default_keys_for(self.action_id)

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
                # Conflict keys (bound to another action) render in red
                is_conflict = k in self._conflict_keys
                pill_bg = s["conflict_pill_bg"] if is_conflict else s["pill_bg"]
                pill_text = s["conflict_pill_text"] if is_conflict else s["pill_text"]
                lbl = QLabel(native_key_display(k))
                lbl.setStyleSheet(
                    f"color: {pill_text}; background: transparent; border: none;"
                )
                fl.addWidget(lbl)
                close_btn = QPushButton("x")
                close_btn.setFixedSize(22, 22)
                close_btn.setStyleSheet(
                    f"QPushButton {{ border: none; border-radius: 2px; color: {s['close_clr']}; "
                    f"background: transparent; padding: 0px; }}"
                    f"QPushButton:hover {{ color: {s['close_hvr']}; "
                    f"background: {s['close_hvr_bg']}; }}"
                )
                close_btn.clicked.connect(partial(self._remove_shortcut, k))
                fl.addWidget(close_btn)
                frame.setStyleSheet(
                    f"QFrame {{ background: {pill_bg}; border-radius: 4px; }}"
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
        edit.editingFinished.connect(self._on_add_sequence_finished)

    def _on_add_sequence_finished(self):
        edit = self.sender()
        if not isinstance(edit, QKeySequenceEdit):
            return
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
        defaults = default_keys_for(self.action_id)
        if defaults:
            pcfg.shortcuts[self.action_id] = list(defaults)
        elif self.action_id in pcfg.shortcuts:
            del pcfg.shortcuts[self.action_id]
        self._rebuild_pills()
        self.shortcut_changed.emit()

    def refresh(self, conflict_keys=None):
        self._conflict_keys = conflict_keys or set()
        self._rebuild_pills()

    def effective_keys(self) -> list:
        """Current effective keys (user override or default)."""
        return self._get_keys()


class ShortcutEditor(QWidget):
    """Grouped shortcut rows. Flat widget — the settings page provides
    scrolling via _wrap_page (same pattern as ProfileManagerWidget)."""

    shortcut_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Build grouped layout
        from .theme_helpers import shortcut_styles

        s = shortcut_styles()

        for group_name, action_ids in _SHORTCUT_GROUPS:
            group_box = PanelGroupBox(group_name)
            group_layout = group_box.contentLayout()

            for idx, action_id in enumerate(action_ids):
                if idx > 0:
                    sep = QWidget()
                    sep.setFixedHeight(1)
                    sep.setStyleSheet(f"background: {s['add_bdr']};")
                    group_layout.addWidget(sep)
                row = _ShortcutRow(action_id)
                row.shortcut_changed.connect(self._on_row_changed)
                self._rows[action_id] = row
                group_layout.addWidget(row)

            layout.addWidget(group_box)
            layout.addSpacing(6)

        layout.addStretch()

        self.refresh()

    def _on_row_changed(self):
        """Recompute conflicts and re-render all rows, then forward signal."""
        self.refresh()
        self.shortcut_changed.emit()

    def _compute_conflicts(self) -> set:
        """Return the set of key sequences bound to more than one action."""
        return find_conflict_keys({
            row.action_id: row.effective_keys() for row in self._rows.values()
        })

    def refresh(self):
        conflicts = self._compute_conflicts()
        for row in self._rows.values():
            row.refresh(conflicts)


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

        # Fork-private one-shot simplify button
        self.simplify_btn = QPushButton(self.tr("Simplify Font List"))
        self.simplify_btn.setObjectName("ConfigButton")
        self.simplify_btn.setToolTip(
            self.tr("Hide duplicate weight/language variants of the same font")
        )
        self.simplify_btn.clicked.connect(self._on_simplify)
        layout.addWidget(self.simplify_btn)

        # OK / Cancel buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # 「一键精简」当前对话框状态：{隐藏家族名: 规范名}，确定时写回
        # pcfg。必须从 pcfg 播种——否则重开对话框看不到已精简条目，
        # 点 OK 会把 pcfg.simplified_font_map 覆盖成空 dict 丢盘
        from utils.config import pcfg as _pcfg

        self._simplify_map: Dict[str, str] = dict(_pcfg.simplified_font_map)
        # Populate lists
        self._populate_lists()

    def _add_font_item(
        self, list_widget: QListWidget, font_name: str, simplified: bool = False
    ):
        """Add a font name to a list widget.

        Legacy fonts skip the typeface preview and get a "[Legacy]" suffix.
        Simplified entries get a "(Simplified)" suffix. The original font
        name is stored in ``Qt.UserRole``.
        """
        from qtpy.QtCore import Qt

        from utils.shared import LEGACY_FONTS

        is_legacy = font_name in LEGACY_FONTS
        display = font_name
        if simplified:
            display += f"（{self.tr('Simplified')}）"
        elif is_legacy:
            display = f"{font_name} [{self.tr('Legacy')}]"
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

        available = shared.get_filtered_font_list(pcfg.excluded_fonts)
        for font in available:
            if font not in self._simplify_map:
                self._add_font_item(self.available_list, font)

        # 精简条目与手动排除同住 excluded_fonts（同一条落盘路径，
        # 复刻「老旧字体」按钮的持久化方式）；标记映射只负责
        # 「已精简」后缀与恢复跟踪
        for font in pcfg.excluded_fonts:
            self._add_font_item(
                self.excluded_list, font, simplified=font in self._simplify_map
            )

        # 对话框内新精简的名字在确定前还没写进 excluded_fonts，补进隐藏列表
        hidden = {
            self._real_name(self.excluded_list.item(i))
            for i in range(self.excluded_list.count())
        }
        for font in sorted(self._simplify_map):
            if font not in hidden:
                self._add_font_item(self.excluded_list, font, simplified=True)

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
            name = self._real_name(item)
            self.excluded_list.takeItem(self.excluded_list.row(item))
            self._add_font_item(self.available_list, name)
            # 移回可用列表 = 撤销该条的「已精简」隐藏
            self._simplify_map.pop(name, None)

    def _on_simplify(self):
        """按本项目规则把字重/语言变体一次性加入隐藏列表。

        结果在对话框内生效并标记「已精简」，确定时才写回 pcfg；
        选中标记条目点 "<" 可随时恢复。
        """
        from utils import font_scan

        mapping = font_scan.compute_simplify_map()
        # 已在隐藏列表的（手动排除或上一轮精简）不再重复标记
        hidden = {
            self._real_name(self.excluded_list.item(i))
            for i in range(self.excluded_list.count())
        }
        mapping = {a: c for a, c in mapping.items() if a not in hidden}
        if not mapping:
            QMessageBox.information(
                self,
                self.tr("Simplify Font List"),
                self.tr("No simplifiable font entries detected."),
            )
            return

        names = sorted(mapping)
        preview = "\n".join(names[:15])
        if len(names) > 15:
            preview += "\n..."
        answer = QMessageBox.question(
            self,
            self.tr("Simplify Font List"),
            self.tr(
                "Detected {count} duplicate entries (weight/language variants):\n\n{fonts}\n\nHide them all? You can move them back later."
            )
            .replace("{count}", str(len(names)))
            .replace("{fonts}", preview),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._simplify_map.update(mapping)
        self._populate_lists()

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
        """隐藏列表全量返回：精简条目与手动排除同走 excluded_fonts 落盘。"""
        return [
            self._real_name(self.excluded_list.item(i))
            for i in range(self.excluded_list.count())
        ]

    def get_simplify_map(self) -> Dict[str, str]:
        """「一键精简」标记映射（含本对话框内被移回撤销的扣除）。"""
        return dict(self._simplify_map)


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
    clip_overflow_changed = Signal()
    apply_auto_tate_chu_yoko_requested = Signal()
    check_update = Signal()
    check_commit_update = Signal()

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
        dlConfigPanel = _DeadBlock(self.tr("DL Module"))  # noqa: F841
        generalConfigPanel = _DeadBlock(self.tr("General"))

        label_text_det = self.tr("Text Detection")
        label_text_ocr = self.tr("OCR")
        label_inpaint = self.tr("Inpaint")
        label_translator = self.tr("Translator")
        label_project = self.tr("Project")
        label_typesetting = self.tr("Typesetting")
        label_interface = self.tr("Interface")
        label_shortcuts = self.tr("Shortcuts")

        # === Models group ===
        models_group = PanelGroupBox(self.tr("Models"))
        self.models_group = models_group
        models_group.setProperty("cfgPage", True)
        models_group.setObjectName("GroupModels")
        models_vlayout = models_group.contentLayout()
        models_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        models_vlayout.setSpacing(8)

        # -- Model Loading section --
        models_vlayout.addWidget(_section_header(self.tr("Model Loading")))

        self.load_model_checker = ConfigCheckBox(
            self.tr("Load models on demand to save memory.")
        )
        self.load_model_checker.stateChanged.connect(self.on_load_model_changed)
        models_vlayout.addWidget(
            ConfigFormRow(
                "",
                self.load_model_checker,
                note=self.tr("<p>When enabled, models are loaded only on <b>first use</b> instead of at startup. Reduces initial memory and launch time. Recommended for systems with limited GPU memory.</p>"),
            )
        )

        self.empty_runcache_checker = ConfigCheckBox(
            self.tr("Empty cache after RUN to save memory.")
        )
        self.empty_runcache_checker.stateChanged.connect(self.on_runcache_changed)
        models_vlayout.addWidget(
            ConfigFormRow(
                "",
                self.empty_runcache_checker,
                note=self.tr("<p>Clears intermediate inference data after each pipeline run. Frees <b>GPU/CPU memory</b> between runs. Useful when working with large projects or limited hardware.</p>"),
            )
        )

        # -- Management section --
        models_vlayout.addWidget(_section_header(self.tr("Management")))

        unload_btn = QPushButton(self.tr("Unload All Models"))
        unload_btn.setObjectName("ConfigButton")
        unload_btn.clicked.connect(self.unload_models)
        models_vlayout.addWidget(
            ConfigFormRow(
                self.tr("Unload models"),
                unload_btn,
                note=self.tr("<p>Immediately releases all loaded models from memory. Use this to free <b>GPU/CPU resources</b> without restarting the application.</p>"),
            )
        )

        network_btn = QPushButton(self.tr("Network & Mirror Settings..."))
        network_btn.setObjectName("ConfigButton")
        network_btn.clicked.connect(self._open_network_settings)
        models_vlayout.addWidget(
            ConfigFormRow(
                self.tr("Network"),
                network_btn,
                note=self.tr("<p>Configure network proxies, mirror servers, and download sources. Useful for systems behind <b>firewalls</b> or in restricted environments.</p>"),
            )
        )

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
        project_layout.addWidget(_section_header(self.tr("Startup")))
        project_layout.addWidget(
            ConfigFormRow(
                "",
                self.open_on_startup_checker,
                note=self.tr("<p>Reopen the last project automatically when the application starts. Saves time when continuing work on the same project.</p>"),
            )
        )

        # Output
        project_layout.addWidget(_section_header(self.tr("Output")))

        self.rst_imgformat_combobox = ConfigComboBox(scrollWidget=self)
        self.rst_imgformat_combobox.addItems(["PNG", "JPG", "WEBP", "JXL"])
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        self.rst_imgsave_sublock = ConfigFormRow(
            self.tr("Result image format"),
            self.rst_imgformat_combobox,
            note=self.tr("<p>Choose the output format for translated images:</p><p><b>PNG</b> — lossless quality<br/><b>JPG / WEBP</b> — smaller files, some quality loss<br/><b>JXL</b> — high compression efficiency with lossless option</p>"),
        )
        project_layout.addWidget(self.rst_imgsave_sublock)

        self.rst_imgquality_edit = PercentageLineEdit("100")
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)
        self.rst_quality_sublock = ConfigFormRow(
            self.tr("Quality"),
            self.rst_imgquality_edit,
            note=self.tr("<p>Output image quality (<code>0-100</code>). Higher values give better quality but larger file sizes. Applies to <b>JPG</b> and <b>WEBP</b> only. Also used when <b>Auto detect source format</b> matches a lossy source.</p>"),
        )
        project_layout.addWidget(self.rst_quality_sublock)

        self.rst_autoformat_checker = ConfigCheckBox(
            self.tr("Auto detect source format")
        )
        self.rst_autoformat_checker.stateChanged.connect(self.on_autoformat_changed)
        project_layout.addWidget(
            ConfigFormRow(
                "",
                self.rst_autoformat_checker,
                note=self.tr("<p>When enabled, the output format automatically matches the <b>source image format</b>. Overrides the format selected above. If the source uses a lossy format (<b>JPG</b>, <b>WEBP</b>), the <b>Quality</b> setting above is used.</p>"),
            )
        )

        self.intermediate_imgformat_combobox = ConfigComboBox(scrollWidget=self)
        self.intermediate_imgformat_combobox.addItems(["PNG", "JXL"])
        self.intermediate_imgformat_combobox.activated.connect(
            self.on_intermediate_imgformat_changed
        )
        project_layout.addWidget(
            ConfigFormRow(
                self.tr("Intermediate image format"),
                self.intermediate_imgformat_combobox,
                note=self.tr("<p>Format used for intermediate processing data:</p><p><b>PNG</b> — default lossless option<br/><b>JXL</b> — better compression for mask and inpainted images</p>"),
            )
        )

        # Temporary Projects
        project_layout.addWidget(_section_header(self.tr("Temporary Projects")))

        self.temp_clean_checker = ConfigCheckBox(
            self.tr("Clean up imported image projects on exit")
        )
        self.temp_clean_checker.stateChanged.connect(
            self.on_auto_clean_temp_changed
        )
        project_layout.addWidget(
            ConfigFormRow(
                "",
                self.temp_clean_checker,
                note=self.tr(
                    "<p>When enabled, projects created by importing individual images (via drag-drop or <b>Open Image…</b>) will be <b>automatically deleted</b> when the application closes.</p><p>Use <b>Save Project As…</b> to keep a project permanently.</p>"
                ),
            )
        )

        self.project_block = generalConfigPanel.addGroupedBlock(
            label_project, project_widget, object_name="GroupGeneral"
        )

        dec_program_str = self.tr("decide by program")
        use_global_str = self.tr("use global setting")

        self._preset_editors = {}

        def _make_preset_row(label: str, config_key: str, target_layout: QVBoxLayout):
            """Build a compact form row for a preset list."""
            edit = ConfigLineEdit()
            edit.setText(", ".join(str(v) for v in getattr(pcfg, config_key)))
            edit.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
            edit.setPlaceholderText(self.tr("comma-separated values"))
            target_layout.addWidget(
                ConfigFormRow(label, edit, label_width=110)
            )
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
        ts_layout.addWidget(_section_header(self.tr("Default Font Format")))

        delegation_frame = QFrame()
        delegation_frame.setObjectName("CompactDelegationFrame")
        delegation_layout = QVBoxLayout(delegation_frame)
        delegation_layout.setContentsMargins(16, 8, 16, 8)
        delegation_layout.setSpacing(8)

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

        ts_layout.addWidget(ConfigSubBlock(delegation_frame, content_margins=(16, 4, 16, 4)))

        # Fonts section — font-management items (dropdown filtering and the
        # render-time size clamp); kept out of the vertical-typography group.
        ts_layout.addWidget(_section_header(self.tr("Fonts")))

        self.exclude_fonts_btn = QPushButton(self.tr("Exclude Fonts..."), parent=self)
        self.exclude_fonts_btn.setObjectName("ConfigButton")
        # Match the max-font-size spinbox width below for a uniform column
        self.exclude_fonts_btn.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.exclude_fonts_btn.clicked.connect(self.on_exclude_fonts_clicked)
        ts_layout.addWidget(
            ConfigFormRow(
                self.tr("Font Exclusion"),
                self.exclude_fonts_btn,
                note=self.tr("<p>Hide selected fonts from all font selection dropdowns. Useful for filtering out <b>unusable or decorative</b> fonts.</p>"),
            )
        )

        self.max_font_size_edit = NoArrowsSpinBox()
        self.max_font_size_edit.setRange(10, 1000)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.max_font_size_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.max_font_size_edit.valueChanged.connect(self.on_max_font_size_changed)
        ts_layout.addWidget(
            ConfigFormRow(
                self.tr("Max Font Size (px)"),
                self.max_font_size_edit,
                note=self.tr("<p>Maximum allowed font size in pixels. Text that would render larger than this limit is <b>scaled down</b> automatically.</p>"),
            )
        )

        # Text formatting section
        ts_layout.addWidget(_section_header(self.tr("Text formatting")))

        self.let_uppercase_checker = ConfigCheckBox(self.tr("To uppercase"))
        self.let_uppercase_checker.stateChanged.connect(self.on_uppercase_changed)
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.let_uppercase_checker,
                note=self.tr("<p>Convert all translated text to uppercase. Useful for certain <b>typographic styles</b> or all-caps conventions.</p>"),
            )
        )

        self.auto_squeeze_checker = ConfigCheckBox(
            self.tr("Shrink text blocks after running")
        )
        self.auto_squeeze_checker.stateChanged.connect(
            self.on_auto_squeeze_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.auto_squeeze_checker,
                note=self.tr("<p>After a run, shrink each text block to fit its translated text. Disable to keep the blocks exactly as you placed them — useful when you want to run background cleanup (inpaint) before translating, since empty blocks would otherwise collapse to a tiny sliver.</p>"),
            )
        )

        self.stroke_auto_follow_checker = ConfigCheckBox(
            self.tr("Stroke color follows text color")
        )
        self.stroke_auto_follow_checker.stateChanged.connect(
            self.on_stroke_auto_follow_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.stroke_auto_follow_checker,
                note=self.tr("<p>When a text block's stroke color is not manually set, automatically use the <b>inverse</b> of its font color (black text gets white stroke, white text gets black stroke). Disable to keep each block's stored stroke color and stop it from following the font color.</p>"),
            )
        )

        self.let_textstyle_indep_checker = ConfigCheckBox(
            self.tr("Independent text styles for each projects")
        )
        self.let_textstyle_indep_checker.stateChanged.connect(
            self.on_textstyle_indep_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.let_textstyle_indep_checker,
                note=self.tr("<p>When enabled, each project maintains its own <b>text style settings</b> independently instead of using shared global styles.</p>"),
            )
        )

        # Text Format Presets section — values offered in the font format panel dropdowns
        ts_layout.addWidget(_section_header(self.tr("Text Format Presets")))

        preset_hint = ConfigTextLabel(
            self.tr("Comma-separated values — used in font format panel dropdowns."),
            CONFIG_FONTSIZE_CONTENT - 3,
        )
        preset_hint.setContentsMargins(16, 0, 16, 4)
        ts_layout.addWidget(preset_hint)

        _make_preset_row(self.tr("Font Size:"), "font_size_presets", ts_layout)
        _make_preset_row(
            self.tr("Line Spacing:"), "line_spacing_presets", ts_layout
        )
        _make_preset_row(
            self.tr("Letter Spacing:"), "letter_spacing_presets", ts_layout
        )
        _make_preset_row(
            self.tr("Stroke Width:"), "stroke_width_presets", ts_layout
        )
        _make_preset_row(self.tr("Opacity:"), "opacity_presets", ts_layout)

        # Quick insert characters — feeds the Quick Symbol palette's custom
        # section (opened with the quick-symbol shortcut while editing text).
        self.quick_insert_characters_edit = ConfigLineEdit()
        self.quick_insert_characters_edit.setText(pcfg.quick_insert_characters)
        self.quick_insert_characters_edit.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.quick_insert_characters_edit.textChanged.connect(
            self.on_quick_insert_characters_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                self.tr("Quick insert characters"),
                self.quick_insert_characters_edit,
                note=self.tr("<p>Characters offered in the <b>Quick Symbol</b> palette's custom section. Insert them with the quick-symbol shortcut while editing text.</p>"),
            )
        )

        # Vertical Text section — vertical-only typography controls
        ts_layout.addWidget(_section_header(self.tr("Vertical Text")))

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
        ts_layout.addWidget(
            ConfigFormRow(
                self.tr("Punctuation Position"),
                self.punctuation_position_combo,
                note=self.tr("<p>Choose punctuation alignment:</p><p><b>Centered</b> — traditional CJK style (Traditional Chinese / Japanese)<br/><b>Edge-aligned</b> — modern style (Simplified Chinese)</p>"),
            )
        )

        # Compact punctuation spacing in vertical text
        self.compact_punctuation_checker = ConfigCheckBox(
            self.tr("Compact punctuation spacing")
        )
        self.compact_punctuation_checker.setChecked(
            pcfg.compact_vertical_punctuation_spacing
        )
        self.compact_punctuation_checker.toggled.connect(
            self.on_compact_punctuation_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.compact_punctuation_checker,
                note=self.tr("<p>Remove extra spacing around punctuation in <b>vertical</b> text.</p>"),
            )
        )

        # Half-width Japanese corner brackets (「」『』) in vertical mode
        self.halfwidth_corner_bracket_checker = ConfigCheckBox(
            self.tr("Compact 「」『』 in vertical text (half-width style)")
        )
        self.halfwidth_corner_bracket_checker.setChecked(
            pcfg.halfwidth_jp_corner_brackets
        )
        self.halfwidth_corner_bracket_checker.stateChanged.connect(
            self.on_halfwidth_corner_bracket_changed
        )
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                self.halfwidth_corner_bracket_checker,
                note=self.tr("<p>When enabled, 「」『』 <b>corner brackets</b> in <b>vertical</b> text use half-width compact layout, matching narrow half-width punctuation width instead of full-width CJK character width. Use the sub-option below to also apply the effect to <b>horizontal</b> text.</p>"),
            )
        )

        # Sub-option: also apply in horizontal text
        self.halfwidth_horizontal_checker = ConfigCheckBox(
            self.tr("Also apply in horizontal text")
        )
        self.halfwidth_horizontal_checker.setChecked(
            pcfg.halfwidth_jp_corner_brackets_horizontal
        )
        self.halfwidth_horizontal_checker.setEnabled(
            pcfg.halfwidth_jp_corner_brackets
        )
        self.halfwidth_horizontal_checker.setVisible(
            pcfg.halfwidth_jp_corner_brackets
        )
        self.halfwidth_horizontal_checker.stateChanged.connect(
            self.on_halfwidth_corner_bracket_horizontal_changed
        )
        halfwidth_horizontal_wrapper = QWidget()
        # Indent the sub-option under the parent's control column
        # (16 margin + 110 label + 8 spacing = 134) plus a 24px hierarchy
        # indent so it reads as a child row. Margins must go on the layout,
        # not the widget, or they are lost before the layout exists.
        hw_layout = QHBoxLayout(halfwidth_horizontal_wrapper)
        hw_layout.setContentsMargins(158, 0, 0, 4)
        hw_layout.addWidget(self.halfwidth_horizontal_checker)
        hw_layout.addStretch()
        self._halfwidth_horizontal_sublock = halfwidth_horizontal_wrapper
        halfwidth_horizontal_wrapper.setVisible(
            pcfg.halfwidth_jp_corner_brackets
        )
        ts_layout.addWidget(halfwidth_horizontal_wrapper)

        # Automatic Tate-chu-yoko (pipeline formatting pass)
        self.auto_tate_chu_yoko_checker = ConfigCheckBox(
            self.tr("Automatic Tate-chu-yoko")
        )
        self.auto_tate_chu_yoko_checker.setChecked(
            pcfg.auto_tate_chu_yoko.enabled
        )
        self.auto_tate_chu_yoko_checker.toggled.connect(
            self.on_auto_tate_chu_yoko_changed
        )
        self.auto_tate_chu_yoko_apply_btn = QPushButton(
            self.tr("Apply"), parent=self
        )
        self.auto_tate_chu_yoko_apply_btn.setObjectName("ConfigButton")
        self.auto_tate_chu_yoko_apply_btn.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.auto_tate_chu_yoko_apply_btn.clicked.connect(
            self.on_apply_auto_tate_chu_yoko_clicked
        )
        auto_tcy_row = QWidget()
        auto_tcy_row_layout = QHBoxLayout(auto_tcy_row)
        auto_tcy_row_layout.setContentsMargins(0, 0, 0, 0)
        auto_tcy_row_layout.setSpacing(8)
        auto_tcy_row_layout.addWidget(self.auto_tate_chu_yoko_checker)
        auto_tcy_row_layout.addWidget(self.auto_tate_chu_yoko_apply_btn)
        auto_tcy_row_layout.addStretch()
        ts_layout.addWidget(
            ConfigFormRow(
                "",
                auto_tcy_row,
                note=self.tr("<p>Automatically combine matching character runs into one upright horizontal unit in <b>vertical</b> text. Applied to translated results after each run, or to the whole project via <b>Apply</b>.</p>"),
            )
        )

        # Sub-options: what may participate in an automatic run
        self.auto_tate_chu_yoko_max_length = NoArrowsSpinBox()
        self.auto_tate_chu_yoko_max_length.setRange(1, 99)
        self.auto_tate_chu_yoko_max_length.setValue(
            pcfg.auto_tate_chu_yoko.max_length
        )
        self.auto_tate_chu_yoko_max_length.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.auto_tate_chu_yoko_max_length.valueChanged.connect(
            self.on_auto_tate_chu_yoko_max_length_changed
        )
        self.auto_tate_chu_yoko_numbers = ConfigCheckBox(
            self.tr("Numbers")
        )
        self.auto_tate_chu_yoko_numbers.setChecked(
            pcfg.auto_tate_chu_yoko.include_numbers
        )
        self.auto_tate_chu_yoko_numbers.toggled.connect(
            self.on_auto_tate_chu_yoko_numbers_changed
        )
        self.auto_tate_chu_yoko_letters = ConfigCheckBox(
            self.tr("Letters")
        )
        self.auto_tate_chu_yoko_letters.setChecked(
            pcfg.auto_tate_chu_yoko.include_letters
        )
        self.auto_tate_chu_yoko_letters.toggled.connect(
            self.on_auto_tate_chu_yoko_letters_changed
        )
        self.auto_tate_chu_yoko_additional_chars = ConfigLineEdit()
        self.auto_tate_chu_yoko_additional_chars.setText(
            pcfg.auto_tate_chu_yoko.additional_chars
        )
        self.auto_tate_chu_yoko_additional_chars.setFixedWidth(
            CONFIG_COMBOBOX_SHORT
        )
        self.auto_tate_chu_yoko_additional_chars.textChanged.connect(
            self.on_auto_tate_chu_yoko_additional_chars_changed
        )

        auto_tcy_category_row = QWidget()
        auto_tcy_category_layout = QHBoxLayout(auto_tcy_category_row)
        auto_tcy_category_layout.setContentsMargins(0, 0, 0, 0)
        auto_tcy_category_layout.setSpacing(16)
        auto_tcy_category_layout.addWidget(self.auto_tate_chu_yoko_numbers)
        auto_tcy_category_layout.addWidget(self.auto_tate_chu_yoko_letters)
        auto_tcy_category_layout.addStretch()

        self.auto_tcy_options_widget = QWidget()
        auto_tcy_options_layout = QVBoxLayout(self.auto_tcy_options_widget)
        auto_tcy_options_layout.setContentsMargins(24, 0, 0, 0)
        auto_tcy_options_layout.setSpacing(4)
        auto_tcy_options_layout.addWidget(
            ConfigFormRow(
                self.tr("Maximum Run Length"),
                self.auto_tate_chu_yoko_max_length,
            )
        )
        auto_tcy_options_layout.addWidget(
            ConfigFormRow(
                self.tr("Character Sets"),
                auto_tcy_category_row,
            )
        )
        auto_tcy_options_layout.addWidget(
            ConfigFormRow(
                self.tr("Additional Characters"),
                self.auto_tate_chu_yoko_additional_chars,
            )
        )
        self.auto_tcy_options_widget.setVisible(
            pcfg.auto_tate_chu_yoko.enabled
        )
        self.auto_tate_chu_yoko_apply_btn.setVisible(
            pcfg.auto_tate_chu_yoko.enabled
        )
        ts_layout.addWidget(self.auto_tcy_options_widget)

        self.typesetting_block = generalConfigPanel.addGroupedBlock(
            label_typesetting, ts_widget, object_name="GroupGeneral"
        )

        # === Save controls moved into Project group above ===

        # === General: Interface (canvas behavior + appearance) ===
        interface_widget = QWidget()
        interface_layout = QVBoxLayout(interface_widget)
        interface_layout.setContentsMargins(0, 0, 0, 0)
        interface_layout.setSpacing(0)

        # Appearance section — UI animation smoothness
        interface_layout.addWidget(_section_header(self.tr("Appearance")))

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
        interface_layout.addWidget(
            ConfigFormRow(
                self.tr("Animation"),
                self.anim_combo,
                note=self.tr("<p>Controls UI transition smoothness:</p><p><b>Auto</b> — matches display refresh rate<br/><b>Specific FPS</b> — cap GPU usage<br/><b>Off</b> — disables all animations</p>"),
            )
        )

        # Canvas section — canvas viewing & editing behaviors
        interface_layout.addWidget(_section_header(self.tr("Canvas")))

        self.fit_window_checker = ConfigCheckBox(
            self.tr("Fit image to window when opening")
        )
        self.fit_window_checker.stateChanged.connect(self.on_fit_window_changed)
        interface_layout.addWidget(
            ConfigFormRow(
                "",
                self.fit_window_checker,
                note=self.tr("<p>Automatically scale the image to fit the window when opening a project. Avoids <b>manual zooming</b> on every file open.</p>"),
            )
        )

        # Sub-option: also fit on page switch
        self.fit_window_page_checker = ConfigCheckBox(
            self.tr("Also fit when switching pages")
        )
        self.fit_window_page_checker.stateChanged.connect(
            self.on_fit_window_page_changed
        )
        self.fit_window_page_checker.setVisible(False)
        self._fit_page_sublock = ConfigFormRow("", self.fit_window_page_checker)
        self._fit_page_sublock.setVisible(False)
        # Same 24px hierarchy indent as the half-width bracket sub-option
        fit_sublock_wrapper = QWidget()
        fsl_layout = QVBoxLayout(fit_sublock_wrapper)
        fsl_layout.setContentsMargins(24, 0, 0, 0)
        fsl_layout.addWidget(self._fit_page_sublock)
        self._fit_page_sublock_wrapper = fit_sublock_wrapper
        interface_layout.addWidget(fit_sublock_wrapper)

        self.seq_badge_checker = ConfigCheckBox(
            self.tr("Sequence Badge")
        )
        self.seq_badge_checker.setChecked(pcfg.show_seq_badge)
        self.seq_badge_checker.stateChanged.connect(self.on_seq_badge_changed)
        interface_layout.addWidget(
            ConfigFormRow(
                "",
                self.seq_badge_checker,
                note=self.tr("<p>Displays the block <b>sequence number</b> at the top-left corner of each text block on the canvas. Disable to avoid occlusion when working with small fonts.</p>"),
            )
        )

        self.clip_overflow_checker = ConfigCheckBox(
            self.tr("Overflow Clip")
        )
        self.clip_overflow_checker.setChecked(pcfg.clip_text_overflow)
        self.clip_overflow_checker.stateChanged.connect(self.on_clip_overflow_changed)
        interface_layout.addWidget(
            ConfigFormRow(
                "",
                self.clip_overflow_checker,
                note=self.tr("<p>When translation text exceeds the block boundary, <b>clip it</b> instead of enlarging the block. A <b>yellow border</b> indicates clipping. Drag a corner handle to resize and un-clip.</p>"),
            )
        )

        self.drag_decorations_checker = ConfigCheckBox(
            self.tr("Show decorations while resizing")
        )
        self.drag_decorations_checker.setChecked(pcfg.show_decorations_during_drag)
        self.drag_decorations_checker.toggled.connect(
            self._on_decorations_during_drag_changed
        )
        interface_layout.addWidget(
            ConfigFormRow(
                "",
                self.drag_decorations_checker,
                note=self.tr("<p>When checked, <b>text stroke and shadow</b> remain visible while dragging or resizing a text block. Uncheck for maximum frame rate during resize.</p>"),
            )
        )

        # ── Original Compare ───────────────────────────────────
        interface_layout.addWidget(_section_header(self.tr("Original Compare")))
        self.orig_opacity_toggle_spin = NoArrowsSpinBox()
        self.orig_opacity_toggle_spin.setRange(0, 99)
        self.orig_opacity_toggle_spin.setValue(pcfg.original_transparency_preset)
        self.orig_opacity_toggle_spin.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.orig_opacity_toggle_spin.valueChanged.connect(
            lambda v: setattr(pcfg, "original_transparency_preset", v)
        )
        interface_layout.addWidget(
            ConfigFormRow(
                self.tr("Original Compare Preset"),
                self.orig_opacity_toggle_spin,
                note=self.tr("<p>Background opacity level when using the <b>Original Compare</b> shortcut. Lower values show more of the original image beneath the translation.</p>"),
            )
        )

        self.interface_block = generalConfigPanel.addGroupedBlock(
            label_interface, interface_widget, object_name="GroupGeneral"
        )

        # === General: Shortcuts ===
        # Flat page (page stack provides the scroll area), same pattern as
        # ProfileManagerWidget. Edits save and re-bind shortcuts live.
        self.shortcuts_editor = ShortcutEditor()
        self.shortcuts_editor.shortcut_changed.connect(self._on_shortcuts_edited)
        self._add_page(self.shortcuts_editor)

        # === General: Quick Menus ===
        # Multi-menu drag-config editor; edits save pcfg.pie_menus live.
        # Page will host both pie (ring) and vertical-list styles.
        from .pie_menu_editor import PieMenuEditor
        self.quick_menus_editor = PieMenuEditor()
        self._add_page(self.quick_menus_editor)

        # === App (Updates + Config Import/Export) ===
        label_app = self.tr("App")
        config_mgmt_widget = QWidget()
        config_mgmt_layout = QVBoxLayout(config_mgmt_widget)
        config_mgmt_layout.setContentsMargins(0, 0, 0, 0)
        config_mgmt_layout.setSpacing(0)

        # Updates section (moved from Project)
        config_mgmt_layout.addWidget(_section_header(self.tr("Updates")))

        self.check_update_on_startup_checker = ConfigCheckBox(
            self.tr("Check update on startup")
        )
        self.check_update_on_startup_checker.stateChanged.connect(
            self.on_check_update_onstartup_changed
        )
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                "",
                self.check_update_on_startup_checker,
                note=self.tr(
                    "<p>Automatically check for a newer release when the application starts. You will only be notified when a new version is available.</p>"
                ),
            )
        )

        # Update status row: Check update button + current/latest version labels
        update_status_widget = QWidget()
        update_status_widget.setObjectName("ConfigInlineRow")
        update_status_layout = QHBoxLayout(update_status_widget)
        update_status_layout.setContentsMargins(0, 0, 0, 0)
        update_status_layout.setSpacing(12)
        update_status_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.check_update_btn = QPushButton(self.tr("Check update"))
        self.check_update_btn.setObjectName("ConfigButton")
        self.check_update_btn.clicked.connect(self.check_update)
        update_status_layout.addWidget(self.check_update_btn)

        self.current_version_label = ConfigTextLabel(
            self.tr("Current version: ") + APP_VERSION,
            CONFIG_FONTSIZE_CONTENT,
        )
        update_status_layout.addWidget(self.current_version_label)

        self.latest_version_label = ConfigTextLabel(
            self.tr("Latest version: ") + self.tr("Not checked"),
            CONFIG_FONTSIZE_CONTENT,
        )
        update_status_layout.addWidget(self.latest_version_label)
        update_status_layout.addStretch()
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                self.tr("Update status:"),
                update_status_widget,
                note=self.tr("<p>Manually trigger a version check and view the current and latest release numbers.</p>"),
            )
        )

        # Commit-based update check (developer channel) with risk notice
        self.check_commit_btn = QPushButton(self.tr("Check commit updates"))
        self.check_commit_btn.setObjectName("ConfigButton")
        self.check_commit_btn.clicked.connect(self.check_commit_update)
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                self.tr("Developer channel:"),
                self.check_commit_btn,
                note=self.tr("<p>Check for the latest commit (unverified developer changes). Not guaranteed to work on every device.</p>"),
            )
        )

        commit_risk = QLabel(
            "⚠ "
            + self.tr(
                "Updates from the latest commit are unverified developer changes and are not guaranteed to work on every device."
            )
        )
        commit_risk.setWordWrap(True)
        from ui.misc import get_theme_color

        commit_risk.setStyleSheet(
            f"color: {get_theme_color(key='@warningColor').name()}; font-size: 12px;"
        )
        risk_wrapper = QWidget()
        # Align with the control column of ConfigFormRow
        risk_wrapper.setContentsMargins(134, 0, 16, 4)
        risk_layout = QHBoxLayout(risk_wrapper)
        risk_layout.setContentsMargins(0, 0, 0, 0)
        risk_layout.addWidget(commit_risk)
        risk_layout.addStretch()
        config_mgmt_layout.addWidget(risk_wrapper)

        # Export section
        config_mgmt_layout.addWidget(_section_header(self.tr("Export Config")))

        self.export_exclude_keys = ConfigCheckBox(
            self.tr("Exclude API keys when exporting")
        )
        self.export_exclude_keys.setChecked(True)
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                "",
                self.export_exclude_keys,
                note=self.tr(
                    "<p>API profiles will be exported without <b>api_key</b> and <b>proxy</b> fields. Structure and all other settings remain intact. Uncheck to include credentials (not recommended for sharing).</p>"
                ),
            )
        )

        export_btn = QPushButton(self.tr("Export Config..."))
        export_btn.setObjectName("ConfigButton")
        export_btn.clicked.connect(self.on_export_config)
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                self.tr("Export file:"),
                export_btn,
                note=self.tr(
                    "<p>Save current settings to a <b>.json</b> file. Useful for backups or transferring configurations between machines.</p>"
                ),
            )
        )

        # Import section
        config_mgmt_layout.addWidget(_section_header(self.tr("Import Config")))

        import_btn = QPushButton(self.tr("Import Config..."))
        import_btn.setObjectName("ConfigButton")
        import_btn.clicked.connect(self.on_import_config)
        config_mgmt_layout.addWidget(
            ConfigFormRow(
                self.tr("Import file:"),
                import_btn,
                note=self.tr(
                    "<p>Load settings from a previously exported <b>.json</b> file. A compatibility summary will be shown before applying.</p>"
                ),
            )
        )

        self.config_mgmt_block = generalConfigPanel.addGroupedBlock(
            label_app, config_mgmt_widget, object_name="GroupGeneral"
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
        self.configTable.addSection(general_header, label_interface, "interface", self.interface_block.section_widget)
        self.configTable.addSection(general_header, label_shortcuts, "shortcuts", self.shortcuts_editor)
        label_quick_menus = self.tr("Quick Menus")
        self.configTable.addSection(
            general_header, label_quick_menus, "quick_menus", self.quick_menus_editor
        )
        self.configTable.addSection(
            general_header, label_app, "config_mgmt",
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
            "interface": self.interface_block.section_widget,
            "shortcuts": self.shortcuts_editor,
            "quick_menus": self.quick_menus_editor,
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
        self.fit_window_page_checker.setEnabled(checked)
        self._fit_page_sublock.setVisible(checked)
        self._fit_page_sublock_wrapper.setVisible(checked)

    def on_fit_window_page_changed(self):
        pcfg.fit_window_on_page_switch = self.fit_window_page_checker.isChecked()

    def on_seq_badge_changed(self):
        pcfg.show_seq_badge = self.seq_badge_checker.isChecked()
        self.seq_badge_changed.emit()

    def on_clip_overflow_changed(self):
        pcfg.clip_text_overflow = self.clip_overflow_checker.isChecked()
        self.clip_overflow_changed.emit()

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

        if note is not None:
            # Group title gets a ? note button. ConfigSubBlock would drop the
            # note silently when name is None, so place it beside the title.
            title_row = QWidget(group)
            title_row_layout = QHBoxLayout(title_row)
            title_row_layout.setContentsMargins(0, 0, 0, 0)
            title_row_layout.setSpacing(6)
            title_row_layout.addWidget(group.title_label)
            title_row_layout.addWidget(_make_note_btn(note))
            title_row_layout.addStretch()
            group.layout().insertWidget(0, title_row)

        group_vlayout = group.contentLayout()
        group_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        group_vlayout.setSpacing(0)

        sublock = ConfigSubBlock(widget, name=name, description=description, note=note)
        group_vlayout.addWidget(sublock)

        # Hide the panel-internal module_label — PanelGroupBox title already
        # provides the same heading, avoiding visual redundancy.
        if hasattr(widget, 'module_label') and widget.module_label is not None:
            widget.module_label.hide()

        self._add_page(group)  # registers page in stack
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

    def on_check_update_onstartup_changed(self):
        pcfg.check_update_on_startup = (
            self.check_update_on_startup_checker.isChecked()
        )

    def setLatestVersion(self, version: str):
        self.latest_version_label.setText(self.tr("Latest version: ") + version)

    def setUpdateChecking(self, checking: bool):
        self.check_update_btn.setEnabled(not checking)

    def on_fntsize_flag_changed(self):
        pcfg.let_fntsize_flag = self.let_fntsize_combox.currentIndex()

    def on_fntstroke_flag_changed(self):
        pcfg.let_fntstroke_flag = self.let_fntstroke_combox.currentIndex()

    def on_uppercase_changed(self):
        pcfg.let_uppercase_flag = self.let_uppercase_checker.isChecked()

    def on_auto_squeeze_changed(self):
        pcfg.auto_squeeze_after_run = self.auto_squeeze_checker.isChecked()

    def on_stroke_auto_follow_changed(self):
        pcfg.stroke_auto_follow = self.stroke_auto_follow_checker.isChecked()

    def on_textstyle_indep_changed(self):
        pcfg.let_textstyle_indep_flag = self.let_textstyle_indep_checker.isChecked()
        self.reload_textstyle.emit(pcfg.let_textstyle_indep_flag)

    def on_exclude_fonts_clicked(self):
        dialog = FontExcludeDialog(self)
        if self._run_modal_dialog(dialog) == QDialog.DialogCode.Accepted:
            excluded = dialog.get_excluded_fonts()
            pcfg.excluded_fonts = excluded
            pcfg.simplified_font_map = dialog.get_simplify_map()
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

    def _apply_punctuation_settings(self):
        """Apply punctuation_position to ALL existing text items."""
        from .shared_widget import canvas as sw_canvas
        from .textitem import TextBlkItem

        if sw_canvas is None:
            return
        for item in sw_canvas.items():
            if isinstance(item, TextBlkItem):
                layout = item.layout
                if layout is not None and hasattr(layout, "setPunctuationPosition"):
                    layout.setPunctuationPosition(pcfg.punctuation_position)
                item.repaint_background()
                item.update()

    def on_compact_punctuation_changed(self, enabled: bool):
        pcfg.compact_vertical_punctuation_spacing = enabled
        self._apply_compact_punctuation_settings()

    def _apply_compact_punctuation_settings(self):
        """Apply compact_vertical_punctuation_spacing to ALL existing items."""
        from .shared_widget import canvas as sw_canvas
        from .textitem import TextBlkItem

        if sw_canvas is None:
            return
        for item in sw_canvas.items():
            if isinstance(item, TextBlkItem):
                # The engine layout reads the pcfg key per layoutBlock pass.
                layout = item.layout
                if layout is not None and hasattr(layout, "reLayout"):
                    layout.reLayout()
                item.repaint_background()
                item.update()

    def on_auto_tate_chu_yoko_changed(self, enabled: bool):
        pcfg.auto_tate_chu_yoko.enabled = enabled
        self.auto_tcy_options_widget.setVisible(enabled)
        self.auto_tate_chu_yoko_apply_btn.setVisible(enabled)

    def on_apply_auto_tate_chu_yoko_clicked(self):
        self.apply_auto_tate_chu_yoko_requested.emit()

    def on_auto_tate_chu_yoko_max_length_changed(self, value: int):
        pcfg.auto_tate_chu_yoko.max_length = value

    def on_auto_tate_chu_yoko_numbers_changed(self, checked: bool):
        pcfg.auto_tate_chu_yoko.include_numbers = checked

    def on_auto_tate_chu_yoko_letters_changed(self, checked: bool):
        pcfg.auto_tate_chu_yoko.include_letters = checked

    def on_auto_tate_chu_yoko_additional_chars_changed(self, text: str):
        pcfg.auto_tate_chu_yoko.additional_chars = text

    def on_quick_insert_characters_changed(self, text: str):
        pcfg.quick_insert_characters = text

    def on_halfwidth_corner_bracket_changed(self, state: int):
        pcfg.halfwidth_jp_corner_brackets = bool(state)
        # Hide and disable the horizontal sub-option when the parent is off
        self.halfwidth_horizontal_checker.setEnabled(bool(state))
        self.halfwidth_horizontal_checker.setVisible(bool(state))
        self._halfwidth_horizontal_sublock.setVisible(bool(state))
        self._apply_halfwidth_corner_bracket_settings()

    def on_halfwidth_corner_bracket_horizontal_changed(self, state: int):
        pcfg.halfwidth_jp_corner_brackets_horizontal = bool(state)
        self._apply_halfwidth_corner_bracket_settings()

    def _apply_halfwidth_corner_bracket_settings(self):
        """Apply halfwidth_jp_corner_brackets to ALL existing text items."""
        from .shared_widget import canvas as sw_canvas
        from .textitem import TextBlkItem

        if sw_canvas is None:
            return
        for item in sw_canvas.items():
            if isinstance(item, TextBlkItem):
                layout = item.layout
                if layout is not None and hasattr(layout, "halfwidth_jp_corner_brackets"):
                    layout.halfwidth_jp_corner_brackets = pcfg.halfwidth_jp_corner_brackets
                    layout.reLayout()
                # Horizontal half-width: apply/restore text substitution in document
                if pcfg.halfwidth_jp_corner_brackets and pcfg.halfwidth_jp_corner_brackets_horizontal:
                    item.apply_horizontal_halfwidth_corner_brackets()
                else:
                    item.restore_horizontal_halfwidth_corner_brackets()
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

    def _on_shortcuts_edited(self):
        """A shortcut row changed: persist and re-bind shortcuts live
        (previously only refreshed when the dialog closed)."""
        from utils.config import save_config

        save_config()
        self.shortcuts_changed.emit()

    def _on_anim_mode_changed(self):
        idx = self.anim_combo.currentIndex()
        mapping = {0: 0, 1: 60, 2: 30, 3: -1}
        pcfg.animation_fps = mapping.get(idx, 0)

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

        if pcfg.check_update_on_startup:
            self.check_update_on_startup_checker.setChecked(True)

        self.detect_config_panel.keep_existing_checker.setChecked(
            pcfg.module.keep_exist_textlines
        )
        self.let_fntsize_combox.setCurrentIndex(pcfg.let_fntsize_flag)
        self.let_fntstroke_combox.setCurrentIndex(pcfg.let_fntstroke_flag)
        self.let_alignment_combox.setCurrentIndex(pcfg.let_alignment_flag)
        self.let_family_combox.setCurrentIndex(pcfg.let_family_flag)
        self.let_writing_mode_combox.setCurrentIndex(pcfg.let_writing_mode_flag)
        self.let_uppercase_checker.setChecked(pcfg.let_uppercase_flag)
        self.auto_squeeze_checker.setChecked(pcfg.auto_squeeze_after_run)
        self.stroke_auto_follow_checker.setChecked(pcfg.stroke_auto_follow)
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
        self.compact_punctuation_checker.setChecked(
            pcfg.compact_vertical_punctuation_spacing
        )
        self.auto_tate_chu_yoko_checker.setChecked(
            pcfg.auto_tate_chu_yoko.enabled
        )
        self.auto_tate_chu_yoko_max_length.setValue(
            pcfg.auto_tate_chu_yoko.max_length
        )
        self.auto_tate_chu_yoko_numbers.setChecked(
            pcfg.auto_tate_chu_yoko.include_numbers
        )
        self.auto_tate_chu_yoko_letters.setChecked(
            pcfg.auto_tate_chu_yoko.include_letters
        )
        self.auto_tate_chu_yoko_additional_chars.setText(
            pcfg.auto_tate_chu_yoko.additional_chars
        )
        self.auto_tcy_options_widget.setVisible(
            pcfg.auto_tate_chu_yoko.enabled
        )
        self.auto_tate_chu_yoko_apply_btn.setVisible(
            pcfg.auto_tate_chu_yoko.enabled
        )
        self.quick_insert_characters_edit.setText(pcfg.quick_insert_characters)
        self.halfwidth_corner_bracket_checker.setChecked(
            pcfg.halfwidth_jp_corner_brackets
        )
        self.halfwidth_horizontal_checker.setChecked(
            pcfg.halfwidth_jp_corner_brackets_horizontal
        )
        self.halfwidth_horizontal_checker.setEnabled(
            pcfg.halfwidth_jp_corner_brackets
        )

        self.fit_window_checker.setChecked(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setVisible(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setChecked(pcfg.fit_window_on_page_switch)

        anim_idx = {0: 0, 60: 1, 30: 2, -1: 3}.get(pcfg.animation_fps, 0)
        self.anim_combo.setCurrentIndex(anim_idx)

        self.blockSignals(False)
