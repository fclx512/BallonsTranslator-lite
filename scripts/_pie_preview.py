"""Generate dark/light preview images for the pie menu.

Run with the bundled interpreter:
    ./ballontrans_pylibs_win/python.exe scripts/_pie_preview.py

Outputs:
    scripts/_pie_preview_dark.png
    scripts/_pie_preview_light.png
"""

import os
import sys

# Use the default windows platform so font rendering works for previews.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtCore import QObject, QPoint, Signal
from qtpy.QtGui import QFont, QPixmap
from qtpy.QtWidgets import QApplication

from utils import shared
from utils.config import load_config, pcfg

_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pie_preview_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)
pcfg.pie_sectors = [
    ["ocr_translate"],
    ["ocr"],
    ["copy"],
    ["paste"],
    ["delete"],
    ["merge"],
    ["align_left", "align_right", "align_hcenter"],
    ["translate"],
]


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

    from ui.pie_menu import PieMenu, TOTAL_RADIUS
    from ui.context_menu_config import run_cmd

    canvas = MockCanvas()
    pm = PieMenu(canvas)
    pm.command_triggered.connect(lambda cid: run_cmd(canvas, cid))

    font = QFont("Microsoft YaHei")
    font.setPointSize(13)
    pm.setFont(font)

    pm.start_hold(QPoint(400, 400))
    # Highlight the middle card of the left fan sector.
    pm._update_hover((6, 1))
    pm.update()
    app.processEvents()

    pm.grab().save(path)
    pm.cancel()


def main():
    render(True, os.path.join("scripts", "_pie_preview_dark.png"))
    render(False, os.path.join("scripts", "_pie_preview_light.png"))
    print("saved scripts/_pie_preview_dark.png")
    print("saved scripts/_pie_preview_light.png")


if __name__ == "__main__":
    main()
