"""块级标签体系：类型注册表 + 块标签读写 + OCR 置信度自动挂标。

设计见 docs/技术实现/AI辅助功能_规划.md §8.3/§8.7：
- 标签 = 块的持久属性 ``TextBlock.tags``（dict，键为类型 id），
  随 ``TextBlock.to_dict``（vars 全量导出）自动随项目 JSON 保存。
- 每块每类型只设一个标签位（布尔语义），条目为
  ``{"source": "program"|"manual"|"ai", ...额外元数据}``；
  扩展字段（如 score）随类型语义自由携带，不进注册表。
- 名称在字面量定义处用 ``QCoreApplication.translate`` 显式标注上下文
  （i18n 模块级翻译表规则），下游直接使用已翻译值。
  **加载顺序约束：** 本模块被 ``modules/ocr`` 等模块顶层导入，任何在
  启动翻译器安装前的导入（如模块 resolve() 探测）都会把名字冻结成
  英文——launch.py 的翻译器安装必须先于一切模块导入。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from PyQt6.QtCore import QCoreApplication

from .textblock import TextBlock


@dataclass(frozen=True)
class TagDef:
    id: str
    name: str  # 已翻译显示名
    nature: str  # "doubt" 疑点（一次性处理） / "directive" 指示（持久属性）
    source: str  # "program" / "manual" / "ai"（预留）
    glyph: str  # 画布徽标字形


TAG_DEFS = [
    # 名称在字面量定义处显式标注翻译上下文（i18n 模块级翻译表规则）
    TagDef("ocr_low_conf",
           QCoreApplication.translate("BlockTags", "Low OCR Confidence"),
           "doubt", "program", "!"),
    TagDef("handwritten",
           QCoreApplication.translate("BlockTags", "Handwritten"),
           "directive", "manual", "✎"),
    TagDef("onomatopoeia",
           QCoreApplication.translate("BlockTags", "Onomatopoeia"),
           "directive", "manual", "♪"),
    TagDef("trans_confusing",
           QCoreApplication.translate("BlockTags", "Confusing Translation"),
           "doubt", "manual", "?"),
    TagDef("trans_polish",
           QCoreApplication.translate("BlockTags", "Polish Translation"),
           "doubt", "manual", "✦"),
]

TAG_REGISTRY: Dict[str, TagDef] = {t.id: t for t in TAG_DEFS}

# 疑点优先：徽标取色与同类并挂取保守动作时按此排序
NATURE_PRIORITY = {"doubt": 0, "directive": 1}

# 指示标签 → 批量管线翻译指令（LLM prompt，不需翻译；规划 §8.3 消费时机）。
# 疑点标签不阻塞自动管线，不在此表。
DIRECTIVE_INSTRUCTIONS = {
    "handwritten": (
        "This block is handwritten; the OCR result may be unreliable. "
        "Infer the intended text from the context and translate its meaning."
    ),
    "onomatopoeia": (
        "This block is an onomatopoeia/sound effect with no literal "
        "meaning: render it phonetically (by its sound) in the target "
        "language, not semantically."
    ),
}


def sorted_tag_ids(blk: TextBlock) -> List[str]:
    """块上挂着的标签 id，疑点在前、注册表顺序次之。"""
    ids = [tid for tid in blk.tags if tid in TAG_REGISTRY]
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


def toggle_on_blocks(blks: List[TextBlock], tag_id: str) -> bool:
    """多选批量切换（入口三件套共用）：非全员带标签 → 全部挂上，否则全部摘除。

    打标不进撤销栈（轻量元数据，误点再点即撤）；返回切换后的挂载状态。
    """
    checked = not all(has_tag(b, tag_id) for b in blks)
    for b in blks:
        if checked:
            set_tag(b, tag_id, "manual")
        else:
            remove_tag(b, tag_id)
    return checked


def apply_ocr_confidence_tag(
    blk: TextBlock, score: Optional[float], threshold: float
) -> None:
    """按识别分数维护「OCR 置信度低」程序标签（分数不喂 AI，见规划 §8.2）。

    - ``score`` 取该块各行的**最差**分数（min，由调用方聚合）；
    - 人工确认过的标签（source != program）不受程序覆写；
    - 分数回好后自动摘除程序标签，重跑 OCR 即可刷新。
    """
    entry = get_tag(blk, "ocr_low_conf")
    if entry is not None and entry.get("source") != "program":
        return
    if score is not None and score < threshold:
        set_tag(blk, "ocr_low_conf", "program", score=round(float(score), 4))
    else:
        remove_tag(blk, "ocr_low_conf")
