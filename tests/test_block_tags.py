"""标签体系数据层测试：注册表契约、块标签读写、OCR 置信度／误识别自动挂标语义。

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
    MANUAL_TAG_DEFS,
    MISREAD_SUBTYPES,
    MISREAD_SUBTYPE_LABELS,
    MISREAD_TAG_ID,
    NATURE_PRIORITY,
    TAG_DEFS,
    TAG_REGISTRY,
    apply_misread_tag,
    apply_ocr_confidence_tag,
    classify_misread_text,
    clear_program_tags,
    get_tag,
    has_tag,
    is_tag_reviewed,
    iter_misread_blocks,
    misread_queue_summary,
    misread_subtype_counts,
    program_tag_ids,
    prune_program_tags_after_source_edit,
    remove_tag,
    set_tag,
    set_tags_reviewed,
    sorted_tag_ids,
    tag_misread_hook,
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

    def test_misread_tag_registered_as_program_only(self):
        """D38 的「程序专用」声明是人工打标途径的判据，不是 source 字段。"""
        tag = TAG_REGISTRY[MISREAD_TAG_ID]
        self.assertTrue(tag.program_only)
        self.assertEqual(tag.nature, "doubt")
        self.assertEqual(tag.source, "program")
        # ocr_low_conf 的 source 同为 program，但仍提供人工打标途径
        self.assertFalse(TAG_REGISTRY["ocr_low_conf"].program_only)
        self.assertTrue(TAG_REGISTRY["ocr_low_conf"].name)

    def test_manual_tag_defs_exclude_program_only(self):
        ids = [t.id for t in MANUAL_TAG_DEFS]
        self.assertNotIn(MISREAD_TAG_ID, ids)
        self.assertEqual(
            sorted(ids), sorted(t.id for t in TAG_DEFS if not t.program_only)
        )
        # 其余 5 类保持可人工打标（工具栏行数契约不变）
        self.assertEqual(len(MANUAL_TAG_DEFS), 5)

    def test_misread_subtypes_declared(self):
        self.assertEqual(len(MISREAD_SUBTYPES), 4)
        for sid in MISREAD_SUBTYPES:
            self.assertTrue(MISREAD_SUBTYPE_LABELS[sid])


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

    def test_reviewed_entry_skipped_on_rerun(self):
        """D28 三分支：已驳回条目在重跑时既不被覆写也不被摘除。"""
        set_tag(self.blk, "ocr_low_conf", "program", score=0.2, reviewed=True)
        apply_ocr_confidence_tag(self.blk, 0.99, threshold=0.6)
        entry = self.blk.tags["ocr_low_conf"]
        self.assertEqual(entry["score"], 0.2)
        self.assertTrue(entry["reviewed"])


class TestMisreadClassifier(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(classify_misread_text(""), ["empty"])
        self.assertEqual(classify_misread_text("   "), ["empty"])
        self.assertEqual(classify_misread_text(None), ["empty"])

    def test_empty_short_circuits_other_classes(self):
        """空块只命中 empty，否则「无假名无汉字」会被空块平凡命中。"""
        self.assertEqual(classify_misread_text(""), ["empty"])

    def test_numeric(self):
        for s in ("8", "166", "11", "４２"):
            self.assertIn("numeric", classify_misread_text(s))
        self.assertNotIn("numeric", classify_misread_text("8a"))

    def test_symbolic(self):
        for s in ("?", "……", "①", "#", "—", "☆"):
            self.assertIn("symbolic", classify_misread_text(s))
        # ① 是 Unicode 类别 No：str.isalnum()／str.isdigit() 都会把它算作
        # 数字，判定刻意改用 isalpha()/isdecimal() 才归得到「纯符号」
        self.assertTrue("①".isalnum())
        self.assertTrue("①".isdigit())
        self.assertFalse("①".isdecimal())
        self.assertNotIn("symbolic", classify_misread_text("166"))

    def test_no_japanese(self):
        for s in ("SS", "H1", "anad", "166", "?"):
            self.assertIn("no_japanese", classify_misread_text(s))
        for s in ("こんにちは", "漫画", "ガッ", "々"):
            self.assertNotIn("no_japanese", classify_misread_text(s))
        # 混排：有假名即不算
        self.assertNotIn("no_japanese", classify_misread_text("SSあ"))

    def test_normal_japanese_text_has_no_subtype(self):
        self.assertEqual(classify_misread_text("おはよう"), [])
        self.assertEqual(classify_misread_text("Hello 世界"), [])

    def test_multiple_subtypes_allowed(self):
        """D38：一个块可同时命中多类。"""
        self.assertEqual(
            classify_misread_text("166"), ["numeric", "no_japanese"]
        )
        self.assertEqual(
            classify_misread_text("?"), ["symbolic", "no_japanese"]
        )


class TestMisreadTag(unittest.TestCase):
    def setUp(self):
        self.blk = TextBlock()

    def test_apply_and_clear(self):
        apply_misread_tag(self.blk, ["empty"])
        entry = get_tag(self.blk, MISREAD_TAG_ID)
        self.assertEqual(entry["source"], "program")
        self.assertEqual(entry["subtypes"], ["empty"])
        apply_misread_tag(self.blk, [])
        self.assertFalse(has_tag(self.blk, MISREAD_TAG_ID))

    def test_subtypes_refreshed_without_reviewed(self):
        apply_misread_tag(self.blk, ["numeric", "no_japanese"])
        apply_misread_tag(self.blk, ["empty"])
        self.assertEqual(
            get_tag(self.blk, MISREAD_TAG_ID)["subtypes"], ["empty"]
        )

    def test_reviewed_entry_skipped(self):
        apply_misread_tag(self.blk, ["numeric"])
        set_tags_reviewed(self.blk, True)
        apply_misread_tag(self.blk, ["empty"])
        entry = get_tag(self.blk, MISREAD_TAG_ID)
        self.assertEqual(entry["subtypes"], ["numeric"])
        self.assertTrue(entry["reviewed"])
        # 未命中也不摘除已驳回条目（驳回记忆存活）
        apply_misread_tag(self.blk, [])
        self.assertTrue(has_tag(self.blk, MISREAD_TAG_ID))

    def test_manual_entry_not_overwritten(self):
        set_tag(self.blk, MISREAD_TAG_ID, "manual")
        apply_misread_tag(self.blk, ["empty"])
        self.assertEqual(
            get_tag(self.blk, MISREAD_TAG_ID), {"source": "manual"}
        )

    def test_hook_tags_each_block(self):
        blks = [
            TextBlock(text=["166"]),
            TextBlock(text=["こんにちは"]),
            TextBlock(text=[]),
        ]
        tag_misread_hook(textblocks=blks, img=None, ocr_module=None)
        self.assertEqual(
            get_tag(blks[0], MISREAD_TAG_ID)["subtypes"],
            ["numeric", "no_japanese"],
        )
        self.assertFalse(has_tag(blks[1], MISREAD_TAG_ID))
        self.assertEqual(get_tag(blks[2], MISREAD_TAG_ID)["subtypes"], ["empty"])

    def test_hook_handles_str_text(self):
        """LLM OCR 会把 blk.text 写成字符串（非逐行列表）。"""
        blk = TextBlock()
        blk.text = "8"
        tag_misread_hook(textblocks=[blk], img=None, ocr_module=None)
        self.assertTrue(has_tag(blk, MISREAD_TAG_ID))

    def test_hook_short_circuits_none_ocr(self):
        """D39：none_ocr 语义是保留已有文本，不得凭空挂标。"""

        class _Mod:
            name = "none_ocr"

        blk = TextBlock(text=[])
        tag_misread_hook(textblocks=[blk], img=None, ocr_module=_Mod())
        self.assertFalse(has_tag(blk, MISREAD_TAG_ID))


class TestReviewState(unittest.TestCase):
    def setUp(self):
        self.blk = TextBlock()

    def test_reject_is_block_level_and_reversible(self):
        """D28：驳回作用于该块全部 program 条目，且可逆、不引入第三态。"""
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1)
        apply_misread_tag(self.blk, ["numeric"])
        set_tag(self.blk, "handwritten", "manual")
        self.assertEqual(set_tags_reviewed(self.blk, True), 2)
        self.assertTrue(is_tag_reviewed(self.blk, "ocr_low_conf"))
        self.assertTrue(is_tag_reviewed(self.blk, MISREAD_TAG_ID))
        self.assertFalse(is_tag_reviewed(self.blk, "handwritten"))
        # 取消驳回：条目保留、reviewed 清掉
        self.assertEqual(set_tags_reviewed(self.blk, False), 2)
        self.assertFalse(is_tag_reviewed(self.blk, "ocr_low_conf"))
        self.assertTrue(has_tag(self.blk, "ocr_low_conf"))
        # 重复驳回/重复取消是幂等的
        self.assertEqual(set_tags_reviewed(self.blk, False), 0)

    def test_old_project_entry_without_reviewed(self):
        self.blk.tags[MISREAD_TAG_ID] = {"source": "program", "subtypes": ["empty"]}
        self.assertFalse(is_tag_reviewed(self.blk, MISREAD_TAG_ID))

    def test_program_tag_ids_ignores_non_dict(self):
        self.blk.tags["ghost"] = "not-a-dict"
        set_tag(self.blk, "ocr_low_conf", "program")
        self.assertEqual(program_tag_ids(self.blk), ["ocr_low_conf"])

    def test_clear_program_tags_removes_even_reviewed(self):
        """D29：人工编辑即知情，整条删除、不区分是否带 reviewed。"""
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1, reviewed=True)
        apply_misread_tag(self.blk, ["numeric"])
        set_tag(self.blk, "handwritten", "manual")
        self.assertEqual(clear_program_tags(self.blk), 2)
        self.assertEqual(list(self.blk.tags), ["handwritten"])


class TestSourceEditPrune(unittest.TestCase):
    """D29 人工编辑即知情：原文被改写后清该块全部程序标签。"""

    def setUp(self):
        self.blk = TextBlock(text=["166"])
        set_tag(self.blk, "ocr_low_conf", "program", score=0.1, reviewed=True)
        apply_misread_tag(self.blk, classify_misread_text(self.blk.get_text()))
        set_tag(self.blk, "handwritten", "manual")

    def test_panel_text_differs_clears_all_program_tags(self):
        # 面板原文与数据层不一致 ＝ 人工编辑 → 清（含已驳回条目）
        self.assertEqual(
            prune_program_tags_after_source_edit(self.blk, "160"), 2
        )
        self.assertEqual(list(self.blk.tags), ["handwritten"])

    def test_in_sync_panel_write_keeps_tags(self):
        # 程序性面板回写（如合并命令把并集文本同步回面板）两侧一致 → 不清
        self.assertEqual(
            prune_program_tags_after_source_edit(self.blk, self.blk.get_text()),
            0,
        )
        self.assertEqual(
            sorted(self.blk.tags), sorted(["ocr_low_conf", MISREAD_TAG_ID, "handwritten"])
        )

    def test_no_program_tags_is_noop(self):
        blk = TextBlock(text=["こんにちは"])
        set_tag(blk, "trans_confusing", "manual")
        self.assertEqual(
            prune_program_tags_after_source_edit(blk, "こんばんは"), 0
        )
        self.assertEqual(list(blk.tags), ["trans_confusing"])

    def test_str_text_block(self):
        blk = TextBlock()
        blk.text = "8"
        apply_misread_tag(blk, classify_misread_text(blk.get_text()))
        self.assertEqual(prune_program_tags_after_source_edit(blk, "8"), 0)
        self.assertEqual(prune_program_tags_after_source_edit(blk, "8月"), 1)
        self.assertEqual(blk.tags, {})

    def test_none_panel_text_treated_as_empty(self):
        blk = TextBlock(text=[])
        apply_misread_tag(blk, classify_misread_text(blk.get_text()))
        self.assertEqual(prune_program_tags_after_source_edit(blk, None), 0)
        self.assertEqual(prune_program_tags_after_source_edit(blk, "12"), 1)


class TestQueueQueries(unittest.TestCase):
    def _pages(self):
        clean = TextBlock(text=["こんにちは"])
        empty = TextBlock(text=[])
        numeric = TextBlock(text=["166"])
        rejected = TextBlock(text=["?"])
        apply_misread_tag(empty, classify_misread_text(empty.get_text()))
        apply_misread_tag(numeric, classify_misread_text(numeric.get_text()))
        apply_misread_tag(rejected, classify_misread_text(rejected.get_text()))
        set_tags_reviewed(rejected, True)
        # 只带 ocr_low_conf 的块不进队列（D28 口径）
        low_conf = TextBlock(text=["あ"])
        set_tag(low_conf, "ocr_low_conf", "program", score=0.1)
        return {
            "p1": [clean, empty, numeric],
            "p2": [rejected, low_conf],
        }

    def test_iter_misread_blocks(self):
        got = [(p, i) for p, i, _ in iter_misread_blocks(self._pages())]
        self.assertEqual(got, [("p1", 1), ("p1", 2), ("p2", 0)])

    def test_summary_counts(self):
        self.assertEqual(
            misread_queue_summary(self._pages()),
            {"total": 3, "rejected": 1, "pending": 2},
        )

    def test_subtype_counts_may_exceed_blocks(self):
        counts = misread_subtype_counts(self._pages())
        self.assertEqual(counts["empty"], 1)
        self.assertEqual(counts["numeric"], 1)
        self.assertEqual(counts["symbolic"], 1)
        # 纯符号块同时命中 no_japanese：条目数可大于块数（D38）
        self.assertEqual(counts["no_japanese"], 2)

    def test_empty_pages(self):
        self.assertEqual(misread_queue_summary({}), {"total": 0, "rejected": 0, "pending": 0})


class TestOcrHookWiring(unittest.TestCase):
    """D39：钩子注册在 ``OCRBase`` 上即覆盖全部 OCR 子类实例。"""

    def test_hook_registered_on_base_reaches_subclass(self):
        from modules.ocr.base import OCRBase

        calls = []

        def _probe(*, textblocks=None, img=None, ocr_module=None):
            calls.append((textblocks, img, ocr_module))

        OCRBase.register_postprocess_hooks({"probe_for_test": _probe})

        class _Stub(OCRBase):
            def all_model_loaded(self):
                return True

            def load_model(self):
                pass

            def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
                for b in blk_list:
                    b.text = ["166"]

        import numpy as np

        try:
            blks = [TextBlock(), TextBlock()]
            _Stub().run_ocr(np.zeros((4, 4, 3), np.uint8), blks)
        finally:
            OCRBase._postprocess_hooks.pop("probe_for_test", None)

        # 钩子按纯关键字被调用，且拿到整块列表
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], blks)

    def test_real_hook_tags_end_to_end(self):
        """真钩子挂在基类上：跑一次 OCR 即自动挂出误识别标签。"""
        from modules.ocr.base import OCRBase

        OCRBase.register_postprocess_hooks({"tag_misread": tag_misread_hook})

        class _Stub(OCRBase):
            def all_model_loaded(self):
                return True

            def load_model(self):
                pass

            def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
                for b in blk_list:
                    b.text = ["166"]

        import numpy as np

        blks = [TextBlock()]
        _Stub().run_ocr(np.zeros((4, 4, 3), np.uint8), blks)
        self.assertEqual(
            get_tag(blks[0], MISREAD_TAG_ID)["subtypes"],
            ["numeric", "no_japanese"],
        )

    def test_none_ocr_instance_short_circuits(self):
        from modules.ocr.base import OCRBase

        class _Stub(OCRBase):
            def all_model_loaded(self):
                return True

            def load_model(self):
                pass

            def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
                pass

        import numpy as np

        stub = _Stub()
        stub.name = "none_ocr"
        blks = [TextBlock(text=[])]
        stub.run_ocr(np.zeros((4, 4, 3), np.uint8), blks)
        self.assertFalse(has_tag(blks[0], MISREAD_TAG_ID))


if __name__ == "__main__":
    unittest.main()
