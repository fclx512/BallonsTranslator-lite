"""Insert performance-related code into textitem.py and scenetext_manager.py."""

# === textitem.py ===
with open("ui/textitem.py", encoding="utf-8") as f:
    content = f.read()

# 1. __init__: Add cache mode check after setCacheMode
content = content.replace(
    '        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)',
    '        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)\n'
    '        # Crisp mode: always vector rendering (NoCache)\n'
    '        from utils.config import pcfg\n'
    '\n'
    '        if pcfg.text_rendering == 0:  # Crisp (always vector)\n'
    '            self.setCacheMode(QGraphicsItem.CacheMode.NoCache)'
)

# 2. repaint_background: Add SmoothPixmapTransform toggle
content = content.replace(
    '        painter = QPainter(target_map)',
    '        painter = QPainter(target_map)\n'
    '        from utils.config import pcfg\n'
    '        if pcfg.text_rendering == 0:  # Crisp — full quality\n'
    '            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)'
)

# But need to keep the original paint_stroke check right after. Let me check
# what comes after the painter = QPainter line in repaint_background.
# The code has: painter = QPainter(target_map); from utils.config import pcfg; if pcfg.canvas_render_quality...
# Wait, the original doesn't have that. Let me check the original code.

# Actually let me do this differently - read the actual file sections.
with open("ui/textitem.py", encoding="utf-8") as f:
    content = f.read()

# === textitem.py ===

# 1. __init__: after setCacheMode, add cache mode check
old = '        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)\n'
new = old + (
    '        # Crisp mode: always vector rendering (NoCache)\n'
    '        from utils.config import pcfg\n'
    '\n'
    '        if pcfg.text_rendering == 0:  # Crisp (always vector)\n'
    '            self.setCacheMode(QGraphicsItem.CacheMode.NoCache)\n'
)
content = content.replace(old, new, 1)

# 2. repaint_background: after painter = QPainter(target_map), add SmoothPixmapTransform toggle
old = '        painter = QPainter(target_map)\n'
new = old + (
    '        from utils.config import pcfg\n'
    '        if pcfg.text_rendering == 0:  # Crisp — full quality\n'
    '            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)\n'
)
content = content.replace(old, new, 1)

# 3. _render_text_only: after p = QPainter(pm), add SmoothPixmapTransform toggle
old = '        p = QPainter(pm)\n'
new = old + (
    '        from utils.config import pcfg\n'
    '\n'
    '        if pcfg.text_rendering == 0:  # Crisp — full quality\n'
    '            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)\n'
)
content = content.replace(old, new, 1)

# 4. _build_full_pixmap: after painter = QPainter(pm), add SmoothPixmapTransform toggle
old = '        painter = QPainter(pm)\n'
# Be careful - this also matches repaint_background. Let me use a more specific match.
# Actually _build_full_pixmap has: painter = QPainter(pm)\n        from utils.config import pcfg
# which is the 2nd occurrence. Let me search backwards or use a unique context.
# Actually, let me check what follows each occurrence.
# repaint_background: painter = QPainter(target_map)\n        from utils.config import pcfg
# _build_full_pixmap:   painter = QPainter(pm)\n        from utils.config import pcfg
# They're in different methods. Let me use the appearance of "pm" vs "target_map".

# Actually, wait. I already added the from utils.config import pcfg and SmoothPixmapTransform 
# after painter = QPainter(target_map) in repaint_background. But _build_full_pixmap also 
# starts with painter = QPainter(pm) and then from utils.config import pcfg.
# Hmm, but the original file doesn't have from utils.config import pcfg in _build_full_pixmap.
# That was added in the previous session.
# So I need to add it fresh.

# Let me check what's after painter = QPainter(pm) in the original file.
with open("ui/textitem.py", encoding="utf-8") as f:
    content2 = f.read()

# Find painter = QPainter(pm)
import re
for m in re.finditer(r'painter = QPainter\(pm\)\n', content2):
    pos = m.end()
    print(f"After 'painter = QPainter(pm)': {content2[pos:pos+60]!r}")
for m in re.finditer(r'painter = QPainter\(target_map\)\n', content2):
    pos = m.end()
    print(f"After 'painter = QPainter(target_map)': {content2[pos:pos+60]!r}")
