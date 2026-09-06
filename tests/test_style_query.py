"""Offscreen unit tests for ``utils/style_query.py`` — the pure data-layer
block query engine (text × format predicates).

No ``QApplication`` needed: the module only touches ``TextBlock`` data and
``re``/``numpy``.

Run from the repo root:
    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_style_query.py -q
"""

import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from utils.base_styles import DIFF_FIELDS, IDENTITY_FIELDS  # noqa: E402
from utils.fontformat import FontFormat  # noqa: E402
from utils.style_query import (  # noqa: E402
    BlockQuery,
    FormatCondition,
    FormatPredicate,
    PREDICATE_FIELDS,
    TextPredicate,
    build_query_changes,
)


class FakeBlock:
    """Minimal block stand-in: fontformat + the text fields the predicate reads."""

    def __init__(self, fontformat, text="", translation=""):
        self.fontformat = fontformat
        self._text = text
        self.translation = translation

    def get_text(self):
        return self._text


class FakeProj:
    def __init__(self, pages=None):
        self.pages = pages or {}


def _ffmt(**kw) -> FontFormat:
    return FontFormat(**kw)


# ═══════════════════════════════════════════════════════════════════════
# TextPredicate
# ═══════════════════════════════════════════════════════════════════════


def test_text_predicate_plain_and_case():
    blk = FakeBlock(_ffmt(), text="Hello World", translation="你好 世界")
    p = TextPredicate(pattern_text="world")
    assert p.matches(blk)  # default: case-insensitive, src+trans
    p_cs = TextPredicate(pattern_text="world", case_sensitive=True)
    assert not p_cs.matches(blk)
    p_exact = TextPredicate(pattern_text="Hello World", case_sensitive=True)
    assert p_exact.matches(blk)


def test_text_predicate_plain_escapes_regex_metachars():
    blk = FakeBlock(_ffmt(), text="a.c", translation="")
    # '.' must be literal in plain mode: does not match "abc"
    assert TextPredicate(pattern_text="a.c").matches(blk)
    assert not TextPredicate(pattern_text="ab").matches(blk)


def test_text_predicate_regex_whole_word():
    blk = FakeBlock(_ffmt(), text="cat catalog", translation="")
    p = TextPredicate(pattern_text="cat", is_regex=True, whole_word=True)
    assert p.matches(blk)
    spans = p.match_spans(blk)
    assert spans["src"] == [(0, 3)]


def test_text_predicate_src_trans_selection():
    blk = FakeBlock(_ffmt(), text="source hit", translation="訳文ヒット")
    src_only = TextPredicate(pattern_text="hit", match_trans=False)
    assert src_only.matches(blk)
    assert src_only.match_spans(blk)["trans"] == []
    trans_only = TextPredicate(pattern_text="ヒット", match_src=False)
    assert trans_only.matches(blk)
    assert trans_only.match_spans(blk)["src"] == []
    # "hit" only exists in src → trans-only predicate must fail
    assert not TextPredicate(pattern_text="hit", match_src=False).matches(blk)


def test_text_predicate_empty_inactive_invalid_regex():
    blk = FakeBlock(_ffmt(), text="anything")
    assert not TextPredicate().is_active
    assert TextPredicate().matches(blk)  # inactive passes
    bad = TextPredicate(pattern_text="([", is_regex=True)
    assert bad.compile() is None
    assert not bad.matches(blk)  # invalid regex matches nothing


# ═══════════════════════════════════════════════════════════════════════
# FormatPredicate
# ═══════════════════════════════════════════════════════════════════════


def test_format_condition_eq_quantized():
    # 24.03 quantizes to 24.0 (step 0.5) — same noise floor as variant diff
    blk = FakeBlock(_ffmt(font_size=24.03))
    assert FormatCondition("font_size", "eq", 24.0).matches(blk)
    assert FormatCondition("font_size", "ne", 24.0).matches(blk) is False
    assert FormatCondition("font_size", "eq", 26.0).matches(blk) is False


def test_format_condition_ordering_numeric():
    blk = FakeBlock(_ffmt(font_size=21.0))
    assert FormatCondition("font_size", "ge", 20).matches(blk)
    assert FormatCondition("font_size", "gt", 20).matches(blk)
    assert FormatCondition("font_size", "lt", 22).matches(blk)
    assert FormatCondition("font_size", "le", 21).matches(blk)
    # ordering against a non-numeric value is a non-match, not a crash
    assert not FormatCondition("font_size", "ge", "big").matches(blk)


def test_format_condition_color_and_bool():
    blk = FakeBlock(_ffmt(frgb=[255, 0, 0], italic=True))
    assert FormatCondition("frgb", "eq", [255, 0, 0]).matches(blk)
    assert FormatCondition("frgb", "eq", (255, 0, 0)).matches(blk)
    assert not FormatCondition("frgb", "eq", [0, 0, 0]).matches(blk)
    assert FormatCondition("italic", "eq", True).matches(blk)


def test_format_condition_validation():
    try:
        FormatCondition("nonexistent_field")
        raise AssertionError("expected ValueError for unknown field")
    except ValueError:
        pass
    try:
        FormatCondition("font_size", "like", 1)
        raise AssertionError("expected ValueError for unknown op")
    except ValueError:
        pass


def test_format_predicate_and_combination():
    blk = FakeBlock(_ffmt(font_size=30.0, letter_spacing=1.5))
    pred = FormatPredicate(
        conditions=[
            FormatCondition("font_size", "ge", 20),
            FormatCondition("letter_spacing", "eq", 1.5),
        ]
    )
    assert pred.matches(blk)
    pred.font_size_fail = FormatPredicate(
        conditions=[
            FormatCondition("font_size", "ge", 20),
            FormatCondition("letter_spacing", "eq", 0.0),
        ]
    )
    assert not pred.font_size_fail.matches(blk)
    assert not FormatPredicate().is_active
    assert FormatPredicate().matches(blk)  # inactive passes


def test_format_condition_text_effects_queryable():
    # 批次②：text_effects 进入 DIFF_FIELDS 后对谓词引擎免费可查，
    # 整栈 eq 比较（中性栈 vs 默认栈相等，带描边则不等）。
    from utils.text_effects import StrokeEffect, TextEffectStack  # noqa: E402

    cond = FormatCondition("text_effects", "eq", TextEffectStack())
    assert cond.matches(FakeBlock(_ffmt()))
    assert not cond.matches(
        FakeBlock(_ffmt(text_effects=TextEffectStack(effects=(StrokeEffect(),))))
    )


# ═══════════════════════════════════════════════════════════════════════
# BlockQuery
# ═══════════════════════════════════════════════════════════════════════


def _sample_proj():
    return FakeProj(
        {
            "p1": [
                FakeBlock(_ffmt(font_family="A", font_size=24.0), text="cat"),
                FakeBlock(_ffmt(font_family="A", font_size=40.0), text="dog"),
            ],
            "p2": [
                FakeBlock(_ffmt(font_family="B", vertical=True, font_size=24.0), text="猫"),
            ],
        }
    )


def test_blockquery_inactive_matches_nothing():
    proj = _sample_proj()
    assert BlockQuery().collect(proj) == []
    assert not BlockQuery().is_active


def test_blockquery_text_only():
    q = BlockQuery(text=TextPredicate(pattern_text="cat"))
    assert q.collect(_sample_proj()) == [("p1", 0)]


def test_blockquery_format_only_and_combined():
    q = BlockQuery(format=FormatPredicate(
        conditions=[FormatCondition("font_size", "ge", 30)]
    ))
    assert q.collect(_sample_proj()) == [("p1", 1)]
    # AND: text "cat" × size ≥ 30 → nothing
    q2 = BlockQuery(
        text=TextPredicate(pattern_text="cat"),
        format=FormatPredicate(conditions=[FormatCondition("font_size", "ge", 30)]),
    )
    assert q2.collect(_sample_proj()) == []
    # vertical identity field as a condition
    q3 = BlockQuery(format=FormatPredicate(
        conditions=[FormatCondition("vertical", "eq", True)]
    ))
    assert q3.collect(_sample_proj()) == [("p2", 0)]


def test_blockquery_iter_matches_yields_block():
    q = BlockQuery(text=TextPredicate(pattern_text="dog"))
    got = list(q.iter_matches(_sample_proj()))
    assert len(got) == 1
    pname, bidx, blk = got[0]
    assert (pname, bidx) == ("p1", 1)
    assert blk.get_text() == "dog"


def test_predicate_fields_covers_diff_and_identity():
    assert set(IDENTITY_FIELDS) < set(PREDICATE_FIELDS)
    assert set(DIFF_FIELDS) <= set(PREDICATE_FIELDS)


# ═══════════════════════════════════════════════════════════════════════
# build_query_changes
# ═══════════════════════════════════════════════════════════════════════


def test_build_query_changes_patches_only_changed():
    proj = _sample_proj()
    q = BlockQuery(format=FormatPredicate(
        conditions=[FormatCondition("font_size", "ge", 30)]
    ))
    changes = build_query_changes(proj, q, {"font_size": 28.0})
    assert len(changes) == 1
    ch = changes[0]
    assert (ch["pagename"], ch["block_idx"]) == ("p1", 1)
    assert ch["old_ffmt"].font_size == 40.0
    assert ch["new_ffmt"].font_size == 28.0
    # untouched fields survive the patch
    assert ch["new_ffmt"].font_family == "A"
    # originals untouched (deepcopy contract)
    assert proj.pages["p1"][1].fontformat.font_size == 40.0


def test_build_query_changes_rejects_unknown_field():
    try:
        build_query_changes(_sample_proj(), BlockQuery(text=TextPredicate("x")), {"nope": 1})
        raise AssertionError("expected ValueError for unknown patch field")
    except ValueError:
        pass
