"""Regression test: DirectWrite sentinel tightBoundingRect must not poison layout metrics.

某些字体（如「攸望竹带体」）存在 advance=0 的字形，Windows DirectWrite 对其
取不到轮廓，``QFontMetricsF.tightBoundingRect`` 返回坐标/尺寸近 1e5 的哨兵
矩形。``ui/text_engine/layout.py::get_punc_rect`` 须将其判定为退化值并回退到
``boundingRect``，否则竖排推进量被放大 1 万倍、画布被撑爆。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_punc_rect_sentinel.py
"""

import os
import os.path as osp
import sys
import unittest
from unittest.mock import patch

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QRectF  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.text_engine import layout as engine_layout  # noqa: E402
from ui.text_engine.layout import get_punc_rect  # noqa: E402


class _MockMetrics:
    """QFontMetricsF 替身：tightBoundingRect 返回 DirectWrite 哨兵。"""

    SENTINEL = QRectF(100000.0, 100000.0, 100000.0, 100000.0)
    NORMAL = QRectF(1.5, -12.25, 18.0, 22.0)

    def __init__(self, *args, **kwargs):
        pass

    def tightBoundingRect(self, char):
        return self.SENTINEL

    def boundingRect(self, char):
        return self.NORMAL


class PuncRectSentinelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        # lru_cache 会固化结果：换唯一 family 键位 + 前后清缓存，防污染其它测试
        get_punc_rect.cache_clear()
        self.family = "SentinelFont-test"
        self.args = (self.family, 20.0, 400, False)

    def tearDown(self):
        get_punc_rect.cache_clear()

    def test_sentinel_falls_back_to_bounding_rect(self):
        with patch.object(engine_layout, "QFontMetricsF", _MockMetrics):
            tbr, br = get_punc_rect("啊", *self.args)
        self.assertEqual(tbr, _MockMetrics.NORMAL)
        self.assertEqual(br, _MockMetrics.NORMAL)

    def test_sane_tight_rect_passes_through(self):
        sane = QRectF(1.5, -12.25, 18.0, 22.0)

        class _SaneMetrics(_MockMetrics):
            def tightBoundingRect(self, char):
                return sane

        with patch.object(engine_layout, "QFontMetricsF", _SaneMetrics):
            tbr, _ = get_punc_rect("啊", *self.args)
        self.assertEqual(tbr, sane)


if __name__ == "__main__":
    unittest.main()
