"""Offscreen end-to-end tests for the FontFormatPanel selection-state rework.

覆盖 2026-09 选中态重构阶段 2/3 的面板链路：多选镜像副本（非
global_format）、编辑经选中项广播、新块默认跟随最近编辑、闲置回落、
单选回读、重置默认格式、单选改字重全链（引擎同次派生 face + 镜像 +
默认跟随）。

真实运行时 ``SW.st_manager``/``SW.canvas`` 由 MainWindow 注入；离屏以 stub
替代。offscreen 平台枚举不到系统字体，face 派生为 ""（Qt 渲染走 weight
距离匹配），不影响 weight 断言。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_selection_state_panel.py
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

from qtpy.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from utils import config as C  # noqa: E402
from utils.fontformat import FontFormat  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(y, weight, translation):
    blk = TextBlock(xyxy=[100, y, 300, y + 100], translation=translation)
    blk.fontformat.font_weight = weight
    blk._bounding_rect = [100, y, 300, y + 100]
    return blk


class _StubCanvas:
    def hasFocus(self):
        return True

    def selected_text_items(self):
        return []


class SelectionStatePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui import shared_widget as SW
        from ui.text_panel import FontFormatPanel
        from ui.textitem import TextBlkItem

        cls.SW = SW
        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()
        SW.canvas = _StubCanvas()

        cls.panel = FontFormatPanel(cls.app)
        cls.panel.global_format = FontFormat()
        C.active_format = cls.panel.global_format

        class _StubManager:
            formatpanel = cls.panel

        SW.st_manager = _StubManager()

        cls.items = []
        for i, w in enumerate([400, 700, 400]):
            item = cls.TextBlkItem(blk=_make_blk(100 + i * 120, w, f"块{i}"), idx=i)
            cls.scene.addItem(item)
            cls.items.append(item)

    def test_01_multi_selection_uses_mirror(self):
        # 多选：镜像副本作为 C.active_format，不得命中 global_format
        self.panel.set_textblk_item(self.items[-1], multi_items=self.items)
        self.assertIsNot(C.active_format, self.panel.global_format)
        self.assertEqual(self.panel._active_multi_items, self.items)
        self.assertFalse(self.panel.global_mode())

    def test_02_multi_edit_broadcasts(self):
        self.panel.set_textblk_item(self.items[-1], multi_items=self.items)
        self.panel.on_param_changed("font_size", 40)
        for item in self.items:
            got = item.get_fontformat().font_size
            self.assertAlmostEqual(got, 40, delta=0.5)

    def test_03_default_follows_last_edit(self):
        self.panel.set_textblk_item(self.items[-1], multi_items=self.items)
        self.panel.on_param_changed("font_size", 40)
        self.assertEqual(self.panel.global_format.font_size, 40)

    def test_04_idle_state_restores_global(self):
        self.panel.set_textblk_item(self.items[-1], multi_items=self.items)
        self.panel.set_textblk_item()
        self.assertIs(C.active_format, self.panel.global_format)
        self.assertIsNone(self.panel.textblk_item)
        self.assertIsNone(self.panel._active_multi_items)

    def test_05_single_selection_readback(self):
        self.panel.set_textblk_item(self.items[1])
        self.assertIs(self.panel.textblk_item, self.items[1])
        # 镜像来自 get_fontformat 回读（weight 是文档 canonical 值）
        self.assertEqual(C.active_format.font_weight, 700)

    def test_06_reset_default_format(self):
        self.panel.global_format.font_size = 99
        self.panel._reset_global_format()
        self.assertEqual(self.panel.global_format.font_size, 24)

    def test_07_single_weight_edit_full_chain(self):
        # 单选改字重：引擎文档 900（同次派生 face）+ 镜像 + 默认跟随
        self.panel.set_textblk_item(self.items[1])
        self.panel.on_param_changed("font_weight", 900)
        self.assertEqual(self.items[1].document().defaultFont().weight(), 900)
        self.assertEqual(C.active_format.font_weight, 900)
        self.assertEqual(self.panel.global_format.font_weight, 900)

    @classmethod
    def tearDownClass(cls):
        cls.panel.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
