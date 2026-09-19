#!/usr/bin/env python3
"""Trim docs/daily_log.md to the most recent KEEP_DAYS calendar days.

Enforcement for the "仅保留最近 3 天记录" rule in AGENTS.md -- the constraint
is applied by script (pre-commit hook, see scripts/hooks/pre-commit), not by
prompting the writing agent, so stale entries cannot accumulate.

Parsing is by date headings (`## YYYY-MM-DD`); kept sections are spliced back
verbatim (no reformatting, EOL-safe). Sections without a parseable date are
always kept, so a malformed heading can never destroy content.

Usage:
  python scripts/trim_daily_log.py            # trim in place (no-op when clean)
  python scripts/trim_daily_log.py --check    # exit 1 if trimming would change the file
  python scripts/trim_daily_log.py <path>     # operate on another file
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

KEEP_DAYS = 3  # keep today and the previous KEEP_DAYS-1 calendar days

DATE_HEADING = re.compile(r"(?m)^## (\d{4}-\d{2}-\d{2})\b")


def trim(text: str, today: date) -> tuple[str, list[str]]:
    """Return (new_text, dropped_dates); kept sections stay byte-identical."""
    matches = list(DATE_HEADING.finditer(text))
    if not matches:
        return text, []
    kept: list[str] = []
    dropped: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entry_date = date.fromisoformat(m.group(1))
        if (today - entry_date).days < KEEP_DAYS:
            kept.append(text[m.start():end])
        else:
            dropped.append(m.group(1))
    return text[:matches[0].start()] + "".join(kept), dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=None,
                        help="log file (default: <repo>/docs/daily_log.md)")
    parser.add_argument("--check", action="store_true",
                        help="report only; exit 1 when trimming would change the file")
    args = parser.parse_args()

    path = (Path(args.path) if args.path
            else Path(__file__).resolve().parent.parent / "docs" / "daily_log.md")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new_text, dropped = trim(text, date.today())

    if new_text == text:
        print(f"daily_log: clean ({path})")
        return 0

    if args.check:
        print(f"daily_log: would drop {len(dropped)} day(s): {', '.join(dropped)}")
        return 1

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    print(f"daily_log: dropped {len(dropped)} day(s): {', '.join(dropped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
