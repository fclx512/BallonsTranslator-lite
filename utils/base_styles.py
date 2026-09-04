"""Project-level base styles and derived variant discovery.

数据层契约（大样式/子样式体系）：

* **大样式 (BaseStyle)** 是项目内持久实体，由 ``utils/proj_imgtrans.py``
  随项目 JSON 保存；身份键为 ``(font_family, vertical)``，项目内唯一。
* **子样式 (VariantEntry)** 不持久化：由块参数与大样式参数的量化差异
  （override）动态派生；相同 override 组合的块自动归并为一个子样式，
  无引用块时自然消失。子样式名由参数摘要自动生成（ASCII token，避免
  翻译问题）。
* 块不存样式引用，归属完全由身份键驱动：块的
  ``(font_family, vertical)`` 命中某大样式即归属之；找不到的大样式键的
  块进入「未分组」，按全参数签名聚类（原 FontStyleManager 行为）。
* 浮点字段量化后才比较/聚类：OCR 派生的连续值（字号、间距……）不会把
  视觉一致的参数裂成多个变体。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import numpy as np

from utils.face_resolver import sync_face
from utils.fontformat import FontFormat, TextTransformStack

# ═══════════════════════════════════════════════════════════════════════
# Field sets & quantization
# ═══════════════════════════════════════════════════════════════════════

# Fields entering the full-parameter signature (ungrouped clustering).
# Kept identical to the historical FontStyleManager discovery so legacy
# ungrouped entries cluster the same way.
_SIGNATURE_FIELDS = [
    "font_family",
    "font_size",
    "stroke_width",
    "frgb",
    "srgb",
    "italic",
    "underline",
    "alignment",
    "vertical",
    "font_weight",
    "line_spacing",
    "letter_spacing",
    "opacity",
    "shadow_radius",
    "shadow_strength",
    "shadow_color",
    "shadow_offset",
    "gradient_enabled",
    "gradient_start_color",
    "gradient_end_color",
    "gradient_angle",
    "gradient_size",
    "line_spacing_type",
]

# Float fields are quantized before hashing/comparing.
_FLOAT_QUANT = {
    "font_size": 0.5,
    "stroke_width": 0.1,
    "line_spacing": 0.05,
    "letter_spacing": 0.05,
    "opacity": 0.01,
    "shadow_radius": 0.5,
    "shadow_strength": 0.05,
    "gradient_angle": 1.0,
    "gradient_size": 1.0,
}

# Identity fields: (font_family, vertical) decides which base style a block
# belongs to. They never appear in variant overrides.
IDENTITY_FIELDS = ("font_family", "vertical")

# Fields entering the variant-override diff. Every render parameter except
# the identity keys, ``_style_name`` (label, not a render param),
# ``punctuation_alignment`` (deprecated → global setting) and
# ``deprecated_attributes``.
DIFF_FIELDS = [
    "font_size",
    "stroke_width",
    "frgb",
    "srgb",
    "italic",
    "underline",
    "strikeout",
    "alignment",
    "font_weight",
    "line_spacing",
    "letter_spacing",
    "standard_vertical_roman_alignment",
    "ligature_common",
    "ligature_discretionary",
    "ligature_contextual",
    "oldstyle_nums",
    "opacity",
    "shadow_radius",
    "shadow_strength",
    "shadow_color",
    "shadow_offset",
    "shadow_include_stroke",
    "gradient_enabled",
    "gradient_start_color",
    "gradient_end_color",
    "gradient_angle",
    "gradient_size",
    "line_spacing_type",
    "text_transform",
    "glyph_slant_angle",
]


def _stack_key(value: Any) -> Any:
    """Normalize a TextTransformStack for equality/hashing.

    ``TextTransformStack`` itself may not implement value equality; the
    tuple of immutable transform dataclasses inside does.
    """
    if isinstance(value, TextTransformStack):
        return tuple(value)
    return value


def _quantize(fname: str, value: Any) -> Any:
    """Return a hashable, noise-free comparison key for one field value."""
    if fname == "text_transform":
        return _stack_key(value)
    step = _FLOAT_QUANT.get(fname)
    if step is not None:
        return round(round(float(value) / step) * step, 4)
    if isinstance(value, (list, tuple, np.ndarray)):
        return tuple(round(float(v), 2) for v in value)
    return value


def compute_signature(ffmt: FontFormat) -> str:
    """Return a stable 12-char hex hash for a FontFormat's visible properties."""
    parts: List[str] = []
    for fname in _SIGNATURE_FIELDS:
        parts.append(repr(_quantize(fname, getattr(ffmt, fname))))
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════
# BaseStyle entity
# ═══════════════════════════════════════════════════════════════════════


class BaseStyle:
    """Named project-level base style: a full FontFormat template."""

    def __init__(self, name: str, fontformat: FontFormat):
        self.name = name
        self.fontformat = fontformat

    @property
    def identity(self) -> Tuple[str, bool]:
        return (self.fontformat.font_family, bool(self.fontformat.vertical))

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "fontformat": self.fontformat.to_serializable_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "BaseStyle":
        return cls(d.get("name", ""), FontFormat(**d.get("fontformat", {})))


def ensure_default_base_styles(
    base_styles: List[BaseStyle], seed: FontFormat | None
) -> bool:
    """Register a default base style when the project carries none.

    The seed is the global default format (``pcfg.global_fontformat``); the
    style is named after its font family — pure data, no hardcoded prose.
    Returns True when a style was created.
    """
    if base_styles:
        return False
    if seed is None:
        seed = FontFormat()
    base_styles.append(BaseStyle(seed.font_family, seed.deepcopy()))
    return True


# ═══════════════════════════════════════════════════════════════════════
# Discovery: override diff + variant clustering
# ═══════════════════════════════════════════════════════════════════════


def compute_override(block_ffmt: FontFormat, base_ffmt: FontFormat) -> Dict[str, Any]:
    """Quantized diff of *block_ffmt* against *base_ffmt* over DIFF_FIELDS.

    Returns ``{field: block value}`` for every field that differs after
    quantization; empty dict means the block matches the base style exactly.
    """
    overrides: Dict[str, Any] = {}
    for fname in DIFF_FIELDS:
        bval = getattr(block_ffmt, fname)
        pval = getattr(base_ffmt, fname)
        if _quantize(fname, bval) != _quantize(fname, pval):
            overrides[fname] = bval
    return overrides


def _override_key(overrides: Dict[str, Any]) -> tuple:
    """Hashable clustering key: quantized override values in DIFF_FIELDS order."""
    return tuple(
        (f, _quantize(f, overrides[f])) for f in DIFF_FIELDS if f in overrides
    )


@dataclass
class VariantEntry:
    """One derived variant under a base style: blocks sharing one override set."""

    overrides: Dict[str, Any] = field(default_factory=dict)
    blocks: List[Tuple[str, int]] = field(default_factory=list)  # (pagename, blk_idx)
    key: tuple = ()  # clustering key; lets the UI reselect a variant after refresh

    @property
    def count(self) -> int:
        return len(self.blocks)

    @property
    def page_count(self) -> int:
        return len({p for p, _ in self.blocks})

    @property
    def is_pure(self) -> bool:
        """A pure variant carries no override — blocks equal to the base style."""
        return not self.overrides


@dataclass
class StyleEntry:
    """One unique style discovered from ungrouped blocks (signature clustering)."""

    signature: str
    fontformat: FontFormat  # representative copy
    blocks: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.blocks)

    @property
    def page_count(self) -> int:
        return len({p for p, _ in self.blocks})


@dataclass
class BaseStyleNode:
    """Tree node: a base style plus its derived variants."""

    base: BaseStyle
    pure: VariantEntry = field(default_factory=VariantEntry)  # no-override blocks
    variants: List[VariantEntry] = field(default_factory=list)  # sorted by count

    @property
    def total_count(self) -> int:
        return self.pure.count + sum(v.count for v in self.variants)

    @property
    def page_count(self) -> int:
        pages = {p for p, _ in self.pure.blocks}
        for v in self.variants:
            pages |= {p for p, _ in v.blocks}
        return len(pages)


@dataclass
class StyleTree:
    """Full discovery result: base-style nodes + ungrouped signature entries."""

    nodes: List[BaseStyleNode] = field(default_factory=list)
    ungrouped: List[StyleEntry] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.nodes) or bool(self.ungrouped)


def discover_style_tree(proj, base_styles: List[BaseStyle]) -> StyleTree:
    """Scan all pages and group blocks into base-style variant trees.

    Blocks whose ``(font_family, vertical)`` matches a base style are diffed
    against it (→ variant clustering); the rest go to ungrouped signature
    clustering. Duplicate identity keys among *base_styles*: the first wins.
    """
    id_map: Dict[Tuple[str, bool], BaseStyle] = {}
    for bs in base_styles:
        if bs.identity not in id_map:
            id_map[bs.identity] = bs

    node_map: Dict[Tuple[str, bool], BaseStyleNode] = {
        key: BaseStyleNode(base=bs) for key, bs in id_map.items()
    }
    var_map: Dict[Tuple[str, bool], Dict[tuple, VariantEntry]] = {
        key: {} for key in id_map
    }
    sig_map: Dict[str, StyleEntry] = {}

    for pname, blklist in proj.pages.items():
        for bidx, blk in enumerate(blklist):
            ffmt = blk.fontformat
            key = (ffmt.font_family, bool(ffmt.vertical))
            node = node_map.get(key)
            if node is not None:
                overrides = compute_override(ffmt, node.base.fontformat)
                vmap = var_map[key]
                vkey = _override_key(overrides)
                entry = vmap.get(vkey)
                if entry is None:
                    entry = VariantEntry(overrides=overrides, key=vkey)
                    vmap[vkey] = entry
                entry.blocks.append((pname, bidx))
            else:
                sig = compute_signature(ffmt)
                entry = sig_map.get(sig)
                if entry is None:
                    entry = StyleEntry(signature=sig, fontformat=ffmt.deepcopy())
                    sig_map[sig] = entry
                entry.blocks.append((pname, bidx))

    tree = StyleTree()
    for key, node in node_map.items():
        vmap = var_map[key]
        for vkey, entry in vmap.items():
            if entry.is_pure:
                node.pure = entry
            else:
                node.variants.append(entry)
        node.variants.sort(key=lambda v: v.count, reverse=True)
        # Keep zero-block nodes visible: the default/registered base style
        # stays reachable for editing even before any block matches it.
        tree.nodes.append(node)
    tree.nodes.sort(key=lambda n: n.total_count, reverse=True)
    tree.ungrouped = sorted(sig_map.values(), key=lambda e: e.count, reverse=True)
    return tree


# ═══════════════════════════════════════════════════════════════════════
# Variant auto-naming
# ═══════════════════════════════════════════════════════════════════════

# Display order: size first (the most common per-bubble adjustment), then
# the remaining fields in DIFF_FIELDS order.
_TOKEN_ORDER = ["font_size"] + [f for f in DIFF_FIELDS if f != "font_size"]

_BOOL_TOKENS = {"italic": "I", "underline": "U", "strikeout": "S"}
_COLOR_FIELDS = {
    "frgb": "fg",
    "srgb": "st",
    "shadow_color": "sh",
    "gradient_start_color": "g0",
    "gradient_end_color": "g1",
}
_NUM_PREFIX = {
    "stroke_width": "stw",
    "line_spacing": "ls",
    "letter_spacing": "lsp",
    "opacity": "op",
    "shadow_radius": "shr",
    "shadow_strength": "shs",
    "shadow_offset": "sho",
    "gradient_angle": "ga",
    "gradient_size": "gs",
    "glyph_slant_angle": "slant",
}
_MAX_TOKENS = 4


def _fmt_num(v: float) -> str:
    return f"{v:g}"


def _fmt_color(value) -> str:
    if isinstance(value, (list, tuple, np.ndarray)):
        try:
            return "#{:02X}{:02X}{:02X}".format(
                *(max(0, min(255, int(round(c)))) for c in value[:3])
            )
        except (ValueError, TypeError):
            pass
    return str(value)


def _override_token(fname: str, value: Any) -> str:
    # Format from the quantized value: the stored representative keeps the
    # raw first-block value (e.g. 40.0001), which would render ugly labels
    # for fields the clustering already treats as equal.
    value = _quantize(fname, value)
    if fname == "font_size":
        return _fmt_num(float(value)) + "px"
    if fname in _BOOL_TOKENS:
        return _BOOL_TOKENS[fname] if value else "-" + _BOOL_TOKENS[fname]
    if fname in _COLOR_FIELDS:
        return _COLOR_FIELDS[fname] + _fmt_color(value)
    if fname == "alignment":
        return {0: "L", 1: "C", 2: "R"}.get(value, f"al{value}")
    if fname in _NUM_PREFIX:
        if isinstance(value, (list, tuple, np.ndarray)):
            vals = _NUM_PREFIX[fname] + "[" + ",".join(_fmt_num(float(v)) for v in value) + "]"
            return vals
        return _NUM_PREFIX[fname] + _fmt_num(float(value))
    if fname == "text_transform":
        n = len(_stack_key(value))
        return f"T{n}" if n else "T-"
    if fname == "line_spacing_type":
        return "lsAbs" if value else "lsPct"
    if fname == "standard_vertical_roman_alignment":
        return "SVR" if value else "SVR-"
    if isinstance(value, bool):
        return f"{fname[:3]}{'+' if value else '-'}"
    if isinstance(value, (int, float)):
        return f"{fname[:3]}{_fmt_num(float(value))}"
    return f"{fname[:3]}:{value}"


def variant_display_name(base_name: str, overrides: Dict[str, Any]) -> str:
    """Auto-generated variant label: ``<base> · 38px · fg#FF0000 …``.

    Pure ASCII tokens — this is generated data (not UI text), so it stays
    untranslated and stable across locales.
    """
    if not overrides:
        return base_name
    summary = overrides_summary(overrides)
    return f"{base_name} · {summary}"


def overrides_summary(overrides: Dict[str, Any]) -> str:
    """Token summary of an override set (``38px · fg#FF0000``, truncating)."""
    tokens = []
    for fname in _TOKEN_ORDER:
        if fname in overrides:
            tokens.append(_override_token(fname, overrides[fname]))
            if len(tokens) >= _MAX_TOKENS:
                break
    rest = len(overrides) - len(tokens)
    label = " · ".join(tokens)
    if rest > 0:
        label += f" +{rest}"
    return label or "-"


# Public alias — the style manager UI diffs its control values against a
# baseline FontFormat with the same quantization the discovery uses.
quantize_field = _quantize


# ═══════════════════════════════════════════════════════════════════════
# Flatten (batch edit) helpers
# ═══════════════════════════════════════════════════════════════════════


def collect_base_blocks(proj, base_style: BaseStyle) -> List[Tuple[str, int, Any]]:
    """All blocks whose identity key matches *base_style* (live scan)."""
    key = base_style.identity
    live = []
    for pname, blklist in proj.pages.items():
        for bidx, blk in enumerate(blklist):
            ffmt = blk.fontformat
            if (ffmt.font_family, bool(ffmt.vertical)) == key:
                live.append((pname, bidx, blk))
    return live


def build_flatten_changes(
    proj, base_style: BaseStyle, changed: Dict[str, Any]
) -> List[Dict]:
    """Change list flattening *changed* params onto every block of the style.

    Only the parameters in *changed* are written — other per-block overrides
    survive. ``new_ffmt`` is a per-block deepcopy so undo restores exactly.
    ``_style_name`` 重算在此（快照之前）：face 是派生显示缓存，必须与
    new_ffmt 的 weight/family 同源（utils/face_resolver.sync_face）。

    Ordering contract: blocks are collected by ``base_style.identity`` **at
    call time**. When *changed* contains an identity key (font_family /
    vertical), the caller MUST call this BEFORE updating
    ``base_style.fontformat`` — otherwise the collection scans by the new
    key and misses the style's own blocks (see _apply_base in the style
    manager UI for the correct collect-then-update sequence).
    """
    changes = []
    for pname, bidx, blk in collect_base_blocks(proj, base_style):
        new_ffmt = blk.fontformat.deepcopy()
        for k, v in changed.items():
            setattr(new_ffmt, k, copy_value(v))
        sync_face(new_ffmt)
        changes.append(
            {
                "pagename": pname,
                "block_idx": bidx,
                "old_ffmt": blk.fontformat.deepcopy(),
                "new_ffmt": new_ffmt,
            }
        )
    return changes


def build_variant_changes(
    blocks: List[Tuple[str, int]], proj, changed: Dict[str, Any]
) -> List[Dict]:
    """Change list writing *changed* onto an explicit variant block list."""
    changes = []
    for pname, bidx in blocks:
        page = proj.pages.get(pname)
        if page is None or not 0 <= bidx < len(page):
            continue
        blk = page[bidx]
        new_ffmt = blk.fontformat.deepcopy()
        for k, v in changed.items():
            setattr(new_ffmt, k, copy_value(v))
        sync_face(new_ffmt)
        changes.append(
            {
                "pagename": pname,
                "block_idx": bidx,
                "old_ffmt": blk.fontformat.deepcopy(),
                "new_ffmt": new_ffmt,
            }
        )
    return changes


def copy_value(value: Any) -> Any:
    """Deep-ish copy for override payloads (lists, stacks)."""
    import copy as _copy

    if isinstance(value, TextTransformStack):
        return value
    return _copy.deepcopy(value)
