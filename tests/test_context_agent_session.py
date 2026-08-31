"""context_agent 会话循环与工具面回归测试(阶段 1,mock chat,offscreen)。

覆盖:纯文本即指令轮结束 / patch 落草稿回执 / 软护栏两段式(请收尾→停)
/ 单工具崩溃不作废整轮 / cancel / 轮间消息裁剪 / execute_context_tool
的页索引→页名解析与只读白名单拒绝。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_context_agent_session.py
"""

import json
import os
import os.path as osp
import sys
import types
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from modules.context_agent import session as session_mod  # noqa: E402
from modules.context_agent.draft import GlossaryDraft, StoryDraft  # noqa: E402
from modules.context_agent.session import (  # noqa: E402
    STOP_MAX_TURNS,
    STOP_REPLY,
    KEEP_LAST_MESSAGES,
    run_agent_session,
    trim_session_messages,
)
from modules.context_agent.tools import (  # noqa: E402
    CONTEXT_READ_TOOLS,
    GLOSSARY_PATCH_TOOL_NAME,
    PAGE_SUMMARIES_TOOL_NAME,
    STORY_PATCH_TOOL_NAME,
    build_context_tools,
    execute_context_tool,
)
from utils.ai_tools import ToolError  # noqa: E402


def _msg(content="", tool_calls=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _call(call_id, name, **arguments):
    return types.SimpleNamespace(
        id=call_id,
        function=types.SimpleNamespace(
            name=name, arguments=json.dumps(arguments)
        ),
    )


class FakeChat:
    """按脚本顺序回放 assistant 消息,记录每次收到的 messages。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, tools, tool_choice):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0), 10


def make_env(with_story=True):
    glossary = GlossaryDraft.from_entries([("勇者", "Hero")])
    story = StoryDraft.from_base({"p01": "existing summary"})
    proj = types.SimpleNamespace(
        pages={"p01": [], "p02": []},
        idx2pagename=lambda i: ["p01", "p02"][i],
    )

    def execute_tool_fn(name, arguments):
        return execute_context_tool(
            name,
            arguments,
            project=proj,
            glossary_draft=glossary,
            story_draft=story,
        )

    return glossary, story, execute_tool_fn


class TestSessionLoop(unittest.TestCase):
    def test_plain_reply_ends_round(self):
        chat = FakeChat([_msg("All done, added nothing.")])
        _, _, exec_fn = make_env()
        result = run_agent_session(
            chat,
            exec_fn,
            system_message="sys",
            user_message="do it",
            tools_openai=[],
        )
        self.assertEqual(result.stopped_reason, STOP_REPLY)
        self.assertEqual(result.reply, "All done, added nothing.")
        self.assertEqual(result.turns, 1)
        self.assertEqual(result.usage_total, 10)

    def test_patch_then_reply(self):
        chat = FakeChat(
            [
                _msg(
                    None,
                    [
                        _call(
                            "c1",
                            GLOSSARY_PATCH_TOOL_NAME,
                            entries=[{"src": "魔王", "dst": "Demon King"}],
                        )
                    ],
                ),
                _msg("Added one entry."),
            ]
        )
        glossary, _, exec_fn = make_env()
        result = run_agent_session(
            chat, exec_fn, system_message="sys", user_message="u",
            tools_openai=[],
        )
        self.assertEqual(result.stopped_reason, STOP_REPLY)
        self.assertEqual(
            sorted(e.source for e in glossary.entries), ["勇者", "魔王"]
        )
        # 第二次调用应含 assistant + tool 回执
        second = chat.calls[1]
        self.assertEqual(second[-1]["role"], "tool")
        receipt = json.loads(second[-1]["content"])
        self.assertEqual(receipt["applied"], 1)

    def test_soft_guardrail_two_stage(self):
        tool_msg = _msg(None, [_call("c", "search_blocks", query="x")])
        chat = FakeChat([tool_msg, tool_msg])
        _, _, exec_fn = make_env()
        result = run_agent_session(
            chat, exec_fn, system_message="sys", user_message="u",
            tools_openai=[], max_turns=1,
        )
        self.assertEqual(result.stopped_reason, STOP_MAX_TURNS)
        self.assertEqual(result.reply, "")
        # 第 2 次调用注入了收尾请求(位于 tool 回执之后)
        self.assertEqual(len(chat.calls), 2)
        self.assertEqual(chat.calls[1][-1]["role"], "user")
        self.assertIn("wrap up", chat.calls[1][-1]["content"])

    def test_token_budget_soft_stop(self):
        chat = FakeChat([_msg("reply")])
        _, _, exec_fn = make_env()
        result = run_agent_session(
            chat, exec_fn, system_message="s", user_message="u",
            tools_openai=[], token_budget=5,
        )
        # 预算耗尽 → 注入收尾 → 模型纯文本收尾,合法结束
        self.assertEqual(result.stopped_reason, STOP_REPLY)
        self.assertEqual(result.reply, "reply")
        self.assertEqual(chat.calls[0][-1]["role"], "user")

    def test_tool_crash_reported_not_fatal(self):
        def boom(name, arguments):
            if name == "search_blocks":
                raise RuntimeError("boom")
            return {"ok": True}

        chat = FakeChat(
            [
                _msg(None, [_call("c1", "search_blocks", query="x")]),
                _msg("recovered."),
            ]
        )
        result = run_agent_session(
            chat, boom, system_message="s", user_message="u",
            tools_openai=[],
        )
        self.assertEqual(result.stopped_reason, STOP_REPLY)
        receipt = json.loads(chat.calls[1][-1]["content"])
        self.assertIn("Tool execution failed", receipt["error"])

    def test_toolerror_passed_to_model(self):
        def exec_fn(name, arguments):
            raise ToolError("narrow it down")

        chat = FakeChat(
            [
                _msg(None, [_call("c1", "read_pages", start=0)]),
                _msg("ok"),
            ]
        )
        result = run_agent_session(
            chat, exec_fn, system_message="s", user_message="u",
            tools_openai=[],
        )
        self.assertEqual(result.stopped_reason, STOP_REPLY)
        receipt = json.loads(chat.calls[1][-1]["content"])
        self.assertEqual(receipt["error"], "narrow it down")

    def test_cancel_raises(self):
        chat = FakeChat([])
        _, _, exec_fn = make_env()
        with self.assertRaises(session_mod._Cancelled):
            run_agent_session(
                chat, exec_fn, system_message="s", user_message="u",
                tools_openai=[], cancel_check=lambda: True,
            )

    def test_trim_keeps_system_and_legal_tail(self):
        messages = [{"role": "system", "content": "s"}]
        messages += [{"role": "tool", "tool_call_id": str(i), "content": "x"}
                     for i in range(20)]
        trimmed = trim_session_messages(messages)
        self.assertEqual(trimmed[0]["role"], "system")
        # 起点落在 tool 回执上必须前移(孤儿 tool 消息会被端点拒绝);
        # 全部裁光也合法(草稿承担记忆),只要不留孤儿 tool 在开头
        if len(trimmed) > 1:
            self.assertNotEqual(trimmed[1]["role"], "tool")
        self.assertLessEqual(len(trimmed), 1 + KEEP_LAST_MESSAGES)

    def test_trim_history_tail_roundtrip(self):
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "u1"},
                    {"role": "assistant", "content": "r1"},
                    {"role": "user", "content": "u2"}]
        trimmed = trim_session_messages(messages, keep_last=3)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["content"], "u1")

    def test_trim_noop_short(self):
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "u"}]
        self.assertEqual(trim_session_messages(messages), messages)


class TestContextTools(unittest.TestCase):
    def test_story_patch_resolves_page_index(self):
        _, story, exec_fn = make_env()
        result = exec_fn(
            STORY_PATCH_TOOL_NAME,
            {"page_summaries": [{"page": 1, "summary": "mage appears."}]},
        )
        self.assertEqual(result["applied"], 1)
        self.assertIn("p02", story.page_summaries)

    def test_story_patch_index_out_of_range(self):
        _, _, exec_fn = make_env()
        result = exec_fn(
            STORY_PATCH_TOOL_NAME,
            {"page_summaries": [{"page": 9, "summary": "x"}]},
        )
        self.assertEqual(result["applied"], 0)
        self.assertEqual(len(result["errors"]), 1)

    def test_read_page_summaries_reports_origins(self):
        _, _, exec_fn = make_env()
        result = exec_fn(PAGE_SUMMARIES_TOOL_NAME, {})
        self.assertEqual(result["type"], "page_summaries")
        self.assertEqual(result["synopsis"], "")
        by_page = {p["page"]: p for p in result["pages"]}
        self.assertEqual(by_page["p01"]["origin"], "existing")

    def test_readonly_whitelist_rejects_write_tools(self):
        _, _, exec_fn = make_env()
        with self.assertRaises(Exception) as ctx:
            exec_fn("set_font", {"ids": "0:0", "ff": "Arial"})
        self.assertIn("Unknown or forbidden", str(ctx.exception))

    def test_build_context_tools_shape(self):
        tools = build_context_tools()
        names = {t["function"]["name"] for t in tools}
        self.assertEqual(names, set(CONTEXT_READ_TOOLS) | {
            GLOSSARY_PATCH_TOOL_NAME,
            STORY_PATCH_TOOL_NAME,
            PAGE_SUMMARIES_TOOL_NAME,
        })
        no_story = build_context_tools(with_story=False)
        self.assertNotIn(
            STORY_PATCH_TOOL_NAME,
            {t["function"]["name"] for t in no_story},
        )


if __name__ == "__main__":
    unittest.main()
