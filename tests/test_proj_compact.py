"""Tests for utils/proj_compact.py — AI-friendly compact project representation."""

import copy
import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"


def _make_mock_proj():
    """Create a minimal in-memory ProjImgTrans for testing (no filesystem)."""
    from utils.proj_imgtrans import ProjImgTrans

    proj = ProjImgTrans()
    proj.directory = "/mock/测试项目"
    proj.proj_path = "/mock/测试项目/imgtrans_测试项目.json"

    # page 0: 3 blocks with mixed content
    # page 1: 1 block, empty
    proj.pages = {
        "01.jpg": [],
        "02.jpg": [],
    }

    from utils.textblock import TextBlock

    blk0 = TextBlock(
        xyxy=[100, 200, 300, 400],
        lines=[[[100, 200], [300, 200], [300, 400], [100, 400]]],
        language="ja",
        text=["こんにちは"],
        translation="你好",
        src_is_vertical=False,
        label=None,
        fontformat={
            "font_family": "尙古圆体SC",
            "font_size": 32.0,
            "stroke_width": 0.2,
            "frgb": [251, 66, 126],
            "srgb": [255, 255, 255],
            "bold": True,
            "italic": False,
            "alignment": 1,
            "vertical": False,
            "font_weight": 700,
            "line_spacing": 1.2,
            "letter_spacing": 1.1,
        },
    )
    blk1 = TextBlock(
        xyxy=[500, 100, 700, 300],
        lines=[[[500, 100], [700, 100], [700, 300], [500, 300]]],
        language="eng",
        text=["Hey!"],
        translation="嘿！",
        src_is_vertical=False,
        label="balloon",
        fontformat={
            "font_family": "Arial",
            "font_size": 24.0,
            "stroke_width": 0.0,
            "frgb": [0, 0, 0],
            "srgb": [0, 0, 0],
            "bold": False,
            "italic": True,
            "alignment": 0,
            "vertical": False,
            "font_weight": 400,
            "line_spacing": 1.2,
            "letter_spacing": 1.15,
        },
    )
    blk2 = TextBlock(
        xyxy=[800, 50, 1000, 250],
        lines=[[[800, 50], [1000, 50], [1000, 250], [800, 250]]],
        language="unknown",
        text=[""],
        translation="",
        src_is_vertical=None,
        label=None,
    )

    proj.pages["01.jpg"] = [blk0, blk1, blk2]
    proj.pages["02.jpg"] = []

    proj._pagename2idx = {"01.jpg": 0, "02.jpg": 1}
    proj._idx2pagename = {0: "01.jpg", 1: "02.jpg"}
    proj._image_info = {
        "01.jpg": {"finish_code": 15, "width": 2480, "height": 3508},
        "02.jpg": {"finish_code": 0, "width": 1920, "height": 1080},
    }
    proj.current_img = None
    return proj


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


def test_compact_block_excludes_geometry():
    """Geometry fields (xyxy, lines, _bounding_rect) are NOT in compact output."""
    from utils.proj_compact import compact_block
    from utils.textblock import TextBlock

    blk = TextBlock(
        xyxy=[100, 200, 300, 400],
        lines=[[[100, 200], [300, 200], [300, 400], [100, 400]]],
        text=["テスト"],
        translation="测试",
    )
    cd = compact_block(blk, "0:0")
    for geo_key in (
        "xyxy",
        "lines",
        "_bounding_rect",
        "angle",
        "distance",
        "vec",
        "norm",
    ):
        assert geo_key not in cd, f"{geo_key} should be excluded"
    assert cd["id"] == "0:0"
    assert cd["trans"] == "测试"


def test_compact_block_excludes_render_effects():
    """Shadow and gradient fields are excluded."""
    from utils.proj_compact import compact_block
    from utils.textblock import TextBlock

    blk = TextBlock(
        xyxy=[0, 0, 100, 100],
        text=["abc"],
        translation="xyz",
        fontformat={
            "shadow_radius": 5.0,
            "shadow_strength": 0.5,
            "gradient_enabled": True,
            "gradient_angle": 45.0,
            "opacity": 0.8,
            "underline": True,
        },
    )
    cd = compact_block(blk, "0:0")
    for fx_key in (
        "shadow_radius",
        "shadow_strength",
        "shadow_color",
        "shadow_offset",
        "gradient_enabled",
        "gradient_start_color",
        "gradient_end_color",
        "gradient_angle",
        "gradient_size",
        "opacity",
        "underline",
    ):
        assert fx_key not in cd, f"{fx_key} should be excluded"


def test_compact_block_omits_font_defaults():
    """Font fields equal to FontFormat class defaults are omitted."""
    from utils.proj_compact import compact_block
    from utils.textblock import TextBlock

    # All font defaults
    blk = TextBlock(
        xyxy=[0, 0, 100, 100],
        text=["hello"],
        translation="你好",
    )
    cd = compact_block(blk, "0:0")
    # font fields at defaults should be absent
    for font_key in ("ff", "fs", "fw", "fg", "bg", "b", "i", "sw"):
        assert font_key not in cd, (
            f"{font_key} (default) should be omitted, got {cd.get(font_key)}"
        )


def test_fields_whitelist():
    """fields_whitelist filters output keys correctly."""
    from utils.proj_compact import compact_block
    from utils.textblock import TextBlock

    blk = TextBlock(
        xyxy=[0, 0, 100, 100],
        text=["hello world"],
        translation="你好世界",
        language="eng",
        fontformat={"font_size": 48.0, "bold": True, "frgb": [255, 0, 0]},
    )
    # only src + trans
    cd = compact_block(blk, "0:0", fields_whitelist={"src", "trans"})
    assert "src" in cd
    assert "trans" in cd
    assert "lang" not in cd
    assert "fs" not in cd
    assert "b" not in cd


def test_build_index():
    """Index has correct structure."""
    proj = _make_mock_proj()
    from utils.proj_compact import build_index

    idx = build_index(proj)
    assert idx["type"] == "index"
    assert idx["total_pages"] == 2
    assert idx["total_blocks"] == 3
    assert len(idx["pages"]) == 2
    assert idx["pages"][0]["n_blocks"] == 3
    assert idx["pages"][1]["n_blocks"] == 0
    assert "global_font" in idx
    assert "ff" in idx["global_font"]


def test_build_detail():
    """Detail has correct structure for specified pages."""
    proj = _make_mock_proj()
    from utils.proj_compact import build_detail

    detail = build_detail(proj, [0])
    assert detail["type"] == "detail"
    assert len(detail["pages"]) == 1
    pg = detail["pages"][0]
    assert pg["pidx"] == 0
    assert pg["name"] == "01.jpg"
    assert pg["w"] == 2480
    assert pg["h"] == 3508
    assert len(pg["blocks"]) == 2  # blk2 (empty text) is skipped
    assert "meta" in detail
    assert "hash" in detail["meta"]


def test_build_detail_empty_page_omitted():
    """Empty page should have no block entries in detail."""
    proj = _make_mock_proj()
    from utils.proj_compact import build_detail

    detail = build_detail(proj, [1])
    pg = detail["pages"][0]
    assert pg["blocks"] == []


def test_parse_block_id():
    """parse_block_id correctly splits and parses IDs."""
    from utils.proj_compact import parse_block_id

    assert parse_block_id("0:3") == (0, 3)
    assert parse_block_id("15:99") == (15, 99)

    try:
        parse_block_id("abc")
        assert False, "should have raised"
    except ValueError:
        pass

    try:
        parse_block_id("0:xyz")
        assert False, "should have raised"
    except ValueError:
        pass


def test_expand_block_ids():
    """expand_block_ids expands wildcard and comma-separated IDs."""
    proj = _make_mock_proj()
    from utils.proj_compact import expand_block_ids

    # simple single
    assert expand_block_ids("0:0", proj) == ["0:0"]

    # comma separated
    assert expand_block_ids("0:0,0:1", proj) == ["0:0", "0:1"]

    # wildcard
    expanded = expand_block_ids("0:*", proj)
    assert expanded == ["0:0", "0:1", "0:2"]

    # wildcard on empty page
    assert expand_block_ids("1:*", proj) == []


def test_apply_modifications_translation():
    """Applying a modification changes the translation."""
    proj = _make_mock_proj()
    from utils.proj_compact import apply_modifications

    mod = {
        "type": "modifications",
        "changes": [{"id": "0:0", "trans": "こんにちは世界！"}],
    }
    changed, warnings = apply_modifications(proj, mod)
    assert changed == 1
    assert warnings == []
    assert proj.pages["01.jpg"][0].translation == "こんにちは世界！"


def test_apply_modifications_font():
    """Applying a font modification changes font fields."""
    proj = _make_mock_proj()
    from utils.proj_compact import apply_modifications

    mod = {"type": "modifications", "changes": [{"id": "0:0", "fs": 48, "ff": "黑体"}]}
    changed, _ = apply_modifications(proj, mod)
    assert changed == 1
    blk = proj.pages["01.jpg"][0]
    assert blk.font_size == 48
    assert blk.font_family == "黑体"


def test_sparse_patch_leaves_other_fields_unchanged():
    """Unspecified fields in a patch are not modified."""
    proj = _make_mock_proj()
    from utils.proj_compact import apply_modifications

    original_blk = copy.deepcopy(proj.pages["01.jpg"][0])
    mod = {
        "type": "modifications",
        "changes": [{"id": "0:0", "trans": "only translation changed"}],
    }
    apply_modifications(proj, mod)

    blk = proj.pages["01.jpg"][0]
    assert blk.translation == "only translation changed"
    # these should be unchanged
    assert blk.font_size == original_blk.font_size
    assert blk.font_family == original_blk.font_family
    assert blk.bold == original_blk.bold


def test_invalid_block_id_raises():
    """Invalid block ID raises InvalidModificationError."""
    proj = _make_mock_proj()
    from utils.proj_compact import InvalidModificationError, apply_modifications

    mod = {"type": "modifications", "changes": [{"id": "999:999", "trans": "x"}]}
    try:
        apply_modifications(proj, mod)
        assert False, "should have raised"
    except InvalidModificationError:
        pass


def test_stale_project_raises():
    """Stale hash mismatch raises StaleProjectError."""
    proj = _make_mock_proj()
    from utils.proj_compact import StaleProjectError, apply_modifications

    mod = {"type": "modifications", "changes": [{"id": "0:0", "trans": "x"}]}
    metadata = {"hash": "deadbeef"}  # wrong hash
    try:
        apply_modifications(proj, mod, metadata=metadata)
        assert False, "should have raised"
    except StaleProjectError:
        pass


def test_roundtrip():
    """Full roundtrip: export detail → apply modification → verify."""
    proj = _make_mock_proj()
    from utils.proj_compact import (
        apply_modifications,
        build_detail,
    )

    detail = build_detail(proj, [0])
    proj_hash = detail["meta"]["hash"]

    # Simulate AI response based on the detail
    mod = {
        "type": "modifications",
        "changes": [
            {"id": "0:0", "trans": "roundtrip test", "fs": 36},
        ],
    }

    changed, warnings = apply_modifications(proj, mod, metadata=detail["meta"])
    assert changed == 1
    assert warnings == []

    blk = proj.pages["01.jpg"][0]
    assert blk.translation == "roundtrip test"
    assert blk.font_size == 36


def test_paginated_detail():
    """Paginated detail splits pages into chunks."""
    proj = _make_mock_proj()
    from utils.proj_compact import build_paginated_detail

    chunks = build_paginated_detail(proj, [0, 1], max_pages_per_chunk=1)
    assert len(chunks) == 2
    assert len(chunks[0]["pages"]) == 1
    assert len(chunks[1]["pages"]) == 1


def test_proj_imgtrans_delegation():
    """ProjImgTrans delegation methods return correct data."""
    proj = _make_mock_proj()

    idx = proj.dump_compact_index()
    assert idx["type"] == "index"
    assert idx["total_pages"] == 2

    detail = proj.dump_compact_detail([0])
    assert detail["type"] == "detail"
    assert len(detail["pages"][0]["blocks"]) == 2

    changed, _ = proj.apply_compact_modifications(
        {
            "type": "modifications",
            "changes": [{"id": "0:1", "trans": "delegation test"}],
        }
    )
    assert changed == 1
    assert proj.pages["01.jpg"][1].translation == "delegation test"


# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    tests = [
        fn
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  PASS {test_fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL {test_fn.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
    if failed:
        sys.exit(1)
