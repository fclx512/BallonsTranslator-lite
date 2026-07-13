# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-07-12

### P0#1 — ModuleThread 可取消 + 准备进度

**需求：** 上游 `module_manager.py` 的 `ModuleThread` 有 `cancel_event`（`threading.Event`）线程安全取消、`module_prepare_progress` 信号分阶段上报加载进度。我们的版本缺少这两个特性，模块切换时用户无法获知进度也无法取消。

**改动：**

1. **`ui/module_manager.py`** — ModuleThread 新增 `cancel_event`、`module_prepare_progress` Signal、`_prepare_module_class()`、`installMissingPackagesAndSetModule()`；`_set_module()` 重写为三阶段（importing → instantiating → loading_model）各阶段检测取消标记；ModuleManager 新增准备进度对话框 + 4 线程信号连接 + `cancelModulePreparation()`

2. **`ui/custom_widget/message.py`** — `ProgressMessageBox` 新增可选停止按钮（`show_stop_btn=True` 时在任务条下方添加 Stop 按钮）

**涉及文件：** `ui/module_manager.py`、`ui/custom_widget/message.py`

---

### P0#2 — BottomBar ModuleSelectionWidget

**需求：** 将 BottomBar 中基于组合框的模块选择器（`SelectionWithConfigWidget`、`TranslatorSelectionWidget`）替换为上游风格的 `QToolButton` + 图标 + 弹出菜单选择器。

**改动：**

1. **`ui/module_tool_button.py`（新）** — `ModuleSelectionWidget` 类（QToolButton + 18px SVG 图标 + 模块名 + QMenu 弹出菜单 + 悬停配置齿轮按钮）；菜单从隐藏的 SmallComboBox 动态重建，处理分隔符条目
2. **`ui/mainwindowbars.py`** — 4 个选择器替换为 ModuleSelectionWidget 实例（各带专属图标）；删除旧的 `SelectionWithConfigWidget`、`TranslatorSelectionWidget`、`SmallConfigPutton`、`CFG_ICON`
3. **`ui/mainwindow.py`、`ui/mainwindow_mixin.py`** — `finishSetTranslator()` → `setSelectedValue()`
4. **`icons/small_ocr.svg`、`icons/edit.svg`** — 从上游复制新增

**涉及文件：** `ui/module_tool_button.py`（新）、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`ui/mainwindow_mixin.py`、`icons/small_ocr.svg`、`icons/edit.svg`

---

### 上游样式迁移（首次）

**需求：** 看齐上游（BallonsTranslator）设置面板 UI 样式：checkbox indicator、输入框下划线聚焦、BottomBar 模块按钮、QMenu 菜单样式。

**改动：**

1. **`config/stylesheet.css`** — 新增 ConfigCheckBox/ParamCheckBox indicator 13×13px + SVG 勾选；ConfigContent 输入框下划线聚焦样式；BottomBarModuleToolButton 悬停/按下/展开色 + SVG 菜单指示器；QMenu 圆角 + 26px item 高 + 分割线
2. **`ui/configpanel.py`** — checkbox 设 `objectName('ConfigCheckBox')`、`ConfigContent` 设 `objectName`
3. **`ui/module_parse_widgets.py`** — `ParamCheckBox` 设 `objectName`
4. **`utils/shared.py`** — 控件尺寸紧凑化（comboBox height 30→26, width 200→180/332→300/468→420, lineedit 45→30）
5. **`icons/`** — 新增 5 个 SVG（`checkbox_checked.svg`、`textdetect.svg`、`text.svg`、`eye.svg`、`image.svg`）

⚠️ **样式覆盖不完全** — Checkbox、ConfigContent、BottomBar 等模块的部分子控件/状态仍有未覆盖的样式缺口，需后续完整逐项排查。

**涉及文件：** `config/stylesheet.css`、`ui/configpanel.py`、`ui/module_parse_widgets.py`、`utils/shared.py`、`icons/*.svg`

---

### P1#6 LLM Profile 页面：占位按钮 → 内联表单（方案 A）

**需求：** 将 LLM Profile 从占位按钮（打开模态 dialog）改为内联编辑页面，布局从左右 splitter 改为顶部工具栏 + 下方全宽表单。

**改动：**

1. **`utils/profile_manager.py`** — 新增 `ProfileManagerWidget` 类：
   - 顶部工具栏：`QComboBox` 切换 profile + [+ Add] [Delete] [Restore Builtins] 按钮
   - `QScrollArea` 内包含完整编辑表单（4 组：Basic Settings / Connection & Rate Limiting / Translation Settings / OCR Settings）
   - 自动保存：切换 profile / 添加 / 删除时落盘，离开页面（`hideEvent`）时自动保存
   - 发出 `profiles_changed` 信号供外部监听

2. **`ui/configpanel.py`** — 替换占位页：占位 QWidget + 按钮 → `ProfileManagerWidget` 实例；连接 `profiles_changed` → `self.profiles_changed`；移除已无引用的 `_open_profile_manager` 方法

**解决的核心问题：**
- 原 dialog 的 splitter 左右布局（220px 列表 + 520px 表单）在页面可用宽度 ~520px 下过于拥挤
- 方案 A：顶部 combo 切换 + 表单独占全宽，充分利用横向空间
- 避免了双层 scroll 嵌套（用 `_add_page` 而非 `_add_grouped_page`）

**涉及文件：** `utils/profile_manager.py`、`ui/configpanel.py`

---

### 启动冒烟测试（tests/test_startup_imports.py）

**需求：** 此前 `ProfileManagerWidget._build_ui()` 使用了未导入的 `QFrame`，启动时 `NameError` 崩溃。需添加测试在下次改类似代码时提前拦截。

**改动：**
- `tests/test_startup_imports.py`（新）— 5 个测试用例：
  1. `utils.config` 导入
  2. `utils.profile_manager` 所有 public 符号导入
  3. `launch.py` 顶层导入
  4. `ProfileManagerWidget()` 实例化（`QApplication` offscreen 模式，直接触发 `_build_ui()`，捕捉 `QFrame` 类缺失）
  5. `ui.configpanel` 模块级导入

**用法：** `./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py`

**涉及文件：** `tests/test_startup_imports.py`（新）

---

### i18n：ProfileManagerWidget 翻译条目

**需求：** `ProfileManagerWidget` 是新增类，其 `self.tr()` 字符串缺乏对应 `<context>` 条目。

**改动：**
- `scripts/add_ts_context.py`（新建，一次性工具）— 从已有语境（`ProfileManagerDialog` 等）收集翻译，自动生成 `ProfileManagerWidget` 的 `<context>` 块
- `translate/zh_CN.ts` — 新增 `ProfileManagerWidget` 语境，60 条 `<message>`，翻译复用已有条目
- `translate/zh_CN.qm` — 重新编译（917 条，比之前 +59）

**涉及文件：** `scripts/add_ts_context.py`（新）、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### AGENTS.md 更新：测试流程

**改动：** AGENTS.md 新增「测试流程」章节，定义四步验证顺序：语法检查 → i18n 检查 → 启动冒烟测试 → 目视确认。

**涉及文件：** `AGENTS.md`

---

### P2 样式补齐 + 所有 checkbox 统一 indicator 样式

**需求：** 逐段对比上游 stylesheet.css 发现 8 条缺失规则；所有非图标类 QCheckBox（设置页、模块参数、对话框等）缺少 `setObjectName` 导致 indicator 回退原生渲染。

**改动：**

1. **`config/stylesheet.css`** — 
   - 新增：`SeparatorWidget { color }`、`ColorPickerLabel` 边框 + `::hover`、`SmallColorPickerLabel` 边框、`QLabel#fontAngleLabel`、`ConfigContent QPushButton` 尺寸/字号、`ConfigContent #ConfigInlineRow` 背景、`ConfigContent QLabel#ParamFieldLabel` 字号、`QMenu::item { bg }`
   - 新增：`QGroupBox::indicator` 全套样式（可勾选标题框）、`QDialog QCheckBox::indicator` 全套样式（一次覆盖所有对话框 checkbox）
   
2. **`ui/module_parse_widgets.py`** — `ParamCheckGroup` 内 checker、`ParamCheckerBox.checker`、`TextDetectConfigPanel.keep_existing_checker` 加 `setObjectName('ParamCheckBox')`

3. **`utils/profile_manager.py`** — `ProfileManagerWidget.vision_check`、`ProfileManagerDialog.vision_check` 加 `setObjectName('ConfigCheckBox')`

4. **`ui/mainwindow.py`** — `all_pages_cb`、`cb`（stage labels）、`ctx_trans_cb`、`glossary_cb`、`wo_update_cb` 加 `setObjectName('ConfigCheckBox')`

5. **`ui/mainwindow_mixin.py`** — `all_pages_cb` 加 `setObjectName('ConfigCheckBox')`

6. **`ui/fontstyle_manager.py`** — Bold/Italic/Underline/Vertical 4 个 checkbox 加 `setObjectName('ConfigCheckBox')`

7. **`icons/rotation.svg`** — 从上游复制

**涉及文件：** `config/stylesheet.css`、`ui/module_parse_widgets.py`、`utils/profile_manager.py`、`ui/mainwindow.py`、`ui/mainwindow_mixin.py`、`ui/fontstyle_manager.py`、`icons/rotation.svg`

---

### i18n 修复：14 条缺失 ts 条目 + ProfileManagerWidget 空翻译

**需求：** `i18n_check.py` 报 14 条 `self.tr()` 无对应 `<message>`，包括 ConfigPanel 导航树标题（Modules/Module Actions/LLM Profile）、BottomBar Translator、FilterableListDialog、ModuleThread 进度文字、ProgressMessageBox；ProfileManagerWidget 50 条 `<translation type="unfinished"/>` 未填入实际翻译。

**改动：**
- `translate/zh_CN.ts` — 新增 14 条缺失条目（ConfigPanel/BottomBar/FilterableListDialog/ModuleThread/ProgressMessageBox）、填充 ProfileManagerWidget 全部 50 条空翻译、新增 5 条缺失源字符串；同时保留 ProfileManagerDialog 已有翻译
- `translate/zh_CN.qm` — 重新编译（936 → 936 translations）

**涉及文件：** `translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### BottomBar 管线模块选择器始终显示

**需求：** 底部栏的 TextDetector/OCR/Inpaint/Translator 选择器此前跟随 Run 对话框的阶段勾选状态显隐（旧 UI 拥挤时为了省空间）。新版 UI 有充足空间，上游已改为全部显示。

**改动：**
- **`ui/mainwindow.py`** — `on_enable_module()` 中移除 4 个 `setVisible(checked)` 调用；初始设置全部改为 `setVisible(True)`

**涉及文件：** `ui/mainwindow.py`

---

### 上游设置面板布局对齐（P0+P1）

**需求：** 此前跟进了上游的拆分设计（导航树 + 页面栈），但各管线模块页面的内部布局（间距、标签重复、控件对齐、label_above 支持）尚未同步。

**改动：**

1. **`ui/module_parse_widgets.py`** —
   - `ModuleConfigParseWidget.vlayout` 间距 `30→14`，与上游紧凑风格一致
   - `ParamWidget` 添加 `SizePolicy(Preferred, Maximum)`，允许水平填充/限制纵向增长，修复参数项在父布局中居中显示的问题
   - `ParamWidget` 添加 `label_above` 支持：param dict 中声明 `"label_above": True` 时，标签置于控件上方跨宽布局（供长文本编辑器使用）
   - `ParamWidget` 添加 `exclude_keys` 参数，供子类跳过特定参数的网格渲染

2. **`ui/configpanel.py`** —
   - `_add_grouped_page` 中自动隐藏 `widget.module_label`（PanelGroupBox 标题已提供相同信息，消除视觉冗余）

**涉及文件：** `ui/module_parse_widgets.py`、`ui/configpanel.py`

---

### Translator API Profile 独立凸出

**需求：** `active_profile` 是 LLM API 翻译器最常用的切换项，原来混在 `ParamWidget` 网格参数中不够醒目，需要独立出来作为视觉重点。

**改动：**

1. **`ui/module_parse_widgets.py`** —
   - `TranslatorConfigPanel` 新增独立 API Profile 区块（header + ConfigComboBox + "Manage…" 按钮），位于语言选择器下方、其他参数上方
   - 新增 `navigate_to_llm_profile` 信号，"Manage…" 按钮点击时触发，跳转到 LLM Profile 编辑页
   - 覆盖 `updateModuleParamWidget`，创建 ParamWidget 时过滤掉 `exclude_keys={"active_profile"}`
   - `_refresh_profile_section()` 根据当前翻译器是否支持 profile 自动显隐区块并填充选项

2. **`ui/configpanel.py`** —
   - 连接 `trans_config_panel.navigate_to_llm_profile` → `self.focusOnLLMProfile`

**涉及文件：** `ui/module_parse_widgets.py`、`ui/configpanel.py`

---

## 2026-07-13

### 帮助系统框架（HelpDialog）

**需求：** 软件内帮助文档阅读器，支持文档浏览、标题导航、跨文档搜索。

**改动：**

1. **`ui/help_dialog.py`（新）** — `HelpDialog(QDialog)` 完整实现：
   - 非模态窗口，左侧栏（文档列表 + 本节目录），主内容区 `QTextBrowser.setMarkdown()`
   - 跨文档全文搜索，结果以 PanelGroupBox 风格渲染到主内容区（主题色自适应），点击跳转

2. **`ui/mainwindowbars.py`** — Help 菜单新增"使用手册" action

3. **`ui/mainwindow.py`** — 连接信号 + `show_help_dialog()` 懒加载

4. **`translate/zh_CN.ts`** / **`.qm`** — 新增 HelpDialog 上下文翻译 12 条

5. **`tests/test_startup_imports.py`** — 新增 HelpDialog 导入测试 + 静态方法测试

6. **`docs/help/测试文档.md`（新）** — 用于验证样式渲染和标题跳转的测试文档

⚠️ **当前状态：框架已实现，文档正文和体验细节需后续细化。**

**涉及文件：** `ui/help_dialog.py`（新）、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_startup_imports.py`、`docs/help/测试文档.md`（新）

