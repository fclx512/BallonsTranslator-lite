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


class FontFamilySpacingResolutionTest(unittest.TestCase):
    """家族名空格变体归一（ui/text_engine/font_family.py）。

    背景：Qt 对家族名按名精确匹配，字体元数据自带尾随空格（实测攸望系列）
    或项目数据与 Qt 注册名差在空白上时整体失配、回退默认字体（用户实测
    宋体）。契约：差在空白/大小写上的名字映射回真实注册名；可解析名与
    未知名一律原样返回（不臆造替换，防止「修名字」修丢字体）。索引直接
    注入，不依赖真实字体库（offscreen 平台本就无字体）。
    """

    REAL = "攸望竹带体（简繁）Medium  "  # Qt 注册名自带 2 个尾随空格
    OTHER = "Microsoft YaHei UI"

    def setUp(self):
        from ui.text_engine import font_family as ff

        self.ff = ff
        self._saved = (
            ff._QT_FAMILY_EXACT,
            ff._QT_FAMILY_BY_NORM_KEY,
            ff._NORM_INDEX_READY,
        )
        ff._QT_FAMILY_EXACT = {self.REAL, self.OTHER}
        ff._QT_FAMILY_BY_NORM_KEY = {
            ff._norm_key(self.REAL): self.REAL,
            ff._norm_key(self.OTHER): self.OTHER,
        }
        ff._NORM_INDEX_READY = True

    def tearDown(self):
        (
            self.ff._QT_FAMILY_EXACT,
            self.ff._QT_FAMILY_BY_NORM_KEY,
            self.ff._NORM_INDEX_READY,
        ) = self._saved

    def test_exact_and_project_alias_take_priority(self):
        # 精确名原样返回；项目别名机制优先于空格归一。
        self.assertEqual(self.ff.font_family_for_qt(self.REAL), self.REAL)
        self.ff._QT_FAMILY_BY_PROJECT_NAME["自定义别名"] = self.OTHER
        try:
            self.assertEqual(self.ff.font_family_for_qt("自定义别名"), self.OTHER)
        finally:
            self.ff._QT_FAMILY_BY_PROJECT_NAME.clear()

    def test_whitespace_variants_map_to_registered_name(self):
        for variant in (
            self.REAL.strip(),  # 尾随空格全被剥掉（上游旧数据形态）
            self.REAL + " ",  # 空格数比注册名多
            self.REAL[:-1],  # 少 1 个空格
            self.REAL.casefold(),  # 大小写变体
        ):
            self.assertEqual(self.ff.font_family_for_qt(variant), self.REAL)

    def test_unknown_name_passes_through(self):
        # 字体未装/名字真不存在：原样返回，交回 Qt 既有回退行为。
        self.assertEqual(self.ff.font_family_for_qt("不存在的字体"), "不存在的字体")
        self.assertEqual(self.ff.font_family_for_qt(""), "")

    def test_qfont_with_family_resolves_variant(self):
        from qtpy.QtGui import QFont

        font = self.ff.qfont_with_family(QFont(), self.REAL.strip())
        self.assertEqual(font.families()[0], self.REAL)

    def test_document_normalization_fixes_variants_only(self):
        # HTML 里嵌的失配名（富文本渲染的实际路径）在 setHtml 后逐片段
        # 归一；可解析文档零写入（count==0），归一幂等。
        from qtpy.QtGui import QFont, QTextDocument

        from ui.text_engine.annotations import load_rich_text_html

        doc = QTextDocument()
        load_rich_text_html(
            doc,
            '<p style="font-family:\'' + self.REAL.strip() + '\'">字</p>',
        )
        frag = doc.firstBlock().begin().fragment()
        self.assertEqual(frag.charFormat().font().family(), self.REAL)

        doc2 = QTextDocument()
        load_rich_text_html(
            doc2,
            '<p style="font-family:\'' + self.OTHER + '\'">字</p>',
        )
        self.assertEqual(
            doc2.firstBlock().begin().fragment().charFormat().font().family(),
            self.OTHER,
        )
        # 再归一一次应零写入（幂等，不产生数据搅动）。
        from ui.text_engine.font_family import normalize_document_font_families

        self.assertEqual(normalize_document_font_families(doc2), 0)
        self.assertEqual(normalize_document_font_families(doc), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
