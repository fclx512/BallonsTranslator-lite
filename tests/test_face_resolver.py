"""Offscreen unit tests for ``utils/face_resolver.py`` — font_weight 单一
真值基建（face 派生显示缓存）与各写入点的同步契约。

offscreen 平台不枚举系统字体，``QFontDatabase`` 静态 API 统一 stub 为
一组固定 face（Regular/Bold/Light/SemiBold/DemiBold/Italic/Bold Italic），
验证就近匹配、italic 放宽、tie-break 与幂等性；写入点测试覆盖
``build_flatten_changes``/``build_variant_changes``（快照之前重算）、
``FontFormat`` 的 bold 折算与 ``FormatEditorPanel`` 的 font_weight 原值
透传（350 类存量不静默改值）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_face_resolver.py -q
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

from utils import face_resolver  # noqa: E402
from utils.face_resolver import (  # noqa: E402
    resolve_face,
    style_string_helper,
    sync_face,
    weight_of_face,
)
from utils.fontformat import FontFormat  # noqa: E402

# stub 字体库：同 (weight, italic) 两条 face（SemiBold/DemiBold 均 600）
FACES = [
    ("Thin", 100, False),
    ("Light", 300, False),
    ("Regular", 400, False),
    ("Medium", 500, False),
    ("SemiBold", 600, False),
    ("DemiBold", 600, False),
    ("Bold", 700, False),
    ("Black", 900, False),
    ("Italic", 400, True),
    ("Bold Italic", 700, True),
]


def _stub_database(test_case):
    def styles(family):
        return [f[0] for f in FACES] if family == "TestFam" else []

    def weight(family, style):
        # 真实 QFontDatabase：family 二分查不到时返回 -1（不走别名）
        if family != "TestFam":
            return -1
        for name, w, i in FACES:
            if name == style:
                return w
        return -1

    def italic(family, style):
        if family != "TestFam":
            return False
        for name, w, i in FACES:
            if name == style:
                return i
        return False

    return patch.multiple(
        "qtpy.QtGui.QFontDatabase",
        styles=styles,
        weight=weight,
        italic=italic,
    )


class StyleStringHelperTest(unittest.TestCase):
    """Qt styleStringHelper 阈值逻辑的锚点测试。"""

    def test_weight_names(self):
        self.assertEqual(style_string_helper(900, False), "Black")
        self.assertEqual(style_string_helper(800, False), "Extra Bold")
        self.assertEqual(style_string_helper(700, False), "Bold")
        self.assertEqual(style_string_helper(600, False), "Demi Bold")
        self.assertEqual(style_string_helper(500, False), "Medium")
        self.assertEqual(style_string_helper(400, False), "Regular")
        self.assertEqual(style_string_helper(300, False), "Light")
        self.assertEqual(style_string_helper(200, False), "Extra Light")
        self.assertEqual(style_string_helper(100, False), "Thin")

    def test_italic_names(self):
        self.assertEqual(style_string_helper(400, True), "Italic")
        self.assertEqual(style_string_helper(700, True), "Bold Italic")
        self.assertEqual(style_string_helper(500, True), "Medium Italic")

    def test_non_canonical_weight_uses_thresholds(self):
        # 350 落在 [300, 400) → Light；Qt5 刻度旧值（<100）按 400 处理
        self.assertEqual(style_string_helper(350, False), "Light")
        self.assertEqual(style_string_helper(50, False), "Regular")


class ResolveFaceTest(unittest.TestCase):
    def setUp(self):
        face_resolver.invalidate_face_cache()

    def tearDown(self):
        face_resolver.invalidate_face_cache()

    def test_exact_and_nearest_weight(self):
        with _stub_database(self):
            self.assertEqual(resolve_face("TestFam", 700, False), "Bold")
            self.assertEqual(resolve_face("TestFam", 400, False), "Regular")
            # 550：500/600 双侧平手 → Qt 阈值合成名 "Medium" 优先
            self.assertEqual(resolve_face("TestFam", 550, False), "Medium")
            # 350 就近 400 与 300 平手 → 枚举序靠前者 Light（100 在前）
            self.assertEqual(resolve_face("TestFam", 350, False), "Light")
            # 999 就近 900
            self.assertEqual(resolve_face("TestFam", 999, False), "Black")

    def test_tie_break_prefers_qt_canonical_name(self):
        with _stub_database(self):
            # SemiBold/DemiBold 均 600：Qt 合成名 "Demi Bold" 命中 DemiBold
            self.assertEqual(resolve_face("TestFam", 600, False), "DemiBold")

    def test_italic_relaxed_when_missing(self):
        with _stub_database(self):
            # 斜体 700 存在
            self.assertEqual(resolve_face("TestFam", 700, True), "Bold Italic")
            # 斜体 900 无斜体 face → 放宽 italic 就近 700（Bold Italic）
            self.assertEqual(resolve_face("TestFam", 900, True), "Bold Italic")

    def test_unknown_family_and_none_weight(self):
        with _stub_database(self):
            self.assertEqual(resolve_face("Nope", 400, False), "")
            self.assertEqual(resolve_face("TestFam", None, False), "")
            self.assertEqual(resolve_face("", 400, False), "")

    def test_weight_of_face_fallback(self):
        with _stub_database(self):
            self.assertEqual(weight_of_face("TestFam", "Bold"), 700)
            self.assertEqual(weight_of_face("TestFam", "Regular"), 400)
            self.assertIsNone(weight_of_face("Nope", "Bold"))
            self.assertIsNone(weight_of_face("TestFam", ""))


class SyncFaceTest(unittest.TestCase):
    def setUp(self):
        face_resolver.invalidate_face_cache()

    def tearDown(self):
        face_resolver.invalidate_face_cache()

    def test_sync_derives_and_clears(self):
        with _stub_database(self):
            ffmt = FontFormat(font_family="TestFam", font_weight=700)
            sync_face(ffmt)
            self.assertEqual(ffmt._style_name, "Bold")
            # None → 清空（渲染端 Qt 走 weight 距离匹配）
            ffmt.font_weight = None
            sync_face(ffmt)
            self.assertEqual(ffmt._style_name, "")

    def test_sync_is_idempotent(self):
        with _stub_database(self):
            ffmt = FontFormat(font_family="TestFam", font_weight=350)
            sync_face(ffmt)
            first = ffmt._style_name
            sync_face(ffmt)
            self.assertEqual(ffmt._style_name, first)
            self.assertEqual(first, "Light")

    def test_sync_follows_italic(self):
        with _stub_database(self):
            ffmt = FontFormat(font_family="TestFam", font_weight=700, italic=True)
            sync_face(ffmt)
            self.assertEqual(ffmt._style_name, "Bold Italic")


class FontformatFoldTest(unittest.TestCase):
    """bold 字段级折算（__post_init__）。"""

    def test_bold_true_folds_into_weight(self):
        ffmt = FontFormat(bold=True)
        self.assertFalse(ffmt.bold)
        self.assertEqual(ffmt.font_weight, 700)

    def test_bold_with_existing_weight_takes_max(self):
        ffmt = FontFormat(bold=True, font_weight=900)
        self.assertFalse(ffmt.bold)
        self.assertEqual(ffmt.font_weight, 900)

    def test_bold_false_untouched(self):
        ffmt = FontFormat(font_weight=300)
        self.assertFalse(ffmt.bold)
        self.assertEqual(ffmt.font_weight, 300)


class _FakeBlk:
    def __init__(self, ffmt: FontFormat):
        self.fontformat = ffmt


class _FakeProj:
    def __init__(self, pages: dict):
        self.pages = pages


class BatchChangesSyncTest(unittest.TestCase):
    """批量应用写入点：face 重算在快照（old_ffmt 深拷贝）之后、new_ffmt 定型时。"""

    def setUp(self):
        face_resolver.invalidate_face_cache()

    def tearDown(self):
        face_resolver.invalidate_face_cache()

    def test_flatten_changes_resync_face(self):
        with _stub_database(self):
            proj = _FakeProj(
                {"p1": [_FakeBlk(FontFormat(font_family="TestFam", font_weight=400))]}
            )
            base_style = type(
                "S", (), {"identity": ("TestFam", False)}
            )()
            from utils.base_styles import BaseStyle

            base_style = BaseStyle(
                "t", FontFormat(font_family="TestFam", font_weight=400)
            )
            changes = __import__(
                "utils.base_styles", fromlist=["build_flatten_changes"]
            ).build_flatten_changes(proj, base_style, {"font_weight": 700})
            self.assertEqual(len(changes), 1)
            new_ffmt = changes[0]["new_ffmt"]
            old_ffmt = changes[0]["old_ffmt"]
            # new_ffmt：weight=700 → face 派生 Bold；old_ffmt 快照保真
            self.assertEqual(new_ffmt.font_weight, 700)
            self.assertEqual(new_ffmt._style_name, "Bold")
            self.assertEqual(old_ffmt.font_weight, 400)
            self.assertEqual(old_ffmt._style_name, "")

    def test_variant_changes_resync_face(self):
        from utils.base_styles import build_variant_changes

        with _stub_database(self):
            proj = _FakeProj(
                {"p1": [_FakeBlk(FontFormat(font_family="TestFam", font_weight=400))]}
            )
            changes = build_variant_changes(
                [("p1", 0)], proj, {"font_weight": 900}
            )
            self.assertEqual(changes[0]["new_ffmt"]._style_name, "Black")


class FormatEditorPanelWeightTest(unittest.TestCase):
    """样式编辑器 font_weight 字段：getter 原值透传 + "(default)"=None。"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.patches = _stub_database(cls)
        cls.patches.start()

    @classmethod
    def tearDownClass(cls):
        cls.patches.stop()
        face_resolver.invalidate_face_cache()

    def test_getter_passthrough_keeps_350(self):
        from ui.style_format_editor import FormatEditorPanel

        panel = FormatEditorPanel()
        ffmt = FontFormat(font_family="TestFam", font_weight=350)
        panel.set_format(ffmt)
        try:
            ed = panel._editors["font_weight"]
            self.assertEqual(ed.value(), 350)
            # 未触碰时 changed_values 不含 font_weight（不静默改写）
            self.assertNotIn("font_weight", panel.changed_values())
        finally:
            panel.deleteLater()

    def test_default_entry_maps_to_none(self):
        from ui.style_format_editor import FormatEditorPanel

        panel = FormatEditorPanel()
        ffmt = FontFormat(font_family="TestFam", font_weight=None)
        panel.set_format(ffmt)
        try:
            ed = panel._editors["font_weight"]
            self.assertIsNone(ed.value())
            self.assertNotIn("font_weight", panel.changed_values())
        finally:
            panel.deleteLater()

    def test_explicit_change_writes_new_value(self):
        from ui.style_format_editor import FormatEditorPanel

        panel = FormatEditorPanel()
        ffmt = FontFormat(font_family="TestFam", font_weight=350)
        panel.set_format(ffmt)
        try:
            panel.set_field_value("font_weight", 900)
            self.assertEqual(panel.field_value("font_weight"), 900)
            self.assertEqual(
                panel.changed_values().get("font_weight"), 900
            )
        finally:
            panel.deleteLater()


class MixedFieldsTest(unittest.TestCase):
    """多选混合检测（FontFormatPanel._mixed_fields，静态逻辑）。"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _fake_item(self, ffmt):
        class _It:
            pass

        it = _It()
        it.get_fontformat = lambda: ffmt.deepcopy()
        return it

    def test_mixed_detection(self):
        from ui.text_panel import FontFormatPanel

        base = FontFormat(font_size=30, font_weight=700, line_spacing=1.2)
        other = FontFormat(font_size=30, font_weight=400, line_spacing=1.2)
        items = [
            self._fake_item(other),
            self._fake_item(base),
        ]
        mixed = FontFormatPanel._mixed_fields(items, base)
        self.assertIn("font_weight", mixed)
        self.assertNotIn("font_size", mixed)
        self.assertNotIn("line_spacing", mixed)

    def test_no_mixed_when_identical(self):
        from ui.text_panel import FontFormatPanel

        base = FontFormat(font_size=30, font_weight=700)
        items = [self._fake_item(base.deepcopy()), self._fake_item(base)]
        mixed = FontFormatPanel._mixed_fields(items, base)
        self.assertEqual(mixed, set())

    def test_mixed_family_across_styles(self):
        from ui.text_panel import FontFormatPanel

        base = FontFormat(font_family="TestFam")
        other = FontFormat(font_family="OtherFam")
        items = [self._fake_item(other), self._fake_item(base)]
        mixed = FontFormatPanel._mixed_fields(items, base)
        self.assertIn("font_family", mixed)


if __name__ == "__main__":
    unittest.main()
