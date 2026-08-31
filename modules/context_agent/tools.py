"""工作台工具面:只读探索 + 两个写出口(均只落草稿,零写盘能力)。

只读工具直接复用 translators/agent/tools 的白名单执行器(list_pages/
read_pages/search_blocks/get_page_info),写类工具在本层不可达;
read_page_summaries 让模型优先读已有摘要而非重读原文(上游吸收点);
submit_glossary_patch / submit_story_patch 是仅有的两个写出口,
由本模块直接写进权威草稿并回执 applied/conflicts(单条失败不作废整轮)。
"""

from typing import Any, Dict, Optional

from modules.context_agent.draft import DraftValueError
from modules.translators.agent.tools import (
    AGENT_TOOL_DEFINITIONS,
    cap_tool_result,
    execute_agent_tool,
)
from utils.ai_tools import to_openai_tools

GLOSSARY_PATCH_TOOL_NAME = "submit_glossary_patch"
STORY_PATCH_TOOL_NAME = "submit_story_patch"
PAGE_SUMMARIES_TOOL_NAME = "read_page_summaries"

# 只读探索子集(会话里始终可用)
CONTEXT_READ_TOOLS = ("list_pages", "read_pages", "search_blocks", "get_page_info")

_PATCH_DEFS = [
    {
        "name": GLOSSARY_PATCH_TOOL_NAME,
        "description": (
            "Submit glossary changes to the draft table. This does NOT "
            "write any file; the user reviews the draft and applies it "
            "manually. Entries already curated by the user are protected: "
            "conflicting proposals are returned as conflict rows instead "
            "of being applied. Actions: 'add' inserts or updates your own "
            "earlier entry, 'update' changes an entry (rejected on "
            "user-owned rows), 'remove' deletes an entry (only your own)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "description": "List of change operations.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["add", "update", "remove"],
                            },
                            "src": {
                                "type": "string",
                                "description": "Source term (identity key).",
                            },
                            "dst": {
                                "type": "string",
                                "description": "Translation (required for add/update).",
                            },
                            "info": {
                                "type": "string",
                                "description": "Optional note (category, usage hint).",
                            },
                        },
                        "required": ["src"],
                    },
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
    {
        "name": STORY_PATCH_TOOL_NAME,
        "description": (
            "Submit story-context changes to the draft panel. This does "
            "NOT write any file; the user reviews and applies manually. "
            "Two layers: per-page summaries and one global synopsis "
            "(a full replacement each time, keep it short). Summaries "
            "already present are user-owned unless you created them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_summaries": {
                    "type": "array",
                    "description": "Per-page summary operations.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["set", "remove"]},
                            "page": {
                                "type": "string",
                                "description": (
                                    "Page index as a string (e.g. \"3\")."
                                ),
                            },
                            "summary": {
                                "type": "string",
                                "description": (
                                    "2-4 sentence plot summary of the page "
                                    "(required for set)."
                                ),
                            },
                        },
                        "required": ["page"],
                    },
                },
                "synopsis": {
                    "type": "string",
                    "description": (
                        "Full replacement of the global synopsis: the "
                        "overall story so far in a compact paragraph."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": PAGE_SUMMARIES_TOOL_NAME,
        "description": (
            "Read the story summaries already present in the draft "
            "(per page and the global synopsis). Prefer this over "
            "re-reading raw pages when you only need the plot so far."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def build_context_tools(with_story: bool = True) -> list:
    """构建 OpenAI schema 工具列表。with_story=False 时砍掉剧情工具。"""
    names = set(CONTEXT_READ_TOOLS) | {
        GLOSSARY_PATCH_TOOL_NAME,
        PAGE_SUMMARIES_TOOL_NAME,
    }
    if with_story:
        names.add(STORY_PATCH_TOOL_NAME)
    defs = [d for d in AGENT_TOOL_DEFINITIONS if d["name"] in CONTEXT_READ_TOOLS]
    defs += [d for d in _PATCH_DEFS if d["name"] in names]
    return to_openai_tools(defs)


def _resolve_page_name(proj, value) -> str:
    """页操作里 page 允许传页索引(字符串/整数),统一解析为页名。"""
    if isinstance(value, bool):
        raise DraftValueError('Field "page" is invalid.')
    if isinstance(value, int):
        idx = value
    elif isinstance(value, str) and value.strip().isdigit():
        idx = int(value.strip())
    else:
        # 已是页名则原样接受
        name = str(value or "").strip()
        if not name:
            raise DraftValueError('Field "page" must not be empty.')
        return name
    n_pages = len(proj.pages)
    if not 0 <= idx < n_pages:
        raise DraftValueError(
            f"Page index {idx} out of range (project has {n_pages} pages)."
        )
    return proj.idx2pagename(idx)


def _execute_read_page_summaries(glossary_draft, story_draft) -> Dict[str, Any]:
    pages, synopsis = story_draft.snapshot()
    return {
        "type": "page_summaries",
        "synopsis": synopsis,
        "synopsis_origin": story_draft.synopsis_origin,
        "pages": [
            {"page": p.page_name, "summary": p.summary, "origin": p.origin}
            for p in pages
        ],
        "hint": (
            "origin 'existing'/'user' rows are user-owned; conflicting "
            "changes come back as conflict rows instead of being applied."
        ),
    }


def execute_context_tool(
    name: str,
    arguments: Optional[Dict[str, Any]],
    *,
    project,
    glossary_draft,
    story_draft,
) -> Dict[str, Any]:
    """执行一个工作台工具。patch 工具写草稿;其余走只读白名单。

    抛 DraftValueError / ToolError 都由调用方(session 循环)转成
    error 回执给模型,单条失败不作废整轮。
    """
    arguments = dict(arguments or {})
    if name == GLOSSARY_PATCH_TOOL_NAME:
        return glossary_draft.apply_patch(arguments.get("entries"))
    if name == STORY_PATCH_TOOL_NAME:
        raw_pages = arguments.get("page_summaries")
        pages = None
        if raw_pages is not None:
            if not isinstance(raw_pages, list):
                raise DraftValueError('Field "page_summaries" must be an array.')
            pages = raw_pages
        return story_draft.apply_patch(
            pages,
            arguments.get("synopsis"),
            page_resolver=lambda page: _resolve_page_name(project, page),
        )
    if name == PAGE_SUMMARIES_TOOL_NAME:
        return _execute_read_page_summaries(glossary_draft, story_draft)
    if name in CONTEXT_READ_TOOLS:
        return execute_agent_tool(name, arguments, project=project)
    raise DraftValueError(f"Unknown or forbidden tool: {name}")


__all__ = [
    "CONTEXT_READ_TOOLS",
    "GLOSSARY_PATCH_TOOL_NAME",
    "PAGE_SUMMARIES_TOOL_NAME",
    "STORY_PATCH_TOOL_NAME",
    "build_context_tools",
    "cap_tool_result",
    "execute_context_tool",
]
