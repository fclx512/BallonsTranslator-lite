"""气泡面板各状态 → PNG（目视验收排版用，同 settings_render.py 的引导口径）。

照抄 app 启动的三个点（原因见 scripts/settings_render.py 模块注释）：
windows 平台插件 + YaHei UI 字体 + zh_CN 翻译早于 ui.* 导入 + 主题样式表。

输出 scripts/probes/out/bubble_panel/<theme>_<宽度>.png，覆盖四种状态：
disabled（关）/ default（开+描边+尾巴）/ custom（自定义区域展开）/
narrow（260 最小宽下的 default）。

用法::

    ./ballontrans_pylibs_win/python.exe scripts/probes/render_bubble_panel.py
    ... --theme light
"""

import argparse
import os
import sys

sys.path.insert(0, os.getcwd())


def build_app(scale=1, lang="zh"):
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    from qtpy.QtWidgets import QApplication
    from qtpy.QtGui import QFont

    app = QApplication([])
    f = QFont("Microsoft YaHei UI")
    if f.exactMatch():
        QApplication.setFont(f)

    # 翻译器早于 ui.* 导入（本文件在顶层 import ui.* 之前先 build_app）
    from qtpy.QtCore import QTranslator

    from utils import shared
    from utils.config import load_config, pcfg

    load_config()
    if lang != "en":
        translator = QTranslator()
        if translator.load(pcfg.display_lang, shared.TRANSLATE_DIR):
            app.installTranslator(translator)
            app._render_translator = translator
    return app


def apply_theme(app, theme):
    from ui.misc import _resolve_theme, build_stylesheet_from_dict

    app.setStyleSheet(build_stylesheet_from_dict(dict(_resolve_theme(theme))))


def snapshot(widget, path):
    from qtpy.QtGui import QImage, QPainter

    widget.grab().save(path)
    print(f"[render_bubble_panel] saved {path}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", default="eva-dark")
    parser.add_argument("--lang", default="zh", choices=["zh", "en"])
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()

    app = build_app(args.scale, lang=args.lang)
    apply_theme(app, args.theme)

    from qtpy.QtCore import QRectF, Qt
    from qtpy.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

    from ui.bubble import BubbleStyle, BubbleTail
    from ui.block_bubble_panel import BlockBubblePanel
    from ui.custom_widget.rail_dock_panel import RailDockPanel
    from ui.panel_rail import PanelRail, RailLauncherButton

    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out", "bubble_panel"
    )
    os.makedirs(out_dir, exist_ok=True)
    theme = args.theme
    prefix = f"{theme}_{args.lang}"

    # ── 宿主窗口 + 真窄栏 + 真 dock（260 最小宽即用户实际形态） ──
    host = QWidget()
    host.setWindowTitle("bubble panel probe")
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    rail = PanelRail(host)
    rail.setFixedHeight(700)
    layout.addWidget(rail)
    spacer = QFrame(host)
    spacer.setFixedSize(2, 700)
    layout.addWidget(spacer)
    host.resize(560, 700)
    host.show()
    app.processEvents()

    def make_dock(width):
        panel = BlockBubblePanel()
        dock = RailDockPanel(
            panel.tr("Bubble"), panel, rail, config_open="bubble_dock_open"
        )
        dock.setMinimumSize(260, 300)
        dock.resize(width, 420)
        dock.show()
        app.processEvents()
        return dock, panel

    def style_default():
        return BubbleStyle(
            tail=BubbleTail(angle=220, length=0.45, width=0.18),
            border=True,
        )

    # 1) 关闭态
    dock, panel = make_dock(260)
    panel.set_from_style(None)
    snapshot(dock, os.path.join(out_dir, f"{prefix}_260_disabled.png"))

    # 2) 默认开启：描边+尾巴
    panel.set_from_style(style_default(), None)
    panel.seed_region(QRectF(0, 0, 260, 160))
    snapshot(dock, os.path.join(out_dir, f"{prefix}_260_default.png"))
    dock.close_panel()
    dock.deleteLater()

    # 3) 自定义区域展开
    dock, panel = make_dock(300)
    panel.set_from_style(style_default(), QRectF(12, 8, 300, 180))
    panel.seed_region(QRectF(0, 0, 300, 180))
    snapshot(dock, os.path.join(out_dir, f"{prefix}_300_custom.png"))
    dock.close_panel()
    dock.deleteLater()

    print("[render_bubble_panel] OK", flush=True)


if __name__ == "__main__":
    main()
