#!/usr/bin/env python3
"""i18n audit script for BallonsTranslator.

Three checks:
  1. Hardcoded Chinese strings in UI code (outside self.tr())
  2. self.tr() calls missing from zh_CN.ts
  3. Active zh_CN.ts entries with no matching self.tr() call (orphans)

Usage:
  python scripts/i18n_check.py          # report all issues
  python scripts/i18n_check.py --ci     # non-zero exit on any finding
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TS_FILE = PROJECT_ROOT / "translate" / "zh_CN.ts"

# Chinese character range (CJK Unified Ideographs)
CJK_RE = re.compile(r"[一-鿿]")

# Match self.tr("...") or self.tr('...') — captures the string content
TR_CALL_RE = re.compile(r'self\.tr\("((?:[^"\\]|\\.)*)"\)')
TR_CALL_SQ_RE = re.compile(r"self\.tr\('((?:[^'\\]|\\.)*)'\)")

# Match a class definition: class ClassName(...) or class ClassName:
CLASS_RE = re.compile(r'^\s*class\s+(\w+)\s*[(:]')


def find_ui_py_files():
    """Python files under ui/ — for hardcoded Chinese check."""
    ui_dir = PROJECT_ROOT / "ui"
    return sorted(ui_dir.rglob("*.py")) if ui_dir.is_dir() else []


def find_all_py_files():
    """All Python files in ui/ and modules/ — for tr-coverage check."""
    files = []
    for scan_dir in ("ui", "modules"):
        dir_path = PROJECT_ROOT / scan_dir
        if dir_path.is_dir():
            files.extend(dir_path.rglob("*.py"))
    return sorted(files)


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

    for fpath in files:
        try:
            lines = fpath.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            if is_comment_or_docstring(lines, i):
                continue

            # Remove portions inside self.tr(...) calls so we don't flag
            # correctly-wrapped strings
            cleaned = TR_CALL_RE.sub("", line)
            cleaned = TR_CALL_SQ_RE.sub("", cleaned)

            # Find string literals containing Chinese
            for pat in [r'"([^"]*)"', r"'([^']*)'"]:
                for m in re.finditer(pat, cleaned):
                    if has_chinese(m.group(1)):
                        issues.append(
                            (str(fpath.relative_to(PROJECT_ROOT)), i + 1, m.group(1))
                        )

    return issues


# ── Check 2 & 3: tr() .ts coverage ──────────────────────────────────

def extract_context_and_tr_calls(content):
    """Parse a .py file: return list of (context_class, tr_string)."""
    lines = content.splitlines()
    results = []
    current_class = None

    for line in lines:
        m = CLASS_RE.match(line)
        if m:
            current_class = m.group(1)
            continue

        for tr_re in (TR_CALL_RE, TR_CALL_SQ_RE):
            for m in tr_re.finditer(line):
                s = m.group(1)
                # Unescape common escape sequences
                s = s.replace('\\"', '"').replace("\\'", "'")
                s = s.replace("\\\\", "\\")
                s = s.replace("\\n", "\n").replace("\\t", "\t")
                # Skip format strings and variable references
                if "{" in s:
                    continue
                ctx = current_class or "Unknown"
                results.append((ctx, s))

    return results


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
                trans_elem = msg.find("translation")
                if source_elem is None or source_elem.text is None:
                    continue
                if trans_elem is not None and trans_elem.get("type") == "obsolete":
                    continue
                entries.add((context, source_elem.text))
    except Exception as e:
        print(f"Error parsing {ts_path}: {e}", file=sys.stderr)
        return None
    return entries


def find_missing_and_orphans(files):
    """Return (missing_tr_calls, orphan_ts_entries)."""
    ts_entries = extract_ts_entries(TS_FILE)
    if ts_entries is None:
        return [], []

    all_tr_calls = set()
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception:
            continue
        for ctx, s in extract_context_and_tr_calls(content):
            all_tr_calls.add((ctx, s))

    missing = sorted(
        (ctx, s) for ctx, s in all_tr_calls if (ctx, s) not in ts_entries
    )
    orphans = sorted(
        (ctx, s) for ctx, s in ts_entries if (ctx, s) not in all_tr_calls
    )
    return missing, orphans


# ── Main ────────────────────────────────────────────────────────────────

def _reconfigure_stdout():
    """Windows GBK console can't encode certain Unicode chars (e.g. U+9FFF).

    Reconfigure stdout to UTF-8 so that print() doesn't raise
    UnicodeEncodeError.  Safe no-op if the stream doesn't support
    reconfigure (e.g. piped output on Unix).
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _reconfigure_stdout()

    parser = argparse.ArgumentParser(description="i18n audit for BallonsTranslator")
    parser.add_argument("--ci", action="store_true", help="Exit non-zero on findings")
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
            print(f"  {fname}:{lineno}  \"{text}\"")
    else:
        print("\n[HARDCODED CHINESE] None found.")

    # Check 2 & 3: tr() <-> .ts coverage — ui/ + modules/
    all_files = find_all_py_files()
    missing, orphans = find_missing_and_orphans(all_files)

    if missing:
        exit_code |= 2
        print(f"\n[MISSING .ts ENTRIES] {len(missing)} self.tr() call(s) without "
              f"a matching <message> in zh_CN.ts:")
        for ctx, s in missing:
            print(f"  [{ctx}] \"{s}\"")
    else:
        print("\n[MISSING .ts ENTRIES] All self.tr() calls have .ts entries.")

    if orphans:
        exit_code |= 4
        print(f"\n[ORPHAN .ts ENTRIES] {len(orphans)} active .ts entry(ies) with no "
              f"matching self.tr() call:")
        for ctx, s in orphans:
            print(f"  [{ctx}] \"{s}\"")
    else:
        print("\n[ORPHAN .ts ENTRIES] None found.")

    if exit_code == 0:
        print("\n[PASS] i18n check passed.")
    else:
        print(f"\n[FAIL] i18n check failed (exit code {exit_code}).")

    if args.ci:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
