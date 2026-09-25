"""MainWindow 在线演练台：拉起真实主窗口做模拟复现与交互驱动。

静态审查与离屏单测覆盖不到的问题类别——真实绘制路径、原生模态对话
框、GC 时机、模态嵌套、FramelessWindow win32 交互——用本工具在真机
上演练。起源：撤销阶段4第二批确认弹窗的 GC 悬空 AV 闪退（见
docs/基础速查/经验教训.md §3.3），当时以临时脚本定位，本工具为其
常驻化。

要点（来自那次排查的直接教训）：
- 必须**窗口模式**（offscreen 拉不起 FramelessWindow，win32 句柄无效）；
- 自动点击确认弹窗须延迟 ≥200ms（弹窗绘制完成前点击是另一类崩溃源）；
- faulthandler 常开；无声 AV 的 Python 栈行号是下游受害者，仅作起点。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe scripts/mw_repro.py [options]
"""

import argparse
import faulthandler
import os
import sys
import tempfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)
# 注意：不可在此吞掉 sys.argv——argparse 的场景参数靠它传递
# （QApplication 以空参数构造，CLI 参数不会泄入 Qt）。

faulthandler.enable()


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project", metavar="DIR",
        help="打开真实工程（只读演练：强制 scenario=none，不跑改写场景）",
    )
    parser.add_argument("--pages", type=int, default=2, help="合成工程页数")
    parser.add_argument("--blocks", type=int, default=8, help="每页块数")
    parser.add_argument(
        "--scenario",
        choices=["group-undo", "stroke-switch", "none"], default="group-undo",
        help="group-undo=高级对齐→组化确认弹窗→撤销→重渲 全链路；"
        "stroke-switch=竖排描边块快速切页（闪退回归演练）",
    )
    parser.add_argument(
        "--switch-rounds", type=int, default=12,
        help="stroke-switch 场景的快速切页轮数",
    )
    parser.add_argument(
        "--confirm-delay", type=int, default=200,
        help="自动点击确认弹窗 AcceptRole 按钮的延迟 ms（勿低于 200）",
    )
    parser.add_argument(
        "--no-panel", action="store_true", help="不开历史面板（默认开）"
    )
    parser.add_argument(
        "--no-show", action="store_true",
        help="不显示主窗口（默认显示，走真实绘制路径）",
    )
    parser.add_argument(
        "--watchdog", type=int, default=60,
        help="卡死看门狗秒数：到点打印全线程 Python 栈并退出",
    )
    return parser.parse_args()


def _setup_qt(args):
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication, QMessageBox

    app = QApplication([])
    if args.scenario == "group-undo" or args.confirm_delay:
        # 自动点击真实确认弹窗的 AcceptRole 按钮（模拟真人手点节奏）
        original_exec = QMessageBox.exec

        def _patched_exec(self):
            for button in self.buttons():
                if self.buttonRole(button) == self.ButtonRole.AcceptRole:
                    QTimer.singleShot(args.confirm_delay, button.click)
                    break
            return original_exec(self)

        QMessageBox.exec = _patched_exec
    return app, QTimer


def _open_mainwindow(app, args):
    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    # 演练台禁用两类启动副作用
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    from ui.mainwindow import MainWindow

    window = MainWindow(app, config, open_dir=args.project or "")
    if not args.no_show:
        window.show()
    app.processEvents()

    if args.project:
        window.openDir(args.project)
        app.processEvents()
    return window


def _make_synthetic(window, app, args, style="plain"):
    """合成临时工程：N 页 × M 块（富文本 + 描边），保证渲染路径真实。"""
    import numpy as np

    from utils.io_utils import imwrite
    from utils.textblock import TextBlock

    tmp = tempfile.mkdtemp(prefix="mw_repro_")
    window._temp_project_dirs.add(tmp)
    for i in range(args.pages):
        img = np.full((1200, 900, 3), 200, dtype=np.uint8)
        imwrite(os.path.join(tmp, f"p{i:02d}.jpeg"), img, ext=".jpeg")

    window.openDir(tmp)
    app.processEvents()
    proj = window.imgtrans_proj

    rich = "<p style=\"color:#222\">演练文本 <b>{}</b> 行一<br>行二内容</p>"
    # 竖排 + 收紧字距：真实字体下原布局按 85% 字距折列，描边克隆经
    # toHtml 往返丢字距后按 100% 折列——两文档行结构漂移（闪退回归场景）
    vertical_rich = (
        "<p style=\"color:#222\">"
        "<span style=\"letter-spacing:-0.15em\">"
        "竖排描边演练：这一段文本足够长，会让竖排布局在真实字体下"
        "折出多列，从而触发描边克隆文档与原布局之间的行结构漂移，"
        "用于复现快速切图时的 IndexError 闪退。继续补充更多文字，"
        "确保即使单列容量较大也能折出至少三列。"
        "</span></p>"
    )
    for pname in list(proj.pages.keys()):
        for i in range(args.blocks):
            if style == "vertical-stroke":
                if i >= 2:  # 竖排块占满整页，两块即可
                    break
                blk = TextBlock(
                    xyxy=[80, 80, 480, 820],
                    translation=f"竖排演练 {pname} {i}",
                )
                blk._bounding_rect = [80, 80, 400, 740]
            else:
                blk = TextBlock(
                    xyxy=[80, 80 + i * 120, 480, 200 + i * 120],
                    translation=f"演练 {pname} {i}",
                )
            if style == "vertical-stroke":
                blk.rich_text = vertical_rich
                blk.vertical = True
            else:
                blk.rich_text = rich.format(f"{pname}-{i}")
                blk._bounding_rect = [80, 80 + i * 120, 400, 120]
            blk.font_family = "SimHei"
            blk.fontformat.stroke_width = 2.0
            proj.pages[pname].append(blk)
    proj.current_img = next(iter(proj.pages))
    window.st_manager.updateSceneTextitems()
    app.processEvents()


def _open_history_panel(app):
    from ui.history_panel import HistoryPanel

    panel = HistoryPanel()
    panel.show()
    app.processEvents()
    print(f"[mw_repro] history panel bound: {panel.stack is not None}", flush=True)


def _scenario_group_undo(window, app, args):
    """高级对齐 → 组化确认弹窗（自动点击）→ 撤销 → 重渲 全链路。"""
    import faulthandler

    proj = window.imgtrans_proj
    window.execute_advanced_align(None, 100.0, "top", "y")
    app.processEvents()
    stack = window.canvas.text_undo_stack
    print(
        f"[mw_repro] stack count={stack.count()} index={stack.index()} "
        f"dirty={[p for p in proj.pages if proj.page_needs_rerender(p)]}",
        flush=True,
    )

    if not args.no_panel:
        _open_history_panel(app)

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    window.canvas.undo()
    for _ in range(20):
        app.processEvents()
    faulthandler.cancel_dump_traceback_later()

    print(
        f"[mw_repro] after undo: index={stack.index()} count={stack.count()} "
        f"dirty={[p for p in proj.pages if proj.page_needs_rerender(p)]}",
        flush=True,
    )
    print("[mw_repro] SCENARIO OK: group-undo", flush=True)


def _scenario_stroke_switch(window, app, args):
    """竖排描边块快速切页：克隆描边与原布局行结构漂移的闪退回归演练。

    用户反馈的复现路径：竖排文本 + 文字描边，快速切换图片时描边渲染
    走克隆文档路径，克隆经 toHtml 往返丢 letter-spacing 后行结构与原
    布局漂移，曾以旧偏移表索引新结构 IndexError 闪退（修复见
    vertical_layout.py::updateDrawOffsets 的形状校验守卫）。
    """
    import faulthandler

    from qtpy.QtCore import Qt

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    if len(pages) < 2:
        raise SystemExit("[mw_repro] stroke-switch 需要至少 2 页")

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    for r in range(args.switch_rounds):
        nxt = pages[(r + 1) % len(pages)]
        rows = window.pageList.findItems(nxt, Qt.MatchFlag.MatchExactly)
        # 走完整 UI 切页链路（会话落账、条件保存、场景重建、重绘）
        window.pageList.setCurrentItem(rows[0])
        for _ in range(8):
            app.processEvents()
        # 模拟切换 churn 中的字号自适应（应用自身的 setRelFontSize）：
        # relayout 抑制窗口内每次 mergeCharFormat 同步触发 contentsChanged
        # → 描边光栅重生成 → 克隆描边路径，曾以旧偏移表撞新文档结构闪退
        items = window.st_manager.textblk_item_list
        if items:
            items[0].setRelFontSize(1.1 if r % 2 == 0 else 0.9)
            for _ in range(4):
                app.processEvents()

    # 竞态演练段：复现引擎事务的抑制窗口——字号/字体/文本应用等事务
    # 在 relayout_on_changed=False 下改文档（行结构随之变化），窗口期内
    # 的同步 contentsChanged → 描边光栅重生成会以旧偏移表克隆描边。
    # 修复前此处以用户同款调用栈 IndexError 闪退。
    from qtpy.QtGui import QTextCursor

    items = window.st_manager.textblk_item_list
    if items:
        item = items[0]
        layout = item.layout
        layout.relayout_on_changed = False
        cursor = QTextCursor(item.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(
            "竞态演练：这段明显更长的文本会在 relayout 抑制窗口内改变行结构，"
            "让克隆文档的列数超过原布局的旧偏移表，从而复现 IndexError 闪退。"
            "继续补足长度：竖排布局每字符占一行，行数随字符数线性增长，"
            "所以新文本必须比原文更长才能让形状失配朝越界方向发生，"
            "这与用户切换图片时新页面文本更长的情形一致。"
            "竞态演练：这段明显更长的文本会在 relayout 抑制窗口内改变行结构，"
            "让克隆文档的列数超过原布局的旧偏移表，从而复现 IndexError 闪退。"
            "继续补足长度：竖排布局每字符占一行，行数随字符数线性增长，"
            "所以新文本必须比原文更长才能让形状失配朝越界方向发生。"
        )
        layout.relayout_on_changed = True
        layout.reLayoutEverything()
        for _ in range(8):
            app.processEvents()
    faulthandler.cancel_dump_traceback_later()

    print(
        f"[mw_repro] stroke-switch: {args.switch_rounds} 轮切页完成，"
        f"current={proj.current_img}",
        flush=True,
    )
    print("[mw_repro] SCENARIO OK: stroke-switch", flush=True)


def main():
    args = _parse_args()
    if args.project and args.scenario != "none":
        # 真实工程只读：改写场景会动并保存用户数据，一律拒绝
        print(
            "[mw_repro] --project 只读演练，--scenario 强制为 none"
            "（改写场景请用合成工程）",
            flush=True,
        )
        args.scenario = "none"

    app, QTimer = _setup_qt(args)
    window = _open_mainwindow(app, args)
    print(
        f"[mw_repro] pages={window.imgtrans_proj.num_pages} "
        f"current={window.imgtrans_proj.current_img}",
        flush=True,
    )

    if not args.project:
        _make_synthetic(
            window, app, args,
            style=(
                "vertical-stroke"
                if args.scenario == "stroke-switch" else "plain"
            ),
        )

    if args.scenario == "group-undo":
        _scenario_group_undo(window, app, args)
    elif args.scenario == "stroke-switch":
        _scenario_stroke_switch(window, app, args)
    else:
        print("[mw_repro] scenario=none：主窗口已就绪，进入事件循环", flush=True)

    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
