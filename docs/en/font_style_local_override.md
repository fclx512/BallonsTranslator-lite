# Font Style Local Override — Technical Summary

## Problem Description

In BallonsTranslator, text boxes (`TextBlkItem`) are internally based on Qt's `QTextDocument`, which natively supports per-character independent font styles (different fonts, sizes, bold/italic, etc. within the same text box). However, when applying changes via the font format panel controls or Text Style Presets, the operation overwrites the formatting of the **entire text box**, ignoring any current text selection and applying the changes to the full block.

Goal: Change to **smart override** — when the entire text box is selected, apply full-block formatting; when only part of the text is selected, only override the font style of the selected portion.

---

## Architecture Overview

### Core Data Model

```
utils/fontformat.py ─── FontFormat data class
    ├── font_family, font_size, bold, italic, underline
    ├── frgb (foreground color), srgb (stroke color), stroke_width
    ├── alignment, vertical, line_spacing, letter_spacing
    ├── opacity, shadow_*, gradient_*
    └── _style_name (font style name, e.g. "Bold", "Regular")

utils/textblock.py ─── TextBlock data class
    └── fontformat: FontFormat  (block-level default font format)

ui/textitem.py ─── TextBlkItem (QGraphicsTextItem subclass)
    ├── QTextDocument internally stores per-character QTextCharFormat
    ├── get_fontformat() ── reads format from current cursor, merges into FontFormat
    ├── set_fontformat() ── writes FontFormat into QTextDocument (block-level replacement)
    ├── setFontFamily() ── sets font family (iterates over fragments)
    ├── setFontWeight/Italic/Underline/Color/Size ── single property methods
    ├── _before_set_ffmt() ── cursor/selection management before format application
    └── _after_set_ffmt() ── cursor restoration after format application
```

### Two Paths for Format Changes

**Path A: Single Property Change** (e.g., clicking Bold, changing the font family dropdown)

```
FontFormatPanel.on_param_changed()
  → fontformat_commands.ffmt_change_xxx()
    → TextBlkItem.setFontXxx()
      → _before_set_ffmt(set_selected, restore_cursor)  // selection management
      → execute format change
      → _after_set_ffmt(...)                             // cursor restoration
```

- Global mode (`is_global=True`): `set_selected=False` → always full document
- Local mode (`is_global=False`): `set_selected=True` → checks for text selection

**Path B: Style Preset Application** (clicking the → arrow on a preset label)

```
TextStyleLabel.on_applybtn_clicked()
  → TextStylePresetPanel.apply_fontfmt signal
    → SceneTextManager.onFormatTextblks()
      → apply_fontformat(fmt)
        → ApplyFontformatCommand.redo()
          → TextBlkItem.set_fontformat(fmt, set_char_format=True)
            → entire QTextDocument format is replaced
```

---

## Root Cause Analysis

### 1. `setFontFamily` Ignores Selection Parameters

**File**: `ui/textitem.py`, method `setFontFamily` (original lines 767-776)

```python
# Original code
def setFontFamily(self, value, style_name="", repaint_background=True,
                  set_selected=False, restore_cursor=False):
    self.repainting = True
    cursor = self.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)  # ← forces full selection!
    self._doc_set_font_family(value, style_name, cursor)
    ...
```

Problem: The parameters `set_selected` and `restore_cursor` are declared but **never used**. The method always creates a new cursor and selects the entire document, so `_doc_set_font_family` (which already supports filtering fragments by selection range) always receives a cursor in full-selection state.

Other similar methods (`setFontWeight`, `setFontItalic`, `setFontUnderline`, `setFontColor`, `setFontSize`, `setGradientEnabled`) correctly use the `_before_set_ffmt` / `_after_set_ffmt` pattern — only `setFontFamily` does not.

### 2. `set_fontformat` Always Replaces the Full Document

**File**: `ui/textitem.py`, method `set_fontformat` (original lines 633-711)

This method is the final executor of the style preset "Apply" button, called by `ApplyFontformatCommand.redo()` with `set_char_format=True`.

```python
# Original code key snippet (lines 670-678)
cursor.setCharFormat(format)
cursor.select(QTextCursor.SelectionType.Document)  # ← forces full selection
cursor.setBlockCharFormat(format)
if set_char_format:
    cursor.setCharFormat(format)  # ← under full selection, replaces all char formats
cursor.clearSelection()
```

Problem: Regardless of whether there is a current text selection, it always executes `cursor.select(Document)` to select the full document, then `setCharFormat` overwrites all character formats. There is no option for the caller to "preserve the selection."

---

## Fix Plan

### Fix 1: `setFontFamily` Uses `_before_set_ffmt` / `_after_set_ffmt` Pattern

**File**: `ui/textitem.py:778-787`

```python
def setFontFamily(self, value: str, style_name: str = "", repaint_background: bool = True,
                  set_selected: bool = False, restore_cursor: bool = False):
    cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
    self.repainting = True
    self._doc_set_font_family(value, style_name, cursor)
    self.repainting = False
    if not set_selected or after_kwargs['has_set_all']:
        self.fontformat.font_family = value          # only update block-level format on full-block override
        if style_name:
            self.fontformat._style_name = style_name
    self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)
```

**Key Points**:

| Scenario | `set_selected` | `has_selection()` | `has_set_all` | Cursor Behavior | `fontformat` Update |
|----------|:---:|:---:|:---:|------|:---:|
| Global mode | False | — | False | New cursor created, select full document | ✅ Yes |
| Local mode, no text selection | True | False | True | Cursor expanded to full document | ✅ Yes |
| **Local mode, has text selection** | **True** | **True** | **False** | **Keep original selection** | **❌ No** |

- `_before_set_ffmt(set_selected, restore_cursor)` returns a cursor with the correct selection range
- `_doc_set_font_family` already filters fragments by `cursor.selectionStart()` / `cursor.selectionEnd()` internally — no modification needed
- `self.fontformat.font_family` (block-level default format) is only updated when the format is applied to the **entire text box**; the original block-level default is preserved on local override
- `_after_set_ffmt` handles cursor position restoration, background repaint, and `endEditBlock()` cleanup

### Fix 2: `set_fontformat` Supports Selection Awareness

**File**: `ui/textitem.py:633-689`

```python
def set_fontformat(self, ffmat: FontFormat, set_char_format=False,
                   set_stroke_width=True, set_effect=True, respect_selection=False):
    ...
    after_kwargs = None
    if respect_selection:
        cursor, after_kwargs = self._before_set_ffmt(set_selected=True, restore_cursor=True)
    else:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)  # original logic
    ...
    # Format template construction (font, format configuration) unchanged
    ...
    if respect_selection:
        cursor.setCharFormat(format)
        cursor.setBlockCharFormat(format)
        if set_char_format:
            cursor.setCharFormat(format)           # only applies to selection
        self._after_set_ffmt(cursor, repaint_background=False,
                            restore_cursor=True, **after_kwargs)
    else:
        cursor.setCharFormat(format)
        cursor.select(QTextCursor.SelectionType.Document)  # original full-select logic
        cursor.setBlockCharFormat(format)
        if set_char_format:
            cursor.setCharFormat(format)
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
    ...
```

**New parameter** `respect_selection=False`:

- `False` (default): behavior is identical to the original code, full-block override
- `True`: uses `_before_set_ffmt` to obtain a selection-aware cursor; character-level properties (font family, font size, bold/italic, color, etc.) only affect the selection; block-level properties (alignment, line spacing, stroke, shadow, opacity, etc.) still apply to the full block

### Fix 3: `ApplyFontformatCommand` Passes Selection Flag

**File**: `ui/textedit_commands.py:84-87`

```python
def redo(self):
    for item, edit in zip(self.items, self.trans_widget_lst):
        item.set_fontformat(self.new_fmt, set_char_format=True,
                           respect_selection=True)   # ← new parameter
        edit.document().clearUndoRedoStacks()
```

`undo()` does not pass `respect_selection=True`, because the undo operation restores full-block content via `setHtml()`, which must perform a full replacement.

---

## Unmodified Paths

The following methods **do not need modification**, either because they already correctly use selections, or because they are block-level properties that should not be affected by selection:

| Method | Status | Explanation |
|--------|:---:|------|
| `setFontWeight` | ✅ Correct | Uses `_before_set_ffmt(set_selected, restore_cursor)` |
| `setFontItalic` | ✅ Correct | Same |
| `setFontUnderline` | ✅ Correct | Same |
| `setFontColor` | ✅ Correct | Same |
| `setFontSize` | ✅ Correct | Same |
| `setGradientEnabled` | ✅ Correct | Same |
| `setStrokeWidth` | ⏭️ Skipped | Block-level property, hardcoded `set_selected=False` |
| `setAlignment` | ⏭️ Skipped | Block-level property, hardcoded `set_selected=False` |
| `setLineSpacing` | ⏭️ Skipped | Layout-level property, set via `self.layout.setLineSpacing()` |
| `setLetterSpacing` | ⏭️ Skipped | Layout-level property; vertical text set via layout, horizontal text full-block override |
| `setOpacity` | ⏭️ Skipped | QGraphicsItem-level effect, not a per-character property |
| `setRelFontSize` | ⏭️ Skipped | Relative font size scaling, iterates over fragments but does not filter by selection (low-frequency operation, does not affect main experience) |

---

## `_before_set_ffmt` / `_after_set_ffmt` Detailed Explanation

These two methods are the core mechanism of this fix. Understanding them aids future extensions.

### `_before_set_ffmt(set_selected, restore_cursor)`

```python
def _before_set_ffmt(self, set_selected: bool, restore_cursor: bool):
    self.is_formatting = True
    cursor = self.textCursor()

    cursor_pos = None
    if restore_cursor:
        cursor_pos = (cursor.position(), cursor.anchor().__pos__())

    if set_selected:
        has_set_all = not cursor.hasSelection()
        if has_set_all:
            cursor.select(QTextCursor.SelectionType.Document)
    else:
        has_set_all = False
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)

    cursor.beginEditBlock()
    return cursor, dict(cursor_pos=cursor_pos, has_set_all=has_set_all)
```

**Return values**:
- `cursor`: QTextCursor with the correct selection range
- `after_kwargs`: `{cursor_pos: (pos, anchor) | None, has_set_all: bool}`
  - `cursor_pos` is the original cursor position tuple when `restore_cursor=True`, used for later restoration
  - `has_set_all` is `True` when the cursor had no text selection (or `set_selected=False` full-block mode — note the exception)

**Key semantic distinction**:

The semantics of `has_set_all` is "the cursor had no selection and was expanded to full-select," **not** "whether the format applies to the full document." When `set_selected=False`, even if the cursor does select the full document, `has_set_all` is set to `False`. Therefore, determining "whether the format applies to the full block" should use:

```python
is_full_block = not set_selected or has_set_all
```

### `_after_set_ffmt(cursor, repaint_background, restore_cursor, cursor_pos, has_set_all)`

```python
def _after_set_ffmt(self, cursor, repaint_background, restore_cursor,
                    cursor_pos, has_set_all):
    if restore_cursor:
        if cursor_pos is not None:
            pos1, pos2 = cursor_pos
            if has_set_all:
                cursor.setPosition(pos1)                        # restore single-point cursor
            else:
                cursor.setPosition(min(pos1, pos2))
                cursor.setPosition(max(pos1, pos2), KeepAnchor) # restore selection
            self.setTextCursor(cursor)

    if repaint_background:
        self.repaint_background()

    cursor.endEditBlock()
    self.is_formatting = False
```

---

## Complete Call Chain

```
User clicks font format control (e.g., Bold checkbox / Font Family dropdown)
│
├── Path A: Single Property Change
│   FontFormatPanel.on_param_changed(param_name, value)
│   │
│   ├── global_mode() == True
│   │   └── func(..., is_global=True)
│   │       └── set_kwargs = {set_selected: False, restore_cursor: False}
│   │           └── TextBlkItem.setFontXxx(value, set_selected=False, ...)
│   │               └── _before_set_ffmt(False, False)
│   │                   └── new cursor, select Document → full-block override ✅
│   │
│   └── global_mode() == False
│       └── func(..., is_global=False, blkitems=[textblk_item])
│           └── set_kwargs = {set_selected: True, restore_cursor: True}
│               └── TextBlkItem.setFontXxx(value, set_selected=True, ...)
│                   └── _before_set_ffmt(True, True)
│                       ├── has text selection → keep selection → override selection only ✅ (after fix)
│                       └── no text selection → expand to full select → full-block override ✅
│
└── Path B: Style Preset Apply
    TextStyleLabel.on_applybtn_clicked()
    └── apply_fontfmt.emit(fontfmt)
        └── SceneTextManager.onFormatTextblks(fmt)
            └── apply_fontformat(fmt)
                └── ApplyFontformatCommand(items, ...)
                    ├── redo(): set_fontformat(fmt, set_char_format=True, respect_selection=True)
                    │   └── _before_set_ffmt(True, True)
                    │       ├── has text selection → character properties override selection only ✅ (after fix)
                    │       └── no text selection → full-block override ✅
                    │
                    └── undo(): setHtml(html) + set_fontformat(fmt)
                        └── full-block restore (does not pass respect_selection)
```

---

## Affected Files

| File | Change |
|------|--------|
| `ui/textitem.py:778-787` | `setFontFamily` refactored to `_before_set_ffmt` / `_after_set_ffmt` pattern |
| `ui/textitem.py:633-689` | `set_fontformat` added `respect_selection` parameter and selection-aware branch |
| `ui/textedit_commands.py:84-87` | `ApplyFontformatCommand.redo` passes `respect_selection=True` |
