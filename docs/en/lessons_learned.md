# Lessons Learned & Technical Records

A collection of bugs, pitfalls, root causes, and fixes encountered during development.
Intended for both human developers and AI agents to avoid repeating the same work.

---

## 1. I18n & Translation System

### 1.1 QM Encoding Bug (June 2026)

**Problem:** `scripts/qm_compile.py` used `latin-1` encoding with `errors="replace"` for
string fields. Non-Latin-1 characters such as `—` (em dash, U+2014), `→`, `⚠`, `✓`
were silently replaced with `?`, causing:
- Qt `QTranslator` hash lookup failure → entries not found at runtime
- All strings containing any of these characters displayed in English

**Fix:** Changed to `s.encode("utf-8")`. Both hash computation and string storage now
use UTF-8 consistently.

**File:** `scripts/qm_compile.py` (`_iso8859_str` function → renamed semantically)

**Detection:**
```bash
python -c "
import struct
data = open('translate/zh_CN.qm', 'rb').read()
pos = 16
while pos < len(data):
    t, l = data[pos], struct.unpack('>I', data[pos+1:pos+5])[0]
    if t == 0x69:  # SECTION_MESSAGES
        raw = data[pos+5:pos+5+l]
        for keyword in [b'? recommended', b'? from', b'? Model']:
            if keyword in raw:
                print('QM corrupted — recompile!')
                break
    pos += 5 + l
"
```

### 1.2 i18n Checker — Multiline Regex Miss

**Problem:** `scripts/i18n_check.py` scanned with `self\.tr\("((?:[^"\\]|\\.)*)"\)`
(single-line only). Multiline `tr()` calls and implicit string concatenation were
invisible to the scanner:

```python
self.tr("line1\nline2")                    # spans 2 source lines
self.tr("part1 " "part2")                  # implicit concatenation
self.tr("Enable this for models..."
        "Vision-capable...")               # concatenation across lines
```

**Fix:** Regex updated to `re.DOTALL` mode, allowing newlines within `tr()` arguments:
```python
r'self\.tr\(\s*("(?:[^"\\]|\\.)*")\s*\)', re.DOTALL
```

**File:** `scripts/i18n_check.py`

### 1.3 `type="obsolete"` Detection Fix

**Problem:** The obsolete-entry check looked for `type="obsolete"` on `<translation>`,
but the standard places it on `<message>`. The check was effectively a no-op.

**Fix:** Check both `<message type="obsolete">` and `<translation type="obsolete">`.

**File:** `scripts/i18n_check.py`

### 1.4 Self-tr() String Concatenation Rule

`self.tr()` arguments must be a single string literal. **Never use Python implicit
concatenation:**

```python
# ✅ Correct — single string
self.tr("A long sentence that spans lines.")

# ❌ Wrong — checker regex cannot detect
self.tr("part one "
        "part two")
```

If a string is too long, break after `tr(` — the multiline regex handles it:
```python
self.tr("A long sentence the checker will find across lines.")
```

### 1.5 The 37 "False Positive" Orphans

`i18n_check.py --ci` exits with code 4 (orphan bit) — this is expected. All 37
reported orphans are false positives from two categories:

**Variable-based tr() — 27 entries:**
- `ThreadBase`: `self.tr(self._thread_error_msg)` — class variable assigned in subclasses
- `_ShortcutRow`: `self.tr(_ACTION_NAMES.get(action_id, action_id))` — dictionary lookup

Both resolve correctly at runtime via Qt's `.qm` lookup. **Never prune these.**

**Multiline tr() — 9 entries:** (see 1.2 above — now fixed, but older files may still
have entries the checker couldn't originally find)

### 1.6 Renaming a Class? Update .ts Context

If `class OldName` → `class NewName`, all `.ts` messages under `<name>OldName</name>`
must be moved to `<name>NewName</name>`. Qt looks up translations by class name.

This affected:
- `ShortcutEditor` → `_ShortcutRow` (27 messages)
- `AiChatPanel` → `ChangeReviewWindow` (2 messages)
- `FontFormatPanel` → `ConfigPanel` ("Effect")
- `TranslateThread` → `ImgtransThread`
- `ProgressMessageBox` → `ImgtransProgressMessageBox`

### 1.7 Duplicate Context Blocks in .ts

The `_normalize_ts()` function merges duplicate `<context>` blocks with the same
`<name>`. `MainWindow` once had two separate blocks (61 + 11 messages). The first
block's entries were silently dropped before the merge was implemented.

### 1.8 Module Param Descriptions

Module parameters with `description` fields use `self.tr(variable_name)` at runtime.
The i18n regex cannot detect these. They live under `<context>ParamWidget</context>`
and are explicitly excluded from orphan detection.

---

## 2. Code Cleanup Pitfalls

### 2.1 Auto-Cleanup Deleting Re-exports (May 2026)

**Problem:** An auto code-cleanup tool removed "unused" imports from files where
they served as **re-exports** — a name imported in one file and re-exported via
another module's `from .base import *`. The deleting file compiled fine, but every
consumer immediately crashed with `ImportError`.

**Affected files:**
| File | Deleted re-exports | Dependent modules |
|------|-------------------|-------------------|
| `utils/structures.py` | `List, Dict, Union, Tuple, field` | `fontformat.py`, `config.py`, `textblock.py`, `misc.py` |
| `modules/ocr/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `ocr/__init__.py`, `ocr_mit.py` |
| `modules/textdetector/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `textdetector/__init__.py` |
| `modules/translators/base.py` | `DEVICE_SELECTOR` | `translators/__init__.py` |

**Lesson:** Before deleting an import, grep the entire codebase for
`from <this_file> import <name>`. A clean `ruff check .` does not mean the app
will start.

**Prevention:**
- Prefer explicit export lists over `from .base import *` in `__init__.py`
- After bulk import cleanup, at minimum run:
  ```python
  python -c "from modules.base import init_module_registries; init_module_registries()"
  ```

---

## 3. UI Rendering

### 3.1 OverlaySlider Composite Artifact Fix

**Problem:** When multiple left-side overlay panels were animated (e.g. GlobalSearch
closing + AI Chat sliding in), a 75px-wide white vertical stripe appeared at the
right edge of the previously opened panel. Pure white `#FFFFFF`, regardless of theme.

**Root cause:** `OverlaySlider.show()` used a complex composite rendering pipeline:
1. Move real widget off-screen, `grab()` its appearance as QPixmap
2. `hide()` the real widget, composite the pixmap onto a `_SharedOverlay`
3. Animate the composite layer
4. On completion: destroy composite, `show()` the real widget
5. The hand-off window between composite and real widget rendered incomplete pixels

**Fix:** Abandon the composite approach. `show()` now:
1. Places the real widget at start position
2. `widget.show()` + `widget.raise_()` immediately
3. Drives position with `widget.move()` each animation frame
4. Maintains Z-order via per-frame `raise_()`

The real widget stays visible throughout — no grab, hide, composite, or rebuild cycle.

**Files:**
- `ui/overlay_slide.py` — `show()` uses direct animation path, `_update_animation()`
  fallback adds `raise_()`
- `ui/ai_chat_panel.py` — QScrollArea viewport `setAutoFillBackground(True)`
- `config/stylesheet.css` — `#AIChatArea > QWidget { background-color... }`

**Note:** The `hide()` animation still uses `_SharedOverlay` compositing (a widget
being hidden doesn't need to stay interactive during animation).

---

## 4. Image Format Compatibility

### 4.1 JXL (JPEG XL) Issues — ⛔ Frozen / Deactivated

JXL was used for text detection masks and inpainted images, but the feature is
currently **deactivated** in the UI and frozen indefinitely. The code remains in
the source tree but is not reachable from the UI — kept for reference in case of
future centralized rework.

Three layers of issues were found; attempted fixes were insufficient:

**4.1.1 Ultralytics Hijacks PIL.Image.open**

`ultralytics.utils.patches` monkey-patches `Image.open` with a wrapper that catches
all exceptions and tries `pip install pi-heif`. For `.jxl` files, this:
- Swallows the real PIL exception
- Fails on pip (embedded Python has no pip)
- Throws `ModuleNotFoundError: No module named 'pi_heif'` → crash

**Partial fix:** Bypass ultralytics' patch by using the saved original:
```python
from ultralytics.utils.patches import _image_open as _pil_image_open  # saved original ref
```

**4.1.2 Meaningless Retries for `.jxl` Files**

When a JXL codec is registered but can't decode a file, retrying 5 times is wasteful.

**Partial fix:** `.jxl` files handled separately — PIL tries once, falls back to cv2
immediately on failure.

**4.1.3 cv2.imread Hijacked by Ultralytics**

Ultralytics replaces `cv2.imread` with `np.fromfile` + `cv2.imdecode`. On empty files,
`cv2.imdecode(empty_buffer)` throws a `cv2.error` assertion instead of returning None.

**Partial fix:** Wrap cv2 fallback in try/except `cv2.error`.

**Why frozen:**

- `pillow-jxl-plugin` has ongoing compatibility issues with recent Pillow versions
  — upstream situation is unresolved, and maintaining workarounds isn't worth the
  effort given low usage
- JXL save path in `imwrite` still lacks proper error handling (no try/except;
  a failed encode can leave 0-byte or corrupt files)
- Existing `.jxl` cache files from before the freeze may be unreadable
- No bandwidth to implement a robust solution; the feature is preserved as-is for
  a future centralized clean-up pass

**Current disposition:**

- UI entry for JXL save format is removed from config panel
- `intermediate_imgsave_ext` defaults to `".png"`, but an existing config with
  `".jxl"` will still be accepted (backward compatibility)
- Reading masks saved as `.jxl` before the freeze works on a best-effort basis
  (PIL once → cv2 fallback → return None)
- The code is **not deleted** — only deactivated, pending future rework

**Files:**
- `utils/io_utils.py` — `imread()`, `imwrite()`, `_imread_jxl_fallback()`
- `utils/config.py` — `intermediate_imgsave_ext` config field
- `ui/configpanel.py` — JXL option removed from save format selector

---

## 5. Dependency Management

### 5.1 Removing `click` (June 2026)

`click` was used only by `scripts/run_module.py` (a dev-only testing script).
Replaced with standard library `argparse`. Removed from `requirements.txt` and
`pyproject.toml`.

### 5.2 Unifying PyQt6 → qtpy

Two files (`utils/profile_manager.py`, `ui/psd_export_dialog.py`) imported directly
from `PyQt6`, bypassing the `qtpy` compatibility layer. Changed to `from qtpy.xxx`
imports.

### 5.3 Version Floor Bumps for Python 3.13

Dependency minimum versions were raised to ensure cp313 binary wheels are available:

| Dependency | Old floor | New floor |
|-----------|-----------|-----------|
| pillow | `>=10.0` | `>=11.0` |
| opencv-python | `>=4.8.1.78` | `>=4.10.0.84` |
| PyQt6 | `>=6.6.1` | `>=6.8.1` |
| PyQt6-Qt6 | `>=6.6.2` | `>=6.8.1` |

---

## Appendix: When Things Break — Quick Checks

| Symptom | Likely Cause | Check |
|---------|-------------|-------|
| Translation shows English at runtime | QM corruption | `python -c "..."` hex scan (see 1.1) |
| Chinese shows as `?` in UI | Old qm_compile.py | Recompile with current script |
| ImportError on startup | Re-export deleted | Check `__init__.py` and `from .base import *` |
| White 75px stripe on slide-in | Composite render race | Use `OverlaySlider` direct animation path |
| `.jxl` file causes crash | ultralytics monkey-patch | Use `_pil_image_open` saved reference |
