# JXL (JPEG XL) Compatibility Issue Record

## Overview

The JXL format is used in the project for saving/reading text detection masks and inpainted images. Issues involve three layers: ultralytics monkey-patch interference, pillow-jxl-plugin compatibility, and exception handling gaps. The crash issue has been fixed, but the encoding/decoding reliability of the JXL format still requires investigation.

## Related Files

| File | Content |
|------|---------|
| `utils/io_utils.py` | `imread()` / `imwrite()` / `_imread_jxl_fallback()` — image read/write |
| `utils/proj_imgtrans.py:399-418` | `get_mask_path()` / `load_mask_by_imgname()` — mask path parsing |
| `utils/config.py:171` | `intermediate_imgsave_ext: str = ".png"` — intermediate image save format config |
| `C:\Program Files\Python313\Lib\site-packages\ultralytics\utils\patches.py` | ultralytics monkey-patch for PIL.Image.open and cv2.imread |

## Fixed Issues

### 1. ultralytics hijacks `Image.open`, exception type is swapped

**Symptoms**: `imread` only catches `PIL.UnidentifiedImageError`, but ultralytics' `image_open` patch (`patches.py:78`) uses a bare `except Exception` to intercept all exceptions, then attempts `pip install pi-heif` (HEIF decoding library). For `.jxl` files, this causes:
- The original exception is swallowed
- pip install always fails (embedded Python has no pip module)
- Finally throws `ModuleNotFoundError: No module named 'pi_heif'`, bypassing retry logic and crashing immediately

**Fix** (`io_utils.py:28-34`):
```python
# Bypass ultralytics' Image.open monkey-patch, use the saved original reference directly
try:
    from ultralytics.utils.patches import _image_open as _pil_image_open
except ImportError:
    _pil_image_open = Image.open
```
`imread` now uses `_pil_image_open()` instead of `Image.open()`, completely avoiding the ultralytics patch.

### 2. Meaningless retries for `.jxl` files

**Symptoms**: When a JXL codec is registered but cannot decode a file, retrying 5 times is a pure waste (the file already exists in full; it's not an I/O race condition).

**Fix** (`io_utils.py:284-302`): `.jxl` files are handled separately — PIL tries once, on failure immediately falls back to cv2, no retries.

### 3. cv2.imread also hijacked by ultralytics patch

**Symptoms**: `cv2.imread` is replaced with `np.fromfile` + `cv2.imdecode` (`patches.py:21-50`). When the file is empty or corrupted, `cv2.imdecode(empty buffer)` throws `cv2.error: !buf.empty()` assertion failure instead of returning None, causing a crash.

**Fix** (`io_utils.py:266-274`):
```python
def _imread_jxl_fallback(imgpath, read_type):
    try:
        img = cv2.imread(imgpath, read_type)
    except cv2.error:
        return None
    ...
```

## Current Status

- **Reading**: `.jxl` file → PIL once → cv2 fallback → returns None on failure (no crash)
- **Writing** (`imwrite`): Uses JXL encoding when JXL codec is available, falls back to PNG + warning when unavailable
- **Default format**: `intermediate_imgsave_ext` is `".png"`, user can manually change to `".jxl"`

## Issues to Investigate (for the next AI)

### A. Existing `.jxl` mask file cannot be read by any method

Logs show that `QQ20260521-142831 - 副本.jxl` can neither be decoded by the PIL JXL decoder nor read by cv2. Possible causes:
1. **pillow-jxl-plugin version incompatible with Pillow 12+** — the codec is registered but actual decoding silently fails, and the JXL save path in `imwrite` also lacks try/except protection (`io_utils.py:367-372`), potentially leaving empty or corrupted files on encoding failure
2. **File itself is empty** — `np.fromfile` returns an empty buffer, indicating the file is 0 bytes

**Suggested investigation steps**:
1. Check if the file is 0 bytes: `ls -la "D:/新建文件夹/mask/QQ20260521-142831 - 副本.jxl"`
2. Check pillow-jxl-plugin version compatibility: `pip show pillow-jxl-plugin`
3. Try decoding the file with other tools (ImageMagick, cjxl)
4. Add try/except to the JXL save path in `imwrite` (lines 367-372) to prevent empty files from encoding failures

### B. pillow-jxl-plugin compatibility with the current environment

- `pillow-jxl-plugin` 1.3.7 has incomplete support for Pillow 12.2.0+ (known issue, see record in the now-deleted `docs/ruff_cleanup_progress.md` document)
- If upstream has fixed it, upgrading `pillow-jxl-plugin` will suffice; if not, consider removing `.jxl` from `IMG_EXT`

### C. Root cause may be the lack of error handling in imwrite's JXL save path

```python
# io_utils.py:364-373 — current code
if ext == ".jxl":
    if ".jxl" in Image.EXTENSION:
        lossless = quality > 99
        Image.fromarray(img).save(  # ← if this throws, the file may be partially written
            img_path,
            quality=quality,
            lossless=lossless,
            effort=jxl_encode_effort,
        )
        return
```

`Image.save()` on encoding failure may: write an empty file, crash after writing partial data, or leave a 0-byte file. Missing try/except + fallback-to-PNG logic.

## Related Configuration Items

- `pcfg.intermediate_imgsave_ext` — default `".png"`, controls mask/inpaint image save format
- `IMG_EXT` — `[".bmp", ".jpg", ".png", ".jpeg", ".webp", ".jxl"]`, allowed intermediate image extensions

## Related ultralytics Patch Source Code Location

```
C:\Program Files\Python313\Lib\site-packages\ultralytics\utils\patches.py
  - Line 21: cv2.imread monkey-patch (multilanguage filename support)
  - Line 54: _image_open = Image.open (preserves original reference)
  - Line 58: image_open monkey-patch (lazy-load pi-heif)
  - Line 89: Image.open = image_open (applies patch)
```
