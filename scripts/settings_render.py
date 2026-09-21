"""设置面板各页 → PNG（目视验收布局/配色用）。

**这不是测试**，是改设置面板 UI 时的"看一眼"工具：把 ``ui/configpanel.py``
的 10 个页面逐页截下来，并在 stdout 回显导航结构（分组卡／项 chip／当前
选中项／页序），不看图也能核对结构。

用法::

    ./ballontrans_pylibs_win/python.exe scripts/settings_render.py
    ... scripts/settings_render.py --theme dark --scale 2 --out tmp/settings_render
    ... scripts/settings_render.py --diag          # 诊断配色，见下

输出：``<out>/<theme>/<序号>_<section_key>.png``（默认暗/亮两套）。

``--diag``：把底色各染一色再出图——页面凹面=品红、Widget 派生面=绿、落到全局
``QWidget`` 规则的裸 QWidget=橙、卡片描边=白。默认主题里前三者常是同色（卡片
描边被同色容器盖掉时肉眼看不见），这个模式让"底是谁画的、谁盖了谁"一眼可见；
输出目录加 ``diag_`` 前缀。用法与判读见 ``docs/基础速查/经验教训.md`` §3.6。

**已知渲染差异（不是 bug）**：管线页四个标签的**参数表是空的**——本进程没有
``ui/module_manager.py`` 装载模块，参数选择器与参数网格不初始化。设置面板的
卡片/配色/间距都不受它影响，看那四张图时只看外壳与标签栏。

**三个必须照抄 app 启动过程的点**（否则截图没有参考价值，同
``scripts/workbench_render.py``）：

1. **不许设 ``QT_QPA_PLATFORM=offscreen``。** 离屏平台插件下
   ``QFontDatabase.families()`` 是**空的**，任何文字都渲染成豆腐块，且设
   ``QApplication.setFont`` 也救不回来——必须走默认的 windows 平台插件。
2. **要装字体 + 翻译 + 主题**：字体用 ``launch.py`` 同款（Microsoft YaHei
   UI）；翻译器必须**早于** ``ui.*`` 导入（导航标签是模块级
   ``QCoreApplication.translate``，晚了就冻成英文）；样式表经
   ``ui/misc.py::parse_stylesheet`` 解析主题变量后 ``setStyleSheet``。
3. **上屏但不占桌面**：打 ``WA_DontShowOnScreen`` 再 ``show()``——布局照跑、
   ``showEvent`` 照发，但不开原生窗口，尺寸就钉在 ``_PANEL_W/_PANEL_H``。
"""

import argparse
import os
import os.path as osp
import sys

sys.path.insert(0, os.getcwd())

from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtGui import QFont  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

# 与 ui/overlay_modal.py 的面板尺寸一致：截图尺寸＝用户实际看到的尺寸
PANEL_W, PANEL_H = 1000, 700

# 导航顺序（与 ConfigPanel 里的 addSection 顺序一致）
SECTIONS = [
    "models",
    "pipeline",
    "llm_profile",
    "project",
    "typesetting",
    "interface",
    "shortcuts",
    "quick_menus",
    "config_mgmt",
    "workbench_temp",
]


def build_app(scale: int) -> QApplication:
    """按 ``launch.py`` 的方式起 QApplication（字体 + 语言 + 主题 + 缩放）。"""
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    app = QApplication([])
    app_font = QFont("Microsoft YaHei UI")
    if app_font.exactMatch():
        QApplication.setFont(app_font)

    from qtpy.QtCore import QTranslator

    from utils import shared
    from utils.config import load_config, pcfg

    load_config()
    # launch.py 同款顺序：注册表要在建面板之前装好。少了它，管线标签页渲染成
    # 空参数表（模块没注册就没有 params）。
    from modules.base import init_module_registries

    init_module_registries()

    # 截图工具不该动用户配置。本进程也没有 ModuleManager：模块参数还是盘上的
    # 扁平形状，真落盘会在 get_saving_params 里 KeyError。注意 save_config 在
    # profile_manager 里是**导入时绑定**的（``from .config import save_config``），
    # 只换 utils.config 那颗不够 —— 切到 LLM 配置页会经 save_all_profiles 走到
    # 旧引用上。
    from utils import config as config_mod
    from utils import profile_manager

    config_mod.save_config = lambda *args, **kwargs: None
    profile_manager.save_config = lambda *args, **kwargs: None

    translator = QTranslator()
    if translator.load(pcfg.display_lang, shared.TRANSLATE_DIR):
        app.installTranslator(translator)
        app._render_translator = translator  # installTranslator 不接管所有权
    return app


# 诊断配色（``--diag``）：把三层底色拆成互不相同的刺眼色，
# 一眼看出"到底是谁在画底色"。默认两套主题里页面凹面与 Widget 面
# 是**同一个** @widgetBackgroundColor 的近邻色，肉眼分不出谁盖了谁。
# 绿的=卡片/行（Widget 类），橙的=落到全局 QWidget 规则的裸 QWidget，
# 品红=页面凹面，白=卡片描边（1px，被不透明子控件盖住时会整段消失）。
DIAG_TOKENS = {
    "@emptyContentBackgroundColor": "#ff00ff",
    "@widgetBackgroundColor": "#00b400",
    "@qwidgetBackgroundColor": "#ff9900",
    "@borderColor": "#ffffff",
}


def apply_theme(app: QApplication, theme: str, diag: bool = False) -> None:
    from ui.misc import _resolve_theme, build_stylesheet_from_dict

    theme_dict = dict(_resolve_theme(theme))
    if diag:
        theme_dict.update(DIAG_TOKENS)
    app.setStyleSheet(build_stylesheet_from_dict(theme_dict))


def render(app: QApplication, panel, out_dir: str, theme: str, diag=False) -> None:
    os.makedirs(out_dir, exist_ok=True)
    apply_theme(app, theme, diag)
    app.processEvents()

    for index, section_key in enumerate(SECTIONS, start=1):
        item = panel.configNav.section_items.get(section_key)
        if item is None:
            print(f"  [skip] {section_key}: 导航里没有这一项")
            continue
        # 点它而不是 setCurrentSection：后者只改选中态不发信号（见
        # ui/configpanel.py::ConfigNavRail.setCurrentSection），页不会切
        item.click()
        app.processEvents()
        name = f"{index:02d}_{section_key}.png"
        panel.grab().save(osp.join(out_dir, name))

    checked = [k for k, i in panel.configNav.section_items.items() if i.isChecked()]
    print(f"[{theme}] saved {len(SECTIONS)} pages -> {out_dir}")
    print(f"[{theme}] last checked: {checked}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=osp.join("tmp", "settings_render"))
    parser.add_argument(
        "--scale", type=int, default=2, help="渲染倍率（默认 2，小字看得清）"
    )
    parser.add_argument(
        "--theme",
        choices=("dark", "light", "both"),
        default="both",
        help="渲染哪套主题（默认 both，两套各出一目录）",
    )
    parser.add_argument(
        "--diag",
        action="store_true",
        help="诊断配色：页面凹面/卡片/裸 QWidget/描边各染一色（四层谁盖谁一眼可见），"
        "输出目录加 diag_ 前缀",
    )
    args = parser.parse_args()

    app = build_app(args.scale)

    from utils.config import pcfg
    from ui.configpanel import ConfigPanel

    # 静态截图：关掉动画（淡入淡出会让 grab 抓到半透明的中间帧）
    pcfg.animation_fps = -1

    panel = ConfigPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.resize(PANEL_W, PANEL_H)
    panel.show()
    app.processEvents()

    themes = ["dark", "light"] if args.theme == "both" else [args.theme]
    theme_names = {
        "dark": pcfg.dark_theme,
        "light": pcfg.light_theme,
    }
    for theme in themes:
        name = theme_names[theme]
        prefix = "diag_" if args.diag else ""
        print(f"=== {theme} ({name}){' [diag]' if args.diag else ''} ===")
        render(
            app,
            panel,
            osp.join(args.out, prefix + theme),
            name,
            diag=args.diag,
        )

    # 导航结构回显：不看图也能核对分组与项
    from ui.configpanel import ConfigNavGroup, ConfigNavItem

    groups = panel.configNav.findChildren(ConfigNavGroup)
    print(
        "groups:",
        [f"{g.title_label.text()}({len(g.findChildren(ConfigNavItem))})" for g in groups],
    )
    print("items:", [i.text() for i in panel.configNav.section_items.values()])
    print("pages:", panel.pageStack.count())
    print("DONE", args.out)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
