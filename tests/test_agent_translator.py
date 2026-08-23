"""Offscreen contract tests for the translation agent package
(``modules/translators/agent/``): loop state machine, submission
validator, prompt assembly and tool surface.

Pure logic tests driven by a fake chat — no network, no QApplication, no
translator instantiation.  Run from the repo root:

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_agent_translator.py -q
"""

import json
import os
import os.path as osp
import sys
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from modules.translators.agent.loop import (  # noqa: E402
    FORCED_TOOL_CHOICE,
    AgentTaskCancelled,
    run_agent_task,
)
from modules.translators.agent.prompts import (  # noqa: E402
    build_history_snippet,
    build_page_context_snippet,
    build_system_message,
    build_user_task_message,
    page_label,
)
from modules.translators.agent.tools import (  # noqa: E402
    GLOSSARY_TOOL_NAME,
    SUBMIT_TOOL_NAME,
    available_tool_defs,
    cap_tool_result,
    execute_agent_tool,
)
from modules.translators.agent.validator import (  # noqa: E402
    clean_translation,
    validate_submission,
)
from utils.ai_tools import ToolError, to_openai_tools  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Function(name, arguments)


class _Message:
    """Mimics the OpenAI chat message surface used by the loop."""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class ScriptedChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools, tool_choice):
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": list(tools),
                "tool_choice": tool_choice,
            }
        )
        assert self.responses, "ScriptedChat ran out of responses"
        message, usage = self.responses.pop(0)
        return message, usage


def submit_call(call_id, translations):
    return _ToolCall(call_id, SUBMIT_TOOL_NAME, json.dumps({"translations": translations}))


def read_call(call_id, start=0, end=-1):
    return _ToolCall(call_id, "read_pages", json.dumps({"start": start, "end": end}))


def _run(chat, *, src=("こんにちは", "世界"), executor=None, **kwargs):
    tools_openai = to_openai_tools(available_tool_defs(project=object()))
    submit_openai = to_openai_tools(
        [d for d in available_tool_defs(None, None) if d["name"] == SUBMIT_TOOL_NAME]
    )[0]
    executed = []

    def recording_executor(name, arguments):
        executed.append((name, arguments))
        if executor is not None:
            return executor(name, arguments)
        return {"type": "detail", "pages": []}

    return (
        run_agent_task(
            chat,
            recording_executor,
            src,
            system_message="system",
            user_message="user",
            tools_openai=tools_openai,
            submit_tool_openai=submit_openai,
            **kwargs,
        ),
        executed,
    )


def test_loop_status_cb_called_per_turn():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 50),
            (_Message(tool_calls=[submit_call("b", {"1": "你好", "2": "世界"})]), 80),
        ]
    )
    seen = []
    _run(
        chat,
        src=("x", "y"),
        status_cb=lambda turn, names, usage: seen.append((turn, names, usage)),
    )
    assert len(seen) == 2
    assert seen[0] == (1, ["read_pages"], 50)
    assert seen[1] == (2, [SUBMIT_TOOL_NAME], 80)


def test_loop_same_as_source_warns_then_rejects():
    # r1: id1 译文=原文 → 警告并接受(缺 id2);r2: id1 再犯 → 打回并移除旧条目;
    # r3: 修正后通过
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[submit_call("a", {"1": "フフ"})]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "フフ", "2": "帅气"})]), 10),
            (_Message(tool_calls=[submit_call("c", {"1": "哈哈哈"})]), 10),
        ]
    )
    result, _ = _run(chat, src=("フフ", "カッコイイ"))
    assert result == {1: "哈哈哈", 2: "帅气"}
    # 警告出现在第一轮结果里
    first_tool = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    payload = json.loads(first_tool["content"])
    assert payload["missing_ids"] == [2]
    assert any("equal their source" in w for w in payload["warnings"])
    # 第二轮:再犯被打回,id1 重新缺失
    second_tool = [m for m in chat.calls[2]["messages"] if m["role"] == "tool"][-1]
    payload2 = json.loads(second_tool["content"])
    assert "still equal" in payload2["warning"]
    assert payload2["missing_ids"] == [1]


def test_loop_term_residue_rejected_on_second_submit():
    # r1: id1 术语残留 → 警告并接受(缺 id2);r2: 再犯 → 打回;r3: 修正后通过
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[submit_call("a", {"1": "ルフィは海賊だ"})]), 10),
            (_Message(tool_calls=[submit_call("b", {"2": "帅气", "1": "ルフィは海賊だ"})]), 10),
            (_Message(tool_calls=[submit_call("c", {"1": "路飞是海贼"})]), 10),
        ]
    )
    result, _ = _run(
        chat,
        src=("ルフィは海賊だ", "カッコイイ"),
        glossary_terms=[("ルフィ", "路飞")],
    )
    assert result == {1: "路飞是海贼", 2: "帅气"}
    second_tool = [m for m in chat.calls[2]["messages"] if m["role"] == "tool"][-1]
    payload = json.loads(second_tool["content"])
    assert "Glossary source terms" in payload["warning"]
    assert payload["missing_ids"] == [1]


# ── loop state machine ───────────────────────────────────────────────


def test_loop_immediate_submit():
    chat = ScriptedChat([(_Message(tool_calls=[submit_call("a", {"1": "你好", "2": "世界"})]), 100)])
    result, _ = _run(chat)
    assert result == {1: "你好", 2: "世界"}
    assert len(chat.calls) == 1


def test_loop_explore_then_submit():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 50),
            (_Message(tool_calls=[submit_call("b", {"1": "你好", "2": "世界"})]), 80),
        ]
    )
    executed = []

    def executor(name, arguments):
        executed.append((name, arguments))
        return {"type": "detail", "pages": [{"pidx": 0}]}

    result, executed = _run(chat, executor=executor, src=("x", "y"))
    assert result == {1: "你好", 2: "世界"}
    assert executed == [("read_pages", {"start": 0, "end": -1})]
    # 工具结果进了 tool 消息,assistant 消息带 tool_calls
    last_messages = chat.calls[1]["messages"]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in last_messages)
    tool_msgs = [m for m in last_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "detail" in tool_msgs[0]["content"]


def test_loop_invalid_id_rejected_then_fixed():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[submit_call("a", {"99": "越界", "1": "你好"})]), 10),
            (_Message(tool_calls=[submit_call("b", {"2": "世界"})]), 10),
        ]
    )
    result, _ = _run(chat)
    assert result == {1: "你好", 2: "世界"}
    first_tool_msg = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    payload = json.loads(first_tool_msg["content"])
    assert "99" in payload["warning"] or "Invalid" in payload["warning"]
    assert payload["missing_ids"] == [2]


def test_loop_partial_submit_demands_missing():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[submit_call("a", {"1": "你好"})]), 10),
            (_Message(tool_calls=[submit_call("b", {"2": "世界"})]), 10),
        ]
    )
    result, _ = _run(chat)
    assert result == {1: "你好", 2: "世界"}
    tool_msg = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(tool_msg["content"])["missing_ids"] == [2]


def test_loop_forced_convergence_on_turn_exhaustion():
    # max_turns=1:第一轮探索,之后必须进入强制收敛轮(tools 只剩 submit + tool_choice 锁定)
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "部分", "2": "结果"})]), 10),
        ]
    )
    result, _ = _run(chat, max_turns=1)
    assert result == {1: "部分", 2: "结果"}
    forced_call = chat.calls[1]
    assert forced_call["tool_choice"] == FORCED_TOOL_CHOICE
    assert [t["function"]["name"] for t in forced_call["tools"]] == [SUBMIT_TOOL_NAME]


def test_loop_forced_convergence_returns_partial_when_incomplete():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "只有一"})]), 10),
        ]
    )
    result, _ = _run(chat, max_turns=1)
    assert result == {1: "只有一"}  # 缺 2,交由调用方补漏(回退直译)


def test_loop_token_budget_triggers_convergence():
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 500),
            (_Message(tool_calls=[submit_call("b", {"1": "a", "2": "b"})]), 10),
        ]
    )
    result, _ = _run(chat, max_turns=8, token_budget=100)
    assert result == {1: "a", 2: "b"}
    assert chat.calls[1]["tool_choice"] == FORCED_TOOL_CHOICE


def test_loop_text_only_round_gets_reminder_then_forced():
    chat = ScriptedChat(
        [
            (_Message(content="I think the translation is..."), 10),
            (_Message(content="still prose"), 10),
            (_Message(tool_calls=[submit_call("c", {"1": "好", "2": "的"})]), 10),
        ]
    )
    result, _ = _run(chat)
    assert result == {1: "好", 2: "的"}
    # 提醒注入在第二轮请求前
    second_request_messages = chat.calls[1]["messages"]
    assert any(
        m["role"] == "user" and "submit_translations" in m["content"]
        for m in second_request_messages
    )
    # 第二次仍纯文本 → 第三次进入强制收敛
    assert chat.calls[2]["tool_choice"] == FORCED_TOOL_CHOICE


def test_loop_bad_json_arguments_reported():
    bad = _ToolCall("a", SUBMIT_TOOL_NAME, "{not json")
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[bad]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "x", "2": "y"})]), 10),
        ]
    )
    result, _ = _run(chat)
    assert result == {1: "x", 2: "y"}
    tool_msg = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "not valid JSON" in json.loads(tool_msg["content"])["error"]


def test_loop_unknown_tool_reported_not_fatal():
    # loop 层把工具名交给 executor 裁决;白名单拒绝(ToolError)原样回传给模型
    def rejecting_executor(name, arguments):
        raise ToolError(f"Unknown or forbidden tool: {name}")

    unknown = _ToolCall("a", "set_font", json.dumps({"ids": "0:0"}))
    chat = ScriptedChat(
        [
            (_Message(tool_calls=[unknown]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "x", "2": "y"})]), 10),
        ]
    )
    result, _ = _run(chat, executor=rejecting_executor)
    assert result == {1: "x", 2: "y"}
    tool_msg = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "forbidden" in json.loads(tool_msg["content"])["error"]


def test_loop_tool_crash_reported_not_fatal():
    def crashing_executor(name, arguments):
        raise RuntimeError("boom")

    chat = ScriptedChat(
        [
            (_Message(tool_calls=[read_call("a")]), 10),
            (_Message(tool_calls=[submit_call("b", {"1": "x", "2": "y"})]), 10),
        ]
    )
    result, _ = _run(chat, executor=crashing_executor)
    assert result == {1: "x", 2: "y"}
    tool_msg = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "boom" in json.loads(tool_msg["content"])["error"]


def test_loop_cancel_raises_immediately():
    chat = ScriptedChat([])
    try:
        run_agent_task(
            chat,
            lambda name, args: {},
            ("a",),
            system_message="s",
            user_message="u",
            tools_openai=[],
            submit_tool_openai={},
            cancel_check=lambda: True,
        )
        raised = False
    except AgentTaskCancelled:
        raised = True
    assert raised
    assert chat.calls == []


# ── validator ────────────────────────────────────────────────────────


def test_validator_accepts_dict_shape_and_cleans():
    accepted, feedback, warnings, newly_warned, _rejected = validate_submission(
        {"translations": {"1": "  你好\r\n世界 ", 2: "ok"}}, frozenset({1, 2})
    )
    assert accepted == {1: "你好\n世界", 2: "ok"}
    assert feedback is None
    assert warnings == []
    assert newly_warned == []


def test_validator_accepts_list_shape():
    accepted, _, _, _, _ = validate_submission(
        {"translations": [{"id": 1, "translation": "一"}, {"id": 2, "translation": "二"}]},
        frozenset({1, 2}),
    )
    assert accepted == {1: "一", 2: "二"}


def test_validator_rejects_out_of_task_ids():
    accepted, feedback, _, _, _ = validate_submission(
        {"translations": {"1": "好", "99": "越界"}}, frozenset({1, 2})
    )
    assert accepted == {1: "好"}  # 部分接受
    assert "99" in feedback


def test_validator_rejects_empty_values():
    accepted, feedback, _, _, _ = validate_submission(
        {"translations": {"1": "好", "2": "   "}}, frozenset({1, 2})
    )
    assert accepted == {1: "好"}
    assert "2" in feedback


def test_validator_full_reject_when_nothing_usable():
    accepted, feedback, _, _, _ = validate_submission(
        {"translations": {"77": "x"}}, frozenset({1, 2})
    )
    assert accepted is None
    assert "rejected" in feedback


def test_validator_bad_shape():
    accepted, feedback, _, _, _ = validate_submission("nonsense", frozenset({1}))
    assert accepted is None
    assert feedback
    accepted, feedback, _, _, _ = validate_submission({"translations": 42}, frozenset({1}))
    assert accepted is None
    assert feedback


# ── validator: 先警告后打回(译文=原文 / 术语残留) ──────────────────────


def test_validator_same_as_source_warns_then_rejects():
    args = {"translations": {"1": "フフ", "2": "好"}}
    src_map = {1: "フフ", 2: "悪い"}
    # 首次:警告并接受
    accepted, feedback, warnings, new_warned, _ = validate_submission(
        args, frozenset({1, 2}), source_map=src_map
    )
    assert accepted == {1: "フフ", 2: "好"}
    assert feedback is None
    assert "equal their source" in warnings[0]
    assert new_warned == [1]
    # 再犯:打回
    accepted, feedback, warnings, new_warned, _ = validate_submission(
        args, frozenset({1, 2}), source_map=src_map, warned_ids=frozenset({1})
    )
    assert accepted == {2: "好"}
    assert "still equal" in feedback


def test_validator_same_as_source_skips_digits_and_short():
    args = {"translations": {"1": "42", "2": "X", "3": "フフ"}}
    src_map = {1: "42", 2: "X", 3: "フフ"}
    accepted, feedback, warnings, _, _ = validate_submission(
        args, frozenset({1, 2, 3}), source_map=src_map
    )
    # 纯数字/单字符豁免;双字符日文判为偷懒并警告
    assert accepted == {1: "42", 2: "X", 3: "フフ"}
    assert feedback is None
    assert any("3" in w for w in warnings)


def test_validator_term_residue_warns_then_rejects():
    args = {"translations": {"1": "路飞是海贼", "2": "ルフィは海賊だ"}}
    src_map = {1: "ルフィは海賊だ", 2: "ルフィは海賊だ"}
    terms = [("ルフィ", "路飞"), ("海賊", "海贼")]
    # 首次:警告并接受
    accepted, feedback, warnings, new_warned, _ = validate_submission(
        args, frozenset({1, 2}), source_map=src_map, glossary_terms=terms
    )
    assert accepted == {1: "路飞是海贼", 2: "ルフィは海賊だ"}
    assert feedback is None
    assert new_warned == [2]
    assert any("Glossary source terms" in w for w in warnings)
    # 再犯:打回
    accepted, feedback, _, _, _ = validate_submission(
        args,
        frozenset({1, 2}),
        source_map=src_map,
        glossary_terms=terms,
        warned_ids=frozenset({2}),
    )
    assert accepted == {1: "路飞是海贼"}
    assert "Glossary source terms" in feedback


def test_validator_residue_ignores_identity_terms():
    # 恒等映射(src==dst)不判残留;译文与原文不同,不触发译文=原文
    args = {"translations": {"1": "ボールはとても丸い"}}
    src_map = {1: "ボールは丸い"}
    terms = [("ボール", "ボール")]
    accepted, feedback, warnings, _, _ = validate_submission(
        args, frozenset({1}), source_map=src_map, glossary_terms=terms
    )
    assert accepted == {1: "ボールはとても丸い"}
    assert feedback is None
    assert warnings == []


def test_clean_translation():
    assert clean_translation("  a\r\nb  ") == "a\nb"
    assert clean_translation("c\rd") == "c\nd"
    assert clean_translation(None) == "None"  # 非 str 强转,由空值检测兜住


# ── prompts ──────────────────────────────────────────────────────────


def _mkblk(src, trans):
    blk = SimpleNamespace(text=src, translation=trans, src_is_vertical=False)
    blk.get_text = lambda s=src: s
    return blk


def _mkproj(pages):
    return SimpleNamespace(pages=pages)


def test_history_snippet_eligibility_and_order():
    pages = {
        "p1": [_mkblk("one", "一")],              # 完成 → 注入
        "p2": [_mkblk("two", "")],                # 未译完 → 排除
        "p3": [],                                 # 空页 → 排除
        "p4": [_mkblk("four", "四")],             # 完成 → 注入
        "cur": [_mkblk("now", "")],
    }
    snippet = build_history_snippet(_mkproj(pages), "cur", 4096)
    assert "p1" in snippet and "p4" in snippet
    assert "p2" not in snippet
    # 阅读顺序:p1 在 p4 之前
    assert snippet.index("p1") < snippet.index("p4")
    assert "- \"four\" -> \"四\"" in snippet


def test_history_snippet_budget_trims_oldest():
    pages = {
        "old": [_mkblk("a" * 40, "一" * 40)],
        "new": [_mkblk("b", "二")],
        "cur": [_mkblk("now", "")],
    }
    from modules.context.token_usage import fallback_token_count

    def _cost(name, src, trans):
        return fallback_token_count(f'Page "{name}":\n- "{src}" -> "{trans}"') + 24

    budget = _cost("new", "b", "二") + 1  # 只装得下最近一页
    snippet = build_history_snippet(_mkproj(pages), "cur", budget)
    assert "new" in snippet
    assert "old" not in snippet  # 最旧页超预算被裁


def test_history_snippet_no_context():
    assert build_history_snippet(None, "cur", 4096) == ""
    assert build_history_snippet(_mkproj({"cur": []}), None, 4096) == ""
    # 当前页不在项目里(如单框的 page_key 缺失)→ 无注入
    assert build_history_snippet(_mkproj({"a": [_mkblk("x", "y")]}), "zzz", 4096) == ""


def test_history_snippet_page_cap():
    # 5 个已完成前页:默认只注入最近的 3 页(邻近语义,防短块页把预算吃满)
    pages = {f"p{i}": [_mkblk("x", "一")] for i in range(5)}
    pages["cur"] = [_mkblk("now", "")]
    snippet = build_history_snippet(_mkproj(pages), "cur", 4096)
    assert snippet.count('Page "p') == 3
    assert 'Page "p4"' in snippet and 'Page "p2"' in snippet
    assert 'Page "p1"' not in snippet and 'Page "p0"' not in snippet
    # 显式放宽页数上限时全部装入
    snippet2 = build_history_snippet(_mkproj(pages), "cur", 4096, max_pages=5)
    assert snippet2.count('Page "p') == 5


def test_page_label():
    pages = {"a": [], "b": []}
    assert page_label(_mkproj(pages), "b") == '"b" (2/2)'
    assert page_label(_mkproj(pages), None) == ""
    assert page_label(_mkproj(pages), "zzz") == '"zzz"'


def test_system_message_contract():
    msg = build_system_message("Japanese", "Simplified Chinese", has_exploration=True)
    assert "submit_translations" in msg
    assert "untrusted comic content" in msg
    assert "Explore only when needed" in msg
    msg2 = build_system_message("Auto", "English", profile_prompt="keep it punchy")
    assert "keep it punchy" in msg2
    assert "No exploration tools" in msg2


def test_user_task_message():
    msg = build_user_task_message(["a", "b"], '"p1" (1/2)', "history...", ())
    assert json.dumps({"id": 1, "text": "a"}, ensure_ascii=False) in msg
    assert '"p1" (1/2)' in msg
    assert "history..." in msg


# ── tools surface ────────────────────────────────────────────────────


def test_available_tool_defs_narrow_without_resources():
    full = available_tool_defs(object(), ("e",))
    names = [d["name"] for d in full]
    assert set(names) == {"list_pages", "read_pages", "search_blocks", "get_page_info", GLOSSARY_TOOL_NAME, SUBMIT_TOOL_NAME}
    bare = [d["name"] for d in available_tool_defs(None, ())]
    assert bare == [SUBMIT_TOOL_NAME]


def test_execute_agent_tool_rejects_write_tools():
    try:
        execute_agent_tool("set_font", {"ids": "0:0"}, project=object())
        raised = False
    except ToolError as e:
        raised = True
        assert "forbidden" in str(e)
    assert raised


def test_execute_agent_tool_requires_project():
    try:
        execute_agent_tool("list_pages", {})
        raised = False
    except ToolError as e:
        raised = True
        assert "not available" in str(e)
    assert raised


def test_execute_search_glossary():
    entries = [
        SimpleNamespace(source="ルフィ", translation="路飞", note=""),
        SimpleNamespace(source="ナミ", translation="娜美", note="navigator"),
    ]
    result = execute_agent_tool(GLOSSARY_TOOL_NAME, {"query": "路飞"}, glossary_entries=entries)
    assert result["n_results"] == 1
    assert result["entries"][0]["source"] == "ルフィ"
    result = execute_agent_tool(GLOSSARY_TOOL_NAME, {"query": "nav"}, glossary_entries=entries)
    assert result["entries"][0]["source"] == "ナミ"
    try:
        execute_agent_tool(GLOSSARY_TOOL_NAME, {"query": " "}, glossary_entries=entries)
        raised = False
    except ToolError:
        raised = True
    assert raised


def test_cap_tool_result():
    small = {"type": "detail", "data": "x"}
    assert cap_tool_result(small) is small
    big = {"type": "detail", "data": "y" * 60000}
    capped = cap_tool_result(big, char_cap=1000)
    assert capped["truncated"] is True
    assert "Narrow your query" in capped["error"]


def test_read_pages_span_guard():
    pages = {f"p{i}": [] for i in range(10)}
    proj = _mkproj(pages)
    # 直接调 execute_tool 走不到守卫前的拦截:execute_agent_tool 里先拦
    try:
        execute_agent_tool("read_pages", {"start": 0, "end": 9}, project=proj)
        raised = False
    except ToolError as e:
        raised = True
        assert "5 pages" in str(e)
    assert raised


def test_read_pages_passthrough_whitelist():
    # 真项目走 build_detail:fields_whitelist 只含 src/trans/v
    pages = {"p0": [_mkblk("hello", "")]}
    proj = _mkproj(pages)
    proj.idx2pagename = lambda i: list(pages.keys())[i]
    proj.proj_name = lambda: "test"
    proj._image_info = {"p0": {"width": 100, "height": 200}}
    result = execute_agent_tool("read_pages", {"start": 0, "end": 0}, project=proj)
    assert result["type"] == "detail"
    block = result["pages"][0]["blocks"][0]
    assert block["src"] == "hello"
    assert "ff" not in block  # 字体字段被白名单裁掉


def test_page_context_snippet_renders_other_blocks():
    pages = {
        "p1": [_mkblk("one", "一")],
        "cur": [_mkblk("task", ""), _mkblk("side", "旁白")],
    }
    snippet = build_page_context_snippet(_mkproj(pages), "cur", exclude=("task",))
    assert "Other text blocks on the current page" in snippet
    assert '"side"' in snippet and '"旁白"' in snippet
    assert "task" not in snippet  # 任务块自身排除


def test_page_context_snippet_keeps_untranslated_src_only():
    pages = {"cur": [_mkblk("task", ""), _mkblk("todo", "")]}
    snippet = build_page_context_snippet(_mkproj(pages), "cur", exclude=("task",))
    assert '"todo"' in snippet
    assert "->" not in snippet


def test_page_context_snippet_empty_for_no_others():
    pages = {"cur": [_mkblk("alone", "")]}
    assert build_page_context_snippet(_mkproj(pages), "cur", exclude=("alone",)) == ""
    assert build_page_context_snippet(_mkproj(pages), "missing", exclude=("x",)) == ""
    assert build_page_context_snippet(None, "cur") == ""
    assert build_page_context_snippet(_mkproj(pages), None) == ""


def test_page_context_snippet_accepts_untyped_exclude():
    # 调用方传的是任务 src 列表(可能含空串/非 str),不应炸
    pages = {"cur": [_mkblk("a", ""), _mkblk("b", "乙")]}
    snippet = build_page_context_snippet(
        _mkproj(pages), "cur", exclude=("a", "", None)
    )
    assert '"b"' in snippet and '"a"' not in snippet


# ── standalone runner ────────────────────────────────────────────────


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failures else 0)
