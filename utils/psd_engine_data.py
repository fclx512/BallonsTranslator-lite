"""Photoshop text engine data (``/EngineDict``) serialization.

Port of Koharu's ``engine_data.rs``.

Produces the PostScript-style dictionary blob embedded inside the
``TySh`` descriptor's ``EngineData`` field.  This is what makes text
layers *editable* in Photoshop.

The output format is plain ASCII text (plus embedded UTF-16BE strings)
using ``<< >>`` for dicts, ``[ ]`` for arrays, ``/key`` for property
names, and ``(string)`` for strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Tuple

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class TextOrientation(IntEnum):
    Horizontal = 0
    Vertical = 2  # PS convention: 0 = horizontal, 2 = vertical


class TextJustification(IntEnum):
    Left = 0
    Right = 1
    Center = 2


# ------------------------------------------------------------------
# Input spec
# ------------------------------------------------------------------


@dataclass
class TextEngineSpec:
    """All information needed to generate the EngineData blob."""

    text: str
    font_index: int  # index into font_set
    font_set: List[str]  # PostScript font names
    font_size: float  # in points
    color: Tuple[int, int, int, int]  # (R, G, B, A) each 0-255
    faux_bold: bool = False
    faux_italic: bool = False
    orientation: TextOrientation = TextOrientation.Horizontal
    justification: TextJustification = TextJustification.Left
    box_width: float = 0.0
    box_height: float = 0.0


# ------------------------------------------------------------------
# Internal value tree
# ------------------------------------------------------------------


class _EV:
    """EngineValue — the PostScript-style value tree.

    Tagged variant: use class methods as constructors:
    ``_EV.int(42)``, ``_EV.float(14.0)``, ``_EV.bool(True)``,
    ``_EV.string("hello")``, ``_EV.array([...])``, ``_EV.dict([...])``.
    """

    def __init__(self, tag: str, payload: object):
        self._tag = tag
        self._payload = payload

    @classmethod
    def int(cls, v: int) -> _EV:
        return cls("int", v)

    @classmethod
    def float(cls, v: float) -> _EV:
        return cls("float", v)

    @classmethod
    def bool(cls, v: bool) -> _EV:
        return cls("bool", v)

    @classmethod
    def string(cls, v: str) -> _EV:
        return cls("string", v)

    @classmethod
    def array(cls, v: List[_EV]) -> _EV:
        return cls("array", v)

    @classmethod
    def dict(cls, v: List[Tuple[str, _EV]]) -> _EV:
        return cls("dict", v)


# ======================================================================
# Public entry point
# ======================================================================


def encode_engine_data(spec: TextEngineSpec) -> bytes:
    """Generate the full EngineData blob.

    Returns a ``bytes`` object containing the PostScript-style dictionary
    terminated by a newline.
    """
    text = _normalize_text(spec.text)
    par_lengths = _paragraph_run_lengths(text)
    total_len = _utf16_len(text)

    font_idx = spec.font_index
    writing_dir = int(spec.orientation)  # 0 or 2
    procession = 0 if spec.orientation == TextOrientation.Horizontal else 1

    par_props = _paragraph_properties(spec.justification)
    base_ss = _base_style_sheet(font_idx)
    style_run = _style_run_sheet(spec, font_idx)
    font_set_arr = [_font_descriptor(name) for name in spec.font_set]

    # Build each paragraph run entry (one per paragraph)
    par_run_array = [
        _EV.dict(
            [
                (
                    "ParagraphSheet",
                    _EV.dict(
                        [
                            ("DefaultStyleSheet", _EV.int(0)),
                            ("Properties", _EV.dict(par_props)),
                        ]
                    ),
                ),
                (
                    "Adjustments",
                    _EV.dict(
                        [
                            (
                                "Axis",
                                _EV.array(
                                    [
                                        _EV.float(1.0),
                                        _EV.float(0.0),
                                        _EV.float(1.0),
                                    ]
                                ),
                            ),
                            (
                                "XY",
                                _EV.array(
                                    [
                                        _EV.float(0.0),
                                        _EV.float(0.0),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
            ]
        )
        for _ in par_lengths
    ]

    root = _EV.dict(
        [
            (
                "EngineDict",
                _EV.dict(
                    [
                        (
                            "Editor",
                            _EV.dict(
                                [
                                    ("Text", _EV.string(text)),
                                ]
                            ),
                        ),
                        (
                            "ParagraphRun",
                            _EV.dict(
                                [
                                    (
                                        "DefaultRunData",
                                        _EV.dict(
                                            [
                                                (
                                                    "ParagraphSheet",
                                                    _EV.dict(
                                                        [
                                                            (
                                                                "DefaultStyleSheet",
                                                                _EV.int(0),
                                                            ),
                                                            (
                                                                "Properties",
                                                                _EV.dict([]),
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                                (
                                                    "Adjustments",
                                                    _EV.dict(
                                                        [
                                                            (
                                                                "Axis",
                                                                _EV.array(
                                                                    [
                                                                        _EV.float(1.0),
                                                                        _EV.float(0.0),
                                                                        _EV.float(1.0),
                                                                    ]
                                                                ),
                                                            ),
                                                            (
                                                                "XY",
                                                                _EV.array(
                                                                    [
                                                                        _EV.float(0.0),
                                                                        _EV.float(0.0),
                                                                    ]
                                                                ),
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                            ]
                                        ),
                                    ),
                                    ("RunArray", _EV.array(par_run_array)),
                                    (
                                        "RunLengthArray",
                                        _EV.array(
                                            [_EV.int(length) for length in par_lengths]
                                        ),
                                    ),
                                    ("IsJoinable", _EV.int(1)),
                                ]
                            ),
                        ),
                        (
                            "StyleRun",
                            _EV.dict(
                                [
                                    (
                                        "DefaultRunData",
                                        _EV.dict(
                                            [
                                                (
                                                    "StyleSheet",
                                                    _EV.dict(
                                                        [
                                                            (
                                                                "StyleSheetData",
                                                                _EV.dict([]),
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                            ]
                                        ),
                                    ),
                                    (
                                        "RunArray",
                                        _EV.array(
                                            [
                                                _EV.dict(
                                                    [
                                                        (
                                                            "StyleSheet",
                                                            _EV.dict(
                                                                [
                                                                    (
                                                                        "StyleSheetData",
                                                                        _EV.dict(
                                                                            style_run
                                                                        ),
                                                                    ),
                                                                ]
                                                            ),
                                                        ),
                                                    ]
                                                ),
                                            ]
                                        ),
                                    ),
                                    (
                                        "RunLengthArray",
                                        _EV.array(
                                            [
                                                _EV.int(total_len),
                                            ]
                                        ),
                                    ),
                                    ("IsJoinable", _EV.int(2)),
                                ]
                            ),
                        ),
                        (
                            "GridInfo",
                            _EV.dict(
                                [
                                    ("GridIsOn", _EV.bool(False)),
                                    ("ShowGrid", _EV.bool(False)),
                                    ("GridSize", _EV.float(18.0)),
                                    ("GridLeading", _EV.float(22.0)),
                                    (
                                        "GridColor",
                                        _EV.dict(_color_type_values([0, 0, 255, 255])),
                                    ),
                                    (
                                        "GridLeadingFillColor",
                                        _EV.dict(_color_type_values([0, 0, 255, 255])),
                                    ),
                                    ("AlignLineHeightToGridFlags", _EV.bool(False)),
                                ]
                            ),
                        ),
                        ("AntiAlias", _EV.int(4)),
                        ("UseFractionalGlyphWidths", _EV.bool(True)),
                        (
                            "Rendered",
                            _EV.dict(
                                [
                                    ("Version", _EV.int(1)),
                                    (
                                        "Shapes",
                                        _EV.dict(
                                            [
                                                (
                                                    "WritingDirection",
                                                    _EV.int(writing_dir),
                                                ),
                                                (
                                                    "Children",
                                                    _EV.array(
                                                        [
                                                            _EV.dict(
                                                                [
                                                                    (
                                                                        "ShapeType",
                                                                        _EV.int(1),
                                                                    ),
                                                                    (
                                                                        "Procession",
                                                                        _EV.int(
                                                                            procession
                                                                        ),
                                                                    ),
                                                                    (
                                                                        "Lines",
                                                                        _EV.dict(
                                                                            [
                                                                                (
                                                                                    "WritingDirection",
                                                                                    _EV.int(
                                                                                        writing_dir
                                                                                    ),
                                                                                ),
                                                                                (
                                                                                    "Children",
                                                                                    _EV.array(
                                                                                        []
                                                                                    ),
                                                                                ),
                                                                            ]
                                                                        ),
                                                                    ),
                                                                    (
                                                                        "Cookie",
                                                                        _EV.dict(
                                                                            [
                                                                                (
                                                                                    "Photoshop",
                                                                                    _EV.dict(
                                                                                        [
                                                                                            (
                                                                                                "ShapeType",
                                                                                                _EV.int(
                                                                                                    1
                                                                                                ),
                                                                                            ),
                                                                                            (
                                                                                                "BoxBounds",
                                                                                                _EV.array(
                                                                                                    [
                                                                                                        _EV.float(
                                                                                                            0.0
                                                                                                        ),
                                                                                                        _EV.float(
                                                                                                            0.0
                                                                                                        ),
                                                                                                        _EV.float(
                                                                                                            spec.box_width
                                                                                                        ),
                                                                                                        _EV.float(
                                                                                                            spec.box_height
                                                                                                        ),
                                                                                                    ]
                                                                                                ),
                                                                                            ),
                                                                                            (
                                                                                                "Base",
                                                                                                _EV.dict(
                                                                                                    [
                                                                                                        (
                                                                                                            "ShapeType",
                                                                                                            _EV.int(
                                                                                                                1
                                                                                                            ),
                                                                                                        ),
                                                                                                        (
                                                                                                            "TransformPoint0",
                                                                                                            _EV.array(
                                                                                                                [
                                                                                                                    _EV.float(
                                                                                                                        1.0
                                                                                                                    ),
                                                                                                                    _EV.float(
                                                                                                                        0.0
                                                                                                                    ),
                                                                                                                ]
                                                                                                            ),
                                                                                                        ),
                                                                                                        (
                                                                                                            "TransformPoint1",
                                                                                                            _EV.array(
                                                                                                                [
                                                                                                                    _EV.float(
                                                                                                                        0.0
                                                                                                                    ),
                                                                                                                    _EV.float(
                                                                                                                        1.0
                                                                                                                    ),
                                                                                                                ]
                                                                                                            ),
                                                                                                        ),
                                                                                                        (
                                                                                                            "TransformPoint2",
                                                                                                            _EV.array(
                                                                                                                [
                                                                                                                    _EV.float(
                                                                                                                        0.0
                                                                                                                    ),
                                                                                                                    _EV.float(
                                                                                                                        0.0
                                                                                                                    ),
                                                                                                                ]
                                                                                                            ),
                                                                                                        ),
                                                                                                    ]
                                                                                                ),
                                                                                            ),
                                                                                        ]
                                                                                    ),
                                                                                ),
                                                                            ]
                                                                        ),
                                                                    ),
                                                                ]
                                                            ),
                                                        ]
                                                    ),
                                                ),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                    ]
                ),
            ),
            (
                "ResourceDict",
                _EV.dict(
                    _resource_dict(
                        font_set_arr,
                        par_props,
                        base_ss,
                    )
                ),
            ),
            (
                "DocumentResources",
                _EV.dict(
                    _resource_dict(
                        font_set_arr,
                        par_props,
                        base_ss,
                    )
                ),
            ),
        ]
    )

    out = bytearray()
    _write_value(out, root, 0, False, None)
    out.append(ord("\n"))
    return bytes(out)


# ======================================================================
# Text helpers
# ======================================================================


def _normalize_text(text: str) -> str:
    """Convert line endings to Photoshop convention (``\\r``)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
    return f"{normalized}\r"


def _paragraph_run_lengths(text: str) -> List[int]:
    """Return UTF-16 code-unit counts per paragraph (split on ``\\r``)."""
    lengths: List[int] = []
    for run in text.split("\r"):
        lengths.append(_utf16_len(run))
    return lengths


def _utf16_len(text: str) -> int:
    """Number of UTF-16 code units in *text*."""
    return len(text.encode("utf-16-le")) // 2


# ======================================================================
# Tree construction helpers
# ======================================================================


def _paragraph_properties(justification: TextJustification) -> List[Tuple[str, _EV]]:
    return [
        ("Justification", _EV.int(int(justification))),
        ("FirstLineIndent", _EV.float(0.0)),
        ("StartIndent", _EV.float(0.0)),
        ("EndIndent", _EV.float(0.0)),
        ("SpaceBefore", _EV.float(0.0)),
        ("SpaceAfter", _EV.float(0.0)),
        ("AutoHyphenate", _EV.bool(True)),
        ("HyphenatedWordSize", _EV.int(6)),
        ("PreHyphen", _EV.int(2)),
        ("PostHyphen", _EV.int(2)),
        ("ConsecutiveHyphens", _EV.int(8)),
        ("Zone", _EV.float(36.0)),
        (
            "WordSpacing",
            _EV.array(
                [
                    _EV.float(0.8),
                    _EV.float(1.0),
                    _EV.float(1.33),
                ]
            ),
        ),
        (
            "LetterSpacing",
            _EV.array(
                [
                    _EV.float(0.0),
                    _EV.float(0.0),
                    _EV.float(0.0),
                ]
            ),
        ),
        (
            "GlyphSpacing",
            _EV.array(
                [
                    _EV.float(1.0),
                    _EV.float(1.0),
                    _EV.float(1.0),
                ]
            ),
        ),
        ("AutoLeading", _EV.float(1.2)),
        ("LeadingType", _EV.int(0)),
        ("Hanging", _EV.bool(False)),
        ("Burasagari", _EV.bool(False)),
        ("KinsokuOrder", _EV.int(0)),
        ("EveryLineComposer", _EV.bool(False)),
    ]


def _base_style_sheet(font_index: int) -> List[Tuple[str, _EV]]:
    return [
        ("Font", _EV.int(font_index)),
        ("FontSize", _EV.float(12.0)),
        ("FauxBold", _EV.bool(False)),
        ("FauxItalic", _EV.bool(False)),
        ("AutoLeading", _EV.bool(True)),
        ("Leading", _EV.float(0.0)),
        ("HorizontalScale", _EV.float(1.0)),
        ("VerticalScale", _EV.float(1.0)),
        ("Tracking", _EV.int(0)),
        ("AutoKerning", _EV.bool(True)),
        ("Kerning", _EV.int(0)),
        ("BaselineShift", _EV.float(0.0)),
        ("FontCaps", _EV.int(0)),
        ("FontBaseline", _EV.int(0)),
        ("Underline", _EV.bool(False)),
        ("Strikethrough", _EV.bool(False)),
        ("Ligatures", _EV.bool(True)),
        ("DLigatures", _EV.bool(False)),
        ("BaselineDirection", _EV.int(2)),
        ("Tsume", _EV.float(0.0)),
        ("StyleRunAlignment", _EV.int(2)),
        ("Language", _EV.int(0)),
        ("NoBreak", _EV.bool(False)),
        ("FillColor", _EV.dict(_color_type_values([0, 0, 0, 255]))),
        ("StrokeColor", _EV.dict(_color_type_values([0, 0, 0, 255]))),
        ("FillFlag", _EV.bool(True)),
        ("StrokeFlag", _EV.bool(False)),
        ("FillFirst", _EV.bool(True)),
        ("YUnderline", _EV.int(1)),
        ("OutlineWidth", _EV.float(1.0)),
        ("CharacterDirection", _EV.int(0)),
        ("HindiNumbers", _EV.bool(False)),
        ("Kashida", _EV.int(1)),
        ("DiacriticPos", _EV.int(2)),
    ]


def _style_run_sheet(spec: TextEngineSpec, font_index: int) -> List[Tuple[str, _EV]]:
    r, g, b, a = spec.color
    return [
        ("Font", _EV.int(font_index)),
        ("FontSize", _EV.float(spec.font_size)),
        ("FauxBold", _EV.bool(spec.faux_bold)),
        ("FauxItalic", _EV.bool(spec.faux_italic)),
        ("AutoKerning", _EV.bool(True)),
        ("Kerning", _EV.int(0)),
        ("FillColor", _EV.dict(_color_type_values(spec.color))),
    ]


def _resource_dict(
    font_set: List[_EV],
    paragraph_properties: List[Tuple[str, _EV]],
    style_sheet: List[Tuple[str, _EV]],
) -> List[Tuple[str, _EV]]:
    """Build the shared ``/ResourceDict`` / ``/DocumentResources`` subtree."""
    return [
        (
            "KinsokuSet",
            _EV.array(
                [
                    _EV.dict(
                        [
                            ("Name", _EV.string("PhotoshopKinsokuHard")),
                            (
                                "NoStart",
                                _EV.string(
                                    "、。，．・：；？！"
                                    "ー―’”）〕］｝〉"
                                    "》」』】ヽヾゝゞ々"
                                    "ぁぃぅぇぉっゃゅょ"
                                    "ゎァィゥェォッャュ"
                                    "ョヮヵヶ゛゜?!)"
                                    "]},.:;℃℉¢％‰"
                                ),
                            ),
                            (
                                "NoEnd",
                                _EV.string("‘“（〔［｛〈《「『【([{￥＄£＠§〒＃"),
                            ),
                            ("Keep", _EV.string("―‥")),
                            ("Hanging", _EV.string("、。.,")),
                        ]
                    ),
                    _EV.dict(
                        [
                            ("Name", _EV.string("PhotoshopKinsokuSoft")),
                            (
                                "NoStart",
                                _EV.string(
                                    "、。，．・：；？！’”）〕］｝〉》」』】ヽヾゝゞ々"
                                ),
                            ),
                            ("NoEnd", _EV.string("‘“（〔［｛〈《「『【")),
                            ("Keep", _EV.string("―‥")),
                            ("Hanging", _EV.string("、。.,")),
                        ]
                    ),
                ]
            ),
        ),
        (
            "MojiKumiSet",
            _EV.array(
                [
                    _EV.dict([("InternalName", _EV.string("Photoshop6MojiKumiSet1"))]),
                    _EV.dict([("InternalName", _EV.string("Photoshop6MojiKumiSet2"))]),
                    _EV.dict([("InternalName", _EV.string("Photoshop6MojiKumiSet3"))]),
                    _EV.dict([("InternalName", _EV.string("Photoshop6MojiKumiSet4"))]),
                ]
            ),
        ),
        ("TheNormalStyleSheet", _EV.int(0)),
        ("TheNormalParagraphSheet", _EV.int(0)),
        (
            "ParagraphSheetSet",
            _EV.array(
                [
                    _EV.dict(
                        [
                            ("Name", _EV.string("Normal RGB")),
                            ("DefaultStyleSheet", _EV.int(0)),
                            ("Properties", _EV.dict(paragraph_properties)),
                        ]
                    ),
                ]
            ),
        ),
        (
            "StyleSheetSet",
            _EV.array(
                [
                    _EV.dict(
                        [
                            ("Name", _EV.string("Normal RGB")),
                            ("StyleSheetData", _EV.dict(style_sheet)),
                        ]
                    ),
                ]
            ),
        ),
        ("FontSet", _EV.array(font_set)),
        ("SuperscriptSize", _EV.float(0.583)),
        ("SuperscriptPosition", _EV.float(0.333)),
        ("SubscriptSize", _EV.float(0.583)),
        ("SubscriptPosition", _EV.float(0.333)),
        ("SmallCapSize", _EV.float(0.7)),
    ]


def _font_descriptor(name: str) -> _EV:
    return _EV.dict(
        [
            ("Name", _EV.string(name)),
            ("Script", _EV.int(0)),
            ("FontType", _EV.int(0)),
            ("Synthetic", _EV.int(0)),
        ]
    )


def _color_type_values(color: Tuple[int, int, int, int]) -> List[Tuple[str, _EV]]:
    """RGBA → (Type=1, Values=[A/255, R/255, G/255, B/255])."""
    r, g, b, a = color
    return [
        ("Type", _EV.int(1)),
        (
            "Values",
            _EV.array(
                [
                    _EV.float(a / 255.0),
                    _EV.float(r / 255.0),
                    _EV.float(g / 255.0),
                    _EV.float(b / 255.0),
                ]
            ),
        ),
    ]


# ======================================================================
# Serialization
# ======================================================================

_FLOAT_KEYS = frozenset(
    {
        "Axis",
        "XY",
        "Zone",
        "WordSpacing",
        "FirstLineIndent",
        "GlyphSpacing",
        "StartIndent",
        "EndIndent",
        "SpaceBefore",
        "SpaceAfter",
        "LetterSpacing",
        "Values",
        "GridSize",
        "GridLeading",
        "PointBase",
        "BoxBounds",
        "TransformPoint0",
        "TransformPoint1",
        "TransformPoint2",
        "FontSize",
        "Leading",
        "HorizontalScale",
        "VerticalScale",
        "BaselineShift",
        "Tsume",
        "OutlineWidth",
        "AutoLeading",
    }
)


def _serialize_float(value: float, key: Optional[str]) -> str:
    """Format a float for EngineData output.

    Known float-keys always produce decimal notation (e.g. ``14.0``,
    ``1.33333``).  Other keys use integer notation (e.g. ``1``) when
    the value has no fractional part.  Trailing zeros are stripped.
    """
    is_float = (key in _FLOAT_KEYS) or (value % 1.0 != 0.0)

    if not is_float:
        return str(int(value))

    formatted = f"{value:.5f}"
    if "." in formatted:
        # Strip trailing zeros (but keep at least one digit after dot)
        while formatted.endswith("0") and len(formatted) > formatted.index(".") + 2:
            formatted = formatted[:-1]
    return formatted


def _write_value(
    out: bytearray,
    value: _EV,
    indent: int,
    in_property: bool,
    key: Optional[str],
) -> None:
    tag = value._tag
    payload = value._payload

    if tag == "int":
        _write_prefix(out, indent, in_property)
        out.extend(str(payload).encode("ascii"))

    elif tag == "float":
        _write_prefix(out, indent, in_property)
        out.extend(_serialize_float(payload, key).encode("ascii"))

    elif tag == "bool":
        _write_prefix(out, indent, in_property)
        out.extend(b"true" if payload else b"false")

    elif tag == "string":
        _write_prefix(out, indent, in_property)
        _write_ps_string(out, payload)

    elif tag == "array":
        items: List[_EV] = payload
        _write_prefix(out, indent, in_property)
        if all(_is_scalar(item) for item in items):
            # Inline array: [ val1 val2 val3 ]
            out.extend(b"[")
            for item in items:
                out.extend(b" ")
                _write_inline_value(out, item, key)
            out.extend(b" ]")
        else:
            # Multi-line array
            out.extend(b"[\n")
            for item in items:
                _write_value(out, item, indent + 1, False, key)
                out.append(ord("\n"))
            _write_indent(out, indent)
            out.extend(b"]")

    elif tag == "dict":
        entries: List[Tuple[str, _EV]] = payload
        if in_property:
            out.append(ord("\n"))
        else:
            _write_indent(out, indent)
        out.extend(b"<<\n")
        for entry_key, entry_value in entries:
            _write_indent(out, indent + 1)
            out.append(ord("/"))
            out.extend(entry_key.encode("ascii"))
            _write_value(out, entry_value, indent + 1, True, entry_key)
            out.append(ord("\n"))
        _write_indent(out, indent)
        out.extend(b">>")


def _write_inline_value(out: bytearray, value: _EV, key: Optional[str]) -> None:
    tag = value._tag
    payload = value._payload
    if tag == "int":
        out.extend(str(payload).encode("ascii"))
    elif tag == "float":
        out.extend(_serialize_float(payload, key).encode("ascii"))
    elif tag == "bool":
        out.extend(b"true" if payload else b"false")
    else:
        _write_value(out, value, 0, False, key)


def _write_prefix(out: bytearray, indent: int, in_property: bool) -> None:
    if in_property:
        out.extend(b" ")
    else:
        _write_indent(out, indent)


def _write_indent(out: bytearray, indent: int) -> None:
    out.extend(b"\t" * indent)


def _write_ps_string(out: bytearray, text: str) -> None:
    """Write a PostScript-style UTF-16BE encoded string.

    Format: ``(`` + ``\\xFE\\xFF`` + UTF-16BE (with ``(``, ``)``, ``\\``
    escaped) + ``)``.
    """
    out.append(ord("("))
    out.extend(b"\xfe\xff")
    for unit in text.encode("utf-16-be"):
        _write_escaped_byte(out, unit)
    out.append(ord(")"))


def _write_escaped_byte(out: bytearray, byte: int) -> None:
    if byte in (ord("("), ord(")"), ord("\\")):
        out.append(ord("\\"))
    out.append(byte)


def _is_scalar(value: _EV) -> bool:
    return value._tag in ("int", "float", "bool", "string")
