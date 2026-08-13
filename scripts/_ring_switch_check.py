"""Regression check: editor preview list -> ring style switch must not clip.

Builds a PieMenuEditor whose first menu is a List layout, then switches the
style combo to Ring (the exact user action from the bug report) and grabs
the preview.  Before the `_sync_fixed_size` fix the preview kept the small
list window and the ring rendered clipped (only its top-left part visible).

Run with the bundled interpreter (default platform, NOT offscreen):
    ./ballontrans_pylibs_win/python.exe scripts/_ring_switch_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtpy.QtGui import QFont
from qtpy.QtWidgets import QApplication

from utils import shared
from utils.config import load_config, pcfg

_TMP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_ring_switch_check_config.json")
shared.CONFIG_PATH = _TMP_CFG
if os.path.exists(_TMP_CFG):
    os.remove(_TMP_CFG)
load_config(_TMP_CFG)

pcfg.pie_menus = [{
    "id": "edit",
    "name": "Edit Menu",
    "trigger": "Tab",
    "layout": "list",
    "direction": "right",
    "sectors": 8,
    "slots": [[], ["copy", "paste", "delete"], ["merge"], [], [], [], [], []],
    "panels": [["copy", "paste", "delete"], ["merge"], []],
}]


def main():
    app = QApplication.instance() or QApplication([])
    from ui.pie_menu_editor import PieMenuEditor

    editor = PieMenuEditor()
    font = QFont("Microsoft YaHei")
    font.setPointSize(13)
    editor.setFont(font)
    editor.resize(520, 800)
    editor.show()
    app.processEvents()

    print("list preview size:", editor.preview.size())

    # user action: switch the style combo Ring
    editor.style_combo.setCurrentIndex(0)
    app.processEvents()
    print("ring preview size:", editor.preview.size())
    print("ring menu layout:", editor._current_menu().get("layout"))
    print("ring slots:", editor._current_menu().get("slots"))

    editor.preview.grab().save(
        os.path.join("scripts", "_ring_switch_check.png"))
    print("saved scripts/_ring_switch_check.png")

    if os.path.exists(_TMP_CFG):
        os.remove(_TMP_CFG)


if __name__ == "__main__":
    main()
