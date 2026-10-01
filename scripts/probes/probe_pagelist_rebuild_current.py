"""只读探针：复现 updatePageList 重建 pageList 时 currentItemChanged 的触发序列。

背景：global_search_widget.pages_dirtied 直连 MainWindow.updatePageList（无信号屏蔽）。
updatePageList 内部 clear() + addItem(...) + setCurrentItem(...)。
若 Qt 在空列表插入首项时自动置 current 并发 currentItemChanged，
pageListCurrentItemChanged 会重入并切到第一页。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QListWidget, QListWidgetItem

app = QApplication(sys.argv)

lst = QListWidget()
events = []


def on_current_changed(item):
    name = item.text() if item is not None else None
    events.append(("currentItemChanged", name, lst.currentRow()))
    # 模拟 pageListCurrentItemChanged 的重入副作用：记录此刻会被切到哪页


lst.currentItemChanged.connect(on_current_changed)

# 模拟初始态：已打开项目，当前在第 2 页（index 1）
lst.addItem(QListWidgetItem("page01"))
lst.addItem(QListWidgetItem("page02"))
lst.addItem(QListWidgetItem("page03"))
events.append(("init", "setCurrentRow(1)", None))
lst.setCurrentRow(1)

# 模拟 updatePageList：clear + 逐项 addItem + setCurrentItem
events.append(("--- rebuild start ---", None, None))
lst.clear()
for name in ("page01", "page02", "page03"):
    lst.addItem(QListWidgetItem(name))
    if name == "page02":  # 假设当前页是 page02
        pass
events.append(("--- rebuild done, now setCurrentItem(page02) ---", None, None))
from qtpy.QtCore import Qt as QtC
lst.setCurrentItem(lst.findItems("page02", QtC.MatchFlag.MatchExactly)[0])

print("events:")
for e in events:
    print("  ", e)
print("final current:", lst.currentItem().text() if lst.currentItem() else None,
      "row:", lst.currentRow())
