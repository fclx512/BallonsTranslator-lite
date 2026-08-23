"""agent loop 状态机(设计方案 §4)。

协议:原生 function calling。每轮模型要么调只读工具(结果进 tool 消息),
要么调 submit_translations(唯一结束路径);纯文本轮注入一次提醒,再犯
直接进入强制收敛;轮数/token 预算耗尽进入强制收敛轮——tools 只剩
submit_translations 且 tool_choice 锁定,模型被迫收卷而非失败。
"""

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from utils.ai_tools import ToolError

from .tools import SUBMIT_TOOL_NAME, cap_tool_result
from .validator import validate_submission


class AgentUnsupportedTools(Exception):
    """端点不支持 function calling(400 且报错指向 tools),应降级直译。"""


class AgentTaskCancelled(Exception):
    """任务被取消(cancel_check 返回 True)。"""


FORCED_TOOL_CHOICE = {"type": "function", "function": {"name": SUBMIT_TOOL_NAME}}


def _assistant_message(message) -> Dict[str, Any]:
    """把端点返回的 message 对象重构成可回传的消息 dict(只保留必要字段)。"""
    msg: Dict[str, Any] = {"role": "assistant", "content": message.content}
    calls = [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        for tc in (getattr(message, "tool_calls", None) or [])
    ]
    if calls:
        msg["tool_calls"] = calls
    return msg


def _append_tool_result(
    messages: List[Dict], tool_call_id: str, result: Dict[str, Any]
) -> None:
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, ensure_ascii=False, default=str),
        }
    )


def _handle_submission(
    translations: Dict[int, str],
    arguments,
    valid_ids: frozenset,
    *,
    source_map=None,
    glossary_terms=(),
    warned_ids,
) -> Tuple[Dict[str, Any], bool]:
    """校验并吸收一次提交。返回 (tool 结果, 是否收齐)。

    warned_ids 为可变集合:先警告后打回的累计状态,校验器在本轮新警告的
    id 会追加进来。
    """
    accepted, feedback, warnings, newly_warned, rejected_ids = validate_submission(
        arguments,
        valid_ids,
        source_map=source_map,
        glossary_terms=glossary_terms,
        warned_ids=frozenset(warned_ids),
    )
    if accepted is None:
        for rid in rejected_ids:
            # 整单被拒也要移除警告轮残留的旧条目(否则缺失检查误判已覆盖)
            translations.pop(rid, None)
        return {"error": feedback or "Invalid submission."}, False
    translations.update(accepted)
    warned_ids.update(newly_warned)
    for rid in rejected_ids:
        # 打回:把警告轮接受的旧条目移除,让该 id 回到 missing 重新请求
        translations.pop(rid, None)
    result: Dict[str, Any] = {"accepted": len(accepted)}
    if warnings:
        result["warnings"] = warnings
    if feedback:
        result["warning"] = feedback
    missing = sorted(valid_ids - set(translations))
    if missing:
        result["missing_ids"] = missing
        result["hint"] = (
            "These ids are still missing. Call submit_translations again "
            "with translations for them."
        )
        return result, False
    result["ok"] = True
    result["received"] = len(translations)
    return result, True


def run_agent_task(
    chat: Callable[..., Tuple[Any, Optional[int]]],
    execute_tool_fn: Callable[[str, Dict], Dict],
    src_list: Sequence[str],
    *,
    system_message: str,
    user_message: str,
    tools_openai: List[Dict],
    submit_tool_openai: Dict,
    max_turns: int = 8,
    token_budget: int = 0,
    cancel_check: Optional[Callable[[], bool]] = None,
    status_cb: Optional[Callable[[int, List[str], Optional[int]], None]] = None,
    source_map: Optional[Dict[int, str]] = None,
    glossary_terms: Sequence = (),
    log: Optional[Callable[[str], None]] = None,
) -> Dict[int, str]:
    """跑一个 agent 翻译任务,返回 {id: 译文}(可能部分完成,由调用方补漏)。

    chat(messages, tools, tool_choice) -> (message, usage_total);
    execute_tool_fn(name, arguments) -> dict(只读工具;抛 ToolError 回给模型);
    status_cb(turn, tool_names, usage_total) 每轮回调一次(不黑盒,供 UI/日志);
    source_map 为 {id: 原文}(译文=原文/术语残留校验用);glossary_terms 为
    [(src, dst)] 命中术语对。
    """

    def _log(msg: str) -> None:
        if log:
            log(msg)

    valid_ids = frozenset(range(1, len(src_list) + 1))
    translations: Dict[int, str] = {}
    warned_ids: set = set()
    if source_map is None:
        source_map = {i + 1: str(src) for i, src in enumerate(src_list)}
    messages: List[Dict] = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]
    turns = 0
    usage_total = 0
    idle_reminders = 0
    force = False
    forced_used = False

    while True:
        if cancel_check and cancel_check():
            raise AgentTaskCancelled(
                "Agent translation cancelled before completion."
            )

        missing = sorted(valid_ids - set(translations))
        if not missing:
            return translations

        if force and forced_used:
            _log(f"[agent] forced round done, ids still missing: {missing}")
            return translations

        budget_exceeded = token_budget > 0 and usage_total >= token_budget
        if turns >= max_turns or budget_exceeded or force:
            if not force:
                _log(
                    f"[agent] forcing convergence (turns={turns}, "
                    f"tokens={usage_total})"
                )
            force = True
            tools, tool_choice = [submit_tool_openai], FORCED_TOOL_CHOICE
        else:
            tools, tool_choice = tools_openai, "auto"

        message, usage = chat(messages, tools, tool_choice)
        turns += 1
        usage_total += usage or 0
        if force:
            forced_used = True

        tool_calls = list(getattr(message, "tool_calls", None) or [])
        call_names = [tc.function.name for tc in tool_calls]
        _log(f"[agent] turn {turns}: {call_names if call_names else 'text only'}")
        if status_cb:
            status_cb(turns, call_names, usage)

        if not tool_calls:
            if force:
                return translations
            if idle_reminders >= 1:
                # 提醒过一次仍在散文输出:不再纠缠,直接强制收敛
                force = True
                continue
            messages.append(
                {"role": "assistant", "content": message.content or ""}
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No tool was called. Deliver results only via the "
                        "submit_translations tool, covering every id from "
                        "the task."
                    ),
                }
            )
            idle_reminders += 1
            continue

        messages.append(_assistant_message(message))
        finished = False
        for tc in tool_calls:
            name = tc.function.name
            raw_args = tc.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                arguments = None

            if name == SUBMIT_TOOL_NAME:
                if arguments is None:
                    _append_tool_result(
                        messages,
                        tc.id,
                        {"error": "submit_translations arguments are not valid JSON."},
                    )
                    continue
                result, finished_now = _handle_submission(
                    translations,
                    arguments,
                    valid_ids,
                    source_map=source_map,
                    glossary_terms=glossary_terms,
                    warned_ids=warned_ids,
                )
                _append_tool_result(messages, tc.id, result)
                finished = finished or finished_now
                continue

            if arguments is None:
                _append_tool_result(
                    messages,
                    tc.id,
                    {"error": f"Arguments of tool '{name}' are not valid JSON."},
                )
                continue
            try:
                result = execute_tool_fn(name, arguments)
            except ToolError as e:
                result = {"error": str(e)}
            except Exception as e:
                # 工具执行异常不致命:回给模型自行调整
                _log(f"[agent] tool '{name}' crashed: {type(e).__name__}: {e}")
                result = {"error": f"Tool execution failed: {type(e).__name__}: {e}"}
            _append_tool_result(messages, tc.id, cap_tool_result(result))

        if finished:
            return translations
