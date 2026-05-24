# 一、架构总览

## 1.1 分层设计

AI 聊天子系统采用严格的分层设计，数据 / 编排 / UI 三层分离：

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

## 1.2 当前 UI 方案

当前使用 **左侧面板方案**，宽度 480px，通过 `QVariantAnimation` 做 0↔480 宽度动画（350ms InOutExpo），canvas 弹性填充剩余空间：

```
[LeftBar 48px] [AiChat 0↔480px] [Canvas 弹性] [RightPanel 360px]
```

UI 控件采用 **CSS 样式 + QTextBrowser/QPlainTextEdit** 的简化方案，而非早期的 QML 或 Creeper-QPainter 方案。

---

# 二、后端模块（完整可用，无需修改）

## 2.1 文件清单

| 文件 | 层 | 用途 |
|------|-----|------|
| `utils/ai_controller.py` | 编排 | 对话逻辑、消息构建、worker 生命周期、工具循环 |
| `utils/ai_tools.py` | 逻辑 | 工具定义、执行分发、系统 prompt、模式检测、变更/文本解析 |
| `utils/ai_logger.py` | 工具 | 日志系统（`config/ai_chat.log`，5MB 轮转） |
| `utils/proj_compact.py` | 数据 | 紧凑项目序列化（index → detail），修改验证与安全应用 |
| `ui/ai_chat_worker.py` | 数据 | `QThread` 流式调用 OpenAI 兼容 API |
| `ui/ai_chat_model.py` | 数据 | `ChangeItem`、`ChatMessage` dataclass；`estimate_tokens()` |

## 2.2 utils/ai_logger.py

模块级 `ai_chat` logger：
- `RotatingFileHandler` → `config/ai_chat.log`（5MB，3 备份，DEBUG）
- `StreamHandler` → stderr（INFO）
- 导入时自动初始化，下游代码用 `logging.getLogger('ai_chat')`

## 2.3 utils/proj_compact.py — 数据层

两级项目访问，优化 token 效率：

- **Index**（第一级）：页面列表、块数量、字符统计（`build_index()`）
- **Detail**（第二级）：逐块紧凑表示，省略匹配全局默认值的字段（`build_detail()`, `build_paginated_detail()`）

**紧凑键名**（单键 1-4 字符）：

| 键 | TextBlock 字段 | 类型 |
|----|---------------|------|
| `id` | 构造值 `"页:块"` | string |
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
| `a` | `fontformat.alignment` | 0=左/1=中/2=右 |
| `sw` | `fontformat.stroke_width` | float |
| `ls` | `fontformat.line_spacing` | float |
| `lsp` | `fontformat.letter_spacing` | float |

**排除的数据**（AI 无法有效处理）：几何坐标、内部缓存、检测结果、高级渲染效果（shadow/gradient/opacity/underline）。

**修改应用**：
```python
from utils.proj_compact import apply_modifications, StaleProjectError
changed, warnings = apply_modifications(proj, mod, metadata=detail["meta"])
```
支持通配符 `"页:*"` 批量修改整页。内建陈旧项目检测（hash 比对）。

## 2.4 utils/ai_tools.py — 工具系统

11 个工具定义，四个功能组：

**`TOOL_DEFINITIONS`** — 所有工具的 JSON Schema：
- 读取类：`list_pages`、`read_pages`、`search_blocks`、`get_config`、`get_page_info`
- 修改类：`set_font`、`set_color`、`set_layout`、`search_replace`
- 元工具：`describe_tool`
- 独立翻译：`translate_text`

**`execute_tool(proj, name, args, fields_whitelist)`** — 分发工具调用到对应 handler。

**`parse_tool_calls(text)`** / **`parse_changes(text)`** — 从 LLM 响应中提取结构化 JSON。

**`build_agent_system_prompt(...)`** / **`build_chat_system_prompt()`** / **`build_system_prompt(...)`** — 动态构建 system prompt（根据 `fields_whitelist` 拼接字段描述，杜绝 AI 幻觉）。

**`detect_mode(user_text)`** — 关键词启发式判断 agent vs. chat 模式。

## 2.5 ui/ai_chat_worker.py

`AiChatWorker(QThread)` — 单次流式 LLM 调用。

**信号**：`chunk_ready(str)`、`stream_finished(str)`、`error_occurred(str)`、`token_count(int)`

**取消**：`cancel()` 设置标志，`run()` 在下一个 chunk 退出。

同时收集 `delta.content` 和 `delta.tool_calls`，流结束后将 tool calls 序列化为 JSON 拼入 full_text。

## 2.6 ui/ai_chat_model.py

纯数据，无 Qt widget 依赖：

```python
@dataclass
class ChangeItem:
    block_id: str       # "page_idx:block_idx"
    field: str          # e.g. "trans", "fs", "ff"
    old_value: Any
    new_value: Any
    accepted: bool | None = None
    src_text: str = ''  # 翻译类变更的原文上下文

@dataclass
class ChatMessage:
    role: str           # "user" | "assistant" | "system"
    content: str
    changes: list[ChangeItem] = []

def estimate_tokens(text: str) -> int:
    """粗略 token 计数（CJK ~1.5, 其他 ~0.25 tok/char）"""
```

---

# 三、AiController — 编排中枢

`utils/ai_controller.py` :: `AiController(QObject)`

## 3.1 构造

```python
controller = AiController(
    proj_getter: Callable[[], ProjImgTrans],
    parent: QObject | None = None,
)
```

## 3.2 信号（UI 层监听）

| 信号 | 参数 | 触发时机 |
|------|------|----------|
| `system_message` | `str` | 系统状态行 |
| `thinking_started` | — | LLM 开始处理 |
| `thinking_finished` | — | LLM 返回响应或首 chunk 到达 |
| `streaming_started` | — | 新的助手回复开始 |
| `chunk_received` | `str` | 文本增量追加到当前气泡 |
| `stream_finished` | `str` | 完整助手文本（显示格式，JSON 已剥离） |
| `changes_ready` | `list[ChangeItem]` | 解析出的变更供用户审核 |
| `tool_trace_ready` | `list[dict]` | 工具执行追踪 |
| `prompt_tokens_estimated` | `int` | API 调用前粗略估算 |
| `api_tokens_reconciled` | `int` | API 返回的真实 token 数 |
| `status_changed` | `str, bool` | 状态文字 + active 标志 |
| `conversation_cleared` | — | `clear_conversation()` 被调用 |
| `error_occurred` | `str` | 不可恢复错误 |

## 3.3 方法（UI 层调用）

```python
controller.handle_message(user_text: str)   # 主入口
controller.stop()                           # 取消当前 worker
controller.clear_conversation()             # 清空消息历史
```

## 3.4 配置属性

```python
controller.chat_mode          # "auto" | "agent" | "chat"
controller.fields_whitelist   # set[str], e.g. {"src", "trans", "fs"}
controller.translation_mode   # bool
controller.context_scope      # "auto" | "page" | "all"
controller.api_config         # dict: api_host, api_key, model, temperature, proxy, max_tokens
controller.custom_prompt      # str
controller.attachments        # list[{"filename": str, "content": str}] (只读)
controller.history_path       # str, JSON 文件路径
controller.messages           # list[ChatMessage] (只读)
```

## 3.5 工具调用循环

```
用户输入 → handle_message()
  → _resolve_mode() → agent / chat
  → _build_messages() → system_prompt + 项目数据 + 历史 + 附件
  → panel.set_prompt_tokens(估算)
  → _start_worker() → AiChatWorker(QThread)
    → chunk_ready → panel.append_stream_chunk()
    → stream_finished → _on_stream_finished()
      → parse_tool_calls()? → _execute_tool_calls_with_results()
        → 全部修改类? → _finalize_with_changes() → 审批流程
        → 有数据类? → 继续 LLM 轮次（最多 10 轮）
      → parse_changes() → ChangeItem[] → panel.set_changes()
```

修改类工具（`set_font`、`set_color`、`set_layout`、`search_replace`）返回 `{"type": "modifications", "changes": [...]}`，自动路由到审批流程，跳过额外 LLM 轮次。

## 3.6 历史持久化

```python
controller.history_path = osp.join(project_dir, 'ai_chat_history.json')
```
设置后自动加载，每轮对话结束后自动保存（含 `prompt_tokens` / `completion_tokens`）。

---

# 四、当前 UI 层 — AiChatPanel

`ui/ai_chat_panel.py`（~410 行），CSS + QTextBrowser/QPlainTextEdit 简化方案。

## 4.1 组件结构

```
AiChatPanel (QWidget, 480px 宽)
├── 标题栏: 标签 + AIStatusBadge + Token 计数 + 清空菜单
├── QScrollArea 消息列表
│   ├── AIUserBubble (QTextBrowser, 右对齐)
│   ├── AIAssistantBubble (QTextBrowser, 左对齐)
│   └── AISystemMsg (居中)
├── 输入栏: _ChatInputEdit (QPlainTextEdit, Enter 发送/Shift+Enter 换行) + 发送/停止按钮
└── 宽度动画: QVariantAnimation 控制 setFixedWidth, 0↔480px
```

## 4.2 信号（Panel → 外部）

- `send_message(str)` — 用户发送消息
- `stop_requested()` — 用户点击停止
- `clear_requested()` — 用户点击清空

## 4.3 Controller → Panel 信号连线

| Controller 信号 | Panel 方法 |
|------|------|
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

## 4.4 面板互斥逻辑

| 操作 | 隐藏的对象 |
|------|-----------|
| 打开 AI Chat | PageList、GlobalSearch |
| 打开 PageList | AI Chat |
| 打开 GlobalSearch | AI Chat |
| 点击 imgtrans（返回画布） | AI Chat、ConfigPanel |
| 再次点击 AI Chat 按钮 | AI Chat（自身 toggle） |

## 4.5 CSS 样式参考

所有 AI Chat 样式在 `config/stylesheet.css:1100-1711`，关键 objectName：

| Widget | objectName | CSS 选择器 |
|--------|-----------|-----------|
| 面板容器 | `AiChatPanel` | `#AiChatPanel` |
| 标题栏 | `AITitleBar` | `#AITitleBar` |
| 状态徽章 | `AIStatusBadge` / `AIStatusBadgeActive` | `#AIStatusBadge` 等 |
| Token 标签 | `AITokenLabel` | `#AITokenLabel` |
| 消息区域 | `AIChatArea` | `#AIChatArea` |
| 用户气泡 | `AIUserBubble` | `#AIUserBubble` |
| 助手气泡 | `AIAssistantBubble` | `#AIAssistantBubble` |
| 系统消息 | `AISystemMsg` | `#AISystemMsg` |
| 输入栏 | `AIInputBar` | `#AIInputBar` |
| 输入框 | `AIInput` | `#AIInput` |
| 发送按钮 | `AISendBtn` | `#AISendBtn` |
| 停止按钮 | `AIStopBtn` | `#AIStopBtn` |
| 清空按钮 | `AIClearBtn` | `#AIClearBtn` |
| 变更卡片/审核 | — | `#AIChangeCard`、`#AIReviewDialog` 等 |

---

# 五、待完成工作

## 5.1 P0 — 修复现有 UI 问题

1. **Markdown 渲染**：将 `_streaming_browser.setHtml()` 改为 `setMarkdown()`
2. **气泡布局**：助手气泡左对齐、用户气泡右对齐、系统消息居中
3. **自动滚动**：消息到达时自动滚到底部（需验证流式场景有效性）
4. **输入框高度自适应**：当前 `setFixedHeight(40)` 限制死，长文本体验差
5. **停止按钮异常**：`"■"` 方块字符可能渲染异常

## 5.2 P1 — 变更审核

- 变更卡片：`changes_ready` 时以卡片展示 `ChangeItem` 列表
- 逐项审批：接受/拒绝按钮，`accepted` 状态切换
- 批量操作：全接受 / 全拒绝
- 应用修改：调用 `proj_compact.apply_modifications()`，刷新 canvas

## 5.3 P2 — 设置面板

- API 配置：host / model / temperature / max_tokens
- 模式选择：auto / agent / chat
- 字段白名单：src / trans / fs / fc / fl / ff 等
- 上下文范围：auto / page / all
- 自定义 prompt 编辑器
- 配置持久化：`config/ai_chat_config.json`

## 5.4 P3 — 增强功能

- 思考过程面板：展示工具调用链，可折叠
- 欢迎卡片：对话为空时显示快捷指令 chips
- 附件上传：后端已支持但 UI 无入口
- 重新生成：助手气泡旁的重新生成按钮
- Token 统计准确性验证

## 5.5 P4 — 打磨

- i18n：所有用户可见文本已用 `self.tr()`，需更新 `translate/zh_CN.ts`
- 快捷键：`Ctrl+Shift+A` 切换面板
- 暗色主题适配
- 面板记忆：启动时恢复上次状态

---

# 六、关键文件路径

| 文件 | 用途 |
|------|------|
| `ui/ai_chat_panel.py` | 面板主文件（需修改） |
| `ui/mainwindow.py` | 布局和信号连线（已修改） |
| `ui/mainwindowbars.py` | LeftBar 按钮（已修改） |
| `utils/ai_controller.py` | 编排器（无需修改） |
| `utils/ai_tools.py` | 工具系统（无需修改） |
| `utils/proj_compact.py` | 数据层（无需修改） |
| `ui/ai_chat_worker.py` | 流式 worker（无需修改） |
| `ui/ai_chat_model.py` | 数据模型（无需修改） |
| `utils/ai_logger.py` | 日志系统（无需修改） |
| `config/stylesheet.css` | AI Chat 样式（~600 行） |
| `config/ai_chat_config.json` | API 配置持久化 |
| `config/ai_chat_history.json` | 对话历史（项目目录下） |

---

# 附录：已废弃的方案

以下方案已经历并被完全替换，**不要重新引入**：

| 方案 | 描述 | 文档 |
|------|------|------|
| **QML 方案** | `QQuickWidget` + 17 QML 文件 + `QmlBridge`（413 行） | `ai_tech_doc.md`、`ai_chat_qml_status.md` |
| **Creeper-Qt 方案** | 20 个 QPainter 自绘组件（`ui/creeper/` + `ui/ai_chat/`） | `ai_chat_creeper_refactor.md`、`ai_chat_ui_architecture.md` |
| **Phase 2 规划** | 初始 UI 规划文档 | `ai_handoff_phase2.md` |

**Creeper 组件库**（`ui/creeper/`）中的主题系统（`ColorScheme` 29 色、`ThemeManager`、`ThemePack`）和动画常量（350ms InOutExpo）的设计理念**可作为后续打磨时的参考**，但不应重新引入整套 QPainter 组件。当前 CSS 方案更简洁易维护。