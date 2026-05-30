from utils.config import pcfg


def isDarkTheme():
    return pcfg.darkmode


def themeColor():
    from ui.misc import get_theme_color
    from utils.config import pcfg

    return get_theme_color(pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme)
