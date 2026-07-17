# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。

## 2026-07-17

### 拖拽导入图片 + "打开最近"禁用样式 + 项目失效画布清空

**需求：** 支持单张/多张图片拖拽或通过菜单"打开图片..."导入，自动创建临时工作目录；添加"项目另存为..."功能；无历史时"打开最近"灰色禁用；项目文件被外部删除时画布自动清空。

**改动：**

1. **配置层**（`utils/shared.py`、`utils/config.py`）：
   - 新增 `TEMP_PROJECTS_DIR` 常量
   - `ProgramConfig` 新增 `temp_project_dir` / `auto_clean_temp_projects` 字段

2. **Canvas 拖拽**（`ui/canvas.py`）：
   - 空白画布居中提示 + 拖拽悬停蓝色半透明 overlay
   - 已有项目时不响应拖拽
   - 拖拽/菜单导入图片 → emit `drop_images` 信号
   - 项目失效时 `_clear_canvas()` 重置所有图层

3. **MainWindow 工作流**（`ui/mainwindow.py`）：
   - `openImages()`：创建 `<程序目录>/projects/<basename>_<uuid8>/` 临时目录，拷入图片
   - `saveProjectAs()`：`shutil.copytree` 复制全部内容到用户指定位置，切换到新路径，删除原临时目录
   - `closeEvent`：若 `auto_clean_temp_projects` 则清理本次会话的所有临时目录
   - 启动时设默认几何尺寸（屏幕 80%），避免拖拽还原后窗口过小

4. **LeftBar 菜单**（`ui/mainwindowbars.py`）：
   - 新增 "Open Image ..." / "Save Project As ..." 菜单项及快捷键
   - "打开最近"子菜单尾部追加分隔线 + "清空历史记录"按钮
   - 无历史时 `_recent_menu_action.setEnabled(False)` 灰色禁用

5. **设置面板**（`ui/configpanel.py`）：
   - "项目"页面新增 "临时项目" 区块 + `auto_clean_temp_projects` 开关

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：新增 5 条翻译，qm 编译 1009 条

**验证：** 语法检查 ✅、i18n 检查 ✅、启动导入测试 ✅

**涉及文件：** `ui/canvas.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/configpanel.py`、`utils/shared.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 🏷️ Release v0.3.0 — 设置面板 UI 统一 + 打包控件组件化 + 字体样式重构

**版本：** `v0.3.0`（基于 `v0.2.1`，+5 commits，+2,153 / -1,993 行）

**主要变更：**
1. **设置面板 UI 统一 + 主题色系统重构** — 分组标题统一、状态色改用主题变量、NavList 焦点框修复
2. **打包控件功能组件化** — 抽取 `NoArrowsSpinBox`、`ColorSwatchBtn`、`ConfigCheckBox`、`ConfigLineEdit`/`ConfigTextEdit`、`GroupFrame`、`SeparatorWidget` 等共享组件，新增使用文档
3. **字体样式编辑器视觉重构** — 焦点边框统一 `#5DADE2`、`GroupFrame` 分区包裹、预设/高级面板紧凑条式标题、字体下拉取消可编辑
4. **Qt Warning 统一管控** — 三层架构（全局消息处理器 → 模型级钳位 → 调用点修复），`utils/safe_qt.py` 新增
5. **老旧字体一键排除** — `LEGACY_FONTS` 常量 + FontExcludeDialog 一键操作
6. **移除帮助手册** — 删除 `ui/help_dialog.py` 及相关引用（-884 行减法）

**验证：** 语法检查 ✅、启动导入测试 ✅、i18n 检查 ✅

---

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

### 渐变样式不随重启重置修复

**问题：** 用户启用渐变后 `gradient_enabled: true` 写入 `config.json`，下次启动加载配置后保持开启。

**根因：** `global_fontformat.gradient_enabled` 随 `save_config()` 持久化，`load_config()` 无重置逻辑。

**修复：** `utils/config.py:load_config()` — `pcfg.merge(config)` 后重置渐变字段为 `FontFormat` 默认值（`gradient_enabled=False` 等）。

**验证：** 语法检查 ✅、启动导入测试 ✅

**涉及文件：** `utils/config.py`

---

### 空白画布启动时显示滚动条修复

**问题：** 刚启动时空白画布出现滚动条，暗示场景有缩放但实际无内容。

**根因：** `ui/canvas.py` 初始化时 `baseLayer` rect 设为 `QRectF(0, 0, SCREEN_W, SCREEN_H)`（3840×2160），但无图片时 `updateCanvas()` 因 `base_pixmap is None` 跳过，`_fitToWindow()`/`scaleImage()` 不执行。场景以 1:1 显示巨大的 baseLayer，视口装不下，ScrollBar 因而显示。

**修复：** 初始 rect 改为 `QRectF(0, 0, 1, 1)`。加载图片后 `updateCanvas()` 会重新设为实际图片尺寸。

**验证：** 语法检查 ✅、启动导入测试 ✅

**涉及文件：** `ui/canvas.py`

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

### 配置导入导出功能

**需求：** 支持导入/导出配置（`.json`），可配置排除敏感信息（API Key），导入时自动比对 schema 并给出兼容性提示。

**改动：**

1. **工具函数**（`utils/config.py`）：
   - 新增 `export_config(path, exclude_api_keys, exclude_recent_projects)` — 序列化 pcfg → JSON，可选清除 `model_profiles` 中的 `api_key`/`proxy`，插入 `_export_meta` 元信息段（版本、时间、排除项），原子写入
   - 新增 `import_config(path)` — 读取 JSON，用 `_compare_export_schema()` 递归比对导出 key 与当前 `ProgramConfig` schema，收集未知 key（未来版本）和缺失 key（旧版本），`pcfg.merge()` 合并后 `save_config()`
   - 新增辅助函数 `_compare_export_schema(data, ref_obj, prefix)` — 递归比对，返回 `{unknown_keys, missing_keys}`

2. **UI 页面**（`ui/configpanel.py`）：
   - General 分组下新增"配置管理"页面，含"导出配置"/"导入配置"按钮
   - 导出时可选"排除 API 密钥"（默认勾选）
   - 导入后弹兼容摘要对话框，显示未知项/缺失项数量（最多 5 条），提示关闭重开面板刷新

3. **翻译**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：新增 20 条翻译，qm 编译 1030 条

4. **文档**（`docs/配置导入导出.md`，新）：功能说明、导出格式、敏感信息排除、导入兼容性说明

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 2 条预存缺失）、启动导入测试 5/5 ✅

**涉及文件：** `utils/config.py`、`ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/配置导入导出.md`（新）

---

### 上下文翻译两处运行时错误修复

**问题：** 上下文翻译功能运行时两处 AttributeError：

1. `ui/context_log_dialog.py:37` — `clear_btn` 连接了不存在的 `self.clear`，应为 `self.output.clear`
2. `modules/translators/context_batch.py:467` — 局部变量 `re` 遮蔽了模块 `re`，导致后续 `re.search()` 抛出 `AttributeError: 'str' object has no attribute 'search'`

**改动：**

1. `ui/context_log_dialog.py`：`clear_btn.clicked.connect(self.clear)` → `self.output.clear`
2. `modules/translators/context_batch.py`：`re = self.api_config.get("reasoning_effort", "")` → `reasoning_effort = ...`，解除对模块 `re` 的遮蔽

**验证：** 语法检查 ✅

**涉及文件：** `ui/context_log_dialog.py`、`modules/translators/context_batch.py`

---