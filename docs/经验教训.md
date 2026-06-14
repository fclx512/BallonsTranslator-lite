# 经验教训与技术记录

开发过程中遇到的 bug、陷阱、根因与修复方案的汇总。
供开发者和 AI 代理参考，避免重复劳动。

---

## 1. 国际化与翻译系统

### 1.1 QM 编码 Bug（2026 年 6 月）

**问题：** `scripts/qm_compile.py` 使用 `latin-1` 编码加 `errors="replace"` 处理字符串字段。`—`（em dash, U+2014）、`→`、`⚠`、`✓` 等非 Latin-1 字符被静默替换为 `?`，导致：
- Qt `QTranslator` 哈希查找失败 → 运行时的字符串找不到翻译
- 所有含这些字符的字符串显示为英文

**修复：** 改为 `s.encode("utf-8")`。哈希计算和字符串存储统一使用 UTF-8。

**文件：** `scripts/qm_compile.py`（`_iso8859_str` 函数 → 语义重命名）

**检测：**

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
                print('QM 已损坏 — 请重新编译！')
                break
    pos += 5 + l
"
```

### 1.2 i18n 检查器 — 多行正则遗漏

**问题：** `scripts/i18n_check.py` 使用 `self\.tr\("((?:[^"\\]|\\.)*)"\)`（仅单行）扫描。多行 `tr()` 调用和隐式字符串拼接对扫描器不可见：

```python
self.tr("line1\nline2")                    # 跨 2 行
self.tr("part1 " "part2")                  # 隐式拼接
self.tr("Enable this for models..."
        "Vision-capable...")               # 换行拼接
```

**修复：** 正则改用 `re.DOTALL` 模式，允许 `tr()` 参数内换行：

```python
r'self\.tr\(\s*("(?:[^"\\]|\\.)*")\s*\)', re.DOTALL
```

**文件：** `scripts/i18n_check.py`

### 1.3 `type="obsolete"` 检测修复

**问题：** 过时条目检测原在 `<translation>` 上查 `type="obsolete"`，但标准将该属性放在 `<message>` 上。检测实际上一直无效。

**修复：** 同时检查 `<message type="obsolete">` 和 `<translation type="obsolete">`。

**文件：** `scripts/i18n_check.py`

### 1.4 Self-tr() 字符串拼接规则

`self.tr()` 参数必须为单个字符串字面量。**严禁使用 Python 隐式拼接：**

```python
# ✅ 正确 — 单个字符串
self.tr("A long sentence that spans lines.")

# ❌ 错误 — 检查器正则检测不到
self.tr("part one "
        "part two")
```

若字符串太长，在 `tr(` 后换行即可（多行正则会处理）：

```python
self.tr("A long sentence the checker will find across lines.")
```

### 1.5 37 个"孤立条目"均为误报

`i18n_check.py --ci` 退出码为 4（孤立条目位）— 这是正常现象。所有 37 个报告出的孤立条目来自两类误报：

**基于变量的 tr() — 27 个条目：**
- `ThreadBase`：`self.tr(self._thread_error_msg)` — 子类中赋值的类变量
- `_ShortcutRow`：`self.tr(_ACTION_NAMES.get(action_id, action_id))` — 字典查找

两者均在运行时通过 Qt 的 `.qm` 查找正确解析。**切勿清除。**

**多行 tr() — 9 个条目：**（见 1.2 — 现已修复，但旧文件可能仍有检查器最初找不到的条目）

### 1.6 类重命名时，需更新 .ts 上下文

如果 `class OldName` → `class NewName`，则 .ts 中所有位于 `<name>OldName</name>` 下的消息必须移至 `<name>NewName</name>`。Qt 通过类名查找翻译。

波及范围：
- `ShortcutEditor` → `_ShortcutRow`（27 条消息）
- `AiChatPanel` → `ChangeReviewWindow`（2 条消息）
- `FontFormatPanel` → `ConfigPanel`（"Effect"）
- `TranslateThread` → `ImgtransThread`
- `ProgressMessageBox` → `ImgtransProgressMessageBox`

### 1.7 .ts 中的重复上下文块

`_normalize_ts()` 函数合并具有相同 `<name>` 的重复 `<context>` 块。`MainWindow` 曾有两个独立块（61 + 11 条消息），第一个块中的条目在实现合并前被静默丢弃。

### 1.8 模块参数描述

含有 `description` 字段的模块参数在运行时使用 `self.tr(variable_name)`。i18n 正则检测不到它们。它们在 .ts 中位于 `<context>ParamWidget</context>` 下，并明确排除在孤立条目检测之外。

---

## 2. 代码清理陷阱

### 2.1 自动清理误删 Re-export（2026 年 5 月）

**问题：** 自动代码检查工具删除了文件中"未使用的 import"，但这些实际上是 **re-export**——在一个文件中导入并通过另一个模块的 `from .base import *` 重新导出的名字。被删除的文件编译正常，但所有依赖方立刻崩溃。

**波及范围：**

| 文件 | 被删的 re-export | 依赖方 |
|------|-----------------|--------|
| `utils/structures.py` | `List, Dict, Union, Tuple, field` | `fontformat.py`, `config.py`, `textblock.py`, `misc.py` |
| `modules/ocr/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `ocr/__init__.py`, `ocr_mit.py` |
| `modules/textdetector/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `textdetector/__init__.py` |
| `modules/translators/base.py` | `DEVICE_SELECTOR` | `translators/__init__.py` |

**教训：** 删除 import 前必须 grep 全库确认没有其他模块从当前位置导入该名字。仅 `ruff check .` 通过不代表能启动。

**预防：**
- 优先显式列出导出名，避免 `__init__.py` 中的 `from .base import *`
- 批量 import 清理后，至少运行：
  ```python
  python -c "from modules.base import init_module_registries; init_module_registries()"
  ```

---

## 3. UI 渲染

### 3.1 OverlaySlider 合成渲染伪影修复

**问题：** 多个左侧覆盖面板同时动画时（如 GlobalSearch 关闭 + AI Chat 滑入），在之前打开的面板右边缘出现 75px 宽的纯白竖条。纯白 `#FFFFFF`，与任何主题色不匹配。

**根因：** `OverlaySlider.show()` 使用复杂的合成渲染流程：
1. 将真实控件移至屏幕外，`grab()` 抓取为 QPixmap
2. `hide()` 真实控件，将像素图合成到 `_SharedOverlay`
3. 驱动合成层动画
4. 完成后：销毁合成层，`show()` 真实控件
5. 从合成层切换到真实控件的时间窗口渲染了不完整像素

**修复：** 放弃合成方式。`show()` 现在：
1. 将真实控件置于起始位置
2. 立即 `widget.show()` + `widget.raise_()`
3. 每帧通过 `widget.move()` 驱动位置
4. 通过每帧 `raise_()` 维持 Z 序

真实控件全程保持可见——无需抓图、隐藏、合成、重建循环。

**文件：**
- `ui/overlay_slide.py` — `show()` 走直接动画路径，`_update_animation()` 兜底加 `raise_()`
- `ui/ai_chat_panel.py` — QScrollArea viewport `setAutoFillBackground(True)`
- `config/stylesheet.css` — `#AIChatArea > QWidget { background-color... }`

**注意：** `hide()` 动画仍使用 `_SharedOverlay` 合成（隐藏中的控件无需在动画期间保持交互）。

---

## 4. 图像格式兼容性

### 4.1 JXL (JPEG XL) 问题 — ⛔ 封存 / 未激活

JXL 格式曾用于文字检测掩码和修复图像，但目前已在 UI 中**封存禁用**，无限期冻结。
相关代码保留在源码中但无法从 UI 触发——留待未来集中整改时参考。

发现并尝试修复了三层问题，但修复方案均不充分：

**4.1.1 Ultralytics 劫持 PIL.Image.open**

`ultralytics.utils.patches` 用包装器 monkey-patch `Image.open`，捕获所有异常后尝试 `pip install pi-heif`。对 `.jxl` 文件：
- 吞掉真实 PIL 异常
- pip 安装失败（嵌入式 Python 无 pip）
- 抛出 `ModuleNotFoundError: No module named 'pi_heif'` → 闪退

**部分修复：** 绕过 ultralytics 补丁，使用保存的原始引用：
```python
from ultralytics.utils.patches import _image_open as _pil_image_open
```

**4.1.2 `.jxl` 文件无意义重试**

JXL codec 已注册但无法解码时，重试 5 次纯属浪费。

**部分修复：** `.jxl` 单独处理——PIL 试一次，失败立刻走 cv2 兜底。

**4.1.3 cv2.imread 被 Ultralytics 劫持**

Ultralytics 将 `cv2.imread` 替换为 `np.fromfile` + `cv2.imdecode`。空文件时，`cv2.imdecode(空buffer)` 抛出 `cv2.error` 断言而非返回 None。

**部分修复：** cv2 兜底路径包 try/except `cv2.error`。

**封存原因：**

- `pillow-jxl-plugin` 与新版 Pillow 存在持续兼容性问题，上游未解决
- `imwrite` 的 JXL 保存路径仍缺少错误处理（无 try/except，编码失败可能留下 0 字节或损坏文件）
- 封存前遗留的 `.jxl` 缓存文件可能已无法读取
- 当前无精力实施彻底修复，功能原样保留等待后续集中清理

**当前处置：**

- 配置面板中已移除 JXL 保存格式的 UI 选项
- `intermediate_imgsave_ext` 默认 `".png"`，但既有配置含 `".jxl"` 仍会接受（向后兼容）
- 封存前保存的 `.jxl` 掩码文件以尽力而为方式读取（PIL 一次 → cv2 兜底 → 返回 None）
- **代码未删除**，仅取消激活，等待未来集中处理

**文件：**
- `utils/io_utils.py` — `imread()`, `imwrite()`, `_imread_jxl_fallback()`
- `utils/config.py` — `intermediate_imgsave_ext` 配置字段
- `ui/configpanel.py` — JXL 选项已从保存格式选择器中移除

---

## 5. 依赖管理

### 5.1 移除 `click`（2026 年 6 月）

`click` 仅由 `scripts/run_module.py`（开发测试脚本）使用。已替换为标准库 `argparse`。从 `requirements.txt` 和 `pyproject.toml` 移除。

### 5.2 统一 PyQt6 → qtpy

两个文件（`utils/profile_manager.py`、`ui/psd_export_dialog.py`）直接导入 `PyQt6`，绕过了 `qtpy` 兼容层。已改为 `from qtpy.xxx` 导入。

### 5.3 为 Python 3.13 提升版本下限

提升依赖最小版本以确保 cp313 二进制 wheel 可用：

| 依赖 | 旧下限 | 新下限 |
|-----------|-----------|-----------|
| pillow | `>=10.0` | `>=11.0` |
| opencv-python | `>=4.8.1.78` | `>=4.10.0.84` |
| PyQt6 | `>=6.6.1` | `>=6.8.1` |
| PyQt6-Qt6 | `>=6.6.2` | `>=6.8.1` |

---

## 附录：故障快速排查

| 症状 | 可能原因 | 检查方法 |
|------|---------|---------|
| 翻译显示为英文 | QM 损坏 | `python -c "..."` 十六进制扫描（见 1.1） |
| 中文显示为 `?` | 旧版 qm_compile.py | 用当前脚本重新编译 |
| 启动报 ImportError | Re-export 被删 | 检查 `__init__.py` 和 `from .base import *` |
| 滑入时出现 75px 白条 | 合成渲染竞争 | 使用 `OverlaySlider` 直接动画路径 |
| `.jxl` 文件导致闪退 | ultralytics monkey-patch | 使用 `_pil_image_open` 保存的引用 |
