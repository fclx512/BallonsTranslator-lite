"""
AI Chat Translator — context-aware batch translation via LLM.

Extends LLM_API_Translator to reuse profile management, API key rotation,
rate limiting, and retry logic.  Overrides translate_textblk_lst() to build
context-rich prompts that include surrounding pages, a term glossary, and
(optionally) progressive summaries of earlier batches.

Context strategies
------------------
full
    Translate all pages (or batches of batch_size) in a single call.
    Best for short projects (1–20 pages).  The first page of a batch
    triggers a bulk call; subsequent pages in the same batch read from
    an in-memory cache.
sliding_window
    Per-page translation with a configurable window of neighbouring pages
    injected as context.  Already-translated pages show src→trans pairs;
    pending pages show source only.  Best for medium projects (10–50 pp).
progressive_summary
    Project is partitioned into batch_size groups.  Before translating a
    batch, all previous batches are compressed into a short summary
    (character names, key terms, page themes) + a term-consistency
    glossary.  Best for long projects (50–300+ pp).
"""

from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

from .base import register_translator
from .trans_llm_api import (
    LLM_API_Translator,
    SAMPLE_PROFILES,
    ProfileManagerDialog,
    TranslationResponse,
)

if TYPE_CHECKING:
    from utils.textblock import TextBlock
    from utils.proj_imgtrans import ProjImgTrans

# ── Pydantic models for context-aware responses ─────────────────────────

class CtxTranslationElement(BaseModel):
    id: str = Field(..., description="Block ID in 'page_idx:block_idx' format")
    translation: str = Field(..., description="The translated text")


class CtxTranslationResponse(BaseModel):
    translations: List[CtxTranslationElement]


# ── System prompt ──────────────────────────────────────────────────────

CONTEXT_SYSTEM_PROMPT = (
    "You are a professional manga translator from {source} to {target}.\n"
    "Your translations should:\n"
    "- Accurately convey the original meaning while preserving character voice and tone\n"
    "- Sound natural in {target}\n"
    "- Keep terminology consistent — use the same translation for the same term across all pages\n"
    "- Localize sound effects and onomatopoeia appropriately\n"
    "- Pay attention to context blocks for dialogue flow and character relationships\n\n"
    "{glossary_section}"
    "Output format: strict JSON.\n"
    '{{"translations": [{{"id": "page:block", "translation": "..."}}, ...]}}\n'
    "Return translations for ALL blocks listed in the === TASK === section."
)

# ── Glossary size cap ──────────────────────────────────────────────────

MAX_GLOSSARY_ENTRIES = 50
MAX_SUMMARY_CHARS = 800


@register_translator("AI_Chat_Translator")
class AI_Chat_Translator(LLM_API_Translator):
    """Context-aware translator that sees full pages instead of isolated strings."""

    # Extend parent params with context-management options
    _PARENT_PARAMS = {
        k: v for k, v in LLM_API_Translator.params.items()
        if k != "description"
    }

    params = {
        **_PARENT_PARAMS,
        "batch_size": {
            "type": "selector",
            "options": ["1", "3", "5", "10", "20"],
            "value": "5",
            "description": "Pages per translation API call",
        },
        "context_strategy": {
            "type": "selector",
            "options": ["full", "sliding_window", "progressive_summary"],
            "value": "full",
            "description": (
                "full: all pages in one call (short projects). "
                "sliding_window: N surrounding pages as context (medium). "
                "progressive_summary: batch with summaries of earlier pages (long)."
            ),
        },
        "context_pages": {
            "value": 3,
            "description": "For sliding_window: number of surrounding pages per side",
        },
        "use_glossary": {
            "type": "checkbox",
            "value": True,
            "description": "Track recurring terms and enforce translation consistency",
        },
        "custom_trans_prompt": {
            "value": "",
            "description": (
                "Additional translation guidelines appended to the system prompt "
                "(e.g. character personalities, style preferences)"
            ),
        },
        "description": (
            "Context-aware AI translator. Unlike standard translators that see "
            "isolated text snippets, this one reads full pages with surrounding "
            "context, enabling consistent character voices and term disambiguation. "
            "Configure context strategy for different project sizes."
        ),
    }

    # ── Setup ──────────────────────────────────────────────────────────

    def _setup_translator(self):
        super()._setup_translator()
        self._proj: Optional["ProjImgTrans"] = None
        self._blklist_to_pagekey: Dict[int, str] = {}
        self._cached_translations: Dict[str, Dict[int, str]] = {}
        self._term_glossary: Dict[str, str] = {}
        self._page_summaries: Dict[int, str] = {}
        self._completed_page_keys: Set[str] = set()

    def is_computational_intensive(self) -> bool:
        """Force synchronous pipeline path so pages are processed in order."""
        return True

    # ── Pipeline hooks ─────────────────────────────────────────────────

    def set_project(self, proj: "ProjImgTrans"):
        """Called by the pipeline before the page loop starts."""
        self._proj = proj
        self._blklist_to_pagekey.clear()
        self._cached_translations.clear()
        self._term_glossary.clear()
        self._page_summaries.clear()
        self._completed_page_keys.clear()

        # Pre-build the identity map for fast page-key resolution
        for pname, blklist in proj.pages.items():
            self._blklist_to_pagekey[id(blklist)] = pname

    def finalize(self):
        """Called by the pipeline after all pages have been processed."""
        self._proj = None
        self._blklist_to_pagekey.clear()
        self._cached_translations.clear()
        self._term_glossary.clear()
        self._page_summaries.clear()
        self._completed_page_keys.clear()

    # ── Override: context-aware translation ────────────────────────────

    def translate_textblk_lst(self, textblk_lst: List["TextBlock"]):
        """Context-aware batch translation entry point."""
        # Initialize translations list (same pattern as BaseTranslator)
        non_empty_ids = []
        text_list = []
        translations = []
        for ii, blk in enumerate(textblk_lst):
            text = blk.get_text()
            if text.strip() != '':
                non_empty_ids.append(ii)
                text_list.append(text)
            translations.append(text)

        # Preprocess hooks (inherited)
        for _cb_name, callback in self._preprocess_hooks.items():
            callback(translations=translations, textblocks=textblk_lst,
                     translator=self, source_text=text_list)

        if text_list:
            page_key = self._resolve_page_key(textblk_lst)
            if page_key is not None and self._proj is not None:
                _translations = self._contextual_translate(
                    text_list, textblk_lst, page_key, non_empty_ids
                )
            else:
                # Fallback: standard per-page translation (no context)
                _translations = self.translate(text_list)
            for ii, idx in enumerate(non_empty_ids):
                translations[idx] = _translations[ii]

        # Postprocess hooks (inherited — e.g. chs2cht for traditional Chinese)
        for _cb_name, callback in self._postprocess_hooks.items():
            callback(translations=translations, textblocks=textblk_lst,
                     translator=self)

        for tr, blk in zip(translations, textblk_lst):
            blk.translation = tr

    # ── Page resolution ────────────────────────────────────────────────

    def _resolve_page_key(self, textblk_lst: List["TextBlock"]) -> Optional[str]:
        """Find which page a blk_list belongs to (identity match + content fallback)."""
        list_id = id(textblk_lst)
        if list_id in self._blklist_to_pagekey:
            return self._blklist_to_pagekey[list_id]
        if self._proj is None:
            return None
        # Content fallback: match by first block's text
        if textblk_lst:
            first_text = textblk_lst[0].get_text()
            for pname, blklist in self._proj.pages.items():
                if blklist and blklist[0].get_text() == first_text:
                    self._blklist_to_pagekey[list_id] = pname
                    return pname
        return None

    def _page_index(self, page_key: str) -> int:
        """Return the positional index of *page_key* in the project."""
        for i, pname in enumerate(self._proj.pages.keys()):
            if pname == page_key:
                return i
        return -1

    # ── Core: context-aware translation ────────────────────────────────

    def _contextual_translate(
        self,
        text_list: List[str],
        textblk_lst: List["TextBlock"],
        page_key: str,
        non_empty_ids: List[int],
    ) -> List[str]:
        """Translate one page's blocks with full context awareness."""
        strategy = self.get_param_value("context_strategy")
        batch_size = int(self.get_param_value("batch_size"))
        page_idx = self._page_index(page_key)
        all_page_keys = list(self._proj.pages.keys())
        total_pages = len(all_page_keys)

        # ── Determine which pages to translate in this call ──
        if strategy == "full":
            if total_pages <= batch_size:
                batch_keys = all_page_keys  # translate everything at once
                trigger_pages = {all_page_keys[0]}
            else:
                # Chunk into batch_size groups; first page of each group triggers
                batch_start = (page_idx // batch_size) * batch_size
                batch_end = min(batch_start + batch_size, total_pages)
                batch_keys = all_page_keys[batch_start:batch_end]
                trigger_pages = {batch_keys[0]}
        elif strategy == "progressive_summary":
            batch_start = (page_idx // batch_size) * batch_size
            batch_end = min(batch_start + batch_size, total_pages)
            batch_keys = all_page_keys[batch_start:batch_end]
            trigger_pages = {batch_keys[0]}
        else:  # sliding_window
            batch_keys = [page_key]
            trigger_pages = {page_key}

        # ── Check cache ──
        if page_key in self._cached_translations:
            return self._apply_cached(textblk_lst, non_empty_ids,
                                      self._cached_translations[page_key])

        # Only the trigger page actually makes the API call
        if page_key not in trigger_pages:
            # Should not normally happen in synchronous mode, but guard
            return self.translate(text_list)

        # ── Build context ──
        context_pages = self._build_context(page_key, batch_keys, strategy, all_page_keys)

        # ── Build messages & call LLM ──
        target_blocks = self._collect_target_blocks(batch_keys)
        messages = self._build_translation_messages(context_pages, target_blocks)

        raw_translations = self._call_llm_with_retry(messages, len(target_blocks))

        # ── Cache results for all pages in the batch ──
        self._cache_batch_results(batch_keys, target_blocks, raw_translations)

        # ── Update state ──
        for key in batch_keys:
            self._completed_page_keys.add(key)

        if self.get_param_value("use_glossary"):
            self._update_glossary(textblk_lst, raw_translations)

        if strategy == "progressive_summary":
            self._update_page_summary(batch_start, batch_end, batch_keys)

        # ── Return translations for the current page ──
        return self._apply_cached(textblk_lst, non_empty_ids,
                                  self._cached_translations[page_key])

    def _apply_cached(
        self, textblk_lst: List["TextBlock"],
        non_empty_ids: List[int], cache: Dict[int, str],
    ) -> List[str]:
        """Return translations from cache, ordered by non_empty_ids."""
        result = []
        for idx in non_empty_ids:
            result.append(cache.get(idx, textblk_lst[idx].get_text()))
        return result

    # ── Context building ───────────────────────────────────────────────

    def _build_context(
        self, page_key: str, batch_keys: List[str],
        strategy: str, all_page_keys: List[str],
    ) -> List[dict]:
        """Build a list of context page descriptors for the prompt."""
        context_window = int(self.get_param_value("context_pages"))
        page_idx = self._page_index(page_key)
        context = []

        if strategy == "sliding_window":
            start = max(0, page_idx - context_window)
            end = min(len(all_page_keys), page_idx + context_window + 1)
            context_indices = list(range(start, end))
        elif strategy == "full":
            # Context = all pages before the current batch
            batch_start = self._page_index(batch_keys[0])
            context_indices = list(range(0, batch_start))
        elif strategy == "progressive_summary":
            # Progressive summary compresses context into summaries — no raw pages
            context_indices = []
        else:
            context_indices = []

        for ci in context_indices:
            if ci == page_idx:
                continue
            if all_page_keys[ci] in batch_keys:
                continue  # target pages are not context
            context.append(self._describe_page(all_page_keys[ci], ci))

        # For progressive_summary: prepend summaries of previous batches
        if strategy == "progressive_summary" and self._page_summaries:
            summary_text = "\n".join(
                f"  Batch {k}: {v}"
                for k, v in sorted(self._page_summaries.items())
            )
            if summary_text:
                context.insert(0, {
                    "type": "summary",
                    "text": (
                        "=== SUMMARY OF PREVIOUS PAGES ===\n"
                        f"{summary_text}"
                    ),
                })

        return context

    def _describe_page(self, pname: str, pidx: int) -> dict:
        """Build a compact descriptor for one context page."""
        blklist = self._proj.pages[pname]
        blocks = []
        translated = pname in self._completed_page_keys

        for bidx, blk in enumerate(blklist):
            src = blk.get_text()
            if not src.strip():
                continue
            entry = {"id": f"{pidx}:{bidx}", "src": src}
            if translated and blk.translation:
                entry["trans"] = blk.translation
            blocks.append(entry)

        return {
            "pidx": pidx,
            "name": pname,
            "translated": translated,
            "blocks": blocks,
        }

    def _collect_target_blocks(self, batch_keys: List[str]) -> List[dict]:
        """Collect all blocks that need translation in this batch."""
        result = []
        for pname in batch_keys:
            pidx = self._page_index(pname)
            blklist = self._proj.pages[pname]
            for bidx, blk in enumerate(blklist):
                src = blk.get_text()
                if not src.strip():
                    continue
                result.append({
                    "id": f"{pidx}:{bidx}",
                    "pidx": pidx,
                    "bidx": bidx,
                    "src": src,
                })
        return result

    # ── Prompt assembly ────────────────────────────────────────────────

    def _build_translation_messages(
        self, context_pages: List[dict], target_blocks: List[dict],
    ) -> List[dict]:
        """Build the full messages list (system + user) for the LLM call."""
        source = self.lang_source
        target = self.lang_target

        # ── System prompt ──
        glossary_section = ""
        if self.get_param_value("use_glossary") and self._term_glossary:
            lines = ["Term consistency guide (use these translations for recurring terms):"]
            for src_term, tgt_term in list(self._term_glossary.items())[:MAX_GLOSSARY_ENTRIES]:
                lines.append(f'  "{src_term}" → "{tgt_term}"')
            lines.append("")
            glossary_section = "\n".join(lines) + "\n"

        system = CONTEXT_SYSTEM_PROMPT.format(
            source=source, target=target,
            glossary_section=glossary_section,
        )

        custom = self.get_param_value("custom_trans_prompt")
        if custom:
            system += f"\n\nAdditional guidelines:\n{custom}"

        messages = [{"role": "system", "content": system}]

        # ── User prompt ──
        parts = []

        if context_pages:
            parts.append("=== CONTEXT (nearby pages for reference) ===")
            for cp in context_pages:
                if cp.get("type") == "summary":
                    parts.append(cp["text"])
                    continue

                status = "[translated]" if cp.get("translated") else "[pending]"
                parts.append(f"\nPage {cp['pidx']} \"{cp['name']}\" {status}:")
                for blk in cp["blocks"]:
                    line = f"  [{blk['id']}] \"{blk['src']}\""
                    if blk.get("trans"):
                        line += f" → \"{blk['trans']}\""
                    parts.append(line)

        parts.append("\n=== TASK: Translate the following blocks ===")
        for blk in target_blocks:
            parts.append(f"  [{blk['id']}] \"{blk['src']}\"")

        parts.append(
            f"\nRespond with JSON: "
            f'{{"translations": [{{"id": "pidx:bidx", "translation": "..."}}, ...]}}'
        )

        messages.append({"role": "user", "content": "\n".join(parts)})
        return messages

    # ── LLM API call ───────────────────────────────────────────────────

    def _call_llm_with_retry(
        self, messages: List[dict], expected_count: int,
    ) -> Dict[str, str]:
        """Call the LLM, parse response, retry on failure. Returns {block_id: translation}."""
        RETRYABLE_EXCEPTIONS = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            openai.APIStatusError,
            httpx.RequestError,
        )

        mismatch_retry = 0
        api_retry = 0
        max_mismatch = int(self.get_param_value("invalid_repeat_count"))
        max_api_retry = int(self.get_param_value("retry_attempts"))
        retry_timeout = int(self.get_param_value("retry_timeout"))

        while True:
            try:
                api_key = self._select_api_key()
                if not api_key:
                    raise ConnectionError("No available API key.")

                if not self.client or self.client.api_key != api_key:
                    if not self._initialize_client(api_key):
                        raise ConnectionError("Failed to initialize API client.")

                self._respect_delay()

                api_args = {
                    "model": self._effective_model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                }
                if self.max_tokens is not None:
                    api_args["max_tokens"] = self.max_tokens

                # Use json_schema for structured output when supported
                profile = self._active_profile
                rf = profile.get("response_format", "")
                if rf == "json_schema":
                    api_args["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"schema": CtxTranslationResponse.model_json_schema()},
                    }
                elif self._is_local_endpoint:
                    api_args["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"schema": CtxTranslationResponse.model_json_schema()},
                    }
                else:
                    api_args["response_format"] = {"type": "json_object"}

                fp = profile.get("frequency_penalty")
                if fp is not None:
                    api_args["frequency_penalty"] = float(fp)
                pp = profile.get("presence_penalty")
                if pp is not None:
                    api_args["presence_penalty"] = float(pp)

                completion = self.client.chat.completions.create(**api_args)

                if completion.usage:
                    self.token_count += completion.usage.total_tokens
                    self.token_count_last = completion.usage.total_tokens
                else:
                    self.token_count_last = 0

                raw = (
                    completion.choices[0].message.content
                    if completion.choices and completion.choices[0].message
                    else ""
                )
                if not raw:
                    raise ValueError("Empty API response")

                # Parse JSON from response
                json_str = raw.strip()
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_str, re.DOTALL)
                if match:
                    json_str = match.group(1)
                else:
                    start = json_str.find("{")
                    end = json_str.rfind("}")
                    if start != -1 and end > start:
                        json_str = json_str[start:end + 1]

                data = json.loads(json_str)

                # Try to validate with our model
                try:
                    validated = CtxTranslationResponse.model_validate(data)
                except ValidationError:
                    # Fallback: try integer-keyed format and convert
                    if "translations" in data:
                        validated = CtxTranslationResponse.model_validate(data)
                    elif isinstance(data, dict):
                        # Try the parent's TranslationResponse (integer IDs)
                        try:
                            legacy = TranslationResponse.model_validate(data)
                            items = [
                                CtxTranslationElement(
                                    id=str(item.id), translation=item.translation
                                )
                                for item in legacy.translations
                            ]
                            validated = CtxTranslationResponse(translations=items)
                        except ValidationError:
                            # Last resort: simple dict of id→translation
                            items = []
                            for k, v in data.items():
                                if k == "translations":
                                    continue
                                if isinstance(v, str):
                                    try:
                                        inner = json.loads(v)
                                        if "translations" in inner:
                                            for t in inner["translations"]:
                                                items.append(CtxTranslationElement(
                                                    id=str(t.get("id", "")),
                                                    translation=t.get("translation", ""),
                                                ))
                                    except (json.JSONDecodeError, TypeError):
                                        items.append(CtxTranslationElement(
                                            id=k, translation=v,
                                        ))
                            if items:
                                validated = CtxTranslationResponse(translations=items)
                            else:
                                raise
                if not validated or not validated.translations:
                    raise ValueError("No translations in response")
                if len(validated.translations) != expected_count:
                    raise ValueError(
                        f"Expected {expected_count} translations, "
                        f"got {len(validated.translations)}"
                    )

                return {
                    item.id: item.translation
                    for item in validated.translations
                }

            except ValueError as e:
                mismatch_retry += 1
                self.logger.warning(
                    f"Translation mismatch: {e}. "
                    f"Attempt {mismatch_retry}/{max_mismatch}."
                )
                if mismatch_retry >= max_mismatch:
                    self.logger.error("Failed to get correct translation structure.")
                    return {}
                time.sleep(retry_timeout / 2)

            except RETRYABLE_EXCEPTIONS as e:
                api_retry += 1
                self.logger.warning(
                    f"API Error ({type(e).__name__}): {e}. "
                    f"Attempt {api_retry}/{max_api_retry}."
                )
                if api_retry >= max_api_retry:
                    self.logger.error(f"Failed after {max_api_retry} attempts.")
                    return {}
                time.sleep(retry_timeout)

    # ── Cache management ───────────────────────────────────────────────

    def _cache_batch_results(
        self, batch_keys: List[str],
        target_blocks: List[dict], translations: Dict[str, str],
    ):
        """Distribute API results into per-page caches."""
        for pname in batch_keys:
            self._cached_translations[pname] = {}

        for blk in target_blocks:
            bid = blk["id"]
            pidx = blk["pidx"]
            bidx = blk["bidx"]
            pname = list(self._proj.pages.keys())[pidx]
            if bid in translations:
                self._cached_translations[pname][bidx] = translations[bid]
            else:
                self._cached_translations[pname][bidx] = blk["src"]

    # ── Glossary ───────────────────────────────────────────────────────

    def _update_glossary(
        self, textblk_lst: List["TextBlock"],
        translations: Dict[str, str],
    ):
        """Extract recurring source→target pairs for term consistency."""
        for blk in textblk_lst:
            src = blk.get_text().strip()
            if not src or len(src) < 2 or len(src) > 30:
                continue
            # Find the translation for this block
            tr = blk.translation
            if not tr or tr == src:
                continue
            # Only add if the source term appears in the project more than once
            if self._count_source_occurrences(src) >= 2:
                self._term_glossary[src] = tr

        # Prune to max size
        if len(self._term_glossary) > MAX_GLOSSARY_ENTRIES:
            # Keep most recent entries
            items = list(self._term_glossary.items())
            self._term_glossary = dict(items[-MAX_GLOSSARY_ENTRIES:])

    def _count_source_occurrences(self, text: str) -> int:
        """Count how many times *text* appears as source in the project."""
        if self._proj is None:
            return 1
        count = 0
        for blklist in self._proj.pages.values():
            for blk in blklist:
                if blk.get_text().strip() == text:
                    count += 1
        return count

    # ── Progressive summary ────────────────────────────────────────────

    def _update_page_summary(
        self, batch_start: int, batch_end: int, batch_keys: List[str],
    ):
        """Build a compact summary of the just-translated batch."""
        chars = []
        terms = []

        for pname in batch_keys:
            blklist = self._proj.pages[pname]
            for blk in blklist:
                tr = blk.translation
                if tr and tr.strip():
                    chars.append(tr)

        # Extract potential proper nouns / key terms
        seen = set()
        for text in chars:
            # Simple heuristic: capitalized words in target, or 2-4 char CJK terms
            for word in text.split():
                word = word.strip(",.!?;:()[]\"'")
                if len(word) >= 2 and word not in seen:
                    seen.add(word)

        # Build summary card
        summary_parts = [f"Pages {batch_start}-{batch_end - 1}:"]
        if self._term_glossary:
            recent_terms = list(self._term_glossary.items())[-10:]
            terms_str = ", ".join(
                f"{s}→{t}" for s, t in recent_terms
            )
            summary_parts.append(f"  Terms: {terms_str}")

        summary = " ".join(summary_parts)
        self._page_summaries[batch_start] = summary[:MAX_SUMMARY_CHARS]

    # ── updateParam override ───────────────────────────────────────────

    def updateParam(self, param_key: str, param_content):
        if param_key == "manage_profiles":
            self._open_profile_manager()
            self._refresh_active_profile_options()
            return
        super().updateParam(param_key, param_content)
        if param_key == "active_profile":
            self.client = None
        if param_key == "proxy":
            self.client = None
        if param_key in ["max_requests_per_minute", "delay"]:
            self.request_count_minute = 0
            self.minute_start_time = time.time()
            self.last_request_time = 0
