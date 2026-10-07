"""幽灵字体真机探针：枚举下拉列表候选，逐个 QFontInfo 探测「枚举得到但
渲染不了」的家族。

背景：用户反馈字体下拉里有「幽灵字体」——有选项、无任何样式、应用后
仍是默认字体。成因是 QFontDatabase.families() 能枚举、DirectWrite 却
加载不了的家族（GDI 老光栅字体、方括号铸字厂名），选中后 Qt 静默替换
成默认字体。生产闸门在 ``utils/shared.py::_drop_unloadable_families``
（init_font_list 时过滤），本探针复算同一判据并列出明细，供用户报
「某字体是幽灵」时核对。

只读，不写任何状态。Run:
    ./ballontrans_pylibs_win/python.exe scripts/probes/font_ghost_probe.py
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
os.environ.setdefault("QT_API", "pyqt6")

from qtpy.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from qtpy.QtGui import QFont, QFontDatabase, QFontInfo  # noqa: E402

from utils import font_scan  # noqa: E402
from utils.shared import LEGACY_FONTS, _drop_unloadable_families  # noqa: E402


def norm(value):
    return " ".join(value.casefold().split())


def main():
    data = font_scan.build_font_data()
    display = data["display_families"]
    ghosts = set(display) - set(
        _drop_unloadable_families(display, data["alias_to_canonical"])
    )

    print(f"display families: {len(display)}, ghosts: {len(ghosts)}")
    for family in sorted(ghosts):
        resolved = QFontInfo(QFont(family)).family()
        styles = QFontDatabase.styles(family)
        print(
            f"  {family!r} -> {resolved!r}, styles={list(styles)}"
            f"{' [LEGACY_FONTS]' if family in LEGACY_FONTS else ''}"
        )
    if not ghosts:
        print("  (none)")


if __name__ == "__main__":
    main()
