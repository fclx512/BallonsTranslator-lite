"""标签体系数据层测试：注册表契约、块标签读写、OCR 置信度自动挂标语义。

覆盖 utils/block_tags.py 与 TextBlock.tags 字段的往返持久化。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_block_tags.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils.block_tags import (  # noqa: E402
    TAG_DEFS,
    TAG_REGISTRY,
    NATURE_PRIORITY,
    apply_ocr_confidence_tag,
    has_tag,
    remove_tag,
    set_tag,
    sorted_tag_ids,
)
from utils.textblock import TextBlock  # noqa: E402


class TestTagRegistry(unittest.TestCase):
    def test_ids_unique(self):
        ids = [t.id for t in TAG_DEFS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(TAG_REGISTRY, {t.id: t for t in TAG_DEFS})

    def test_names_translated_at_definition(self):
        # 显式翻译上下文规则：名称须是字面量定义处已翻译的值（非源串原样透传
        # 也可——zh 环境外 tr 原样返回；这里只验非空与含翻译函数调用路径）
        for t in TAG_DEFS:
            self.assertTrue(t.name)
        self.assertTrue(hasattr(TAG_REGISTRY["ocr_low_conf"], "nature"))

    def test_natures_valid(self):
        for t in TAG_DEFS:
            self.assertIn(t.nature, NATURE_PRIORITY)
        self.assertLess(
            NATURE_PRIORITY["doubt"], NATURE_PRIORITY["directive"]
        )


class TestBlockTags(unittest.TestCase):
    def setUp(self):
        self.blk = TextBlock()

    def test_default_empty(self):
        self.assertEqual(self.blk.tags, {})

    def test_set_get_remove(self):
        set_tag(self.blk, "handwritten", "manual")
        self.assertTrue(has_tag(self.blk, "handwritten"))
        self.assertEqual(
            self.blk.tags["handwritten"], {"source": "manual"}
        )
        remove_tag(self.blk, "handwritten")
        self.assertFalse(has_tag(self.blk, "handwritten"))
        # 重复移除不抛错
        remove_tag(self.blk, "handwritten")

    def test_sorted_tag_ids_doubt_first(self):
        set_tag(self.blk, "handwritten", "manual")  # directive
        set_tag(self.blk, "ocr_low_conf", "program")  # doubt
        set_tag(self.blk, "trans_polish", "manual")  # doubt
        self.assertEqual(
            sorted_tag_ids(self.blk),
            ["ocr_low_conf", "trans_polish", "handwritten"],
        )

    def test_unknown_ids_ignored_in_sort(self):
        self.blk.tags["ghost_tag"] = {"source": "manual"}
        self.assertEqual(sorted_tag_ids(self.blk), [])

    def test_to_dict_roundtrip(self):
        set_tag(self.blk, "ocr_low_conf", "program", score=0.42)
        d = self.blk.to_dict()
        self.assertEqual(
            d["tags"], {"ocr_low_conf": {"source": "program", "score": 0.42}}
        )
        blk2 = TextBlock(**d)
        self.assertEqual(blk2.tags, d["tags"])

    def test_load_old_project_without_tags(self):
        blk2 = TextBlock(**{"translation": "x", "unknown_future_field": 1})
        self.assertEqual(blk2.tags, {})


class TestOcrConfidenceTag(unittest.TestCase):
    def setUp(self):
        self.blk = TextBlock()

    def test_low_score_tags_with_score(self):
        apply_ocr_confidence_tag(self.blk, 0.42, threshold=0.6)
        entry = self.blk.tags.get("ocr_low_conf")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["source"], "program")
        self.assertEqual(entry["score"], 0.42)

    def test_good_score_removes_program_tag(self):
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1)
        apply_ocr_confidence_tag(self.blk, 0.95, threshold=0.6)
        self.assertFalse(has_tag(self.blk, "ocr_low_conf"))

    def test_manual_tag_not_overwritten(self):
        set_tag(self.blk, "ocr_low_conf", "manual")
        apply_ocr_confidence_tag(self.blk, 0.1, threshold=0.6)
        self.assertEqual(self.blk.tags["ocr_low_conf"]["source"], "manual")
        apply_ocr_confidence_tag(self.blk, 0.95, threshold=0.6)
        self.assertTrue(has_tag(self.blk, "ocr_low_conf"))

    def test_none_score_keeps_state(self):
        apply_ocr_confidence_tag(self.blk, None, threshold=0.6)
        self.assertFalse(has_tag(self.blk, "ocr_low_conf"))
        set_tag(self.blk, "ocr_low_conf", "program")
        apply_ocr_confidence_tag(self.blk, None, threshold=0.6)
        # score None 走 else 分支摘除程序标签（无分数视为不挂）
        self.assertFalse(has_tag(self.blk, "ocr_low_conf"))


if __name__ == "__main__":
    unittest.main()
