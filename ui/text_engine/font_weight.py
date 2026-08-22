"""Engine-side canonical font-weight helpers (2a landing, 2026-08-21).

The engine files migrated from upstream (item / annotations / formatting
panel …) were written against upstream's canonical 100-900 ``FontWeight``
enum and its HTML serialization helpers.  Node 1 of the v1.5.12 port keeps
``utils.fontformat.FontFormat.font_weight`` a plain int owned by
``utils.fontformat.fix_fontweight_qt`` — the fork deliberately did not adopt
upstream's FontWeight enum or Qt-HTML weight round-trip in utils.  Those
symbols are provided here, engine-local, with the native<->canonical tables
derived from the existing local maps so the two stay in one place.
"""

import enum
import re

from utils.fontformat import fontweight_qt5_to_qt6, fontweight_qt6_to_qt5
from utils.logger import logger as LOGGER


class FontWeight(enum.IntEnum):
    Thin = 100
    ExtraLight = 200
    Light = 300
    Normal = 400
    Medium = 500
    DemiBold = 600
    Bold = 700
    ExtraBold = 800
    Black = 900


# Qt 5 native integer weight <-> canonical CSS weight (local maps).
_QT5_TO_CANONICAL = fontweight_qt5_to_qt6
_CANONICAL_TO_QT5 = fontweight_qt6_to_qt5
# Qt 5 HTML serializer writes QFont::Weight * 8 to mimic CSS 100-900.
_QT5_CSS_TO_CANONICAL = {
    native * 8: canonical for native, canonical in _QT5_TO_CANONICAL.items()
}
_CANONICAL_TO_QT5_CSS = {
    canonical: native * 8 for native, canonical in _QT5_TO_CANONICAL.items()
}

_FONT_WEIGHT_CSS_PATTERN = re.compile(
    r"(font-weight\s*:\s*)(\d+)", re.IGNORECASE
)
_QT_RICH_TEXT_META = '<meta name="qrichtext" content="1" />'
_QT_RICH_TEXT_MARKER = "name=\"qrichtext\""
_UTF8_META = '<meta charset="utf-8" />'
_CHARSET_META_MARKER = "<meta charset="


def _replace_css_font_weights(html: str, mapping: dict) -> str:
    return _FONT_WEIGHT_CSS_PATTERN.sub(
        lambda match: (
            match.group(1)
            + str(mapping.get(int(match.group(2)), int(match.group(2))))
        ),
        html,
    )


def coerce_font_weight(weight: int) -> FontWeight:
    """Return the canonical weight nearest to *weight* (either Qt scale)."""
    if not isinstance(weight, bool) and isinstance(weight, int):
        if 0 <= weight < 100:
            native = min(
                _QT5_TO_CANONICAL,
                key=lambda candidate: abs(candidate - weight),
            )
            weight = _QT5_TO_CANONICAL[native]
        elif 100 <= weight <= 1000:
            weight = min(
                FontWeight,
                key=lambda candidate: abs(int(candidate) - weight),
            )
        try:
            return FontWeight(weight)
        except ValueError:
            pass
    if weight is not None:
        LOGGER.warning(
            "Ignoring invalid font weight %r; using Normal.",
            weight,
        )
    return FontWeight.Normal


def font_weight_to_qt(weight, *, qt6: bool = None) -> int:
    """Return the current Qt binding's native integer weight."""
    canonical = coerce_font_weight(weight)
    if qt6 is None:
        from utils import shared as _shared

        qt6 = _shared.FLAG_QT6
    if qt6:
        return int(canonical)
    return _CANONICAL_TO_QT5[int(canonical)]


def font_weight_from_qt(weight: int) -> FontWeight:
    """Return a canonical weight from either Qt 5 or Qt 6 native value."""
    return coerce_font_weight(int(weight))


def export_font_weight_html(html: str, *, qt6: bool) -> str:
    """Write canonical CSS weights from either Qt HTML serializer."""
    if qt6:
        return html
    html = _replace_css_font_weights(html, _QT5_CSS_TO_CANONICAL)
    if (
        _QT_RICH_TEXT_META in html
        and _CHARSET_META_MARKER not in html.lower()
    ):
        html = html.replace(
            _QT_RICH_TEXT_META,
            _QT_RICH_TEXT_META + _UTF8_META,
            1,
        )
    return html


def import_font_weight_html(html: str, *, qt6: bool) -> str:
    """Adapt canonical or legacy Qt 5 CSS to the active Qt parser."""
    lowered_html = html.lower()
    legacy_qt5 = (
        _QT_RICH_TEXT_MARKER in lowered_html
        and _CHARSET_META_MARKER not in lowered_html
    )
    if legacy_qt5:
        return (
            _replace_css_font_weights(html, _QT5_CSS_TO_CANONICAL)
            if qt6
            else html
        )
    return (
        html
        if qt6
        else _replace_css_font_weights(html, _CANONICAL_TO_QT5_CSS)
    )