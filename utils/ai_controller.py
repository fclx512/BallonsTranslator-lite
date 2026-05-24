"""
AiController — orchestration brain for the AI chat assistant.

Owns conversation logic: mode resolution, message building, LLM worker
lifecycle, tool-use loop, and response parsing.

Zero coupling to any widget class.  The panel connects to signals and
calls public methods — no duck-typing or reverse dependency needed.
"""

from __future__ import annotations

import json
import logging
import os
import os.path as osp
import re
from typing import Any, Callable, Dict, List, Optional, Set

from qtpy.QtCore import QObject, Signal, Qt

logger = logging.getLogger('ai_chat')

from .ai_tools import (
    TOOL_DEFINITIONS,
    build_agent_system_prompt,
    build_chat_system_prompt,
    detect_mode,
    execute_tool,
    parse_changes,
    parse_tool_calls,
)
from .config import pcfg
from .proj_compact import FIELD_PROMPT_SNIPPETS, parse_block_id, _COMPACT_DEF
from ui.ai_chat_model import ChatMessage, ChangeItem, estimate_tokens

MAX_TOOL_TURNS = 10


# ── Internal helpers ─────────────────────────────────────────────────────

def _summarize_args(name: str, args: Dict[str, Any]) -> str:
    """Return a one-line human-readable summary of tool arguments."""
    if name == 'describe_tool':
        return f'工具: {args.get("name", "?")}'
    elif name == 'list_pages':
        return '扫描全部页面'
    elif name == 'read_pages':
        s, e = args.get('start', 0), args.get('end', -1)
        return f'页 {s}' + (f'–{e}' if e >= 0 and e != s else '')
    elif name == 'get_page_info':
        s, e = args.get('start', 0), args.get('end', 0)
        return f'页 {s}' + (f'–{e}' if e != s else '')
    elif name == 'search_blocks':
        return f'搜索: "{args.get("query", "")}"'
    elif name == 'search_replace':
        return f'替换: "{args.get("query", "")}" → "{args.get("replacement", "")}"'
    elif name == 'set_font':
        parts = [f'{k}={v}' for k, v in args.items() if k not in ('ids',) and v is not None]
        return f'{args.get("ids", "?")}: {", ".join(parts)}' if parts else args.get('ids', '?')
    elif name == 'set_color':
        parts = [f'{k}={v}' for k, v in args.items() if k not in ('ids',) and v is not None]
        return f'{args.get("ids", "?")}: {", ".join(parts)}' if parts else args.get('ids', '?')
    elif name == 'set_layout':
        parts = [f'{k}={v}' for k, v in args.items() if k not in ('ids',) and v is not None]
        return f'{args.get("ids", "?")}: {", ".join(parts)}' if parts else args.get('ids', '?')
    elif name == 'translate_text':
        texts = args.get('texts', [])
        return f'{len(texts)} 段文本'
    elif name == 'get_config':
        return '读取全局配置'
    return ', '.join(f'{k}={v}' for k, v in list(args.items())[:2])


def _summarize_result(result: Any) -> str:
    """Return a one-line summary of a tool execution result."""
    if not isinstance(result, dict):
        return str(result)[:80]
    if 'error' in result:
        return f'错误: {result["error"]}'[:80]
    t = result.get('type', '')
    if t == 'index':
        n = result.get('total_blocks', 0)
        return f'{result.get("total_pages", "?")} 页, {n} 个文本块'
    elif t in ('detail', 'paginated_detail'):
        pages = result.get('pages', [])
        n_blocks = sum(len(p.get('blocks', [])) for p in pages)
        return f'{len(pages)} 页, {n_blocks} 个文本块'
    elif t == 'page_info':
        pages = result.get('pages', [])
        return f'{len(pages)} 页尺寸信息'
    elif t == 'search_results':
        return f'找到 {result.get("n_hits", 0)} 条匹配'
    elif t == 'modifications':
        return f'生成 {len(result.get("changes", []))} 项修改'
    elif t == 'tool_description':
        return f'{result.get("name", "?")} 的参数说明'
    elif t == 'config':
        return '全局字体和语言配置'
    elif t == 'translate_request':
        return f'{len(result.get("texts", []))} 条待翻译文本'
    return str(result)[:80]


def _strip_json_blocks(text: str) -> str:
    """Remove JSON blocks from display text."""
    depth = 0
    start = -1
    to_remove = []
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    json.loads(text[start:i + 1])
                    to_remove.append((start, i + 1))
                except json.JSONDecodeError:
                    pass
    result = []
    last_end = 0
    for s, e in sorted(to_remove):
        result.append(text[last_end:s])
        last_end = e
    result.append(text[last_end:])
    return ''.join(result).strip()


# ── Controller ───────────────────────────────────────────────────────────

class AiController(QObject):
    """Orchestrates AI chat conversations.

    No widget dependency — emit signals that any panel can connect to.
    Call :meth:`handle_message` when the user sends text, and connect
    to the output signals for UI updates.
    """

    # ── Output signals (panel connects to these) ─────────────────
    system_message = Signal(str)
    """Emit a system-status line for the conversation."""

    thinking_started = Signal()
    thinking_finished = Signal()

    streaming_started = Signal()
    """A new assistant response bubble should begin."""

    chunk_received = Signal(str)
    """A text delta from the LLM stream."""

    stream_finished = Signal(str)
    """The full assistant text is ready (display form, no JSON)."""

    changes_ready = Signal(list)
    """Parsed ChangeItems available for review."""

    tool_trace_ready = Signal(list)
    """Tool execution trace metadata for display."""

    prompt_tokens_estimated = Signal(int)
    """Rough token count before the API call."""

    api_tokens_reconciled = Signal(int)
    """Real token count from the API response."""

    status_changed = Signal(str, bool)
    """(status_text, active) — e.g. ('处理中...', True)."""

    conversation_cleared = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        proj_getter: Callable[[], Any],
        parent=None,
    ):
        super().__init__(parent)
        self._proj_getter = proj_getter
        self._worker: Optional['AiChatWorker'] = None
        self._turn_messages: List[Dict[str, str]] = []
        self._accumulated_text = ''
        self._last_user_text = ''
        self._is_running = False

        # ── Conversation history ─────────────────────────────────
        self.messages: List[ChatMessage] = []
        self._token_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._pending_prompt_estimate = 0

        # ─── History persistence ─────────────────────────────────
        self._history_path: str = ''
        self._settings_path: str = ''

        # ── Config (settable by panel) ───────────────────────────
        self._chat_mode: str = 'auto'
        self._fields_whitelist: Set[str] = {'src', 'trans'}
        self._translation_mode: bool = False
        self._context_scope: str = 'auto'
        self._context_message_limit: int = 20
        self._auto_compress: bool = False
        self._attachments: List[Dict[str, str]] = []
        self._api_config: Dict[str, Any] = {}
        self._custom_prompt: str = ''

    # ── Public API ────────────────────────────────────────────────

    def handle_message(self, user_text: str):
        """Entry point. Called when the user sends a message."""
        if self._is_running:
            self.system_message.emit('── 上一轮对话仍在处理中，请稍后再试 ──')
            return

        proj = self._proj_getter()
        if not proj or not hasattr(proj, 'pages') or len(proj.pages) == 0:
            self.system_message.emit('── 错误：未打开项目 ──')
            self.status_changed.emit('待机中', False)
            return

        self._last_user_text = user_text
        self._accumulated_text = ''
        self._is_running = True
        self.status_changed.emit('处理中...', True)

        mode = self._resolve_mode(user_text)
        logger.info("handle_message: mode=%s text_len=%d", mode, len(user_text))
        api_config = self._api_config
        if not api_config.get('api_host') or not api_config.get('model'):
            logger.warning("handle_message: missing API config")
            self._is_running = False
            self.system_message.emit('── 请先在设置中选择 API 模型 ──')
            self.status_changed.emit('待机中', False)
            return

        messages = self._build_messages(user_text, mode)
        self._turn_messages = messages
        logger.info("_build_messages: %d messages, starting worker", len(messages))
        self.thinking_started.emit()
        self.streaming_started.emit()
        self._start_worker(api_config, messages, tool_turns_left=MAX_TOOL_TURNS)

    def stop(self):
        """Cancel the active worker."""
        self._is_running = False
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.system_message.emit('── 已停止生成 ──')
            self.stream_finished.emit('')
            self.status_changed.emit('待机中', False)

    def clear_conversation(self):
        """Clear all messages and reset token counters."""
        self._is_running = False
        self.messages.clear()
        self._token_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._pending_prompt_estimate = 0
        self._last_user_text = ''
        self.conversation_cleared.emit()
        self._save_history()

    # ── Config setters ────────────────────────────────────────────

    @property
    def chat_mode(self) -> str:
        return self._chat_mode

    @chat_mode.setter
    def chat_mode(self, value: str):
        self._chat_mode = value

    @property
    def fields_whitelist(self) -> Set[str]:
        return self._fields_whitelist.copy()

    @fields_whitelist.setter
    def fields_whitelist(self, value: Set[str]):
        self._fields_whitelist = set(value)

    @property
    def translation_mode(self) -> bool:
        return self._translation_mode

    @translation_mode.setter
    def translation_mode(self, value: bool):
        self._translation_mode = value

    @property
    def context_scope(self) -> str:
        return self._context_scope

    @context_scope.setter
    def context_scope(self, value: str):
        self._context_scope = value

    @property
    def context_message_limit(self) -> int:
        return self._context_message_limit

    @context_message_limit.setter
    def context_message_limit(self, value: int):
        self._context_message_limit = max(10, min(99, int(value)))

    @property
    def auto_compress(self) -> bool:
        return self._auto_compress

    @auto_compress.setter
    def auto_compress(self, value: bool):
        self._auto_compress = value

    @property
    def attachments(self) -> List[Dict[str, str]]:
        return list(self._attachments)

    def add_attachment(self, filename: str, content: str):
        self._attachments.append({'filename': filename, 'content': content})

    def remove_attachment(self, filename: str):
        self._attachments = [a for a in self._attachments if a['filename'] != filename]

    @property
    def api_config(self) -> Dict[str, Any]:
        return dict(self._api_config)

    @api_config.setter
    def api_config(self, value: Dict[str, Any]):
        self._api_config = dict(value)

    @property
    def custom_prompt(self) -> str:
        return self._custom_prompt

    @custom_prompt.setter
    def custom_prompt(self, value: str):
        self._custom_prompt = value

    # ── History persistence ──────────────────────────────────────

    @property
    def history_path(self) -> str:
        return self._history_path

    @property
    def settings_path(self) -> str:
        return self._settings_path

    @settings_path.setter
    def settings_path(self, path: str):
        self._settings_path = path

    @history_path.setter
    def history_path(self, path: str):
        self._history_path = path
        self._load_history()

    def _load_history(self):
        """Load conversation from JSON file."""
        self.messages.clear()
        self._token_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0

        if not self._history_path or not osp.exists(self._history_path):
            return
        try:
            with open(self._history_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for m in data.get('messages', []):
                changes = [
                    ChangeItem(
                        block_id=c.get('block_id', ''),
                        field=c.get('field', ''),
                        old_value=c.get('old_value'),
                        new_value=c.get('new_value'),
                        accepted=c.get('accepted'),
                        src_text=c.get('src_text', ''),
                    )
                    for c in m.get('changes', [])
                ]
                self.messages.append(ChatMessage(
                    role=m.get('role', ''),
                    content=m.get('content', ''),
                    changes=changes,
                ))
            self._token_count = data.get('token_count', 0)
            self._prompt_tokens = data.get('prompt_tokens', 0)
            self._completion_tokens = data.get('completion_tokens', 0)
        except (json.JSONDecodeError, OSError):
            pass

    def _save_history(self):
        """Persist conversation to JSON file."""
        if not self._history_path:
            return
        try:
            data = {
                'messages': [
                    {
                        'role': m.role,
                        'content': m.content,
                        'changes': [
                            {
                                'block_id': c.block_id,
                                'field': c.field,
                                'old_value': c.old_value,
                                'new_value': c.new_value,
                                'accepted': c.accepted,
                                'src_text': c.src_text,
                            }
                            for c in (m.changes or [])
                        ],
                    }
                    for m in self.messages
                ],
                'token_count': self._token_count,
                'prompt_tokens': self._prompt_tokens,
                'completion_tokens': self._completion_tokens,
            }
            with open(self._history_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ── Settings persistence ─────────────────────────────────────────

    def load_ai_settings(self, path: str = ''):
        """Read AI settings from a JSON file, restoring controller state."""
        path = path or self._settings_path
        if not path or not osp.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'api_config' in data:
                api_cfg = data['api_config']
                self._api_config = api_cfg if isinstance(api_cfg, dict) else {}
            if 'chat_mode' in data:
                self._chat_mode = data['chat_mode']
            if 'context_scope' in data:
                self._context_scope = data['context_scope']
            if 'translation_mode' in data:
                self._translation_mode = data['translation_mode']
            if 'custom_prompt' in data:
                self._custom_prompt = data['custom_prompt']
            if 'fields_whitelist' in data:
                self._fields_whitelist = set(data['fields_whitelist'])
            if 'context_message_limit' in data:
                self._context_message_limit = data['context_message_limit']
            if 'auto_compress' in data:
                self._auto_compress = data['auto_compress']
        except (json.JSONDecodeError, OSError):
            pass

    def save_ai_settings(self, path: str = ''):
        """Persist current AI settings to a JSON file."""
        path = path or self._settings_path
        if not path:
            return
        try:
            data = {
                'api_config': self._api_config,
                'chat_mode': self._chat_mode,
                'context_scope': self._context_scope,
                'translation_mode': self._translation_mode,
                'custom_prompt': self._custom_prompt,
                'fields_whitelist': sorted(self._fields_whitelist),
                'context_message_limit': self._context_message_limit,
                'auto_compress': self._auto_compress,
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ── Mode resolution ───────────────────────────────────────────

    def _resolve_mode(self, user_text: str) -> str:
        if self._chat_mode in ('agent', 'chat'):
            return self._chat_mode
        return detect_mode(user_text)

    # ── Message building ──────────────────────────────────────────

    def _build_messages(
        self, user_text: str, mode: str
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []

        # System prompt
        whitelist = self._fields_whitelist
        is_trans = self._translation_mode
        custom_prompt = self._custom_prompt

        if is_trans and custom_prompt:
            from_lang = getattr(pcfg.module, 'lang_source', '') if hasattr(pcfg, 'module') else ''
            to_lang = getattr(pcfg.module, 'lang_target', '') if hasattr(pcfg, 'module') else ''
            sys_prompt = custom_prompt.replace('{from_lang}', from_lang or 'auto').replace('{to_lang}', to_lang or 'auto')
            sys_prompt += '\n\n你仍然可以使用工具来读取和修改项目数据。'
        elif mode == 'agent':
            sys_prompt = build_agent_system_prompt(
                fields_whitelist=whitelist,
                translation_mode=is_trans,
            )
        else:
            sys_prompt = build_chat_system_prompt()
        messages.append({'role': 'system', 'content': sys_prompt})

        # Project data injection (agent mode only)
        if mode == 'agent':
            proj = self._proj_getter()
            scope = self._context_scope
            if scope == 'auto':
                index = execute_tool(proj, 'list_pages', {})
                messages.append({
                    'role': 'system',
                    'content': f'[项目索引]\n{json.dumps(index, ensure_ascii=False, indent=2)}',
                })
            elif scope == 'all':
                index = execute_tool(proj, 'list_pages', {})
                detail = execute_tool(proj, 'read_pages', {'start': 0, 'end': -1}, whitelist)
                messages.append({
                    'role': 'system',
                    'content': (
                        f'[项目索引]\n{json.dumps(index, ensure_ascii=False, indent=2)}\n\n'
                        f'[全部页面数据]\n{json.dumps(detail, ensure_ascii=False, indent=2)}'
                    ),
                })
            else:  # 'page'
                current = max(getattr(proj, 'current_idx', 0), 0)
                detail = execute_tool(proj, 'read_pages', {'start': current, 'end': current}, whitelist)
                messages.append({
                    'role': 'system',
                    'content': f'[当前页数据]\n{json.dumps(detail, ensure_ascii=False, indent=2)}',
                })

        # Conversation history (last N messages, user-configurable; cap at ~8000 chars)
        limit = self._context_message_limit
        texts: List[str] = []
        total_chars = 0
        for m in reversed(self.messages[-limit:]):
            texts.insert(0, m.content)
            total_chars += len(m.content)
            if total_chars > 8000:
                break
        for m in self.messages[-len(texts):]:
            messages.append({'role': m.role, 'content': m.content})

        # Attachments
        for att in self._attachments:
            messages.append({
                'role': 'system',
                'content': f'[附件: {att["filename"]}]\n{att["content"]}',
            })

        # User message
        messages.append({'role': 'user', 'content': user_text})
        return messages

    # ── Worker lifecycle ──────────────────────────────────────────

    def _start_worker(
        self,
        api_config: Dict[str, Any],
        messages: List[Dict[str, str]],
        tool_turns_left: int,
    ):
        from ui.ai_chat_worker import AiChatWorker
        prompt_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
        self._pending_prompt_estimate = prompt_tokens
        self._prompt_tokens += prompt_tokens
        self._token_count += prompt_tokens
        self.prompt_tokens_estimated.emit(prompt_tokens)
        self._worker = AiChatWorker(api_config, messages)
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.stream_finished.connect(
            lambda full: self._on_stream_finished(full, messages, tool_turns_left),
            Qt.ConnectionType.SingleShotConnection,
        )
        self._worker.error_occurred.connect(
            self._on_worker_error, Qt.ConnectionType.SingleShotConnection,
        )
        self._worker.token_count.connect(
            self._on_api_tokens, Qt.ConnectionType.SingleShotConnection,
        )
        self._worker.start()

    def _on_chunk(self, chunk: str):
        self.thinking_finished.emit()
        self._accumulated_text += chunk
        self.chunk_received.emit(chunk)

    def _on_api_tokens(self, total: int):
        if self._pending_prompt_estimate > 0:
            self._token_count -= self._pending_prompt_estimate
            self._pending_prompt_estimate = 0
        self._token_count += total
        self._completion_tokens += total
        self.api_tokens_reconciled.emit(total)

    def _on_stream_finished(
        self,
        full_text: str,
        messages: List[Dict[str, str]],
        tool_turns_left: int,
    ):
        logger.info("_on_stream_finished: text_len=%d turns_left=%d",
                     len(full_text), tool_turns_left)
        tool_calls = parse_tool_calls(full_text)
        if tool_calls and tool_turns_left > 0:
            logger.info("_on_stream_finished: %d tool calls found, executing",
                         len(tool_calls))
            tool_results = self._execute_tool_calls_with_results(tool_calls, messages)

            mod_changes = []
            for tr in tool_results:
                if isinstance(tr, dict) and tr.get('type') == 'modifications':
                    mod_changes.extend(tr.get('changes', []))
            if mod_changes and all(
                isinstance(tr, dict) and tr.get('type') == 'modifications'
                for tr in tool_results
            ):
                self.system_message.emit(
                    f'── AI 生成了 {len(mod_changes)} 项修改提案 ──')
                self._finalize_with_changes(mod_changes)
                return

            if tool_turns_left - 1 > 0:
                self.system_message.emit(
                    f'── AI 调用了 {len(tool_calls)} 个工具，继续处理... ──')
                self.stream_finished.emit(self._accumulated_text)
                self._accumulated_text = ''
                self.streaming_started.emit()
                self._start_worker(
                    self._api_config, messages,
                    tool_turns_left - 1,
                )
                return

        self._finalize_turn(full_text)

    def _on_worker_error(self, error_msg: str):
        logger.error("Worker error: %s", error_msg)
        self._is_running = False
        self.thinking_finished.emit()
        self.system_message.emit(f'── 错误：{error_msg} ──')
        self.stream_finished.emit('')
        self.status_changed.emit('待机中', False)

    def _execute_tool_calls_with_results(
        self,
        tool_calls: List[Dict[str, Any]],
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Execute tool calls, append results to messages, return raw results."""
        proj = self._proj_getter()
        whitelist = self._fields_whitelist
        messages.append({
            'role': 'assistant',
            'content': json.dumps({'tool_calls': tool_calls}, ensure_ascii=False),
        })
        results: List[Dict[str, Any]] = []
        auto_changes: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []
        for tc in tool_calls:
            try:
                result = execute_tool(proj, tc['name'], tc.get('arguments', {}), whitelist)
                logger.info("Tool executed: %s → type=%s", tc['name'], result.get('type', '?'))
            except Exception as e:
                logger.exception("Tool execution error: %s", tc.get('name', '?'))
                result = {'error': str(e)}
            results.append(result)
            trace.append({
                'name': tc['name'],
                'args_summary': _summarize_args(tc['name'], tc.get('arguments', {})),
                'result_type': result.get('type', 'error') if isinstance(result, dict) else 'unknown',
                'result_summary': _summarize_result(result),
                'is_error': isinstance(result, dict) and 'error' in result,
            })
            if isinstance(result, dict) and result.get('type') == 'modifications':
                auto_changes.extend(result.get('changes', []))
            else:
                messages.append({
                    'role': 'user',
                    'content': json.dumps(result, ensure_ascii=False, indent=2),
                })
        if auto_changes:
            messages.append({
                'role': 'user',
                'content': json.dumps({
                    'type': 'modifications_applied',
                    'n_changes': len(auto_changes),
                    'changes': auto_changes,
                }, ensure_ascii=False, indent=2),
            })
        self.tool_trace_ready.emit(trace)
        return results

    def _finalize_with_changes(self, raw_changes: List[Dict[str, Any]]):
        """Create ChangeItems from raw change dicts and finish the turn."""
        self._is_running = False
        self.thinking_finished.emit()
        proj = self._proj_getter()
        changes: List[ChangeItem] = []
        for c in raw_changes:
            block_id = c.get('id', '')
            src_text = self._lookup_old_value(proj, block_id, 'src') or ''
            for key in c:
                if key == 'id':
                    continue
                old_val = self._lookup_old_value(proj, block_id, key)
                changes.append(ChangeItem(
                    block_id=block_id,
                    field=key,
                    old_value=old_val,
                    new_value=c[key],
                    src_text=src_text,
                ))
        self.changes_ready.emit(changes)
        self.stream_finished.emit('')

    def _finalize_turn(self, full_text: str):
        self._is_running = False
        self.thinking_finished.emit()
        self.status_changed.emit('待机中', False)

        changes_raw = parse_changes(full_text)
        logger.info("_finalize_turn: parsed %d changes from %d chars",
                     len(changes_raw) if changes_raw else 0, len(full_text))
        display_text = _strip_json_blocks(full_text)

        changes: List[ChangeItem] = []
        if changes_raw:
            proj = self._proj_getter()
            for c in changes_raw:
                block_id = c.get('id', '')
                src_text = self._lookup_old_value(proj, block_id, 'src') or ''
                old_values = {}
                for key in c:
                    if key == 'id':
                        continue
                    old_values[key] = self._lookup_old_value(proj, block_id, key)
                for key, old_val in old_values.items():
                    changes.append(ChangeItem(
                        block_id=block_id,
                        field=key,
                        old_value=old_val,
                        new_value=c[key],
                        src_text=src_text,
                    ))

        self.changes_ready.emit(changes)
        self.stream_finished.emit(display_text)

        # Record in history
        self.messages.append(ChatMessage(role='user', content=self._last_user_text))
        self.messages.append(ChatMessage(role='assistant', content=display_text,
                                          changes=list(changes)))
        self._save_history()

    @staticmethod
    def _lookup_old_value(proj, block_id: str, field: str) -> str:
        """Read the current project value for a block+field combination."""
        try:
            from .proj_compact import parse_block_id, _COMPACT_DEF
            pidx, bidx = parse_block_id(block_id)
            if pidx is None or bidx is None:
                return ''
            page = proj.pages[proj.idx2pagename(pidx)]
            if bidx >= len(page):
                return ''
            blk = page[bidx]
            attr_name = _COMPACT_DEF.get(field, (None,))[0]
            if attr_name:
                val = getattr(blk, attr_name, '')
                return str(val) if val is not None else ''
            return ''
        except Exception:
            return ''
