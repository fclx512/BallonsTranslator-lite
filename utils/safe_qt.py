"""
Unified Qt safety utilities.

Provides a global Qt message handler that silently absorbs benign yet noisy
warnings (font size ≤ 0, RGB out of range) at the framework level — the
"unified management" layer that catches everything without touching call sites.

Also provides ``safe_qcolor()`` and ``clamp_font_size()`` helpers for code
paths that want to prevent bad values at the call site rather than relying
solely on the message handler.

Usage in ``launch.py``::

    from utils.safe_qt import install_qt_warning_filter
    install_qt_warning_filter()
"""

import os
import sys

from qtpy.QtCore import qInstallMessageHandler
from qtpy.QtGui import QColor

# ── Qt message handler (global safety net) ─────────────────────────────

# Tuple of message prefixes to suppress.  str.startswith(tuple) is O(1) per
# prefix-length and avoids compiling many regexes.
_SUPPRESSED_PREFIXES = (
    "QFont::setPointSize: Point size <= 0",
    "QFont::setPixelSize: Pixel size <= 0",
    "QColor::fromRgb: RGB parameters out of range",
    "QColor::fromRgbF: RGB parameters out of range",
    "QColor::fromHsv: HSV parameters out of range",
    "QColor::fromCmyk: CMYK parameters out of range",
)

_DEBUG_FILTER = os.environ.get("BALLOONTRANS_DEBUG_QT_WARNINGS") == "1"
_original_handler = None


def _default_fallback(msg_type, context, message):
    """Mimic Qt's default handler: print to stderr."""
    print(message, file=sys.stderr)


def _handler(msg_type, context, message):
    if _DEBUG_FILTER:
        # Pass everything through in debug mode (no suppression)
        if _original_handler:
            _original_handler(msg_type, context, message)
        return

    # Fast-path: check if message starts with any suppressed prefix
    if isinstance(message, str) and message.startswith(_SUPPRESSED_PREFIXES):
        return  # silently suppress

    # Forward everything else to the original handler (or fallback)
    if _original_handler:
        _original_handler(msg_type, context, message)


def install_qt_warning_filter():
    """Install a global Qt message handler that suppresses benign warnings.

    Call this **once** after ``QApplication`` is created (e.g. in ``launch.py``
    right after ``app = QApplication(sys.argv)``).

    To see the suppressed warnings during debugging, set the environment
    variable ``BALLOONTRANS_DEBUG_QT_WARNINGS=1`` before launching.
    """
    global _original_handler
    # qInstallMessageHandler returns the previous handler (or None if none
    # was installed, meaning Qt uses its built-in default).
    prev = qInstallMessageHandler(_handler)
    _original_handler = prev if prev is not None else _default_fallback


# ── Safe QColor factory ────────────────────────────────────────────────


def safe_qcolor(*args) -> QColor:
    """Return a ``QColor`` with all RGB(A) values clamped to [0, 255].

    Accepts the same signatures as ``QColor()``:

    * ``safe_qcolor(r, g, b)``
    * ``safe_qcolor(r, g, b, a)``
    * ``safe_qcolor([r, g, b])`` / ``safe_qcolor((r, g, b))``
    * ``safe_qcolor(hex_string)``  — passed through unchanged (hex strings
      cannot produce out-of-range values).

    Any integer/float RGB(A) component is clamped to ``[0, 255]``.
    """
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        vals = [max(0, min(255, int(round(c)))) for c in args[0][:4]]
        return QColor(*vals)
    if len(args) in (3, 4) and all(isinstance(a, (int, float)) for a in args):
        vals = [max(0, min(255, int(round(c)))) for c in args[:3]]
        if len(args) == 4:
            vals.append(max(0, min(255, int(round(args[3])))))
        return QColor(*vals)
    # Fallback — string, QColor, or other type
    return QColor(*args)


# ── Font size clamping ─────────────────────────────────────────────────


def clamp_font_size(pt: float, min_val: float = 1.0) -> float:
    """Clamp a font size so it never triggers Qt warnings.

    Qt warns when ``setPointSizeF`` / ``setPixelSize`` is called with a
    value ≤ 0, so the default minimum is 1.0 pt.

    Returns
    -------
    float
        ``max(pt, min_val)`` — the clamped value.
    """
    return max(float(pt), min_val)
