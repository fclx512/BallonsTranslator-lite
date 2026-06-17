"""正确解析 PSD TySh 的描述符并提取 EngineData。"""

import struct, os

# ============================================================
# Correct PSD descriptor parser (version >= 16 format)
# ============================================================

def read_unicode_string(data, pos):
    """Read a Pascal-style Unicode string: 4-byte count followed by UTF-16BE."""
    count = struct.unpack(">I", data[pos:pos+4])[0]
    if count == 0:
        return "", pos + 4
    raw = data[pos+4:pos+4+count*2]
    s = raw.decode("utf-16be", errors="replace")
    return s, pos + 4 + count * 2


def parse_value(data, pos, type_sig, depth=0):
    """Parse a descriptor value by type signature."""
    indent = "  " * depth

    if type_sig == b"obj ":
        # Reference: obj type + UnicodeString ref
        class_str, pos = read_unicode_string(data, pos)
        # Followed by inner descriptor
        inner, pos = parse_descriptor(data, pos, depth)
        return ("obj", class_str, inner), pos

    elif type_sig == b"VlLs":
        count = struct.unpack(">I", data[pos:pos+4])[0]
        pos += 4
        items = []
        for _ in range(count):
            item_type = data[pos:pos+4]
            pos += 4
            val, pos = parse_value(data, pos, item_type, depth+1)
            items.append(val)
        return items, pos

    elif type_sig == b"doub":
        val = struct.unpack(">d", data[pos:pos+8])[0]
        pos += 8
        return val, pos

    elif type_sig == b"long":
        val = struct.unpack(">i", data[pos:pos+4])[0]
        pos += 4
        return val, pos

    elif type_sig == b"bool":
        val = data[pos] != 0
        pos += 1
        return val, pos

    elif type_sig == b"TEXT":
        s, pos = read_unicode_string(data, pos)
        return s, pos

    elif type_sig == b"enum":
        type_str, pos = read_unicode_string(data, pos)
        val_str, pos = read_unicode_string(data, pos)
        return ("enum", type_str, val_str), pos

    elif type_sig == b"Objc":
        inner, pos = parse_descriptor(data, pos, depth)
        return inner, pos

    elif type_sig == b"tdta":
        raw_len = struct.unpack(">I", data[pos:pos+4])[0]
        raw = data[pos+4:pos+4+raw_len]
        pos += 4 + raw_len
        return raw, pos

    elif type_sig == b"Enmr":
        class_str, pos = read_unicode_string(data, pos)
        type_str, pos = read_unicode_string(data, pos)
        val_str, pos = read_unicode_string(data, pos)
        return ("Enmr", class_str, type_str, val_str), pos

    elif type_sig == b"UntF":
        unit = struct.unpack(">d", data[pos:pos+8])[0]
        pos += 8
        unit_id = data[pos:pos+4]
        pos += 4
        return ("unit", unit, unit_id), pos

    elif type_sig == b"Clss":
        class_str, pos = read_unicode_string(data, pos)
        return ("class", class_str), pos

    elif type_sig == b"GlbO":
        # Global object - just a class reference
        class_str, pos = read_unicode_string(data, pos)
        return ("global_obj", class_str), pos

    elif type_sig == b"ObSn":
        # Object style - placeholder
        # Might contain additional data
        class_str, pos = read_unicode_string(data, pos)
        inner, pos = parse_descriptor(data, pos, depth)
        return ("ObSn", class_str, inner), pos

    elif type_sig == b"alis":
        # Alias (file path)
        alias_len = struct.unpack(">I", data[pos:pos+4])[0]
        alias = data[pos+4:pos+4+alias_len]
        pos += 4 + alias_len
        return alias, pos

    elif type_sig == b"Pth":
        # File path
        path_len = struct.unpack(">I", data[pos:pos+4])[0]
        path_data = data[pos+4:pos+4+path_len]
        pos += 4 + path_len
        return path_data, pos

    elif type_sig == b"cur ":
        # Cursor/descriptor reference
        # enum or obj follow
        class_str, pos = read_unicode_string(data, pos)
        return ("cur", class_str), pos

    else:
        # Unknown type - try to skip 4 bytes and return
        print(f"{indent}  UNKNOWN type {type_sig} at {pos:#x}")
        unknown = data[pos:pos+4]
        pos += 4
        return unknown, pos


def parse_descriptor(data, pos=0, depth=0):
    """Parse a version 16+ descriptor from PSD data."""
    indent = "  " * depth
    start_pos = pos

    # For version >= 16: class ID as UnicodeString
    class_name, pos = read_unicode_string(data, pos)

    # Number of items
    num_items = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4

    items = {}
    for i in range(num_items):
        # Key as UnicodeString
        key_name, pos = read_unicode_string(data, pos)

        # Type signature
        type_sig = data[pos:pos+4]
        pos += 4

        val, pos = parse_value(data, pos, type_sig, depth+1)
        items[key_name] = val

    return items, pos


def extract_tysh_engine_data(psd_path):
    """Extract the TySh block and its EngineData from a PSD file."""
    with open(psd_path, "rb") as f:
        data = f.read()

    # Find TySh in additional layer info
    # The PSD file has the structure:
    # - Header (26 bytes)
    # - Color mode data
    # - Image resources
    # - Layer & Mask info (which contains additional layer info like TySh)

    pos = 26
    # Skip color mode
    cm_len = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4 + cm_len
    # Skip image resources
    ir_len = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4 + ir_len
    # Layer & Mask info
    lm_len = struct.unpack(">I", data[pos:pos+4])[0]
    lm_end = pos + 4 + lm_len
    pos += 4

    # Find "8BIMTySh" in the layer info
    tysh_pos = data.find(b"8BIMTySh", pos, lm_end)
    if tysh_pos == -1:
        print("TySh block not found!")
        return None, None

    # Tagged block: 8BIM(4) + key(4) + length(4) + data
    tag_len = struct.unpack(">I", data[tysh_pos+8:tysh_pos+12])[0]
    tysh_data = data[tysh_pos+12:tysh_pos+12+tag_len]

    print(f"TySh body: {len(tysh_data)} bytes")

    # Parse TySh body
    pos2 = 0
    version = struct.unpack(">h", tysh_data[pos2:pos2+2])[0]
    pos2 += 2
    print(f"TySh version: {version}")

    # Transform matrix (6 float64)
    tx = struct.unpack(">6d", tysh_data[pos2:pos2+48])
    pos2 += 48
    print(f"Transform: x={tx[4]:.4f}, y={tx[5]:.4f}")

    # Descriptor version
    desc_ver = struct.unpack(">h", tysh_data[pos2:pos2+2])[0]
    pos2 += 2
    print(f"Descriptor version: {desc_ver}")

    # Parse the text descriptor
    text_desc, pos2 = parse_descriptor(tysh_data, pos2)

    print(f"\nText Descriptor items:")
    for k, v in text_desc.items():
        if k == "EngineData":
            ed_data = v if isinstance(v, bytes) else b""
            print(f"  {k}: <{len(ed_data)} bytes raw data>")
        else:
            vstr = str(v)
            if len(vstr) > 80:
                vstr = vstr[:77] + "..."
            print(f"  {k}: {vstr}")

    # Warp version
    warp_ver = struct.unpack(">h", tysh_data[pos2:pos2+2])[0]
    pos2 += 2
    warp_desc, pos2 = parse_descriptor(tysh_data, pos2)

    # Bounds
    top, left, bottom, right = struct.unpack(">4f", tysh_data[pos2:pos2+16])
    pos2 += 16
    print(f"\nBounds: T={top:.2f} L={left:.2f} B={bottom:.2f} R={right:.2f}")
    print(f"  Size: {right-left:.2f} × {bottom-top:.2f}")
    print(f"  Remaining: {len(tysh_data)-pos2} bytes")

    # Return EngineData
    if "EngineData" in text_desc and isinstance(text_desc["EngineData"], bytes):
        return text_desc["EngineData"], {
            "transform": tx,
            "bounds": (left, top, right, bottom),
            "text_desc": text_desc,
        }
    return None, None


def main():
    psd_path = r"D:\下载\测试.psd"
    if not os.path.exists(psd_path):
        print(f"PSD not found: {psd_path}")
        return

    ed_ref, info = extract_tysh_engine_data(psd_path)
    if ed_ref:
        ed_ref_path = os.path.join(os.path.dirname(psd_path), "_engine_data_reference.bin")
        with open(ed_ref_path, "wb") as f:
            f.write(ed_ref)
        print(f"\nReference EngineData ({len(ed_ref)} bytes) saved to {ed_ref_path}")

    # Also save full TySh body hexdump
    with open(psd_path, "rb") as f:
        data = f.read()
    tysh_pos = data.find(b"8BIMTySh")
    tag_len = struct.unpack(">I", data[tysh_pos+8:tysh_pos+12])[0]
    tysh_data = data[tysh_pos+12:tysh_pos+12+tag_len]
    hex_path = os.path.join(os.path.dirname(psd_path), "_tysh_hexdump.txt")
    with open(hex_path, "w", encoding="utf-8") as f:
        for i in range(0, len(tysh_data), 16):
            chunk = tysh_data[i:i+16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            f.write(f"{i:06x}: {hex_part:<48s}  {ascii_part}\n")
    print(f"Full hex dump: {hex_path}")


if __name__ == "__main__":
    main()
