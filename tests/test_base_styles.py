"""Offscreen unit tests for ``utils/base_styles.py`` — project-level base
styles and derived variant discovery.

This is a pure data-layer module (no Qt widgets), so the whole suite runs
without a ``QApplication``.  Environment variables are pinned to the headless
platform anyway, matching the other offscreen suites, because the import
chain of ``utils.proj_imgtrans`` pulls cv2/numpy (no Qt needed here).

Run from the repo root:
    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_base_styles.py -q
"""

import json
import os
import os.path as osp
import sys
import tempfile

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from utils.base_styles import (  # noqa: E402
    BaseStyle,
    build_flatten_changes,
    build_variant_changes,
    compute_override,
    compute_signature,
    copy_value,
    discover_style_tree,
    ensure_default_base_styles,
    overrides_summary,
    quantize_field,
    variant_display_name,
    DIFF_FIELDS,
    IDENTITY_FIELDS,
)
from utils.config import pcfg  # noqa: E402
from utils.fontformat import FontFormat, TextTransformStack  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans, TextBlkEncoder  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


class FakeBlock:
    """Minimal block stand-in: only ``fontformat`` is needed by discovery."""

    def __init__(self, fontformat):
        self.fontformat = fontformat


class FakeProj:
    """Minimal ProjImgTrans stand-in exposing an ordered ``pages`` dict."""

    def __init__(self, pages=None):
        self.pages = pages or {}


def _discover_setup():
    """Return ``(base_styles, pages)`` for the discovery tests.

    * ``A`` and ``A2`` share identity ``("A", False)`` → first wins.
    * ``B`` is a registered base style with zero matching blocks.
    * ``X`` blocks have no base style → ungrouped signature clustering.
    """
    base_styles = [
        BaseStyle("A", FontFormat(font_family="A", font_size=24, frgb=[0, 0, 0], bold=False)),
        BaseStyle("B", FontFormat(font_family="B", font_size=24)),
        BaseStyle("A2", FontFormat(font_family="A", font_size=99)),  # duplicate identity
    ]
    p1 = [
        FakeBlock(FontFormat(font_family="A", font_size=24, frgb=[0, 0, 0], bold=False)),  # pure A
        FakeBlock(FontFormat(font_family="A", font_size=40, frgb=[0, 0, 0], bold=False)),  # variant {size:40}
        FakeBlock(FontFormat(font_family="A", font_size=40, frgb=[0, 0, 0], bold=False)),  # same variant
        FakeBlock(FontFormat(font_family="A", font_size=40, frgb=[255, 0, 0], bold=False)),  # variant {size:40,fg}
        FakeBlock(FontFormat(font_family="X", font_size=22)),  # ungrouped
    ]
    p2 = [
        FakeBlock(FontFormat(font_family="A", font_size=24, frgb=[0, 0, 0], bold=False)),  # pure A
        FakeBlock(FontFormat(font_family="X", font_size=22)),  # ungrouped same signature
    ]
    return base_styles, {"p1.png": p1, "p2.png": p2}


# ═══════════════════════════════════════════════════════════════════════
# compute_override / quantize_field
# ═══════════════════════════════════════════════════════════════════════


def test_quantize_field_public_alias():
    # quantize_field is the public alias of _quantize.
    assert quantize_field("font_size", 35.0001) == quantize_field("font_size", 35.0) == 35.0


def test_compute_override_float_micro_difference_no_override():
    base = FontFormat(font_family="Zed", font_size=35.0, frgb=[0, 0, 0], bold=False)
    block = FontFormat(font_family="Other", font_size=35.0001, frgb=[0, 0, 0], bold=False)
    # font_size differs by far less than the 0.5 step → quantize to the same key.
    assert quantize_field("font_size", 35.0001) == quantize_field("font_size", 35.0)
    assert compute_override(block, base) == {}


def test_compute_override_real_differences():
    # bold 已弃用：构造期真值化进 font_weight（fontformat.py），bold 标志
    # 本身不参与 diff；font_weight 是真 diff 字段，字重不同即进 override。
    base = FontFormat(font_family="Z", font_size=24.0, frgb=[0, 0, 0], bold=False)
    block = FontFormat(font_family="Z", font_size=40.0, frgb=[255, 0, 0], bold=True)
    assert block.bold is False and block.font_weight == 700
    ov = compute_override(block, base)
    assert ov == {"font_size": 40.0, "frgb": [255, 0, 0], "font_weight": 700}
    # Override stores the block's raw value (not a quantized key).
    assert ov["font_size"] == 40.0
    assert ov["frgb"] == [255, 0, 0]


def test_compute_override_identity_fields_never_override():
    # font_family / vertical are identity keys, excluded from DIFF_FIELDS, so
    # they can never appear in an override even when they differ.
    base = FontFormat(font_family="Base", vertical=False, font_size=24)
    block = FontFormat(font_family="Other", vertical=True, font_size=24)
    ov = compute_override(block, base)
    assert ov == {}
    assert not (set(ov) & set(IDENTITY_FIELDS))
    assert set(ov) <= set(DIFF_FIELDS)


# ═══════════════════════════════════════════════════════════════════════
# compute_signature
# ═══════════════════════════════════════════════════════════════════════


def test_compute_signature_quantized_same():
    a = FontFormat(font_family="X", font_size=24.1)
    b = FontFormat(font_family="X", font_size=24.2)
    # Both quantize to 24.0 (step 0.5) → identical signature.
    assert compute_signature(a) == compute_signature(b)


def test_compute_signature_differs_beyond_step():
    a = FontFormat(font_family="X", font_size=24.0)
    b = FontFormat(font_family="X", font_size=24.7)
    assert quantize_field("font_size", 24.0) != quantize_field("font_size", 24.7)
    assert compute_signature(a) != compute_signature(b)


# ═══════════════════════════════════════════════════════════════════════
# discover_style_tree
# ═══════════════════════════════════════════════════════════════════════


def test_discover_style_tree_attributes_blocks_and_clusters():
    base_styles, pages = _discover_setup()
    tree = discover_style_tree(FakeProj(pages), base_styles)

    # Only the two unique-identity bases appear; duplicate "A2" is dropped.
    assert [n.base.name for n in tree.nodes] == ["A", "B"]
    a_node, b_node = tree.nodes

    # Blocks attributed to base A by identity, diffed against the base.
    assert a_node.base.name == "A"
    assert a_node.base.fontformat.font_size == 24  # first duplicate wins, not A2's 99
    assert a_node.total_count == 5

    # No-override blocks collapse to the single "pure" entry.
    assert a_node.pure.blocks == [("p1.png", 0), ("p2.png", 0)]

    # Same override set merges into one variant; variants sorted by count desc.
    assert [v.overrides for v in a_node.variants] == [
        {"font_size": 40.0},
        {"font_size": 40.0, "frgb": [255, 0, 0]},
    ]
    assert [v.count for v in a_node.variants] == [2, 1]
    assert a_node.variants[0].blocks == [("p1.png", 1), ("p1.png", 2)]
    assert a_node.variants[1].blocks == [("p1.png", 3)]

    # No-identity-key blocks go to ungrouped, clustered by signature.
    assert len(tree.ungrouped) == 1
    entry = tree.ungrouped[0]
    assert entry.count == 2
    assert entry.page_count == 2
    assert entry.fontformat.font_family == "X"
    assert entry.blocks == [("p1.png", 4), ("p2.png", 1)]


def test_discover_style_tree_zero_block_node_kept():
    base_styles, pages = _discover_setup()
    tree = discover_style_tree(FakeProj(pages), base_styles)
    b_node = [n for n in tree.nodes if n.base.name == "B"][0]
    # A registered base style stays reachable even before any block matches it.
    assert b_node.total_count == 0
    assert b_node.pure.blocks == []
    assert b_node.variants == []


def test_discover_style_tree_duplicate_identity_first_wins():
    base_styles, pages = _discover_setup()
    tree = discover_style_tree(FakeProj(pages), base_styles)
    a_names = [n.base.name for n in tree.nodes if n.base.name in ("A", "A2")]
    assert a_names == ["A"]  # "A2" never appears
    a_node = tree.nodes[0]
    assert a_node.base.fontformat.font_size == 24  # "A" first, not "A2"'s 99


# ═══════════════════════════════════════════════════════════════════════
# BaseStyle round-trip
# ═══════════════════════════════════════════════════════════════════════


def test_base_style_roundtrip_json():
    bs = BaseStyle(
        "s", FontFormat(font_family="A", font_size=30, vertical=True, frgb=[255, 0, 0])
    )
    d = bs.to_dict()
    d2 = json.loads(json.dumps(d, cls=TextBlkEncoder))
    bs2 = BaseStyle.from_dict(d2)
    assert bs2.name == "s"
    assert bs2.identity == ("A", True)
    assert bs2.fontformat.font_family == "A"
    assert bs2.fontformat.font_size == 30.0
    assert bs2.fontformat.vertical is True
    assert bs2.fontformat.frgb == [255, 0, 0]


# ═══════════════════════════════════════════════════════════════════════
# ensure_default_base_styles
# ═══════════════════════════════════════════════════════════════════════


def test_ensure_default_base_styles_empty_registers_seed():
    lst = []
    seed = FontFormat(font_family="MyFont", font_size=18)
    assert ensure_default_base_styles(lst, seed) is True
    assert len(lst) == 1
    assert lst[0].name == "MyFont"
    assert lst[0].fontformat.font_family == "MyFont"
    assert lst[0].fontformat.font_size == 18
    assert lst[0].fontformat is not seed  # stored as a deepcopy


def test_ensure_default_base_styles_nonempty_noop():
    lst = [BaseStyle("existing", FontFormat())]
    assert ensure_default_base_styles(lst, FontFormat()) is False
    assert len(lst) == 1
    assert lst[0].name == "existing"


def test_ensure_default_base_styles_seed_none_uses_default():
    lst = []
    assert ensure_default_base_styles(lst, None) is True
    assert len(lst) == 1
    # 对 FontFormat() 缺省值断言而非 pcfg 单例——pcfg 可能已被套件中
    # 更早的测试 reload 成开发者本机 config.json 的内容。
    assert lst[0].name == FontFormat().font_family
    assert lst[0].fontformat.font_family == FontFormat().font_family


# ═══════════════════════════════════════════════════════════════════════
# Variant auto-naming
# ═══════════════════════════════════════════════════════════════════════


def test_overrides_summary_size_first_color_hex_upper():
    ov = {"font_size": 38.0, "frgb": [255, 0, 0]}
    assert overrides_summary(ov) == "38px · fg#FF0000"
    assert variant_display_name("base", ov) == "base · 38px · fg#FF0000"


def test_overrides_summary_bool_tokens():
    ov = {"italic": True, "underline": False, "strikeout": True}
    assert overrides_summary(ov) == "I · -U · S"


def test_overrides_summary_truncates_over_four_tokens():
    ov = {
        "font_size": 38.0,
        "frgb": [255, 0, 0],
        "italic": True,
        "underline": True,
        "strikeout": True,
    }
    # font_size, frgb, italic, underline fill the 4-token budget; rest -> "+1".
    assert overrides_summary(ov) == "38px · fg#FF0000 · I · U +1"


def test_overrides_summary_empty():
    assert overrides_summary({}) == "-"
    assert variant_display_name("base", {}) == "base"


# ═══════════════════════════════════════════════════════════════════════
# build_flatten_changes
# ═══════════════════════════════════════════════════════════════════════


def _flat_setup():
    base = BaseStyle("A", FontFormat(font_family="A", font_size=24, frgb=[0, 0, 0]))
    a0 = FakeBlock(FontFormat(font_family="A", font_size=18, frgb=[200, 100, 50]))
    a1 = FakeBlock(FontFormat(font_family="A", font_size=22, frgb=[10, 20, 30]))
    b0 = FakeBlock(FontFormat(font_family="B", font_size=24, frgb=[0, 0, 0]))
    pages = {"p.png": [a0, a1, b0]}
    return base, FakeProj(pages)


def test_build_flatten_changes_writes_only_changed_params():
    base, proj = _flat_setup()
    changes = build_flatten_changes(proj, base, {"font_size": 40})
    assert [c["block_idx"] for c in changes] == [0, 1]
    # Only the identity-matched "A" blocks are flattened; the "B" block is not.
    assert all(c["block_idx"] != 2 for c in changes)
    for c in changes:
        assert c["new_ffmt"].font_size == 40
        # Per-block overrides survive: frgb keeps each block's own value.
        assert c["new_ffmt"].frgb == c["old_ffmt"].frgb
    assert changes[0]["new_ffmt"].frgb == [200, 100, 50]
    assert changes[1]["new_ffmt"].frgb == [10, 20, 30]
    # old_ffmt is a per-block snapshot, unaffected by the new format.
    assert changes[0]["old_ffmt"].font_size == 18
    assert changes[0]["old_ffmt"] is not changes[0]["new_ffmt"]


def test_build_flatten_changes_deepcopies_old_and_block_untouched():
    base, proj = _flat_setup()
    a0 = proj.pages["p.png"][0]
    changes = build_flatten_changes(proj, base, {"font_size": 40})
    c = changes[0]
    # Mutating the new format must not leak into old_ffmt nor the source block.
    c["new_ffmt"].frgb[0] = 999
    assert c["old_ffmt"].frgb[0] == 200
    assert a0.fontformat.frgb[0] == 200
    assert a0.fontformat.font_size == 18


def test_build_flatten_changes_identity_change_collects_by_new_key():
    # HAZARD (current implementation, asserted as-is): the caller applies
    # `changed` onto base_style.fontformat BEFORE calling build_flatten_changes,
    # and collect_base_blocks reads base_style.identity *at call time*. So an
    # identity change (font_family / vertical) makes the flattener scan for
    # blocks of the NEW identity rather than the original style's blocks.
    base = BaseStyle("A", FontFormat(font_family="A", font_size=24))
    a_blk = FakeBlock(FontFormat(font_family="A", font_size=18, frgb=[1, 2, 3]))
    b_blk = FakeBlock(FontFormat(font_family="B", font_size=18, frgb=[4, 5, 6]))
    proj = FakeProj({"p.png": [a_blk, b_blk]})
    changed = {"font_family": "B", "font_size": 40}
    # Reproduce the UI call order: apply changed to the base before building.
    for k, v in changed.items():
        setattr(base.fontformat, k, copy_value(v))
    changes = build_flatten_changes(proj, base, changed)
    assert len(changes) == 1
    assert changes[0]["block_idx"] == 1  # only the *new* family block is hit
    assert changes[0]["new_ffmt"].font_family == "B"
    assert changes[0]["new_ffmt"].font_size == 40
    # The original style's "A" block is left untouched — this is the hazard.
    assert not any(c["block_idx"] == 0 for c in changes)


# ═══════════════════════════════════════════════════════════════════════
# build_variant_changes
# ═══════════════════════════════════════════════════════════════════════


def test_build_variant_changes_writes_and_skips_missing():
    page = [FakeBlock(FontFormat(font_size=20)), FakeBlock(FontFormat(font_size=20))]
    proj = FakeProj({"ok.png": page, "empty.png": []})
    blocks = [("ok.png", 0), ("ok.png", 5), ("gone.png", 0)]
    changes = build_variant_changes(blocks, proj, {"font_size": 99})
    # Out-of-range and vanished-page entries are skipped; the valid one lands.
    assert len(changes) == 1
    assert changes[0]["pagename"] == "ok.png"
    assert changes[0]["block_idx"] == 0
    assert changes[0]["new_ffmt"].font_size == 99
    assert changes[0]["old_ffmt"].font_size == 20


# ═══════════════════════════════════════════════════════════════════════
# copy_value
# ═══════════════════════════════════════════════════════════════════════


def test_copy_value_list_deepcopied():
    orig = [1, [2, 3], 4]
    c = copy_value(orig)
    assert c == orig
    assert c is not orig
    c[1].append(99)  # nested list must be independent
    assert orig[1] == [2, 3]


def test_copy_value_text_transform_stack_returns_same():
    st = TextTransformStack()
    assert copy_value(st) is st


# ═══════════════════════════════════════════════════════════════════════
# Persistence integration with ProjImgTrans
# ═══════════════════════════════════════════════════════════════════════


def test_proj_to_dict_base_styles_json_roundtrip():
    p = ProjImgTrans()
    p.pages = {
        "p.png": [
            TextBlock(
                translation="hi",
                fontformat=FontFormat(
                    font_family="A", font_size=30, vertical=True, frgb=[255, 0, 0]
                ),
            )
        ]
    }
    p.base_styles = [
        BaseStyle(
            "s", FontFormat(font_family="A", font_size=30, vertical=True, frgb=[255, 0, 0])
        )
    ]
    d = p.to_dict()
    d2 = json.loads(json.dumps(d, cls=TextBlkEncoder))
    bs = BaseStyle.from_dict(d2["base_styles"][0])
    assert bs.name == "s"
    assert bs.identity == ("A", True)
    assert bs.fontformat.font_family == "A"
    assert bs.fontformat.font_size == 30.0
    assert bs.fontformat.vertical is True
    assert bs.fontformat.frgb == [255, 0, 0]


def test_proj_load_from_dict_with_base_styles():
    proj_dict = {
        "pages": {
            "fake.png": [
                {
                    "translation": "x",
                    "fontformat": {
                        "font_family": "A",
                        "font_size": 30,
                        "vertical": True,
                        "frgb": [255, 0, 0],
                    },
                }
            ]
        },
        "base_styles": [
            {
                "name": "myStyle",
                "fontformat": {
                    "font_family": "A",
                    "font_size": 30,
                    "vertical": True,
                    "frgb": [255, 0, 0],
                },
            }
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        p = ProjImgTrans()
        p.directory = td  # load_from_dict scans find_all_imgs(directory)
        p.load_from_dict(proj_dict)
        assert len(p.base_styles) == 1
        bs = p.base_styles[0]
        assert bs.name == "myStyle"
        assert bs.identity == ("A", True)
        assert bs.fontformat.font_size == 30.0
        assert bs.fontformat.frgb == [255, 0, 0]


def test_proj_load_from_dict_without_base_styles_registers_default():
    with tempfile.TemporaryDirectory() as td:
        p = ProjImgTrans()
        p.directory = td
        p.load_from_dict({"pages": {"fake.png": []}})
        assert len(p.base_styles) == 1
        # Legacy projects (no base_styles) get one default style named after
        # the global font family.
        assert p.base_styles[0].name == pcfg.global_fontformat.font_family
        assert p.base_styles[0].fontformat.font_family == pcfg.global_fontformat.font_family
