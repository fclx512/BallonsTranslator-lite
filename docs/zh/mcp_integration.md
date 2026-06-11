# MCP 服务器集成指南

BallonsTranslator-lite 现在支持 **模型上下文协议（MCP）**，允许外部 AI 代理（如 Claude Code）
通过工具调用直接读取和修改项目数据。

这取代了旧的应用程序内 AI 聊天面板，该面板已被移除。

## 架构

MCP 服务器是一个独立的 Python 进程（无需 PyQt），通过 **stdio 传输**（stdin/stdout 上的 JSON-RPC 消息）
与 MCP 客户端通信。

```
MCP 客户端（例如 Claude Code）
    │
    │  stdio 传输
    ▼
BallonsTranslator MCP 服务器 (mcp_server/)
    │
    ├── project_manager.py   — 项目加载/保存/缓存
    ├── tools.py             — MCP 工具注册
    │
    ├── utils/proj_compact.py (复用)  — build_index, build_detail, apply_modifications
    ├── utils/ai_tools.py (复用)      — execute_tool 分发器, TOOL_DEFINITIONS
    ├── utils/proj_imgtrans.py (复用) — ProjImgTrans 加载/保存
    └── utils/config.py (最小依赖)    — pcfg 初始化
```

## 设置

### 1. 安装 MCP 依赖

```bash
pip install "mcp>=1.0.0"
# 或从可选依赖安装：
pip install -e ".[mcp]"
```

### 2. 配置 Claude Code

将以下内容添加到您的 `~/.claude/settings.json` 或项目 `.claude/settings.json`：

```json
{
  "mcpServers": {
    "ballonstranslator": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {
        "PYTHONPATH": "D:\\ruanjian\\BallonsTranslator-lite"
      }
    }
  }
}
```

请将 `PYTHONPATH` 调整为指向您的项目根目录。

## 可用工具

### 项目管理

| 工具 | 描述 | 参数 |
|------|------|------|
| `open_project` | 从磁盘加载项目 | `directory`（字符串，必填） |
| `save_project` | 将当前状态写入磁盘 | 无 |
| `close_project` | 卸载当前项目 | 无 |
| `get_state` | 获取服务器/项目状态 | 无 |

### 读取工具

| 工具 | 描述 | 参数 |
|------|------|------|
| `list_pages` | 轻量级页面索引（名称、尺寸、块数） | 无 |
| `read_pages` | 页面的完整文本块数据 | `start`（整数，必填）, `end`（整数，可选） |
| `search_blocks` | 搜索所有块中的文本 | `query`（字符串，必填）, `field`（字符串，可选） |
| `get_config` | 全局配置（字体、语言设置） | 无 |
| `get_page_info` | 页面尺寸和元数据 | `start`（整数，必填）, `end`（整数，可选） |

### 写入工具

| 工具 | 描述 | 参数 |
|------|------|------|
| `set_font` | 批量设置字体属性 | `ids`, `ff`, `fs`, `fw`, `b`, `i` |
| `set_color` | 批量设置颜色属性 | `ids`, `fg`, `bg`, `sw` |
| `set_layout` | 批量设置排版参数 | `ids`, `a`, `ls`, `lsp`, `v` |
| `search_replace` | 搜索并替换文本 | `query`, `replacement`, `field` |

### 块 ID 格式

块 ID 使用 `{页面索引}:{块索引}` 格式，例如 `"0:3"` 表示第 0 页的第 4 个块。
使用 `*` 作为整页的通配符：`"0:*"` 表示第 0 页的所有块。
多个 ID 可以用逗号分隔：`"0:0,0:1,1:2"`。

### 颜色格式

颜色以字符串形式的 RGB 数组指定，例如红色为 `"[255,0,0]"`。

## 工作流程示例

```
用户："打开我的漫画项目并列出所有页面"
代理：调用 open_project(directory="/path/to/project")
      → 项目已加载，返回页面索引
      调用 list_pages()
      → 返回："第 1 页 (1920×1080, 12 块), 第 2 页 (1920×1080, 8 块)..."

用户："读取第 1 页并将文本块翻译成中文"
代理：调用 read_pages(start=0, end=0)
      → 读取第 1 页的所有块
      [使用 LLM 翻译文本]
      调用 search_replace(query="hello", replacement="你好", field="trans")
      → 替换每个块的翻译字段
      调用 save_project()
      → 更改持久化到磁盘
```

## Token 效率

服务器保留了原始实现中的两级访问模式：

- **`list_pages`** 仅返回元数据（页面名称、尺寸、块数）— 轻量级
- **`read_pages`** 返回完整的块数据（源文本、翻译、字体样式）— 详细
- **`build_paginated_detail`** 将大型页面范围分割为最多 20 页的块

这意味着您可以先检查项目索引，然后只读取您需要的页面。

## 注意事项

- 服务器直接操作项目文件。在进行 MCP 操作前后，请确保保存您的项目。
- 每次操作都会读取/写入项目 JSON 文件，因此更改是持久的。
- 服务器不需要正在运行的 BallonsTranslator GUI 实例。
