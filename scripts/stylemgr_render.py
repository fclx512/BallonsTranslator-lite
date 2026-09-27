"""样式管理器（Font Style Manager）的目视验收台。

**这不是测试**，是改 `ui/fontstyle_manager.py` 左树/右栏样式时的"看一眼"工具：
合成一个带内容的工程（2 个带变体的大样式 + 未分组 + 3 条库模板，库条目刻意与
项目样式同字体同方向——真实用户的库就是这么攒起来的），把面板渲染成 PNG，
并在 stdout 回显三区带结构与展开态（不看图也能核对分区/计数）。

用法::

    ./ballontrans_pylibs_win/python.exe scripts/stylemgr_render.py
    ... scripts/stylemgr_render.py --out tmp/stylemgr_render --scale 2

输出：``<out>/<theme>_<序号>_<场景>.png``（默认暗/亮两套主题）。四个场景：
1_asis = 刚打开（区带默认展开）；2/3 = 分别选中库条目 / 项目大样式（看右栏
动作区与选中高亮）；4 = **选中已有变体**（走数据层的 ``VariantEntry`` + 既有的
``select_payload`` 接口，不碰任何 UI 私有控件名），并把右栏的 ``text`` 字体组
展开、滚动条归零——变体模式下"字体迁移卡 + 字体字段组"（改 font_family 让块
换归属）都挤在右栏顶部，这样一张图里能同时看到迁移卡与它下面的字段区。

场景 4 的选择与实际分布都不依赖 UI 新增控件名：变体 payload 从 ``fsm._tree``
（发现结果）现算，画面只做"展开 text 组 + 回顶"两件不碰私有的动作，迁移卡
（``StyleFontMigration``）的存在与否只影响诊断行，不影响能不能出图。右侧 UI
还在并行改动时，这张图就是后续字体迁移区域的验收底图。

**三个必须照抄 app 启动过程的点**（同 ``scripts/settings_render.py``）：

1. **不许设 ``QT_QPA_PLATFORM=offscreen``**：离屏平台插件下
   ``QFontDatabase.families()`` 是空的，文字全渲染成豆腐块。
2. **要装字体 + 翻译 + 主题**，且**翻译器必须早于 ``ui.*`` 导入**——树/右栏的
   组名是模块级 ``QCoreApplication.translate``，晚了就冻成英文。
3. **切主题时要连 ``pcfg.darkmode`` 一起切**：委托里取主题强调色（选中底色、
   库条目「模板」标签）走的是 ``ui/misc.py::get_theme_color``，无参时按
   ``pcfg.darkmode`` 选主题；只 ``setStyleSheet`` 会让两套主题的强调色对不上，
   出图上表现为"选中行左深右浅"，看着像产品 bug、其实是渲染台的坑。

进程内短路了 ``save_config`` 与 ``save_global_styles``：截图工具不该动用户配置，
也不该把假工程写进真实样式库。
"""

import argparse
import os
import os.path as osp
import sys

sys.path.insert(0, os.getcwd())

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import QApplication

# 与 ui/mainwindow.py::on_open_fontstyle_manager 里的对话框尺寸一致
PANEL_W, PANEL_H = 800, 540


def build_app(scale: int) -> QApplication:
    """按 ``launch.py`` 的方式起 QApplication（字体 + 语言 + 主题）。"""
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    app = QApplication([])
    app_font = QFont("Microsoft YaHei UI")
    if app_font.exactMatch():
        QApplication.setFont(app_font)

    from qtpy.QtCore import QTranslator

    from utils import shared
    from utils.config import load_config, pcfg

    load_config()
    pcfg.animation_fps = -1  # 静态截图：关掉动画，grab 才不会抓到中间帧

    from utils import config as config_mod
    from utils import global_styles as gs

    config_mod.save_config = lambda *a, **k: None
    gs.save_global_styles = lambda *a, **k: True

    translator = QTranslator()
    if translator.load(pcfg.display_lang, shared.TRANSLATE_DIR):
        app.installTranslator(translator)
        app._render_translator = translator  # installTranslator 不接管所有权
    return app


def apply_theme(app: QApplication, theme: str, darkmode: bool) -> None:
    from ui.misc import _resolve_theme, build_stylesheet_from_dict
    from utils.config import pcfg

    pcfg.darkmode = darkmode  # 与主题同步，见模块 docstring 第 3 点
    app.setStyleSheet(build_stylesheet_from_dict(dict(_resolve_theme(theme))))
    app.processEvents()


class _FakeBlk:
    def __init__(self, **kw):
        from utils.fontformat import FontFormat

        self.fontformat = FontFormat()
        for k, v in kw.items():
            setattr(self.fontformat, k, v)
        self.text = "こんにちは"
        self.translation = "你好"


class _FakeProj:
    def __init__(self, pages):
        self.pages = pages
        self.current_img = "p001.png"
        self.base_styles = []
        self.rerendered = []

    def mark_page_needs_rerender(self, p):
        self.rerendered.append(p)


def build_scene():
    """合成工程与样式库；库条目与项目样式同字体同方向，用于检验二者辨识度。"""
    from utils import global_styles as gs
    from utils.base_styles import BaseStyle
    from utils.fontformat import FontFormat

    body = [
        _FakeBlk(font_family="SimHei", vertical=True, font_size=26),
        _FakeBlk(font_family="SimHei", vertical=True, font_size=26),
        _FakeBlk(font_family="SimHei", vertical=True, font_size=26),
        _FakeBlk(font_family="SimHei", vertical=True, font_size=22, frgb=[255, 0, 0]),
        _FakeBlk(font_family="SimHei", vertical=True, font_size=22, frgb=[255, 0, 0]),
    ]
    title = [
        _FakeBlk(font_family="SimSun", font_size=40, italic=True),
        _FakeBlk(font_family="SimSun", font_size=40, italic=True),
    ]
    loose = [
        _FakeBlk(font_family="Microsoft YaHei", font_size=18),
        _FakeBlk(font_family="Microsoft YaHei", font_size=18),
        _FakeBlk(font_family="KaiTi", font_size=30, vertical=True),
    ]
    proj = _FakeProj({"p001.png": body + title, "p002.png": loose})
    proj.base_styles.append(
        BaseStyle("正文", FontFormat(font_family="SimHei", vertical=True, font_size=26))
    )
    proj.base_styles.append(
        BaseStyle("标题", FontFormat(font_family="SimSun", font_size=40, italic=True))
    )

    gs.global_styles.clear()
    gs.global_styles.extend(
        [
            BaseStyle(
                "正文 黑体",
                FontFormat(font_family="SimHei", vertical=True, font_size=26),
            ),
            BaseStyle(
                "标题 明朝",
                FontFormat(font_family="SimSun", font_size=40, italic=True),
            ),
            BaseStyle(
                "旁白",
                FontFormat(font_family="SimHei", font_size=20, frgb=[255, 255, 255]),
            ),
        ]
    )
    return proj


def _pick_variant(fsm):
    """One existing variant of the synthetic project: ``(payload, node, variant)``.

    变体不从 UI 控件里捞——UI 还在并行改，名字随时会变。这里直接读 discover
    结果 ``fsm._tree``（``utils/base_styles.py::BaseStyleNode.variants``），
    payload 形状与 ``StyleTreeWidget`` 的 UserRole 约定一致，走既有
    ``select_payload`` 选中。优先挑覆盖「字体区」（``FIELD_GROUPS["text"]``，
    含 font_size / font_family 等）的变体：变体只渲染 override 字段，字体区
    有内容才谈得上验收字体迁移那块。找不到变体返回 ``(None, None, None)``。
    """
    from utils.style_query import FIELD_GROUPS

    nodes = getattr(getattr(fsm, "_tree", None), "nodes", None) or []
    fallback = None
    for node in nodes:
        for var in node.variants:
            payload = {
                "type": "variant",
                "identity": node.base.identity,
                "key": var.key,
            }
            if fallback is None:
                fallback = (payload, node, var)
            if set(var.overrides) & set(FIELD_GROUPS["text"]):
                return payload, node, var
    return fallback if fallback is not None else (None, None, None)


def _frame_font_region(fsm) -> None:
    """Expand the font field group and keep the panel top in frame.

    字体迁移卡贴在右栏顶部（预览卡/参数 chips 之下、四个字段组之上，objectName
    ``StyleFontMigration``），而变体模式下四个组默认全收起（基线＝自身 → 无
    改动）。这里只展开 ``text`` 组、**不** ``ensureWidgetVisible`` 滚过去——
    滚到字段组会把迁移卡顶出画面；展开后把滚动条归零，让"迁移卡 + 字体组"
    同框。UI 改名/换结构时各步 getattr 兜底，退化成不调整而不是崩掉。
    """
    detail = fsm.detailContent
    card = (getattr(getattr(detail, "_panel", None), "_cards", None) or {}).get("text")
    expand = getattr(card, "set_collapsed", None)
    if expand is not None:
        expand(False)
    bar_fn = getattr(detail, "verticalScrollBar", None)
    if bar_fn is not None:
        bar_fn().setValue(0)


def _print_variant_state(theme: str, fsm, node, variant) -> None:
    """Echo the variant detail state (mode / visible groups / font-area fields).

    与模块其余部分同一口径：不看图也能核对分区与字段。``_panel``/``_cards``
    等私有名一律 getattr 兜底——并行期 UI 改名只该让这行诊断退化成问号，
    不该让渲染台崩掉。
    """
    from utils.style_query import FIELD_GROUPS

    detail = fsm.detailContent
    panel = getattr(detail, "_panel", None)
    cards = getattr(panel, "_cards", None) or {}
    editors = getattr(panel, "_editors", None) or {}
    print(
        f"  [{theme}] 变体: base={node.base.name!r} identity={node.base.identity} "
        f"count={variant.count} overrides={sorted(variant.overrides)}"
    )
    header = getattr(detail, "_header_info", None)
    print(
        f"      header={(header.text() if header is not None else '?')!r} "
        f"mode={getattr(detail, '_mode', '?')}"
    )
    for key in FIELD_GROUPS:
        card = cards.get(key)
        if card is None:
            continue
        collapsed_fn = getattr(card, "is_collapsed", None)
        status_lbl = getattr(card, "_status", None)
        print(
            f"      group {key}: visible={card.isVisible()} "
            f"collapsed={collapsed_fn() if collapsed_fn else '?'} "
            f"status={(status_lbl.text() if status_lbl is not None else '')!r}"
        )
    shown = [
        f for f in FIELD_GROUPS["text"] if f in editors and editors[f].isVisible()
    ]
    print(f"      text 组可见字段（字体区）= {shown}")
    card = getattr(detail, "_font_migration", None)
    if card is not None:
        hint = getattr(detail, "_migrate_hint", None)
        print(
            f"      StyleFontMigration 卡: visible={card.isVisible()} "
            f"hint={(hint.text() if hint is not None else '')!r}"
        )


def render(app: QApplication, out_dir: str, theme: str, darkmode: bool) -> None:
    from ui.fontstyle_manager import FontStyleManager, _DISPLAY_ROLE

    os.makedirs(out_dir, exist_ok=True)
    apply_theme(app, theme, darkmode)

    fsm = FontStyleManager()
    fsm.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    fsm.resize(PANEL_W, PANEL_H)
    fsm.set_project(build_scene(), None)
    fsm.refresh()
    fsm.show()
    app.processEvents()

    tree = fsm.styleTree
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        data = top.data(0, _DISPLAY_ROLE) or {}
        kids = [top.child(j) for j in range(top.childCount())]
        print(
            f"  [{theme}] 区带 {data.get('count')} 条 展开={top.isExpanded()} "
            f"{tree.tr(data.get('title', '')) if data.get('title') else top.text(0)!r}"
        )
        for child in kids:
            cdata = child.data(0, _DISPLAY_ROLE) or {}
            tag = cdata.get("tag")
            print(
                f"      {cdata.get('title')!r} count={cdata.get('count')}"
                + (f" tag={tree.tr(tag)!r}" if tag else "")
            )
    fsm.grab().save(osp.join(out_dir, f"{theme}_1_asis.png"))

    # 无选中态（刚打开的样子就在上面这张 1_asis，这里再钉一张写明的）
    detail = fsm.detailContent
    print(
        f"  [{theme}] 无选中: hint={detail._hint.isVisible()} "
        f"content={detail._content.isVisible()} "
        f"buttons={sum(bool(b.isVisible()) for b in (detail._reset_base_btn, detail._promote_btn, detail._add_library_btn, detail._delete_base_btn, detail._copy_project_btn, detail._delete_library_btn))}"
    )
    fsm.grab().save(osp.join(out_dir, f"{theme}_0_empty.png"))

    for payload, suffix in (
        ({"type": "global", "name": "正文 黑体"}, "2_library_selected"),
        ({"type": "base", "identity": ("SimHei", True)}, "3_project_selected"),
    ):
        tree.select_payload(payload)
        fsm._on_node_selected(payload)
        app.processEvents()
        fsm.grab().save(osp.join(out_dir, f"{theme}_{suffix}.png"))

    # ── 场景 4：选中已有变体（字体迁移区域的验收底图）────────────────
    saved = 3
    payload, node, variant = _pick_variant(fsm)
    if payload is None:
        print(f"  [{theme}] 变体场景: 合成工程没有变体，跳过 4_variant_selected")
    else:
        tree.select_payload(payload)
        fsm._on_node_selected(payload)
        app.processEvents()
        _frame_font_region(fsm)
        app.processEvents()
        fsm.grab().save(osp.join(out_dir, f"{theme}_4_variant_selected.png"))
        _print_variant_state(theme, fsm, node, variant)
        saved += 1
    print(f"[{theme}] saved {saved} shots -> {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=osp.join("tmp", "stylemgr_render"))
    parser.add_argument("--scale", type=int, default=2, help="渲染倍率（默认 2）")
    parser.add_argument(
        "--theme",
        choices=("dark", "light", "both"),
        default="both",
        help="渲染哪套主题（默认 both，各出一套图）",
    )
    args = parser.parse_args()

    app = build_app(args.scale)
    from utils.config import pcfg

    themes = ["dark", "light"] if args.theme == "both" else [args.theme]
    for theme in themes:
        name = getattr(pcfg, f"{theme}_theme")
        print(f"=== {theme} ({name}) ===")
        render(app, args.out, name, darkmode=theme == "dark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
