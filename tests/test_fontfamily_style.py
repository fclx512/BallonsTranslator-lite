"""Offscreen regression tests for font-family/weight sync (真值化后).

历史背景：fork 曾在 ``ui/textitem.py::setFontFamily`` 上带 ``style_name``
参数（家族切换顺带按样式名同步字重），2026-09 字重真值化后该补丁退役——
face 是 ``font_weight`` 的派生显示缓存（``utils/face_resolver.py``），
字重落文档的唯一通道收敛为引擎 ``setFontWeight``/``setFontItalic``（同次
merge 派生 face）。

本套件守护的新契约：weight 写入必须同时到达 defaultFont 与 fragment、
不得拖动 fragment 字号、``setFontFamily`` 退化为纯家族变更（不接
``style_name``）、spacing setter 兼容 legacy kwargs。

Note: the offscreen platform exposes no system fonts, so face derivation
resolves to ""（渲染端 Qt 走 weight 距离匹配），不影响 weight 断言。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_fontfamily_style.py
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

from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy=(100, 100, 300, 200), translation="测试文字A"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class FontFamilyStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])
        cls.scene = QGraphicsScene()

    def _new_item(self, translation="测试文字A"):
        item = self.TextBlkItem(blk=_make_blk(translation=translation), idx=0)
        self.scene.addItem(item)
        return item

    def test_command_layer_family_change_no_crash(self):
        # 家族变更命令路径：引擎 setFontFamily + 数据层 font_family 对齐 +
        # 逐块 sync_face（offscreen 无字体 → face 为 ""，Qt 走 weight 匹配）。
        from ui.funcmaps import handle_ffmt_change
        from utils.fontformat import FontFormat

        item = self._new_item()
        fmt = FontFormat(font_family="Arial", font_size=24)
        handle_ffmt_change["font_family"](
            "font_family", "Arial", fmt, is_global=False, blkitems=[item]
        )
        self.assertEqual(item.fontformat.font_family, "Arial")

    def test_weight_reaches_default_font_and_fragments(self):
        # setFontWeight(700) 必须同时到达 defaultFont 与 fragment 格式。
        item = self._new_item()
        item.setFontWeight(700)
        doc = item.document()
        self.assertGreaterEqual(doc.defaultFont().weight(), 700)
        frag = doc.firstBlock().begin().fragment()
        self.assertGreaterEqual(frag.charFormat().font().weight(), 700)
        self.assertTrue(frag.charFormat().font().bold())

    def test_light_weight_visible(self):
        # 300 必须可见地变细（stale-bold 回归守卫）。
        item = self._new_item()
        item.setFontWeight(700)
        item.setFontWeight(300)
        doc = item.document()
        self.assertLessEqual(doc.defaultFont().weight(), 300)
        self.assertFalse(doc.defaultFont().bold())
        frag = doc.firstBlock().begin().fragment()
        self.assertLessEqual(frag.charFormat().font().weight(), 300)

    def test_family_change_is_plain(self):
        # setFontFamily 不再接受 style_name（真值化后该参数随 fork 补丁
        # 退役），纯家族变更不改动字重。
        item = self._new_item()
        weight_before = item.document().defaultFont().weight()
        with self.assertRaises(TypeError):
            item.setFontFamily("Times New Roman", style_name="Bold")
        item.setFontFamily("Times New Roman")
        self.assertEqual(
            item.document().defaultFont().family(), "Times New Roman"
        )
        self.assertEqual(
            item.document().defaultFont().weight(), weight_before
        )

    def test_weight_sync_preserves_fragment_size(self):
        # setFontSize 写 per-fragment pointSize 且不触碰 defaultFont；
        # weight/face 同次 merge 只允许动 weight/styleName 两个字段——
        # 拖入 defaultFont 的 pointSize 会让字号回退选区建立时的旧值。
        item = self._new_item()
        item.setFontSize(48.0)
        doc = item.document()
        frag = doc.firstBlock().begin().fragment()
        self.assertAlmostEqual(frag.charFormat().fontPointSize(), 48.0, places=1)
        item.setFontWeight(700)
        frag = doc.firstBlock().begin().fragment()
        self.assertAlmostEqual(frag.charFormat().fontPointSize(), 48.0, places=1)
        self.assertGreaterEqual(frag.charFormat().font().weight(), 700)

    def test_spacing_setters_accept_legacy_kwargs(self):
        # ffmt_change_line/letter_spacing & line_spacing_type still pass
        # set_kwargs/restore_cursor the old compat layer used to take.
        item = self._new_item()
        item.setLineSpacing(1.5, set_selected=True, restore_cursor=True)
        item.setLetterSpacing(1.0, set_selected=True, restore_cursor=True)
        item.setLineSpacingType(0, restore_cursor=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
