"""Fix i18n ts file: add missing entries and remove orphans.

Missing entries to add (self.tr() calls without <message>):
  [ConfigPanel] "Punctuation Position", "Simplified Chinese", "Traditional Chinese"
  [MainWindow]  "Generating TIF thumbnails...", "Loading", "Loading project...",
                "Reading project data...", "Updating interface..."

Orphan entries to remove (<message> without self.tr() call):
  60 entries across various contexts (listed in i18n_check output).
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

TS_PATH = Path(__file__).resolve().parent.parent / "translate" / "zh_CN.ts"
assert TS_PATH.exists(), f"Not found: {TS_PATH}"

# ── Missing entries to add (context_name -> list of source texts) ──
MISSING: dict[str, list[str]] = {
    "ConfigPanel": [
        "Punctuation Position",
        "Simplified Chinese",
        "Traditional Chinese",
    ],
    "MainWindow": [
        "Generating TIF thumbnails...",
        "Loading",
        "Loading project...",
        "Reading project data...",
        "Updating interface...",
    ],
}

# ── Orphan entries to remove (full list from i18n_check.py output) ──
ORPHANS: dict[str, set[str]] = {
    "ConfigPanel": {
        "Browse",
        "Delete Theme",
        'Delete theme "%s"? This cannot be undone.',
        "Edit...",
        "Miscellaneous",
        'Optional — Photoshop.exe path for COM detection',
        "Photoshop executable (Photoshop.exe);;Executables (*.exe);;All files (*)",
        "Photoshop path",
        "Save",
        "Select Photoshop executable",
        "Startup",
        "built-in",
    },
    "LeftBar": {
        "Font Style Manager",
    },
    "MainWindow": {
        ' PSD(s).\n\nOutput:\n',
    },
    "ModelCheckDialog": {
        "Inpainting",
        "OCR",
        "Text Detection",
        "Translator",
        "Utility",
    },
    "ShortcutEditor": {
        "Edit",
        "General",
        "Navigation",
        "Search",
        "Tools",
        "View",
    },
    "StyleDetail": {
        "Apply",
        "Change alignment",
        "Change font flags",
        "Change stroke",
        "Change text color",
    },
    "TextAdvancedFormatPanel": {
        "Center",
        "Punctuation Alignment",
        "Upper-Right",
    },
    "_ShortcutRow": {
        "Bold",
        "Delete",
        "Delete (alt)",
        "Draw Board",
        "Escape",
        "Global Search",
        "Hand Tool",
        "Inpaint",
        "Inpaint Tool",
        "Italic",
        "Merge Tool",
        "Page Down",
        "Page Down (alt)",
        "Page Search",
        "Page Up",
        "Page Up (alt)",
        "Pen Tool",
        "Preview",
        "Rect Tool",
        "Redo",
        "Select All",
        "Text Block",
        "Text Editor",
        "Underline",
        "Undo",
        "Zoom In",
        "Zoom Out",
    },
    # These contexts are not directly in the xml — they may be sub-contexts
    # that appear as <context><name>...</name> in the ts. Let the scanner find them.
}

# Also check these contexts that appeared in the orphan list
EXTRA_ORPHAN_CONTEXTS = [
    "InpaintPanel",
    "PenConfigPanel",
    "RectPanel",
    "ProfileManagerDialog",
    "MergeDialog",
    "ShadowGradientPreview",
    "ParamWidget",
]

# ── Scan the .ts file for extra orphans in these less obvious contexts ──
# We'll build the full orphan set by scanning for all messages that lack
# a <location> element — those are leftovers from refactored code.
# But some orphans do have <location> — those are entries whose source
# code no longer calls self.tr(). We remove those too.


def normalize(s: str) -> str:
    """Normalize whitespace for comparison."""
    return re.sub(r"\s+", " ", s).strip()


def main():
    # Parse the XML preserving order (use ET)
    tree = ET.parse(str(TS_PATH))
    root = tree.getroot()

    # Collect all contexts
    contexts: list[ET.Element] = []
    context_names: set[str] = set()
    for ctx in root.iter("context"):
        name_el = ctx.find("name")
        if name_el is not None and name_el.text:
            context_names.add(name_el.text)
            contexts.append(ctx)

    # ── Phase 1: Remove orphan entries ──
    # Build the full orphan set by scanning for entries without <location>
    # in contexts that might have them.
    removed_count = 0
    for ctx in contexts:
        name_el = ctx.find("name")
        if name_el is None or name_el.text is None:
            continue
        ctx_name = name_el.text

        # Get the orphan set for this context
        orphan_sources = set()
        for key, vals in ORPHANS.items():
            # Normalize both for matching
            if key == ctx_name:
                orphan_sources.update(normalize(v) for v in vals)

        if not orphan_sources:
            continue

        # Check all messages in this context
        for msg in list(ctx.findall("message")):
            src_el = msg.find("source")
            if src_el is not None and src_el.text is not None:
                src_normalized = normalize(src_el.text)
                if src_normalized in orphan_sources:
                    ctx.remove(msg)
                    removed_count += 1

    if removed_count:
        print(f"Removed {removed_count} orphan entries.")
    else:
        print("No orphans removed.")

    # ── Phase 2: Add missing entries ──
    added_count = 0
    for ctx in contexts:
        name_el = ctx.find("name")
        if name_el is None or name_el.text is None:
            continue
        ctx_name = name_el.text

        missing_sources = MISSING.get(ctx_name, [])
        if not missing_sources:
            continue

        # Collect existing source texts
        existing = set()
        for msg in ctx.findall("message"):
            src_el = msg.find("source")
            if src_el is not None and src_el.text:
                existing.add(normalize(src_el.text))

        for src_text in missing_sources:
            if normalize(src_text) in existing:
                print(f"  [skip] already exists: [{ctx_name}] \"{src_text}\"")
                continue

            # Create new <message> element
            msg = ET.SubElement(ctx, "message")
            source = ET.SubElement(msg, "source")
            source.text = src_text
            trans = ET.SubElement(msg, "translation")
            trans.text = ""
            trans.set("type", "unfinished")
            added_count += 1
            print(f"  [add] [{ctx_name}] \"{src_text}\"")

    # ── Write back ──
    # ET writes <translation type="unfinished"/> as <translation type="unfinished" />
    # which is fine for Qt.
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)

    # Reconstruct the XML declaration
    output = '<?xml version="1.0" encoding="utf-8"?>\n' + raw

    TS_PATH.write_text(output, encoding="utf-8")
    print(f"\nDone. Added {added_count}, removed {removed_count}.")


if __name__ == "__main__":
    main()
