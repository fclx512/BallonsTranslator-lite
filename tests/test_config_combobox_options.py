"""Regression test: ``ConfigComboBox(options=...)`` constructor kwarg.

曾只支持「先构造再 addItems」，构造期传 ``options=`` 直接 TypeError。
``options=`` 现为受支持的构造参数（等价构造后 addItems，含 adjustSize）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_config_combobox_options.py
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

from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.custom_widget import ConfigComboBox  # noqa: E402


class ConfigComboBoxOptionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_options_kwarg(self):
        box = ConfigComboBox(options=["A", "B", "C"])
        self.assertEqual(box.count(), 3)
        self.assertEqual(box.itemText(0), "A")

    def test_options_matches_additems_width(self):
        w_ctor = ConfigComboBox(options=["LongOptionLabel"]).width()
        w_late = ConfigComboBox()
        w_late.addItems(["LongOptionLabel"])
        self.assertEqual(w_ctor, w_late.width())

    def test_no_options_keeps_empty(self):
        self.assertEqual(ConfigComboBox().count(), 0)


if __name__ == "__main__":
    unittest.main()
