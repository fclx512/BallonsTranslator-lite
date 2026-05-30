"""
AI Prompt loader — externalized prompt templates with built-in fallbacks.

Loads templates from ``config/prompts.json`` when available; falls back to
hard-coded defaults (zh + en) for any missing key.  Prompts use
``{placeholder}`` format strings filled in by the callers.
"""

from __future__ import annotations

import json
import logging
import os.path as osp
from typing import Dict, Optional

logger = logging.getLogger("ai_chat")

# ── Built-in defaults (zh) ──────────────────────────────────────────────

_AGENT_SYSTEM_ZH = """你是一个漫画翻译编辑助手，可通过工具读取、搜索和修改项目文本块。

## 数据模型
- 页码从 0 开始：第1页→start=0，前N页→end=N-1，X到Y页→start=X-1,end=Y-1
- 文本块 id 格式："页:块"（如 0:3 表示第1页第4个块）
- 页面索引已在上下文注入，无需调用 list_pages；用 read_pages 读取具体数据

## 可用工具

__TOOL_INDEX__

## 响应规则
1. 每次用户请求，先判断是否需要查询数据 → 使用工具函数读取，得到数据后给出回答 + changes
2. 少量修改 → 在回答后输出 changes JSON：
{"changes": [{"id": "0:0", "trans": "译文"}, {"id": "0:3", "fs": 28}]}
3. 批量样式修改优先使用 set_font、set_color、set_layout、search_replace
4. 只输出需修改的字段，不要输出未修改的内容
5. 如果用户要求同时修改译文和样式（如字体、粗体、颜色等），**同一个 block 的多种修改必须放在同一个 change 对象中**：
{"changes": [{"id": "0:0", "trans": "译文", "b": true, "fs": 28}]}
6. 工具调用和 changes JSON 互补：工具批量改样式，changes JSON 逐块改译文，两者可在同一轮同时产出
7. 每一轮都按上述规则使用工具。你的工具调用记录会保留在上下文中供参考

## 可修改字段

__FIELD_DESC__

__TRANSLATION_RULES__
__FEW_SHOT_EXAMPLES__"""

_AGENT_SYSTEM_EN = """You are a manga translation editor assistant. You can read, search, and modify project text blocks via tools.

## Data Model
- Pages are 0-indexed: page 1 → start=0, first N pages → end=N-1, pages X through Y → start=X-1, end=Y-1
- Block id format: "page:block" (e.g. 0:3 = page 1, block 4)
- Page index is pre-injected in context; skip list_pages, use read_pages for details

## Available Tools

__TOOL_INDEX__

## Response Rules
1. For each user request, first decide if you need data → use the tool function to read it, then answer with changes
2. Few modifications → output changes JSON at the end of your reply:
{"changes": [{"id": "0:0", "trans": "Hello"}, {"id": "0:3", "fs": 28}]}
3. For batch style edits, prefer set_font, set_color, set_layout, search_replace
4. Only output fields that need changing; skip unchanged fields
5. When the user asks to modify BOTH translation AND style (font, bold, color, etc.) for the same block, **combine all fields into a single change object**:
{"changes": [{"id": "0:0", "trans": "Hello", "b": true, "fs": 28}]}
6. Tool calls and changes JSON are complementary: use tools for batch style edits, changes JSON for per-block translation edits — both can be produced in the same turn
7. Follow these rules on every turn. Your tool call records are preserved in context for reference

## Modifiable Fields

__FIELD_DESC__

__TRANSLATION_RULES__
__FEW_SHOT_EXAMPLES__"""

_CHAT_SYSTEM_ZH = """你是一个专业的漫画/图片翻译顾问。你可以帮助用户解决以下问题：

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

_CHAT_SYSTEM_EN = """You are a professional manga/image translation consultant. You can help users with:

- Manga translation strategy and best practices
- Cultural localization advice (JP↔CN, JP↔EN, etc.)
- Language-specific and terminology questions
- Manga typesetting and font selection recommendations
- Manga translation workflow guidance
- General questions about BallonsTranslator

## Response Guidelines
- Concise but thorough
- Use examples when helpful
- Markdown formatting supported (bold, italic, lists, code)
- Be honest when uncertain
- If the user asks about project data operations, suggest switching to Agent mode

In the current mode you do NOT have project tool access. You can only provide advice and knowledge-based answers."""

_TRANSLATION_RULES_ZH = """## 翻译规则
- 译文填入 `trans`，保留角色语气情感，术语全篇统一
- 拟声词效果音本地化（ドキドキ→怦怦）
- 目标语言自然流畅
- trans 必须填译文，严禁输出原文"""

_TRANSLATION_RULES_EN = """## Translation Rules
- Put translations in `trans`; preserve character voice and emotional tone
- Keep terminology consistent across the entire project
- Localize onomatopoeia naturally (ドキドキ→ba-thump)
- Target language must be natural and idiomatic
- `trans` must contain the translation, NEVER the source text"""

_FEW_SHOT_ZH = """## 示例

例1 翻译：用户"翻译前3页"
→ tool_calls: {"tool_calls": [{"name": "read_pages", "arguments": {"start": 0, "end": 2}}]}
← 系统返回页面数据
→ changes: {"changes": [{"id": "0:0", "trans": "我是学生会长"}, {"id": "0:1", "trans": "请多指教"}]}

例2 改样式：用户"把标题字号改成36"
→ tool_calls: {"tool_calls": [{"name": "search_blocks", "arguments": {"query": "标题"}}]}
← 系统返回匹配块 [{"id": "0:2", "src": "第1话 标题"}]
好的，找到第0页的标题块。→ changes: {"changes": [{"id": "0:2", "fs": 36}]}

例3 混合修改：用户"把第1页的对话译成中文并全部加粗"
→ tool_calls: {"tool_calls": [{"name": "read_pages", "arguments": {"start": 0, "end": 0}}]}
← 系统返回页面数据（2个文本块，当前译文为空）
好的，以下是翻译并加粗的结果。→ changes:
{"changes": [{"id": "0:0", "trans": "前情提要", "b": true}, {"id": "0:1", "trans": "某个少女的故事", "b": true}]}"""

_FEW_SHOT_EN = """## Examples

Ex1 Translation: User "translate first 3 pages"
→ tool_calls: {"tool_calls": [{"name": "read_pages", "arguments": {"start": 0, "end": 2}}]}
← System returns page data
→ changes: {"changes": [{"id": "0:0", "trans": "I am the student council president"}, {"id": "0:1", "trans": "Nice to meet you"}]}

Ex2 Styling: User "make the title font size 36"
→ tool_calls: {"tool_calls": [{"name": "search_blocks", "arguments": {"query": "title"}}]}
← System returns match [{"id": "0:2", "src": "Chapter 1 Title"}]
Found the title block on page 0. → changes: {"changes": [{"id": "0:2", "fs": 36}]}

Ex3 Mixed: User "translate the dialogue on page 1 into English and make it bold"
→ tool_calls: {"tool_calls": [{"name": "read_pages", "arguments": {"start": 0, "end": 0}}]}
← System returns page data (2 blocks, no existing translation)
Here is the translation with bold applied. → changes:
{"changes": [{"id": "0:0", "trans": "Previously on...", "b": true}, {"id": "0:1", "trans": "A story of a girl", "b": true}]}"""

# ── Defaults dict (used as fallback) ────────────────────────────────────

_BUILTIN_DEFAULTS: Dict[str, Dict[str, str]] = {
    "agent_system": {"zh": _AGENT_SYSTEM_ZH, "en": _AGENT_SYSTEM_EN},
    "chat_system": {"zh": _CHAT_SYSTEM_ZH, "en": _CHAT_SYSTEM_EN},
    "translation_rules": {"zh": _TRANSLATION_RULES_ZH, "en": _TRANSLATION_RULES_EN},
    "few_shot_examples": {"zh": _FEW_SHOT_ZH, "en": _FEW_SHOT_EN},
}

# ── Runtime cache ───────────────────────────────────────────────────────

_prompts: Optional[Dict] = None
_prompts_path: Optional[str] = None


def _prompts_file_path() -> str:
    global _prompts_path
    if _prompts_path is None:
        from . import shared

        _prompts_path = osp.join(shared.PROGRAM_PATH, "config", "prompts.json")
    return _prompts_path


def get_active_lang() -> str:
    """Return 'zh' or 'en' based on the current app display language."""
    from . import shared

    lang = getattr(shared, "DEFAULT_DISPLAY_LANG", "English")
    if isinstance(lang, str) and lang.startswith("zh"):
        return "zh"
    return "en"


def load_prompts(force_reload: bool = False) -> Dict:
    """Load prompts from config/prompts.json, merging with built-in defaults.

    Returns a dict of shape ``{template_key: {lang: str}}``.  Missing keys
    or languages are filled from the built-in defaults, so callers never
    get KeyError.
    """
    global _prompts
    if _prompts is not None and not force_reload:
        return _prompts

    merged: Dict[str, Dict[str, str]] = {}
    # Start with a deep copy of built-in defaults
    for key, langs in _BUILTIN_DEFAULTS.items():
        merged[key] = dict(langs)

    # Try loading external config
    path = _prompts_file_path()
    try:
        if osp.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                external = json.load(f)
            version = external.get("version", 0)
            logger.info("Loaded prompts.json v%d from %s", version, path)
            # Merge each template key
            for key in _BUILTIN_DEFAULTS:
                if key in external:
                    for lang in ("zh", "en"):
                        if lang in external[key] and external[key][lang]:
                            merged[key][lang] = external[key][lang]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load prompts.json: %s — using built-in defaults", e)

    _prompts = merged
    return merged


def get_prompt(key: str, lang: Optional[str] = None) -> str:
    """Return a single prompt template string.

    Args:
        key: One of ``agent_system``, ``chat_system``, ``translation_rules``,
             ``few_shot_examples``.
        lang: ``'zh'`` or ``'en'``.  Uses ``get_active_lang()`` when omitted.
    """
    prompts = load_prompts()
    if lang is None:
        lang = get_active_lang()
    return prompts[key][lang]


def get_version() -> int:
    """Return the prompts config version (1 = external, 0 = built-in only)."""
    try:
        path = _prompts_file_path()
        if osp.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("version", 0)
    except Exception:
        pass
    return 0
