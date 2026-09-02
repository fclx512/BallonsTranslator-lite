#!/usr/bin/env python3
"""Synchronize zh_CN.ts with self.tr() calls in Python source code.

Two operations:
  1. Fill missing: add self.tr() calls not yet in .ts as type="unfinished"
  2. Prune orphans: remove .ts entries with no matching self.tr() call

Dry-run by default. Use --apply to write changes. After writing, the
.qm is recompiled automatically.

Usage:
  python scripts/ts_auto_fill.py                        # dry-run report
  python scripts/ts_auto_fill.py --apply                 # fill + prune (write)
  python scripts/ts_auto_fill.py --fill-missing          # dry-run fill only
  python scripts/ts_auto_fill.py --fill-missing --apply  # fill only (write)
  python scripts/ts_auto_fill.py --prune                 # dry-run prune only
  python scripts/ts_auto_fill.py --prune --apply         # prune only (write)
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from io import StringIO
from pathlib import Path

# 便携 Python 开启 safe_path 时不自动加脚本目录，自举以导入同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from i18n_common import (
    KNOWN_ORPHAN_CONTEXTS,
    TS_FILE,
    gather_tr_calls,
    is_obsolete_ts_msg,
    reconfigure_stdout,
)


# ── ts parsing ──────────────────────────────────────────────────────────


def _normalize_ts(root):
    """Merge duplicate <context> blocks with the same <name>. Mutates in place."""
    seen = {}
    for ctx in list(root.findall("context")):
        name_el = ctx.find("name")
        if name_el is None or name_el.text is None:
            continue
        name = name_el.text
        if name in seen:
            for msg in list(ctx.findall("message")):
                seen[name].append(msg)
            root.remove(ctx)
        else:
            seen[name] = ctx
    # Remove empty context blocks
    for ctx in list(root.findall("context")):
        if not ctx.findall("message"):
            root.remove(ctx)


def _parse_ts(path):
    """Parse .ts, return (tree, root, ctx_map, ts_entries).

    ctx_map: {context_name: (ctx_element, [(msg_element, source_text), ...])}
    ts_entries: set of (context_name, source_text)

    Duplicate context blocks are merged into one during parsing.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    # Gather all existing entries as a set (correct even with duplicates)
    ts_entries = set()
    for ctx in root.findall("context"):
        name_el = ctx.find("name")
        if name_el is None or name_el.text is None:
            continue
        for msg in ctx.findall("message"):
            src = msg.find("source")
            if src is None or src.text is None:
                continue
            if is_obsolete_ts_msg(msg):
                continue
            ts_entries.add((name_el.text, src.text))

    # Normalize: merge duplicates so ctx_map is reliable
    _normalize_ts(root)

    # Build ctx_map from normalized tree
    ctx_map = {}
    for ctx in root.findall("context"):
        name_el = ctx.find("name")
        if name_el is None or name_el.text is None:
            continue
        msgs = []
        for msg in ctx.findall("message"):
            src = msg.find("source")
            if src is None or src.text is None:
                continue
            if is_obsolete_ts_msg(msg):
                continue
            msgs.append((msg, src.text))
        ctx_map[name_el.text] = (ctx, msgs)

    return tree, root, ctx_map, ts_entries


def _make_message(source_text):
    """Build <message><source>TEXT</source><translation type="unfinished"/></message>."""
    msg = ET.SubElement(ET.Element("_"), "message")
    ET.SubElement(msg, "source").text = source_text
    ET.SubElement(msg, "translation", type="unfinished").text = ""
    return msg


def _write_ts(tree, path):
    """Serialize ElementTree to .ts, preserving the XML declaration and DOCTYPE."""
    ET.indent(tree, space="    ")
    buf = StringIO()
    tree.write(buf, encoding="unicode", xml_declaration=False)
    body = buf.getvalue()
    # Inject custom declaration and DOCTYPE
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + body,
        encoding="utf-8",
    )


def _recompile_qm():
    """Recompile the .qm from the freshly written .ts (best effort)."""
    import qm_compile

    qm = TS_FILE.with_suffix(".qm")
    qm_compile.compile_ts(str(TS_FILE), str(qm))


# ── main ────────────────────────────────────────────────────────────────


def main():
    reconfigure_stdout()
    parser = argparse.ArgumentParser(
        description="Synchronize zh_CN.ts with self.tr() calls in source code."
    )
    parser.add_argument(
        "--fill-missing",
        action="store_true",
        help="Add missing entries as type='unfinished'.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove orphan entries.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes (without this, dry-run only).",
    )
    args = parser.parse_args()

    # Determine operations:
    #   --apply alone means both.  --apply with specific flags means only those.
    if args.apply:
        if args.fill_missing or args.prune:
            do_fill = args.fill_missing
            do_prune = args.prune
        else:
            do_fill = True
            do_prune = True
    else:
        do_fill = args.fill_missing
        do_prune = args.prune

    # ── 1. Gather self.tr() calls from source ──────────────────────────
    all_tr = gather_tr_calls()

    # ── 2. Parse .ts ───────────────────────────────────────────────────
    tree, root, ctx_map, ts_entries = _parse_ts(TS_FILE)

    # ── 3. Compute missing and orphan sets ─────────────────────────────
    missing = sorted(all_tr - ts_entries)
    orphans = sorted(
        (ctx, s)
        for ctx, s in ts_entries
        if (ctx, s) not in all_tr
        and "{" not in s
        and ctx not in KNOWN_ORPHAN_CONTEXTS
    )

    # ── Report ─────────────────────────────────────────────────────────
    if missing:
        print(f"[MISSING] {len(missing)} entry(ies) not in zh_CN.ts:")
        for ctx, s in missing:
            print(f'  [{ctx}] "{s}"')
    else:
        print("[MISSING] None -- all tr() calls have .ts entries.")

    if orphans:
        print(f"\n[ORPHAN] {len(orphans)} entry(ies) with no matching tr() call:")
        for ctx, s in orphans:
            print(f'  [{ctx}] "{s}"')
    else:
        print("\n[ORPHAN] None.")

    changed = False

    # ── Prune ──────────────────────────────────────────────────────────
    if do_prune and orphans:
        for ctx, s in orphans:
            ctx_elem, msgs = ctx_map.get(ctx, (None, []))
            if ctx_elem is None:
                continue
            for msg, src in msgs:
                if src == s:
                    ctx_elem.remove(msg)
                    break
        changed = True
        print(f"\n  >> Removed {len(orphans)} orphan entry(ies).")

    # Rebuild ctx_map after prune (some contexts may have been emptied).
    # Must rebuild from the in-memory tree — re-parsing TS_FILE here would
    # read the stale on-disk file and silently drop the fill step below.
    if changed:
        for ctx_elem in list(root.findall("context")):
            if not ctx_elem.findall("message"):
                root.remove(ctx_elem)
        ctx_map = {}
        for ctx_elem in root.findall("context"):
            name_el = ctx_elem.find("name")
            if name_el is None or name_el.text is None:
                continue
            msgs = []
            for msg in ctx_elem.findall("message"):
                src = msg.find("source")
                if src is None or src.text is None:
                    continue
                if is_obsolete_ts_msg(msg):
                    continue
                msgs.append((msg, src.text))
            ctx_map[name_el.text] = (ctx_elem, msgs)

    # ── Fill missing ───────────────────────────────────────────────────
    if do_fill and missing:
        for ctx, s in missing:
            ctx_elem, _ = ctx_map.get(ctx, (None, []))
            if ctx_elem is None:
                ctx_elem = ET.SubElement(root, "context")
                ET.SubElement(ctx_elem, "name").text = ctx
                ctx_map[ctx] = (ctx_elem, [])
            ctx_elem.append(_make_message(s))
        changed = True
        print(f"\n  >> Added {len(missing)} missing entry(ies) as type='unfinished'.")

    # ── Write ──────────────────────────────────────────────────────────
    if changed:
        _write_ts(tree, TS_FILE)
        print(f"\n[APPLIED] {TS_FILE} updated.")
        _recompile_qm()
    else:
        if not args.apply and not args.fill_missing and not args.prune:
            print(
                "\nDry-run. Pass --apply, --fill-missing, or --prune to write changes."
            )
        else:
            print("\nNothing to do.")


if __name__ == "__main__":
    main()
