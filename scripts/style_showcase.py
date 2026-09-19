"""控件样式展示台（人工目视工具）。

三个用途：
  1. 输入类对照 —— Qt 原生类 vs ui/custom_widget 封装类并排对比。
     左右一样的行 = 全局 QSS 兜底；右边明显不同 = 类名选择器，
     代码里必须用封装类（详见 docs/基础速查/打包控件功能使用说明.md
     「样式生效机制：全局兜底 vs 类名选择器」一节）。
  2. 控件一览 —— 按「用在哪个面板」分区的全部控件，含 ui/custom_widget
     封装类与应用层复合控件（配置面板 / 模块参数 / 变换面板 / 效果栈 …）。
     每行带样式来源徽章（类名 / objectName / 自绘 / 内联 / 全局兜底 /
     无规则）与 `路径::符号`，右上角一键复制，方便直接指定优化目标。
  3. 排查开关 —— 搜索框 + 左侧目录跳转、样式来源筛选、亮暗主题切换、
     状态矩阵（正常 / 禁用 / 悬停 / 聚焦）。

维护方式：新增控件时在 `_sections()` 对应分区的 rows 列表里追加一行
`Row(说明, "路径::符号", 工厂函数)`；无法离线实例化的控件登记到文件中部
`EXCLUDED` 字典（会渲染成「未纳入展示」分区）。`tests/test_showcase_coverage.py`
会比对 `ui/custom_widget/__init__.py` 导出清单与这里的展示/排除名单，
新增导出而没登记即测试失败。

用法（仓库根目录）：
    ./ballontrans_pylibs_win/python.exe scripts/style_showcase.py
    ./ballontrans_pylibs_win/python.exe scripts/style_showcase.py --selftest
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # QSS url(icons/...) 相对 cwd


def _reexec_bundled_python():
    """qtpy 只装在便携环境里：用系统 Python/双击启动时自动切便携解释器重跑。

    展示台必须反映项目真实样式，只要便携解释器存在就一律用它，
    避免系统 Python 碰巧装了 qtpy（PyQt5）导致样式观感失真。
    """
    bundled = os.path.join(ROOT, "ballontrans_pylibs_win", "python.exe")
    if not os.path.isfile(bundled):
        return  # 非便携布局，按当前解释器继续
    if os.path.realpath(sys.executable) == os.path.realpath(bundled):
        return
    raise SystemExit(subprocess.call([bundled, os.path.abspath(__file__)] + sys.argv[1:]))


_reexec_bundled_python()

try:
    from qtpy.QtCore import Qt, QTimer
    from qtpy.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFrame,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSpinBox,
        QTabWidget,
        QTextEdit,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError:
    sys.exit("未找到 qtpy：请用仓库便携解释器运行"
             "（ballontrans_pylibs_win/python.exe scripts/style_showcase.py）")


# ── 样式来源探测 ────────────────────────────────────────────────

_BADGE_COLORS = {
    "类名": "#5dade2",
    "objectName": "#48c9b0",
    "自绘": "#e67e22",
    "内联": "#d68910",
    "全局兜底": "#95a5a6",
    "无规则": "#e74c3c",
}

_qss_cache = ""


def _qss_text():
    global _qss_cache
    if not _qss_cache:
        with open(os.path.join(ROOT, "config", "stylesheet.css"), encoding="utf-8") as f:
            _qss_cache = f.read()
    return _qss_cache


def _has_selector(token):
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", _qss_text()) is not None


def _is_self_painted(cls):
    for base in cls.__mro__:
        if base.__module__.split(".")[0] in ("PyQt5", "PyQt6", "PySide2", "PySide6"):
            continue
        if "paintEvent" in base.__dict__:
            return True
    return False


def _style_source(widget):
    """这个控件的样式到底从哪来？决定该改 QSS 还是改代码。"""
    cls_name = widget.metaObject().className()
    if _has_selector(cls_name):
        return "类名"
    obj_name = widget.objectName()
    if obj_name and _has_selector("#" + obj_name):
        return "objectName"
    if _is_self_painted(type(widget)):
        return "自绘"
    if widget.styleSheet():
        return "内联"
    base = next((c.__name__ for c in type(widget).__mro__ if c.__name__.startswith("Q")), "")
    if base and _has_selector(base):
        return "全局兜底"
    return "无规则"


class Row:
    """展示台的一行。symbol 形如 ui/custom_widget/spinbox.py::NoArrowsSpinBox。"""

    __slots__ = ("name", "symbol", "factory", "wide")

    def __init__(self, name, symbol, factory, wide=False):
        self.name = name
        self.symbol = symbol
        self.factory = factory
        self.wide = wide


# ── 无法离线展示的控件（渲染成「未纳入展示」分区）────────────────
# 值 = 原因；键需与 ui/custom_widget/__init__.py 导出名一致（测试会比对）。
EXCLUDED = {
    "ColorPickerDialog": "模态对话框；点 ColorPickerLabel / SmallColorPickerLabel 即可看到",
    "MODE_CARD": "模式常量（RowTable 卡片模式），非控件；RowTable 卡片行即此模式",
    "MODE_TABLE": "模式常量（RowTable 表格模式），非控件；RowTable 表格行即此模式",
    "MessageBox": "模态消息框，需宿主与交互",
    "FrameLessMessageBox": "无边框模态消息框，需宿主与交互",
    "ProgressMessageBox": "模态进度框，需任务流程驱动",
    "ImgtransProgressMessageBox": "模态四管线进度框，需任务流程驱动",
    "NotificationCenter": "需画布 attach 后才能定位锚点",
    "notification": "NotificationCenter 的模块级单例，非控件",
    "RailDockPanel": "需主窗口宿主与窄栏锚点",
    "FloatDropPanel": "需主窗口宿主，打开时惰性解析 centralWidget",
    "PanelArea": "需项目面板的 config/expand 字段与全局展开管理器",
    "ViewWidget": "需 PanelArea 注册与配置字段",
    "PanelAreaContent": "仅作为 PanelArea/ViewWidget 的内容容器存在",
    "pick_screen_color": "需交互取色（全屏覆盖 + 放大镜）",
    "combobox_with_label": "返回 (控件, 标签, 布局) 三元组，非单一控件",
    "WidePopupComboMixin": "混入类，非控件",
    "themeColor": "工具函数（取主题色），非控件",
    "borderColor": "工具函数（取边框色），非控件",
    "isDarkTheme": "工具函数（判断当前主题），非控件",
    "widgetBackgroundColor": "工具函数（取控件底色），非控件",
}


# ── 通用工厂 ────────────────────────────────────────────────────

def _label(text, color="gray", size=12):
    lab = QLabel(text)
    lab.setStyleSheet(f"background: transparent; color: {color}; font-size: {size}px;")
    return lab


def _config_combo(options):
    from ui.custom_widget import ConfigComboBox
    return ConfigComboBox(options=options)


def _icon_checker(cls, object_name):
    def make():
        cb = cls()
        cb.setObjectName(object_name)
        return cb
    return make


def _scroll_area_bar(orient):
    """画布用的淡出滚动条需要 QAbstractScrollArea 宿主。"""
    from ui.custom_widget import ScrollBar
    area = QScrollArea()
    area.resize(220, 90)
    area.setWidgetResizable(True)
    holder = QWidget()
    holder.setMinimumSize(600, 400)
    area.setWidget(holder)
    bar = ScrollBar(orient, area, fadeout=True)
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(area)
    box._showcase_probe = bar
    return box


# ── 分区数据 ────────────────────────────────────────────────────

def _sections():
    from ui.custom_widget import (
        AlignmentChecker,
        CheckableLabel,
        ClickableLabel,
        ClockDial,
        ColorPickerLabel,
        ColorSwatchBtn,
        ComboBox,
        ConfigCheckBox,
        ConfigClickableLabel,
        ConfigComboBox,
        ConfigLineEdit,
        ConfigScrollBar,
        ConfigSectionHeader,
        ConfigTextEdit,
        ExpandingToolButton,
        ExpandLabel,
        FlowLayout,
        GroupFrame,
        MODE_CARD,
        MODE_TABLE,
        NoArrowsDoubleSpinBox,
        NoArrowsSpinBox,
        NoBorderPushBtn,
        PageProgressRangeBar,
        PageRangeProgressWidget,
        PageRangeSpinBox,
        PaintQSlider,
        PanelGroupBox,
        ParamComboBox,
        ParamNameLabel,
        QFontChecker,
        RangeSlider,
        RowTable,
        SeparatorWidget,
        SizeComboBox,
        SizeControlLabel,
        SmallColorPickerLabel,
        SmallComboBox,
        SmallParamLabel,
        SmallSizeComboBox,
        SmallSizeControlLabel,
        TaskProgressBar,
        TextCheckerLabel,
        Widget,
    )

    def combo(options):
        cb = ConfigComboBox()
        cb.addItems(options)
        return cb

    def hscrollbar():
        sb = ConfigScrollBar()
        sb.setOrientation(Qt.Orientation.Horizontal)
        sb.setRange(0, 100)
        sb.setPageStep(10)
        sb.setFixedWidth(180)
        return sb

    def clock_dial():
        d = ClockDial(compact=True)
        d.setFixedSize(120, 120)
        return d

    def group_frame():
        gf = GroupFrame()
        lay = QVBoxLayout(gf)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(QLabel("GroupFrame 内容"))
        return gf

    def separator():
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(2, 6, 2, 6)
        lay.addWidget(QLabel("上方"))
        lay.addWidget(SeparatorWidget())
        lay.addWidget(QLabel("下方"))
        return frame

    def widget_base():
        w = Widget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(QLabel("Widget（WA_StyledBackground 底色生效）"))
        return w

    def flow_layout_demo():
        holder = Widget()
        flow = FlowLayout(holder)
        flow.setSpacing(6)
        for i in range(9):
            flow.addWidget(QPushButton(f"标签 {i + 1}"))
        holder.setMinimumWidth(320)
        return holder

    def config_button():
        btn = QPushButton("配置按钮")
        btn.setObjectName("ConfigButton")
        return btn

    def progress_bar():
        bar = TaskProgressBar("正在检测文字…")
        bar.updateProgress(40)
        return bar

    _demo_pages = ["%03d.jpg" % i for i in range(1, 41)]
    _demo_finished = [True] * 11 + [False] * 6 + [True] * 4 + [False] * 19

    def page_range_spin():
        sb = PageRangeSpinBox()
        sb.setRange(1, len(_demo_pages))
        sb.setValue(7)
        sb.setFixedWidth(82)
        return sb

    def page_range_bar():
        bar = PageProgressRangeBar(_demo_pages)
        bar.set_finished_pages(_demo_finished)
        bar.set_range(3, 26, emit=False)
        return bar

    def page_range_progress():
        holder = PageRangeProgressWidget(_demo_pages, start=3, end=26)
        holder.set_finished_pages(_demo_finished)
        return holder

    def sub_block():
        from ui.configpanel import ConfigSubBlock
        return ConfigSubBlock(
            widget=ConfigLineEdit("内容控件"),
            name="参数名",
            description="说明文字（ConfigSubBlock 自动排版）",
        )

    def form_row():
        from ui.configpanel import ConfigFormRow
        return ConfigFormRow("标签", ConfigLineEdit("内容控件"))

    def config_text_label():
        from ui.configpanel import ConfigTextLabel
        return ConfigTextLabel("ConfigTextLabel 正文", 12)

    def param_widget():
        from ui.module_parse_widgets import ParamWidget
        return ParamWidget({"enable_demo": True, "steps_demo": 8, "mode_demo": "A"})

    def param_check_group():
        from ui.module_parse_widgets import ParamCheckGroup
        return ParamCheckGroup("check_demo", {"选项一": True, "选项二": False})

    def param_checker_box():
        from ui.module_parse_widgets import ParamCheckerBox
        return ParamCheckerBox("switch_demo")

    def param_check_box():
        from ui.module_parse_widgets import ParamCheckBox
        return ParamCheckBox("flag_demo")

    def param_push_button():
        from ui.module_parse_widgets import ParamPushButton
        return ParamPushButton("button_demo")

    def param_line_editor():
        from ui.module_parse_widgets import ParamLineEditor
        return ParamLineEditor("demo", force_digital=False)

    def param_editor():
        from ui.module_parse_widgets import ParamEditor
        return ParamEditor("prompt_demo")

    def field_editor():
        from ui.style_format_editor import FieldEditor
        return FieldEditor("font_size")

    def format_group_card():
        from ui.style_format_editor import FormatGroupCard
        return FormatGroupCard("text")

    def transform_control():
        from ui.text_engine.transforms.panel import CommittedTransformControl
        control = CommittedTransformControl("X 偏移", "offset_x", 1.0, -100.0, 100.0, "", 1.0)
        control.set_model_value(12.0)
        return control

    def transform_choice():
        from ui.text_engine.transforms.panel import CommittedTransformChoiceControl
        return CommittedTransformChoiceControl(
            "插值", "interp", [("linear", lambda: "线性"), ("cubic", lambda: "三次")]
        )

    def transform_drag_label():
        from ui.text_engine.transforms.panel import TransformDragLabel
        return TransformDragLabel(text="拖拽标签", direction=0)

    def stroke_card():
        from ui.text_engine.effects.cards import StrokeEffectCard
        return StrokeEffectCard(0)

    def effect_numeric():
        from ui.text_engine.effects.cards import EffectNumericControl
        control = EffectNumericControl("宽度", "width", 1.0, 0.0, 50.0, "", 0.5)
        control.set_model_value(3.0)
        return control

    def blend_selector():
        from ui.text_engine.effects.cards import BlendModeSelector
        return BlendModeSelector("showcase")

    def advanced_disclosure():
        from ui.text_engine.effects.cards import _AdvancedDisclosure
        return _AdvancedDisclosure()

    def effect_buttons():
        from ui.text_engine.effects.cards import (
            EffectDeleteButton,
            EffectMoveDownButton,
            EffectMoveUpButton,
            EffectVisibilityButton,
        )
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for cls in (EffectVisibilityButton, EffectMoveUpButton, EffectMoveDownButton, EffectDeleteButton):
            lay.addWidget(cls())
        lay.addStretch(1)
        return box

    def style_preview_card():
        from ui.fontstyle_manager import StylePreviewCard
        from utils.fontformat import FontFormat
        card = StylePreviewCard()
        card.set_format(FontFormat(font_family="Microsoft YaHei", font_size=22))
        return card

    def chip_bar():
        from ui.fontstyle_manager import _ChipBar
        bar = _ChipBar()
        bar.set_chips([("a", "正文", "#5dade2"), ("b", "标题", None), ("c", "旁白", "#e67e22")])
        return bar

    def command_palette():
        from ui.pie_menu_editor import CommandPalette
        return CommandPalette()

    def row_table_demo(mode):
        table = RowTable(mode)
        if mode == MODE_TABLE:
            table.set_header_labels(["页", "旧矩形", "新矩形"])
            table.set_stretch_column(2)
            table.set_rows([
                {"checked": True, "cells": ["002.jpg", "24 26 132 56", "14 16 142 60"],
                 "rejected": False, "tooltip": "002.jpg · 24 26 132 56"},
                {"checked": False, "cells": ["002.jpg", "150 26 230 56", "142 16 240 60"],
                 "rejected": False, "tooltip": "002.jpg · 150 26 230 56"},
                {"checked": True, "cells": ["004.jpg", "24 116 132 146", "14 106 142 150"],
                 "rejected": False, "tooltip": "004.jpg · 24 116 132 146"},
            ])
            table.setMinimumHeight(140)
        else:
            table.set_rows([
                {"checked": True, "primary": "こんにちは", "meta": "002.jpg · 纯数字, 无假名",
                 "badge": "待处理", "badge_tone": "warning", "rejected": False,
                 "tooltip": "002.jpg"},
                {"checked": False, "primary": "………", "meta": "003.jpg · 纯符号",
                 "badge": "已驳回", "badge_tone": "muted", "rejected": True,
                 "tooltip": "003.jpg"},
            ])
            table.setMinimumHeight(130)
        return table

    return [
        ("输入类（必须用封装类，原生类静默掉样式）", [
            Row("ConfigComboBox", "ui/custom_widget/combobox.py::ConfigComboBox",
                lambda: _config_combo(["标准", "斜体", "粗偏移"])),
            Row("ComboBox（未聚焦禁滚轮）", "ui/custom_widget/combobox.py::ComboBox",
                lambda: ComboBox(options=["标准", "斜体", "粗偏移"])),
            Row("ParamComboBox", "ui/custom_widget/combobox.py::ParamComboBox",
                lambda: ParamComboBox("translator", ["保留", "覆盖"])),
            Row("SizeComboBox（拖拽调值）", "ui/custom_widget/combobox.py::SizeComboBox",
                lambda: SizeComboBox([1, 100], init_value=24)),
            Row("SmallComboBox", "ui/custom_widget/combobox.py::SmallComboBox",
                lambda: SmallComboBox(options=["A", "B"])),
            Row("SmallSizeComboBox（拖拽调值）", "ui/custom_widget/combobox.py::SmallSizeComboBox",
                lambda: SmallSizeComboBox([1, 100], init_value=1.2)),
            Row("NoArrowsSpinBox（拖拽调值）", "ui/custom_widget/spinbox.py::NoArrowsSpinBox",
                NoArrowsSpinBox),
            Row("NoArrowsDoubleSpinBox（拖拽调值）", "ui/custom_widget/spinbox.py::NoArrowsDoubleSpinBox",
                NoArrowsDoubleSpinBox),
            Row("ConfigLineEdit", "ui/custom_widget/text_input.py::ConfigLineEdit",
                lambda: ConfigLineEdit("单行文本")),
            Row("ConfigTextEdit", "ui/custom_widget/text_input.py::ConfigTextEdit",
                ConfigTextEdit, wide=True),
            Row("ConfigCheckBox", "ui/custom_widget/checkbox.py::ConfigCheckBox",
                lambda: ConfigCheckBox("启用")),
        ]),
        ("图标式复选框（objectName 决定图标）", [
            Row("AlignmentChecker 左", "ui/custom_widget/checkbox.py::AlignmentChecker",
                _icon_checker(AlignmentChecker, "AlignLeftChecker")),
            Row("AlignmentChecker 中", "ui/custom_widget/checkbox.py::AlignmentChecker",
                _icon_checker(AlignmentChecker, "AlignCenterChecker")),
            Row("AlignmentChecker 右", "ui/custom_widget/checkbox.py::AlignmentChecker",
                _icon_checker(AlignmentChecker, "AlignRightChecker")),
            Row("QFontChecker 斜体", "ui/custom_widget/checkbox.py::QFontChecker",
                _icon_checker(QFontChecker, "FontItalicChecker")),
            Row("QFontChecker 删除线", "ui/custom_widget/checkbox.py::QFontChecker",
                _icon_checker(QFontChecker, "FontStrikeChecker")),
            Row("QFontChecker 下划线", "ui/custom_widget/checkbox.py::QFontChecker",
                _icon_checker(QFontChecker, "FontUnderlineChecker")),
        ]),
        ("按钮类（全局 QPushButton 兜底）", [
            Row("QPushButton（原生参照）", "PyQt6.QtWidgets::QPushButton",
                lambda: QPushButton("普通按钮")),
            Row("NoBorderPushBtn", "ui/custom_widget/push_button.py::NoBorderPushBtn",
                lambda: NoBorderPushBtn("无边框按钮")),
            Row("ExpandingToolButton", "ui/custom_widget/push_button.py::ExpandingToolButton",
                ExpandingToolButton),
            Row("ColorSwatchBtn（色块按钮）", "ui/custom_widget/color_button.py::ColorSwatchBtn",
                lambda: ColorSwatchBtn("#1e93e5")),
            Row("ConfigButton（objectName 选择器）", "config/stylesheet.css",
                config_button),
        ]),
        ("标签类", [
            Row("ClickableLabel（悬停高亮）", "ui/custom_widget/label.py::ClickableLabel",
                lambda: ClickableLabel("可点击")),
            Row("ConfigClickableLabel", "ui/custom_widget/label.py::ConfigClickableLabel",
                lambda: ConfigClickableLabel("配置可点击")),
            Row("CheckableLabel（点击切换文本）", "ui/custom_widget/label.py::CheckableLabel",
                lambda: CheckableLabel("已选", "未选")),
            Row("TextCheckerLabel", "ui/custom_widget/label.py::TextCheckerLabel",
                lambda: TextCheckerLabel("互斥项")),
            Row("ParamNameLabel", "ui/custom_widget/label.py::ParamNameLabel",
                lambda: ParamNameLabel("参数名")),
            Row("SmallParamLabel", "ui/custom_widget/label.py::SmallParamLabel",
                lambda: SmallParamLabel("小参数名")),
            Row("SizeControlLabel（拖拽调值）", "ui/custom_widget/label.py::SizeControlLabel",
                lambda: SizeControlLabel(text="拖拽标签")),
            Row("SmallSizeControlLabel", "ui/custom_widget/label.py::SmallSizeControlLabel",
                lambda: SmallSizeControlLabel(text="小拖拽标签")),
            Row("ColorPickerLabel（点击弹取色器）", "ui/custom_widget/label.py::ColorPickerLabel",
                ColorPickerLabel),
            Row("SmallColorPickerLabel", "ui/custom_widget/label.py::SmallColorPickerLabel",
                SmallColorPickerLabel),
            Row("ConfigTextLabel", "ui/configpanel.py::ConfigTextLabel", config_text_label),
        ]),
        ("滑块 / 拨盘 / 进度", [
            Row("PaintQSlider", "ui/custom_widget/slider.py::PaintQSlider",
                lambda: PaintQSlider("不透明度"), wide=True),
            Row("RangeSlider", "ui/custom_widget/slider.py::RangeSlider",
                lambda: RangeSlider(0, 100), wide=True),
            Row("PageProgressRangeBar（页码区间轨，悬停读出页名/页码）",
                "ui/custom_widget/page_range_progress.py::PageProgressRangeBar",
                page_range_bar, wide=True),
            Row("PageRangeSpinBox（页码框 + chevron 步进）",
                "ui/custom_widget/page_range_progress.py::PageRangeSpinBox",
                page_range_spin, wide=True),
            Row("PageRangeProgressWidget（运行窗口页码行）",
                "ui/custom_widget/page_range_progress.py::PageRangeProgressWidget",
                page_range_progress, wide=True),
            Row("ClockDial（影子方向）", "ui/custom_widget/clock_dial.py::ClockDial", clock_dial),
            Row("TaskProgressBar", "ui/custom_widget/message.py::TaskProgressBar",
                progress_bar, wide=True),
        ]),
        ("容器 / 结构", [
            Row("GroupFrame", "ui/custom_widget/group_frame.py::GroupFrame", group_frame),
            Row("SeparatorWidget", "ui/custom_widget/widget.py::SeparatorWidget", separator),
            Row("Widget 基类", "ui/custom_widget/widget.py::Widget", widget_base, wide=True),
            Row("PanelGroupBox", "ui/custom_widget/view_panel.py::PanelGroupBox",
                lambda: PanelGroupBox("卡片分组")),
            Row("ConfigSectionHeader", "ui/custom_widget/section_header.py::ConfigSectionHeader",
                lambda: ConfigSectionHeader("章节标题")),
            Row("ExpandLabel（默认）", "ui/custom_widget/view_panel.py::ExpandLabel",
                lambda: ExpandLabel("可折叠标题")),
            Row("ExpandLabel（capsule）", "ui/custom_widget/view_panel.py::ExpandLabel",
                lambda: ExpandLabel("胶囊标题", capsule=True)),
            Row("FlowLayout（自动换行）", "ui/custom_widget/flow_layout.py::FlowLayout",
                flow_layout_demo, wide=True),
        ]),
        ("滚动条", [
            Row("ConfigScrollBar（横向）", "ui/custom_widget/scroll_bar.py::ConfigScrollBar",
                hscrollbar, wide=True),
            Row("ScrollBar 纵向（画布淡出）", "ui/custom_widget/scrollbar.py::ScrollBar",
                lambda: _scroll_area_bar(Qt.Orientation.Vertical), wide=True),
            Row("ScrollBar 横向（画布淡出）", "ui/custom_widget/scrollbar.py::ScrollBar",
                lambda: _scroll_area_bar(Qt.Orientation.Horizontal), wide=True),
        ]),
        ("配置面板（ui/configpanel.py）", [
            Row("ConfigSubBlock", "ui/configpanel.py::ConfigSubBlock", sub_block, wide=True),
            Row("ConfigFormRow", "ui/configpanel.py::ConfigFormRow", form_row, wide=True),
        ]),
        ("模块参数表单（ui/module_parse_widgets.py）", [
            Row("ParamWidget", "ui/module_parse_widgets.py::ParamWidget", param_widget, wide=True),
            Row("ParamCheckGroup", "ui/module_parse_widgets.py::ParamCheckGroup",
                param_check_group, wide=True),
            Row("ParamCheckerBox", "ui/module_parse_widgets.py::ParamCheckerBox",
                param_checker_box, wide=True),
            Row("ParamCheckBox", "ui/module_parse_widgets.py::ParamCheckBox", param_check_box),
            Row("ParamPushButton", "ui/module_parse_widgets.py::ParamPushButton", param_push_button),
            Row("ParamLineEditor", "ui/module_parse_widgets.py::ParamLineEditor", param_line_editor),
            Row("ParamEditor", "ui/module_parse_widgets.py::ParamEditor", param_editor, wide=True),
        ]),
        ("文本格式面板（ui/style_format_editor.py）", [
            Row("FieldEditor（font_size）", "ui/style_format_editor.py::FieldEditor",
                field_editor, wide=True),
            Row("FormatGroupCard（text）", "ui/style_format_editor.py::FormatGroupCard",
                format_group_card, wide=True),
        ]),
        ("变换面板（ui/text_engine/transforms/panel.py）", [
            Row("CommittedTransformControl", "ui/text_engine/transforms/panel.py::CommittedTransformControl",
                transform_control, wide=True),
            Row("CommittedTransformChoiceControl",
                "ui/text_engine/transforms/panel.py::CommittedTransformChoiceControl",
                transform_choice, wide=True),
            Row("TransformDragLabel", "ui/text_engine/transforms/panel.py::TransformDragLabel",
                transform_drag_label),
        ]),
        ("效果栈（ui/text_engine/effects/cards.py）", [
            Row("StrokeEffectCard", "ui/text_engine/effects/cards.py::StrokeEffectCard",
                stroke_card, wide=True),
            Row("EffectNumericControl", "ui/text_engine/effects/cards.py::EffectNumericControl",
                effect_numeric, wide=True),
            Row("BlendModeSelector", "ui/text_engine/effects/cards.py::BlendModeSelector",
                blend_selector),
            Row("_AdvancedDisclosure", "ui/text_engine/effects/cards.py::_AdvancedDisclosure",
                advanced_disclosure),
            Row("效果卡操作按钮族", "ui/text_engine/effects/cards.py::EffectVisibilityButton",
                effect_buttons),
        ]),
        ("样式管理器 / 饼菜单", [
            Row("StylePreviewCard", "ui/fontstyle_manager.py::StylePreviewCard",
                style_preview_card, wide=True),
            Row("_ChipBar", "ui/fontstyle_manager.py::_ChipBar", chip_bar, wide=True),
            Row("CommandPalette", "ui/pie_menu_editor.py::CommandPalette",
                command_palette, wide=True),
        ]),
        ("行列表（工作台批量候选，delegate 全自绘）", [
            Row("RowTable 表格模式", "ui/custom_widget/row_table.py::RowTable",
                lambda: row_table_demo(MODE_TABLE), wide=True),
            Row("RowTable 卡片模式", "ui/custom_widget/row_table.py::RowTable",
                lambda: row_table_demo(MODE_CARD), wide=True),
        ]),
    ]


# ── 状态矩阵取图 ────────────────────────────────────────────────

_pixmap_host = None


def _host():
    global _pixmap_host
    if _pixmap_host is None:
        _pixmap_host = QWidget()
        _pixmap_host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        _pixmap_host.resize(400, 140)
        QVBoxLayout(_pixmap_host).setContentsMargins(0, 0, 0, 0)
        _pixmap_host.show()
        _pixmap_host.activateWindow()
    return _pixmap_host


def _grab_state(factory, mode):
    """悬停/聚焦态只能靠抓图：焦点全局唯一、悬停需 WA_UnderMouse 重绘。

    用 sizeHint 自然尺寸渲染，避免被宿主布局拉伸导致文字裁切。
    """
    widget = factory()
    host = _host()
    widget.setParent(host)
    widget.move(0, 0)
    hint = widget.sizeHint()
    if hint.isValid() and hint.width() > 0:
        widget.resize(hint)
    widget.show()
    QApplication.processEvents()
    if mode == "hover":
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    elif mode == "focus":
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        widget.setFocus()
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    QApplication.processEvents()
    pixmap = widget.grab()
    widget.hide()
    widget.deleteLater()
    return pixmap


def _state_grid(factory):
    """2×2 状态快照：正常 / 禁用 / 悬停 / 聚焦。"""
    from qtpy.QtWidgets import QGridLayout

    box = QWidget()
    box.setStyleSheet("background: transparent;")
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(2)
    for index, (caption, mode) in enumerate(
        (("正常", "normal"), ("禁用", "disabled"), ("悬停", "hover"), ("聚焦", "focus"))
    ):
        cell = QWidget()
        cell.setStyleSheet("background: transparent;")
        col = QVBoxLayout(cell)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(_label(caption, size=10))
        pixmap = _grab_state(factory, mode)
        image = QLabel()
        image.setPixmap(pixmap)
        image.setFixedSize(pixmap.size())
        col.addWidget(image)
        col.addStretch(1)
        grid.addWidget(cell, index // 2, index % 2)
    return box


# ── 行渲染 ──────────────────────────────────────────────────────

def _copy_symbol(button, symbol):
    QApplication.clipboard().setText(symbol)
    button.setText("已复制")
    QTimer.singleShot(900, lambda: _restore_button(button))


def _restore_button(button):
    try:
        button.setText("复制")
    except RuntimeError:  # 重建时控件已被销毁
        pass


def _build_row(row, show_states, records):
    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    root = QVBoxLayout(outer)
    root.setContentsMargins(16, 2, 8, 2)
    root.setSpacing(4)

    line = QWidget()
    line.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(line)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    name = QLabel(row.name)
    name.setStyleSheet("background: transparent; color: gray;")
    name.setFixedWidth(140)
    lay.addWidget(name)

    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(holder)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(0)
    try:
        probe = row.factory()
        badge_kind = _style_source(getattr(probe, "_showcase_probe", probe))
        hl.addWidget(probe, 1 if row.wide else 0)
    except Exception as exc:  # 展示台不该因为单个控件挂掉
        probe = None
        badge_kind = "无规则"
        hl.addWidget(
            _label(f"构造失败：{type(exc).__name__}: {exc}", color="#e74c3c", size=11)
        )
        print(f"[showcase] {row.symbol} 构造失败: {exc!r}", file=sys.stderr)
    hl.addStretch(1)
    lay.addWidget(holder, 1)

    # 右侧固定宽簇：所有行的徽章 / 符号 / 复制按钮纵向对齐
    cluster = QWidget()
    cluster.setStyleSheet("background: transparent;")
    cluster.setFixedWidth(262)
    cl = QHBoxLayout(cluster)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.setSpacing(6)

    badge = QLabel(badge_kind)
    color = _BADGE_COLORS.get(badge_kind, "#95a5a6")
    badge.setStyleSheet(
        f"background: transparent; color: {color}; font-size: 11px;"
        f"border: 1px solid {color}; border-radius: 3px; padding: 0 4px;"
    )
    badge.setFixedWidth(66)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setToolTip("样式命中来源：决定该改 config/stylesheet.css 还是改代码")
    cl.addWidget(badge)

    symbol = QLabel()
    symbol.setStyleSheet("background: transparent; color: gray; font-size: 11px;")
    symbol.setToolTip(row.symbol)
    symbol.setFixedWidth(140)
    metrics = symbol.fontMetrics()
    if metrics.horizontalAdvance(row.symbol) > 140:
        symbol.setText(metrics.elidedText(row.symbol, Qt.TextElideMode.ElideMiddle, 140))
    else:
        symbol.setText(row.symbol)
    cl.addWidget(symbol)

    copy_btn = QToolButton()
    copy_btn.setText("复制")
    copy_btn.setFixedWidth(44)
    copy_btn.setToolTip("复制 路径::符号，直接粘给 AI 说「优化这个」")
    copy_btn.clicked.connect(lambda _, s=row.symbol, b=copy_btn: _copy_symbol(b, s))
    cl.addWidget(copy_btn)
    lay.addWidget(cluster)
    root.addWidget(line)

    if show_states:
        grid_row = QWidget()
        grid_row.setStyleSheet("background: transparent;")
        gl = QHBoxLayout(grid_row)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addSpacing(148)  # 与名称列 + 间距对齐
        gl.addWidget(_state_grid(row.factory))
        gl.addStretch(1)
        root.addWidget(grid_row)

    records.append({
        "widget": outer,
        "badge": badge_kind,
        "blob": f"{row.name} {row.symbol} {badge_kind}".lower(),
    })
    return outer


# ── Tab 1：输入类对照 ───────────────────────────────────────────

def _cell(inner):
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(6, 2, 6, 2)
    lay.addWidget(inner)
    return frame


def build_compare_tab():
    from ui.custom_widget import (
        ConfigCheckBox,
        ConfigComboBox,
        ConfigLineEdit,
        ConfigTextEdit,
        NoArrowsDoubleSpinBox,
        NoArrowsSpinBox,
    )

    opts = ["标准", "斜体", "粗偏移"]
    rows = [
        ("下拉框", lambda: QComboBox(), lambda: ConfigComboBox(options=opts)),
        ("数字输入（拖拽调值）", lambda: QSpinBox(), NoArrowsSpinBox),
        ("浮点输入（拖拽调值）", lambda: QDoubleSpinBox(), NoArrowsDoubleSpinBox),
        ("单行输入", lambda: QLineEdit(), lambda: ConfigLineEdit("文本")),
        ("多行输入", lambda: QTextEdit(), ConfigTextEdit),
        ("复选框", lambda: QCheckBox("启用"), lambda: ConfigCheckBox("启用")),
        ("按钮（全局兜底）", lambda: QPushButton("运行"), None),
        ("单选（全局兜底）", lambda: QRadioButton("竖排"), None),
    ]

    root = QTreeWidget()
    root.setHeaderLabels(["控件", "Qt 原生（随手写的）", "封装控件（正确写法）"])
    root.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    root.setAlternatingRowColors(False)
    for name, native_fn, wrapped_fn in rows:
        item = QTreeWidgetItem([name, "", ""])
        root.addTopLevelItem(item)
        root.setItemWidget(item, 1, _cell(native_fn()))
        if wrapped_fn is not None:
            root.setItemWidget(item, 2, _cell(wrapped_fn()))
    root.expandAll()

    hint = QLabel(
        "左右一致 = 全局 QSS 兜底，写原生类也不会错；\n"
        "右边明显不同 = 类名选择器，必须 from ui.custom_widget import …\n"
        "（QSpinBox/QTextEdit 甚至完全无规则 → Windows 原生外观）"
    )
    hint.setStyleSheet("color: gray; font-size: 12px; background: transparent;")

    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.addWidget(hint)
    lay.addWidget(root)
    return wrap


# ── Tab 2：控件一览 ─────────────────────────────────────────────

_KIND_FILTERS = ["全部", "类名", "objectName", "自绘", "内联", "全局兜底", "无规则"]


class GalleryTab(QWidget):
    def __init__(self, theme_name):
        super().__init__()
        self._theme_name = theme_name
        self._sections = []
        # rebuild 里 _grab_state 会 processEvents（抓悬停/聚焦态必须），
        # 重建耗时期间再点开关/切主题就会重入：内层 setWidget 删掉外层
        # 已登记的区块，外层 _rebuild_index 拿到已析构对象 → RuntimeError 闪退。
        self._rebuilding = False
        self._rebuild_pending = False

        from ui.custom_widget import ConfigCheckBox, ConfigComboBox, ConfigLineEdit
        from utils.config import pcfg

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.index = QListWidget()
        self.index.setFixedWidth(220)
        self.index.currentRowChanged.connect(self._jump)
        outer.addWidget(self.index)

        right = QVBoxLayout()
        outer.addLayout(right, 1)

        bar = QHBoxLayout()
        self.search = ConfigLineEdit()
        self.search.setPlaceholderText("搜索控件 / 文件 / 面板…")
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)

        self.kind_filter = ConfigComboBox(options=_KIND_FILTERS)
        self.kind_filter.setToolTip("只看某种样式来源（「无规则」最值得优化）")
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.kind_filter)

        self.theme_combo = ConfigComboBox(
            options=[f"暗色 · {pcfg.dark_theme}", f"亮色 · {pcfg.light_theme}"]
        )
        self.theme_combo.setCurrentIndex(0 if pcfg.darkmode else 1)
        self.theme_combo.setToolTip(
            "切换会临时重写 icons/ 的填充色（退出展示台时自动逐字节还原）"
        )
        self.theme_combo.currentIndexChanged.connect(self._switch_theme)
        bar.addWidget(self.theme_combo)

        self.states_check = ConfigCheckBox("状态矩阵")
        self.states_check.setToolTip("每个控件并排显示 正常 / 禁用 / 悬停 / 聚焦")
        self.states_check.stateChanged.connect(lambda _: self.rebuild())
        bar.addWidget(self.states_check)
        right.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        right.addWidget(self.scroll, 1)

        self.rebuild()

    # ── 构建 ────────────────────────────────────────────────────

    def rebuild(self):
        """重建整页；重入时只记一个待办，等当前重建结束再跑一次。"""
        if self._rebuilding:
            self._rebuild_pending = True
            return
        self._rebuilding = True
        try:
            self._rebuild()
        finally:
            self._rebuilding = False
        if self._rebuild_pending:
            self._rebuild_pending = False
            self.rebuild()

    def _rebuild(self):
        self._sections = []
        show_states = self.states_check.isChecked()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(2)

        for title, rows in _sections():
            sec = QWidget()
            sec.setStyleSheet("background: transparent;")
            sv = QVBoxLayout(sec)
            sv.setContentsMargins(12, 10, 12, 4)
            sv.setSpacing(4)
            caption = QLabel(title)
            caption.setStyleSheet("font-weight: bold; font-size: 14px; background: transparent;")
            sv.addWidget(caption)

            records = []
            for row in rows:
                sv.addWidget(_build_row(row, show_states, records))
            sv.addStretch(1)
            lay.addWidget(sec)
            self._sections.append({"widget": sec, "rows": records})

        # 未纳入展示分区（登记在 EXCLUDED 的控件）
        sec = QWidget()
        sec.setStyleSheet("background: transparent;")
        sv = QVBoxLayout(sec)
        sv.setContentsMargins(12, 10, 12, 4)
        sv.setSpacing(2)
        caption = QLabel("未纳入展示（需宿主 / 模态 / 交互）")
        caption.setStyleSheet("font-weight: bold; font-size: 14px; background: transparent;")
        sv.addWidget(caption)
        records = []
        for name, reason in EXCLUDED.items():
            line = QWidget()
            line.setStyleSheet("background: transparent;")
            ll = QHBoxLayout(line)
            ll.setContentsMargins(16, 1, 8, 1)
            ll.setSpacing(8)
            name_label = QLabel(name)
            name_label.setStyleSheet("background: transparent; color: gray;")
            name_label.setFixedWidth(180)
            ll.addWidget(name_label)
            reason_label = QLabel(reason)
            reason_label.setStyleSheet("background: transparent; color: gray; font-size: 11px;")
            ll.addWidget(reason_label, 1)
            sv.addWidget(line)
            records.append({
                "widget": line,
                "badge": "排除",
                "blob": f"{name} {reason}".lower(),
            })
        sv.addStretch(1)
        lay.addWidget(sec)
        self._sections.append({"widget": sec, "rows": records})

        lay.addStretch(1)
        self.scroll.setWidget(inner)
        self._rebuild_index()
        self._apply_filter()

    def _rebuild_index(self):
        self.index.blockSignals(True)
        self.index.clear()
        for sec in self._sections:
            try:
                caption = sec["widget"].findChild(QLabel)
            except RuntimeError:  # 防御：极端情况下区块已被析构
                caption = None
            title = caption.text() if caption else ""
            item = QListWidgetItem(title)
            item.setToolTip(title)
            self.index.addItem(item)
        self.index.setCurrentRow(0)
        self.index.blockSignals(False)

    # ── 交互 ────────────────────────────────────────────────────

    def _jump(self, index):
        if 0 <= index < len(self._sections):
            self.scroll.ensureWidgetVisible(self._sections[index]["widget"], 0, 0)

    def _apply_filter(self):
        query = self.search.text().strip().lower()
        kind = self.kind_filter.currentText()
        for sec in self._sections:
            visible = 0
            for record in sec["rows"]:
                match = (not query or query in record["blob"]) and (
                    kind == "全部" or record["badge"] == kind
                )
                record["widget"].setVisible(match)
                visible += int(match)
            sec["widget"].setVisible(visible > 0)

    def _switch_theme(self, index):
        from utils.config import pcfg
        self._theme_name = pcfg.dark_theme if index == 0 else pcfg.light_theme
        _apply_theme(self._theme_name, reverse_icons=True)
        self.rebuild()


# ── 入口 ────────────────────────────────────────────────────────

_ICON_SNAPSHOT = {}


def _apply_theme(theme, reverse_icons):
    """应用主题。reverse_icons=True 会就地重写 icons/*.svg 填充色（应用内同机制）。"""
    from ui.misc import parse_stylesheet
    QApplication.instance().setStyleSheet(parse_stylesheet(theme, reverse_icon=reverse_icons))


def _snapshot_icons():
    """启动时记下 icons/*.svg 原始字节，退出时按字节还原，保证仓库零痕迹。

    （按主题色反推还原不可靠：仓库里个别图标本身的填充色与其他图标不一致。）
    """
    icon_dir = os.path.join(ROOT, "icons")
    if not os.path.isdir(icon_dir):
        return
    for name in os.listdir(icon_dir):
        if name.lower().endswith(".svg"):
            with open(os.path.join(icon_dir, name), "rb") as f:
                _ICON_SNAPSHOT[name] = f.read()


def _restore_icons():
    """切主题会重写 svg，退出时逐字节还原。"""
    icon_dir = os.path.join(ROOT, "icons")
    for name, data in _ICON_SNAPSHOT.items():
        path = os.path.join(icon_dir, name)
        try:
            with open(path, "rb") as f:
                if f.read() == data:
                    continue
            with open(path, "wb") as f:
                f.write(data)
        except OSError:
            pass


def _install_translator(app):
    from qtpy.QtCore import QTranslator
    from utils import shared
    translator = QTranslator()
    if translator.load("zh_CN", shared.TRANSLATE_DIR):
        app.installTranslator(translator)


def _prepare_app():
    app = QApplication(sys.argv)
    from utils.config import load_config, pcfg
    load_config()
    # 与应用一致：屏蔽「字号<=0 / RGB 越界」这类良性 Qt 噪音
    from utils.safe_qt import install_qt_warning_filter
    install_qt_warning_filter()
    _install_translator(app)
    theme = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
    # 与应用启动一致：启动不重写 icons/ 填充色（只有主动切主题才重写）；
    # 切主题确实会改仓库里被跟踪的 svg，所以启动先快照、退出再逐字节还原。
    _snapshot_icons()
    _apply_theme(theme, reverse_icons=False)
    app.aboutToQuit.connect(_restore_icons)
    return app, theme


def selftest():
    """无界面自检：逐个构造工厂并探测样式来源，失败即非零退出。"""
    app, _theme = _prepare_app()
    failures = []
    count = 0
    for title, rows, *_ in _sections():
        for row in rows:
            count += 1
            try:
                widget = row.factory()
                if widget is None:
                    raise RuntimeError("factory 返回 None")
                probe = getattr(widget, "_showcase_probe", widget)
                _style_source(probe)
                _grab_state(row.factory, "hover")
                _grab_state(row.factory, "focus")
                widget.setParent(None)
                widget.deleteLater()
            except Exception as exc:
                failures.append((row.symbol, f"{type(exc).__name__}: {exc}"))
    print(f"checked {count} rows, {len(failures)} failures")
    for symbol, error in failures:
        print(f"FAIL {symbol}: {error}")
    return 1 if failures else 0


def main():
    app, theme = _prepare_app()

    window = QWidget()
    window.setWindowTitle("控件样式展示台")
    window.resize(1180, 780)
    lay = QVBoxLayout(window)
    tabs = QTabWidget()
    tabs.addTab(build_compare_tab(), "输入类对照（原生 vs 封装）")
    tabs.addTab(GalleryTab(theme), "控件一览")
    lay.addWidget(tabs)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
