"""真机探针：全局替换后点击脏页（pageList / 搜索结果）是否跳错页。

用户报告：全局搜索替换内容后，点击脏页会错误跳转（点 2 页跳到 11 页）。
本探针在合成工程（15 页 × 3 块）上完整走一遍真实链路：

  1. commit_search 建结果树；
  2. 逐个模拟点击各页的搜索结果条目（走 selectionChanged 真实信号），
     校验落点页 == 期望页；
  3. on_replace 全局替换（弹窗自动点 Yes / 重渲询问点 Later 保留脏页）；
  4. 替换后再逐个点击 pageList 的第 2 项与搜索结果条目，校验落点。

只读排查用，不修改任何业务代码。用法：
    ./ballontrans_pylibs_win/python.exe scripts/probes/probe_dirty_page_jump.py
"""

import faulthandler
import os
import sys
import tempfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)

faulthandler.enable()


def _patch_messagebox():
    """自动应答：重渲询问点 Later（保留脏页）、替换确认点 Yes。"""
    from qtpy.QtWidgets import QMessageBox

    original_exec = QMessageBox.exec

    def _patched_exec(self):
        later = None
        yes = None
        for button in self.buttons():
            if button.text().lower() == "later":
                later = button
            if (
                self.buttonRole(button)
                == QMessageBox.ButtonRole.YesRole
            ):
                yes = button
        target = later or yes
        if target is not None:
            print(f"[probe] auto-click messagebox button: {target.text()!r}", flush=True)
            from qtpy.QtCore import QTimer

            QTimer.singleShot(200, target.click)
        return original_exec(self)

    QMessageBox.exec = _patched_exec


def _make_project(window, app, n_pages=15, n_blocks=3):
    import numpy as np

    from utils.io_utils import imwrite
    from utils.textblock import TextBlock

    tmp = tempfile.mkdtemp(prefix="probe_dirty_jump_")
    window._temp_project_dirs.add(tmp)
    for i in range(n_pages):
        img = np.full((1200, 900, 3), 200, dtype=np.uint8)
        imwrite(os.path.join(tmp, f"p{i:02d}.jpeg"), img, ext=".jpeg")

    window.openDir(tmp)
    app.processEvents()
    proj = window.imgtrans_proj
    for pname in list(proj.pages.keys()):
        for i in range(n_blocks):
            blk = TextBlock(
                xyxy=[80, 80 + i * 120, 480, 200 + i * 120],
                translation=f"演练 {pname} 目标词 {i}",
            )
            blk.rich_text = f"<p>演练 {pname} 目标词 {i}</p>"
            blk._bounding_rect = [80, 80 + i * 120, 400, 120]
            proj.pages[pname].append(blk)
    proj.current_img = next(iter(proj.pages))
    window.st_manager.updateSceneTextitems()
    app.processEvents()
    return tmp


def _click_search_result(window, app, pagename, blk_idx, expect_note):
    """模拟点击某页的第一个搜索结果条目（走真实 selectionChanged）。"""
    tree = window.global_search_widget.search_tree
    proj = window.imgtrans_proj
    page_node = None
    for r in range(tree.rowCount()):
        node = tree.sm.item(r, 0)
        if node.pagename == pagename:
            page_node = node
            break
    if page_node is None or page_node.rowCount() == 0:
        print(f"[probe] {expect_note}: 树中无 {pagename} 的结果条目", flush=True)
        return
    child = page_node.child(0, 0)

    from qtpy.QtCore import QItemSelection, QItemSelectionModel, QItemSelectionRange, Qt

    sm = tree.selectionModel()
    idx = tree.sm.indexFromItem(child)
    sel = QItemSelection()
    sel.append(QItemSelectionRange(idx, idx))
    sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    for _ in range(5):
        app.processEvents()
    landed = proj.current_img
    status = "OK" if landed == pagename else "★★跳错页"
    print(
        f"[probe] {expect_note}: 点击 {pagename} blk{blk_idx} → 落点 {landed} → {status}",
        flush=True,
    )


def _click_pagelist_row(window, app, row, expect_note):
    from qtpy.QtCore import Qt

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    want = pages[row]
    window.pageList.setCurrentRow(row)
    for _ in range(5):
        app.processEvents()
    landed = proj.current_img
    status = "OK" if landed == want else "★★跳错页"
    print(
        f"[probe] {expect_note}: pageList 第{row + 1}项({want}) → 落点 {landed} → {status}",
        flush=True,
    )


def main():
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication

    _patch_messagebox()
    app = QApplication([])

    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    from ui.mainwindow import MainWindow

    window = MainWindow(app, config, open_dir="")
    window.show()
    app.processEvents()

    _make_project(window, app)
    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    print(f"[probe] pages={proj.num_pages} current={proj.current_img}", flush=True)

    gsw = window.global_search_widget
    window.on_global_search()  # 打开全局搜索面板
    app.processEvents()

    from qtpy.QtCore import Qt

    gsw.search_editor.setPlainText("目标词")
    gsw.commit_search()
    app.processEvents()
    print(f"[probe] 搜索完成 counter_sum={gsw.counter_sum}", flush=True)

    # ── 阶段1：替换前逐页点击搜索结果 ──
    for r in (1, 5, 10):  # 第2、6、11页
        _click_search_result(window, app, pages[r], 0, f"替换前 第{r + 1}页")

    # ── 阶段2：全局替换（确认点 Yes，重渲询问点 Later 保留脏页） ──
    gsw.replace_editor.setPlainText("替换词")
    gsw.on_replace()
    for _ in range(20):
        app.processEvents()
    dirty = [p for p in proj.pages if proj.page_needs_rerender(p)]
    print(
        f"[probe] 替换后 current={proj.current_img} 脏页数={len(dirty)}",
        flush=True,
    )
    print(f"[probe] 替换后 pageList 行数={window.pageList.count()}", flush=True)

    # ── 阶段3：替换后点 pageList 第2项与搜索结果 ──
    _click_pagelist_row(window, app, 1, "替换后 pageList")
    _click_pagelist_row(window, app, 10, "替换后 pageList")
    for r in (1, 5, 10):
        _click_search_result(window, app, pages[r], 0, f"替换后(陈旧结果树) 第{r + 1}页")

    # 页序一致性：pageList 行序 vs pages 字典序
    list_order = [
        window.pageList.item(r).text() for r in range(window.pageList.count())
    ]
    print(
        f"[probe] pageList 与 pages 字典序一致: {list_order == pages}",
        flush=True,
    )

    faulthandler.dump_traceback_later(120, exit=True)
    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
