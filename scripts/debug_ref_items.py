"""精确追踪参考 PSD TySh 的所有描述符项，逐一对比。"""
# ruff: noqa
import struct

data = open(r"D:\下载\测试.psd", "rb").read()
tysh_off = data.find(b"8BIMTySh")
if tysh_off == -1:
    print("TySh block not found!")
    exit(1)
tag_len = int.from_bytes(data[tysh_off + 8: tysh_off + 12], "big")
tysh = data[tysh_off + 12: tysh_off + 12 + tag_len]
print(f"TySh at offset {tysh_off:#x}, {len(tysh)} bytes")

def align(p, base):
    while (p - base) % 4 != 0:
        p += 1
    return p

# Skip TySh header
p = 2 + 48
desc_ver = struct.unpack(">h", tysh[p:p+2])[0]
p += 2
print(f"Descriptor version: {desc_ver}")

inner_ver = struct.unpack(">I", tysh[p:p+4])[0]
p += 4
print(f"Inner version: {inner_ver}")

name_len = struct.unpack(">I", tysh[p:p+4])[0]
p += 4
name = ""
if name_len > 0:
    name = tysh[p:p+name_len*2].decode("utf-16be", errors="replace").rstrip("\x00")
    p += name_len * 2
print(f"Name: {name!r} (len={name_len})")

bare = struct.unpack(">i", tysh[p:p+4])[0]
p += 4
class_id = tysh[p:p+4].decode("ascii", errors="replace")
p += 4
print(f"Class: {class_id!r} (bare={bare})")

num_items = struct.unpack(">I", tysh[p:p+4])[0]
p += 4
print(f"Items: {num_items}")

print("\n=== PARSING ITEMS ===")
item_p = p
for i in range(num_items):
    item_start = item_p

    bare_check = struct.unpack(">i", tysh[item_p:item_p+4])[0]
    if bare_check == 0:
        key_sig = tysh[item_p+4:item_p+8].decode("ascii", errors="replace")
        key = key_sig
        key_format = "OSType"
        item_p += 8
    else:
        key_len = bare_check
        if key_len > 0 and key_len < 100:
            raw = tysh[item_p+4:item_p+4+key_len*2]
            try:
                key = raw.decode("utf-16be")
            except:
                key = f"<raw:{raw.hex()}>"
            key_format = f"UStr({key_len})"
            item_p += 4 + key_len * 2
            item_p = align(item_p, item_start)
        else:
            key = f"<unk:{bare_check}>"
            key_format = "?"
            item_p += 4

    typ = tysh[item_p:item_p+4].decode("ascii", errors="replace")
    item_p += 4
    val_start = item_p
    val_preview = ""

    if typ == "TEXT":
        vl = struct.unpack(">I", tysh[val_start:val_start+4])[0]
        s = ""
        if vl > 0:
            s = tysh[val_start+4:val_start+4+vl*2].decode("utf-16be", errors="replace")
        skip = 4 + vl * 2
        skip = align(skip, 0)
        val_preview = f"TEXT={s[:60]!r}"
        item_p += skip

    elif typ == "enum":
        tl = struct.unpack(">I", tysh[val_start:val_start+4])[0]
        t = tysh[val_start+4:val_start+4+tl*2].decode("utf-16be", errors="replace")
        pp = val_start + 4 + tl * 2
        pp = align(pp, val_start)
        vl = struct.unpack(">I", tysh[pp:pp+4])[0]
        v = tysh[pp+4:pp+4+vl*2].decode("utf-16be", errors="replace")
        pp += 4 + vl * 2
        pp = align(pp, val_start)
        val_preview = f"enum({t}={v})"
        item_p = pp

    elif typ == "Objc":
        ol = struct.unpack(">I", tysh[val_start:val_start+4])[0]
        pp = val_start + 4 + ol * 2
        pp = align(pp, val_start)
        ni = struct.unpack(">I", tysh[pp:pp+4])[0]
        pp += 4
        for j in range(ni):
            bare2 = struct.unpack(">i", tysh[pp:pp+4])[0]
            if bare2 == 0:
                pp += 8
            else:
                kl = bare2
                pp += 4 + kl * 2
                pp = align(pp, 0)
            t2 = tysh[pp:pp+4]; pp += 4
            if t2 == b"UntF":
                pp += 12
            elif t2 == b"doub":
                pp += 8
            elif t2 == b"long":
                pp += 4
            elif t2 == b"bool":
                pp += 1; pp = align(pp, 0)
            elif t2 == b"TEXT":
                tl2 = struct.unpack(">I", tysh[pp:pp+4])[0]
                pp += 4 + tl2 * 2
                pp = align(pp, 0)
            elif t2 == b"enum":
                ekl = struct.unpack(">I", tysh[pp:pp+4])[0]
                pp += 4 + ekl * 2
                pp = align(pp, 0)
                evl = struct.unpack(">I", tysh[pp:pp+4])[0]
                pp += 4 + evl * 2
                pp = align(pp, 0)
            elif t2 == b"VlLs":
                cnt = struct.unpack(">I", tysh[pp:pp+4])[0]; pp += 4
                for _ in range(cnt):
                    it = tysh[pp:pp+4]; pp += 4
                    if it == b"doub": pp += 8
                    elif it == b"long": pp += 4
                    else: pp += 4
            elif t2 == b"Objc":
                ol2 = struct.unpack(">I", tysh[pp:pp+4])[0]
                pp += 4 + ol2 * 2
                pp = align(pp, 0)
                ni2 = struct.unpack(">I", tysh[pp:pp+4])[0]; pp += 4
                for _ in range(ni2):
                    bare3 = struct.unpack(">i", tysh[pp:pp+4])[0]
                    if bare3 == 0:
                        pp += 8
                    else:
                        pp += 4 + bare3 * 2
                        pp = align(pp, 0)
                    t3 = tysh[pp:pp+4]; pp += 4
                    if t3 == b"UntF": pp += 12
                    elif t3 == b"doub": pp += 8
                    elif t3 == b"long": pp += 4
                    elif t3 == b"bool": pp += 1; pp = align(pp, 0)
                    elif t3 == b"TEXT":
                        tl3 = struct.unpack(">I", tysh[pp:pp+4])[0]
                        pp += 4 + tl3 * 2
                        pp = align(pp, 0)
                    elif t3 == b"enum":
                        ekl3 = struct.unpack(">I", tysh[pp:pp+4])[0]
                        pp += 4 + ekl3 * 2
                        pp = align(pp, 0)
                        evl3 = struct.unpack(">I", tysh[pp:pp+4])[0]
                        pp += 4 + evl3 * 2
                        pp = align(pp, 0)
                    elif t3 == b"VlLs":
                        cnt3 = struct.unpack(">I", tysh[pp:pp+4])[0]; pp += 4
                        for _ in range(cnt3):
                            it3 = tysh[pp:pp+4]; pp += 4
                            if it3 == b"doub": pp += 8
                            else: pp += 4
                    else: pp += 4
            else:
                pp += 4
        val_preview = f"Objc(items={ni})"
        item_p = pp

    elif typ == "tdta":
        rl = struct.unpack(">I", tysh[val_start:val_start+4])[0]
        val_preview = f"raw({rl} bytes)"
        item_p += 4 + rl
        item_p = align(item_p, item_start)

    elif typ == "long":
        v = struct.unpack(">i", tysh[val_start:val_start+4])[0]
        val_preview = f"int={v}"
        item_p += 4

    elif typ == "doub":
        v = struct.unpack(">d", tysh[val_start:val_start+8])[0]
        val_preview = f"doub={v}"
        item_p += 8

    elif typ == "bool":
        val_preview = f"bool={bool(tysh[val_start])}"
        item_p += 1
        item_p = align(item_p, item_start)

    elif typ == "UntF":
        v = struct.unpack(">d", tysh[val_start:val_start+8])[0]
        unit = tysh[val_start+8:val_start+12].decode("ascii", errors="replace")
        val_preview = f"Unit({v:.4f} {unit})"
        item_p += 12

    else:
        val_preview = f"<{typ}>"
        item_p += 4

    print(f"  [{i:2d}] key={key!r:20s} type={typ:5s} {val_preview}  @{item_start:#x}-{item_p:#x}")

    # Stop if we're going past reasonable bounds
    if item_p > 800:
        print("  ... (truncated)")
        break

# Now parse OUR descriptor the same way
import sys

sys.path.insert(0, r"D:\ruanjian\BallonsTranslator-lite")
from utils.psd_binary_exporter import TextLayerMetadata, _tysh_body
from utils.psd_engine_data import TextJustification, TextOrientation

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

print("\n=== OUR ITEMS ===")
op = 2 + 48 + 2  # skip version + transform + desc_ver
inner_ver = struct.unpack(">I", our[op:op+4])[0]
op += 4
print(f"Inner version: {inner_ver}")
name_len = struct.unpack(">I", our[op:op+4])[0]
op += 4
if name_len > 0:
    name = our[op:op+name_len*2].decode("utf-16be", errors="replace").rstrip("\x00")
    op += name_len * 2
print(f"Name: {name!r} (len={name_len})")
bare = struct.unpack(">i", our[op:op+4])[0]
op += 4
class_id = our[op:op+4].decode("ascii", errors="replace")
op += 4
print(f"Class: {class_id!r}")
num_items = struct.unpack(">I", our[op:op+4])[0]
op += 4
print(f"Items: {num_items}")

item_p = op
for i in range(num_items):
    item_start = item_p
    bare_check = struct.unpack(">i", our[item_p:item_p+4])[0]
    if bare_check == 0:
        key = our[item_p+4:item_p+8].decode("ascii", errors="replace")
        key_format = "OSType"
        item_p += 8
    else:
        key_len = bare_check
        if key_len > 0 and key_len < 100:
            key = our[item_p+4:item_p+4+key_len*2].decode("utf-16be", errors="replace")
            key_format = f"UStr({key_len})"
            item_p += 4 + key_len * 2
            item_p = align(item_p, item_start)
        else:
            key = f"<unk:{bare_check}>"
            key_format = "?"
            item_p += 4

    typ = our[item_p:item_p+4].decode("ascii", errors="replace")
    item_p += 4
    val_start = item_p
    val_preview = ""

    if typ == "TEXT":
        vl = struct.unpack(">I", our[val_start:val_start+4])[0]
        s = our[val_start+4:val_start+4+vl*2].decode("utf-16be", errors="replace") if vl > 0 else ""
        skip = align(4 + vl * 2, 0)
        val_preview = f"TEXT={s[:60]!r}"
        item_p += skip
    elif typ == "enum":
        tl = struct.unpack(">I", our[val_start:val_start+4])[0]
        t = our[val_start+4:val_start+4+tl*2].decode("utf-16be", errors="replace")
        pp = align(val_start + 4 + tl * 2, val_start)
        vl = struct.unpack(">I", our[pp:pp+4])[0]
        v = our[pp+4:pp+4+vl*2].decode("utf-16be", errors="replace")
        pp = align(pp + 4 + vl * 2, val_start)
        val_preview = f"enum({t}={v})"
        item_p = pp
    elif typ == "Objc":
        ol = struct.unpack(">I", our[val_start:val_start+4])[0]
        ni = struct.unpack(">I", our[val_start+4+ol*2:val_start+8+ol*2])[0]
        val_preview = f"Objc(items={ni})"
        # just skip it roughly
        pp = val_start + 4 + ol * 2
        pp = align(pp, val_start)
        pp += 4  # num_items
        for j in range(ni):
            bare2 = struct.unpack(">i", our[pp:pp+4])[0]
            pp += 8 if bare2 == 0 else 8  # OSType key always
            t2 = our[pp:pp+4]; pp += 4
            if t2 == b"UntF": pp += 12
            elif t2 == b"enum":
                ekl = struct.unpack(">I", our[pp:pp+4])[0]; pp += 4 + ekl * 2
                pp = align(pp, 0)
                evl = struct.unpack(">I", our[pp:pp+4])[0]; pp += 4 + evl * 2
                pp = align(pp, 0)
            elif t2 == b"bool": pp += 1; pp = align(pp, 0)
            elif t2 == b"long": pp += 4
            elif t2 == b"doub": pp += 8
            elif t2 == b"TEXT":
                tl2 = struct.unpack(">I", our[pp:pp+4])[0]; pp += 4 + tl2 * 2
                pp = align(pp, 0)
            elif t2 == b"VlLs":
                cnt = struct.unpack(">I", our[pp:pp+4])[0]; pp += 4
                for _ in range(cnt):
                    it = our[pp:pp+4]; pp += 4
                    pp += 8 if it == b"doub" else 4
            else: pp += 4
        item_p = pp
    elif typ == "tdta":
        rl = struct.unpack(">I", our[val_start:val_start+4])[0]
        val_preview = f"raw({rl} bytes)"
        item_p += 4 + rl
        item_p = align(item_p, item_start)
    elif typ == "long":
        val_preview = f"int={struct.unpack('>i', our[val_start:val_start+4])[0]}"
        item_p += 4
    elif typ == "doub":
        val_preview = f"doub={struct.unpack('>d', our[val_start:val_start+8])[0]}"
        item_p += 8
    elif typ == "bool":
        val_preview = f"bool={bool(our[val_start])}"
        item_p += 1; item_p = align(item_p, item_start)
    elif typ == "UntF":
        v = struct.unpack(">d", our[val_start:val_start+8])[0]
        unit = our[val_start+8:val_start+12].decode("ascii", errors="replace")
        val_preview = f"Unit({v:.4f} {unit})"
        item_p += 12
    else:
        val_preview = f"<{typ}>"
        item_p += 4

    print(f"  [{i:2d}] key={key!r:20s} type={typ:5s} {val_preview}  @{item_start:#x}-{item_p:#x}")
