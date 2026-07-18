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