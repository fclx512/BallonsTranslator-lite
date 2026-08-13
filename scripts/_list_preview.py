"""Generate dark/light preview images for the vertical list quick menu.

Run with the bundled interpreter:
    ./ballontrans_pylibs_win/python.exe scripts/_list_preview.py

Outputs:
    scripts/_list_preview_dark.png
    scripts/_list_preview_light.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtCore import QObject, QPoint, Signal
from qtpy.QtGui import QFont, QPixmap
from qtpy.QtWidgets import QApplication

from utils import shared
from utils.config import load_config, pcfg

_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_list_preview_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)

# A half-ring list menu with a realistic mix of labels/lengths.
pcfg.pie_menus = [{
    "id": "edit",
    "name": "Edit Menu",
    "trigger": "Tab",
    "layout": "list",
    "direction": "right",
    "sectors": 8,
    "slots": [[] for _ in range(8)],
    "panels": [
        ["copy", "paste", "delete"],
        ["translate", "ocr", "ocr_translate"],
        ["ocr_translate_inpaint", "copy_src", "paste_src"],
    ],
}]

_LABEL_ZH = {
    "OCR and translate": "OCR并翻译",
    "OCR": "OCR",
    "Copy": "复制",
    "Paste": "粘贴",
    "Delete": "删除",
    "Merge": "合并",
    "Align Left Edges": "左边缘对齐",
    "Align Right Edges": "右边缘对齐",
    "Align Horizontal Centers": "水平中心对齐",
    "translate": "翻译",
    "Reset Angle": "重置角度",
    "Squeeze": "收缩",
    "OCR, translate and inpaint": "OCR、翻译并修复",
    "Copy source text": "复制原文",
    "Paste source text": "粘贴原文",
}


class MockCanvas(QObject):
    delete_textblks = Signal(int)
    merge_textblks = Signal()
    align_textblks = Signal(str)
    run_blktrans = Signal(int)
    reset_angle = Signal()
    squeeze_blk = Signal()
    copy_src_signal = Signal()
    paste_src_signal = Signal()

    def __init__(self):
        super().__init__()
        self._selected = 2
        self.canvas = self

    def tr(self, s):
        return _LABEL_ZH.get(s, s)

    def selected_text_items(self):
        return list(range(self._selected))

    @property
    def have_selected_blkitem(self):
        return self._selected > 0


def render(dark: bool, path: str):
    pcfg.darkmode = dark
    app = QApplication.instance() or QApplication([])

    from ui.pie_menu import PieMenu
    from ui.context_menu_config import run_cmd

    canvas = MockCanvas()
    pm = PieMenu(canvas, mw=canvas)
    pm.command_triggered.connect(lambda cid: run_cmd(canvas, cid))

    font = QFont("Microsoft YaHei")
    font.setPointSize(13)
    pm.setFont(font)

    pm.start_hold(QPoint(400, 400))
    # Highlight the "delete" row (top panel, row 2) to verify hover fill.
    pm._update_hover((0, 2))
    pm.update()
    app.processEvents()

    pm.grab().save(path)
    pm.cancel()


def main():
    render(True, os.path.join("scripts", "_list_preview_dark.png"))
    render(False, os.path.join("scripts", "_list_preview_light.png"))
    print("saved scripts/_list_preview_dark.png")
    print("saved scripts/_list_preview_light.png")


if __name__ == "__main__":
    main()
