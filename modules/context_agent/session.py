"""会话式 agent 循环(与 translators/agent/loop.py 的收敛式分开)。

差异:纯文本回复是**合法的指令轮结束**,不存在强制收卷;护栏只软性生效
——轮数/token 预算耗尽时注入一次「请收尾」消息,再给一轮机会总结,
仍不停就直接停(不伪造结果)。

草稿即外置记忆:每次指令轮由调用方重建 messages(system 提示重新生成,
含当前梗概),轮内不再额外裁剪;轮间裁剪由 trim_session_messages 提供。
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from modules.translators.agent.tools import cap_tool_result
from utils.ai_tools import ToolError

STOP_REPLY = "reply"
STOP_MAX_TURNS = "max_turns"
STOP_TOKEN_BUDGET = "token_budget"
STOP_CANCELLED = "cancelled"

_WRAPUP_MESSAGE = (
    "You are running out of budget. Stop exploring and wrap up now: "
    "submit any pending patches, then reply with a brief summary of "
    "what you changed and what you left undone."
)

# 指令轮结束后保留在消息列表里的最近消息数(含本轮 assistant 收尾),
# 其余轮中 tool 消息由草稿承担记忆,可安全丢弃。
KEEP_LAST_MESSAGES = 6


@dataclass
class SessionResult:
    """一次指令轮的结果。reply 为最终纯文本(预算耗尽可能为空)。

    messages 为本轮完整消息列表(含 system),供调用方经
    trim_session_messages 裁剪后作为下一轮 history_tail 续接。
    """

    reply: str
    turns: int
    usage_total: int
    stopped_reason: str
    messages: List[Dict[str, Any]] = None


def _assistant_message(message) -> Dict[str, Any]:
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


def trim_session_messages(
    messages: List[Dict[str, Any]], keep_last: int = KEEP_LAST_MESSAGES
) -> List[Dict[str, Any]]:
    """指令轮间裁剪:保留 system(首条)与最近 keep_last 条,余者丢弃。

    被裁掉的轮中信息已沉淀进草稿(patch)或本就不需要保留(只读探索)。
    裁剪起点若落在 tool 回执上(其带 tool_calls 的 assistant 消息已被
    裁掉),继续前移到合法起点——否则端点会拒绝孤儿 tool 消息。
    """
    if len(messages) <= keep_last + 1:
        return messages
    head, tail = messages[0], messages[-keep_last:]
    while tail and tail[0]["role"] == "tool":
        tail = tail[1:]
    return [head, *tail]


def run_agent_session(
    chat: Callable[..., Tuple[Any, Optional[int]]],
    execute_tool_fn: Callable[[str, Dict], Dict],
    *,
    system_message: str,
    user_message: str,
    tools_openai: List[Dict],
    max_turns: int = 12,
    token_budget: int = 0,
    cancel_check: Optional[Callable[[], bool]] = None,
    status_cb: Optional[Callable[[int, List[str], Optional[int]], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    history_tail: Optional[List[Dict[str, Any]]] = None,
    stream_cb: Optional[Callable[[str], None]] = None,
) -> SessionResult:
    """跑一次指令轮,返回最终回复与用量。

    chat(messages, tools, tool_choice[, on_delta]) -> (message, usage_total);
    execute_tool_fn(name, arguments) -> dict;抛 ToolError 回给模型。
    工具执行异常不致命(上游失败语义),patch 冲突同理——都作为回执
    继续,由模型决定是否调整;纯文本回复即结束。
    history_tail 为上一指令轮裁剪后的消息(system 之后部分),续接在
    本轮 system 之后、新 user 指令之前。
    stream_cb 非 None 时请求流式,content 增量实时回调(UI 逐字刷新);
    端点不支持时由 chat 侧降级,此处无感。
    """

    def _log(msg: str) -> None:
        if log:
            log(msg)

    messages: List[Dict] = [{"role": "system", "content": system_message}]
    if history_tail:
        messages.extend(history_tail)
    messages.append({"role": "user", "content": user_message})
    turns = 0
    usage_total = 0
    wrapup_sent = False

    while True:
        if cancel_check and cancel_check():
            raise _Cancelled()

        budget_exceeded = token_budget > 0 and usage_total >= token_budget
        if turns >= max_turns or budget_exceeded:
            if wrapup_sent:
                reason = (
                    STOP_TOKEN_BUDGET if budget_exceeded else STOP_MAX_TURNS
                )
                _log(f"[context-agent] stopped: {reason}")
                return SessionResult(
                    "", turns, usage_total, reason, messages
                )
            _log(
                f"[context-agent] soft limit reached (turns={turns}, "
                f"tokens={usage_total}), requesting wrap-up"
            )
            messages.append({"role": "user", "content": _WRAPUP_MESSAGE})
            wrapup_sent = True

        if stream_cb is not None:
            message, usage = chat(
                messages, tools_openai, "auto", on_delta=stream_cb
            )
        else:
            message, usage = chat(messages, tools_openai, "auto")
        turns += 1
        usage_total += usage or 0

        tool_calls = list(getattr(message, "tool_calls", None) or [])
        call_names = [tc.function.name for tc in tool_calls]
        _log(f"[context-agent] turn {turns}: {call_names or 'reply'}")
        if status_cb:
            status_cb(turns, call_names, usage)

        if not tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            return SessionResult(
                message.content or "", turns, usage_total, STOP_REPLY, messages
            )

        messages.append(_assistant_message(message))
        for tc in tool_calls:
            name = tc.function.name
            raw_args = tc.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                arguments = None
            if arguments is None:
                result: Dict[str, Any] = {
                    "error": f"Arguments of tool '{name}' are not valid JSON."
                }
            else:
                try:
                    result = execute_tool_fn(name, arguments)
                except ToolError as e:
                    result = {"error": str(e)}
                except Exception as e:  # 单工具崩溃不作废整轮
                    _log(
                        f"[context-agent] tool '{name}' crashed: "
                        f"{type(e).__name__}: {e}"
                    )
                    result = {
                        "error": (
                            f"Tool execution failed: {type(e).__name__}: {e}"
                        )
                    }
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        cap_tool_result(result), ensure_ascii=False, default=str
                    ),
                }
            )


class _Cancelled(Exception):
    """cancel_check 返回 True 时抛出,由调用方(UI worker)转取消语义。"""
