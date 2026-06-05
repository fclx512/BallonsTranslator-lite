# 1. Architecture Overview

## 1.1 Layered Design

The AI chat subsystem adopts a strict layered design, separating data/orchestration/UI into three layers:

```
┌─────────────────────────────────────────────────────┐
│  UI 层 (ui/ai_chat_panel.py)                        │
│  ┌────────────────────────────────────────────────┐ │
│  │  AiController  (utils/ai_controller.py)        │ │
│  │  ┌──────────┐  ┌───────────┐  ┌─────────────┐ │ │
│  │  │ ai_tools │  │ ai_worker │  │ ai_chat_model│ │ │
│  │  └──────────┘  └───────────┘  └─────────────┘ │ │
│  │  ┌──────────────────────────────────────────┐  │ │
│  │  │ proj_compact  (data serialisation)       │  │ │
│  │  └──────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

## 1.2 Current UI Scheme

Currently uses the **left panel scheme**, 480px wide, with 0↔480 width animation via `QVariantAnimation` (350ms InOutExpo), and the canvas flexibly fills the remaining space:

```
[LeftBar 48px] [AiChat 0↔480px] [Canvas Flexible] [RightPanel 360px]
```

UI controls adopt a simplified scheme of **CSS styling + QTextBrowser/QPlainTextEdit**, instead of the earlier QML or Creeper-QPainter approaches.

---

# 2. Backend Modules (Fully Functional, No Modifications Needed)

## 2.1 File List

| File | Layer | Purpose |
|------|-------|---------|
| `utils/ai_controller.py` | Orchestration | Conversation logic, message construction, worker lifecycle, tool loop |
| `utils/ai_tools.py` | Logic | Tool definitions, execution dispatch, system prompt, mode detection, change/text parsing |
| `utils/ai_logger.py` | Utility | Logging system (`config/ai_chat.log`, 5MB rotation) |
| `utils/proj_compact.py` | Data | Compact project serialization (index → detail), modification validation and safe application |
| `ui/ai_chat_worker.py` | Data | `QThread` streaming calls to OpenAI-compatible API |
| `ui/ai_chat_model.py` | Data | `ChangeItem`, `ChatMessage` dataclasses; `estimate_tokens()` |

## 2.2 utils/ai_logger.py

Module-level `ai_chat` logger:
- `RotatingFileHandler` → `config/ai_chat.log` (5MB, 3 backups, DEBUG)
- `StreamHandler` → stderr (INFO)
- Auto-initialized on import, downstream code uses `logging.getLogger('ai_chat')`

## 2.3 utils/proj_compact.py — Data Layer

Two-level project access for optimized token efficiency:

- **Index** (first level): Page list, block count, character statistics (`build_index()`)
- **Detail** (second level): Per-block compact representation, omitting fields matching global defaults (`build_detail()`, `build_paginated_detail()`)

**Compact key names** (1-4 characters per key):

| Key | TextBlock Field | Type |
|-----|-----------------|------|
| `id` | Constructed value `"page:block"` | string |
| `src` | `get_text()` | string |
| `trans` | `translation` | string |
| `lang` | `language` | `"ja"/"eng"/"unknown"` |
| `v` | `src_is_vertical` | bool/null |
| `lb` | `label` | string/null |
| `ff` | `fontformat.font_family` | string |
| `fs` | `fontformat.font_size` | float |
| `fw` | `fontformat.font_weight` | int/null |
| `fg` | `fontformat.frgb` | [R,G,B] |
| `bg` | `fontformat.srgb` | [R,G,B] |
| `b` | `fontformat.bold` | bool |
| `i` | `fontformat.italic` | bool |
| `a` | `fontformat.alignment` | 0=left/1=center/2=right |
| `sw` | `fontformat.stroke_width` | float |
| `ls` | `fontformat.line_spacing` | float |
| `lsp` | `fontformat.letter_spacing` | float |

**Excluded data** (AI cannot handle effectively): geometric coordinates, internal caches, detection results, advanced rendering effects (shadow/gradient/opacity/underline).

**Applying modifications**:
```python
from utils.proj_compact import apply_modifications, StaleProjectError
changed, warnings = apply_modifications(proj, mod, metadata=detail["meta"])
```
Supports wildcard `"page:*"` for batch modification of entire pages. Built-in stale project detection (hash comparison).

## 2.4 utils/ai_tools.py — Tool System

11 tool definitions, four functional groups:

**`TOOL_DEFINITIONS`** — JSON Schema for all tools:
- Read type: `list_pages`, `read_pages`, `search_blocks`, `get_config`, `get_page_info`
- Modify type: `set_font`, `set_color`, `set_layout`, `search_replace`
- Meta tool: `describe_tool`
- Standalone translation: `translate_text`

**`execute_tool(proj, name, args, fields_whitelist)`** — Dispatches tool calls to corresponding handlers.

**`parse_tool_calls(text)`** / **`parse_changes(text)`** — Extracts structured JSON from LLM responses.

**`build_agent_system_prompt(...)`** / **`build_chat_system_prompt()`** / **`build_system_prompt(...)`** — Dynamically builds system prompts (concatenates field descriptions based on `fields_whitelist`, preventing AI hallucinations).

**`detect_mode(user_text)`** — Keyword heuristic for agent vs. chat mode.

## 2.5 ui/ai_chat_worker.py

`AiChatWorker(QThread)` — Single streaming LLM call.

**Signals**: `chunk_ready(str)`, `stream_finished(str)`, `error_occurred(str)`, `token_count(int)`

**Cancellation**: `cancel()` sets a flag, `run()` exits on the next chunk.

Collects both `delta.content` and `delta.tool_calls` simultaneously; after streaming finishes, serializes tool calls as JSON and appends them to `full_text`.

## 2.6 ui/ai_chat_model.py

Pure data, no Qt widget dependencies:

```python
@dataclass
class ChangeItem:
    block_id: str       # "page_idx:block_idx"
    field: str          # e.g. "trans", "fs", "ff"
    old_value: Any
    new_value: Any
    accepted: bool | None = None
    src_text: str = ''  # Source text context for translation-type changes

@dataclass
class ChatMessage:
    role: str           # "user" | "assistant" | "system"
    content: str
    changes: list[ChangeItem] = []

def estimate_tokens(text: str) -> int:
    """Rough token count (CJK ~1.5, others ~0.25 tok/char)"""
```

---

# 3. AiController — Orchestration Hub

`utils/ai_controller.py` :: `AiController(QObject)`

## 3.1 Construction

```python
controller = AiController(
    proj_getter: Callable[[], ProjImgTrans],
    parent: QObject | None = None,
)
```

## 3.2 Signals (Listened to by UI Layer)

| Signal | Parameters | Trigger |
|--------|------------|---------|
| `system_message` | `str` | System status line |
| `thinking_started` | — | LLM begins processing |
| `thinking_finished` | — | LLM returns response or first chunk arrives |
| `streaming_started` | — | New assistant reply begins |
| `chunk_received` | `str` | Text delta appended to current bubble |
| `stream_finished` | `str` | Complete assistant text (display format, JSON stripped) |
| `changes_ready` | `list[ChangeItem]` | Parsed changes awaiting user review |
| `tool_trace_ready` | `list[dict]` | Tool execution trace |
| `prompt_tokens_estimated` | `int` | Rough estimate before API call |
| `api_tokens_reconciled` | `int` | Actual token count returned by API |
| `status_changed` | `str, bool` | Status text + active flag |
| `conversation_cleared` | — | `clear_conversation()` called |
| `error_occurred` | `str` | Unrecoverable error |

## 3.3 Methods (Called by UI Layer)

```python
controller.handle_message(user_text: str)   # Main entry point
controller.stop()                           # Cancel current worker
controller.clear_conversation()             # Clear message history
```

## 3.4 Configuration Properties

```python
controller.chat_mode          # "auto" | "agent" | "chat"
controller.fields_whitelist   # set[str], e.g. {"src", "trans", "fs"}
controller.translation_mode   # bool
controller.context_scope      # "auto" | "page" | "all"
controller.api_config         # dict: api_host, api_key, model, temperature, proxy, max_tokens
controller.custom_prompt      # str
controller.attachments        # list[{"filename": str, "content": str}] (read-only)
controller.history_path       # str, JSON file path
controller.messages           # list[ChatMessage] (read-only)
```

## 3.5 Tool Call Loop

```
User input → handle_message()
  → _resolve_mode() → agent / chat
  → _build_messages() → system_prompt + project data + history + attachments
  → panel.set_prompt_tokens(estimate)
  → _start_worker() → AiChatWorker(QThread)
    → chunk_ready → panel.append_stream_chunk()
    → stream_finished → _on_stream_finished()
      → parse_tool_calls()? → _execute_tool_calls_with_results()
        → all modify type? → _finalize_with_changes() → approval flow
        → has data type? → continue LLM rounds (max 10 rounds)
      → parse_changes() → ChangeItem[] → panel.set_changes()
```

Modify-type tools (`set_font`, `set_color`, `set_layout`, `search_replace`) return `{"type": "modifications", "changes": [...]}`, automatically routed to the approval flow, skipping additional LLM rounds.

## 3.6 History Persistence

```python
controller.history_path = osp.join(project_dir, 'ai_chat_history.json')
```
Auto-loads on set, auto-saves after each dialogue turn (includes `prompt_tokens` / `completion_tokens`).

---

# 4. Current UI Layer — AiChatPanel

`ui/ai_chat_panel.py` (~410 lines), CSS + QTextBrowser/QPlainTextEdit simplified approach.

## 4.1 Component Structure

```
AiChatPanel (QWidget, 480px wide)
├── Title bar: label + AIStatusBadge + Token count + Clear menu
├── QScrollArea message list
│   ├── AIUserBubble (QTextBrowser, right-aligned)
│   ├── AIAssistantBubble (QTextBrowser, left-aligned)
│   └── AISystemMsg (centered)
├── Input bar: _ChatInputEdit (QPlainTextEdit, Enter to send/Shift+Enter for newline) + Send/Stop buttons
└── Width animation: QVariantAnimation controls setFixedWidth, 0↔480px
```

## 4.2 Signals (Panel → External)

- `send_message(str)` — User sends a message
- `stop_requested()` — User clicks stop
- `clear_requested()` — User clicks clear

## 4.3 Controller → Panel Signal Connections

| Controller Signal | Panel Method |
|-------------------|-------------|
| `system_message` | `add_system_message()` |
| `streaming_started` | `start_streaming_response()` |
| `chunk_received` | `append_stream_chunk()` |
| `stream_finished` | `finish_streaming()` |
| `changes_ready` | `set_changes()` |
| `tool_trace_ready` | `set_last_tool_trace()` |
| `thinking_started` | `show_thinking()` |
| `thinking_finished` | `hide_thinking()` |
| `prompt_tokens_estimated` | `set_prompt_tokens()` |
| `api_tokens_reconciled` | `reconcile_api_tokens()` |
| `status_changed` | `update_status()` |
| `conversation_cleared` | `on_conversation_cleared()` |
| `error_occurred` | `on_error()` |

## 4.4 Panel Mutual Exclusion Logic

| Action | Hidden Objects |
|--------|----------------|
| Open AI Chat | PageList, GlobalSearch |
| Open PageList | AI Chat |
| Open GlobalSearch | AI Chat |
| Click imgtrans (return to canvas) | AI Chat, ConfigPanel |
| Click AI Chat button again | AI Chat (self-toggle) |

## 4.5 CSS Style Reference

All AI Chat styles are in `config/stylesheet.css:1100-1711`, key objectNames:

| Widget | objectName | CSS Selector |
|--------|------------|--------------|
| Panel container | `AiChatPanel` | `#AiChatPanel` |
| Title bar | `AITitleBar` | `#AITitleBar` |
| Status badge | `AIStatusBadge` / `AIStatusBadgeActive` | `#AIStatusBadge` etc. |
| Token label | `AITokenLabel` | `#AITokenLabel` |
| Message area | `AIChatArea` | `#AIChatArea` |
| User bubble | `AIUserBubble` | `#AIUserBubble` |
| Assistant bubble | `AIAssistantBubble` | `#AIAssistantBubble` |
| System message | `AISystemMsg` | `#AISystemMsg` |
| Input bar | `AIInputBar` | `#AIInputBar` |
| Input field | `AIInput` | `#AIInput` |
| Send button | `AISendBtn` | `#AISendBtn` |
| Stop button | `AIStopBtn` | `#AIStopBtn` |
| Clear button | `AIClearBtn` | `#AIClearBtn` |
| Change card/review | — | `#AIChangeCard`, `#AIReviewDialog` etc. |

---

# 5. Pending Work

## 5.1 P0 — Fix Existing UI Issues

1. **Markdown rendering**: Change `_streaming_browser.setHtml()` to `setMarkdown()`
2. **Bubble layout**: Assistant bubbles left-aligned, user bubbles right-aligned, system messages centered
3. **Auto-scroll**: Auto-scroll to bottom when messages arrive (needs validation for streaming scenarios)
4. **Input field height adaptation**: Currently constrained by `setFixedHeight(40)`, poor experience with long text
5. **Stop button anomaly**: `"■"` square character may render incorrectly

## 5.2 P1 — Change Review

- Change cards: display `ChangeItem` list as cards on `changes_ready`
- Per-item approval: accept/reject buttons, `accepted` state toggle
- Batch operations: accept all / reject all
- Apply modifications: call `proj_compact.apply_modifications()`, refresh canvas

## 5.3 P2 — Settings Panel

- API configuration: host / model / temperature / max_tokens
- Mode selection: auto / agent / chat
- Fields whitelist: src / trans / fs / fc / fl / ff etc.
- Context scope: auto / page / all
- Custom prompt editor
- Configuration persistence: `config/ai_chat_config.json`

## 5.4 P3 — Enhanced Features

- Thinking process panel: display tool call chain, collapsible
- Welcome card: show quick command chips when conversation is empty
- Attachment upload: backend supports it but UI has no entry point
- Regenerate: regenerate button next to assistant bubbles
- Token statistics accuracy verification

## 5.5 P4 — Polish

- i18n: all user-visible text already uses `self.tr()`, needs `translate/zh_CN.ts` update
- Shortcut: `Ctrl+Shift+A` to toggle panel
- Dark theme adaptation
- Panel memory: restore last state on startup

---

# 6. Key File Paths

| File | Purpose |
|------|---------|
| `ui/ai_chat_panel.py` | Panel main file (needs modification) |
| `ui/mainwindow.py` | Layout and signal connections (already modified) |
| `ui/mainwindowbars.py` | LeftBar buttons (already modified) |
| `utils/ai_controller.py` | Orchestrator (no modification needed) |
| `utils/ai_tools.py` | Tool system (no modification needed) |
| `utils/proj_compact.py` | Data layer (no modification needed) |
| `ui/ai_chat_worker.py` | Streaming worker (no modification needed) |
| `ui/ai_chat_model.py` | Data model (no modification needed) |
| `utils/ai_logger.py` | Logging system (no modification needed) |
| `config/stylesheet.css` | AI Chat styles (~600 lines) |
| `config/ai_chat_config.json` | API configuration persistence |
| `config/ai_chat_history.json` | Conversation history (under project directory) |

---

# Appendix: Deprecated Approaches

The following approaches have been evaluated and completely replaced. **Do not reintroduce them**:

| Approach | Description | Documentation |
|----------|-------------|---------------|
| **QML Approach** | `QQuickWidget` + 17 QML files + `QmlBridge` (413 lines) | `ai_tech_doc.md`, `ai_chat_qml_status.md` |
| **Creeper-Qt Approach** | 20 QPainter custom components (`ui/creeper/` + `ui/ai_chat/`) | `ai_chat_creeper_refactor.md`, `ai_chat_ui_architecture.md` |
| **Phase 2 Plan** | Initial UI planning document | `ai_handoff_phase2.md` |

The design concepts from the **Creeper component library** (`ui/creeper/`) — including the theme system (`ColorScheme` with 29 colors, `ThemeManager`, `ThemePack`) and animation constants (350ms InOutExpo) — **can serve as references during subsequent polishing**, but the entire set of QPainter components should not be reintroduced. The current CSS approach is simpler and more maintainable.
