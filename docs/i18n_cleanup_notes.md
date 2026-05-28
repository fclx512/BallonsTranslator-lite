# i18n Cleanup Guide (for AI agents)

This document captures patterns, pitfalls, and design decisions from the i18n cleanup
so future AI agents can avoid repeating the same work.

## Current State (May 2026)

| Check | Count | Notes |
|-------|-------|-------|
| Hardcoded Chinese | 0 | All handled |
| Missing .ts entries | 0 | Fully synced |
| Orphans (reported) | 37 | **All false positives** — see below |
| Total .ts messages | 639 | Compiled OK |
| Language count | 2 | English + zh_CN |

## The 37 "Orphans" Are False Positives

The i18n_check.py orphan detection works by regex-scanning Python source for
`self.tr("literal string")`. It cannot detect:

### 1. Variable-based tr() — 27 entries

| Context | Count | Pattern |
|---------|-------|---------|
| `ThreadBase` | 6 | `self.tr(self._thread_error_msg)` — class variable assigned in subclasses |
| `_ShortcutRow` | 21 | `self.tr(_ACTION_NAMES.get(action_id, action_id))` — dictionary lookup in `ui/configpanel.py` |

**All work at runtime.** Qt resolves the string value and looks it up in the .qm
under the correct context. The regex simply cannot see the literal because it's
not a literal — it's indirection.

### 2. Multiline tr() — 9 entries

| Context | Count | Pattern |
|---------|-------|---------|
| `UpdateThread` | 2 | `self.tr('line1\nline2')` — string spans 2 source lines |
| `ProfileManagerDialog` | 1 | `self.tr("Enable this for models..."\n"Vision-capable...")` — implicit string concatenation across lines |
| `ImgtransThread` | 1 | `self.tr(' is required for ' + self.translator.name)` — concatenation with variable |

The regex `self\.tr\("((?:[^"\\]|\\.)*)"\)` only matches single-line strings.
Multiline or concatenated arguments are invisible to it.

### Decision: Keep All 37

They produce exit code 4 (orphan bit). Never prune these:
- They are in the correct .ts context and resolve correctly at runtime.
- Pruning them would break translations for shortcuts, error messages, and progress text.

## Key Patterns Learned

### When a class is renamed, update .ts context

If `class OldName` is renamed to `class NewName`, all its .ts messages under
`<name>OldName</name>` must be moved to `<name>NewName</name>`. Otherwise the
translations silently stop working — Qt looks up by class name.

This happened with:
- `ShortcutEditor` → `_ShortcutRow` (27 action-name messages)
- `AiChatPanel` → `ChangeReviewWindow` (2 messages)
- `FontFormatPanel` → `ConfigPanel` ("Effect")
- `TranslateThread` → `ImgtransThread` (" is required for ")
- `ProgressMessageBox` → `ImgtransProgressMessageBox` (4 progress messages)

### Duplicate context blocks in .ts

The `_normalize_ts()` function in `ts_auto_fill.py` merges duplicate `<context>`
blocks with the same `<name>`. This happened with `MainWindow` having two
separate context blocks (61 + 11 messages). The first block's entries were being
silently dropped.

### Format strings with `{` are auto-skipped

The regex `_extract_tr_calls` in `ts_auto_fill.py` skips strings containing `{`.
These are treated as Python format strings. If one genuinely needs translation,
the .ts entry must be added manually.

### Module param descriptions

Module parameters with `description` fields in `utils/config.py` use
`self.tr(variable_name)` at runtime (the description value gets passed to tr()).
The regex cannot detect these. They live under `<context>ParamWidget</context>`
in .ts and are explicitly excluded from orphan detection.

## What Was Done

### Batch 1: Fill missing entries
`python scripts/ts_auto_fill.py --fill-missing --apply`
Added 24 missing `self.tr()` entries as `type="unfinished"`.

### Batch 2: Delete true orphans + move context entries
Created `scripts/_ts_cleanup.py` (deleted after use) that:
- Deleted 56 truly orphaned messages (classes/code removed)
- Moved 31 messages to correct contexts
- Removed 9 empty context blocks

### Batch 3: Handle hardcoded Chinese
49 strings in `ui/`:

| Action | Count | Category |
|--------|-------|----------|
| `self.tr()` wrap | 7 | API error messages (ai_chat_worker.py) |
| Change to English | 12 | LOGGER + error data (io_thread.py) |
| Change docstrings to English | 16 | Single-line docstrings |
| Add exclusion | 11 | Font test chars "啊""木""木fg", CJK range "一""鿿", language param "简体中文" |
| Skip via checker fix | 4 | Single-line docstring detection improved |

### i18n_check.py improvements
- Added `NON_UI_PATTERNS` set for font test chars and CJK range checks
- Added single-line triple-quoted docstring skip
- Console encoding: reconfigure stdout to UTF-8 on Windows (was `UnicodeEncodeError`)

## Running the Pipeline

### Add new tr() calls

```bash
python scripts/ts_auto_fill.py --fill-missing --apply
# then edit zh_CN.ts to fill translations for type="unfinished" entries
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm
python scripts/i18n_check.py
```

### Prune orphans

```bash
python scripts/ts_auto_fill.py --prune           # dry-run
python scripts/ts_auto_fill.py --prune --apply    # write
```

### Full CI check

```bash
python scripts/i18n_check.py --ci
```

Non-zero exit if any issues:
- bit 1: hardcoded Chinese
- bit 2: missing .ts entries
- bit 4: orphan .ts entries

Current expected status: `i18n_check.py --ci` exits with 4 (orphans only, all false positives).
