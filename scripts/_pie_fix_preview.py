"""Preview the 2026-08-14 pie-menu fixes: top-sector vertical stack +
checkbox toggles, ring and list.

Run with the bundled interpreter:
    ./ballontrans_pylibs_win/python.exe scripts/_pie_fix_preview.py

Outputs:
    scripts/_pie_fix_ring.png
    scripts/_pie_fix_list.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtCore import QObject, QPoint, Signal
from qtpy.QtGui import QFont
from qtpy.QtWidgets import QApplication

from utils import shared
from utils.config import load_config, pcfg

_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_pie_fix_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)

_LABEL_ZH = {
    "Copy": "复制",
    "Paste": "粘贴",
    "Delete": "删除",
    "Merge": "合并",
    "Snap Alignment": "吸附对齐",
    "Reset Angle": "重置角度",
    "Squeeze": "收缩",
    "translate": "翻译",
    "OCR": "OCR",
    "OCR and translate": "OCR并翻译",
    "OCR, translate and inpaint": "OCR、翻译并修复",
    "Copy source text": "复制原文",
    "Paste source text": "粘贴原文",
}


class MockUndoStack:
    def canUndo(self):
        return False

    def canRedo(self):
        return False


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
        self._selected = 1
        self.alignment_enabled = True   # snap-alignment toggle ON
        self.canvas = self
        self.undo_stack = MockUndoStack()
        self.text_undo_stack = self.undo_stack
        self.draw_undo_stack = MockUndoStack()

    def tr(self, s):
        return _LABEL_ZH.get(s, s)

    def selected_text_items(self):
        return list(range(self._selected))

    @property
    def have_selected_blkitem(self):
        return self._selected > 0

    def textEditMode(self):
        return True

    def drawMode(self):
        return False


def render(dark: bool, menu, path: str, hover=None):
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
    pm.set_menu_config(menu)
    pm.start_hold(QPoint(400, 400))
    if hover is not None:
        pm._update_hover(hover)
        pm.update()
    app.processEvents()
    pm.grab().save(path)
    pm.cancel()


def main():
    # Ring: 3-card top stack (vertical, no overlap) + snap_alignment toggle
    # ON at upper-right and upper-left.  The hovered bottom card of the top
    # stack is occluded by the upper-right card — hover-to-front draws it
    # on top (2026-08-14).
    ring = {
        "id": "ring", "name": "Edit Menu", "trigger": "Tab",
        "sectors": 8, "layout": "ring",
        "slots": [
            ["copy", "paste", "delete"],          # 0 top: vertical stack
            ["snap_alignment"],                   # 1 upper-right: checkbox
            [], [], [],
            ["merge"],                            # 4 bottom
            [], [], ["snap_alignment"],           # 7 upper-left: checkbox
        ],
    }
    render(True, ring, os.path.join("scripts", "_pie_fix_ring.png"),
           hover=(0, 2))

    # List: five anchor panels (top / upper-diag / lateral / lower-diag /
    # bottom) with a toggle row in the lateral panel (hovered).
    lst = {
        "id": "list", "name": "Edit Menu", "trigger": "Tab",
        "layout": "list", "direction": "right", "sectors": 8,
        "slots": [[] for _ in range(8)],
        "panels": [
            ["copy", "paste"],
            ["delete"],
            ["snap_alignment", "merge"],
            ["translate", "ocr"],
            ["ocr_translate"],
        ],
    }
    render(True, lst, os.path.join("scripts", "_pie_fix_list.png"),
           hover=(2, 0))
    print("saved scripts/_pie_fix_ring.png")
    print("saved scripts/_pie_fix_list.png")


if __name__ == "__main__":
    main()
