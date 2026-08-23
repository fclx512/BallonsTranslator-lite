"""agent 工具面(设计方案 §5):只读探索 + 唯一写出口 submit_translations。

- 只读工具的执行复用 utils/ai_tools.py::execute_tool,经 READONLY_TOOLS
  白名单收窄——写类工具(set_*/search_replace/...)在本层不可达;
- search_glossary 为翻译 agent 新增,直接在本文件实现;
- submit_translations 不走 execute_tool,由 loop 拦截进校验器。
"""

import json
from typing import Any, Dict, List, Optional, Sequence

from utils.ai_tools import ToolError, execute_tool, to_openai_tools

SUBMIT_TOOL_NAME = "submit_translations"
GLOSSARY_TOOL_NAME = "search_glossary"

# 翻译 agent 可用的只读工具(execute_tool 白名单,写类工具不可达)
READONLY_TOOLS = ("list_pages", "read_pages", "search_blocks", "get_page_info")

# read_pages 输出仅保留原文/译文/竖排标记(紧凑键,见 utils/proj_compact.py::_COMPACT_DEF)
_TRANSLATION_FIELDS = {"src", "trans", "v"}

# 单次 read_pages 页数上限(超范围让模型自己缩小查询)
_MAX_READ_PAGES = 5

# 单条工具结果的字符上限(超出截断并告知,防上下文爆炸)
TOOL_RESULT_CHAR_CAP = 24000


AGENT_TOOL_DEFINITIONS = [
    {
        "name": "list_pages",
        "description": (
            "List an index of all pages in the project: page number, name "
            "and block counts. Use it to get the overall structure before "
            "reading specific pages."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_pages",
        "description": (
            "Read detailed block data of a page range: each block's source "
            "text (src) and existing translation (trans). Use it to check "
            "how a name or term was translated on other pages. At most 5 "
            "pages per call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "integer",
                    "description": "First page index (0-based)",
                },
                "end": {
                    "type": "integer",
                    "description": (
                        "Last page index (inclusive), -1 = last page"
                    ),
                },
            },
            "required": ["start"],
        },
    },
    {
        "name": "search_blocks",
        "description": (
            "Search all text blocks of the project for a substring, in "
            "source text, translations, or both. Returns matching block "
            "ids and snippets. Use it to locate where a name or phrase "
            "appears across the book."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to search for",
                },
                "field": {
                    "type": "string",
                    "enum": ["src", "trans", "both"],
                    "description": "Where to search",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_page_info",
        "description": (
            "Get page metadata (name, width, height) for a page range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["start"],
        },
    },
    {
        "name": GLOSSARY_TOOL_NAME,
        "description": (
            "Search the project glossary by substring (matches source "
            "term, translation or note). Use it to verify the required "
            "wording of a term before submitting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to search for",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": SUBMIT_TOOL_NAME,
        "description": (
            "Submit the final translations and finish the task. This is "
            "the ONLY way to deliver results. 'translations' maps every "
            "block id from the task to its translation string. All ids "
            "must be covered; ids reported as missing must be submitted "
            "in a follow-up call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "object",
                    "description": (
                        'Mapping of block id to translation, '
                        'e.g. {"1": "...", "2": "..."}'
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["translations"],
            "additionalProperties": False,
        },
    },
]


def submit_tool_def() -> Dict[str, Any]:
    for definition in AGENT_TOOL_DEFINITIONS:
        if definition["name"] == SUBMIT_TOOL_NAME:
            return definition
    raise KeyError(SUBMIT_TOOL_NAME)


def available_tool_defs(project, glossary_entries=None) -> List[Dict[str, Any]]:
    """按任务资源构建工具定义列表。

    无 project 时砍掉探索工具(只剩提交),无术语表时砍掉 search_glossary。
    """
    definitions: List[Dict[str, Any]] = []
    if project is not None:
        definitions.extend(
            d for d in AGENT_TOOL_DEFINITIONS if d["name"] in READONLY_TOOLS
        )
    if glossary_entries:
        definitions.extend(
            d for d in AGENT_TOOL_DEFINITIONS if d["name"] == GLOSSARY_TOOL_NAME
        )
    definitions.append(submit_tool_def())
    return definitions


def build_openai_tools(project, glossary_entries=None) -> List[Dict[str, Any]]:
    """available_tool_defs 的 OpenAI schema 形态(发给端点用)。"""
    return to_openai_tools(available_tool_defs(project, glossary_entries))


def _check_read_span(project, arguments: Dict[str, Any]) -> None:
    start = arguments.get("start", 0)
    end = arguments.get("end", -1)
    n_pages = len(project.pages)
    if not isinstance(start, int) or not isinstance(end, int):
        return  # 形状问题交给 execute_tool 报错
    if end == -1:
        end = n_pages - 1
    if 0 <= start < n_pages and start <= end < n_pages:
        span = end - start + 1
        if span > _MAX_READ_PAGES:
            raise ToolError(
                f"Range too large: {span} pages requested, at most "
                f"{_MAX_READ_PAGES} pages per call. Narrow the range."
            )


def _execute_search_glossary(
    entries: Sequence, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    query = str((arguments or {}).get("query", "")).strip()
    if not query:
        raise ToolError("Parameter 'query' must be a non-empty string.")
    folded = query.casefold()
    hits = [
        {"source": e.source, "translation": e.translation, "note": e.note}
        for e in entries
        if folded in e.source.casefold()
        or folded in e.translation.casefold()
        or (e.note and folded in e.note.casefold())
    ]
    return {
        "type": "glossary_results",
        "query": query,
        "n_results": len(hits),
        "entries": hits[:50],
        "truncated": len(hits) > 50,
    }


def execute_agent_tool(
    name: str,
    arguments: Optional[Dict[str, Any]],
    project=None,
    glossary_entries=None,
) -> Dict[str, Any]:
    """执行一个 agent 工具(submit_translations 除外,由 loop 处理)。"""
    arguments = dict(arguments or {})
    if name == GLOSSARY_TOOL_NAME:
        return _execute_search_glossary(glossary_entries or (), arguments)
    if name not in READONLY_TOOLS:
        raise ToolError(f"Unknown or forbidden tool: {name}")
    if project is None:
        raise ToolError("Project context is not available for this task.")
    if name == "read_pages":
        _check_read_span(project, arguments)
    return execute_tool(
        project, name, arguments, fields_whitelist=_TRANSLATION_FIELDS
    )


def cap_tool_result(
    result: Dict[str, Any], char_cap: int = TOOL_RESULT_CHAR_CAP
) -> Dict[str, Any]:
    """单条工具结果超字符上限时截断,并告知模型缩小查询(循环护栏)。"""
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= char_cap:
        return result
    return {
        "type": result.get("type", "result"),
        "truncated": True,
        "error": (
            "Tool result was too large and has been truncated. Narrow your "
            "query range (fewer pages, more specific search term)."
        ),
        "preview": serialized[: char_cap // 2],
    }
