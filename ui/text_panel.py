import copy

from qtpy.QtCore import QSignalBlocker, Qt, Signal
from qtpy.QtGui import QFont, QTextCursor
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
)

from utils import config as C
from utils import shared
from utils.fontformat import FontFormat, LineSpacingType, fix_fontweight_qt

from . import funcmaps as FM
from .custom_widget import (
    AlignmentChecker,
    ColorPickerLabel,
    GroupFrame,
    QFontChecker,
    SizeComboBox,
    SizeControlLabel,
    Widget,
)
from .text_advanced_format import TextStyleEntryButton
from .text_style_presets import TextStylePresetPanel
from .text_engine.annotations import (
    EMPHASIS_GLYPHS,
    EMPHASIS_POSITIONS,
    EMPHASIS_STYLES,
    LIGATURE_AXIS_VALUES,
    LIGATURE_COMMON,
    LIGATURE_CONTEXTUAL,
    LIGATURE_DISCRETIONARY,
    RUBY_POSITIONS,
    RUBY_TYPES,
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


class FormatGroupBtn(QFrame):
    param_changed = Signal(str, bool)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.boldBtn = QFontChecker(self)
        self.boldBtn.setObjectName("FontBoldChecker")
        self.boldBtn.clicked.connect(self.setBold)
        self.italicBtn = QFontChecker(self)
        self.italicBtn.setObjectName("FontItalicChecker")
        self.italicBtn.clicked.connect(self.setItalic)
        self.underlineBtn = QFontChecker(self)
        self.underlineBtn.setObjectName("FontUnderlineChecker")
        self.underlineBtn.clicked.connect(self.setUnderline)
        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.boldBtn)
        hlayout.addWidget(self.italicBtn)
        hlayout.addWidget(self.underlineBtn)
        hlayout.setSpacing(0)
        hlayout.setContentsMargins(0, 0, 0, 0)

    def setBold(self):
        self.param_changed.emit("bold", self.boldBtn.isChecked())

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
    """In-place annotation controls (emphasis / Ruby / tate-chu-yoko /
    ligatures / oldstyle numerals).

    Node 2c first-pass UI: every change emits a named signal so the panel can
    route it to the engine ``TextBlkItem`` setter that owns the undo document
    transaction.  ``set_*`` helpers restore widget state without emitting.
    """

    annotation_changed = Signal(str, object)
    ruby_remove = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        label_font = self.font()
        label_font.setPointSizeF(shared.CONFIG_FONTSIZE_CONTENT * 0.9)

        # ── emphasis ────────────────────────────────────────────────
        self.emphasisLabel = QLabel(self.tr("Emphasis"))
        self.emphasisLabel.setFont(label_font)
        self.emphasisBox = QComboBox(self)
        self.emphasisBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        for style in EMPHASIS_STYLES:
            glyph = EMPHASIS_GLYPHS.get(style, "")
            text = style if not glyph else f"{glyph} {style}"
            self.emphasisBox.addItem(text, style)
        self.emphasisBox.setToolTip(self.tr("Emphasis mark style"))
        self.emphasisPosBox = QComboBox(self)
        self.emphasisPosBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        for position in EMPHASIS_POSITIONS:
            self.emphasisPosBox.addItem(position, position)
        self.emphasisPosBox.setToolTip(self.tr("Emphasis mark position"))
        emphasis_hlayout = QHBoxLayout()
        emphasis_hlayout.addWidget(self.emphasisLabel)
        emphasis_hlayout.addWidget(self.emphasisBox, 1)
        emphasis_hlayout.addWidget(self.emphasisPosBox)
        emphasis_hlayout.setSpacing(4)
        emphasis_hlayout.setContentsMargins(0, 0, 0, 0)

        # ── Ruby ────────────────────────────────────────────────────
        self.rubyLabel = QLabel(self.tr("Ruby"))
        self.rubyLabel.setFont(label_font)
        self.rubyTypeBox = QComboBox(self)
        for ruby_type in RUBY_TYPES:
            self.rubyTypeBox.addItem(ruby_type, ruby_type)
        self.rubyEdit = QLineEdit(self)
        self.rubyEdit.setPlaceholderText(self.tr("Reading"))
        self.rubyPosBox = QComboBox(self)
        for position in RUBY_POSITIONS:
            self.rubyPosBox.addItem(position, position)
        self.rubyApplyBtn = QPushButton(self.tr("Apply"), self)
        self.rubyRemoveBtn = QPushButton(self.tr("Remove"), self)
        ruby_hlayout = QHBoxLayout()
        ruby_hlayout.addWidget(self.rubyLabel)
        ruby_hlayout.addWidget(self.rubyTypeBox)
        ruby_hlayout.addWidget(self.rubyEdit, 1)
        ruby_hlayout.addWidget(self.rubyPosBox)
        ruby_hlayout.addWidget(self.rubyApplyBtn)
        ruby_hlayout.addWidget(self.rubyRemoveBtn)
        ruby_hlayout.setSpacing(4)
        ruby_hlayout.setContentsMargins(0, 0, 0, 0)

        # ── tate-chu-yoko ───────────────────────────────────────────
        self.tcyChecker = QFontChecker(self)
        self.tcyChecker.setObjectName("FontTateChuYokoChecker")
        self.tcyChecker.setText(self.tr("Tate-chu-yoko"))
        tcy_hlayout = QHBoxLayout()
        tcy_hlayout.addWidget(self.tcyChecker)
        tcy_hlayout.addStretch()
        tcy_hlayout.setContentsMargins(0, 0, 0, 0)

        # ── ligature axes ───────────────────────────────────────────
        self.ligatureLabel = QLabel(self.tr("Ligatures"))
        self.ligatureLabel.setFont(label_font)
        self.ligatureBoxes = {}
        for axis, tooltip in (
            (LIGATURE_COMMON, self.tr("Common ligatures")),
            (LIGATURE_DISCRETIONARY, self.tr("Discretionary ligatures")),
            (LIGATURE_CONTEXTUAL, self.tr("Contextual alternates")),
        ):
            box = QComboBox(self)
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents
            )
            for state in LIGATURE_AXIS_VALUES:
                box.addItem(state, state)
            box.setToolTip(tooltip)
            self.ligatureBoxes[axis] = box
        ligature_hlayout = QHBoxLayout()
        ligature_hlayout.addWidget(self.ligatureLabel)
        for axis, box in self.ligatureBoxes.items():
            ligature_hlayout.addWidget(box)
        ligature_hlayout.addStretch()
        ligature_hlayout.setSpacing(4)
        ligature_hlayout.setContentsMargins(0, 0, 0, 0)

        # ── oldstyle numerals ───────────────────────────────────────
        self.onumLabel = QLabel(self.tr("Oldstyle nums"))
        self.onumLabel.setFont(label_font)
        self.onumBox = QComboBox(self)
        for state in LIGATURE_AXIS_VALUES:
            self.onumBox.addItem(state, state)
        self.onumBox.setToolTip(self.tr("Oldstyle figures"))
        onum_hlayout = QHBoxLayout()
        onum_hlayout.addWidget(self.onumLabel)
        onum_hlayout.addWidget(self.onumBox)
        onum_hlayout.addStretch()
        onum_hlayout.setSpacing(4)
        onum_hlayout.setContentsMargins(0, 0, 0, 0)

        vlayout = QVBoxLayout(self)
        vlayout.addLayout(emphasis_hlayout)
        vlayout.addLayout(ruby_hlayout)
        vlayout.addLayout(tcy_hlayout)
        vlayout.addLayout(ligature_hlayout)
        vlayout.addLayout(onum_hlayout)
        vlayout.setSpacing(4)
        vlayout.setContentsMargins(0, 0, 0, 0)

        self.emphasisBox.currentIndexChanged.connect(self._emit_emphasis)
        self.emphasisPosBox.currentIndexChanged.connect(self._emit_emphasis)
        self.tcyChecker.toggled.connect(
            lambda checked: self.annotation_changed.emit("tcy", checked)
        )
        for axis, box in self.ligatureBoxes.items():
            box.currentIndexChanged.connect(
                lambda _index, a=axis, b=box: self.annotation_changed.emit(
                    "ligature", (a, b.currentData())
                )
            )
        self.onumBox.currentIndexChanged.connect(
            lambda _index: self.annotation_changed.emit(
                "onum", self.onumBox.currentData()
            )
        )
        self.rubyApplyBtn.clicked.connect(self._emit_ruby)
        self.rubyRemoveBtn.clicked.connect(self.ruby_remove)

    def _emit_emphasis(self):
        self.annotation_changed.emit(
            "emphasis", (self.emphasisBox.currentData(), self.emphasisPosBox.currentData())
        )

    def _emit_ruby(self):
        self.annotation_changed.emit(
            "ruby",
            (
                self.rubyTypeBox.currentData(),
                self.rubyEdit.text(),
                self.rubyPosBox.currentData(),
            ),
        )

    def set_emphasis(self, style: str, position: str) -> None:
        with QSignalBlocker(self.emphasisBox), QSignalBlocker(
            self.emphasisPosBox
        ):
            index = self.emphasisBox.findData(style)
            if index >= 0:
                self.emphasisBox.setCurrentIndex(index)
            index = self.emphasisPosBox.findData(position)
            if index >= 0:
                self.emphasisPosBox.setCurrentIndex(index)

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

    def set_tcy(self, enabled: bool) -> None:
        with QSignalBlocker(self.tcyChecker):
            self.tcyChecker.setChecked(enabled)

    def set_ligature(self, axis: str, state: str) -> None:
        box = self.ligatureBoxes.get(axis)
        if box is None:
            return
        with QSignalBlocker(box):
            index = box.findData(state)
            if index >= 0:
                box.setCurrentIndex(index)

    def set_onum(self, state: str) -> None:
        with QSignalBlocker(self.onumBox):
            index = self.onumBox.findData(state)
            if index >= 0:
                self.onumBox.setCurrentIndex(index)


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

        FONTFORMAT_SPACING = 5

        vl0 = QVBoxLayout()
        vl0.addWidget(self.textstyle_panel.view_widget)
        # 样式入口按钮：不透明度/行距/阴影/渐变 统一收进 Text Style 对话框
        vl0.addWidget(self.text_style_btn)
        vl0.addWidget(self.texttransform_panel.view_widget)
        vl0.setSpacing(0)
        vl0.setContentsMargins(0, 0, 0, 0)
        hl1_font = QHBoxLayout()
        hl1_font.addWidget(self.familybox, 3)  # 字体框占绝大部分伸缩空间
        hl1_font.addWidget(self.stylebox)  # 字重框按内容自适应
        hl1_font.setSpacing(4)
        hl1_font.setContentsMargins(0, 2, 0, 0)
        hl1_size = QHBoxLayout()
        hl1_size.addWidget(self.colorPicker)
        hl1_size.addWidget(self.fontsizebox)
        hl1_size.addWidget(self.lineSpacingLabel)
        hl1_size.addWidget(self.lineSpacingBox)
        hl1_size.addWidget(self.letterSpacingLabel)
        hl1_size.addWidget(self.letterSpacingBox)
        hl1_size.addStretch()  # 防止控件被水平拉伸分散，保持紧凑靠左
        hl1_size.setSpacing(4)
        hl1_size.setContentsMargins(0, 2, 0, 0)
        hl2 = QHBoxLayout()
        hl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl2.addWidget(self.alignBtnGroup)
        hl2.addWidget(self.formatBtnGroup)
        hl2.addWidget(self.verticalChecker)
        hl2.setSpacing(FONTFORMAT_SPACING)
        hl2.setContentsMargins(0, 0, 0, 0)
        hl3 = QHBoxLayout()
        hl3.setAlignment(Qt.AlignmentFlag.AlignLeft)
        hl3.addLayout(stroke_hlayout)
        hl3.setContentsMargins(3, 0, 3, 0)
        hl3.setSpacing(13)
        # vl0（预设+高级面板）用 GroupFrame 包裹，标题与内容共享同一边框
        vl0_frame = GroupFrame(self)
        vl0_layout_inner = QVBoxLayout(vl0_frame)
        vl0_layout_inner.setContentsMargins(6, 0, 6, 0)
        vl0_layout_inner.addLayout(vl0)
        self.vlayout.addWidget(vl0_frame)

        # 合并行：字体行 + 颜色/字号/间距行 → 一个胶囊
        hl1_merged = QVBoxLayout()
        hl1_merged.addLayout(hl1_font)
        hl1_merged.addLayout(hl1_size)
        hl1_merged.setSpacing(0)
        hl1_merged.setContentsMargins(0, 0, 0, 0)

        # 合并行：加粗斜体行 + 轮廓行 → 一个胶囊，行间保留 4px 间距
        hl2_merged = QVBoxLayout()
        hl2_merged.addLayout(hl2)
        hl2_merged.addLayout(hl3)
        hl2_merged.setSpacing(4)
        hl2_merged.setContentsMargins(0, 0, 0, 0)

        # 用 GroupFrame 包裹各行，提供圆角主题边框
        for hl in [hl1_merged, hl2_merged]:
            frame = GroupFrame(self)
            fl = QVBoxLayout(frame)
            pad = 4
            fl.setContentsMargins(6, pad, 6, pad)
            fl.addLayout(hl)
            self.vlayout.addWidget(frame)

        # 注解分区（节点 2c 初版）：着重号/Ruby/縦中横/连字/旧式数字。
        # 操作目标是文字块内部字符格式，经引擎 TextBlkItem 注解 setter 落地。
        self.annotation_group = AnnotationFormatGroup(self)
        annotation_frame = GroupFrame(self)
        annotation_layout = QVBoxLayout(annotation_frame)
        annotation_layout.setContentsMargins(6, 4, 6, 4)
        annotation_layout.addWidget(self.annotation_group)
        self.vlayout.addWidget(annotation_frame)
        self.annotation_group.annotation_changed.connect(
            self._on_annotation_changed
        )
        self.annotation_group.ruby_remove.connect(self._on_ruby_remove)
        self.annotation_group.setEnabled(False)

        self.vlayout.setContentsMargins(0, 0, 0, 0)
        self.vlayout.setSpacing(4)

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
        self.formatBtnGroup.boldBtn.setChecked(font_format.bold)
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

            # Keep the bold button in sync
            self.formatBtnGroup.boldBtn.setChecked(act_ffmt.bold)

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

        Called on every selection transition; during text editing the engine
        item re-reports its own state, so a per-keystroke sync is deferred to
        node 3 panel consolidation.
        """
        item = self.textblk_item
        group = self.annotation_group
        if item is None:
            group.setEnabled(False)
            return
        group.setEnabled(True)
        with QSignalBlocker(group):
            style, position = item.emphasis_values()
            group.set_emphasis(style, position)
            group.set_tcy(item.tate_chu_yoko_enabled())
            for axis in (
                LIGATURE_COMMON,
                LIGATURE_DISCRETIONARY,
                LIGATURE_CONTEXTUAL,
            ):
                group.set_ligature(axis, item.ligature_axis_value(axis))
            group.set_onum(item.oldstyle_nums_value())
            ruby_type, text, position, enabled = item.ruby_editor_values()
            group.set_ruby(ruby_type, text, position, enabled)

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
                self.annotation_group.set_tcy(item.tate_chu_yoko_enabled())
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
