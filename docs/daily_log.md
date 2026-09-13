# 每日开发日志

> 记录**仓库层面**的改动（功能增删、远端分支变动、规范调整），供变更史查阅。踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见 `AGENTS.md` 的「多代理协作」一节），不进仓库。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-09-12

### Tools 下拉栏软键盘项改勾选态并单独分组

**问题/需求：** 软键盘已是"功能开关"响应类型（窄栏图标勾选态驱动），但在 Tools 下拉栏里仍以普通触发项混在文字/样式工具组中，语义不符。

**改动要点：** `ui/mainwindowbars.py` 的 Soft Keyboard 动作 `setCheckable(True)`，从文字/样式组移出，菜单末尾以分隔线单独分组（视图型开关与触发式工具区分）；动作引用存为 `ui/mainwindowbars.py::quickSymbolAction`。`ui/mainwindow.py` 接线双向同步：启动时按 `symbol_launcher.isChecked()` 对齐初始勾选，此后窄栏图标开合经 `toggled` 信号回写菜单勾选态；菜单点击仍走 `toggle_symbol_dock` 原路径。

**涉及文件：** `ui/mainwindowbars.py`、`ui/mainwindow.py`、`docs/daily_log.md`

---

### 行拖拽交互重构：抓取式真身堆叠 + 实时让位 + 追随/退应动画

**问题/需求：** 原生 QDrag 拖拽可视化差（凭输入框上下边缘 hover 样式判断落点）、拖拽中误触发输入框 hover、多选（尤其隔行选）时组内行渲染相互裁剪；用户期望"实时让位"的列表拖拽，状态只在松手后落账。

**改动要点：**

- **抓取式拖拽**：鼠标抓取在视口（`viewport().grabMouse()`）替代 QDrag，hover 误亮从根上隔离（app 级 eventFilter 兜底吞列表内 hover）；`ui/scenetext_manager.py` 的 `drag_move`/`pw_drop` 旧信号接线移除。
- **实时让位**：拖拽中各行移出布局手动定位，落点槽位（gap）按"邻行中点穿越"规则实时移动（基于目标坐标计算避免与动画反馈振荡，while 循环吸收快拖跨行）；块顺序只在松手时经 `rearrange_blks` 既有命令链落账。
- **真身堆叠 + 变暗遮罩**：被拖组保持原生全分辨率渲染（不再抓快照合成半透明幽灵），多选折叠成堆（第 i 张卡下沉 `ui/textedit_area.py::TextEditListScrollArea.PILE_PEEK` 阶梯、后位卡在上露各卡徽标），聚拢锚点=光标位置，gap 槽高取折叠后堆高；非拖拽内容盖约 15% 变暗遮罩（`DIM_ALPHA`）。
- **追随 + 退应动画**：拖拽组永远朝光标做 140ms OutCubic 补间、鼠标移动只重定目标（聚拢动画天然可见、任何时刻不瞬移；目标未变不重启防抖）；松手/取消时快照→布局同步激活落账（消费端 ghost 自动跳过、数据零延迟窗口）→ 行搬回快照位置整体退应飞向终态（`_settle_to_layout`，落账与取消共用）；快速连拖前停净上一局残留动画。
- **卡片样式消重影**：堆叠重影根因是选中/悬停底色为半透明 `@accentPrimary20`——`ui/misc.py::_derive_solid_tints` 在主题解析时派生预混不透明变量 `@accentPrimary20Solid`，卡片选中/悬停底色改实底（视觉一致不透底，自定义主题自动生效）；卡片圆角 4→6px、选中态加强调描边；落点指示框改 `_DragGapFrame` 自绘（3px 加粗虚线 + 半透明强调底，替代 QSS 细虚线）。
- **测试**：新增 `tests/test_row_drag.py` 16 项（聚拢锚点/让位中点判定/落账置换/Esc 取消/追随不瞬移/退应动画/hover 吞噬/自动滚动等）。

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`、`config/stylesheet.css`、`ui/misc.py`、`tests/test_row_drag.py`（新）、`docs/daily_log.md`

---

### 软键盘焦点切换随迁动画

**问题/需求：** 软键盘浮层在切换文本框焦点时重播淡入，观感突兀；期望已在其它编辑器旁显示时改为滑动随迁。

**改动要点：** `ui/quick_symbol_panel.py::SymbolFloatPanel.open_at_editor` 新增分支：已在该编辑器旁不重播动画；在其它编辑器旁（含收起途中）取消进行中收起动画、由 `_move_animated` 从当前位置滑向新锚点（`pcfg.animation_fps` 门控），不重播淡入。

**涉及文件：** `ui/quick_symbol_panel.py`、`docs/daily_log.md`

---

### 底部栏翻译器模型子菜单 + Tools 下拉栏重排 + AI 辅助功能规划文档入库

**问题/需求：** 底部栏 Translator 按钮菜单缺少模型快切入口（上游有但为三类 profile 的 555 行子系统，不合 fork 体量）；「不常用功能工具箱收纳」方向经用户复议废弃——功能不多，只需对 Tools 下拉菜单做一次重排版；另把多轮讨论定稿的 AI 辅助功能规划文档（框级打标 + 标签体系）入库。

**改动要点：**

- **模型子菜单**：`ui/module_tool_button.py::ModuleSelectionWidget` 新增 `model_menu_provider` 回调属性（菜单每次打开重建时取数，保证反映他处改动）+ `model_changed` 信号 + 「模型」子菜单；`ui/mainwindow.py::_trans_model_menu_data` 读活动 profile 的 `model_options`（无清单不显示子菜单），`on_trans_model_changed` 经 `remember_model_option`/`save_all_profiles` 写回 `profile.model` 即改即落盘。翻译器逐请求重读 profile，切换下一次翻译请求即生效；Profile 卡片页 showEvent 重读磁盘覆盖回显。有意裁剪：只做翻译器模型切换，不搬 vision/image 模态与按钮文字显示模型名。
- **Tools 下拉栏重排**：`ui/mainwindowbars.py` 菜单两组化——文字/样式（样式管理器、Quick Symbol、高级对齐、整理换行）+ 页面/图像（区域合并、路径重排、无字图配对）；整理换行从「导出/批量处理」、无字图配对从「外部工具」各自归位，「外部工具」分组取消。方向改定与最终分组记录在 `docs/技术实现/不常用功能工具箱_规划.md`（已重写为「整理工具下拉栏」）。
- **AI 辅助功能规划文档**：新增 `docs/技术实现/AI辅助功能_规划.md`（六轮讨论定稿：§7 框级打标触发、§8 疑点/指示标签体系与五标签定稿、OCR 置信度透传调研、实施批次 A–D），状态「规划定稿，可实施」。

**涉及文件：** `ui/module_tool_button.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/不常用功能工具箱_规划.md`、`docs/技术实现/AI辅助功能_规划.md`

---

### 快捷符号改版为软键盘（贴框跟随浮层 + 焦点驱动弹收 + 撤销链修复）

**问题/需求：** 原 Quick Symbol 是对话框形态、需先选中文本框才能插入，罗马字输入与目标框割裂；实测还发现插入不经撤销历史、且可能透传出画布意外渲染。经三轮迭代定稿：窄栏图标只作功能开关，聚焦编辑器自动弹出软键盘。

**改动要点：**

- **功能开关 + 焦点驱动弹收**：窄栏新图标（`icons/rail_symbol.svg`）勾选态=开关（`pcfg.symbol_keyboard_enabled`）；焦点进入触发范围编辑器弹出、离开即收（`ui/text_panel.py::_sync_symbol_keyboard` 编排，浮层不写开合记忆、不占 dock 互斥名额）。触发范围 `pcfg.symbol_keyboard_source_only` 默认仅原文框，设置页可选两侧。
- **软键盘面板**：新增 `ui/quick_symbol_panel.py`——假名/符号两页键区（清音+浊音/小书き/记号，全内容滚动不裁剪）、罗马字转假名输入行（wapuro 式贪心最长匹配 + 促音/拨音/片假名切换）、全角空格与退格；键区全部 NoFocus 不抢编辑器焦点，页签与假名模式按钮带 checked 激活样式。原 `ui/quick_symbol_dialog.py` 删除（已登记 deprecated）。
- **贴框跟随浮层**：`SymbolFloatPanel`（继承 `ui/custom_widget/float_drop_panel.py::FloatDropPanel`）锚定聚焦编辑器——默认贴编辑器左缘向画布展开、画布过窄改贴右缘并夹回宿主；换编辑器随迁，编辑器滚动/改尺寸与宿主缩放自动重锚；显隐带 16px 滑动过渡（`pcfg.animation_fps` 门控，可打断反向）。`FloatDropPanel` 构造期锚点无父级时的 parent 环死循环已加守卫。
- **撤销/透传修复（根因三连）**：①`ui/textedit_area.py::insert_external_text` 显式登记与文档信号链重复致双登记，删多余调用；②`ui/canvas.py::note_source_edit` 原文无会话时降级忽略，改为重建 before 开会话（纯插入精确可逆）；③插入/退格前 `editor.setFocus()` 让 focus_in 重开会话并使光标可见。
- **文档与测试**：i18n 全量同步（新词条 + 上下文标注）；`tests/test_configpanel_node3.py` 迁移、`tests/test_rail_docks.py` 桩补齐；`docs/项目概述.md`、`docs/基础速查/i18n.md`、`docs/技术实现/反向移植_规范.md`、上游移植完成记录同步。

**涉及文件：** `ui/quick_symbol_panel.py`、`ui/quick_symbol_dialog.py`（删）、`ui/text_panel.py`、`ui/textedit_area.py`、`ui/canvas.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/scenetext_manager.py`、`ui/custom_widget/float_drop_panel.py`、`ui/custom_widget/rail_dock_panel.py`、`utils/config.py`、`icons/rail_symbol.svg`、`icons/text-effect-stroke.svg`、`config/stylesheet.css`、`scripts/audit_registry.json`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_configpanel_node3.py`、`tests/test_rail_docks.py`、`docs/`（4 处）

---

## 2026-09-11

### 分支清理 + 多代理协作约定（当日立规并撤回）（WorkBuddy）

**问题/需求：** 清理仓库中不再使用的分支（`dev`/`master`/`vision_context` 三个上游来源分支须保留）。

**改动要点：**

- **远端分支清理**：`origin` 上 4 个 `feat/backport-*`（当初给上游提 PR 用）全部删除，`origin` 只剩 `main`。依据：PR #1279（快捷键）/ #1280（序号徽标）/ #1281（路径重排）closed 但上游做了修改适配、未直接合并，#1282（点击空白弃选）已合并；4 项功能代码均确认存在于 `main`。上游 `upstream-tmp` 保留 `dev`/`master`/`vision_context` 并更新到上游最新（`dev` → `2653fc41`），`vertical_text_layout` 上游远端早已删除、本地残留引用清除。取上游引用统一加 `--no-tags`。
- **多代理协作约定（当日立规并撤回）**：当日上午在 `AGENTS.md` 新增「多代理协作」一节与一条 Git 规则，并在用户目录建立跨代理共享记忆区 `~/.agent-memory/`（按项目名分目录，**不入库**）。**当晚撤回该方案**：`~/.agent-memory/` 整目录删除，`AGENTS.md`、`docs/daily_log.md` 中指向它的引用与那条 Git 规则一并清除，改用「代码写入、提交与推送以 zcode 为主，WorkBuddy 只承担规划与零散杂活」的分工，只保留「同一时间一个代理有写权」「交替期间只暂存自己的文件」「私人笔记不入库」三条协作规则。

**涉及文件：** `AGENTS.md`、`docs/daily_log.md`

---

### 设置面板死布局清理 + Pipeline 页引擎切换下拉回归

**问题/需求：** 承接 2026-09-10 普查的挂账：`ui/configpanel.py` 里一组死布局类被展示台当活控件展示、牵动覆盖门禁，用户拍板另起一批清理；另拍板把合并 Pipeline 页的引擎切换下拉加回来（原「等用户反馈」的条件触发项兑现），设置面板「上次所在页」记忆不做（页面少）。已实机验收。

**改动要点：**

- **死布局清理（全批 +105/−495 行）**：删 `ui/configpanel.py::ConfigBlock`、`ui/configpanel.py::ConfigContent`（活代码零实例化，仅展示台引用）、configpanel 局部版 `combobox_with_label` / `checkbox_with_label`（唯一调用方是死掉的 ConfigBlock）、`ConfigPanel.addConfigBlock` shim 与 `dlConfigPanel` 死变量。**`_DeadBlock`/`_DeadLayout` 是活的**（General 四页构建器经 `generalConfigPanel.addGroupedBlock` 注册分页），保留；`_scroll_interval` 有 NavList 滚动动画在用，保留；`Tuple`/`QLabel` 两个失去用途的导入一并清。
- **QSS 死规则**：`config/stylesheet.css` 删约 70 行 `ConfigContent *` 下划线风格规则（分页改造后无任何控件再带该祖先类）与 `QScrollArea#ConfigContent` 边框豁免；`ConfigLineEdit`/`ConfigTextEdit`/`ConfigComboBox` 选择器列表里的 `ConfigContent` 前缀分支随之缩减。
- **展示台同步**：`scripts/style_showcase.py` 删 ConfigBlock/ConfigContent 两工厂与两行展示（`EXCLUDED` 里 `combobox_with_label` 指 `ui/custom_widget` 的活函数，保留）；覆盖门禁照常通过（只比对 `ui/custom_widget` 导出）。
- **引擎切换下拉回归**：`_build_pipeline_page` 不再隐藏选择行，四个标签顶栏恢复 `[模块名][引擎下拉][?]`，备注按钮改锚定 `module_combobox` 之后；在设置页切换即真正换引擎（`ui/module_manager.py` 的 `set*` 槽镜像回底部栏）。`ui/module_parse_widgets.py` 的 `engine_label`/`_refresh_engine_label`/`set_module_selector_visible` 整套删除；检测页 note 文案改「直接在上方的下拉框切换引擎」。
- **画布侧改镜像下拉（关键设计）**：原实现 showEvent 时把共享 `module_combobox` reparent 借走且不还——恢复设置页下拉会让修复标签开天窗。改为 `ui/module_parse_widgets.py::ModuleConfigParseWidget.create_mirror_selector`：画布 `ui/drawingpanel.py::InpaintPanel` / `RectPanel` 各持独立镜像（真值源不变，show 时 `sync_items` 重建条目与 tooltip、源变更单向跟随、镜像 `activated` 回流 `setCurrentText` 走正常切换链），reparent 借用 hack 与 `hideEvent` 全删，高度锁定移入镜像构造。与底部栏「各持一份、信号同步」同模式。
- **i18n/测试**：ts 清 3 孤儿（DL Module / Engine: %1 / 旧检测 note）+ 新 note 手填译文，qm 重编；`tests/test_pipeline_page_merge.py` 改断言选择行可见 + 新增镜像下拉行为测试（条目同步/文本跟随/activated 回流）。**坑**：裸 `ConfigPanel()` 的阶段面板没走 `addModulesParamWidgets`，`currentTextChanged → on_module_changed` 连接不存在，引擎切换信号链无法用 addItem 探测，故测镜像行为代替。`verify.py --full` 全绿。

**涉及文件：** `ui/configpanel.py`、`ui/module_parse_widgets.py`、`ui/drawingpanel.py`、`ui/overlay_slide.py`（清注释残留）、`scripts/style_showcase.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_pipeline_page_merge.py`、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`、`docs/基础速查/打包控件功能使用说明.md`

---

## 2026-09-10

### 设置面板管线页合并 + 运行窗口重做 + 杂项落位（三提交）

**问题/需求：** 承接 2026-09-09 的调研结论：管线四页靠硬编码下标往基础布局里插杂项，四页骨架不一致（翻译页四个附加块全堆在 Parameters 标题上方、检测页复选框挤在模块下拉同一行、修复页附加项在参数下方）。用户拍板的方向：管线页**合并成一页**、页内用标签切换；模块选择交给底部栏（设置面板不再放）；常用管线开关搬进「运行」窗口（对齐上游「启用模块」网格）；搬剩的杂项并入 General 的 App 页。

**改动要点：**

- **管线页合并（`52d2d40`）**：`ui/configpanel.py::_build_pipeline_page` 用 `QTabBar` + `QStackedWidget` 承载四阶段，Modules 组导航 6 项 → 3 项（Module Actions / Pipeline / LLM Profile），全站 12 页 → 10 页。四个阶段面板**对象与信号链原样保留**（底部栏、`module_manager`、画布修复工具面板都持有引用），只经 `ui/module_parse_widgets.py::set_module_selector_visible(False)` 隐藏模块选择行、改为只读引擎名（`engine_label`）。`module_combobox` 只隐藏不删除——它仍是真值源。合并页整体不套 `_wrap_page`（否则标签栏随内容滚走），各标签内容单独 `_wrap_page` 并去掉左右留白。`focusOnDetect/OCR/Inpaint/Translator` 改为「选中 Pipeline + 切对应标签」，底部栏四个齿轮路径不变。删掉修复页 `showEvent`/`hideEvent` 往返搬运模块下拉的 hack；画布侧 `ui/drawingpanel.py::InpaintPanel` / `RectPanel` 改为在 `showEvent` 里显式点亮借来的下拉（不做显式隐藏——切工具时新面板的 show 可能先于旧面板的 hide）。
- **运行窗口重做（`74b58b1`）**：内联在 `ui/mainwindow.py::run_imgtrans` 的约 400 行对话框抽成 `ui/run_pipeline_dialog.py`，按上游同名文件的形态重做——上半「启用模块」网格（每阶段 = 图标开关 + 模块下拉），下半各阶段折叠选项区（`ExpandingToolButton`）。阶段强调色取主题的 `@accentDetect`/`@accentOCR`/`@accentInpaint`/`@accentTranslate`（与 LLM Profile 徽章同一套），不移植上游硬编码调色板。模块下拉写回底部栏选择器（唯一真值源），不新增 ModuleManager 接口。搬入的选项：检测＝Keep Existing Lines、修复＝Skip simple cases、翻译＝源/目标语言 + 单块翻译模式 + LLM 上下文/术语表整块。保留 Render Only 批量渲染、页码 RangeSlider + All Pages、术语表状态指示、Run without update textstyle、清空二次确认。**刻意不移植**：无边框外壳与 `DialogCloseButton`、每阶段 hover 齿轮（运行窗口是模态的，无法在其上拉起设置浮层；底部栏齿轮已承担该入口）、上游 `page_range_progress`。资源补 4 个图标 + `RunPipeline*` QSS。`InpainterBase.check_need_inpaint` 初值改由 `module_manager` 从 pcfg 直接推入；翻译器异步加载完成后经 `_sync_run_dialog_translator` 刷新语言下拉。
- **杂项落位（本提交）**：从管线页搬出的两项直接并入 General 的 **App 页**，不单设暂存页——`Misc` 这种名字会自己招来下一条无家可归的选项。App 页成为 Updates / External Editor / Workbench / Export Config / Import Config 五节，General 组 7 → 6 页、全站 10 → 9 页。Photoshop 路径（从修复页搬来，`ConfigPanel.ps_path_edit`）归 App 的 External Editor 节，工作台确认开关（从翻译页搬来，`ConfigPanel.confirm_costly_checker`）归 Workbench 节；`ui/drawingpanel.py` 的「未找到 Photoshop」提示同步改指 `Settings → App → Photoshop Path`。导航首项 `Module Actions` → `Models`（与该页自己的 `PanelGroupBox` 标题一致）。新增 `tests/test_settings_app_page.py`。
- **i18n**：新增 `RunPipelineDialog` 上下文 33 条（译文复用既有条目）、ConfigPanel/DrawingPanel 补 11 条，清掉移出产生的 41 条孤儿，qm 重编。
- **测试**：新增 `tests/test_pipeline_page_merge.py`（8 例）、`tests/test_run_pipeline_dialog.py`（13 例）、`tests/test_settings_app_page.py`（6 例）；冒烟测试补 `RunPipelineDialog` 实例化。`verify.py --full` 全绿。

**涉及文件：** `ui/configpanel.py`、`ui/module_parse_widgets.py`、`ui/run_pipeline_dialog.py`（新）、`ui/drawingpanel.py`、`ui/mainwindow.py`、`ui/module_manager.py`、`config/stylesheet.css`、`icons/text_disabled.svg` 等 4 个图标（新）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_pipeline_page_merge.py`（新）、`tests/test_run_pipeline_dialog.py`（新）、`tests/test_settings_app_page.py`（新）、`tests/test_startup_imports.py`、`AGENTS.md`、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

### 设置面板普查整理 + 运行窗口字号对齐（三提交）

**问题/需求：** 用户实机验收三提交后反馈「运行界面的拓展管线功能项的**字号**需要对上游」，并要求在继续下一步前先改掉；随后要求对设置面板做**一次全面普查**，据此判断如何整理与编排。

**改动要点：**

- **运行窗口字号对齐**：折叠区内的标签与折叠头本来就在 QSS 里钉了上游的 12px，但区内的下拉落到了全局 `QComboBox` 的 14px，比同一行标签大一圈；复选框与数值框则未显式钉住，靠应用字体 9pt 凑巧对齐。补一组 `QWidget#RunPipelineSettingsSection` 规则（下拉 12px 且收到 20px 高、复选框 12px + 12px 指示器、数值框 12px），对齐上游同名规则。**顺带修一个离屏渲染才暴露的真 bug**：`ui/run_pipeline_dialog.py::_build_translate_options` 把「Source」标签 `addWidget` 到了整页布局而不是它自己那一行，实际渲染成 Translation 折叠区下方一行孤立的 Source；已改回 `lang_layout`，并补回归测试（断言页布局里没有裸 `QLabel`、Source 标签与下拉同行）。
- **普查结论**（用户实机验收第一批后要求对设置面板做一次全面普查）：页面骨架本身是对的，问题集中在——Typesetting/App 有三处手写 `setContentsMargins` 没走 `ConfigFormRow`；另有三个死/错项（见后两条提交）。**明确不搬**：设置在面板外的大批持久项（画笔粗细/裁剪比例/搜索选项/暗色模式等）就地调整更自然，收进设置页只会把一次点击变成「开面板→找页→改→关」，违背本分支「交互路径越短」。设置面板只收「配置一次、之后不常改」的项——这条是持久取向。
- **子行排版归一 + 清死项**：子选项缩进统一到一个写法（`ConfigFormRow("", 控件)` 套 24px 缩进 wrapper，Interface 页既有做法），替换 Typesetting 与 App 页各一处手写的 `QHBoxLayout` + 写死 158/134；App 页 Developer channel 行删掉与常显 `⚠` 行逐字重复的 `?` 备注；Typesetting 的 `Quick insert characters` 独立成「Quick Symbol Palette」节。清项：删零引用字段 `pcfg.expand_font_format_panel`（`nested_dataclass` 忽略多余键，旧 config.json 不受影响）、修两处把工作台确认开关指向已不存在的「翻译器页」的注释、补上术语面板「不再提示」后回写设置页复选框的同步（`ConfigPanel.setupConfig` 只在启动跑一次，不回写会停在旧值）。
- **审计登记表新增 `dormant_symbols`**：普查发现 `ui/context_menu_config.py` 的右键菜单自定义对话框全仓无实例化（用户拍板本轮不接线也不删）。原有 `suspended` 是**文件级**契约，登记不了活文件里的一个符号，于是新增第三类（键 `路径::符号`）：文件里必须仍定义该符号，且定义文件之外全仓不得引用——一旦有人接线，检查失败并要求撤销登记，与 `deprecated` 的「残留引用必须清零」互为镜像。

**涉及文件：** `ui/run_pipeline_dialog.py`、`ui/configpanel.py`、`ui/drawingpanel.py`、`ui/glossary_agent_panel.py`、`ui/module_parse_widgets.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/check_audit.py`、`scripts/audit_registry.json`、`.agents/skills/audit-docs/SKILL.md`、`tests/test_run_pipeline_dialog.py`、`tests/test_settings_app_page.py`（新）、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

### 部署可用性修复（用户报「源码包双击打不开」）+ 依赖声明理清

**问题/需求：** 有用户按说明下载源码包 + CPU 依赖包、解压后**双击终端打不开**（窗口一闪而过）。该用户能正常启动上游，基本排除微软运行库缺失。开发环境数月未验证过可部署性，遂做一次系统排查——查出四个独立缺陷，其中三个会直接导致启动失败。

**改动要点：**

- **根因一：`.bat` 是 LF 行尾。** 本仓库自 2026-05-06 分叉（`6649de10`）后未再合并上游，而 `.gitattributes` 是上游 2026-07-16 才加入的，本分支谱系里从来没有该文件。缺了它，Windows 上 git 把 `launch.bat` 按 LF 存进 blob，GitHub 源码包（`git archive`）解出来也是 LF；cmd.exe 读 LF-only 批处理会解析错位后中止，且来不及执行到 `pause`，窗口一闪即关。恢复上游同款 `.gitattributes`（blob 与 `upstream-tmp/dev` 逐字节一致）：`* text=auto eol=lf`，`.bat`/`.cmd` 强制 `eol=crlf`。
- **根因二：安装路径含括号。** 路径里有 `(` `)` 时，`launch.bat` 的 `echo ... %PYTHON%` 在括号块内被 cmd 解析期截断（`%VAR%` 在整块解析时展开，`)` 提前闭合块）→ 退出码 255 且零输出。三处改用延迟展开 `!PYTHON!`。
- **根因三：更新器会把 `.bat` 改回 LF。** `raw.githubusercontent.com` 返回的是仓储 blob（`eol=crlf` 只作用于 checkout/archive 输出，不影响 blob 存储），manifest 增量更新下载 `launch.bat` 后按原样写入即变回 LF，把本来能用的启动器静默改坏——即使源码包修好了，装完第一次更新又会复发。`scripts/check_update.py` 新增 `_as_crlf()`，写盘前对 `.bat`/`.cmd` 强制 CRLF；新增 `tests/test_update_eol.py` 锁死该契约（含幂等性与后缀作用域，已验证修复前该用例会失败）。
- **根因四：`download_models.bat` 存量损坏。** if 括号块里写了裸 `(` `)` 且不结行，第 24 行即 parse error 退出，任何模型都下载不了（与本次报障无关，但同属「照说明操作走不通」）。5 处转义为 `^(` `^)`。
- **行尾口径统一：** 索引里残留两条 CRLF blob（`modules/textdetector/panel_finder.py`、`utils/merger.py`，`.gitattributes` 缺失期的历史遗留），`git add --renormalize` 一并规范化为 LF，使全仓文本文件口径一致。这两个文件在本提交里是**纯行尾改动**，无内容变化。
- **依赖声明理清：** `ultralytics` 从 `requirements.txt` 移除（它会连带拉 torch + matplotlib），改为在 `modules/textdetector/detector_ysg.py` 按模块声明两处——`dependencies` 供懒加载 AST 扫描（模块管理对话框据此提示安装），`requires_packages` 供 `modules/base.py::BaseModule.ensure_dependencies` 在 `load_model` 与 `launch.py` 的模型文件回退路径读取。用户拍板「用源码的要么有一键包要么自己知道需要什么，需要给自由度」，故不塞进必修表。`pyproject.toml` 补 `fonttools`（原先是靠 ultralytics 传递引入，去掉后会失去来源）。删除 `scripts/build_portable.py`（产出的 `python_embeded/` + `run.bat` 与实际分发的 `ballontrans_pylibs_win/` + `launch.bat` 布局早已不符、拷贝清单漏 `icons/` 与 `scripts/`，且整目录拷贝 `config/` 会把含 API 密钥的 `config/config.json` 打进包里）与 `config/requirements_core.txt`（唯一生成方就是被删的脚本）。两个删除均已登记 `scripts/audit_registry.json` 的 `deprecated`。
- **CPU 依赖包补齐：** 分发的 `ballontrans_pylibs_win` 缺 `numba`/`llvmlite`（`ui/text_engine/effects/paint_numba.py`、`ui/text_engine/transforms/grid_numba.py` 的加速路径，缺失时退回纯 NumPy），已补进依赖包目录，`docs/基础速查/依赖库说明.md` 的手动搭建步骤相应加第四批。

**涉及文件：** `.gitattributes`（新增，内容同上游）、`launch.bat`、`scripts/download_models.bat`、`scripts/check_update.py`、`tests/test_update_eol.py`（新）、`requirements.txt`、`pyproject.toml`、`modules/textdetector/detector_ysg.py`、`scripts/build_portable.py`（删）、`config/requirements_core.txt`（删）、`scripts/check_docs.py`、`utils/updater.py`、`scripts/audit_registry.json`、`docs/基础速查/依赖库说明.md`、`docs/项目概述.md`、`scripts/README.md`、`modules/textdetector/panel_finder.py`、`utils/merger.py`（后两者仅行尾）

---

## 2026-09-09

### 设置面板 LLM Profile 页改卡片式（A 方案·精简版）

**问题/需求：** 用户反馈设置面板开始堆屎山：LLM 页加入在线修复后比上游麻烦、管线页杂项排布乱。调研发现上游 1.5.14 走的是相反方向——管线合并成一页、LLM Profile 独立成卡片列表页（能力徽章 + 折叠详情 + 声明式分节），而 fork 是「下拉框 + 一张常显大表单」，所以每加一种能力页面必然变长。本批只做 LLM 页，形态对齐上游。

**改动要点：**

- **新增 `ui/llm_profile_cards.py`**：`LLMProfileListWidget`（工具栏 + 卡片列表）/ `LLMProfileCardWidget`（摘要 + 折叠详情）/ `LLMProfileDetailsWidget`（通用参数 + 三个能力分节，每节一个 `ParamWidget`）/ `LLMProfileBadge`（点击切换 `vision_support` / `image_support`）。参数定义走 `PROFILE_COMMON_PARAM_DEFS` / `PROFILE_SECTION_PARAM_DEFS`（镜像上游同名常量），写回类型转换走 `PROFILE_FIELD_TYPES`。
- **数据层不动**：`utils/profile_manager.py` 退为纯数据/服务层，保留 dict 模型、`model_profiles` JSON 字符串、name 作主键与全部消费者；不移植上游的 `LLMProfile` dataclass / SecretStore / id 主键。保存时机保持「增删 / 恢复内置 / Fetch 成功 / 离开页面才落盘」，避免敲键即触发 `module_manager` 重建参数面板。
- **删死代码**：`ProfileManagerDialog`（约 629 行，全仓无实例化）、`ProfileManagerWidget`（被新页取代）、`save_profile` / `delete_profile`（无调用者）与随之失效的导入；网络辅助改名公开（`NetWorker` / `probe_connection` / `probe_model_list`）供新 UI 复用。
- **接线**：`ui/configpanel.py` 换用 `LLMProfileListWidget`，注入 `_run_modal_dialog`（顺带修掉 profile 子对话框打开时 scrim 可点关整面板的旧问题）；`focusOnLLMProfile(name)` 现在会展开并滚动到对应卡片。
- **资源**：从上游拷 `icons/text.svg`、`eye.svg`、`image.svg`、`llm_key_ok.svg`、`llm_key_missing.svg`（上游禁用态与激活态形状相同，故只取 5 个，颜色改由 `render_svg_pixmap(override_fill=…)` 按主题与模态强调色着色）；`config/stylesheet.css` 追加 `LLMProfile*` 规则。
- **i18n**：删 `ProfileManagerDialog`（66 条）与 `ProfileManagerWidget`（69 条）两个 context，新增 `LLMProfileCardWidget`（58 条）/ `LLMProfileListWidget`（7 条）；中文译文复用 git 历史里旧 context 的既有术语并补齐余项，qm 重编。
- **测试**：`tests/test_startup_imports.py` 改为实例化 `LLMProfileListWidget`；新增 `tests/test_llm_profile_cards.py`（9 例：卡片数 / 初始折叠 / 徽章门控分节 / 字段写回与类型转换 / 内置禁删 / `hideEvent` 发信号 / 新增唯一名；写盘被 patch 掉以免污染真实 `config.json`）。

**涉及文件：** `ui/llm_profile_cards.py`（新）、`utils/profile_manager.py`、`ui/configpanel.py`、`config/stylesheet.css`、`icons/text.svg` 等 5 个图标（新）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_startup_imports.py`、`tests/test_llm_profile_cards.py`（新）、`AGENTS.md`、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

### LLM 页模型切换对齐上游 + 上游 v1.5.13→v1.5.15 底层修复移植

**问题/需求：** 用户审查卡片页后反馈「单供应商切模型体验不好，上游做得到位」。调研上游 v1.5.15 的机制：每个 profile 持久化一份模型清单（`PROVIDER_DEFAULTS` 预置供应商模型表），摘要行常驻模型下拉 + 增删按钮，底部模块栏还能一键切模型；上游**没有** Fetch Models。用户拍板：吸收上游的清单 + 下拉，保留 fork 的 Fetch Models 并升级为多选添加，**不维护固化的模型表**（清单只由抓取或手填产生）。同批按「底层性能/实现优先服从上游」的原则，把 v1.5.13→v1.5.15 的底层修复移植过来。

**改动要点：**

- **模型清单（无内置表）**：profile 增 `model_options` / `image_model_options` 两个字段（`utils/profile_manager.py::PROFILE_FIELDS`、`SAMPLE_PROFILES` 全空）；`utils/profile_manager.py::normalize_model_options` 在 `load_profiles` 时规整清单并把当前值补进列表，`utils/profile_manager.py::remember_model_option` 负责去重追加。存储格式与消费者（仍读 `profile["model"]`）不变。
- **摘要行模型下拉**：新增 `ui/llm_profile_cards.py::_ModelSelector`（`ConfigComboBox` 拉伸模式 + `+` 手填 / `−` 删当前），文本模型常显、图像模型随 `image_support` 显隐；`model` / `image_model` 从详情参数里移除（不再有双入口），`_rebuild_details` 随之删除。切模型 / 增删是离散操作，立即落盘（`ui/module_manager.py::_on_profiles_changed` 只刷新类级选项，代价小）。
- **Fetch Models 多选**：`utils/profile_manager.py::FilterableListDialog` 增 `multi_select`（`ExtendedSelection` + `selected_items`）；`ui/llm_profile_cards.py::_pick_model` 把选中项全部入清单、首个设为当前模型并落盘——网络只用于刷新清单，之后切模型完全离线。
- **控件库**：`ui/custom_widget/combobox.py::ConfigComboBox` 增 `stretch` 参数（不做宽度分级锁定，交给布局横向拉伸）；`config/stylesheet.css` 补 `LLMProfileModelRow` / `LLMProfileFieldLabel` / `LLMProfileModelAddButton` / `LLMProfileModelRemoveButton` 规则。
- **上游底层修复移植**：①效果栈瓦片——`ui/text_engine/rendering/raster.py::plan_effect_raster` 补取整余量（`-1`）与随缩放分层的 `tile_tier`，`ui/text_engine/effects/renderer.py::_draw_tiled_effects` 整块 staging 超光栅策略时不再抛错丢弃，改为逐瓦片裁剪直接绘制（大窗口 / 大描边半径下效果不再整块消失，上游 9b34135 + e88c655）；②LaMa 预处理改「补边对齐」而非重采样（`modules/inpaint/base.py`，上游 b36210b，保住网点与遮罩边缘）；③16-bit 灰度 PNG 读入取高字节（`utils/io_utils.py::imread`，上游 afad9f5）；④`ParamLineEditor` 的 `QDoubleValidator` 固定 C locale（`ui/module_parse_widgets.py`，上游 73741cf）；⑤Ctrl+C/V/X 改 `QKeySequence.StandardKey` 匹配（`ui/canvas.py`、`ui/text_engine/item.py`，上游 a86ebb1）；⑥水平排版单个空格软换行不再跳动（`ui/text_engine/horizontal_layout.py::_trailing_space_layout`，上游 595f6fd）。
- **画笔粗细**：`ui/custom_widget/slider.py::Slider` 抽出 `_value_to_position_ratio` / `_position_ratio_to_value` 两个映射钩子（默认线性，groove 绘制与命中测试都走它）；`ui/drawingpanel.py::_BrushThicknessSlider` 重载为对数映射（小笔头不再挤在左端，数值仍是精确像素），`ui/drawingpanel.py::_create_thickness_control` 给两个画笔面板配 `NoArrowsSpinBox` 精确输入（双向同步，滑块程序化 setValue 也同步输入框）。
- **未移植**：合成粗体（用户不感兴趣，此前已决定删除该特性）、上游 LLM 上下文/记忆/全页 OCR/PS Bridge/HayaiOCR/多语言 ts（依赖上游 `LLMProfile` 数据模型或方向相反）。
- **测试**：`tests/test_llm_profile_cards.py` 扩到 18 例（清单只有当前值 / 下拉写回 / 手填追加 / 删当前选邻居 / Fetch 多选全入列 / 图像行随徽章显隐 / 数据层归一与对话框多选）；新增 `tests/test_image_io.py`（16-bit PNG）、`tests/test_effect_raster_policy.py`（瓦片余量 + tier 分层）、`tests/test_brush_thickness.py`（对数映射 + 精确输入框）。verify.py --full 全绿。

**涉及文件：** `ui/llm_profile_cards.py`、`utils/profile_manager.py`、`ui/custom_widget/combobox.py`、`ui/custom_widget/slider.py`、`ui/drawingpanel.py`、`ui/text_engine/effects/renderer.py`、`ui/text_engine/rendering/raster.py`、`ui/text_engine/horizontal_layout.py`、`ui/text_engine/item.py`、`ui/canvas.py`、`ui/module_parse_widgets.py`、`modules/inpaint/base.py`、`utils/io_utils.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_llm_profile_cards.py`、`tests/test_image_io.py`（新）、`tests/test_effect_raster_policy.py`（新）、`tests/test_brush_thickness.py`（新）、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

### LLM 页与修复面板实机反馈五修

**问题/需求：** 上一批落地后实机验收提出五处：①摘要行模型下拉横向拉满整行，看着空且怪；②主机地址 / API Key 埋在杂项参数里不够显眼，图像修复端口也应并到一处；③画笔粗细的精确输入框挤占了滑条，滑条太短；④AI 修图页的比例提示被横向裁掉，且长句不如表格；⑤生图模型需要可手动输入（中转站可用性检查常误报）。

**改动要点：**

- **模型下拉按内容自适应**：`ui/llm_profile_cards.py::_ModelSelector` 去掉 `stretch`，改 `ConfigComboBox(fix_size=False)` + `AdjustToContents`（受分级上限兜底），`+` / `−` 紧跟其右并留尾部空白。
- **连接信息块**：新增 `ui/llm_profile_cards.py::_ConnectionBlock`（`CONNECTION_PARAM_KEYS` 定序 `api_host` / `api_key` / `image_base_url`），标签在上 + 整行加高输入框（`LINEEDIT_FIXHEIGHT`），容器带强调左边条（`LLMProfileConnectionBlock` QSS）；`image_base_url` 随 `image_support` 显隐（`set_section_visible("image", …)` 同步）。这三个字段从 `PROFILE_COMMON_PARAM_DEFS` / 图像分节移出，详情页改为「连接信息 + 生成参数 + 三能力分节」。
- **画笔粗细滑条恢复原长**：`ui/drawingpanel.py::_create_thickness_control` 改为 `(slider, spinbox, row_layout)`，数值框（宽 56）放进标签列右端、标签可省略（`_ElidedToolNameLabel`），滑条独占其余宽度——实测 86px → 166px（与加精确输入前一致）。
- **AI 修图页比例支持表 + 裁剪修复**：面板最小宽从 388px 降到 253px（可用约 326px）。三处根因：`CropControls` 把「裁剪模式」复选框和比例下拉挤在同一行（改为独立一行）；`QComboBox` 默认把**最长条目宽度**算进 `minimumSizeHint`，长 profile / 模型名把面板顶宽——新增 `ui/drawingpanel.py::_shrinkable_combo`（`AdjustToMinimumContentsLengthWithIcon` + `minimumContentsLength(0)`）处理面板内所有下拉；整句比例说明换成 `InpaintAspectTable` 紧凑表格（模型 / 支持比例两列 + 其它模型脚注），比例单元格 `setWordWrap(True)` 窄栏换行而非撑宽。
- **生图模型栏**：`ui/drawingpanel.py::AIConfigPanel` 在 Profile 下新增可编辑下拉（清单取所选 profile 的 `image_model_options`，`activated` / `editingFinished` 才提交），`_commit_image_model` 写回该 profile 的 `image_model` 并记进清单（`save_all_profiles`）。跨页写同一 profile，故 `ui/llm_profile_cards.py::LLMProfileListWidget.showEvent` 在 `pcfg.module.model_profiles` 与上次落盘值不一致时重载，避免旧副本在 `hideEvent` 覆盖。
- **i18n**：新增 `Connection` / `Generation` / `Model` / `Ratios` / `Other models follow the Nano Banana set.` / `Image Model` / `Model name` 七条（中文：连接信息 / 生成参数 / 模型 / 支持比例 / 其它模型按 Nano Banana 的比例集处理。/ 生图模型 / 模型名称），删掉旧整句比例说明的孤儿条目，qm 重编。
- **测试**：`tests/test_llm_profile_cards.py` 扩到 23 例（模型下拉非拉伸且有上限 / 连接块承载 host+key / 连接块写回 / 图像端口随徽章显隐 / `showEvent` 重载外部改动）；`tests/test_brush_thickness.py` 补「数值框在标签列内、滑条独占其余宽度」断言；新增 `tests/test_ai_inpaint_panel.py`（6 例：模型清单 / 手输与点选写回 profile / 比例表内容 / 比例单元格换行 / 面板最小宽 ≤ 326）。

**涉及文件：** `ui/llm_profile_cards.py`、`ui/drawingpanel.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_llm_profile_cards.py`、`tests/test_brush_thickness.py`、`tests/test_ai_inpaint_panel.py`（新）、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

### 修复面板实机反馈收尾：数值框去单位 + 按钮内边距 + 对齐普查

**问题/需求：** 五修验收后用户反馈三点：①粗细数值框里的 `20 px` 被裁成 `20 p`，干脆去掉单位；②「修复 / 清除遮罩」按钮字紧贴边框；③顺带看看还有没有边距不合适或该对齐没对齐的地方。

**改动要点：**

- **数值框去单位**：`ui/drawingpanel.py::_create_thickness_control` 去掉 `setSuffix(" px")`（宽度 56 不变，够显示 4 位数）。
- **按钮内边距**：stylesheet 新增 `DrawingPanel QPushButton { padding: 0 12px; }`——全局 `QPushButton` 无内边距，中文两字按钮的 sizeHint 就等于文字宽（实测 30px 宽装 28px 文字）。
- **对齐普查**（实测离屏渲染 + 真实字号/译文逐控件量 x）：①内嵌体（画笔体 / 框选体 / `CropControls`）的布局 margins 归零，原来默认 9px 让这一组比上方字段整体右移；②粗细滑条行不再自设 `spacing(10)`，沿用父布局 14px，滑条起点与同栏下拉框同列（原来差 4px）；③`_shrinkable_combo` 统一锁高 `CONFIG_COMBOBOX_HEIGHT`（默认 29 vs 26 参差），修复工具下拉框（属设置页控件）在 `DrawingPanel` 构造时补锁一次；④框选页 `box_layout` 行距 8 → 14，与画笔 / AI 页同节奏。
- **测试**：`tests/test_brush_thickness.py` 断言改为「无后缀」；`tests/test_ai_inpaint_panel.py` 补 2 例（滑条与下拉同列 / 面板按钮有内边距，含 `DrawingPanel` 类名一致性守卫）。

**涉及文件：** `ui/drawingpanel.py`、`config/stylesheet.css`、`tests/test_brush_thickness.py`、`tests/test_ai_inpaint_panel.py`、`docs/基础速查/设置面板排版思路.md`

---


