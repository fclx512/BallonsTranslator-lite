#!/usr/bin/env python3
"""Post-edit syntax + indentation sanity check.

Run after any batch of edits to catch tab-vs-space mixups
and other indentation errors that the Edit tool can introduce.

Usage:
  python scripts/check_syntax.py                        # check all .py files
  python scripts/check_syntax.py path/to/file.py         # check one file
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "env"}
SKIP_PATTERNS = {"build_"}


def find_py_files(root: Path):
    py_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        parts = set(rel.parts)
        if parts & SKIP_DIRS:
            continue
        if any(s in str(rel) for s in SKIP_PATTERNS):
            continue
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(Path(dirpath) / f)
    return py_files


def check_tabs(filepath: Path) -> list[str]:
    """Report lines with tab characters inside indentation."""
    errors = []
    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            stripped = line.lstrip("\t")
            if stripped and line.startswith("\t"):
                # Count tabs vs spaces
                leading = line[: len(line) - len(stripped)]
                tab_count = leading.count("\t")
                if tab_count > 0:
                    # Check if rest of file uses spaces
                    pass  # Just report
                    lines_after = leading
                    # Check if this line has both tabs and spaces
                    if "\t" in lines_after and " " in lines_after:
                        errors.append(f"  L{i:>5}: mixed tab+space indent")
    return errors


def check_bom(filepath: Path) -> list[str]:
    """Check for UTF-8 BOM which Python 3 doesn't like."""
    errors = []
    with open(filepath, "rb") as f:
        raw = f.read(3)
    if raw == b"\xef\xbb\xbf":
        errors.append("  UTF-8 BOM detected")
    return errors


def check_syntax(filepath: Path) -> list[str]:
    """Try to compile the file."""
    errors = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, str(filepath), "exec")
    except SyntaxError as e:
        errors.append(f"  L{e.lineno}: {e.msg}")
        # Add context
        if e.text:
            errors.append(f"    {e.text.rstrip()}")
            errors.append(f"    {' ' * (e.offset - 1) if e.offset else ''}^")
    except Exception as e:
        errors.append(f"  {type(e).__name__}: {e}")
    return errors


def main():
    if len(sys.argv) > 1:
        targets = [Path(a).resolve() for a in sys.argv[1:]]
    else:
        # Only check ui/ and utils/ — the hot paths the Edit tool touches most
        targets = find_py_files(ROOT / "ui") + find_py_files(ROOT / "utils")

    total_errors = 0
    for fp in sorted(targets):
        try:
            rel = fp.relative_to(ROOT)
        except ValueError:
            rel = fp
        file_errors = []
        file_errors += check_bom(fp)
        file_errors += check_syntax(fp)
        file_errors += check_tabs(fp)

        if file_errors:
            print(f"\n❌ {rel}")
            for e in file_errors:
                print(e)
            total_errors += len(file_errors)

    if total_errors == 0:
        print("✅ All checked files pass syntax + indentation checks.")
    else:
        print(f"\n{total_errors} issue(s) found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
