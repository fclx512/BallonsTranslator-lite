"""比较参考 PSD 和我们的 TySh 描述符项。"""
import struct, sys

sys.path.insert(0, r"D:\ruanjian\BallonsTranslator-lite")
from utils.psd_binary_exporter import TextLayerMetadata, _tysh_body
from utils.psd_engine_data import TextOrientation, TextJustification

# ==================================================================
# 1. Generate our TySh
# ==================================================================
meta = TextLayerMetadata(
    index=0, text="A",
    bounds=(1243.0, 282.0, 1284.0, 370.0),
    transform=(1.0, 0.0, 0.0, 1.0, 1243.875, 282.125),
    orientation=TextOrientation.Horizontal,
    justification=TextJustification.Left,
    font_index=0, font_set=["ArialMT"], font_size=72.0,
    color=(255, 0, 0, 255),
    faux_bold=False, faux_italic=False,
    box_width=41.0, box_height=88.0,
)
our = _tysh_body(meta)

# ==================================================================
# 2. Load reference TySh
# ==================================================================
with open(r"D:\下载\测试.psd", "rb") as f:
    data = f.read()
tysh_off = data.find(b"8BIMTySh")
tag_len = struct.unpack(">I", data[tysh_off+8:tysh_off+12])[0]
ref = data[tysh_off+12:tysh_off+12+tag_len]

# ==================================================================
# 3. Simple brute-force top-level item parser
# ==================================================================
def parse_ascii_key(data, pos):
    """Read ascii_or_class_id: i32(0)+4byte or i32(len)+ascii. Return (key, new_pos)."""
    pfx = struct.unpack(">i", data[pos:pos+4])[0]
    if pfx == 0:
        return data[pos+4:pos+8].decode("ascii", errors="replace"), pos + 8
    else:
        return data[pos+4:pos+4+pfx].decode("ascii", errors="replace"), pos + 4 + pfx

def skip_unknown_item(data, pos):
    """Try to skip past a single item by trying different boundaries."""
    # If we have at least 8 bytes (shortest item is key(OSType=8) + type(4) + value(>=1))
    # We try OSType key first
    item_start = pos
    try:
        k, pos = parse_ascii_key(data, pos)
        typ = data[pos:pos+4]; pos += 4
        # Value skip by type
        if typ == b"TEXT":
            cnt = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4 + cnt * 2
            while pos % 4 != 0: pos += 1
        elif typ == b"enum":
            _, pos = parse_ascii_key(data, pos)
            _, pos = parse_ascii_key(data, pos)
        elif typ == b"Objc":
            cnt = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4 + cnt * 2
            while pos % 4 != 0: pos += 1
            ni = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
            for _ in range(ni):
                _, pos = parse_ascii_key(data, pos)
                t2 = data[pos:pos+4]; pos += 4
                if t2 == b"UntF": pos += 12
                elif t2 == b"enum": _, pos = parse_ascii_key(data, pos); _, pos = parse_ascii_key(data, pos)
                elif t2 == b"bool": pos += 1; pos += (4-pos%4)%4
                elif t2 == b"long": pos += 4
                elif t2 == b"doub": pos += 8
                elif t2 == b"TEXT":
                    c2 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + c2*2
                    while pos % 4 != 0: pos += 1
                elif t2 == b"VlLs":
                    cnt2 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
                    for _ in range(cnt2):
                        it = data[pos:pos+4]; pos += 4
                        pos += 8 if it == b"doub" else 4
                elif t2 == b"Objc":
                    c2 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + c2*2
                    while pos % 4 != 0: pos += 1
                    ni2 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
                    for _ in range(ni2):
                        _, pos = parse_ascii_key(data, pos)
                        t3 = data[pos:pos+4]; pos += 4
                        if t3 == b"UntF": pos += 12
                        elif t3 == b"enum": _, pos = parse_ascii_key(data, pos); _, pos = parse_ascii_key(data, pos)
                        elif t3 == b"long": pos += 4
                        elif t3 == b"doub": pos += 8
                        elif t3 == b"bool": pos += 1; pos += (4-pos%4)%4
                        elif t3 == b"VlLs":
                            cnt3 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
                            for _ in range(cnt3): it3 = data[pos:pos+4]; pos += 4; pos += 8 if it3 == b"doub" else 4
                        elif t3 == b"TEXT":
                            c3 = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + c3*2
                            while pos % 4 != 0: pos += 1
                        else: pos += 4
                else: pos += 4
        elif typ == b"tdta":
            rl = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + rl
            while pos % 4 != 0: pos += 1
        elif typ == b"long": pos += 4
        elif typ == b"doub": pos += 8
        elif typ == b"bool": pos += 1; pos += (4-pos%4)%4
        elif typ == b"UntF": pos += 12
        elif typ == b"VlLs":
            cnt = int.from_bytes(data[pos:pos+4], 'big'); pos += 4
            for _ in range(cnt):
                it = data[pos:pos+4]; pos += 4
                pos += 8 if it == b"doub" else 4
        else: pos += 4
        return k, typ.decode("ascii", errors="replace"), pos
    except Exception as e:
        raise ValueError(f"Parse error at {pos:#x}: {e}")


def list_items(data, name):
    """Print all top-level items in a descriptor."""
    # TySh header: 2 (ver) + 48 (tx) + 2 (desc_ver) = 52
    pos = 2 + 48 + 2
    inner_ver = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    name_cnt = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    if name_cnt > 0:
        pos += name_cnt * 2
    bare = struct.unpack(">i", data[pos:pos+4])[0]
    pos += 4
    class_id = data[pos:pos+4].decode("ascii", errors="replace")
    pos += 4
    num_items = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4

    print(f"\n=== {name} (class={class_id}, inner_ver={inner_ver}, items={num_items}) ===")

    for i in range(num_items):
        try:
            k, typ, next_pos = skip_unknown_item(data, pos)
            print(f"  [{i:2d}] key={k:20s} type={typ:5s}  @{pos:#x}-{next_pos:#x} ({next_pos-pos} bytes)")
            pos = next_pos
        except Exception as e:
            print(f"  [{i:2d}] ERROR at {pos:#x}: {e}")
            # Dump raw bytes
            chunk = data[pos:pos+32]
            hexp = " ".join(f"{b:02x}" for b in chunk)
            print(f"        raw: {hexp}")
            break

    print(f"  Remaining after items: {len(data)-pos} bytes")

list_items(our, "OUR TySh")
list_items(ref, "REF TySh")

# ==================================================================
# 4. Show TySh tail data (warp descriptor + bounds)
# ==================================================================
# Find end of text descriptor items in both
def find_after_items(data):
    pos = 2 + 48 + 2
    inner_ver = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    name_cnt = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    if name_cnt > 0: pos += name_cnt * 2
    bare = struct.unpack(">i", data[pos:pos+4])[0]; pos += 4
    class_id = data[pos:pos+4]; pos += 4
    num_items = int.from_bytes(data[pos:pos+4], 'big'); pos += 4
    for i in range(num_items):
        try:
            _, _, next_pos = skip_unknown_item(data, pos)
            pos = next_pos
        except:
            break
    return pos

our_tail = find_after_items(our)
ref_tail = find_after_items(ref)

print(f"\n{'='*60}")
print(f"AFTER TEXT DESCRIPTOR ITEMS:")
print(f"  Our: offset {our_tail:#x} (remaining {len(our)-our_tail} bytes)")
print(f"  Ref: offset {ref_tail:#x} (remaining {len(ref)-ref_tail} bytes)")

# Dump the tail area (warp descriptor + bounds)
for label, d, tail in [("OUR", our, our_tail), ("REF", ref, ref_tail)]:
    print(f"\n--- {label} tail ({len(d)-tail} bytes) ---")
    chunk = d[tail:tail+128]
    for i in range(0, len(chunk), 16):
        c = chunk[i:i+16]
        hexp = " ".join(f"{b:02x}" for b in c)
        ascp = "".join(chr(b) if 32 <= b < 127 else "." for b in c)
        print(f"  {tail+i:04x}: {hexp:<48s} {ascp}")
