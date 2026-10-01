"""fork 落盘数据的「上游可读」契约（纯数据层，不开 GUI）。

上游 BallonsTranslator 打开本 fork 保存的工程时有两条会静默出事的路径，这里
把两边的约定钉住：

* 上游的 ``FontFormat.__post_init__`` 只认自己声明的字段，未知键会被收进
  ``deprecated_attributes``、逐块打印 "Ignoring unsupported font format
  fields" 警告后清空（随后它自己的保存就把这些值丢掉）。所以本 fork 独有的
  字段必须只在非默认值时落盘，否则常规工程每个块白送一条警告。
* 上游把块级 ``text_layout_version`` 缺失当成版本 0 旧数据，升级动作是
  「竖排块一律 alignment=Right」并回写。本 fork 的竖排渲染按存档 alignment
  走，语义等同上游版本 1，所以落盘必须带上这个键。

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_upstream_writer_compat.py -q
"""

import json
import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from utils.fontformat import FontFormat  # noqa: E402
from utils.proj_imgtrans import TextBlkEncoder  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

# 上游 FontFormat 未声明的字段（写出来就是警告 + 被丢）。
_UPSTREAM_UNKNOWN = (
    "punctuation_alignment",
    "shadow_include_stroke",
    "strikeout",
    "stroke_color_custom",
)


def _blk_dict(**ffmt_overrides) -> dict:
    blk = TextBlock(text=["あ"], translation="")
    for key, value in ffmt_overrides.items():
        setattr(blk.fontformat, key, value)
    return json.loads(json.dumps(blk, cls=TextBlkEncoder))


class ForkOnlyFieldWriteTest(unittest.TestCase):
    def test_defaults_are_not_written(self):
        payload = FontFormat().to_serializable_dict()
        for name in _UPSTREAM_UNKNOWN:
            self.assertNotIn(name, payload)

    def test_non_default_values_are_written(self):
        ffmt = FontFormat()
        ffmt.strikeout = True
        ffmt.stroke_color_custom = True
        payload = ffmt.to_serializable_dict()
        for name in ("strikeout", "stroke_color_custom"):
            self.assertIn(name, payload)

    def test_shadow_include_stroke_written_when_set(self):
        # 该开关以效果卡顺序表达，没有阴影卡 + 描边卡时无从置位。
        ffmt = FontFormat()
        ffmt.stroke_width = 1.0
        ffmt.shadow_radius = 5.0
        ffmt.shadow_include_stroke = True
        self.assertTrue(ffmt.to_serializable_dict()["shadow_include_stroke"])

    def test_deprecated_punctuation_alignment_never_written(self):
        ffmt = FontFormat()
        ffmt.punctuation_alignment = 1
        self.assertNotIn("punctuation_alignment", ffmt.to_serializable_dict())


class LayoutVersionWriteTest(unittest.TestCase):
    def test_block_payload_carries_layout_version(self):
        self.assertEqual(_blk_dict()["text_layout_version"], 1)

    def test_upstream_value_is_preserved_on_roundtrip(self):
        payload = _blk_dict()
        payload["text_layout_version"] = 2
        blk = TextBlock(**payload)
        self.assertEqual(blk.text_layout_version, 2)
        again = json.loads(json.dumps(blk, cls=TextBlkEncoder))
        self.assertEqual(again["text_layout_version"], 2)


if __name__ == "__main__":
    unittest.main()
