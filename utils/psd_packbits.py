"""PackBits RLE channel encoding for PSD image data.

Port of Koharu's ``packbits.rs``.

Each color channel (R, G, B, A) of a PSD layer is encoded per-row using
Apple PackBits (also called *run-length encoded* in the PSD spec).  Each
row produces a ``u16`` length value followed by PackBits-encoded bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Sequence

import numpy as np


class ChannelId(IntEnum):
    """PSD channel identifiers.

    Values match the PSD spec: 0 = Red, 1 = Green, 2 = Blue, -1 = Alpha.
    """

    Red = 0
    Green = 1
    Blue = 2
    Alpha = -1

    def rgba_offset(self) -> int:
        """Return the index into an RGBA ``[R, G, B, A]`` array."""
        return {self.Red: 0, self.Green: 1, self.Blue: 2, self.Alpha: 3}[self]

    def psd_id(self) -> int:
        """Return the PSD channel ID as ``int`` (``-1`` for Alpha)."""
        return self.value


@dataclass
class EncodedChannel:
    """One RLE-encoded channel for a PSD layer.

    The ``data`` field contains:
      1. A ``u16`` big-endian row-length table (one entry per image row).
      2. The concatenated PackBits-encoded row data.
    """

    channel_id: int
    data: bytes


class PackbitsError(ValueError):
    """Raised when a row's RLE data exceeds the PSD ``u16`` limit."""


def encode_image_rle(
    pixels: np.ndarray,
    channels: Sequence[ChannelId],
    layer_name: str = "",
) -> List[EncodedChannel]:
    """Encode an RGBA image into RLE channels for a PSD layer.

    Args:
        pixels: ``(H, W, 4)`` uint8 RGBA array.
        channels: Which channels to encode (e.g. ``[Red, Green, Blue, Alpha]``).
        layer_name: Used in error messages for debugging.

    Returns:
        One ``EncodedChannel`` per requested channel, in the same order.

    Raises:
        PackbitsError: If any row's RLE data exceeds ``u16::MAX`` bytes.
    """
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("pixels must be an (H, W, 4) RGBA array")

    height, width = pixels.shape[:2]
    encoded: List[EncodedChannel] = []

    for channel in channels:
        offset = channel.rgba_offset()
        lengths: List[int] = []
        data = bytearray()

        for y in range(height):
            start = len(data)
            row = pixels[y, :, offset].ravel()
            _encode_row(row, data)
            row_length = len(data) - start
            if row_length > 0xFFFF:
                raise PackbitsError(
                    f"RLE row {y} for layer '{layer_name}' exceeded "
                    f"PSD limits ({row_length} bytes)"
                )
            lengths.append(row_length)

        # Build output: row-length table (u16 BE) + concatenated RLE data
        out = bytearray()
        for length in lengths:
            out.extend(struct.pack(">H", length))
        out.extend(data)

        encoded.append(
            EncodedChannel(channel_id=channel.psd_id(), data=bytes(out))
        )

    return encoded


# ------------------------------------------------------------------
# internal — PackBits per row
# ------------------------------------------------------------------


def _encode_row(row: np.ndarray, out: bytearray) -> None:
    """Encode a single scanline with PackBits and append to *out*."""
    i = 0
    length = len(row)

    while i < length:
        run_len = _repeated_run_len(row, i)

        # Repeat packet: 3+ identical bytes
        if run_len >= 3:
            chunk = min(run_len, 128)
            out.append((1 - chunk) & 0xFF)
            out.append(int(row[i]))
            i += chunk
            continue

        # Literal packet: up to 128 non-repeating bytes
        literal_start = i
        literal_len = 0
        while i < length and literal_len < 128:
            next_run = _repeated_run_len(row, i)
            if next_run >= 3:
                break
            i += 1
            literal_len += 1

        out.append((literal_len - 1) & 0xFF)
        out.extend(int(row[literal_start + j]) for j in range(literal_len))


def _repeated_run_len(row: np.ndarray, start: int) -> int:
    """Count consecutive identical bytes starting at *start* (max 128)."""
    if start >= len(row):
        return 0
    value = row[start]
    end = min(start + 128, len(row))
    length = 1
    while start + length < end and row[start + length] == value:
        length += 1
    return length
