"""Qt font family name → Photoshop PostScript font name mapping & fallback."""

import difflib
from typing import Dict, List, Optional, Set, Tuple

# 预置 Qt→PS 字体名映射表
# Qt uses display names; Photoshop COM/ExtendScript expects PostScript or
# system-registered font names.  These two often differ (e.g. "Microsoft YaHei UI"
# vs "Microsoft YaHei").  This table covers common mismatches.
QT_TO_PS_FONT_MAP: Dict[str, str] = {
    "Microsoft YaHei UI": "Microsoft YaHei",
    "Microsoft YaHei": "Microsoft YaHei",
    "SimHei": "SimHei",
    "SimSun": "SimSun",
    "NSimSun": "NSimSun",
    "FangSong": "FangSong",
    "KaiTi": "KaiTi",
    "FZYaSong": "FZYaoSong_GB18030",
    "Source Han Sans SC": "SourceHanSansSC",
    "Source Han Serif SC": "SourceHanSerifSC",
    "Noto Sans CJK SC": "NotoSansCJKsc",
    "Noto Serif CJK SC": "NotoSerifCJKsc",
    "Arial": "Arial",
    "Arial Black": "ArialBlack",
    "Times New Roman": "TimesNewRoman",
    "Courier New": "CourierNew",
    "Comic Sans MS": "ComicSansMS",
    "Impact": "Impact",
    "Verdana": "Verdana",
    "Georgia": "Georgia",
    "Tahoma": "Tahoma",
    "Trebuchet MS": "TrebuchetMS",
    "Palatino Linotype": "PalatinoLinotype",
    "Lucida Console": "LucidaConsole",
    "Calibri": "Calibri",
    "Cambria": "Cambria",
    "Consolas": "Consolas",
    "Segoe UI": "SegoeUI",
}


def normalize_font_name(name: str) -> str:
    """Strip whitespace and collapse internal spaces for fuzzy comparison."""
    return " ".join(name.split()).casefold()


def resolve_font_name(
    qt_family: str,
    ps_available: Optional[Set[str]] = None,
) -> Tuple[str, bool]:
    """Resolve a Qt font family to the best PS-compatible name.

    Returns ``(resolved_name, is_exact_match)``.

    Resolution order:
    1. Exact match in *ps_available*.
    2. Lookup in static ``QT_TO_PS_FONT_MAP``.
    3. Case-insensitive fuzzy match against *ps_available*.
    4. Fall back to the original *qt_family* (PS will use its default if missing).
    """
    # 1. Direct match (case-insensitive) in available PS fonts.
    if ps_available:
        key = normalize_font_name(qt_family)
        for ps_name in ps_available:
            if normalize_font_name(ps_name) == key:
                return ps_name, True

    # 2. Static lookup table.
    mapped = QT_TO_PS_FONT_MAP.get(qt_family)
    if mapped is not None:
        if ps_available is None or mapped in ps_available:
            return mapped, True
        # mapped name not in PS — fall through to fuzzy.

    # 3. Fuzzy match against PS available fonts.
    if ps_available:
        best = _fuzzy_match(qt_family, ps_available)
        if best is not None:
            return best, False

    # 4. Give up — return original.
    return qt_family, False


def _fuzzy_match(target: str, candidates: Set[str]) -> Optional[str]:
    """Return the closest font name from *candidates* via difflib, or None."""
    target_norm = normalize_font_name(target)
    norm_to_orig = {normalize_font_name(c): c for c in candidates}

    # Try substring match first.
    for norm, orig in norm_to_orig.items():
        if target_norm in norm or norm in target_norm:
            return orig

    # Fall back to sequence matcher.
    if not norm_to_orig:
        return None
    matches = difflib.get_close_matches(target_norm, list(norm_to_orig), n=1, cutoff=0.6)
    if matches:
        return norm_to_orig[matches[0]]
    return None


def suggest_alternative(
    font_family: str,
    ps_fonts: Set[str],
    top_n: int = 3,
) -> List[str]:
    """Return up to *top_n* alternative font names available in PS."""
    norm_to_orig = {normalize_font_name(f): f for f in ps_fonts}
    target_norm = normalize_font_name(font_family)
    matches = difflib.get_close_matches(
        target_norm, list(norm_to_orig), n=top_n, cutoff=0.5
    )
    return [norm_to_orig[m] for m in matches]


def check_project_fonts(
    font_families: Set[str],
    ps_available: Optional[Set[str]],
) -> Dict[str, Optional[str]]:
    """Check every font used in a project against PS availability.

    Returns ``{qt_family: ps_name_or_None}``.
    ``None`` means the font could not be resolved.
    """
    result: Dict[str, Optional[str]] = {}
    for family in sorted(font_families):
        resolved, _ = resolve_font_name(family, ps_available)
        if ps_available is not None:
            result[family] = resolved if resolved in ps_available else None
        else:
            # No PS font list available (e.g. ExtendScript path) —
            # only check static map.
            mapped = QT_TO_PS_FONT_MAP.get(family)
            result[family] = mapped  # None if not in map
    return result
