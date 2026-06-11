# MCP Server Integration Guide

BallonsTranslator-lite now supports the **Model Context Protocol (MCP)**, allowing external AI agents
(such as Claude Code) to read and modify project data directly through tool calls.

This replaces the old in-app AI chat panel, which has been removed.

## Architecture

The MCP server is a standalone Python process (no PyQt required) that communicates with MCP clients
over **stdio transport** (JSON-RPC messages over stdin/stdout).

```
MCP Client (e.g. Claude Code)
    │
    │  stdio transport
    ▼
BallonsTranslator MCP Server (mcp_server/)
    │
    ├── project_manager.py   — project load/save/cache
    ├── tools.py             — MCP tool registration
    │
    ├── utils/proj_compact.py (reused)  — build_index, build_detail, apply_modifications
    ├── utils/ai_tools.py (reused)      — execute_tool dispatcher, TOOL_DEFINITIONS
    ├── utils/proj_imgtrans.py (reused) — ProjImgTrans load/save
    └── utils/config.py (minimal)       — pcfg initialization
```

## Setup

### 1. Install the MCP dependency

```bash
pip install "mcp>=1.0.0"
# or from optional extras:
pip install -e ".[mcp]"
```

### 2. Configure Claude Code

Add the following to your `~/.claude/settings.json` or project `.claude/settings.json`:

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

Adjust the `PYTHONPATH` to point to your project root directory.

## Available Tools

### Project Management

| Tool | Description | Parameters |
|------|-------------|-----------|
| `open_project` | Load a project from disk | `directory` (string, required) |
| `save_project` | Write current state to disk | None |
| `close_project` | Unload the current project | None |
| `get_state` | Get server/project state | None |

### Read Tools

| Tool | Description | Parameters |
|------|-------------|-----------|
| `list_pages` | Lightweight page index (names, sizes, block counts) | None |
| `read_pages` | Full block data for pages | `start` (int, required), `end` (int, optional) |
| `search_blocks` | Search text across all blocks | `query` (string, required), `field` (string, optional) |
| `get_config` | Global config (fonts, language settings) | None |
| `get_page_info` | Page dimensions and metadata | `start` (int, required), `end` (int, optional) |

### Write Tools

| Tool | Description | Parameters |
|------|-------------|-----------|
| `set_font` | Batch-set font properties | `ids`, `ff`, `fs`, `fw`, `b`, `i` |
| `set_color` | Batch-set color properties | `ids`, `fg`, `bg`, `sw` |
| `set_layout` | Batch-set layout parameters | `ids`, `a`, `ls`, `lsp`, `v` |
| `search_replace` | Search and replace text | `query`, `replacement`, `field` |

### Block ID format

Block IDs use the format `{page_index}:{block_index}`, e.g. `"0:3"` for the 4th block on page 0.
Use `*` as a wildcard for all blocks on a page: `"0:*"` targets all blocks on page 0.
Multiple IDs can be comma-separated: `"0:0,0:1,1:2"`.

### Color format

Colors are specified as RGB arrays as strings, e.g. `"[255,0,0]"` for red.

## Example Workflow

```
User: "Open my manga project and list all pages"
Agent: calls open_project(directory="/path/to/project")
       → project loaded, returns page index
       calls list_pages()
       → returns: "Page 1 (1920×1080, 12 blocks), Page 2 (1920×1080, 8 blocks)..."

User: "Read page 1 and translate the text blocks to Chinese"
Agent: calls read_pages(start=0, end=0)
       → reads all blocks on page 1
       [translates text using LLM]
       calls search_replace(query="hello", replacement="你好", field="trans")
       → replaces in each block's translation field
       calls save_project()
       → changes persisted to disk
```

## Token Efficiency

The server preserves the two-tier access pattern from the original implementation:

- **`list_pages`** returns only metadata (page names, dimensions, block counts) — lightweight
- **`read_pages`** returns full block data (source text, translations, font styles) — detailed
- **`build_paginated_detail`** splits large page ranges into chunks of 20 pages max

This means you can inspect the project index first, then read only the pages you need.

## Notes

- The server operates directly on project files. Make sure to save your project before and after MCP operations.
- Each operation reads/writes the project JSON file, so changes are durable.
- The server does not require a running BallonsTranslator GUI instance.
