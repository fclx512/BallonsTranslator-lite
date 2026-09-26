"""框级 AI 动作（批次 C）：注册表 + 上下文组装 + 单轮无工具 LLM 调用。

设计见 docs/技术实现/AI辅助功能_设计与实现.md 的「框级动作」：调用结构比 AgentTranslator
的收敛式 loop 更轻一级——无工具、单轮、代码预组装上下文、固定出口；
边界靠"根本没有工具"结构保证。AI 只出草稿，写回由用户在确认卡片
显式「应用」（人工在环总纲）。

**载荷在主线程、按前端观感组装**（2026-09-13 重构）：画布合并只改视觉层，
数据层要等 `ui/scenetext_manager.py::updateTextBlkList` 才重建，所以动作
一律先做数据一致性修复再读数据，并且把"送什么图、送什么文本"在调用线程
算完，worker 只负责发请求——避免工位上读到半成品数据（实测：合并块只
返回第一个子块的内容）。

程序侧的确定性信息优先于模型猜：
- OCR 校正送**按行透视纠正**的拼图（`TextBlock.get_transformed_region`，
  与 OCR 管线同一套裁剪），而不是整块轴对齐外接框；逐行附上现有 OCR 文本，
  回复按行对齐后用户可逐行取舍。
- OCR 置信度分数不进 prompt（噪声，设计 §4）；指示/疑点标签的语义
  以指令文本进 prompt。

**两个动作对任何单选块都开放**（交接 §4.1）：现场看到可疑就走「校对原文／
重译」，不必先挂标签；`BlockActionDef.consumes` 只声明「应用这一份草稿后，
哪几条问题记录算处理完了」。
"""

import base64
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import QCoreApplication

from .block_tags import (
    LOW_CONF_ID,
    OCR_REVIEW_ID,
    TRANS_REVIEW_ID,
)
from .textblock import TextBlock

# 拼图参数：行高归一（与 OCR 管线 collect_textblock_regions 的 text_height
# 同一思路）、行间灰缝、整图上限（避免超长块把请求撑爆）
_LINE_TARGET_H = 64
_LINE_MAX_UPSCALE = 4.0
_STITCH_GAP = 6
_STITCH_BG = 235
_STITCH_MAX_H = 1800
_STITCH_MAX_W = 2000


@dataclass(frozen=True)
class BlockActionDef:
    id: str
    name: str  # 已翻译显示名
    short_label: str  # 工具栏紧凑钮短标签（已翻译）
    kind: str  # "ocr_fix"（写原文，需 vision） / "retranslate"（写译文）
    consumes: Tuple[str, ...]  # 确认后清除的问题标签（含旧 ID，见下）
    needs_vision: bool


BLOCK_ACTIONS = [
    # 名称在字面量定义处显式标注翻译上下文（i18n 模块级翻译表规则）
    #
    # ``consumes`` ＝ 该动作「处理完了」的那几条问题（含旧 ID，好让老项目的
    # 旧标记也被一次应用清掉）。**持久翻译指示不在此列**：手写字／拟声词是块
    # 的属性，处理原文不消除它。这两个动作对**任何**单选块都开放（交接 §4.1：
    # 不需要预先挂标签即可校对／重译），``consumes`` 只决定应用后清哪几条记录。
    BlockActionDef(
        "act_ocr_fix",
        QCoreApplication.translate("BlockActions", "Vision OCR Fix"),
        QCoreApplication.translate("BlockActions", "Fix OCR"),
        "ocr_fix", (OCR_REVIEW_ID, LOW_CONF_ID), True,
    ),
    BlockActionDef(
        "act_retranslate",
        QCoreApplication.translate("BlockActions", "Contextual Retranslate"),
        QCoreApplication.translate("BlockActions", "Retranslate"),
        "retranslate",
        (TRANS_REVIEW_ID, "trans_confusing", "trans_polish"),
        False,
    ),
]

ACTION_REGISTRY: Dict[str, BlockActionDef] = {a.id: a for a in BLOCK_ACTIONS}


def page_data_needs_sync(blk_list, items) -> bool:
    """画布与数据层是否脱节（框级动作前的一致性判据）。

    合并/撤销只改视觉层：``ui/scenetext_manager.py::updateTextBlkList``
    要等保存等时机才把 ``proj.pages`` 按画布重建。动作读的是数据层，
    不先对齐就会拿到合并前的子块（实测：合并块重译只返回第一个子块的
    内容）。长度或对象身份不一致即需要重建。
    """
    if blk_list is None:
        return False
    if len(blk_list) != len(items):
        return True
    return any(blk is not item.blk for blk, item in zip(blk_list, items))


def build_context_lines(
    blk_list: List[TextBlock], blk_idx: int, radius: int = 2
) -> List[str]:
    """邻近块原文（上下文组装半径 ±radius，设计 §5 的代码预组装）。"""
    lines = []
    for ii in range(
        max(0, blk_idx - radius), min(len(blk_list), blk_idx + radius + 1)
    ):
        text = blk_list[ii].get_text()
        if text:
            mark = " ->" if ii == blk_idx else "   "
            lines.append(f"{mark} {text}")
    return lines


def _hint_section(hint: str) -> str:
    """用户补充要求段（确定性输入：用户最懂具体情景，见设计 §5）。"""
    hint = (hint or "").strip()
    if not hint:
        return ""
    return (
        "Additional requirement from the user for this block "
        "(follow it; it describes tone, wording or intent):\n" + hint
    )


def build_ocr_fix_messages(
    context_lines: List[str], current_text: str, hint: str = ""
) -> List[dict]:
    """疑难 OCR 校正的单轮消息（整块一条，图像部分由调用方插入）。"""
    context = "\n".join(context_lines) if context_lines else "(none)"
    system = (
        "You are a meticulous manga OCR corrector. You are shown the text of "
        "ONE text block. Reply with the corrected transcription ONLY: "
        "original language, no quotes, no comments, no translations."
    )
    parts = [
        "Nearby bubble text on the same page (for reference; the line "
        "marked with '->' is the target block):\n"
        f"{context}\n\n"
        "Current OCR result of the target block (may be wrong, "
        "incomplete or missing):\n"
        f"{current_text or '(empty)'}\n\n"
        "Return the corrected transcription of the target block only."
    ]
    hint_part = _hint_section(hint)
    if hint_part:
        parts.append(hint_part)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_ocr_fix_line_messages(
    line_texts: List[str], context_lines: List[str], hint: str = ""
) -> List[dict]:
    """逐行校正消息：图是同一块按行纠正后的纵向拼图，文给出每行现有 OCR。

    行数写进 system，回复必须等行对应——解析不上就退化为整块草稿
    （见 ``parse_ocr_fix_reply``），不猜对齐。
    """
    n = len(line_texts)
    context = "\n".join(context_lines) if context_lines else "(none)"
    numbered = "\n".join(
        f"{i + 1}: {str(t).strip() or '(empty)'}" for i, t in enumerate(line_texts)
    )
    system = (
        "You are a meticulous manga OCR corrector. The image shows the text "
        "lines of ONE text block, top to bottom in reading order (each line "
        "was perspective-corrected separately).\n"
        "Rules:\n"
        f"- Reply with the corrected transcription ONLY: one line per input "
        f"line, in the same order, exactly {n} lines.\n"
        "- Repeat already-correct lines unchanged.\n"
        "- Original language, no numbering, no quotes, no comments, no "
        "translations."
    )
    parts = [
        "Current OCR result of each line of the target block (may be wrong, "
        "incomplete or missing):\n"
        f"{numbered}\n\n"
        "Nearby bubble text on the same page (for reference only; use it for "
        "names and terms, never merge it into the result):\n"
        f"{context}\n\n"
        f"Return the corrected transcription of these {n} lines, one line per "
        "reply line."
    ]
    hint_part = _hint_section(hint)
    if hint_part:
        parts.append(hint_part)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


_NUMBER_PREFIX = re.compile(r"^\s*(?:line\s*)?\d+\s*[:.)\-]?\s*", re.IGNORECASE)


def parse_ocr_fix_reply(reply: str, n_lines: int) -> List[str]:
    """把逐行回复切成 ``n_lines`` 行；行数对不上返回空表（调用方退化整块）。"""
    if n_lines <= 0:
        return []
    text = (reply or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_NUMBER_PREFIX.sub("", ln).strip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) != n_lines:
        return []
    return lines


def block_crop_base64(img: np.ndarray, blk: TextBlock) -> Optional[str]:
    """块外接框裁剪 → JPEG base64（跟随 ocr_llm_api 的裁剪先例）。"""
    im_h, im_w = img.shape[:2]
    x1, y1, x2, y2 = blk.xyxy
    if not (0 <= x1 < x2 <= im_w and 0 <= y1 < y2 <= im_h):
        return None
    cropped = img[y1:y2, x1:x2]
    return _encode_jpeg(cropped)


def _encode_jpeg(img: np.ndarray) -> Optional[str]:
    if img is None or img.size == 0:
        return None
    ok, buffer = cv2.imencode(
        ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 92]
    )
    if not ok:
        return None
    return base64.b64encode(buffer).decode("utf-8")


def stitch_line_crops(img: np.ndarray, blk: TextBlock) -> Optional[str]:
    """按行透视纠正裁图 → 纵向拼成一张参考图（JPEG base64）。

    走 ``TextBlock.get_transformed_region``：与 OCR 管线同一套按行四点纠正，
    斜排/歪斜气泡不再是糊图；各行高矮归一到 ``_LINE_TARGET_H`` 后堆叠，
    避免合并块外接框里混进空当与邻居气泡。任一行取不到图即返回 None，
    由调用方退化为整块外接框裁剪。
    """
    crops: List[np.ndarray] = []
    for idx in range(len(blk.lines)):
        try:
            region = blk.get_transformed_region(img, idx, None)
        except Exception:
            return None
        if region is None or region.size == 0:
            return None
        crops.append(region)
    if not crops:
        return None

    normed: List[np.ndarray] = []
    for crop in crops:
        h, w = crop.shape[:2]
        if h <= 0 or w <= 0:
            return None
        scale = min(_LINE_TARGET_H / float(h), _LINE_MAX_UPSCALE)
        tgt_h = max(1, int(round(h * scale)))
        tgt_w = max(1, int(round(w * scale)))
        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
        normed.append(cv2.resize(crop, (tgt_w, tgt_h), interpolation=interp))

    width = max(c.shape[1] for c in normed)
    total_h = sum(c.shape[0] for c in normed) + _STITCH_GAP * (len(normed) - 1)
    canvas = np.full((total_h, width, 3), _STITCH_BG, dtype=np.uint8)
    y = 0
    for crop in normed:
        ch, cw = crop.shape[:2]
        x = (width - cw) // 2
        canvas[y:y + ch, x:x + cw] = crop
        y += ch + _STITCH_GAP

    h, w = canvas.shape[:2]
    if h > _STITCH_MAX_H or w > _STITCH_MAX_W:
        ratio = min(_STITCH_MAX_H / float(h), _STITCH_MAX_W / float(w))
        canvas = cv2.resize(
            canvas,
            (max(1, int(w * ratio)), max(1, int(h * ratio))),
            interpolation=cv2.INTER_AREA,
        )
    return _encode_jpeg(canvas)


@dataclass(frozen=True)
class OcrFixPayload:
    """疑难 OCR 校正的已组装载荷（主线程构建，worker 只发请求）。"""

    messages: List[dict]  # 完整多模态消息（图像段已内嵌）
    preview_b64: str  # 卡片预览：与模型所见同一张图
    line_texts: List[str]  # 逐行模式下为每行现有 OCR；否则为整块文本一条
    per_line: bool  # line_texts 是否与行一一对应（决定卡片能否逐行取舍）


def build_ocr_fix_payload(
    img: np.ndarray,
    blk: TextBlock,
    context_lines: List[str],
    hint: str = "",
) -> OcrFixPayload:
    """组装 OCR 校正载荷（主线程调用；图与文都在这里定死）。

    逐行模式条件：块的行几何齐全，且 ``blk.text`` 正好是每行一条
    （OCR 模块写入的形态）。人工改过或合并过的块文本会失去逐行对应，
    此时退化为整块一条——不拿行数去凑对齐。
    """
    n_lines = len(blk.lines)
    per_line = (
        n_lines > 0
        and isinstance(blk.text, list)
        and len(blk.text) == n_lines
    )
    img_b64 = stitch_line_crops(img, blk) if n_lines > 0 else None
    if img_b64 is None:
        img_b64 = block_crop_base64(img, blk)
        per_line = False
    if img_b64 is None:
        raise RuntimeError("Block is out of image bounds; cannot crop.")

    if per_line:
        line_texts = [str(t) for t in blk.text]
        messages = build_ocr_fix_line_messages(line_texts, context_lines, hint)
    else:
        line_texts = [blk.get_text()]
        messages = build_ocr_fix_messages(context_lines, line_texts[0], hint)

    # 多模态消息：文本段 + 图像段（content 须为数组，不能 append 到 str）
    messages[-1]["content"] = [
        {"type": "text", "text": messages[-1]["content"]},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        },
    ]
    return OcrFixPayload(
        messages=messages,
        preview_b64=img_b64,
        line_texts=line_texts,
        per_line=per_line,
    )


def run_vision_ocr_fix(
    profile: dict,
    messages: List[dict],
    cancel_event=None,
) -> str:
    """单轮 vision 调用（阻塞，须在 worker 线程跑）。

    profile 取自 utils/profile_manager 的 vision profile dict；
    messages 为主线程组装好的载荷（见 ``build_ocr_fix_payload``）；
    cancel_event 只能丢弃结果，无法中断已在途的 HTTP 请求。
    """
    import httpx
    import openai

    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError()

    api_host = (profile.get("api_host") or "").strip()
    if not api_host:
        raise RuntimeError("Vision profile has no api_host configured.")
    api_key = profile.get("api_key") or "dummy-key"
    model = profile.get("model") or ""
    if not model:
        raise RuntimeError("Vision profile has no model configured.")

    http_client = None
    proxy = (profile.get("proxy") or "").strip()
    if proxy:
        http_client = httpx.Client(
            mounts={"all://": httpx.HTTPTransport(proxy=proxy)}
        )
    client = openai.OpenAI(
        api_key=api_key,
        base_url=api_host,
        http_client=http_client,
        # 单框校正不需要长等待；无限等会让取消后槽位悬置很久
        timeout=90.0,
    )
    try:
        response = client.chat.completions.create(
            model=model, messages=messages
        )
    finally:
        if http_client is not None:
            http_client.close()

    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError()
    if not (response.choices and response.choices[0].message.content):
        raise RuntimeError("Empty response from vision model.")
    return response.choices[0].message.content.strip()


class CancelledError(RuntimeError):
    pass
