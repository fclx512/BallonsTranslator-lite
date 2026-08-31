"""权威草稿模型:AI patch 的唯一落点,「应用」前的全部状态。

两种草稿共用同一套语义:

- 打开工作台即载入现有数据为基底(origin=existing),AI 增量加工,无第二份数据;
- AI 提交的条目 origin=ai,后续轮次可自由更新/撤销自己的条目;
- 碰撞 existing/user 条目且内容不同 → 不落草稿,以冲突行回报,由人裁决
  (user-owned 保护:人工改过即受保护);
- UI 编辑经 set_user_* 写入,origin 升级为 user(受保护)。

本模块不做 IO,落盘由 UI 层从镜像写(阶段 2)。
"""

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

ORIGIN_EXISTING = "existing"
ORIGIN_AI = "ai"
ORIGIN_USER = "user"

_ACTIONS_GLOSSARY = ("add", "update", "remove")


@dataclass
class DraftEntry:
    """草稿中的一条术语映射。source 为身份键(casefold 比对)。"""

    source: str
    translation: str
    note: str = ""
    origin: str = ORIGIN_EXISTING


class DraftConflict(Exception):
    """单条 patch 操作因撞上受保护条目而未应用(不作废整轮)。"""

    def __init__(self, message: str, row: Dict[str, Any]):
        super().__init__(message)
        self.row = row


class DraftValueError(Exception):
    """patch 形状/内容非法(回报给模型自纠)。"""


def _clean_str(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DraftValueError(f'Field "{field}" must be a string.')
    value = value.strip()
    if not value and not allow_empty:
        raise DraftValueError(f'Field "{field}" must not be empty.')
    return value


class GlossaryDraft:
    """术语表草稿:有序条目列表 + patch 应用/冲突裁决。"""

    def __init__(self) -> None:
        self.entries: List[DraftEntry] = []

    @classmethod
    def from_entries(cls, entries, origin: str = ORIGIN_EXISTING) -> "GlossaryDraft":
        """从 (source, translation, note) 序列或 DraftEntry 序列载入基底。"""
        draft = cls()
        for entry in entries:
            if isinstance(entry, DraftEntry):
                draft.entries.append(replace(entry))
            else:
                src, dst, *rest = entry
                draft.entries.append(
                    DraftEntry(src, dst, rest[0] if rest else "", origin)
                )
        return draft

    def _index(self) -> Dict[str, int]:
        return {e.source.casefold(): i for i, e in enumerate(self.entries)}

    def apply_patch(self, entries: Any) -> Dict[str, Any]:
        """应用一次 submit_glossary_patch 的 entries 数组。

        单条失败(冲突/非法)记入 conflicts/errors 继续处理余下条目,
        不作废整轮(上游失败语义)。返回 {"applied", "conflicts", "errors"}。
        """
        if not isinstance(entries, list):
            raise DraftValueError('Field "entries" must be an array.')
        applied = 0
        conflicts: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for i, item in enumerate(entries):
            row = {"index": i}
            try:
                if not isinstance(item, dict):
                    raise DraftValueError("each entry must be an object")
                action = item.get("action", "add")
                if action not in _ACTIONS_GLOSSARY:
                    raise DraftValueError(
                        f'Invalid action "{action}"; expected '
                        f"{'/'.join(_ACTIONS_GLOSSARY)}."
                    )
                src = _clean_str(item.get("src"), "src")
                row["src"] = src
                if action == "remove":
                    self._remove(src)
                else:
                    dst = _clean_str(item.get("dst"), "dst")
                    note = _clean_str(item.get("info"), "info", allow_empty=True)
                    row["dst"] = dst
                    self._upsert(src, dst, note)
                applied += 1
            except DraftConflict as exc:
                conflicts.append(exc.row)
            except DraftValueError as exc:
                errors.append({**row, "error": str(exc)})
        return {"applied": applied, "conflicts": conflicts, "errors": errors}

    def _upsert(self, src: str, dst: str, note: str) -> None:
        idx = self._index().get(src.casefold())
        if idx is None:
            self.entries.append(DraftEntry(src, dst, note, ORIGIN_AI))
            return
        current = self.entries[idx]
        if current.translation == dst and current.note == note:
            return  # 内容一致,无操作
        if current.origin in (ORIGIN_EXISTING, ORIGIN_USER):
            raise DraftConflict(
                f'Entry "{current.source}" is user-owned; proposed '
                f'"{dst}" differs from "{current.translation}". '
                "Resolve it manually in the draft table.",
                {"src": current.source, "current": current.translation,
                 "proposed": dst, "reason": "user_owned"},
            )
        # ai 条目:后续轮次可自由修正
        self.entries[idx] = DraftEntry(src, dst, note, ORIGIN_AI)

    def _remove(self, src: str) -> None:
        idx = self._index().get(src.casefold())
        if idx is None:
            raise DraftConflict(
                f'Entry "{src}" not found; nothing removed.',
                {"src": src, "reason": "not_found"},
            )
        current = self.entries[idx]
        if current.origin in (ORIGIN_EXISTING, ORIGIN_USER):
            raise DraftConflict(
                f'Entry "{current.source}" is user-owned and cannot be '
                "removed by the agent. Remove it manually if unwanted.",
                {"src": current.source, "reason": "user_owned"},
            )
        del self.entries[idx]

    def set_user_entry(self, source: str, translation: str, note: str) -> None:
        """UI 编辑入口:写入或就地更新,origin 升级为 user(受保护)。"""
        source = source.strip()
        translation = translation.strip()
        if not source or not translation:
            raise DraftValueError("Source and translation must not be empty.")
        idx = self._index().get(source.casefold())
        if idx is None:
            self.entries.append(
                DraftEntry(source, translation, note.strip(), ORIGIN_USER)
            )
        else:
            self.entries[idx] = DraftEntry(
                source, translation, note.strip(), ORIGIN_USER
            )

    def remove_user_entry(self, source: str) -> None:
        idx = self._index().get(source.strip().casefold())
        if idx is not None:
            del self.entries[idx]

    def snapshot(self) -> Tuple[DraftEntry, ...]:
        """UI 镜像同步用的只读快照。"""
        return tuple(replace(e) for e in self.entries)


@dataclass
class PageSummary:
    page_name: str
    summary: str
    origin: str = ORIGIN_EXISTING


class StoryDraft:
    """剧情草稿:页段摘要层 + 全局梗概层(格式对齐上游 vision_context)。"""

    def __init__(self, synopsis: str = "") -> None:
        self.page_summaries: Dict[str, PageSummary] = {}
        self.synopsis: str = synopsis
        self.synopsis_origin: str = (
            ORIGIN_EXISTING if synopsis.strip() else ORIGIN_AI
        )

    @classmethod
    def from_base(
        cls, page_summaries: Dict[str, str], synopsis: str = ""
    ) -> "StoryDraft":
        draft = cls(synopsis)
        for page_name, summary in page_summaries.items():
            if summary and summary.strip():
                draft.page_summaries[page_name] = PageSummary(
                    page_name, summary, ORIGIN_EXISTING
                )
        return draft

    def apply_patch(
        self, page_summaries: Any, synopsis: Any = None, page_resolver=None
    ) -> Dict[str, Any]:
        """应用一次 submit_story_patch。

        synopsis 为可选的全文替换(整段梗概,非增量);page_summaries 为
        [{action: set|remove, page: 页名或索引, summary}]。page_resolver
        可选,把页身份解析为页名(抛 DraftValueError 记为单条错误)。
        单条失败继续,不作废整轮。
        """
        if page_summaries is not None and not isinstance(page_summaries, list):
            raise DraftValueError('Field "page_summaries" must be an array.')
        applied = 0
        conflicts: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for i, item in enumerate(page_summaries or []):
            row: Dict[str, Any] = {"index": i}
            try:
                if not isinstance(item, dict):
                    raise DraftValueError("each item must be an object")
                action = item.get("action", "set")
                if action not in ("set", "remove"):
                    raise DraftValueError(
                        f'Invalid action "{action}"; expected set/remove.'
                    )
                page = self._clean_page(item.get("page"))
                if page_resolver is not None:
                    page = page_resolver(page)
                row["page"] = page
                if action == "remove":
                    self._remove_summary(page)
                else:
                    summary = _clean_str(item.get("summary"), "summary")
                    row["summary_head"] = summary[:60]
                    self._set_summary(page, summary)
                applied += 1
            except DraftConflict as exc:
                conflicts.append(exc.row)
            except DraftValueError as exc:
                errors.append({**row, "error": str(exc)})
        if synopsis is not None:
            if not isinstance(synopsis, str):
                errors.append({"field": "synopsis", "error": "must be a string"})
            else:
                synopsis = synopsis.strip()
                if synopsis and synopsis != self.synopsis:
                    if self.synopsis and self.synopsis_origin in (
                        ORIGIN_EXISTING,
                        ORIGIN_USER,
                    ):
                        conflicts.append(
                            {
                                "field": "synopsis",
                                "reason": "user_owned",
                                "current_head": self.synopsis[:60],
                                "proposed_head": synopsis[:60],
                            }
                        )
                    else:
                        self.synopsis = synopsis
                        self.synopsis_origin = ORIGIN_AI
                        applied += 1
        return {"applied": applied, "conflicts": conflicts, "errors": errors}

    @staticmethod
    def _clean_page(value: Any) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        return _clean_str(value, "page")

    def _set_summary(self, page: str, summary: str) -> None:
        current = self.page_summaries.get(page)
        if current is not None:
            if current.summary == summary:
                return
            if current.origin in (ORIGIN_EXISTING, ORIGIN_USER):
                raise DraftConflict(
                    f'Summary of page "{page}" is user-owned; resolve the '
                    "difference manually in the draft panel.",
                    {"page": page, "current_head": current.summary[:60],
                     "proposed_head": summary[:60], "reason": "user_owned"},
                )
        self.page_summaries[page] = PageSummary(page, summary, ORIGIN_AI)

    def _remove_summary(self, page: str) -> None:
        current = self.page_summaries.get(page)
        if current is None:
            raise DraftConflict(
                f'No summary for page "{page}"; nothing removed.',
                {"page": page, "reason": "not_found"},
            )
        if current.origin in (ORIGIN_EXISTING, ORIGIN_USER):
            raise DraftConflict(
                f'Summary of page "{page}" is user-owned and cannot be '
                "removed by the agent.",
                {"page": page, "reason": "user_owned"},
            )
        del self.page_summaries[page]

    def set_user_summary(self, page: str, summary: str) -> None:
        self.page_summaries[page] = PageSummary(page, summary, ORIGIN_USER)

    def set_user_synopsis(self, synopsis: str) -> None:
        self.synopsis = synopsis.strip()
        self.synopsis_origin = ORIGIN_USER

    def snapshot(self) -> Tuple[Tuple[PageSummary, ...], str]:
        return (
            tuple(replace(p) for p in self.page_summaries.values()),
            self.synopsis,
        )
