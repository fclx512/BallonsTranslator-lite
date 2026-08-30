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

import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TS_FILE = PROJECT_ROOT / "translate" / "zh_CN.ts"

# Contexts whose .ts entries are never matched by a literal self.tr() call
# and are maintained by hand in zh_CN.ts.  Currently empty: module param
# descriptions (ParamWidget) are extracted via the AST rule below, and the
# former indirect-lookup tables (_ShortcutRow / ShortcutEditor / Canvas /
# PieMenu* …) have all been converted to explicit-context
# QCoreApplication.translate() literals.
KNOWN_ORPHAN_CONTEXTS = frozenset()

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
    # Explicit-context module-level tables (e.g. shortcut names in
    # ui/configpanel.py): first literal is the ts <context>.
    qt_translate_re = re.compile(
        r'QCoreApplication\.translate\(\s*("(?:[^"\\]|\\.)*")\s*,\s*("(?:[^"\\]|\\.)*")\s*\)',
        re.DOTALL,
    )
    qt_translate_sq_re = re.compile(
        r"QCoreApplication\.translate\(\s*('(?:[^'\\]|\\.)*')\s*,\s*('(?:[^'\\]|\\.)*')\s*\)",
        re.DOTALL,
    )

    def _unquote(s):
        s = s[1:-1]  # strip surrounding quotes
        s = s.replace('\\"', '"').replace("\\'", "'")
        s = s.replace("\\\\", "\\")
        s = s.replace("\\n", "\n").replace("\\t", "\t")
        return s

    for tr_regex in (tr_re, tr_sq_re):
        for m in tr_regex.finditer(content):
            s = _unquote(m.group(1))
            # Skip format strings containing placeholders
            if "{" in s:
                continue
            yield (_context_for(m.start()), s)

    for qt_regex in (qt_translate_re, qt_translate_sq_re):
        for m in qt_regex.finditer(content):
            ctx = _unquote(m.group(1))
            s = _unquote(m.group(2))
            if "{" in s:
                continue
            yield (ctx, s)


def extract_param_descriptions(content: str, fpath: Path):
    """Yield ("ParamWidget", description) from module param dict literals.

    Module params are plain data (``{"param": {"value": …, "description":
    "…"}}``) rendered via ``ParamWidget.tr(params["description"])`` — a
    value lookup the regex extractor cannot see.  This AST rule covers it:

    - files under ``modules/`` (except the translation-agent ``agent/``
      package, whose "description" keys are LLM function-calling prompts
      that must stay English): every dict literal with a string
      ``description`` key;
    - everywhere else: only dicts that *also* carry a ``value`` key, so
      LLM tool schemas (``utils/ai_tools.py``, whose descriptions are
      already Chinese prompt text) stay out.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    rel = fpath.relative_to(PROJECT_ROOT).parts
    module_scope = len(rel) >= 1 and rel[0] == "modules" and "agent" not in rel

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            k.value if isinstance(k, ast.Constant) and isinstance(k.value, str)
            else None
            for k in node.keys
        ]
        if "description" not in keys:
            continue
        if not module_scope and "value" not in keys:
            continue
        val = node.values[keys.index("description")]
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        desc = val.value
        if "{" in desc:
            continue
        yield ("ParamWidget", desc)


def gather_tr_calls():
    """Return set of (context, source) from all scanned Python files."""
    result = set()
    for fpath in find_py_files():
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception:
            continue
        result.update(extract_tr_calls(content))
        result.update(extract_param_descriptions(content, fpath))
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
