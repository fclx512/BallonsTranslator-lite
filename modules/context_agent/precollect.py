"""频率启发式术语收集(自 modules/glossary_extractor.py 迁入)。

在新工作台里降级为「草稿预填充」按钮:把项目里反复出现且已有稳定译文的
原文串整理成草稿基底,供人工筛查/AI 增量加工,不再是独立提取模式。
"""

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Dict, Tuple

if TYPE_CHECKING:
    from utils.proj_imgtrans import ProjImgTrans

_MIN_SRC_LEN = 2
_MAX_SRC_LEN = 50
_DEFAULT_MIN_COUNT = 2


def extract_by_frequency(
    proj: "ProjImgTrans",
    min_count: int = _DEFAULT_MIN_COUNT,
) -> Tuple[Tuple[str, str, str], ...]:
    """统计全项目原文出现次数,提取出现 >= min_count 次且已有非同文译文的条目。

    同一原文被译成多种译文时取最高频译文。返回 (src, dst, note) 三元组
    序列,按出现次数降序,供 GlossaryDraft.from_entries 载入。
    """
    source_counter: Counter[str] = Counter()
    trans_map: Dict[str, Counter[str]] = defaultdict(Counter)

    for blk_list in proj.pages.values():
        for blk in blk_list:
            src = blk.get_text().strip()
            if not _MIN_SRC_LEN <= len(src) <= _MAX_SRC_LEN:
                continue
            tr = (blk.translation or "").strip()
            if not tr or tr == src:
                continue
            source_counter[src] += 1
            trans_map[src][tr] += 1

    entries = []
    for src, count in source_counter.most_common():
        if count < min_count:
            continue
        tr_counts = trans_map.get(src)
        if not tr_counts:
            continue
        best_tr = tr_counts.most_common(1)[0][0]
        entries.append((src, best_tr, ""))
    return tuple(entries)
