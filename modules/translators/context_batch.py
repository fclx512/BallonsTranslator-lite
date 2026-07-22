"""
ContextBatchTranslator — lightweight batch translator, config sourced from
the currently selected translator's active profile.

Created on-the-fly by the Run dialog when "Context Translation (beta)" is enabled.
Not registered as a translator module. Uses openai.OpenAI directly with the
same api_host/api_key/model/temperature/prompt_template as the user's current
LLM_API_Translator profile. No separate profile management needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from utils.proj_imgtrans import ProjImgTrans
    from utils.textblock import TextBlock

from modules.context.glossary import (
    GlossaryEntry,
    GlossaryError,
    load_glossary,
)
from utils.config import pcfg

logger = logging.getLogger("context_batch")

# ── Pydantic models ─────────────────────────────────────────────────────


class CtxElement(BaseModel):
    id: str = Field(..., description="Block ID in 'page_idx:block_idx' format")
    translation: str = Field(..., description="The translated text")


class CtxResponse(BaseModel):
    translations: List[CtxElement]


MAX_GLOSSARY = 50
MAX_SUMMARY = 800


class ContextBatchTranslator:
    """Standalone context-aware batch translator.

    Implements the minimal interface the pipeline needs (translate_textblk_lst,
    set_project, finalize) while calling the LLM with API config sourced from
    the currently selected translator's active profile.

    Context strategy is automatic based on project size:
    - short  (<= batch_size pages):      full context (all prior pages)
    - medium (<= batch_size * 4 pages):  windowed context (+/- context_pages)
    - long   (>  batch_size * 4 pages):  windowed context + auto-summaries
    """

    def __init__(
        self,
        api_config: dict,
        translation_prompt: str = "",
        status_callback=None,
        glossary_path: str = "",
        glossary_mode: str = "matching",
        custom_glossary_text: str = "",
    ):
        self.api_config = dict(api_config)
        self.translation_prompt = translation_prompt
        self._status_cb = status_callback
        self._glossary_path = glossary_path
        self._glossary_mode = glossary_mode
        self._custom_glossary_text = custom_glossary_text

        # Runtime state (reset per project via set_project)
        self._proj: Optional["ProjImgTrans"] = None
        self._blklist_to_pagekey: Dict[int, str] = {}
        self._cached: Dict[str, Dict[int, str]] = {}
        self._file_glossary_entries: Tuple[GlossaryEntry, ...] = ()
        self._custom_glossary_entries: Tuple[GlossaryEntry, ...] = ()
        self._glossary: Dict[str, str] = {}
        self._summaries: Dict[int, str] = {}
        self._completed: Set[str] = set()
        self._batch_boundaries: List[Tuple[int, int]] = []

        # Run-time params (set by Run dialog)
        self.batch_size = 5
        self.context_pages = 3
        self.use_glossary = True
        self.max_retries = 3
        self.retry_parse_sleep = 2
        self.retry_api_sleep = 5

    # ── Pipeline interface ────────────────────────────────────────────

    @property
    def low_vram_mode(self) -> bool:
        return False

    def is_computational_intensive(self) -> bool:
        return True  # force synchronous path so pages come in order

    def set_project(self, proj: "ProjImgTrans"):
        self._proj = proj
        self._blklist_to_pagekey.clear()
        self._cached.clear()
        self._file_glossary_entries = ()
        self._glossary.clear()
        self._summaries.clear()
        self._completed.clear()
        for pname, blklist in proj.pages.items():
            self._blklist_to_pagekey[id(blklist)] = pname
        self._batch_boundaries.clear()
        self._auto_configure()

        # Load file-based glossary
        if self._glossary_path and self.use_glossary:
            try:
                self._file_glossary_entries = load_glossary(self._glossary_path)
                if self._file_glossary_entries:
                    self._status(
                        f"Glossary loaded: {len(self._file_glossary_entries)} "
                        f"entries from {os.path.basename(self._glossary_path)}"
                    )
            except GlossaryError as e:
                self._status(f"Glossary error: {e}")
                self._file_glossary_entries = ()

        # Generate AI glossary from user's custom text
        if self._custom_glossary_text and self.use_glossary:
            try:
                self._custom_glossary_entries = self._generate_custom_glossary(
                    self._custom_glossary_text
                )
                if self._custom_glossary_entries:
                    self._status(
                        f"AI glossary generated: {len(self._custom_glossary_entries)} "
                        f"entries from custom input"
                    )
            except Exception as e:
                self._status(f"AI glossary generation error: {e}")
                self._custom_glossary_entries = ()

    def finalize(self):
        self._proj = None
        self._blklist_to_pagekey.clear()
        self._cached.clear()
        self._file_glossary_entries = ()
        self._custom_glossary_entries = ()
        self._glossary.clear()
        self._summaries.clear()
        self._completed.clear()
        self._batch_boundaries.clear()

    def translate_textblk_lst(self, textblk_lst: List["TextBlock"], **kwargs):
        non_empty = []
        text_list = []
        translations = []
        for ii, blk in enumerate(textblk_lst):
            text = blk.get_text()
            if text.strip():
                non_empty.append(ii)
                text_list.append(text)
            translations.append(text)

        if not text_list:
            for tr, blk in zip(translations, textblk_lst):
                blk.translation = tr
            return

        page_key = self._resolve_page(textblk_lst)
        if page_key is not None and self._proj is not None:
            result = self._contextual(text_list, textblk_lst, page_key, non_empty)
        else:
            result = self._direct_call(text_list)

        for ii, idx in enumerate(non_empty):
            translations[idx] = result[ii]
        for tr, blk in zip(translations, textblk_lst):
            # Final safeguard: ensure no stray \n reaches the canvas
            tr = tr.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            tr = tr.replace("\u2028", " ").replace("\u2029", " ").replace("\u0085", " ")
            blk.translation = tr

    def _status(self, msg: str):
        logger.info(msg)
        if self._status_cb:
            self._status_cb(msg)

    # ── Page resolution ────────────────────────────────────────────────

    def _resolve_page(self, blk_list: List["TextBlock"]) -> Optional[str]:
        lid = id(blk_list)
        if lid in self._blklist_to_pagekey:
            return self._blklist_to_pagekey[lid]
        if self._proj is None:
            return None
        if blk_list:
            first = blk_list[0].get_text()
            for pname, blist in self._proj.pages.items():
                if blist and blist[0].get_text() == first:
                    self._blklist_to_pagekey[lid] = pname
                    return pname
        return None

    def _page_idx(self, key: str) -> int:
        if self._proj is None:
            return -1
        for i, p in enumerate(self._proj.pages.keys()):
            if p == key:
                return i
        return -1

    # ── Core contextual translate ─────────────────────────────────────

    def _contextual(self, text_list, blk_list, page_key, non_empty):
        pi = self._page_idx(page_key)
        if self._proj is None:
            return self._direct_call(text_list)
        all_keys = list(self._proj.pages.keys())

        # Resolve batch from pre-computed boundaries (auto-configured)
        batch_keys = None
        batch_idx = 0
        for bi, (s, e) in enumerate(self._batch_boundaries):
            if s <= pi < e:
                batch_keys = all_keys[s:e]
                batch_idx = bi
                break
        if batch_keys is None:
            batch_keys = [all_keys[pi]]
        triggers = {batch_keys[0]}
        total_batches = len(self._batch_boundaries)

        # Cache hit
        if page_key in self._cached:
            return self._apply_cache(blk_list, non_empty, self._cached[page_key])

        if page_key not in triggers:
            return self._direct_call(text_list)

        ctx = self._build_ctx(page_key, batch_keys, all_keys)
        target = self._collect_target(batch_keys)

        ctx_pages = len([c for c in ctx if c.get("type") != "summary"])
        ctx_blks = sum(len(c.get("blocks", [])) for c in ctx if "blocks" in c)
        use_summary = total_batches > 4 and self._summaries
        mode = "summary" if use_summary else ("full" if total_batches <= 1 else "auto")
        self._status(
            "────────────────────────────────────────"
        )
        self._status(
            f"Batch {batch_idx + 1}/{total_batches} · "
            f"Pages {batch_keys[0]}-{batch_keys[-1]} · {mode}"
        )
        self._status(
            f"Context: {ctx_pages} pages, {ctx_blks} ref blocks"
        )
        self._status(
            f"→ translating {len(target)} blocks"
        )

        messages = self._build_msgs(ctx, target)

        t0 = time.time()
        raw = self._llm_call(
            messages, len(target), parser="txt", batch_keys=batch_keys
        )
        self._status(f"LLM done: {len(target)} translations in {time.time() - t0:.1f}s")

        # Log per-block results
        elapsed = time.time() - t0
        for blk in target:
            tr_text = raw.get(blk["id"], blk["src"])
            self._status(f'  [{blk["id"]}] "{blk["src"]}" → "{tr_text}"')
        self._status(f"✓ {elapsed:.1f}s, {len(target)} blocks translated")

        # Cache batch results
        for pname in batch_keys:
            self._cached[pname] = {}
        for blk in target:
            bid = blk["id"]
            pidx = blk["pidx"]
            bidx = blk["bidx"]
            pname = list(self._proj.pages.keys())[pidx]
            self._cached[pname][bidx] = raw.get(bid, blk["src"])

        for key in batch_keys:
            self._completed.add(key)

        if self.use_glossary:
            self._update_glossary(blk_list, raw)
        if total_batches > 4:
            start_idx = self._page_idx(batch_keys[0])
            end_idx = self._page_idx(batch_keys[-1]) + 1
            self._update_summary(start_idx, end_idx, batch_keys)

        return self._apply_cache(blk_list, non_empty, self._cached[page_key])

    def _apply_cache(self, blk_list, non_empty, cache):
        if self._proj is not None:
            page_key = self._resolve_page(blk_list)
            if page_key in self._proj.pages:
                id_to_bidx = {
                    id(b): i for i, b in enumerate(self._proj.pages[page_key])
                }
                return [
                    cache.get(id_to_bidx.get(id(blk_list[idx]), idx),
                              blk_list[idx].get_text())
                    for idx in non_empty
                ]
        return [cache.get(idx, blk_list[idx].get_text()) for idx in non_empty]

    # ── Auto configuration ──────────────────────────────────────────────

    def _auto_configure(self):
        """Auto-determine batch organization based on page content density.

        Groups consecutive pages into batches where each batch stays within
        a character budget (~4000 CJK chars ≈ ~2000 tokens). Never splits a
        single page across batches. Context window is sized proportionally.
        """
        if self._proj is None:
            return

        MAX_CHARS_PER_BATCH = 4000  # ~2000 CJK tokens
        all_keys = list(self._proj.pages.keys())
        total = len(all_keys)
        if total == 0:
            return

        # Estimate chars per page from source text
        page_chars = []
        for pname in all_keys:
            blks = self._proj.pages[pname]
            chars = sum(
                len(b.get_text().strip())
                for b in blks
                if b.get_text().strip()
            )
            page_chars.append(chars)

        total_chars = sum(page_chars)

        # Compute batch boundaries — never split a page
        boundaries = []
        start = 0
        while start < total:
            batch_chars = 0
            end = start
            while end < total:
                next_chars = page_chars[end]
                if batch_chars + next_chars > MAX_CHARS_PER_BATCH and end > start:
                    break
                batch_chars += next_chars
                end += 1
            boundaries.append((start, end))
            start = end
        self._batch_boundaries = boundaries

        # Auto context pages: budget ~2000 chars for reference context
        avg_chars = total_chars / total if total else 0
        if avg_chars > 0:
            self.context_pages = max(1, int(2000 / avg_chars))
        else:
            self.context_pages = 3
        self.context_pages = min(self.context_pages, 10)

        self._status(
            f"Auto-config: {len(boundaries)} batch(es), "
            f"{total_chars} chars across {total} pages, "
            f"context ±{self.context_pages} pages"
        )

    # ── Context building ──────────────────────────────────────────────

    def _build_ctx(self, page_key, batch_keys, all_keys):
        ctx_win = self.context_pages
        pi = self._page_idx(page_key)
        total = len(all_keys)
        ctx = []

        # Window context: pages within +/-ctx_win of current page
        indices = range(max(0, pi - ctx_win), min(total, pi + ctx_win + 1))
        for ci in indices:
            if ci == pi or all_keys[ci] in batch_keys:
                continue
            ctx.append(self._describe(all_keys[ci], ci))

        # Summaries for completed batches (only for long projects)
        if len(self._batch_boundaries) > 4 and self._summaries:
            lines = "\n".join(
                f"  Batch {k}: {v}" for k, v in sorted(self._summaries.items())
            )
            if lines:
                ctx.insert(
                    0,
                    {
                        "type": "summary",
                        "text": "=== SUMMARY OF PREVIOUS PAGES ===\n" + lines,
                    },
                )

        return ctx

    def _describe(self, pname, pidx):
        if self._proj is None:
            return {"pidx": pidx, "name": pname, "translated": False, "blocks": []}
        blks = self._proj.pages[pname]
        entries = []
        done = pname in self._completed
        for bidx, blk in enumerate(blks):
            src = blk.get_text()
            if not src.strip():
                continue
            e = {"id": f"{pidx}:{bidx}", "src": src}
            if done and blk.translation:
                # Clean \n from existing translations when building context.
                # Normal translator does NOT sanitize \n, so previous runs may
                # have left them. Passing raw \n in TXT format context corrupts
                # the format and encourages the LLM to produce \n in output.
                e["trans"] = (
                    blk.translation.replace("\r\n", " ")
                    .replace("\n", " ")
                    .replace("\r", " ")
                )
            entries.append(e)
        return {
            "pidx": pidx,
            "name": pname,
            "translated": done,
            "blocks": entries,
        }

    def _collect_target(self, batch_keys):
        if self._proj is None:
            return []
        result = []
        for pname in batch_keys:
            pi = self._page_idx(pname)
            for bidx, blk in enumerate(self._proj.pages[pname]):
                src = blk.get_text()
                if src.strip():
                    result.append(
                        {
                            "id": f"{pi}:{bidx}",
                            "pidx": pi,
                            "bidx": bidx,
                            "pname": pname,
                            "src": src,
                        }
                    )
        return result

        # ── Message assembly ──────────────────────────────────────────────

    def _build_msgs(self, ctx_pages, target_blocks):
        source = pcfg.module.translate_source if hasattr(pcfg, "module") else "auto"
        target = pcfg.module.translate_target if hasattr(pcfg, "module") else "auto"

        # System prompt — 3-step chain-of-thought methodology
        sys_prompt = (
            f"You are a professional manga/comic translator "
            f"translating from {source} to {target}.\n\n"
            f"### 3-Step Translation Process (internal, for each block):\n\n"
            f"Step 1 — Literal translation:\n"
            f"  Translate each block literally in your mind first, preserving "
            f"all original markers, numbers, placeholders, and special characters.\n\n"
            f"Step 2 — Context-aware correction:\n"
            f"  Review each literal translation considering:\n"
            f"  • Context from nearby pages (character names, terminology, "
            f"dialogue tone)\n"
            f"  • Reading order — adjacent blocks must flow as natural dialogue\n"
            f"  • Character voice and personality consistency\n"
            f"  • Semantic accuracy within the scene\n\n"
            f"Step 3 — Final polishing:\n"
            f"  Polish into natural, idiomatic {target} dialogue. Sound effects "
            f"localized appropriately. Character names and terms consistent "
            f"with CONTEXT.\n\n"
            f"### Rules\n"
            f"- Blocks are in READING ORDER. Adjacent blocks must read "
            f"naturally in sequence — "
            f"flow like continuous dialogue or narration.\n"
            f"- Do NOT include any line breaks inside a translation.\n"
            f"- Keep character names and terms consistent with CONTEXT.\n"
            f"- Translate faithfully. The original work is art — use natural "
            f"language, do not censor or sanitize.\n\n"
            f"### Output format (TXT only)\n"
            f"### page_name.ext\n"
            f"1. translation for first block\n"
            f"2. translation for second block\n"
            f"\n"
            f"### next_page.ext\n"
            f"1. translation ...\n"
            f"- Include ALL pages listed above — do not skip any.\n"
        )

        # Glossary section — custom (AI-generated), file, auto-learned
        glossary_lines = []
        if self.use_glossary and self._custom_glossary_entries:
            glossary_lines.append("Terminology (must match exactly):")
            for entry in self._custom_glossary_entries:
                line = f'  "{entry.source}" → "{entry.translation}"'
                if entry.note:
                    line += f"  ({entry.note})"
                glossary_lines.append(line)
        if self.use_glossary and self._file_glossary_entries:
            if not glossary_lines:
                glossary_lines.append("Terminology (must match exactly):")
            for entry in self._file_glossary_entries:
                line = f'  "{entry.source}" → "{entry.translation}"'
                if entry.note:
                    line += f"  ({entry.note})"
                glossary_lines.append(line)
        if self.use_glossary and self._glossary:
            if not glossary_lines:
                glossary_lines.append("Term consistency guide:")
            for s, t in list(self._glossary.items())[:MAX_GLOSSARY]:
                glossary_lines.append(f'  "{s}" → "{t}"')
        if glossary_lines:
            sys_prompt += "\n\n" + "\n".join(glossary_lines)

        messages = [{"role": "system", "content": sys_prompt}]

        # User prompt — all pages in unified project TXT format
        parts = []

        # Context section — pages with translations
        if ctx_pages:
            parts.append("=== CONTEXT (nearby pages) ===")
            for cp in ctx_pages:
                if cp.get("type") == "summary":
                    parts.append(cp["text"])
                    continue
                parts.append(f'\n### {cp["name"]}\n')
                for b in cp["blocks"]:
                    # page:block → 1-based block number
                    bid = int(b["id"].split(":")[1]) + 1
                    line = f'{bid}. {b["src"]}'
                    if b.get("trans"):
                        line += f' → {b["trans"]}'
                    parts.append(line)

        # Target section — source text only
        parts.append("\n=== TRANSLATE THESE ===")
        by_page = {}
        for b in target_blocks:
            pn = b.get("pname", f"page_{b['pidx']}")
            by_page.setdefault(pn, []).append(b)
        for pname, blocks in by_page.items():
            parts.append(f'\n### {pname}\n')
            for b in blocks:
                parts.append(f'{b["bidx"] + 1}. {b["src"]}')

        messages.append({"role": "user", "content": "\n".join(parts)})
        return messages

    # ── Response parsers ────────────────────────────────────────────────

    def _parse_json_response(self, raw: str):
        """Parse JSON response.

        Supports two formats:
        1. CtxResponse schema: {"translations": [{"id": "...", "translation": "..."}, ...]}
        2. Simple array:       {"translations": ["...", "...", ...]}

        Returns dict {id_str: translation} or None on failure.
        All \n in translations replaced with space.
        """

        def _clean(text: str) -> str:
            return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

        json_str = raw.strip()
        m = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            json_str,
            re.DOTALL,
        )
        if m:
            json_str = m.group(1)
        else:
            s = json_str.find("{")
            e = json_str.rfind("}")
            if s != -1 and e > s:
                json_str = json_str[s : e + 1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        translations = data.get("translations")
        if not isinstance(translations, list) or not translations:
            return None

        # Try CtxResponse format first (objects with id + translation)
        if isinstance(translations[0], dict) and "id" in translations[0]:
            try:
                validated = CtxResponse.model_validate(data)
                if validated and validated.translations:
                    return {
                        item.id: _clean(item.translation)
                        for item in validated.translations
                    }
            except ValidationError:
                pass

        # Fallback: simple array of strings → use index as id
        if isinstance(translations[0], str):
            return {
                str(i): _clean(t)
                for i, t in enumerate(translations)
            }

        return None

    def _parse_txt_response(self, raw: str, batch_keys) -> dict | None:
        """Parse project import TXT format response.

        Uses the same approach as parse_txt_translation() — captures all text
        between block markers (including continuation lines) so multi-line
        translations aren't truncated, then strips \n from each block.

        Expected format:
            ### page_name.ext

            1. translation for block 0
            continues on next line
            2. translation for block 1

            ### next_page.ext

            1. translation ...

        Returns flat dict {pidx:bidx → translation_clean} or None.
        """
        # Strip leading/trailing ``` code fences that some LLMs add
        raw = raw.strip()
        raw = re.sub(r"^```\w*\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
        PAGE_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
        BLOCK_RE = re.compile(r"^\d+\.", re.MULTILINE)

        # Split raw text into page sections by ### headers
        page_starts = [
            (m.start(), m.group(1).strip()) for m in PAGE_RE.finditer(raw)
        ]
        if not page_starts:
            return None

        result = {}  # {pname: {bidx: text}}
        for i, (pos, pname) in enumerate(page_starts):
            # Content from this page header to the next (or end of string)
            next_pos = (
                page_starts[i + 1][0]
                if i + 1 < len(page_starts)
                else len(raw)
            )
            page_text = raw[pos:next_pos]

            # Extract blocks: content between "N." markers (multi-line safe).
            # Use the LLM's block number (1-based "N." → 0-based bidx) so
            # mapping stays correct even when empty blocks were skipped.
            blocks = {}
            prev_end = None
            prev_m = None
            for m in BLOCK_RE.finditer(page_text):
                if prev_end is not None and prev_m is not None:
                    raw_text = page_text[prev_end : m.start()].strip()
                    bidx = int(prev_m.group().rstrip(".")) - 1
                    blocks[bidx] = (
                        raw_text.replace("\r\n", " ")
                        .replace("\n", " ")
                        .replace("\r", " ")
                    )
                prev_end = m.end()
                prev_m = m
            if prev_end is not None and prev_m is not None:
                raw_text = page_text[prev_end:].strip()
                bidx = int(prev_m.group().rstrip(".")) - 1
                blocks[bidx] = (
                    raw_text.replace("\r\n", " ")
                    .replace("\n", " ")
                    .replace("\r", " ")
                )

            result[pname] = blocks

        # Tolerant validation: log missing pages but keep what we have
        missing = [
            pname for pname in batch_keys
            if pname not in result or not result[pname]
        ]
        if missing:
            logger.warning(
                "TXT parser: %d/%d page(s) missing or empty — %s. "
                "Using partial results.",
                len(missing), len(batch_keys), ", ".join(missing),
            )

        # Flatten to {pidx:bidx → text}
        flat = {}
        proj_keys = list(self._proj.pages.keys()) if self._proj else []
        for pname, blocks in result.items():
            try:
                pidx = proj_keys.index(pname)
            except ValueError:
                logger.warning("TXT parser: unknown page %s", pname)
                continue
            for bidx, text in blocks.items():
                flat[f"{pidx}:{bidx}"] = text

        return flat if flat else None

    # ── LLM API call ──────────────────────────────────────────────────

    def _direct_call(self, text_list: List[str]) -> List[str]:
        """Fallback: translate a plain list of strings with no context."""
        source = pcfg.module.translate_source if hasattr(pcfg, "module") else "auto"
        target = pcfg.module.translate_target if hasattr(pcfg, "module") else "auto"
        if self.translation_prompt:
            prompt = self.translation_prompt.replace("{from_lang}", source).replace(
                "{to_lang}", target
            )
        else:
            prompt = (
                f"Translate from {source} to {target}. Return JSON array of strings."
            )
        prompt += '\nReturn JSON: {"translations": ["...", ...]}'

        parts = [f'  [{i}] "{t}"' for i, t in enumerate(text_list) if t.strip()]
        if not parts:
            return text_list
        user = "Translate these:\n" + "\n".join(parts)

        self._status(f"Direct translate (no context): {len(text_list)} texts")
        result = self._llm_call(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            len(text_list),
            parser="json",
        )
        out = []
        for i in range(len(text_list)):
            out.append(result.get(str(i), text_list[i]))
        return out

    def _llm_call(self, messages, expected_count, parser="json", batch_keys=None):
        RETRYABLE = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            openai.APIStatusError,
            httpx.RequestError,
        )
        ac = self.api_config
        api_key = ac.get("api_key", "")
        api_host = ac.get("api_host", "")
        model = ac.get("model", "gpt-4o")
        temperature = float(ac.get("temperature", 0.1))
        max_tokens = ac.get("max_tokens") or None
        proxy = ac.get("proxy", "")

        if not api_key or not api_host:
            logger.error(
                "ContextBatchTranslator: missing api_key or api_host. "
                "Configure API in the translator settings before using "
                "Context Translation."
            )
            return {}

        http_client = None
        if proxy:
            try:
                http_client = httpx.Client(proxy=proxy)
            except Exception:
                pass

        client = openai.OpenAI(
            api_key=api_key, base_url=api_host, http_client=http_client
        )
        try:
            for attempt in range(self.max_retries):
                try:
                    args = dict(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                    )
                    if max_tokens:
                        args["max_tokens"] = int(max_tokens)
                    reasoning_effort = self.api_config.get("reasoning_effort", "")
                    if reasoning_effort:
                        from utils.reasoning_params import build_reasoning_kwargs

                        args.update(
                            build_reasoning_kwargs(
                                api_host=api_host,
                                effort=reasoning_effort,
                                model=model,
                            )
                        )

                    completion = client.chat.completions.create(**args)
                    raw = (
                        (completion.choices[0].message.content or "")
                        if completion.choices
                        else ""
                    )

                    if not raw:
                        logger.warning(
                            "Empty response, attempt %d/%d",
                            attempt + 1,
                            self.max_retries,
                        )
                        self._status(
                            f"Empty response, retry {attempt + 1}/{self.max_retries}..."
                        )
                        time.sleep(self.retry_parse_sleep)
                        continue

                    # Parse response
                    if parser == "txt":
                        result = self._parse_txt_response(raw, batch_keys or [])
                    else:
                        result = self._parse_json_response(raw)

                    if result is None:
                        raise ValueError(
                            f"Failed to parse {parser} response"
                        )
                    if len(result) == 0:
                        raise ValueError(
                            f"Parsed {parser} response is empty"
                        )
                    if len(result) < expected_count:
                        logger.warning(
                            "TXT parser: expected %d entries, got %d — "
                            "missing entries will use original text",
                            expected_count, len(result),
                        )

                    return result

                except (ValidationError, ValueError) as e:
                    logger.warning(
                        "Parse error (attempt %d/%d): %s",
                        attempt + 1,
                        self.max_retries,
                        e,
                    )
                    self._status(
                        f"Parse error, retry {attempt + 1}/{self.max_retries}..."
                    )
                    time.sleep(self.retry_parse_sleep)
                except RETRYABLE as e:
                    logger.warning(
                        "API error (attempt %d/%d): %s",
                        attempt + 1,
                        self.max_retries,
                        e,
                    )
                    self._status(
                        f"API error, retry {attempt + 1}/{self.max_retries}..."
                    )
                    time.sleep(self.retry_api_sleep)
        finally:
            if http_client is not None:
                http_client.close()

        logger.error(
            "ContextBatchTranslator: all %d attempts failed",
            self.max_retries,
        )
        return {}

    # ── Glossary ──────────────────────────────────────────────────────

    def _update_glossary(self, blk_list, raw):
        prev = len(self._glossary)
        for blk in blk_list:
            src = blk.get_text().strip()
            if not src or len(src) < 2 or len(src) > 30:
                continue
            tr = blk.translation
            if not tr or tr == src:
                continue
            if self._count_src(src) >= 2:
                self._glossary[src] = tr
        if len(self._glossary) > MAX_GLOSSARY:
            items = list(self._glossary.items())
            self._glossary = dict(items[-MAX_GLOSSARY:])
        if len(self._glossary) != prev:
            self._status(f"Glossary: {len(self._glossary)} terms")

    def _count_src(self, text):
        if self._proj is None:
            return 1
        n = 0
        for blist in self._proj.pages.values():
            for blk in blist:
                if blk.get_text().strip() == text:
                    n += 1
        return n

    # ── AI glossary generation ─────────────────────────────────────────

    def _generate_custom_glossary(
        self, user_text: str
    ) -> Tuple[GlossaryEntry, ...]:
        """Send user's natural language description to the LLM and parse
        the structured glossary response.

        The prompt instructs the LLM to extract source→target term pairs
        and return them as a compact JSON array.  Returns an empty tuple
        on any failure so translation can continue without custom terms.
        """
        if not user_text or not user_text.strip():
            return ()

        prompt = (
            "You are a translation glossary assistant. The user provides "
            "terminology descriptions in natural language or semi-structured "
            "format. Convert them into a structured glossary.\n\n"
            "Rules:\n"
            "- Extract every named entity (character names, place names, "
            "special terms) and its intended translation.\n"
            '- Format: {"src": "original", "dst": "translated", '
            '"info": "brief context note if helpful, else empty"}\n'
            "- If the user uses notation like A > B or A → B, treat A as "
            "source and B as translation.\n"
            '- Return ONLY valid JSON: {"glossary": [...]}\n'
            "- If nothing can be extracted, return {\"glossary\": []}.\n\n"
            f"User input:\n{user_text}"
        )

        messages = [{"role": "system", "content": prompt}]
        self._status("Generating AI glossary from custom input...")

        try:
            raw = self._raw_llm_call(messages)
            if not raw:
                self._status("AI glossary: empty response")
                return ()
            entries = self._parse_glossary_response(raw)
            return tuple(entries)
        except Exception as e:
            logger.warning("AI glossary generation failed: %s", e)
            return ()

    def _parse_glossary_response(self, raw: str) -> List[GlossaryEntry]:
        """Parse the LLM's glossary JSON response into GlossaryEntry objects.

        Handles markdown code fences and various JSON whitespace patterns.
        """
        text = raw.strip()

        # Strip ```json ... ``` fences
        m = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL
        )
        if m:
            text = m.group(1)
        else:
            # Fallback: find first { and last }
            s = text.find("{")
            e = text.rfind("}")
            if s != -1 and e > s:
                text = text[s : e + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("AI glossary: failed to parse JSON response")
            return []

        glossary_data = data.get("glossary", [])
        if not isinstance(glossary_data, list):
            return []

        entries: List[GlossaryEntry] = []
        for item in glossary_data:
            if not isinstance(item, dict):
                continue
            src = item.get("src", "").strip()
            dst = item.get("dst", "").strip()
            if not src or not dst:
                continue
            note = item.get("info", "").strip() or ""
            entries.append(GlossaryEntry(src, dst, note))

        return entries

    def _raw_llm_call(self, messages: list) -> str:
        """Make a single LLM call with the given messages.

        Unlike _llm_call(), this is a one-shot call without retry logic
        or batch-target parsing — intended for glossary generation and
        other lightweight side tasks.
        """
        ac = self.api_config
        api_key = ac.get("api_key", "")
        api_host = ac.get("api_host", "")
        model = ac.get("model", "gpt-4o")
        proxy = ac.get("proxy", "")

        if not api_key or not api_host:
            logger.error(
                "ContextBatchTranslator._raw_llm_call: "
                "missing api_key or api_host"
            )
            return ""

        http_client = None
        if proxy:
            try:
                http_client = httpx.Client(proxy=proxy)
            except Exception:
                pass

        try:
            client = openai.OpenAI(
                api_key=api_key, base_url=api_host, http_client=http_client
            )
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
            )
            return (
                (completion.choices[0].message.content or "")
                if completion.choices
                else ""
            )
        finally:
            if http_client is not None:
                http_client.close()

    # ── Progressive summary ───────────────────────────────────────────

    def _update_summary(self, batch_start, batch_end, batch_keys):
        terms = []
        if self._glossary:
            recent = list(self._glossary.items())[-10:]
            terms.append("Terms: " + ", ".join(f"{s}→{t}" for s, t in recent))
        summary = f"Pages {batch_start}-{batch_end - 1}: " + "; ".join(terms)
        self._summaries[batch_start] = summary[:MAX_SUMMARY]
        self._status(
            f"Summary: pages {batch_start}-{batch_end - 1} ({len(summary)} chars)"
        )
