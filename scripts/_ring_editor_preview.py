"""Preview the PieMenuEditor inside a QScrollArea to check clipping.

Run with the bundled interpreter:
    ./ballontrans_pylibs_win/python.exe scripts/_pie_editor_preview.py

Outputs:
    scripts/_ring_editor_preview.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont, QPixmap
from qtpy.QtWidgets import (
    QApplication, QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from utils import shared
from utils.config import load_config, pcfg

_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_ring_editor_preview_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)

# A long vertical list menu to stress the config-page preview.
pcfg.pie_menus = [{
    "id": "edit",
    "name": "Edit Menu",
    "trigger": "Tab",
    "layout": "ring",
    "direction": "right",
    "sectors": 8,
    "slots": [[] for _ in range(8)],
    "items": [
        "copy", "paste", "delete",
        "copy_src", "paste_src",
        "reset_angle", "squeeze", "merge",
        "translate", "ocr", "ocr_translate", "ocr_translate_inpaint",
        "undo", "redo", "fit_window", "zoom_in", "zoom_out",
        "prev_page", "next_page",
        "align_left", "align_right", "align_hcenter", "align_vcenter",
    ],
}]


def main():
    app = QApplication.instance() or QApplication([])
    from ui.pie_menu_editor import PieMenuEditor
    from ui.custom_widget import ConfigScrollBar

    editor = PieMenuEditor()
    font = QFont("Microsoft YaHei")
    font.setPointSize(13)
    editor.setFont(font)

    # Mimic ConfigPanel._wrap_page: put editor in a scroll area with a
    # realistic viewport size.
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setVerticalScrollBar(ConfigScrollBar(scroll))

    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.addWidget(editor)
    editor.setSizePolicy(
        QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    scroll.setWidget(page)
    scroll.resize(520, 380)
    scroll.show()
    app.processEvents()

    print("preview geometry:", editor.preview.geometry())
    print("preview sizeHint:", editor.preview.sizeHint())
    print("palette_hint geometry:", editor.palette_hint.geometry())
    print("palette geometry:", editor.palette.geometry())
    print("editor size:", editor.size())
    print("editor sizeHint:", editor.sizeHint())
    print("editor minimumSize:", editor.minimumSize())
    print("page size:", page.size())
    print("page sizeHint:", page.sizeHint())
    print("viewport size:", scroll.viewport().size())

    # Grab the scroll-area viewport to see what the user actually sees
    # (including any scroll bars).  Also dump the full page for comparison.
    pm = scroll.grab()
    pm.save(os.path.join("scripts", "_ring_editor_preview.png"))
    page.grab().save(os.path.join("scripts", "_ring_editor_preview_full.png"))
    print("saved scripts/_ring_editor_preview.png")
    print("saved scripts/_ring_editor_preview_full.png")


if __name__ == "__main__":
    main()
