"""直接对比我们的 TySh 输出与参考 PSD 的 TySh 结构差异。"""
import struct, sys

sys.path.insert(0, r"D:\ruanjian\BallonsTranslator-lite")

from utils.psd_engine_data import encode_engine_data, TextEngineSpec, TextOrientation, TextJustification
from utils.psd_binary_exporter import TextLayerMetadata, _tysh_body

# ========================================================================
# 1. Generate our TySh for a simple 'A', vertical text, no rotation
# ========================================================================
meta = TextLayerMetadata(
    index=0,
    text="A",
    bounds=(1243.0, 282.0, 1284.0, 370.0),  # approximately matching ref
    transform=(1.0, 0.0, 0.0, 1.0, 1243.875, 282.125),  # from ref
    orientation=TextOrientation.Horizontal,  # ref is Hrzn
    justification=TextJustification.Left,
    font_index=0,
    font_set=["ArialMT"],
    font_size=72.0,
    color=(255, 0, 0, 255),  # RGBA red
    faux_bold=False,
    faux_italic=False,
    box_width=41.0,
    box_height=88.0,
)

our_tysh = _tysh_body(meta)
print(f"Our TySh body: {len(our_tysh)} bytes")

# ========================================================================
# 2. Extract reference TySh
# ========================================================================
with open(r"D:\下载\测试.psd", "rb") as f:
    data = f.read()

pos = 26
cm_len = struct.unpack(">I", data[pos:pos+4])[0]
pos += 4 + cm_len
ir_len = struct.unpack(">I", data[pos:pos+4])[0]
pos += 4 + ir_len
lm_len = struct.unpack(">I", data[pos:pos+4])[0]
pos += 4

tysh_off = data.find(b"8BIMTySh", pos)
tag_len = struct.unpack(">I", data[tysh_off+8:tysh_off+12])[0]
ref_tysh = data[tysh_off+12:tysh_off+12+tag_len]
print(f"Ref TySh body:  {len(ref_tysh)} bytes")

# ========================================================================
# 3. Compare headers
# ========================================================================
print("\n=== TYSH VERSION ===")
print(f"  Our: version={struct.unpack('>h', our_tysh[0:2])[0]}")
print(f"  Ref: version={struct.unpack('>h', ref_tysh[0:2])[0]}")

print("\n=== TRANSFORM ===")
our_tx = struct.unpack(">6d", our_tysh[2:50])
ref_tx = struct.unpack(">6d", ref_tysh[2:50])
for i in range(6):
    match = "✓" if abs(our_tx[i] - ref_tx[i]) < 0.001 else "DIFF"
    print(f"  tx[{i}]: our={our_tx[i]:.6f}, ref={ref_tx[i]:.6f}  {match}")

print("\n=== DESCRIPTOR VERSION (2 bytes in TySh header) ===")
our_dv = struct.unpack(">h", our_tysh[50:52])[0]
ref_dv = struct.unpack(">h", ref_tysh[50:52])[0]
print(f"  Our: {our_dv}")
print(f"  Ref: {ref_dv}")

# ========================================================================
# 4. Compare the descriptor body (after version marker)
# ========================================================================
print(f"\n=== DESCRIPTOR BODY (starting at offset 52 = 0x34) ===")

# After the version 50 marker, what does each file have?
print("\n--- Our descriptor body after u16(50): ---")
# Our _tysh_body writes: u16(50), then write_versioned_descriptor writes u32(16), then descriptor body
# So after offset 52, we have u32(16) + descriptor_body
our_inner_ver = struct.unpack(">I", our_tysh[52:56])[0]
print(f"  u32 inner version: {our_inner_ver}")
# Then comes our unicode string name (empty = u32(0) + 4-byte padding)
our_name_len = struct.unpack(">I", our_tysh[56:60])[0]
print(f"  name length: {our_name_len}")
# Then OSType class ID "TxLr"
if len(our_tysh) > 64:
    our_class = our_tysh[64:68].decode("ascii", errors="replace")
    print(f"  class ID: {our_class!r}")

print("\n--- Reference descriptor body after u16(50): ---")
# The reference doesn't have the inner u32(16). So offset 52 (0x34) is the descriptor body.

# What are the first 32 bytes?
ref_body_start = ref_tysh[52:84]
print(f"  First 32 bytes of descriptor body:")
for i in range(0, len(ref_body_start), 16):
    chunk = ref_body_start[i:i+16]
    hexp = " ".join(f"{b:02x}" for b in chunk)
    ascp = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    print(f"    {52+i:04x}: {hexp:<48s} {ascp}")

# ========================================================================
# 5. COMPLETE HEX DIFF of the descriptor portions
# ========================================================================
print(f"\n=== COMPLETE HEX SIDE-BY-SIDE (first 300 bytes) ===")
print(f"{'Offset':>6} | {'Our TySh':<48s} | {'Ref TySh':<48s}")
print("-"*120)
max_len = min(len(our_tysh), len(ref_tysh), 300)
for i in range(0, max_len, 16):
    o = our_tysh[i:i+16]
    r = ref_tysh[i:i+16]
    o_hex = " ".join(f"{b:02x}" for b in o)
    r_hex = " ".join(f"{b:02x}" for b in r)
    o_asc = "".join(chr(b) if 32 <= b < 127 else "." for b in o)
    r_asc = "".join(chr(b) if 32 <= b < 127 else "." for b in r)
    diff = " <--" if o_hex != r_hex else ""
    print(f"{i:6d} | {o_hex:<48s} {o_asc:<16s} | {r_hex:<48s} {r_asc:<16s}{diff}")

# ========================================================================
# 6. Show data sizes
# ========================================================================
print(f"\n=== LENGTH COMPARISON ===")
print(f"  Our TySh: {len(our_tysh)} bytes")
print(f"  Ref TySh:  {len(ref_tysh)} bytes")
print(f"  Difference: {abs(len(our_tysh)-len(ref_tysh))} bytes")

# ========================================================================
# 7. Save both for hex dump comparison
# ========================================================================
with open(r"D:\下载\_our_tysh.bin", "wb") as f:
    f.write(our_tysh)
print(f"\nSaved our TySh to D:\\下载\\_our_tysh.bin")
