# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。

---

## 2026-06-19

### 高级对齐扩展 X 轴对齐

**需求：** 高级对齐支持 X 轴（左/中/右），与 Y 轴互斥选择。

**改动：**
1. `utils/point_alignment.py` — 新增 `_blk_x_bounds()`；`compute_offsets()` 加 `axis` 参数分发 X/Y 计算
2. `ui/point_align_dialog.py` — 顶部加 Axis 单选（X 轴/Y 轴），切换时动态更新坐标标签（X:/Y:）和对齐模式单选按钮文字（左/中/右 ↔ 顶/中/底）。信号 `pick_y_clicked` → `pick_clicked`，方法 `target_y()` → `target_value()`，`set_picked_y()` → `set_picked_value()`
3. `ui/canvas.py` — 泛化拾取模式：移除 `y_picking`/`y_pick_line`/`y_picked`，改为 `_pick_axis`/`_pick_line`/`position_picked`。`enter_y_pick_mode()` → `enter_pick_mode(axis)`，X 轴时画垂直品红线
4. `ui/mainwindow.py` — `on_open_advanced_align`/`execute_advanced_align` 加 `axis` 参数，视觉偏移方向随轴变化（`QPointF(dx,0)`/`QPointF(0,dy)`）
5. `translate/zh_CN.ts` — PointAlignDialog 新增 7 条翻译；重新编译 qm

**涉及文件：** `utils/point_alignment.py`、`ui/point_align_dialog.py`、`ui/canvas.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

### 移除 PP-OCRv6 字典的冗余下载条目

**问题：** `ppocrv6_dict_proper.txt` 已在仓库中通过 git 跟踪，但被列在 `download_file_list` 中，URL 指向本仓库（循环下载），且地址无效。

**修复：** 移除 `modules/ocr/ocr_onnx.py` 中 `# ── shared dictionary ──` 对应的字典下载条目。文件已由 git 同步，无需额外下载。

**涉及文件：** `modules/ocr/ocr_onnx.py`

### 修复 QRadioButton 暗色模式下不可见

**问题：** Qt 原生单选框渲染忽略 Windows 暗色调色板，indicator 圆圈无显式样式，在暗色背景下难以辨识。
**修复：** 在 `config/stylesheet.css` 中添加 `QRadioButton::indicator` 完整样式（边框、底色、选中态/悬停态/禁用态），配套 `QCheckBox` 文本颜色与间距基础样式。

**涉及文件：** `config/stylesheet.css`

---

## 2026-06-18

### 高级对齐功能实现 + 修复

**需求：** 工具菜单新增"高级对齐"，用户指定 Y 坐标后将选定页面内非旋转文本框的上边/垂直中心/下边对齐到该 Y 值，仅调整垂直位置，支持拾取 Y 值和撤销。

**实现（第1轮）：**
1. `utils/point_alignment.py` — **新增**，纯计算函数 `compute_offsets()`，基于 `_bounding_rect`/`xyxy` 计算 dy
2. `ui/point_align_dialog.py` — **新增**，对话框 UI（Y 输入 + 拾取按钮 + 对齐单选 + RangeSlider 范围选择 + 全页复选框）
3. `ui/canvas.py` — 添加 Y 拾取模式：y_picking 标志、y_pick_line（洋红虚线跟随鼠标）、y_picked 信号、exit_y_pick_mode()/restore_drag_mode()
4. `ui/mainwindowbars.py` — "工具"菜单增加 "Advanced Alignment" 条目
5. `ui/mainwindow.py` — **新增** `_PointAlignCommand`（QUndoCommand，跨页数据+当前页视觉撤销）、`on_open_advanced_align()`（QEventLoop 避免 hide() 取消模态）、`execute_advanced_align()`
6. `ui/configpanel.py` — 注册 `advanced_align` 快捷键（默认无）

**修复（第2轮）：**
- 拾取值应用后无响应 — 根因：dialog.hide() 在 exec_() 中导致 Qt 返回 Rejected。改用 show() + QEventLoop 替代 exec_()
- 对话框关闭后画布拖拽残留 — exit_y_pick_mode() 不恢复 ScrollHandDrag，统一在 on_accepted/on_rejected 中调用 restore_drag_mode()
- "All Pages" 默认勾选时横条未禁用 — setChecked(True) 移到 toggled.connect() 之后
- 规范设计文档 `docs/superpowers/specs/2026-06-18-point-alignment-design.md` 删除

### 工具栏与对话框 i18n 补全

**问题：** 工具栏和工具对话框中有多处硬编码英文未包裹 self.tr() 或 ts 条目缺失：Quick Symbol 分组名、高级对齐窗口标签、画布 PREVIEW 提示、快捷键 "Advanced Alignment" 等。

**修复：**
1. `ui/quick_symbol_dialog.py` — 分组名 `self.tr(group_name)` 包裹
2. `ui/point_align_dialog.py` — `QLabel("Y:")` → `self.tr("Y:")`
3. `ui/canvas.py` — `"PREVIEW"` → `self.tr("PREVIEW")`
4. `translate/zh_CN.ts` — 补全 PointAlignDialog、QuickSymbolDialog、TitleBar、_ShortcutRow、MainWindow 等 20+ 条目；重新编译 zh_CN.qm

**涉及文件（两次改动合并）：** `ui/mainwindow.py`、`ui/canvas.py`、`ui/point_align_dialog.py`、`ui/mainwindowbars.py`、`ui/configpanel.py`、`utils/point_alignment.py`、`ui/quick_symbol_dialog.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-06-17

### 快捷键面板翻译缺失修复 + CLAUDE.md 入库

**问题：** 设置中的快捷键编辑面板（`ShortcutEditor` / `_ShortcutRow`）部分文字显示为英文。根因是 `_ACTION_NAMES`（28个动作名）和 `_SHORTCUT_GROUPS`（6个分组名）的 ts 条目从未添加到 `translate/zh_CN.ts` 中——这些字符串通过 `self.tr(variable)` 间接调用，i18n_check 只将它们标记为 orphan 而不报错，容易被忽略。

**修复：**
1. 补全 `_ShortcutRow` 上下文 28 条动作名翻译（"Page Up"、"Zoom In"、"Bold" 等）
2. 新建 `ShortcutEditor` 上下文 6 条分组名翻译（"Navigation"、"View"、"Edit" 等）
3. 重新编译 `zh_CN.qm`
4. CLAUDE.md 的 i18n 节添加说明：`self.tr(variable)` 间接调用的翻译需手动维护

**CLAUDE.md 入库决策：** 从 `.gitignore` 移除 CLAUDE.md，提交到仓库实现多设备 git 同步。个人偏好走 `~/.claude/CLAUDE.md`。

---

## 2026-06-17（续）

### PSD 二进制导出 — UnitFloat 顺序修复 + 文字栅格化根治

**问题：** `utils/psd_descriptor.py` 的 `_write_descriptor_value` 中 UnitFloat 的字段顺序写反了。PS 规范要求 `UntF` → unit_id(`#Pnt`/`#Pxl`) → `f64(value)`，代码却写了 `UntF` → f64 → unit_id。这是导致所有 PSD 导出文字层在 PS 中栅格化（不可编辑）的根本原因。

**修复：** `psd_descriptor.py:213-221` 交换 `write_f64(value)` 和 `write_signature(unit_id)` 的顺序。

**连带修复（第 2 轮）：**
- TySh 描述符从 8 项增至 9 项（加 `TxMg`）
- `AntA` 枚举值改为 `AnCr`
- bounds 单位从 `#Pxl` 改为 `#Pnt`
- 新增 `unit_points()` / `unit_float()` 方法

**验证：** 50 个测试通过；横排/竖排文字、中/英文、含 `0x5C` 反斜杠字节的字体名均可在 PS 中编辑。唯特定项目数据仍栅格化（原因未明，无法复现）。

**涉及文件：** `utils/psd_descriptor.py`、`utils/psd_engine_data.py`、`utils/psd_binary_exporter.py`、`docs/psd_binary_export.md`

**涉及文件：**
- `translate/zh_CN.ts` / `.qm`
- `CLAUDE.md`
- `.gitignore`

---

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
