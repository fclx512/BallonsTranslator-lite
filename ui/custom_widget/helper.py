from qtpy.QtGui import QColor

from utils.config import pcfg


def isDarkTheme():
    return pcfg.darkmode


def themeColor():
    from ui.misc import get_theme_color
    return get_theme_color(pcfg.theme_name)