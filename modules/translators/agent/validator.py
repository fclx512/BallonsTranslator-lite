"""提交校验器(出口质量护栏,设计方案 §6.E 的格式与封闭集部分)。

submit_translations 的唯一校验入口:参数形状、id 封闭集、空值检测、统一清洗、
译文=原文与术语残留(先警告后打回)。覆盖完整性(缺失块)由 loop 在接受提交后
检查并打回。
"""

from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple


def clean_translation(text) -> str:
    """统一清洗:换行规整(\\r\\n → \\n)+ 首尾空白。取代散落各处的清理。"""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _iter_submission_items(arguments) -> Tuple[List[Tuple], Optional[str]]:
    """从 submit_translations 参数中取出 (id, translation) 对。

    兼容两种形状:{"translations": {"1": "译文"}} 与
    {"translations": [{"id": 1, "translation": "译文"}]}。
    """
    if not isinstance(arguments, dict):
        return [], "submit_translations arguments must be a JSON object."

    payload = arguments.get("translations")
    if isinstance(payload, dict):
        return list(payload.items()), None
    if isinstance(payload, list):
        items = []
        for item in payload:
            if (
                not isinstance(item, dict)
                or "id" not in item
                or "translation" not in item
            ):
                return [], (
                    'Each item in "translations" must be an object with '
                    '"id" and "translation" fields.'
                )
            items.append((item["id"], item["translation"]))
        return items, None
    return [], (
        '"translations" must be an object mapping block id to translated '
        'string, e.g. {"1": "...", "2": "..."}.'
    )


def _same_as_source(source: str, translation: str) -> bool:
    """译文与原文完全相同(日→中等语言对下大概率是偷懒)。

    纯数字与单字符源不判定(数字/拟声豁免的粗糙第一版,详见设计方案 §6.E)。
    """
    if not source or len(source) < 2:
        return False
    if source.isdigit():
        return False
    return source == translation


def _residue_terms(
    glossary_terms: Sequence, source: str, translation: str
) -> List[str]:
    """命中术语的 src 原词仍留在对应译文中(未遵守术语约束)。

    只查本块原文确实含该词、且术语不是恒等映射(src==dst)的词条;
    子串精确判定,不判断译名正确性(设计 §6.E.5)。
    """
    found = []
    for src, dst in glossary_terms:
        term_src = (src or "").strip()
        term_dst = (dst or "").strip()
        if not term_src or not term_dst or len(term_src) < 2:
            continue
        if term_src == term_dst:
            continue
        if term_src in source and term_src in translation:
            found.append(term_src)
    return found


def validate_submission(
    arguments,
    valid_ids: FrozenSet[int],
    *,
    source_map: Optional[Dict[int, str]] = None,
    glossary_terms: Sequence = (),
    warned_ids: FrozenSet[int] = frozenset(),
) -> Tuple[Optional[Dict[int, str]], Optional[str], List[str], List[int], List[int]]:
    """校验一次 submit_translations 提交。

    返回 (translations, feedback, warnings, newly_warned_ids, rejected_ids):
    - translations 为 None:整体拒绝,feedback 是回给模型的错误说明;
    - translations 非 None:接受的部分({id: 清洗后译文},键为 valid_ids
      子集),feedback 为需要回传的硬问题(无问题则为 None);
    - warnings:本轮接受的"先警告后打回"软警告(译文=原文/术语残留首次);
    - newly_warned_ids:本轮新警告的 id,调用方累计,再犯即打回;
    - rejected_ids:本轮因"警告过再犯"被打回的 id,调用方须把它们从
      已累积结果中移除(否则旧条目让缺失检查误判为已覆盖)。
    """
    items, err = _iter_submission_items(arguments)
    if err:
        return None, err, [], [], []

    translations: Dict[int, str] = {}
    invalid_ids: List[str] = []
    empty_ids: List[int] = []
    same_warned: List[int] = []
    same_rejected: List[int] = []
    residue_warned: List[int] = []
    residue_rejected: List[int] = []
    newly_warned: List[int] = []

    for raw_id, raw_text in items:
        try:
            block_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            invalid_ids.append(str(raw_id))
            continue
        if block_id not in valid_ids:
            invalid_ids.append(str(raw_id))
            continue
        text = clean_translation(raw_text)
        if not text:
            empty_ids.append(block_id)
            continue

        source = ""
        if source_map is not None:
            source = (source_map.get(block_id) or "").strip()
        same = bool(source) and _same_as_source(source, text)
        residue = _residue_terms(glossary_terms, source, text) if source else []

        if same or residue:
            if block_id in warned_ids:
                # 已警告过仍再犯 → 打回(该 id 进 missing,由 loop 重求)
                if same:
                    same_rejected.append(block_id)
                if residue:
                    residue_rejected.append(block_id)
                continue
            # 首次 → 警告并接受
            newly_warned.append(block_id)
            if same:
                same_warned.append(block_id)
            if residue:
                residue_warned.append(block_id)
        translations[block_id] = text

    problems = []
    if invalid_ids:
        shown = [str(i) for i in invalid_ids[:10]]
        suffix = " ..." if len(invalid_ids) > 10 else ""
        problems.append(
            f"Invalid or out-of-task ids (ignored): {shown}{suffix}. "
            f"Valid ids are: {sorted(valid_ids)}."
        )
    if empty_ids:
        problems.append(
            f"Empty translations for ids {sorted(set(empty_ids))} were "
            "rejected; every block needs a non-empty translation."
        )
    if same_rejected:
        problems.append(
            f"Translations for ids {sorted(set(same_rejected))} still equal "
            "their source text and were rejected; provide a real translation."
        )
    if residue_rejected:
        problems.append(
            "Glossary source terms still appear untranslated in the rejected "
            "translations; replace them with the glossary translations."
        )

    warnings = []
    if same_warned:
        warnings.append(
            f"Translations equal their source for ids {sorted(set(same_warned))}: "
            "accepted this round, but they will be rejected if resubmitted "
            "unchanged."
        )
    if residue_warned:
        warnings.append(
            f"Glossary source terms remain in translations of ids "
            f"{sorted(set(residue_warned))}: accepted this round, but they will "
            "be rejected if resubmitted unchanged."
        )

    rejected_ids = sorted(set(same_rejected) | set(residue_rejected))
    if not translations:
        detail = " ".join(problems) if problems else "No translations found."
        return None, f"Submission rejected. {detail}", warnings, newly_warned, rejected_ids
    if problems:
        return translations, " ".join(problems), warnings, newly_warned, rejected_ids
    return translations, None, warnings, newly_warned, rejected_ids
