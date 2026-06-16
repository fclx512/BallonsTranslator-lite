# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。

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

### 上游 v1.5.0 分析

dmMaze/BallonsTranslator v1.5.0（2026-06-14 发布）分析：

- `lazy_registry.py` — AST 扫描模块文件获取元数据，不 import 实际代码。`ModuleSpec.resolve()` 仅在用户选中模块时才真正导入
- `core_requirements.py` — 启动时 probe 核心 import，缺失则自动 pip install
- `requirements.txt` 零 ML 依赖（仅有 PyQt6、numpy、opencv-python 等基础包）

本分支已有 `ModuleSpec` 系统，方向一致，无需大改。

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
