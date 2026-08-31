"""术语/剧情整合 agent 工作台(方案见 memory/context-agent-workbench-plan)。

- ``GlossaryAgentWorker``:QThread 内的权威状态持有者。AI patch 只落
  worker 草稿(modules/context_agent/draft.py),经同步信号单向刷新
  UI 镜像;落盘只经「应用」按钮。LLM 会话复用 AgentTranslator 的
  profile/client/重试基建(translator 实例在 worker 线程内构造)。
- ``GlossaryAgentPanel``:RailDockPanel 内容(窄栏入口见
  ui/text_panel.py::install_glossary_launcher)。三页:Chat(日志流 +
  指令输入)、Glossary(草稿表)、Story(页段摘要 + 全局梗概)。

替代原 GlossaryExtractorDialog(one-shot LLM 提取,已删)。
"""

import json
import logging

from qtpy.QtCore import QObject, QThread, Qt, Signal, Slot
from qtpy.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
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

logger = logging.getLogger("glossary_agent_panel")

_STOP_REASONS = {
    STOP_MAX_TURNS: "Turn limit reached — the round ended without a reply.",
    STOP_TOKEN_BUDGET: "Token budget reached — the round ended without a reply.",
    STOP_CANCELLED: "Cancelled.",
}

_ORIGIN_LABELS = {
    ORIGIN_EXISTING: "base",
    ORIGIN_AI: "AI",
    ORIGIN_USER: "you",
}


class GlossaryAgentWorker(QObject):
    """worker 线程内的权威草稿 + LLM 会话执行体。"""

    log_line = Signal(str)
    busy_changed = Signal(bool)
    glossary_synced = Signal(list)  # [(src, dst, note, origin)]
    story_synced = Signal(str, list)  # (synopsis, [(page, summary, origin)])
    round_finished = Signal(str)  # 最终回复(可为空)
    round_failed = Signal(str)
    applied = Signal(str)

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
            translator._translated_lang(pcfg.target_lang),
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
                pcfg.source_lang,
                pcfg.target_lang,
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        # ── Chat 页 ────────────────────────────────────────────
        chat_tab = QWidget(self)
        chat_layout = QVBoxLayout(chat_tab)
        chat_layout.setContentsMargins(0, 4, 0, 0)
        self.log_view = QTextBrowser(chat_tab)
        self.log_view.setOpenExternalLinks(False)
        chat_layout.addWidget(self.log_view, 1)
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
        self.prefill_btn = QPushButton(self.tr("Prefill (frequency)"), glossary_tab)
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

        # ── Apply 行 ───────────────────────────────────────────
        apply_row = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("Apply draft…"), self)
        apply_row.addStretch(1)
        apply_row.addWidget(self.apply_btn)
        layout.addLayout(apply_row)

        self._connect_signals()

    # ── 信号接线 ──────────────────────────────────────────────

    def _connect_signals(self):
        self.send_btn.clicked.connect(self._on_send)
        self.input_edit.installEventFilter(self)
        self.synopsis_edit.installEventFilter(self)
        self.stop_btn.clicked.connect(lambda: self._worker.request_stop())
        self.prefill_btn.clicked.connect(
            lambda: self._worker.prefill_from_frequency()
        )
        self.glossary_remove_btn.clicked.connect(self._on_glossary_remove)
        self.story_remove_btn.clicked.connect(self._on_story_remove)
        self.glossary_table.itemChanged.connect(self._on_glossary_item_changed)
        self.story_table.itemChanged.connect(self._on_story_item_changed)
        self.apply_btn.clicked.connect(self._on_apply)

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
        from qtpy.QtWidgets import QApplication

        QApplication.instance().aboutToQuit.connect(self._shutdown)
        return self._worker

    def _wire_worker(self, worker: "GlossaryAgentWorker"):
        """worker → UI 镜像接线(测试可直接调用以绕过线程)。"""
        worker.log_line.connect(self._append_log)
        worker.busy_changed.connect(self._on_busy_changed)
        worker.glossary_synced.connect(self._sync_glossary_table)
        worker.story_synced.connect(self._sync_story_tab)
        worker.round_finished.connect(
            lambda reply: self._append_log(
                reply or "(no reply)"
            )
        )
        worker.round_failed.connect(
            lambda err: self._append_log(f"[error] {err}")
        )
        worker.applied.connect(self._append_log)

    def _shutdown(self):
        if self._worker is not None:
            self._worker.shutdown()
            self._worker = None
            self._thread = None

    # ── 用户动作 ──────────────────────────────────────────────

    def _on_send(self):
        text = self.input_edit.toPlainText().strip()
        if not text or (self._worker is not None and self._worker._busy):
            return
        worker = self._ensure_worker()
        self._append_log(f"> {text}")
        self.input_edit.clear()
        worker.run_instruction(text)

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
        worker.apply_glossary(path)
        worker.apply_story()

    def _on_glossary_remove(self):
        rows = sorted(
            {i.row() for i in self.glossary_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            item = self.glossary_table.item(row, 0)
            if item is not None:
                self._worker.user_glossary_remove(item.text())

    def _on_story_remove(self):
        rows = sorted(
            {i.row() for i in self.story_table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            item = self.story_table.item(row, 0)
            if item is not None:
                self._worker.user_summary_remove(item.text())

    def _on_synopsis_edited(self):
        if self._syncing:
            return
        self._worker.user_synopsis_edit(self.synopsis_edit.toPlainText())

    def _on_glossary_item_changed(self, item):
        if self._syncing or item.column() == 3:
            return
        row = item.row()
        src_item = self.glossary_table.item(row, 0)
        dst_item = self.glossary_table.item(row, 1)
        note_item = self.glossary_table.item(row, 2)
        if src_item is None or dst_item is None:
            return
        self._worker.user_glossary_edit(
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
        self._worker.user_summary_edit(page_item.text(), item.text())

    def _on_busy_changed(self, busy: bool):
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setVisible(busy)
        self.prefill_btn.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.glossary_table.setEnabled(not busy)
        self.story_table.setEnabled(not busy)
        if busy:
            self.tabs.setCurrentIndex(0)

    # ── worker → UI 镜像同步 ─────────────────────────────────

    def _append_log(self, text: str):
        self.log_view.append(text)

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
                    self.story_table.setItem(r, c, item)
        finally:
            self._syncing = False
