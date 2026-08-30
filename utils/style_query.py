"""Block query engine: text × format predicates over project pages.

纯数据层查询引擎（设计见 docs/技术实现/查找替换与样式管理器重构_设计方案.md §3）：

* ``TextPredicate`` 文本谓词，匹配语义对齐
  ``ui/global_search_widget.py::get_regex_pattern``（正则/大小写/全词）。
* ``FormatPredicate`` 格式谓词：字段级条件（eq/ne/gt/ge/lt/le），量化比较
  复用 ``utils/base_styles.py::quantize_field``，与样式体系 diff 语义一致。
* 两者 AND 组合为 ``BlockQuery``；遍历直读 ``proj.pages`` 数据、不经渲染层，
  离屏可测。未来查找替换 UI 与样式管理器共用本引擎。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Dict, Iterator, List, Optional, Tuple

from utils.base_styles import (
    DIFF_FIELDS,
    IDENTITY_FIELDS,
    copy_value,
    quantize_field,
)

# Fields a FormatPredicate may reference: identity keys + every render
# parameter participating in variant diffs. New render parameters added to
# DIFF_FIELDS (e.g. an upstream effect stack) become queryable for free.
PREDICATE_FIELDS = tuple(IDENTITY_FIELDS) + tuple(DIFF_FIELDS)

# Grouping shared by the style-manager detail panel and the find/replace
# format editor (design doc §5.3). Effects is a replaceable implementation:
# upstream's effect-stack migration swaps this group's controls only.
FIELD_GROUPS: Dict[str, Tuple[str, ...]] = {
    "text": (
        "font_family",
        "font_size",
        "font_weight",
        "italic",
        "underline",
        "strikeout",
    ),
    "color": ("frgb", "srgb", "stroke_width", "opacity"),
    "layout": (
        "alignment",
        "vertical",
        "line_spacing",
        "line_spacing_type",
        "letter_spacing",
        "ligature_common",
        "ligature_discretionary",
        "ligature_contextual",
        "oldstyle_nums",
        "standard_vertical_roman_alignment",
    ),
    "effects": (
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
        "text_transform",
        "glyph_slant_angle",
    ),
}

_ORDERING_OPS = ("gt", "ge", "lt", "le")


# ═══════════════════════════════════════════════════════════════════════
# Text predicate
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TextPredicate:
    """Regex/plain-text predicate over a block's source and translation.

    Matching semantics mirror the global search widget: plain patterns are
    escaped, whole-word wraps ``\\b``, default flags ``DOTALL`` (+``IGNORECASE``
    unless case-sensitive). An empty pattern makes the predicate inactive.
    """

    pattern_text: str = ""
    is_regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    match_src: bool = True
    match_trans: bool = True

    @property
    def is_active(self) -> bool:
        return bool(self.pattern_text) and (self.match_src or self.match_trans)

    def compile(self) -> Optional[re.Pattern]:
        """Compiled pattern; ``None`` when empty or on invalid regex."""
        if not self.pattern_text:
            return None
        body = self.pattern_text if self.is_regex else re.escape(self.pattern_text)
        if self.whole_word:
            body = r"\b" + body + r"\b"
        flag = re.DOTALL
        if not self.case_sensitive:
            flag |= re.IGNORECASE
        try:
            return re.compile(body, flag)
        except re.error:
            return None

    def match_spans(self, blk) -> Dict[str, List[Tuple[int, int]]]:
        """Hit spans per target: ``{"src": [...], "trans": [...]}``."""
        spans: Dict[str, List[Tuple[int, int]]] = {"src": [], "trans": []}
        if not self.is_active:
            return spans
        pattern = self.compile()
        if pattern is None:
            return spans  # invalid regex matches nothing
        if self.match_src:
            spans["src"] = [m.span() for m in pattern.finditer(blk.get_text())]
        if self.match_trans:
            spans["trans"] = [m.span() for m in pattern.finditer(blk.translation or "")]
        return spans

    def matches(self, blk) -> bool:
        if not self.is_active:
            return True
        spans = self.match_spans(blk)
        return bool(spans["src"] or spans["trans"])


# ═══════════════════════════════════════════════════════════════════════
# Format predicate
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class FormatCondition:
    """One field-level condition: ``<field> <op> <value>``.

    ``op``: ``eq`` / ``ne`` compare quantized values (same noise floor as
    variant clustering); ``gt`` / ``ge`` / ``lt`` / ``le`` order raw numeric
    values (interval semantics, e.g. "字号 ≥ 20").
    """

    fname: str
    op: str = "eq"
    value: Any = None

    def __post_init__(self):
        if self.fname not in PREDICATE_FIELDS:
            raise ValueError(f"unknown predicate field: {self.fname!r}")
        if self.op not in ("eq", "ne") + _ORDERING_OPS:
            raise ValueError(f"unknown predicate op: {self.op!r}")

    def matches(self, blk) -> bool:
        actual = getattr(blk.fontformat, self.fname)
        if self.op == "eq":
            return quantize_field(self.fname, actual) == quantize_field(
                self.fname, self.value
            )
        if self.op == "ne":
            return quantize_field(self.fname, actual) != quantize_field(
                self.fname, self.value
            )
        # Ordering ops require numeric values on both sides.
        if not isinstance(actual, Real) or not isinstance(self.value, Real):
            return False
        if self.op == "gt":
            return actual > self.value
        if self.op == "ge":
            return actual >= self.value
        if self.op == "lt":
            return actual < self.value
        return actual <= self.value


@dataclass
class FormatPredicate:
    """AND-combined field conditions over a block's FontFormat."""

    conditions: List[FormatCondition] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return bool(self.conditions)

    def matches(self, blk) -> bool:
        return all(cond.matches(blk) for cond in self.conditions)


# ═══════════════════════════════════════════════════════════════════════
# Combined query
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class BlockQuery:
    """Text × format query. Both predicates AND-combined.

    A query with no active predicate matches nothing (a criteria-less query
    is a UI bug, not "select all").
    """

    text: Optional[TextPredicate] = None
    format: Optional[FormatPredicate] = None

    @property
    def is_active(self) -> bool:
        return bool(
            (self.text is not None and self.text.is_active)
            or (self.format is not None and self.format.is_active)
        )

    def matches(self, blk) -> bool:
        if not self.is_active:
            return False
        if self.text is not None and not self.text.matches(blk):
            return False
        if self.format is not None and not self.format.matches(blk):
            return False
        return True

    def iter_matches(self, proj) -> Iterator[Tuple[str, int, Any]]:
        """Yield ``(pagename, blk_idx, blk)`` for every matching block."""
        if not self.is_active:
            return
        for pname, blklist in proj.pages.items():
            for bidx, blk in enumerate(blklist):
                if self.matches(blk):
                    yield pname, bidx, blk

    def collect(self, proj) -> List[Tuple[str, int]]:
        """All matching blocks as ``(pagename, blk_idx)`` targets."""
        return [(pname, bidx) for pname, bidx, _ in self.iter_matches(proj)]


def build_query_changes(
    proj, query: BlockQuery, changed: Dict[str, Any]
) -> List[Dict]:
    """Query-driven partial format patch over every matched block.

    Only the parameters in *changed* are written — other per-block overrides
    survive (same contract as
    ``utils/base_styles.py::build_flatten_changes``). Unknown fields are
    rejected eagerly so a UI typo cannot silently no-op the batch.
    """
    for k in changed:
        if k not in PREDICATE_FIELDS:
            raise ValueError(f"unknown patch field: {k!r}")
    changes = []
    for pname, bidx, blk in query.iter_matches(proj):
        new_ffmt = blk.fontformat.deepcopy()
        for k, v in changed.items():
            setattr(new_ffmt, k, copy_value(v))
        changes.append(
            {
                "pagename": pname,
                "block_idx": bidx,
                "old_ffmt": blk.fontformat.deepcopy(),
                "new_ffmt": new_ffmt,
            }
        )
    return changes
