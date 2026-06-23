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
import re
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Set

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from utils.proj_imgtrans import ProjImgTrans
    from utils.textblock import TextBlock

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
    ):
        self.api_config = dict(api_config)
        self.translation_prompt = translation_prompt
        self._status_cb = status_callback

        # Runtime state (reset per project via set_project)
        self._proj: Optional["ProjImgTrans"] = None
        self._blklist_to_pagekey: Dict[int, str] = {}
        self._cached: Dict[str, Dict[int, str]] = {}
        self._glossary: Dict[str, str] = {}
        self._summaries: Dict[int, str] = {}
        self._completed: Set[str] = set()

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
        self._glossary.clear()
        self._summaries.clear()
        self._completed.clear()
        for pname, blklist in proj.pages.items():
            self._blklist_to_pagekey[id(blklist)] = pname

    def finalize(self):
        self._proj = None
        self._blklist_to_pagekey.clear()
        self._cached.clear()
        self._glossary.clear()
        self._summaries.clear()
        self._completed.clear()

    def translate_textblk_lst(self, textblk_lst: List["TextBlock"]):
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
        bs = self.batch_size
        pi = self._page_idx(page_key)
        if self._proj is None:
            return self._direct_call(text_list)
        all_keys = list(self._proj.pages.keys())
        total = len(all_keys)

        # Group pages into batches of batch_size
        start = (pi // bs) * bs
        end = min(start + bs, total)
        batch_keys = all_keys[start:end]
        triggers = {batch_keys[0]}

        # Cache hit
        if page_key in self._cached:
            return self._apply_cache(blk_list, non_empty, self._cached[page_key])

        if page_key not in triggers:
            return self._direct_call(text_list)

        ctx = self._build_ctx(page_key, batch_keys, all_keys)
        target = self._collect_target(batch_keys)

        ctx_pages = len([c for c in ctx if c.get("type") != "summary"])
        ctx_blks = sum(len(c.get("blocks", [])) for c in ctx if "blocks" in c)
        use_summary = total > bs * 4 and self._summaries
        mode = "summary" if use_summary else ("full" if total <= bs else "window")
        batch_idx = (pi // bs) + 1
        total_batches = (total + bs - 1) // bs
        self._status(
            f"────────────────────────────────────────\n"
            f"Batch {batch_idx}/{total_batches} · Pages {start}-{end-1} · {mode}\n"
            f"Context: {ctx_pages} pages, {ctx_blks} ref blocks\n"
            f"→ translating {len(target)} blocks"
        )

        messages = self._build_msgs(ctx, target)

        t0 = time.time()
        raw = self._llm_call(messages, len(target))
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
        if total > bs * 4:
            self._update_summary(start, end, batch_keys)

        return self._apply_cache(blk_list, non_empty, self._cached[page_key])

    def _apply_cache(self, blk_list, non_empty, cache):
        return [cache.get(idx, blk_list[idx].get_text()) for idx in non_empty]

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
        if total > self.batch_size * 4 and self._summaries:
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
                e["trans"] = blk.translation
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
                            "src": src,
                        }
                    )
        return result

    # ── Message assembly ──────────────────────────────────────────────

    def _build_msgs(self, ctx_pages, target_blocks):
        source = pcfg.module.translate_source if hasattr(pcfg, "module") else "auto"
        target = pcfg.module.translate_target if hasattr(pcfg, "module") else "auto"

        # System prompt — always use dedicated context prompt, NOT profile's
        # prompt_template (which is designed for simple batch translation and
        # lacks any context-awareness instructions).
        sys_prompt = (
            f"You are a professional manga/comic translator "
            f"translating from {source} to {target}.\n\n"
            f"The user message contains two sections:\n"
            f"1. === CONTEXT (nearby pages) === — Pages near the current text, "
            f"already translated for reference.\n"
            f"   [done] = fully translated with both source and translation shown;\n"
            f"   [raw]  = source text only, not yet translated.\n"
            f"   Use these to maintain consistency in character names, "
            f"terminology, and dialogue tone.\n\n"
            f"2. === TRANSLATE THESE === — The text blocks that need translation now.\n\n"
            f"RULES:\n"
            f"- Keep character names and terms consistent with the CONTEXT "
            f"translations.\n"
            f"- Match the speaking style and tone established in the CONTEXT.\n"
            f"- Use surrounding dialogue to disambiguate pronouns and implied "
            f"subjects.\n"
            f"- Localize sound effects and onomatopoeia appropriately.\n"
            f"- Output ONLY valid JSON with no extra text.\n\n"
            f"Output strict JSON:\n"
            '{"translations": [{"id": "page:block", "translation": "..."}, ...]}'
        )

        # Glossary section
        if self.use_glossary and self._glossary:
            lines = ["Term consistency guide:"]
            for s, t in list(self._glossary.items())[:MAX_GLOSSARY]:
                lines.append(f'  "{s}" → "{t}"')
            sys_prompt += "\n\n" + "\n".join(lines)

        messages = [{"role": "system", "content": sys_prompt}]

        # User prompt
        parts = []
        if ctx_pages:
            parts.append("=== CONTEXT (nearby pages) ===")
            for cp in ctx_pages:
                if cp.get("type") == "summary":
                    parts.append(cp["text"])
                    continue
                status = "[done]" if cp.get("translated") else "[raw]"
                parts.append(f'\nPage {cp["pidx"]} "{cp["name"]}" {status}:')
                for b in cp["blocks"]:
                    line = f'  [{b["id"]}] "{b["src"]}"'
                    if b.get("trans"):
                        line += f' → "{b["trans"]}"'
                    parts.append(line)

        parts.append("\n=== TRANSLATE THESE ===")
        parts.extend(f'  [{b["id"]}] "{b["src"]}"' for b in target_blocks)
        messages.append({"role": "user", "content": "\n".join(parts)})
        return messages

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
        )
        out = []
        for i in range(len(text_list)):
            out.append(result.get(str(i), text_list[i]))
        return out

    def _llm_call(self, messages, expected_count):
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

                    # Parse JSON
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

                    data = json.loads(json_str)
                    validated = CtxResponse.model_validate(data)

                    if not validated or not validated.translations:
                        raise ValueError("No translations")
                    if len(validated.translations) != expected_count:
                        raise ValueError(
                            f"Expected {expected_count}, "
                            f"got {len(validated.translations)}"
                        )

                    return {
                        item.id: item.translation for item in validated.translations
                    }

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
