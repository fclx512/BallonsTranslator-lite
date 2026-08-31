"""context_agent 草稿模型回归测试(阶段 1)。

覆盖:patch 应用/冲突裁决(user-owned 保护)/AI 自有条目自由修正/
UI 编辑入口/StoryDraft 两层(页段摘要 + 全局梗概)的同样语义。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_context_agent_draft.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from modules.context_agent.draft import (  # noqa: E402
    ORIGIN_AI,
    ORIGIN_EXISTING,
    ORIGIN_USER,
    DraftEntry,
    DraftValueError,
    GlossaryDraft,
    StoryDraft,
)


class TestGlossaryDraft(unittest.TestCase):
    def test_load_base_and_snapshot(self):
        draft = GlossaryDraft.from_entries(
            [("勇者", "Hero", "title"), ("魔王", "Demon King")]
        )
        snap = draft.snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual(snap[0].origin, ORIGIN_EXISTING)
        self.assertEqual(snap[1].note, "")

    def test_add_new_entry(self):
        draft = GlossaryDraft()
        result = draft.apply_patch(
            [{"action": "add", "src": "魔法使", "dst": "Mage", "info": "race"}]
        )
        self.assertEqual(result["applied"], 1)
        self.assertFalse(result["conflicts"])
        self.assertEqual(draft.entries[0].origin, ORIGIN_AI)

    def test_existing_entry_conflict_not_applied(self):
        draft = GlossaryDraft.from_entries([("勇者", "Hero")])
        result = draft.apply_patch(
            [{"action": "add", "src": "勇者", "dst": "Brave"}]
        )
        self.assertEqual(result["applied"], 0)
        self.assertEqual(len(result["conflicts"]), 1)
        row = result["conflicts"][0]
        self.assertEqual(row["reason"], "user_owned")
        self.assertEqual(row["current"], "Hero")
        self.assertEqual(row["proposed"], "Brave")
        # 草稿未被改动
        self.assertEqual(draft.entries[0].translation, "Hero")

    def test_existing_entry_same_content_noop(self):
        draft = GlossaryDraft.from_entries([("勇者", "Hero")])
        result = draft.apply_patch(
            [{"action": "add", "src": "勇者", "dst": "Hero"}]
        )
        self.assertEqual(result["applied"], 1)
        self.assertEqual(draft.entries[0].origin, ORIGIN_EXISTING)

    def test_ai_entry_free_update(self):
        draft = GlossaryDraft()
        draft.apply_patch([{"src": "a", "dst": "1"}])
        result = draft.apply_patch([{"src": "a", "dst": "2"}])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(draft.entries[0].translation, "2")
        self.assertEqual(draft.entries[0].origin, ORIGIN_AI)

    def test_user_edit_protected_even_against_update(self):
        draft = GlossaryDraft()
        draft.apply_patch([{"src": "a", "dst": "1"}])
        draft.set_user_entry("a", "manual", "note")
        result = draft.apply_patch([{"src": "a", "dst": "2"}])
        self.assertEqual(result["applied"], 0)
        self.assertEqual(draft.entries[0].translation, "manual")
        self.assertEqual(draft.entries[0].origin, ORIGIN_USER)

    def test_remove_rules(self):
        draft = GlossaryDraft.from_entries([("keep", "old")])
        draft.apply_patch([{"src": "mine", "dst": "x"}])
        # 删不存在的 → not_found 冲突
        result = draft.apply_patch([{"action": "remove", "src": "nope"}])
        self.assertEqual(result["conflicts"][0]["reason"], "not_found")
        # 删 existing → user_owned 冲突
        result = draft.apply_patch([{"action": "remove", "src": "keep"}])
        self.assertEqual(result["conflicts"][0]["reason"], "user_owned")
        self.assertEqual(len(draft.entries), 2)
        # 删 AI 自有条目 → 成功
        result = draft.apply_patch([{"action": "remove", "src": "mine"}])
        self.assertEqual(result["applied"], 1)
        self.assertEqual([e.source for e in draft.entries], ["keep"])

    def test_single_failure_does_not_void_round(self):
        draft = GlossaryDraft.from_entries([("protected", "old")])
        result = draft.apply_patch(
            [
                {"action": "add", "src": "protected", "dst": "conflict"},
                {"action": "add", "src": "good", "dst": "ok"},
                {"action": "add", "src": "", "dst": "invalid"},
            ]
        )
        self.assertEqual(result["applied"], 1)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual([e.source for e in draft.entries],
                         ["protected", "good"])

    def test_entries_not_list_rejected(self):
        with self.assertRaises(DraftValueError):
            GlossaryDraft().apply_patch("not a list")

    def test_casefold_identity(self):
        draft = GlossaryDraft.from_entries([("Hero", "勇者")])
        result = draft.apply_patch([{"src": "hero", "dst": "勇者"}])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(len(draft.entries), 1)


class TestStoryDraft(unittest.TestCase):
    def test_load_base(self):
        draft = StoryDraft.from_base(
            {"p01": "Hero meets mage.", "p02": ""}, synopsis="So far..."
        )
        pages, synopsis = draft.snapshot()
        self.assertEqual([p.page_name for p in pages], ["p01"])
        self.assertEqual(pages[0].origin, ORIGIN_EXISTING)
        self.assertEqual(synopsis, "So far...")
        self.assertEqual(draft.synopsis_origin, ORIGIN_EXISTING)

    def test_set_ai_summary_and_update(self):
        draft = StoryDraft()
        draft.apply_patch([{"page": "p1", "summary": "s1"}])
        draft.apply_patch([{"page": "p1", "summary": "s2"}])
        pages, _ = draft.snapshot()
        self.assertEqual(pages[0].summary, "s2")
        self.assertEqual(pages[0].origin, ORIGIN_AI)

    def test_existing_summary_conflict(self):
        draft = StoryDraft.from_base({"p1": "original"})
        result = draft.apply_patch([{"page": "p1", "summary": "rewritten"}])
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["conflicts"][0]["reason"], "user_owned")
        self.assertEqual(draft.page_summaries["p1"].summary, "original")

    def test_remove_rules(self):
        draft = StoryDraft.from_base({"p1": "original"})
        draft.apply_patch([{"page": "p2", "summary": "mine"}])
        result = draft.apply_patch([{"action": "remove", "page": "p1"}])
        self.assertEqual(result["conflicts"][0]["reason"], "user_owned")
        result = draft.apply_patch([{"action": "remove", "page": "p2"}])
        self.assertEqual(result["applied"], 1)
        result = draft.apply_patch([{"action": "remove", "page": "p9"}])
        self.assertEqual(result["conflicts"][0]["reason"], "not_found")

    def test_synopsis_full_replacement(self):
        draft = StoryDraft()
        draft.apply_patch([], synopsis="v1")
        result = draft.apply_patch([], synopsis="v2")
        self.assertEqual(result["applied"], 1)
        self.assertEqual(draft.synopsis, "v2")

    def test_synopsis_existing_conflict(self):
        draft = StoryDraft.from_base({}, synopsis="human written")
        result = draft.apply_patch([], synopsis="ai rewrite")
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["conflicts"][0]["field"], "synopsis")
        self.assertEqual(draft.synopsis, "human written")

    def test_user_synopsis_protected(self):
        draft = StoryDraft()
        draft.set_user_synopsis("mine")
        result = draft.apply_patch([], synopsis="ai rewrite")
        self.assertEqual(result["applied"], 0)
        self.assertEqual(draft.synopsis, "mine")

    def test_empty_synopsis_ignored(self):
        draft = StoryDraft()
        result = draft.apply_patch([], synopsis="   ")
        self.assertEqual(result["applied"], 0)
        self.assertEqual(draft.synopsis, "")


if __name__ == "__main__":
    unittest.main()
