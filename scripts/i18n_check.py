#!/usr/bin/env python3
"""i18n audit script for BallonsTranslator.

Three checks:
  1. Hardcoded Chinese strings in UI code (outside self.tr())
  2. self.tr() calls missing from zh_CN.ts
  3. Active zh_CN.ts entries with no matching self.tr() call (orphans)

Orphans from KNOWN_ORPHAN_CONTEXTS (see i18n_common.py — indirect
``self.tr(variable)`` calls, maintained by hand in the .ts) are reported
as "expected" and do not count toward the exit code; pass
``--show-expected`` to list them.

Usage:
  python scripts/i18n_check.py          # report all issues
  python scripts/i18n_check.py --ci     # non-zero exit on any finding
  python scripts/i18n_check.py --show-expected  # also list known orphans
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 便携 Python 开启 safe_path 时不自动加脚本目录，自举以导入同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from i18n_common import (
    KNOWN_ORPHAN_CONTEXTS,
    PROJECT_ROOT,
    TR_CALL_RE,
    TR_CALL_SQ_RE,
    TS_FILE,
    gather_tr_calls,
    is_obsolete_ts_msg,
    reconfigure_stdout,
)

# Chinese character range (CJK Unified Ideographs)
CJK_RE = re.compile(r"[一-鿿]")


def find_ui_py_files():
    """Python files under ui/ — for hardcoded Chinese check."""
    ui_dir = PROJECT_ROOT / "ui"
    return sorted(ui_dir.rglob("*.py")) if ui_dir.is_dir() else []


def is_comment_or_docstring(lines, line_idx):
    """Check if line_idx is inside a comment or docstring."""
    stripped = lines[line_idx].strip()
    if stripped.startswith("#"):
        return True
    # Track triple-quote pairs to detect docstrings
    in_docstring = False
    for i in range(line_idx + 1):
        tq = lines[i].count('"""') + lines[i].count("'''")
        if tq % 2 == 1:
            in_docstring = not in_docstring
    return in_docstring


def has_chinese(text):
    return bool(CJK_RE.search(text))


# ── Check 1: Hardcoded Chinese (ui/ only) ───────────────────────────────


def find_hardcoded_chinese(files):
    """Find Chinese characters in string literals not wrapped in self.tr()."""
    issues = []

    # Short strings used in font-metrics / CJK-range checks — not UI text.
    NON_UI_PATTERNS = frozenset(
        {
            "一",
            "鿿",
            "啊",
            "木",
            "木fg",
            "X木",
            "X",
            "简体中文",
            "无字图配对工具.py",  # file path, not UI text
        }
    )

    for fpath in files:
        try:
            lines = fpath.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            if is_comment_or_docstring(lines, i):
                continue

            # Skip single-line triple-quoted docstrings (e.g.  """blah""")
            stripped = line.strip()
            if (
                stripped.startswith('"""')
                and stripped.endswith('"""')
                and len(stripped) >= 6
            ) or (
                stripped.startswith("'''")
                and stripped.endswith("'''")
                and len(stripped) >= 6
            ):
                continue

            # Remove portions inside self.tr(...) calls so we don't flag
            # correctly-wrapped strings
            cleaned = TR_CALL_RE.sub("", line)
            cleaned = TR_CALL_SQ_RE.sub("", cleaned)

            # Find string literals containing Chinese
            for pat in [r'"([^"]*)"', r"'([^']*)'"]:
                for m in re.finditer(pat, cleaned):
                    text = m.group(1)
                    if has_chinese(text) and text not in NON_UI_PATTERNS:
                        issues.append(
                            (str(fpath.relative_to(PROJECT_ROOT)), i + 1, text)
                        )

    return issues


# ── Check 2 & 3: tr() .ts coverage ──────────────────────────────────


def extract_ts_entries(ts_path):
    """Parse zh_CN.ts: return set of (context, source) for active entries."""
    entries = set()
    try:
        tree = ET.parse(ts_path)
        root = tree.getroot()
        for ctx_elem in root.findall("context"):
            ctx_name = ctx_elem.find("name")
            if ctx_name is None:
                continue
            context = ctx_name.text
            for msg in ctx_elem.findall("message"):
                source_elem = msg.find("source")
                if source_elem is None or source_elem.text is None:
                    continue
                if is_obsolete_ts_msg(msg):
                    continue
                entries.add((context, source_elem.text))
    except Exception as e:
        print(f"Error parsing {ts_path}: {e}", file=sys.stderr)
        return None
    return entries


def find_missing_and_orphans(ts_entries, all_tr_calls):
    """Return (missing_tr_calls, orphan_ts_entries, expected_orphans)."""
    missing = sorted((ctx, s) for ctx, s in all_tr_calls if (ctx, s) not in ts_entries)
    # Filter orphans: exclude format strings (skipped by tr() extractor)
    # and known-indirect contexts (shortcut names/group titles, module param
    # descriptions — hand-maintained in .ts, always orphans).
    orphans = sorted(
        (ctx, s)
        for ctx, s in ts_entries
        if (ctx, s) not in all_tr_calls
        and "{" not in s
        and ctx not in KNOWN_ORPHAN_CONTEXTS
    )
    expected = sorted(
        (ctx, s)
        for ctx, s in ts_entries
        if (ctx, s) not in all_tr_calls and ctx in KNOWN_ORPHAN_CONTEXTS
    )
    return missing, orphans, expected


# ── Main ────────────────────────────────────────────────────────────────


def main():
    reconfigure_stdout()

    parser = argparse.ArgumentParser(description="i18n audit for BallonsTranslator")
    parser.add_argument("--ci", action="store_true", help="Exit non-zero on findings")
    parser.add_argument(
        "--show-expected",
        action="store_true",
        help="Also list known orphans (indirect self.tr() calls, hand-maintained in .ts)",
    )
    args = parser.parse_args()

    exit_code = 0

    # Check 1: Hardcoded Chinese — ui/ only (modules/ has log messages,
    # LLM prompts, language maps that legitimately contain Chinese)
    hc_files = find_ui_py_files()
    hc = find_hardcoded_chinese(hc_files)
    if hc:
        exit_code |= 1
        print(f"\n[HARDCODED CHINESE] {len(hc)} string(s) outside self.tr():")
        for fname, lineno, text in hc:
            print(f'  {fname}:{lineno}  "{text}"')
    else:
        print("\n[HARDCODED CHINESE] None found.")

    # Check 2 & 3: tr() <-> .ts coverage — ui/ + modules/
    ts_entries = extract_ts_entries(TS_FILE)
    if ts_entries is None:
        ts_entries = set()
    missing, orphans, expected = find_missing_and_orphans(
        ts_entries, gather_tr_calls()
    )

    if missing:
        exit_code |= 2
        print(
            f"\n[MISSING .ts ENTRIES] {len(missing)} self.tr() call(s) without "
            f"a matching <message> in zh_CN.ts:"
        )
        for ctx, s in missing:
            print(f'  [{ctx}] "{s}"')
    else:
        print("\n[MISSING .ts ENTRIES] All self.tr() calls have .ts entries.")

    if orphans:
        exit_code |= 4
        print(
            f"\n[ORPHAN .ts ENTRIES] {len(orphans)} active .ts entry(ies) with no "
            f"matching self.tr() call:"
        )
        for ctx, s in orphans:
            print(f'  [{ctx}] "{s}"')
    else:
        print("\n[ORPHAN .ts ENTRIES] None found.")

    if args.show_expected and expected:
        print(
            f"\n[KNOWN ORPHANS] {len(expected)} expected entry(ies) from "
            f"indirect self.tr(variable) calls / param descriptions "
            f"(contexts: {', '.join(sorted(KNOWN_ORPHAN_CONTEXTS))}) — "
            f"hand-maintained in .ts, not counted as failures:"
        )
        for ctx, s in expected:
            print(f'  [{ctx}] "{s}"')

    if exit_code == 0:
        print("\n[PASS] i18n check passed.")
    else:
        print(f"\n[FAIL] i18n check failed (exit code {exit_code}).")

    if args.ci:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
