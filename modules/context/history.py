"""翻译请求上下文快照（阶段 4 起：历史窗口状态机已废弃，仅保留 RequestContext）。

历史窗口（HistoryWindow / eligible_history_for_request / recover_context_length
等）随翻译 agent 化重构删除——直译路径降为回退，前页历史注入由 agent loop 的
编排片段承担（docs/技术实现/翻译agent化_设计方案.md §11）。token 计数在
token_usage.py。
"""

from dataclasses import dataclass
from typing import Tuple

from .glossary import GlossaryEntry


@dataclass(frozen=True)
class RequestContext:
    """Immutable glossary snapshot used for provider retries.

    ``history`` is always empty since the agent rework; the field is kept for
    shape stability of the snapshot pattern.
    """

    history: Tuple[tuple, ...] = ()
    glossary: Tuple[GlossaryEntry, ...] = ()
    glossary_mode: str = ''
    history_budget: int = 0
    window_key: object = None
    request_page_key: str = None
    diagnostic: object = None