"""
Qt-free glossary extraction from project source/translation pairs.

Provides two extraction strategies:
• **Frequency heuristic** — counts source-text occurrences across the
  project and promotes recurring terms to glossary entries.
• **LLM extraction** — sends source/translation pairs to an LLM with a
  structured prompt that asks it to identify important named entities
  and recurring terms that benefit from consistent translation.

Both return ``Tuple[GlossaryEntry, ...]`` compatible with the existing
``modules/context/glossary`` loading/selection pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Tuple

import httpx
import openai

if TYPE_CHECKING:
    from utils.proj_imgtrans import ProjImgTrans

from modules.context.glossary import GlossaryEntry

logger = logging.getLogger("glossary_extractor")

# ── Constants ──────────────────────────────────────────────────────────────

_MIN_SRC_LEN = 2
_MAX_SRC_LEN = 50
_DEFAULT_MIN_COUNT = 2
_LLM_TEMPERATURE = 0.1

_LLM_TIMEOUT_SEC = 60

# Target language options for the glossary extractor dialog.
# Displayed in native form; not translatable UI strings.
TARGET_LANGUAGES = [
    "简体中文",
    "繁體中文",
    "English",
    "日本語",
    "한국어",
    "Tiếng Việt",
    "Français",
    "Deutsch",
    "Español",
    "Português",
    "Brazilian Portuguese",
    "русский язык",
    "Arabic",
    "Hindi",
    "Thai",
]

# ── Frequency-based extraction ─────────────────────────────────────────────


def extract_by_frequency(
    proj: "ProjImgTrans",
    min_count: int = _DEFAULT_MIN_COUNT,
) -> Tuple[GlossaryEntry, ...]:
    """Extract glossary entries from project data by source-text frequency.

    Scans every text block across all pages, counts how many times each
    source string appears, and promotes those that appear at least
    *min_count* times *and* have a non-identical translation.

    When the same source string has been translated differently on
    different pages, the most frequently used translation is chosen.

    Parameters
    ----------
    proj:
        The loaded project (``ProjImgTrans`` instance).
    min_count:
        Minimum number of occurrences to qualify as a glossary entry.

    Returns
    -------
    Tuple[GlossaryEntry, ...]
        Deduplicated entries in descending occurrence order.
    """
    # Count source occurrences, tracking all translations seen
    source_counter: Counter[str] = Counter()
    trans_map: Dict[str, Counter[str]] = defaultdict(Counter)

    for blk_list in proj.pages.values():
        for blk in blk_list:
            src = blk.get_text().strip()
            if not src or len(src) < _MIN_SRC_LEN or len(src) > _MAX_SRC_LEN:
                continue
            tr = (blk.translation or "").strip()
            if not tr or tr == src:
                continue
            source_counter[src] += 1
            trans_map[src][tr] += 1

    # Build entries: select most common translation for each qualified source
    entries: List[GlossaryEntry] = []
    for src, count in source_counter.most_common():
        if count < min_count:
            continue
        tr_counts = trans_map.get(src)
        if not tr_counts:
            continue
        best_tr = tr_counts.most_common(1)[0][0]
        entries.append(GlossaryEntry(source=src, translation=best_tr, note=""))

    return tuple(entries)


# ── LLM-based extraction ───────────────────────────────────────────────────


def extract_by_llm(
    proj: "ProjImgTrans",
    api_config: dict,
    status_cb: Optional[Callable[[str], None]] = None,
    target_language: str = "简体中文",
) -> Tuple[GlossaryEntry, ...]:
    """Extract glossary entries via an LLM call.

    Two modes, auto-selected:

    * **With translations** — collects all unique source/translation
      pairs and sends them to the LLM for analysis.  The LLM identifies
      important terms based on how they were translated.

    * **Without translations** (source-only) — falls back when the
      project has not been translated yet.  Collects unique source texts
      and asks the LLM to identify important terms **and** suggest
      appropriate translations based on its knowledge.

    *target_language* is the display name of the target language
    (e.g. ``"简体中文"``, ``"English"``), injected into the LLM prompt
    so the model knows which language to translate glossary entries into.

    The LLM always returns a compact JSON array of
    ``{"src": …, "dst": …, "info": …}`` objects.

    Parameters
    ----------
    proj:
        The loaded project.
    api_config:
        Dict with at least ``api_host``, ``api_key``, ``model``, and
        optionally ``proxy``.
    status_cb:
        Optional callback for progress/status messages.
    target_language:
        Display name of the target language (default ``"简体中文"``).

    Returns
    -------
    Tuple[GlossaryEntry, …]
    """
    # Try pair mode first (requires translations)
    if status_cb:
        status_cb("Collecting source/translation pairs...")

    pairs = _collect_src_tr_pairs(proj)
    if pairs:
        has_translations = True
        input_data = pairs
        input_label = "unique terms"
    else:
        # Fall back to source-only mode
        if status_cb:
            status_cb("No translations found, collecting source texts only...")
        texts = _collect_src_texts(proj)
        if not texts:
            logger.info("extract_by_llm: no source texts found either")
            return ()
        has_translations = False
        input_data = texts
        input_label = "unique source texts"

    prompt = _build_llm_prompt(
        input_data,
        has_translations=has_translations,
        target_language=target_language,
    )

    if status_cb:
        count = len(pairs) if has_translations else len(input_data)  # type: ignore[arg-type]
        status_cb(f"Sending {count} {input_label} to LLM...")

    raw = _raw_llm_call(
        api_config=api_config,
        messages=[
            {"role": "system", "content": prompt},
        ],
    )
    if not raw:
        logger.warning("extract_by_llm: empty response from LLM")
        return ()

    if status_cb:
        status_cb("Parsing LLM response...")

    entries = _parse_llm_response(raw)
    logger.info("extract_by_llm: extracted %d glossary entries", len(entries))
    return entries


# ── Saving ──────────────────────────────────────────────────────────────────


def save_glossary_json(entries: Iterable[GlossaryEntry], path: str) -> None:
    """Save glossary entries to a JSON file in the format expected by
    ``modules/context/glossary.load_glossary()``.

    The output is an array of ``{"src": …, "dst": …, "info": …}`` objects.
    """
    rows = [
        {
            "src": entry.source,
            "dst": entry.translation,
            "info": entry.note,
        }
        for entry in entries
    ]
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ── Internal helpers ───────────────────────────────────────────────────────


def _collect_src_tr_pairs(proj: "ProjImgTrans") -> Dict[str, List[str]]:
    """Collect all unique (source → [translations]) from the project.

    Returns a dict keyed by source text, where each value is a list of
    unique translations seen for that source across the project.
    """
    pairs: Dict[str, Dict[str, int]] = defaultdict(Counter)
    for blk_list in proj.pages.values():
        for blk in blk_list:
            src = blk.get_text().strip()
            if not src:
                continue
            tr = (blk.translation or "").strip()
            if not tr or tr == src:
                continue
            pairs[src][tr] += 1

    # Convert to {source: [translations sorted by frequency]}
    result: Dict[str, List[str]] = {}
    for src, tr_counts in pairs.items():
        if len(src) < _MIN_SRC_LEN or len(src) > _MAX_SRC_LEN:
            continue
        sorted_trs = [tr for tr, _ in tr_counts.most_common()]
        result[src] = sorted_trs
    return result


def _collect_src_texts(proj: "ProjImgTrans") -> List[str]:
    """Collect unique, non-trivial source texts from the project.

    Unlike ``_collect_src_tr_pairs``, this does **not** require a
    translation — it is used when the project has not been translated yet
    (glossary extraction before translation).

    Returns a deduplicated list of source strings sorted alphabetically.
    """
    seen: set[str] = set()
    for blk_list in proj.pages.values():
        for blk in blk_list:
            src = blk.get_text().strip()
            if not src or len(src) < _MIN_SRC_LEN or len(src) > _MAX_SRC_LEN:
                continue
            seen.add(src)

    result = sorted(seen)
    logger.debug("_collect_src_texts: %d unique source texts", len(result))
    return result


def _build_llm_prompt(
    pairs_or_texts: "Dict[str, List[str]] | List[str]",
    has_translations: bool = True,
    target_language: str = "简体中文",
) -> str:
    """Build the system prompt for LLM-based glossary extraction.

    Two modes:

    * ``has_translations=True`` (default) — source/translation pairs are
      provided as ``{src: [translations]}``.  The prompt asks the LLM to
      analyse existing translations and extract important glossary terms.

    * ``has_translations=False`` — only a list of source texts is
      provided.  The prompt asks the LLM to identify important terms and
      suggest appropriate translations based on its knowledge.

    *target_language* is the display name of the target language
    (e.g. ``"简体中文"``, ``"English"``).  It is injected into the prompt
    so the LLM knows which language to translate glossary entries into.

    Output format is always a JSON array of
    ``{"src": …, "dst": …, "info": …}`` objects.
    """

    MAX_INPUT_LINES = 3000

    if has_translations:
        # ── Pair mode: source → translation ────────────────────────────
        pairs: "Dict[str, List[str]]" = pairs_or_texts  # type: ignore
        pair_lines = []
        for src, translations in pairs.items():
            if len(translations) == 1:
                pair_lines.append(f"{src} → {translations[0]}")
            else:
                alternatives = " | ".join(translations)
                pair_lines.append(f"{src} → {alternatives}")

        if len(pair_lines) > MAX_INPUT_LINES:
            pair_lines = pair_lines[:MAX_INPUT_LINES]

        input_text = "\n".join(pair_lines)

        return (
            "You are a glossary extraction assistant specialised in manga and "
            "comic-book translation.\n\n"
            "Below is a list of source-text → translation pairs from a manga "
            "translation project.  Each line shows a source phrase and how it "
            "was translated (if multiple translations exist they are separated "
            "by `|`).\n\n"
            "Your task is to identify the IMPORTANT terms that should be added "
            "to a translation glossary for consistent use across the project.  "
            f"The target language is **{target_language}**.\n"
            "Focus on:\n"
            "1. Character names (protagonists, antagonists, supporting cast)\n"
            "2. Place names and location names\n"
            "3. Organisation, faction, and race names\n"
            "4. Special techniques, items, or magical terms\n"
            "5. Any term whose translation is non-literal or where the "
            "translation differs notably from the source\n"
            "6. Any word that appears frequently and whose consistent "
            "translation matters for readability\n\n"
            "RULES:\n"
            "- Only include terms that genuinely benefit from glossary "
            "consistency.  Skip ordinary vocabulary (e.g. \"hello\", \"thank you\").\n"
            "- If a source has multiple translations, pick the MOST COMMON or "
            "most appropriate one as the glossary translation.\n"
            "- Return ONLY valid JSON — no markdown, no explanation.\n"
            "- Output format:\n"
            '  [{"src": "original", "dst": "translation", "info": "category"}, ...]\n'
            "- If nothing useful is found, return an empty array [].\n\n"
            "Source→Translation pairs:\n"
            "---\n"
            f"{input_text}\n"
            "---\n\n"
            "JSON output:"
        )

    # ── Source-only mode (no translations available yet) ───────────────
    texts: "List[str]" = pairs_or_texts  # type: ignore

    if len(texts) > MAX_INPUT_LINES:
        texts = texts[:MAX_INPUT_LINES]

    input_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    return (
        "You are a glossary extraction assistant specialised in manga and "
        "comic-book translation.\n\n"
        "Below is a list of source texts extracted from a manga/comic "
        "translation project.  The project has not been translated yet.\n\n"
        "Your task is to identify the IMPORTANT terms that should be added "
        "to a translation glossary for consistent use across the project.  "
        f"The target language is **{target_language}**.\n"
        "Focus on:\n"
        "1. Character names (protagonists, antagonists, supporting cast)\n"
        "2. Place names and location names\n"
        "3. Organisation, faction, and race names\n"
        "4. Special techniques, items, or magical terms\n"
        "5. Any proper noun or specialised term whose consistent "
        "translation matters for readability\n\n"
        "For each identified term, suggest an appropriate translation "
        f"in **{target_language}** based on your knowledge.  If you are "
        "uncertain about the best translation, still include the term "
        "— the user can review and adjust it later.\n\n"
        "RULES:\n"
        "- Only include terms that genuinely benefit from glossary "
        "consistency.  Skip ordinary vocabulary (e.g. \"hello\", \"thank you\").\n"
        "- Return ONLY valid JSON — no markdown, no explanation.\n"
        "- Output format:\n"
        '  [{"src": "original", "dst": "suggested_translation_or_empty", '
        '"info": "category"}, ...]\n'
        "- The \"dst\" field must be non-empty — provide your best guess.\n"
        "- If nothing useful is found, return an empty array [].\n\n"
        "Source texts:\n"
        "---\n"
        f"{input_text}\n"
        "---\n\n"
        "JSON output:"
    )


def _parse_llm_response(raw: str) -> Tuple[GlossaryEntry, ...]:
    """Parse the LLM's JSON response into GlossaryEntry objects.

    Handles markdown code fences and various JSON whitespace patterns.
    Returns an empty tuple on any failure.
    """
    text = raw.strip()

    # Strip ```json ... ``` fences
    import re

    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # Fallback: find first [ and last ]
        s = text.find("[")
        e = text.rfind("]")
        if s != -1 and e > s:
            text = text[s : e + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("_parse_llm_response: failed to parse JSON")
        return ()

    if not isinstance(data, list):
        logger.warning("_parse_llm_response: response is not a list")
        return ()

    entries: List[GlossaryEntry] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        src = (item.get("src") or "").strip()
        dst = (item.get("dst") or "").strip()
        if not src or not dst:
            continue
        note = (item.get("info") or "").strip()
        entry = GlossaryEntry(source=src, translation=dst, note=note)
        if entry not in seen:
            seen.add(entry)
            entries.append(entry)

    return tuple(entries)


def _raw_llm_call(api_config: dict, messages: list) -> str:
    """Make a single one-shot LLM call.

    No retry, no batch parsing (the beta batch translator this mirrored was
    removed in the agent rework).  Returns the raw response text, or empty
    string on failure.
    """
    api_key = api_config.get("api_key", "")
    api_host = api_config.get("api_host", "")
    model = api_config.get("model", "gpt-4o")
    proxy = api_config.get("proxy", "")

    if not api_key or not api_host:
        logger.error("_raw_llm_call: missing api_key or api_host")
        return ""

    http_client = None
    if proxy:
        try:
            http_client = httpx.Client(proxy=proxy, timeout=_LLM_TIMEOUT_SEC)
        except Exception as exc:
            logger.warning("_raw_llm_call: proxy setup failed: %s", exc)

    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url=api_host,
            http_client=http_client,
        )
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=_LLM_TEMPERATURE,
        )
        content = (
            (completion.choices[0].message.content or "")
            if completion.choices
            else ""
        )
        logger.debug("_raw_llm_call: received %d chars", len(content))
        return content
    except openai.OpenAIError as exc:
        logger.error("_raw_llm_call: OpenAI API error: %s", exc)
        return ""
    except Exception as exc:
        logger.error("_raw_llm_call: unexpected error: %s", exc)
        return ""
    finally:
        if http_client is not None:
            http_client.close()
