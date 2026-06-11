"""Direct binary PSD exporter with editable text layers.

Port of Koharu's ``export.rs``.

Assembles a complete Photoshop PSD file from a BallonsTranslator project
page, including:

- Source image, inpainted, mask, and rendered layers
- Editable (TySh + EngineData) text layers
- Merged composite image
- RLE-compressed channel data
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import numpy as np
from PIL import Image as PILImage

from .font_mapping import resolve_font_name
from .proj_imgtrans import ProjImgTrans
from .psd_binary_writer import PsdBinaryWriter
from .psd_descriptor import (
    DescriptorObject,
    DescriptorValue,
    bounds_descriptor,
    write_versioned_descriptor,
)
from .psd_engine_data import (
    TextEngineSpec,
    TextJustification,
    TextOrientation,
    encode_engine_data,
)
from .psd_exporter import AbstractPsdExporter, ExportOptions
from .psd_packbits import ChannelId, encode_image_rle

# ------------------------------------------------------------------
# Internal data types
# ------------------------------------------------------------------


@dataclass
class TextLayerMetadata:
    """Metadata required to build the ``TySh`` section for editable text."""

    index: int
    text: str
    bounds: Tuple[float, float, float, float]  # left, top, right, bottom
    transform: Tuple[float, float, float, float, float, float]  # [cos, sin, -sin, cos, tx, ty]
    orientation: TextOrientation
    justification: TextJustification
    font_index: int
    font_set: List[str]
    font_size: float  # in points
    color: Tuple[int, int, int, int]  # (R, G, B, A)
    faux_bold: bool
    faux_italic: bool
    box_width: float
    box_height: float


@dataclass
class ExportLayer:
    """One layer in the PSD file."""

    id: int = 0
    name: str = ""
    left: int = 0
    top: int = 0
    pixels: Optional[np.ndarray] = None  # (H, W, 4) uint8 RGBA
    hidden: bool = False
    text: Optional[TextLayerMetadata] = None


# ======================================================================
# Exporter
# ======================================================================


class PsBinaryExporter(AbstractPsdExporter):
    """Produce a binary .psd file directly, no Photoshop dependency."""

    # ------------------------------------------------------------------
    # AbstractPsdExporter interface
    # ------------------------------------------------------------------

    def check_availability(self, passive: bool = False) -> Tuple[bool, str]:
        return True, "Binary PSD is generated directly — no external dependencies"

    def get_available_fonts(self) -> Set[str]:
        return set()

    def export_page(
        self,
        proj: ProjImgTrans,
        page_name: str,
        options: ExportOptions,
    ) -> str:
        os.makedirs(options.output_dir, exist_ok=True)

        src_path = os.path.join(proj.directory, page_name)

        # 1. Read source image → determine DPI, dimensions
        with PILImage.open(src_path) as pil_img:
            width, height = pil_img.size
            dpi_info = pil_img.info.get("dpi")
            dpi = max(dpi_info[0], dpi_info[1], 72.0) if dpi_info and len(dpi_info) >= 2 else 96.0

        self._validate_dimensions(width, height, page_name)

        # 2. Collect layers
        layers_bottom_to_top = self._collect_layers(proj, page_name, dpi)

        # 3. Build merged composite
        composite = self._merged_composite(proj, page_name, layers_bottom_to_top, width, height)

        # 4. Assemble PSD binary
        psd = PsdBinaryWriter()
        self._write_header(psd, width, height)

        # Color mode data section (empty for RGB)
        psd.write_u32(0)

        # Image resources section (empty)
        psd.write_u32(0)

        # Layer & mask info
        layer_info = self._build_layer_and_mask_info(layers_bottom_to_top)
        psd.write_u32(len(layer_info))
        psd.write_bytes(layer_info)

        # Merged composite image data
        self._write_image_data(psd, composite, "Merged Composite")

        # 5. Write to file
        base, _ = os.path.splitext(page_name)
        psd_path = os.path.join(options.output_dir, f"{base}.psd")
        with open(psd_path, "wb") as f:
            f.write(psd.to_bytes())

        return psd_path

    def cleanup(self):
        pass

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_dimensions(width: int, height: int, name: str) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"Zero or negative dimensions for {name}: {width}x{height}")
        if width > 30000 or height > 30000:
            raise ValueError(
                f"PSD only supports dimensions up to 30000x30000, "
                f"got {width}x{height} for {name}"
            )

    @staticmethod
    def _write_header(w: PsdBinaryWriter, width: int, height: int) -> None:
        w.write_signature("8BPS")
        w.write_u16(1)       # version
        w.write_zeroes(6)    # reserved
        w.write_u16(4)       # channels (RGBA)
        w.write_u32(height)
        w.write_u32(width)
        w.write_u16(8)       # bits/channel
        w.write_u16(3)       # color mode (RGB)

    # ------------------------------------------------------------------
    # Layer collection
    # ------------------------------------------------------------------

    def _collect_layers(
        self, proj: ProjImgTrans, page_name: str, dpi: float
    ) -> List[ExportLayer]:
        """Build layers bottom-to-top."""
        layers: List[ExportLayer] = []
        src_path = os.path.join(proj.directory, page_name)

        # --- Source image ---
        has_inpainted = os.path.exists(
            proj.get_inpainted_path(page_name, get_last_modified=True)
        ) or os.path.exists(
            os.path.join(proj.inpainted_dir(), os.path.splitext(page_name)[0] + ".png")
        )

        src_pixels = self._load_image_rgba(src_path)
        layers.append(ExportLayer(
            id=1,
            name="Original Image",
            left=0, top=0,
            pixels=src_pixels,
            hidden=has_inpainted,
        ))

        # --- Inpainted ---
        inpainted_path = proj.get_inpainted_path(page_name, get_last_modified=True)
        if os.path.exists(inpainted_path):
            inpainted_pixels = self._load_image_rgba(inpainted_path)
            layers.append(ExportLayer(
                id=2,
                name="Inpainted",
                left=0, top=0,
                pixels=inpainted_pixels,
                hidden=False,
            ))

        # --- Segmentation Mask ---
        mask_path = os.path.join(
            proj.mask_dir(),
            os.path.splitext(page_name)[0] + ".png"
        )
        if os.path.exists(mask_path):
            mask_pixels = self._load_grayscale_as_rgba(mask_path)
            layers.append(ExportLayer(
                id=len(layers) + 1,
                name="Segmentation Mask",
                left=0, top=0,
                pixels=mask_pixels,
                hidden=True,
            ))

        # --- Text layers ---
        blk_list = proj.pages.get(page_name, [])
        font_set = self._collect_font_set(blk_list)

        text_layers: List[ExportLayer] = []
        text_index = 1
        for blk in blk_list:
            if not blk.translation or not blk.translation.strip():
                continue
            layer = self._text_layer(blk, text_index, dpi, font_set, proj, page_name)
            text_layers.append(layer)
            text_index += 1

        # Reverse text layers so first block in list appears topmost in PSD
        for layer in reversed(text_layers):
            layer.id = len(layers) + 1
            layers.append(layer)

        return layers

    # ------------------------------------------------------------------
    # Text layer construction
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_font_set(blk_list: list) -> List[str]:
        """Collect unique PS-compatible font names, preserving first-appearance order."""
        seen: Set[str] = set()
        font_set: List[str] = []
        for blk in blk_list:
            if not blk.translation:
                continue
            resolved, _ = resolve_font_name(blk.fontformat.font_family, ps_available=None)
            if resolved not in seen:
                seen.add(resolved)
                font_set.append(resolved)
        return font_set or ["ArialMT"]

    def _text_layer(
        self, blk, index: int, dpi: float, font_set: List[str],
        proj: ProjImgTrans, page_name: str,
    ) -> ExportLayer:
        """Build a single text ExportLayer from a TextBlock."""
        x1, y1, x2, y2 = blk.xyxy
        left = int(math.floor(x1))
        top = int(math.floor(y1))
        right = int(math.ceil(x2))
        bottom = int(math.ceil(y2))
        w = max(right - left, 1)
        h = max(bottom - top, 1)

        # Try to get rendered text pixels from the result image so the text
        # layer has visible content in the PSD.  If the result image isn't
        # available, use a transparent placeholder (the TySh/EngineData
        # section still makes text editable in Photoshop).
        pixels = self._crop_from_result(proj, page_name, left, top, right, bottom, w, h)

        ff = blk.fontformat
        resolved_font, _ = resolve_font_name(ff.font_family, ps_available=None)
        font_idx = font_set.index(resolved_font) if resolved_font in font_set else 0

        # Font size: image pixels → points
        size_pt = ff.font_size * 72.0 / dpi

        # Orientation
        orientation = TextOrientation.Vertical if ff.vertical else TextOrientation.Horizontal

        # Justification
        just_map = {0: TextJustification.Left, 1: TextJustification.Center, 2: TextJustification.Right}
        justification = just_map.get(ff.alignment, TextJustification.Left)

        # Color: RGBA
        frgb_list = [max(0, min(255, int(round(c)))) for c in ff.frgb]
        alpha = max(0, min(255, int(round(ff.opacity * 255))))
        color = (frgb_list[0], frgb_list[1], frgb_list[2], alpha)

        # Transform: rotation matrix
        angle_rad = math.radians(blk.angle)
        transform = (
            math.cos(angle_rad), math.sin(angle_rad),
            -math.sin(angle_rad), math.cos(angle_rad),
            float(x1), float(y1),
        )

        meta = TextLayerMetadata(
            index=index,
            text=blk.translation.strip(),
            bounds=(float(x1), float(y1), float(x2), float(y2)),
            transform=transform,
            orientation=orientation,
            justification=justification,
            font_index=font_idx,
            font_set=font_set,
            font_size=size_pt,
            color=color,
            faux_bold=ff.bold,
            faux_italic=ff.italic,
            box_width=float(w),
            box_height=float(h),
        )

        return ExportLayer(
            id=0,
            name=f"TL {index:03d} {blk.translation[:20].replace(chr(10),' ')}",
            left=left, top=top,
            pixels=pixels,
            hidden=False,
            text=meta,
        )

    # ------------------------------------------------------------------
    # Image loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_image_rgba(path: str) -> np.ndarray:
        """Load any image as an (H, W, 4) uint8 RGBA numpy array."""
        pil_img = PILImage.open(path).convert("RGBA")
        arr = np.array(pil_img, dtype=np.uint8)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3 + [np.full_like(arr, 255)], axis=-1)
        elif arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=-1)
        return arr

    @staticmethod
    def _crop_from_result(
        proj: ProjImgTrans, page_name: str,
        left: int, top: int, right: int, bottom: int,
        width: int, height: int,
    ) -> np.ndarray:
        """Crop a text block region from the page's rendered result image.

        *left* / *top* / *right* / *bottom* are the integer pixel bounds of
        the text block (matching the layer's position in the PSD).

        Falls back to transparent pixels if the result image doesn't exist
        or the region is outside its bounds.
        """
        result_path = proj.get_result_path(page_name)
        if not os.path.exists(result_path):
            return np.zeros((height, width, 4), dtype=np.uint8)

        try:
            result = PILImage.open(result_path).convert("RGBA")
            res_arr = np.array(result, dtype=np.uint8)
            rh, rw = res_arr.shape[:2]

            # Clamp crop region to image bounds (same coords as layer position)
            sx = max(left, 0)
            sy = max(top, 0)
            ex = min(right, rw)
            ey = min(bottom, rh)

            if sx >= ex or sy >= ey:
                return np.zeros((height, width, 4), dtype=np.uint8)

            cropped = res_arr[sy:ey, sx:ex]

            # Pad to full size in case the crop was smaller than expected
            out = np.zeros((height, width, 4), dtype=np.uint8)
            out[:cropped.shape[0], :cropped.shape[1]] = cropped
            return out

        except Exception:
            return np.zeros((height, width, 4), dtype=np.uint8)

    @staticmethod
    def _load_grayscale_as_rgba(path: str) -> np.ndarray:
        """Load a grayscale mask as (H, W, 4) uint8 RGBA."""
        pil_img = PILImage.open(path).convert("L")
        gray = np.array(pil_img, dtype=np.uint8)
        h, w = gray.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 0] = gray  # R
        rgba[:, :, 1] = gray  # G
        rgba[:, :, 2] = gray  # B
        rgba[:, :, 3] = 255   # A
        return rgba

    # ------------------------------------------------------------------
    # Merged composite
    # ------------------------------------------------------------------

    @staticmethod
    def _merged_composite(
        proj: ProjImgTrans,
        page_name: str,
        layers: List[ExportLayer],
        width: int,
        height: int,
    ) -> np.ndarray:
        """Build the merged RGBA composite for the image data section.

        Prefers the rendered result image if it exists; otherwise composites
        visible layers bottom-to-top.
        """
        # Try result image first
        result_path = proj.get_result_path(page_name)
        if os.path.exists(result_path):
            try:
                return PsBinaryExporter._load_image_rgba(result_path)
            except Exception:
                pass

        # Composite visible layers
        canvas = np.zeros((height, width, 4), dtype=np.uint8)
        for layer in layers:
            if layer.hidden:
                continue
            _overlay(canvas, layer.pixels, layer.left, layer.top)

        return canvas

    # ------------------------------------------------------------------
    # Layer & mask info
    # ------------------------------------------------------------------

    @staticmethod
    def _build_layer_and_mask_info(layers: List[ExportLayer]) -> bytes:
        """Build the Layer and Mask Information section.

        This is the most complex part of the PSD: layer records, channel
        image data, extra data (name, ID, TySh).
        """
        layer_info = PsdBinaryWriter()

        # Layer count: negative if > 0 layers
        if layers:
            layer_info.write_i16(-len(layers))
        else:
            layer_info.write_i16(0)

        # Pre-encode channels and extra data for all layers
        encoded_layers: List[List] = []
        extra_data_list: List[bytes] = []

        for layer in layers:
            pix = layer.pixels if layer.pixels is not None else np.zeros((1, 1, 4), dtype=np.uint8)
            channels = encode_image_rle(
                pix,
                [ChannelId.Red, ChannelId.Green, ChannelId.Blue, ChannelId.Alpha],
                layer.name,
            )
            extra = PsBinaryExporter._build_extra_data(layer)
            encoded_layers.append(channels)
            extra_data_list.append(extra)

        # Write layer records
        for layer, channels, extra in zip(layers, encoded_layers, extra_data_list):
            pix = layer.pixels if layer.pixels is not None else np.zeros((1, 1, 4), dtype=np.uint8)
            h, w = pix.shape[:2]

            top = layer.top
            left = layer.left
            bottom = top + h
            right = left + w

            layer_info.write_i32(top)
            layer_info.write_i32(left)
            layer_info.write_i32(bottom)
            layer_info.write_i32(right)
            layer_info.write_u16(len(channels))  # 4 for RGBA

            for ch in channels:
                layer_info.write_i16(ch.channel_id)
                layer_info.write_u32(2 + len(ch.data))  # 2 for the u16(1) RLE flag

            layer_info.write_signature("8BIM")
            layer_info.write_signature("norm")
            layer_info.write_u8(255)  # opacity
            layer_info.write_u8(0)    # clipping
            layer_info.write_u8(0x0A if layer.hidden else 0x08)  # flags
            layer_info.write_u8(0)    # filler
            layer_info.write_u32(len(extra))
            layer_info.write_bytes(extra)

        # Write channel image data (RLE per channel)
        for channels in encoded_layers:
            for ch in channels:
                layer_info.write_u16(1)  # compression = RLE
                layer_info.write_bytes(ch.data)

        layer_info.pad_to_multiple(4)

        # Wrap with length prefix and global layer mask
        full = PsdBinaryWriter()
        full.write_u32(len(layer_info))
        full.write_bytes(layer_info.to_bytes())
        full.write_u32(0)  # global layer mask (empty)
        return full.to_bytes()

    # ------------------------------------------------------------------
    # Extra data per layer
    # ------------------------------------------------------------------

    @staticmethod
    def _build_extra_data(layer: ExportLayer) -> bytes:
        """Build the extra data section for one layer.

        Contains layer mask (empty), blending ranges (empty), layer name,
        layer ID, optional unicode name, and optional TySh section.
        """
        extra = PsdBinaryWriter()
        extra.write_u32(0)  # layer mask data length
        extra.write_u32(0)  # blending ranges data length
        extra.write_pascal_string(layer.name, 4)

        _write_additional_info_block(extra, "lyid", layer.id.to_bytes(4, "big"), 4)

        if layer.text is not None:
            _write_additional_info_block(extra, "luni", _luni_body(layer.name), 4)
            _write_additional_info_block(extra, "TySh", _tysh_body(layer.text), 2)

        # Pad to 4
        body = extra.to_bytes()
        extra_pad = bytearray(body)
        while len(extra_pad) % 4 != 0:
            extra_pad.append(0)
        return bytes(extra_pad)

    # ------------------------------------------------------------------
    # Image data section (merged composite)
    # ------------------------------------------------------------------

    @staticmethod
    def _write_image_data(writer: PsdBinaryWriter, pixels: np.ndarray, name: str) -> None:
        """Write the merged composite image data section.

        Unlike per-layer channels, the image data section groups all row
        length tables (across channels) first, then all RLE payloads.
        """
        height, width = pixels.shape[:2]
        writer.write_u16(1)  # compression = RLE

        channels = encode_image_rle(
            pixels, [ChannelId.Red, ChannelId.Green, ChannelId.Blue, ChannelId.Alpha], name,
        )

        row_len_bytes = height * 2  # u16 per row per channel

        # Group all row length tables first
        for ch in channels:
            writer.write_bytes(ch.data[:row_len_bytes])

        # Then all RLE payloads
        for ch in channels:
            writer.write_bytes(ch.data[row_len_bytes:])


# ======================================================================
# Helper functions (shared by multiple methods)
# ======================================================================


def _write_additional_info_block(
    w: PsdBinaryWriter, key: str, body: bytes, alignment: int
) -> None:
    """Write an ``8BIM`` additional info block.

    Format: ``8BIM`` + 4-char key + ``u32(total_len)`` + body + zero padding.
    """
    padding = (alignment - (len(body) % alignment)) % alignment
    w.write_signature("8BIM")
    w.write_signature(key)
    w.write_u32(len(body) + padding)
    w.write_bytes(body)
    w.write_zeroes(padding)


def _luni_body(name: str) -> bytes:
    """Build the body of a ``luni`` (layer Unicode name) block."""
    w = PsdBinaryWriter()
    w.write_unicode_string(name)
    return w.to_bytes()


def _tysh_body(meta: TextLayerMetadata) -> bytes:
    """Build the ``TySh`` (type tool info) section for an editable text layer."""
    engine_data = encode_engine_data(TextEngineSpec(
        text=meta.text,
        font_index=meta.font_index,
        font_set=meta.font_set,
        font_size=meta.font_size,
        color=meta.color,
        faux_bold=meta.faux_bold,
        faux_italic=meta.faux_italic,
        orientation=meta.orientation,
        justification=meta.justification,
        box_width=meta.box_width,
        box_height=meta.box_height,
    ))

    b = meta.bounds
    fbounds = bounds_descriptor("bounds", b[0], b[1], b[2], b[3])
    fbounding_box = bounds_descriptor("boundingBox", b[0], b[1], b[2], b[3])

    ornt_value = "Hrzn" if meta.orientation == TextOrientation.Horizontal else "Vrtc"

    text_descriptor = (
        DescriptorObject("", "TxLr")
        .with_item("Txt ", DescriptorValue.text(meta.text))
        .with_item("textGridding", DescriptorValue.enum("textGridding", "None"))
        .with_item("Ornt", DescriptorValue.enum("Ornt", ornt_value))
        .with_item("AntA", DescriptorValue.enum("Annt", "antiAliasSharp"))
        .with_item("bounds", DescriptorValue.object(fbounds))
        .with_item("boundingBox", DescriptorValue.object(fbounding_box))
        .with_item("TextIndex", DescriptorValue.integer(meta.index))
        .with_item("EngineData", DescriptorValue.raw(engine_data))
    )

    warp_descriptor = (
        DescriptorObject("", "warp")
        .with_item("warpStyle", DescriptorValue.enum("warpStyle", "warpNone"))
        .with_item("warpValue", DescriptorValue.double(0.0))
        .with_item("warpPerspective", DescriptorValue.double(0.0))
        .with_item("warpPerspectiveOther", DescriptorValue.double(0.0))
        .with_item("warpRotate", DescriptorValue.enum("Ornt", ornt_value))
        .with_item("bounds", DescriptorValue.object(
            bounds_descriptor("bounds", b[0], b[1], b[2], b[3])
        ))
    )

    body = PsdBinaryWriter()
    body.write_i16(1)  # TySh version
    for v in meta.transform:
        body.write_f64(v)
    body.write_i16(50)  # descriptor version
    write_versioned_descriptor(body, text_descriptor)
    body.write_i16(1)   # warp version
    write_versioned_descriptor(body, warp_descriptor)
    # Bounds: Top, Left, Bottom, Right (f32)
    body.write_f32(b[1])  # Top
    body.write_f32(b[0])  # Left
    body.write_f32(b[3])  # Bottom
    body.write_f32(b[2])  # Right

    # Pad to 4
    result = bytearray(body.to_bytes())
    while len(result) % 4 != 0:
        result.append(0)
    return bytes(result)


def _overlay(
    canvas: np.ndarray, layer: np.ndarray, left: int, top: int
) -> None:
    """Overlay a layer onto the canvas with alpha compositing."""
    if layer is None or layer.size == 0:
        return

    lh, lw = layer.shape[:2]
    ch, cw = canvas.shape[:2]

    # Clamp to canvas bounds
    x0 = max(left, 0)
    y0 = max(top, 0)
    x1 = min(left + lw, cw)
    y1 = min(top + lh, ch)

    if x0 >= x1 or y0 >= y1:
        return

    # Source region in layer
    sx = x0 - left
    sy = y0 - top
    ex = sx + (x1 - x0)
    ey = sy + (y1 - y0)

    src = layer[sy:ey, sx:ex].astype(np.float32)
    dst = canvas[y0:y1, x0:x1].astype(np.float32)

    # Alpha blending
    src_alpha = src[:, :, 3:4] / 255.0
    dst_alpha = dst[:, :, 3:4] / 255.0
    out_alpha = src_alpha + dst_alpha * (1.0 - src_alpha)

    # Avoid division by zero
    mask = out_alpha[:, :, 0] > 0
    result = np.zeros_like(src)
    for c in range(3):
        result[:, :, c] = np.where(
            mask,
            (src[:, :, c] * src_alpha[:, :, 0] + dst[:, :, c] * dst_alpha[:, :, 0] * (1.0 - src_alpha[:, :, 0]))
            / out_alpha[:, :, 0],
            0,
        )
    result[:, :, 3] = out_alpha[:, :, 0] * 255.0

    canvas[y0:y1, x0:x1] = np.clip(result, 0, 255).astype(np.uint8)
