"""Theme-aware color helpers."""

from qtpy.QtGui import QColor

from utils.config import pcfg


def is_dark_theme() -> bool:
    return pcfg.darkmode


def shortcut_styles() -> dict:
    """Return a dict of stylesheet strings for ShortcutEditor widgets.
    Colors adapt to current theme (light/dark).
    """
    dark = is_dark_theme()
    if dark:
        return {
            'pill_bg':     '#3a3a42',
            'pill_text':   '#d4d4d8',
            'pill':        "_ShortcutPill { background: #3a3a42; border-radius: 4px; padding: 1px 2px; }",
            'card_bg':     '#2a2a32',
            'name_clr':    '#ccc',
            'btn_clr':     '#888',
            'btn_hvr':     '#fff',
            'close_clr':   '#888',
            'close_hvr':   '#f88',
            'add_bdr':     '#555',
            'add_clr':     '#aaa',
            'add_hvr_bdr': '#88f',
            'add_hvr_clr': '#fff',
            'place_clr':   '#888',
            'reset_hvr':   '#fff',
            'header_clr':  '#ccc',
            'disabled_clr':'#666',
            'disabled_bg': '#2a2a30',
        }
    else:
        return {
            'pill_bg':     '#e8e8ec',
            'pill_text':   '#444448',
            'pill':        "_ShortcutPill { background: #e8e8ec; border-radius: 4px; padding: 1px 2px; }",
            'card_bg':     '#f4f4f8',
            'name_clr':    '#333336',
            'btn_clr':     '#666',
            'btn_hvr':     '#333',
            'close_clr':   '#888',
            'close_hvr':   '#e55',
            'add_bdr':     '#bbb',
            'add_clr':     '#666',
            'add_hvr_bdr': '#55a',
            'add_hvr_clr': '#222',
            'place_clr':   '#999',
            'reset_hvr':   '#222',
            'header_clr':  '#333336',
            'disabled_clr':'#aaa',
            'disabled_bg': '#e0e0e4',
        }


def scrollbar_colors():
    """Return QColor values for scrollbar groove and handle."""
    dark = is_dark_theme()
    if dark:
        return QColor(0, 0, 0, 30), QColor(0, 0, 0, 90)
    else:
        return QColor(0, 0, 0, 10), QColor(0, 0, 0, 40)


def slider_colors():
    """Return color values for custom slider widgets."""
    dark = is_dark_theme()
    if dark:
        # Handle outer, groove
        return QColor(69, 69, 69), QColor(255, 255, 255, 115)
    else:
        return QColor(225, 228, 235), QColor(0, 0, 0, 100)
