"""术语/剧情整合 agent 工作台(方案见 memory/context-agent-workbench-plan)。

- ``GlossaryAgentWorker``:QThread 内的权威状态持有者。AI patch 只落
  worker 草稿(modules/context_agent/draft.py),经同步信号单向刷新
  UI 镜像;落盘只经「应用」按钮。LLM 会话复用 AgentTranslator 的
  profile/client/重试基建(translator 实例在 worker 线程内构造)。
- ``GlossaryAgentPanel``:主窗口左侧嵌入栏内容(与全局搜索同槽位、
  更宽,入口见 ui/mainwindowbars.py::LeftBar 的 glossaryChecker 与
  ui/mainwindow.py::on_set_glossary_widget)。三页:Chat(气泡对话 +
  常驻引导卡,流式刷新)、Glossary(草稿表)、Story(页段摘要 + 全局梗概)。

替代原 GlossaryExtractorDialog(one-shot LLM 提取,已删)。
"""

import json
import logging

from qtpy.QtCore import QCoreApplication, QObject, QThread, QTimer, Qt, Signal, Slot
from qtpy.QtGui import QBrush
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
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modules.context.glossary import GlossaryEntry, load_glossary
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

logger = logging.getLogger("glossary_agent_panel")

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

# _finish_stream 的 final_text 哨兵:区分「未提供(沿用已累积文本)」与
# 「提供了空回复(要清空/删气泡)」
_UNSET = object()


class GlossaryAgentWorker(QObject):
    """worker 线程内的权威草稿 + LLM 会话执行体。

    面板的所有操作入口都经 *:_requested 信号跨线程排队(QueuedConnection)
    触发——直接方法调用会在调用者(主)线程同步执行,长任务会冻结 UI;
    唯一例外 request_stop:只写取消标志,直调让检查点尽早生效。
    """

    log_line = Signal(str)
    chat_delta = Signal(str)  # 流式 content 增量(worker 线程 → UI 节流刷新)
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
            raise RuntimeError(
                "No available API key. Check the active profile's api_key."
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
            stream_cb=lambda d: self.chat_delta.emit(d),
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
    """RailDockPanel 内容:三页工作台。"""

    def __init__(self, proj, parent=None):
        super().__init__(parent)
        self._proj = proj
        self._syncing = False
        self._worker = None
        self._thread = None
        self._stream_label = None
        self._stream_text = ""
        self._user_bubbles = []
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(80)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_stream)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 未打开项目时空态页接管全部交互(page 0);内容页在 page 1。
        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack, 1)

        empty_page = QWidget(self)
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
        content_lay = QVBoxLayout(content_page)
        content_lay.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget(content_page)
        content_lay.addWidget(self.tabs, 1)

        # ── Chat 页(气泡流,参考旧 AI 助手面板) ─────────────────
        chat_tab = QWidget(self)
        chat_layout = QVBoxLayout(chat_tab)
        chat_layout.setContentsMargins(0, 4, 0, 0)
        self._chat_area = QScrollArea(chat_tab)
        self._chat_area.setWidgetResizable(True)
        self._chat_area.setFrameShape(QFrame.Shape.NoFrame)
        self._chat_inner = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_inner)
        self._chat_layout.setContentsMargins(2, 2, 2, 2)
        self._chat_layout.setSpacing(6)
        self._chat_layout.addStretch(1)
        self._chat_area.setWidget(self._chat_inner)
        chat_layout.addWidget(self._chat_area, 1)
        input_row = QHBoxLayout()
        self.input_edit = ConfigTextEdit(chat_tab)
        self.input_edit.setFixedHeight(52)
        self.input_edit.setPlaceholderText(
            self.tr("Instruction for the agent… (Ctrl+Enter to send)")
        )
        input_row.addWidget(self.input_edit, 1)
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        self.send_btn = QPushButton(self.tr("Send"), chat_tab)
        self.stop_btn = QPushButton(self.tr("Stop"), chat_tab)
        self.stop_btn.hide()
        btn_col.addWidget(self.send_btn)
        btn_col.addWidget(self.stop_btn)
        input_row.addLayout(btn_col)
        chat_layout.addLayout(input_row)
        self.tabs.addTab(chat_tab, self.tr("Chat"))

        # ── Glossary 页 ────────────────────────────────────────
        glossary_tab = QWidget(self)
        g_layout = QVBoxLayout(glossary_tab)
        g_layout.setContentsMargins(0, 4, 0, 0)
        self.glossary_table = QTableWidget(0, 4, glossary_tab)
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
        g_layout.addWidget(self.glossary_table, 1)
        g_btn_row = QHBoxLayout()
        self.prefill_btn = QPushButton(
            self.tr("Extract by frequency"), glossary_tab
        )
        self.prefill_btn.setToolTip(
            self.tr(
                "Scan all pages' existing translations and merge recurring source→translation pairs into the draft. No AI involved — for AI proposals, send an instruction in the Chat tab."
            )
        )
        self.glossary_remove_btn = QPushButton(
            self.tr("Remove selected"), glossary_tab
        )
        g_btn_row.addWidget(self.prefill_btn)
        g_btn_row.addStretch(1)
        g_btn_row.addWidget(self.glossary_remove_btn)
        g_layout.addLayout(g_btn_row)
        self.tabs.addTab(glossary_tab, self.tr("Glossary"))

        # ── Story 页 ───────────────────────────────────────────
        story_tab = QWidget(self)
        s_layout = QVBoxLayout(story_tab)
        s_layout.setContentsMargins(0, 4, 0, 0)
        s_layout.addWidget(QLabel(self.tr("Global synopsis"), story_tab))
        self.synopsis_edit = ConfigTextEdit(story_tab)
        self.synopsis_edit.setFixedHeight(72)
        s_layout.addWidget(self.synopsis_edit)
        s_layout.addWidget(
            QLabel(self.tr("Page summaries"), story_tab)
        )
        self.story_table = QTableWidget(0, 3, story_tab)
        self.story_table.setHorizontalHeaderLabels(
            [self.tr("Page"), self.tr("Summary"), self.tr("Origin")]
        )
        s_header = self.story_table.horizontalHeader()
        s_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.story_table.verticalHeader().setVisible(False)
        s_layout.addWidget(self.story_table, 1)
        self.story_remove_btn = QPushButton(
            self.tr("Remove selected"), story_tab
        )
        s_layout.addWidget(self.story_remove_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.tabs.addTab(story_tab, self.tr("Story"))

        # 引导卡依赖两张表的存在(动态指令按表格镜像统计),故在三页建完后插入
        self._build_guide_card()

        # ── Apply 行 ───────────────────────────────────────────
        apply_row = QHBoxLayout()
        self.prepare_btn = QPushButton(
            self.tr("Prepare for translation…"), content_page
        )
        self.prepare_btn.setToolTip(
            self.tr(
                "One-click warmup: scan existing translations, then ask the AI to fill in missing glossary entries and page summaries. A confirmation lists the steps (and API cost) first."
            )
        )
        apply_row.addWidget(self.prepare_btn)
        apply_row.addStretch(1)
        self.apply_btn = QPushButton(self.tr("Apply draft…"), content_page)
        apply_row.addWidget(self.apply_btn)
        content_lay.addLayout(apply_row)

        self._stack.addWidget(content_page)
        self._stack.setCurrentIndex(1 if self.has_project() else 0)

        self._update_tab_badges()
        self._connect_signals()
        QApplication.instance().aboutToQuit.connect(self._shutdown)

    def has_project(self) -> bool:
        """未打开项目(directory 为空)时工作台不可用。"""
        return bool(getattr(self._proj, "directory", None))

    def refresh_project_state(self):
        """项目打开/切换后刷新:空态 ⇄ 内容页,worker 重建以载入新基底。"""
        if self._worker is not None:
            self._shutdown()
        if self.has_project():
            self._stack.setCurrentIndex(1)
            if self.isVisible():
                self._ensure_worker()
        else:
            self._stack.setCurrentIndex(0)
        # 项目切换改变总页数,徽章与动态引导指令随之刷新
        self._update_tab_badges()
        self._refresh_guide_card()

    # ── 信号接线 ──────────────────────────────────────────────

    def _connect_signals(self):
        self.send_btn.clicked.connect(self._on_send)
        self.input_edit.installEventFilter(self)
        self.synopsis_edit.installEventFilter(self)
        self.stop_btn.clicked.connect(self._on_stop)
        self.prefill_btn.clicked.connect(self._on_prefill)
        self.prepare_btn.clicked.connect(self._on_prepare)
        self.glossary_remove_btn.clicked.connect(self._on_glossary_remove)
        self.story_remove_btn.clicked.connect(self._on_story_remove)
        self.glossary_table.itemChanged.connect(self._on_glossary_item_changed)
        self.story_table.itemChanged.connect(self._on_story_item_changed)
        self.apply_btn.clicked.connect(self._on_apply)

    def showEvent(self, event):
        """工作台首次露出时即建 worker 并载入基底(草稿不需要等首条指令);
        未打开项目时保持空态页,不建 worker。"""
        super().showEvent(event)
        if self.has_project():
            self._ensure_worker()

    def eventFilter(self, obj, event):
        """Ctrl+Enter 发送指令;synopsis 焦点离开时提交到 worker。"""
        from qtpy.QtCore import QEvent, Qt as Qt2

        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() == Qt2.Key.Key_Return
                and event.modifiers() & Qt2.KeyboardModifier.ControlModifier
            ):
                self._on_send()
                return True
        elif (
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
        worker.chat_delta.connect(self._on_chat_delta)
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

    # ── 一键准备(耗时/耗费操作,先弹确认) ─────────────────────

    def _prepare_command(self) -> str:
        return self.tr(
            "Prepare the drafts for translation: 1) read the pages and propose glossary entries for recurring character, place and item names that are still missing from the draft; 2) write 2-4 sentence summaries for every page that doesn't have one yet; 3) refresh the global synopsis. Keep my existing entries untouched."
        )

    def _prepare_confirmed(self) -> bool:
        """耗时/耗费操作的显式确认:说明将做的事与 API 花销,用户批准后
        才执行。勾选「不再询问」写入 pcfg.workbench_confirm_costly=False
        (立即落盘),可在翻译设置区重新开启。"""
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
        dont_ask = QCheckBox(
            self.tr("Don't ask again (re-enable in translation settings)")
        )
        box.setCheckBox(dont_ask)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return False
        if dont_ask.isChecked():
            pcfg.workbench_confirm_costly = False
            save_config()
        return True

    def _on_prepare(self):
        if not self.has_project():
            return
        if self._worker is not None and self._worker._busy:
            return
        if not self._prepare_confirmed():
            return
        # 先词频提取(无 AI,秒回)再发 AI 指令:两信号同线程按序排队,
        # worker 依次执行
        self._ensure_worker().prefill_requested.emit()
        self._send_text(self._prepare_command())

    # ── Chat 页:气泡流 ────────────────────────────────────────

    def _build_guide_card(self):
        """顶部常驻引导卡;示例指令链接点击即发送(_on_guide_link)。

        链接 href 只携带序号(cmd:0/1/2),指令文本经 ts 翻译后按序
        存入 _guide_commands,避开 QUrl 对非 ASCII 指令的编码问题。
        指令列表按项目状态动态生成(_refresh_guide_card):缺摘要页数、
        术语表是否为空等,让用户多数时候点一下即可,不必打字。
        """
        card = QWidget(self._chat_inner)
        card.setObjectName("AIChatGuideCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(8, 6, 8, 6)
        card_lay.setSpacing(4)
        title = QLabel(self.tr("How to use this workbench"), card)
        title.setObjectName("AIChatGuideTitle")
        title.setWordWrap(True)
        card_lay.addWidget(title)
        self._guide_body = QLabel(card)
        self._guide_body.setObjectName("AIChatGuideBody")
        self._guide_body.setWordWrap(True)
        self._guide_body.setTextFormat(Qt.TextFormat.RichText)
        self._guide_body.setOpenExternalLinks(False)
        self._guide_body.linkActivated.connect(self._on_guide_link)
        card_lay.addWidget(self._guide_body)
        self._guide_commands = []
        self._refresh_guide_card()
        self._insert_chat_widget(card)

    def _guide_state(self):
        """引导指令依赖的项目状态:(总页数, 缺摘要页数, 术语草稿是否为空)。

        统计来源是 UI 镜像(两张表),不直读 worker 状态。
        """
        pages = getattr(self._proj, "pages", None) or {}
        n_pages = len(pages) if self.has_project() else 0
        n_missing = max(0, n_pages - self.story_table.rowCount())
        glossary_empty = self.glossary_table.rowCount() == 0
        return n_pages, n_missing, glossary_empty

    def _refresh_guide_card(self):
        """按当前项目状态重建引导指令列表(草稿同步后调用)。"""
        n_pages, n_missing, glossary_empty = self._guide_state()
        commands = []
        if n_missing > 0:
            commands.append(
                self.tr(
                    "Write 2-4 sentence summaries for the %1 pages that don't have one yet, then refresh the global synopsis."
                ).replace("%1", str(n_missing))
            )
        if glossary_empty:
            commands.append(
                self.tr(
                    "Read the first 10 pages and propose glossary entries for recurring character, place and item names."
                )
            )
        else:
            commands.append(
                self.tr(
                    "Check the glossary draft against the page texts for wrong or missing translations of recurring terms, then propose fixes."
                )
            )
        commands.append(
            self.tr(
                "Read the recent pages and check whether the page summaries and global synopsis are still up to date; propose updates if the plot has moved on."
            )
        )
        self._guide_commands = commands
        items = "".join(
            f'<li><a href="cmd:{i}">{cmd}</a></li>'
            for i, cmd in enumerate(commands)
        )
        self._guide_body.setText(
            "<p>"
            + self.tr(
                "Send an instruction below — the agent reads your project's pages (read-only) and writes proposals into the Glossary and Story drafts."
            )
            + f"</p><ul>{items}</ul><p>"
            + self.tr(
                'Proposals appear as "AI" rows in the Glossary / Story tabs; your own rows are protected. "Apply draft…" saves them — the translation agent picks them up automatically.'
            )
            + "</p>"
        )

    def _on_guide_link(self, link: str):
        if link.startswith("cmd:"):
            try:
                idx = int(link.split(":", 1)[1])
            except ValueError:
                return
            if 0 <= idx < len(getattr(self, "_guide_commands", [])):
                self._send_text(self._guide_commands[idx])

    def _bubble_max_width(self) -> int:
        return max(160, int(self._chat_area.viewport().width() * 0.88))

    def _add_user_bubble(self, text: str):
        label = QLabel(text)
        label.setObjectName("AIChatUserBubble")
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setMaximumWidth(self._bubble_max_width())
        self._user_bubbles.append(label)
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(label)
        self._insert_chat_widget(wrap)

    def _add_ai_bubble(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("AIChatAssistantBubble")
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._insert_chat_widget(label)
        return label

    def _add_status_line(self, text: str, error: bool = False):
        label = QLabel(text)
        label.setObjectName("AIChatErrorLine" if error else "AIChatStatusLine")
        label.setWordWrap(True)
        self._insert_chat_widget(label)

    def _insert_chat_widget(self, widget: QWidget):
        """插到末尾 stretch 之前并滚动到底。"""
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, widget)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self):
        sb = self._chat_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _ensure_stream_label(self) -> QLabel:
        if self._stream_label is None:
            self._stream_text = ""
            self._stream_label = self._add_ai_bubble("…")
        return self._stream_label

    def _on_chat_delta(self, delta: str):
        self._ensure_stream_label()
        self._stream_text += delta
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _flush_stream(self):
        """节流合并的流式刷新:一次性回写已累积文本。"""
        if self._stream_label is not None:
            self._stream_label.setText(self._stream_text or "…")
            QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _finish_stream(self, final_text=_UNSET, *, keep=True):
        """收束当前流式气泡;final_text 覆盖为最终回复,keep=False 删除。"""
        if self._stream_label is None:
            return
        label = self._stream_label
        text = self._stream_text
        if final_text is not _UNSET:
            text = final_text
            label.setText(text)
        self._stream_label = None
        self._stream_flush_timer.stop()
        if not keep or not text.strip():
            label.parentWidget().deleteLater()

    def _on_send(self):
        if self._send_text(self.input_edit.toPlainText().strip()):
            self.input_edit.clear()

    def _send_text(self, text: str) -> bool:
        if not text or (self._worker is not None and self._worker._busy):
            return False
        worker = self._ensure_worker()
        self._add_user_bubble(text)
        worker.instruction_requested.emit(text)
        return True

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
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setVisible(busy)
        self.prefill_btn.setEnabled(not busy)
        self.prepare_btn.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.glossary_table.setEnabled(not busy)
        self.story_table.setEnabled(not busy)
        if busy:
            self.tabs.setCurrentIndex(0)

    # ── worker → UI 镜像同步 ─────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self._bubble_max_width()
        for label in self._user_bubbles:
            label.setMaximumWidth(width)

    def _on_round_finished(self, reply: str):
        self._finish_stream(reply)

    def _on_round_failed(self, err: str):
        self._finish_stream(_UNSET, keep=False)
        self._add_status_line(err, error=True)

    def _append_log(self, text: str):
        """worker 日志(轮次状态/载入摘要/落盘回执) → 灰色状态行;
        若有流式气泡先原样收束(空内容气泡随之移除)。"""
        self._finish_stream()
        self._add_status_line(text)

    def _update_tab_badges(self):
        """Tab 标题即状态总览:术语条数 / 摘要覆盖(已有/总页数)。

        不点进去也能看出准备度——「不打扰」的核心:信息放在余光扫得到处。
        """
        n_glossary = self.glossary_table.rowCount()
        self.tabs.setTabText(
            1,
            self.tr("Glossary (%1)").replace("%1", str(n_glossary)),
        )
        pages = getattr(self._proj, "pages", None) or {}
        n_pages = len(pages) if self.has_project() else 0
        self.tabs.setTabText(
            2,
            self.tr("Story (%1/%2)")
            .replace("%1", str(self.story_table.rowCount()))
            .replace("%2", str(n_pages)),
        )

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
        self._update_tab_badges()
        self._refresh_guide_card()

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
        self._update_tab_badges()
        self._refresh_guide_card()
