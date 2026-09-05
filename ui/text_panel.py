import copy

from qtpy.QtCore import QSignalBlocker, Qt, Signal
from qtpy.QtGui import (
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPixmap,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from utils import config as C
from utils import face_resolver
from utils import shared
from utils.base_styles import DIFF_FIELDS, quantize_field
from utils.fontformat import FontFormat, LineSpacingType, fix_fontweight_qt

from . import funcmaps as FM
from .custom_widget import (
    AlignmentChecker,
    ColorPickerLabel,
    ConfigLineEdit,
    FlowLayout,
    NoBorderPushBtn,
    QFontChecker,
    SizeComboBox,
    SizeControlLabel,
    SmallComboBox,
    SmallParamLabel,
    Widget,
)
from .text_style_presets import TextStylePresetPanel
from .text_style_dock import (
    GRADIENT_PARAMS,
    SHADOW_PARAMS,
    TextStyleGroup,
)
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

# 混合态占位符（符号免译）；下拉里以禁用项呈现
MIXED_PLACEHOLDER = "—"

# 多选混合检测字段：身份键（可跨大样式多选）+ 全部 diff 字段
_MIXED_CHECK_FIELDS = ("font_family", "vertical") + tuple(DIFF_FIELDS)


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


class EmphasisFormatGroup(QFrame):
    """Emphasis marks: visible mark + position pickers (rail dock content).

    Replaces the old ``EmphasisToolButton`` popup menu: the dock shows the
    ten CSS-compatible marks and the four positions as combo boxes, so the
    current values stay visible while editing.  Selecting the leading
    "None" entry clears emphasis (the old unchecked-button semantics).
    """

    emphasis_changed = Signal(str, str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def _unit(label_text: str, control: QWidget) -> QWidget:
            unit = QWidget(self)
            unit_layout = QVBoxLayout(unit)
            unit_layout.setContentsMargins(0, 0, 0, 0)
            unit_layout.setSpacing(2)
            unit_layout.addWidget(SmallParamLabel(label_text))
            unit_layout.addWidget(control)
            return unit

        self.markBox = SmallComboBox(parent=self)
        self.markBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.markBox.addItem(self.tr("None"), "none")
        mark_icons = self._build_mark_icons()
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
            self.markBox.addItem(mark_icons.get(style), label, style)

        self.positionBox = SmallComboBox(parent=self)
        self.positionBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        position_labels = (
            self.tr("Over / Right"),
            self.tr("Under / Right"),
            self.tr("Over / Left"),
            self.tr("Under / Left"),
        )
        for label, position in zip(position_labels, EMPHASIS_POSITIONS):
            self.positionBox.addItem(label, position)

        flow = FlowLayout()
        flow.setContentsMargins(0, 0, 0, 0)
        flow.addWidget(_unit(self.tr("Marks"), self.markBox))
        flow.addWidget(_unit(self.tr("Position"), self.positionBox))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        layout.addLayout(flow)

        self.markBox.currentIndexChanged.connect(self._emit)
        self.positionBox.currentIndexChanged.connect(self._emit)

    def _build_mark_icons(self) -> "dict":
        """Glyph icons for the mark dropdown (same drawing as the old menu)."""
        icon_size = 16
        ratio = max(1.0, self.devicePixelRatioF())
        font = self.font()
        font.setPixelSize(13)
        color = self.palette().text().color()
        icons = {}
        for style in EMPHASIS_STYLES[1:]:
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
            icons[style] = QIcon(pixmap)
        return icons

    def values(self) -> tuple[str, str]:
        return self.markBox.currentData(), self.positionBox.currentData()

    def set_values(self, style: str, position: str) -> None:
        with QSignalBlocker(self.markBox), QSignalBlocker(self.positionBox):
            index = self.markBox.findData(style)
            if index >= 0:
                self.markBox.setCurrentIndex(index)
            index = self.positionBox.findData(position)
            if index >= 0:
                self.positionBox.setCurrentIndex(index)

    def _emit(self, *_args):
        self.emphasis_changed.emit(*self.values())


class FormatGroupBtn(QFrame):
    param_changed = Signal(str, bool)

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
        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.strikeBtn)
        hlayout.addWidget(self.italicBtn)
        hlayout.addWidget(self.underlineBtn)
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


class WidePopupComboMixin:
    """下拉弹出列表按最长条目撑宽，闭合态宽度不受内容牵引。

    组合框本体保持布局给定的稳定宽度（长名截断显示）；点击展开时把
    弹出视图的最小宽度抬到最长条目宽，完整显示不裁剪。
    """

    def showPopup(self):
        view = self.view()
        if view is not None:
            need = view.sizeHintForColumn(0)
            if need >= 0:
                # 弹出后滚动条可能出现，预留其宽度
                need += view.verticalScrollBar().sizeHint().width()
                need += 2 * view.frameWidth() + 8
                view.setMinimumWidth(max(need, self.width()))
        super().showPopup()


class FontFamilyComboBox(WidePopupComboMixin, QComboBox):
    param_changed = Signal(str, object)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.currentTextChanged.connect(self.on_fontfamily_changed)
        self.setItemDelegate(FontItemDelegate())

    def apply_fontfamily(self):
        ffamily = self.currentText()
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


class FontStyleComboBox(WidePopupComboMixin, QComboBox):
    """字重选择框：闭合态宽度由布局拉伸固定（不随字重名变化），
    弹出列表经 WidePopupComboMixin 撑宽到最长条目。"""


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
    Controls use the app's custom widget set (``SmallComboBox`` /
    ``ConfigLineEdit`` / ``NoBorderPushBtn`` / ``SmallParamLabel``) so the
    dock stays theme-consistent with the rest of the application.
    """

    annotation_changed = Signal(str, object)
    ruby_remove = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def _small_label(text: str, bold: bool = False) -> SmallParamLabel:
            label = SmallParamLabel(text)
            if bold:
                font = label.font()
                font.setBold(True)
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
        self.rubyTypeBox = SmallComboBox(parent=self)
        self.rubyTypeBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.rubyTypeBox.addItem(self.tr("Group"), "group")
        self.rubyTypeBox.addItem(self.tr("Mono"), "mono")
        self.rubyPosBox = SmallComboBox(parent=self)
        self.rubyPosBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.rubyPosBox.addItem(self.tr("Over / Right"), "over")
        self.rubyPosBox.addItem(self.tr("Under / Left"), "under")

        self.rubyEdit = ConfigLineEdit(parent=self)
        self.rubyEdit.setPlaceholderText(self.tr("Ruby text"))
        self.rubyEdit.setToolTip(
            self.tr("For Mono Ruby, separate readings with whitespace")
        )
        self.rubyApplyBtn = NoBorderPushBtn(self.tr("Apply"), self)
        self.rubyRemoveBtn = NoBorderPushBtn(self.tr("Remove"), self)

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
            box = SmallComboBox(parent=self)
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
    # 多选态的选中块列表（镜像副本作为 C.active_format 时非空）
    _active_multi_items: list = None

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

        self.stylebox = FontStyleComboBox()
        self.stylebox.setObjectName("FontStyleBox")
        self.stylebox.setToolTip(self.tr("Font Style"))
        self.stylebox.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
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

        self.global_fontfmt_str = self.tr("New Block Default Format")
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

        # Text style group (opacity / shadow / gradient) — former modal
        # TextStyleDialog, now a rail dock panel for live canvas editing.
        self.textstyle_group = TextStyleGroup(self)
        self.textstyle_group.preview_changed.connect(self._preview_style_param)
        self.textstyle_group.commit_changed.connect(self._on_text_style_commit)
        self.textstyle_group.shadow_include_stroke_changed.connect(
            self._apply_shadow_include_stroke
        )
        self.textstyle_group.hide()

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
        # 预设折叠胶囊（标题承载 新块默认格式 / TextBlock #N）+ 右侧重置按钮
        self.reset_global_btn = NoBorderPushBtn(self.tr("Reset"), self)
        self.reset_global_btn.setToolTip(self.tr("Reset the new-block default format"))
        self.reset_global_btn.clicked.connect(self._reset_global_format)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        title_row.addWidget(self.textstyle_panel.view_widget, 1)
        title_row.addWidget(self.reset_global_btn)
        self.vlayout.addLayout(title_row)

        # ── Zone B：基本选项（平铺，无边框）─────────────────────────
        # Row 1：字体选择 [颜色 | 字体 | 字重]——字号迁往测量行；两个
        # 下拉框宽度由 stretch 比例固定（3:2），不随内容伸缩，展开时
        # 弹出列表自行撑宽完整显示（WidePopupComboMixin）
        font_selector = QHBoxLayout()
        font_selector.addWidget(self.colorPicker)
        font_selector.addWidget(self.familybox, 3)
        font_selector.addWidget(self.stylebox, 2)
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

        # ── Zone C：拓展样式 ──────────────────────────────────────
        # 样式(/变换) 内容外迁画布浮层（图标栏入口）。变换面板以
        # PanelArea（QScrollArea）本体进浮层：内部折叠标题条被去
        # 掉（浮层标题条已承载标题），滚动几何由 TransformPanel
        # 自身维护，随浮层宽度自适应重排变换卡片。

        # 注解组：内容外迁画布浮层（图标栏入口，见 install_annotation_launcher），
        # 着重号组随注解一同外迁：两者都是选中级注解，浮层懒创建，
        # 创建前组本体必须隐藏，避免在格式面板左上角裸显。
        self.annotation_group = AnnotationFormatGroup(self)
        self.annotation_group.annotation_changed.connect(
            self._on_annotation_changed
        )
        self.annotation_group.ruby_remove.connect(self._on_ruby_remove)
        self.annotation_group.setEnabled(False)
        self.annotation_group.hide()
        self.emphasis_group = EmphasisFormatGroup(self)
        self.emphasis_group.emphasis_changed.connect(
            lambda style, position: self._on_annotation_changed(
                "emphasis", (style, position)
            )
        )
        self.emphasis_group.setEnabled(False)
        self.emphasis_group.hide()
        self.tcyChecker.setEnabled(False)
        self.annotation_launcher = None
        self.annotation_dock = None
        self.emphasis_launcher = None
        self.emphasis_dock = None
        self.transform_launcher = None
        self.transform_dock = None
        self.textstyle_launcher = None
        self.textstyle_dock = None

        self.vlayout.setContentsMargins(0, 0, 0, 0)
        self.vlayout.setSpacing(7)

        self.focusOnColorDialog = False
        C.active_format = self.global_format

        if shared.ALL_FONT_FAMILIES:
            from utils.config import pcfg

            self.familybox.addItems(shared.get_filtered_font_list(pcfg.excluded_fonts))

    def global_mode(self):
        gf = self.global_format
        # None 守卫：global_format 到 mainwindow 构造尾部才注入
        # （pcfg.global_fontformat）。注入前 C.active_format 与它同为
        # None，id(None)==id(None) 会误判成全局模式，把启动期字体下拉
        # 填充触发的 font_family 信号派发到 None 上，刷
        # "undefined param name: font_family"
        return gf is not None and id(C.active_format) == id(gf)

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
            # 单选作用于当前块；多选镜像态作用于全部选中块（wrap 重定向）
            if self.textblk_item is not None:
                blkitems = self.textblk_item
            else:
                blkitems = self._active_multi_items or []
            if not blkitems:
                return
            func(
                param_name,
                value,
                C.active_format,
                is_global=False,
                blkitems=blkitems,
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

    def _sync_stroke_color_after_change(self, param_name: str):
        """颜色变更后的描边色状态同步（取色/右键应用共用）。

        srgb：手动指定轮廓颜色 → 置「自定义」标记，此后该块完全按手动值渲染，
        不再自动跟随文字反色（默认态无 UI，显式取色即成为自定义，无恢复场景）。
        frgb：字体颜色变更 → 描边色自动跟随文字反色即时刷新（黑字白边/白字黑边），
        无延迟（此前面板 swatch 需等选中态重载才更新）。
        """
        if param_name == "srgb":
            C.active_format.stroke_color_custom = True
        elif param_name == "frgb":
            fmt = C.active_format
            if fmt is not None and not fmt.stroke_color_custom:
                self.strokeColorPicker.setPickerColor(
                    fmt.effective_stroke_color(
                        auto_follow=C.pcfg.stroke_auto_follow
                    )
                )

    def onColorLabelChanged(self, is_valid=True):
        self.focusOnColorDialog = False
        if is_valid:
            sender: ColorPickerLabel = self.sender()
            rgb = sender.rgb()
            self.on_param_changed(sender.param_name, rgb)
            self._sync_stroke_color_after_change(sender.param_name)

    def on_apply_color(self, param_name, rgb):
        self.on_param_changed(param_name, rgb)
        self._sync_stroke_color_after_change(param_name)

    def onLineSpacingCtrlChanged(self, delta: int):
        if C.active_format.line_spacing_type == LineSpacingType.Distance:
            mul = 0.1
        else:
            mul = 0.01
        self.lineSpacingBox.setValue(self.lineSpacingBox.value() + delta * mul)

    def _on_text_style_commit(self, name: str, value):
        if name == "shadow_include_stroke":
            self._apply_shadow_include_stroke(value)
            return
        self.on_param_changed(name, value)
        self._update_textstyle_indicator()

    def _preview_style_param(self, name: str, value):
        """Live drag preview without an undo entry (rail Text Style dock).

        Global mode has no block to preview onto — commits write the
        global format anyway, so preview is a no-op there.
        """
        item = self.textblk_item
        if item is None:
            return
        if name == "opacity":
            item.setOpacity(value)
        elif name in SHADOW_PARAMS:
            item.setBGAttribute(name, value)
        elif name in GRADIENT_PARAMS:
            item.setGradientAttribute(name, value)
        else:
            self.on_param_changed(name, value)

    def _apply_shadow_include_stroke(self, include_stroke: bool):
        # shadow_include_stroke is project-wide (PS behavior): applies to
        # ALL text blocks on the current page, not just the selection.
        from .shared_widget import canvas as SW_canvas
        from .textitem import TextBlkItem

        for item in SW_canvas.items():
            if isinstance(item, TextBlkItem):
                item.setBGAttribute("shadow_include_stroke", include_stroke)
                item.update()
        self.global_format.shadow_include_stroke = include_stroke

    def _set_combo_mixed(self, combo: QComboBox, mixed: bool, current: str):
        """非可编辑下拉的混合态：插入禁用 "—" 占位项并选中。

        非混合时按普通文本回显；调用方自行负责 blockSignals。
        """
        # 清掉上次插入的占位项（禁用项不参与用户选择，但驻留会污染列表）
        for i in range(combo.count()):
            if (
                combo.itemText(i) == MIXED_PLACEHOLDER
                and not combo.model().item(i).isEnabled()
            ):
                combo.removeItem(i)
                break
        if mixed:
            combo.addItem(MIXED_PLACEHOLDER)
            model = combo.model()
            model.item(combo.count() - 1).setEnabled(False)
            combo.setCurrentText(MIXED_PLACEHOLDER)
        else:
            combo.setCurrentText(current)

    def set_active_format(
        self, font_format: FontFormat, multi_size=False, mixed: set = None
    ):
        C.active_format = font_format
        self.familybox.blockSignals(True)
        self.stylebox.blockSignals(True)  # 新增

        from utils.config import pcfg

        mixed = mixed or set()
        font_size = min(round(font_format.font_size, 1), pcfg.max_font_size)
        if int(font_size) == font_size:
            font_size = str(int(font_size))
        else:
            font_size = f"{font_size:.1f}"
        if multi_size or "font_size" in mixed:
            font_size += "+"
        self.fontsizebox.fcombobox.setCurrentText(font_size)
        # 旧数据可能存的是被归并隐藏的别名家族名（如字重变体/英文名），
        # 非可编辑下拉对不存在的名字 setCurrentText 是 no-op，会显示滞留，
        # 这里先映射到规范名再回显；混合家族显示 "—" 占位（禁用项）
        self._set_combo_mixed(
            self.familybox,
            "font_family" in mixed,
            shared.canonical_font_family(font_format.font_family),
        )

        # 回显 Style（face 为 font_weight 的派生显示缓存，直接按缓存名回显）
        styles = shared.FONT_STYLES.get(font_format.font_family, [])
        self.stylebox.clear()
        self.stylebox.addItems(styles)
        if "font_weight" in mixed:
            self._set_combo_mixed(self.stylebox, True, "")
        elif font_format._style_name and font_format._style_name in styles:
            self.stylebox.setCurrentText(font_format._style_name)
        else:
            idx = self.stylebox.findText("Regular")
            if idx < 0 and len(styles) > 0:
                idx = 0
            if idx >= 0:
                self.stylebox.setCurrentIndex(idx)
        self.colorPicker.setPickerColor(font_format.foreground_color())
        self.strokeColorPicker.setPickerColor(
            font_format.effective_stroke_color(
                auto_follow=C.pcfg.stroke_auto_follow
            )
        )
        self.strokeWidthBox.setValue(font_format.stroke_width)
        self.lineSpacingBox.setValue(font_format.line_spacing)
        self.letterSpacingBox.setValue(font_format.letter_spacing)
        # 可编辑数值下拉的混合态：显示 "—"（value() 解析失败回退旧值，
        # 不会把占位符当数值写回）
        for box, field in (
            (self.strokeWidthBox, "stroke_width"),
            (self.lineSpacingBox, "line_spacing"),
            (self.letterSpacingBox, "letter_spacing"),
        ):
            if field in mixed:
                box.setCurrentText(MIXED_PLACEHOLDER)
        self.verticalChecker.setChecked(font_format.vertical)
        self.romanAlignmentChecker.setChecked(
            font_format.standard_vertical_roman_alignment
        )
        self.formatBtnGroup.strikeBtn.setChecked(font_format.strikeout)
        self.formatBtnGroup.underlineBtn.setChecked(font_format.underline)
        self.formatBtnGroup.italicBtn.setChecked(font_format.italic)
        self.alignBtnGroup.setAlignment(font_format.alignment)
        if getattr(self, "textstyle_group", None) is not None:
            self.textstyle_group.set_from_format(font_format)

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
            face = active_text_style_label.fontfmt._style_name
            if not face:
                self.textstyle_panel.setTitle(self.global_fontfmt_str)
                return
            title = self.global_fontfmt_str + " - " + face
            valid_title = self.textstyle_panel.elidedText(title)
            self.textstyle_panel.setTitle(valid_title)

    def _reset_global_format(self):
        """重置默认格式：恢复 FontFormat 工厂默认（Affinity 有默认无重置
        的多年差评来源，见修复计划决策3）。"""
        self.global_format.merge(FontFormat(), compare=False)
        face_resolver.sync_face(self.global_format)
        if self.global_mode():
            self.set_active_format(self.global_format)
        self.update_text_style_label()
        self.set_globalfmt_title()

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
            # merge 非 compare 键会连带复制预设的 _style_name；face 是派生
            # 显示缓存，须与合并后的 weight/family/italic 重新同源
            face_resolver.sync_face(self.global_format)
            if self.global_mode() and len(updated_keys) > 0:
                self.set_active_format(self.global_format)
            self.set_globalfmt_title()
        else:
            if self.global_mode():
                self.set_globalfmt_title()

    def on_active_stylename_edited(self):
        if self.global_mode():
            self.set_globalfmt_title()

    def _reload_stylebox(self, family: str):
        self.stylebox.blockSignals(True)
        self.stylebox.clear()
        self.stylebox.addItems(shared.FONT_STYLES.get(family, []))
        self.stylebox.blockSignals(False)

    def on_familybox_changed(self, family: str):
        """家族切换：stylebox 重载后按字重真值派生 face 回显（决策5——
        每块按自身 (weight, italic) 映射新家族 face，缺失就近/回落）。"""
        self._reload_stylebox(family)
        self.apply_font_change(family_change=True)

    def on_fontstyle_changed(self, style: str):
        """Trigger format update when style changes"""
        self.apply_font_change()

    def apply_font_change(self, family_change: bool = False):
        """Unified entry point for applying Family/Style dropdown changes.

        字重真值化后 face（``_style_name``）是派生显示缓存：显式选 face
        时以 face 对应的 weight 入库；``QFontDatabase.weight`` 查不到
        （-1，别名家族/可变命名实例）时不再入库污染数据，保持当前
        weight 由渲染端按距离匹配兜底。
        """
        family = self.familybox.currentText()
        style = self.stylebox.currentText()

        if family not in shared.ALL_FONT_FAMILIES:
            return

        act_ffmt = self.global_format if self.global_mode() else C.active_format
        broadcast_weight = None
        if act_ffmt is not None:
            if family_change:
                # 家族切换：字重保持，face 按 (新家族, 当前weight, italic) 派生
                act_ffmt.font_family = family
                face_resolver.sync_face(act_ffmt)
                with QSignalBlocker(self.stylebox):
                    idx = self.stylebox.findText(act_ffmt._style_name)
                    if idx >= 0:
                        self.stylebox.setCurrentIndex(idx)
            elif style:
                weight = QFontDatabase.weight(family, style)
                if weight > 0:
                    act_ffmt.font_weight = fix_fontweight_qt(weight)
                    broadcast_weight = act_ffmt.font_weight
                # 查不到(-1)不入库：保持当前字重，face 交派生/weight 匹配
                act_ffmt._style_name = style
            else:
                # 样式列表为空（被精简/虚拟家族）：字重回默认
                act_ffmt.font_weight = None
                act_ffmt._style_name = ""
        # Then update font_family
        self.on_param_changed("font_family", family)
        if broadcast_weight is not None:
            # 显式选 face 引起的字重变更：走统一管道（引擎端同次派生 face）
            self.on_param_changed("font_weight", broadcast_weight)

    @staticmethod
    def _mixed_fields(items: list, active_fmt: FontFormat) -> set:
        """逐字段量化比较：与活动块不一致的字段集合（多选混合态）。"""
        others = [it.get_fontformat() for it in items if it is not items[-1]]
        mixed = set()
        for fname in _MIXED_CHECK_FIELDS:
            base = quantize_field(fname, getattr(active_fmt, fname, None))
            for fmt in others:
                if quantize_field(fname, getattr(fmt, fname, None)) != base:
                    mixed.add(fname)
                    break
        return mixed

    def _set_multi_selection(self, items: list, active_item=None):
        """多选镜像态：C.active_format = 活动块（默认最后选中）格式副本。

        镜像副本仅作显示与编辑载体（wrapper 写入），不整包回写块——
        块格式经文档编辑 + get_fontformat 回读落账。混合字段显示 "—"。
        """
        self.textblk_item = None
        self._active_multi_items = list(items)
        active = active_item if active_item is not None else items[-1]
        mirror = active.get_fontformat()
        # gradient 是块级数据层权威（get_fontformat 不回读 gradient）
        if hasattr(active.fontformat, "gradient_enabled"):
            mirror.gradient_enabled = active.fontformat.gradient_enabled
            mirror.gradient_start_color = active.fontformat.gradient_start_color
            mirror.gradient_end_color = active.fontformat.gradient_end_color
            mirror.gradient_angle = active.fontformat.gradient_angle
            mirror.gradient_size = active.fontformat.gradient_size
        mixed = self._mixed_fields(items, mirror)
        multi_size = not active.isEditing() and active.isMultiFontSize()
        self.set_active_format(mirror, multi_size, mixed)
        self.textstyle_panel.setTitle(f"TextBlock #{active.idx + 1}")

    def set_textblk_item(
        self,
        textblk_item: TextBlkItem = None,
        multi_select: bool = False,
        multi_items: list = None,
    ):
        """选中态 → 面单同步。

        * 多选（≥2）：``multi_items`` 传选中列表，优先判定（活动块取
          ``textblk_item``，缺省为最后选中项）；``multi_select=True`` 为
          旧签名兼容，内部取当前画布选中列表。
        * 单选：``textblk_item`` 非 None 且无多选列表。
        * 闲置：两者皆空 → 显示新块默认格式（``global_format``）。
        """
        # A selection transition is a transaction boundary for transform text.
        # Commit typed values against the old target list before replacing it.
        self.text_transform_editor.finish_pending_edits()
        if multi_items is None and multi_select:
            from ui import shared_widget as SW

            multi_items = SW.canvas.selected_text_items()
        if multi_items is not None and len(multi_items) < 2:
            # multi_select=True 但实际选中不足 2 → 回落单选/闲置语义
            multi_items = None

        if multi_items:
            transform_items = list(multi_items)
        elif textblk_item is not None:
            transform_items = [textblk_item]
        else:
            transform_items = []

        preserve_local_owner = False
        if multi_items:
            self._set_multi_selection(multi_items, textblk_item)
        elif textblk_item is None:
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
                # Store the current text block's format before switching to global.
                # 整包 deepcopy 回写仅保留单选→闲置路径：多选镜像副本不回写
                # （块格式经文档编辑 + 回读落账），避免与镜像双重写。
                if self.textblk_item is not None:
                    # Save all format properties including gradient state
                    self.textblk_item.fontformat = copy.deepcopy(C.active_format)
                    self.textblk_item = None
                self._active_multi_items = None
                self.set_active_format(self.global_format)
                self.set_globalfmt_title()

        else:
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
            self._active_multi_items = None
            multi_size = (
                not textblk_item.isEditing() and textblk_item.isMultiFontSize()
            )
            self.set_active_format(blk_fmt, multi_size)
            self.textstyle_panel.setTitle(f"TextBlock #{textblk_item.idx + 1}")
        self.text_transform_editor.replace_targets(transform_items)
        if transform_items:
            self.texttransform_panel.set_transform_items(transform_items)
        self._sync_annotation_controls()

    def _iter_docks(self):
        """Yield (key, launcher, dock) for the four rail docks.

        Launchers/docks are None until their ``install_*_launcher`` ran;
        ``getattr`` default keeps this safe on partially-built panels.
        """
        for key in ("annotation", "emphasis", "transform", "textstyle"):
            yield (
                key,
                getattr(self, f"{key}_launcher", None),
                getattr(self, f"{key}_dock", None),
            )

    def _close_other_docks(self, opener_key: str) -> None:
        """Only one rail dock shows at a time (PS sidebar behaviour).

        Opening one float closes every other open float so they never stack
        over each other.  The other dock hides through ``close_panel`` (its
        launcher unchecks itself via the normal ``closed`` signal path and
        its ``pcfg`` open-state key clears), so a later page-return reopens
        just the one still open.
        """
        for key, _launcher, dock in self._iter_docks():
            # "open" == not explicitly hidden (isVisible would be False for
            # a shown child whose top-level host isn't shown, e.g. in the
            # offscreen test harness).
            if key == opener_key or dock is None or dock.isHidden():
                continue
            dock.close_panel()

    def install_annotation_launcher(self, rail) -> None:
        """注解浮层入口：图标栏按钮 + RailDockPanel（内容=AnnotationFormatGroup）。

        替代原「Annotations」折叠胶囊；浮层展开在窄栏左侧的画布区、
        可拖拽/拉伸，不占用文本编辑区，仅图标或 × 关闭。浮层懒创建
        （创建时才能从 rail 解析主窗口宿主）。图标（ruby 点 + 正文
        行）角标在当前块存在注解时点亮（见 _update_annotation_indicator），
        开合状态记忆在 ``pcfg.annotation_dock_open``。
        """
        from ui.panel_rail import RailLauncherButton

        self.rail = rail
        self.annotation_launcher = RailLauncherButton("rail_annotation")
        self.annotation_launcher.setToolTip(self.tr("Annotations"))
        self.annotation_launcher.toggled.connect(
            self._on_annotation_launcher_toggled
        )
        rail.add_launcher(self.annotation_launcher)

    def install_transform_launcher(self, rail) -> None:
        """文本变换浮层入口（同注解浮层模式）。

        变换面板不是选中级作用域：无选中项时编辑全局格式
        （TextTransformEditSession 空 items 走 global_format），所以
        launcher 在全局模式保持可用、不做禁用。角标在当前块存在
        变换（变换栈非空或字形斜角非 0）时点亮。开合记忆在
        ``pcfg.transform_dock_open``。
        """
        from ui.panel_rail import RailLauncherButton

        self.rail = rail
        self.transform_launcher = RailLauncherButton("rail_transform")
        self.transform_launcher.setToolTip(self.tr("Text Transform"))
        self.transform_launcher.toggled.connect(
            self._on_transform_launcher_toggled
        )
        rail.add_launcher(self.transform_launcher)

    def _ensure_transform_dock(self):
        if self.transform_dock is None:
            from ui.custom_widget import RailDockPanel

            # Content is the panel itself, not its view_widget: the dock
            # header already carries the "Text Transform" title, so the
            # panel's own collapsible/fold title bar is dropped and the
            # scroll content fills the dock width directly (its width-sync
            # reflows the transform cards to whatever the dock is sized to).
            self.transform_dock = RailDockPanel(
                self.tr("Text Transform"),
                self.texttransform_panel,
                rail=self.rail,
                config_open="transform_dock_open",
            )
            # The transform section grids (2-3 columns of label+editor) need
            # real width; 230px is far too cramped, so pin a wider floor.
            # The user can still drag wider; no lower than this.
            self.transform_dock.setMinimumWidth(340)
            self.transform_dock.closed.connect(
                self._on_transform_dock_closed
            )
        return self.transform_dock

    def _on_transform_launcher_toggled(self, checked: bool):
        if self.transform_dock is None and not checked:
            return
        if checked:
            self._close_other_docks("transform")
            self._ensure_transform_dock().open_panel()
        elif self.transform_dock is not None:
            self.transform_dock.close_panel()

    def _on_transform_dock_closed(self):
        if self.transform_launcher is not None and self.transform_launcher.isChecked():
            with QSignalBlocker(self.transform_launcher):
                self.transform_launcher.setChecked(False)

    def _update_transform_indicator(self):
        """Rail icon corner dot while the block holds any transform."""
        item = self.textblk_item
        active = False
        if item is not None:
            fmt = item.blk.fontformat
            transform_active = bool(fmt.text_transform) or (
                fmt.glyph_slant_angle != 0.0
            )
            active = bool(transform_active)
        if self.transform_launcher is not None:
            self.transform_launcher.set_dot(active)
        if self.transform_dock is not None:
            title = self.tr("Text Transform")
            if active:
                title += " •"
            self.transform_dock.set_title(title)

    def install_emphasis_launcher(self, rail) -> None:
        """着重号浮层入口（同注解浮层模式，选中级作用域）。

        格式行里的着重号按钮迁到这里：launcher 图标用着重号标记本体，
        浮层内容为可见的标记/位置选择（EmphasisFormatGroup）。全局模式
        禁用（无当前块没有可作用的选中文本），角标在当前块带强调时点亮。
        开合记忆在 ``pcfg.emphasis_dock_open``。
        """
        from ui.panel_rail import RailLauncherButton

        self.rail = rail
        self.emphasis_launcher = RailLauncherButton("rail_emphasis")
        self.emphasis_launcher.setToolTip(self.tr("Emphasis Marks"))
        self.emphasis_launcher.toggled.connect(
            self._on_emphasis_launcher_toggled
        )
        rail.add_launcher(self.emphasis_launcher)

    def _ensure_emphasis_dock(self):
        if self.emphasis_dock is None:
            from ui.custom_widget import RailDockPanel

            self.emphasis_dock = RailDockPanel(
                self.tr("Emphasis Marks"),
                self.emphasis_group,
                rail=self.rail,
                config_open="emphasis_dock_open",
            )
            self.emphasis_dock.closed.connect(
                self._on_emphasis_dock_closed
            )
        return self.emphasis_dock

    def _on_emphasis_launcher_toggled(self, checked: bool):
        if self.emphasis_dock is None and not checked:
            return
        if checked:
            self._close_other_docks("emphasis")
            self._ensure_emphasis_dock().open_panel()
        elif self.emphasis_dock is not None:
            self.emphasis_dock.close_panel()

    def _on_emphasis_dock_closed(self):
        if self.emphasis_launcher is not None and self.emphasis_launcher.isChecked():
            with QSignalBlocker(self.emphasis_launcher):
                self.emphasis_launcher.setChecked(False)

    def _update_emphasis_indicator(self):
        """Rail icon corner dot while the block carries an emphasis mark."""
        item = self.textblk_item
        active = False
        if item is not None:
            active = item.emphasis_values()[0] != EMPHASIS_STYLES[0]
        if self.emphasis_launcher is not None:
            self.emphasis_launcher.set_dot(active)
        if self.emphasis_dock is not None:
            title = self.tr("Emphasis Marks")
            if active:
                title += " •"
            self.emphasis_dock.set_title(title)

    def install_textstyle_launcher(self, rail) -> None:
        """文本样式浮层入口（不透明度/阴影/渐变，同注解浮层模式）。

        图标暂借 rail_effects（层叠方片）：阶段 D 本浮层内容并入
        效果栈后，图标与开合记忆直接沿用。内容=TextStyleGroup；非选中级
        作用域：全局模式也可用（提交走
        on_param_changed 落地全局格式，与旧对话框一致）。角标在当前块
        带非默认样式（透明度≠1 / 阴影半径>0 / 阴影偏移非零 / 渐变开）
        时点亮。开合记忆在 ``pcfg.textstyle_dock_open``。
        """
        from ui.panel_rail import RailLauncherButton

        self.rail = rail
        self.textstyle_launcher = RailLauncherButton("rail_effects")
        self.textstyle_launcher.setToolTip(self.tr("Text Style"))
        self.textstyle_launcher.toggled.connect(
            self._on_textstyle_launcher_toggled
        )
        rail.add_launcher(self.textstyle_launcher)

    def _ensure_textstyle_dock(self):
        if self.textstyle_dock is None:
            from ui.custom_widget import RailDockPanel

            self.textstyle_dock = RailDockPanel(
                self.tr("Text Style"),
                self.textstyle_group,
                rail=self.rail,
                config_open="textstyle_dock_open",
            )
            self.textstyle_dock.closed.connect(
                self._on_textstyle_dock_closed
            )
        return self.textstyle_dock

    def _on_textstyle_launcher_toggled(self, checked: bool):
        if self.textstyle_dock is None and not checked:
            return
        if checked:
            self._close_other_docks("textstyle")
            self._ensure_textstyle_dock().open_panel()
        elif self.textstyle_dock is not None:
            self.textstyle_dock.close_panel()

    def _on_textstyle_dock_closed(self):
        if self.textstyle_launcher is not None and self.textstyle_launcher.isChecked():
            with QSignalBlocker(self.textstyle_launcher):
                self.textstyle_launcher.setChecked(False)

    def _update_textstyle_indicator(self):
        """Rail icon corner dot while the block carries a non-default style."""
        item = self.textblk_item
        active = False
        if item is not None:
            fmt = item.blk.fontformat
            active = (
                fmt.opacity != 1.0
                or fmt.shadow_radius > 0.0
                or fmt.shadow_offset != [0.0, 0.0]
                or fmt.gradient_enabled
            )
        if self.textstyle_launcher is not None:
            self.textstyle_launcher.set_dot(active)
        if self.textstyle_dock is not None:
            title = self.tr("Text Style")
            if active:
                title += " •"
            self.textstyle_dock.set_title(title)

    def _ensure_annotation_dock(self):
        if self.annotation_dock is None:
            from ui.custom_widget import RailDockPanel

            self.annotation_dock = RailDockPanel(
                self.tr("Annotations"),
                self.annotation_group,
                rail=self.rail,
                config_open="annotation_dock_open",
            )
            self.annotation_dock.closed.connect(
                self._on_annotation_dock_closed
            )
        return self.annotation_dock

    def _on_annotation_launcher_toggled(self, checked: bool):
        if self.annotation_dock is None and not checked:
            return
        if checked:
            self._close_other_docks("annotation")
            self._ensure_annotation_dock().open_panel()
        elif self.annotation_dock is not None:
            self.annotation_dock.close_panel()

    def _on_annotation_dock_closed(self):
        if self.annotation_launcher is not None and self.annotation_launcher.isChecked():
            with QSignalBlocker(self.annotation_launcher):
                self.annotation_launcher.setChecked(False)

    def on_textpanel_visibility(self, visible: bool):
        """嵌字页显隐时同步浮层：页面隐藏则随隐（保留开合状态与勾选）。"""
        docks = (
            (
                self.annotation_launcher,
                self.annotation_dock,
                self._ensure_annotation_dock,
                "annotation_dock_open",
            ),
            (
                self.emphasis_launcher,
                self.emphasis_dock,
                self._ensure_emphasis_dock,
                "emphasis_dock_open",
            ),
            (
                self.transform_launcher,
                self.transform_dock,
                self._ensure_transform_dock,
                "transform_dock_open",
            ),
            (
                self.textstyle_launcher,
                self.textstyle_dock,
                self._ensure_textstyle_dock,
                "textstyle_dock_open",
            ),
        )
        if visible:
            # Mutual exclusion: at most one dock is open-state at a time, so
            # reopen the first eligible one and stop — never stack several.
            for launcher, _dock, ensure, config_open in docks:
                if (
                    launcher is not None
                    and launcher.isEnabled()
                    and (launcher.isChecked() or getattr(C.pcfg, config_open))
                ):
                    ensure().open_panel()
                    break
        else:
            for _launcher, dock, _ensure, _config_open in docks:
                if dock is not None and not dock.isHidden():
                    dock.hide_keep_state()

    def _sync_annotation_controls(self):
        """Restore the annotation controls from the active text item.

        The annotation dock is selection-scoped like the old capsule: in
        global mode the rail icon is disabled and the dock (if open) stays
        open with grayed content — per user requirement the float never
        closes itself, only the rail icon or its × button does.  The rail
        icon carries a corner dot while the current block has any
        annotation active, so the collapsed rail still hints at content.

        Called on every selection transition; during text editing the engine
        item re-reports its own state, so a per-keystroke sync is deferred to
        node 3 panel consolidation.
        """
        item = self.textblk_item
        group = self.annotation_group
        has_item = item is not None
        group.setEnabled(has_item)
        self.tcyChecker.setEnabled(has_item)
        self.emphasis_group.setEnabled(has_item)
        if self.annotation_launcher is not None:
            self.annotation_launcher.setEnabled(has_item)
        if self.emphasis_launcher is not None:
            self.emphasis_launcher.setEnabled(has_item)
        self._update_annotation_indicator()
        self._update_emphasis_indicator()
        self._update_textstyle_indicator()
        self._update_transform_indicator()
        if item is None:
            return
        with QSignalBlocker(group), QSignalBlocker(self.tcyChecker):
            self.emphasis_group.set_values(*item.emphasis_values())
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

    def _update_annotation_indicator(self):
        """Rail icon corner dot + dock title while the block has annotations.

        Emphasis has its own launcher and is reported by
        ``_update_emphasis_indicator``; this indicator covers the
        remaining annotation family (tate-chu-yoko, oldstyle, ligatures,
        ruby).
        """
        item = self.textblk_item
        active = False
        if item is not None:
            active = (
                item.tate_chu_yoko_enabled()
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
        if self.annotation_launcher is not None:
            self.annotation_launcher.set_dot(active)
        if self.annotation_dock is not None:
            title = self.tr("Annotations")
            if active:
                title += " •"
            self.annotation_dock.set_title(title)

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
        # 块注解变化后即时刷新 rail 角标（不等待下次选择同步）
        self._update_annotation_indicator()
        self._update_emphasis_indicator()

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
