# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在文档末尾写入。

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

### 整理换行功能改造：可选直接删除模式 + 移出右键菜单 + 死代码清理

**需求：**
1. 批量整理换行增加"直接删除"（不添加空格）选项
2. 移除右键菜单入口（保留顶部工具菜单）
3. 改名"批量整理换行"→"整理换行"

**改动：**

1. **核心逻辑**（`utils/text_normalize.py`）：
   - `normalize_softbreaks()` 新增 `mode="space"|"delete"` 参数
   - `"space"`（默认）：换行→空格；`"delete"`：直接删除换行

2. **对话框**（`ui/normalize_breaks_dialog.py`）：
   - 新增「替换为空格」/「直接删除」两个 QRadioButton
   - 窗口标题从「Batch Normalize Breaks」改为「Normalize Breaks」

3. **右键菜单**（`ui/context_menu_config.py`）：
   - 移除 `normalize_breaks` 命令定义及 `DEFAULT_ORDER` 条目
   - Behavior 子菜单移除「Normalize Breaks and Shrink」选项
   - 删除 `_toggle_normalize_shrink`、`_normalize_breaks_enabled` 辅助函数

4. **菜单改名**（`ui/mainwindowbars.py`）：
   - 「Batch Normalize Breaks…」→「Normalize Breaks…」

5. **死代码清理**：
   - `utils/config.py`：删除 `normalize_shrink` 字段
   - `ui/canvas.py`：删除 `normalize_break_requested` 信号
   - `ui/scenetext_manager.py`：删除信号连接及 `on_normalize_break` 方法

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 3 条翻译（Normalize Breaks、Replace with space、Delete directly）
   - 旧字符串标记为 `type="obsolete"`

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 2 条预存缺失）、qm 编译 1028 条 ✅、启动导入测试 5/5 ✅

**涉及文件：** `utils/text_normalize.py`、`ui/normalize_breaks_dialog.py`、`ui/context_menu_config.py`、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`utils/config.py`、`ui/canvas.py`、`ui/scenetext_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

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

## 2026-07-18

### 移除 MCP 服务器子系统 + 清理旧文档

**需求：** MCP 功能基本未使用，用户决定彻底移除。同时清理一批已删除的旧文档，同步 README.md 的文档索引。

**改动：**

1. **移除 MCP 服务器代码**（`mcp_server/` 目录，5 文件 ~405 行）：
   - `__init__.py`、`__main__.py`、`main.py`、`project_manager.py`、`tools.py`
   - `docs/MCP集成指南.md`、`docs/MCP用户指南.md`
   - `.mcp.json`（Tavily 搜索配置，与项目无关）

2. **清理 MCP 引用**（8 文件）：
   - `ui/configpanel.py` — 删除 `MCPInfoDialog` 类
   - `ui/mainwindow.py` — 删除 import、signal 连接、`show_mcp_info_dialog` 方法
   - `ui/mainwindowbars.py` — 删除 help 菜单中的 MCP 条目
   - `utils/env_diagnostic.py` — 删除 `check_mcp()` 函数及调用
   - `README.md` / `README_EN.md` — 删除 MCP FAQ 和对比表条目
   - `docs/项目概述.md` — 删除整个「MCP 服务器子系统」章节
   - `docs/经验教训.md` — 更新 AI 面板历史描述
   - `ui/dependency_dialog.py`、`scripts/build_portable.py` — 注释更新
   - `translate/zh_CN.ts` — 删除 MCPInfoDialog（9 条）和 TitleBar（1 条）翻译，重编译 qm

3. **同步文档索引**（`docs/README.md`）：
   - 移除已不存在文件的条目：`配置参考.md`、`模块开发指南.md`、`上下文翻译.md`、`字体局部覆盖.md`、`环境兼容方案-双路径启动设计.md`
   - 补列现有文件：`打包控件功能使用说明.md`、`配置导入导出.md`、`上游参考.md`

**涉及文件：**
- 删除：`mcp_server/`（5 文件）、`docs/MCP集成指南.md`、`docs/MCP用户指南.md`、`.mcp.json`
	- 修改：`README.md`、`README_EN.md`、`docs/README.md`、`docs/项目概述.md`、`docs/经验教训.md`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`utils/env_diagnostic.py`、`ui/dependency_dialog.py`、`scripts/build_portable.py`、`scripts/i18n_check.py`、`manifest.json`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### Default Font Format 精简 + 死代码清理

**需求：**
- 字体名称检测模块此前已被移除，Font Color / Stroke Color / Effect 三个选项的「decide by program」分支实际什么都不做（只是保留默认值），没有展示意义，占用用户视线
- `ui/mainwindow_mixin.py` 完整复制了 `on_pagtrans_finished` 逻辑但从未被 import，100% 死代码

**改动：**

1. **删除死代码**：
   - `ui/mainwindow_mixin.py` — 整个文件删除（`MainWindowMixin.on_pagtrans_finished` 从未被调用）

2. **Default Font Format 从 8 项精简为 5 项**：
   - 移除 Font Color、Stroke Color、Effect 三个下拉框（`let_fntcolor_flag` / `let_fnt_scolor_flag` / `let_fnteffect_flag` 字段一并删除）
   - 这三个属性改为无条件应用全局格式值（和「use global setting」行为一致）
   - 网格 2×4 → 2×3 布局：Font Size / Stroke Size / Alignment → Writing-mode / Font Family
   - 剩余 5 项 combo box 均添加 tooltip，悬停时解释「decide by program」实际在决定什么

3. **清理配置与配置示例**（`config/config.json.example`）

4. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 移除 3 条旧翻译（Font Color / Stroke Color / Effect）
   - 新增 5 条 tooltip 翻译（含中文释义），qm 编译 1021 条

**验证：** 语法检查 ✅、qm 编译 ✅、i18n 检查 ✅（仅剩 2 条预存缺失）、启动导入测试 5/5 ✅

**涉及文件：**
- 删除：`ui/mainwindow_mixin.py`
- 修改：`ui/configpanel.py`、`ui/mainwindow.py`、`utils/config.py`、`config/config.json.example`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 自动匹配源图格式 + 质量设置的交互说明与逻辑完善

**需求：** 当勾选「自动匹配源图格式」且源图为有损格式（JPG/WEBP）时，实际使用什么质量值？确认是用户当前的「质量」设置后，在相关选项的备注框中写明。

**改动：**

1. **行为修正**（`ui/configpanel.py`）：
   - `on_autoformat_changed`：自动匹配启用时，强制启用质量控件（源图可能是 JPG/WEBP，质量设置生效）
   - `_loadConfig`：config 加载时同样处理——自动匹配启用则质量启用，否则才按格式下拉框判断
   - 修复了此前自动匹配开启后质量控件跟随格式下拉框（可能显示为禁用的"—"）的误导

2. **备注更新**（`ui/configpanel.py`）：
   - 「自动匹配源图格式」tooltip 增加：*若源图为有损格式（JPG、WEBP），则使用上方质量设置*
   - 「质量」tooltip 增加：*当自动匹配源图格式匹配到有损源图时同样有效*

3. **翻译同步**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 2 条预存缺失）、qm 编译 1021 条 ✅

**涉及文件：** `ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 左侧栏面板/搜索控件统一替换为打包控件 + 圆角样式

**需求：** 全局搜索、图片列表、Ctrl+F 单图搜索等位置的控件尚未应用新的 ConfigComboBox 等打包控件，左侧展开面板的边框缺少圆角。

**改动：**

1. **ConfigComboBox 替换**：
   - `ui/global_search_widget.py` — `GlobalSearchWidget.range_combobox`：`QComboBox` → `ConfigComboBox`
   - `ui/page_search_widget.py` — `PageSearchWidget.range_combobox`：`QComboBox` → `ConfigComboBox`

2. **SearchEditor 视觉统一**（`config/stylesheet.css`）：
   - 搜索输入框增加与 `ConfigLineEdit` 一致的半透明背景、圆角边框、focus 高亮
   - 此前仅有 `height: 32px`，现在背景 `rgba(128,128,128,0.13)`、边框 `1px solid rgba(128,128,128,0.25)`、圆角 4px

3. **展开面板圆角**（`config/stylesheet.css`）：
   - `GlobalSearchWidget` 增加 `border-radius: 6px`
   - `PageListView` 增加 `#PageListView` 选择器 + `border-radius: 6px`
   - `ui/mainwindow.py` — `PageListView.__init__` 增加 `setObjectName("PageListView")`

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 2 条预存缺失）

**涉及文件：** `ui/global_search_widget.py`、`ui/page_search_widget.py`、`ui/mainwindow.py`、`config/stylesheet.css`

---

### 左侧面板与画布区间距 + 全局搜索防抖

**需求：**
1. 左侧展开面板与右侧画布区零间距，需添加 5px 间距
2. 全局搜索需手动点击按钮或按 Enter 才能搜索，改为输入停止 0.5s 后自动搜索

**改动：**

1. **5px 间距**（`ui/mainwindow.py`）：
   - `mainHLayout` 中在 `global_search_widget` 和 `centralStackWidget` 之间插入 `addSpacing(5)`

2. **全局搜索防抖**（`ui/global_search_widget.py`）：
   - `SearchEditor` 的 `commit_latency` 从 `-1`（关闭）改为 `500`（500ms）
   - 连接 `commit` 信号到 `commit_search`，实现输入停止 0.5s 后自动搜索
   - Enter 键和切换选项等仍保持原有即时搜索行为

**验证：** 语法检查 ✅

**涉及文件：** `ui/mainwindow.py`、`ui/global_search_widget.py`
### 文本框编号徽章 + QTextEdit 圆角修复

**需求：**
1. 文本框输入区内部 QTextEdit（SourceTextEdit/TransTextEdit）直角 → 圆角
2. 左侧独立编号标签占用空间大且视觉冗余，改为紧凑徽章

**改动：**

1. **QTextEdit 圆角**（`ui/textedit_area.py`、`config/stylesheet.css`）：
   - `SourceTextEdit.__init__` 移除内联 `setStyleSheet("QScrollBar:horizontal {height: 5px;}")`（干扰全局样式），改到 CSS 统一管理
   - `TransTextEdit` CSS 补 `border-style: none`（此前缺少此项，默认矩形 frame 外露）
   - `SourceTextEdit QScrollBar:horizontal { height: 5px; }` 移入全局 CSS

2. **左侧编号 → 叠加徽章**（`ui/textedit_area.py`）：
   - 移除 `RowIndexLabel`（独立列）及 `hlayout.addWidget(self.idx_label)`
   - 改为 `QLabel` 叠加在 `SourceTextEdit.viewport()` 左上角（`move(0, 0)`），避开 QAbstractScrollArea 裁剪
   - 文字居中 + `setContentsMargins(4, 0, 4, 0)` 匹配画布 `_draw_seq_badge` 4px padding
   - 通过 `SourceTextEdit.hover_enter/hover_leave` 信号切换 `[hovered="true"]` 属性

3. **徽章样式**（`config/stylesheet.css`）：
   - 匹配画布 `_draw_seq_badge`：半透明黑底 `rgba(0,0,0,170)`、白字 `10px bold`、无边框、`border-radius: 3px`
   - Hover：背景与文字一同淡出（`rgba(0,0,0,60)` + `rgba(255,255,255,60)`）

4. **踩坑修复**：
   - `viewport().setStyleSheet("border-radius: 5px;")` 导致原文/译文框背景色被覆盖 → 删除此行（`TransTextEdit` 已有 `border-radius` + `border-style: none` 效果已足）
   - CSS `padding` 不被 `adjustSize()` 计入，徽章尺寸过小 → 改用 `setContentsMargins`
   - 多次 `\t` 混入空格导致缩进混乱 → 规范化缩进

**已知问题（待后续处理）：**
- 徽章在 viewport 内会随文本滚动，少量内容时无影响
- 徽章文字在某些高 DPI 下可能偏小

**验证：** 语法检查 ✅

**涉及文件：** `ui/textedit_area.py`、`config/stylesheet.css`

---

### 文本框编号徽章 → 右上角 + QTextEdit 圆角修复 + 布局边距/滚动条精简

**需求：**
1. 徽章编号从左上改到右上（占比更小），字号稍大
2. 原文/译文输入框圆角被意外破坏，需修复
3. 布局边距过大且左右不等，需缩窄调平
4. 右侧滚动条遮挡视线，需隐藏（保留滚轮滑动）

**改动：**

1. **QTextEdit 圆角修复**（`ui/textedit_area.py`、`config/stylesheet.css`）：
   - `SourceTextEdit.__init__`：`setFrameStyle(QFrame.NoFrame)` + `viewport().setAutoFillBackground(False)` — 框架归零，viewport 透明，CSS `border-radius: 5px` 透出
   - `TransTextEdit`（继承 SourceTextEdit）自动生效
   - CSS `QLabel#TextBlockIndexBadge`：`font-size: 10px → 12px`

2. **徽章右上角**（`ui/textedit_area.py`）：
   - 保持 viewport 子控件（避免 QAbstractScrollArea 裁剪）
   - `move(0,0)` → `_repos_badge_tr()`：`move(vp.width() - badge.width(), 0)` 定位到右上
   - 初始定位经 `QTimer.singleShot(0, ...)` 在布局完成后执行
   - `updateIndex` 时重定位
   - hover 行为不变（仅响应 SourceTextEdit 信号）

3. **布局边距精简+调平**（`ui/textedit_area.py`）：
   - `hlayout.setSpacing(7) → 0`：移除左侧 accent_bar 与文本框之间的间距
   - `vlayout.setContentsMargins(2,2,**3**,2)`：右侧设为 3px，匹配左侧 accent_bar(3px) 宽度，实现左右对称
   - `scrollContent` 的 vlayout 右边距 `3 → 0`（右侧间距完全由 TransPairWidget 的 vlayout 控制）
   - `self.document().setDocumentMargin(0)`：移除 QTextEdit 内部文字起始间距
   - 原文/译文框间距 `7 → 2`

4. **滚动条移除**（`ui/textedit_area.py`）：
   - 删除 `ScrollBar(Qt.Orientation.Vertical, self)` 控件创建
   - `ScrollBarAlwaysOff` 策略保持，滚轮滑动正常
   - 清理未使用的 `ScrollBar` import

5. **底部分割线移除**（`ui/textedit_area.py`）：
   - 删除 `vlayout.addWidget(SeparatorWidget(self))` 及对应 import

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/textedit_area.py`、`config/stylesheet.css`

---

### 输入/下拉控件样式主题化：从固定 rgba 改为 @inputBackgroundColor

**需求：** `ConfigLineEdit`、`ConfigTextEdit`、`ConfigComboBox` 等输入框背景使用硬编码 `rgba(128,128,128,0.13)`，在深色主题下比容器背景亮一截，观感突兀。要求改为以背景色为基准变暗固定值，使控件在任何主题下都比容器更深。

**改动：**

1. **主题色**（`config/themes.json`）：
   - eva-light：`@inputBackgroundColor` 从 `"whitesmoke"`（比 `@widgetBackgroundColor` #ebeef5 还亮）改为 `"#d8dbe2"`（略深）
   - eva-dark：从 `"#191d24"`（过深）调整为 `"#22262e"`（适度深于 #282c34）

2. **全局 CSS**（`config/stylesheet.css`）：
   - 新增 `ConfigLineEdit`、`ConfigTextEdit`、`ConfigComboBox`、`ParamComboBox` 四条选择器（含 `ConfigContent` 前缀覆盖），使用 `@inputBackgroundColor` / `@borderColor` / `@accentPrimary` / `@disabledForegroundColor`
   - 同步更新 `SizeComboBox`、`SearchEditor`、`SmallComboBox`：`rgba` 固定值 → 相同主题变量

3. **移除内联 setStyleSheet**：
   - `ui/custom_widget/text_input.py`：`ConfigLineEdit`、`ConfigTextEdit` 移除内联 stylesheet（改为纯 CSS）
   - `ui/custom_widget/combobox.py`：`ConfigComboBox`、`ParamComboBox` 移除 `setStyleSheet(_COMBO_STYLE)` 调用；彻底移除 `_COMBO_STYLE` 常量
   - `ui/custom_widget/spinbox.py`：`NoArrowsSpinBox`、`NoArrowsDoubleSpinBox` 移除 `_SPIN_STYLE` 模板和内联 stylesheet
   - `ui/text_panel.py`：`FontFamilyComboBox`(#FontFamilyBox)、`QComboBox`(#FontStyleBox) 移除 `_COMBO_STYLE` 内联样式
   - `ui/fontstyle_manager.py`：`StyleDetail` 内的 `_family_combo`、`_align_combo` 移除 `_COMBO_STYLE` 内联样式

4. **全局 CSS 补充**：
   - 新增 `NoArrowsSpinBox` / `NoArrowsDoubleSpinBox` 选择器（含 `::up-button` / `::down-button` 隐藏）
   - 新增 `QComboBox#FontFamilyBox`、`QComboBox#FontStyleBox` 选择器（替换原 `QFontComboBox#FontFamilyBox` 旧规则）
   - 新增 `StyleDetail QComboBox` 选择器（覆盖字体管理面板的下拉框）

5. **文档更新**（`docs/打包控件功能使用说明.md`）：
   - §4「样式内容」从固定 rgba 描述改为主题变量说明，移除 `_SPIN_STYLE` 常量描述
   - §10、§11 的「样式内容」从固定 rgba 描述改为主题变量说明
   - §10「注意事项」更新为描述 CSS 覆盖机制（非内联优先级）

**验证：** 语法检查 ✅

**涉及文件：** `config/themes.json`、`config/stylesheet.css`、`ui/custom_widget/text_input.py`、`ui/custom_widget/combobox.py`、`ui/custom_widget/spinbox.py`、`ui/text_panel.py`、`ui/fontstyle_manager.py`、`docs/打包控件功能使用说明.md`

---

## 2026-07-19

### Fold 按钮布局改造：左侧拖拽区 + 双模式切换

**需求：** 原左侧编号栏压缩后拖拽交互困难、选中蓝条不可见。将 fold 按钮升级为完整布局模式切换。

**改动：**

1. **布局重构**（`ui/textedit_area.py`）：
   - 新增 `drag_area` QFrame（22px），置于 accent_bar 与文本内容之间
   - 双 badge：`badge_vp`（viewport 右上角，fold=OFF 使用）/ `badge_drag`（drag_area 内居中，fold=ON 使用）
   - `setFold(fold)`：fold=ON → accent_bar 3px + drag_area 显示 + badge 在左侧；fold=OFF → accent_bar 3px + drag_area 隐藏 + badge 回 viewport
   - 移除原 fold 对 QTextEdit（NoWrap/min_height）的影响

2. **滚动条**（`ui/custom_widget/scrollbar.py`、`ui/textedit_area.py`）：
   - `scrollbar.py` 替换为上游版本（支持 `hover_style` / `fadeout` 参数）
   - `TextEditListScrollArea` 加回 `ScrollBar(Qt.Vertical, self, fadeout=True)` — 默认淡出，hover 展开

3. **默认值**（`utils/config.py`）：
   - `fold_textarea: False → True`

4. **按钮改名**（`ui/scenetext_manager.py`）：
   - `CheckableLabel("Unfold", "Fold")` → `CheckableLabel("Edit", "Review")`

5. **样式**（`config/stylesheet.css`）：
   - 新增 `QLabel#TextBlockIndexBadge[folded="true"]` 拖拽区徽章样式（`font-size: 13px`，透明底、主题色字）
   - 新增 `TransPairWidget #dragArea` 透明区样式

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - "Unfold" → "Edit"（编辑）/ "Fold" → "Review"（审阅）

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅、qm 编译 1021 条 ✅

**涉及文件：** `ui/textedit_area.py`、`ui/custom_widget/scrollbar.py`、`ui/scenetext_manager.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 文本框模式蓝色边框修复 + Run 对话框始终显示 + 默认编辑模式

**需求/问题：**

1. **Run 对话框**：`run` 功能只在项目页数 > 1 时弹出设置窗口，单页项目直接执行上次流程。改为无条件弹出。
2. **默认模式**：每次启动右侧文本框区模式默认为「审阅」（Review），需改为「编辑」（Edit）。
3. **蓝色边框不显示**：按 W 进入文本框模式时，已有文本框的蓝色边框「几乎不可见」。手动调整任一文本框尺寸后才恢复正常。

**根因与修复：**

1. **Run 对话框**（`ui/mainwindow.py:2750`）— 移除 `if num_pages > 1:` 条件判断，Run 对话框始终弹出。

2. **默认编辑模式**（`ui/mainwindow.py:603,606`）— `foldTextBtn.setChecked(True)` 和 `fold_textarea(True)` 硬编码启动，忽略配置值。

3. **蓝色边框**（`ui/textitem.py`）：

   - **根因：** 文本渲染模式设为「清晰（矢量渲染）」时，`_use_full_pixmap = False` + `CacheMode = NoCache`，走 `paint()` 慢路径。慢路径中 `_draw_accessories()` 以 `DestinationOver` 合成模式绘制，将蓝色边框画在 QTextDocument 内容**后面**，被文字遮盖不可见。流畅（位图渲染）模式走快路径，边框通过 `_draw_border_rect()` 以 `SourceOver` 画在文字**上面**，一切正常。
   - **修复：** 慢路径非编辑状态下，背景（stroke/shadow）仍用 `DestinationOver` 画在文字后，边框改用 `_draw_border_rect()` 以 `SourceOver` 画在文字上方。新增 `_draw_background_only()` 方法剥离边框绘制。
   - **辅助**（`ui/scenetext_manager.py`）：`showTextblkItemRect()` 中加上 `_invalidate_cache()` + `update()` 确保 Qt `DeviceCoordinateCache` 失效。

**验证：** 语法检查 ✅（`ui/textitem.py` + `ui/scenetext_manager.py`）

**涉及文件：** `ui/mainwindow.py`、`ui/textitem.py`、`ui/scenetext_manager.py`

---

## 2026-07-19

### 右侧文本框区 hover 跳变修复 + 编辑光标偏移修复 + documentMargin 恢复

**需求/问题：**

1. **hover 跳变**：鼠标悬停原文/译文框时，`QGraphicsDropShadowEffect`（blurRadius=12）改变渲染管道，导致内部文字轻微偏移跳动。尤其在当前紧凑布局下影响明显。
2. **编辑光标偏移**：进入编辑模式时，由于 `documentMargin=0` 文字紧贴边框边缘，光标比字形高，Qt 会重新排版使光标完整显示，导致文本行向上/下偏移。

**修复：**

1. **hover 改用 CSS**:（`ui/textedit_area.py`）— 移除 `QGraphicsDropShadowEffect`，`setHoverEffect` 改为空方法。视觉反馈由 CSS `:hover` 伪类接管（边框变 accent 色）。
2. **新增淡边框**（`config/stylesheet.css`）— `SourceTextEdit` / `TransTextEdit` 添加 `1px solid rgba(128,128,128,0.20)` 永久淡边框稳定内容区域；`:hover` / `:focus` 边框变 `@accentPrimary`。
3. **恢复 documentMargin**（`ui/textedit_area.py:97`）— 从 `0` 改为 `2`，保留 2px 上下气口使光标完整显示，消除编辑点击时的文本偏移。

**验证：** 语法检查 ✅

**涉及文件：** `ui/textedit_area.py`、`config/stylesheet.css`

---

### PPOCRv6 ONNX 竖排文本框方向修复

**问题：** ppocrv6_onnx 文本检测模型识别出的竖排文本框未正确应用文本方向参数（`src_is_vertical` / `vertical` 始终为 False），导致 OCR 虽然能正确识别竖排文字，但框的方向参数仍为横排。

**根因：** `detector_paddlev6.py` 的 `_detect()` 将检测框直接转为 `TextBlock`，跳过了 `sort_pnts()` 方向判断和 `examine_textblk()` 计算，`src_is_vertical` 在 `__post_init__` 中默认走 `self.vertical`（`False`）。

**修复：** 对每个检测框调用 `sort_pnts()` 判断竖排/横排，设置 `src_is_vertical` / `vertical` 标志，调用 `examine_textblk()` 正确计算角度和字号。

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅

**涉及文件：** `modules/textdetector/detector_paddlev6.py`

---

### Auto Layout 功能完整移除

**理由：** 该功能（根据气泡 mask 自动分割译文为多行）的 mask 提取基于硬编码 Canny 阈值 + flood fill，对非常规场景完全不可靠。遵循减法原则：不维护半桶水功能。

**清理范围：**

1. **配置层**（`utils/config.py`）— 删除 `let_autolayout_flag`
2. **UI**（`ui/configpanel.py`）— 删除复选框、处理器、setChecked
3. **触发逻辑**（`ui/mainwindow.py`）— 删除 `auto_textlayout_flag` 设置/重置
4. **核心逻辑**（`ui/scenetext_manager.py`）— 删除 imports、属性、addTextBlock 拦截分支、`onAutoLayoutTextblks`、`layout_textblk`（217行）、`get_text_size` / `get_words_length_list` 死函数
5. **撤销命令**（`ui/textedit_commands.py`）— 删除 `AutoLayoutCommand`
6. **信号**（`ui/canvas.py`）— 删除 `layout_textblks = Signal()`
7. **布局引擎**（`utils/text_layout.py`）— 整文件删除（623行）
8. **分词**（`utils/text_processing.py`）— 删除 `seg_text` 及全部下游依赖，保留 `full_len`/`half_len`/`is_cjk`
9. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）— 删除 2 条翻译，重编译

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅、i18n 检查 ✅、qm 编译 1019 条 ✅

**涉及文件：**
- 删除：`utils/text_layout.py`
- 修改：`utils/config.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/scenetext_manager.py`、`ui/textedit_commands.py`、`ui/canvas.py`、`utils/text_processing.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-19

### 上游 Context 系统适配 + Profile Manager 清理 + 提示词策略替换

**需求：** 借鉴上游的 LLM translation context 系统（glossary / history window / token budget / context recovery），替换现有的三段式提示词；清理 Profile Manager 中废弃的「翻译设置」；新增「返回 JSON Schema」勾选框和「额外翻译指令」编辑框。

**改动：**

1. **新增 Context 基础设施**（`modules/context/`）：
   - `glossary.py` — 术语表加载（JSON/TXT/TSV）、LRU 缓存、大小写不敏感匹配、稳定渲染
   - `history.py` — `HistoryPage`/`RenderedHistoryPage`/`HistoryWindow` 不可变快照；`eligible_history_for_request()` 智能历史选择（60% low-water mark）；`recover_context_length()` context overflow 恢复；`ContextDiagnostic` 诊断日志
   - `token_usage.py` — tiktoken 精确计数 + fallback 估算；`format_token_usage()` 兼容各厂商 usage 字段
   - `errors.py` — `ContextLengthError` + `is_context_length_error()` 三阶段识别（status code / error code / message regex）

2. **配置层**（`utils/config.py`）：
   - 新增 `TranslateContext`、`LLMTranslateContext`、`LLMGlossaryMode` 枚举
   - `ModuleConfig` 新增 5 个字段：`translate_context`、`llm_translate_context`、`llm_prior_context_token_budget`、`llm_glossary_path`、`llm_glossary_mode`
   - `__post_init__` 验证逻辑

3. **Profile Manager 重构**（`utils/profile_manager.py`）：
   - **删除**「Translation Settings (optional)」整个 section（Response Format ComboBox、Prompt Template、Few-Shot Examples、Frequency Penalty、Presence Penalty）
   - **删除** `DEFAULT_PROMPT_TEMPLATE`、`DEFAULT_CHAT_SAMPLES` 常量
   - **新增**「返回 JSON Schema」`ConfigCheckBox`（字段 `return_json_schema`，默认 False）
   - **新增**「Extra Translation Instructions (optional)」可折叠 `ConfigTextEdit`（字段 `system_prompt`，留空则纯用硬编码 contract）
   - 同步更新 `PROFILE_FIELDS`、`SAMPLE_PROFILES`、`ProfileManagerDialog` 和 `ProfileManagerWidget` 的 UI/保存/填充/清空方法

4. **LLM 翻译器重写**（`modules/translators/trans_llm_api.py`）：
   - **移除**三段式：`DEFAULT_SYSTEM_PROMPT`、`_assemble_prompts()`、`_parse_chat_samples()`、`build_copy_prompt()`
   - **新增**上游 contract 策略：`_system_prompt()`（JSON 输出合约 + history_rule + 可选额外指令）、`_render_user_prompt()`（"Translate from X to Y:\nINPUT:\n{json}" + 可选 GLOSSARY）、`_assemble_request()`（cache-friendly 前缀顺序：system → glossary → history → current user）
   - **集成 Context**：`_snapshot_request_context()` 冻结 glossary + eligible history pages；`_history_window` 实例级缓存；`translate()`/`_translate()` 支持 `project`/`page_key`/`commit_history_window`；ContextLengthError recovery 自动重试
   - 保留原有 profile 访问、API key 管理、rate limiting、响应解析

5. **管线集成**（`ui/module_manager.py`、`ui/mainwindow.py`）：
   - `translate_textblk_lst()` 调用传入 `project` 和 `page_key`
   - `mainwindow.py` 中 `context_batch` 引用从 `prompt_template` 改为 `system_prompt`

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 移除旧翻译设置相关条目（Response Format / Prompt Template / Few-Shot Examples / Frequency Penalty / Presence Penalty）
   - 新增 6 条翻译（Return JSON Schema / Extra Translation Instructions / Instructions 等）
   - 重编译为 1013 条

**验证：** 语法检查 ✅、i18n 检查 ✅、启动导入测试 5/5 ✅、Context 模块独立导入验证 ✅

**涉及文件：**
- 新增：`modules/context/__init__.py`、`modules/context/glossary.py`、`modules/context/history.py`、`modules/context/token_usage.py`、`modules/context/errors.py`
- 修改：`modules/translators/trans_llm_api.py`、`utils/config.py`、`utils/profile_manager.py`、`ui/module_manager.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### Run 对话框简化 + 上游 LLM Context 设置集成 + 尺寸锁定

**需求：**
1. Run 对话框样式过多（2×2 网格 / 可折叠 Settings），仅上下文翻译相关设置有用，其他接口设置无用
2. 缺少上游的 LLM Context（page/+history）设置项
3. 窗口高度应在内容折叠时自动收缩，不可手动拉伸
4. 下拉框使用自定义 `ConfigComboBox` 样式，边框颜色需与背景有区分
5. Glossary 上传后无法清除，退出窗口后应自动清理

**改动：**

1. **Run 对话框 UI 简化**（`ui/mainwindow.py`）：
   - 去掉 Activate Modules 2×2 网格 + Settings 可折叠章节
   - 还原为简单逐行勾选框列表（Enable Text Detection / Enable OCR / Enable Translation / Enable Inpainting）
   - 去掉 Text Detection（Keep Existing Lines）和 Inpainting（Skip simple cases）设置项
   - Translation 行 inline 放置 Context Translation (beta) 复选框

2. **添加上游 LLM Context 设置**（`ui/mainwindow.py`）：
   - 新增 LLM Context 下拉框（page / +history），绑定 `llm_translate_context`
   - 新增 Token Budget `NoArrowsSpinBox`（512-16384），绑定 `llm_prior_context_token_budget`，仅 +history 时显示
   - Glossary 设置（文件路径 + Matching/All 模式）保留并优化
   - 仅 CT beta 勾选时显示 Context 区域，删除冗余的普通 Context（textblock/page）

3. **复选框联动**（`ui/mainwindow.py`）：
   - 勾选 CT beta → 自动勾选 Enable Translation
   - 取消 Enable Translation → 自动取消 CT beta

4. **尺寸锁定**（`ui/mainwindow.py`）：
   - `_resize_to_fit()` 辅助函数：先解锁 → `adjustSize()` → `setFixedSize()` 锁定
   - 所有可见性切换（CT beta toggle、LLM Context 模式、Glossary toggle）均调用 `_resize_to_fit()`

5. **自定义控件替换**（`ui/mainwindow.py`）：
   - 3 个下拉框：`QComboBox` → `ConfigComboBox`（主题感知圆角样式）
   - Token Budget：`QSpinBox` → `NoArrowsSpinBox`
   - Browse 按钮：加 `setFixedHeight(27)` 匹配 QLineEdit 行高
   - Glossary 路径在 dialog 关闭时自动清空

6. **边框颜色调亮**（`config/stylesheet.css`）：
   - `ConfigComboBox`/`ParamComboBox` 边框：`@borderColor` → `@accentPrimary80`
   - 浅色/深色主题下均有明显蓝色边框，与输入框背景形成对比

7. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 3 条翻译（LLM Context / +history / Token Budget）
   - 编译为 1036 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/mainwindow.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 合并功能简化：移除 LTR/RTL 方向，改为按列表次序合并

**需求：** 右键合并的 LTR/RTL 方向判断对上下排列的文本框无意义，且不尊重用户手动排好的阅读顺序。改为始终按文本框 `idx`（列表顺序）合并，去掉「默认从右到左合并」开关。

**改动：**

1. **配置层**（`utils/config.py`）— 删除 `merge_rtl` 字段

2. **信号变更**（`ui/canvas.py`）— `merge_textblks = Signal(str)` → `Signal()`，不再传方向

3. **右键菜单**（`ui/context_menu_config.py`）：
   - `_build_merge()`：去掉 direction 判断，直接 emit
   - `_build_behavior()`：去掉「Merge Right-to-Left」切换和分隔线
   - 删除 `_toggle_merge_rtl()` 函数

4. **合并执行**（`ui/scenetext_manager.py`）：
   - `on_merge_textblks()`：排序改为 `b.idx`（列表顺序），不再按 `center_x` 位置排
   - 合并前增加 **UI→blk 文字同步**：用户手动在画布输入的文字存于 QTextDocument，未写回 `blk.translation`/`blk.text`。合并前遍历选中块，从 `b.toPlainText()` 和 `pw.e_source.toPlainText()` 同步到 `blk`，确保原文和译文不丢失（原有 bug，与本次方向改动无关）
   - `_build_merged_blk()`：删除无用的 `direction` 参数

5. **快捷键**（`ui/mainwindow.py`）— `shortcutMergeBlks()` 直接 emit 无参数

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）— 删除 Left-to-Right、Right-to-Left、Merge Right-to-Left 三条翻译，重编译为 1032 条

**行为变更：** 合并不再区分 LTR/RTL/上下，直接按文本框在侧栏列表中的顺序（`idx`）拼接文字/译文。用户先排好顺序再合并即可获得预期的文字顺序。

**验证：** 语法检查 ✅、i18n 检查 ✅、qm 编译 1032 条 ✅、手动合并测试 ✅（含手动创建文本框输入文字的场景）

**涉及文件：** `utils/config.py`、`ui/canvas.py`、`ui/context_menu_config.py`、`ui/scenetext_manager.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 自定义术语 AI 转换 + 文件路径替换为状态指示器 + 独立日志窗口替换为调试日志文件

**需求：**
1. 在 Run 对话框术语表 Browse 按钮旁加「Custom...」按钮，用户可用自然语言描述角色/术语，运行前由 AI 转为结构化术语表
2. 删除文件路径地址栏，改为紧凑状态指示器（○/✓）
3. 删除独立 ContextLogDialog 窗口，改为默认关闭的调试日志文件输出

**改动：**

1. **自定义术语对话框**（`ui/glossary_dialog.py` — 从 git 恢复并增强）：
   - `CustomGlossaryDialog(parent, initial_text="")` 支持回显上次输入
   - 提示文字支持自然语言描述（示例改用 Dragon Ball / One Piece 等常见作品）
   - `get_raw_text()` 返回编辑器原始内容供 AI 转换
   - 移除分隔符说明文字，按钮宽度从 90px 加宽至 110px

2. **Run 对话框 Glossary UI 改造**（`ui/mainwindow.py`）：
   - 删除 `glossary_path_edit`（QLineEdit），改用 `glossary_status_label` 显示 ○/✓
   - 新增 `glossary_custom_btn`（Custom...），复选框与 `_custom_glossary_text` 闭包变量配合
   - 对话框关闭时清理自定义文本和 glossary 路径

3. **AI 术语表生成**（`modules/translators/context_batch.py`）：
   - `ContextBatchTranslator` 新增 `custom_glossary_text` 参数
   - `set_project()` 中调用 `_generate_custom_glossary()` 将用户自然语言转为 `GlossaryEntry` 列表
   - `_raw_llm_call()` / `_parse_glossary_response()` — JSON 响应解析（含 markdown fence 处理）
   - 优先级：自定义术语 > 文件术语 > 自动学习术语

4. **调试日志替代独立窗口**：
   - `ui/context_log_dialog.py` — 删除
   - `utils/debug_log.py` — 新建 `DebugLogger`，输出到 `debug/context_translation_<timestamp>.log`
   - `utils/config.py` — `ProgramConfig` 新增 `context_translation_debug_log: bool = False`（默认关闭，config.json 已 gitignore）
   - `ui/mainwindow.py` — 删除 `ContextLogDialog` 创建/显示/关闭逻辑，`_ctx_status` 在开关开启时写入调试日志文件

5. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 CustomGlossaryDialog 提示文字翻译（含 Dragon Ball / Goku 等示例）
   - 删除 ContextLogDialog 上下文（2 条 message）
   - 编译为 1035 条

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 orphan，均为预存间接调用）、qm 编译 ✅、启动导入测试 5/5 ✅

**使用方式：** `config/config.json` 中设 `"context_translation_debug_log": true` 启用调试日志，输出至 `debug/context_translation_*.log`

**涉及文件：**
- 新增：`utils/debug_log.py`
- 删除：`ui/context_log_dialog.py`
- 修改：`ui/glossary_dialog.py`、`ui/mainwindow.py`、`modules/translators/context_batch.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-20

### 术语表提取工具 — 频率启发 + LLM 语义提取

**需求：** 从已有翻译项目中自动提取术语表，支持两种模式：快速频率统计和 LLM 语义分析。参考 AiNiee 的术语表提取方式设计。

**研究结论：** AiNiee 使用两阶段 LLM 管线（提取 → 去重合并）识别角色名/专有名词/不翻译项，结合结构化 prompt + JSON 输出。本项目已有 glossary 系统（`modules/context/glossary.py`）和 `ContextBatchTranslator._generate_custom_glossary()` 作为 LLM glossary 参考实现，但缺少从已有翻译项目自动提取的功能。

**改动：**

1. **核心提取逻辑**（`modules/glossary_extractor.py` — 新建）：
   - `extract_by_frequency(proj, min_count=2)` — 遍历项目统计词频，从高频重复且有对应译文的 source 中提取术语
   - `extract_by_llm(proj, api_config, status_cb)` — 收集项目原文/译文对，发送给 LLM 识别重要命名实体和术语
   - `save_glossary_json(entries, path)` — 保存为标准 JSON glossary 格式
   - LLM prompt 聚焦角色/地点/组织/特殊术语/非直译术语，输出 `[{"src", "dst", "info"}]` 格式

2. **提取对话框 UI**（`ui/glossary_extractor_dialog.py` — 新建）：
   - `GlossaryExtractorDialog(QDialog)` — LLM 配置选择 + 提取模式切换（频率/LLM）
   - 后台线程 `_ExtractWorker` 避免 UI 卡顿
   - 可编辑的预览表格（QTableWidget），支持提取结果编辑
   - 保存后可选立即设置为活动术语表
   - 覆盖缺少数据/无配置等边界情况

3. **集成：Tools 菜单**（`ui/mainwindowbars.py` + `ui/mainwindow.py`）：
   - 在顶部 TitleBar 的「Tools」菜单中添加「Extract Glossary…」菜单项
   - 点击打开 `GlossaryExtractorDialog` 作为独立窗口（不依赖 Run 对话框）
   - 自动读取当前翻译器激活的 profile 作为默认 LLM 配置
   - 提取保存后自动设置 `pcfg.module.llm_glossary_path`
   - 移除之前 Run 对话框中的「Extract...」按钮，解耦运行管线与提取流程

4. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 `GlossaryExtractorDialog` 上下文（27 条翻译）
   - 新增 `_ExtractWorker` 上下文（4 条翻译）
   - `TitleBar` 新增「Extract Glossary…」翻译
   - `MainWindow` 移除已删除的「Extract...」翻译
   - 重编译为 1064 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1064 条 ✅、启动导入测试 5/5 ✅、glossary_extractor 模块独立导入验证 ✅、save/load 双向测试 ✅

**涉及文件：**
- 新增：`modules/glossary_extractor.py`、`ui/glossary_extractor_dialog.py`
- 修改：`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`


### 字体样式管理器修复 + 一键应用预设

**问题/需求：**

1. **"应用修改"不生效** — `_apply_all()` 中 `BatchFontformatCommand.redo()` 的 `_first_redo` 跳过机制导致实际块的 fontformat 从未被修改，仅更新了内存中的代表副本。
2. **操作顺序错误** — 修改在创建命令之前执行，构造函数捕获的是新状态而非旧状态，undo 无法正确还原。
3. **离线页面不更新** — 修改离线页块的数据后缺少画布重建机制。
4. **一键应用预设** — 希望从已保存的字体样式预设中选取应用到当前风格的所有块。

**改动：**

1. **修复应用不生效**（`ui/fontstyle_manager.py`）：
   - 拆分 `_apply_all()` 操作顺序为：① 创建命令（捕获旧状态）→ ② push 到撤销栈 → ③ 直接应用到所有块 → ④ `updateSceneTextitems()` 全局刷新
   - 新增 `_apply_changes_to_blocks()` 统一处理当前页（`set_fontformat`）和离线页（`blk.fontformat = new_ffmt`）的修改

2. **一键应用预设**（`ui/fontstyle_manager.py`）：
   - Batch Edit 区新增 "Preset" 行：`QComboBox`（列出 `utils.config.text_styles`）+ "Apply Preset" 按钮
   - 新增 `_load_presets()` 加载预设列表
   - 新增 `_apply_preset()` 复用完整 apply 流程：创建命令 → push → 直接应用 → 全局刷新 → 同步控件值
   - `show_entry()` 中调用 `_load_presets()` 保持下拉与最新预设同步
   - 新增 `_make_change_dict_from_ffmt()` 以预设 `FontFormat` 直接构建 change list

3. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - `StyleDetail` 上下文新增 6 条翻译（Preset / Apply Preset / (Select a preset) / (unnamed) / Apply preset style）
   - 重编译为 1070 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1070 条 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/fontstyle_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-21

### 上游 PR #1238 调研：独立文字缩放与倾斜变换

**需求：** 调研上游 https://github.com/dmMaze/BallonsTranslator/pull/1238 的内容，评估可学习点，形成文档后暂搁置。

**调研结论：**

该 PR 为 Advanced Text Format 添加了四个独立变换维度（Horizontal Scale 10%–400%、Vertical Scale 10%–400%、Box Slant -85°–85°、Glyph Slant -45°–45°），15 文件 ~5800 行净改动。

**核心亮点：**
- `text_glyph_renderer.py`（新）— 只读字形级倾斜渲染引擎，路径优先 + 栅格回退
- `text_transform.py`（新）— 旋转补偿矩阵解决 Qt 旋转先于 setTransform 的问题
- 多边形 shape control 使 Box Slant 后手柄仍然贴合
- 预览系统 + 批量更新合并防抖
- 项目格式版本化迁移

**决定：** 暂搁置，功能太大不急于实装。详细调研文档见 `docs/上游PR-1238-文字变换调研.md`。

**涉及文件：** `docs/上游PR-1238-文字变换调研.md`（新）、`docs/README.md`

---

### 术语表提取：导出崩溃 + 结果持久化 + i18n `\n` 陷阱修复

**问题/需求：**

1. **导出崩溃**：`_on_save()` 引用不存在的 `pcfg.lastdir`（`ProgramConfig` 无此属性，且该文件未 import `pcfg`），AttributeError。
2. **结果不持久**：对话框关闭后提取条目丢失，再次打开需重新提取。
3. **i18n 中文不显示**："Note" 列头和保存确认对话框虽在 `.ts` 有对应条目，运行时仍显示英文。

**根因与修复：**

1. **导出崩溃**（`ui/glossary_extractor_dialog.py:366`）— `pcfg.lastdir` → `""`（直接用文件名作默认路径）。

2. **结果持久化**（`ui/glossary_extractor_dialog.py` / `ui/mainwindow.py`）：
   - `__init__` 新增 `existing_entries` 参数，传入即有历史结果时恢复显示
   - 新增 `done()` 重写，对话框关闭前将 `self._entries` 存到 `self.parent()._glossary_extractor_entries`
   - `mainwindow.py` 打开对话框前用 `getattr(self, "_glossary_extractor_entries", ())` 取回
   - 效果：条目存活于 MainWindow 实例，随主窗口生命周期

3. **i18n `\n` 陷阱**（`translate/zh_CN.ts`）：
   - **根因**：`.ts` 是 XML，其中 `\n` 是**字面反斜杠+字母 n**，不是换行符。但 `self.tr("...\n...")` 的 `\n` 是 Python 转义得到真正的换行符（0x0A）。两边字符串不同 → Qt 的 ELF hash 不匹配 → 翻译查找失败 → 回退显示英文。
   - **修复**：将 `GlossaryExtractorDialog` 上下文中 3 组 `<source>`/`<translation>` 的 `\n` 替换为真正的换行符。
   - 同步新增 `Previously extracted {} terms.` 翻译条目。

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1065 条 ✅、Qt QTranslator 运行时查找全部返回正确中文 ✅

**涉及文件：** `ui/glossary_extractor_dialog.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 全局搜索 UI 布局调整 + 替换后界面卡死修复

**需求/问题：**

1. **UI 布局**：全局搜索替换输入框比查找输入框窄太多，右侧搜索区域下拉栏占空间过多，替换框宽度应与查找框一致。
2. **替换后界面卡死**：多次替换操作后界面持续显示"内容已更新，请按回车刷新搜索"，按回车无响应，搜索和替换功能失效。

**根因分析：**

**UI 布局问题：** `hlayout_bar1`（查找行）将 `search_editor` 放在独立的 `hlayout_bar1_0` 子布局中（可自由伸缩），而 `hlayout_bar2`（替换行）让 `replace_editor` 与 `range_label` + `range_combobox`（固定 300px）平铺在同一层，导致替换框被严重挤压。

**替换后卡死根因：**

1. **焦点问题** — `replace_editor`（替换输入框）也是 `SearchEditor`，按下 Enter 会发射 `enter_pressed` 信号，但该信号**没有被连接**。替换操作完成后，用户焦点通常落在替换框（刚输完替换文本），此时按 Enter → 信号发射但无人监听 → 用户感觉"没反应"。
2. **线程重入** — `on_replace()`（简单替换）启动后台线程后，若线程尚未结束用户再次点击"Replace All"，`self.start()` 被 Qt 忽略（线程已在运行），新设置的 `job` lambda 在旧线程结束时被 `self.job = None` 覆盖，第二次替换静默丢失。

**改动：**

1. **UI 布局**（`ui/global_search_widget.py`）：
   - `ConfigComboBox` 改为 `fix_size=False` + `setMaximumWidth(120)`，不再固定 300px
   - `hlayout_bar2` 拆分为 `hlayout_bar2_0`（`replace_editor` 伸缩区）+ `hlayout_bar2_1`（`range_label` + `range_combobox` 紧凑区），完全镜像 `hlayout_bar1` 结构

2. **替换框回车响应**（`ui/global_search_widget.py`）：
   - `replace_editor.enter_pressed` 连接到 `commit_search()`，焦点在替换框时按 Enter 也能触发重新搜索

3. **线程忙碌守卫**（`ui/global_search_widget.py`）：
   - `on_replace()` 入口增加 `if self.replace_thread.isRunning(): return`，防止线程重入导致替换丢失

**验证：** 语法检查 ✅

**涉及文件：** `ui/global_search_widget.py`