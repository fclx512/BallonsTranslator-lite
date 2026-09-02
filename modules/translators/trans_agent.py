"""LLM_Agent_Translator:agent 式 LLM 翻译器(设计方案 §3/§4)。

懒加载注册表用 AST 扫描 modules/translators/ 顶层的 trans_*.py,要求
本文件自身含 @register_translator 装饰的类定义且 params 可静态求值,故
类与 params 直接落在此处;loop/tools/prompts/validator 逻辑在
modules/translators/agent/ 包内。继承 LLM_API_Translator 复用 profile
体系、client 构造、多 key 轮换、RPM 限流与重试语义;translate() 改走
agent loop(原生 function calling),任何失败降级到父类直译路径(§10)。
"""

import time
from typing import Dict, List, Optional

import httpx
import openai

from modules.context.errors import is_context_length_error, provider_error_message
from modules.context.glossary import load_glossary
from modules.context_agent.story import project_synopsis
from modules.translators.base import register_translator
from modules.translators.trans_llm_api import LLM_API_Translator
from modules.translators.agent.loop import (
    AgentTaskCancelled,
    AgentUnsupportedTools,
    run_agent_task,
)
from modules.translators.agent.prompts import (
    build_history_snippet,
    build_page_context_snippet,
    build_system_message,
    build_user_task_message,
    effective_history_budget,
    page_label,
    select_matched_glossary,
)
from modules.translators.agent.tools import (
    build_openai_tools,
    execute_agent_tool,
    submit_tool_def,
)
from utils.ai_tools import to_openai_tools
from utils.config import SingleBlkTranslateMode, pcfg
from utils.io_utils import text_is_empty


@register_translator("LLM_Agent_Translator")
class AgentTranslator(LLM_API_Translator):
    """LLM 翻译的 agent 形态:只读工具 + 多轮循环 + 唯一提交出口。"""

    concate_text = False

    params = {
        "active_profile": {
            "type": "selector",
            "options": [],
            "value": "",
            "description": "Quickly switch between saved profiles",
        },
        "max_requests_per_minute": {
            "value": 20,
            "description": "Max requests per minute per API key",
        },
        "delay": {
            "value": 0.3,
            "description": "Delay between requests (seconds)",
        },
        "retry_attempts": {
            "value": 3,
            "description": "Retry count on API connection failure",
        },
        "retry_timeout": {
            "value": 15,
            "description": "Retry wait time (seconds)",
        },
        "invalid_repeat_count": {
            "value": 2,
            "description": "Retry count on translation count mismatch",
        },
        "proxy": {
            "value": "",
            "description": "Proxy address (e.g. http(s)://user:password@host:port)",
        },
        "agent_max_turns": {
            "value": 8,
            "description": "Max model turns per page agent task",
        },
        "agent_token_budget": {
            "value": 0,
            "description": "Max total tokens per page agent task (0 = unlimited)",
        },
    }

    def _setup_translator(self):
        super()._setup_translator()
        self._agent_cancel = False
        self._status_cb = None

    def set_status_callback(self, cb):
        """注入每轮状态回调(page_key, turn, tool_names),由管线连到 UI(阶段 5 F 类)。"""
        self._status_cb = cb

    def request_agent_stop(self):
        """取消信号:编排层请求停止当前 agent 任务(loop 每轮检查)。"""
        self._agent_cancel = True

    # --- 翻译入口(覆写:agent loop + 回退链) ---

    def translate(
        self,
        text,
        *,
        project=None,
        page_key: Optional[str] = None,
        commit_history_window: bool = False,
    ):
        if text_is_empty(text):
            return text
        if not self.all_model_loaded():
            self.load_model()

        is_list = isinstance(text, list)
        src_list = text if is_list else [text]

        # 单框任务策略(设计 §9):plain = 单条直译,不注入页面上下文
        single_block = len(src_list) == 1
        if (
            single_block
            and pcfg.module.single_blk_translate_mode
            == SingleBlkTranslateMode.Plain
        ):
            return super().translate(text, project=None, page_key=None)

        translations: Dict[int, str] = {}
        self._agent_cancel = False

        try:
            translations = self._run_agent_task(
                src_list,
                project=project,
                page_key=page_key,
                block_mode=single_block,
            )
        except AgentTaskCancelled:
            raise
        except AgentUnsupportedTools as e:
            self.logger.warning(
                "Endpoint rejected function calling, falling back to "
                f"direct translation: {e}"
            )
        except Exception as e:
            self.logger.warning(
                f"Agent loop failed ({type(e).__name__}: {e}); falling "
                "back to direct translation."
            )

        missing_ids = [i for i in range(1, len(src_list) + 1) if i not in translations]
        if missing_ids:
            missing_texts = [src_list[i - 1] for i in missing_ids]
            try:
                backfill = super().translate(missing_texts)
                if not isinstance(backfill, list):
                    backfill = [backfill]
                for i, translated in zip(missing_ids, backfill):
                    translations[i] = translated if translated is not None else ""
            except AgentTaskCancelled:
                raise
            except Exception:
                if not translations:
                    raise
                # 部分成功:缺的块显式置空并报错,不静默(调研 §11 #11 教训)
                self.logger.error(
                    f"Direct backfill failed for {len(missing_ids)} block(s); "
                    "leaving their translations empty."
                )
                for i in missing_ids:
                    translations[i] = ""

        result = [translations[i] for i in range(1, len(src_list) + 1)]
        return result if is_list else result[0]

    # --- agent 任务组装 ---

    def _run_agent_task(
        self,
        src_list: List[str],
        *,
        project=None,
        page_key: Optional[str] = None,
        block_mode: bool = False,
    ) -> Dict[int, str]:
        api_key = self._select_api_key()
        if not api_key:
            raise ConnectionError(
                "No available API key. Check the active profile's api_key field."
            )
        if not self.client or self.client.api_key != api_key:
            if not self._initialize_client(api_key):
                raise ConnectionError("Failed to initialize API client.")
        model = self._effective_model
        if not model:
            raise RuntimeError("No model configured in the active profile.")

        glossary_entries = ()
        glossary_path = pcfg.module.llm_glossary_path
        if glossary_path:
            try:
                glossary_entries = load_glossary(glossary_path)
            except Exception as e:
                self.logger.warning(
                    f"Failed to load glossary '{glossary_path}': {e}"
                )
        matched_glossary = select_matched_glossary(glossary_entries, src_list)

        # 剧情注入(工作台阶段 3):全局梗概进 system 稳定前缀,强制注入项
        # 先于可选历史页占预算(上游注入预算优先级);开关关闭或无数据零开销。
        synopsis = ""
        if project is not None and pcfg.module.llm_story_context:
            synopsis = project_synopsis(project)

        from_lang = self._translated_lang(self.lang_source)
        to_lang = self._translated_lang(self.lang_target)
        system_message = build_system_message(
            from_lang,
            to_lang,
            profile_prompt=self.system_prompt_override,
            has_exploration=project is not None,
            synopsis=synopsis,
        )
        # 历史注入开关(设计方案 §12:page/history 枚举简化为 bool,默认注入前页)
        history = ""
        if pcfg.module.llm_translate_context:
            history = build_history_snippet(
                project,
                page_key,
                effective_history_budget(
                    pcfg.module.llm_prior_context_token_budget, synopsis
                ),
            )
        if block_mode and project is not None and page_key:
            page_ctx = build_page_context_snippet(project, page_key, exclude=src_list)
            if page_ctx:
                history = "\n\n".join(s for s in (history, page_ctx) if s)
        user_message = build_user_task_message(
            src_list, page_label(project, page_key), history, matched_glossary
        )

        # agent 模式下历史注入是模块固有行为(设计方案 §11),不再走旧 beta 编排
        tools_openai = build_openai_tools(project, glossary_entries)
        submit_openai = to_openai_tools([submit_tool_def()])[0]

        if block_mode:
            # 单框 context 模式:轻量 agent,最多 2 轮(设计 §4.4)
            max_turns = 2
        else:
            max_turns = max(1, int(self.get_param_value("agent_max_turns") or 8))
        try:
            token_budget = int(self.get_param_value("agent_token_budget") or 0)
        except (TypeError, ValueError):
            token_budget = 0

        def _execute(name: str, arguments: Dict) -> Dict:
            return execute_agent_tool(
                name,
                arguments,
                project=project,
                glossary_entries=glossary_entries,
            )

        def _status(turn: int, tool_names, usage) -> None:
            """每轮状态:转发 UI 回调 + 按开关写 debug 日志(阶段 5 F 类)。"""
            if self._status_cb:
                self._status_cb(page_key, turn, tool_names)
            if pcfg.module.agent_translation_debug_log:
                from utils.debug_log import debug_logger

                debug_logger.start()
                debug_logger.write(
                    f"[agent] page={page_key} turn={turn} "
                    f"tools={tool_names or 'text only'} tokens={usage}"
                )

        return run_agent_task(
            self._agent_chat,
            _execute,
            src_list,
            system_message=system_message,
            user_message=user_message,
            tools_openai=tools_openai,
            submit_tool_openai=submit_openai,
            max_turns=max_turns,
            token_budget=token_budget,
            cancel_check=lambda: self._agent_cancel,
            status_cb=_status,
            source_map={i + 1: str(src) for i, src in enumerate(src_list)},
            glossary_terms=[
                (e.source, e.translation) for e in matched_glossary
            ],
            log=self.logger.debug,
        )

    # --- agent 轮次请求 ---

    def _agent_chat(self, messages, tools_spec, tool_choice, on_delta=None):
        """一轮 agent 请求:与 _request_translation 同构,但带 tools/tool_choice、不带 response_format。

        连接类错误沿用父类重试语义(retry_attempts × retry_timeout);
        400 且报错指向 tools/function 视为端点不支持 → AgentUnsupportedTools
        (预期内降级,不烧重试)。返回 (message, usage_total)。
        on_delta 非 None 时走流式,content 增量实时回调(UI 逐字刷新),
        聚合出与非流式同构的 message;端点拒绝 stream 参数时自动降级
        非流式重发一次。
        """
        retry_attempt = 0
        while True:
            self._respect_delay()
            profile = self._active_profile
            api_args = {
                "model": self._effective_model,
                "messages": messages,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "tools": tools_spec,
                "tool_choice": tool_choice,
            }
            if self.max_tokens is not None:
                api_args["max_tokens"] = self.max_tokens
            if on_delta is not None:
                api_args["stream"] = True
                api_args["stream_options"] = {"include_usage": True}
            reasoning_effort = profile.get("reasoning_effort", "")
            if reasoning_effort:
                from utils.reasoning_params import build_reasoning_kwargs

                api_args.update(
                    build_reasoning_kwargs(
                        api_host=profile.get("api_host", ""),
                        effort=reasoning_effort,
                        model=self._effective_model,
                    )
                )
            frequency_penalty = profile.get("frequency_penalty")
            if frequency_penalty:
                api_args["frequency_penalty"] = float(frequency_penalty)
            presence_penalty = profile.get("presence_penalty")
            if presence_penalty:
                api_args["presence_penalty"] = float(presence_penalty)

            try:
                response = self.client.chat.completions.create(**api_args)
            except openai.BadRequestError as e:
                # 注意:BadRequestError 是 APIStatusError 子类,必须最先接
                message_text = provider_error_message(e)
                if on_delta is not None and "stream" in message_text.lower():
                    self.logger.warning(
                        "Endpoint rejected streaming; retrying non-streamed."
                    )
                    on_delta = None
                    continue
                if is_context_length_error(e):
                    raise RuntimeError(
                        f"Context length exceeded in agent loop: {message_text}"
                    ) from e
                lowered = message_text.lower()
                if "tool" in lowered or "function" in lowered:
                    raise AgentUnsupportedTools(message_text) from e
                raise RuntimeError(message_text) from e
            except openai.AuthenticationError as e:
                from modules.exceptions import LLMApiKeyRequiredError

                raise LLMApiKeyRequiredError(
                    profile.get("name", ""), profile.get("name", "")
                ) from e
            except (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
                openai.APIStatusError,
                httpx.RequestError,
            ) as e:
                retry_attempt += 1
                self.logger.warning(
                    f"API Error ({type(e).__name__}): {e}. "
                    f"Attempt {retry_attempt}/{self.retry_attempts}."
                )
                if retry_attempt >= self.retry_attempts:
                    raise
                time.sleep(self.retry_timeout)
                continue
            except Exception as e:
                self.logger.error(f"API request failed: {e}")
                raise

            if on_delta is not None:
                return self._consume_agent_stream(response, on_delta)

            usage_total = None
            if response.usage:
                usage_total = response.usage.total_tokens
                self.token_count += response.usage.total_tokens
                self.token_count_last = response.usage.total_tokens
            message = (
                response.choices[0].message
                if response.choices and response.choices[0].message
                else None
            )
            if message is None:
                raise RuntimeError("No message in API response.")
            return message, usage_total

    def _consume_agent_stream(self, stream, on_delta):
        """聚合流式响应为与非流式同构的 (message, usage_total)。

        content 增量经 on_delta 回调;tool_calls 分片按 index 拼装;
        usage 由 stream_options include_usage 的末块携带。
        """
        from types import SimpleNamespace

        content_parts = []
        tool_chunks: Dict[int, Dict[str, str]] = {}
        usage_total = None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage_total = chunk.usage.total_tokens
                self.token_count += chunk.usage.total_tokens
                self.token_count_last = chunk.usage.total_tokens
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                on_delta(delta.content)
            for tc in delta.tool_calls or []:
                idx = tc.index if tc.index is not None else 0
                entry = tool_chunks.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    entry["id"] = tc.id
                if tc.function is not None:
                    if tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        calls = [
            SimpleNamespace(
                id=entry["id"] or f"call_{i}",
                function=SimpleNamespace(
                    name=entry["name"], arguments=entry["arguments"]
                ),
            )
            for i, entry in sorted(tool_chunks.items())
        ]
        message = SimpleNamespace(
            content="".join(content_parts), tool_calls=calls or None
        )
        return message, usage_total
