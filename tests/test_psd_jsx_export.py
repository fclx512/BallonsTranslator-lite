"""Tests for the batch ExtendScript (.jsx) PSD exporter.

The generated .jsx is meant to run inside Photoshop; there is no PS in CI,
so we verify the payload construction and the emitted script text.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

import pytest
from PIL import Image

from utils.fontformat import FontFormat, LineSpacingType
from utils.proj_imgtrans import ProjImgTrans
from utils.psd_exporter import ExportOptions, create_exporter
from utils.psd_jsx_exporter import PsJsxExporter
from utils.textblock import TextBlock


# ======================================================================
# helpers
# ======================================================================


def _make_proj(tmpdir: str, pages: dict) -> ProjImgTrans:
    proj = ProjImgTrans.__new__(ProjImgTrans)
    proj.type = "test"
    proj.directory = tmpdir
    proj.pages = pages
    return proj


def _blk(translation="你好\n世界", font_size=16.0, angle=15, **ff_over) -> TextBlock:
    ff_kwargs = {
        "font_family": "Arial",
        "font_size": font_size,
        "frgb": [10, 20, 30],
        "stroke_width": 0.5,
        "vertical": False,
    }
    ff_kwargs.update(ff_over)
    ff = FontFormat(**ff_kwargs)
    return TextBlock(
        xyxy=[4.0, 8.0, 100.0, 60.0],
        translation=translation,
        angle=angle,
        fontformat=ff,
    )


def _make_image(tmpdir: str, name="page001.png", dpi=(150, 150)) -> str:
    path = os.path.join(tmpdir, name)
    Image.new("RGBA", (64, 64), (200, 200, 200, 255)).save(path, dpi=dpi)
    return path


# ======================================================================
# Payload construction
# ======================================================================


class TestBlockPayload:
    def test_core_fields(self):
        blk = _blk()
        p = PsJsxExporter._block_payload(blk, 1, dpi=150.0, center=True)

        assert p["text"] == "你好\n世界"  # multi-line preserved
        assert p["font"] == "Arial"
        assert p["size_pt"] == round(16.0 * 72.0 / 150.0, 2)  # px → pt via dpi
        assert p["color"] == [10, 20, 30]
        assert p["bold"] is False
        assert p["rotation"] == 15.0  # passthrough (CCW+ convention)
        assert p["center"] is True
        assert p["box"] == [4.0, 8.0, 100.0, 60.0]
        assert p["line_spacing"] == 120.0  # default 1.2 proportional → 120%
        assert p["stroke_size"] == 8.0  # font_size × stroke_width
        assert p["stroke_color"] == [0, 0, 0]
        assert p["shadow_blur"] == 0.0
        assert p["shadow_opacity"] == 0

    def test_stroke_disabled_when_no_width(self):
        blk = _blk(stroke_width=0.0)
        p = PsJsxExporter._block_payload(blk, 1, dpi=96.0, center=False)
        assert p["stroke_size"] == 0.0
        assert p["center"] is False

    def test_vertical_rotation_passthrough(self):
        # Vertical blocks already carry their rotation on top of the vertical
        # layout (blk.angle = rotation_angle - 90) — no extra correction.
        blk = _blk(vertical=True, angle=5)
        p = PsJsxExporter._block_payload(blk, 1, dpi=96.0, center=True)
        assert p["vertical"] is True
        assert p["rotation"] == 5.0

    def test_line_spacing_distance_type(self):
        # Distance-type spacing is stored in px; convert to a percentage of
        # the font size for PS auto-leading.
        blk = _blk(line_spacing=4.0, line_spacing_type=LineSpacingType.Distance)
        p = PsJsxExporter._block_payload(blk, 1, dpi=96.0, center=True)
        assert p["line_spacing"] == 25.0  # 4 / 16 * 100

    def test_leading_whitespace_stripped(self):
        blk = _blk(translation="  译 文  \n")
        p = PsJsxExporter._block_payload(blk, 1, dpi=96.0, center=True)
        assert p["text"] == "译 文"


class TestBuildPages:
    def test_paths_use_forward_slashes(self):
        tmpdir = tempfile.mkdtemp()
        _make_image(tmpdir)
        proj = _make_proj(tmpdir, {"page001.png": [_blk()]})
        pages = PsJsxExporter._build_pages(
            proj, ["page001.png"], ExportOptions(output_dir=tmpdir)
        )
        assert len(pages) == 1
        page = pages[0]
        assert "\\" not in page["path"]
        assert page["path"].endswith("page001.png")
        assert page["out_path"].endswith("page001.psd")
        assert page["dpi"] == pytest.approx(150.0, abs=1.0)  # pHYs rounding
        assert len(page["blocks"]) == 1

    def test_empty_translation_skipped(self):
        tmpdir = tempfile.mkdtemp()
        _make_image(tmpdir)
        proj = _make_proj(
            tmpdir,
            {"page001.png": [_blk(translation="   "), _blk(translation="ok")]},
        )
        pages = PsJsxExporter._build_pages(
            proj, ["page001.png"], ExportOptions(output_dir=tmpdir)
        )
        assert len(pages[0]["blocks"]) == 1
        assert pages[0]["blocks"][0]["text"] == "ok"


# ======================================================================
# Script emission
# ======================================================================


class TestExportBatch:
    def _export(self, tmpdir: str) -> str:
        _make_image(tmpdir)
        proj = _make_proj(tmpdir, {"page001.png": [_blk()]})
        exporter = PsJsxExporter()
        return exporter.export_batch(
            proj, ["page001.png"], ExportOptions(output_dir=tmpdir)
        )

    def test_writes_jsx_with_bom(self):
        tmpdir = tempfile.mkdtemp()
        jsx_path = self._export(tmpdir)

        with open(jsx_path, "rb") as f:
            head = f.read(3)
        assert head == b"\xef\xbb\xbf"  # UTF-8 BOM for ExtendScript

    def test_script_is_self_contained(self):
        tmpdir = tempfile.mkdtemp()
        jsx_path = self._export(tmpdir)

        with open(jsx_path, "r", encoding="utf-8-sig") as f:
            script = f.read()

        assert script.startswith("#target photoshop")
        assert "var DATA = {" in script
        # No leftover Template placeholders / literal '$' anywhere.
        assert "$" not in script

        m = re.search(r"var DATA = (\{.*\});", script, re.S)
        assert m is not None
        data = json.loads(m.group(1))
        assert data["pages"][0]["name"] == "page001.png"
        assert data["pages"][0]["blocks"][0]["text"] == "你好\n世界"

    def test_batch_covers_all_pages(self):
        tmpdir = tempfile.mkdtemp()
        _make_image(tmpdir, "page001.png")
        _make_image(tmpdir, "page002.png")
        proj = _make_proj(
            tmpdir,
            {
                "page001.png": [_blk(translation="one")],
                "page002.png": [_blk(translation="two")],
            },
        )
        exporter = PsJsxExporter()
        jsx_path = exporter.export_batch(
            proj, ["page001.png", "page002.png"], ExportOptions(output_dir=tmpdir)
        )

        with open(jsx_path, "r", encoding="utf-8-sig") as f:
            script = f.read()
        assert "page001.psd" in script
        assert "page002.psd" in script


class TestExportPageCompat:
    def test_single_page(self):
        tmpdir = tempfile.mkdtemp()
        _make_image(tmpdir)
        proj = _make_proj(tmpdir, {"page001.png": [_blk()]})
        exporter = PsJsxExporter()
        path = exporter.export_page(
            proj, "page001.png", ExportOptions(output_dir=tmpdir)
        )
        assert os.path.exists(path)
        assert path.endswith(".jsx")


# ======================================================================
# Options / factory
# ======================================================================


class TestOptionsAndFactory:
    def test_export_options_defaults(self):
        opts = ExportOptions(output_dir="x")
        assert opts.export_method == "jsx"
        assert opts.center_align is True

    def test_factory_jsx(self):
        exporter = create_exporter("jsx")
        assert isinstance(exporter, PsJsxExporter)

    def test_factory_default_is_jsx(self):
        exporter = create_exporter()
        assert isinstance(exporter, PsJsxExporter)
