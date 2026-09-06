"""自定义控件样式展示台（人工目视工具）。

两个标签页：
  1. 输入类对照 —— Qt 原生类 vs ui/custom_widget 封装类并排对比。
     左右一样的行 = 全局 QSS 兜底；右边明显不同 = 类名选择器，
     代码里必须用封装类（详见 docs/基础速查/打包控件功能使用说明.md
     「样式生效机制：全局兜底 vs 类名选择器」一节）。
  2. 封装控件一览 —— 按分区展示全部可离线实例化的自定义控件，
     供人工查看/调整 config/stylesheet.css 后核对效果。

维护方式：新增控件时在下方对应分区的 rows 列表里追加一行
`(说明, 工厂函数)` 即可；无法离线实例化的控件（模态对话框、
需要宿主画布的 NotificationCenter/RailDockPanel 等）在文件尾部
EXCLUDED 注释里登记原因，不必强塞。

用法（仓库根目录）：
    ./ballontrans_pylibs_win/python.exe scripts/style_showcase.py
"""
import os
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
    raise SystemExit(subprocess.call([bundled, os.path.abspath(__file__)]))


_reexec_bundled_python()

try:
    from qtpy.QtCore import Qt
except ModuleNotFoundError:
    sys.exit("未找到 qtpy：请用仓库便携解释器运行"
             "（ballontrans_pylibs_win/python.exe scripts/style_showcase.py）")

from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ── 小工具 ──────────────────────────────────────────────────────

def cell(inner):
    """对比表单元格：包一层 QFrame 留边距。"""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(6, 2, 6, 2)
    lay.addWidget(inner)
    return frame


def section(title, rows, wide=False):
    """一览页的一个分区：标题 + 行列表。rows = [(说明, 工厂), ...]"""
    box = QWidget()
    box.setStyleSheet("background: transparent;")
    outer = QVBoxLayout(box)
    outer.setContentsMargins(12, 10, 12, 4)
    outer.setSpacing(4)
    cap = QLabel(title)
    cap.setStyleSheet("font-weight: bold; font-size: 14px; background: transparent;")
    outer.addWidget(cap)
    for name, factory in rows:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 1, 8, 1)
        lay.setSpacing(10)
        label = QLabel(name)
        label.setStyleSheet("background: transparent; color: gray;")
        label.setFixedWidth(210)
        lay.addWidget(label)
        w = factory()
        if wide:
            lay.addWidget(w, 1)
        else:
            lay.addWidget(w)
            lay.addStretch(1)
        outer.addWidget(row)
    outer.addStretch(1)
    return box


# ── Tab 1：输入类对照 ───────────────────────────────────────────

def build_compare_tab():
    from ui.custom_widget import (
        ConfigCheckBox,
        ConfigComboBox,
        ConfigLineEdit,
        ConfigTextEdit,
        NoArrowsDoubleSpinBox,
        NoArrowsSpinBox,
    )

    def make_config_combo(options):
        # options= 构造参数已支持（2026-09-06 修复）；两种写法均可用
        return ConfigComboBox(options=options)

    opts = ["标准", "斜体", "粗偏移"]
    rows = [
        ("下拉框", lambda: QComboBox(), lambda: make_config_combo(opts)),
        ("数字输入", lambda: QSpinBox(), lambda: NoArrowsSpinBox()),
        ("浮点输入", lambda: QDoubleSpinBox(), lambda: NoArrowsDoubleSpinBox()),
        ("单行输入", lambda: QLineEdit(), lambda: ConfigLineEdit("文本")),
        ("多行输入", lambda: QTextEdit(), lambda: ConfigTextEdit()),
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
        root.setItemWidget(item, 1, cell(native_fn()))
        if wrapped_fn is not None:
            root.setItemWidget(item, 2, cell(wrapped_fn()))
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


# ── Tab 2：封装控件一览 ─────────────────────────────────────────

def build_gallery_tab():
    from ui.custom_widget import (
        AlignmentChecker,
        CheckableLabel,
        ClickableLabel,
        ClockDial,
        ColorPickerLabel,
        ColorSwatchBtn,
        ConfigCheckBox,
        ConfigClickableLabel,
        ConfigComboBox,
        ConfigLineEdit,
        ConfigScrollBar,
        ConfigSectionHeader,
        ConfigTextEdit,
        ExpandLabel,
        ExpandingToolButton,
        GroupFrame,
        NoArrowsDoubleSpinBox,
        NoArrowsSpinBox,
        NoBorderPushBtn,
        PaintQSlider,
        PanelGroupBox,
        ParamComboBox,
        ParamNameLabel,
        QFontChecker,
        RangeSlider,
        SeparatorWidget,
        SmallColorPickerLabel,
        SmallComboBox,
        SmallParamLabel,
        SmallSizeComboBox,
        SizeComboBox,
        TextCheckerLabel,
        Widget,
    )

    def config_combo(options):
        cb = ConfigComboBox()
        cb.addItems(options)
        return cb

    def align_checker(object_name):
        cb = AlignmentChecker()
        cb.setObjectName(object_name)
        return cb

    def font_checker(object_name):
        cb = QFontChecker()
        cb.setObjectName(object_name)
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

    sections = [
        ("输入类（必须用封装类，原生类静默掉样式）", [
            ("ConfigComboBox", lambda: config_combo(["标准", "斜体", "粗偏移"])),
            ("ParamComboBox", lambda: ParamComboBox("translator", ["保留", "覆盖"])),
            ("SizeComboBox", lambda: SizeComboBox([1, 100], init_value=24)),
            ("SmallComboBox", lambda: SmallComboBox(options=["A", "B"])),
            ("SmallSizeComboBox", lambda: SmallSizeComboBox([1, 100], init_value=1.2)),
            ("NoArrowsSpinBox", lambda: NoArrowsSpinBox()),
            ("NoArrowsDoubleSpinBox", lambda: NoArrowsDoubleSpinBox()),
            ("ConfigLineEdit", lambda: ConfigLineEdit("单行文本")),
            ("ConfigTextEdit", ConfigTextEdit),
            ("ConfigCheckBox", lambda: ConfigCheckBox("启用")),
        ], True),
        ("图标式复选框（objectName 决定图标）", [
            ("AlignmentChecker 左", lambda: align_checker("AlignLeftChecker")),
            ("AlignmentChecker 中", lambda: align_checker("AlignCenterChecker")),
            ("AlignmentChecker 右", lambda: align_checker("AlignRightChecker")),
            ("QFontChecker 斜体", lambda: font_checker("FontItalicChecker")),
            ("QFontChecker 删除线", lambda: font_checker("FontStrikeChecker")),
            ("QFontChecker 下划线", lambda: font_checker("FontUnderlineChecker")),
        ]),
        ("按钮类（全局 QPushButton 兜底）", [
            ("QPushButton（原生参照）", lambda: QPushButton("普通按钮")),
            ("NoBorderPushBtn", lambda: NoBorderPushBtn("无边框按钮")),
            ("ExpandingToolButton", ExpandingToolButton),
            ("ColorSwatchBtn（色块按钮）", lambda: ColorSwatchBtn("#1e93e5")),
        ]),
        ("标签类", [
            ("ClickableLabel（悬停高亮）", lambda: ClickableLabel("可点击")),
            ("ConfigClickableLabel", lambda: ConfigClickableLabel("配置可点击")),
            ("CheckableLabel（点击切换文本）", lambda: CheckableLabel("已选", "未选")),
            ("TextCheckerLabel", lambda: TextCheckerLabel("互斥项")),
            ("ParamNameLabel", lambda: ParamNameLabel("参数名")),
            ("SmallParamLabel", lambda: SmallParamLabel("小参数名")),
            ("ColorPickerLabel（点击弹取色器）", ColorPickerLabel),
            ("SmallColorPickerLabel", SmallColorPickerLabel),
        ]),
        ("滑块 / 拨盘", [
            ("PaintQSlider", lambda: PaintQSlider("不透明度")),
            ("RangeSlider", lambda: RangeSlider(0, 100)),
            ("ClockDial（影子方向）", clock_dial),
        ], True),
        ("容器 / 结构", [
            ("GroupFrame", group_frame),
            ("SeparatorWidget", separator),
            ("Widget 基类", widget_base),
            ("PanelGroupBox", lambda: PanelGroupBox("卡片分组")),
            ("ConfigSectionHeader", lambda: ConfigSectionHeader("章节标题")),
            ("ExpandLabel（默认）", lambda: ExpandLabel("可折叠标题")),
            ("ExpandLabel（capsule）", lambda: ExpandLabel("胶囊标题", capsule=True)),
            ("ConfigScrollBar（横向）", hscrollbar),
        ], True),
    ]

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setSpacing(2)
    for title, rows, *rest in sections:
        wide = bool(rest and rest[0])
        lay.addWidget(section(title, rows, wide=wide))
    scroll.setWidget(inner)
    return scroll


# ── 无法离线展示的控件（需要宿主/模态环境，维护时勿强塞）────────
# ColorPickerDialog / MessageBox 族          模态对话框
# NotificationCenter                          需画布 attach
# RailDockPanel / FloatDropPanel              需主窗口宿主与窄栏锚点
# PanelArea / ViewWidget                      需配套内容结构
# ScrollBar（scrollbar.py）                   旧版遗留，预留特殊场景
# FadeLabel / SizeControlLabel                行为型（动画/拖拽），样式无单独规则


def main():
    app = QApplication(sys.argv)
    from ui.misc import parse_stylesheet

    app.setStyleSheet(parse_stylesheet())

    window = QWidget()
    window.setWindowTitle("自定义控件样式展示台")
    window.resize(860, 640)
    lay = QVBoxLayout(window)
    tabs = QTabWidget()
    tabs.addTab(build_compare_tab(), "输入类对照（原生 vs 封装）")
    tabs.addTab(build_gallery_tab(), "封装控件一览")
    lay.addWidget(tabs)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
