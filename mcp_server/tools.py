"""MCP tool registration — wraps ai_tools.execute_tool() for each tool.

Each function decorated with @mcp.tool() is a thin wrapper that:
1. Acquires the active project from project_manager
2. Calls execute_tool() with appropriate arguments
3. Returns the result as a JSON string
"""

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

from utils.ai_tools import execute_tool, ToolError

from mcp_server.project_manager import (
    get_active_project,
    get_state,
    open_project as pm_open_project,
    save_project as pm_save_project,
    close_project as pm_close_project,
)

logger = logging.getLogger("mcp_server.tools")

# ── helpers ──────────────────────────────────────────────────────────────


def _run(tool_name: str, **kwargs) -> str:
    """Execute a tool on the active project and return JSON result."""
    try:
        proj = get_active_project()
        result = execute_tool(proj, tool_name, kwargs)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except ToolError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── registration ────────────────────────────────────────────────────────


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tool handlers with the server."""

    # ── Project management ──────────────────────────────────────────

    @mcp.tool()
    def open_project(directory: str) -> str:
        """加载 BallonsTranslator 项目到内存。读取项目目录中的图片和 JSON 数据。"""
        try:
            result = pm_open_project(directory)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": f"Failed to open project: {e}"}, ensure_ascii=False)

    @mcp.tool()
    def save_project() -> str:
        """将当前项目状态写回磁盘。修改项目内容后需调用此工具持久化。"""
        try:
            result = pm_save_project()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    def close_project() -> str:
        """关闭当前项目，释放内存。"""
        try:
            result = pm_close_project()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    def get_state() -> str:
        """获取当前服务器状态：项目是否打开、路径、页面数等。"""
        result = get_state()
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ── Read tools ──────────────────────────────────────────────────

    @mcp.tool()
    def list_pages() -> str:
        """获取项目所有页面的概览索引（轻量级），包括每页的名称、尺寸、文本块数量和字符统计。"""
        return _run("list_pages")

    @mcp.tool()
    def read_pages(start: int, end: Optional[int] = None) -> str:
        """读取指定页面的详细数据，包括每个文本块的原文(src)、译文(trans)及字体样式信息。

        Args:
            start: 起始页索引（从 0 开始）
            end: 结束页索引（包含）。为 None 或 -1 时表示到最后一页。
        """
        kwargs = {"start": start}
        if end is not None:
            kwargs["end"] = end
        return _run("read_pages", **kwargs)

    @mcp.tool()
    def search_blocks(query: str, field: Optional[str] = None) -> str:
        """在所有页面中搜索包含指定文本的文本块。

        Args:
            query: 搜索关键词
            field: 搜索范围。可为 'src'（原文）、'trans'（译文）、'both'（两者）。默认 both。
        """
        kwargs = {"query": query}
        if field is not None:
            kwargs["field"] = field
        return _run("search_blocks", **kwargs)

    @mcp.tool()
    def get_config() -> str:
        """读取项目的全局配置：默认字体、源语言、目标语言等。"""
        return _run("get_config")

    @mcp.tool()
    def get_page_info(start: int, end: Optional[int] = None) -> str:
        """获取指定页面的图片尺寸和元信息（不含文本块内容）。

        Args:
            start: 起始页索引（从 0 开始）
            end: 结束页索引（包含）。为 None 时与 start 相同。
        """
        kwargs = {"start": start}
        if end is not None:
            kwargs["end"] = end
        return _run("get_page_info", **kwargs)

    # ── Write tools ─────────────────────────────────────────────────

    @mcp.tool()
    def set_font(
        ids: str,
        ff: Optional[str] = None,
        fs: Optional[float] = None,
        fw: Optional[int] = None,
        b: Optional[bool] = None,
        i: Optional[bool] = None,
    ) -> str:
        """批量设置文本块的字体样式。

        Args:
            ids: 块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页
            ff: 字体名称（如 'Noto Sans SC'）
            fs: 字号（像素）
            fw: 字重（100-900）
            b: 粗体
            i: 斜体
        """
        kwargs = {"ids": ids}
        if ff is not None:
            kwargs["ff"] = ff
        if fs is not None:
            kwargs["fs"] = fs
        if fw is not None:
            kwargs["fw"] = fw
        if b is not None:
            kwargs["b"] = b
        if i is not None:
            kwargs["i"] = i
        return _run("set_font", **kwargs)

    @mcp.tool()
    def set_color(
        ids: str,
        fg: Optional[str] = None,
        bg: Optional[str] = None,
        sw: Optional[float] = None,
    ) -> str:
        """批量设置文本块的颜色属性。

        Args:
            ids: 块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页
            fg: 文字颜色，JSON 数组如 '[255,0,0]'（会被解析为 RGB 列表）
            bg: 轮廓颜色，JSON 数组如 '[0,0,0]'
            sw: 轮廓宽度（0=无轮廓）
        """
        kwargs = {"ids": ids}
        if fg is not None:
            kwargs["fg"] = json.loads(fg)
        if bg is not None:
            kwargs["bg"] = json.loads(bg)
        if sw is not None:
            kwargs["sw"] = sw
        return _run("set_color", **kwargs)

    @mcp.tool()
    def set_layout(
        ids: str,
        a: Optional[int] = None,
        ls: Optional[float] = None,
        lsp: Optional[float] = None,
        v: Optional[bool] = None,
    ) -> str:
        """批量设置文本块的排版参数。

        Args:
            ids: 块 ID 列表，逗号分隔（如 '0:0,0:1'），或 '页:*' 表示整页
            a: 对齐方式：0=左对齐, 1=居中, 2=右对齐
            ls: 行距（1.0=单倍行距，默认 1.2）
            lsp: 字距（像素）
            v: 竖排
        """
        kwargs = {"ids": ids}
        if a is not None:
            kwargs["a"] = a
        if ls is not None:
            kwargs["ls"] = ls
        if lsp is not None:
            kwargs["lsp"] = lsp
        if v is not None:
            kwargs["v"] = v
        return _run("set_layout", **kwargs)

    @mcp.tool()
    def search_replace(query: str, replacement: str, field: Optional[str] = None) -> str:
        """在指定字段中搜索并替换文本。

        Args:
            query: 搜索文本
            replacement: 替换文本
            field: 目标字段：'src'（原文）或 'trans'（译文）。默认 'trans'。
        """
        kwargs = {"query": query, "replacement": replacement}
        if field is not None:
            kwargs["field"] = field
        return _run("search_replace", **kwargs)
