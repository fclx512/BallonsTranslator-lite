# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。

---

---

## 2026-06-17

### 底部快捷栏模块排序统一

**问题：** 底部快捷栏的下拉列表顺序与设置面板不一致。设置面板始终将 `none*` 模块排在最前加分隔线，再排其余模块；底部快捷栏则直接按 registry 插入顺序排列。此外 OCR 默认选中 `paddleocr_v6_onnx`，启动时若模型文件缺失会出错。

**修复：**

1. **排序统一** — `setupConfig()` 中三个底部选择器（翻译器、OCR、文本检测）均改为：`none*` 模块 → 分隔线 → 其余模块，与设置面板的 `ModuleConfigParseWidget.addModulesParamWidgets()` 分组逻辑一致。
2. **默认值矫正** — `launch.py` 中 OCR 启动回退逻辑从"选择性检测已知模块依赖"简化为：只要预存值不是 `none_ocr`/`llm_ocr`，强制重置为 `none_ocr`，避免模型缺失导致的启动错误。
3. **初始选中** — 文本检测和翻译器原先在 `setupConfig` 中没有显式设置选中值，依赖后台线程完成后才更新；现统一在列表填充后即从 `pcfg` 读取当前值设置选中项。

**涉及文件：**
- `ui/mainwindow.py` (L411-460)
- `launch.py` (L373-379)

### DeepSeek 翻译报"结构不匹配"修复

**问题：** DeepSeek API 翻译时总提示 `[ERROR: Structure Mismatch]`，实际 API 调用从未发出。

**根因：** `modules/translators/trans_llm_api.py` 中 `frequency_penalty` / `presence_penalty` 用 `if fp is not None:` 判空，但 DeepSeek 内置 profile 中这两个字段是空字符串 `""`，`"" is not None` 为 `True`，`float("")` 抛出 `ValueError`。错误在 `_request_translation` 的 try 块外，被 `_translate` 的 `except ValueError` 接住，误判为"翻译结构不匹配"重试 2 次后失败。

**修复：** `if fp is not None:` → `if fp:`（空字符串是 falsy），跳过空值转换。

**涉及文件：**
- `modules/translators/trans_llm_api.py` (L479-484)

### Run 对话框上下文策略描述自动换行 + 补全翻译

**问题：** 启用"上下文翻译（beta）"后，展开的策略描述文本因 `mode_label` 未开启 `setWordWrap` 横向撑开窗口，影响上方页数选择横条；且"Full context..."字符串翻译遗漏。

**修复：**
1. `ui/mainwindow.py` — `mode_label` 添加 `setWordWrap(True)`，文本纵向增高不横向撑开
2. `translate/zh_CN.ts` — 补全"全文上下文（%1页，以之前所有翻译为参考）"

**涉及文件：**
- `ui/mainwindow.py` (L2137)
- `translate/zh_CN.ts` / `.qm`

---

## 2026-06-16

### PS 风格文本框对齐功能（Smart Guides + 批量对齐）

**需求：** 画布区文本框添加 PS 风格对齐——拖拽时吸附到相邻块边缘/中心并显示品红参考线，选中多块后右键菜单执行批量对齐/分布操作。可开关。

**实现：**

1. **Smart Guides（默认开启）：** `TextBlkItem.mouseMoveEvent` → `_apply_snap()` → `compute_snap()` 计算吸附位置 → `setPos` 吸附 + `SnapGuideItem` 渲染品红虚线。参考线最初用 `drawForeground()` 但因 Qt 只绘 dirty 区域导致不稳定，改为独立 `QGraphicsItem`（Z=100）后稳定。

2. **批量对齐/分布：** 右键菜单 "Align" 子菜单（8 操作）→ `align_textblks` Signal → `SceneTextManager.onAlignTextBlks()` → 复用 `MoveBlkItemsCommand` undo。

3. **开关：** 右键菜单 "Snap Alignment" toggle → `Canvas.alignment_enabled`。

**涉及文件：**
- `utils/text_alignment.py` — 新增，纯计算（`compute_snap`, `align_*`, `distribute_*`）
- `ui/canvas.py` — 新增 `SnapGuideItem`、`align_textblks` Signal、context menu、开关
- `ui/textitem.py` — `mouseMoveEvent`/`mouseReleaseEvent` 中接入吸附逻辑
- `ui/scenetext_manager.py` — `onAlignTextBlks()` handler
- `translate/zh_CN.ts` / `.qm` — 新增 10 条翻译

---

### uv `--prefer-binary` Bug 修复验证

**问题：** `ui/module_manager.py` 和 `ui/dependency_dialog.py` 中 uv runner 错误地传递了 `--prefer-binary` 参数，而 uv 不支持此参数。

**修复：**
1. `_pip_install()` 分离 `is_uv` 判断，uv runner 干净无 `--prefer-binary`/`--timeout`
2. `_detect_installer()` 返回 `(cmd_base, using_uv)` 二元组，uv 命令不含 `--prefer-binary`
3. uv 失败后自动 fallback 到 pip

**涉及文件：**
- `ui/module_manager.py` (L908-937)
- `ui/dependency_dialog.py` (L150-198)

---

### 便携包构建系统设计实现

**架构：三层分发**
- Layer 1 — 便携包 (15.2 MB ZIP)：Python 3.12 embeddable + 应用代码 + `requirements_core.txt` + `run.bat`
- Layer 2 — OCR 模型包（待实现）：onnxruntime + onnxocr + PP-OCRv6 模型
- Layer 3 — GPU 增强包（待实现）：PyTorch + CUDA + ultralytics/diffusers

**新建文件：**
- `scripts/build_portable.py` — 8 步构建脚本
- `.github/workflows/build-portable.yml` — GitHub Actions CI
- `config/requirements_core.txt` — 核心依赖列表（构建时从 pyproject.toml 自动生成）
- `pyproject.toml` — 添加 `version = "0.2.0"`

**注意：** `release/` 和 `.build_cache/` 应加入 `.gitignore`。

---
