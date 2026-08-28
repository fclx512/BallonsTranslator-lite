"""Shared helpers for i18n_check.py (audit) and ts_auto_fill.py (sync).

Single source of truth for:
  - Python file discovery (ui/ + modules/ + utils/)
  - self.tr() extraction with class-context attribution
  - the orphan whitelist (contexts whose .ts entries are maintained by
    hand because the code calls self.tr(variable) indirectly)

Both consumers import this module; the whitelist MUST NOT be duplicated
elsewhere — a drifted copy would make ts_auto_fill prune hand-maintained
entries that i18n_check considers expected orphans.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TS_FILE = PROJECT_ROOT / "translate" / "zh_CN.ts"

# Contexts whose .ts entries are never matched by a literal self.tr() call:
# the code renders them via self.tr(variable) / canvas.tr(key) or they are
# module param descriptions.  They are maintained by hand in zh_CN.ts, so
# i18n_check would report them as orphans on every run — treat as expected,
# and ts_auto_fill must never prune them.
KNOWN_ORPHAN_CONTEXTS = frozenset({"_ShortcutRow", "ShortcutEditor", "ParamWidget"})

# Match self.tr("...") / self.tr('...') — single-line, for the hardcoded-
# Chinese cleaner in i18n_check.
TR_CALL_RE = re.compile(r'self\.tr\("((?:[^"\\]|\\.)*)"\)')
TR_CALL_SQ_RE = re.compile(r"self\.tr\('((?:[^'\\]|\\.)*)'\)")


def find_py_files():
    """Python files in ui/, modules/ and utils/ — the tr() coverage corpus."""
    files = []
    for scan_dir in ("ui", "modules", "utils"):
        dir_path = PROJECT_ROOT / scan_dir
        if dir_path.is_dir():
            files.extend(dir_path.rglob("*.py"))
    return sorted(files)


def extract_tr_calls(content: str):
    """Yield (context_class, tr_string) from Python source.

    Handles single-line ``self.tr("...")`` and multi-line variants
    where the string body is on a different line (e.g.
    ``self.tr(\\n    "text"\\n)``) via DOTALL regex.

    *Implicit* Python string concatenation across continuation lines
    is *not* supported (e.g. ``self.tr("part1 " "part2")``) — keep
    the whole translatable string as a single literal.
    """
    # Position-index of class definitions so each tr() call is attributed
    # to its enclosing class (regardless of line breaks).
    class_positions = []  # (byte_offset, class_name)
    for m in re.finditer(r"^\s*class\s+(\w+)\s*[(:]", content, re.MULTILINE):
        class_positions.append((m.start(), m.group(1)))

    def _context_for(pos):
        for cp, cn in reversed(class_positions):
            if cp < pos:
                return cn
        return "Unknown"

    tr_re = re.compile(r'self\.tr\(\s*("(?:[^"\\]|\\.)*")\s*\)', re.DOTALL)
    tr_sq_re = re.compile(r"self\.tr\(\s*('(?:[^'\\]|\\.)*')\s*\)", re.DOTALL)

    for tr_regex in (tr_re, tr_sq_re):
        for m in tr_regex.finditer(content):
            s = m.group(1)[1:-1]  # strip surrounding quotes
            s = s.replace('\\"', '"').replace("\\'", "'")
            s = s.replace("\\\\", "\\")
            s = s.replace("\\n", "\n").replace("\\t", "\t")
            # Skip format strings containing placeholders
            if "{" in s:
                continue
            yield (_context_for(m.start()), s)


def gather_tr_calls():
    """Return set of (context, source) from all scanned Python files."""
    result = set()
    for fpath in find_py_files():
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception:
            continue
        result.update(extract_tr_calls(content))
    return result


def is_obsolete_ts_msg(msg):
    """<message> or its <translation> carries type="obsolete"."""
    trans = msg.find("translation")
    return msg.get("type") == "obsolete" or (
        trans is not None and trans.get("type") == "obsolete"
    )


def reconfigure_stdout():
    """Windows GBK console can't encode certain Unicode chars (e.g. U+9FFF)."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
