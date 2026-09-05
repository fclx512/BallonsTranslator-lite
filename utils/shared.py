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

CONFIG_COMBOBOX_HEIGHT = 26
CONFIG_COMBOBOX_SHORT = 180
CONFIG_COMBOBOX_MIDEAN = 300
CONFIG_COMBOBOX_LONG = 420

CONFIGBLOCK_CONTENT_MARGINS = (24, 24, 24, 24)
GROUPBOX_CONTENT_MARGINS = (8, 4, 8, 6)
CONFIG_SUBBLOCK_SPACING = 20
LINEEDIT_FIXHEIGHT = 30
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

TEMP_PROJECTS_DIR = osp.join(PROGRAM_PATH, "projects")

cache_data: Dict = None
cache_dir: str = osp.join(PROGRAM_PATH, ".btrans_cache")
cache_path: str = osp.join(PROGRAM_PATH, ".btrans_cache/cache.json")
CACHE_UPDATED = False
check_local_file_hash = True

FONT_FAMILIES: set = None
ALL_FONT_FAMILIES = []  # 系统+自定义，去重合并，按字母排序

# 已知可能触发 DirectWrite CreateFontFaceFromHDC 告警的 Windows 老旧字体
LEGACY_FONTS = frozenset({
    "MS Sans Serif", "MS Serif", "Small Fonts",
    "System", "Fixedsys", "Terminal",
    "Courier", "Modern", "Roman", "Script",
})
FONT_STYLES = {}  # 所有真实 Qt 家族名（含被归并隐藏的别名）→ 各自样式 { FamilyName: [Style1, Style2...] }
FONT_FAMILY_ALIAS = {}  # 归并规范名 -> [被隐藏的别名家族名]（见 utils/font_scan.py）
FONT_PS_NAMES = {}  # 任意家族名（规范名+别名）-> {OS/2 字重: PostScript 名}，PSD 导出用
FONT_VARIABLE_AXES = {}  # { FamilyName: { 'wght': (min, max, default) } }
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
    """枚举系统字体并归并重复家族名（详见 utils/font_scan.py）。

    ``ALL_FONT_FAMILIES`` 只保留归并后的规范名（同一字体的字重变体与
    中英双名各留一项，对齐 Photoshop 的组织方式）；``FONT_STYLES`` 仍
    覆盖所有真实 Qt 家族名（含被隐藏的别名），保证旧项目数据按原名
    回显字重不受影响。「一键精简」条目存在 ``pcfg.excluded_fonts`` 里
    （与手动排除同一条落盘路径），在 ``get_filtered_font_list`` 过滤，
    不在此处剔除——否则会话内恢复要重启才生效。
    """
    from utils import font_scan
    from utils.config import pcfg

    global ALL_FONT_FAMILIES, FONT_STYLES, FONT_FAMILY_ALIAS, FONT_PS_NAMES
    data = font_scan.build_font_data()
    ALL_FONT_FAMILIES = data["display_families"]
    FONT_STYLES = data["styles"]
    FONT_FAMILY_ALIAS = data["canonical_to_aliases"]
    FONT_PS_NAMES = data["ps_index"]
    # face 派生缓存按字体库内容 memoize，刷新点重建
    from utils.face_resolver import invalidate_face_cache

    invalidate_face_cache()
    # 精简别名是真实 Qt 家族名：PS 索引借规范名的记录，旧项目数据按
    # 别名存储时才能正常导出
    for alias, canonical in pcfg.simplified_font_map.items():
        if alias not in FONT_PS_NAMES and canonical in FONT_PS_NAMES:
            FONT_PS_NAMES[alias] = FONT_PS_NAMES[canonical]


def _alias_to_canonical() -> Dict[str, str]:
    """反排 FONT_FAMILY_ALIAS 并入精简映射 → {别名: 规范名}。"""
    from utils.config import pcfg

    inverse = {}
    for canonical, aliases in FONT_FAMILY_ALIAS.items():
        for alias in aliases:
            inverse[alias] = canonical
    inverse.update(pcfg.simplified_font_map)
    return inverse


def canonical_font_family(name: str) -> str:
    """归并别名（旧字重变体/另一语言名）到规范名；未知名字原样返回。"""
    return _alias_to_canonical().get(name, name)


def get_filtered_font_list(excluded=None) -> list:
    """Return ALL_FONT_FAMILIES minus the excluded font names.

    排除名若是被归并隐藏的别名（旧字重变体 / 另一语言名），视为排除
    其规范名——用户排除重复项时记的旧名在归并后依然生效。「一键精简」
    条目（``pcfg.simplified_font_map`` 的键，也在 excluded 里）例外：
    只隐藏自身，**不**扩展到规范名，否则整组字体会被连带隐藏。
    """
    if excluded is None:
        excluded = []
    from utils.config import pcfg

    excluded_set = set(excluded)
    alias_map = _alias_to_canonical()
    simplified = set(pcfg.simplified_font_map)
    for name in excluded:
        if name in simplified:
            continue
        canonical = alias_map.get(name)
        if canonical:
            excluded_set.add(canonical)
    return [f for f in ALL_FONT_FAMILIES if f not in excluded_set]


config_name_to_view_widget = {}
action_to_view_config_name = {}
# MainWindow 启动时会注入真正的实现；未注入前（如离屏测试）为 no-op。
register_view_widget = lambda *args, **kwargs: None


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
