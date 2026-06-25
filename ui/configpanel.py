from typing import List, Tuple, Union

from qtpy.QtCore import QEasingCurve, QElapsedTimer, QPoint, QSize, Qt, QTimer, Signal
from qtpy.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QGuiApplication,
    QIntValidator,
    QPainter,
    QValidator,
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QScrollBar,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

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
    NAVLIST_ITEM_FONTSIZE,
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


class PercentageLineEdit(QLineEdit):
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
        widget: Union[QWidget, QLayout],
        name: str = None,
        description: str = None,
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
        if name is not None:
            textlabel = ConfigTextLabel(
                name, CONFIG_FONTSIZE_CONTENT, QFont.Weight.Normal
            )
            self.name_label = textlabel
            layout.addWidget(textlabel)
        if description is not None:
            layout.addWidget(ConfigTextLabel(description, CONFIG_FONTSIZE_CONTENT - 2))
        if insert_stretch:
            layout.insertStretch(-1)
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
        self.widget = widget
        self.setContentsMargins(*content_margins)


def combobox_with_label(
    sel: List[str],
    name: str,
    description: str = None,
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
    name: str, description: str = None, target_block: QWidget = None
):
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
        le = QLineEdit()
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


class NavItemDelegate(QStyledItemDelegate):
    """Paints item backgrounds through QPainter so text
    rendering stays on the native DirectWrite path (no stylesheet override)."""

    _hover_bg: "QColor | None" = None
    _text_clr: "QColor | None" = None

    @classmethod
    def invalidate_colors(cls):
        cls._hover_bg = None
        cls._text_clr = None

    @classmethod
    def _ensure_colors(cls):
        if cls._hover_bg is not None:
            return
        from ui.misc import load_all_themes
        from utils.config import pcfg

        themes = load_all_themes()
        tn = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
        t = themes.get(tn, {})
        cls._hover_bg = QColor(t.get("@hoverBackgroundColor", "#cad7ed"))
        cls._text_clr = QColor(t.get("@textColor", "#5d5d5f"))

    def paint(self, painter, option, index):
        self._ensure_colors()

        painter.save()
        rect = option.rect

        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        # Background — solid fill matching original stylesheet
        if is_selected or is_hovered:
            painter.fillRect(rect, self._hover_bg)

        # Text — draw through native style so DirectWrite handles it
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            font = index.data(Qt.ItemDataRole.FontRole)
            if font is None or not isinstance(font, QFont):
                font = option.font
            painter.setFont(font)
            painter.setPen(self._text_clr)
            # Indent sub-items (selectable) more than headers (non-selectable)
            left_pad = 32 if (index.flags() & Qt.ItemFlag.ItemIsSelectable) else 16
            painter.drawText(
                rect.adjusted(left_pad, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                text,
            )

        painter.restore()

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        base.setHeight(max(base.height(), 34))
        return base


class NavList(QListWidget):
    """QListWidget with WinUI‑style animated accent indicator bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(NavItemDelegate(self))

        self._indicator_y = 0.0
        self._anim_from = 0.0
        self._anim_to = 0.0
        self._animating = False
        self._duration = 200
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()

        # Indicator is driven by currentRowChanged, which fires for BOTH user
        # clicks (Qt's internal selection-model path) and programmatic
        # setCurrentRow. Overriding setCurrentRow does not catch clicks.
        self.currentRowChanged.connect(self._on_row_changed)

    def _on_row_changed(self, new_row: int):
        """Animate the accent indicator to the newly selected row.

        Skips header/separator rows (non-selectable items) so the indicator
        never parks on a label. Uses the current indicator position as the
        animation start, so it slides from wherever it currently is.
        """
        if new_row < 0:
            return
        item = self.item(new_row)
        if item is None:
            return
        # Skip non-selectable items (group headers / separators).
        if not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        new_rect = self.visualItemRect(item)
        if not new_rect.isValid() or new_rect.height() <= 0:
            return
        target_y = new_rect.y() + new_rect.height() / 2.0
        # If the indicator has not been placed yet (lazy-init via paintEvent),
        # snap instead of animating from 0.0.
        from_y = self._indicator_y if self._indicator_y != 0.0 else target_y
        self._start_indicator_anim(from_y, target_y)

    def _start_indicator_anim(self, from_y: float, to_y: float):
        self._anim_from = from_y
        self._anim_to = to_y
        self._indicator_y = from_y

        if pcfg.animation_fps < 0:
            self._indicator_y = to_y
            self.viewport().update()
            return

        self._animating = True
        self._elapsed.start()
        self._anim_timer.start(_scroll_interval())

    def _tick(self):
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._duration, 1.0)
        eased = self._easing.valueForProgress(progress)
        self._indicator_y = self._anim_from + (self._anim_to - self._anim_from) * eased
        self.viewport().update()
        if progress >= 1.0:
            self._anim_timer.stop()
            self._animating = False
            self._indicator_y = self._anim_to
            self.viewport().update()

    def _sync_indicator(self):
        """Snap indicator to current item (no animation)."""
        row = self.currentRow()
        if row < 0:
            return
        item = self.item(row)
        if not item:
            return
        r = self.visualItemRect(item)
        if r.isValid() and r.height() > 0:
            self._indicator_y = r.y() + r.height() / 2.0

    def paintEvent(self, event):
        super().paintEvent(event)

        # Lazy‑init indicator position on first real paint
        if self._indicator_y == 0.0:
            self._sync_indicator()

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        from ui.misc import get_theme_color

        accent = get_theme_color(alpha=200)
        painter.setBrush(accent)
        painter.setPen(Qt.NoPen)

        indicator_h = 20
        y = int(round(self._indicator_y - indicator_h / 2.0))
        painter.drawRoundedRect(0, y, 3, indicator_h, 1, 1)


class AnimatedScrollBar(QScrollBar):
    """Vertical scrollbar with timer‑based handle brightness on hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOrientation(Qt.Orientation.Vertical)

        self._factor = 0.0  # 0 = normal, 1 = fully hovered
        self._factor_start = 0.0
        self._factor_end = 0.0
        self._animating = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()
        self._duration = 150
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)

        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._sync_style()

    # ── Hover events ────────────────────────────────────────────

    def enterEvent(self, event):
        self._start_anim(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._start_anim(0.0)
        super().leaveEvent(event)

    # ── Animation ───────────────────────────────────────────────

    def _start_anim(self, target: float):
        if pcfg.animation_fps < 0:
            self._factor = target
            self._sync_style()
            return
        self._factor_start = self._factor
        self._factor_end = target
        self._elapsed.start()
        if not self._animating:
            self._animating = True
            self._timer.start(_scroll_interval())

    def _tick(self):
        elapsed = self._elapsed.elapsed()
        progress = min(elapsed / self._duration, 1.0)
        eased = self._easing.valueForProgress(progress)
        self._factor = (
            self._factor_start + (self._factor_end - self._factor_start) * eased
        )
        self._sync_style()
        if progress >= 1.0:
            self._timer.stop()
            self._animating = False

    def _sync_style(self):
        f = self._factor
        r = int(102 + 51 * f)  # #666 → #999
        g = int(102 + 51 * f)
        b = int(102 + 51 * f)
        self.setStyleSheet(
            f"QScrollBar:vertical {{"
            f"  width: 8px; background: transparent; margin: 0; }}"
            f"QScrollBar::handle:vertical {{"
            f"  background: rgb({r},{g},{b});"
            f"  border-radius: 4px; min-height: 20px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{"
            f"  height: 0; }}"
        )


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

        self.setVerticalScrollBar(AnimatedScrollBar(self))

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
    "toggle_original_opacity": [],
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
    "toggle_original_opacity": "Toggle Original Opacity",
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
            "space_inpaint",
            "quick_symbol",
            "advanced_align",
        ],
    ),
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
        self.search_edit = QLineEdit()
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
        return [
            self.excluded_list.item(i).text() for i in range(self.excluded_list.count())
        ]


class MCPInfoDialog(QDialog):
    """Informational dialog about the MCP Server feature."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("MCP Server Setup"))
        self.setMinimumSize(520, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        title = QLabel(self.tr("MCP Server"))
        f = title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            self.tr(
                "MCP (Model Context Protocol) allows external AI agents such as Claude Code to read and edit BallonsTranslator project data directly through tool calls — no GUI needed."
            )
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # How to use
        steps_title = QLabel(self.tr("Quick start:"))
        sf = steps_title.font()
        sf.setBold(True)
        steps_title.setFont(sf)
        layout.addWidget(steps_title)

        steps = QLabel(
            self.tr(
                '1. Install:  pip install -e ".[mcp]"\n2. Add a config entry in .claude/settings.json\n3. Run Claude Code in the project directory\n4. Ask it to open your project and edit text blocks'
            )
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        # Doc link
        doc_hint = QLabel(
            self.tr("Full user guide available at docs/MCP用户指南.md")
        )
        doc_hint.setWordWrap(True)
        doc_hint.setStyleSheet("font-size: 12px;")
        layout.addWidget(doc_hint)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        open_guide_btn = QPushButton(self.tr("Open User Guide"))
        open_guide_btn.clicked.connect(self._open_guide)
        btn_row.addWidget(open_guide_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _open_guide(self):
        import webbrowser
        from pathlib import Path

        from utils.shared import PROGRAM_PATH

        guide = Path(PROGRAM_PATH) / "docs" / "MCP用户指南.md"
        if guide.exists():
            webbrowser.open(guide.as_uri())


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
    save_config = Signal()
    unload_models = Signal()
    reload_textstyle = Signal(bool)
    font_exclusion_changed = Signal()
    profiles_changed = Signal()
    shortcuts_changed = Signal()
    presets_changed = Signal()

    # Active instance used by _DeadBlock/_DeadLayout to find the page stack
    # during __init__ construction.
    _active_panel: "ConfigPanel | None" = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("ConfigPanel")
        ConfigPanel._active_panel = self
        self._modal_ref = None  # OverlayModal, injected by MainWindow

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
        label_interface = self.tr("Interface")

        # === Models group ===
        models_group = PanelGroupBox(self.tr("Models"))
        self.models_group = models_group
        models_group.setProperty("cfgPage", True)
        models_vlayout = models_group.contentLayout()
        models_vlayout.setContentsMargins(*GROUPBOX_CONTENT_MARGINS)
        models_vlayout.setSpacing(4)

        self.load_model_checker, cb_block = checkbox_with_label(
            self.tr("Load models on demand"),
            description=self.tr("Load models on demand to save memory."),
        )
        models_vlayout.addWidget(cb_block)

        self.empty_runcache_checker, cb_block2 = checkbox_with_label(
            self.tr("Empty cache after RUN"),
            description=self.tr("Empty cache after RUN to save memory."),
        )
        models_vlayout.addWidget(cb_block2)

        self.load_model_checker.stateChanged.connect(self.on_load_model_changed)
        self.empty_runcache_checker.stateChanged.connect(self.on_runcache_changed)

        unload_btn = QPushButton(self.tr("Unload All Models"))
        unload_btn.clicked.connect(self.unload_models)
        models_vlayout.addWidget(unload_btn)

        profiles_btn = QPushButton(self.tr("Manage API Profiles..."))
        profiles_btn.clicked.connect(self._open_profile_manager)
        models_vlayout.addWidget(profiles_btn)

        self.detect_config_panel = TextDetectConfigPanel(
            self.tr("Detector"), scrollWidget=self
        )
        detect_group, self.detect_sub_block = self._build_grouped_widget(
            label_text_det, self.detect_config_panel, object_name="GroupDetect"
        )
        self.detect_config_panel.keep_existing_checker.clicked.connect(
            self.on_keepline_clicked
        )

        self.ocr_config_panel = OCRConfigPanel(self.tr("OCR"), scrollWidget=self)
        ocr_group, self.ocr_sub_block = self._build_grouped_widget(
            label_text_ocr, self.ocr_config_panel, object_name="GroupOCR"
        )

        self.inpaint_config_panel = InpaintConfigPanel(
            self.tr("Inpainter"), scrollWidget=self
        )
        inpaint_group, self.inpaint_sub_block = self._build_grouped_widget(
            label_inpaint, self.inpaint_config_panel, object_name="GroupInpaint"
        )

        self.trans_config_panel = TranslatorConfigPanel(
            label_translator, scrollWidget=self
        )
        trans_group, self.trans_sub_block = self._build_grouped_widget(
            label_translator, self.trans_config_panel, object_name="GroupTranslate"
        )

        # === Combined DL Module pipeline page ===
        dl_container = QWidget()
        dl_layout = QVBoxLayout(dl_container)
        dl_layout.setContentsMargins(0, 0, 0, 0)
        dl_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        dl_layout.addWidget(models_group)
        dl_layout.addWidget(detect_group)
        dl_layout.addWidget(ocr_group)
        dl_layout.addWidget(inpaint_group)
        dl_layout.addWidget(trans_group)

        self._dl_combined_widget = dl_container
        self._add_page(dl_container)
        idx = self._page_index[id(dl_container)]
        self._dl_scroll_area = self.pageStack.widget(idx)

        self._dl_section_widgets = {
            "models": models_group,
            "detect": detect_group,
            "ocr": ocr_group,
            "inpaint": inpaint_group,
            "trans": trans_group,
        }

        # === General: Project (startup + save merged) ===
        project_widget = QWidget()
        project_layout = QVBoxLayout(project_widget)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setSpacing(0)

        # Startup
        self.open_on_startup_checker = QCheckBox(
            self.tr("Reopen last project on startup")
        )
        self.open_on_startup_checker.stateChanged.connect(
            self.on_open_onstartup_changed
        )
        project_layout.addWidget(self.open_on_startup_checker)

        # Output section label
        output_label = ConfigTextLabel(self.tr("Output"), CONFIG_FONTSIZE_CONTENT - 2)
        project_layout.addWidget(output_label)

        # JXL removed from options: pillow-jxl-plugin compatibility issues
        self.rst_imgformat_combobox, imsave_sublock = combobox_with_label(
            ["PNG", "JPG", "WEBP"], self.tr("Result image format"), parent=self
        )
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        project_layout.addWidget(imsave_sublock)

        self.rst_autoformat_checker, autoformat_sublock = checkbox_with_label(
            self.tr("Auto detect source format")
        )
        self.rst_autoformat_checker.stateChanged.connect(self.on_autoformat_changed)
        project_layout.addWidget(autoformat_sublock)

        self.rst_imgquality_edit = PercentageLineEdit("100")
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)

        quality_sublock = ConfigSubBlock(
            self.rst_imgquality_edit, self.tr("Quality"), vertical_layout=False
        )
        quality_sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        quality_sublock.layout().insertStretch(-1)
        imsave_sublock.layout().addWidget(quality_sublock)

        # JXL removed from options
        self.intermediate_imgformat_combobox, intermediate_imsave_sublock = (
            combobox_with_label(
                ["PNG"], self.tr("Intermediate image format"), parent=self
            )
        )
        self.intermediate_imgformat_combobox.activated.connect(
            self.on_intermediate_imgformat_changed
        )
        project_layout.addWidget(intermediate_imsave_sublock)

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
            edit = QLineEdit()
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

        # Compact container for font format delegation grid
        delegation_frame = QFrame()
        delegation_frame.setObjectName("CompactDelegationFrame")
        delegation_layout = QVBoxLayout(delegation_frame)
        delegation_layout.setContentsMargins(12, 8, 12, 8)
        delegation_layout.setSpacing(4)

        # Context label
        delegation_label = ConfigTextLabel(
            self.tr("Default font format (when not set per-textblock):"),
            CONFIG_FONTSIZE_CONTENT - 2,
        )
        delegation_layout.addWidget(delegation_label)

        global_fntfmt_widget = QWidget()
        global_fntfmt_layout = QGridLayout(global_fntfmt_widget)
        global_fntfmt_layout.setSpacing(0)
        global_fntfmt_widget.setContentsMargins(0, 0, 0, 0)

        b = ConfigSubBlock(global_fntfmt_widget)
        b.layout().setContentsMargins(0, 0, 0, 0)
        b.setContentsMargins(0, 0, 0, 0)
        delegation_layout.addWidget(b)

        DELEGATION_COMBO_WIDTH = 140

        self.let_fntsize_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Font Size"),
            parent=self,
            insert_stretch=True,
        )
        self.let_fntsize_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_fntsize_combox.activated.connect(self.on_fntsize_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 0, 0)

        self.let_fntstroke_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Stroke Size"),
            parent=self,
            insert_stretch=True,
        )
        self.let_fntstroke_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_fntstroke_combox.activated.connect(self.on_fntstroke_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 0, 1)

        self.let_fntcolor_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Font Color"),
            parent=self,
            insert_stretch=True,
        )
        self.let_fntcolor_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_fntcolor_combox.activated.connect(self.on_fontcolor_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 1, 0)
        self.let_fnt_scolor_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Stroke Color"),
            parent=self,
            insert_stretch=True,
        )
        self.let_fnt_scolor_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_fnt_scolor_combox.activated.connect(self.on_font_scolor_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 1, 1)

        self.let_effect_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Effect"),
            parent=self,
            insert_stretch=True,
        )
        self.let_effect_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_effect_combox.activated.connect(self.on_effect_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 2, 0)
        self.let_alignment_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Alignment"),
            parent=self,
            insert_stretch=True,
        )
        self.let_alignment_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_alignment_combox.activated.connect(self.on_alignment_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 2, 1)

        self.let_writing_mode_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Writing-mode"),
            parent=self,
            insert_stretch=True,
        )
        self.let_writing_mode_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_writing_mode_combox.activated.connect(
            self.on_writing_mode_flag_changed
        )
        global_fntfmt_layout.addWidget(sublock, 3, 0)
        self.let_family_combox, sublock = combobox_with_label(
            [self.tr("Keep existing"), self.tr("Always use global setting")],
            self.tr("Font Family"),
            parent=self,
            insert_stretch=True,
        )
        self.let_family_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_family_combox.activated.connect(self.on_family_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 3, 1)

        global_fntfmt_layout.addItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            ),
            0,
            2,
        )

        ts_layout.addWidget(delegation_frame)

        # Text formatting sub-label
        fmt_label = ConfigTextLabel(
            self.tr("Text formatting"), CONFIG_FONTSIZE_CONTENT - 2
        )
        ts_layout.addWidget(fmt_label)

        self.let_autolayout_checker, al_sublock = checkbox_with_label(
            self.tr("Auto layout"),
            description=self.tr(
                "Split translation into multi-lines according to the extracted balloon region."
            ),
        )
        self.let_autolayout_checker.stateChanged.connect(self.on_autolayout_changed)
        ts_layout.addWidget(al_sublock)

        self.let_uppercase_checker, uc_sublock = checkbox_with_label(
            self.tr("To uppercase")
        )
        self.let_uppercase_checker.stateChanged.connect(self.on_uppercase_changed)
        ts_layout.addWidget(uc_sublock)

        self.let_textstyle_indep_checker, ti_sublock = checkbox_with_label(
            self.tr("Independent text styles for each projects")
        )
        self.let_textstyle_indep_checker.stateChanged.connect(
            self.on_textstyle_indep_changed
        )
        ts_layout.addWidget(ti_sublock)

        # Punctuation Position
        self.punctuation_position_combo = QComboBox()
        self.punctuation_position_combo.addItems(
            [self.tr("Centered (Traditional Chinese / Japanese)"), self.tr("Edge-aligned (Simplified Chinese)")]
        )
        self.punctuation_position_combo.setCurrentIndex(pcfg.punctuation_position)
        self.punctuation_position_combo.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.punctuation_position_combo.currentIndexChanged.connect(
            self.on_punctuation_position_changed
        )
        punct_pos_sublock = ConfigSubBlock(
            self.punctuation_position_combo, self.tr("Punctuation Position")
        )
        ts_layout.addWidget(punct_pos_sublock)

        self.exclude_fonts_btn = QPushButton(self.tr("Exclude Fonts..."), parent=self)
        self.exclude_fonts_btn.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self.exclude_fonts_btn.clicked.connect(self.on_exclude_fonts_clicked)
        btn_sublock = ConfigSubBlock(self.exclude_fonts_btn)
        ts_layout.addWidget(btn_sublock)

        self.max_font_size_edit = QSpinBox()
        self.max_font_size_edit.setRange(10, 1000)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.max_font_size_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.max_font_size_edit.valueChanged.connect(self.on_max_font_size_changed)
        max_font_sublock = ConfigSubBlock(
            self.max_font_size_edit, self.tr("Max Font Size (px)")
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

        # Behavior sub-label
        behavior_label = ConfigTextLabel(
            self.tr("Behavior"), CONFIG_FONTSIZE_CONTENT - 2
        )
        interface_layout.addWidget(behavior_label)

        # Fit image to window on open
        self.fit_window_checker = QCheckBox(
            self.tr("Fit image to window when opening")
        )
        self.fit_window_checker.stateChanged.connect(self.on_fit_window_changed)
        interface_layout.addWidget(self.fit_window_checker)

        # Sub-option: also fit on page switch
        self.fit_window_page_checker = QCheckBox(
            self.tr("Also fit when switching pages")
        )
        self.fit_window_page_checker.stateChanged.connect(
            self.on_fit_window_page_changed
        )
        self.fit_window_page_checker.setVisible(False)
        interface_layout.addWidget(self.fit_window_page_checker)

        # Animation mode
        anim_row = QHBoxLayout()
        anim_row.setSpacing(6)
        anim_label = QLabel(self.tr("Animation"))
        anim_row.addWidget(anim_label)
        self.anim_combo = ConfigComboBox()
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
        anim_row.addWidget(self.anim_combo)
        anim_row.addStretch()
        interface_layout.addLayout(anim_row)

        # Shortcut button
        self.shortcut_btn = QPushButton(self.tr("Edit Shortcuts..."), parent=self)
        self.shortcut_btn.setFixedWidth(CONFIG_COMBOBOX_LONG + 32)
        self.shortcut_btn.clicked.connect(self._open_shortcut_dialog)
        interface_layout.addWidget(self.shortcut_btn)

        # Combo Box Presets (moved from Typesetting)
        preset_header = QLabel(self.tr("Combo Box Presets"))
        preset_header.setStyleSheet("font-weight: bold; padding: 12px 0 4px 24px;")
        interface_layout.addWidget(preset_header)

        # Helper label
        preset_hint = ConfigTextLabel(
            self.tr("Comma-separated values — used in font format panel dropdowns."),
            CONFIG_FONTSIZE_CONTENT - 3,
        )
        interface_layout.addWidget(preset_hint)

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

        # ── Original Opacity Toggle ──────────────────────────────────
        toggle_header = QLabel(self.tr("Original Opacity Toggle"))
        toggle_header.setStyleSheet("font-weight: bold; padding: 12px 0 4px 24px;")
        interface_layout.addWidget(toggle_header)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        toggle_lbl = QLabel(self.tr("Toggle Preset (%):"))
        toggle_lbl.setFixedWidth(110)
        toggle_row.addWidget(toggle_lbl)
        self.orig_opacity_toggle_spin = QSpinBox()
        self.orig_opacity_toggle_spin.setRange(0, 99)
        self.orig_opacity_toggle_spin.setValue(pcfg.original_transparency_preset)
        self.orig_opacity_toggle_spin.valueChanged.connect(
            lambda v: setattr(pcfg, "original_transparency_preset", v)
        )
        toggle_row.addWidget(self.orig_opacity_toggle_spin, 1)
        toggle_row.addStretch()
        togglesublock = ConfigSubBlock(toggle_row)
        interface_layout.addWidget(togglesublock)

        self.interface_block = generalConfigPanel.addGroupedBlock(
            label_interface, interface_widget, object_name="GroupGeneral"
        )

        # === Environment (network, deps, models, diagnostic) ===
        label_environment = self.tr("Environment")
        env_widget = QWidget()
        env_layout = QVBoxLayout(env_widget)
        env_layout.setContentsMargins(0, 0, 0, 0)
        env_layout.setSpacing(6)

        # Helper: add a button row (label + button) to env_layout
        def _env_button(text, slot):
            btn = QPushButton(text)
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            env_layout.addWidget(btn)
            return btn

        self.env_network_btn = _env_button(
            self.tr("Network & Mirror Settings..."),
            self._open_network_settings,
        )
        self.env_tools_btn = _env_button(
            self.tr("Tools..."),
            self._open_tools_dialog,
        )
        self.env_diag_btn = _env_button(
            self.tr("Run System Diagnostic..."),
            self._open_system_diagnostic,
        )
        self.env_mcp_btn = _env_button(
            self.tr("MCP Server Info..."),
            self._open_mcp_info,
        )

        env_layout.addStretch()
        self.env_block = generalConfigPanel.addGroupedBlock(
            label_environment, env_widget, object_name="GroupGeneral"
        )

        # === Navigation list (replaces horizontal nav bar) ===
        self.navList = NavList()
        self.navList.setFixedWidth(NAVLIST_WIDTH)
        self.navList.setSpacing(2)
        self.navList.setFrameShape(QListWidget.NoFrame)
        self.navList.setObjectName("ConfigNavList")

        # Build section list with group headers
        sections = [
            (self._dl_combined_widget, self.tr("Pipeline")),
            ("_sep", None),
            ("_header", self.tr("General")),
            (self.project_block.section_widget, label_project),
            (self.typesetting_block.section_widget, label_typesetting),
            (self.interface_block.section_widget, label_interface),
            (self.env_block.section_widget, label_environment),
        ]
        self._nav_items = []  # (widget_or_None, row)
        for target, text in sections:
            if target == "_header":
                item = QListWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                f = QFont()
                f.setPixelSize(NAVLIST_HEADER_FONTSIZE)
                f.setBold(True)
                item.setFont(f)
                self.navList.addItem(item)
                self._nav_items.append((None, self.navList.count() - 1))
            elif target == "_sep":
                item = QListWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setSizeHint(QSize(0, 16))
                self.navList.addItem(item)
                self._nav_items.append((None, self.navList.count() - 1))
            else:
                item = QListWidgetItem(text)
                f = QFont()
                f.setPixelSize(NAVLIST_ITEM_FONTSIZE)
                item.setFont(f)
                self.navList.addItem(item)
                self._nav_items.append((target, self.navList.count() - 1))

        self.navList.currentRowChanged.connect(self._on_nav_row_changed)

        # Select first selectable section by default (Models)
        for first_target, first_row in self._nav_items:
            if first_target is not None:
                self.navList.setCurrentRow(first_row)
                break

        # Layout: fixed horizontal layout with nav list | page stack
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(2)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.navList)
        main_layout.addWidget(self.pageStack, 1)

    def on_load_model_changed(self):
        pcfg.module.load_model_on_demand = self.load_model_checker.isChecked()

    def on_runcache_changed(self):
        pcfg.module.empty_runcache = self.empty_runcache_checker.isChecked()

    def on_fit_window_changed(self):
        checked = self.fit_window_checker.isChecked()
        pcfg.open_image_fit_window = checked
        self.fit_window_page_checker.setVisible(checked)

    def on_fit_window_page_changed(self):
        pcfg.fit_window_on_page_switch = self.fit_window_page_checker.isChecked()

    def on_keepline_clicked(self):
        pcfg.module.keep_exist_textlines = (
            self.detect_config_panel.keep_existing_checker.isChecked()
        )

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
        area.setVerticalScrollBar(AnimatedScrollBar(area))
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
        self, group_title, widget, object_name=None, name=None, description=None
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

        sublock = ConfigSubBlock(widget, name=name, description=description)
        group_vlayout.addWidget(sublock)

        idx = self._add_page(group)
        sublock.section_widget = group
        self.subblock_list = getattr(self, "subblock_list", [])
        # keep a reference list of sublocks for parity with legacy blocks
        if not hasattr(self, "_all_subblocks"):
            self._all_subblocks = []
        self._all_subblocks.append(sublock)
        return sublock

    def _build_grouped_widget(
        self, group_title, widget, object_name=None, name=None, description=None
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
        sublock = ConfigSubBlock(widget, name=name, description=description)
        group_vlayout.addWidget(sublock)
        sublock.section_widget = group
        if not hasattr(self, "_all_subblocks"):
            self._all_subblocks = []
        self._all_subblocks.append(sublock)
        return group, sublock

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
        if self._run_modal_dialog(dialog) == QDialog.DialogCode.Accepted:
            excluded = dialog.get_excluded_fonts()
            pcfg.excluded_fonts = excluded
            self.font_exclusion_changed.emit()
            from utils.config import save_config

            save_config()

    def on_rst_imgformat_changed(self):
        pcfg.imgsave_ext = "." + self.rst_imgformat_combobox.currentText().lower()

    def on_autoformat_changed(self):
        pcfg.imgsave_auto_format = self.rst_autoformat_checker.isChecked()
        self.rst_imgformat_combobox.setEnabled(not pcfg.imgsave_auto_format)

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
        """Switch page stack to the section for the selected nav row."""
        if row < 0 or row >= len(self._nav_items):
            return
        target, _ = self._nav_items[row]
        if target is None:
            return
        idx = self._page_index.get(id(target))
        if idx is not None:
            self.pageStack.setCurrentIndex(idx)

    def _nav_select(self, section_widget) -> int:
        """Select the nav-list row whose target matches *section_widget*.
        Returns the row index, or -1 if not found.
        """
        for idx, (target, _) in enumerate(self._nav_items):
            if target is section_widget:
                self.navList.setCurrentRow(idx)
                return idx
        return -1

    def _focus_on_dl_section(self, section_key: str):
        """Navigate to the combined DL pipeline page and scroll to a section."""
        self._nav_select(self._dl_combined_widget)
        widget = self._dl_section_widgets.get(section_key)
        if widget is not None:
            self._dl_scroll_area.ensureWidgetVisible(widget, 0, 100)

    def focusOnTranslator(self):
        self._focus_on_dl_section("trans")

    def focusOnInpaint(self):
        self._focus_on_dl_section("inpaint")

    def focusOnDetect(self):
        self._focus_on_dl_section("detect")

    def focusOnOCR(self):
        self._focus_on_dl_section("ocr")

    def _run_modal_dialog(self, dialog) -> int:
        """Run a child QDialog while disabling the backdrop's click-to-close
        so the config modal isn't dismissed by stray scrim clicks."""
        if self._modal_ref is not None:
            self._modal_ref.set_backdrop_closable(False)
        result = dialog.exec()
        if self._modal_ref is not None:
            self._modal_ref.set_backdrop_closable(True)
        return result

    def _open_profile_manager(self):
        from utils.profile_manager import (
            ProfileManagerDialog,
            load_profiles,
            save_all_profiles,
        )

        profiles = load_profiles()
        dialog = ProfileManagerDialog(self, profiles, on_changed=lambda: None)
        self._run_modal_dialog(dialog)
        save_all_profiles(profiles)
        self.profiles_changed.emit()

    def _open_network_settings(self):
        from ui.network_settings_dialog import NetworkSettingsDialog

        dialog = NetworkSettingsDialog(self)
        self._run_modal_dialog(dialog)

    def _open_tools_dialog(self):
        from ui.tools_dialog import ToolsDialog

        dialog = ToolsDialog(self)
        self._run_modal_dialog(dialog)

    def _open_system_diagnostic(self):
        from ui.system_diagnostic_dialog import SystemDiagnosticDialog

        dialog = SystemDiagnosticDialog(self)
        self._run_modal_dialog(dialog)

    def _open_mcp_info(self):
        dialog = MCPInfoDialog(self)
        self._run_modal_dialog(dialog)

    def _open_shortcut_dialog(self):
        dialog = ShortcutDialog(self)
        self._run_modal_dialog(dialog)
        self.shortcuts_changed.emit()

    def _on_anim_mode_changed(self):
        idx = self.anim_combo.currentIndex()
        mapping = {0: 0, 1: 60, 2: 30, 3: -1}
        pcfg.animation_fps = mapping.get(idx, 0)
        from utils.config import save_config

        save_config()

    def hideEvent(self, e) -> None:
        self.save_config.emit()
        return super().hideEvent(e)

    def setupConfig(self):
        self.blockSignals(True)

        if pcfg.open_recent_on_startup:
            self.open_on_startup_checker.setChecked(True)

        self.detect_config_panel.keep_existing_checker.setChecked(
            pcfg.module.keep_exist_textlines
        )
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
        self.rst_imgformat_combobox.setCurrentText(
            pcfg.imgsave_ext.replace(".", "").upper()
        )
        self.rst_autoformat_checker.setChecked(pcfg.imgsave_auto_format)
        self.rst_imgformat_combobox.setEnabled(not pcfg.imgsave_auto_format)
        self.intermediate_imgformat_combobox.setCurrentText(
            pcfg.intermediate_imgsave_ext.replace(".", "").upper()
        )
        self.rst_imgquality_edit.setText(str(pcfg.imgsave_quality))
        self.load_model_checker.setChecked(pcfg.module.load_model_on_demand)
        self.empty_runcache_checker.setChecked(pcfg.module.empty_runcache)
        self.max_font_size_edit.setValue(pcfg.max_font_size)
        self.punctuation_position_combo.setCurrentIndex(pcfg.punctuation_position)

        self.fit_window_checker.setChecked(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setVisible(pcfg.open_image_fit_window)
        self.fit_window_page_checker.setChecked(pcfg.fit_window_on_page_switch)

        anim_idx = {0: 0, 60: 1, 30: 2, -1: 3}.get(pcfg.animation_fps, 0)
        self.anim_combo.setCurrentIndex(anim_idx)

        self.blockSignals(False)
