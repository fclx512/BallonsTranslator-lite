"""Background stage tasks: frozen input, no project writes, main-thread settlement.

Recognition, translation and inpainting are separate tasks. Revision requests
produce proposals only. A page fingerprint also covers generation and image
files, so deleted/replaced pages and manual edits cannot receive stale results.
"""

import copy
import hashlib
import json
import os
import threading
import time

from qtpy.QtCore import QThread, Signal

from modules.translators.agent.loop import AgentTaskCancelled
from utils.config import RunStatus
from utils.font_size_estimate import apply_mask_font_sizes_from_detector
from utils.proj_imgtrans import TextBlkEncoder
from utils.textblock import sort_regions


def block_signature(block):
    return hashlib.sha256(json.dumps(
        block.to_dict(), cls=TextBlkEncoder, sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def page_signature(project, page):
    if page not in project.pages:
        return None
    files = []
    path = os.path.join(project.directory or "", page)
    try:
        stat = os.stat(path)
        files.append((path, stat.st_size, stat.st_mtime_ns))
    except (OSError, TypeError):
        files.append((path, None, None))
    for path in (project.get_mask_path(page, get_last_modified=True),
                 project.get_inpainted_path(page, get_last_modified=True)):
        try:
            with open(path, "rb") as image_file:
                digest = hashlib.sha256()
                for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                    digest.update(chunk)
                digest = digest.hexdigest()
        except (OSError, TypeError):
            digest = None
        files.append((path, digest))
    return (
        tuple(block_signature(block) for block in project.pages[page]),
        project.page_generation(page), project.page_image_generation(page),
        tuple(files),
    )


def freeze_project(project):
    snapshot = copy.copy(project)
    for key, value in project.__dict__.items():
        # Canvas image buffers are never used or changed by this worker.
        if key in {"img_array", "mask_array", "inpainted_array", "notext_array"}:
            setattr(snapshot, key, None)
        else:
            setattr(snapshot, key, copy.deepcopy(value))
    return snapshot


class PipelineRunner(QThread):
    activity = Signal(object)
    page_ready = Signal(object)
    def __init__(self, project, pages, mode, config, modules, *,
                 overwrite=False, targets=None, hint="", await_settlement=False, parent=None):
        super().__init__(parent)
        self.project = freeze_project(project)
        self.pages = list(pages)
        self.mode = mode
        self.config = copy.deepcopy(config)
        self.modules = modules
        self.overwrite = overwrite
        self.targets = targets or {}
        self.hint = hint
        self.await_settlement = await_settlement
        self._settled_page = threading.Event()
        self._accepted_blocks = None
        self.signatures = {page: page_signature(project, page) for page in pages}
        self.cancel_event = threading.Event()
        self.report = {"completed": [], "failed": {}, "skipped": [], "cancelled": False}
        self.current_stage = ""
        self.known_tokens = 0
        self.unknown_usage = 0
        self.total_blocks = sum(
            1 for page in self.pages for i, block in enumerate(self.project.pages[page])
            if block.get_text().strip() and
            ((mode == "revise" and i in self.targets.get(page, [])) or
             (mode == "translate" and (overwrite or not block.translation.strip())))
        )

    def cancel(self):
        self.cancel_event.set()
        translator = self.modules.get("translator")
        if translator is not None and hasattr(translator, "request_agent_stop"):
            translator.request_agent_stop()

    def acknowledge(self, blocks=None):
        self._accepted_blocks = copy.deepcopy(blocks)
        self._settled_page.set()

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise AgentTaskCancelled("Task cancelled")

    def _stage(self, page, stage, state, **extra):
        """报告某阶段的开始/结束。

        阶段进度只能到这种粒度：检测、OCR、修复的模型调用本身没有逐块回调
        （见 ``modules/ocr/base.py::run_ocr`` 等），所以 UI 拿到的是"这个阶段
        在跑""跑完了、得到 N 个块、花了多久"。翻译是唯一有轮次细节的阶段，
        走 ``_status``。
        """
        payload = {"page": page, "stage": stage, "state": state}
        payload.update(extra)
        self.current_stage = stage
        self.activity.emit(payload)

    def _status(self, page, turn, tools, usage=None, covered=None, total=None):
        if usage is None:
            self.unknown_usage += 1
        else:
            self.known_tokens += usage
        self.activity.emit({"page": page, "turn": turn, "tools": tools,
                            "tokens": self.known_tokens, "unknown": self.unknown_usage,
                            "covered": covered, "covered_total": total,
                            "request_mode": getattr(self.modules.get("translator"), "_task_request_mode", "profile")})

    def run(self):
        translator = self.modules.get("translator")
        old_callback = getattr(translator, "_status_cb", None)
        if translator is not None and hasattr(translator, "set_status_callback"):
            translator.set_status_callback(self._status)
        try:
            for number, page in enumerate(self.pages, 1):
                self._check_cancel()
                self.activity.emit({"page": page, "number": number,
                                    "total": len(self.pages), "waiting": True})
                try:
                    original_blocks = copy.deepcopy(self.project.pages[page])
                    self._accepted_blocks = None
                    result = self._process_page(page)
                    self._check_cancel()
                    if result is None:
                        self.report["skipped"].append(page)
                    else:
                        self._settled_page.clear()
                        self.page_ready.emit(copy.deepcopy(result))
                        if self.await_settlement:
                            while not self._settled_page.wait(0.1):
                                self._check_cancel()
                        self._check_cancel()
                        self.report["completed"].append(page)
                except AgentTaskCancelled:
                    raise
                except Exception as error:
                    self.report["failed"][page] = str(error)
                    self.activity.emit({"page": page, "error": str(error),
                                        "stage": self.current_stage})
                finally:
                    # Context always comes from the input snapshot, including
                    # pages whose result is still queued for UI acceptance.
                    self.project.pages[page] = self._accepted_blocks if self._accepted_blocks is not None else original_blocks
        except AgentTaskCancelled:
            self.report["cancelled"] = True
        except Exception as error:
            self.report["failed"]["task"] = str(error)
        finally:
            if translator is not None and hasattr(translator, "set_status_callback"):
                translator.set_status_callback(old_callback)

    def _process_page(self, page):
        blocks = self.project.pages[page]
        result = {"page": page, "blocks": blocks, "mask": None,
                  "inpainted": None, "bits": 0, "proposals": []}
        if self.mode == "recognize":
            image = self.project.read_img(page)
            if self.config.enable_detect:
                self._stage(page, "detect", "running")
                started = time.monotonic()
                mask, detected = self.modules["textdetector"].detect(image, self.project)
                self._check_cancel()
                if self.config.keep_exist_textlines:
                    import numpy as np
                    detected = sort_regions(blocks + detected)
                    old_mask = self.project.load_mask_by_imgname(page)
                    if old_mask is not None and mask is not None:
                        mask = np.bitwise_or(old_mask, mask)
                blocks = detected
                self.project.pages[page] = blocks
                result.update(blocks=blocks, mask=mask)
                result["bits"] |= RunStatus.FIN_DET
                self._stage(page, "detect", "done", total=len(blocks),
                            seconds=time.monotonic() - started)
            if self.config.enable_ocr:
                self._check_cancel()
                self._stage(page, "ocr", "running", total=len(blocks))
                started = time.monotonic()
                self.modules["ocr"].run_ocr(image, blocks)
                # 字形掩膜 -> 原文字号；无掩膜或检测器未声明能力时为空操作。
                apply_mask_font_sizes_from_detector(
                    blocks, result.get("mask"), self.modules.get("textdetector")
                )
                self._stage(page, "ocr", "done", total=len(blocks),
                            seconds=time.monotonic() - started)
                result["bits"] |= RunStatus.FIN_OCR
        elif self.mode in {"translate", "revise"}:
            indexes = self.targets.get(page, []) if self.mode == "revise" else range(len(blocks))
            indexes = [i for i in indexes if blocks[i].get_text().strip()
                       and (self.mode == "revise" or self.overwrite
                            or not blocks[i].translation.strip())]
            if not indexes:
                return None
            selected = [blocks[i] for i in indexes]
            before = [block_signature(block) for block in selected]
            old_texts = [block.translation for block in selected]
            translator = self.modules["translator"]
            self._stage(page, "translate", "running", total=len(selected))
            started = time.monotonic()
            hint = self.hint
            if self.mode == "revise":
                hint = "Revise only the requested task IDs. Current translations:\n" + "\n".join(
                    f"{i + 1}: {text}" for i, text in enumerate(old_texts)
                ) + "\nUser instructions: " + hint
            if hasattr(translator, "translate_selection_with_context"):
                texts = translator.translate_selection_with_context(
                    selected, project=self.project, page_key=page, hint=hint,
                    cancel_check=self.cancel_event.is_set,
                )
                if len(texts) != len(selected) or any(not text.strip() for text in texts):
                    raise RuntimeError("Translation did not cover every selected block")
                for block, text in zip(selected, texts):
                    block.translation = text
            else:
                for block in selected:
                    block.translation = ""
                translator.translate_textblk_lst(selected, project=self.project, page_key=page)
                if any(not block.translation.strip() for block in selected):
                    raise RuntimeError("Translation returned empty text")
            self._check_cancel()
            for index, signature, old, block in zip(indexes, before, old_texts, selected):
                if self.mode == "revise":
                    result["proposals"].append({
                        "page": page, "index": index, "signature": signature,
                        "source": block.get_text(), "before": old,
                        "after": block.translation,
                    })
                    block.translation = old
                else:
                    block.rich_text = ""
            result["count"] = len(selected)
            result["indexes"] = indexes
            result["bits"] = RunStatus.FIN_TRANSLATE
            self._stage(page, "translate", "done", total=len(selected),
                        seconds=time.monotonic() - started)
        elif self.mode == "inpaint":
            mask = self.project.load_mask_by_imgname(page)
            if mask is None:
                return None
            self._stage(page, "inpaint", "running")
            started = time.monotonic()
            result["inpainted"] = self.modules["inpainter"].inpaint(
                self.project.read_img(page), mask, blocks,
            )
            result["bits"] = RunStatus.FIN_INPAINT
            self._stage(page, "inpaint", "done", seconds=time.monotonic() - started)
        else:
            raise ValueError(self.mode)
        result["image_info"] = copy.deepcopy(self.project._image_info.get(page, {}))
        return result
