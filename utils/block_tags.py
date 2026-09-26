"""块级标签体系：类型注册表 + 块标签读写 + OCR 置信度／误识别自动挂标。

设计见 docs/技术实现/AI辅助功能_设计与实现.md 的「标签数据模型」「程序筛选器」两节
与「决策一览」（D1／D2／D28／D38／D39）：

- 标签 = 块的持久属性 ``TextBlock.tags``（dict，键为类型 id），
  随 ``TextBlock.to_dict``（vars 全量导出）自动随项目 JSON 保存。
- 每块每类型只设一个标签位（布尔语义），条目为
  ``{"source": "program"|"manual"|"ai", ...额外元数据}``；
  扩展字段（如 ``score``／``subtypes``／``reviewed``）随类型语义自由携带，
  不进注册表。
- **程序专用标签**（``TagDef.program_only``）不提供人工打标途径：
  不进标签工具栏、不进右键菜单，也不进自定义菜单的可选列表（D2／D38）。
  判据是这条声明，不是 ``source``（``ocr_low_conf`` 的 source 同为
  program，却仍可人工打标）。
- **审阅表态**用条目内 ``reviewed`` 字段（D28）：布尔、可逆、**粒度＝该条
  程序问题自己**（``set_tags_reviewed`` 带 ``tag_id``；不传则是旧调用方的
  块级全量语义）。驳回后条目仍在、从活动集合移除；挂标处对已驳回条目跳过，
  故重跑 OCR 后驳回记忆存活。粒度必须是「一个问题一条表态」：否则驳回误框
  误报会把同一块的低置信度建议一起永久冻结。``reviewed`` 不参与画布徽标
  过滤（D15）。
- **人工待办与旧 ID 兼容**（交接 §4.2③⑥、§5）：前台只有两个人工待办 ID
  （``ocr_review_pending``／``trans_review_pending``，见 ``REVIEW_PENDING_IDS``），
  持久翻译指示仍是 ``handwritten``／``onomatopoeia``。旧项目里人工来源的
  ``ocr_low_conf`` 在读侧算「稍后校对」、``trans_confusing``／``trans_polish``
  读作同一条「稍后重译」；**加载时不静默重写整本项目**，只在用户真去
  取消那条待办时顺手清掉旧 ID（``set_manual_tag`` 的 ``_clear_legacy_review``）。
- **人工编辑即知情**（D29）：该块**原文**被人工改写后清除其全部程序标签
  （``prune_program_tags_after_source_edit`` 供原文面板链路用，写原文的
  框级动作命令直接调 ``clear_program_tags``）。**译文编辑不清**——两个程序
  标签判的都是原文的 OCR 结果，改译文不使该判断失效。
- 名称在字面量定义处用 ``QCoreApplication.translate`` 显式标注上下文
  （i18n 模块级翻译表规则），下游直接使用已翻译值。
  **加载顺序约束：** 本模块被 ``modules/ocr`` 等模块顶层导入，任何在
  启动翻译器安装前的导入（如模块 resolve() 探测）都会把名字冻结成
  英文——launch.py 的翻译器安装必须先于一切模块导入。
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from PyQt6.QtCore import QCoreApplication

from .textblock import TextBlock


@dataclass(frozen=True)
class TagDef:
    id: str
    name: str  # 已翻译显示名
    nature: str  # "doubt" 疑点（一次性处理） / "directive" 指示（持久属性）
    source: str  # "program" / "manual" / "ai"（预留）
    glyph: str  # 画布徽标字形
    program_only: bool = False  # 程序专用：不提供人工打标途径（D2／D38）


# 前台标签 id（人工待办两个、程序问题两个、持久翻译指示两个）＋ 只读兼容的旧 id
OCR_REVIEW_ID = "ocr_review_pending"
TRANS_REVIEW_ID = "trans_review_pending"
LOW_CONF_ID = "ocr_low_conf"
HANDWRITTEN_ID = "handwritten"
ONOMATOPOEIA_ID = "onomatopoeia"
# 旧 id（只读兼容：读侧归一到上面两个待办；不再出现在任何前台入口）
LEGACY_TRANS_REVIEW_IDS = ("trans_confusing", "trans_polish")

# 人工待办的两个前台 ID（「稍后校对」「稍后重译」）。人工取消待办时把旧 ID
# 一并清掉——**不在加载时静默重写整本项目**，兼容读旧、写新，旧条目等用户
# 真去操作它的那一天才被清。
REVIEW_PENDING_IDS = (OCR_REVIEW_ID, TRANS_REVIEW_ID)

TAG_DEFS = [
    # 名称在字面量定义处显式标注翻译上下文（i18n 模块级翻译表规则）
    TagDef(OCR_REVIEW_ID,
           QCoreApplication.translate("BlockTags", "Review Source Later"),
           "doubt", "manual", "!"),
    TagDef(TRANS_REVIEW_ID,
           QCoreApplication.translate("BlockTags", "Retranslate Later"),
           "doubt", "manual", "?"),
    TagDef(LOW_CONF_ID,
           QCoreApplication.translate("BlockTags", "Low OCR Confidence"),
           "doubt", "program", "!"),
    TagDef(HANDWRITTEN_ID,
           QCoreApplication.translate("BlockTags", "Handwritten"),
           "directive", "manual", "✎"),
    TagDef(ONOMATOPOEIA_ID,
           QCoreApplication.translate("BlockTags", "Onomatopoeia"),
           "directive", "manual", "♪"),
    # 旧译文疑点 id：只读兼容（读作一条「稍后重译」），不再有前台入口
    TagDef(LEGACY_TRANS_REVIEW_IDS[0],
           QCoreApplication.translate("BlockTags", "Confusing Translation"),
           "doubt", "manual", "?"),
    TagDef(LEGACY_TRANS_REVIEW_IDS[1],
           QCoreApplication.translate("BlockTags", "Polish Translation"),
           "doubt", "manual", "✦"),
    # 误识别类（D9／D38）：单标签 + 条目内子类型，仅由 OCR 后处理钩子自动挂
    TagDef("ocr_misread",
           QCoreApplication.translate("BlockTags", "Misread Text"),
           "doubt", "program", "⚠", program_only=True),
]

TAG_REGISTRY: Dict[str, TagDef] = {t.id: t for t in TAG_DEFS}

# 前台只暴露人工待办与持久翻译指示；其余 id 仅用于兼容读取。
MANUAL_TAG_IDS = (OCR_REVIEW_ID, TRANS_REVIEW_ID, HANDWRITTEN_ID, ONOMATOPOEIA_ID)

MANUAL_TAG_DEFS = [t for t in TAG_DEFS if t.id in MANUAL_TAG_IDS]

# 疑点优先：徽标取色与同类并挂取保守动作时按此排序
NATURE_PRIORITY = {"doubt": 0, "directive": 1}

# 指示标签 → 批量管线翻译指令（LLM prompt，不需翻译；设计 §3 的消费时机）。
# 疑点标签不阻塞自动管线，不在此表。
DIRECTIVE_INSTRUCTIONS = {
    HANDWRITTEN_ID: (
        "This block is handwritten; the OCR result may be unreliable. "
        "Infer the intended text from the context and translate its meaning."
    ),
    ONOMATOPOEIA_ID: (
        "This block is an onomatopoeia/sound effect with no literal "
        "meaning: render it phonetically (by its sound) in the target "
        "language, not semantically."
    ),
}


# 旧 ID → 前台 ID 的显示归一（徽标只画一条，不让同一个待办显成两个记号）
def _display_id(blk: TextBlock, tag_id: str) -> str:
    """把旧的人工待办 ID 归一到新 ID；程序来源的 ``ocr_low_conf`` 原样保留。"""
    if tag_id == LOW_CONF_ID and has_ocr_review_pending(blk):
        return OCR_REVIEW_ID
    if tag_id in LEGACY_TRANS_REVIEW_IDS and has_trans_review_pending(blk):
        return TRANS_REVIEW_ID
    return tag_id


def sorted_tag_ids(blk: TextBlock) -> List[str]:
    """块上挂着的标签 id，旧 ID 归一去重后：疑点在前、注册表顺序次之。"""
    ids: List[str] = []
    for tag_id in blk.tags:
        if tag_id not in TAG_REGISTRY:
            continue
        tag_id = _display_id(blk, tag_id)
        if tag_id not in ids:
            ids.append(tag_id)
    return sorted(
        ids,
        key=lambda tid: (
            NATURE_PRIORITY.get(TAG_REGISTRY[tid].nature, 99),
            ids.index(tid),
        ),
    )


def has_tag(blk: TextBlock, tag_id: str) -> bool:
    return tag_id in blk.tags


def get_tag(blk: TextBlock, tag_id: str) -> Optional[dict]:
    return blk.tags.get(tag_id)


def set_tag(blk: TextBlock, tag_id: str, source: str, **extra) -> None:
    blk.tags[tag_id] = {"source": source, **extra}


def remove_tag(blk: TextBlock, tag_id: str) -> None:
    blk.tags.pop(tag_id, None)


def directive_instructions(blk: TextBlock) -> List[str]:
    """块上指示标签对应的管线翻译指令文本（无则空表）。"""
    return [
        DIRECTIVE_INSTRUCTIONS[tid]
        for tid in sorted_tag_ids(blk)
        if tid in DIRECTIVE_INSTRUCTIONS
    ]


def _clear_legacy_review(blk: TextBlock, tag_id: str) -> None:
    """清掉该待办对应的旧 ID（新 ID 的写入/取消共用这一步）。"""
    if tag_id == OCR_REVIEW_ID:
        entry = blk.tags.get(LOW_CONF_ID)
        # 程序来源的 ocr_low_conf 是「程序建议」，不能被人工作业顺手抹掉
        if isinstance(entry, dict) and entry.get("source") == "manual":
            remove_tag(blk, LOW_CONF_ID)
    elif tag_id == TRANS_REVIEW_ID:
        for legacy in LEGACY_TRANS_REVIEW_IDS:
            remove_tag(blk, legacy)


def set_manual_tag(blk: TextBlock, tag_id: str, checked: bool) -> None:
    """人工打标／取消的**唯一写入点**（工具栏、右键菜单、快捷键、饼菜单共用）。

    对两个人工待办 ID 而言「取消」必须连旧 ID 一起清——否则旧项目里
    取消过一次、待办却还在队列里（读侧认得旧 ID）。指示标签原样写入。
    """
    if checked:
        set_tag(blk, tag_id, "manual")
    else:
        remove_tag(blk, tag_id)
    if tag_id in REVIEW_PENDING_IDS:
        _clear_legacy_review(blk, tag_id)


def toggle_on_blocks(blks: List[TextBlock], tag_id: str) -> bool:
    """多选批量切换（入口三件套共用）：非全员带标签 → 全部挂上，否则全部摘除。

    打标不进撤销栈（轻量元数据，误点再点即撤）；返回切换后的挂载状态。
    """
    checked = not all(has_tag(b, tag_id) for b in blks)
    for b in blks:
        set_manual_tag(b, tag_id, checked)
    return checked


# ── 程序标签的审阅表态（D28：布尔、可逆、块级）─────────────────────


def program_tag_ids(blk: TextBlock) -> List[str]:
    """块上全部程序来源标签 id（保序）。"""
    return [
        tid
        for tid, entry in blk.tags.items()
        if isinstance(entry, dict) and entry.get("source") == "program"
    ]


def is_tag_reviewed(blk: TextBlock, tag_id: str) -> bool:
    """该标签条目是否已被人工驳回（D28：驳回即 ``reviewed`` 为真）。"""
    entry = blk.tags.get(tag_id)
    return isinstance(entry, dict) and bool(entry.get("reviewed"))


def set_tags_reviewed(
    blk: TextBlock, reviewed: bool = True, tag_id: Optional[str] = None
) -> int:
    """驳回或恢复**指定**程序问题（D28 修订：粒度＝一条问题，不是一个块）。

    ``tag_id=None`` 是旧调用方的块级全量语义（测试与导入旧数据的兜底）；
    生产路径一律带 ID——否则驳回误框误报会把同一块的低置信度建议一起
    永久冻结，两轴再也分不开。返回实际改动的条目数。"""
    changed = 0
    ids = [tag_id] if tag_id is not None else program_tag_ids(blk)
    for tid in ids:
        entry = blk.tags.get(tid)
        if not isinstance(entry, dict) or entry.get("source") != "program":
            continue
        if reviewed:
            if not entry.get("reviewed"):
                entry["reviewed"] = True
                changed += 1
        elif entry.pop("reviewed", None) is not None:
            changed += 1
    return changed


def has_ocr_review_pending(blk: TextBlock) -> bool:
    """该块是否被人工记为「稍后校对」（含旧项目的旧 ID，读侧兼容）。"""
    return OCR_REVIEW_ID in blk.tags or (
        isinstance(blk.tags.get(LOW_CONF_ID), dict)
        and blk.tags[LOW_CONF_ID].get("source") == "manual"
    )


def has_trans_review_pending(blk: TextBlock) -> bool:
    """该块是否被人工记为「稍后重译」；旧的两个 ID 读作同一条待办。"""
    return any(tag_id in blk.tags for tag_id in (TRANS_REVIEW_ID,) + LEGACY_TRANS_REVIEW_IDS)


def has_ocr_suggestion(blk: TextBlock) -> bool:
    """程序给出的「低置信度」建议：仍有条目且未被驳回（缺分数不算低）。"""
    entry = blk.tags.get(LOW_CONF_ID)
    return (
        isinstance(entry, dict)
        and entry.get("source") == "program"
        and not entry.get("reviewed")
    )


def active_review_ids(blk: TextBlock) -> List[str]:
    """该块上**活动待处理问题**的 id（E／Q 跳转、菜单可用性、徽标口径共用）。

    只列「还没处理掉的」：人工待办（原文／译文）、程序建议（低置信度、
    误识别）中尚未驳回的那些。**不含持久翻译指示**（手写字／拟声词是块的
    属性，处理完也不消失，拿它当跳转靶子会把用户反复送回同一批块），
    也不含已被驳回的程序建议与历史未知 ID。
    """
    active = []
    if has_ocr_review_pending(blk) or has_ocr_suggestion(blk):
        active.append(OCR_REVIEW_ID)
    if has_trans_review_pending(blk):
        active.append(TRANS_REVIEW_ID)
    if MISREAD_TAG_ID in blk.tags and not is_tag_reviewed(blk, MISREAD_TAG_ID):
        active.append(MISREAD_TAG_ID)
    return active


def has_active_review(blk: TextBlock) -> bool:
    """该块是否还有活动待处理问题（跳转与菜单可用性的统一判据）。"""
    return bool(active_review_ids(blk))


def iter_active_review_blocks(pages: Dict) -> Iterator[Tuple[str, int, TextBlock]]:
    """遍历项目页字典，产出 ``(页名, 块序号, 块)``——仍有活动待处理问题的块。"""
    for pagename, blks in (pages or {}).items():
        for idx, blk in enumerate(blks or []):
            if has_active_review(blk):
                yield pagename, idx, blk


def count_active_review(pages: Dict) -> int:
    """活动待处理问题的块数（全量只读扫描；口径与 ``active_review_ids`` 同）。"""
    return sum(1 for _ in iter_active_review_blocks(pages))


def clear_program_tags(blk: TextBlock) -> int:
    """清除该块全部程序来源临时标签（D29：人工编辑即知情）。

    整条删除、**不区分是否带 ``reviewed``**；返回删除的条目数。
    标签不进撤销栈——撤销文本编辑不会恢复被清标签（D29 明确可接受：
    重跑 OCR 会重算）。
    """
    ids = program_tag_ids(blk)
    for tid in ids:
        remove_tag(blk, tid)
    return len(ids)


def prune_program_tags_after_source_edit(blk: TextBlock, panel_text: str) -> int:
    """原文面板内容变更后的清标签判据（D29）。

    ``panel_text`` 与该块**数据层原文**不一致 ＝ 存在尚未落盘的人工编辑，
    此时清除该块全部程序来源标签（含已驳回条目）；两侧一致则是程序性
    面板回写（例如合并命令把并集文本同步回面板），**不清**——合并的标签
    归组语义见设计 §15 的 D33（并集保留）。

    返回清除的条目数（未触发或无可清条目时为 0）。
    """
    if (panel_text or "") == (blk.get_text() or ""):
        return 0
    return clear_program_tags(blk)


def _program_entry_writable(blk: TextBlock, tag_id: str) -> bool:
    """程序挂标的前置判据：人工／AI 挂的条目不覆写；已驳回的条目保持不变。"""
    entry = blk.tags.get(tag_id)
    if entry is None:
        return True
    if not isinstance(entry, dict) or entry.get("source") != "program":
        return False
    return not entry.get("reviewed")


def apply_ocr_confidence_tag(
    blk: TextBlock, score: Optional[float], threshold: float
) -> None:
    """按识别分数维护「OCR 置信度低」程序标签（分数不喂 AI，见设计 §4）。

    - ``score`` 取该块各行的**最差**分数（min，由调用方聚合）；
    - 人工确认过的标签（source != program）不受程序覆写；
    - 已驳回（``reviewed``）的条目跳过，重跑 OCR 后驳回记忆存活（D28）；
    - 分数回好后自动摘除程序标签，重跑 OCR 即可刷新。
    """
    if not _program_entry_writable(blk, LOW_CONF_ID):
        return
    if score is not None and score < threshold:
        set_tag(blk, LOW_CONF_ID, "program", score=round(float(score), 4))
    else:
        remove_tag(blk, LOW_CONF_ID)


# ── 误识别类程序筛选器（D2／D9／D38／D39）──────────────────────────

MISREAD_TAG_ID = "ocr_misread"

# 四类判定（D38）：存条目内子类型字段，允许一个块同时命中多类。
# 顺序即展示序；"no_japanese" 是兜底类，纯数字／纯符号块通常也命中它。
MISREAD_SUBTYPES = ("empty", "numeric", "symbolic", "no_japanese")

# 子类型显示名（字面量定义处显式标注翻译上下文）
MISREAD_SUBTYPE_LABELS = {
    "empty": QCoreApplication.translate("BlockTags", "Empty text"),
    "numeric": QCoreApplication.translate("BlockTags", "Numeric only"),
    "symbolic": QCoreApplication.translate("BlockTags", "Symbols only"),
    "no_japanese": QCoreApplication.translate("BlockTags", "No kana/kanji"),
}


def _is_kana(ch: str) -> bool:
    """平假名／片假名（含半角片假名与片假名扩展）。"""
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # 平假名 + 片假名
        or 0x31F0 <= o <= 0x31FF  # 片假名扩展
        or 0xFF66 <= o <= 0xFF9F  # 半角片假名
    )


def _is_cjk(ch: str) -> bool:
    """CJK 表意文字（含扩展 A／兼容区与重复记号 々〆）。"""
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or o in (0x3005, 0x3007)
    )


def _is_letter_or_digit(ch: str) -> bool:
    """字母或十进制数字（仅 ASCII／全角 0-9）。

    刻意不用 ``str.isalnum()``／``str.isdigit()``：两者都把 ①（Unicode
    类别 No，Numeric_Type=Digit）算作数字，会让「纯符号」漏判——样例里
    ``①`` 正是该类的代表块。``str.isdecimal()`` 恰只认十进制数字。
    """
    return ch.isalpha() or ch.isdecimal()


def classify_misread_text(text: str) -> List[str]:
    """按四类判定给出命中的子类型（无命中返回空表）。

    - ``empty``：整块无有效文本（含只有空白）；
    - ``numeric``：非空白字符全是数字；
    - ``symbolic``：非空白字符无字母无数字（标点／符号／圈号等）；
    - ``no_japanese``：整块无假名也无汉字（疑似 OCR 乱码，如 ``SS``／``anad``）。

    ``empty`` 单独短路——空块不参与其余三类，否则「无假名无汉字」会被
    空块平凡命中、把队列灌满。
    """
    text = (text or "").strip()
    if not text:
        return ["empty"]
    out = []
    if all(c.isdecimal() for c in text):
        out.append("numeric")
    if not any(_is_letter_or_digit(c) for c in text):
        out.append("symbolic")
    if not any(_is_kana(c) or _is_cjk(c) for c in text):
        out.append("no_japanese")
    return out


def apply_misread_tag(blk: TextBlock, subtypes: List[str]) -> None:
    """维护「误识别文本」程序标签（形态见 D38：单标签 + 条目内子类型）。

    命中即写（覆写子类型）；不命中即摘。已驳回的条目跳过（D28）。
    """
    if not _program_entry_writable(blk, MISREAD_TAG_ID):
        return
    if subtypes:
        set_tag(blk, MISREAD_TAG_ID, "program", subtypes=list(subtypes))
    else:
        remove_tag(blk, MISREAD_TAG_ID)


def tag_misread_hook(*, textblocks=None, img=None, ocr_module=None) -> None:
    """OCR 后处理钩子：跑完 OCR 后逐块判定误识别（落点见 D39）。

    - 纯关键字形参——``modules/ocr/base.py`` 的 ``run_ocr`` 以
      ``textblocks=``／``img=``／``ocr_module=`` 调用；
    - **必须短路 ``none_ocr``**：钩子遍历在 ``run_ocr`` 的 none_ocr 分支
      之外，而 none_ocr 的语义是「保留已有文本」，不短路会在「不跑 OCR」
      的项目上凭空挂出一批空文本／纯符号标签。
    """
    if ocr_module is not None and getattr(ocr_module, "name", "") == "none_ocr":
        return
    for blk in textblocks or []:
        apply_misread_tag(blk, classify_misread_text(blk.get_text()))


# ── 队列查询（工作台「误识别清理」的数据源，D28 口径）──────────────


def iter_misread_blocks(pages: Dict) -> Iterator[Tuple[str, int, TextBlock]]:
    """遍历项目页字典，产出 ``(页名, 块序号, 块)``——带误识别标签的块。

    队列按**块**去重（一个块只出现一次）；``ocr_low_conf`` 不单独入队
    （其出口是 ``utils/block_actions.py`` 的框级 OCR 校正动作）。
    """
    for pagename, blks in (pages or {}).items():
        for idx, blk in enumerate(blks or []):
            if MISREAD_TAG_ID in blk.tags:
                yield pagename, idx, blk


def misread_queue_summary(pages: Dict) -> Dict[str, int]:
    """队列规模：``待处理计数 = 队列内块数 − 已驳回块数``（D28）。"""
    total = rejected = 0
    for _, _, blk in iter_misread_blocks(pages):
        total += 1
        if is_tag_reviewed(blk, MISREAD_TAG_ID):
            rejected += 1
    return {"total": total, "rejected": rejected, "pending": total - rejected}


def misread_subtype_counts(pages: Dict) -> Dict[str, int]:
    """各子类型的命中**条目数**（一块可命中多类，故合计可大于块数）。"""
    counts = {sid: 0 for sid in MISREAD_SUBTYPES}
    for _, _, blk in iter_misread_blocks(pages):
        entry = blk.tags.get(MISREAD_TAG_ID) or {}
        for sid in entry.get("subtypes") or []:
            if sid in counts:
                counts[sid] += 1
    return counts
