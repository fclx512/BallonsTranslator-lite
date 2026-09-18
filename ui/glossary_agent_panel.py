"""泛用工作台（设计见 docs/技术实现/AI辅助功能_设计与实现.md 的「工作台」部分，本文件＝界面层）。

**定位**：工作台是**任务容器**，不是聊天窗（D19 砍掉 Chat；D2 起就不做通用
对话）。六个任务共用一套三段结构（D20）——

1. **任务导航**（一级）：顶部六个任务钮，默认顺序＝用户工作流顺序（D16／§3）
   误识别清理 → 合并相邻框 → 框扩张 → 背景修复 → 术语提取 → 剧情摘要。
   前四项属「问题清理」任务族、参与跳步提示的"还有 N 个未处理"计数（D37），
   术语／剧情是探索性任务、不计数。**运行时不禁跳步**，只提示。
2. **候选列表**：任务自己的清单（批量任务见
   ``ui/workbench_batch_view.py``，逐条可勾选、点选即在下方展开 100% 原比例
   审批图——D23；术语／剧情见本文件的两个草稿页）。
3. **执行／写回**：无候选时禁用；批量执行前一律弹窗告知后果（D27）。

**批量任务的接线纪律**（设计 §8／§18）：界面只做"调 ``plan``→收勾选
→把标识交回 ``apply``"，不自己写几何、不自己改 ``proj.pages``、不绕开
``ui/batch_ops.py``；引擎与适配层在 ``ui/workbench_tasks.py``（Qt-free）与
四个 ``ui/batch_*.py``。批量事务外壳（D35 版本 + D40 五步）在这里建好并
注入，故版本快照会先落盘当前面板编辑。

**D25 单入口**：主窗口左栏只有一个「工作台」入口（``ui/mainwindowbars.py::LeftBar``
的 workbenchChecker 槽位），任务切换全在本面板内完成。

术语／剧情两页仍是原 ``GlossaryAgentWorker`` 的镜像：AI 产物只落 worker 草稿，
落盘只经「应用草稿…」。**Chat 砍掉后 worker 日志落在本面板底部的日志条**。
"""

import json
import logging

from qtpy.QtCore import QCoreApplication, QObject, QThread, Qt, Signal, Slot
from qtpy.QtGui import QBrush
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
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
from ui.custom_widget import ConfigTextEdit
from ui.misc import get_theme_color
from ui.workbench_batch_view import BatchTaskView
from ui.workbench_tasks import (
    CLEANUP_TASK_IDS,
    EXPAND,
    GLOSSARY,
    MERGE,
    MISREAD,
    SIMPLE_INPAINT,
    STORY,
    build_batch_tasks,
)

logger = logging.getLogger("glossary_agent_panel")

# 任务导航顺序（设计 §8／D16）：①–④「问题清理」任务族 → ⑤术语 ⑥剧情。
# 只有前四项参与跳步提示计数（D37）。
WORKBENCH_ORDER = (
    MISREAD,
    MERGE,
    EXPAND,
    SIMPLE_INPAINT,
    GLOSSARY,
    STORY,
)

# 导航钮的短标签（字面量定义处显式标注翻译上下文；模块级翻译表规则）。
# "Glossary"/"Story" 沿用旧 tab 的源串，既有译文直接接上。
_TASK_LABELS = {
    MISREAD: QCoreApplication.translate("GlossaryAgentPanel", "Misread cleanup"),
    MERGE: QCoreApplication.translate("GlossaryAgentPanel", "Merge blocks"),
    EXPAND: QCoreApplication.translate("GlossaryAgentPanel", "Expand blocks"),
    SIMPLE_INPAINT: QCoreApplication.translate(
        "GlossaryAgentPanel", "Background fill"
    ),
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


class WorkbenchTaskNav(QWidget):
    """一级导航：六个互斥任务钮（D20 第 ① 段；D25 单入口下的任务切换）。

    互斥按仓库既有做法手写 ``setChecked(False)``（不引
    ``QButtonGroup`` 的跨版本信号差异），且**不允许全不选**——工作台总有
    一个当前任务。
    """

    task_selected = Signal(str)

    def __init__(self, task_ids, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkbenchTaskNav")
        self._buttons = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 0)
        grid.setSpacing(4)
        for index, task_id in enumerate(task_ids):
            button = QPushButton(_TASK_LABELS.get(task_id, task_id), self)
            button.setObjectName("WorkbenchTaskButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, tid=task_id: self._on_clicked(tid)
            )
            grid.addWidget(button, index // 3, index % 3)
            self._buttons[task_id] = button

    def _on_clicked(self, task_id: str):
        button = self._buttons[task_id]
        if button.isChecked():
            self._select(task_id)
        else:
            # 不允许取消当前任务（工作台必须有一个当前任务）
            button.setChecked(True)

    def _select(self, task_id: str):
        for other_id, other in self._buttons.items():
            if other_id != task_id and other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
        self.task_selected.emit(task_id)

    def select(self, task_id: str, *, emit: bool = True):
        """程序性切换（打开项目回默认任务）。``emit=False`` 不触发跳步提示。"""
        button = self._buttons.get(task_id)
        if button is None:
            return
        if emit:
            button.setChecked(True)
            self._on_clicked(task_id)
            return
        for other_id, other in self._buttons.items():
            other.blockSignals(True)
            other.setChecked(other_id == task_id)
            other.blockSignals(False)

    def current(self) -> str:
        for task_id, button in self._buttons.items():
            if button.isChecked():
                return task_id
        return ""

    def set_count(self, task_id: str, count) -> None:
        """在任务钮上缀「还有 N 个未处理」（D37：余光扫得到的状态）。"""
        label = _TASK_LABELS.get(task_id, task_id)
        if count:
            label = label + " (" + str(int(count)) + ")"
        button = self._buttons.get(task_id)
        if button is not None:
            button.setText(label)


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
        # 四个批量任务首次切入时各自规划一次；批量写回／回滚／换项目后重新标脏
        self._dirty_tasks = set(CLEANUP_TASK_IDS)
        self._last_version_seq = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 未打开项目时空态页接管全部交互(page 0);内容页在 page 1。
        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack, 1)

        empty_page = QWidget(self)
        empty_page.setObjectName("WorkbenchSurface")
        empty_lay = QVBoxLayout(empty_page)
        empty_lay.addStretch(1)
        empty_hint = QLabel(
            self.tr(
                "Open a project first — the workbench reads page texts from it."
            ),
            empty_page,
        )
        empty_hint.setObjectName("WorkbenchEmptyHint")
        empty_hint.setWordWrap(True)
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_hint)
        empty_lay.addStretch(1)
        self._stack.addWidget(empty_page)

        content_page = QWidget(self)
        content_page.setObjectName("WorkbenchSurface")
        content_lay = QVBoxLayout(content_page)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(4)

        # ① 一级导航
        self.nav = WorkbenchTaskNav(WORKBENCH_ORDER, content_page)
        self.nav.task_selected.connect(self._on_task_selected)
        content_lay.addWidget(self.nav)

        # ②③ 候选列表 + 执行（每个任务自己一整页）
        self.pages = QStackedWidget(content_page)
        content_lay.addWidget(self.pages, 1)
        self._build_batch_pages()
        self._build_glossary_page()
        self._build_story_page()

        # 底部：批量撤回 + worker 日志落点（砍 Chat 后的新落点）
        content_lay.addWidget(self._build_bottom_row(content_page))

        self._stack.addWidget(content_page)
        self._stack.setCurrentIndex(1 if self.has_project() else 0)

        # 默认停在第一个任务（§3 的用户工作流顺序）；emit=False 跳过跳步提示
        self._current_task = ""
        self.nav.select(WORKBENCH_ORDER[0], emit=False)
        self._current_task = WORKBENCH_ORDER[0]

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
            view = BatchTaskView(task)
            view.jump_requested.connect(self.jump_requested)
            view.notify_requested.connect(self._toast)
            view.status_requested.connect(self._append_log)
            view.batch_applied.connect(self._on_batch_applied)
            self._batch_views[task_id] = view
            self.pages.addWidget(view)

    def _build_glossary_page(self):
        page = QWidget(self)
        page.setObjectName("WorkbenchSurface")
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
        self.prepare_btn = QPushButton(self.tr("Prepare for translation…"), page)
        self.prepare_btn.setToolTip(
            self.tr(
                "One-click warmup: scan existing translations, then ask the AI to fill in missing glossary entries and page summaries. A confirmation lists the steps (and API cost) first."
            )
        )
        self.stop_btn = QPushButton(self.tr("Stop"), page)
        self.apply_btn = QPushButton(self.tr("Apply draft…"), page)
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.prepare_btn)
        apply_row.addWidget(self.stop_btn)
        apply_row.addStretch(1)
        apply_row.addWidget(self.apply_btn)
        layout.addLayout(apply_row)
        self.pages.addWidget(page)
        self._glossary_page = page
        self._glossary_page_index = self.pages.count() - 1

    def _build_story_page(self):
        page = QWidget(self)
        page.setObjectName("WorkbenchSurface")
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
        self.pages.addWidget(page)
        self._story_page = page
        self._story_page_index = self.pages.count() - 1

    def _build_bottom_row(self, parent) -> QWidget:
        holder = QWidget(parent)
        holder.setObjectName("WorkbenchSurface")
        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 0, 6, 6)
        self.rollback_btn = QPushButton(self.tr("Undo last batch"), holder)
        self.rollback_btn.setToolTip(
            self.tr(
                "Rolls the project back to the state before the last batch action (merge / expand / delete / background fill). The version is consumed, so it can be undone once."
            )
        )
        self.rollback_btn.setEnabled(False)
        row.addWidget(self.rollback_btn)
        row.addStretch(1)
        self._log_view = ConfigTextEdit(parent)
        self._log_view.setObjectName("WorkbenchLogView")
        self._log_view.setReadOnly(True)
        self._log_view.setFixedHeight(56)
        self._log_view.document().setMaximumBlockCount(200)
        row.addWidget(self._log_view, 1)
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

    def _toast(self, text: str, kind: str = "info"):
        """面板内通知：批量回执与失败提示（kind 见通知中心的 KIND_STYLES）。"""
        from ui.custom_widget import notification

        try:
            notification.toast(text, kind=kind, anchor="bottom-left")
        except Exception as error:
            logger.error(f"workbench toast failed: {error}")
        self._append_log(text)

    def _append_log(self, text: str):
        """worker 日志与批量回执的落点（D19：砍 Chat 后日志不再无处可去）。"""
        if not text:
            return
        # document 已设 maximumBlockCount，超长自动丢弃最旧的行
        self._log_view.append(text)
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── 任务导航 ────────────────────────────────────────────────

    def _page_index(self, task_id: str) -> int:
        if task_id == GLOSSARY:
            return self._glossary_page_index
        if task_id == STORY:
            return self._story_page_index
        return list(CLEANUP_TASK_IDS).index(task_id)

    def current_task(self) -> str:
        return self.nav.current()

    def _on_task_selected(self, task_id: str):
        previous = getattr(self, "_current_task", "")
        if task_id == previous:
            return
        if self._warn_skip_order(previous, task_id):
            self.nav.select(previous, emit=False)
            return
        self._current_task = task_id
        self.pages.setCurrentIndex(self._page_index(task_id))
        if task_id in (GLOSSARY, STORY):
            self._ensure_worker()
        else:
            self._ensure_current_planned()
        self._refresh_nav_counts()

    def _ensure_current_planned(self):
        """当前批量任务若标脏（还没规划过／数据变过）就跑一次只读 ``plan``。"""
        current = self.current_task()
        view = self._batch_views.get(current)
        if view is None or current not in self._dirty_tasks:
            return
        self._dirty_tasks.discard(current)
        view.replan()

    def earlier_pending(self, target: str):
        """``target`` 之前、仍有未处理条目的「问题清理」步骤：``[(标签, 计数)]``。

        D37 的计数口径：只有前四项参与（术语／剧情是探索性任务、没有
        "未处理标记"的概念）。计数一律取自任务自己的 ``pending``——误识别＝
        未驳回块数（D28）、合并＝默认勾选的组数、扩张＝当前数值下的可扩框数、
        背景修复＝已扫过那次的简单块数（它要读全书页图，不为提示重跑）。
        """
        pending = []
        if target not in WORKBENCH_ORDER:
            return pending
        limit = WORKBENCH_ORDER.index(target)
        for task_id in CLEANUP_TASK_IDS:
            if WORKBENCH_ORDER.index(task_id) >= limit:
                break
            view = self._batch_views.get(task_id)
            task = self._batch_tasks.get(task_id)
            if view is None or task is None:
                continue
            try:
                count = task.pending(view.options())
            except Exception as error:
                logger.error(f"Skip-order probe failed for {task_id}: {error}")
                continue
            if count:
                pending.append((_TASK_LABELS.get(task_id, task_id), int(count)))
        return pending

    def _warn_skip_order(self, previous: str, target: str) -> bool:
        """跳步提示（D37）：前序「问题清理」步骤还有未处理条目时提示，可禁用。

        只在**往后跳**时提示（往前回看不打扰）；不门禁、可任意跳转，
        提示里的「不再提示」写 ``pcfg.workbench_warn_skip_order``。
        返回真＝用户取消，调用方应停原任务不动。
        """
        from utils.config import pcfg, save_config

        if not pcfg.workbench_warn_skip_order:
            return False
        if not previous or previous not in WORKBENCH_ORDER:
            return False
        if WORKBENCH_ORDER.index(target) <= WORKBENCH_ORDER.index(previous):
            return False
        pending = self.earlier_pending(target)
        if not pending:
            return False
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Unprocessed items in earlier steps"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(self.tr("Earlier cleanup steps still have unprocessed items:"))
        box.setInformativeText(
            "<br>".join(label + ": " + str(count) for label, count in pending)
        )
        box.addButton(QMessageBox.StandardButton.Ok).setText(self.tr("Continue"))
        box.addButton(QMessageBox.StandardButton.Cancel)
        dont_ask = QCheckBox(self.tr("Don't ask again"))
        box.setCheckBox(dont_ask)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return True
        if dont_ask.isChecked():
            pcfg.workbench_warn_skip_order = False
            save_config()
            self._sync_warn_skip_checkbox()
        return False

    def _sync_warn_skip_checkbox(self):
        """把「不再提示」回写设置页复选框（ConfigPanel.setupConfig 只跑一次）。"""
        from utils.config import pcfg

        panel = getattr(self.window(), "configPanel", None)
        checker = getattr(panel, "warn_skip_checker", None)
        if checker is None:
            return
        checker.blockSignals(True)
        checker.setChecked(pcfg.workbench_warn_skip_order)
        checker.blockSignals(False)

    def _refresh_nav_counts(self):
        """导航钮上的「还有 N 个未处理」（D37；只有前四项参与计数）。"""
        for task_id in CLEANUP_TASK_IDS:
            view = self._batch_views.get(task_id)
            task = self._batch_tasks.get(task_id)
            if view is None or task is None:
                continue
            try:
                count = task.pending(view.options())
            except Exception as error:
                logger.error(f"Nav count failed for {task_id}: {error}")
                continue
            self.nav.set_count(task_id, count)

    # ── 项目状态 ────────────────────────────────────────────────

    def has_project(self) -> bool:
        """未打开项目(directory 为空)时工作台不可用。"""
        return bool(getattr(self._proj, "directory", None))

    def refresh_project_state(self):
        """项目打开/切换后刷新:空态 ⇄ 内容页,worker 与批量任务重建。"""
        if self._worker is not None:
            self._shutdown()
        self._dirty_tasks = set(CLEANUP_TASK_IDS)
        self._last_version_seq = None
        self.rollback_btn.setEnabled(False)
        for task in self._batch_tasks.values():
            task.reset()
        if self.has_project():
            self._stack.setCurrentIndex(1)
            self.nav.select(WORKBENCH_ORDER[0], emit=False)
            self._current_task = WORKBENCH_ORDER[0]
            self._dirty_tasks.discard(WORKBENCH_ORDER[0])
            self.pages.setCurrentIndex(0)
            if self.isVisible():
                self._ensure_worker()
                self._ensure_current_planned()
            self._refresh_nav_counts()
        else:
            self._stack.setCurrentIndex(0)

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
        # worker → UI
        worker.log_line.connect(self._append_log)
        worker.busy_changed.connect(self._on_busy_changed)
        worker.glossary_synced.connect(self._sync_glossary_table)
        worker.story_synced.connect(self._sync_story_tab)
        worker.round_finished.connect(self._on_round_finished)
        worker.round_failed.connect(self._on_round_failed)
        worker.applied.connect(self._append_log)
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
        self.rollback_btn.setEnabled(seq is not None)
        self._dirty_tasks = set(CLEANUP_TASK_IDS)
        self._dirty_tasks.discard(self.current_task())
        self._refresh_nav_counts()

    def clear_batch_version(self):
        """批量已被撤销（主窗口回滚后调用）：版本已消耗，按钮随之失效。

        数据是整体换入的，故所有队列列表都失效；当前任务立刻重跑一次，
        其余任务等切过去时再补（``_dirty_tasks``）。
        """
        self._last_version_seq = None
        self.rollback_btn.setEnabled(False)
        self._dirty_tasks = set(CLEANUP_TASK_IDS)
        current = self.current_task()
        self._dirty_tasks.discard(current)
        view = self._batch_views.get(current)
        if view is not None:
            view.replan()
        self._refresh_nav_counts()

    # ── 一键准备(耗时/耗费操作,先弹确认) ─────────────────────

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
            self._append_log(self.tr("Reply: %1").replace("%1", reply.strip()))

    def _on_round_failed(self, err: str):
        self._append_log(err)

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
