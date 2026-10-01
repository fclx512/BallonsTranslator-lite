"""真机探针（对齐用户确切操作流）：全局替换 → 稍后处理 → 左侧页列表点页码。

用户报告流：全局搜索替换 → 重渲询问选「稍后」（保留脏页）→ 关搜索面板、
切到左侧页列表 → 点页码 → 跳到别的页。

与 probe_dirty_page_jump_real.py 的差别：本探针把交互做成用户同款——
先关搜索 overlay、打开页列表 overlay，再 QTest 点击**可见的**页列表；
另测两个变体：
  A. 缩略图模式（≤100 页）逐个点脏页；
  B. 非缩略图模式（monkeypatch 阈值，模拟 >100 页工程）点脏页；
  C. 懒重渲进行中快速连点第二页（排队事件顺序处理）。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/probe_user_flow_pageclick.py \
        --project "D:/汉化/施工区副本" --keyword "私の"
"""

import argparse
import faulthandler
import os
import shutil
import sys
import tempfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)

faulthandler.enable()


def _patch_messagebox():
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QMessageBox

    original_exec = QMessageBox.exec

    def _patched_exec(self):
        later = None
        yes = None
        for button in self.buttons():
            if button.text().lower() == "later":
                later = button
            if self.buttonRole(button) == QMessageBox.ButtonRole.YesRole:
                yes = button
        target = later or yes
        if target is not None:
            print(f"[probe] auto-click: {target.text()!r}", flush=True)
            QTimer.singleShot(200, target.click)
        return original_exec(self)

    QMessageBox.exec = _patched_exec


def _click_row(window, app, row, note, expect=None):
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    want = expect or pages[row]
    item = window.pageList.item(row)
    if item is None:
        print(f"[probe] {note}: row {row} 不存在", flush=True)
        return
    rect = window.pageList.visualItemRect(item)
    QTest.mouseClick(
        window.pageList.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        rect.center(),
    )
    for _ in range(10):
        app.processEvents()
    landed = proj.current_img
    row_now = window.pageList.currentRow()
    row_of_landed = pages.index(landed) if landed in pages else -1
    ok = landed == want and row_now == row_of_landed
    status = "OK" if ok else "★★落点/行号不一致"
    print(
        f"[probe] {note}: 点第{row + 1}项(期望 {want}) → 落点 {landed} "
        f"(列表第{row_now + 1}项) → {status}",
        flush=True,
    )


def _setup_panels(window, app):
    """用户同款面板态：关搜索 overlay，打开页列表 overlay。"""
    if window.leftBar.globalSearchChecker.isChecked():
        window.leftBar.globalSearchChecker.setChecked(False)
        window.on_global_search()
    window.leftBar.showPageListLabel.setChecked(True)
    window.pageLabelStateChanged()
    app.processEvents()
    print(
        f"[probe] 面板态: 搜索可见={window.global_search_widget.isVisible()} "
        f"页列表可见={window.pageList.isVisible()}",
        flush=True,
    )


def _run_variant(window, app, dirty, phase):
    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    # 点全部脏页 + 两个非脏页
    targets = [pages.index(p) for p in dirty if p in pages]
    targets += [0, len(pages) - 1]
    for r in targets:
        _click_row(window, app, r, f"{phase} 点第{r + 1}项")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--keyword", default="私の")
    parser.add_argument("--replacement", default="わたしの")
    args = parser.parse_args()

    _patch_messagebox()

    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication

    app = QApplication([])

    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    tmp = tempfile.mkdtemp(prefix="probe_user_flow_")
    dst = os.path.join(tmp, os.path.basename(os.path.normpath(args.project)))
    shutil.copytree(args.project, dst)

    from ui.mainwindow import MainWindow

    window = MainWindow(app, config, open_dir="")
    window.show()
    app.processEvents()
    window.openDir(dst)
    app.processEvents()

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    print(f"[probe] pages={proj.num_pages} current={proj.current_img}", flush=True)

    # ── 用户流：全局搜索替换 → 稍后 ──
    window.on_global_search()
    app.processEvents()
    gsw = window.global_search_widget
    gsw.search_editor.setPlainText(args.keyword)
    gsw.range_combobox.setCurrentIndex(2)
    gsw.commit_search()
    app.processEvents()
    print(f"[probe] 搜索 {args.keyword!r}: counter_sum={gsw.counter_sum}", flush=True)

    gsw.replace_editor.setPlainText(args.replacement)
    gsw.on_replace()
    for _ in range(30):
        app.processEvents()
    dirty = [p for p in proj.pages if proj.page_needs_rerender(p)]
    print(f"[probe] 替换后脏页数={len(dirty)} current={proj.current_img}", flush=True)

    # ── 变体 A：缩略图模式，用户同款面板态，逐个点脏页 ──
    _setup_panels(window, app)
    _run_variant(window, app, dirty, "A(缩略图)")

    # ── 变体 B：非缩略图模式（>100 页工程形态） ──
    from utils import shared

    shared.PAGELIST_THUMBNAIL_MAXNUM = 1  # 强制走非缩略图分支
    # 重新标脏（A 阶段点击已把脏页清了）→ 再跑一轮替换
    for p in dirty:
        proj.mark_page_needs_rerender(p)
    window.updatePageList()
    app.processEvents()
    _run_variant(window, app, dirty, "B(非缩略图)")
    shared.PAGELIST_THUMBNAIL_MAXNUM = 100

    # ── 变体 C：懒重渲卡顿期间快速连点两页 ──
    for p in dirty:
        proj.mark_page_needs_rerender(p)
    window.updatePageList()
    app.processEvents()
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest

    r1, r2 = pages.index(dirty[0]), pages.index(dirty[-1])
    for r in (r1, r2):
        rect = window.pageList.visualItemRect(window.pageList.item(r))
        QTest.mouseClick(
            window.pageList.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        app.processEvents()  # 不排空事件，模拟连点
    for _ in range(20):
        app.processEvents()
    landed = proj.current_img
    ok = landed == pages[r2] and window.pageList.currentRow() == r2
    print(
        f"[probe] C(连点): 连点第{r1 + 1}、{r2 + 1}项 → 最终落点 {landed} "
        f"(列表第{window.pageList.currentRow() + 1}项) → "
        f"{'OK' if ok else '★★不一致'}",
        flush=True,
    )

    faulthandler.dump_traceback_later(300, exit=True)
    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
