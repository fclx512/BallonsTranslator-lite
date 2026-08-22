import json
import os
import os.path as osp
import re
from pathlib import Path
from typing import Dict, List, Tuple, Union

import cv2
import numpy as np
from qtpy.QtCore import QPointF, Qt
from qtpy.QtGui import QColor, QImage, QPixmap, QTextCursor, QTextDocument

from utils import shared as C

QKEY = Qt.Key
QNUMERIC_KEYS = {
    QKEY.Key_0: 0,
    QKEY.Key_1: 1,
    QKEY.Key_2: 2,
    QKEY.Key_3: 3,
    QKEY.Key_4: 4,
    QKEY.Key_5: 5,
    QKEY.Key_6: 6,
    QKEY.Key_7: 7,
    QKEY.Key_8: 8,
    QKEY.Key_9: 9,
}

ARROWKEY2DIRECTION = {
    QKEY.Key_Left: QPointF(-1.0, 0.0),
    QKEY.Key_Right: QPointF(1.0, 0.0),
    QKEY.Key_Up: QPointF(0.0, -1.0),
    QKEY.Key_Down: QPointF(0.0, 1.0),
}


# return bgr tuple
def qrgb2bgr(color: Union[QColor, Tuple, List] = None) -> Tuple[int, int, int]:
    if color is not None:
        if isinstance(color, QColor):
            color = (color.blue(), color.green(), color.red())
        else:
            assert isinstance(color, (tuple, list))
            color = (color[2], color[1], color[0])
    return color


# https://stackoverflow.com/questions/45020672/convert-pyqt5-qpixmap-to-numpy-ndarray
def pixmap2ndarray(pixmap: Union[QPixmap, QImage], keep_alpha=True):
    size = pixmap.size()
    h = size.width()
    w = size.height()
    if isinstance(pixmap, QPixmap):
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    else:
        qimg = pixmap.convertToFormat(QImage.Format.Format_RGBA8888)

    byte_str = qimg.bits()
    if byte_str is None:
        return None

    if hasattr(byte_str, "asstring"):
        byte_str = qimg.bits().asstring(h * w * 4)
    else:
        byte_str = byte_str.tobytes()

    img = np.frombuffer(byte_str, dtype=np.uint8).reshape((w, h, 4)).copy()

    if keep_alpha:
        return img
    else:
        return np.ascontiguousarray(img[:, :, :3])


def ndarray2pixmap(img, return_qimg=False):
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    height, width, channel = img.shape
    bytesPerLine = channel * width
    if channel == 4:
        img_format = QImage.Format.Format_RGBA8888
    else:
        img_format = QImage.Format.Format_RGB888
    img = np.ascontiguousarray(img)
    qImg = QImage(img.data, width, height, bytesPerLine, img_format)
    if return_qimg:
        return qImg
    return QPixmap(qImg)


class LruIgnoreArg:
    def __init__(self, **kwargs) -> None:
        for key in kwargs:
            setattr(self, key, kwargs[key])

    def __hash__(self) -> int:
        return hash(type(self))

    def __eq__(self, other):
        return isinstance(other, type(self))


span_pattern = re.compile(r"<span style=\"(.*?)\">", re.DOTALL)
p_pattern = re.compile(r"<p style=\"(.*?)\">", re.DOTALL)
fragment_pattern = re.compile(r"<!--(.*?)Fragment-->", re.DOTALL)
color_pattern = re.compile(r"color:(.*?);", re.DOTALL)
td_pattern = re.compile(r"<td(.*?)>(.*?)</td>", re.DOTALL)
table_pattern = re.compile(r"(.*?)<table", re.DOTALL)
fontsize_pattern = re.compile(r"font-size:(.*?)pt;", re.DOTALL)
ffamily_pattern = re.compile(r"font-family:\'(.*?)\'", re.DOTALL)


def span_repl_func(matched, color):
    style = '<p style="' + matched.group(1) + " color:" + color + ';">'
    return style


def p_repl_func(matched, color):
    style = '<p style="' + matched.group(1) + " color:" + color + ';">'
    return style


def set_html_color(html, rgb):
    hex_color = "#%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])
    html = fragment_pattern.sub("", html)
    html = p_pattern.sub(lambda matched: p_repl_func(matched, hex_color), html)
    if color_pattern.findall(html):
        return color_pattern.sub(f"color:{hex_color};", html)
    else:
        return span_pattern.sub(
            lambda matched: span_repl_func(matched, hex_color), html
        )


def set_html_family(html, family):
    return ffamily_pattern.sub(f"font-family:'{family}'", html)


def html_max_fontsize(html: str) -> float:
    size_list = fontsize_pattern.findall(html)
    size_list = [float(size) for size in size_list]
    if len(size_list) > 0:
        return max(size_list)
    else:
        return None


def doc_replace(doc: QTextDocument, span_list: List, target: str) -> List:
    len_replace = len(target)
    cursor = QTextCursor(doc)
    cursor.setPosition(0)
    cursor.beginEditBlock()
    pos_delta = 0
    sel_list = []
    for span in span_list:
        sel_start = span[0] + pos_delta
        sel_end = span[1] + pos_delta
        cursor.setPosition(sel_start)
        cursor.setPosition(sel_end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(target)
        sel_list.append([sel_start, sel_end])
        pos_delta += len_replace - (sel_end - sel_start)
    cursor.endEditBlock()
    return sel_list


def doc_replace_no_shift(doc: QTextDocument, span_list: List, target: str):
    cursor = QTextCursor(doc)
    cursor.setPosition(0)
    cursor.beginEditBlock()
    for span in span_list:
        cursor.setPosition(span[0])
        cursor.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(target)
    cursor.endEditBlock()


def hex2rgb(h: str):  # rgb order (PIL)
    return tuple(int(h[1 + i : 1 + i + 2], 16) for i in (0, 2, 4))


def load_theme_dict() -> Dict:
    with open(C.THEME_PATH, "r", encoding="utf8") as f:
        return json.loads(f.read())


def load_custom_themes() -> Dict:
    if osp.exists(C.CUSTOM_THEME_PATH):
        with open(C.CUSTOM_THEME_PATH, "r", encoding="utf8") as f:
            return json.loads(f.read())
    return {}


def load_all_themes() -> Dict:
    """Return merged dict of built-in + custom themes (delegates to shared)."""
    return C._load_all_themes()


def _resolve_theme(theme: str) -> Dict:
    """Resolve a theme by name. Checks custom themes first, then built-in."""
    if not theme:
        from utils.config import pcfg

        theme = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
    custom = load_custom_themes()
    if theme in custom:
        return dict(custom[theme])
    builtin = load_theme_dict()
    if theme in builtin:
        return dict(builtin[theme])
    # Fallback: first available built-in theme
    if builtin:
        return dict(builtin[list(builtin.keys())[0]])
    return {}


def get_theme_color(theme_name: str = "", alpha: int = 255, key: str = "@accentPrimary") -> "QColor":
    """Return a QColor for a theme variable from the resolved current theme.

    Parameters
    ----------
    theme_name : str
        Theme name to resolve (default: current theme from pcfg).
    alpha : int
        Alpha channel (0-255) for the returned color.
    key : str
        Theme variable name, e.g. ``"@accentPrimary"``, ``"@dangerColor"``.
    """
    if not theme_name:
        from utils.config import pcfg

        theme_name = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
    tgt = _resolve_theme(theme_name)
    c = QColor(tgt.get(key, "#1e93e5"))
    if alpha != 255:
        c.setAlpha(alpha)
    return c


def parse_stylesheet(theme: str = "", reverse_icon: bool = False) -> str:
    if reverse_icon:
        set_icon_theme(theme)
    tgt_theme = _resolve_theme(theme)
    return build_stylesheet_from_dict(tgt_theme)


_stylesheet_cache = ""


def build_stylesheet_from_dict(tgt_theme: Dict) -> str:
    """Build a stylesheet string from a resolved theme dict (no disk I/O)."""
    global _stylesheet_cache
    if not _stylesheet_cache:
        with open(C.STYLESHEET_PATH, "r", encoding="utf-8") as f:
            _stylesheet_cache = f.read()
    stylesheet = _stylesheet_cache
    C.FOREGROUND_FONTCOLOR = hex2rgb(tgt_theme["@qwidgetForegroundColor"])
    for key, val in sorted(tgt_theme.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not key.startswith("_"):
            stylesheet = stylesheet.replace(key, val)
    return stylesheet


ICON_DIR = "icons"
ICON_LIST = []


def set_icon_theme(theme_name: str = ""):
    global ICON_LIST
    if not ICON_LIST:
        for filename in os.listdir(ICON_DIR):
            if Path(filename).suffix.lower() == ".svg":
                ICON_LIST.append(osp.join(ICON_DIR, filename))

    theme_dict = load_all_themes()
    tgt = _resolve_theme(theme_name)
    tgt_active = tgt.get("_iconFillActive", "")
    tgt_normal = tgt.get("_iconFill", "")
    if not tgt_active or not tgt_normal:
        return

    # Build replacement map: every known icon fill → target fill
    replacements = {}
    all_colors = set()
    for t in theme_dict.values():
        if a := t.get("_iconFillActive"):
            all_colors.add(a)
            replacements[f'fill="{a}"'] = f'fill="{tgt_active}"'
        if n := t.get("_iconFill"):
            all_colors.add(n)
            replacements[f'fill="{n}"'] = f'fill="{tgt_normal}"'

    pattern = re.compile("|".join(re.escape(f'fill="{c}"') for c in all_colors))
    for svgpath in ICON_LIST:
        with open(svgpath, "r", encoding="utf-8") as f:
            svg_content = f.read()
        svg_content = pattern.sub(lambda m: replacements[m.group()], svg_content)
        with open(svgpath, "w", encoding="utf-8") as f:
            f.write(svg_content)


def themed_icon_path(filename: str, theme: str = None) -> str:
    """Return the path of a themed SVG icon file.

    Icons are themed in place by :func:`set_icon_theme`, so there is no
    per-theme cache directory — the `icons/` folder already holds the
    currently applied fill (2026-08-21, engine 2a landing).
    """
    return osp.join(ICON_DIR, filename)


def themed_icon_url(filename: str, theme: str = None) -> str:
    return (
        '"' + Path(themed_icon_path(filename, theme)).as_posix().replace('"', '\\"') + '"'
    )


def icon_url(filename: str) -> str:
    return (
        '"' + Path(osp.join(ICON_DIR, filename)).as_posix().replace('"', '\\"') + '"'
    )


def mutate_dict_key(adict: dict, old_key: Union[str, int], new_key: str):
    # https://stackoverflow.com/questions/12150872/change-key-in-ordereddict-without-losing-order
    key_list = list(adict.keys())
    if isinstance(old_key, int):
        old_key = key_list[old_key]

    for key in key_list:
        value = adict.pop(key)
        adict[new_key if old_key == key else key] = value
