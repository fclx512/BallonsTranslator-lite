"""菜单工具容器（设计见 docs/技术实现/AI辅助功能_设计与实现.md 的「菜单工具」部分）。

桌面端由工具菜单打开单个工具，menu_mode 隐藏旧导航；管线工作台独立在
ui/run_pipeline_dialog.py。这里保留任务、worker 草稿与批量事务；主窗口引用仍叫
glossary_workbench。默认导航形态只供既有演练台与兼容调用，不再占左栏入口。

**定位**：七项任务共用固定两行导航、任务页与可展开活动区。导航标签与数量分行，
切换和计数更新不移动按钮。候选点行预览与勾选操作独立；任务说明可展开，
来源／跳过原因／过时提示直接显示。翻译准备含术语与剧情两份草稿，仍由同一
应用入口分别提交；调板仍走真实画布取色。最近状态按任务归属，完整近期活动
跨任务保留（最多 200 个文本块），长内容按纯文本呈现。

**批量任务的接线纪律**（设计 §8／§18）：界面只做"调 ``plan``→收勾选
→把标识交回 ``apply``"，不自己写几何、不自己改 ``proj.pages``、不绕开
``ui/batch_ops.py``；引擎与适配层在 ``ui/workbench_tasks.py``（Qt-free）与
四个 ``ui/batch_*.py``。批量事务外壳（D35 版本 + D40 五步）在这里建好并
注入，故版本快照会先落盘当前面板编辑。**待办队列（原文待校对／译文待重译）
不走这条路**：它们没有整批写回、没有版本，出口只有「跳画布」与「从列表
移除／忽略」。

**入口**：主窗口工具菜单经 ``open_tool`` 选择任务，工具窗口是非模态的；
跳画布时隐藏窗口，关闭时解除取色并收起审批预览，草稿仍保留。

术语／剧情两页仍是原 ``GlossaryAgentWorker`` 的镜像：AI 产物只落 worker 草稿，
落盘只经「应用草稿…」。**Chat 砍掉后 worker 日志落在本面板底部的日志条**
（日志行带所属任务名，术语／剧情的行归「翻译准备」）。
"""

import json
import logging
from typing import Dict, Optional

from qtpy.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QRectF,
    QSize,
    Qt,
    QThread,
    Signal,
    Slot,
)
from qtpy.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QTextCursor
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
from ui.adaptive_wrap_layout import AdaptiveWrapLayout
from ui.custom_widget import ConfigTextEdit
from ui.misc import get_theme_color
from ui.workbench_batch_view import BatchTaskView
from ui.workbench_review_view import ReviewQueueView
from ui.workbench_tasks import (
    CLEANUP_TASK_IDS,
    FG_PALETTE,
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
# 取色调板 → 翻译准备 → 译文待重译。
WORKBENCH_ORDER = (
    MISREAD,
    MERGE,
    OCR_REVIEW,
    SIMPLE_INPAINT,
    FG_PALETTE,
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
        (FG_PALETTE, TRANSLATION_PREP, TRANS_REVIEW),
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
    FG_PALETTE: QCoreApplication.translate("GlossaryAgentPanel", "Font color palette"),
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


# ── 固定两行任务导航 ─────────────────────────────────────────────
# 固定两行导航：第一行原文/图像四项，第二行翻译三项。
# 标签与数量分行，更新计数或切换任务不改变任何按钮的位置。
class WorkbenchTaskChip(QPushButton):
    """任务导航按钮：完整逻辑文本保留供无障碍与 tooltip 使用。"""

    def __init__(self, text: str, group_title: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("WorkbenchTaskButton")
        self.setCheckable(True)
        self.setMinimumWidth(0)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._group_title = group_title
        self.label = text
        self.count_text = ""
        self.sync_tooltip()

    def set_full_text(self, text: str):
        self.setText(text)
        self.setAccessibleName(text)
        self.sync_tooltip()
        self.update()

    def sync_tooltip(self):
        self.setToolTip(self._group_title + " · " + self.text())

    def event(self, event):
        if event.type() == QEvent.Type.StyleChange:
            _THEME_COLOR_CACHE.clear()
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        selected = self.isChecked()
        if selected or self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_theme_color(
                "@accentPrimary20Solid" if selected else "@hoverBackgroundColor"
            ))
            painter.drawRoundedRect(rect, 4, 4)
        color = QColor(_theme_color("@qwidgetForegroundColor"))
        painter.setPen(color)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(selected)
        painter.setFont(font)
        label = QFontMetrics(font).elidedText(
            self.label, Qt.TextElideMode.ElideRight, max(0, self.width() - 8)
        )
        painter.drawText(QRectF(4, 3 if self.count_text else 0, self.width()-8,
                               22 if self.count_text else 44),
                         Qt.AlignmentFlag.AlignCenter, label)
        if self.count_text:
            font.setBold(False)
            painter.setFont(font)
            color.setAlpha(200)
            painter.setPen(color)
            painter.drawText(QRectF(4, 24, self.width()-8, 17),
                             Qt.AlignmentFlag.AlignCenter, self.count_text)
        if self.hasFocus():
            painter.setPen(_theme_color("@accentPrimary"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 4, 4)


class WorkbenchTaskNav(QWidget):
    """固定两行、七个直接入口；任务选择与计数不改变布局。"""

    task_selected = Signal(str)

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        _styled(self).setObjectName("WorkbenchTaskNav")
        self._buttons = {}
        self._counts = {}
        self._count_units = {}
        self._groups = tuple(groups)
        rows = QVBoxLayout(self)
        rows.setContentsMargins(8, 6, 8, 6)
        rows.setSpacing(2)
        for title, task_ids in self._groups:
            row = QHBoxLayout()
            row.setSpacing(3)
            for task_id in task_ids:
                chip = WorkbenchTaskChip(_TASK_LABELS.get(task_id, task_id), title, self)
                chip.clicked.connect(lambda _checked=False, tid=task_id: self._on_clicked(tid))
                row.addWidget(chip, 1)
                self._buttons[task_id] = chip
            rows.addLayout(row)
        self.setFixedHeight(102)

    def _on_clicked(self, task_id):
        if self._buttons[task_id].isChecked():
            self.select(task_id)
        else:
            self._buttons[task_id].setChecked(True)

    def select(self, task_id, *, emit=True):
        if task_id not in self._buttons:
            return
        for other_id, button in self._buttons.items():
            button.setChecked(other_id == task_id)
        if emit:
            self.task_selected.emit(task_id)

    def current(self):
        return next((key for key, button in self._buttons.items() if button.isChecked()), "")

    def set_count(self, task_id, count, unit=""):
        self._counts[task_id] = int(count or 0)
        self._count_units[task_id] = unit
        chip = self._buttons.get(task_id)
        if chip is not None:
            chip.label = _TASK_LABELS.get(task_id, task_id)
            chip.count_text = (str(int(count)) + (" " + unit if unit else "")) if count else ""
            chip.set_full_text(_with_count(chip.label, count, unit))


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
            from modules.translators.trans_agent import AgentTranslator
            from utils.config import pcfg

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


class WorkbenchStatusLabel(QLabel):
    """单行最近状态；完整内容在活动记录和 tooltip 中保留。"""

    def set_status(self, text):
        self._status = text
        self.setToolTip(text)
        self._fit_text()

    def _fit_text(self):
        text = getattr(self, "_status", "").splitlines()
        self.setText(self.fontMetrics().elidedText(
            text[0] if text else "", Qt.TextElideMode.ElideRight, max(0, self.width())
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()


class WorkbenchPages(QStackedWidget):
    """只按当前页计算滚动高度，避免隐藏的长队列撑高其他任务页。"""

    def minimumSizeHint(self):
        page = self.currentWidget()
        return QSize(0, page.minimumSizeHint().height() if page else 0)

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page else QSize(460, 400)

    def heightForWidth(self, width):
        page = self.currentWidget()
        if page and page.layout() and page.hasHeightForWidth():
            return page.layout().minimumHeightForWidth(width)
        return -1


class GlossaryAgentPanel(QWidget):
    """菜单工具共用的视图与草稿容器；menu_mode 保留单任务界面。"""

    # 队列 → 画布跳转（D26：界面不做翻页控件，交给主窗口的页链路）
    jump_requested = Signal(str, int)
    # 待办队列 → 直接开框级确认卡（交接 §4.2③⑥：与「跳画布」并列的第二条出口）
    action_requested = Signal(str, int, str)
    revision_requested = Signal(object, str)
    # 撤回最近一次批量操作（D4／D35：整体一条撤回）
    rollback_requested = Signal(int)

    def __init__(self, proj, parent=None, *, menu_mode=False):
        super().__init__(parent)
        _styled(self).setObjectName("WorkbenchRoot")
        self._proj = proj
        self._owner = parent
        self._menu_mode = menu_mode
        self._worker_log_task = TRANSLATION_PREP
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
        # 取色模式是否已接线（画布取色 ↔ 调板页）；画布自行退出时在这里解除
        self._palette_wired = False
        # 首次切入某任务时才规划它（列表懒规划）；批量写回／回滚／换项目后
        # 全部重新标脏。待办队列同样参与（读取便宜，但口径保持一致）。
        self._dirty_tasks = set(CLEANUP_TASK_IDS) | {OCR_REVIEW, TRANS_REVIEW}
        self._last_version_seq = None
        self._latest_activity = {}

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

        # ① 固定两行七项入口；标签与计数分行，任务切换不重排。
        self.nav = WorkbenchTaskNav(WORKBENCH_NAV_GROUPS, content_page)
        self.nav.task_selected.connect(self._on_task_selected)
        content_lay.addWidget(self.nav)
        self.nav.setVisible(not menu_mode)

        # ②③ 候选列表 + 执行（每个任务自己一整页）
        self.pages = WorkbenchPages(content_page)
        self.pages.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.pages.currentChanged.connect(lambda _: self.pages.updateGeometry())
        self._page_scroll = QScrollArea(content_page)
        self._page_scroll.setObjectName("WorkbenchPageScroll")
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_scroll.setWidget(self.pages)
        self.pages.currentChanged.connect(
            lambda _: self._page_scroll.verticalScrollBar().setValue(0)
        )
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_lay.addWidget(self._page_scroll, 1)
        self._build_batch_pages()
        self._build_review_pages()
        self._build_palette_page()
        self._build_translation_prep_page()
        if menu_mode:
            palette_layout = self._palette_view.layout()
            palette_layout.setStretch(palette_layout.indexOf(self._palette_view._empty_label), 1)
            self._palette_view.findChild(QLabel, "WorkbenchPageTitle").setText(self.tr("Colors and speakers"))

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
            view.plan_changed.connect(self._refresh_latest_status)
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
            view.revision_requested.connect(self.revision_requested)
            view.preview_requested.connect(self._on_preview_requested)
            view.preview_dismissed.connect(self._on_preview_dismissed)
            view.plan_changed.connect(self._refresh_nav_counts)
            view.plan_changed.connect(self._refresh_latest_status)
            self._review_views[task_id] = view
            self.pages.addWidget(view)
            self._page_indices[task_id] = self.pages.count() - 1

    def _build_palette_page(self):
        """取色调板页（``ui/workbench_palette.py::FgPalettePage``）。

        工具页而非任务页：无 plan/apply、无计数（同翻译准备不计数）。
        数据属主是项目（``fg_palette``），本页自己读写；画布当前页的
        视觉层刷新经注入回调走主窗口。
        """
        from ui.workbench_palette import FgPalettePage

        self._palette_view = FgPalettePage(self._proj)
        self._palette_view.notify_requested.connect(
            lambda text, kind: self._toast(text, kind, FG_PALETTE)
        )
        # 取样开关 → 画布取色模式（真正的取样发生在画布上）；开关被拒绝时
        # 由 set_palette_sampling 回灌 set_sampling_state(False) + toast 原因。
        self._palette_view.sampling_toggled.connect(self.set_palette_sampling)
        self.pages.addWidget(self._palette_view)
        self._page_indices[FG_PALETTE] = self.pages.count() - 1

    def _build_glossary_page(self):
        """术语草稿子页（翻译准备页的第一个子页）。"""
        page = QWidget(self)
        _styled(page).setObjectName("WorkbenchSurface")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
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
        self.glossary_table.setShowGrid(False)
        self.glossary_table.verticalHeader().setDefaultSectionSize(30)
        self.glossary_table.setColumnWidth(0, 80)
        self.glossary_table.setColumnWidth(1, 80)
        self.glossary_table.setColumnWidth(3, 56)
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
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(QLabel(self.tr("Global synopsis"), page))
        self.synopsis_edit = ConfigTextEdit(page)
        self.synopsis_edit.setMinimumHeight(96)
        self.synopsis_edit.setMaximumHeight(144)
        layout.addWidget(self.synopsis_edit)
        layout.addWidget(QLabel(self.tr("Page summaries"), page))
        self.story_table = QTableWidget(0, 3, page)
        self.story_table.setShowGrid(False)
        self.story_table.verticalHeader().setDefaultSectionSize(30)
        self.story_table.setColumnWidth(0, 80)
        self.story_table.setColumnWidth(2, 56)
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
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(_TASK_LABELS[TRANSLATION_PREP], page)
        if self._menu_mode:
            title.setText(self.tr("Translation materials"))
        title.setObjectName("WorkbenchPageTitle")
        layout.addWidget(title)
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
        if self._menu_mode:
            self.prepare_btn.setText(self.tr("Generate drafts…"))
        self.prepare_btn.setToolTip(
            self.tr(
                "One-click warmup: scan existing translations, then ask the AI to fill in missing glossary entries and page summaries. A confirmation lists the steps (and API cost) first."
            )
        )
        # 说话人赋名（方案评估 §3.4 第一期）：固定按钮拼指令，AI 只产建议，
        # 人工到调板行订正——AI 不直接写调板
        self.assign_speakers_btn = QPushButton(
            self.tr("Assign speaker names…"), page
        )
        if self._menu_mode:
            self.assign_speakers_btn.setText(self.tr("Suggest speaker names…"))
        self.assign_speakers_btn.setToolTip(
            self.tr(
                "Ask the AI to propose real character names for the palette's placeholder speakers (Speaker A, …), using their lines and the glossary. Suggestions go to the log; edit the palette rows yourself."
            )
        )
        self.stop_btn = QPushButton(self.tr("Stop"), page)
        self.apply_btn = QPushButton(self.tr("Apply draft…"), page)
        self.apply_btn.setToolTip(
            self.tr(
                "Applies both drafts: the glossary goes to its json file, the story context into the project. This is not part of the batch rollback."
            )
        )
        generate_row = AdaptiveWrapLayout(horizontal_spacing=6, vertical_spacing=6)
        generate_row.addWidget(self.prepare_btn)
        if self._menu_mode:
            self._palette_view.layout().addWidget(self.assign_speakers_btn)
        else:
            generate_row.addWidget(self.assign_speakers_btn)
            generate_row.addWidget(self.stop_btn)
        self.stop_btn.hide()
        layout.addLayout(generate_row)
        apply_hint = _page_hint(self.tr("Apply submits both glossary and story drafts. Batch undo does not cover this action."), page)
        layout.addWidget(apply_hint)
        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn.setObjectName("WorkbenchExecuteButton")
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
        """当前任务最近状态 + 可展开近期活动；批量撤回仍保持原作用范围。"""
        holder = _styled(QWidget(parent))
        holder.setObjectName("WorkbenchStatusBar")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self._activity_toggle = QPushButton(self.tr("Activity"), holder)
        self._activity_toggle.setObjectName("WorkbenchActivityToggle")
        self._activity_toggle.setCheckable(True)
        self._activity_toggle.setToolTip(self.tr("Show or hide recent activity (up to 200 lines)."))
        row.addWidget(self._activity_toggle)
        if self._menu_mode:
            row.addWidget(self.stop_btn)
        layout.addLayout(row)
        self._latest_status = WorkbenchStatusLabel(holder)
        self._latest_status.set_status(self.tr("No recent activity for this task."))
        self._latest_status.setObjectName("WorkbenchLatestStatus")
        self._latest_status.setTextFormat(Qt.TextFormat.PlainText)
        self._latest_status.setWordWrap(False)
        self._latest_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._latest_status, 1)
        self._log_view = ConfigTextEdit(holder)
        self._log_view.setObjectName("WorkbenchLogView")
        self._log_view.setReadOnly(True)
        self._log_view.setFrameShape(QFrame.Shape.NoFrame)
        self._log_view.setFixedHeight(156)
        self._log_view.document().setMaximumBlockCount(200)
        self._log_view.hide()
        self._activity_toggle.toggled.connect(self._log_view.setVisible)
        layout.addWidget(self._log_view)
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
        window = self._owner or self.window()
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


    # ── 取色调板：画布取色模式接线（W1）─────────────────────────

    def _canvas(self):
        """主窗口画布（取样开关要接管它的左键）；未就绪返回 None。"""
        window = self._mainwindow()
        return getattr(window, "canvas", None)

    def set_palette_sampling(self, on: bool) -> bool:
        """调板页取样开关 → 画布取色模式；返回是否真的武装。

        开机（``on=True``）：画布拒绝时（没开工程／被整本对齐、路径重排、
        区域再检测占用）不改任何状态，回灌 False 并 toast 原因——开关不能
        停在一个假的「正在取样」上。关机：解除画布模式并收掉接线与常驻提示。
        """
        if not on:
            canvas = self._canvas()
            if canvas is not None:
                try:
                    canvas.leave_color_sample_mode()
                except Exception as error:
                    logger.error(f"Failed to leave color sample mode: {error}")
            self._release_palette_sampling()
            return True
        canvas = self._canvas()
        refused = False
        if canvas is None:
            refused = True
        elif not canvas.is_color_sample_mode():
            try:
                refused = not canvas.enter_color_sample_mode()
            except Exception as error:
                logger.error(f"Failed to enter color sample mode: {error}")
                refused = True
        if refused:
            self._palette_view.set_sampling_state(False)
            self._toast(self._palette_sampling_refused_text(canvas), "warning", FG_PALETTE)
            return False
        self._wire_palette_sampling(canvas)
        return True

    def _palette_sampling_refused_text(self, canvas) -> str:
        proj = getattr(canvas, "imgtrans_proj", None) if canvas is not None else None
        if not getattr(proj, "img_valid", False):
            return self.tr("Sampling needs the canvas: open a project first.")
        return self.tr(
            "Cannot start sampling: another canvas mode is active (whole-book align, path reorder or region redetect)."
        )

    def _wire_palette_sampling(self, canvas):
        """接上画布取色信号并亮常驻提示；重复调用不重复接线。

        常驻 activity 提示是刻意的（交接 §3.4／§6.8）：武装期间左键语义被
        换掉了，用户必须能一眼看到「现在在取色」，并且看到它是可关的。
        """
        from ui.custom_widget import notification

        if not self._palette_wired:
            self._palette_wired = True
            canvas.color_sampled.connect(self._palette_view.accept_canvas_sample)
            canvas.color_sample_mode_changed.connect(
                self._on_palette_sample_mode_changed
            )
        self._palette_view.set_sampling_state(True)
        notification.activity(
            "palette-sampling",
            True,
            self.tr("Sampling colors — click a text glyph on the canvas (Esc to stop)"),
            anchor="top-center",
        )

    def _release_palette_sampling(self):
        """收掉画布取色接线与常驻提示（可重复调用）。"""
        from ui.custom_widget import notification

        if self._palette_wired:
            self._palette_wired = False
            canvas = self._canvas()
            if canvas is not None:
                for signal, slot in (
                    (canvas.color_sampled, self._palette_view.accept_canvas_sample),
                    (
                        canvas.color_sample_mode_changed,
                        self._on_palette_sample_mode_changed,
                    ),
                ):
                    try:
                        signal.disconnect(slot)
                    except (TypeError, RuntimeError):
                        pass
        self._palette_view.set_sampling_state(False)
        notification.activity("palette-sampling", False)

    def _on_palette_sample_mode_changed(self, on: bool):
        """画布取色模式状态回流：同步开关；画布自行退出时收线（Esc/模式切换）。

        页面开关与面板武装态都跟着画布走——「开关显示 False 但面板还在
        拦截信号」这种半解除状态会让下一次开机静默失效。
        """
        self._palette_view.set_sampling_state(on)
        if not on:
            self._release_palette_sampling()

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

        展开后的记录**跨任务共用**，故按来源署名：批量任务与
        待办队列的行前缀自己的任务名（否则在翻译准备页读到「删除 2 个框」
        不知道是谁干的），术语／剧情的草稿与会话行归「翻译准备」名下，不再
        以无归属的裸行出现。页面级的 plan 摘要不写这里（它就在页面上）。
        """
        if not text:
            return
        task_id = task_id or TRANSLATION_PREP
        self._latest_activity[task_id] = text
        label = _TASK_LABELS.get(task_id, task_id)
        scrollbar = self._log_view.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 2
        cursor = QTextCursor(self._log_view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self._log_view.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(label + " · " + text)
        if follow:
            scrollbar.setValue(scrollbar.maximum())
        self._refresh_latest_status()

    def _refresh_latest_status(self):
        text = self._latest_activity.get(self.current_task(), "")
        text = text or self.tr("No recent activity for this task.")
        self._latest_status.set_status(text)

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
        if task_id == TRANSLATION_PREP and self.has_project():
            # 术语／剧情的草稿在 worker 线程里（懒建、载入基底），与批量任务无关
            self._ensure_worker()
        else:
            self._ensure_current_planned()
        self._refresh_nav_counts()
        self._refresh_latest_status()
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
        for task_id in (() if self._menu_mode else WORKBENCH_ORDER):
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
        self._latest_activity.clear()
        self._log_view.clear()
        self._refresh_latest_status()
        for task in self._batch_tasks.values():
            task.reset()
        for task in self._review_tasks.values():
            task.reset()
        for view in (
            list(self._batch_views.values()) + list(self._review_views.values())
        ):
            view.forget_plan()  # 旧项目的列表与过时灯都不该带进新项目
        # 换项目/关工程：取色模式必须强制解除（新工程不该继承旧画布的武装态）
        self.set_palette_sampling(False)
        self._palette_view.refresh_state()  # 调板随项目走：色表重挂、开关归位
        if self.has_project():
            self._stack.setCurrentIndex(1)
            self.nav.select(WORKBENCH_ORDER[0], emit=False)
            self._current_task = WORKBENCH_ORDER[0]
            self.pages.setCurrentIndex(self._page_index(WORKBENCH_ORDER[0]))
            if self.isVisible():
                if not self._menu_mode:
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
        self.assign_speakers_btn.clicked.connect(self._on_assign_speakers)
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
            if not self._menu_mode or self.current_task() == TRANSLATION_PREP:
                self._ensure_worker()
            self._ensure_current_planned()

    def hideEvent(self, event):
        if self._menu_mode:
            self._on_preview_dismissed()
            self.set_palette_sampling(False)
        super().hideEvent(event)

    def open_tool(self, task_id):
        """菜单选择一个工具；隐藏导航，保留任务与草稿的原有状态。"""
        if task_id not in self._page_indices:
            return
        self.nav.select(task_id)
        self._ensure_current_planned()

    def tool_count(self, task_id):
        """菜单只查询人工待办的便宜计数，不运行批量任务规划。"""
        task = self._review_tasks.get(task_id)
        return task.pending() if task is not None and self.has_project() else 0

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
            lambda text: self._append_log(text, self._worker_log_task)
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
        self._worker_log_task = TRANSLATION_PREP
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
        self._refresh_latest_status()
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
        self._refresh_latest_status()
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
        from utils.config import save_config

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

        panel = getattr(self._mainwindow(), "configPanel", None)
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
        self._worker_log_task = TRANSLATION_PREP
        # 先词频提取(无 AI,秒回)再发 AI 指令:两信号同线程按序排队,
        # worker 依次执行。指令直接入队——D19 砍掉 Chat 后不再有用户气泡,
        # 但 instruction_requested 本身与 Chat 无关,原地保留。
        self._ensure_worker().prefill_requested.emit()
        self._ensure_worker().instruction_requested.emit(self._prepare_command())

    # ── 说话人赋名（方案评估 §3.4 第一期）────────────────────────

    def _on_assign_speakers(self):
        if not self.has_project():
            return
        if self._worker is not None and self._worker._busy:
            return
        self._worker_log_task = FG_PALETTE if self._menu_mode else TRANSLATION_PREP
        command = self._assign_speakers_command()
        if command is None:
            return
        if not self._costly_confirmed(
            self.tr("Assign speaker names"),
            self.tr(
                "Ask the AI to read the sampled speakers' lines and the glossary, then propose real character names for the palette placeholders (API call, may incur cost). Suggestions only go to the log; nothing is written until you edit the palette rows yourself."
            ),
        ):
            return
        self._ensure_worker().instruction_requested.emit(command)
        if self._menu_mode:
            self._activity_toggle.setChecked(True)

    def _costly_confirmed(self, title: str, text: str) -> bool:
        """耗时/耗费确认的通用壳（与 _prepare_confirmed 同一开关与落盘）。"""
        from utils.config import save_config

        if not pcfg.workbench_confirm_costly:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
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

    def _assign_speakers_command(self) -> Optional[str]:
        """拼「为调板角色赋真名」指令（调板 + 术语表快照 + 台词样本内联）。

        返回 None 表示没有可赋名的占位（空调板/全部已有真名），此时只记
        日志不打扰。术语表快照取**已应用**的 json（与 has_glossary_base 同
        源）——草稿里的 AI 候选不算「就绪」。
        """
        from utils.proj_imgtrans import (
            is_placeholder_role,
            normalize_rgb,
            speaker_display_name,
        )

        placeholders = []
        for entry in getattr(self._proj, "fg_palette", None) or []:
            role = entry.get("role", "") if isinstance(entry, dict) else ""
            rgb = normalize_rgb(entry.get("rgb") if isinstance(entry, dict) else None)
            if rgb is None or not is_placeholder_role(role):
                continue
            placeholders.append((rgb, str(role)))
        if not placeholders:
            self._append_log(
                self.tr(
                    "No placeholder speakers to assign: sample colors in the palette page first."
                ),
                self._worker_log_task,
            )
            return None

        role_by_rgb = {tuple(rgb): role for rgb, role in placeholders}
        samples: Dict[str, list] = {role: [] for _, role in placeholders}
        _MAX_SAMPLES = 8
        for pagename, blk_list in (getattr(self._proj, "pages", None) or {}).items():
            for blk in blk_list:
                fg = normalize_rgb(getattr(blk, "fg_colors", None))
                role = role_by_rgb.get(tuple(fg)) if fg is not None else None
                if role is None:
                    continue
                text = " ".join(str(blk.get_text() or "").split())
                if not text:
                    continue
                if len(samples[role]) < _MAX_SAMPLES:
                    samples[role].append(f'"{text}" ("{pagename}")')

        parts = [
            self.tr(
                "Assign real character names to the palette's placeholder speakers: infer each speaker's name from their lines, the existing glossary and the story, then reply with a suggestion table (speaker → proposed name, with the supporting lines). This is a suggestion only: do not modify the glossary or story drafts — the user will edit the palette manually."
            ),
            self.tr("Palette (text color → speaker):"),
        ]
        for rgb, role in placeholders:
            parts.append(
                f"- #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}: "
                + speaker_display_name(role)
            )
        parts.append(self.tr("Sample lines per speaker:"))
        for rgb, role in placeholders:
            lines = samples[role]
            if lines:
                parts.append(f"- {speaker_display_name(role)}: " + ", ".join(lines))
            else:
                parts.append(
                    f"- {speaker_display_name(role)}: "
                    + self.tr("(no translated-source lines matched this color)")
                )
        glossary_text = self._glossary_snapshot_text()
        if glossary_text:
            parts.append(self.tr("Existing glossary:"))
            parts.append(glossary_text)
        return "\n".join(parts)

    def _glossary_snapshot_text(self) -> str:
        """已应用术语表的只读快照（内联进指令用，draft 候选不算）。"""

        try:
            path = pcfg.module.llm_glossary_path or ""
            entries = load_glossary(path) if path else ()
        except Exception:
            return ""
        lines = [
            f"- {e.source} → {e.translation}"
            for e in entries
            if str(e.source).strip()
        ]
        return "\n".join(lines[:200])

    # ── 术语/剧情：用户编辑与落盘 ──────────────────────────────

    def _on_apply(self):
        self._worker_log_task = TRANSLATION_PREP
        worker = self._ensure_worker()

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
        self.stop_btn.setVisible(busy)
        self.stop_btn.setEnabled(busy)
        self.prefill_btn.setEnabled(not busy)
        self.prepare_btn.setEnabled(not busy)
        self.assign_speakers_btn.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.glossary_table.setEnabled(not busy)
        self.story_table.setEnabled(not busy)

    def _on_round_finished(self, reply: str):
        if (reply or "").strip():
            self._append_log(
                self.tr("Reply: %1").replace("%1", reply.strip()), self._worker_log_task
            )

    def _on_round_failed(self, err: str):
        self._append_log(err, self._worker_log_task)

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
                    item.setToolTip(value)
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
                    item.setToolTip(value)
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
