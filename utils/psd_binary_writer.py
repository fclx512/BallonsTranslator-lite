"""Big-endian binary accumulator for PSD file construction.

Port of Koharu's ``writer.rs``.  Accumulates bytes in a ``bytearray`` and
exposes write methods that mirror the Photoshop file format conventions.
All multi-byte integers are **big-endian**.
"""

import struct


def ascii_legacy(text: str, max_bytes: int = 255) -> str:
    """Map non-ASCII characters to ``?``, truncate to *max_bytes*.

    Photoshop's Pascal-string encoding does not support non-ASCII, so
    we replace anything outside the ASCII range.
    """
    out: list[str] = []
    byte_count = 0
    for ch in text:
        mapped = ch if ch.isascii() else "?"
        ch_bytes = mapped.encode("ascii")
        if byte_count + len(ch_bytes) > max_bytes:
            break
        out.append(mapped)
        byte_count += len(ch_bytes)
    return "".join(out)


class PsdBinaryWriter:
    """Big-endian byte accumulator for building PSD files.

    Usage::

        w = PsdBinaryWriter()
        w.write_u32(42)
        w.write_signature("8BPS")
        data = w.to_bytes()
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    # ------------------------------------------------------------------
    # public accessors
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._buf)

    def to_bytes(self) -> bytes:
        """Return all accumulated bytes as an immutable ``bytes`` object."""
        return bytes(self._buf)

    # ------------------------------------------------------------------
    # raw byte helpers
    # ------------------------------------------------------------------

    def write_bytes(self, data: bytes) -> None:
        """Append arbitrary *data* to the buffer."""
        self._buf.extend(data)

    def write_zeroes(self, count: int) -> None:
        """Append *count* zero bytes."""
        self._buf.extend(b"\x00" * count)

    def pad_to_multiple(self, multiple: int) -> None:
        """Pad the buffer with zero bytes up to a multiple of *multiple*."""
        while len(self._buf) % multiple != 0:
            self._buf.append(0)

    # ------------------------------------------------------------------
    # primitive big-endian writes
    # ------------------------------------------------------------------

    def write_u8(self, value: int) -> None:
        self._buf.append(value & 0xFF)

    def write_i16(self, value: int) -> None:
        self._buf.extend(struct.pack(">h", value))

    def write_u16(self, value: int) -> None:
        self._buf.extend(struct.pack(">H", value))

    def write_i32(self, value: int) -> None:
        self._buf.extend(struct.pack(">i", value))

    def write_u32(self, value: int) -> None:
        self._buf.extend(struct.pack(">I", value))

    def write_f32(self, value: float) -> None:
        self._buf.extend(struct.pack(">f", value))

    def write_f64(self, value: float) -> None:
        self._buf.extend(struct.pack(">d", value))

    # ------------------------------------------------------------------
    # PSD-specific string encodings
    # ------------------------------------------------------------------

    def write_signature(self, sig: str) -> None:
        """Write a 4-byte ASCII signature.

        Raises ``AssertionError`` if *sig* is not exactly 4 bytes.
        """
        assert len(sig) == 4, f"PSD signatures must be 4 bytes, got {len(sig)}"
        self.write_bytes(sig.encode("ascii"))

    def write_ascii_or_class_id(self, value: str) -> None:
        """Write an OSType key or length-prefixed ASCII string.

        4-byte values (except ``warp``, ``time``, ``hold``, ``list``) are
        written as *bare class IDs*: ``i32(0) + 4-bytes``.

        Everything else is written as: ``i32(len) + bytes``.
        """
        special_exceptions = {"warp", "time", "hold", "list"}
        if len(value) == 4 and value not in special_exceptions:
            self.write_i32(0)  # bare class ID indicator
            self.write_signature(value)
        else:
            encoded = value.encode("ascii")
            self.write_i32(len(encoded))
            self.write_bytes(encoded)

    def write_pascal_string(self, text: str, pad_to: int = 4) -> None:
        """Write a Pascal-style string (length-prefixed ASCII).

        Non-ASCII characters are replaced with ``?``.  The string is
        truncated to 255 bytes, then zero-padded to a multiple of
        *pad_to*.
        """
        ascii_text = ascii_legacy(text, 255)
        self.write_u8(len(ascii_text))
        self.write_bytes(ascii_text.encode("ascii"))
        self.pad_to_multiple(pad_to)

    def write_unicode_string(self, text: str) -> None:
        """Write a length-prefixed UTF-16BE string.

        Format: ``u32(code_unit_count)`` + ``[u16]`` UTF-16BE code units.
        """
        utf16 = text.encode("utf-16-be")
        code_unit_count = len(utf16) // 2
        self.write_u32(code_unit_count)
        self.write_bytes(utf16)

    def write_unicode_string_with_padding(self, text: str) -> None:
        """Write a UTF-16BE string with a trailing NUL.

        Format: ``u32(code_unit_count + 1)`` + ``[u16]`` UTF-16BE +
        ``u16(0)``.
        """
        utf16 = text.encode("utf-16-be")
        code_unit_count = len(utf16) // 2
        self.write_u32(code_unit_count + 1)
        self.write_bytes(utf16)
        self.write_u16(0)
