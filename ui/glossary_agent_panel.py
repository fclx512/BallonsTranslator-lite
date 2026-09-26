"""泛用工作台（设计见 docs/技术实现/AI辅助功能_设计与实现.md 的「工作台」部分，本文件＝界面层）。

**定位**：工作台是**任务容器**，不是聊天窗（D19 砍掉 Chat；D2 起就不做通用
对话）。六个任务共用一套三段结构（D20）——

1. **任务导航**（批次 C 起＝**扁平六项**，见 ``WORKBENCH_ORDER``）：单入口下
   按处理先后排列，分组只做视觉分段、**不再要求先选大类再选 chip**
   （空的大类与跳步弹窗都已去掉，也不再门禁或提示顺序）。批次 D 把形态从
   「一行一个整宽按钮」收成**按需换行的 chip 流**（组标题行去掉、激活项完整
   显示、放不下的非激活项省略，见 ``WorkbenchTaskNav``）。顺序＝用户工作流：
   ① 可疑框清理 ② 合并相邻文本框 ③ 原文待校对 ④ 简单背景修复
   ⑤ 翻译准备（术语＋剧情两块草稿）⑥ 译文待重译。每项只报**自己可解释的
   数量与单位**（``BatchTask.pending``／``count_label``），不把块/组/页混加。
2. **候选列表**：任务自己的清单（批量任务见
   ``ui/workbench_batch_view.py``，待办队列见 ``ui/workbench_review_view.py``
   ——勾选、点选即在浮层展开 100% 原比例审批图，D23／D44；翻译准备页把
   术语表与剧情两块草稿放在同一入口的两个子页里）。
3. **执行／写回**：无候选时禁用并显示「无候选」；批量执行前一律弹窗告知
   后果（D27）。

**批量任务的接线纪律**（设计 §8／§18）：界面只做"调 ``plan``→收勾选
→把标识交回 ``apply``"，不自己写几何、不自己改 ``proj.pages``、不绕开
``ui/batch_ops.py``；引擎与适配层在 ``ui/workbench_tasks.py``（Qt-free）与
四个 ``ui/batch_*.py``。批量事务外壳（D35 版本 + D40 五步）在这里建好并
注入，故版本快照会先落盘当前面板编辑。**待办队列（原文待校对／译文待重译）
不走这条路**：它们没有整批写回、没有版本，出口只有「跳画布」与「从列表
移除／忽略」。

**D25 单入口**：主窗口左栏只有一个「工作台」入口（``ui/mainwindowbars.py::LeftBar``
的 workbenchChecker 槽位），任务切换全在本面板内完成。

术语／剧情两页仍是原 ``GlossaryAgentWorker`` 的镜像：AI 产物只落 worker 草稿，
落盘只经「应用草稿…」。**Chat 砍掉后 worker 日志落在本面板底部的日志条**
（日志行带所属任务名，术语／剧情的行归「翻译准备」）。
"""

import html
import json
import logging

from qtpy.QtCore import (
    Property,
    QAbstractAnimation,
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QThread,
    Qt,
    Signal,
    Slot,
)
from qtpy.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from modules.context.glossary import load_glossary
from modules.context_agent.draft import (
    ORIGIN_AI,
    ORIGIN_EXISTING,
    ORIGIN_USER,
    GlossaryDraft,
    StoryDraft,
)
from modules.context_agent.precollect import extract_by_frequency
from modules.context_agent.prompts import build_system_prompt
from modules.context_agent.session import (
    STOP_CANCELLED,
    STOP_MAX_TURNS,
    STOP_TOKEN_BUDGET,
    SessionResult,
    run_agent_session,
    trim_session_messages,
)
from modules.context_agent.story import (
    PAGE_SUMMARY_KEY,
    SYNOPSIS_KEY,
    load_story_base,
)
from modules.context_agent.tools import build_context_tools, execute_context_tool
from ui.adaptive_wrap_layout import AdaptiveWrapLayout, wrap_rows
from ui.custom_widget import ConfigTextEdit
from ui.misc import get_theme_color
from ui.workbench_batch_view import BatchTaskView
from ui.workbench_review_view import ReviewQueueView
from ui.workbench_tasks import (
    CLEANUP_TASK_IDS,
    GLOSSARY,
    MERGE,
    MISREAD,
    OCR_REVIEW,
    SIMPLE_INPAINT,
    STORY,
    TRANS_REVIEW,
    TRANSLATION_PREP,
    build_batch_tasks,
    build_review_tasks,
)
from utils.config import pcfg

logger = logging.getLogger("glossary_agent_panel")

# 扁平任务导航（批次 C 交接 §4.2）：单入口下按处理先后列出六项，**不再要求
# 先选大类再选 chip**；分组只做视觉分段（不参与选择，也没有空的大类）。
# 顺序＝用户工作流：可疑框清理 → 合并 → 原文待校对 → 简单背景修复 →
# 翻译准备 → 译文待重译。
WORKBENCH_ORDER = (
    MISREAD,
    MERGE,
    OCR_REVIEW,
    SIMPLE_INPAINT,
    TRANSLATION_PREP,
    TRANS_REVIEW,
)

# 内容一变就要跟着刷新的**便宜计数**（纯内存遍历、不跑引擎 plan）：
# 三个队列的「还有几条待处理」都由块上的标签直接数出来，画布上处理完一个
# 框，导航上的数字不该停在旧值；合并组数／修复页数要跑全书 plan，只在切任务
# 与重规划时算（`_refresh_nav_counts` 的 cheap_only 开关）。
_CHEAP_COUNT_TASKS = (MISREAD, OCR_REVIEW, TRANS_REVIEW)

# 视觉分段：原文／图像侧的清理与审校一段，翻译侧的准备与审校一段。组名不再
# 独占一行（批次 D：组标题行去掉，组间只画一条细分隔线），改随本组 chip 的
# tooltip 露出——分组数据仍是这一份，不另立一份。
# 组内顺序必须与 WORKBENCH_ORDER 一致（下面的断言防两处各自漂移）。
# 组标题上下文用 ``WorkbenchTaskNav`` 而非 GlossaryAgentPanel：后者已被
# 术语表列头占用（"Translation" → 译文），同 context 同 source 只能有一个译文。
WORKBENCH_NAV_GROUPS = (
    (
        QCoreApplication.translate("WorkbenchTaskNav", "Source and image"),
        (MISREAD, MERGE, OCR_REVIEW, SIMPLE_INPAINT),
    ),
    (
        QCoreApplication.translate("WorkbenchTaskNav", "Translation"),
        (TRANSLATION_PREP, TRANS_REVIEW),
    ),
)

assert WORKBENCH_ORDER == tuple(
    task_id for _title, task_ids in WORKBENCH_NAV_GROUPS for task_id in task_ids
), "workbench nav groups are out of sync with WORKBENCH_ORDER"

# 导航钮的短标签（字面量定义处显式标注翻译上下文；模块级翻译表规则）。
# 「翻译准备」是**一个入口**：术语与剧情在同一页的两个子页里各留自己的
# 草稿与应用语义（不合并落盘事务，也不合并 worker）。
_TASK_LABELS = {
    MISREAD: QCoreApplication.translate("GlossaryAgentPanel", "Suspicious blocks"),
    MERGE: QCoreApplication.translate("GlossaryAgentPanel", "Merge text blocks"),
    OCR_REVIEW: QCoreApplication.translate(
        "GlossaryAgentPanel", "Review source text"
    ),
    SIMPLE_INPAINT: QCoreApplication.translate(
        "GlossaryAgentPanel", "Simple background fill"
    ),
    TRANSLATION_PREP: QCoreApplication.translate(
        "GlossaryAgentPanel", "Prepare for translation"
    ),
    TRANS_REVIEW: QCoreApplication.translate("GlossaryAgentPanel", "Retranslate"),
    # 翻译准备页两个子页的标题（各自的草稿，各自应用）
    GLOSSARY: QCoreApplication.translate("GlossaryAgentPanel", "Glossary"),
    STORY: QCoreApplication.translate("GlossaryAgentPanel", "Story"),
}

# 模块级翻译表在字面量定义处显式标注上下文(self.tr(variable) 间接查表
# 检查器看不见,必漏翻译);launch.py 安装翻译器早于本模块导入。
_STOP_REASONS = {
    STOP_MAX_TURNS: QCoreApplication.translate(
        "GlossaryAgentWorker",
        "Turn limit reached — the round ended without a reply."
    ),
    STOP_TOKEN_BUDGET: QCoreApplication.translate(
        "GlossaryAgentWorker",
        "Token budget reached — the round ended without a reply."
    ),
    STOP_CANCELLED: QCoreApplication.translate(
        "GlossaryAgentWorker", "Cancelled."
    ),
}

_ORIGIN_LABELS = {
    ORIGIN_EXISTING: QCoreApplication.translate(
        "GlossaryAgentPanel", "base"
    ),
    ORIGIN_AI: QCoreApplication.translate("GlossaryAgentPanel", "AI"),
    ORIGIN_USER: QCoreApplication.translate("GlossaryAgentPanel", "you"),
}

# Origin 列文字着色(主题变量键,get_theme_color 解析;不硬编码色值):
# base 灰 / AI 主题强调色 / you 成功绿,一眼区分条目来源。
_ORIGIN_THEME_KEYS = {
    ORIGIN_EXISTING: "@disabledForegroundColor",
    ORIGIN_AI: "@accentPrimary",
    ORIGIN_USER: "@successColor",
}


def _with_count(label: str, count: int, unit: str = "") -> str:
    """导航钮文字＝标签 + 带单位的数量；0 不缀。"""
    count = int(count or 0)
    if not count:
        return label
    suffix = " " + unit if unit else ""
    return label + " (" + str(count) + suffix + ")"


def _styled(widget: QWidget) -> QWidget:
    """容器 QSS（底色/分隔线）生效的前提——纯 QWidget 不上屏 QSS 背景。

    仓库既有做法（``ui/tag_toolbar.py``、``ui/run_pipeline_dialog.py``）：
    objectName 选择器写了 background／border 就必须开 WA_StyledBackground，
    否则规则静默失效。
    """
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


_THEME_COLOR_CACHE = {}


def _theme_color(key: str, alpha: int = 255) -> QColor:
    """主题色按 key 现查 + 本模块内缓存（导航 chip 自绘用）。

    ``ui/misc.py::get_theme_color`` 每次调用都要重读一遍主题 json，paint 里直接
    调会在悬浮重绘时反复落盘（同 ``ui/custom_widget/row_table.py`` 的说明），
    故按 (key, alpha) 缓存；全局换肤会重发 ``StyleChange``，据它失效。
    """
    cache_key = (key, alpha)
    cached = _THEME_COLOR_CACHE.get(cache_key)
    if cached is None:
        cached = get_theme_color(key=key, alpha=alpha)
        _THEME_COLOR_CACHE[cache_key] = cached
    return cached


def _page_hint(text: str, parent: QWidget) -> QLabel:
    """任务页顶部的一行说明（与批量任务的 ``BatchTask.hint`` 同一观感）。

    术语／剧情两页原先只有表格，没有一句话说清"这页是干什么的、什么时候
    才落盘"；批量任务页都有说明行，这里补齐一致性。
    """
    hint = QLabel(text, parent)
    hint.setObjectName("WorkbenchTaskHint")
    hint.setWordWrap(True)
    return hint


# ── 任务导航（chip 流）的尺寸与动画常量 ─────────────────────────────
# 批次 D 修订：六个任务原先各占一行整宽按钮（＋两行组标题＝8 行高），把页内
# 信息挤没了。改成**按需换行的 chip 流**：组标题行去掉、分组只用细分隔线，
# 激活 chip 完整显示、放不下的非激活 chip 压到可用宽度并在**渲染层**省略。
# 常态两行 = 6(上边距) + 22 + 4 + 22 = 54px。
_CHIP_HEIGHT = 22
_CHIP_GAP = 4  # chip 之间的水平间距＝换行后的行距（同一套，观感均匀）
_CHIP_PAD = 9  # chip 内文字左右留白
_CHIP_MIN_WIDTH = 44  # 非激活 chip 的压缩下限：两个汉字 + 省略号
_CHIP_SLACK = 2  # 量完整宽度时的余量（圆角绘制的取整误差）
_CHIP_ANIM_MS = 160  # 激活项展开／旧激活项收回的时长
_NAV_ROWS_NORMAL = 2  # 常态行数（宽度分配优先够到这个行数）
_NAV_ROWS_MAX = 3  # 行数上限（窄面板／超长标签的兜底）
_NAV_MARGINS = (6, 6, 6, 0)
_GROUP_LINE_WIDTH = 1  # 组间细分隔线（画在两组相邻 chip 的缝里）
_GROUP_LINE_HEIGHT = 14


class WorkbenchTaskChip(QPushButton):
    """导航 chip：自绘圆角底 + **渲染层省略**的单行标签。

    为什么不用 ``QPushButton`` 现成的绘制：它**不会省略**，文字放不下就从中间
    硬裁（实测 80px 宽的按钮把 "Merge text blocks (12 groups)" 画成
    "xt blocks (12" 并被切掉半截），所以 ``paintEvent`` 自己画，宽度不够时用
    ``QFontMetrics.elidedText`` 出省略号。

    ``text()`` 仍是**完整逻辑标签**（含 ``(N unit)`` 计数）：省略只发生在渲染层，
    逻辑文本一个字不改——``tests/test_workbench_panel.py`` 按 ``text()`` 断言计数
    口径（``assertNotIn("(", ...)``）。宽度由 ``WorkbenchTaskNav`` 统一分配，本
    控件不参与尺寸谈判（``sizeHint`` 就报当前宽度，供换行布局逐帧重排）。
    """

    def __init__(self, text: str, group_title: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("WorkbenchTaskButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(_CHIP_HEIGHT)
        self._group_title = group_title
        self._chip_width = 0
        self._full_width = 0
        self._measured_text = ""
        self._hover = False
        # 分配到的目标宽度：**动画进行中也真**（离屏测试读它，不依赖补间）
        self.target_width = 0
        self.sync_tooltip()

    # 宽度合成一个 Qt 属性：min/max 两处得同时改，动画只需一条
    def _get_chip_width(self) -> int:
        return self._chip_width

    def _set_chip_width(self, width: int):
        self._chip_width = int(width)
        self.setFixedWidth(self._chip_width)

    chipWidth = Property(int, _get_chip_width, _set_chip_width)

    def set_chip_width(self, width: int):
        """一步到位（不走补间）：关动画档与离屏测试走这里。"""
        self._set_chip_width(width)

    def sizeHint(self) -> QSize:
        return QSize(self._chip_width, _CHIP_HEIGHT)

    def full_width(self) -> int:
        """完整标签需要的宽度（含计数）：按**粗体**量——激活 chip 是粗体。"""
        if self._measured_text != self.text():
            metrics = QFontMetrics(self._label_font(bold=True))
            self._full_width = (
                metrics.horizontalAdvance(self.text()) + 2 * _CHIP_PAD + _CHIP_SLACK
            )
            self._measured_text = self.text()
        return self._full_width

    def set_full_text(self, text: str) -> None:
        """换标签（导航 ``set_count`` 的唯一入口）：逻辑文本 + tooltip 一起更新。"""
        if text == self.text():
            return
        self.setText(text)
        self.sync_tooltip()
        self.update()

    def sync_tooltip(self) -> None:
        """tooltip＝完整标签：被省略号裁过的 chip 靠悬停看全（组名顺带露出）。"""
        tip = self.text()
        if self._group_title:
            tip = self._group_title + " · " + tip
        self.setToolTip(tip)

    def _label_font(self, bold: bool) -> QFont:
        font = QFont(self.font())
        font.setBold(bold)
        return font

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def event(self, event):
        # 全局换肤会重发 StyleChange：主题色缓存据它失效（同 row_table 的做法）
        if event.type() == QEvent.Type.StyleChange:
            _THEME_COLOR_CACHE.clear()
        return super().event(event)

    def paintEvent(self, event):
        """自绘（QPushButton 默认不省略文字，见类 docstring）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 先铺满导航底色：chip 只画一个内缩的圆角块，块外要透出导航底色
        # （通用 QPushButton 规则带着背景，底类绘制会填满整个矩形）
        painter.fillRect(self.rect(), _theme_color("@emptyContentBackgroundColor"))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        if checked:
            # 选中＝强调色淡染：用主题预混好的**不透明**版（``@accentPrimary20``
            # 是 QSS 的 rgba 写法，QColor 解析不了，见 ui/misc.py::_derive_solid_tints）
            fill = _theme_color("@accentPrimary20Solid")
            edge = _theme_color("@accentPrimary")
            text_color = edge
        else:
            fill = _theme_color(
                "@hoverBackgroundColor"
                if self._hover
                else "@widgetBackgroundColor"
            )
            edge = _theme_color("@borderColor")
            text_color = _theme_color("@qwidgetForegroundColor")
        radius = rect.height() / 2
        painter.setBrush(QBrush(fill))
        painter.setPen(edge)
        painter.drawRoundedRect(rect, radius, radius)
        font = self._label_font(bold=checked)
        painter.setFont(font)
        inner = rect.adjusted(_CHIP_PAD, 0, -_CHIP_PAD, 0)
        label = QFontMetrics(font).elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(int(inner.width()), 0)
        )
        painter.setPen(text_color)
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, label)


class WorkbenchTaskNav(QWidget):
    """扁平任务导航＝**按需换行的 chip 流**（批次 D 修订）。

    六个任务按处理先后排成 chip：组标题行去掉（分组改由两组相邻 chip 之间的
    细分隔线表达，见 ``paintEvent``），**激活 chip 始终完整显示**，放不下的
    非激活 chip 压到可用宽度、文字在渲染层省略（``text()`` 不受影响）。每项只缀
    **自己可解释的数量与单位**（如「12」「3 groups」——块口径的队列只报数字，组/页
    的单位才是信息），零候选不缀。

    宽度分配见 ``_allocate``：先试完整标签能放进一行，再试两行（常态两行，
    约 54px），两行放不下完整标签才把非激活项按等分线压缩——仍放不下用三行。
    换行交给 ``ui/adaptive_wrap_layout.py::AdaptiveWrapLayout``，行数预算与它
    共用 ``ui/adaptive_wrap_layout.py::wrap_rows``，不会两处各算各的。

    互斥按仓库既有做法手写 ``setChecked(False)``（不引 ``QButtonGroup``
    的跨版本信号差异），且**不允许全不选**——工作台总有一个当前任务。
    """

    task_selected = Signal(str)

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        _styled(self).setObjectName("WorkbenchTaskNav")
        self._buttons = {}  # 任务 id → 导航 chip（测试与 set_count 按此名取）
        self._counts = {}  # 任务 id → 任务自己的候选数
        self._count_units = {}  # 任务 id → 任务自己的数量单位
        self._groups = tuple(groups)
        self._animations = {}  # chip → 宽度动画（懒建，随本控件一起销毁）
        self._rows = None  # 目标行数（动画收尾后把高度收回它）
        self._laid_out_width = -1

        # 换行用现成的 AdaptiveWrapLayout（height-for-width、不拆原子项）
        wrap = AdaptiveWrapLayout(self, _CHIP_GAP, _CHIP_GAP)
        wrap.setContentsMargins(*_NAV_MARGINS)
        self._wrap = wrap
        for _title, task_ids in self._groups:
            for task_id in task_ids:
                chip = WorkbenchTaskChip(
                    _TASK_LABELS.get(task_id, task_id), _title, self
                )
                chip.clicked.connect(
                    lambda _checked=False, tid=task_id: self._on_clicked(tid)
                )
                wrap.addWidget(chip)
                self._buttons[task_id] = chip
        self._set_rows(1)

    # ── 交互 ────────────────────────────────────────────────────

    def _on_clicked(self, task_id: str):
        button = self._buttons[task_id]
        if button.isChecked():
            self.select(task_id)
        else:
            # 不允许取消当前任务（工作台必须有一个当前任务）
            button.setChecked(True)

    def select(self, task_id: str, *, emit: bool = True):
        """切到某任务。``emit=False`` 用于打开项目回默认任务。

        未知 id 直接忽略（不是导航里的任务就没有可切的页）。
        """
        if task_id not in self._buttons:
            return
        for other_id, button in self._buttons.items():
            button.blockSignals(True)
            button.setChecked(other_id == task_id)
            button.blockSignals(False)
        # 宽度跟着当前任务走：新激活项展开到完整标签，旧的自回可压缩宽度
        self._relayout()
        if emit:
            self.task_selected.emit(task_id)

    def current(self) -> str:
        for task_id, button in self._buttons.items():
            if button.isChecked():
                return task_id
        return ""

    def set_count(self, task_id: str, count, unit: str = "") -> None:
        """在导航 chip 上缀任务自己的数量与单位；0 不缀（只标"还有活"）。"""
        self._counts[task_id] = int(count or 0)
        self._count_units[task_id] = unit
        chip = self._buttons.get(task_id)
        if chip is None:
            return
        text = self._task_label(task_id)
        if chip.text() == text:
            return
        chip.set_full_text(text)
        # 标签变长可能改变分配（激活项始终要完整显示），故跟着重排一次
        self._relayout()

    def _task_label(self, task_id: str) -> str:
        return _with_count(
            _TASK_LABELS.get(task_id, task_id),
            self._counts.get(task_id, 0),
            self._count_units.get(task_id, ""),
        )

    # ── chip 宽度分配与换行 ─────────────────────────────────────

    def paintEvent(self, event):
        """导航底色 + 组间细分隔线。

        组标题行（"Source and image"／"Translation"）不再独占一行——它原先只是
        视觉分段，却和 chip 一样吃掉一整行高度。分组仍取自
        ``WORKBENCH_NAV_GROUPS``：线画在两组相邻 chip 之间的缝里（组名随该组
        chip 的 tooltip 露出）。分界正好落在换行处时**不画**——行首一条孤立的
        竖线反而像渲染故障。
        """
        painter = QPainter(self)
        painter.fillRect(
            self.rect(), _theme_color("@emptyContentBackgroundColor")
        )
        color = _theme_color("@borderColor", 150)  # 只是分组暗示线，不抢注意力
        for before_id, after_id in self._group_boundaries():
            before = self._buttons[before_id].geometry()
            after = self._buttons[after_id].geometry()
            if before.top() != after.top():
                continue  # 分界处正好换行
            x = before.right() + 1 + (_CHIP_GAP - _GROUP_LINE_WIDTH) // 2
            top = before.top() + (before.height() - _GROUP_LINE_HEIGHT) // 2
            painter.fillRect(
                QRect(x, top, _GROUP_LINE_WIDTH, _GROUP_LINE_HEIGHT), color
            )

    def _group_boundaries(self) -> list:
        """分组线要画在哪两个 chip 之间（每组末项 → 下一组首项）。"""
        return [
            (left_ids[-1], right_ids[0])
            for (_left_title, left_ids), (_right_title, right_ids) in zip(
                self._groups, self._groups[1:]
            )
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 只对**宽度**变化重排：高度就是本次分配的结果，跟着再排一次会打断动画
        if self.width() != self._laid_out_width:
            self._relayout(animate=False)

    def _flow_items(self) -> list:
        """流里的项目顺序＝导航顺序（分组线是画上去的，不占位、不占宽）。"""
        return [
            task_id for _title, task_ids in self._groups for task_id in task_ids
        ]

    def _available_width(self) -> int:
        margins = self._wrap.contentsMargins()
        return max(0, self.width() - margins.left() - margins.right())

    def _preferred_widths(self, widths: dict) -> list:
        return [widths[task_id] for task_id in self._flow_items()]

    def _pack(self, widths: dict, available: int) -> list:
        """打包成行（每行＝``_flow_items`` 的下标元组）；与布局同一个实现。"""
        return wrap_rows(self._preferred_widths(widths), available, _CHIP_GAP)

    def _row_count(self, widths: dict, available: int) -> int:
        return max(1, len(self._pack(widths, available)))

    def _row_slacks(self, widths: dict, available: int) -> "list[int]":
        """每行放完之后的剩余空白（贪心打包会把这些空白整段丢掉）。"""
        preferred = self._preferred_widths(widths)
        return [
            max(
                0,
                available
                - sum(preferred[index] for index in row)
                - _CHIP_GAP * (len(row) - 1),
            )
            for row in self._pack(widths, available)
        ]

    def _allocate(self, available: int) -> "tuple[dict, int]":
        """分配每个 chip 的宽度，返回 (宽度表, 目标行数)。

        ① **完整标签**能放进常态行数（两行）就一行都不省略，一行放得下就占一行；
        ② 完整标签两行装不下时，把非激活项按等分线压缩（**激活项始终占满自己的
           完整宽度**）去够两行——压缩档不从一行起试：把每一项都压成"…"的一行
           读不出是什么任务，不如多占一行；
        ③ 两行怎么压都放不下（激活项自己就超一行）才用三行，宽度尽量给足。
        """
        full = {tid: chip.full_width() for tid, chip in self._buttons.items()}
        active = self.current()
        if active not in full:
            active = next(iter(full))
        fixed = (len(self._flow_items()) - 1) * _CHIP_GAP
        rows = self._row_count(full, available)
        if rows <= _NAV_ROWS_NORMAL:
            return full, rows
        widths = self._fit_rows(full, active, available, _NAV_ROWS_NORMAL, fixed)
        if self._row_count(widths, available) <= _NAV_ROWS_NORMAL:
            return widths, _NAV_ROWS_NORMAL
        widths = self._fit_rows(full, active, available, _NAV_ROWS_MAX, fixed)
        return widths, self._row_count(widths, available)

    def _fit_rows(
        self, full: dict, active: str, available: int, rows: int, fixed: int
    ) -> dict:
        """在 ``rows`` 行预算里定宽度：按整额给，再把**行尾丢掉的空白**剪掉重给。

        贪心打包放不下就在行尾留下半行空白（下一项比空白宽），那一段不产生任何
        可用宽度，所以要按它下调预算、把这些宽度让给各项（等分线会分掉）。不剪
        的话预算看着够、实际排出来却多一行。
        """
        budget = rows * available - (rows - 1) * _CHIP_GAP - fixed
        widths = self._fill(full, active, budget)
        for _ in range(len(self._flow_items())):
            if self._row_count(widths, available) <= rows:
                break
            slack = sum(self._row_slacks(widths, available)[:-1])
            if slack <= 0:
                break
            budget = max(budget - slack, 0)
            widths = self._fill(full, active, budget)
        return widths

    def _fill(self, full: dict, active: str, budget: int) -> dict:
        """水填：激活项先占满自己的完整宽度，其余从压缩下限起按等分线抬升。

        激活项**不打折**（无论预算多紧都给完整宽度）：它是"当前页信息显示完整"
        的落点，压缩只针对非激活项。抬到各自完整宽度即停（能完整显示就不必再
        宽）；非激活项的总额不超过 ``budget``。
        """
        widths = {active: full[active]}
        rest = [tid for tid in full if tid != active]
        for task_id in rest:
            widths[task_id] = min(_CHIP_MIN_WIDTH, full[task_id])
        pool = budget - sum(widths.values())
        while pool > 0:
            hungry = [tid for tid in rest if widths[tid] < full[tid]]
            if not hungry:
                break
            share = max(1, pool // len(hungry))
            moved = 0
            for task_id in hungry:
                gain = min(share, full[task_id] - widths[task_id], pool - moved)
                if gain <= 0:
                    continue
                widths[task_id] += gain
                moved += gain
            if not moved:
                break
            pool -= moved
        return widths

    def _relayout(self, animate: bool = True):
        """按当前宽度与当前任务重新分配 chip 宽度，并把高度收到目标行数。"""
        width = self.width()
        self._laid_out_width = width
        if width <= 0:
            return
        available = self._available_width()
        widths, rows = self._allocate(available)
        if animate:
            # 展开／收回期间各项宽度在两端之间，行数可能比目标多一行：高度先按
            # 两端较大者给（动画收尾 ``_settle_rows`` 再收回），免得过程中被裁
            current = {
                task_id: chip.chipWidth for task_id, chip in self._buttons.items()
            }
            rows = max(rows, self._row_count(current, available))
        for task_id, chip in self._buttons.items():
            chip.target_width = widths[task_id]
            self._animate(chip, widths[task_id], animate)
        self._rows = rows
        self._set_rows(rows)

    def _set_rows(self, rows: int):
        """行数 → 固定高度（上下边距 + 各行 chip 高 + 行距）。"""
        rows = max(1, int(rows))
        height = (
            _NAV_MARGINS[1]
            + rows * _CHIP_HEIGHT
            + (rows - 1) * _CHIP_GAP
            + _NAV_MARGINS[3]
        )
        if height != self.height():
            self.setFixedHeight(height)

    def _animate(self, chip: WorkbenchTaskChip, target: int, animate: bool):
        """把 chip 的宽度移到目标值：开动画时补间，否则一步到位。

        关动画档（``pcfg.animation_fps < 0``，仓库既有门控）与离屏测试都不依赖
        补间：目标宽度随时可读（``WorkbenchTaskChip.target_width``），也不阻塞。
        """
        animation = self._animations.get(chip)
        if animation is not None:
            animation.stop()
        if not animate or pcfg.animation_fps < 0 or chip.chipWidth == target:
            chip.set_chip_width(target)
            return
        if animation is None:
            animation = QPropertyAnimation(chip, b"chipWidth", self)
            animation.setDuration(_CHIP_ANIM_MS)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(self._settle_rows)
            self._animations[chip] = animation
        animation.setStartValue(chip.chipWidth)
        animation.setEndValue(int(target))
        animation.start()

    def _any_animation_running(self) -> bool:
        return any(
            animation.state() == QAbstractAnimation.State.Running
            for animation in self._animations.values()
        )

    def _settle_rows(self):
        """动画收尾：宽度都到位了，把高度收回目标行数（过程中可能多占一行）。"""
        if self._rows is not None and not self._any_animation_running():
            self._set_rows(self._rows)




class GlossaryAgentWorker(QObject):
    """worker 线程内的权威草稿 + LLM 会话执行体。

    面板的所有操作入口都经 *:*_requested 信号跨线程排队(QueuedConnection)
    触发——直接方法调用会在调用者(主)线程同步执行,长任务会冻结 UI;
    唯一例外 request_stop:只写取消标志,直调让检查点尽早生效。
    """

    log_line = Signal(str)
    busy_changed = Signal(bool)
    glossary_synced = Signal(list)  # [(src, dst, note, origin)]
    story_synced = Signal(str, list)  # (synopsis, [(page, summary, origin)])
    round_finished = Signal(str)  # 最终回复(可为空)
    round_failed = Signal(str)
    applied = Signal(str)

    # 面板 → worker 操作入口(经队列在 worker 线程执行)
    instruction_requested = Signal(str)
    prefill_requested = Signal()
    glossary_edit_requested = Signal(str, str, str)
    glossary_remove_requested = Signal(str)
    synopsis_edit_requested = Signal(str)
    summary_edit_requested = Signal(str, str)
    summary_remove_requested = Signal(str)
    glossary_apply_requested = Signal(str)
    story_apply_requested = Signal()

    def __init__(self, proj, parent=None):
        super().__init__(parent)
        self._proj = proj
        self._thread = None
        self._translator = None
        self._cancel = False
        self._busy = False
        self._history_tail = None
        self.glossary = GlossaryDraft()
        self.story = StoryDraft()

    # ── 线程生命周期(由面板驱动) ──────────────────────────────

    def start_in_thread(self, thread: QThread):
        self._thread = thread
        self.moveToThread(thread)
        thread.started.connect(self.initialize)
        thread.start()

    def shutdown(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)

    # ── 槽:初始化 / 用户操作 ──────────────────────────────────

    @Slot()
    def initialize(self):
        """打开工作台时的基底载入:现有数据进草稿,无第二份数据。"""
        path = ""
        try:
            from utils.config import pcfg

            path = pcfg.module.llm_glossary_path or ""
            entries = load_glossary(path) if path else ()
        except Exception as e:
            entries = ()
            self.log_line.emit(
                self.tr("Failed to load glossary '%1': %2").replace("%1", path)
                .replace("%2", str(e))
            )
        self.glossary = GlossaryDraft.from_entries(
            [(e.source, e.translation, e.note) for e in entries]
        )
        synopsis = getattr(self._proj, SYNOPSIS_KEY, "") or ""
        pages, synopsis = load_story_base(
            getattr(self._proj, "_image_info", {}), synopsis
        )
        self.story = StoryDraft.from_base(pages, synopsis)
        self._sync_all()
        self.log_line.emit(
            self.tr("Draft loaded: %1 glossary entries, %2 page summaries.")
            .replace("%1", str(len(self.glossary.entries)))
            .replace("%2", str(len(pages)))
        )

    @Slot(str)
    def run_instruction(self, text: str):
        if self._busy:
            return
        self._busy = True
        self._cancel = False
        self.busy_changed.emit(True)
        try:
            self._run_instruction(text)
        except Exception as e:
            logger.exception("context agent round failed")
            self.round_failed.emit(f"{type(e).__name__}: {e}")
        finally:
            self._busy = False
            self.busy_changed.emit(False)

    @Slot()
    def request_stop(self):
        self._cancel = True

    @Slot()
    def prefill_from_frequency(self):
        """频率启发式预填充:工具产物作基底数据,冲突仍由人裁决。"""
        try:
            rows = extract_by_frequency(self._proj)
        except Exception as e:
            self.round_failed.emit(f"Prefill failed: {e}")
            return
        result = self.glossary.apply_patch(
            [
                {"action": "add", "src": src, "dst": dst, "info": ""}
                for src, dst, _ in rows
            ]
        )
        # 预填充是工具行为而非 AI 建议:撞 existing 的行按 existing 处理
        for row in result["conflicts"]:
            entry = self._find_entry(row.get("src"))
            if entry is not None and row.get("reason") == "user_owned":
                entry.origin = ORIGIN_EXISTING
        self._sync_all()
        self.log_line.emit(
            self.tr("Prefill: %1 rows merged, %2 skipped.")
            .replace("%1", str(len(rows) - len(result["errors"])))
            .replace("%2", str(len(result["errors"])))
        )

    @Slot(str, str, str)
    def user_glossary_edit(self, src: str, dst: str, note: str):
        try:
            self.glossary.set_user_entry(src, dst, note)
        except Exception as e:
            self.round_failed.emit(str(e))
        self._sync_all()

    @Slot(str)
    def user_glossary_remove(self, src: str):
        self.glossary.remove_user_entry(src)
        self._sync_all()

    @Slot(str)
    def user_synopsis_edit(self, text: str):
        self.story.set_user_synopsis(text)
        self._sync_story()

    @Slot(str, str)
    def user_summary_edit(self, page: str, summary: str):
        self.story.set_user_summary(page, summary)
        self._sync_story()

    @Slot(str)
    def user_summary_remove(self, page: str):
        try:
            self.story.apply_patch([{"action": "remove", "page": page}])
        except Exception as e:
            self.round_failed.emit(str(e))
        self._sync_story()

    @Slot(str)
    def apply_glossary(self, path: str):
        """唯一落盘路径:草稿 → 活动术语表文件(json)。"""
        if not path:
            self.round_failed.emit("No glossary path configured.")
            return
        rows = [
            {"src": e.source, "dst": e.translation, "info": e.note}
            for e in self.glossary.entries
        ]
        try:
            import os

            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(rows, ensure_ascii=False, indent=2))
        except Exception as e:
            self.round_failed.emit(f"Save failed: {e}")
            return
        self.applied.emit(self.tr("Glossary saved: %1").replace("%1", path))

    @Slot()
    def apply_story(self):
        """唯一落盘路径:草稿 → 项目内存结构(随项目保存持久化)。"""
        pages, synopsis = self.story.snapshot()
        image_info = getattr(self._proj, "_image_info", {})
        for p in pages:
            info = image_info.setdefault(p.page_name, {})
            info[PAGE_SUMMARY_KEY] = p.summary
        setattr(self._proj, SYNOPSIS_KEY, synopsis)
        self.applied.emit(
            self.tr("Story context applied to project (%1 pages).")
            .replace("%1", str(len(pages)))
        )

    # ── 内部:一次指令轮 ──────────────────────────────────────

    def _run_instruction(self, text: str):
        from utils.config import pcfg

        translator = self._ensure_translator()
        api_key = translator._select_api_key()
        if not api_key:
            from utils.profile_manager import profile_usage_hint

            raise RuntimeError(
                "No available API key. "
                + profile_usage_hint(translator._active_profile.get("name", ""))
                + ". Configure it in Model Management."
            )
        if not translator.client or translator.client.api_key != api_key:
            if not translator._initialize_client(api_key):
                raise RuntimeError("Failed to initialize API client.")
        model = translator._effective_model
        if not model:
            raise RuntimeError("No model configured in the active profile.")

        pages, synopsis = self.story.snapshot()
        system_message = build_system_prompt(
            translator._translated_lang(pcfg.module.translate_target),
            has_glossary_base=bool(self.glossary.entries),
            has_story_base=bool(pages),
            n_pages=len(self._proj.pages),
            synopsis=synopsis or None,
        )
        tools_openai = build_context_tools(with_story=True)
        result: SessionResult = run_agent_session(
            translator._agent_chat,
            self._execute_tool,
            system_message=system_message,
            user_message=text,
            tools_openai=tools_openai,
            max_turns=12,
            token_budget=0,
            cancel_check=lambda: self._cancel,
            status_cb=self._on_turn,
            log=lambda m: self.log_line.emit(m),
            history_tail=self._history_tail,
        )
        self._history_tail = trim_session_messages(result.messages)[1:]
        self._sync_all()
        if result.stopped_reason == STOP_CANCELLED:
            self.round_failed.emit(_STOP_REASONS[STOP_CANCELLED])
        elif result.stopped_reason in _STOP_REASONS and not result.reply:
            self.round_failed.emit(_STOP_REASONS[result.stopped_reason])
        else:
            self.round_finished.emit(result.reply)

    def _ensure_translator(self):
        if self._translator is None:
            from utils.config import pcfg
            from modules.translators.trans_agent import AgentTranslator

            self._translator = AgentTranslator(
                pcfg.module.translate_source,
                pcfg.module.translate_target,
                raise_unsupported_lang=False,
            )
        return self._translator

    def _execute_tool(self, name, arguments):
        return execute_context_tool(
            name,
            arguments,
            project=self._proj,
            glossary_draft=self.glossary,
            story_draft=self.story,
        )

    def _on_turn(self, turn, tool_names, usage):
        self.log_line.emit(
            self.tr("— turn %1: %2").replace(
                "%1", str(turn)
            ).replace("%2", ", ".join(tool_names) or "reply")
        )

    def _sync_all(self):
        self.glossary_synced.emit(
            [
                (e.source, e.translation, e.note, e.origin)
                for e in self.glossary.entries
            ]
        )
        self._sync_story()

    def _sync_story(self):
        pages, synopsis = self.story.snapshot()
        self.story_synced.emit(
            synopsis,
            [(p.page_name, p.summary, p.origin) for p in pages],
        )

    def _find_entry(self, src):
        key = (src or "").casefold()
        for e in self.glossary.entries:
            if e.source.casefold() == key:
                return e
        return None


class GlossaryAgentPanel(QWidget):
    """主窗口左侧栏内嵌的泛用工作台（与全局搜索同槽位、更宽）。"""

    # 队列 → 画布跳转（D26：界面不做翻页控件，交给主窗口的页链路）
    jump_requested = Signal(str, int)
    # 待办队列 → 直接开框级确认卡（交接 §4.2③⑥：与「跳画布」并列的第二条出口）
    action_requested = Signal(str, int, str)
    # 撤回最近一次批量操作（D4／D35：整体一条撤回）
    rollback_requested = Signal(int)

    def __init__(self, proj, parent=None):
        super().__init__(parent)
        self._proj = proj
        self._syncing = False
        self._worker = None
        self._thread = None
        self._batch_tasks = {}
        self._batch_views = {}
        # 非删除待办队列（原文待校对／译文待重译）：数据侧与视图分开存
        self._review_tasks = {}
        self._review_views = {}
        # 任务 id → self.pages 下标（构建时登记，见 _page_index）
        self._page_indices = {}
        # 审批预览浮层（D44）：懒建，挂在主窗口中央区上（工作台之外）
        self._preview_panel = None
        # 首次切入某任务时才规划它（列表懒规划）；批量写回／回滚／换项目后
        # 全部重新标脏。待办队列同样参与（读取便宜，但口径保持一致）。
        self._dirty_tasks = set(CLEANUP_TASK_IDS) | {OCR_REVIEW, TRANS_REVIEW}
        self._last_version_seq = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 未打开项目时空态页接管全部交互(page 0);内容页在 page 1。
        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack, 1)

        empty_page = QWidget(self)
        _styled(empty_page).setObjectName("WorkbenchSurface")
        empty_lay = QVBoxLayout(empty_page)
        empty_lay.addStretch(1)
        empty_hint = QLabel(
            self.tr("Open a project to use the workbench."),
            empty_page,
        )
        empty_hint.setObjectName("WorkbenchEmptyHint")
        empty_hint.setWordWrap(True)
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_hint)
        empty_lay.addStretch(1)
        self._stack.addWidget(empty_page)

        content_page = QWidget(self)
        _styled(content_page).setObjectName("WorkbenchSurface")
        content_lay = QVBoxLayout(content_page)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(4)

        # ① 扁平任务导航（批次 C/D：六项 chip 流按需换行，分组只画细分隔线）
        self.nav = WorkbenchTaskNav(WORKBENCH_NAV_GROUPS, content_page)
        self.nav.task_selected.connect(self._on_task_selected)
        content_lay.addWidget(self.nav)

        # ②③ 候选列表 + 执行（每个任务自己一整页）
        self.pages = QStackedWidget(content_page)
        content_lay.addWidget(self.pages, 1)
        self._build_batch_pages()
        self._build_review_pages()
        self._build_translation_prep_page()

        # 底部：批量撤回 + worker 日志落点（砍 Chat 后的新落点）
        content_lay.addWidget(self._build_bottom_row(content_page))

        self._stack.addWidget(content_page)
        self._stack.setCurrentIndex(1 if self.has_project() else 0)

        # 默认停在第一个任务（用户工作流顺序）；emit=False 不触发任务切换动作
        self._current_task = ""
        self.nav.select(WORKBENCH_ORDER[0], emit=False)
        self._current_task = WORKBENCH_ORDER[0]
        self._update_rollback_scope()

        self._connect_signals()
        QApplication.instance().aboutToQuit.connect(self._shutdown)

    # ── 页面构建 ────────────────────────────────────────────────

    def _build_batch_pages(self):
        op = self._batch_op()
        self._batch_tasks = build_batch_tasks(
            self._proj,
            op,
            inpainter_provider=self._inpainter_provider,
            on_changed=self._mark_project_changed,
        )
        for task_id in CLEANUP_TASK_IDS:
            task = self._batch_tasks[task_id]
            # pre_replan＝重扫前的前置对齐（修「刷新按钮读旧数据」）：本层
            # 拿不到主窗口，对齐口子由面板注入
            view = BatchTaskView(task, pre_replan=self._sync_before_plan)
            view.jump_requested.connect(self.jump_requested)
            view.notify_requested.connect(
                lambda text, kind="info", tid=task_id: self._toast(text, kind, tid)
            )
            view.status_requested.connect(
                lambda text, tid=task_id: self._append_log(text, tid)
            )
            view.batch_applied.connect(self._on_batch_applied)
            view.preview_requested.connect(self._on_preview_requested)
            view.preview_dismissed.connect(self._on_preview_dismissed)
            # 参数一变候选就变（框扩张的扩张量），导航上的计数不能停在旧值
            view.plan_changed.connect(self._refresh_nav_counts)
            self._batch_views[task_id] = view
            self.pages.addWidget(view)
            self._page_indices[task_id] = self.pages.count() - 1

    def _build_review_pages(self):
        """两个**非删除**待办队列页（原文待校对／译文待重译）。

        它们的出口只有「跳画布处理」与「从列表移除／忽略」，故走
        ``ui/workbench_review_view.py::ReviewQueueView``，不进批量任务的
        勾选—执行—版本撤回那条链路（批次 C 交接 §4.2③⑥）。
        """
        self._review_tasks = build_review_tasks(
            self._proj, on_changed=self._mark_project_changed
        )
        for task_id in (OCR_REVIEW, TRANS_REVIEW):
            task = self._review_tasks[task_id]
            view = ReviewQueueView(task, pre_replan=self._sync_before_plan)
            view.jump_requested.connect(self.jump_requested)
            view.notify_requested.connect(
                lambda text, kind="info", tid=task_id: self._toast(text, kind, tid)
            )
            view.status_requested.connect(
                lambda text, tid=task_id: self._append_log(text, tid)
            )
            view.action_requested.connect(self.action_requested)
            view.preview_requested.connect(self._on_preview_requested)
            view.preview_dismissed.connect(self._on_preview_dismissed)
            view.plan_changed.connect(self._refresh_nav_counts)
            self._review_views[task_id] = view
            self.pages.addWidget(view)
            self._page_indices[task_id] = self.pages.count() - 1

    def _build_glossary_page(self):
        """术语草稿子页（翻译准备页的第一个子页）。"""
        page = QWidget(self)
        _styled(page).setObjectName("WorkbenchSurface")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 6, 4)
        self.glossary_table = QTableWidget(0, 4, page)
        self.glossary_table.setHorizontalHeaderLabels(
            [
                self.tr("Source"),
                self.tr("Translation"),
                self.tr("Note"),
                self.tr("Origin"),
            ]
        )
        header = self.glossary_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.glossary_table.verticalHeader().setVisible(False)
        layout.addWidget(self.glossary_table, 1)
        button_row = QHBoxLayout()
        self.prefill_btn = QPushButton(self.tr("Extract by frequency"), page)
        self.prefill_btn.setToolTip(
            self.tr(
                "Scan all pages' existing translations and merge recurring source→translation pairs into the draft. No AI involved — use \"Prepare for translation…\" to ask the AI for the rest."
            )
        )
        self.glossary_remove_btn = QPushButton(self.tr("Remove selected"), page)
        button_row.addWidget(self.prefill_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.glossary_remove_btn)
        layout.addLayout(button_row)
        self._glossary_page = page

    def _build_story_page(self):
        """剧情草稿子页（翻译准备页的第二个子页）。"""
        page = QWidget(self)
        _styled(page).setObjectName("WorkbenchSurface")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel(self.tr("Global synopsis"), page))
        self.synopsis_edit = ConfigTextEdit(page)
        self.synopsis_edit.setFixedHeight(72)
        layout.addWidget(self.synopsis_edit)
        layout.addWidget(QLabel(self.tr("Page summaries"), page))
        self.story_table = QTableWidget(0, 3, page)
        self.story_table.setHorizontalHeaderLabels(
            [self.tr("Page"), self.tr("Summary"), self.tr("Origin")]
        )
        s_header = self.story_table.horizontalHeader()
        s_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.story_table.verticalHeader().setVisible(False)
        layout.addWidget(self.story_table, 1)
        self.story_remove_btn = QPushButton(self.tr("Remove selected"), page)
        layout.addWidget(self.story_remove_btn, 0, Qt.AlignmentFlag.AlignRight)
        self._story_page = page

    def _build_translation_prep_page(self):
        """翻译准备：**一个导航入口**，术语与剧情各留自己的草稿与应用语义。

        ``GlossaryAgentWorker`` 仍是两块的权威草稿持有者，落盘仍是两条独立
        路径（术语 → json 文件；剧情 → 项目内存），「应用草稿…」按既有语义
        同时发这两条请求（D9／设计 §9——**不合并写盘事务、不合并 worker**）。
        本入口只是把两块导航收成一项（批次 C 交接 §4.2⑤）。
        """
        page = QWidget(self)
        _styled(page).setObjectName("WorkbenchSurface")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(
            _page_hint(
                self.tr(
                    "Translation preparation: glossary entries and story context, each with its own draft. Nothing is saved until you apply the drafts."
                ),
                page,
            )
        )
        self._build_glossary_page()
        self._build_story_page()
        self._prep_tabs = QTabWidget(page)
        self._prep_tabs.setObjectName("WorkbenchPrepTabs")
        self._prep_tabs.addTab(self._glossary_page, _TASK_LABELS[GLOSSARY])
        self._prep_tabs.addTab(self._story_page, _TASK_LABELS[STORY])
        self._prep_tab_indices = {GLOSSARY: 0, STORY: 1}
        layout.addWidget(self._prep_tabs, 1)
        # 两个「耗时/耗费」动作与唯一落盘出口属于整页：应用同时落术语与剧情
        self.prepare_btn = QPushButton(self.tr("Prepare for translation…"), page)
        self.prepare_btn.setToolTip(
            self.tr(
                "One-click warmup: scan existing translations, then ask the AI to fill in missing glossary entries and page summaries. A confirmation lists the steps (and API cost) first."
            )
        )
        self.stop_btn = QPushButton(self.tr("Stop"), page)
        self.apply_btn = QPushButton(self.tr("Apply draft…"), page)
        self.apply_btn.setToolTip(
            self.tr(
                "Applies both drafts: the glossary goes to its json file, the story context into the project. This is not part of the batch rollback."
            )
        )
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.prepare_btn)
        apply_row.addWidget(self.stop_btn)
        apply_row.addStretch(1)
        apply_row.addWidget(self.apply_btn)
        layout.addLayout(apply_row)
        self.pages.addWidget(page)
        self._page_indices[TRANSLATION_PREP] = self.pages.count() - 1

    def _update_rollback_scope(self):
        """「撤销上次批量」只在**批量任务**情境露出（批次 C 交接 §4.2 末段）。

        它撤的是 ``utils/batch_versions.py`` 里的**最近一版批量操作**，
        与术语／剧情草稿的应用（两条独立落盘路径）和单框卡片的写回都无关；
        待办队列更没有版本。故只在当前任务属于批量族且确有版本时露出并可用，
        切到别的任务即收起（版本号仍留着，切回来还能撤）。
        """
        usable = (
            self._last_version_seq is not None
            and self.current_task() in CLEANUP_TASK_IDS
        )
        self.rollback_btn.setVisible(usable)
        self.rollback_btn.setEnabled(usable)

    def _build_bottom_row(self, parent) -> QWidget:
        """底部状态条：只读日志区 + 批量撤回钮。

        日志是**只读状态区，不是输入框**（D43）——``ConfigTextEdit`` 自带领边
        输入框外观，这里用 ``QTextEdit#WorkbenchLogView`` 规则覆盖成平底色，
        并靠 ``WorkbenchStatusBar`` 的顶边线与上面的候选列表分开。
        """
        holder = _styled(QWidget(parent))
        holder.setObjectName("WorkbenchStatusBar")
        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 4, 6, 6)
        row.setSpacing(6)
        self._log_view = ConfigTextEdit(parent)
        self._log_view.setObjectName("WorkbenchLogView")
        self._log_view.setReadOnly(True)
        self._log_view.setFrameShape(QFrame.Shape.NoFrame)
        self._log_view.setFixedHeight(52)
        self._log_view.document().setMaximumBlockCount(200)
        row.addWidget(self._log_view, 1)
        self.rollback_btn = QPushButton(self.tr("Undo last batch"), holder)
        self.rollback_btn.setObjectName("WorkbenchRollbackButton")
        self.rollback_btn.setToolTip(
            self.tr(
                "Rolls the project back to the state before the last batch action (merge / delete / background fill). Only batch actions are covered — applying the translation drafts or a single-block card is not part of it. The version is consumed, so it can be undone once."
            )
        )
        # 显隐由 _update_rollback_scope 定（非批量任务页不露出：它撤不了那些操作）
        self.rollback_btn.setVisible(False)
        self.rollback_btn.setEnabled(False)
        row.addWidget(self.rollback_btn, 0, Qt.AlignmentFlag.AlignTop)
        return holder

    # ── 主窗口接线 ──────────────────────────────────────────────

    def _mainwindow(self):
        """主窗口（提供 D40 前置对齐、落盘、模块实例、批量回滚）。"""
        window = self.window()
        if window is None or not hasattr(window, "_sync_block_data"):
            return None
        return window

    def _batch_op(self):
        """批量事务外壳：落盘 + 版本 + D40 前置对齐（设计 §8）。"""
        from ui.batch_ops import BatchOperation

        window = self._mainwindow()
        commit = getattr(window, "_sync_and_commit_project", None)
        sync = getattr(window, "_sync_block_data", None)
        return BatchOperation(self._proj, commit=commit, sync_block_data=sync)

    def _sync_before_plan(self):
        """重扫（``replan``）前的前置对齐：把画布/面板上未回写的编辑冲进
        ``proj.pages``，刷新按钮读到的才是当前数据（2026-09-23 实测「刷新
        无效」的根因：``plan`` 直读数据层，而手动增删框／键入只落在视觉层）。

        两道判据各管一类，都是现成口子：``text_change_unsaved`` 门控
        ``updateTextBlkList``（纯文字键入——对象身份不变，
        ``page_data_needs_sync`` 查不出）；``_sync_block_data`` 兜结构性增删
        （加框/删框改长度与对象身份）。只需对齐当前页：其余页在切页时已被
        ``conditional_save`` 冲刷。对齐失败不挡重扫（记日志后照常规划，
        与旧行为一致）。
        """
        window = self._mainwindow()
        if window is None:
            return
        canvas = getattr(window, "canvas", None)
        st_manager = getattr(window, "st_manager", None)
        if canvas is not None and st_manager is not None:
            try:
                if canvas.text_change_unsaved():
                    st_manager.updateTextBlkList()
            except Exception as error:
                logger.error(f"Pre-replan text flush failed: {error}")
        try:
            window._sync_block_data()
        except Exception as error:
            logger.error(f"Pre-replan block sync failed: {error}")

    def mark_content_changed(self, *_):
        """画布内容改动过（``canvas.content_modified`` 广播）：给**已规划出
        列表**的任务页亮「列表可能过时」，并顺手刷新**便宜计数**。

        只亮灯不重扫（精简取向：不做数据变更信号的全量监听），出口仍是各页
        刷新钮——``replan`` 熄灯。未规划的页没有可过时的列表，不打扰；
        翻译准备页无候选列表概念，不参与。

        计数照刷：现场处理完一个框（接受 AI 草稿会清掉该块的问题记录）、
        或在画布上打了「稍后校对／稍后重译」，工作台就在同屏——导航上的数字
        不该停在旧值。这里只刷纯内存可数的三个队列（见 ``_CHEAP_COUNT_TASKS``），
        要跑引擎 plan 的任务留给切任务与重规划。
        """
        for view in (
            list(self._batch_views.values()) + list(self._review_views.values())
        ):
            view.mark_stale()
        self._refresh_nav_counts(cheap_only=True)

    def _on_preview_requested(self, pixmap, caption: str):
        """把审批图交给浮层显示（D44：预览已不在任务页里）。"""
        self._ensure_preview_panel().show_content(pixmap, caption)

    def _on_preview_dismissed(self):
        """收起审批浮层：行集重建或换任务后，旧图对应的行可能已不存在。"""
        if self._preview_panel is not None:
            self._preview_panel.close_panel()

    def _on_preview_closed(self):
        """用户关掉浮层（点画布／Esc）：转达给各任务视图"预览已收起"。

        视图不这么做的话，**只有一行候选时**关掉浮层后再点那行会没反应
        （选中行没变 → 不发 ``itemSelectionChanged`` → 浮层再也弹不出来）。
        """
        for view in (
            list(self._batch_views.values()) + list(self._review_views.values())
        ):
            view.forget_preview()

    def _ensure_preview_panel(self):
        """预览浮层的宿主＝主窗口中央区（与画布浮层同款，能盖在画布上、
        跟着窗口走、不占工作台宽度）。拿不到中央区时退回主窗口本身。"""
        if self._preview_panel is None:
            from ui.workbench_preview import WorkbenchPreviewPanel

            window = self._mainwindow()
            host = (
                getattr(window, "centralStackWidget", None)
                or window
                or self.window()  # 兜底：拿不到主窗口时别让浮层变成独立窗口
            )
            self._preview_panel = WorkbenchPreviewPanel(host)
            # 用户关掉浮层（点画布／Esc）→ 任务视图要知道，否则"再点那一行"
            # 不会重新取图（表格的选中行没变，不发 itemSelectionChanged）
            self._preview_panel.closed.connect(self._on_preview_closed)
        return self._preview_panel

    def _inpainter_provider(self):
        window = self._mainwindow()
        manager = getattr(window, "module_manager", None)
        return getattr(manager, "inpainter", None)

    def _mark_project_changed(self):
        """标签表态等程序外修改：置未保存位（标签不进撤销栈，D28）。"""
        window = self._mainwindow()
        canvas = getattr(window, "canvas", None)
        if canvas is not None:
            try:
                canvas.setProjSaveState(True)
            except Exception as error:  # 画布未就绪时不该打断队列操作
                logger.error(f"Failed to mark project dirty: {error}")

    def _toast(self, text: str, kind: str = "info", task_id: str = ""):
        """面板内通知：批量回执与失败提示（kind 见通知中心的 KIND_STYLES）。"""
        from ui.custom_widget import notification

        try:
            notification.toast(text, kind=kind, anchor="bottom-left")
        except Exception as error:
            logger.error(f"workbench toast failed: {error}")
        self._append_log(text, task_id)

    def _append_log(self, text: str, task_id: str = ""):
        """worker 日志与任务回执的落点（D19：砍 Chat 后日志不再无处可去）。

        日志是**跨任务共用**的一小块区域（D43），故按来源署名：批量任务与
        待办队列的行前缀自己的任务名（否则在翻译准备页读到「删除 2 个框」
        不知道是谁干的），术语／剧情的草稿与会话行归「翻译准备」名下，不再
        以无归属的裸行出现。页面级的 plan 摘要不写这里（它就在页面上）。
        """
        if not text:
            return
        label = _TASK_LABELS.get(task_id, "")
        if label:
            color = get_theme_color(key="@disabledForegroundColor").name()
            # 署名与正文之间要有分隔符（纯标点，中英文都读得顺），否则两段
            # 文字粘成一句：「Prepare for translation Draft loaded: …」
            text = (
                '<span style="color:' + color + '">' + html.escape(label)
                + "</span>&nbsp;·&nbsp;" + html.escape(text)
            )
        # document 已设 maximumBlockCount，超长自动丢弃最旧的行
        self._log_view.append(text)
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── 任务导航 ────────────────────────────────────────────────

    def _page_index(self, task_id: str) -> int:
        """任务 → ``self.pages`` 下标（构建时登记，不靠"添加顺序"的算术）。"""
        return self._page_indices[task_id]

    def current_task(self) -> str:
        return self.nav.current()

    def select_prep_tab(self, task_id: str) -> None:
        """切到翻译准备页里的某个子页（``GLOSSARY``／``STORY``）；未知 id 忽略。

        翻译准备是**一个导航入口**，两块草稿只是它内部的两个子页，不占导航位，
        故不走 ``nav.select`` 的任务切换语义（那会走懒规划与计数刷新）。
        """
        index = self._prep_tab_indices.get(task_id)
        if index is None:
            return
        if self.current_task() != TRANSLATION_PREP:
            self.nav.select(TRANSLATION_PREP)
        self._prep_tabs.setCurrentIndex(index)

    def _on_task_selected(self, task_id: str):
        if task_id == getattr(self, "_current_task", ""):
            return
        self._on_preview_dismissed()  # 换了任务，上一页的审批图不再对应当前列表
        self._current_task = task_id
        self.pages.setCurrentIndex(self._page_index(task_id))
        if task_id == TRANSLATION_PREP:
            # 术语／剧情的草稿在 worker 线程里（懒建、载入基底），与批量任务无关
            self._ensure_worker()
        else:
            self._ensure_current_planned()
        self._refresh_nav_counts()
        self._update_rollback_scope()

    def _ensure_current_planned(self):
        """当前任务若标脏（还没规划过／数据变过）就跑一次只读 ``plan``。

        批量任务与待办队列共用这一入口——两者都是懒规划的快照，差别只在
        后者没有 ``options``（队列没有参数）。
        """
        current = self.current_task()
        if current not in self._dirty_tasks:
            return
        view = self._batch_views.get(current) or self._review_views.get(current)
        if view is None:
            return
        self._dirty_tasks.discard(current)
        view.replan()

    def _refresh_nav_counts(self, cheap_only: bool = False):
        """刷新导航钮上的计数：每项只报**自己可解释的数量与单位**。

        ``cheap_only`` ＝ 只刷纯内存能数的队列（画布内容一变就会被调用，
        见 ``mark_content_changed``），跑引擎 plan 的任务跳过、保留旧值。

        - 批量任务／待办队列：数量取自任务自己的 ``pending``（可疑框＝待审校
          条数、合并＝组数、修复＝页、待办＝人工记下的条数），单位取自
          ``count_label``；
        - 程序低置信度**建议**不参与待办队列的计数（它不冒充用户承诺的工作），
          只在页内摘要里报数；
        - 翻译准备是探索性入口，没有"未处理条目"的概念，不计数。
        """
        for task_id in WORKBENCH_ORDER:
            task = self._batch_tasks.get(task_id) or self._review_tasks.get(task_id)
            if task is None:
                self.nav.set_count(task_id, 0)
                continue
            if cheap_only and task_id not in _CHEAP_COUNT_TASKS:
                continue
            view = self._batch_views.get(task_id) or self._review_views.get(task_id)
            try:
                # 只有批量任务有参数（扩张量等）；待办队列按 ``options=None`` 计
                options = view.options() if task_id in self._batch_views else None
                count = task.pending(options)
            except Exception as error:
                logger.error(f"Nav count failed for {task_id}: {error}")
                continue
            self.nav.set_count(task_id, count, task.count_label())

    # ── 项目状态 ────────────────────────────────────────────────

    def has_project(self) -> bool:
        """未打开项目(directory 为空)时工作台不可用。"""
        return bool(getattr(self._proj, "directory", None))

    def refresh_project_state(self):
        """项目打开/切换后刷新:空态 ⇄ 内容页,worker 与批量任务重建。"""
        if self._worker is not None:
            self._shutdown()
        if self._preview_panel is not None:
            self._preview_panel.close_panel()  # 换项目：旧页的审批图已过期
        self._dirty_tasks = set(CLEANUP_TASK_IDS) | {OCR_REVIEW, TRANS_REVIEW}
        self._last_version_seq = None
        for task in self._batch_tasks.values():
            task.reset()
        for task in self._review_tasks.values():
            task.reset()
        for view in (
            list(self._batch_views.values()) + list(self._review_views.values())
        ):
            view.forget_plan()  # 旧项目的列表与过时灯都不该带进新项目
        if self.has_project():
            self._stack.setCurrentIndex(1)
            self.nav.select(WORKBENCH_ORDER[0], emit=False)
            self._current_task = WORKBENCH_ORDER[0]
            self.pages.setCurrentIndex(self._page_index(WORKBENCH_ORDER[0]))
            if self.isVisible():
                self._ensure_worker()
                # 标脏位只由 *它* 清（``_ensure_current_planned``）：面板不可见
                # （打开项目时工作台通常还没开）就跳过规划，此处先前无条件清掉
                # 会让首个任务"以为已规划过"，之后 showEvent 也不再补——列表
                # 永远空白，而导航计数照旧（2026-09-18 实测：误识别清理 15 条
                # 却点进去什么都没有）。
                self._ensure_current_planned()
            self._refresh_nav_counts()
        else:
            self._stack.setCurrentIndex(0)
        self._update_rollback_scope()

    # ── 信号接线 ────────────────────────────────────────────────

    def _connect_signals(self):
        self.stop_btn.clicked.connect(self._on_stop)
        self.prefill_btn.clicked.connect(self._on_prefill)
        self.prepare_btn.clicked.connect(self._on_prepare)
        self.glossary_remove_btn.clicked.connect(self._on_glossary_remove)
        self.story_remove_btn.clicked.connect(self._on_story_remove)
        self.glossary_table.itemChanged.connect(self._on_glossary_item_changed)
        self.story_table.itemChanged.connect(self._on_story_item_changed)
        self.apply_btn.clicked.connect(self._on_apply)
        self.rollback_btn.clicked.connect(self._on_rollback)
        self.synopsis_edit.installEventFilter(self)

    def showEvent(self, event):
        """工作台首次露出时即建 worker 并载入基底(草稿不需要等首条指令);
        未打开项目时保持空态页,不建 worker。"""
        super().showEvent(event)
        if self.has_project():
            self._ensure_worker()
            self._ensure_current_planned()

    def eventFilter(self, obj, event):
        """synopsis 焦点离开时提交到 worker。"""
        from qtpy.QtCore import QEvent

        if (
            obj is self.synopsis_edit
            and event.type() == QEvent.Type.FocusOut
            and not self._syncing
        ):
            self._on_synopsis_edited()
        return super().eventFilter(obj, event)

    def _ensure_worker(self):
        if self._worker is not None:
            return self._worker
        self._thread = QThread(self)
        self._worker = GlossaryAgentWorker(self._proj)
        self._worker.start_in_thread(self._thread)
        self._wire_worker(self._worker)
        return self._worker

    def _wire_worker(self, worker: "GlossaryAgentWorker"):
        """worker ↔ UI 接线(测试可直接调用 _wire_worker 以绕过线程)。"""
        # worker → UI。日志行**归到「翻译准备」名下**（D43 的作用域）：底部日志
        # 是跨任务共用的，草稿／会话的行不署名的话，在别的任务页读到
        # 「Draft loaded: …」会以为是当前任务的回执。
        worker.log_line.connect(
            lambda text: self._append_log(text, TRANSLATION_PREP)
        )
        worker.busy_changed.connect(self._on_busy_changed)
        worker.glossary_synced.connect(self._sync_glossary_table)
        worker.story_synced.connect(self._sync_story_tab)
        worker.round_finished.connect(self._on_round_finished)
        worker.round_failed.connect(self._on_round_failed)
        worker.applied.connect(
            lambda text: self._append_log(text, TRANSLATION_PREP)
        )
        # UI → worker 操作入口(跨线程 Queued,长任务不占主线程)
        worker.instruction_requested.connect(worker.run_instruction)
        worker.prefill_requested.connect(worker.prefill_from_frequency)
        worker.glossary_edit_requested.connect(worker.user_glossary_edit)
        worker.glossary_remove_requested.connect(worker.user_glossary_remove)
        worker.synopsis_edit_requested.connect(worker.user_synopsis_edit)
        worker.summary_edit_requested.connect(worker.user_summary_edit)
        worker.summary_remove_requested.connect(worker.user_summary_remove)
        worker.glossary_apply_requested.connect(worker.apply_glossary)
        worker.story_apply_requested.connect(worker.apply_story)

    def _shutdown(self):
        if self._worker is not None:
            self._worker.shutdown()
            self._worker = None
            self._thread = None

    # ── 用户动作 ──────────────────────────────────────────────

    def _on_stop(self):
        # 取消标志要尽早生效,直调(只写一个布尔)而非排队
        self._ensure_worker().request_stop()

    def _on_prefill(self):
        self._ensure_worker().prefill_requested.emit()

    def _on_rollback(self):
        if self._last_version_seq is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Undo last batch"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            self.tr(
                "Roll the project back to the state before the last batch action?"
            )
        )
        box.setInformativeText(
            self.tr(
                "Any manual edits made after that batch action are discarded too. The version is consumed, so each batch can only be undone once."
            )
        )
        box.addButton(QMessageBox.StandardButton.Ok).setText(self.tr("Roll back"))
        box.addButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        self.rollback_requested.emit(int(self._last_version_seq))

    def _on_batch_applied(self, version):
        """一次批量写回后：记住版本号，并把其余任务的列表标脏（数据变了）。"""
        seq = getattr(version, "seq", None)
        self._last_version_seq = seq
        self._dirty_tasks = set(CLEANUP_TASK_IDS) | {OCR_REVIEW, TRANS_REVIEW}
        self._dirty_tasks.discard(self.current_task())
        # 数据变了：其余已规划的任务页亮过时灯（当前页紧随 replan 熄灯）
        self.mark_content_changed()
        self._refresh_nav_counts()
        self._update_rollback_scope()

    def clear_batch_version(self):
        """批量已被撤销（主窗口回滚后调用）：版本已消耗，按钮随之失效。

        数据是整体换入的，故所有队列列表都失效；当前任务立刻重跑一次，
        其余任务等切过去时再补（``_dirty_tasks``）。
        """
        self._last_version_seq = None
        self._dirty_tasks = set(CLEANUP_TASK_IDS) | {OCR_REVIEW, TRANS_REVIEW}
        # 数据整体换入：先给所有已规划页亮过时灯，当前页紧随 replan 熄灯
        self.mark_content_changed()
        current = self.current_task()
        self._dirty_tasks.discard(current)
        view = self._batch_views.get(current) or self._review_views.get(current)
        if view is not None:
            view.replan()
        self._refresh_nav_counts()
        self._update_rollback_scope()

    # ── 翻译准备(耗时/耗费操作,先弹确认) ─────────────────────

    def _prepare_command(self) -> str:
        return self.tr(
            "Prepare the drafts for translation: 1) read the pages and propose glossary entries for recurring character, place and item names that are still missing from the draft; 2) write 2-4 sentence summaries for every page that doesn't have one yet; 3) refresh the global synopsis. Keep my existing entries untouched."
        )

    def _prepare_confirmed(self) -> bool:
        """耗时/耗费操作的显式确认:说明将做的事与 API 花销,用户批准后
        才执行。勾选「不再提示」写入 pcfg.workbench_confirm_costly=False
        (立即落盘),可在设置面板「应用 → Workbench」重新开启——写入的同
        时回写那边的复选框,否则设置页会停在启动时的旧值。"""
        from utils.config import pcfg, save_config

        if not pcfg.workbench_confirm_costly:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Prepare for translation"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(self.tr("This will do the following in order:"))
        box.setInformativeText(
            "<ol><li>"
            + self.tr(
                "Scan all pages' existing translations and pull recurring terms into the glossary draft (no AI, instant)."
            )
            + "</li><li>"
            + self.tr(
                "Ask the AI to read the pages and propose glossary entries that are still missing (API call, may incur cost)."
            )
            + "</li><li>"
            + self.tr(
                "Ask the AI to write summaries for pages without one and refresh the global synopsis (API call, may incur cost)."
            )
            + "</li></ol><p>"
            + self.tr(
                "Nothing is saved until you click \"Apply draft…\". You can stop the AI at any time with the Stop button."
            )
            + "</p>"
        )
        box.addButton(QMessageBox.StandardButton.Ok).setText(self.tr("Start"))
        box.addButton(QMessageBox.StandardButton.Cancel)
        dont_ask = QCheckBox(self.tr("Don't ask again"))
        box.setCheckBox(dont_ask)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return False
        if dont_ask.isChecked():
            pcfg.workbench_confirm_costly = False
            save_config()
            self._sync_confirm_costly_checkbox()
        return True

    def _sync_confirm_costly_checkbox(self) -> None:
        """Mirror "don't ask again" into Settings → App → Workbench.

        ``ConfigPanel.setupConfig`` only runs once at startup, so without this
        the settings checkbox would keep showing the value from launch time.
        """
        from utils.config import pcfg

        panel = getattr(self.window(), "configPanel", None)
        checker = getattr(panel, "confirm_costly_checker", None)
        if checker is None:
            return
        checker.blockSignals(True)
        checker.setChecked(pcfg.workbench_confirm_costly)
        checker.blockSignals(False)

    def _on_prepare(self):
        if not self.has_project():
            return
        if self._worker is not None and self._worker._busy:
            return
        if not self._prepare_confirmed():
            return
        # 先词频提取(无 AI,秒回)再发 AI 指令:两信号同线程按序排队,
        # worker 依次执行。指令直接入队——D19 砍掉 Chat 后不再有用户气泡,
        # 但 instruction_requested 本身与 Chat 无关,原地保留。
        self._ensure_worker().prefill_requested.emit()
        self._ensure_worker().instruction_requested.emit(self._prepare_command())

    # ── 术语/剧情：用户编辑与落盘 ──────────────────────────────

    def _on_apply(self):
        worker = self._ensure_worker()
        from utils.config import pcfg

        path = pcfg.module.llm_glossary_path or ""
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self,
                self.tr("Save glossary"),
                "glossary.json",
                "JSON (*.json)",
            )
            if not path:
                return
            pcfg.module.llm_glossary_path = path
        worker.glossary_apply_requested.emit(path)
        worker.story_apply_requested.emit()

    def _on_glossary_remove(self):
        rows = sorted(
            {i.row() for i in self.glossary_table.selectedIndexes()},
            reverse=True,
        )
        worker = self._ensure_worker()
        for row in rows:
            item = self.glossary_table.item(row, 0)
            if item is not None:
                worker.glossary_remove_requested.emit(item.text())

    def _on_story_remove(self):
        rows = sorted(
            {i.row() for i in self.story_table.selectedIndexes()}, reverse=True
        )
        worker = self._ensure_worker()
        for row in rows:
            item = self.story_table.item(row, 0)
            if item is not None:
                worker.summary_remove_requested.emit(item.text())

    def _on_synopsis_edited(self):
        if self._syncing:
            return
        self._ensure_worker().synopsis_edit_requested.emit(
            self.synopsis_edit.toPlainText()
        )

    def _on_glossary_item_changed(self, item):
        if self._syncing or item.column() == 3:
            return
        row = item.row()
        src_item = self.glossary_table.item(row, 0)
        dst_item = self.glossary_table.item(row, 1)
        note_item = self.glossary_table.item(row, 2)
        if src_item is None or dst_item is None:
            return
        self._ensure_worker().glossary_edit_requested.emit(
            src_item.text(),
            dst_item.text(),
            note_item.text() if note_item is not None else "",
        )

    def _on_story_item_changed(self, item):
        if self._syncing or item.column() != 1:
            return
        page_item = self.story_table.item(item.row(), 0)
        if page_item is None:
            return
        self._ensure_worker().summary_edit_requested.emit(
            page_item.text(), item.text()
        )

    def _on_busy_changed(self, busy: bool):
        self.prefill_btn.setEnabled(not busy)
        self.prepare_btn.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.glossary_table.setEnabled(not busy)
        self.story_table.setEnabled(not busy)

    def _on_round_finished(self, reply: str):
        if (reply or "").strip():
            self._append_log(
                self.tr("Reply: %1").replace("%1", reply.strip()), TRANSLATION_PREP
            )

    def _on_round_failed(self, err: str):
        self._append_log(err, TRANSLATION_PREP)

    # ── worker → UI 镜像同步 ─────────────────────────────────

    @staticmethod
    def _origin_brush(origin: str) -> "QBrush | None":
        key = _ORIGIN_THEME_KEYS.get(origin)
        return QBrush(get_theme_color(key=key)) if key else None

    def _sync_glossary_table(self, rows: list):
        self._syncing = True
        try:
            self.glossary_table.setRowCount(len(rows))
            for r, (src, dst, note, origin) in enumerate(rows):
                values = (src, dst, note, _ORIGIN_LABELS.get(origin, origin))
                for c, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if c == 3:
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsEditable
                        )
                        brush = self._origin_brush(origin)
                        if brush is not None:
                            item.setForeground(brush)
                    self.glossary_table.setItem(r, c, item)
        finally:
            self._syncing = False

    def _sync_story_tab(self, synopsis: str, rows: list):
        self._syncing = True
        try:
            if self.synopsis_edit.toPlainText() != synopsis:
                self.synopsis_edit.setPlainText(synopsis)
            self.story_table.setRowCount(len(rows))
            for r, (page, summary, origin) in enumerate(rows):
                for c, value in enumerate(
                    (page, summary, _ORIGIN_LABELS.get(origin, origin))
                ):
                    item = QTableWidgetItem(value)
                    if c != 1:
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsEditable
                        )
                    if c == 2:
                        brush = self._origin_brush(origin)
                        if brush is not None:
                            item.setForeground(brush)
                    self.story_table.setItem(r, c, item)
        finally:
            self._syncing = False
