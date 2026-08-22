import copy

from qtpy.QtCore import QSignalBlocker, Qt, Signal
from qtpy.QtGui import (
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from utils import config as C
from utils import shared
from utils.fontformat import FontFormat, LineSpacingType, fix_fontweight_qt

from . import funcmaps as FM
from .custom_widget import (
    AlignmentChecker,
    ColorPickerLabel,
    FlowLayout,
    QFontChecker,
    SizeComboBox,
    SizeControlLabel,
    ViewWidget,
    Widget,
)
from .text_advanced_format import TextStyleEntryButton
from .text_style_presets import TextStylePresetPanel
from .text_engine.annotations import (
    DEFAULT_EMPHASIS_POSITION,
    EMPHASIS_GLYPHS,
    EMPHASIS_POSITIONS,
    EMPHASIS_STYLES,
    LIGATURE_AXIS_VALUES,
    LIGATURE_COMMON,
    LIGATURE_CONTEXTUAL,
    LIGATURE_DEFAULT,
    LIGATURE_DISCRETIONARY,
    OLDSTYLE_NUMS,
    RubyValidationError,
)
from .text_engine.transforms.editor import TextTransformEditSession
from .text_engine.transforms.panel import TextTransformPanel
from .textitem import TextBlkItem


class AlignmentBtnGroup(QFrame):
    param_changed = Signal(str, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.alignLeftChecker = AlignmentChecker(self)
        self.alignLeftChecker.clicked.connect(self.alignBtnPressed)
        self.alignCenterChecker = AlignmentChecker(self)
        self.alignCenterChecker.clicked.connect(self.alignBtnPressed)
        self.alignRightChecker = AlignmentChecker(self)
        self.alignRightChecker.clicked.connect(self.alignBtnPressed)
        self.alignLeftChecker.setObjectName("AlignLeftChecker")
        self.alignRightChecker.setObjectName("AlignRightChecker")
        self.alignCenterChecker.setObjectName("AlignCenterChecker")

        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.alignLeftChecker)
        hlayout.addWidget(self.alignCenterChecker)
        hlayout.addWidget(self.alignRightChecker)
        hlayout.setSpacing(0)
        hlayout.setContentsMargins(0, 0, 0, 0)

    def alignBtnPressed(self):
        btn = self.sender()
        if btn == self.alignLeftChecker:
            self.alignLeftChecker.setChecked(True)
            self.alignCenterChecker.setChecked(False)
            self.alignRightChecker.setChecked(False)
            self.param_changed.emit("alignment", 0)
        elif btn == self.alignRightChecker:
            self.alignRightChecker.setChecked(True)
            self.alignCenterChecker.setChecked(False)
            self.alignLeftChecker.setChecked(False)
            self.param_changed.emit("alignment", 2)
        else:
            self.alignCenterChecker.setChecked(True)
            self.alignLeftChecker.setChecked(False)
            self.alignRightChecker.setChecked(False)
            self.param_changed.emit("alignment", 1)

    def setAlignment(self, alignment: int):
        if alignment == 0:
            self.alignLeftChecker.setChecked(True)
            self.alignCenterChecker.setChecked(False)
            self.alignRightChecker.setChecked(False)
        elif alignment == 1:
            self.alignLeftChecker.setChecked(False)
            self.alignCenterChecker.setChecked(True)
            self.alignRightChecker.setChecked(False)
        else:
            self.alignLeftChecker.setChecked(False)
            self.alignCenterChecker.setChecked(False)
            self.alignRightChecker.setChecked(True)


class EmphasisToolButton(QToolButton):
    """Toggle emphasis and pick its CSS-compatible mark and position.

    Ported from the upstream formatting panel: the button face is drawn
    procedurally (selected mark over a ``あ`` glyph), and the popup menu
    picks the mark style / position.  Checked means an emphasis style is
    active; unchecking (clicking the face) clears it to ``none``.
    """

    emphasis_changed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selected_style = "filled dot"
        self._position = DEFAULT_EMPHASIS_POSITION
        self.setObjectName("FontEmphasisToolButton")
        self.setCheckable(True)
        self.setToolTip(self.tr("Emphasis Marks"))
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(self)
        menu.setObjectName("FontEmphasisMenu")
        section_font = menu.font()
        section_font.setBold(True)
        marks_header = menu.addAction(self.tr("Marks"))
        marks_header.setEnabled(False)
        marks_header.setFont(section_font)
        self._style_group = QActionGroup(self)
        self._style_group.setExclusive(True)
        self._style_actions = {}
        style_labels = (
            self.tr("Filled Dot"),
            self.tr("Open Dot"),
            self.tr("Filled Circle"),
            self.tr("Open Circle"),
            self.tr("Filled Double Circle"),
            self.tr("Open Double Circle"),
            self.tr("Filled Triangle"),
            self.tr("Open Triangle"),
            self.tr("Filled Sesame"),
            self.tr("Open Sesame"),
        )
        for label, style in zip(style_labels, EMPHASIS_STYLES[1:]):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setData(style)
            self._style_group.addAction(action)
            self._style_actions[style] = action
        self._style_group.triggered.connect(self._on_style_selected)

        position_header = menu.addAction(self.tr("Position"))
        position_header.setEnabled(False)
        position_header.setFont(section_font)
        self._position_group = QActionGroup(self)
        self._position_group.setExclusive(True)
        self._position_actions = {}
        position_labels = (
            self.tr("Over / Right"),
            self.tr("Under / Right"),
            self.tr("Over / Left"),
            self.tr("Under / Left"),
        )
        for label, position in zip(position_labels, EMPHASIS_POSITIONS):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setData(position)
            self._position_group.addAction(action)
            self._position_actions[position] = action

        self._position_group.triggered.connect(self._on_position_selected)
        menu.aboutToShow.connect(self._update_menu_icons)
        self.setMenu(menu)
        self._update_menu_icons()
        self._style_actions[self._selected_style].setChecked(True)
        self._position_actions[self._position].setChecked(True)
        self.clicked.connect(self._on_toggled)

    def values(self) -> tuple[str, str]:
        style = self._selected_style if self.isChecked() else "none"
        return style, self._position

    def _update_menu_icons(self) -> None:
        icon_size = 24
        ratio = max(1.0, self.devicePixelRatioF())
        font = self.font()
        font.setPixelSize(20)
        color = self.palette().text().color()
        icon_key = (ratio, font.toString(), color.rgba())
        if icon_key == getattr(self, "_menu_icon_key", None):
            return
        self._menu_icon_key = icon_key
        for style, action in self._style_actions.items():
            pixmap = QPixmap(
                round(icon_size * ratio), round(icon_size * ratio)
            )
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(
                0,
                0,
                icon_size,
                icon_size,
                Qt.AlignmentFlag.AlignCenter,
                EMPHASIS_GLYPHS[style],
            )
            painter.end()
            action.setIcon(QIcon(pixmap))

    def set_values(self, style: str, position: str) -> None:
        enabled = style in self._style_actions
        if enabled:
            self._selected_style = style
            self._style_actions[style].setChecked(True)
        if position in self._position_actions:
            self._position = position
            self._position_actions[position].setChecked(True)
        with QSignalBlocker(self):
            self.setChecked(enabled)
        self.update()

    def _on_toggled(self, _checked: bool) -> None:
        self.emphasis_changed.emit(*self.values())

    def _on_style_selected(self, action) -> None:
        self._selected_style = str(action.data())
        self.setChecked(True)
        self.update()
        self.emphasis_changed.emit(*self.values())

    def _on_position_selected(self, action) -> None:
        self._position = str(action.data())
        if self.isChecked():
            self.emphasis_changed.emit(*self.values())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        arrow_width = 11
        icon_width = max(1, self.width() - arrow_width)
        icon_rect = self.rect()
        icon_rect.setWidth(icon_width)
        if self.isChecked() and self.isEnabled():
            painter.fillRect(
                icon_rect.adjusted(2, 2, -2, -2), QColor(30, 147, 229)
            )
        if self.isEnabled() and (self.isChecked() or self.underMouse()):
            painter.setPen(QPen(QColor(30, 147, 229), 2))
            painter.drawRect(icon_rect.adjusted(1, 1, -1, -1))
        color = (
            QColor("white")
            if self.isChecked() and self.isEnabled()
            else self.palette().text().color()
        )
        if not self.isEnabled():
            color.setAlpha(110)
        painter.setPen(color)

        glyph_font = self.font()
        glyph_font.setPixelSize(16)
        mark_font = self.font()
        mark_font.setPixelSize(12)
        painter.setFont(mark_font)
        mark = EMPHASIS_GLYPHS[self._selected_style]
        mark_bounds = painter.fontMetrics().tightBoundingRect(mark)
        mark_x = round((icon_width - mark_bounds.width()) / 2 - mark_bounds.left())
        mark_y = self.height() - 3 - mark_bounds.bottom()
        painter.drawText(mark_x, mark_y, mark)

        glyph = "あ"
        glyph_bottom = mark_y + mark_bounds.top() - 1
        painter.setFont(glyph_font)
        glyph_bounds = painter.fontMetrics().tightBoundingRect(glyph)
        available_height = max(1, glyph_bottom - 1)
        if glyph_bounds.height() > 0:
            fitted_size = round(
                glyph_font.pixelSize()
                * available_height
                / glyph_bounds.height()
            )
            glyph_font.setPixelSize(min(19, max(16, fitted_size)))
            painter.setFont(glyph_font)
            glyph_bounds = painter.fontMetrics().tightBoundingRect(glyph)
        glyph_x = round(
            (icon_width - glyph_bounds.width()) / 2 - glyph_bounds.left()
        )
        glyph_y = glyph_bottom - glyph_bounds.bottom()
        painter.drawText(glyph_x, glyph_y, glyph)

        separator = QColor(color)
        separator.setAlpha(90)
        painter.setPen(QPen(separator, 1))
        painter.drawLine(icon_width, 3, icon_width, self.height() - 4)
        painter.setPen(QPen(color, 1.2))
        arrow_x = self.width() - arrow_width // 2
        arrow_y = self.height() // 2
        painter.drawLine(arrow_x - 3, arrow_y - 1, arrow_x, arrow_y + 2)
        painter.drawLine(arrow_x, arrow_y + 2, arrow_x + 3, arrow_y - 1)


class FormatGroupBtn(QFrame):
    param_changed = Signal(str, bool)
    emphasis_changed = Signal(str, str)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.strikeBtn = QFontChecker(self)
        self.strikeBtn.setObjectName("FontStrikeChecker")
        self.strikeBtn.setToolTip(self.tr("Strike-through"))
        self.strikeBtn.clicked.connect(self.setStrikeout)
        self.italicBtn = QFontChecker(self)
        self.italicBtn.setObjectName("FontItalicChecker")
        self.italicBtn.clicked.connect(self.setItalic)
        self.underlineBtn = QFontChecker(self)
        self.underlineBtn.setObjectName("FontUnderlineChecker")
        self.underlineBtn.clicked.connect(self.setUnderline)
        self.emphasisBtn = EmphasisToolButton(self)
        self.emphasisBtn.emphasis_changed.connect(self.emphasis_changed)
        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.strikeBtn)
        hlayout.addWidget(self.italicBtn)
        hlayout.addWidget(self.underlineBtn)
        hlayout.addWidget(self.emphasisBtn)
        hlayout.setSpacing(0)
        hlayout.setContentsMargins(0, 0, 0, 0)

    def setStrikeout(self):
        self.param_changed.emit("strikeout", self.strikeBtn.isChecked())

    def setItalic(self):
        self.param_changed.emit("italic", self.italicBtn.isChecked())

    def setUnderline(self):
        self.param_changed.emit("underline", self.underlineBtn.isChecked())


class FontSizeBox(QFrame):
    param_changed = Signal(str, float)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fcombobox = SizeComboBox([1, 200], "font_size", self)
        self.fcombobox.addItems([str(v) for v in C.pcfg.font_size_presets])
        self.fcombobox.param_changed.connect(self.param_changed)
        # 保证三位数（如 "200"）及多字号标记（如 "150+"）不被省略号截断
        self.fcombobox.setMinimumWidth(90)

        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.fcombobox)
        hlayout.setContentsMargins(0, 0, 0, 0)


class FontItemDelegate(QStyledItemDelegate):
    """Render font preview in the font combo box using the corresponding font"""

    def paint(self, painter, option, index):
        font_family = index.data(Qt.DisplayRole)
        if isinstance(font_family, str):
            # 将选项的字体替换为当前条目对应的字体家族
            option.font = QFont(font_family, option.font.pointSize())
        super().paint(painter, option, index)


class FontFamilyComboBox(QComboBox):
    param_changed = Signal(str, object)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.currentTextChanged.connect(self.on_fontfamily_changed)
        self.setItemDelegate(FontItemDelegate())

    def apply_fontfamily(self):
        ffamily = self.currentText()
        # ===== 新增：处理归并映射 =====
        # 如果用户选择的是归并后的规范名，但 Qt 内部用的是原始名，
        # 需要确保能找到对应的样式
        from utils import shared

        if (
            ffamily not in shared.ALL_FONT_FAMILIES
            and ffamily in shared.CUSTOM_FONT_FAMILIES
        ):
            # 归并后的名字可能不在 Qt 的 families 列表中，但样式映射已建立
            self.param_changed.emit("font_family", ffamily)
            return
        # ===== 新增结束 =====
        if ffamily in shared.ALL_FONT_FAMILIES:
            self.param_changed.emit("font_family", ffamily)

    def update_font_list(self, font_list):
        self.currentTextChanged.disconnect(self.on_fontfamily_changed)
        current_font = self.currentText()
        self.clear()
        self.addItems(font_list)
        if current_font in font_list:
            self.setCurrentText(current_font)
        self.currentTextChanged.connect(self.on_fontfamily_changed)

    def on_fontfamily_changed(self):
        self.apply_fontfamily()


class AnnotationFormatGroup(QFrame):
    """Ruby and OpenType-feature annotation controls.

    Emphasis and tate-chu-yoko live as icon buttons in the format rows
    (``FormatGroupBtn`` / the vertical checker row); this group keeps the
    text-entry annotation (Ruby) and the OpenType feature axes (ligatures,
    oldstyle numerals), mirroring the upstream advanced-format grouping.
    Hosted inside the "Annotations" fold capsule in ``FontFormatPanel`` —
    sub-sections are flat small-cap headers, not nested bordered boxes.
    Every change emits a named signal so the panel can route it to the
    engine ``TextBlkItem`` setter that owns the undo document transaction.
    ``set_*`` helpers restore widget state without emitting.
    """

    annotation_changed = Signal(str, object)
    ruby_remove = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def _small_label(text: str, bold: bool = False) -> QLabel:
            label = QLabel(text)
            font = self.font()
            font.setPointSizeF(shared.CONFIG_FONTSIZE_CONTENT * 0.9)
            font.setBold(bold)
            label.setFont(font)
            return label

        def _atomic_unit(label_widget, control) -> QWidget:
            unit = QWidget(self)
            unit_layout = QVBoxLayout(unit)
            unit_layout.setContentsMargins(0, 0, 0, 0)
            unit_layout.setSpacing(2)
            unit_layout.addWidget(label_widget)
            unit_layout.addWidget(control)
            return unit

        # ── Ruby / Furigana ──────────────────────────────────────────
        self.rubyTypeBox = QComboBox(self)
        self.rubyTypeBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.rubyTypeBox.addItem(self.tr("Group"), "group")
        self.rubyTypeBox.addItem(self.tr("Mono"), "mono")
        self.rubyPosBox = QComboBox(self)
        self.rubyPosBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.rubyPosBox.addItem(self.tr("Over / Right"), "over")
        self.rubyPosBox.addItem(self.tr("Under / Left"), "under")

        self.rubyEdit = QLineEdit(self)
        self.rubyEdit.setPlaceholderText(self.tr("Ruby text"))
        self.rubyEdit.setToolTip(
            self.tr("For Mono Ruby, separate readings with whitespace")
        )
        self.rubyApplyBtn = QPushButton(self.tr("Apply"), self)
        self.rubyRemoveBtn = QPushButton(self.tr("Remove"), self)

        ruby_text_row = QHBoxLayout()
        ruby_text_row.setSpacing(4)
        ruby_text_row.setContentsMargins(0, 0, 0, 0)
        ruby_text_row.addWidget(self.rubyEdit, 1)
        ruby_text_row.addWidget(self.rubyApplyBtn)
        ruby_text_row.addWidget(self.rubyRemoveBtn)

        # ── Ligature / Oldstyle feature axes ─────────────────────────
        self.ligatureBoxes = {}
        ligature_specs = (
            (LIGATURE_COMMON, self.tr("Common"),
             self.tr("Set common ligatures for the selected text")),
            (LIGATURE_DISCRETIONARY, self.tr("Discretionary"),
             self.tr("Set font-specific optional ligatures for the selected text")),
            (LIGATURE_CONTEXTUAL, self.tr("Contextual"),
             self.tr("Set contextual alternate glyphs for the selected text")),
            (OLDSTYLE_NUMS, self.tr("Oldstyle"),
             self.tr("Set oldstyle numerals for the selected text")),
        )
        ligature_units = []
        for axis, label_text, tooltip in ligature_specs:
            box = QComboBox(self)
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents
            )
            for label, state in zip(
                (self.tr("Default"), self.tr("On"), self.tr("Off")),
                LIGATURE_AXIS_VALUES,
            ):
                box.addItem(label, state)
            box.setToolTip(tooltip)
            self.ligatureBoxes[axis] = box
            ligature_units.append(
                _atomic_unit(_small_label(label_text), box)
            )
        self.onumBox = self.ligatureBoxes[OLDSTYLE_NUMS]

        # 平铺两小节：小标题 + 内容行，小节间一条细分隔线。
        # Type/Position 仍走 FlowLayout，窄面板下自动换行。
        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(2, 2, 2, 2)
        vlayout.setSpacing(6)

        vlayout.addWidget(_small_label(self.tr("Ruby / Furigana"), bold=True))
        ruby_selector_flow = FlowLayout()
        ruby_selector_flow.setContentsMargins(0, 0, 0, 0)
        ruby_selector_flow.addWidget(
            _atomic_unit(_small_label(self.tr("Type")), self.rubyTypeBox)
        )
        ruby_selector_flow.addWidget(
            _atomic_unit(_small_label(self.tr("Position")), self.rubyPosBox)
        )
        vlayout.addLayout(ruby_selector_flow)
        vlayout.addWidget(_small_label(self.tr("Reading")))
        vlayout.addLayout(ruby_text_row)

        separator = QFrame(self)
        separator.setObjectName("fmtGroupSeparator")
        separator.setFixedHeight(1)
        separator.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        vlayout.addWidget(separator)

        vlayout.addWidget(_small_label(self.tr("Ligature"), bold=True))
        # 2×2 紧凑网格：主题字号下单个下拉 sizeHint ≈126px，一行四个
        # （≈534px）超出 348 内宽，会挤压到文字不可读
        ligature_grid = QGridLayout()
        ligature_grid.setContentsMargins(0, 0, 0, 0)
        ligature_grid.setHorizontalSpacing(10)
        ligature_grid.setVerticalSpacing(6)
        for index, unit in enumerate(ligature_units):
            ligature_grid.addWidget(unit, index // 2, index % 2)
        for col in (0, 1):
            ligature_grid.setColumnStretch(col, 1)
        vlayout.addLayout(ligature_grid)

        for axis, box in self.ligatureBoxes.items():
            if axis == OLDSTYLE_NUMS:
                box.currentIndexChanged.connect(
                    lambda _index: self.annotation_changed.emit(
                        "onum", self.onumBox.currentData()
                    )
                )
            else:
                box.currentIndexChanged.connect(
                    lambda _index, a=axis, b=box: self.annotation_changed.emit(
                        "ligature", (a, b.currentData())
                    )
                )
        self.rubyApplyBtn.clicked.connect(self._emit_ruby)
        self.rubyRemoveBtn.clicked.connect(self.ruby_remove)

    def _emit_ruby(self):
        self.annotation_changed.emit(
            "ruby",
            (
                self.rubyTypeBox.currentData(),
                self.rubyEdit.text(),
                self.rubyPosBox.currentData(),
            ),
        )

    def set_ruby(
        self,
        ruby_type: str,
        text: str,
        position: str,
        enabled: bool,
    ) -> None:
        with QSignalBlocker(self.rubyTypeBox), QSignalBlocker(
            self.rubyPosBox
        ):
            index = self.rubyTypeBox.findData(ruby_type)
            if index >= 0:
                self.rubyTypeBox.setCurrentIndex(index)
            self.rubyEdit.setText(text)
            index = self.rubyPosBox.findData(position)
            if index >= 0:
                self.rubyPosBox.setCurrentIndex(index)
        self.rubyRemoveBtn.setEnabled(enabled)

    def set_ligature(self, axis: str, state: str) -> None:
        box = self.ligatureBoxes.get(axis)
        if box is None:
            return
        with QSignalBlocker(box):
            index = box.findData(state)
            if index >= 0:
                box.setCurrentIndex(index)

    def set_onum(self, state: str) -> None:
        self.set_ligature(OLDSTYLE_NUMS, state)


class FontFormatPanel(Widget):
    textblk_item: TextBlkItem = None
    text_cursor: QTextCursor = None
    global_format: FontFormat = None
    restoring_textblk: bool = False

    def __init__(self, app: QApplication, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.app = app

        self.vlayout = QVBoxLayout(self)
        self.vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.familybox = FontFamilyComboBox(parent=self)
        self.familybox.setContentsMargins(0, 0, 0, 0)
        self.familybox.setObjectName("FontFamilyBox")
        self.familybox.setToolTip(self.tr("Font Family"))
        self.familybox.param_changed.connect(self.on_param_changed)
        self.familybox.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self.stylebox = QComboBox()
        self.stylebox.setObjectName("FontStyleBox")
        self.stylebox.setToolTip(self.tr("Font Style"))
        self.stylebox.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.stylebox.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.stylebox.setMaximumWidth(110)  # 限制最大宽度，防止挤占字体框
        self.stylebox.currentTextChanged.connect(self.on_fontstyle_changed)

        self.fontsizebox = FontSizeBox(self)
        self.fontsizebox.setToolTip(self.tr("Font Size"))
        self.fontsizebox.setObjectName("FontSizeBox")
        self.fontsizebox.fcombobox.setToolTip(self.tr("Change font size"))
        self.fontsizebox.param_changed.connect(self.on_param_changed)

        self.lineSpacingLabel = SizeControlLabel(
            self, direction=1, transparent_bg=False
        )
        self.lineSpacingLabel.setObjectName("lineSpacingLabel")
        self.lineSpacingLabel.size_ctrl_changed.connect(self.onLineSpacingCtrlChanged)
        self.lineSpacingLabel.btn_released.connect(
            lambda: self.on_param_changed("line_spacing", self.lineSpacingBox.value())
        )

        self.lineSpacingBox = SizeComboBox([0, 100], "line_spacing", self)
        self.lineSpacingBox.addItems([str(v) for v in C.pcfg.line_spacing_presets])
        self.lineSpacingBox.setToolTip(self.tr("Change line spacing"))
        self.lineSpacingBox.param_changed.connect(self.on_param_changed)

        self.colorPicker = ColorPickerLabel(self, param_name="frgb")
        self.colorPicker.setToolTip(self.tr("Change font color"))
        self.colorPicker.changingColor.connect(self.changingColor)
        self.colorPicker.colorChanged.connect(self.onColorLabelChanged)
        self.colorPicker.apply_color.connect(self.on_apply_color)

        self.alignBtnGroup = AlignmentBtnGroup(self)
        self.alignBtnGroup.param_changed.connect(self.on_param_changed)

        self.formatBtnGroup = FormatGroupBtn(self)
        self.formatBtnGroup.param_changed.connect(self.on_param_changed)

        self.verticalChecker = QFontChecker(self)
        self.verticalChecker.setObjectName("FontVerticalChecker")
        self.verticalChecker.clicked.connect(
            lambda: self.on_param_changed("vertical", self.verticalChecker.isChecked())
        )

        # Annotation icon toggles (upstream layout): tate-chu-yoko and the
        # standard vertical roman alignment sit next to the vertical checker.
        self.tcyChecker = QFontChecker(self)
        self.tcyChecker.setObjectName("FontTateChuYokoChecker")
        self.tcyChecker.setToolTip(
            self.tr("Combine the selected text into one upright vertical cell")
        )
        self.tcyChecker.toggled.connect(
            lambda checked: self._on_annotation_changed("tcy", checked)
        )
        self.romanAlignmentChecker = QFontChecker(self)
        self.romanAlignmentChecker.setObjectName("FontRomanAlignmentChecker")
        self.romanAlignmentChecker.setToolTip(
            self.tr("Standard Vertical Roman Alignment")
        )
        self.romanAlignmentChecker.clicked.connect(
            lambda: self.on_param_changed(
                "standard_vertical_roman_alignment",
                self.romanAlignmentChecker.isChecked(),
            )
        )

        self.strokeWidthBox = SizeComboBox([0, 10], "stroke_width", self)
        self.strokeWidthBox.addItems([str(v) for v in C.pcfg.stroke_width_presets])
        self.strokeWidthBox.setToolTip(self.tr("Change stroke width"))
        self.strokeWidthBox.param_changed.connect(self.on_param_changed)

        self.fontStrokeLabel = SizeControlLabel(self, 0, self.tr("Stroke"))
        self.fontStrokeLabel.setObjectName("fontStrokeLabel")
        font = self.fontStrokeLabel.font()
        font.setPointSizeF(shared.CONFIG_FONTSIZE_CONTENT * 0.95)
        self.fontStrokeLabel.setFont(font)
        self.fontStrokeLabel.size_ctrl_changed.connect(
            self.strokeWidthBox.changeByDelta
        )
        self.fontStrokeLabel.btn_released.connect(
            lambda: self.on_param_changed("stroke_width", self.strokeWidthBox.value())
        )

        self.strokeColorPicker = ColorPickerLabel(self, param_name="srgb")
        self.strokeColorPicker.setToolTip(self.tr("Change stroke color"))
        self.strokeColorPicker.changingColor.connect(self.changingColor)
        self.strokeColorPicker.colorChanged.connect(self.onColorLabelChanged)
        self.strokeColorPicker.apply_color.connect(self.on_apply_color)

        stroke_hlayout = QHBoxLayout()
        stroke_hlayout.addWidget(self.fontStrokeLabel)
        stroke_hlayout.addWidget(self.strokeWidthBox)
        stroke_hlayout.addWidget(self.strokeColorPicker)
        stroke_hlayout.setSpacing(shared.WIDGET_SPACING_CLOSE)

        self.letterSpacingBox = SizeComboBox([0, 10], "letter_spacing", self)
        self.letterSpacingBox.addItems([str(v) for v in C.pcfg.letter_spacing_presets])
        self.letterSpacingBox.setToolTip(self.tr("Change letter spacing"))
        self.letterSpacingBox.setMinimumWidth(int(self.letterSpacingBox.height() * 2.5))
        self.letterSpacingBox.param_changed.connect(self.on_param_changed)

        self.letterSpacingLabel = SizeControlLabel(
            self, direction=0, transparent_bg=False
        )
        self.letterSpacingLabel.setObjectName("letterSpacingLabel")
        self.letterSpacingLabel.size_ctrl_changed.connect(
            self.letterSpacingBox.changeByDelta
        )
        self.letterSpacingLabel.btn_released.connect(
            lambda: self.on_param_changed(
                "letter_spacing", self.letterSpacingBox.value()
            )
        )

        lettersp_hlayout = QHBoxLayout()
        lettersp_hlayout.addWidget(self.letterSpacingLabel)
        lettersp_hlayout.addWidget(self.letterSpacingBox)
        lettersp_hlayout.setSpacing(shared.WIDGET_SPACING_CLOSE)

        self.global_fontfmt_str = self.tr("Global Font Format")
        self.textstyle_panel = TextStylePresetPanel(
            self.global_fontfmt_str,
            config_name="show_text_style_preset",
            config_expand_name="expand_tstyle_panel",
            title_capsule=True,
        )
        self.textstyle_panel.active_text_style_label_changed.connect(
            self.on_active_textstyle_label_changed
        )
        self.textstyle_panel.active_stylename_edited.connect(
            self.on_active_stylename_edited
        )

        # Unified text style entry — opens the Text Style dialog (opacity,
        # line spacing, shadow, gradient). Replaces the old inline panel.
        self.text_style_btn = TextStyleEntryButton(self.tr("Text Style"))
        self.text_style_btn.setToolTip(self.tr("Edit text style"))
        self.text_style_btn.clicked.connect(self._on_text_style_btn_clicked)

        # Text transform panel (stage 5 node H) — owned by the same session
        # that talks to the scene controls; the panel is only its UI front.
        self.texttransform_panel = TextTransformPanel(
            self.tr("Text Transform"),
            config_name="text_transform_panel",
            config_expand_name="expand_ttransform_panel",
        )
        self.text_transform_editor = TextTransformEditSession(
            self.texttransform_panel
        )
        self.text_transform_editor.global_format = self.global_format

        # Remove View menu entries + hide buttons for these built-in panels
        # (keep the panels functional; prevent accidental hide via View menu)
        for cfg in [
            "show_text_style_preset",
            "text_transform_panel",
        ]:
            shared.config_name_to_view_widget.pop(cfg, None)
        for p in [
            self.textstyle_panel,
            self.texttransform_panel,
        ]:
            hl = p.view_widget.title_label.hidelabel
            if hl is not None:
                hl.setVisible(False)
                hl.setMaximumSize(0, 0)
                hl.setMinimumSize(0, 0)

        self.familybox.currentTextChanged.connect(self.on_familybox_changed)

        # ── Zone A：全局字体样式 ────────────────────────────────────
        # 预设折叠胶囊（标题承载 Global Font Format / TextBlock #N）
        self.vlayout.addWidget(self.textstyle_panel.view_widget)

        # ── Zone B：基本选项（平铺，无边框）─────────────────────────
        # Row 1：字体选择 [颜色 | 字体 | 字重]——字号迁往测量行，把整行
        # 宽度让给字体名与字重名的显示
        font_selector = QHBoxLayout()
        font_selector.addWidget(self.colorPicker)
        font_selector.addWidget(self.familybox, 1)  # 字体框占绝大部分伸缩空间
        font_selector.addWidget(self.stylebox)  # 字重框按内容自适应
        font_selector.setSpacing(4)
        font_selector.setContentsMargins(2, 0, 2, 0)

        # Row 2：格式图标行 对齐 | B/I/U/着重号 | 竖排(TCY/Roman)，竖线分组
        def _vsep() -> QFrame:
            sep = QFrame(self)
            sep.setObjectName("fmtGroupSeparator")
            sep.setFixedSize(1, 16)
            return sep

        format_icons = QHBoxLayout()
        format_icons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        format_icons.addWidget(self.alignBtnGroup)
        format_icons.addWidget(_vsep())
        format_icons.addWidget(self.formatBtnGroup)
        format_icons.addWidget(_vsep())
        vertical_layout = QHBoxLayout()
        vertical_layout.addWidget(self.verticalChecker)
        vertical_layout.addWidget(self.tcyChecker)
        vertical_layout.addWidget(self.romanAlignmentChecker)
        vertical_layout.setSpacing(0)
        vertical_layout.setContentsMargins(0, 0, 0, 0)
        format_icons.addLayout(vertical_layout)
        format_icons.setSpacing(6)
        format_icons.setContentsMargins(2, 0, 2, 0)

        # Row 3：量测行 [字号] [行距] [字距]——排版数值一组；描边组宽
        # 度放不进同行，单独一行
        linesp_hlayout = QHBoxLayout()
        linesp_hlayout.addWidget(self.lineSpacingLabel)
        linesp_hlayout.addWidget(self.lineSpacingBox)
        linesp_hlayout.setSpacing(shared.WIDGET_SPACING_CLOSE)
        size_and_metrics = QHBoxLayout()
        size_and_metrics.setAlignment(Qt.AlignmentFlag.AlignLeft)
        size_and_metrics.addWidget(self.fontsizebox)
        size_and_metrics.addLayout(linesp_hlayout)
        size_and_metrics.addLayout(lettersp_hlayout)
        size_and_metrics.setContentsMargins(2, 0, 2, 0)
        size_and_metrics.setSpacing(13)
        stroke_row = QHBoxLayout()
        stroke_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        stroke_row.addLayout(stroke_hlayout)
        stroke_row.setContentsMargins(2, 0, 2, 0)

        basics = QVBoxLayout()
        basics.addLayout(font_selector)
        basics.addLayout(format_icons)
        basics.addLayout(size_and_metrics)
        basics.addLayout(stroke_row)
        basics.setSpacing(5)
        basics.setContentsMargins(2, 3, 2, 3)
        self.vlayout.addLayout(basics)

        # ── Zone C：拓展样式与变换 ──────────────────────────────────
        # 样式入口按钮：不透明度/行距/阴影/渐变 统一收进 Text Style 对话框
        self.vlayout.addWidget(self.text_style_btn)
        self.vlayout.addWidget(self.texttransform_panel.view_widget)

        # 注解折叠胶囊：默认收起（pcfg.expand_annotation_panel），且仅在
        # 选中文字块后显示（见 _sync_annotation_controls），消除全局模式
        # 下的灰色死区。着重号/縦中横图标仍在基本选项行内即点即用。
        self.annotation_group = AnnotationFormatGroup(self)
        self.annotation_area = ViewWidget(
            self.annotation_group,
            self.tr("Annotations"),
            title_capsule=True,
        )
        # 不注册进 View 菜单，仅持久化展开状态
        self.annotation_area.config_expand_name = "expand_annotation_panel"
        self.annotation_area.set_expend_area(
            C.pcfg.expand_annotation_panel, set_config=False
        )
        self.annotation_area.setVisible(False)
        self.vlayout.addWidget(self.annotation_area)
        self.annotation_group.annotation_changed.connect(
            self._on_annotation_changed
        )
        self.annotation_group.ruby_remove.connect(self._on_ruby_remove)
        self.annotation_group.setEnabled(False)
        self.formatBtnGroup.emphasis_changed.connect(
            lambda style, position: self._on_annotation_changed(
                "emphasis", (style, position)
            )
        )
        self.tcyChecker.setEnabled(False)
        self.formatBtnGroup.emphasisBtn.setEnabled(False)

        self.vlayout.setContentsMargins(0, 0, 0, 0)
        self.vlayout.setSpacing(7)

        self.focusOnColorDialog = False
        C.active_format = self.global_format

        if shared.ALL_FONT_FAMILIES:
            from utils.config import pcfg

            self.familybox.addItems(shared.get_filtered_font_list(pcfg.excluded_fonts))

    def global_mode(self):
        return id(C.active_format) == id(self.global_format)

    def active_text_style_label(self):
        return self.textstyle_panel.active_text_style_label

    def active_text_style_format(self):
        af = self.active_text_style_label()
        if af is not None:
            return af.fontfmt
        else:
            return None

    def on_param_changed(self, param_name: str, value):
        func = FM.handle_ffmt_change.get(param_name)
        func_kwargs = {}
        if param_name in {"font_size", "rel_font_size"}:
            func_kwargs["clip_size"] = True
        if self.global_mode():
            func(param_name, value, self.global_format, is_global=True, **func_kwargs)
            self.update_text_style_label()
        else:
            func(
                param_name,
                value,
                C.active_format,
                is_global=False,
                blkitems=self.textblk_item,
                set_focus=True,
                **func_kwargs,
            )

    def update_text_style_label(self):
        if self.global_mode():
            active_text_style_label = self.active_text_style_label()
            if active_text_style_label is not None:
                active_text_style_label.update_style(self.global_format)

    def changingColor(self):
        self.focusOnColorDialog = True

    def onColorLabelChanged(self, is_valid=True):
        self.focusOnColorDialog = False
        if is_valid:
            sender: ColorPickerLabel = self.sender()
            rgb = sender.rgb()
            self.on_param_changed(sender.param_name, rgb)

    def on_apply_color(self, param_name, rgb):
        self.on_param_changed(param_name, rgb)

    def onLineSpacingCtrlChanged(self, delta: int):
        if C.active_format.line_spacing_type == LineSpacingType.Distance:
            mul = 0.1
        else:
            mul = 0.01
        self.lineSpacingBox.setValue(self.lineSpacingBox.value() + delta * mul)

    def _on_text_style_btn_clicked(self):
        from .shadow_gradient_dialog import TextStyleDialog

        fmt = self.global_format if self.global_mode() else C.active_format
        dlg = TextStyleDialog(
            fmt,
            tab="basic",
            text_color=fmt.frgb,
            shadow_include_stroke=self.global_format.shadow_include_stroke,
            parent=self.window(),
        )
        dlg.applied.connect(self._on_text_style_applied)
        if dlg.exec_() == QDialog.DialogCode.Accepted:
            self._on_text_style_applied(
                dlg.get_basic_params(),
                dlg.get_shadow_params(),
                dlg.get_gradient_params(),
            )
        dlg.applied.disconnect(self._on_text_style_applied)

    def _on_text_style_applied(
        self, basic_params: dict, shadow_params: dict, gradient_params: dict
    ):
        # Handle shadow_include_stroke separately — it must apply to ALL text blocks
        # on the current page, not just selected ones. This is a project-wide toggle
        # (no local/per-block mode) consistent with PS behavior.
        include_stroke = shadow_params.pop("shadow_include_stroke", None)

        for param_name, value in basic_params.items():
            self.on_param_changed(param_name, value)
        for param_name, value in shadow_params.items():
            self.on_param_changed(param_name, value)
        for param_name, value in gradient_params.items():
            self.on_param_changed(param_name, value)

        if include_stroke is not None:
            # Always propagate to all text items on the scene
            from .shared_widget import canvas as SW_canvas
            from .textitem import TextBlkItem

            for item in SW_canvas.items():
                if isinstance(item, TextBlkItem):
                    item.setBGAttribute("shadow_include_stroke", include_stroke)
                    item.update()

            # Always persist to global format (project-wide toggle).
            self.global_format.shadow_include_stroke = include_stroke

    def set_active_format(self, font_format: FontFormat, multi_size=False):
        C.active_format = font_format
        self.familybox.blockSignals(True)
        self.stylebox.blockSignals(True)  # 新增

        from utils.config import pcfg

        font_size = min(round(font_format.font_size, 1), pcfg.max_font_size)
        if int(font_size) == font_size:
            font_size = str(int(font_size))
        else:
            font_size = f"{font_size:.1f}"
        if multi_size:
            font_size += "+"
        self.fontsizebox.fcombobox.setCurrentText(font_size)
        self.familybox.setCurrentText(font_format.font_family)

        # 【新增】回显 Style
        styles = shared.FONT_STYLES.get(font_format.font_family, [])
        self.stylebox.clear()
        self.stylebox.addItems(styles)
        if font_format._style_name and font_format._style_name in styles:
            self.stylebox.setCurrentText(font_format._style_name)
        else:
            idx = self.stylebox.findText("Regular")
            if idx < 0 and len(styles) > 0:
                idx = 0
            if idx >= 0:
                self.stylebox.setCurrentIndex(idx)
        self.colorPicker.setPickerColor(font_format.foreground_color())
        self.strokeColorPicker.setPickerColor(font_format.stroke_color())
        self.strokeWidthBox.setValue(font_format.stroke_width)
        self.lineSpacingBox.setValue(font_format.line_spacing)
        self.letterSpacingBox.setValue(font_format.letter_spacing)
        self.verticalChecker.setChecked(font_format.vertical)
        self.romanAlignmentChecker.setChecked(
            font_format.standard_vertical_roman_alignment
        )
        self.formatBtnGroup.strikeBtn.setChecked(font_format.strikeout)
        self.formatBtnGroup.underlineBtn.setChecked(font_format.underline)
        self.formatBtnGroup.italicBtn.setChecked(font_format.italic)
        self.alignBtnGroup.setAlignment(font_format.alignment)

        self.familybox.blockSignals(False)
        self.stylebox.blockSignals(False)  # 新增
        # Keep the session's no-items path (global format) in sync and show
        # the active format's transform state in the panel.
        self.text_transform_editor.global_format = self.global_format
        self.texttransform_panel.set_active_format(font_format)

    def set_globalfmt_title(self):
        active_text_style_label = self.active_text_style_label()
        if active_text_style_label is None:
            self.textstyle_panel.setTitle(self.global_fontfmt_str)
        else:
            title = (
                self.global_fontfmt_str
                + " - "
                + active_text_style_label.fontfmt._style_name
            )
            valid_title = self.textstyle_panel.elidedText(title)
            self.textstyle_panel.setTitle(valid_title)

    def reload_presets(self):
        """Reload dropdown items from pcfg preset lists, preserving current values."""
        from utils.config import pcfg

        self.fontsizebox.fcombobox.blockSignals(True)
        cur = self.fontsizebox.fcombobox.value()
        self.fontsizebox.fcombobox.clear()
        self.fontsizebox.fcombobox.addItems([str(v) for v in pcfg.font_size_presets])
        self.fontsizebox.fcombobox.setValue(cur)
        self.fontsizebox.fcombobox.blockSignals(False)

        self.lineSpacingBox.blockSignals(True)
        cur = self.lineSpacingBox.value()
        self.lineSpacingBox.clear()
        self.lineSpacingBox.addItems([str(v) for v in pcfg.line_spacing_presets])
        self.lineSpacingBox.setValue(cur)
        self.lineSpacingBox.blockSignals(False)

        self.letterSpacingBox.blockSignals(True)
        cur = self.letterSpacingBox.value()
        self.letterSpacingBox.clear()
        self.letterSpacingBox.addItems([str(v) for v in pcfg.letter_spacing_presets])
        self.letterSpacingBox.setValue(cur)
        self.letterSpacingBox.blockSignals(False)

        self.strokeWidthBox.blockSignals(True)
        cur = self.strokeWidthBox.value()
        self.strokeWidthBox.clear()
        self.strokeWidthBox.addItems([str(v) for v in pcfg.stroke_width_presets])
        self.strokeWidthBox.setValue(cur)
        self.strokeWidthBox.blockSignals(False)

    def deactivate_style_label(self):
        if self.active_text_style_label() is not None:
            self.textstyle_panel.on_stylelabel_activated(False)

    def on_active_textstyle_label_changed(self):
        """
        merge activate textstyle into global format
        """
        _GRADIENT_FIELDS = {
            "gradient_enabled",
            "gradient_start_color",
            "gradient_end_color",
            "gradient_angle",
            "gradient_size",
        }
        active_text_style_label = self.active_text_style_label()
        if active_text_style_label is not None:
            # Save gradient fields before merge — gradient is a per-text-block
            # visual effect, not a global default. Text styles should not
            # accidentally enable gradient in the global config.
            saved = {
                k: copy.deepcopy(getattr(self.global_format, k))
                for k in _GRADIENT_FIELDS
            }
            updated_keys = self.global_format.merge(
                active_text_style_label.fontfmt, compare=True
            )
            for k, v in saved.items():
                setattr(self.global_format, k, v)
            if self.global_mode() and len(updated_keys) > 0:
                self.set_active_format(self.global_format)
            self.set_globalfmt_title()
        else:
            if self.global_mode():
                self.set_globalfmt_title()

    def on_active_stylename_edited(self):
        if self.global_mode():
            self.set_globalfmt_title()

    def on_familybox_changed(self, family: str):
        """Update style combo box when font family changes.

        Preserves the current _style_name from the active format when possible,
        so switching font family doesn't unconditionally reset to "Regular".
        """
        # Look up the desired style from the active format before touching the combo
        act_ffmt = self.global_format if self.global_mode() else C.active_format
        desired_style = ""
        if act_ffmt is not None and act_ffmt._style_name:
            desired_style = act_ffmt._style_name

        self.stylebox.blockSignals(True)
        self.stylebox.clear()
        styles = shared.FONT_STYLES.get(family, [])
        self.stylebox.addItems(styles)

        if desired_style and desired_style in styles:
            self.stylebox.setCurrentText(desired_style)
        else:
            idx = self.stylebox.findText("Regular")
            if idx < 0 and len(styles) > 0:
                idx = 0
            if idx >= 0:
                self.stylebox.setCurrentIndex(idx)
        self.stylebox.blockSignals(False)

        # 触发格式更新（apply_font_change 会同步 bold/font_weight）
        self.apply_font_change()

    def on_fontstyle_changed(self, style: str):
        """Trigger format update when style changes"""
        self.apply_font_change()

    def apply_font_change(self):
        """Unified entry point for applying format changes, syncs Family and Style updates"""
        family = self.familybox.currentText()
        style = self.stylebox.currentText()

        if family not in shared.ALL_FONT_FAMILIES:
            return

        act_ffmt = self.global_format if self.global_mode() else C.active_format
        if act_ffmt is not None:
            act_ffmt._style_name = style

            # Sync bold and font_weight to match the selected style name.
            # Previously only _style_name was updated, leaving bold/font_weight
            # stale — so the bold button could remain checked after switching
            # font family (which resets style to Regular).
            if style:
                from qtpy.QtGui import QFont, QFontDatabase
                weight = QFontDatabase.weight(family, style)
                act_ffmt.font_weight = fix_fontweight_qt(weight)
                act_ffmt.bold = weight >= QFont.Weight.Bold
            else:
                act_ffmt.font_weight = None
                act_ffmt.bold = False

        # Then update font_family (setFontFamily reads _style_name)
        self.on_param_changed("font_family", family)

    def set_textblk_item(
        self, textblk_item: TextBlkItem = None, multi_select: bool = False
    ):
        # A selection transition is a transaction boundary for transform text.
        # Commit typed values against the old target list before replacing it.
        self.text_transform_editor.finish_pending_edits()
        if textblk_item is not None:
            transform_items = [textblk_item]
        elif multi_select:
            from ui import shared_widget as SW

            transform_items = SW.canvas.selected_text_items()
        else:
            transform_items = []

        preserve_local_owner = False
        if textblk_item is None:
            focus_w = self.app.focusWidget()
            focus_p = None if focus_w is None else focus_w.parentWidget()
            focus_on_fmtoptions = False
            if self.focusOnColorDialog:
                focus_on_fmtoptions = True
            elif focus_p:
                if focus_p == self or focus_p.parentWidget() == self:
                    focus_on_fmtoptions = True
            preserve_local_owner = (
                not transform_items
                and self.textblk_item is not None
                and focus_on_fmtoptions
            )
            if preserve_local_owner:
                # Formatting focus can briefly clear the canvas selection; use
                # the retained local item when comparing effective owners.
                transform_items = [self.textblk_item]
            if not focus_on_fmtoptions:
                # Store the current text block's format before switching to global
                if self.textblk_item is not None:
                    # Save all format properties including gradient state
                    self.textblk_item.fontformat = copy.deepcopy(C.active_format)
                self.textblk_item = None
                self.set_active_format(self.global_format, multi_select)
                self.set_globalfmt_title()

        else:
            if not self.restoring_textblk:
                blk_fmt = textblk_item.get_fontformat()
                # Preserve gradient properties from the text block's format
                if hasattr(textblk_item.fontformat, "gradient_enabled"):
                    blk_fmt.gradient_enabled = textblk_item.fontformat.gradient_enabled
                    blk_fmt.gradient_start_color = (
                        textblk_item.fontformat.gradient_start_color
                    )
                    blk_fmt.gradient_end_color = (
                        textblk_item.fontformat.gradient_end_color
                    )
                    blk_fmt.gradient_angle = textblk_item.fontformat.gradient_angle
                    blk_fmt.gradient_size = textblk_item.fontformat.gradient_size
                self.textblk_item = textblk_item
                multi_size = (
                    not textblk_item.isEditing() and textblk_item.isMultiFontSize()
                )
                self.set_active_format(blk_fmt, multi_size)
                self.textstyle_panel.setTitle(f"TextBlock #{textblk_item.idx + 1}")
        self.text_transform_editor.replace_targets(transform_items)
        if transform_items:
            self.texttransform_panel.set_transform_items(transform_items)
        self._sync_annotation_controls()

    def _sync_annotation_controls(self):
        """Restore the annotation controls from the active text item.

        The annotation capsule is selection-scoped: in global mode it is
        hidden entirely (instead of a greyed dead zone), and its capsule
        title carries a "•" marker while the current block has any
        annotation active, so collapsed state still hints at content.

        Called on every selection transition; during text editing the engine
        item re-reports its own state, so a per-keystroke sync is deferred to
        node 3 panel consolidation.
        """
        item = self.textblk_item
        group = self.annotation_group
        has_item = item is not None
        self.annotation_area.setVisible(has_item)
        group.setEnabled(has_item)
        self.tcyChecker.setEnabled(has_item)
        self.formatBtnGroup.emphasisBtn.setEnabled(has_item)
        self._update_annotation_title()
        if item is None:
            return
        with QSignalBlocker(group), QSignalBlocker(self.tcyChecker):
            self.formatBtnGroup.emphasisBtn.set_values(*item.emphasis_values())
            self.tcyChecker.setChecked(item.tate_chu_yoko_enabled())
            for axis in (
                LIGATURE_COMMON,
                LIGATURE_DISCRETIONARY,
                LIGATURE_CONTEXTUAL,
            ):
                group.set_ligature(axis, item.ligature_axis_value(axis))
            group.set_onum(item.oldstyle_nums_value())
            ruby_type, text, position, enabled = item.ruby_editor_values()
            group.set_ruby(ruby_type, text, position, enabled)

    def _update_annotation_title(self):
        """Capsule title carries "•" while the current block has annotations."""
        item = self.textblk_item
        active = False
        if item is not None:
            active = (
                item.emphasis_values()[0] != EMPHASIS_STYLES[0]
                or item.tate_chu_yoko_enabled()
                or item.oldstyle_nums_value() != LIGATURE_DEFAULT
                or any(
                    item.ligature_axis_value(axis) != LIGATURE_DEFAULT
                    for axis in (
                        LIGATURE_COMMON,
                        LIGATURE_DISCRETIONARY,
                        LIGATURE_CONTEXTUAL,
                    )
                )
                or item.ruby_editor_values()[3]
            )
        title = self.tr("Annotations")
        if active:
            title += " •"
        self.annotation_area.setTitle(title)

    def _on_annotation_changed(self, name: str, value):
        item = self.textblk_item
        if item is None:
            return
        if name == "emphasis":
            item.setEmphasis(*value)
        elif name == "tcy":
            try:
                item.setTateChuYoko(value)
            except RubyValidationError as error:
                # 与 Ruby 互斥；回滚勾选态，避免控件状态与文档不一致
                self.tcyChecker.setChecked(item.tate_chu_yoko_enabled())
                QMessageBox.information(self, self.tr("Tate-chu-yoko"), str(error))
        elif name == "ligature":
            axis, state = value
            item.setLigatureAxis(axis, state)
        elif name == "onum":
            item.setOldstyleNums(value)
        elif name == "ruby":
            try:
                if (
                    not item.isEditing()
                    and not item.textCursor().hasSelection()
                ):
                    # 与 emphasis/TCY/连字/onum 一致：非编辑态应用到整块文本
                    # （编辑态仍需先在编辑器内选中注音基底）
                    cursor = item.textCursor()
                    cursor.select(QTextCursor.SelectionType.Document)
                    item.setTextCursor(cursor)
                item.setRuby(*value)
            except RubyValidationError as error:
                # Ruby needs selected base text; surface the engine's
                # validation reason instead of crashing the panel.
                QMessageBox.information(self, self.tr("Ruby"), str(error))

    def _on_ruby_remove(self):
        if self.textblk_item is not None:
            self.textblk_item.removeRuby()

    def resolve_text_transform_edits_for_save(self):
        # Pending numeric edits are not dirty until they commit; resolve them
        # before the close-time/save-time dirty check.
        self.text_transform_editor.resolve_for_save()

    def resolve_text_transform_edits_for_history_change(self):
        self.text_transform_editor.resolve_for_history_change()

    def resolve_text_transform_edits_for_page_change(self):
        self.text_transform_editor.resolve_for_page_change()

    def cancel_text_transform_edits_for_scene_change(self):
        self.text_transform_editor.cancel_for_scene_change()
