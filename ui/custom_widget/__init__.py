from qtpy.QtWidgets import QHBoxLayout, QVBoxLayout

from .checkbox import AlignmentChecker, ConfigCheckBox, QFontChecker
from .clock_dial import ClockDial
from .color_button import ColorSwatchBtn
from .color_picker import ColorPickerDialog
from .combobox import (
    ComboBox,
    ConfigComboBox,
    ParamComboBox,
    SizeComboBox,
    SmallComboBox,
    SmallSizeComboBox,
)
from .flow_layout import FlowLayout
from .group_frame import GroupFrame
from .helper import borderColor, isDarkTheme, themeColor, widgetBackgroundColor
from .label import (
    CheckableLabel,
    ClickableLabel,
    ColorPickerLabel,
    ConfigClickableLabel,
    FadeLabel,
    ParamNameLabel,
    SizeControlLabel,
    SmallColorPickerLabel,
    SmallParamLabel,
    SmallSizeControlLabel,
    TextCheckerLabel,
)
from .message import (
    FrameLessMessageBox,
    ImgtransProgressMessageBox,
    MessageBox,
    ProgressMessageBox,
    TaskProgressBar,
)
from .push_button import ExpandingToolButton, NoBorderPushBtn
from .scroll_bar import ConfigScrollBar
from .scrollbar import ScrollBar
from .screen_picker import pick_screen_color
from .section_header import ConfigSectionHeader
from .slider import PaintQSlider, RangeSlider
from .spinbox import NoArrowsDoubleSpinBox, NoArrowsSpinBox
from .text_input import ConfigLineEdit, ConfigTextEdit
from .view_panel import (
    ExpandLabel,
    PanelArea,
    PanelAreaContent,
    PanelGroupBox,
    ViewWidget,
)
from .widget import SeparatorWidget, Widget


def combobox_with_label(
    param_name: str = None,
    size="small",
    options=None,
    parent=None,
    scrollWidget=None,
    label_alignment=None,
    vertical_layout=False,
    editable=False,
    label=False,
):
    combobox_cls = SmallComboBox if size == "small" else ComboBox
    combobox = combobox_cls(options=options, parent=parent, scrollWidget=scrollWidget)
    combobox.setEditable(editable)
    if label is None:
        label_cls = SmallParamLabel if size == "small" else ParamNameLabel
        label = label_cls(param_name=param_name, alignment=label_alignment)
    if vertical_layout:
        layout = QVBoxLayout()
    else:
        layout = QHBoxLayout()
    layout.addWidget(label)
    layout.addWidget(combobox)
    return combobox, label, layout
