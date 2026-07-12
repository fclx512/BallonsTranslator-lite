"""Add or update a <context> block in zh_CN.ts with reusable translations.

Usage:
    ./ballontrans_pylibs_win/python.exe scripts/add_ts_context.py ProfileManagerWidget
"""

import re
import sys
from pathlib import Path

TS_PATH = Path(__file__).resolve().parent.parent / "translate" / "zh_CN.ts"

# All self.tr() strings in ProfileManagerWidget (source only)
SOURCES = [
    " (built-in)",
    "+ Add",
    "0 = unlimited",
    "A valid API key is required to test the connection.",
    "API Key:",
    "All built-in profiles already exist.",
    "Basic Settings",
    "Confirm Delete",
    "Connected! API is reachable and credentials are valid.",
    "Connection & Rate Limiting:",
    "Connection Failed",
    "Connection Successful",
    "Connection timed out. Check the URL and network.",
    "Delay (s):",
    "Delete",
    "Delete profile \"{name}\"?",
    "Detail Level:",
    "Error",
    "Error: {err}",
    "Failed to fetch model list: {err}",
    "Failed to fetch model list. HTTP {code}",
    "Fetch Models",
    "Few-Shot Examples:",
    "Frequency Penalty:",
    "Host and API key are required to fetch the model list.",
    "Host is required.",
    "Host:",
    "HTTP {code}: {text}",
    "Max Tokens:",
    "Model:",
    "Name:",
    "New Profile",
    "No Change",
    "No models found.",
    "Notice",
    "OCR Prompt:",
    "OCR Settings (optional)",
    "OCR System Prompt:",
    "Optional system prompt for OCR.",
    "Presence Penalty:",
    "Profile:",
    "Prompt Template:",
    "Proxy:",
    "Reasoning Effort:",
    "Requests/min:",
    "Response Format:",
    "Restore Builtins",
    "Restored",
    "Restored {n} built-in profile(s).",
    "Select Model",
    "Temperature:",
    "Test",
    "Top P:",
    "Translation Settings (optional)",
    "Unlimited (leave empty)",
    "Vision support (for OCR)",
    "Warning",
    "e.g., My Custom API",
    "默认",
    # from FilterableListDialog (same strings, different context)
    "Cancel",
    "OK",
    "Search...",
]

# Strings that should NOT be translated (values, placeholders, technical terms)
SKIP_TRANSLATION = {"0 = unlimited", "默认"}


def gather_translations(ts_text: str) -> dict:
    """Build {source: translation} from all existing <context> blocks."""
    result = {}
    # Match each context block
    ctx_pattern = re.compile(
        r"<context>\s*<name>(.*?)</name>(.*?)</context>", re.DOTALL
    )
    msg_pattern = re.compile(
        r"<message>\s*<source>(.*?)</source>\s*(?:<translation>(.*?)</translation>)?",
        re.DOTALL,
    )
    for ctx_match in ctx_pattern.finditer(ts_text):
        ctx_body = ctx_match.group(2)
        for msg_match in msg_pattern.finditer(ctx_body):
            source = msg_match.group(1).strip()
            translation = msg_match.group(2)
            if translation is None or translation.strip() == "":
                translation = None
            else:
                translation = translation.strip()
            # Keep the first non-empty translation found
            if source not in result or result[source] is None:
                result[source] = translation
    return result


def make_message_xml(source: str, translation: str | None) -> str:
    lines = ["        <message>"]
    lines.append(f"            <source>{_esc(source)}</source>")
    if translation:
        lines.append(f"            <translation>{_esc(translation)}</translation>")
    else:
        lines.append("            <translation type=\"unfinished\"></translation>")
    lines.append("        </message>")
    return "\n".join(lines)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def context_block(name: str, translations: dict) -> str:
    parts = [f"    <context>", f"        <name>{name}</name>"]
    for src in SOURCES:
        if src in SKIP_TRANSLATION:
            continue
        trans = translations.get(src)
        parts.append(make_message_xml(src, trans))
    parts.append("    </context>")
    return "\n".join(parts)


def main():
    if len(sys.argv) < 2:
        print("Usage: add_ts_context.py <ContextName>")
        sys.exit(1)

    context_name = sys.argv[1]
    ts_text = TS_PATH.read_text(encoding="utf-8")

    # Gather all existing translations
    all_trans = gather_translations(ts_text)

    # Check if context already exists
    existing_ctx = re.search(
        rf"<context>\s*<name>{re.escape(context_name)}</name>.*?</context>",
        ts_text,
        re.DOTALL,
    )
    if existing_ctx:
        print(f"Context '{context_name}' already exists. Updating...")
        start, end = existing_ctx.span()
        new_block = context_block(context_name, all_trans)
        ts_text = ts_text[:start] + new_block + ts_text[end:]
    else:
        # Insert before closing </TS> tag
        new_block = context_block(context_name, all_trans)
        ts_text = ts_text.replace("</TS>", new_block + "\n</TS>")

    TS_PATH.write_text(ts_text, encoding="utf-8")
    print(f"Added/updated context '{context_name}' with {len(SOURCES) - len(SKIP_TRANSLATION)} messages.")
    print("Now run: python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm")


if __name__ == "__main__":
    main()
