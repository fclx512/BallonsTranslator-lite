"""
AI Tools — project exploration tools for the AI assistant.

The AI receives a system prompt listing available tools, then makes
tool-call requests in a structured JSON format. The backend executes
them and returns results, enabling the AI to dynamically determine
which pages it needs to read based on the user's natural-language query.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger('ai_chat')

from .proj_compact import (
    build_index, build_detail, build_paginated_detail,
    compact_block, FIELD_PROMPT_SNIPPETS,
    _COMPACT_DEF, _FONT_CLASS_DEFAULTS, _get_font_default, _is_default,
)
from .config import pcfg

# ── Tool definitions ──────────────────────────────────────────────────

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "describe_tool",
        "description": (
            "获取指定工具的详细参数说明。当需要了解某个工具的具体用法时调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "工具名称",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_pages",
        "description": (
            "获取项目所有页面的概览索引，包括每页的名称、尺寸、文本块数量和字符统计。"
            "用于快速了解项目结构和确定需要读取哪些页面。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_pages",
        "description": (
            "读取指定页面的详细数据，包括每个文本块的原文(src)、译文(trans)"
            "及字体样式信息。用于获取需要修改的具体内容。"
            "每次调用建议不超过 20 页以控制数据量。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "integer",
                    "description": "起始页索引（从 0 开始）",
                },
                "end": {
                    "type": "integer",
                    "description": "结束页索引（包含），设为 -1 表示到最后一页",
                },
            },
            "required": ["start"],
        },
    },
    {
        "name": "search_blocks",
        "description": (
            "在所有页面中搜索包含指定文本的文本块。"
            "返回匹配的块 ID、所在页面和文本摘要。"
            "用于定位特定对话、拟声词或关键词所在的文本块。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "field": {
                    "type": "string",
                    "enum": ["src", "trans", "both"],
                    "description": "搜索范围：src=原文, trans=译文, both=两者（默认）",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_config",
        "description": (
            "读取项目的全局配置，包括默认字体、源语言、目标语言等。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_page_info",
        "description": (
            "获取指定页面的图片尺寸和元信息（不包含文本内容）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "integer",
                    "description": "起始页索引（从 0 开始）",
                },
                "end": {
                    "type": "integer",
                    "description": "结束页索引（包含），默认与 start 相同",
                },
            },
            "required": ["start"],
        },
    },
    {
        "name": "set_font",
        "description": (
            "批量设置文本块的字体样式，包括字体名称、字号、字重、粗体、斜体。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "string",
                    "description": "块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页",
                },
                "ff": {"type": "string", "description": "字体名称"},
                "fs": {"type": "number", "description": "字号（像素）"},
                "fw": {"type": "integer", "description": "字重（100-900）"},
                "b": {"type": "boolean", "description": "粗体"},
                "i": {"type": "boolean", "description": "斜体"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "set_color",
        "description": (
            "批量设置文本块的颜色属性，包括文字颜色、轮廓颜色和轮廓宽度。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "string",
                    "description": "块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页",
                },
                "fg": {"type": "array", "description": "文字颜色 [R, G, B]"},
                "bg": {"type": "array", "description": "轮廓颜色 [R, G, B]"},
                "sw": {"type": "number", "description": "轮廓宽度（0=无轮廓）"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "set_layout",
        "description": (
            "批量设置文本块的排版参数，包括对齐方式、行距、字距、竖排。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "string",
                    "description": "块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页",
                },
                "a": {"type": "integer", "description": "对齐：0=左/1=中/2=右"},
                "ls": {"type": "number", "description": "行距（1.0=单倍，默认1.2）"},
                "lsp": {"type": "number", "description": "字距"},
                "v": {"type": "boolean", "description": "竖排"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "search_replace",
        "description": (
            "在指定字段中搜索并替换文本。可用于批量修正术语。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索文本"},
                "replacement": {"type": "string", "description": "替换文本"},
                "field": {
                    "type": "string",
                    "enum": ["src", "trans"],
                    "description": "目标字段",
                },
            },
            "required": ["query", "replacement", "field"],
        },
    },
    {
        "name": "translate_text",
        "description": (
            "翻译独立的文本片段（不修改项目数据）。用于测试翻译或获取建议。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要翻译的文本列表",
                },
                "source_lang": {
                    "type": "string",
                    "description": "源语言代码（如 ja, en, zh）",
                },
                "target_lang": {
                    "type": "string",
                    "description": "目标语言代码（如 zh, en）",
                },
            },
            "required": ["texts"],
        },
    },
]

# ── Tool execution ────────────────────────────────────────────────────

class ToolError(Exception):
    """Tool execution error (reported back to AI, not fatal)."""


def _resolve_range(proj, start: int, end: int) -> List[int]:
    """Resolve start/end to a concrete list of page indices."""
    n = len(proj.pages)
    if start < 0 or start >= n:
        raise ToolError(f"start={start} 超出范围（项目共 {n} 页，索引 0-{n - 1}）")
    if end == -1:
        end = n - 1
    if end < start or end >= n:
        raise ToolError(f"end={end} 无效（start={start}, 项目共 {n} 页）")
    return list(range(start, end + 1))


def _execute_list_pages(proj) -> Dict[str, Any]:
    """Execute list_pages tool."""
    return build_index(proj, include_global_font=True)


def _execute_read_pages(
    proj,
    start: int,
    end: int = -1,
    fields_whitelist: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Execute read_pages tool."""
    indices = _resolve_range(proj, start, end)
    # Paginate if too many pages
    if len(indices) > 20:
        chunks = build_paginated_detail(
            proj, indices, max_pages_per_chunk=20,
            fields_whitelist=fields_whitelist,
        )
        result: Dict[str, Any] = {
            "type": "paginated_detail",
            "n_chunks": len(chunks),
            "total_pages": len(indices),
            "chunks": chunks,
        }
        return result
    return build_detail(proj, indices, fields_whitelist=fields_whitelist)


def _execute_search_blocks(proj, query: str, field: str = "both") -> Dict[str, Any]:
    """Execute search_blocks tool."""
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []

    for pidx, (pname, blklist) in enumerate(proj.pages.items()):
        for bidx, blk in enumerate(blklist):
            match = False
            matched_in = ""
            snippet = ""

            src = blk.get_text()
            trans = blk.translation or ""

            if field in ("src", "both") and query_lower in src.lower():
                match = True
                matched_in = "src"
                # Snippet around match
                pos = src.lower().find(query_lower)
                start = max(0, pos - 15)
                end_val = min(len(src), pos + len(query) + 15)
                snippet = ("..." if start > 0 else "") + src[start:end_val] + ("..." if end_val < len(src) else "")
            elif field in ("trans", "both") and query_lower in trans.lower():
                match = True
                matched_in = "trans"
                pos = trans.lower().find(query_lower)
                start = max(0, pos - 15)
                end_val = min(len(trans), pos + len(query) + 15)
                snippet = ("..." if start > 0 else "") + trans[start:end_val] + ("..." if end_val < len(trans) else "")

            if match:
                results.append({
                    "id": f"{pidx}:{bidx}",
                    "page": pidx,
                    "page_name": pname,
                    "field": matched_in,
                    "snippet": snippet,
                })

    return {
        "type": "search_results",
        "query": query,
        "field": field,
        "n_results": len(results),
        "results": results[:50],  # Cap at 50
    }


def _execute_get_config(proj) -> Dict[str, Any]:
    """Execute get_config tool."""
    gf = pcfg.global_fontformat
    return {
        "type": "config",
        "global_font": {
            "ff": gf.font_family,
            "fs": gf.font_size,
            "fw": gf.font_weight,
            "fg": gf.frgb if isinstance(gf.frgb, list) else list(gf.frgb),
            "bg": gf.srgb if isinstance(gf.srgb, list) else list(gf.srgb),
            "b": gf.bold,
        },
        "source_lang": getattr(pcfg, 'source_lang', ''),
        "target_lang": getattr(pcfg, 'target_lang', ''),
    }


def _execute_get_page_info(proj, start: int, end: int = -1) -> Dict[str, Any]:
    """Execute get_page_info tool."""
    indices = _resolve_range(proj, start, end)
    pages = []
    for pidx in indices:
        pname = proj.idx2pagename(pidx)
        info = proj._image_info.get(pname, {})
        pages.append({
            "pidx": pidx,
            "name": pname,
            "w": info.get("width", 0),
            "h": info.get("height", 0),
        })
    return {"type": "page_info", "pages": pages}


def _resolve_ids(proj, ids_str: str) -> List[str]:
    """Resolve 'ids' parameter to a list of block IDs."""
    if ids_str.endswith(":*"):
        pidx = int(ids_str[:-2])
        pname = proj.idx2pagename(pidx)
        return [f"{pidx}:{bidx}" for bidx in range(len(proj.pages[pname]))]
    return [pid.strip() for pid in ids_str.split(",")]


def _generate_modification_tool_result(proj, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap a list of property changes into a modification-type result."""
    return {"type": "modifications", "changes": changes}


def _execute_set_font(
    proj, ids: str, ff: str = None, fs: float = None,
    fw: int = None, b: bool = None, i: bool = None,
) -> Dict[str, Any]:
    """Execute set_font tool — returns modifications dict."""
    blk_ids = _resolve_ids(proj, ids)
    changes = []
    for bid in blk_ids:
        change: Dict[str, Any] = {"id": bid}
        if ff is not None:
            change["ff"] = ff
        if fs is not None:
            change["fs"] = fs
        if fw is not None:
            change["fw"] = fw
        if b is not None:
            change["b"] = b
        if i is not None:
            change["i"] = i
        if len(change) > 1:
            changes.append(change)
    return _generate_modification_tool_result(proj, changes)


def _execute_set_color(
    proj, ids: str, fg: List[int] = None,
    bg: List[int] = None, sw: float = None,
) -> Dict[str, Any]:
    """Execute set_color tool."""
    blk_ids = _resolve_ids(proj, ids)
    changes = []
    for bid in blk_ids:
        change: Dict[str, Any] = {"id": bid}
        if fg is not None:
            change["fg"] = fg
        if bg is not None:
            change["bg"] = bg
        if sw is not None:
            change["sw"] = sw
        if len(change) > 1:
            changes.append(change)
    return _generate_modification_tool_result(proj, changes)


def _execute_set_layout(
    proj, ids: str, a: int = None, ls: float = None,
    lsp: float = None, v: bool = None,
) -> Dict[str, Any]:
    """Execute set_layout tool."""
    blk_ids = _resolve_ids(proj, ids)
    changes = []
    for bid in blk_ids:
        change: Dict[str, Any] = {"id": bid}
        if a is not None:
            change["a"] = a
        if ls is not None:
            change["ls"] = ls
        if lsp is not None:
            change["lsp"] = lsp
        if v is not None:
            change["v"] = v
        if len(change) > 1:
            changes.append(change)
    return _generate_modification_tool_result(proj, changes)


def _execute_search_replace(
    proj, query: str, replacement: str, field: str,
) -> Dict[str, Any]:
    """Execute search_replace tool."""
    query_lower = query.lower()
    changes = []
    for pidx, (pname, blklist) in enumerate(proj.pages.items()):
        for bidx, blk in enumerate(blklist):
            if field == "src":
                cur = blk.get_text()
            elif field == "trans":
                cur = blk.translation or ""
            else:
                continue
            if query_lower in cur.lower():
                new_val = cur.replace(query, replacement)
                if new_val != cur:
                    changes.append({"id": f"{pidx}:{bidx}", field: new_val})
    return _generate_modification_tool_result(proj, changes)


def _execute_translate_text(
    texts: List[str], source_lang: str = "", target_lang: str = "",
) -> Dict[str, Any]:
    """Execute translate_text tool — returns the input for the LLM to translate.

    Since we're already in an LLM context, we return a prompt-like structure
    that the AI can use to self-translate.
    """
    return {
        "type": "translate_request",
        "texts": texts,
        "source_lang": source_lang or "auto",
        "target_lang": target_lang or "zh",
        "hint": (
            "请在最终回答的 changes 数组中为每个文本输出翻译。"
            "格式: {\"changes\": [{\"id\": \"translate:0\", \"trans\": \"译文\"}, ...]}"
        ),
    }


def _execute_describe_tool(tool_name: str) -> Dict[str, Any]:
    """Execute describe_tool meta-tool."""
    for t in TOOL_DEFINITIONS:
        if t["name"] == tool_name:
            return {
                "type": "tool_description",
                "tool": t,
            }
    available = [t["name"] for t in TOOL_DEFINITIONS]
    return {
        "type": "tool_description",
        "error": f"未知工具: {tool_name!r}",
        "available_tools": available,
    }


def execute_tool(
    proj,
    tool_name: str,
    arguments: Dict[str, Any],
    fields_whitelist: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Dispatch a tool call. Returns the tool result dict."""
    logger.debug("execute_tool: %s args=%s", tool_name, arguments)
    if tool_name == "describe_tool":
        return _execute_describe_tool(arguments.get("name", ""))
    elif tool_name == "list_pages":
        return _execute_list_pages(proj)
    elif tool_name == "read_pages":
        return _execute_read_pages(
            proj,
            start=arguments.get("start", 0),
            end=arguments.get("end", -1),
            fields_whitelist=fields_whitelist,
        )
    elif tool_name == "search_blocks":
        return _execute_search_blocks(
            proj,
            query=arguments.get("query", ""),
            field=arguments.get("field", "both"),
        )
    elif tool_name == "get_config":
        return _execute_get_config(proj)
    elif tool_name == "get_page_info":
        return _execute_get_page_info(
            proj,
            start=arguments.get("start", 0),
            end=arguments.get("end", arguments.get("start", 0)),
        )
    elif tool_name == "set_font":
        return _execute_set_font(
            proj,
            ids=arguments.get("ids", ""),
            ff=arguments.get("ff"),
            fs=arguments.get("fs"),
            fw=arguments.get("fw"),
            b=arguments.get("b"),
            i=arguments.get("i"),
        )
    elif tool_name == "set_color":
        return _execute_set_color(
            proj,
            ids=arguments.get("ids", ""),
            fg=arguments.get("fg"),
            bg=arguments.get("bg"),
            sw=arguments.get("sw"),
        )
    elif tool_name == "set_layout":
        return _execute_set_layout(
            proj,
            ids=arguments.get("ids", ""),
            a=arguments.get("a"),
            ls=arguments.get("ls"),
            lsp=arguments.get("lsp"),
            v=arguments.get("v"),
        )
    elif tool_name == "search_replace":
        return _execute_search_replace(
            proj,
            query=arguments.get("query", ""),
            replacement=arguments.get("replacement", ""),
            field=arguments.get("field", "trans"),
        )
    elif tool_name == "translate_text":
        return _execute_translate_text(
            texts=arguments.get("texts", []),
            source_lang=arguments.get("source_lang", ""),
            target_lang=arguments.get("target_lang", ""),
        )
    else:
        available = [t["name"] for t in TOOL_DEFINITIONS]
        raise ToolError(
            f"未知工具: {tool_name!r}。可用工具: {', '.join(available)}")


def parse_tool_calls(text: str) -> Optional[List[Dict[str, Any]]]:
    """Try to extract tool-call JSON from AI response text.

    Returns a list of tool-call dicts, or None if no tool calls found.
    Each dict has 'name' and 'arguments' keys.
    """
    # Find JSON blocks in the response
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(text[start:i + 1])

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tool_calls" in obj:
            calls = obj["tool_calls"]
            if isinstance(calls, list) and all(
                isinstance(c, dict) and "name" in c and "arguments" in c
                for c in calls
            ):
                logger.debug("parse_tool_calls: found %d calls in %d candidates",
                             len(calls), len(candidates))
                return calls

    logger.debug("parse_tool_calls: no tool calls in %d candidates", len(candidates))
    return None


# ── System prompt ─────────────────────────────────────────────────────

def build_agent_system_prompt(
    fields_whitelist: Optional[Set[str]] = None,
    translation_mode: bool = False,
) -> str:
    """Build the system prompt with compact tool index."""

    # Field descriptions
    if fields_whitelist is None:
        snippets = list(FIELD_PROMPT_SNIPPETS.values())
    else:
        snippets = [
            v for k, v in FIELD_PROMPT_SNIPPETS.items()
            if k in fields_whitelist
        ]
    field_desc = "\n".join(snippets) if snippets else "（仅可读取原文和译文）"

    # Compact tool index (one line per tool)
    tool_index_lines = []
    for t in TOOL_DEFINITIONS:
        name = t["name"]
        desc = t["description"]
        tool_index_lines.append(f"- {name}: {desc}")
    tool_index = "\n".join(tool_index_lines)

    prompt = f"""你是一个专业的漫画翻译编辑助手。你的核心能力是通过工具读取和修改项目中的文本块数据。

## 核心原则

**永远不要说你做不到。** 你有完整的工具集可以获取和实践项目数据。
如果你不确定页面的内容、规模、或分布，直接用工具获取 —— 你有能力做到用户要求的任何数据操作。

**禁止行为：**
- 不要回复"我无法访问项目数据"、"我没有工具"、"我看不到"等否定表述
- 不要空口猜测页面内容
- 不要假设用户指的范围而不先确认

**正确做法：** 无论用户的要求多么模糊（"看看第3页"、"翻译前几页"），直接调用对应工具。

## 数据架构（双层访问）

为提高效率，项目数据分两层：

1. **索引层**：系统已在下一条消息中提供项目索引（list_pages 的结果），包含每页的块数量和字符统计
2. **详情层**：当需要查看具体文本内容时，调用 `read_pages` 获取指定页的逐块数据

**工作流**：
- 用户指定了页码/范围 → 直接 `read_pages`
- 用户没指定范围（"帮我翻译"、"看看项目"）→ 先看索引了解规模，再 `read_pages` 需要处理的页
- 用户要查找内容（"找到'你好'"）→ `search_blocks` + `read_pages`

## 模糊请求处理示例

| 用户输入 | 你的反应 |
|---------|---------|
| "看看第5页有什么" | 立即 `read_pages(start=4, end=4)` |
| "翻译前3页" | 立即 `read_pages(start=0, end=2)` |
| "帮我看看8到15页" | 立即 `read_pages(start=7, end=14)` |
| "翻译全部" | 先看索引判断页数，分批 `read_pages` |
| "搜索'你好'在哪" | `search_blocks(query="你好")`，然后 `read_pages` 对应页 |
| "把当前页字体调大" | `read_pages` 当前页 → `set_font` |
| "给所有文字加描边" | `set_layout(ids="*")` 或 `read_pages` 后逐个处理 |

**页码索引从 0 开始**：用户说的"第1页"对应 start=0，"第5页"对应 start=4。
"前N页" = start=0, end=N-1。"X到Y页" = start=X-1, end=Y-1。

## 可用工具（索引）

{tool_index}

如需某个工具的详细参数说明，调用 describe_tool。

## 工具调用格式

输出严格的 JSON（不要附带其他文字）：

{{"tool_calls": [{{"name": "工具名", "arguments": {{...}}}}]}}

你可以一次调用多个工具。系统执行后会将结果返回给你。

## 最终回答格式

准备好修改建议后，输出 JSON changes 数组（可附带 Markdown 说明）：

示例：
{{"changes": [{{"id": "0:0", "trans": "你好，世界！"}}, {{"id": "0:3", "fs": 28}}]}}

只输出需要修改的字段。用 "页:*" 表示整页所有块。
**`trans` 必须填译文，严禁填原文（`src`）。**

## 批量修改

使用 set_font、set_color、set_layout、search_replace 自动生成修改提案供审批。

## 可修改字段

{field_desc}

## Markdown

你可以使用 **粗体**、*斜体*、`代码`、无序列表等格式。

## 翻译规则
- 输出目标语言的译文到 `trans` 字段
- 保留角色语气和情感
- 术语全篇统一
- 拟声词/效果音本地化"""

    if translation_mode:
        prompt += """

## 翻译质量标准
- 目标语言表达自然流畅，符合漫画阅读习惯
- 拟声词本地化（如 ドキドキ→怦怦、ザー→哗—）
- 术语一致（同一概念全篇统一译法）
- 保留原文的语气和情感强度
- **无论何种情况，trans 字段必须填入译文，严禁输出原文**"""

    return prompt


# Backward-compatible alias
build_tool_system_prompt = build_agent_system_prompt


# ── Chat system prompt ───────────────────────────────────────────────────

def build_chat_system_prompt() -> str:
    """System prompt for general Q&A chat mode (no tools)."""
    return """你是一个专业的漫画/图片翻译顾问。你可以帮助用户解决以下问题：

- 漫画翻译策略和最佳实践
- 文化本地化建议（日↔中、日↔英等）
- 特定语言和术语问题
- 漫画排版和字体选择建议
- 漫画翻译工作流程指导
- 回答关于 BallonsTranslator 应用的一般问题

## 回答准则
- 简洁但全面
- 在有用时使用示例
- 可使用 Markdown 格式（粗体、斜体、列表、代码）
- 如果不确定，诚实表示
- 如果用户询问项目数据操作，建议切换到 Agent 模式

当前模式下你没有项目工具访问权限。你只能提供建议和知识性回答。"""


# ── Mode detection ───────────────────────────────────────────────────────

_AGENT_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r'翻译', r'翻訳', r'translat',
        r'修改', r'変更', r'modify|change|edit|update',
        r'字体', r'font',
        r'颜色|色彩|color|colour',
        r'样式|style',
        r'对齐|alignment|layout',
        r'页面|页码|第.*页|page',
        r'文本块|文字块|block',
        r'\d+[-–—至到]\d+',
        r'全部|所有|整个',
        r'搜索|查找|search|find',
        r'调整|设置|apply|set',
        r'拟声|象声|onomatopoeia',
        r'原文|译文|source|target',
        r'看看|查看|瞧瞧',
        r'列出|显示|展示|\bshow\b|\blist\b',
        r'打开.*页|打开.*项目',
        r'\bread\b',
        r'帮我|给我',
    ]
]


def detect_mode(user_text: str) -> str:
    """Return 'agent' if user text looks like a project operation, else 'chat'."""
    for pat in _AGENT_PATTERNS:
        if pat.search(user_text):
            return 'agent'
    return 'chat'


# ── Changes parsing ──────────────────────────────────────────────────────

def parse_changes(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract {"changes": [...]} from AI response text.

    Returns the list of change dicts, or None if no changes block found.
    Each change dict should have at least 'id' and one modifiable field.
    """
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(text[start:i + 1])

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and 'changes' in obj:
            changes = obj['changes']
            if isinstance(changes, list) and all(
                isinstance(c, dict) and 'id' in c for c in changes
            ):
                logger.debug("parse_changes: found %d changes in %d candidates",
                             len(changes), len(candidates))
                return changes

    logger.debug("parse_changes: no changes in %d candidates", len(candidates))
    return None
