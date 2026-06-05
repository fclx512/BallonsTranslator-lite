# JXL (JPEG XL) 兼容性问题记录

## 概述

项目中 JXL 格式用于保存/读取文字检测掩码（mask）和修复图像（inpainted）。涉及三个层面的问题：ultralytics monkey-patch 干扰、pillow-jxl-plugin 兼容性、异常处理缺口。当前已修复闪退问题，但 JXL 格式的编解码可靠性仍有待排查。

## 相关文件

| 文件 | 涉及内容 |
|------|----------|
| `utils/io_utils.py` | `imread()` / `imwrite()` / `_imread_jxl_fallback()` — 图像读写 |
| `utils/proj_imgtrans.py:399-418` | `get_mask_path()` / `load_mask_by_imgname()` — 掩码路径解析 |
| `utils/config.py:171` | `intermediate_imgsave_ext: str = ".png"` — 中间图像保存格式配置 |
| `C:\Program Files\Python313\Lib\site-packages\ultralytics\utils\patches.py` | ultralytics 对 PIL.Image.open 和 cv2.imread 的 monkey-patch |

## 已修复的问题

### 1. ultralytics 劫持 `Image.open`，异常类型被偷换

**症状**：`imread` 只捕获 `PIL.UnidentifiedImageError`，但 ultralytics 的 `image_open` 补丁（`patches.py:78`）用 bare `except Exception` 拦截所有异常，然后尝试 `pip install pi-heif`（HEIF 解码库）。对于 `.jxl` 文件，这导致：
- 原异常被吞掉
- pip install 永远失败（嵌入式 Python 无 pip 模块）
- 最终抛出 `ModuleNotFoundError: No module named 'pi_heif'`，绕过 retry 逻辑直接闪退

**修复**（`io_utils.py:28-34`）：
```python
# 绕过 ultralytics 的 Image.open monkey-patch，直接使用其保存的原始引用
try:
    from ultralytics.utils.patches import _image_open as _pil_image_open
except ImportError:
    _pil_image_open = Image.open
```
`imread` 中改用 `_pil_image_open()` 替代 `Image.open()`，彻底不走 ultralytics 补丁。

### 2. `.jxl` 文件无意义重试

**症状**：JXL codec 已注册但无法解码某文件时，retry 5 次纯属浪费（文件已完整存在，不是 I/O 竞争问题）。

**修复**（`io_utils.py:284-302`）：`.jxl` 文件单独处理 — PIL 试一次，失败立刻走 cv2 兜底，不重试。

### 3. cv2.imread 也被 ultralytics 补丁劫持

**症状**：`cv2.imread` 被 replace 为 `np.fromfile` + `cv2.imdecode`（`patches.py:21-50`）。当文件为空或损坏时，`cv2.imdecode(空buffer)` 抛出 `cv2.error: !buf.empty()` 断言失败而非返回 None，导致闪退。

**修复**（`io_utils.py:266-274`）：
```python
def _imread_jxl_fallback(imgpath, read_type):
    try:
        img = cv2.imread(imgpath, read_type)
    except cv2.error:
        return None
    ...
```

## 当前状态

- **读取**：`.jxl` 文件 → PIL 一次 → cv2 兜底 → 失败返回 None（不闪退）
- **写入**（`imwrite`）：JXL codec 可用时走 JXL 编码，不可用时降级 PNG + warning
- **默认格式**：`intermediate_imgsave_ext` 为 `".png"`，用户可手动改为 `".jxl"`

## 待排查问题（给接手的 AI）

### A. 既存 `.jxl` 掩码文件无法被任何方式读取

日志显示 `QQ20260521-142831 - 副本.jxl` 既不能被 PIL JXL decoder 解码，也不能被 cv2 读取。可能原因：
1. **pillow-jxl-plugin 版本与 Pillow 12+ 不兼容** — codec 注册了但实际解码静默失败，`imwrite` 中的 JXL 保存路径也没有 try/except 保护（`io_utils.py:367-372`），编码失败时可能留下空文件或损坏文件
2. **文件本身为空** — `np.fromfile` 返回空 buffer，说明文件 0 字节

**建议排查步骤**：
1. 检查该文件是否 0 字节：`ls -la "D:/新建文件夹/mask/QQ20260521-142831 - 副本.jxl"`
2. 检查 pillow-jxl-plugin 版本兼容性：`pip show pillow-jxl-plugin`
3. 尝试用其他工具（ImageMagick、cjxl）解码该文件
4. 对 `imwrite` 的 JXL 保存路径（line 367-372）加 try/except，防止编码失败留下空文件

### B. pillow-jxl-plugin 与当前环境的兼容性

- `pillow-jxl-plugin` 1.3.7 对 Pillow 12.2.0+ 支持不完整（已知问题，见 `docs/ruff_cleanup_progress.md` 已删除文档中的记录）
- 如果上游已修复，升级 `pillow-jxl-plugin` 即可；如果未修复，考虑从 `IMG_EXT` 中移除 `.jxl`

### C. 根因可能是 imwrite 的 JXL 保存路径没有错误处理

```python
# io_utils.py:364-373 — 当前代码
if ext == ".jxl":
    if ".jxl" in Image.EXTENSION:
        lossless = quality > 99
        Image.fromarray(img).save(  # ← 如果这里抛异常，文件可能半写入
            img_path,
            quality=quality,
            lossless=lossless,
            effort=jxl_encode_effort,
        )
        return
```

`Image.save()` 在编码失败时可能：写入空文件、写入部分数据后崩溃、或留下 0 字节文件。缺少 try/except + 失败回退 PNG 的逻辑。

## 涉及的配置项

- `pcfg.intermediate_imgsave_ext` — 默认 `".png"`，控制掩码/修复图保存格式
- `IMG_EXT` — `[".bmp", ".jpg", ".png", ".jpeg", ".webp", ".jxl"]`，允许的中间图像扩展名

## 相关 ultralytics 补丁源码位置

```
C:\Program Files\Python313\Lib\site-packages\ultralytics\utils\patches.py
  - Line 21: cv2.imread monkey-patch (multilanguage filename support)
  - Line 54: _image_open = Image.open (保存原始引用)
  - Line 58: image_open monkey-patch (懒加载 pi-heif)
  - Line 89: Image.open = image_open (应用补丁)
```
