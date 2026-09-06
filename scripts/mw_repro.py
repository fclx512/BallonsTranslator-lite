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
sys.argv = [sys.argv[0]]  # 吸收 argparse 之外的启动参数

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
        "--scenario", choices=["group-undo", "none"], default="group-undo",
        help="group-undo=高级对齐→组化确认弹窗→撤销→重渲 全链路",
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


def _make_synthetic(window, app, args):
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
    for pname in list(proj.pages.keys()):
        for i in range(args.blocks):
            blk = TextBlock(
                xyxy=[80, 80 + i * 120, 480, 200 + i * 120],
                translation=f"演练 {pname} {i}",
            )
            blk.rich_text = rich.format(f"{pname}-{i}")
            blk.font_family = "SimHei"
            blk._bounding_rect = [80, 80 + i * 120, 400, 120]
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
        _make_synthetic(window, app, args)

    if args.scenario == "group-undo":
        _scenario_group_undo(window, app, args)
    else:
        print("[mw_repro] scenario=none：主窗口已就绪，进入事件循环", flush=True)

    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
