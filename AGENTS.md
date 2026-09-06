# AGENTS.md

> 本项目 AGENTS.md 已提交到仓库中（不在 `.gitignore`），多设备间通过 git 同步。

## 项目概述

漫画/图片翻译工具。五阶段管线：文字检测 → OCR → 翻译 → 图像修复 → 文字渲染。PyQt6 桌面应用，插件式模块系统。

本分支是减法导向的 fork：精简优先，不添加未被要求的功能；交互路径越短越好。

## 架构

每个管线阶段使用注册器模式（`utils/registry.py`），模块通过装饰器自注册：

```text
modules/
  textdetector/  →  detector_*.py  (@register_textdetector)
  ocr/           →  ocr_*.py       (@register_ocr)
  translators/   →  trans_*.py     (@register_translator)
  inpaint/       →  *.py           (@register_inpainter in inpaint/base.py)
  base.py        ←  BaseModule, 模块发现, 设备检测
```

`modules/base.py` 的 `init_module_registries()` 按文件命名模式扫描各目录，动态导入匹配的模块。

## 关键文件

| 路径 | 用途 |
| ------ | ------ |
| `launch.py` | 入口，命令行参数，PyTorch 设备 |
| `utils/proj_imgtrans.py` | 项目管理（页面、文字块、撤销栈） |
| `utils/textblock.py` | 核心数据单元（坐标、原文、译文、字体、遮罩） |
| `utils/font_scan.py` | 字体名称表扫描与家族名归并：字重变体/中英双名/排版家族名归并为单一规范名（对齐 PS 组织方式，canonical 优先中文名），并建立精确 PS 名索引供 PSD 导出；`shared.init_font_list` 消费其结果；`compute_simplify_map` 为「一键精简」私有规则（扩展 heavy/ultra 后缀、无分隔符后缀、face 包含归并），产物写入 `pcfg.simplified_font_map`，由 FontExcludeDialog 的一键精简按钮管理 |
| `utils/base_styles.py` | 项目级大样式 + 变体发现：块按 `(font_family, vertical)` 身份键归属大样式，override 量化 diff 派生子样式（同 override 自动聚类 + 自动命名）；`discover_style_tree` 驱动样式管理器树 |
| `modules/translators/trans_agent.py` + `modules/translators/agent/` | 翻译 agent：`AgentTranslator`（继承 `LLM_API_Translator` 复用 profile/重试/RPM）作唯一 LLM 翻译路径；原生 function calling 多轮循环 + 只读探索工具 + 唯一 `submit_translations` 提交出口；`agent/` 包内为 loop/工具面/prompts/validator 纯逻辑 |
| `utils/config.py` | 配置读写 |
| `utils/shared.py` | 路径常量 |
| `utils/structures.py` | `nested_dataclass`，`Config`/`Dict` 基类 |
| `utils/profile_manager.py` | LLM API 配置管理（翻译器/OCR 共用） |
| `utils/ai_tools.py` | 翻译 agent/术语工作台共享的只读探索工具执行器（4 只读工具 + `to_openai_tools`；写类工具已随旧 AI 助手移除） |
| `ui/mainwindow.py` | 主窗口 |
| `ui/configpanel.py` | 配置面板、快捷键编辑 |
| `ui/text_panel.py` | 文本编辑面板 |
| `ui/panel_rail.py` | 嵌字页格式区左缘窄栏：功能图标列（画布浮层面板入口，见 `ui/custom_widget/rail_dock_panel.py`） |
| `ui/io_thread.py` | 管线编排（检测→OCR→翻译→修复） |
| `ui/textitem.py` / `ui/text_engine/` | 画布文字渲染（textitem 是 fork 适配层，渲染实现在 engine；） |
| `ui/overlay_modal.py` | `OverlayModal` — 中心淡入/淡出模态（scrim 覆盖中央画布区，ConfigPanel 用它） |
| `ui/overlay_slide.py` | `OverlaySlider` — 覆盖面板滑入滑出动画（GlobalSearchWidget、PageList 用它） |
| `ui/custom_widget/` | 可复用控件库（`__init__.py` 统一导出，见下方"打包控件功能"） |
| `config/` | `config.json`(gitignore), `stylesheet.css`, `themes.json`, `custom_themes.json`, `textstyles/` |
| `scripts/` | `verify.py`, `check_docs.py`, `check_syntax.py`, `qm_compile.py`, `i18n_check.py` |

## 打包控件功能

`ui/custom_widget/` 封装了完整的可复用控件库，通过 `__init__.py` 统一导出：
`from ui.custom_widget import ConfigCheckBox, NoArrowsSpinBox, …`。

**输入类控件必须用封装类，禁止直接实例化 `QComboBox`/`QSpinBox`/`QLineEdit`/`QTextEdit`**：
它们的圆角输入样式走类名选择器，原生类静默掉样式（`QSpinBox`/`QTextEdit` 甚至完全无规则）。
下拉框/数字/单行/多行/复选框分别用 `ConfigComboBox`/`NoArrowsSpinBox`/`ConfigLineEdit`/`ConfigTextEdit`/`ConfigCheckBox`。
按钮、滚动条、菜单、单选、tab 等有全局 QSS 兜底，原生类即可。详见使用说明文档「样式生效机制：全局兜底 vs 类名选择器」一节。

**核心模式**（详见 [`docs/基础速查/打包控件功能使用说明.md`](docs/基础速查/打包控件功能使用说明.md)）：

| 模式/控件 | 一句话说明 |
|-----------|----------|
| `ConfigSubBlock` 禁用自动变灰 | `changeEvent` 自动处理禁用态 label 颜色 |
| "—" 占位符模式 | 禁用数值字段时以 "—" 替代，`blockSignals` 防误触 |
| `NoArrowsSpinBox` 族 | 无箭头、主题感知的数字/文本/下拉/滚动条控件族 |
| `ColorSwatchBtn` | 色块按钮，`setColor()`/`color()` + `colorChanged` 信号 |
| `pick_screen_color()` | 屏幕吸色管：全屏覆盖 + 8x 放大镜，左键取色、右键/Esc 取消（冻结帧采样，事件驱动不卡 UI） |
| `ConfigScrollBar` | 全局统一的 8px 圆角滚动条（含悬停动画） |
| `ClockDial` | 指针式角度/距离选择（影子方向用） |
| `ConfigSectionHeader` | 配置面板章节标题 |
| `GroupFrame` | 圆角边框分组容器 |
| `NotificationCenter`（`ui/custom_widget/notification.py`） | 统一画布通知中心：toast / 活动 spinner / 状态角标，锚点堆叠避让、key 去重刷新、`post()` 线程桥接；模块级单例 `notification`，Canvas 初始化时 attach 后由各模块调用 |
| `RailDockPanel` | 画布区浮层面板（主窗口内子控件，展开硬连接锚定窄栏左侧：右缘+顶部固定、宿主缩放/窄栏移动自动重锚、左下角手柄拉伸、尺寸下限随内容布局、Esc/× 关闭不自动关；开合记忆 `pcfg`） |
| `FloatDropPanel`（`ui/custom_widget/float_drop_panel.py`） | 按钮锚定下拉浮层（`ui/global_search_widget.py` 格式条件用）：宿主为 `window.centralWidget()`（打开时惰性解析），左缘钉在锚点所在侧栏右缘、向画布方向按内容展开，不参与布局不撑宿主最小尺寸；Esc/×/再点锚点关闭，无开合记忆、无拉伸手柄（内容自带滚动）；QSS 复用 `RailDock*` objectName |

新增控件时更新上表即可，无需展开详细用法。优先使用已有方案而非重新实现。

## 文档规范

所有文档放 `docs/`，文件名中文化，无英文版本。

- 引用文件用**仓库相对路径**（如 `ui/configpanel.py`）；引用符号用 **`路径::符号`**（如 `ui/configpanel.py::DEFAULT_SHORTCUTS`），**不写行号**（行号易漂移）。**禁止裸符号名**（如 `TextStyleDialog`）——check_docs 查表校验不到裸名，符号被删后静默漏网（2026-08-23 教训）；符号一律带文件路径。
- 改动后跑 `scripts/check_docs.py` 校验文档里的路径/符号引用是否失效（已并入 `verify.py`）。仅归档/日志类文档（`daily_log.md`、`经验教训.md`、`上游参考.md`）不在校验范围；`技术实现/` 已纳入校验，引用上游仓库路径时须带 `ballontranslator/`（或 `BallonsTranslator/`、`resources/`）前缀以触发跨库豁免。
- 深度审计（死代码/休眠登记表、删除残留引用）用 `/audit-docs` 技能（`verify.py` 第 3 步已自动跑核心检查）。

## 配置系统

- `config/config.json` 已 gitignore（含 API 密钥），其余配置/样式文件被跟踪。
- 模块声明 `params: Dict` 自动渲染为 UI 表单。以 `_` 开头的 key 为内部参数，save/load 时保留。

全局配置 `pcfg`（`utils/config.py`）是模块级单例：

- 改 `pcfg` 后须显式调用 `save_config()`。仅 `closeEvent` 和 `ConfigPanel.hideEvent` 触发自动保存。
- 新增配置字段必须声明到对应 Config 类（`utils/config.py::ProgramConfig`/`utils/config.py::ModuleConfig` 等）：代码读到未声明字段是运行时 AttributeError（glossary_dock_open、agent_translation_debug_log、source_lang/target_lang 三度翻车）；`tests/test_config_fields.py` 静态校验 pcfg 引用与声明一致。
- 启动顺序：`launch.py` 先 `load_config()` 再 `init_module_registries()`。

## Git 规则

- **除非用户要求，否则不提交。** 改动先展示审查。
- **「完事/收工/收尾」语义：** 用户以上述收尾表述结束任务时，即视为要求**整理工作区并提交**（按逻辑分批组织，消息先展示），但**不推送**——`git push` 一律由用户自己执行；仅当用户明确描述需要"提交+推送"（如「提交并推送」「推上去」）时才执行推送。
- **"提交" = `git add -A`** — 暂存工作区全部修改（含未跟踪文件）。
- **提交聚合原则：** 工作完成后，用 `git reset --soft <基准>` + `git commit` 将同批次逻辑相关的多个提交聚合为一个原子提交再推送。避免给远端推送碎片化小提交。聚合前先向用户确认消息内容。
- **禁止 `git commit --amend`：** amend 会重写 commit hash。如果旧 hash 已被推送（包括 git GUI 自动推送），本地与远程历史分叉，`git pull` 必然产生多余的 merge 提交。要修改已推送的 commit，用 `git reset --soft <基准>` + 重提交 + `git push --force-with-lease`，且须先经用户同意。
- **禁止在 commit 信息中添加 AI 署名。** 作者只为 `提交者自己`，不添加 `Co-Authored-By` 或任何其他协作署名行。
- **禁止使用 `git push --tags`：** 会把本地所有 tag（含上游 remote 拉下来的残留 tag）一并推送到 fork 远端，造成混淆。**发版推送 tag 时用精准推送：`git push origin vx.y.z`**（只推送指定 tag）。如果误推了多余 tag，用 `git push --delete origin <tag>` 逐个清理。

## i18n 翻译

流程：`self.tr("English")` → `translate/zh_CN.ts` → `translate/zh_CN.qm`。

- 所有 UI 文字用 `self.tr()` 包裹，严禁硬编码中文。
- ts 中 `<context>` 对应类名，`<message>` 对应 tr 字符串。
- 编译：`python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
- 验证：`python scripts/i18n_check.py`；发版前 `--ci`。
- `self.tr()` 字符串必须是单个字符串，不要用隐式拼接（`"a" "b"`）—— `i18n_check.py` 按行扫描，检测不到跨行拼接。长字符串在 `tr(` 后换行即可。
- **模块级翻译表禁止 `self.tr(variable)` 间接查表**（检查器看不见，必漏翻译）：在**字面量定义处**用 `QCoreApplication.translate("上下文", "...")` 显式标注上下文（快捷键名 `ui/configpanel.py::_ACTION_NAMES`、画布右键命令 `ui/context_menu_config.py`、饼菜单分区 `ui/pie_menu_editor.py`、线程错误消息 `ui/io_thread.py` 等），下游一律直接使用已翻译值（tr 对非匹配串原样返回）。孤儿白名单已清空（`scripts/i18n_common.py::KNOWN_ORPHAN_CONTEXTS` 为空集），`--ci` 报孤儿/缺失即真实问题，须修复。
- 模块参数 `description`（`ParamWidget` 上下文）是纯数据，由 `scripts/i18n_common.py::extract_param_descriptions` 以 AST 规则从 dict 字面量提取：`modules/` 下含 `description` 键的 dict 全收（`agent/` 包除外）；其它目录须同时含 `value` 键（排除 LLM prompt 数据）。`modules/translators/agent/tools.py` 与 `utils/ai_tools.py` 的 description 是 LLM 提示词，不入 ts。跑 `ts_auto_fill.py` 即可自动同步。
- **饼菜单默认名**（`utils/config.py::DEFAULT_PIE_MENUS` 的 `name`）因 config.py 导入早于翻译器安装，不能就地翻译；以 `ui/pie_menu.py::_DEFAULT_MENU_NAME_TR` 锚点字面量供工具链提取，勿删。
- 无需翻译：日志、LLM prompt、字体测试字符、语言映射字典。
- 常见问题：source 大小写不一致；context 放错；`type="obsolete"`；**ts 的 `<context>` 块必须平行嵌套**（曾出现 ParamWidget 嵌进 ParamComboBox 导致整块对解析器与 qm 编译隐形）。批量编辑 ts 用 Python 脚本直接操作文本。
- **⚠️ QM 编码陷阱**：`scripts/qm_compile.py` 旧版用 `latin-1` 编码，会把 `—`/`→`/`⚠`/`✓` 等非 Latin-1 字符静默替换成 `?`，导致 Qt 哈希查找失败、翻译回退为英文。`self.tr()` 正确但运行时仍显英文 → 查 qm 是否被污染，确保 `_iso8859_str()` 用 `"utf-8"` 后重新编译。诊断脚本与细节见 [`docs/基础速查/i18n.md`](docs/基础速查/i18n.md)「常见问题」。

## 测试流程

改代码后按以下顺序验证，避免遗漏回归。**首选统一入口**：

`./ballontrans_pylibs_win/python.exe scripts/verify.py`

一条命令依次跑 语法 → 文档 → 审计 → i18n → qm → 冒烟；**成功每步只打一行，失败才完整打印报错（据此修复）**。各步自动判定：

- **语法**：只查 git 改动涉及的 .py（`--all` 改查全部 ui/+utils/）
- **文档**：全量校验 `AGENTS.md` 与 `docs/` 活文档里的路径/符号引用（`scripts/check_docs.py`）
- **审计**：登记表契约（`scripts/check_audit.py` + `scripts/audit_registry.json`）——`deprecated` 已删文件不得复活、残留引用须清零（`allowed_mentions` 白名单外）；`suspended` 休眠文件不得被主 UI import；未登记删除仅提示不失败
- **i18n**：全量扫描；硬编码中文/缺失条目为失败，孤儿条目降级为警告（项目大量 `canvas.tr()`/`self.tr(variable)` 间接调用是已知噪音，详见上方 i18n 说明）
- **qm**：ts 有改动时自动编译
- **冒烟**：改动命中启动链文件（`launch.py`/`modules/base.py`/`utils/profile_manager.py`/`ui/configpanel.py`/`ui/mainwindow.py`）时自动触发，`--smoke` 可强制
- **发版门禁**：`verify.py --full` 追加 ruff 风格检查 + pytest（`tests/`；ruff/pytest 未装或重依赖缺失时自动跳过并提示）。原独立全量门禁脚本已并入此 flag（2026-08-27，见 `scripts/audit_registry.json`）

需要时手动分步跑：

1. **语法检查**：`./ballontrans_pylibs_win/python.exe scripts/check_syntax.py <文件...>`（支持多文件；查编译 + tab 字符 + UTF-8 BOM）
2. **文档校验**：`./ballontrans_pylibs_win/python.exe scripts/check_docs.py`（校验 `AGENTS.md` + `docs/` 活文档的路径/符号引用）
3. **审计登记表**：`./ballontrans_pylibs_win/python.exe scripts/check_audit.py`（死代码/休眠登记表 + 删除文件残留引用；**删除文件前先在 `scripts/audit_registry.json` 登记 `deprecated`**）
4. **i18n 检查**：`./ballontrans_pylibs_win/python.exe scripts/i18n_check.py`；发版前 `--ci`；`--show-expected` 列出已知孤儿
5. **qm 编译**：`./ballontrans_pylibs_win/python.exe scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
6. **启动冒烟测试**：`./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py`（单进程约 2s；模拟关键导入链，捕捉 `NameError` / `ImportError`，含 `ProfileManagerWidget` 实例化）
7. **启动 app 目视确认**（可选，但推荐）：双击 `launch.bat` 或 `python launch.py`，确认导航、页面切换、新功能视觉效果正常
8. **MainWindow 在线演练台**（可选，排查无声崩溃/模态框/GC 时机类问题时用）：`./ballontrans_pylibs_win/python.exe scripts/mw_repro.py`——拉起真实主窗口（必须窗口模式，offscreen 起不来 FramelessWindow）跑预设场景或 `--project` 只读打开真实工程；faulthandler 常开。用法见 `scripts/README.md`，方法论见经验教训 §3.3

## 快捷键系统

定义在 `ui/configpanel.py` 的 `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`，`_SHORTCUT_GROUPS` 分组。安装/刷新见 `ui/mainwindow.py` 的 `_install_shortcuts()`/`refreshShortcuts()`。用户配置持久化在 `pcfg.shortcuts`（`config.json`）。详见 `docs/基础速查/快捷键.md`。

## 动画系统

`ui/overlay_modal.py` 的 `OverlayModal`（中心淡入/淡出 + 压暗 scrim，scrim 仅覆盖 `centralStackWidget`；ConfigPanel 用它，duration 350ms, easing `InOutExpo`，`pcfg.animation_fps<0` 跳过）与 `ui/overlay_slide.py` 的 `OverlaySlider`（侧滑滑入；GlobalSearchWidget、PageList 用它）。`MainWindow` 中分别为 ConfigPanel（`_configModal`）、GlobalSearchWidget、PageList 各创建一个实例。`StateChecker`（`QCheckBox` 子类）实现 LeftBar 面板互斥切换。ConfigPanel 现为**内部分页**（`QStackedWidget`），NavList 点击切页而非滚动；子 `QDialog` 打开时经 `_run_modal_dialog` 暂停 backdrop 点击。

## 开发日志

功能增删或修复经用户确认无误后，在 [`docs/daily_log.md`](docs/daily_log.md) 写简要记录。格式参照已有条目：日期标题 → 问题/需求描述 → 改动要点 → 涉及文件列表。每条之间用 `---` 分隔。仅保留最近 3 天记录。
