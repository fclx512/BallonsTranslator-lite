"""
AI-friendly compact project representation.

Two-tier access (index → detail) + sparse-patch modification with
safe roundtrip.  Reduces per-block JSON from ~1 KB to ~100-200 bytes.
"""

import hashlib
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .fontformat import FontFormat
from .textblock import TextBlock

# ── field classification ────────────────────────────────────────────

# compact_key → (TextBlock getter attr, FontFormat attr for default comparison)
_COMPACT_DEF: Dict[str, Tuple[str, Optional[str]]] = {
    "src":  ("text",        None),
    "trans":("translation", None),
    "lang": ("language",    None),
    "v":    ("src_is_vertical", None),
    "lb":   ("label",       None),
    "ff":   ("font_family",     "font_family"),
    "fs":   ("font_size",       "font_size"),
    "fw":   ("font_weight",     "font_weight"),
    "fg":   ("fg_colors",       "frgb"),
    "bg":   ("bg_colors",       "srgb"),
    "b":    ("bold",            "bold"),
    "i":    ("italic",          "italic"),
    "a":    ("alignment",       "alignment"),
    "sw":   ("stroke_width",    "stroke_width"),
    "ls":   ("line_spacing",    "line_spacing"),
    "lsp":  ("letter_spacing",  "letter_spacing"),
}

# FontFormat class-level defaults — compact output omits fields matching these.
_FONT_CLASS_DEFAULTS: Dict[str, Any] = {
    "font_family":     "Microsoft YaHei UI",
    "font_size":       24.0,
    "stroke_width":    0.0,
    "frgb":            [0, 0, 0],
    "srgb":            [0, 0, 0],
    "bold":            False,
    "italic":          False,
    "alignment":       0,
    "vertical":        False,
    "font_weight":     None,
    "line_spacing":    1.2,
    "letter_spacing":  1.15,
}

# compact_key → TextBlock attribute name for applying modifications
_MOD_ATTR_MAP: Dict[str, str] = {
    "trans": "translation",
    "lang":  "language",
    "v":     "src_is_vertical",
    "lb":    "label",
    "ff":    "font_family",
    "fs":    "font_size",
    "fw":    "font_weight",
    "fg":    "fg_colors",
    "bg":    "bg_colors",
    "b":     "bold",
    "i":     "italic",
    "a":     "alignment",
    "sw":    "stroke_width",
    "ls":    "line_spacing",
    "lsp":   "letter_spacing",
}

# compact_key → one-line prompt description snippet
FIELD_PROMPT_SNIPPETS: Dict[str, str] = {
    "trans": "- trans: 译文文本",
    "ff":    "- ff: 字体名称",
    "fs":    "- fs: 字号（像素）",
    "fw":    "- fw: 字重（100-900，400=常规，700=粗体）",
    "fg":    "- fg: 文字颜色 [R, G, B]",
    "bg":    "- bg: 轮廓颜色 [R, G, B]",
    "b":     "- b: 粗体 (true/false)",
    "i":     "- i: 斜体 (true/false)",
    "a":     "- a: 对齐 (0=左/1=中/2=右)",
    "sw":    "- sw: 轮廓宽度（0=无轮廓）",
    "ls":    "- ls: 行距（1.0=单倍，1.2=默认）",
    "lsp":   "- lsp: 字距",
    "v":     "- v: 竖排 (true/false)",
    "lb":    "- lb: 气泡类型标签",
    "lang":  "- lang: 源语言 (ja/eng/unknown)",
}

SYSTEM_PROMPT_EDIT = (
    "你是一个漫画翻译编辑助手。你可以读取项目中文本块的原文和译文"
    "以及字体样式信息。\n"
    "根据用户的指令，修改译文文本和/或字体样式。\n\n"
    "输出格式：严格的 JSON。\n"
    '{{"changes": [{{"id": "页:块", ...}}]}}\n'
    "只输出需要修改的字段，不要输出未修改的内容。\n\n"
    "可修改的字段：\n"
    "{field_descriptions}"
)

SYSTEM_PROMPT_TRANSLATION = (
    "你是一个专业的漫画翻译。\n"
    "你的翻译应当：\n"
    "- 准确传达原文语义，保留角色语气和情感\n"
    "- 符合目标语言的自然表达习惯\n"
    "- 术语在同一项目中保持统一\n"
    "- 拟声词/效果音做本地化处理\n\n"
    "输出格式：严格的 JSON。\n"
    '{{"changes": [{{"id": "页:块", ...}}]}}\n'
    "只输出需要修改的字段，不要输出未修改的内容。\n\n"
    "可修改的字段：\n"
    "{field_descriptions}"
)

# ── exceptions ───────────────────────────────────────────────────────

class StaleProjectError(Exception):
    """项目在 AI 读取后被用户修改，hash 不匹配，拒绝应用修改。"""

class InvalidModificationError(Exception):
    """AI 返回的 modification JSON 格式不合法。"""

# ── helpers ──────────────────────────────────────────────────────────

def _get_font_default(fmt_attr: str, global_font: Optional[FontFormat]) -> Any:
    """Return the comparison default for *fmt_attr*."""
    if global_font is not None and hasattr(global_font, fmt_attr):
        return getattr(global_font, fmt_attr)
    return _FONT_CLASS_DEFAULTS.get(fmt_attr)

def _is_default(value: Any, default: Any) -> bool:
    """Value equals its default (handles lists)."""
    if default is None:
        return value is None
    if isinstance(default, list) and isinstance(value, list):
        return value == default
    return value == default

# ── compact serialization ────────────────────────────────────────────

def compact_block(
    blk: TextBlock,
    blk_id: str,
    global_font: Optional[FontFormat] = None,
    fields_whitelist: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Convert one TextBlock to its compact dict."""
    result: Dict[str, Any] = {"id": blk_id}

    for ckey, (blk_attr, fmt_attr) in _COMPACT_DEF.items():
        if fields_whitelist is not None and ckey not in fields_whitelist:
            continue

        if blk_attr == "text":
            value = blk.get_text()
        else:
            value = getattr(blk, blk_attr)

        # skip empty / None
        if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
            continue

        # for font fields, skip if matching default
        if fmt_attr is not None:
            default = _get_font_default(fmt_attr, global_font)
            if _is_default(value, default):
                continue

        result[ckey] = value

    return result

def build_index(proj, include_global_font: bool = True) -> Dict[str, Any]:
    """Build tier-1 index view."""
    from .config import pcfg

    pages: List[Dict[str, Any]] = []
    total_blocks = 0
    pages_with_content = 0
    total_src_chars = 0
    total_trans_chars = 0

    for pidx, (pname, blklist) in enumerate(proj.pages.items()):
        n = len(blklist)
        img_info = proj._image_info.get(pname, {})
        w = img_info.get("width", 0)
        h = img_info.get("height", 0)
        pages.append({"pidx": pidx, "name": pname, "w": w, "h": h, "n_blocks": n})
        total_blocks += n
        if n > 0:
            pages_with_content += 1
        for blk in blklist:
            total_src_chars += len(blk.get_text())
            total_trans_chars += len(blk.translation or "")

    result: Dict[str, Any] = {
        "type": "index",
        "project": proj.proj_name(),
        "total_pages": len(proj.pages),
        "total_blocks": total_blocks,
        "pages": pages,
        "summary": {
            "pages_with_content": pages_with_content,
            "pages_empty": len(proj.pages) - pages_with_content,
            "total_src_chars": total_src_chars,
            "total_trans_chars": total_trans_chars,
        },
    }

    if include_global_font:
        gf = pcfg.global_fontformat
        result["global_font"] = {
            "ff": gf.font_family,
            "fs": gf.font_size,
            "fw": gf.font_weight,
            "fg": gf.frgb if isinstance(gf.frgb, list) else list(gf.frgb),
            "bg": gf.srgb if isinstance(gf.srgb, list) else list(gf.srgb),
            "b":  gf.bold,
        }

    return result

def build_detail(
    proj,
    page_indices: List[int],
    fields_whitelist: Optional[Set[str]] = None,
    include_metadata: bool = True,
) -> Dict[str, Any]:
    """Build tier-2 detail view for specific pages."""
    from .config import pcfg

    page_entries: List[Dict[str, Any]] = []
    for pidx in page_indices:
        pname = proj.idx2pagename(pidx)
        blklist = proj.pages[pname]
        img_info = proj._image_info.get(pname, {})
        w = img_info.get("width", 0)
        h = img_info.get("height", 0)

        blocks = []
        for bidx, blk in enumerate(blklist):
            blk_id = f"{pidx}:{bidx}"
            cd = compact_block(blk, blk_id, global_font=pcfg.global_fontformat,
                              fields_whitelist=fields_whitelist)
            if "src" in cd or "trans" in cd:
                blocks.append(cd)

        page_entries.append({
            "pidx": pidx,
            "name": pname,
            "w": w,
            "h": h,
            "blocks": blocks,
        })

    result: Dict[str, Any] = {
        "type": "detail",
        "pages": page_entries,
    }

    if include_metadata:
        result["meta"] = {
            "hash": generate_project_hash(proj),
            "ts": int(time.time()),
        }

    return result

def build_paginated_detail(
    proj,
    page_indices: List[int],
    max_pages_per_chunk: int = 5,
    fields_whitelist: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Split a large page request into multiple detail chunks."""
    chunks = []
    for i in range(0, len(page_indices), max_pages_per_chunk):
        chunk_indices = page_indices[i : i + max_pages_per_chunk]
        chunks.append(build_detail(proj, chunk_indices, fields_whitelist=fields_whitelist))
    return chunks

# ── prompt building ──────────────────────────────────────────────────

def build_system_prompt(
    fields_whitelist: Optional[Set[str]] = None,
    translation_mode: bool = False,
) -> str:
    """Build system prompt dynamically based on enabled fields."""
    if fields_whitelist is None:
        # all modifiable fields
        snippets = list(FIELD_PROMPT_SNIPPETS.values())
    else:
        snippets = [
            v for k, v in FIELD_PROMPT_SNIPPETS.items()
            if k in fields_whitelist
        ]

    if snippets:
        field_descriptions = "\n".join(snippets)
    else:
        field_descriptions = "（仅可读取原文和译文，无可用修改字段）"

    template = SYSTEM_PROMPT_TRANSLATION if translation_mode else SYSTEM_PROMPT_EDIT
    return template.format(field_descriptions=field_descriptions)

# ── ID parsing ───────────────────────────────────────────────────────

def parse_block_id(block_id: str) -> Tuple[int, int]:
    """Parse "pidx:bidx" → (page_index, block_index)."""
    try:
        parts = block_id.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid block id: {block_id!r}")
        return int(parts[0]), int(parts[1])
    except (ValueError, TypeError) as e:
        raise ValueError(f"invalid block id: {block_id!r}") from e

def expand_block_ids(id_str: str, proj) -> List[str]:
    """Expand "0:*" or "0:0,0:1" to a flat list of block ids."""
    result: List[str] = []
    for part in id_str.split(","):
        part = part.strip()
        if part.endswith(":*"):
            pidx_str = part[:-2]
            pidx = int(pidx_str)
            pname = proj.idx2pagename(pidx)
            n = len(proj.pages[pname])
            for bidx in range(n):
                result.append(f"{pidx}:{bidx}")
        else:
            result.append(part)
    return result

# ── project hash ─────────────────────────────────────────────────────

def generate_project_hash(proj) -> str:
    """Fast hash of project structure (page names + block counts + text lengths)."""
    h = hashlib.sha256()
    for pname in proj.pages:
        h.update(pname.encode("utf-8"))
        blklist = proj.pages[pname]
        h.update(str(len(blklist)).encode())
        for blk in blklist:
            h.update(str(len(blk.get_text())).encode())
            h.update(str(len(blk.translation or "")).encode())
    return h.hexdigest()[:8]

# ── modification validation & application ────────────────────────────

def validate_modifications(proj, modifications: Dict[str, Any]) -> List[str]:
    """Validate modification dict. Returns list of error messages (empty = valid)."""
    errors: List[str] = []

    if not isinstance(modifications, dict):
        return ["modifications must be a dict"]

    if modifications.get("type") != "modifications":
        errors.append("missing 'type': 'modifications'")

    changes = modifications.get("changes")
    if not isinstance(changes, list):
        errors.append("'changes' must be a list")
        return errors

    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            errors.append(f"change[{i}]: must be a dict")
            continue
        if "id" not in change:
            errors.append(f"change[{i}]: missing 'id'")
            continue

        raw_id = str(change["id"])
        try:
            for blk_id in expand_block_ids(raw_id, proj):
                pidx, bidx = parse_block_id(blk_id)
                pname = proj.idx2pagename(pidx)
                if bidx < 0 or bidx >= len(proj.pages[pname]):
                    errors.append(f"block {blk_id}: out of range")
        except (ValueError, IndexError, KeyError) as e:
            errors.append(f"change[{i}] id {raw_id!r}: {e}")
            continue

        for ckey in change:
            if ckey == "id":
                continue
            if ckey not in _MOD_ATTR_MAP:
                errors.append(f"change[{i}]: unknown field {ckey!r}")

    return errors

def apply_modifications(
    proj,
    modifications: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[int, List[str]]:
    """Apply AI modifications to the project. Returns (changed_count, warnings)."""
    warnings: List[str] = []

    if metadata:
        stored_hash = metadata.get("hash")
        if stored_hash:
            current_hash = generate_project_hash(proj)
            if stored_hash != current_hash:
                raise StaleProjectError(
                    f"project hash mismatch: stored={stored_hash}, current={current_hash}"
                )

    errs = validate_modifications(proj, modifications)
    if errs:
        raise InvalidModificationError("\n".join(errs))

    changed = 0
    for change in modifications.get("changes", []):
        raw_id = str(change["id"])
        try:
            blk_ids = expand_block_ids(raw_id, proj)
        except (ValueError, IndexError):
            warnings.append(f"skipping invalid id: {raw_id!r}")
            continue

        for blk_id in blk_ids:
            pidx, bidx = parse_block_id(blk_id)
            pname = proj.idx2pagename(pidx)
            blk = proj.pages[pname][bidx]

            for ckey, cvalue in change.items():
                if ckey == "id":
                    continue
                attr = _MOD_ATTR_MAP[ckey]
                setattr(blk, attr, cvalue)
            changed += 1

    return changed, warnings
