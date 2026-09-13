"""框级 AI 动作与指示标签注入测试（批次 C/D 纯逻辑层）。

覆盖 utils/block_actions.py、utils/block_tags.py::directive_instructions、
modules/translators/agent/prompts.py::build_user_task_message 的注入契约。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_block_actions.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils.block_actions import (  # noqa: E402
    ACTION_REGISTRY,
    actions_for_block,
    block_crop_base64,
    build_context_lines,
    build_ocr_fix_messages,
    build_ocr_fix_payload,
    page_data_needs_sync,
    parse_ocr_fix_reply,
    stitch_line_crops,
)
from utils.block_tags import (  # noqa: E402
    directive_instructions,
    set_tag,
)
from utils.textblock import TextBlock  # noqa: E402


class TestActionRegistry(unittest.TestCase):
    def test_registry_complete(self):
        self.assertIn("act_ocr_fix", ACTION_REGISTRY)
        self.assertIn("act_retranslate", ACTION_REGISTRY)
        self.assertTrue(ACTION_REGISTRY["act_ocr_fix"].needs_vision)
        self.assertFalse(ACTION_REGISTRY["act_retranslate"].needs_vision)

    def test_actions_for_block(self):
        blk = TextBlock()
        self.assertEqual(actions_for_block(blk), [])
        set_tag(blk, "ocr_low_conf", "program")
        self.assertEqual(
            [a.id for a in actions_for_block(blk)], ["act_ocr_fix"]
        )
        set_tag(blk, "handwritten", "manual")
        # 同一动作被多标签命中时不重复
        self.assertEqual(
            [a.id for a in actions_for_block(blk)], ["act_ocr_fix"]
        )
        set_tag(blk, "trans_polish", "manual")
        self.assertEqual(
            sorted(a.id for a in actions_for_block(blk)),
            ["act_ocr_fix", "act_retranslate"],
        )


class TestContextAssembly(unittest.TestCase):
    def test_context_lines_radius_and_mark(self):
        blks = [TextBlock(text=[f"line{i}"]) for i in range(5)]
        lines = build_context_lines(blks, 2, radius=1)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].lstrip().startswith("->"))
        self.assertFalse(lines[0].lstrip().startswith("->"))

    def test_context_lines_skips_empty(self):
        blks = [TextBlock(text=["a"]), TextBlock(text=[""]), TextBlock(text=["b"])]
        lines = build_context_lines(blks, 2, radius=2)
        self.assertEqual(lines, ["    a", " -> b"])

    def test_ocr_fix_messages_structure(self):
        messages = build_ocr_fix_messages(["-> target"], "hello")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("-> target", messages[1]["content"])
        self.assertIn("hello", messages[1]["content"])

    def test_crop_out_of_bounds(self):
        import numpy as np

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        inside = TextBlock(xyxy=[10, 10, 50, 50])
        outside = TextBlock(xyxy=[90, 90, 120, 120])
        self.assertIsNotNone(block_crop_base64(img, inside))
        self.assertIsNone(block_crop_base64(img, outside))


def _line_quad(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _ocr_blk(texts, n_lines, vertical=False):
    """带行几何的块（模拟 OCR 模块写入的形态：text 与 lines 一一对应）。"""
    blk = TextBlock()
    blk.src_is_vertical = vertical
    blk.lines = [_line_quad(20, 20 + 40 * i, 180, 50 + 40 * i) for i in range(n_lines)]
    blk.text = list(texts)
    blk.xyxy = [20, 20, 180, 20 + 40 * (n_lines - 1) + 30]
    return blk


class TestStitchLineCrops(unittest.TestCase):
    def test_empty_lines_gives_none(self):
        import numpy as np

        blk = TextBlock()
        blk.lines = []
        self.assertIsNone(stitch_line_crops(np.zeros((100, 100, 3), np.uint8), blk))

    def test_stacks_every_line(self):
        import base64

        import cv2
        import numpy as np

        blk = _ocr_blk(["一", "二"], 2)
        img = np.zeros((160, 220, 3), np.uint8)
        img[18:52, 18:182] = 255
        img[58:92, 18:182] = 200
        b64 = stitch_line_crops(img, blk)
        self.assertIsNotNone(b64)
        crop = cv2.imdecode(
            np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR
        )
        # 两行归一后纵向堆叠：高度约为两行之和 + 行间缝
        self.assertGreater(crop.shape[0], 100)
        self.assertLessEqual(crop.shape[0], 2 * 64 + 20)


class TestOcrFixPayload(unittest.TestCase):
    def test_per_line_when_text_aligns_with_lines(self):
        import numpy as np

        blk = _ocr_blk(["こんにちは", "げんき"], 2)
        img = np.full((160, 220, 3), 255, np.uint8)
        payload = build_ocr_fix_payload(img, blk, ["-> 别的块"], hint="这是手写体")
        self.assertTrue(payload.per_line)
        self.assertEqual(payload.line_texts, ["こんにちは", "げんき"])
        self.assertTrue(payload.preview_b64)
        # 多模态：content 必须是数组（图像段不能 append 到 str）
        content = payload.messages[-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[1]["type"], "image_url")
        text_part = content[0]["text"]
        self.assertIn("1: こんにちは", text_part)
        self.assertIn("-> 别的块", text_part)
        self.assertIn("这是手写体", text_part)

    def test_blob_when_text_does_not_align(self):
        import numpy as np

        # 合并块/人工改过的块：text 是整段，行数对不上 → 不逐行
        blk = _ocr_blk(["一整段合并文本"], 3)
        img = np.full((200, 220, 3), 255, np.uint8)
        payload = build_ocr_fix_payload(img, blk, [])
        self.assertFalse(payload.per_line)
        self.assertEqual(payload.line_texts, ["一整段合并文本"])
        self.assertTrue(payload.preview_b64)

    def test_out_of_bounds_raises(self):
        import numpy as np

        blk = TextBlock()
        blk.xyxy = [500, 500, 600, 600]
        with self.assertRaises(RuntimeError):
            build_ocr_fix_payload(np.zeros((100, 100, 3), np.uint8), blk, [])


class TestPageDataSync(unittest.TestCase):
    """合并/撤销只改视觉层：动作前必须先判出数据层脱节（合并块读到子块的真凶）。"""

    def test_in_sync_is_false(self):
        blk = TextBlock()

        class _Item:
            pass

        item = _Item()
        item.blk = blk
        self.assertFalse(page_data_needs_sync([blk], [item]))

    def test_length_mismatch_is_true(self):
        # 画布合并后：数据层还是两个子块
        blk = TextBlock()

        class _Item:
            pass

        item = _Item()
        item.blk = blk
        self.assertTrue(page_data_needs_sync([blk, TextBlock()], [item]))

    def test_identity_mismatch_is_true(self):
        class _Item:
            pass

        item = _Item()
        item.blk = TextBlock()
        self.assertTrue(page_data_needs_sync([TextBlock()], [item]))

    def test_missing_list_is_false(self):
        self.assertFalse(page_data_needs_sync(None, []))


class TestParseOcrFixReply(unittest.TestCase):

    def test_exact_count(self):
        self.assertEqual(parse_ocr_fix_reply("a\nb", 2), ["a", "b"])

    def test_trailing_newline_ignored(self):
        self.assertEqual(parse_ocr_fix_reply("a\nb\n", 2), ["a", "b"])

    def test_numbering_stripped(self):
        self.assertEqual(
            parse_ocr_fix_reply("1: a\n2) b\n", 2), ["a", "b"]
        )

    def test_interior_empty_line_kept(self):
        self.assertEqual(parse_ocr_fix_reply("a\n\nb", 3), ["a", "", "b"])

    def test_mismatch_gives_empty(self):
        # 模型把两行合成一行：不猜对齐，退回整块草稿
        self.assertEqual(parse_ocr_fix_reply("ab", 2), [])
        self.assertEqual(parse_ocr_fix_reply("a\nb\nc", 2), [])
        self.assertEqual(parse_ocr_fix_reply("", 2), [])
        self.assertEqual(parse_ocr_fix_reply("a", 0), [])


class TestDirectiveInstructions(unittest.TestCase):
    def test_none_by_default(self):
        self.assertEqual(directive_instructions(TextBlock()), [])

    def test_directive_tags_mapped(self):
        blk = TextBlock()
        set_tag(blk, "onomatopoeia", "manual")
        set_tag(blk, "handwritten", "manual")
        inst = directive_instructions(blk)
        self.assertEqual(len(inst), 2)
        # 疑点标签不产出指令
        set_tag(blk, "ocr_low_conf", "program")
        self.assertEqual(len(directive_instructions(blk)), 2)

    def test_doubt_only_gives_empty(self):
        blk = TextBlock()
        set_tag(blk, "trans_confusing", "manual")
        self.assertEqual(directive_instructions(blk), [])


class TestPromptInjection(unittest.TestCase):
    def test_user_task_message_without_tags(self):
        from modules.translators.agent.prompts import build_user_task_message

        msg = build_user_task_message(["a", "b"], "Page 1", "")
        self.assertIn('"id": 1', msg)
        self.assertNotIn("instructions", msg)
        self.assertNotIn("per-block", msg)

    def test_user_task_message_with_tags(self):
        from modules.translators.agent.prompts import build_user_task_message

        msg = build_user_task_message(
            ["a", "b"],
            "Page 1",
            "",
            tag_instructions=[None, ["render phonetically"]],
        )
        self.assertIn("instructions", msg)
        self.assertIn("render phonetically", msg)
        # 无指令的块不写字段
        self.assertIn('{"id": 1, "text": "a"}', msg)
        self.assertIn("per-block", msg)

    def test_user_task_message_all_none(self):
        from modules.translators.agent.prompts import build_user_task_message

        msg = build_user_task_message(
            ["a"], "Page 1", "", tag_instructions=[None]
        )
        self.assertNotIn("instructions", msg)

    def test_user_task_message_with_hint(self):
        from modules.translators.agent.prompts import build_user_task_message

        msg = build_user_task_message(["a"], "Page 1", "", hint="语气更冲")
        self.assertIn("Additional requirement from the user", msg)
        self.assertIn("语气更冲", msg)
        # 纯空白视作没有补充要求
        blank = build_user_task_message(["a"], "Page 1", "", hint="   ")
        self.assertNotIn("Additional requirement from the user", blank)


if __name__ == "__main__":
    unittest.main()
