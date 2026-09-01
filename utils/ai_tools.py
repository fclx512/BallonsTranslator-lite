"""
Read-only project exploration executors shared by the agent tool faces.

The translation agent (modules/translators/agent/tools.py::execute_agent_tool)
and the glossary workbench (modules/context_agent/tools.py) dispatch only the
four read-only tools through execute_tool. Tool *definitions* live with each
tool face; this module only executes them. The write-style tools of the old
AI assistant were removed with the workbench stage-4 cleanup.
"""

import logging
from typing import Any, Dict, List, Optional

from .proj_compact import (
    build_detail,
    build_index,
    build_paginated_detail,
)

logger = logging.getLogger("ai_chat")

# Tools executable through execute_tool (read-only, never mutate the project).
READONLY_TOOL_NAMES = ("list_pages", "read_pages", "search_blocks", "get_page_info")


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
    fields_whitelist: Optional[set] = None,
) -> Dict[str, Any]:
    """Execute read_pages tool."""
    indices = _resolve_range(proj, start, end)
    # Paginate if too many pages
    if len(indices) > 20:
        chunks = build_paginated_detail(
            proj,
            indices,
            max_pages_per_chunk=20,
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
                snippet = (
                    ("..." if start > 0 else "")
                    + src[start:end_val]
                    + ("..." if end_val < len(src) else "")
                )
            elif field in ("trans", "both") and query_lower in trans.lower():
                match = True
                matched_in = "trans"
                pos = trans.lower().find(query_lower)
                start = max(0, pos - 15)
                end_val = min(len(trans), pos + len(query) + 15)
                snippet = (
                    ("..." if start > 0 else "")
                    + trans[start:end_val]
                    + ("..." if end_val < len(trans) else "")
                )

            if match:
                results.append(
                    {
                        "id": f"{pidx}:{bidx}",
                        "page": pidx,
                        "page_name": pname,
                        "field": matched_in,
                        "snippet": snippet,
                    }
                )

    return {
        "type": "search_results",
        "query": query,
        "field": field,
        "n_results": len(results),
        "results": results[:50],  # Cap at 50
    }


def _execute_get_page_info(proj, start: int, end: int = -1) -> Dict[str, Any]:
    """Execute get_page_info tool."""
    indices = _resolve_range(proj, start, end)
    pages = []
    for pidx in indices:
        pname = proj.idx2pagename(pidx)
        info = proj._image_info.get(pname, {})
        pages.append(
            {
                "pidx": pidx,
                "name": pname,
                "w": info.get("width", 0),
                "h": info.get("height", 0),
            }
        )
    return {"type": "page_info", "pages": pages}


def execute_tool(
    proj,
    tool_name: str,
    arguments: Dict[str, Any],
    fields_whitelist: Optional[set] = None,
) -> Dict[str, Any]:
    """Dispatch a read-only tool call. Returns the tool result dict."""
    logger.debug("execute_tool: %s args=%s", tool_name, arguments)
    if tool_name == "list_pages":
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
    elif tool_name == "get_page_info":
        return _execute_get_page_info(
            proj,
            start=arguments.get("start", 0),
            end=arguments.get("end", arguments.get("start", 0)),
        )
    else:
        raise ToolError(
            f"Unknown or forbidden tool: {tool_name!r}. Available tools: "
            f"{', '.join(READONLY_TOOL_NAMES)}"
        )


def to_openai_tools(tool_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Anthropic-style tool definitions (input_schema) to OpenAI format."""
    tools = []
    for t in tool_defs:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
        )
    return tools
