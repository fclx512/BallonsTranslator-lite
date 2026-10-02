"""上游旧工程「块级平铺效果键」加载归一（纯数据层，不开 GUI）。

上游早期版本（以及 fork 的老存档）把描边/渐变/阴影等效果直接平铺在块对象
上，而不是嵌套在 fontformat 里。本 fork 的 TextBlock 只认
``__post_init__`` 里 ``deprecated_blk_fmt_keys`` 登记过的块级键，其余键会被
``nested_dataclass`` 收进 ``deprecated_attributes`` 后静默丢弃——打开这类工程
时效果就没了。``utils/proj_imgtrans.py::_normalize_textblock_effect_payload``
在构造 TextBlock 之前把这些键折进 fontformat，这里把该行为钉住。

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_upstream_flat_effect_load.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from utils.proj_imgtrans import (  # noqa: E402
    ProjImgTrans,
    _normalize_textblock_effect_payload,
)

# 上游块级平铺的遗留效果键（stroke_width 走 default_stroke_width 旧名，
# srgb 走 bg_colors 旧名，另加一组渐变键）。
_LEGACY_FLAT_BLK = {
    "xyxy": [0, 0, 10, 10],
    "text": ["あ"],
    "translation": "",
    "default_stroke_width": 0.7,
    "bg_colors": [10, 20, 30],
    "gradient_enabled": True,
    "gradient_start_color": [255, 0, 0],
    "gradient_end_color": [0, 0, 255],
    "gradient_angle": 45.0,
    "gradient_size": 0.5,
}


def _flat_blk(**overrides) -> dict:
    payload = dict(_LEGACY_FLAT_BLK)
    payload.update(overrides)
    return payload


def _write_page(tmp_dir: str, name: str = "001.png"):
    img = np.zeros((16, 16, 3), dtype=np.uint8)
    cv2.imwrite(osp.join(tmp_dir, name), img)
    return name


class NormalizePayloadTest(unittest.TestCase):
    def test_flat_keys_are_folded_into_fontformat(self):
        normalized = _normalize_textblock_effect_payload(
            _flat_blk(fontformat={"font_size": 30})
        )
        self.assertNotIn("default_stroke_width", normalized)
        self.assertNotIn("gradient_enabled", normalized)
        ffmt = normalized["fontformat"]
        self.assertEqual(ffmt["stroke_width"], 0.7)
        self.assertEqual(ffmt["srgb"], [10, 20, 30])
        self.assertTrue(ffmt["gradient_enabled"])
        self.assertEqual(ffmt["font_size"], 30)

    def test_idempotent(self):
        once = _normalize_textblock_effect_payload(_flat_blk())
        twice = _normalize_textblock_effect_payload(once)
        self.assertEqual(once, twice)

    def test_missing_keys_are_safe(self):
        # 无 fontformat 键也不炸，只是补一个空子字典
        self.assertEqual(
            _normalize_textblock_effect_payload({"xyxy": [0, 0, 1, 1]}),
            {"xyxy": [0, 0, 1, 1], "fontformat": {}},
        )
        self.assertEqual(_normalize_textblock_effect_payload({}), {"fontformat": {}})

    def test_non_dict_fontformat_is_passed_through(self):
        sentinel = object()
        payload = {"fontformat": sentinel, "stroke_width": 0.3}
        self.assertIs(_normalize_textblock_effect_payload(payload)["fontformat"], sentinel)


class FlatEffectLoadTest(unittest.TestCase):
    """经 ProjImgTrans.load_from_dict 的两条构造分支（pages / not_found_pages）。"""

    def _load(self, tmp_dir, imname):
        proj = ProjImgTrans()
        proj.directory = tmp_dir
        proj.load_from_dict({"pages": {imname: [_flat_blk()]}})
        return proj

    def test_found_page_keeps_flat_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            imname = _write_page(tmp)
            proj = self._load(tmp, imname)
            blk = proj.pages[imname][0]
            ffmt = blk.fontformat
            self.assertAlmostEqual(ffmt.stroke_width, 0.7)
            self.assertEqual(list(ffmt.srgb), [10, 20, 30])
            self.assertTrue(ffmt.gradient_enabled)
            self.assertEqual(list(ffmt.gradient_start_color), [255, 0, 0])
            self.assertEqual(list(ffmt.gradient_end_color), [0, 0, 255])
            self.assertAlmostEqual(ffmt.gradient_angle, 45.0)
            self.assertAlmostEqual(ffmt.gradient_size, 0.5)

    def test_not_found_page_keeps_flat_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._load(tmp, "missing.png")
            blk = proj.not_found_pages["missing.png"][0]
            self.assertAlmostEqual(blk.fontformat.stroke_width, 0.7)
            self.assertTrue(blk.fontformat.gradient_enabled)

    def test_nested_fontformat_not_overwritten(self):
        # 块里已经带 fontformat 时，平铺键并入而不是替换整份样式
        with tempfile.TemporaryDirectory() as tmp:
            imname = _write_page(tmp)
            proj = ProjImgTrans()
            proj.directory = tmp
            proj.load_from_dict(
                {
                    "pages": {
                        imname: [_flat_blk(fontformat={"font_family": "Arial"})]
                    }
                }
            )
            blk = proj.pages[imname][0]
            self.assertEqual(blk.fontformat.font_family, "Arial")
            self.assertAlmostEqual(blk.fontformat.stroke_width, 0.7)


class CompactMemoryWriteTest(unittest.TestCase):
    def test_written_as_upstream_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = ProjImgTrans(directory=tmp)
            proj.llm_compact_memory = "全局梗概"
            payload = proj.to_dict()
            record = payload["llm_compact_memory"]
            self.assertEqual(record["version"], 1)
            self.assertEqual(record["text"], "全局梗概")
            self.assertEqual(record["covered_pages"], [])

    def test_empty_memory_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = ProjImgTrans(directory=tmp)
            self.assertNotIn("llm_compact_memory", proj.to_dict())

    def test_roundtrip_keeps_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = ProjImgTrans(directory=tmp)
            proj.llm_compact_memory = "全局梗概"
            restored = ProjImgTrans(directory=tmp)
            restored.load_from_dict(proj.to_dict())
            self.assertEqual(restored.llm_compact_memory, "全局梗概")


if __name__ == "__main__":
    unittest.main()
