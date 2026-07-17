# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。

## 2026-07-17

### 字体样式编辑器 & 文本编辑区视觉层次优化

**需求：** 格式编辑区与文本编辑区的边框无层次区分、样式间隔不一、布局冗余。

**改动：**

1. **3px 边框胶囊 + 内容 5px padding**（`config/stylesheet.css`、`ui/scenetext_manager.py`）：
   - `GroupFrame#formatOuterFrame` / `GroupFrame#textEditOuterFrame` 使用 3px 半透灰边框（内层小框保持 1px），背景色实色填充防止子控件圆角穿透
   - 格式编辑区与文本编辑区各用独立 GroupFrame 包裹，间距统一 5px

2. **Canvas 视觉增强**（`config/stylesheet.css`、`ui/mainwindow.py`）：
   - `CustomGV` 加 `border-radius: 6px`
   - canvas 与编辑区间距 `0→5px`，编辑区右侧贴窗边距 `0→5px`

3. **行间布局合并瘦身**（`ui/text_panel.py`）：
   - 字体行 + 颜色/字号/间距行 → 合并为单个胶囊框
   - 加粗斜体行 + 轮廓行 → 合并为单个胶囊框，行间保留 4px 间距
   - `FormatGroupBtn` / `AlignmentBtnGroup` 内建 QLayout 默认 11px 边距 → 归零（修复图标按钮距边框过大问题）
   - QFontChecker/AlignmentChecker 指示器从 30×30 / 28×28 统一缩至 26×26 + padding/margin 归零

4. **高级文本格式面板列布局**（`ui/text_advanced_format.py`）：
   - Shadow/Gradient 按钮从水平并列改为与上方 Opacity/Line Spacing 控件垂直对齐的两列布局
   - 按钮 padding `1px 8px` → `0px 4px`，字号 `11→12px`
   - `Line Spacing Type` → `Line Spacing`（缩短标签宽度）

5. **工具栏贴顶 + hover**（`ui/scenetext_manager.py`、`config/stylesheet.css`、`ui/custom_widget/label.py`）：
   - 工具栏上边距归零贴顶
   - `CheckableLabel`/`TextCheckerLabel` padding `4px→2px` 缩矮，取消 border-radius 恢复直角

6. **移除帮助手册功能**（预存改动）：删除 `ui/help_dialog.py`、`docs/help/测试文档.md` 及相关引用

**验证：** 语法检查 ✅、qm 编译 1000 条 ✅、i18n 检查 ✅

**涉及文件：** `config/stylesheet.css`、`ui/mainwindow.py`、`ui/scenetext_manager.py`、`ui/text_panel.py`、`ui/text_advanced_format.py`、`ui/custom_widget/label.py`、`ui/help_dialog.py`（删）、`docs/help/测试文档.md`（删）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_startup_imports.py`、`ui/mainwindowbars.py`

---

## 2026-07-16

### 三项 Qt 刷屏 Warning 根治 + 老旧字体一键排除功能

**问题：** 三种 Qt warning 在日志中反复刷屏：
1. `qt.qpa.fonts: DirectWrite: CreateFontFaceFromHDC() failed` — Windows 老旧字体触发
2. `QFont::setPointSize: Point size <= 0 (-1)` — 字体大小路径多处无下限守卫
3. `QColor::fromRgb: RGB parameters out of range` — 渐变色/阴影色未钳位

**改动：**

1. **老旧字体一键排除**（`utils/shared.py`、`ui/configpanel.py`）：
   - 新增 `LEGACY_FONTS` 常量（`frozenset`），收录 10 个触发 DirectWrite 告警的 Windows 老旧字体（MS Sans Serif, System, Fixedsys 等）
   - `FontExcludeDialog` 新增"添加老旧字体到隐藏列表"按钮 → 点击自动检测本机存在的 legacy 字体并加入隐藏列表，同时从可用列表移除，弹窗展示结果
   - `_add_font_item` 对 legacy 字体跳过预览渲染（以防预览时触发告警），并附 `[老旧]` 标识；真实名称存 `Qt.UserRole`，搜索/移动/导出均读该角色

2. **排除字体过滤遗漏修复**（`ui/fontstyle_manager.py`）：`_sync_controls` 中字体系列改用 `get_filtered_font_list(pcfg.excluded_fonts)` 而非裸 `ALL_FONT_FAMILIES`

3. **QFont::setPointSize 五处下限守卫**：
   - `ui/scene_textlayout.py:137` — `_font_metrics` 的 `QFont()` 构造前先 `size = max(size, 1.0)`
   - `ui/textitem.py:1635` — `setFontSize` 加 `value = max(value, 1)`
   - `ui/textitem.py:1299-1302` — char format 的 `pointSize()/pointSizeF()` 包裹 `max(1)/max(1.0)`
   - `ui/fontformat_commands.py:276,294` — `ffmt_change_font_size` 过滤 `< 0` → `<= 0`，`ffmt_change_rel_font_size` 新增 `<= 0` 守卫
   - `ui/scenetext_manager.py:1187` — `np.clip(..., 0, 1)` → `np.clip(..., 0.5, 1)`

4. **QColor 三处钳位**：
   - `ui/textitem.py:1451-1452` — `get_text_gradient` 的 `gradient_start_color/end_color` 钳位到 `[0, 255]`
   - `ui/textitem.py:1550` — `setStrokeColor` 非 QColor 入参钳位
   - `ui/shadow_gradient_dialog.py:268` — `ColorButton.set_color` 的 `QColor(*self._color_list)` 钳位


---

### 设置面板 UI 语言统一

**需求：** 在不改动左侧导航、不合并页面、不改动功能的前提下，统一设置面板各页面的分组标题、留白与对齐方式。

**改动：**

1. 新增可复用组件 `ConfigSectionHeader`（`ui/custom_widget/section_header.py`），统一为左对齐粗体标题 + 固定边距，并在 `ui/custom_widget/__init__.py` 导出。
2. **General / Models 页面**（`ui/configpanel.py`）：
   - Models："Model Loading" / "Management" 改用 `ConfigSectionHeader`。
   - Project："Startup" / "Output" 改用 `ConfigSectionHeader`；`Quality` 从 `Result image format` 子块中抽出，改为独立同级行。
   - Typesetting："Default Font Format" / "Text formatting" 改用 `ConfigSectionHeader`；8 个字体格式下拉框从 2×4 标签-控件并排改为 4×2 标签在上方的紧凑网格。
   - Interface："Behavior" / "Combo Box Presets" / "Original Compare" 改用 `ConfigSectionHeader`，替换之前临时加粗的 `QLabel`。
3. **模块参数页**（`ui/module_parse_widgets.py`）：
   - 在 `ModuleConfigParseWidget` 动态参数区上方增加 "Parameters" 分组标题。
   - `ParamWidget` 标签列设置最小宽度 160px、开启自动换行、控件列拉伸，使不同参数名的控件左边缘对齐。
   - `TranslatorConfigPanel` 的 "API Profile" 标题改用 `ConfigSectionHeader`。
4. **LLM Profile 页面**（`utils/profile_manager.py`）：
   - 所有区块标题改用 `ConfigSectionHeader`，包括新增 "Connection & Rate Limiting" 标题。
   - 移除旧的居中背景色 `_section_label`。
   - `QFormLayout` 标签统一左对齐、字段自动拉伸。
5. **i18n**：在 `translate/zh_CN.ts` 中新增 `ModuleConfigPanel` "Parameters"、`TranslatorConfigPanel` "API Profile" / "Manage…" 翻译；更新 `Connection & Rate Limiting` 源文本（去掉冒号）；重新编译 `zh_CN.qm`。

**验证：**
- `scripts/check_syntax.py`：通过
- `scripts/i18n_check.py`：新增字符串已补齐，剩余 2 个缺失条目为 `ContextMenuCustomizeDialog` 中预存在的 "Move down" / "Move up"，非本次改动引入
- `tests/test_startup_imports.py`：通过
- 离屏实例化 `ConfigPanel`：成功

**涉及文件：** `ui/configpanel.py`、`ui/module_parse_widgets.py`、`ui/custom_widget/section_header.py`、`ui/custom_widget/__init__.py`、`utils/profile_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 主题色系统重构 — 状态颜色改用主题变量

**问题：** `dependency_dialog.py`、`model_check_dialog.py`、`update_checker.py` 中多处状态提示颜色（成功/警告/危险）使用硬编码 hex，不随亮/暗主题切换。

**改动：**

1. **`ui/misc.py`**：`get_theme_color()` 新增 `key` 参数，可查询任意主题变量而不限于 `@accentPrimary`。
2. **主题变量**（`config/themes.json`）：亮/暗主题统一新增 `@warningColor`；`@successColor`/`@dangerColor` 色值与之前各文件硬编码值对齐。
3. **调用方替换**（3 处）：
   - `ui/dependency_dialog.py` — `_STATUS_COLORS` 静态字典改为 `_get_status_colors()` 函数，从主题变量读取 installed/missing/mismatch 颜色。
   - `ui/model_check_dialog.py` — `_theme_colors()` 的 success/warning/danger 改用 `get_theme_color()`。
   - `ui/update_checker.py` — 警告标签 `color` 改用 `@warningColor`。
4. **Disabled 样式**（`config/stylesheet.css`）：新增 `ConfigContent QLineEdit:disabled` / `QComboBox:disabled` 样式表，禁用态使用 `@disabledForegroundColor`。

**涉及文件：** `ui/misc.py`、`ui/dependency_dialog.py`、`ui/model_check_dialog.py`、`ui/update_checker.py`、`config/themes.json`、`config/stylesheet.css`

---

### 打包控件功能组件化 — 抽取共享 UI 组件

**需求：** 多处文件反复出现 inline QSS 模式（无色箭头 QSpinBox、色块按钮 QPushButton、QFrame 分隔线），需抽取为可复用组件，降低维护成本与样式不一致风险。

**改动：**

1. **`NoArrowsSpinBox`**（`ui/custom_widget/spinbox.py`，新）— 隐藏箭头 QSpinBox，替代此前 2 处 inline `_NO_BTN_STYLE`/`no_btn_style` QSS：
   - `ui/custom_widget/color_picker.py` — `QSpinBox` → `NoArrowsSpinBox`
   - `ui/mainwindow.py` — 范围选择器 `QSpinBox` → `NoArrowsSpinBox`
2. **`ColorSwatchBtn`**（`ui/custom_widget/color_button.py`，新）— 色块按钮，替代此前 4 处 inline `setStyleSheet(f"background-color: rgb(…)")`：
   - `ui/fontstyle_manager.py` — 前景色/描边色 2 个按钮改为 `ColorSwatchBtn`
   - `ui/shadow_gradient_dialog.py` — `ColorButton` 改为继承 `ColorSwatchBtn`
3. **`SeparatorWidget`**（`ui/custom_widget/widget.py`，已有）— 本次将 `ui/fontstyle_manager.py` 的内联 `_Separator` 类 3 处使用替换为该共享组件。
4. **默认调色板**（`config/palette.json`，新）— ColorPickerDialog 读取的 30 色默认色板，从 `_DEFAULT_PALETTE` 常量中分离。
5. **AGENTS.md** — 新增「打包控件功能」章节，指引后续开发者优先使用已有组件而非重新实现。
6. **使用文档**（`docs/打包控件功能使用说明.md`，新）— 说明现有封装模式（禁用自动变灰、"—" 占位符等）及适用场景。

**验证：** `scripts/check_syntax.py`：通过

**涉及文件：** `ui/custom_widget/color_button.py`（新）、`ui/custom_widget/spinbox.py`（新）、`config/palette.json`（新）、`docs/打包控件功能使用说明.md`（新）、`AGENTS.md`、`ui/custom_widget/__init__.py`、`ui/custom_widget/color_picker.py`、`ui/shadow_gradient_dialog.py`、`ui/fontstyle_manager.py`、`ui/mainwindow.py`

---

### 字体样式编辑器视觉重构 + 控件焦点样式统一

**需求：** 字体样式编辑区的控件布局无分区、无视觉层次；各输入控件的聚焦边框颜色微弱（`rgba(128,128,128,0.5)`）；字体下拉栏误设为可编辑输入框。

**改动：**

1. **焦点边框颜色统一为 `#5DADE2`**：
   - `_COMBO_STYLE`（`combobox.py`）— `ConfigComboBox` / `ParamComboBox` 及所有导入处
   - `ConfigLineEdit` / `ConfigTextEdit`（`text_input.py`）
   - `_SPIN_STYLE`（`spinbox.py`）— 新增 `:focus` 规则（此前完全没有焦点样式）
   - `SizeComboBox:focus` / `SmallSizeComboBox:focus` / `SmallComboBox:focus`（`stylesheet.css`）

2. **SizeComboBox / SmallComboBox / SmallSizeComboBox 改用半透圆角风格**：背景 `rgba(128,128,128,0.13)`，圆角 `4px`，移除旧 `@borderColor` / `@transtexteditBackgroundColor` 变量依赖；移除 `SizeComboBox` 的 `max-width: 54px` 限制

3. **字号框加宽**：`fontsizebox.fcombobox.setMinimumWidth(75) → 90`

4. **字体下拉栏取消可编辑**：`FontFamilyComboBox` 删除 `setLineEdit(LineEdit)`，连带删除死代码 `LineEdit` 类及不再需要的 `QLineEdit`/`QFocusEvent`/`QKeyEvent` import

5. **`GroupFrame` 容器控件**（`ui/custom_widget/group_frame.py`，新）— 纯 CSS 驱动的圆角边框容器，用于包裹功能区块

6. **FontFormatPanel 布局重构**：
   - 4 行控件（字体/字号/对齐/轮廓）各用 `GroupFrame` 包裹，间距 `0→4`
   - `vl0`（预设+高级面板）也套 `GroupFrame`，标题与内容共享同一边框
   - hl2（粗体斜体行）GroupFrame 上下 padding 从 `4px` 降为 `1px`

7. **预设/高级面板标题改为紧凑条式**：`ExpandLabel` 新增 `capsule` 模式→无箭头/无 hide 按钮、左对齐文字 + `20px` 高 + 浅底色区分；bug 修复：`setText(text)` 被误放在 `else` 分支导致胶囊模式标题不显示

8. **TextPanel 格式区与内容区加边框分离**：`self.format_section`（CollapsibleSection）放入 `GroupFrame`

**验证：** 语法检查 ✅、启动导入测试 7/7 ✅

**涉及文件：** `ui/custom_widget/group_frame.py`（新）、`ui/custom_widget/combobox.py`、`ui/custom_widget/text_input.py`、`ui/custom_widget/spinbox.py`、`ui/custom_widget/__init__.py`、`ui/custom_widget/view_panel.py`、`ui/text_panel.py`、`ui/text_advanced_format.py`、`ui/text_style_presets.py`、`ui/scenetext_manager.py`、`config/stylesheet.css`

---

## 2026-07-15

### PaddleOCRv6ONNX — 三处 Bug 修复（OCR 不输出 + 宽文本截断 + 依赖误报）

**问题：** paddleocr_v6_onnx 模型加载时反复出依赖安装失败警告，且 OCR 输出为空或残缺——即便检测框完整包裹文本，长文本后半部分也丢失。

**修复：**

1. **批处理填充逻辑 Bug**（`modules/ocr/ocr_onnx.py:217-241`）：
   - GPU 模式下 `_make_uniform_batch_rec` 用 `img_list[:pad_count]` 取填充元素。当 `pad_count > len(img_list)` 时（如 1 个 crop、`batch_num=6` → `pad_count=5`），切片只返回 1 个元素而非 5 个，导致总 batch 仅 2 个 → `results[:-3]` 为空列表
   - 修复：改用 `(img_list * repeats)[:pad_count]` 循环重复，确保精确填满

2. **GPU 宽度裁剪导致长文本截断**（`modules/ocr/ocr_onnx.py`）：
   - 原 GPU patch 将 `resize_norm_img` 输出强制裁剪到 320px 宽，但 ONNX 模型输入为动态宽度（`['DynamicDimension.0', 3, 48, 'DynamicDimension.1']`），原生支持任意宽度
   - 宽文本行（如 500px crop → 缩放到 48px 高后 480px 宽）右侧 160px 被切掉，后半部分丢失
   - 修复：移除宽度裁剪部分，保留纯批次填充（不影响精度）

3. **`ensure_dependencies` 对 onnxruntime-gpu 误判**（`modules/base.py:327`）：
   - `requires_packages` 写 `"onnxruntime"` 但安装的是 `onnxruntime-gpu`，metadata 名称不匹配 → `PackageNotFoundError` → pip 重装失败 → 警告
   - 修复：metadata 找不到时回退 `importlib.import_module(req.name)` 直接导入确认

**涉及文件：** `modules/ocr/ocr_onnx.py`、`modules/base.py`

### QColor RGB 越界警告修复

**问题：** `QColor::fromRgb: RGB parameters out of range` 警告在运行日志中反复出现。`FontFormat.frgb`/`srgb` 字段无类型约束，多处直接访问原始字段传入 `QColor()` 未做钳位。

**修复：**

1. **改用安全方法**（4 处）：
   - `ui/text_style_presets.py:227` — `fontfmt.frgb` → `fontfmt.foreground_color()`
   - `ui/textitem.py:1425` — `fontformat.frgb` → `fontformat.foreground_color()`
   - `ui/fontstyle_manager.py:174,183` — `ffmt.frgb`/`sfmt.srgb` → `foreground_color()`/`stroke_color()`

2. **内联钳位**（12 处）：
   - `ui/textitem.py:1536` — `QColor(*value)` 加 `max(0, min(255, int(c)))`
   - `ui/fontstyle_manager.py:773,790` — `_pending_fg`/`_pending_stroke_color` 钳位
   - `ui/shadow_gradient_dialog.py` — 全部 7 处 `QColor(*[...])` 加钳位
   - `ui/drawingpanel.py:451` — `QColor(*color)` 加钳位

**涉及文件：** `ui/text_style_presets.py`、`ui/textitem.py`、`ui/fontstyle_manager.py`、`ui/shadow_gradient_dialog.py`、`ui/drawingpanel.py`

---

### install_cuda.bat 集成 onnxruntime-gpu

**需求：** onnxruntime（CPU）与 onnxruntime-gpu 是独立包且互斥。用户有 CUDA 显卡但 ONNX Runtime 无 `CUDAExecutionProvider`，OCR 阶段被降级到 CPU。

**改动：**

1. **CC 映射表**增加 `ONNX_RT_SPEC` 列：CC≥7 选 `onnxruntime-gpu>=1.20,<1.29`（CUDA 12.x 构建，13.x driver 向下兼容），CC≥6 选 `>=1.17,<1.19`（CUDA 11.8 构建）

2. **Step 2b**（新）：检测 `onnxruntime.get_available_providers()` 中是否有 `CUDAExecutionProvider`，输出 GOOD/CPU/MISSING

3. **Step 6**：PyTorch 安装增加跳过逻辑——若 `torch.__version__` 含 `+cu` 则跳过

4. **Step 7**（新）：`pip uninstall onnxruntime -y` + `pip install onnxruntime-gpu<版本约束>`

5. **报告区**：逐个组件显示 SKIP/Install 状态，用户知情后再开始下载

6. **Manual 模式**：同时打印 PyTorch 和 ONNX 两条命令

7. **修复两个 bug**：
   - `>`/`<` 未转义导致 `ONNX_RT_SPEC` 变量被重定向截断 → 改用 `set "VAR=VAL"` 语法
   - `nvidia-smi` 找不到时 `subprocess.run` 崩溃 → 改用 `os.popen`

**涉及文件：** `install_cuda.bat`

---

### Smart Reorder — 网格排序后处理（Tools 菜单）

**需求：** PP-OCRv6 按行检测，排序 `(center_y, center_x)` 导致跨象限文本块顺序 zigzag（如角色介绍分布在四角时左上→右上混排），后续合并工具按距离合并后语序错乱。

**改动：**

1. **新增 `sort_by_grid()`**（`utils/textblock.py:1043`）：
   - 将画布划分为 `grid_rows × grid_cols` 网格，块按质心分配到网格单元
   - 按阅读顺序遍历单元（LTR/RTL），单元内按 `(y, x)` 排序
   - 直接解决象限 zigzag：同一区域块连续 → 合并工具语序正确

2. **入口位置**：
   - 顶部栏 **Tools → Smart Reorder…**（与「Region Merge Tool」并列）
   - 弹窗选预设：LTR / RTL / **2×2 Grid** / 3×3 Grid / Custom
   - 确认后即时重排 + 刷新画布和文本列表

3. **新增文件/改动**：`utils/textblock.py`（+45 行）、`ui/mainwindow.py`（SmartReorderDialog + on_smart_reorder）、`ui/mainwindowbars.py`（Tools 菜单项）

**验证：** 语法检查 ✅、qm 编译（969 条）✅、启动测试 ✅

**涉及文件：** `utils/textblock.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`

---

### 模块管理器依赖检查 — 兼容 onnxruntime-gpu

**问题：** `ui/module_manager.py` 检出模块依赖时用 `importlib.metadata.distribution("onnxruntime")` 按包名查找。因 `onnxruntime-gpu` 与 `onnxruntime` 互斥，安装 GPU 版后元数据名不匹配导致 `PackageNotFoundError`，对话框误报"缺少 onnxruntime"。

**修复：** 在 `PackageNotFoundError` 处理中增加回退——`importlib.import_module(req.name)` 直接尝试模块级导入。`onnxruntime-gpu` 提供同名的 `onnxruntime` 顶层模块，导入成功则不报缺失。该模式与 `modules/base.py:327-336` 的 `ensure_dependencies()` 一致。

**涉及文件：** `ui/module_manager.py`

---

### 右键菜单自定义对话框 — 暗色模式修复 + UI 优化

**问题：** 对话框的列表区域使用 `setStyleSheet()` + `palette(window)` 引用系统调色板，在 Windows 上始终解析为亮色，覆盖了全局 stylesheet 的暗色变量。同时默认拖拽指示器（粗黑线）挡视野、无拖拽手柄提示。

**改动：**

1. **渲染方案更换**（`ui/context_menu_config.py`）：
   - 删除 `_MenuPreviewDelegate`（native `CE_MenuItem` 绘制），改用标准 QListWidget 默认渲染
   - 删掉 `list_widget` 的本地 stylesheet 中所有 `palette()` 引用，背景/文字/边框色从全局 stylesheet 继承（自动适配亮/暗主题）
   - 分隔线改用 `QFrame(HLine)` 作为 item widget，不再依赖 delegate

2. **UI 交互优化**：
   - 拖拽指示器改为 2px 细线 + `palette(highlight)` 颜色，不再挡视野
   - 列表项前面加 `⠿` 拖拽手柄视觉提示
   - 增加 ↑↓ 移动按钮（选中项后点击），作为拖拽的替代方案
   - 列表区域 `border-radius: 6px` 圆角 + `::item:hover` 悬停高亮
   - 基于 fontMetrics 的固定按钮宽度，防止中/英文标签在多语言下被裁剪

3. **顺手修复**：
   - `_on_add_separator` 插入的分隔符未创建 QFrame 部件
   - 移动操作时 `takeItem` 会删除 item 的 widget，加 `setParent(None)` 保护

**涉及文件：** `ui/context_menu_config.py`

---

### Tools 菜单重排 + PSD 封存 + 路径重排工具

**需求：** Tools 菜单工具过多需重新分组；PSD 导出有兼容问题需封存；智能重排改为画路径排序。

**改动：**

1. **Tools 菜单重排**（`ui/mainwindowbars.py`）：
   - 重新分为 4 组：「页面布局工具」「文字/样式工具」「导出/批量处理」「外部工具」
   - 减少分隔线，逻辑更清晰

2. **PSD 导出封存**：
   - 菜单项灰色禁用，文本标注 "(维修中)" + tooltip 说明原因

3. **路径重排工具（替换 Smart Reorder）**：
   - **`ui/canvas.py`**：新增路径绘制模式。用户拖拽画路径（笔刷半径 20px），文本框首次被路径碰到时高亮选中并显示序号。松手后发射触碰顺序。`enterReorderMode()` / `exitReorderMode()` 等方法
   - **`ui/textitem.py`**：新增 `_reorder_seq` 属性，重排模式下 badge 显示触碰序号而非 `idx+1`
   - **`ui/mainwindow.py`**：删除 `SmartReorderDialog` 内嵌类和 `on_smart_reorder`；新增 `on_path_reorder()` + `_on_reorder_path_done()`，弹窗三选一「应用/继续绘制/取消」
   - 块索引用 `id(blk)` 身份映射，避免 dataclass `__eq__` 崩溃
   - 块列表从 `canvas.textLayer.childItems()` 取（含未保存的新增块），而非 `current_block_list()`

**涉及文件：** `ui/mainwindow.py`, `ui/canvas.py`, `ui/textitem.py`, `ui/mainwindowbars.py`, `translate/zh_CN.ts`, `translate/zh_CN.qm`

---

### 文字块合并 — 修复视觉扩张 + 重新加入右键菜单

**问题：** 此前实现的文字块合并（Merge Text Blocks）后端完整（`MergeTextBlksCommand` + undo/redo）但 UI 已暂撤：
1. 合并后文本框视觉区域未扩张——`setPos`/`setTextWidth` 不动 `_display_rect`，`boundingRect()` 仍返回宿主原尺寸
2. 右键菜单无入口（仅可通过无默认绑定的快捷键调 LTR 方向）

**改动：**

1. **视觉扩张修复 + xyxy 格式转换**（`ui/scenetext_manager.py`）：
   - `MergeTextBlksCommand.redo()/undo()` 中替换 `setPos` + `setTextWidth` 为 `setRect(xywh, padding=False)`
   - **⚠️ 关键：** `setRect` 调用 `QRectF(*list)` 期望 `[x, y, w, h]` 格式，而 `merged.xyxy` 是 `[x1, y1, x2, y2]` 格式。直接传 `xyxy` 会导致 `width=x2`、`height=y3`（异常增大）。修复：传 `[x1, y1, x2-x1, y2-y1]`

2. **边界计算用实际位置**（`_build_merged_blk`）：将 `blk.xyxy`（保存时的原始坐标）改为 `b.absBoundingRect()`（item 当前可视位置），避免因拖动导致并集偏移

3. **右键菜单集成**（`ui/context_menu_config.py`）：
   - 新增 `_build_merge()` 子菜单函数：选中 ≥2 块时启用，提供 "Left-to-Right" / "Right-to-Left" 两个方向
   - 注册 `CmdDef("merge", "Merge", build_fn=_build_merge)` 到 `COMMAND_REGISTRY`
   - 将 `"merge"` 加入 `DEFAULT_ORDER`（在 `"align"` 与 `"snap_alignment"` 之间）
   - 新增 `_merge_default_order()`：已有保存配置的用户自动将新 `DEFAULT_ORDER` 项（如 `merge`）插入到其前驱项之后

4. **配置默认同步**（`utils/config.py`）：`context_menu_order` dataclass 默认也加上 `"merge"`

**验证：** 语法检查 ✅、qm 编译（1003 条）✅、i18n 检查 ✅、启动测试 ✅

5. **撤回定位修复**（`MergeTextBlksCommand`）： 
   - `undo()` 原来用 `survivor_original_blk.xyxy`（保存到文件时的原始坐标）恢复位置。若用户拖动过块再合并，此值过时导致撤回后宿主跳到旧位置
   - 修复：合并瞬间用 `survivor.absBoundingRect()` 记录实际位置 `survivor_original_xyxy`，撤回时用此值恢复

6. **Merge 改一级按钮 + 方向设置移至 Behavior 子菜单**：
   - 用户反馈：Merge 是频繁操作，二级菜单增加点击层级。改为单次点击，方向设置放入新 "Behavior" 子菜单
   - `_build_merge` 从子菜单改为单 `QAction`，方向根据 `pcfg.merge_rtl` 动态决定
   - 新增 `_build_behavior` 子菜单：内含 Snap Alignment + Merge Right-to-Left + Normalize Breaks and Shrink 三个勾选项
   - `DEFAULT_ORDER` 中 `"merge"` 后跟 `"behavior"` 替代原 `"snap_alignment"`；`"normalize_breaks_shrink"` 从一级菜单移除
   - `normalize_breaks` 改为检查 `pcfg.normalize_shrink` 决定是否收缩（`emit(pcfg.normalize_shrink)`）
   - 新增配置 `pcfg.merge_rtl: bool` + `pcfg.normalize_shrink: bool`（`utils/config.py`）

**验证：** 语法检查 ✅、qm 编译（1005 条）✅

**涉及文件：** `ui/scenetext_manager.py`、`ui/context_menu_config.py`、`ui/mainwindow.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`


### HelpDialog — 字体/代码块/搜索修复

**问题：**
1. 英文字符（尤其 `{}`）显示为宋体风格——body CSS 的 `font-family` 与 `setMarkdown()` 内部字体格式冲突
2. 代码块无视觉区分——CSS 对 `<pre>` 无效，因为 `setMarkdown()` 内部设了 inline QTextCharFormat 覆盖 document stylesheet
3. 搜索跳转错位——全文 `find()` 对重复文本跳到第一个出现处

**修复：**

1. **字体**：移除 body CSS 的 `font-family`，改用 `document().setDefaultFont(QFont("Microsoft YaHei", 10))`——document 级默认字体优先级低于 setMarkdown 的 block 级格式，互不冲突

2. **代码块**：新增 `_style_code_blocks()` 程序化后处理——`setMarkdown()` 后遍历所有 block，检测 `charFormat().fontFixedPitch()` 识别围栏代码块，直接设置 `QTextBlockFormat` 的 background/margin。连续代码块合并 margin 消除块间间隙

3. **搜索跳转**：搜索结果锚点从 `(doc_idx, matched)` 扩展为 `(doc_idx, matched, heading)`。跳转时先 `find(heading)` 将光标定位到目标小节，再 `find(matched)` 在正确区间内匹配

**涉及文件：** `ui/help_dialog.py`

---

### HelpDialog — 代码块/搜索/间距统一 + 已知问题文档

**问题/需求：**
1. 代码块样式仍未呈现——`fontFixedPitch()` 对绝大多数代码块返回 False
2. 搜索跳转仍不准——heading 范围限定被代码块/正文中相同文字截胡
3. 正文区右侧、上下零间距；侧栏"本节目录"标签与正文区顶部不对齐；搜索框与正文区底部不对齐；目录树左侧展开图标被裁剪

**改动：**

1. **代码块检测修复**（`_block_is_code_block()` 静态方法）：放弃 `fontFixedPitch()`，改用 `fontFamilies()` 检测 Courier New 等等宽字体。两端扫描（样式 + 合并连续间隙）均用新方法。（⚠️ 检测已修，但视觉效果仍未达预期，见 `docs/已知问题.md`）

2. **搜索导航简化**：放弃 heading 限定，`_search_anchors` 从 `(doc_idx, matched, heading)` 降为 `(doc_idx, matched)`，点击结果直接全文 `find(plain)`。（⚠️ 重复文本仍跳到首次出现处，见 `docs/已知问题.md`）

3. **间距统一**：
   - QSplitter 边距 `(0, 8, 0, 8)` → `(8, 8, 8, 8)`，左右 8px 让正文区右边框不再贴窗边
   - QTextBrowser 加 `document().setDocumentMargin(8)`
   - 去掉"本节目录"标签（`_outline_label` 整条删除）
   - Sidebar 边距 `(8, 8, 8, 8)` → `(8, 9, 8, 9)`，上下 9px 匹配正文区 `1px border + 8px docMargin`，目录框顶部与正文区首行平齐、搜索框底部与正文区尾行平齐
   - 目录树去掉展开折叠箭头（`rootIsDecorated(True)→False`），缩进 16→12，CSS `::branch` 隐藏

4. **`docs/已知问题.md`**：新文件，记录代码块样式和搜索跳转两个待修问题的根因与后续方向。

**涉及文件：** `ui/help_dialog.py`、`config/stylesheet.css`、`docs/已知问题.md`（新）

---

### 文字块合并功能（后端，UI 已暂撤）

**需求：** 右键菜单支持合并多个选中文字块为一个，支持 LTR/RTL 方向控制合并后文本排列顺序。

**改动要点：**
1. **信号+后端**（`ui/canvas.py`、`ui/scenetext_manager.py`）：添加 `merge_textblks` 信号 + `MergeTextBlksCommand`（完整 undo/redo）+ `_build_merged_blk` 合并逻辑（xyxy 并集、原文/译文串联、过滤空白译文）
2. **快捷键**（`ui/configpanel.py`、`ui/mainwindow.py`）：注册 `merge_blks`（默认无绑定），触发时默认 LTR
3. **UI 暂撤**：右键菜单条目已删除，因文本框扩张未生效 + 交互层级待优化。后端保留，通过快捷键可调
4. **已知问题：** `setPos`/`setTextWidth` 无法让 TextBlkItem 视觉区域覆盖合并后的 xyxy 并集，需后续排查 TextBlkItem 的 boundingRect/paint 逻辑

**涉及文件：** `ui/canvas.py`、`ui/scenetext_manager.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`

---

### 设置面板文本输入框全量迁移 NoArrowsSpinBox 风格

**需求：** 设置面板（ConfigContent）中所有文本输入框（QLineEdit / QTextEdit / QPlainTextEdit）视觉上不统一，需全部换用 NoArrowsSpinBox 的半透明圆角外观。同时将复选框等基础控件纳入打包控件体系。

**改动：**

1. **`ConfigCheckBox`**（`ui/custom_widget/checkbox.py`）— 新打包控件，自动设置 `objectName='ConfigCheckBox'`，消除此前 8 处 `QCheckBox()` + `setObjectName()` 重复模式。
2. **`ConfigLineEdit`** / **`ConfigTextEdit`**（`ui/custom_widget/text_input.py`，新）— 与 NoArrowsSpinBox 视觉一致的单行/多行文本输入控件。
3. **迁移范围**：
   - `configpanel.py`：`PercentageLineEdit` 基类切换 + `addLineEdit` / `_make_preset_row` / 对话框搜索框
   - `profile_manager.py`：LLM 配置表单 28 处 `QLineEdit` + 12 处 `QTextEdit` 全部替换
   - `module_parse_widgets.py`：`ParamLineEditor` / `ParamEditor` 基类切换
4. **`configpanel.py`** 原 2 处 `QSpinBox` + `setButtonSymbols` 改用 `NoArrowsSpinBox`（Max Font Size / Original Compare 预设）。
5. **文档** `docs/打包控件功能使用说明.md`：新增 §9 ConfigCheckBox、§10 ConfigLineEdit/ConfigTextEdit。

**验证：** 语法检查 ✅、启动导入测试 7/7 ✅

**涉及文件：** `ui/custom_widget/checkbox.py`、`ui/custom_widget/text_input.py`（新）、`ui/custom_widget/__init__.py`、`ui/configpanel.py`、`utils/profile_manager.py`、`ui/module_parse_widgets.py`、`docs/打包控件功能使用说明.md`

---

### 设置面板下拉框统一 NoArrowsSpinBox 风格

**需求：** 设置面板中已使用 `ConfigComboBox` / `ParamComboBox` 作为打包控件，但未自带样式，外观依赖全局 CSS。需与 `ConfigLineEdit` 等保持一致的半透明圆角外观。

**改动：**

1. **`ui/custom_widget/combobox.py`** — 在 `ConfigComboBox` 和 `ParamComboBox` 的 `__init__` 中应用 NoArrowsSpinBox 风格的 `setStyleSheet`（半透明背景、圆角边框、focus 加亮），样式字符串提取为模块级常量 `_COMBO_STYLE`。
2. **`ui/configpanel.py`** — 唯一遗留的原始 `QComboBox()`（`punctuation_position_combo`）改用 `ConfigComboBox`，移除不再需要的 `QComboBox` qtpy 导入。
3. **`config/stylesheet.css`** — 为 `QComboBox` 弹出下拉列表（`QAbstractItemView`）添加主题配套样式：跟随亮/暗主题的背景、圆角边框、item 悬浮高亮和选中态 accent 色。
4. **`ui/network_settings_dialog.py`** — 1 处 `QComboBox` → `ConfigComboBox`，4 处 `QLineEdit` → `ConfigLineEdit`。
5. **`utils/profile_manager.py`** — `ProfileManagerDialog` / `ProfileManagerWidget` 中 7 处 `QComboBox` → `ConfigComboBox`，2 处 `QCheckBox` → `ConfigCheckBox`，2 处 `QSpinBox` → `NoArrowsSpinBox`，1 处 `QDoubleSpinBox` → `NoArrowsDoubleSpinBox`。
6. **`ui/custom_widget/spinbox.py`** — 新增 `NoArrowsDoubleSpinBox(QDoubleSpinBox)`，与 `NoArrowsSpinBox` 共用 `_SPIN_STYLE` 样式常量。导出到 `__init__.py`。
7. **文档** `docs/打包控件功能使用说明.md`：更新 §4 纳入 `NoArrowsDoubleSpinBox` + 补充迁移表；§10/§11 新增迁移范围。

**验证：** 语法检查 ✅、启动导入测试 7/7 ✅

**涉及文件：** `ui/custom_widget/combobox.py`、`ui/custom_widget/spinbox.py`、`ui/custom_widget/__init__.py`、`ui/configpanel.py`、`config/stylesheet.css`、`ui/network_settings_dialog.py`、`utils/profile_manager.py`、`docs/打包控件功能使用说明.md`

---

### Qt Warning 统一管控 — 三层架构（全局消息处理器 + 模型级钳位 + 调用点修复）

**问题：** 此前逐个添加 `max(1.0)` / `max(0, min(255,...))` 守卫的模式既繁琐又容易遗漏，`QFont::setPointSize: Point size <= 0` 和 `QColor::fromRgb: RGB parameters out of range` 仍偶发。

**方案：** 三层统一管控架构

1. **第 1 层 — Qt 消息处理器（全局安全网）**（`utils/safe_qt.py`，新）：
   - `install_qt_warning_filter()` 在 QApplication 创建后立即安装自定义消息处理器
   - 通过 `str.startswith(tuple)` 匹配拦截 6 种 Qt 警告前缀（font size ≤0、RGB 越界等），静默吞噬
   - 环境变量 `BALLOONTRANS_DEBUG_QT_WARNINGS=1` 可关闭过滤用于调试
   - 还导出了 `safe_qcolor()` 和 `clamp_font_size()` 工具函数供后续代码使用
   - **零调用点改动**，覆盖当前和未来所有违规调用

2. **第 2 层 — FontFormat 模型级钳位**（`utils/fontformat.py`）：
   - `size_pt` property 改为 `max(px2pt(self.font_size), 1.0)`
   - `__post_init__` 中 `font_size = max(float(self.font_size), 1.0)`
   - 从根源阻止零/负字体大小传播到渲染代码

3. **第 3 层 — 7 个明确未防护的调用点修复**：
   - `textitem.py:1060` — `setPointSizeF(ffmat.size_pt)` 加 `max(…, 1.0)`
   - `scenetext_manager.py:1138,1200` — 两处 `setPointSizeF(new_font_size)` 加 `max(…, 1.0)`
   - `configpanel.py:463` — `pointSize() - 2` 加 `max(…, 1)`
   - `color_button.py:45` — `int(c)` → `max(0, min(255, int(c)))`
   - `clock_dial.py:65` — 同上
   - `label.py:90` — 列表路径 `QColor(*color)` 加钳位 + fallback
   - `overlay_modal.py:71` — `int(round(alpha * 255))` 钳位到 `[0, 255]`

**验证：** 语法检查 ✅、启动导入测试 7/7 ✅、i18n 无新问题 ✅

**涉及文件：** `utils/safe_qt.py`（新）、`launch.py`、`utils/fontformat.py`、`ui/textitem.py`、`ui/scenetext_manager.py`、`ui/configpanel.py`、`ui/custom_widget/color_button.py`、`ui/custom_widget/clock_dial.py`、`ui/custom_widget/label.py`、`ui/overlay_modal.py`