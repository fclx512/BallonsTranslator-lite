"""FontExcludeDialog「一键精简」状态契约的离线回归测试。

背景 bug 一：对话框曾把 ``_simplify_map`` 初始化为空 dict 且不从
``pcfg.simplified_font_map`` 播种——重开对话框看不到已精简条目，
点 OK 会把 pcfg 里的映射覆盖成空 dict。
背景 bug 二：精简条目曾单独存字段、只在启动时剔除，点 OK 后刷新
（``get_filtered_font_list`` 不看该字段）立刻打回未精简状态。

最终落盘契约（复刻「老旧字体」按钮）：精简条目随手动排除同住
``pcfg.excluded_fonts``（点 OK 全量写回、刷新即时生效），
``simplified_font_map`` 只作标记——「已精简」后缀、过滤时豁免别名
扩展（否则规范名被连带隐藏）、旧数据规范名回显。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_font_exclude_dialog.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from utils import shared  # noqa: E402


class FontExcludeDialogSimplifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from utils.config import pcfg

        self._pcfg_orig = (
            dict(pcfg.simplified_font_map),
            list(pcfg.excluded_fonts),
        )
        # 起始清空：全量跑时其他测试文件 import 期 load_config 载入的
        # 真实用户配置会污染断言（对话框按 pcfg 现值播种）
        pcfg.simplified_font_map = {}
        pcfg.excluded_fonts = []
        self._orig = (
            shared.ALL_FONT_FAMILIES,
            shared.FONT_FAMILY_ALIAS,
            shared.FONT_STYLES,
            shared.FONT_PS_NAMES,
        )
        shared.ALL_FONT_FAMILIES = ["FontA", "FontB", "FontC"]
        shared.FONT_FAMILY_ALIAS = {}
        shared.FONT_STYLES = {}
        shared.FONT_PS_NAMES = {}

    def tearDown(self):
        from utils.config import pcfg

        (
            pcfg.simplified_font_map,
            pcfg.excluded_fonts,
        ) = self._pcfg_orig
        (
            shared.ALL_FONT_FAMILIES,
            shared.FONT_FAMILY_ALIAS,
            shared.FONT_STYLES,
            shared.FONT_PS_NAMES,
        ) = self._orig

    def _dialog(self):
        from ui.configpanel import FontExcludeDialog

        return FontExcludeDialog()

    @staticmethod
    def _names(list_widget):
        return [
            list_widget.item(i).data(__import__("qtpy").QtCore.Qt.ItemDataRole.UserRole)
            or list_widget.item(i).text()
            for i in range(list_widget.count())
        ]

    def test_seeds_from_pcfg_and_marks_entries(self):
        """重开对话框必须从 pcfg 播种：条目在隐藏列表带标记、可用列表无。"""
        from utils.config import pcfg

        pcfg.simplified_font_map = {"FontB": "FontA"}
        dlg = self._dialog()
        try:
            self.assertEqual(dlg.get_simplify_map(), {"FontB": "FontA"})
            hidden = self._names(dlg.excluded_list)
            self.assertIn("FontB", hidden)
            marked = [
                dlg.excluded_list.item(i).text()
                for i in range(dlg.excluded_list.count())
            ]
            self.assertTrue(
                any(
                    "FontB" in t and ("Simplified" in t or "已精简" in t)
                    for t in marked
                ),
                f"hidden items not marked: {marked}",
            )
            self.assertNotIn("FontB", self._names(dlg.available_list))
        finally:
            dlg.deleteLater()

    def test_ok_writeback_includes_simplified_in_excluded(self):
        """精简条目随 excluded_fonts 全量落盘（legacy 同路径）；
        过滤层靠标记映射豁免别名扩展，不靠写回时跳过。"""
        from utils.config import pcfg

        pcfg.simplified_font_map = {"FontB": "FontA"}
        dlg = self._dialog()
        try:
            self.assertEqual(dlg.get_excluded_fonts(), ["FontB"])
            self.assertEqual(dlg.get_simplify_map(), {"FontB": "FontA"})
        finally:
            dlg.deleteLater()

    def test_moving_back_unmarks_and_restores(self):
        from utils.config import pcfg
        from qtpy.QtCore import Qt

        pcfg.simplified_font_map = {"FontB": "FontA"}
        dlg = self._dialog()
        try:
            for i in range(dlg.excluded_list.count()):
                if dlg._real_name(dlg.excluded_list.item(i)) == "FontB":
                    dlg.excluded_list.item(i).setSelected(True)
            dlg._show_fonts()
            self.assertEqual(dlg.get_simplify_map(), {})
            self.assertIn("FontB", self._names(dlg.available_list))
            self.assertNotIn("FontB", self._names(dlg.excluded_list))
            self.assertEqual(dlg.get_excluded_fonts(), [])
            _ = Qt.ItemDataRole.UserRole  # keep import referenced
        finally:
            dlg.deleteLater()

    def test_button_added_entries_stay_in_map(self):
        """点击一键精简后的对话框内状态在重灌列表后保持。"""
        dlg = self._dialog()
        try:
            dlg._simplify_map.update({"FontC": "FontC"})
            dlg._populate_lists()
            self.assertNotIn("FontC", self._names(dlg.available_list))
            self.assertIn("FontC", self._names(dlg.excluded_list))
            self.assertEqual(dlg.get_simplify_map(), {"FontC": "FontC"})
        finally:
            dlg.deleteLater()

    def test_manual_excluded_roundtrip_untouched(self):
        """手动排除与精简条目同在隐藏列表，写回时都保留（标记各自独立）。"""
        from utils.config import pcfg

        pcfg.excluded_fonts = ["FontC"]
        pcfg.simplified_font_map = {"FontB": "FontA"}
        dlg = self._dialog()
        try:
            hidden = self._names(dlg.excluded_list)
            self.assertIn("FontC", hidden)
            self.assertIn("FontB", hidden)
            self.assertEqual(dlg.get_excluded_fonts(), ["FontC", "FontB"])
            self.assertEqual(dlg.get_simplify_map(), {"FontB": "FontA"})
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
