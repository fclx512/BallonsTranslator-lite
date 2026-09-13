"""框级 AI 动作执行器（批次 C）：worker 线程 + 取消事件 + 信号回主线程。

回调寻址原则（规划 §8.9）：信号携带 (pagename, blk_idx)，不持块引用——
处理期间用户可能切页，块对象会重建。

**载荷由主线程组装**（2026-09-13 重构）：原文、邻近块、裁图都在画布侧
（数据一致性修复之后）算完再交进来，worker 不再读 ``proj.pages``——
合并/撤销只改视觉层，数据层要等同步才一致，工位上读会拿到半成品
（实测：合并块重译只返回第一个子块）。
"""

import threading

from qtpy.QtCore import QObject, Signal

from utils import block_actions
from utils.logger import logger as LOGGER


class BlockActionRunner(QObject):
    started = Signal(str, str, int)  # action_id, pagename, blk_idx
    finished = Signal(str, str, int, str)  # action_id, pagename, blk_idx, result
    failed = Signal(str, str, int, str)  # action_id, pagename, blk_idx, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread = None
        self._cancel_event: threading.Event = None
        self._busy_action: str = ""
        self._retranslator = None

    @property
    def busy_action(self) -> str:
        return self._busy_action

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_ocr_fix(
        self, pagename: str, blk_idx: int, profile: dict, messages: list
    ) -> None:
        """疑难 OCR 校正：vision 单轮（阻塞在 worker 线程）。

        ``messages`` 是 ``utils/block_actions.py::build_ocr_fix_payload``
        的产物（多模态、图像段已内嵌），主线程组装。
        """
        if self.is_busy():
            return
        action_id = "act_ocr_fix"
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._busy_action = action_id

        def _work():
            me = self._thread
            try:
                result = block_actions.run_vision_ocr_fix(
                    profile, messages, cancel_event
                )
                self.finished.emit(action_id, pagename, blk_idx, result)
            except block_actions.CancelledError:
                LOGGER.info("Block action cancelled: %s", action_id)
            except Exception as e:
                if not cancel_event.is_set():
                    self.failed.emit(
                        action_id, pagename, blk_idx, f"{type(e).__name__}: {e}"
                    )
            finally:
                # 只清自己：cancel() 可能在在途请求期间已释放槽位并换了新动作
                if self._thread is me:
                    self._thread = None
                    self._busy_action = ""

        self._thread = threading.Thread(target=_work, daemon=True)
        self.started.emit(action_id, pagename, blk_idx)
        self._thread.start()

    def start_retranslate(
        self,
        translator,
        proj,
        pagename: str,
        blk_idx: int,
        src_text: str,
        tag_instructions=None,
        hint: str = "",
    ) -> None:
        """单块重译：AgentTranslator.translate_with_context（复用其
        profile/重试/RPM 与单框 context 模式；取消走 request_agent_stop）。

        ``src_text`` 由调用方在数据一致性修复之后读出（合并块即完整合并
        文本），worker 不自行寻址块。
        """
        if self.is_busy():
            return
        action_id = "act_retranslate"
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._busy_action = action_id
        self._retranslator = translator

        def _work():
            me = self._thread
            try:
                result = translator.translate_with_context(
                    src_text,
                    project=proj,
                    page_key=pagename,
                    tag_instructions=tag_instructions,
                    hint=hint,
                )
                if cancel_event.is_set():
                    raise block_actions.CancelledError()
                self.finished.emit(action_id, pagename, blk_idx, result or "")
            except block_actions.CancelledError:
                LOGGER.info("Block action cancelled: %s", action_id)
            except Exception as e:
                if not cancel_event.is_set():
                    self.failed.emit(
                        action_id, pagename, blk_idx, f"{type(e).__name__}: {e}"
                    )
            finally:
                if self._thread is me:
                    self._thread = None
                    self._busy_action = ""

        self._thread = threading.Thread(target=_work, daemon=True)
        self.started.emit(action_id, pagename, blk_idx)
        self._thread.start()

    def cancel(self) -> None:
        """请求停止：vision 调用丢弃结果；重译同时转发给 agent loop。

        在途 HTTP 请求无法中断，但槽位立即释放——否则强切页后旧请求
        占死 is_busy()，该动作在请求超时前无法再次发起（实测教训）。
        """
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._busy_action == "act_retranslate":
            self._retranslator.request_agent_stop()
        self._thread = None
        self._busy_action = ""
        self._retranslator = None
