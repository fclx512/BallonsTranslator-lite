"""真机探针：打开无 base_styles 的上游/旧版工程，跑通兼容弹窗整条链路。

缘起（2026-10-01 用户实测）：打开上游工程 → 弹出兼容提示 → 卡死闪退，
终端无报错。本探针即当时的定位工具，也留作该链路的复算台——真因与修法见
``ui/mainwindow.py::_maybe_seed_upstream_styles`` 与
``tests/test_upstream_notice_dialog.py``（无父 QCheckBox 临时对象被 PyQt 回收
→ box 内部指针悬空 → 弹窗布局/``checkBox()`` 访问即 access violation）。

要点：
- **必须窗口模式**（FramelessWindow 在 offscreen 起不来）；
- ``QMessageBox.exec`` 打点：文案、按钮、复选框是否存活（``findChildren``）、
  延迟自动点击（按钮可能到 showEvent 才补上，故点击在延迟回调里做）；
- ``faulthandler`` 看门狗兜卡死——无声 AV 时 Python 栈行号是下游受害者，
  仅作起点；
- ``PROBE_TICK_CHECKBOX=1`` 顺带验证「不再提示」勾选路径（会写用户配置）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/probe_open_upstream_project.py "D:\\汉化\\施工区"
"""

import faulthandler
import os
import sys
import time

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)
faulthandler.enable()


def log(msg: str) -> None:
    print(f"[probe] {msg}", flush=True)


def main() -> int:
    proj_dir = sys.argv[1] if len(sys.argv) > 1 else r"D:\汉化\施工区"

    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication, QCheckBox, QMessageBox

    app = QApplication([])

    original_exec = QMessageBox.exec

    def patched_exec(self):
        buttons = [b.text() for b in self.buttons()]
        log(f"QMessageBox.exec icon={self.icon()} text={self.text()!r}")
        log(f"  informative={self.informativeText()!r}")
        log(f"  buttons={buttons} checkbox={self.checkBox()!r}")
        log(f"  live QCheckBox children={len(self.findChildren(QCheckBox))}")
        click_delay = int(os.environ.get("PROBE_CLICK_DELAY_MS", "1500"))
        want = os.environ.get("PROBE_CLICK_TEXT", "")
        started = time.monotonic()

        def _role(button) -> int:
            role = self.buttonRole(button)
            return role.value if hasattr(role, "value") else int(role)

        def _click():
            # 按钮可能到 showEvent 才补上（无按钮构造 → exec 时补 OK），
            # 因此点击在延迟回调里做，而不是 exec 之前。
            role_of = {self.ButtonRole.AcceptRole.value, self.ButtonRole.YesRole.value}
            candidates = [(b.text(), _role(b)) for b in self.buttons()]
            log(f"  buttons at click time={candidates}")
            target = None
            for button in self.buttons():
                if want and want.casefold() in button.text().casefold():
                    target = button
                    break
            if target is None:
                for button in self.buttons():
                    if _role(button) in role_of:
                        target = button
                        break
            tick = os.environ.get("PROBE_TICK_CHECKBOX") == "1"
            if tick and self.checkBox() is not None:
                self.checkBox().setChecked(True)
                log("  -> ticked 'don't show again' checkbox")
            if target is None:
                log("  -> no clickable button found; dialog left open")
                return
            log(f"  -> auto-click {target.text()!r}")
            target.click()

        QTimer.singleShot(click_delay, _click)
        ret = original_exec(self)
        log(f"  exec returned {int(ret)} after {time.monotonic() - started:.1f}s")
        return ret

    QMessageBox.exec = patched_exec

    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    from ui.mainwindow import MainWindow

    # PROBE_OPEN_AT_STARTUP=1：走启动期开项目（命令行给路径 / 启动开最近），
    # 弹窗出现在 MainWindow 构造期、窗口尚未 show——另一条到达同一弹窗的路径。
    at_startup = os.environ.get("PROBE_OPEN_AT_STARTUP") == "1"
    log(f"constructing MainWindow (open_dir={proj_dir if at_startup else ''!r})")
    faulthandler.dump_traceback_later(60, exit=True)
    window = MainWindow(app, config, open_dir=proj_dir if at_startup else "")
    log("MainWindow constructed")
    window.show()
    app.processEvents()
    log("window shown")

    if at_startup:
        log("project opened during construction (dialog ran before window.show())")
    else:
        log(f"opening project {proj_dir!r} via openDir")
        window.openDir(proj_dir)
        log("openDir returned")

    for _ in range(30):
        app.processEvents()
    log("events drained")

    proj = window.imgtrans_proj
    log(f"base_styles after open: {[bs.name for bs in proj.base_styles]}")
    from utils.config import text_styles

    log(f"quick style bar after open: {[fs._style_name for fs in text_styles]}")
    log(f"upstream_diff_notice_dismissed={config.upstream_diff_notice_dismissed}")

    faulthandler.cancel_dump_traceback_later()
    log("watchdog cancelled; entering event loop briefly")
    QTimer.singleShot(1500, app.quit)
    app.exec()
    log("clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
