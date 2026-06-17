"""Parse TySh descriptor from reference PSD — simple, flat, correct."""
import struct, os

def read_unicode(data, pos):
    c = struct.unpack(">I", data[pos:pos+4])[0]
    if c == 0:
        return "", pos + 4
    return data[pos+4:pos+4+c*2].decode("utf-16be"), pos + 4 + c*2

def skip_value(data, pos, typ):
    if typ == b"TEXT":
        c = struct.unpack(">I", data[pos:pos+4])[0]
        return pos + 4 + c * 2
    elif typ == b"tdta":
        rl = struct.unpack(">I", data[pos:pos+4])[0]
        return pos + 4 + rl
    elif typ == b"doub":
        return pos + 8
    elif typ == b"long":
        return pos + 4
    elif typ == b"bool":
        return pos + 1
    elif typ == b"enum":
        s, pos = read_unicode(data, pos)
        return read_unicode(data, pos)[1]
    elif typ == b"Objc":
        return skip_descriptor(data, pos)
    elif typ == b"VlLs":
        cnt = struct.unpack(">I", data[pos:pos+4])[0]
        pos += 4
        for _ in range(cnt):
            it = data[pos:pos+4]; pos += 4
            pos = skip_value(data, pos, it)
        return pos
    elif typ == b"obj ":
        _, pos = read_unicode(data, pos)
        return skip_descriptor(data, pos)
    elif typ == b"Enmr":
        _, pos = read_unicode(data, pos)
        _, pos = read_unicode(data, pos)
        return read_unicode(data, pos)[1]
    elif typ == b"UntF":
        return pos + 12  # double(8) + OSType(4)
    elif typ == b"alis":
        rl = struct.unpack(">I", data[pos:pos+4])[0]
        return pos + 4 + rl
    elif typ == b"cur ":
        return read_unicode(data, pos)[1]
    else:
        return pos + 4

def skip_descriptor(data, pos):
    _, pos = read_unicode(data, pos)
    n = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
    for _ in range(n):
        _, pos = read_unicode(data, pos)
        typ = data[pos:pos+4]; pos += 4
        pos = skip_value(data, pos, typ)
    return pos

def list_items(data, pos, depth=0):
    prefix = "  " * depth
    cn, pos = read_unicode(data, pos)
    n = struct.unpack(">I", data[pos:pos+4])[0]
    print(f"{prefix}Descriptor class={cn!r} items={n}")
    pos += 4
    for i in range(n):
        key, pos = read_unicode(data, pos)
        typ = data[pos:pos+4]; pos += 4
        val_preview = ""

        if typ == b"TEXT":
            s, pos = read_unicode(data, pos)
            val_preview = f"= {s[:60]!r}"
        elif typ == b"tdta":
            rl = struct.unpack(">I", data[pos:pos+4])[0]
            val_preview = f"= <{rl} bytes>"
            pos += 4 + rl
        elif typ == b"doub":
            v = struct.unpack(">d", data[pos:pos+8])[0]
            val_preview = f"= {v}"
            pos += 8
        elif typ == b"long":
            v = struct.unpack(">i", data[pos:pos+4])[0]
            val_preview = f"= {v}"
            pos += 4
        elif typ == b"bool":
            val_preview = f"= {bool(data[pos])}"
            pos += 1
        elif typ == b"enum":
            t, pos = read_unicode(data, pos)
            v, pos = read_unicode(data, pos)
            val_preview = f"= enum({t}={v})"
        elif typ == b"Objc":
            # print sub-descriptor
            val_preview = f"= [Objc]"
            print(f"{prefix}  [{i}] key={key!r} type={typ} {val_preview}")
            pos = list_items(data, pos, depth+1)
            continue
        elif typ == b"VlLs":
            cnt = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
            items = []
            for _ in range(cnt):
                it = data[pos:pos+4]; pos += 4
                if it == b"doub":
                    items.append(struct.unpack(">d", data[pos:pos+8])[0])
                    pos += 8
                else:
                    items.append(f"<{it}>")
                    pos = skip_value(data, pos, it)
            val_preview = f"= List({cnt}): {items[:8]}"
        elif typ == b"UnFl":
            v = struct.unpack(">d", data[pos:pos+8])[0]; pos += 8
            val_preview = f"= UnitFloat({v})"
        elif typ == b"cur ":
            c, pos = read_unicode(data, pos)
            val_preview = f"= curr({c})"
        else:
            pos = skip_value(data, pos, typ)
            val_preview = f"= <{typ}>"

        print(f"{prefix}  [{i}] key={key!r} type={typ} {val_preview}")
    return pos

# Main
data = open(r"D:\下载\测试.psd", "rb").read()
pos = 26
cm_len = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + cm_len
ir_len = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4 + ir_len
lm_len = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4

tysh_off = data.find(b"8BIMTySh", pos)
tag_len = struct.unpack(">I", data[tysh_off+8:tysh_off+12])[0]
tysh = data[tysh_off+12:tysh_off+12+tag_len]

print(f"TySh body: {len(tysh)} bytes\n")

pp = 0
print(f"TySh version: {struct.unpack('>h', tysh[pp:pp+2])[0]}"); pp += 2
tx = struct.unpack(">6d", tysh[pp:pp+48])
print(f"Transform: [{tx[0]:.4f}, {tx[1]:.4f}, {tx[2]:.4f}, {tx[3]:.4f}, {tx[4]:.4f}, {tx[5]:.4f}]")
pp += 48

dv = struct.unpack(">h", tysh[pp:pp+2])[0]
print(f"Descriptor version: {dv}"); pp += 2

print("\n--- TEXT DESCRIPTOR ---")
pp = list_items(tysh, pp)

print(f"\n--- WARP ---")
wv = struct.unpack(">h", tysh[pp:pp+2])[0]
print(f"Warp version: {wv}"); pp += 2
pp = list_items(tysh, pp)

top, left, bottom, right = struct.unpack(">4f", tysh[pp:pp+16])
print(f"\nBounds: T={top:.2f} L={left:.2f} B={bottom:.2f} R={right:.2f}")
pp += 16
print(f"\nRemaining: {len(tysh)-pp} bytes")
