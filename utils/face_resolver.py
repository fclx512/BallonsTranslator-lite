"""Face 派生与同步：``font_weight`` 单一真值基建（阶段1）。

``FontFormat._style_name`` 降级为派生显示缓存：所有格式写入点经
``sync_face`` 按 ``(font_family, font_weight, italic)`` 从 Qt 字体数据库
反查匹配 face。``font_weight`` 未设（None）时不派生（返回 ``""``），渲染
端 Qt 走 weight 距离匹配。

关键 Qt 事实（QFont.styleName 与某 face 精确相等时 weight 完全被忽略）：
face 必须始终与 weight 同源，否则旧 face 会压过新字重——这是历史字重
编辑失效的根源。缓存在 ``utils/shared.py::init_font_list``（字体库刷新
点）之后重建。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from qtpy.QtGui import QFontDatabase

# [(styleName, weight, italic)]；family 未知/无 face 时不缓存空结果，
# 兼容 QApplication 尚未创建的离屏场景（枚举为空，创建后重查即有值）。
_face_cache: Dict[str, List[Tuple[str, int, bool]]] = {}


def invalidate_face_cache() -> None:
    """字体库刷新后调用（``utils/shared.py::init_font_list`` 末尾）。"""
    _face_cache.clear()


def _faces_of(family: str) -> List[Tuple[str, int, bool]]:
    """family 的全部 face；``QFontDatabase.weight`` 返回 -1 的条目丢弃。"""
    faces = _face_cache.get(family)
    if faces is not None:
        return faces
    faces = []
    try:
        styles = QFontDatabase.styles(family)
    except Exception:
        styles = []
    for style in styles:
        weight = int(QFontDatabase.weight(family, style))
        if weight <= 0:
            continue
        faces.append((style, weight, bool(QFontDatabase.italic(family, style))))
    if faces:
        _face_cache[family] = faces
    return faces


def _weight_key(weight: int) -> int:
    """Qt weightString/styleStringHelper 的阈值刻度（Qt6 直接 100-900）。"""
    return int(weight) if weight >= 100 else 400


def style_string_helper(weight: int, italic: bool) -> str:
    """照抄 Qt ``styleStringHelper`` 阈值逻辑合成规范 face 名（tie-break 用）。"""
    weight = _weight_key(weight)
    if weight >= 900:
        base = "Black"
    elif weight >= 800:
        base = "Extra Bold"
    elif weight >= 700:
        base = "Bold"
    elif weight >= 600:
        base = "Demi Bold"
    elif weight >= 500:
        base = "Medium"
    elif weight >= 400:
        base = "Regular"
    elif weight >= 300:
        base = "Light"
    elif weight >= 200:
        base = "Extra Light"
    else:
        base = "Thin"
    if italic:
        if weight == 400:
            return "Italic"
        if weight >= 700:
            return "Bold Italic"
        return base + " Italic"
    return base


def _norm_key(name: str) -> str:
    """face 名比较键：去空格 + casefold（Qt 样式名解析器的归一倾向）。"""
    return name.replace(" ", "").casefold()


def resolve_face(
    family: str, weight: Optional[int], italic: bool = False
) -> str:
    """按 (family, weight, italic) 反查匹配 face 名；无匹配返回 ``""``。

    *italic* 相同者先按 weight 最近取；无匹配放宽 italic 再取；平手
    tie-break 依次为：Qt 合成规范名（``styleStringHelper``）优先 →
    名字短者优先 → 字典序兜底。``weight`` 为 None 不派生。
    """
    if weight is None:
        return ""
    faces = _faces_of(family)
    if not faces:
        return ""
    pool = [f for f in faces if f[2] == bool(italic)] or faces
    nearest = min(abs(f[1] - int(weight)) for f in pool)
    candidates = [f for f in pool if abs(f[1] - int(weight)) == nearest]
    if len(candidates) == 1:
        return candidates[0][0]
    target = _norm_key(style_string_helper(int(weight), italic))
    for f in candidates:
        if _norm_key(f[0]) == target:
            return f[0]
    return min(candidates, key=lambda f: (len(f[0]), f[0]))[0]


def sync_face(ffmt) -> None:
    """派生并写入 ``ffmt._style_name``；font_weight 为 None 时清空。

    所有 ``font_weight``/``font_family``/``italic`` 变更写入点都必须调用，
    保证 face 派生缓存与真值同源（渲染端 ``set_fontformat`` 以数据层
    ``_style_name`` 直接写 QFont，残留旧 face 会压过新字重）。
    """
    ffmt._style_name = resolve_face(
        getattr(ffmt, "font_family", ""),
        getattr(ffmt, "font_weight", None),
        bool(getattr(ffmt, "italic", False)),
    )


def weight_of_face(family: str, style: str) -> Optional[int]:
    """face 名反查 weight；查询 API 查不到时走候选枚举兜底，仍无则 None。"""
    if not style:
        return None
    weight = int(QFontDatabase.weight(family, style))
    if weight > 0:
        return weight
    for name, w, _italic in _faces_of(family):
        if _norm_key(name) == _norm_key(style):
            return w
    return None
