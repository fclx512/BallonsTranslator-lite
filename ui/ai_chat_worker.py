"""
AiChatWorker — streaming LLM API worker thread for the AI chat panel.

A per-request QThread that calls an OpenAI-compatible chat completions
endpoint with stream=True and emits text deltas in real time.
"""

from __future__ import annotations

from typing import Any, Dict, List

import json
import logging
import openai
import httpx
from qtpy.QtCore import QThread, Signal

logger = logging.getLogger('ai_chat')


class AiChatWorker(QThread):
    """Streaming LLM call in a background thread.

    Create a new instance per request, connect signals, then call start().
    The thread exits naturally after stream_finished or error_occurred is emitted.
    """

    chunk_ready = Signal(str)
    stream_finished = Signal(str)
    error_occurred = Signal(str)
    token_count = Signal(int)

    def __init__(
        self,
        api_config: Dict[str, Any],
        messages: List[Dict[str, str]],
        parent=None,
    ):
        super().__init__(parent)
        self._api_config = api_config
        self._messages = messages
        self._cancelled = False
        self._tool_call_chunks: Dict[int, Dict[str, Any]] = {}

    def cancel(self):
        """Request graceful cancellation. The run() loop exits on the next chunk."""
        self._cancelled = True

    def run(self):
        api_host = self._api_config.get('api_host', '')
        api_key = self._api_config.get('api_key', '')
        model = self._api_config.get('model', 'gpt-4o')
        temperature = self._api_config.get('temperature', 0.1)
        max_tokens = self._api_config.get('max_tokens', 4096)
        proxy = self._api_config.get('proxy', '')

        http_client = None
        if proxy:
            try:
                http_client = httpx.Client(
                    mounts={
                        'http://': httpx.HTTPTransport(proxy=proxy),
                        'https://': httpx.HTTPTransport(proxy=proxy),
                    }
                )
            except Exception:
                pass

        client = openai.OpenAI(
            api_key=api_key or 'dummy-key',
            base_url=api_host,
            http_client=http_client,
        )

        full_text = ''
        msg_count = len(self._messages)
        logger.info("Worker start: model=%s messages=%d max_tokens=%d", model, msg_count, max_tokens)
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=self._messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={'include_usage': True},
            )
            for chunk in stream:
                if self._cancelled:
                    logger.info("Worker cancelled mid-stream (received %d chars)", len(full_text))
                    break
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                if delta.content:
                    full_text += delta.content
                    self.chunk_ready.emit(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in self._tool_call_chunks:
                            self._tool_call_chunks[idx] = {
                                'id': tc.id or '',
                                'type': tc.type or 'function',
                                'function': {'name': '', 'arguments': ''},
                            }
                        entry = self._tool_call_chunks[idx]
                        if tc.id:
                            entry['id'] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry['function']['name'] += tc.function.name
                            if tc.function.arguments:
                                entry['function']['arguments'] += tc.function.arguments
                if getattr(chunk, 'usage', None):
                    self.token_count.emit(chunk.usage.total_tokens)
        except openai.APIConnectionError as e:
            logger.error("APIConnectionError: %s", e)
            self.error_occurred.emit(f'连接失败：{e}')
            return
        except openai.RateLimitError:
            logger.warning("RateLimitError")
            self.error_occurred.emit('API 频率限制，请稍后重试')
            return
        except openai.APITimeoutError:
            logger.error("APITimeoutError")
            self.error_occurred.emit('请求超时，请检查网络或 API 地址')
            return
        except openai.AuthenticationError:
            logger.error("AuthenticationError")
            self.error_occurred.emit('API 密钥无效')
            return
        except openai.BadRequestError as e:
            logger.error("BadRequestError (400): %s", e)
            self.error_occurred.emit(f'请求参数错误 (400): {e}')
            return
        except openai.APIStatusError as e:
            logger.error("APIStatusError (%s): %s", e.status_code, e)
            self.error_occurred.emit(f'API 错误 ({e.status_code}): {e}')
            return
        except Exception as e:
            logger.exception("Unexpected worker error")
            self.error_occurred.emit(f'未知错误：{e}')
            return

        # Serialize accumulated tool calls into full_text so parse_tool_calls() can find them
        if self._tool_call_chunks:
            n_tools = len(self._tool_call_chunks)
            logger.info("Tool calls accumulated: %d tools", n_tools)
            tool_calls = []
            for idx in sorted(self._tool_call_chunks.keys()):
                tc = self._tool_call_chunks[idx]
                tool_calls.append({
                    'id': tc['id'],
                    'type': tc['type'],
                    'function': tc['function'],
                })
                logger.debug("Tool #%d: name=%s args_len=%d",
                             idx, tc['function']['name'], len(tc['function']['arguments']))
            tc_json = json.dumps({'tool_calls': tool_calls}, ensure_ascii=False)
            full_text = tc_json + '\n' + full_text

        logger.info("Worker finished: text_len=%d tool_calls=%d",
                     len(full_text) - (full_text.index('\n') + 1 if '\n' in full_text and self._tool_call_chunks else 0),
                     len(self._tool_call_chunks))
        self.stream_finished.emit(full_text)
