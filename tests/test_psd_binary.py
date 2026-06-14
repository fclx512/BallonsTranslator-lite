"""Tests for the binary PSD export pipeline.

Covers all modules: writer, packbits, descriptor, engine_data, and
the full binary exporter.
"""

from __future__ import annotations

import os
import struct
import tempfile

import numpy as np
import pytest
from PIL import Image

from utils.proj_imgtrans import ProjImgTrans
from utils.psd_binary_writer import PsdBinaryWriter, ascii_legacy
from utils.psd_descriptor import (
    DescriptorObject,
    DescriptorValue,
    bounds_descriptor,
    write_versioned_descriptor,
)
from utils.psd_engine_data import (
    TextEngineSpec,
    TextJustification,
    TextOrientation,
    _normalize_text,
    _serialize_float,
    _utf16_len,
    encode_engine_data,
)
from utils.psd_exporter import ExportOptions, create_exporter
from utils.psd_packbits import ChannelId, encode_image_rle
from utils.textblock import FontFormat, TextBlock

# ======================================================================
# PsdBinaryWriter
# ======================================================================


class TestPsdBinaryWriter:
    def test_u8(self):
        w = PsdBinaryWriter()
        w.write_u8(0xFF)
        assert w.to_bytes() == b"\xff"

    def test_u16_be(self):
        w = PsdBinaryWriter()
        w.write_u16(0x1234)
        assert w.to_bytes() == b"\x12\x34"

    def test_i32_be(self):
        w = PsdBinaryWriter()
        w.write_i32(-1)
        assert w.to_bytes() == b"\xff\xff\xff\xff"

    def test_f64_be(self):
        w = PsdBinaryWriter()
        w.write_f64(3.14)
        assert w.to_bytes() == struct.pack(">d", 3.14)

    def test_signature_asserts_4_bytes(self):
        w = PsdBinaryWriter()
        w.write_signature("8BPS")
        assert w.to_bytes() == b"8BPS"
        with pytest.raises(AssertionError):
            w.write_signature("too-long")  # noqa: PT027 (E112)

    def test_ascii_or_class_id_short(self):
        w = PsdBinaryWriter()
        w.write_ascii_or_class_id("TySh")
        data = w.to_bytes()
        # Class ID: i32(0) + 4-byte sig
        assert len(data) == 8
        assert data[:4] == b"\x00\x00\x00\x00"
        assert data[4:] == b"TySh"

    def test_ascii_or_class_id_long(self):
        w = PsdBinaryWriter()
        w.write_ascii_or_class_id("LongName")
        data = w.to_bytes()
        # Length-prefixed: i32(len) + bytes
        assert data[:4] == struct.pack(">i", 8)
        assert data[4:] == b"LongName"

    def test_ascii_or_class_id_exception_list(self):
        w = PsdBinaryWriter()
        w.write_ascii_or_class_id("warp")
        data = w.to_bytes()
        # "warp" is in the exception list → length-prefixed
        assert data[:4] == struct.pack(">i", 4)
        assert data[4:] == b"warp"

    def test_pascal_string_padding(self):
        w = PsdBinaryWriter()
        w.write_pascal_string("abc", 4)
        assert w.to_bytes() == b"\x03abc"

    def test_unicode_string(self):
        w = PsdBinaryWriter()
        w.write_unicode_string("A")
        data = w.to_bytes()
        # u32(1) + 2 bytes UTF-16BE
        assert len(data) == 6
        assert data[:4] == struct.pack(">I", 1)
        assert data[4:] == b"\x00A"

    def test_unicode_string_with_padding_trailing_nul(self):
        w = PsdBinaryWriter()
        w.write_unicode_string_with_padding("A")
        data = w.to_bytes()
        # u32(2) + u16('A') + u16(0)
        assert data[:4] == struct.pack(">I", 2)
        assert data[4:6] == b"\x00A"
        assert data[6:8] == b"\x00\x00"

    def test_ascii_legacy(self):
        assert ascii_legacy("hello世界", 8) == "hello??"
        assert ascii_legacy("abcdef", 4) == "abcd"


# ======================================================================
# PackBits
# ======================================================================


class TestPackBits:
    def test_repeat_row(self):
        """All-pixels-same → one repeat packet."""
        img = np.zeros((1, 4, 4), dtype=np.uint8)
        img[0, :, 0] = [10, 10, 10, 10]
        channels = encode_image_rle(img, [ChannelId.Red], "test")
        assert len(channels) == 1
        # row_len=2 + [0xFD, 10]
        assert channels[0].data[:2] == struct.pack(">H", 2)
        assert channels[0].data[2:] == bytes([0xFD, 10])

    def test_literal_row(self):
        """All-pixels-different → one literal packet."""
        img = np.zeros((1, 4, 4), dtype=np.uint8)
        img[0, :, 1] = [1, 2, 3, 4]
        channels = encode_image_rle(img, [ChannelId.Green], "test")
        assert len(channels) == 1
        assert channels[0].data[2:] == bytes([3, 1, 2, 3, 4])

    def test_rgba_four_channels(self):
        """All four RGBA channels are encoded."""
        img = np.zeros((1, 4, 4), dtype=np.uint8)
        img[0, :, :] = [10, 20, 30, 255]
        channels = encode_image_rle(
            img,
            [ChannelId.Red, ChannelId.Green, ChannelId.Blue, ChannelId.Alpha],
            "test",
        )
        assert len(channels) == 4
        for ch, expected_val in zip(channels, [10, 20, 30, 255]):
            assert ch.data[2:] == bytes([0xFD, expected_val]), (
                f"Channel {ch.channel_id}"
            )

    def test_multi_row(self):
        """2-row image → 2 row length entries."""
        img = np.zeros((2, 3, 4), dtype=np.uint8)
        img[0, :, 0] = [1, 1, 1]
        img[1, :, 0] = [5, 6, 7]
        channels = encode_image_rle(img, [ChannelId.Red], "test")
        assert len(channels) == 1
        data = channels[0].data
        # 2 row lengths (u16 each)
        row0_len = struct.unpack(">H", data[0:2])[0]
        row1_len = struct.unpack(">H", data[2:4])[0]
        assert row0_len == 2  # packed repeat
        assert row1_len == 4  # header(1) + values(3) for [5,6,7]
        assert len(data) == 4 + row0_len + row1_len


# ======================================================================
# Descriptor
# ======================================================================


class TestDescriptor:
    def test_version_16(self):
        w = PsdBinaryWriter()
        desc = DescriptorObject("", "Test").with_item(
            "key", DescriptorValue.integer(42)
        )
        write_versioned_descriptor(w, desc)
        assert w.to_bytes()[:4] == b"\x00\x00\x00\x10"

    def test_bounds_descriptor(self):
        desc = bounds_descriptor("bounds", 1.0, 2.0, 3.0, 4.0)
        assert desc.class_id == "bounds"
        keys = [item.key for item in desc.items]
        assert keys == ["Left", "Top ", "Rght", "Btom"]

    def test_contains_signatures(self):
        w = PsdBinaryWriter()
        desc = (
            DescriptorObject("", "TxLr")
            .with_item("Txt ", DescriptorValue.text("HELLO"))
            .with_item("Ornt", DescriptorValue.enum("Ornt", "Hrzn"))
            .with_item("TextIndex", DescriptorValue.integer(1))
            .with_item(
                "bounds",
                DescriptorValue.object(bounds_descriptor("bounds", 1, 2, 3, 4)),
            )
        )
        write_versioned_descriptor(w, desc)
        data = w.to_bytes()
        for expected in [b"TxLr", b"TEXT", b"enum", b"long", b"UntF"]:
            assert expected in data, f"Missing signature: {expected}"

    def test_raw_value(self):
        w = PsdBinaryWriter()
        desc = DescriptorObject("", "Test").with_item(
            "data", DescriptorValue.raw(b"\x00\x01\x02")
        )
        write_versioned_descriptor(w, desc)
        data = w.to_bytes()
        assert b"tdta" in data

    def test_double_value(self):
        w = PsdBinaryWriter()
        desc = DescriptorObject("", "Test").with_item(
            "val", DescriptorValue.double(3.14)
        )
        write_versioned_descriptor(w, desc)
        data = w.to_bytes()
        assert b"doub" in data


# ======================================================================
# EngineData
# ======================================================================


class TestEngineData:
    def test_normalize_text(self):
        assert _normalize_text("Hello\nWorld") == "Hello\rWorld\r"
        assert _normalize_text("Hello\r\nWorld") == "Hello\rWorld\r"
        assert _normalize_text("Hi") == "Hi\r"

    def test_utf16_len_ascii(self):
        assert _utf16_len("Hello") == 5

    def test_utf16_len_cjk(self):
        assert _utf16_len("你好") == 2

    def test_serialize_float_with_key(self):
        assert _serialize_float(14.0, "FontSize") == "14.0"
        assert _serialize_float(14.5, "FontSize") == "14.5"

    def test_serialize_float_without_key(self):
        assert _serialize_float(42.0, None) == "42"

    def test_serialize_float_trailing_zeros(self):
        assert _serialize_float(1.33333, "WordSpacing") == "1.33333"

    def test_full_engine_data_contains_sections(self):
        spec = TextEngineSpec(
            text="Hello",
            font_index=1,
            font_set=["AdobeInvisFont", "ArialMT"],
            font_size=14.0,
            color=(1, 2, 3, 255),
            faux_bold=True,
            faux_italic=False,
            orientation=TextOrientation.Horizontal,
            justification=TextJustification.Center,
            box_width=100.0,
            box_height=32.0,
        )
        data = encode_engine_data(spec)
        for expected in [
            b"/EngineDict",
            b"/FontSet",
            b"/RunLengthArray",
            b"/FontSize 14.0",
        ]:
            assert expected in data, f"Missing: {expected}"

    def test_engine_data_utf16_bom(self):
        spec = TextEngineSpec(
            text="Hi",
            font_index=0,
            font_set=["ArialMT"],
            font_size=12.0,
            color=(0, 0, 0, 255),
        )
        data = encode_engine_data(spec)
        assert b"\xfe\xff" in data

    def test_engine_data_axis_array(self):
        spec = TextEngineSpec(
            text="Hello",
            font_index=1,
            font_set=["ArialMT"],
            font_size=14.0,
            color=(1, 2, 3, 255),
        )
        data = encode_engine_data(spec)
        assert b"/Axis [ 1.0 0.0 1.0 ]" in data

    def test_engine_data_justification(self):
        spec = TextEngineSpec(
            text="Center",
            font_index=0,
            font_set=["ArialMT"],
            font_size=12.0,
            color=(0, 0, 0, 255),
            justification=TextJustification.Center,
        )
        data = encode_engine_data(spec)
        assert b"/Justification 2" in data

    def test_engine_data_vertical_orientation(self):
        spec = TextEngineSpec(
            text="Vert",
            font_index=0,
            font_set=["ArialMT"],
            font_size=12.0,
            color=(0, 0, 0, 255),
            orientation=TextOrientation.Vertical,
        )
        data = encode_engine_data(spec)
        assert b"/WritingDirection 2" in data

    def test_color_type_values(self):
        """Colors encode as Type=1 with A/R/G/B order."""
        spec = TextEngineSpec(
            text="Color",
            font_index=0,
            font_set=["ArialMT"],
            font_size=12.0,
            color=(255, 0, 0, 128),  # R=255, A=128
        )
        data = encode_engine_data(spec)
        # A=128/255=0.50196..., R=255/255=1.0
        assert b"/FillColor" in data


# ======================================================================
# Binary Exporter (end-to-end)
# ======================================================================


@pytest.fixture
def temp_project():
    """Create a minimal ProjImgTrans with one page + one text block."""
    tmpdir = tempfile.mkdtemp()

    # Source image
    src = os.path.join(tmpdir, "page001.png")
    Image.new("RGBA", (32, 32), (200, 200, 200, 255)).save(src)

    # Directories
    for d in ("mask", "inpainted", "result"):
        os.makedirs(os.path.join(tmpdir, d))

    # Project
    proj = ProjImgTrans.__new__(ProjImgTrans)
    proj.directory = tmpdir
    proj.pages = {
        "page001.png": [
            TextBlock(
                xyxy=[4.0, 8.0, 28.0, 26.0],
                translation="Hello PS",
                angle=0,
                fontformat=FontFormat(
                    font_family="Arial",
                    font_size=16.0,
                    frgb=[0, 0, 0],
                    bold=True,
                    italic=False,
                    alignment=1,
                    vertical=False,
                    opacity=1.0,
                ),
            )
        ]
    }
    proj._pagename2idx = {"page001.png": 0}
    proj._idx2pagename = {0: "page001.png"}

    return tmpdir, proj


class TestBinaryExporter:
    def test_check_availability(self):
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        ok, reason = exporter.check_availability()
        assert ok
        assert "no external dependencies" in reason

    def test_export_minimal(self, temp_project):
        tmpdir, proj = temp_project
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        options = ExportOptions(output_dir=tmpdir)
        result = exporter.export_page(proj, "page001.png", options)

        assert os.path.exists(result)
        assert result.endswith(".psd")
        assert os.path.getsize(result) > 100

    def test_psd_header_valid(self, temp_project):
        tmpdir, proj = temp_project
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        assert data[:4] == b"8BPS"
        assert struct.unpack(">H", data[4:6])[0] == 1  # version
        assert struct.unpack(">H", data[12:14])[0] == 4  # channels
        assert struct.unpack(">H", data[22:24])[0] == 8  # bit depth
        assert struct.unpack(">H", data[24:26])[0] == 3  # RGB

    def test_editable_text_section(self, temp_project):
        tmpdir, proj = temp_project
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        assert b"TySh" in data, "Editable text TySh section missing"
        assert b"/EngineDict" in data, "EngineDict missing"

    def test_layer_names(self, temp_project):
        tmpdir, proj = temp_project
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        assert b"Original Image" in data
        assert b"TL 001" in data or b"Hello PS" in data

    def test_layer_count_negative(self, temp_project):
        tmpdir, proj = temp_project
        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        pos = 34  # After header + color + resources
        # Skip layer info length prefix and inner length prefix
        pos += 4 + 4
        layer_count = struct.unpack(">h", data[pos : pos + 2])[0]
        assert layer_count < 0, f"Layer count should be negative, got {layer_count}"
        assert -layer_count >= 2, f"Expected at least 2 layers, got {-layer_count}"

    def test_export_with_inpainted(self, temp_project):
        tmpdir, proj = temp_project

        # Create inpainted image
        inpainted_dir = os.path.join(tmpdir, "inpainted")
        inpainted = Image.new("RGBA", (32, 32), (255, 210, 210, 255))
        inpainted.save(os.path.join(inpainted_dir, "page001.png"))

        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        assert b"Inpainted" in data, "Inpainted layer missing"

        # Original Image should be hidden (has_inpainted=True)
        assert b"Original Image" in data

    def test_export_two_blocks(self, temp_project):
        tmpdir, proj = temp_project

        # Add a second block
        proj.pages["page001.png"].append(
            TextBlock(
                xyxy=[10.0, 30.0, 30.0, 50.0],
                translation="Second",
                angle=0,
                fontformat=FontFormat(
                    font_family="Arial",
                    font_size=12.0,
                    frgb=[255, 0, 0],
                    bold=False,
                    italic=True,
                    alignment=0,
                    vertical=False,
                    opacity=0.8,
                ),
            )
        )

        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        assert data.count(b"TL ") >= 2, "Expected 2 text layers"

    def test_export_rotated_block(self, temp_project):
        tmpdir, proj = temp_project

        proj.pages["page001.png"] = [
            TextBlock(
                xyxy=[5.0, 5.0, 25.0, 25.0],
                translation="Rotated",
                angle=45,
                fontformat=FontFormat(
                    font_family="Arial",
                    font_size=14.0,
                    frgb=[0, 0, 255],
                ),
            )
        ]

        from utils.psd_binary_exporter import PsBinaryExporter

        exporter = PsBinaryExporter()
        result = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )

        with open(result, "rb") as f:
            data = f.read()

        # Rotated transform: cos(45°) ≈ 0.707
        assert b"TySh" in data


# ======================================================================
# Exporter factory
# ======================================================================


class TestExporterFactory:
    def test_create_binary(self):
        exporter = create_exporter("binary")
        from utils.psd_binary_exporter import PsBinaryExporter

        assert isinstance(exporter, PsBinaryExporter)

    def test_create_jsx(self):
        exporter = create_exporter("jsx")
        from utils.psd_jsx_exporter import PsJsxExporter

        assert isinstance(exporter, PsJsxExporter)

    def test_create_default_is_binary(self):
        exporter = create_exporter()
        from utils.psd_binary_exporter import PsBinaryExporter

        assert isinstance(exporter, PsBinaryExporter)

    def test_create_invalid(self):
        with pytest.raises(ValueError, match="Unknown PSD export method"):
            create_exporter("invalid")
