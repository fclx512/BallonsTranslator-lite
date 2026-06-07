"""Compile .ts (Qt Linguist) to .qm (Qt message) files.

Generates the standard Qt QM binary format compatible with Qt 5/6.
Format specification from Qt source: qttools/src/linguist/shared/qm.cpp
"""

import struct
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

# --- Constants from Qt source ---
MAGIC = bytes(
    [
        0x3C,
        0xB8,
        0x64,
        0x18,
        0xCA,
        0xEF,
        0x9C,
        0x95,
        0xCD,
        0x21,
        0x1C,
        0xBF,
        0x60,
        0xA1,
        0xBD,
        0xDD,
    ]
)

# Section type tags
SECTION_LANGUAGE = 0xA7
SECTION_HASHES = 0x42
SECTION_MESSAGES = 0x69

# Message record tags
TAG_END = 1
TAG_TRANSLATION = 3
TAG_SOURCE_TEXT = 6
TAG_CONTEXT = 7
TAG_COMMENT = 8


def elf_hash(ba: bytes) -> int:
    """ELF hash algorithm used by Qt QTranslator."""
    h = 0
    for b in ba:
        h = (h << 4) + b
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g
    if h == 0:
        h = 1
    return h


def _iso8859_str(s: str) -> bytes:
    """Encode string as UTF-8 — replaces the original Latin-1 approach.

    The original Latin-1 encoding with ``errors="replace"`` silently
    converted em dashes, arrows, and other non-Latin-1 characters
    to ``?``, which broke runtime translation lookup because Qt's
    ``QTranslator`` computes its hash over UTF-8 bytes of the source
    text — a hash computed over Latin-1 bytes (with ``?`` in place of
    ``—`` etc.) never matches the caller's ``self.tr("…")`` string.

    Using UTF-8 everywhere keeps hashing and comparison consistent.
    """
    return s.encode("utf-8")


def _write_qstring(ds, s: str):
    """Write a QString in QDataStream format (u32 byte-length + UTF-16BE data).

    In QDataStream, QString is serialized as:
    - If the string is null: 0xFFFFFFFF
    - Otherwise: u32 (byte length) + UTF-16BE data
    """
    if s is None:
        ds.write(struct.pack(">I", 0xFFFFFFFF))
        return
    utf16 = s.encode("utf-16-be")
    ds.write(struct.pack(">I", len(utf16)))
    ds.write(utf16)


def _write_tagged_string(ds, tag: int, s: str):
    """Write tag byte + u32 length (incl. NUL) + Latin-1 data + NUL."""
    raw = _iso8859_str(s) + b"\x00"
    ds.write(struct.pack(">B", tag))
    ds.write(struct.pack(">I", len(raw)))
    ds.write(raw)


def compile_ts(ts_path: str, qm_path: str):
    tree = ET.parse(ts_path)
    root = tree.getroot()

    ts_language = root.get("language", "")

    # Parse all messages
    items: list[tuple[str, str, str]] = []  # (context, source, translation)
    for context in root.findall(".//context"):
        ctx_el = context.find("name")
        ctx_name = ctx_el.text if ctx_el is not None else ""
        for msg in context.findall("message"):
            if msg.get("type") == "obsolete":
                continue
            src_el = msg.find("source")
            tr_el = msg.find("translation")
            if src_el is None or tr_el is None:
                continue
            src = src_el.text or ""
            tr = tr_el.text or ""
            if not tr or msg.get("type") == "unfinished":
                tr = src
            items.append((ctx_name, src, tr))

    if not items:
        Path(qm_path).write_bytes(b"")
        print("No translations found.")
        return

    # --- Build Messages section byte array ---
    msg_buf = BytesIO()
    offsets: list[int] = []  # byte offset within msg_buf
    # We don't use comments, so hash is just of source text
    elfs: list[int] = []

    for ctx, src, tr in items:
        off = msg_buf.tell()
        offsets.append(off)

        h = elf_hash(_iso8859_str(src))  # hash over source text only (no comment)
        elfs.append(h)

        # Write Tag_Translation (always first)
        msg_buf.write(struct.pack(">B", TAG_TRANSLATION))
        _write_qstring(msg_buf, tr)

        # We write all three for every message (no delta compression yet).
        # Tag_Comment — skip, we have no comments.
        # Tag_SourceText
        _write_tagged_string(msg_buf, TAG_SOURCE_TEXT, src)
        # Tag_Context
        _write_tagged_string(msg_buf, TAG_CONTEXT, ctx)

        # Tag_End
        msg_buf.write(struct.pack(">B", TAG_END))

    msg_data = msg_buf.getvalue()

    # --- Build Hashes section ---
    # Sort by hash, then by offset (stable sort)
    indexed = list(range(len(items)))
    indexed.sort(key=lambda i: (elfs[i], offsets[i]))

    hash_buf = BytesIO()
    for i in indexed:
        hash_buf.write(struct.pack(">I", elfs[i]))
        hash_buf.write(struct.pack(">I", offsets[i]))
    hash_data = hash_buf.getvalue()

    # --- Build Language section ---
    lang_raw = ts_language.encode("utf-8")
    lang_buf = BytesIO()
    lang_buf.write(struct.pack(">B", len(lang_raw)))
    lang_buf.write(lang_raw)
    lang_data = lang_buf.getvalue()

    # --- Assemble file ---
    out = BytesIO()
    out.write(MAGIC)  # 16 bytes

    # Language section (0xa7)
    out.write(struct.pack(">B", SECTION_LANGUAGE))
    out.write(struct.pack(">I", len(lang_data)))
    out.write(lang_data)

    # Hashes section (0x42)
    out.write(struct.pack(">B", SECTION_HASHES))
    out.write(struct.pack(">I", len(hash_data)))
    out.write(hash_data)

    # Messages section (0x69)
    out.write(struct.pack(">B", SECTION_MESSAGES))
    out.write(struct.pack(">I", len(msg_data)))
    out.write(msg_data)

    Path(qm_path).write_bytes(out.getvalue())
    print(f"Compiled {len(items)} translations: {ts_path} -> {qm_path}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: qm_compile.py input.ts output.qm")
        sys.exit(1)
    compile_ts(sys.argv[1], sys.argv[2])
