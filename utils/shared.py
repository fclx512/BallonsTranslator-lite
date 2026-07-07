import json
import os
import os.path as osp
import sys
from typing import Dict

ICON_PATH = "icons/icon.icns"

PROGRAM_PATH = osp.abspath(osp.dirname(osp.dirname(__file__)))
LOGGING_PATH = osp.join(PROGRAM_PATH, "logs")

LIBS_PATH = osp.join(PROGRAM_PATH, "data/libs")

STYLESHEET_PATH = osp.join(PROGRAM_PATH, "config/stylesheet.css")
THEME_PATH = osp.join(PROGRAM_PATH, "config/themes.json")
CUSTOM_THEME_PATH = osp.join(PROGRAM_PATH, "config/custom_themes.json")
CONFIG_PATH = osp.join(PROGRAM_PATH, "config/config.json")

DEFAULT_TEXTSTYLE_DIR = osp.join(PROGRAM_PATH, "config/textstyles")
if not osp.exists(DEFAULT_TEXTSTYLE_DIR):
    os.makedirs(DEFAULT_TEXTSTYLE_DIR)


CONFIG_FONTSIZE_HEADER = 15
CONFIG_FONTSIZE_TABLE = 13
CONFIG_FONTSIZE_CONTENT = 13

CONFIG_COMBOBOX_HEIGHT = 30
CONFIG_COMBOBOX_SHORT = 200
CONFIG_COMBOBOX_MIDEAN = 332
CONFIG_COMBOBOX_LONG = 468

CONFIGBLOCK_CONTENT_MARGINS = (24, 24, 24, 24)
GROUPBOX_CONTENT_MARGINS = (8, 4, 8, 6)
CONFIG_SUBBLOCK_SPACING = 20
LINEEDIT_FIXHEIGHT = 45
NAVLIST_WIDTH = 180

SHORTCUT_PILL_FONTSIZE = 12
SHORTCUT_CLOSE_FONTSIZE = 12
SHORTCUT_KEYSEQ_WIDTH = 100

_size2width = {
    "short": CONFIG_COMBOBOX_SHORT,
    "median": CONFIG_COMBOBOX_MIDEAN,
    "long": CONFIG_COMBOBOX_LONG,
}


def size2width(size: str):
    global _size2width
    return _size2width[size]


HORSLIDER_FIXHEIGHT = 36

WIDGET_SPACING_CLOSE = 8
TEXTEDIT_FIXWIDTH = 350

TEXTEFFECT_FIXWIDTH = 400
TEXTEFFECT_MAXHEIGHT = 500

LEFTBAR_WIDTH = 48
LEFTBTN_WIDTH = 28

LDPI = 96.0
DPI = 188.75

SCREEN_H = 2160
SCREEN_W = 3840

DEFAULT_FONT_FAMILY = "Microsoft YaHei UI"
APP_DEFAULT_FONT = "Microsoft YaHei UI"

WINDOW_BORDER_WIDTH = 4
BOTTOMBAR_HEIGHT = 32
TITLEBAR_HEIGHT = 30

PAGELIST_THUMBNAIL_MAXNUM = 100
PAGELIST_THUMBNAIL_SIZE = 48

FLAG_QT6 = True

SLIDERHANDLE_COLOR = (85, 85, 96)
FOREGROUND_FONTCOLOR = (93, 93, 95)

MAX_NUM_LOG = 7

TRANSLATE_DIR = osp.join(PROGRAM_PATH, "translate")
DISPLAY_LANGUAGE_MAP = {
    "English": "English",
    "简体中文": "zh_CN",
}
VALID_LANG_SET = set(list(DISPLAY_LANGUAGE_MAP.values()))

DEFAULT_DISPLAY_LANG = "English"

USE_PYSIDE6 = False
ON_WINDOWS = sys.platform == "win32"
ON_MACOS = sys.platform == "darwin"
ON_LINUX = sys.platform.startswith("linux")
HEADLESS = False
DEBUG = False
args = None

FUZZY_MATCH_IMAGE_NAME = False

cache_data: Dict = None
cache_dir: str = osp.join(PROGRAM_PATH, ".btrans_cache")
cache_path: str = osp.join(PROGRAM_PATH, ".btrans_cache/cache.json")
CACHE_UPDATED = False
check_local_file_hash = True

FONT_FAMILIES: set = None
CUSTOM_FONT_FAMILIES = []  # 去重后的自定义字体家族名
ALL_FONT_FAMILIES = []  # 系统+自定义，去重合并，按字母排序
FONT_STYLES = {}  # 所有字体的样式映射 { FamilyName: [Style1, Style2...] }
FONT_FAMILY_ALIAS = {}  # 规范名 -> [原始家族名列表] (用于问题4的归并)
FONT_VARIABLE_AXES = {}  # { FamilyName: { 'wght': (min, max, default) } }
VIRTUAL_FONT_STYLES = {}  # { FamilyName: set("Bold", "Light", ...) } 记录哪些样式是虚拟生成的
pbar = {}
runtime_widget_set = set()


def add_to_runtime_widget_set(widget):
    runtime_widget_set.add(widget)


def remove_from_runtime_widget_set(widget):
    if widget in runtime_widget_set:
        runtime_widget_set.remove(widget)


showed_exception = set()


# it will be set to ui.mainwindow.create_errdialog.emit after UI initialized
def create_errdialog_in_mainthread(*args, **kwargs) -> None:
    return None


def create_infodialog_in_mainthread(*args, **kwargs) -> None:
    return None


def load_cache():
    global cache_data
    if cache_data is None:
        if osp.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf8") as file:
                    cache_data = json.load(file)
            except Exception:
                print(f"cached file {cache_path} is invalid")
                cache_data = {}
        else:
            cache_data = {}


def dump_cache():
    global cache_data
    if cache_data is None:
        return

    cache_dir = osp.dirname(cache_path)
    if not osp.exists(cache_dir):
        os.makedirs(cache_dir)

    with open(cache_path, "w", encoding="utf8") as file:
        json.dump(cache_data, file, indent=4)

    global CACHE_UPDATED
    CACHE_UPDATED = False


def init_font_list():
    """Enumerate all system fonts using QFontDatabase and populate ALL_FONT_FAMILIES and FONT_STYLES."""
    from qtpy.QtGui import QFontDatabase

    families = QFontDatabase.families()
    # Filter out vertical font variants (prefixed with @ on Windows)
    families = [f for f in families if not f.startswith("@")]
    global ALL_FONT_FAMILIES, FONT_STYLES
    ALL_FONT_FAMILIES = sorted(set(families))

    # Populate font styles (weights/variants) for each family
    FONT_STYLES = {}
    for family in ALL_FONT_FAMILIES:
        styles = QFontDatabase.styles(family)
        if styles:
            # Deduplicate by normalized name (some VF fonts return duplicate entries)
            seen = {}
            for s in styles:
                key = s.strip()
                if key not in seen:
                    seen[key] = s
            # Sort by numeric weight (100 Thin → 900 Black), then alphabetically
            deduped = sorted(
                seen.values(), key=lambda s: (QFontDatabase.weight(family, s), s)
            )
            FONT_STYLES[family] = deduped


def get_filtered_font_list(excluded=None) -> list:
    """Return ALL_FONT_FAMILIES minus the excluded font names."""
    if excluded is None:
        excluded = []
    excluded_set = set(excluded)
    return [f for f in ALL_FONT_FAMILIES if f not in excluded_set]


config_name_to_view_widget = {}
action_to_view_config_name = {}
register_view_widget: lambda *args, **kwargs: None


def _load_all_themes() -> dict:
    """Return merged dict of built-in + custom themes."""
    themes = {}
    if osp.exists(THEME_PATH):
        with open(THEME_PATH, "r", encoding="utf-8") as f:
            themes.update(json.load(f))
    if osp.exists(CUSTOM_THEME_PATH):
        with open(CUSTOM_THEME_PATH, "r", encoding="utf-8") as f:
            themes.update(json.load(f))
    return themes


def get_theme_color(var_name: str) -> str:
    """Return a CSS variable value from the active theme."""
    from utils.config import pcfg

    theme_name = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
    themes = _load_all_themes()
    if theme_name not in themes:
        theme_name = "eva-dark" if pcfg.darkmode else "eva-light"
    if theme_name not in themes:
        theme_name = list(themes.keys())[0]
    return themes[theme_name].get(var_name, "#888")
