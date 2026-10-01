"""真机探针（真实工程副本）：全局替换后用真实鼠标事件点脏页，验证落点。

与 probe_dirty_page_jump.py 的差别：
  - 用真实工程（--project 指定的目录会先拷贝到临时目录，原目录只读）；
  - pageList 点击走 QMouseEvent press→release（覆盖「点击期间列表被
    updatePageList 重建」的真实时序）；
  - 全程捕获 Qt slot 内的异常栈。

用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/probe_dirty_page_jump_real.py --project "D:/汉化/施工区副本"
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


def _install_excepthook():
    def _hook(etype, value, tb):
        print("[probe] ★未捕获异常★", flush=True)
        import traceback

        traceback.print_exception(etype, value, tb)

    sys.excepthook = _hook


def _mouse_click(widget, pos):
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest

    QTest.mouseClick(
        widget.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos
    )


def _click_pagelist_row(window, app, row, note):
    from qtpy.QtCore import Qt

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    want = pages[row]
    item = window.pageList.item(row)
    rect = window.pageList.visualItemRect(item)
    _mouse_click(window.pageList, rect.center())
    for _ in range(10):
        app.processEvents()
    landed = proj.current_img
    row_now = window.pageList.currentRow()
    status = "OK" if landed == want else "★★跳错页"
    print(
        f"[probe] {note}: 鼠标点第{row + 1}项({want}) → 落点 {landed} "
        f"列表currentRow={row_now} → {status}",
        flush=True,
    )


def _click_search_result(window, app, pagename, note):
    from qtpy.QtCore import QItemSelection, QItemSelectionModel, QItemSelectionRange

    tree = window.global_search_widget.search_tree
    proj = window.imgtrans_proj
    for r in range(tree.rowCount()):
        node = tree.sm.item(r, 0)
        if node.pagename == pagename and node.rowCount():
            child = node.child(0, 0)
            idx = tree.sm.indexFromItem(child)
            sel = QItemSelection()
            sel.append(QItemSelectionRange(idx, idx))
            tree.selectionModel().select(
                sel, QItemSelectionModel.SelectionFlag.ClearAndSelect
            )
            for _ in range(10):
                app.processEvents()
            landed = proj.current_img
            status = "OK" if landed == pagename else "★★跳错页"
            print(
                f"[probe] {note}: 点搜索结果({pagename}) → 落点 {landed} → {status}",
                flush=True,
            )
            return
    print(f"[probe] {note}: 树中无 {pagename}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--keyword", default="的")
    parser.add_argument("--replacement", default="〇")
    args = parser.parse_args()

    _install_excepthook()
    _patch_messagebox()

    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication

    app = QApplication([])

    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    tmp = tempfile.mkdtemp(prefix="probe_dirty_real_")
    # JSON 文件名 = imgtrans_<目录名>.json，目录名必须与原工程一致
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
    print(f"[probe] 前5页: {pages[:5]}", flush=True)
    for pname in pages[:3]:
        for blk in proj.pages[pname][:2]:
            print(
                f"[probe] 样本 {pname}: src={blk.get_text()!r:.60} "
                f"trans={blk.translation!r:.60}",
                flush=True,
            )

    window.on_global_search()
    app.processEvents()

    gsw = window.global_search_widget
    gsw.search_editor.setPlainText(args.keyword)
    # 默认范围=译文；真实工程译文常为空，切到「原文/译文」双范围
    gsw.range_combobox.setCurrentIndex(2)
    gsw.commit_search()
    app.processEvents()
    print(f"[probe] 搜索 {args.keyword!r}: counter_sum={gsw.counter_sum}", flush=True)

    # 替换前先点几页（真实鼠标）
    for r in (1, 5):
        _click_pagelist_row(window, app, r, f"替换前 pageList 第{r + 1}项")

    gsw.replace_editor.setPlainText(args.replacement)
    gsw.on_replace()
    for _ in range(30):
        app.processEvents()
    dirty = [p for p in proj.pages if proj.page_needs_rerender(p)]
    print(
        f"[probe] 替换后 current={proj.current_img} 脏页数={len(dirty)} "
        f"前几个脏页={dirty[:5]}",
        flush=True,
    )

    # 依次用真实鼠标点脏页（取前几个脏页的行号）
    for pname in dirty[:6]:
        if pname in pages:
            _click_pagelist_row(
                window, app, pages.index(pname), f"替换后点脏页 {pname}"
            )

    # ── 附加态：当前页有未落盘编辑会话时点脏页 ──
    # （切换链路前半段要先提交旧页会话；若中途异常会出现半切换态）
    if dirty:
        target = dirty[0]
        pw_list = window.st_manager.pairwidget_list
        if pw_list:
            edit = pw_list[0].e_trans
            edit.setFocus()
            cursor = edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText("附言")
            for _ in range(5):
                app.processEvents()
            print(
                f"[probe] 已在当前页 {proj.current_img} 制造未落盘编辑，"
                f"开始点脏页 {target}",
                flush=True,
            )
            _click_pagelist_row(
                window, app, pages.index(target), "编辑会话激活时点脏页"
            )

    # 再走搜索结果树（陈旧）
    for pname in dirty[:3]:
        _click_search_result(window, app, pname, f"替换后陈旧树点 {pname}")

    faulthandler.dump_traceback_later(180, exit=True)
    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
