# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-08-23

### 右侧面板排版重构 + 移植收尾审计与文档清理

**问题/需求：** 上游移植后右侧面板拥挤、控件容器语言混杂（GroupFrame 行胶囊/QGroupBox/折叠胶囊/平铺四套并存）；用户确认整体重构方向（全局字体样式→基本选项→拓展样式→文本框输入区）并验收"姑且可用"。随后对 v1.5.12 移植计划做全量执行审计，并清理 docs 里堆砌的过程文档。

**改动要点：**

- **FontFormatPanel 三区重构**（`ui/text_panel.py`）：A 全局字体样式预设（`textstyle_panel.view_widget` 胶囊）→ B 基本选项平铺（字体/样式/字号行、图标行加 `fmtGroupSeparator` 细分隔线、测量行）→ C 拓展区（text_style_btn + 变换折叠 + 注解折叠胶囊）。去 GroupFrame 行胶囊与注解区 QGroupBox，统一"胶囊标题 + 平铺内容"一种容器语言。
- **注解折叠胶囊**：`AnnotationFormatGroup` 改小节平铺（Ruby 行 + 连字/onum 2×2），整组包进 `ViewWidget`——默认收起（`pcfg.expand_annotation_panel`），全局模式整体隐藏无死区，块内有激活注解时标题加 "•" 标记。
- **ExpandLabel 串写修复**（`ui/custom_widget/view_panel.py`）：删 `mousePressEvent` 里硬编码的 `pcfg.expand_tstyle_panel = self.expanded`——任意胶囊点击都会覆盖文本样式面板折叠态；持久化统一走 `config_expand_name` 机制。
- `ui/scenetext_manager.py` 去掉 CollapsibleSection 外壳（format_section），formatpanel 直接入 formatOuterFrame。
- **移植审计**：节点 0-5 + 引擎 2a-2d + 3 逐项符号核对全部通过（含 `_text_overflows` 前置、punctuation_position 不参与渲染、font_weight 本地 int、休眠上游面板现状）；verify + 5 个移植测试套 51 例 + `i18n --ci` 零硬编码零缺失。
- **文档清理**：删 `移植规划_上游v1.5.12.md`、`移植交接_节点2a文件落地.md`、`上游v1.5.11之后提交调研.md` 三份过程文档，蒸馏为唯一存档 `docs/技术实现/上游v1.5.12移植_完成记录.md`（决策摘要/节点→提交→落点/六条持久分歧/回归资产）。

**排障记录：** 连字下拉 1×4 排布超出 348px 面板内宽（组合框 sizeHint 126px × 4 ≈ 534px，文字截断），改 2×2 网格；离屏预览需 `setFixedWidth` 才能复现主窗口强制宽度的挤压效果。

**涉及文件：** `ui/text_panel.py`、`ui/custom_widget/view_panel.py`、`ui/scenetext_manager.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_annotation_controls.py`、`tests/test_configpanel_node3.py`（新增）、`tests/test_pipeline_tcy.py`（新增）、`tests/test_vertical_engine.py`、`docs/技术实现/文本编辑区UI重构.md`（新增）、`docs/技术实现/上游v1.5.12移植_完成记录.md`（新增）、`docs/daily_log.md`

---

### 侧栏图标 + 画布浮层面板（PS 式面板栏，当日三版定稿）

**问题/需求：** 注解折叠胶囊受 348px 宽度约束，草案按 PS 图层面板隐喻定稿为"左缘窄栏 + 浮层"。首版（行号槽迁窄栏 + Qt.Tool 自由浮窗）实机截图暴露四问题：文本输入区不可见、行号错位、浮窗无固定锚、图标无激活态。二版改"镜像覆盖格式区"的停靠式后用户再审提出五点：去掉窄域边框、面板展开到画布区可自由拖拽且不自动关、Edit 模式不收缩单行、三开关按钮回卡片框内、字体/字重下拉定宽但弹层加宽。

**改动要点（三版后终态）：**

- **`ui/panel_rail.py::PanelRail`（新）**：26px 窄栏只挂格式区左缘，`RailLauncherButton` 图标（程序化字形 + 注解角标）；激活态 = CSS 主题色底 + 代码绘白字形/白角标。**窄条透明**：复查后去边框再去底色（去掉 `WA_StyledBackground`，背后格式区透出）。行号槽整套（全局坐标映射/事件过滤去抖/编号点选拖拽）已删。
- **`ui/custom_widget/rail_dock_panel.py::RailDockPanel`（新）**：主窗口内子控件浮层，宿主 `rail.window().centralStackWidget`；展开**硬连接**锚定窄栏左侧 8px（右缘+顶部固定，画布区右缘、不占文本编辑区），**不可拖拽**、左下角 `_ResizeGrip` 拉伸（光标 `SizeBDiag`、斜纹镜像；尺寸下限随内容布局，功能项压不成一线）；**位置补偿**——宿主缩放/窄栏移动经事件过滤器自动重锚，无需重开刷新；只响应图标 toggle/×/Esc 关闭（全局模式保持打开仅置灰）；开合记忆 `pcfg.annotation_dock_open`，位置始终回锚点、尺寸用户调整会话内保持。不用边框/底色，靠 `#RailDockHeader` 标题条底色分层。
- **控件自定义样式**：浮层内部注解控件换成本项目 `ui/custom_widget` 库——`SmallComboBox`（Ruby Type/Position + 连字 4 轴下拉）、`ConfigLineEdit`（Ruby 文本行）、`NoBorderPushBtn`（Apply/Remove）、`SmallParamLabel`（小节标签），与全应用主题一致（`ui/text_panel.py::AnnotationFormatGroup`）。
- **行卡片回归**：`TransPairWidget` 的 `badge_vp`/`badge_drag`/`drag_area`/`accent_bar` 徽章体系从 HEAD 恢复（编号与文本框一体）；保留抽出的 `begin_rows_drag` 共用入口与 fold 传导修复。
- **布局**：`TextPanel` 纵向两行——上行为窄栏 + 格式区（GroupFrame），下行为文本区 GroupFrame：三开关工具栏（Edit/Review | Source | Translation）居中与行卡片列表同框（二版 HeaderGroupFrame 骑跨式已随该控件整体删除）。
- **Edit 不折叠**：`fold_textarea` 映射反转——Edit（勾选）= 完整多行卡片，Review = 单行紧凑；`pcfg.fold_textarea` 默认 False。
- **下拉定宽**：`WidePopupComboMixin`（showPopup 前按最长条目抬弹层视图最小宽）+ 字体/字重框 3:2 布局拉伸（`FontStyleComboBox` 新类），闭合态宽度恒定、弹层完整显示。
- **注解入口**：`install_annotation_launcher(rail)` 懒创建浮层（创建时才能从 rail 解析主窗宿主）；全局模式图标禁用、浮层不关；角标与标题 "•" 语义不变；嵌字页显隐经 `on_textpanel_visibility` 联动。
- 保留双向悬停：行文本框 hover → `_flash_row_item` 点亮画布块描边（不接管常显描边块）。i18n：上下文 FloatingPanel→RailDockPanel（"Close"）。

**排障记录：** ① 首版行号槽错位根因：跨控件 `mapToGlobal` 对齐滚动区兄弟节点在 DPI 缩放/布局挤压下脆弱，修订直接删除该机制；② `QEvent.Type.MouseButtonMove` 不存在（正确为 `MouseMove`），PyQt6 虚函数回调内 AttributeError 逃逸表现为原生崩溃（exit 127 无回溯），bisect + 子类包装 eventFilter 捕获异常才定位；③ offscreen 下 `python -c` 的 stdout 在硬崩溃时随缓冲丢失，诊断须 `-u` 或落文件；④ 未 show 的窗口 resize 事件延迟到首次显示；⑤ `TextEditListScrollArea.pairwidget_list` 是类级共享可变列表，测试多实例需先 clear；⑥ QSizeGrip 只作用于顶层窗口，窗口内子控件浮层需自绘角部把手。

**复查修订（用户再审两轮）：** ① 窄栏去底色（透明窄条）；② 面板缩放主窗错位——补宿主 `Resize` + 窄栏 `Move` 事件过滤器自动重锚；③ 面板向左展开、右下角手柄改左下角生效 + 尺寸下限随内容布局（`_min_size()`）；④ 浮动面板内部控件换自定义控件库（见上）。

**涉及文件：** `ui/panel_rail.py`（新增）、`ui/custom_widget/rail_dock_panel.py`（新增）、`ui/custom_widget/floating_panel.py`（新增后删除）、`ui/custom_widget/group_frame.py`（HeaderGroupFrame 增后又删，净零）、`ui/custom_widget/__init__.py`、`ui/textedit_area.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/mainwindow.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_panel_rail.py`（新增，20 例）、`docs/技术实现/侧栏图标_画布浮层面板实现.md`、`AGENTS.md`

---

### 侧栏收纳收尾：三 dock 入栏 + 文本样式 dock 化 + AI 审计固定策略

**问题/需求：** 侧栏收纳批次（记忆：rail-docks-emphasis-textstyle-transform）收尾提交。面板栏定稿后，把右上角文本格式区剩余的独立交互（着重号/文本样式/变换）一并迁入 PS 式窄栏浮层，实现格式面板瘦身；随后把「AI 自审计」固定成 verify 常规步骤（删除文件的残留引用/死代码复活强制清零），并按规范清理 docs。

**改动要点：**

- **三个新 dock 入栏**（`ui/scenetext_manager.py`）：`install_emphasis_launcher`（着重号样式/位置平铺标记）+ `install_transform_launcher`（变换面板去折叠直接入 dock）+ `install_textstyle_launcher`（不透明度/阴影/渐变三节），与既有注解 launcher 共 4 图标；各 dock 开合记忆加 `pcfg.emphasis_dock_open`/`textstyle_dock_open`/`transform_dock_open`（`utils/config.py`）。
- **文本样式 dock 化替代旧模态**：新增 `ui/text_style_dock.py`——不透明度/阴影/渐变三节进 `RailDockPanel`，拖动实时 preview、松手 commit（`shadow_include_stroke` 项目级开关），全局模式（无选中块）也工作；`ui/shadow_gradient_dialog.py`、`ui/text_advanced_format.py` 两个旧模态对话框退役删除。
- **变换面板重排**（`ui/text_engine/transforms/panel.py`）：控件上下式 + `_relayout_for_width` 按可用宽度挑选列数（FlowLayout 平铺思想替代原固定多列网格），去卡片框/网格列重排/width-sync；头部 Add 下拉 + glyph-slant 左聚不再跨宽居中；`ui/text_engine/transforms/controls.py`（上游 v1.5.12 移植时留存、功能已并入 panel.py 的死副本）删除。
- **姿态/细节**：`clock_dial.py` 增 `compact` 模式（去刻度/度数标签、小尺寸，dock 用）；`rail_dock_panel.py` `_ResizeGrip` 透明化（避免方形覆盖 6px 圆角）；`stylesheet.css` 同步（`RailDockGrip` 透明、`TextTransformCardsFrame`/`TextTransformCardsSeparator` 等）。
- **AI 审计固定策略**（新 `scripts/check_audit.py` + `scripts/audit_registry.json`）：`deprecated` 已删文件不得复活、残留引用须清零（`allowed_mentions` 白名单外）；`suspended` 休眠文件不得被主 UI import；未登记删除仅提示不失败。并入 `scripts/verify.py` 第 3 步；`scripts/check_syntax.py` 不会重复报 deleted file（git 改动集过滤）。
- **文档清理**（docs 12→8 份）：删 `docs/技术实现/反向移植_完整流程.md`（并入规范 §11）、`反向移植_工作进度交接.md`、`快捷键系统.md`（并入 `docs/基础速查/快捷键.md`，动作清单重生成）、`排版技术.md`（陈旧三节）；`ui/scene_textlayout.py` 删除（引擎迁移后零引用旧 fork 布局）并登记；`AGENTS.md` 增"禁止裸符号名 + 删除前登记 deprecated + /audit-docs 技能"三节；`docs/技术实现/文本编辑区UI重构.md` 更新（Zone C 全外迁浮层）、`docs/项目概述.md` 精简。

**排障记录：** check_audit 初版文件名用**裸子串**匹配，`test_annotation_controls.py` 文件名里的 `controls.py` 子串被误判为对已删文件的残留引用（132 处假阳性）——改文件名整词匹配（`(?<![A-Za-z0-9_.])controls\.py`）+ 模块名只认 import 形态（`import controls` / `from .controls import X`，from 分支须后随 `import`，避免 `yield from panel.iter_controls()` 误伤），处理后真残留仅 2 处（panel.py:4 docstring 已改述、1265 为 `iter_controls()` 误报）。另补登记同批次已删 `shadow_gradient_dialog.py`/`text_advanced_format.py`。

**涉及文件：** `ui/text_style_dock.py`（新增）、`ui/scenetext_manager.py`、`ui/text_engine/transforms/panel.py`、`ui/text_engine/transforms/controls.py`（删）、`ui/custom_widget/rail_dock_panel.py`、`ui/custom_widget/clock_dial.py`、`utils/config.py`、`config/stylesheet.css`、`scripts/check_audit.py`（新增）、`scripts/audit_registry.json`（新增）、`scripts/verify.py`、`tests/test_rail_docks.py`（新增）、`tests/test_annotation_controls.py`、`tests/test_panel_rail.py`、`tests/test_strikeout.py`、`tests/test_text_transform_engine.py`、`tests/test_text_transform_ui.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`AGENTS.md`、`docs/daily_log.md`

---

### 项目级大样式 + 自动派生子样式（嵌字样式体系重构）

**问题/需求：** 嵌字实际流程中字体种类固定但字号/颜色/间距每气泡不同——按参数组合建样式则命名困难、样式列表膨胀；末尾想批量改某字体的某参数只能苦哈哈手动跳。经四轮设计问答定稿：大样式（InDesign 段落样式式）+ 子样式（override 补丁自动派生、免命名、自动归并）两级体系，批量改 = 改大样式推平该参数。

**设计决策（用户拍板）：** 子样式同 override 自动合并去重；改大样式参数 = 强制统一（推平后代该参数 override，其他 override 保留）；`(font_family, vertical)` 同为身份键（块上改字体/横竖排自动迁移到对应大样式）；未匹配块进"未分组"+手动提升；预设胶囊区与项目大样式并行互不侵入（另存为预设是唯一交点）；子样式不持久化（纯 diff 派生，块不存样式引用，旧项目零迁移）。

**改动要点：**

- **`utils/base_styles.py`（新）**：数据层全套——`BaseStyle`（身份键 `(font_family, vertical)`、to_dict/from_dict）、`compute_override`（量化 diff，复用签名量化表防浮点噪声）、`discover_style_tree`（身份键归属 → override 聚类 → `BaseStyleNode`/`VariantEntry`/未分组 `StyleEntry` 三层）、`variant_display_name`（ASCII token 自动名：`38px · fg#FF0000`，字号 token 前置、超 4 项截断 +n）、`build_flatten_changes`/`build_variant_changes`（推平/变体批量变更列表，old/new 独立 deepcopy）。`compute_signature`/`discover_styles` 从 fontstyle_manager 迁入并保留 re-export。
- **`utils/proj_imgtrans.py`**：项目 JSON 增 `base_styles` 字段（load 解析 + to_dict 输出）；旧项目无字段时 `ensure_default_base_styles` 以 `pcfg.global_fontformat` 种子注册默认大样式（名 = 字体族名，零硬编码）。
- **`ui/fontstyle_manager.py` 重写**：左列 `StyleTreeWidget`（QTreeWidget 三层树：大样式名+方向徽标+块数 → 变体自动名；未分组根节点不可选仅作分组头；色块 swatch 图标）；右列 `StyleDetail` 三模式——base（改名/推平 Apply/另存为预设/删除大样式）、variant（继承提示+override 摘要+仅作用于该变体块）、sig（原有全量 Apply + 提升为大样式）。推平语义：进入详情时记 baseline 快照，Apply 只提交控件相对 baseline 的量化差异（`_collect_changed`），未动参数不覆盖块上的其他 override；预设 Apply 在 base 模式 = 重定义（全量推平差异字段）。身份键变更（改字体/横竖排）须先收集后更新大样式（否则按新键收集漏掉自家块——已修）。
- **软刷新**：管理器 `set_project` 时挂 `canvas.text_undo_stack.indexChanged` → 300ms debounce `refresh()`（保持当前选中），画布单块编辑实时反映到树。
- i18n：新 context `StyleTreeWidget`（V/H/Ungrouped 等 6 条）+ `StyleDetail` 新增 19 条（含删除确认/提升提示），废弃 "Applied to..." 条目移除，unfinished 3 条补齐。

**排障记录：** ① `self.tr()` 跨行隐式拼接再次漏检（AGENTS.md 既有坑），两条提升提示合并为单行字符串后 orphan 归零；② 变体显示名曾显示 `40.0001px`——聚类按量化值归并但 overrides 存的是首块原始值，`_override_token` 改为量化后格式化；③ `build_flatten_changes` 身份键时序：先 setattr 大样式再收集会按新键扫描漏块，调用顺序固定为 collect→update 并写入 docstring 契约（子代理测试亦证实该隐患）。

**验证：** `tests/test_base_styles.py`（新增，26 例：override diff/量化、归属聚类、round-trip、推平保序、持久化集成）+ 树形化后既有 `tests/test_global_search_fontstyle.py` 18 例全绿；离屏冒烟覆盖三模式切换/改名/推平全链路（跨页块、override 保留、undo 还原、needs_rerender 标记）；全仓 350 例通过（`test_dependency_startup` 1 例为既有基线失败）；verify 六步全绿。

**涉及文件：** `utils/base_styles.py`（新增）、`utils/proj_imgtrans.py`、`ui/fontstyle_manager.py`（重写）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_base_styles.py`（新增）、`AGENTS.md`、`docs/daily_log.md`

---

### agent 翻译实机排查：Context Translation (beta) 整项目分批致"只选几页仍译数百框"

**问题/需求：** 用户实机测试翻译（配置：translator=LLM_Agent_Translator、检测/OCR 关、翻译开），发现 Run 对话框即便只选几页，翻译进度窗口仍显示约三百个文本框的文本、单次等待过久，怀疑 agent 有 bug。用户离开，授意自行排查并按思路解决、写日志回溯。

**根因定位：** 用户实际走的是 Run 对话框的 **Context Translation (beta)**（`ContextBatchTranslator`），而非 agent——工程里唯一向进度窗口写文本的就是它的 `_status`（`_ctx_status` → `translate_bar.updateProgress(value, msg)`，`ui/mainwindow.py` + `ui/custom_widget/message.py::TaskProgressBar`）。其 `set_project → _auto_configure` 按字符预算（4000）对**整项目**分批：实测 94 页项目第一批 ≈ 页 1-46 = **298 块**，正是"近三百"。所选页范围只约束管线遍历，**未约束分批**——选区只要含第一批首页（如选 1-3 页），整批 298 块就被装进单次 LLM 请求。agent 本身按页切任务（每页任务块数 = 该页块数，历史注入由 `llm_prior_context_token_budget` 预算裁剪，页 47 实测 30 页/167 块），与症状不符，排除。

**修复（scoped batching）：** `modules/translators/context_batch.py` 增 `pages_scope`（= Run 对话框所选页列表）：
- `_scope_keys` 属性 = 范围内项目页（不存在的名字过滤；None = 全部，向后兼容）；
- `_auto_configure` 分批只扫范围内页面；`_contextual` 按范围内索引查批、目标收集只含范围内页；范围外页面防御性退化为直译（`translate_textblk_lst` 同步加守卫）；
- 上下文参考窗口仍按全项目索引（可引用范围外的已完成页，保跨页一致性）。
`ui/mainwindow.py`：`page_filter` 计算提前到 ContextBatch 构造之前，以 `pages_scope=page_filter` 传入（删除原滞后重复计算块）。

**验证：** 新增 `tests/test_context_batch_scope.py` 6 例（scope keys / 缺名过滤 / 默认全量 / 分批边界 / TRANSLATE THESE 段只含范围内页 / 范围外直译）；agent 既有 32 例 + 新 6 例全绿；verify 全绿（含 mainwindow 冒烟）。同项目实测：选 1-3 页时单次请求 298 块 → **12 块**。

**涉及文件：** `modules/translators/context_batch.py`、`ui/mainwindow.py`、`tests/test_context_batch_scope.py`（新增）、`docs/daily_log.md`

---

### 已知体验问题登记：长翻译无中间反馈，用户无法区分「在翻译」与「模型出错」

**问题：** 实机验收 scoped batching 时确认翻译质量过关、选页正确，同时暴露一个体验问题——译文要等模型整段输出完成后才一次性填入文本块（beta 与 agent 皆然），篇幅较长时用户盯着空框无从判断是仍在翻译还是模型卡死/出错。

**登记（未修）：** 归入阶段 5 质量护栏（F 类：debug 日志 + 状态栏轮次显示）一并处理，届时同步实现渐进反馈（每轮/每块完成即更新状态栏或进度提示）。决策：不做流式逐字渲染（改动渲染链路、收益低），以"轮次/块粒度"的状态反馈为主。

---

### 阶段 3 单框翻译策略：修 page_key + single_blk_translate_mode + UI 入口

**问题/需求：** 按设计方案推进阶段 3。`ui/module_manager.py::_blktrans_pipeline` 的 `current_page_key` 从未赋值（恒 None），单框/选区翻译定位不到所在页；且单框翻译缺策略分化——现状一律整 agent（8 轮），没有"单条直译"与"轻量 agent 注入本页"的可切换档位。

**改动要点：**

- **page_key 修复**：`runBlktransPipeline` 增 `page_key` 参数存到 `self.current_page_key`；`ui/mainwindow.py::translateBlkitemList` 以 `self._blktrans_at_page`（`imgtrans_proj.current_img`，即页名/页 key）传入。修好后两种档位都能定位页面。
- **配置**：`utils/config.py` 增 `SingleBlkTranslateMode`（plain/context）+ `ModuleConfig.single_blk_translate_mode`（默认 plain，`__post_init__` 坏值兜底）。
- **agent 行为**（`modules/translators/trans_agent.py`）：单源块调用按档位分流——`plain` → `super().translate(project=None, page_key=None)` 走父类直译路径（不注入页面上下文，术语表仍生效）；`context` → `_run_agent_task(block_mode=True)`：`max_turns=2`（设计 §4.4）+ 注入当前页其余块快照（`modules/translators/agent/prompts.py::build_page_context_snippet`，任务块自身按原文排除，整页未译/无其余块时为空）。多块任务不受该配置影响，始终完整 agent。
- **UI 入口**：Run 对话框 Context 区新增 "Block Translate" 行（plain 直译 / context 上下文，写 `pcfg.module.single_blk_translate_mode`），i18n 三条入库并编译 qm。
- **清理 `tests/ui` 阴影坑**：`tests/` 下遗留夹具目录 `tests/ui/`（独立脚本 `text_rendering.py`，已无引用）会阴影 repo 根 `ui` 包——pytest 收集 `tests/*.py` 会把 `tests/` 插到 `sys.path[0]`，多文件合跑时后续文件 `import ui.custom_widget` 解析到 `tests/ui` 报错。已重命名为 `tests/offscreen_ui/`（脚本内 3 层 dirname 定位 APP_ROOT 不受影响），并更新 `test_pie_menu_dismiss.py` 的过时注释。

**验证：** `tests/test_agent_single_block.py`（新增 8 例：plain 走直译不启 agent / plain 不带页面上下文 / context 启 agent block_mode / 多块恒完整 agent / max_turns=2 + 页面上下文注入且不串邻近页 / 多块用配置轮数 / 配置默认值与坏值）+ `test_agent_translator.py`（+4 例 `build_page_context_snippet`）；三套件 + scope/pie 60 例合跑全绿（顺带验证 ui 阴影修复）；verify 六步全绿。

**涉及文件：** `utils/config.py`、`ui/module_manager.py`、`ui/mainwindow.py`、`modules/translators/trans_agent.py`、`modules/translators/agent/prompts.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_agent_single_block.py`（新增）、`tests/test_agent_translator.py`、`tests/ui/` → `tests/offscreen_ui/`（重命名）、`tests/test_pie_menu_dismiss.py`、`docs/daily_log.md`

---

### 阶段 3 实机修正：page_key TypeError + 单框策略入口迁到设置面板

**问题/需求：** 实机回归暴露两处阶段 3 缺陷：① 单框/选区翻译直接崩溃 `ModuleManager.runBlktransPipeline() got an unexpected keyword argument 'page_key'`；② "Block Translate" 档位开关放在运行窗口里语义不清——画布右键单框翻译根本不经过运行窗口，该入口等于摆设，应作为全局行为设置放进设置面板。

**改动要点：**

- **TypeError 根因**：`ui/module_manager.py` 有两个 `runBlktransPipeline`——`ImgtransThread`（行 584，阶段 3 的 page_key 改动落在这里）与 `ModuleManager`（行 1703，mainwindow 实际调用对象）。mainwindow 传 `page_key` 打到 ModuleManager 版本，签名没有该参数直接抛 TypeError。修复：`ModuleManager.runBlktransPipeline` 增 `page_key: str = None` 并转发给 `imgtrans_thread.runBlktransPipeline(..., page_key=page_key)`，worker 侧 `current_page_key` → `_blktrans_pipeline` → `translate_textblk_lst` 链路不动。
- **入口迁移**：删掉运行窗口 Context 区的 "Block Translate" 行（`ui/mainwindow.py`），在设置面板 `ui/module_parse_widgets.py::TranslatorConfigPanel` 新增 "Single-Block Translation" 章节（Mode: plain 直译 / context 上下文），绑定 `pcfg.module.single_blk_translate_mode`；沿用 API Profile 章节的模式——仅当选 `LLM_Agent_Translator` 时显示（`_refresh_single_blk_section` 挂在 `updateModuleParamWidget`）。
- **i18n**：ts 里 MainWindow context 的 Block Translate/plain/context 三条迁到 `TranslatorConfigPanel` context（新增 Single-Block Translation/Mode/plain/context），qm 重编译。

**验证：** `ModuleManager.runBlktransPipeline` 签名核对（`inspect` 含 page_key）；offscreen 面板构造 + 可见性切换（agent 显示 / 非 agent 隐藏，`isHidden` 断言）；81 例测试合跑通过；verify 六步全绿。

**涉及文件：** `ui/module_manager.py`、`ui/module_parse_widgets.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/daily_log.md`

---

### 阶段 4 债务清理：删 beta + 历史窗口机制 + 死配置 + glossary 时序

**问题/需求：** 按设计方案 §11/§13 完成阶段 4：删掉 beta `ContextBatchTranslator` 与运行窗口入口（agent 为唯一 LLM 翻译路径）；直译路径降为回退、历史散文注入由 agent 编排取代；删死配置与历史窗口状态机；修 glossary 路径时序 bug。

**改动要点：**

- **删 beta**：`modules/translators/context_batch.py` 登记 `scripts/audit_registry.json` deprecated 后删除（allowed_mentions：设计方案/现状调研）；运行窗口删除 "Context Translation (beta)" 勾选耦合（`ctx_trans_cb` 联动、上下文帧显隐门控、`_ctx_batch_restore` 换入还原、`pcfg.context_translation_debug_log` 两处消费）；删 `tests/test_context_batch_scope.py`；`ui/glossary_dialog.py`（CustomGlossaryDialog，唯一入口即被删的 Custom... 按钮）一并登记删除；`modules/glossary_extractor.py` 注释去引用。
- **历史窗口状态机废弃**（`modules/context/history.py`）：仅保留 `RequestContext` 快照模式，删 HistoryWindow/HistoryWindowKey/HistoryPage/RenderedHistoryPage/ContextAction/ContextDiagnostic/ContextReason/eligible_history_for_request/recover_context_length/window_rebuild_reason/HISTORY_LOW_WATER_RATIO；`modules/translators/trans_llm_api.py` 直译路径不再构建历史窗口——`_snapshot_request_context` 缩为纯术语表快照，删 `_snapshot_history_page`/`_render_history_page`/`_format_narrative_block` 与 `_system_prompt` history_rule、`_assemble_request` 历史散文段、`_translate` 的越限恢复与窗口提交；`commit_history_window`/`project`/`page_key` 参数保留为调用方兼容与 usage 日志。
- **llm_translate_context 简化**（`utils/config.py`）：删 `TranslateContext`/`LLMTranslateContext` 枚举，`translate_context` 字段删除，`llm_translate_context` 改 `bool`（默认 True = 原 history 语义，坏值兜底）；`modules/translators/trans_agent.py` 的 `build_history_snippet` 注入改受开关约束（关闭则不注前页历史，单框 context 的本页块注入不受影响）；运行窗口 "LLM Context" 下拉（page/+history）改为 "Inject Prior-Page History" 勾选 + Token Budget 联动显隐。
- **glossary 时序修复**（设计 §11 #4）：根治删 `_clear_glossary` 在 `dialog.finished` 清空 `pcfg.module.llm_glossary_path` 的行为——Browse 写入持久生效，译前就绪状态（路径+勾选）可见；同时补上 `glossary_mode_combo` 的 pcfg 回写（此前只有 beta 消费其 currentData，对 agent/回退路径是永不生效的摆设）。
- **文档同步**：现状调研 §4.3-4.6 顶部加阶段 4 横幅，§5/§11 债务对照表按处置结果改写（已删文件/符号改裸名散文，沿用 scene_textlayout 惯例）；设计方案 §7/§11/§13 同步；项目概述文件树去 glossary_dialog.py。
- **i18n**：ts 删 Context Translation (beta)/LLM Context/page/+history/textblock/Custom... 与 CustomGlossaryDialog 上下文，新增 Inject Prior-Page History，qm 重编译。

**验证：** verify 六步全绿（audit 登记表 10 居删 / 2 休眠）；全量测试 399 通过（唯一失败的 `test_dependency_startup` env 子进程断言为 08-22 已记录的基线，launch.py 未动）；`LLM_API_Translator`/`AgentTranslator`/`RequestContext` 导入冒烟通过。

**涉及文件：** `modules/translators/context_batch.py`（删）、`tests/test_context_batch_scope.py`（删）、`ui/glossary_dialog.py`（删）、`modules/context/history.py`、`modules/translators/trans_llm_api.py`、`modules/translators/trans_agent.py`、`modules/glossary_extractor.py`、`utils/config.py`、`ui/mainwindow.py`、`scripts/audit_registry.json`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/翻译agent化_设计方案.md`、`docs/技术实现/翻译管线现状调研.md`、`docs/项目概述.md`、`docs/daily_log.md`

---

### 实机排查"只选几页却翻译几百框" + 阶段 5 质量护栏与轮次反馈

**问题/需求：** 用户实机测试 agent 翻译反馈：只选几页却"填充几百个框"，翻译进度窗口显示近三百个文本框的文本，等待太久无法判断是否正常。同时批准阶段 5：E 类质量护栏（空译文/译文=原文/术语残留检测与打回补译）+ F 类可观测（每轮状态显示 + debug 日志合并）。

**排查结论：** 页过滤链路逐层核对无 bug——Run 对话框 `page_filter` → `on_run_imgtrans` → `ModuleManager.runImgtransPipeline` → `ImgtransThread._imgtrans_pipeline`（`pages_to_process` 限 `pages_to_iterate`）→ 每页 `translate_textblk_lst(blk_list, page_key=imgname)`，并行翻译队列同样只推选中页；agent 写面被 `valid_ids` 封闭集封死，只读工具无法越权。真因有二：① 阶段 4 起 `llm_translate_context` 默认开，`build_history_snippet` 只按 4096 token 预算裁，短块页会把预算吃满——约等于塞进 ~300 个旧块参考文本（"近三百个文本框的文本"即此），每次请求又大又慢；② 进度窗口按页百分比更新，agent 每页数分钟无任何中间反馈（"无法确定是否有问题"）。

**改动要点：**

- **历史注入页数上限**（`modules/translators/agent/prompts.py`）：`build_history_snippet` 增加 `max_pages`（默认 `_MAX_HISTORY_PAGES = 3`），页序向前取、页数上限与 token 预算双重裁剪——保证"邻近页"语义，杜绝几十页短块把预算吃满；超出部分由探索工具按需深挖。
- **E 类出口护栏**（`modules/translators/agent/validator.py` + `loop.py`）：新增"译文=原文"（纯数字/单字符豁免，拟声词豁免名单留待后续）与"术语残留"（命中术语 src≠dst 且本块原文含该词、译文仍保留原词）两项检测，**先警告后打回**——`validate_submission` 返回 `(accepted, feedback, warnings, newly_warned, rejected_ids)`，loop 维护 `warned_ids` 跨轮累计，再犯则打回并把该 id 从已累积结果中移除（回 missing 重新请求，整单被拒时同样移除），修复了"警告轮接受的坏条目让缺失检查误判已覆盖"的闭环缺陷；无效 id 反馈截断到 10 条防上下文爆炸。
- **F 类轮次反馈**（`ui/module_manager.py` + `modules/translators/trans_agent.py` + `modules/translators/agent/loop.py`）：`run_agent_task` 增 `status_cb(turn, tool_names, usage)` 每轮回调；`AgentTranslator.set_status_callback` 接线到 `TranslateThread.agent_status` 信号，`ModuleManager.on_agent_turn_status` 把"第 X 页 · agent 第 N 轮: read_pages…"打到进度窗口 translate_bar 消息（无状态栏控件，进度窗口即用户盯的反馈面）。
- **debug 日志合并**：`context_translation_debug_log` → `agent_translation_debug_log`（`utils/config.py`），`utils/debug_log.py` 前缀改 `agent_translation_*`、`start()` 幂等（会话内单文件）；agent 每轮状态在开关开启时写日志。
- **测试**：validator 新增译文=原文/术语残留/恒等术语豁免/纯数字单字符豁免用例；loop 新增先警告后打回端到端（两轮再犯打回、修正后通过）与 status_cb 每轮调用用例；`build_history_snippet` 页数上限用例（默认 3 页、显式放宽全装）。
- **i18n/文档**：ts 新增 `ModuleManager` 两条（"Page %1 · agent turn %2: %3"/"waiting for model"），qm 重编译；设计方案/现状调研的 debug 字段行更新为 `agent_translation_debug_log`。

**验证：** verify 六步全绿；全量测试 407 通过（唯一失败的 `test_dependency_startup` env 子进程断言为 08-22 已记录的基线，launch.py 未动）。

**涉及文件：** `modules/translators/agent/prompts.py`、`modules/translators/agent/validator.py`、`modules/translators/agent/loop.py`、`modules/translators/trans_agent.py`、`ui/module_manager.py`、`utils/config.py`、`utils/debug_log.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_agent_translator.py`、`docs/技术实现/翻译agent化_设计方案.md`、`docs/技术实现/翻译管线现状调研.md`、`docs/daily_log.md`

---

### 文本输入区删除 Edit/Review 模式切换（常驻左侧编号 + 拖拽列）+ 拖拽重排误显选中修复

**问题/需求：** 编辑/审阅两套样式的切换（工具栏 Edit/Review 按钮 + `fold_textarea` 配置）语义多次被重构反转（config 默认 False、启动又强制 Edit），用户确认直接删除该功能与 UI，常驻"左侧编号徽章 + 22px 拖拽列"样式、文本框恒为整块多行；同批排查并修复拖拽重排时其他卡片闪现选中样式的视觉问题（悬停与选中同用 `@accentPrimary20` 底色，QDrag 期间指针扫过的卡片会闪烁成"被选中"的样子）。

**改动要点：**

- **删除模式切换**（`ui/scenetext_manager.py`、`ui/mainwindow.py`、`utils/config.py`）：删工具栏 `foldTextBtn`（Edit/Review 按钮）、`MainWindow.fold_textarea` 方法与启动强制 Edit、`pcfg.fold_textarea` 配置字段；工具栏只留 Source | Translation 两开关（拉伸索引顺移）。
- **行卡片常驻样式**（`ui/textedit_area.py`）：删 `TransPairWidget.setFold/_apply_fold`、viewport 徽章 `badge_vp`（含 hover 淡出与 `_repos_badge_tr`）、`SourceTextEdit.setFold`（本就只设整块多行）、`TextEditListScrollArea.setFoldTextarea`；拖拽列徽章 `badge_drag` 更名 `badge` 常驻显示（属性 `drag_area` 与 22px 拖拽列不变）。
- **CSS 扁平化**（`config/stylesheet.css`）：`QLabel#TextBlockIndexBadge` 基规则改为透明底 + 主题文字 + 13px（原 `[folded="true"]` 变体并入），删 `[hovered]`/`[folded]` 两条变体规则。
- **拖拽时卡片误显选中修复**：`QDrag.exec` 期间 `TextEditListScrollArea._set_dnd` 给全部卡片置 `dnd` property，CSS `TransPairWidget[dnd="true"]` 中性底色压制悬停/选中同色闪烁（真选中仍有 `[dnd="true"][checked="true"]` 保持 `@accentPrimary20`）；顺带修 `handle_drag_pos` 首次调用对末卡片的多余 polish。
- **i18n/测试/文档**：ts 删 TextPanel 上下文 Edit/Review 条目（qm 重编译）；`tests/test_panel_rail.py::TransPairWidgetTest` 改为常驻样式断言（无 `setFold`/`badge_vp`）；`docs/技术实现/侧栏图标_画布浮层面板实现.md` 的 "Edit/Review 都不折叠" 节改为常驻说明。

**验证：** verify 六步全绿（语法 25 文件、文档、审计、i18n 仅既有孤儿告警、qm 编译、冒烟通过）；`tests/test_panel_rail.py` 跑通。

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`、`ui/mainwindow.py`、`utils/config.py`、`config/stylesheet.css`、`tests/test_panel_rail.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/侧栏图标_画布浮层面板实现.md`、`docs/daily_log.md`

---

## 2026-08-22

### 上游 v1.5.12 移植节点 2a 收尾提交推送 + 节点 2b 竖排双引擎

**问题/需求：** 完成文本引擎迁移的竖排环节——2a（文件落地 + 渲染入口切换）收尾并提交；随后按规划推进节点 2b（竖排双引擎 + 竖排特性），把 fork 竖排的三个排版设置与縦中横/半宽括号行为接回引擎布局。

**改动要点：**

- **节点 2a 聚合提交 `c097b41` 并推送（用户手动）**：阶段一 26 文件落地 + `editing/upstream_commands.py` + i18n 94 条补齐（qm 1284 条）+ 渲染入口切换（`ui/textitem.py` 改为引擎 TextBlkItem 的 fork 兼容子类，引擎补 `vertical_rotation_chars` 等兼容面）+ 节点 4b 重排闪退修复聚合为一个原子提交。
- **节点 2b 竖排三设置接通引擎布局**（`ui/text_engine/vertical_layout.py`）：构造读 `pcfg` 默认补 `punctuation_position` / `tatechuyoko_threshold` / `halfwidth_jp_corner_brackets` 三成员 + `setPunctuationPosition` setter——configpanel 三处 hasattr 防护调用自动接通，UI 零改动。标点偏移分支按 Simplified（右上/右对齐）/Traditional（居中）切换：`centers_vertical_glyph` 的 PAUSEORSTOP 改随 `punctuation_position`（修复 2a 切换后竖排标点从右上变居中的回归），ALIGNCENTER 补 Simplified 右对齐分支。
- **縦中横自动检测注入**：移植 fork `find_tatechuyoko_runs`（`[A-Za-z0-9]+` run 长度 ≤ threshold）到引擎，`layoutBlock` 中把检测 run 注入注解驱动的 `text_combine_ranges`（与显式 `<tcy>` 注解不重叠），复用引擎 setNumColumns / tate_chu_yoko 渲染。
- **半宽角引号紧凑**：`layoutBlock` 的 `needs_vertical_rotation` 分支加 `PUNSET_CORNER_BRACKET` 保持字形宽度 advance；`updateDrawOffsets` 旋转分支移植 fork 的 opening-mark 上移修正。
- **清理项**：`ui/text_engine/effect_renderer.py` clone-stroke 路径改实例化引擎布局（竖排构造后设三属性，横排用引擎 `HorizontalTextDocumentLayout`），不再引用 fork `scene_textlayout` 布局类；`ui/text_engine/rendering/glyph_slant.py` TYPE_CHECKING 注解改 `..layout.SceneTextLayout`。
- 新增 `tests/test_vertical_engine.py` 13 例：`find_tatechuyoko_runs` 阈值语义、pcfg 默认值、`setPunctuationPosition` 重排触发、`centers_vertical_glyph` 随 Simplified/Traditional、TCY 注入/禁用/不与注解重叠、全特性竖排渲染冒烟。

**排障记录：** 测试两处期望笔误（连续 run 整段判定、`ab 12` 双 run）已修正；`test_dependency_startup` 1 例失败属基线（launch 环境准备子进程断言，与本次改动无关）。

**实机审查修正（同日）：** 用户实机反馈"竖排全角符号变右对齐"——初版把引擎 CLREQ Mainland 右上分支错误接给了 Simplified，导致全角句读（`PAUSEORSTOP`）右对齐；核 fork 布局确认其句读**从不右对齐**（x 居中 + y 顶部，`punctuation_position` 只影响间隔号 `ALIGNCENTER`）。已回退：`centers_vertical_glyph` 的 PAUSEORSTOP 恢复跟随 `standard_vertical_roman_alignment`（默认 True 居中）、删除 ALIGNCENTER Simplified 右对齐分支；`punctuation_position` 成员与 `setPunctuationPosition` 保留（configpanel hasattr 兼容），但不再参与竖排渲染。縦中横/半宽括号/紧凑标点不受影响，测试断言同步更新，13 例 + verify 全绿。

**涉及文件：** `ui/text_engine/vertical_layout.py`、`ui/text_engine/effect_renderer.py`、`ui/text_engine/rendering/glyph_slant.py`、`tests/test_vertical_engine.py`（新增）、`docs/daily_log.md`

---

### 节点 2b 提交推送 + 节点 2c 注解层：主 UI 注解入口初版

**问题/需求：** 用户验收 2b 后授权提交并继续推进；按规划进入节点 2c（Ruby/着重号/连字/onum/手动 TCY/字距 注解层）。探索确认：annotations 字重适配（引擎局部 `font_weight.py` shim）与渲染接通（horizontal/vertical 布局均已接 ruby/emphasis 绘制）在 2a 已完成，**缺口是主 UI 无 Ruby/着重号/TCY/连字/onum 入口**（引擎 `formatting/panel.py` 面板存在但未被主窗口使用，`editing/manager.py` 内嵌的 TextPanel 未接入 mainwindow）。

**改动要点：**

- **节点 2b 提交 `c19a5a6`**（用户验收后）：竖排双引擎三设置接通 + 縦中横注入 + 半宽角引号 + clone-stroke 改引擎布局 + 实机审查修正（全角句读恢复居中）聚合为原子提交，已授权提交（用户自行推送）。
- **`ui/text_panel.py` 增补 `AnnotationFormatGroup` 初版控件**（保持 fork 面板形态）：着重号（样式/位置下拉）、Ruby（类型/读音/位置 + 应用/移除）、縦中横（QFontChecker）、连字（common/discretionary/contextual 三下拉）、旧式数字（onum 下拉）。信号按名分发到引擎 `TextBlkItem` 的 `setEmphasis`/`setRuby`/`setTateChuYoko`/`setLigatureAxis`/`setOldstyleNums`（走文档级 undo）；`_sync_annotation_controls()` 在 `set_textblk_item` 切换时从 `emphasis_values`/`ruby_editor_values`/`tate_chu_yoko_enabled`/`ligature_axis_value`/`oldstyle_nums_value` 读回（QSignalBlocker 防回环），无选中块（global mode）时禁用。
- **Ruby 无选区防护**：`_on_annotation_changed` 捕获 `RubyValidationError`（无选区/读音空/与 TCY 重叠）→ `QMessageBox` 提示，避免面板崩溃。
- **i18n**：`AnnotationFormatGroup` context 14 条 + `FontFormatPanel` context 1 条（Ruby 对话框标题）补齐 ts，qm 1299 条。
- 新增 `tests/test_annotation_controls.py` 11 例：item 注解 setter/读回端到端（emphasis/tcy/ruby+remove/ligature/onum，Ruby 需先 `startEdit` + 选区）、group 信号载荷与 `set_*` 恢复不发声、面板路由（TCY 与 Ruby 互斥用独立 item）。

**排障记录：** `set_*` 恢复 helper 初版未 blocker 子控件导致发信号（改按控件 `QSignalBlocker`）；Ruby 无选区时引擎抛 `RubyValidationError`（面板捕获提示；测试先进编辑态选区）。

**另开对话修复后统一提交 `723f7f9`：** 竖排/横排编辑选区绘制缺口——`ui/text_engine/rendering/glyph.py::draw_slanted_line` 补齐 v1.5.12 的 `background_overlays`/`horizontal_shifts` 参数（`_split_paint_spans` 按 shift 边界切分 paint span），否则竖排选区背景传参抛 TypeError；`ui/textitem.py` 的 `_text_overflows` 初始化移到 `super().__init__` 之前（引擎 init 首次布局溢出会经 `on_document_enlarged` → `set_size` 回读未初始化属性）。`tests/test_vertical_engine.py` 补竖排/横排编辑选区渲染 2 例，`tests/test_annotation_controls.py` 扩至 16 例。

**涉及文件：** `ui/text_panel.py`、`ui/text_engine/rendering/glyph.py`、`ui/textitem.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_annotation_controls.py`（新增）、`tests/test_vertical_engine.py`、`docs/daily_log.md`

---

### 节点 2d 管线联动：auto TCY 接线 + 西文竖排对齐传播

**问题/需求：** 按规划完成节点 2d（管线联动）。探索确认：fork 管线编排在 `ui/module_manager.py`（上游 io_thread.py 的对应物），每页收尾在 `ui/mainwindow.py::on_pagtrans_finished`；`pipeline_formatting.py`（2a 落地）此前无任何消费者；fork 无上游 `_render_only` 机制，TCY 守卫可简化为 `enable_translate`。

**改动要点：**

- **每页管线收尾 `on_pagtrans_finished`**：格式覆盖循环后内联 `apply_auto_tate_chu_yoko(blk_list, pcfg.auto_tate_chu_yoko)`——竖排块数字/字母 run 注入 TCY 到 `block.rich_text`，随页面重建 item（`updateSceneTextitems`/`setCurrentRow`）生效；并补 `blk.fontformat.standard_vertical_roman_alignment = gf.standard_vertical_roman_alignment` 传播（节点 1 新增字段此前未接管线）。
- **`AutoTateChuYokoThread` 接线**（照上游 mainwindow 形态）：`setupThread` 建线程 + 进度框，`progress_changed`→进度框、`processing_finished`→`on_auto_tate_chu_yoko_processing_finished`（changed 块 id 集合 → 对应 item `load_rich_text_html` 重载 + `canvas.setProjSaveState(True)`）、进度框 stop→`request_stop`、`closeEvent` 停止线程；新增 `apply_auto_tate_chu_yoko_to_project()` 整项目后台应用入口（设置面板按钮待节点 3）。`on_imgtrans_progressbox_showed` 改 sender 优先定位，TCY 进度框与 RUN 进度框共用；`ui/custom_widget/message.py::ProgressMessageBox` 补 `fit_to_content`/`show_fitted`。
- 新增 `tests/test_pipeline_tcy.py` 9 例：apply 语义（竖排数字注入/横排不变/禁用/长度阈值/无匹配字符/字母开关/既有 rich_text 保留格式）+ 线程跨页应用（跨线程信号 `processEvents` 冲刷）+ 进度框 show_fitted。

**验证：** verify 全绿（含 mainwindow 启动链冒烟）；三个引擎测试套件 40 例全过。

**涉及文件：** `ui/mainwindow.py`、`ui/custom_widget/message.py`、`tests/test_pipeline_tcy.py`（新增）、`docs/daily_log.md`

---

### 节点 3 UI 整理：设置面板三配置项 + 快速插入自定义字符

**问题/需求：** 按规划完成节点 3（UI 整理）。探索确认：格式化面板整合实际已在 2c 完成（fork 保留自有 `FontFormatPanel` 形态，注解层已接入；上游 `formatting/panel.py`/`advanced.py` 仅参考不并入，字重控件保留我方）；节点 1 备好的三个 pcfg 键此前无 UI。本轮落地设置面板 + 快速插入。

**改动要点：**

- **设置面板 Text formatting 区**（`ui/configpanel.py`，两列表单形态）：
  - **自动直排内横排**：开关 + Apply 按钮同行，选项（Maximum Run Length / Numbers / Letters / Additional Characters）缩进分组、主开关关闭时整体隐藏；槽函数直写 `pcfg.auto_tate_chu_yoko.*`；Apply → 新增 `apply_auto_tate_chu_yoko_requested` 信号 → mainwindow `apply_auto_tate_chu_yoko_to_project()`（节点 2d 已接线，设置按钮补上即闭环）。
  - **紧凑标点** checkbox → `pcfg.compact_vertical_punctuation_spacing` + `_apply_compact_punctuation_settings()`（引擎 per-layoutBlock 读 pcfg，仅需对现有 item `reLayout`）。
  - **快速插入字符** line edit → `pcfg.quick_insert_characters`。
- **快速插入**（`ui/quick_symbol_dialog.py`）：`QuickSymbolDialog` 追加 Custom 分组，渲染 `pcfg.quick_insert_characters`（去空白字符），交互形态不变（固定分组 + 自定义分组）。
- **i18n**：ConfigPanel context 12 条 + QuickSymbolDialog context 1 条（Custom→自定义），qm 1313 条。孤儿 173→174（`Custom` 经 `self.tr(group_name)` 间接调用，与 Quotes/Other 同类已知噪音）。

**排障记录：** ts 插入脚本按 4 空格 `</context>` 定位 context 结尾，但 ConfigPanel 的 `</context>` 顶格无缩进 → 12 条消息误入 DependencyDialog context，i18n_check 报 MISSING；改用"context 内最后一条消息"作锚点重插（先 `git checkout` 恢复 ts）后 12 条全部命中。

**涉及文件：** `ui/configpanel.py`、`ui/quick_symbol_dialog.py`、`ui/mainwindow.py`（信号连接）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_configpanel_node3.py`（新增）、`docs/daily_log.md`、`docs/技术实现/移植规划_上游v1.5.12.md`

---

### 节点 3 审查修正：设置面板归组重排 + 注解区对齐上游图标/排版

**问题/需求：** 用户审查节点 3：① 设置面板新项与原设置混排、部分放置意义不明；② 右侧文本样式区新移植的注音等功能无图标，要求 UI 排布暂对齐上游（用上游图标和排版）。

**改动要点：**

- **设置面板 Typesetting 页重排**（`ui/configpanel.py`）：新增 **Vertical Text** 章节头，竖排专属设置归组（Punctuation Position / Compact punctuation / 半宽「」『』+子项 / Vertical Latin-Digits Length / Auto TCY+选项），不再与 Font Exclusion / Max Font Size 混排；Quick insert characters 上移置 Text Format Presets 之后（对齐上游"字体格式区 → 快速插入 → 竖排组"相对顺序）。
- **注解区对齐上游**（`ui/text_panel.py`）：
  - **着重号** → 移植上游 `EmphasisToolButton`（程序化绘制 'あ'+选中标记图标 + 菜单箭头；MenuButtonPopup 菜单选 10 种标记样式 + 4 种位置；勾选即生效、取消勾选=清除），进 `FormatGroupBtn` 的 B/I/U 行；`emphasis_changed` → 面板 `_on_annotation_changed("emphasis", …)`。
  - **縦中横 + Standard Vertical Roman Alignment** → 竖排行图标 QFontChecker（`FontTateChuYokoChecker` / `FontRomanAlignmentChecker`），新增 `icons/fontfmt_tate_chu_yoko*.svg` / `fontfmt_roman_alignment*.svg` 4 个 SVG（上游图形，常规色改 #96a4cd 对齐本地图标族）；qss 增两组 indicator + `FontEmphasisToolButton` / `FontEmphasisMenu` 选择器。roman 走 `on_param_changed("standard_vertical_roman_alignment")`，`set_active_format` 回显。
  - **Ruby/连字/旧式数字** 保留在注解 GroupFrame，改上游式带标题分组：**Ruby / Furigana** 组（Type=组/逐字 + Position 选择行；Reading 编辑 + Apply/Remove 行）、**Ligature** 组（Common/Discretionary/Contextual/Oldstyle 2×2 行，onum 并入同组，仍发 "onum" 载荷走 `setOldstyleNums`）。
  - 无选中块时 emphasis/TCY 一并禁用（`_sync_annotation_controls` 扩展）；TCY 与 Ruby 互斥回滚改走 `self.tcyChecker`。
- **i18n**：AnnotationFormatGroup 21 条新增（Type/Group/Mono/Position/Ruby / Furigana/…）+ 11 条死串删除（Emphasis/Emphasis mark style/…/Tate-chu-yoko）；ConfigPanel +1（Vertical Text→竖排文本）；qm 1331 条。着重号菜单字符串（Emphasis Marks/Filled Dot/…）沿用 2a 移植上游 panel.py 时已入库的 EmphasisToolButton context，零新增。
- **测试**：`tests/test_annotation_controls.py` 重构（AnnotationGroupTest 移除 emphasis/tcy；新增 EmphasisButtonTest 6 例：默认 none/恢复不发声/勾选载荷）；`tests/test_configpanel_node3.py` 新增章节顺序断言（quick insert < Vertical Text 头 < Punctuation Position）。8 相关套件 + verify 五步全绿。

**排障记录：** `ligature_axis_value` 不接受 oldstyle 轴（引擎 `_LIGATURE_AXIS_TOKENS` 无 `oldstyle`），旧式数字回读仍走 `oldstyle_nums_value()`，组合框载荷保持 "onum" 路由；`ConfigFormRow.widget` 存的是包裹行而非控件本身，章节顺序测试改用 `findChildren` 定位。

**涉及文件：** `ui/configpanel.py`、`ui/text_panel.py`、`config/stylesheet.css`、`icons/fontfmt_tate_chu_yoko.svg`、`icons/fontfmt_tate_chu_yoko_activate.svg`、`icons/fontfmt_roman_alignment.svg`、`icons/fontfmt_roman_alignment_activate.svg`（新增）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_annotation_controls.py`、`tests/test_configpanel_node3.py`、`docs/daily_log.md`、`docs/技术实现/移植规划_上游v1.5.12.md`

---

## 2026-08-21

### 上游 v1.5.12 移植推进（节点 0/1/4/5）+ 计划外批次提交推送

**问题/需求：** 按 `docs/技术实现/移植规划_上游v1.5.12.md` 推进上游 v1.5.12 改动向 fork 迁移；先清理计划外挂起批次，再依序完成底层逻辑修复、数据层、角标/路径重排与杂项核对。

**改动要点：**

- **计划外批次聚合提交 `5dc2522` 并推送**：快捷键系统重构（`default_keys_for` 单源解析 / QShortcut 带 `action_id` 语义翻页抑制 / `sanitize_shortcuts` 配置清洗 / 冲突检测组内去重 / 绑定编辑器 Del·Rst）、`auto_squeeze_after_run` 运行后自动收缩开关、设置面板修正（Original Compare、快捷菜单样式），含 `tests/test_auto_squeeze.py`、`tests/test_shortcut_system.py`。mainwindow.py 中节点 0 的 translateBlkitemList hunk 用 `git apply -R --cached` 从暂存区摘出，保证节点 0 留在工作区。
- **节点 0 底层修复**（verify 全过）：LLM 动态 id Schema + 防注入（`modules/translators/trans_llm_api.py::_json_schema/_parse_response`）、SSL 请求局部 context（`utils/download_util.py`）、blk.xyxy 同步（`utils/textblock.py::sync_xyxy_from_bounding_rect` + geometry/textitem/textedit_commands/mainwindow 钩子）、画布光标生命周期（`ui/canvas.py::set_canvas_cursor`）。openai 图片 base url 按决策跳过（本 fork 无 LLM 图片修复）。
- **节点 1 数据层**：FontFormat 增 5 字段（`standard_vertical_roman_alignment` / `ligature_common·discretionary·contextual` / `oldstyle_nums`）；pcfg 增三键（`auto_tate_chu_yoko`(AutoTateChuYokoConfig 含 `allowed_characters()` + `__post_init__` 校验) / `compact_vertical_punctuation_spacing` / `quick_insert_characters` + `ProgramConfig.load` 坏值防护）；`font_weight` 保持本地 int 实现。
- **节点 4**：角标改绘到文本框外——`ui/textitem.py::_OrderBadgeItem` 独立子项（固定像素、`ItemIgnoresTransformations`、NoCache、高 Z、锚定父项 top-left 上方），`refresh_seq_badge()` 同步点接入 mainwindow 开关 / 画布渲染隐藏与重排进出 / scenetext 重编号；路径重排补**接触顺序排序**——`ui/canvas.py::_segment_rect_entry` 移植（段式命中 + RoundCap + 空段椭圆 + 前进距离排序），替代原 childItems 层序编号。
- **节点 5 杂项**：`280e023` 悬停崩溃已修（`ui/texteditshapecontrol.py::ControlBlockItem.hoverMoveEvent` 补 `blk_item is None` 防护）；`af7f1d0` S 键核对无需动作（本地无透射缩放交互，随节点 2 上游机制到来）；`2103976` 字号/Update 行核对本地已达标（`CONFIG_FONTSIZE_CONTENT` 统一 + App 页顶部 Update 状态行）；`c02102e` grid 手柄跟随核对快照已含上游机制（`grid_control_geometry` + `visual_geometry_changed`/`moving` 连接）。
- 文档：`docs/技术实现/反向移植_规范.md` 序号徽标行符号更新（`_draw_seq_badge` → `_OrderBadgeItem`）；`docs/技术实现/移植规划_上游v1.5.12.md` 节点 0/1/4/5 进度标记；新调研与规划文档入库。

**排障记录：** 4a 初版"框内 paint 直接移出框外"方案被 `DeviceCoordinateCache` 超 boundingRect 裁剪否决（padding 可为 0），改走上游式独立子项；verify 的 docs 步抓出 `反向移植_规范.md` 对已删符号 `_draw_seq_badge` 的失效引用，已同步更新。

**涉及文件：** `modules/translators/trans_llm_api.py`、`utils/download_util.py`、`utils/textblock.py`、`utils/config.py`、`utils/fontformat.py`、`utils/shortcut_conflicts.py`、`ui/textitem.py`、`ui/canvas.py`、`ui/mainwindow.py`、`ui/drawingpanel.py`、`ui/scenetext_manager.py`、`ui/textedit_commands.py`、`ui/text_engine/geometry.py`、`ui/texteditshapecontrol.py`、`ui/configpanel.py`、`ui/pie_menu_editor.py`、`ui/theme_helpers.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_auto_squeeze.py`（新增）、`tests/test_shortcut_system.py`（新增）、`docs/技术实现/上游v1.5.11之后提交调研.md`（新增）、`docs/技术实现/移植规划_上游v1.5.12.md`（新增）、`docs/技术实现/反向移植_规范.md`、`docs/daily_log.md`

---

### 路径重排闪退修复（TextBlock 不可哈希 + 路径项重复 addItem）

**问题/需求：** 路径重排（节点 4b）实机点击后报错闪退：`QGraphicsScene::addItem: item has already been added to this scene` 警告后，`ui/canvas.py::_reorder_extend_stroke` 抛 `TypeError: unhashable type: 'TextBlock'`。

**改动要点：**

- 触及块记账 `_reorder_touched_blocks` 改存 **`TextBlkItem`**（按身份可哈希，对齐上游 `_path_reorder_touched`）；原先存 `TextBlock`——nested_dataclass 定义了 `__eq__` 不再有默认 `__hash__`，`set()` 成员判定直接炸。`_reorder_end_stroke` 改用 `id(item.blk)` 映射回 blk 索引。
- 预览虚线路径项去掉重复 `self.addItem(...)`（`setParentItem(self.textLayer)` 本身已把项送入场景），消除 duplicate-add 警告；退出清理沿用 `removeItem` 对 textLayer 子项的既有模式（canvas.py:859）。
- 新增 `tests/test_path_reorder.py` 回归 5 例：行程顺序编号、单帧快拖跨块按 entry 排序、回头路过不重复编号、预览路径项仅一次入场景（parent 为 textLayer）、退出重排模式清理（`_reorder_seq` 复位/取消选中/路径项移除）。

**涉及文件：** `ui/canvas.py`、`tests/test_path_reorder.py`（新增）、`docs/daily_log.md`

---

### 节点 2a 阶段一：引擎文件落地 + 依赖核对（进行中，交接见 `docs/技术实现/移植交接_节点2a文件落地.md`）

**问题/需求：** 计划未完成部分按序推进，先做小节点 —— 节点 2a 的「文件落地 + 依赖核对」，不切渲染入口（入口切换须与落地分开：本地旧快照已 fork 改写，整体覆盖会破坏现行渲染链）。

**改动要点：**

- **落地 26 个上游缺失文件**至 `ui/text_engine/`（annotations/item/layout/horizontal_layout/vertical_layout/shape_control/cache/font_family/pipeline_formatting、editing/context_menu·manager·widgets、formatting/*、rendering/emphasis·native_document·ruby·tate_chu_yoko、transforms/controls·edit_session·grid_numba·modal·projective_control），剥 `ballontranslator.` 前缀、相对导入深度语义一致（最深 `...` = `ui` 包）原样保留。
- **依赖适配**：新建 `ui/text_engine/font_weight.py`（引擎局部 shim：FontWeight IntEnum + coerce/qt 转换 + HTML 字重 helper，数据表由本地 `fontweight_qt5_to_qt6` 派生——节点 1 决策「不引入上游 FontWeight 枚举/HTML 往返进 utils」的落地形态）；导入改写 5 处（item/annotations/formatting·panel/formatting·commands/pipeline_formatting）；`utils/text_processing.py` 增 seg 家族 + `capitalize_sentences`（seg_ch_pkg 删 prepare_pkuseg 依赖、缺 segmenter 降级逐字符）；`utils/text_layout.py`（layout_text）、`ui/icon_rendering.py`、`ui/adaptive_wrap_layout.py`、`ui/spellcheck.py` 落地；`ui/misc.py` 增 themed_icon_path/url、icon_url；`rendering/indexing.py` 换上游版（增 `_grapheme_ranges`，`_grapheme_count` 语义不变，消费方核对无影响）。
- **冒烟**：43 模块 42 可导入，剩 `editing.manager` 缺上游命令类（`ApplyFontformatCommand` 等 14 名）- 推荐方案已写在交接文档（建 `editing/upstream_commands.py` 放上游 15 类 + 模块函数，本地 commands.py 保持不动，改 manager 导入）。i18n 步 exit 6（94 条新 tr() 缺 ts 条目，上游 `resources/translate/zh_CN.ts` 已含，可批量复制 + qm 编译）。

**涉及文件：** 见交接文档 §2 清单（`ui/text_engine/` 新增 26 文件 + `font_weight.py`、`ui/icon_rendering.py`、`ui/adaptive_wrap_layout.py`、`ui/spellcheck.py`、`utils/text_layout.py`、`utils/text_processing.py`、`ui/misc.py`、`ui/text_engine/rendering/indexing.py`、`docs/技术实现/移植交接_节点2a文件落地.md`（新增））
---

## 2026-08-24

### 保存成图与项目数据不一致：面板↔画布同步机制根治 + 溢出裁剪收敛

**问题/需求：** 用户两类反馈——(1) 嵌字复查改措辞/位置后保存，成图有时不生效但项目内显示已保存，重启后恢复；(2) 他人反馈成图偶尔出现意外文本框，拖拽该框重新保存才消失。用户补充关键复现：惯用右侧输入区编辑，曾"删了一个字后保存，成图没响应，位移却正常"。

**根因：** 成图由 `render_result_img` 直接渲染画布场景（`canvas.py`），与 JSON 是两条独立路径，问题出在"保存瞬间场景项的文本/绘制状态过期"，实为两条独立机制：

- **面板↔画布双文档同步机制脆弱（主因，本次根治）**：右侧译文框（`TransTextEdit`）与画布 item 各持一份 QTextDocument，靠位置式差值重放（`propagate_user_edit`）双向同步——面板仅在 `hasFocus()` 时记录编辑位置，失焦时的程序性改动被静默跳过；且插入点在一个文档里记录、在另一份文档里重放，任何一次漏同步后位置即漂移。任一环节出错，两文档永久分叉：面板显示新文本（用户所见即"已保存"），而 item 文档（JSON 与成图唯一取数源）停留在旧文本；位移走 `item.pos` 直接属性，不受影响，与用户"改了字没生效、位移正常"的观察吻合。
- **溢出裁剪标志残留**：`clip_text_overflow` 模式下 `_text_overflows` 仅在 `startReshape`（拖拽调框）时清除，文字改短后标志残留，导出画像继续按旧框裁剪文本并画出金黄边框——即"意外文本框"，拖拽调框后再保存即消失。

**改动要点：**

- **`sync_text_by_diff` 取代差值重放**（`ui/textedit_commands.py`）：面板任何文本变更（不再有焦点门控）→ 以两个文档的**当前全文现场**做 `SequenceMatcher` 差异 → 按最小差异以 QTextCursor 逆序套用到 item 文档。位置永不跨文档记录，漏同步在下一次变更时自动收敛；整个对账包在一个编辑块里，连续键入经 `joint_previous` 并入上一块，维持双文档撤销步数联动（`TextEditCommand.undo()` 是双文档各退一步，步数必须对等）。
- **`ui/textedit_area.py`**：删 `hasFocus()` 门控与 `change_from`/`change_added`/IME 位置记录（连带 `paste_flag` 死代码）；`propagate_user_edited` 改 `Signal(bool)` 只带 `joint_previous`；`handle_content_change` 只负责通知 + 撤销步数推送，`text_changed` 发射逻辑不变（搜索框在监听）。
- **`ui/scenetext_manager.py`**：两个传播处理器改走 `sync_text_by_diff`（面板→item 无门控直接对账；内联编辑→面板用 `in_acts` 挡镜像回写的再同步）；`push_text_command` 仅在真正发生改动时调用；`updateTextBlkList` 保留保存端"面板为权威"回写作为兜底安全网。
- **溢出收敛**：`ui/textitem.py` 新增 `settle_overflow_state()`（按当前 `documentSize` vs 显示框重新求值，放得下即解除 `_text_overflows`）挂到 `endEdit`；`ui/canvas.py` `render_result_img` 渲染前对 textLayer 全部 TextBlkItem 调用一次，兜住未走 endEdit 的路径（含右侧输入触发的溢出）。
- **`scripts/render_sync_probe.py`（新增）**：离屏回归脚本，7 项验证——渲染即时性、聚焦编辑收敛、**失焦程序性改动收敛（原 bug 场景）**、改动区外字符格式保留、内联编辑回写面板、连续快速编辑无分叉无回环、连续键入撤销步数联动。

**取舍：** 面板仍是纯文本视图，多样式块在面板改字仍丢局部样式（现状如此，复杂样式走内联编辑）；`ui/text_engine/editing/manager.py` 存在一份同款脆弱重放的并行实现，但 fork 运行时未实例化（`mainwindow.py` 用的是 `scenetext_manager.SceneTextManager`），不动。

**验证：** `check_syntax` 全过；`render_sync_probe` 7/7 PASS；`test_textblkitem_geometry/effect`、`test_text_transform_engine`、`test_vertical_engine`、`test_startup_imports` 全绿；溢出收敛行为符合预期。

**涉及文件：** `ui/textedit_commands.py`、`ui/textedit_area.py`、`ui/scenetext_manager.py`、`ui/textitem.py`、`ui/canvas.py`、`scripts/render_sync_probe.py`（新增）、`docs/daily_log.md`

---

## 2026-08-25

### 在线 LLM 图像修复全链路（LLMInpaint 模块 / 比例裁剪工具 / mask 高亮红 + 短专注提示词）

**问题/需求：** 用生图模型自动擦除嵌字/破损区域。生图模型（Meshy/nano-banana）较笨：只靠通用提示词遇到复杂场景基本没擦掉嵌字，反而整幅套滤镜；而 gemini 对话式界面生成很完美，怀疑是对话模型读图后生成提示词。讨论确定方案：既然已人工标注 mask，就不需要额外视觉模型——把 mask 区域叠高亮色发给模型，提示词直接说明"这区域是要擦的嵌字、其余像素保持原样"，省去"要求模型不能做 xxx"的长篇。首版用 cyan 高亮 + `_REGIONAL_PROMPT` 拼接在长通用提示词后，实机失败（没擦掉字反而给字加蓝边）——根因是长提示词里"保持原样/别做滤镜"与"擦掉高亮区"自相矛盾，模型误以为要保留被标内容。修订：高亮改红、去掉长通用提示词、只保留一句短专注提示词，实机修复效果满意。

**改动要点：**

- **`modules/inpaint/inpaint_llm.py`（新）`LLMInpaint`**：profile 驱动（`utils/profile_manager` 选图像 profile，读 image-model/image-prompt/image-base-url/proxy/RPM/请求延迟），按端点宿主自动识别 OpenAI-compatible / Gemini / OpenRouter / Meshy 四类请求；Meshy 为异步（建任务→轮询→下载，去 mask、整幅重绘）；`_inpaint` 用 numpy 按原 mask 局部混合（`result*mask_original + img*(1-mask_original)`），带 stop_event 中断、retry（attempts/timeout）。**本批关键修复**：`_inpaint` 对局部 mask（`0 < 覆盖 ≤ 90%`）叠红色高亮（`img*0.55 + 红*0.45`）并只发短专注 `_REGIONAL_PROMPT`（"红色区域是嵌字/破损须擦除，视为修复标记而非图像内容，重建背景、框外像素保持不变"），替代 profile 长通用 image_prompt；`_meshy_aspect_ratio` 把实际裁剪宽高比吸附到模型支持集合（gpt-image-2 仅 1:1/3:2/2:3，nano-banana 系列另含 16:9/9:16/4:3/3:4）。LLM 提示词按规范不 i18n。
- **比例裁剪工具**（`ui/crop_rect_item.py`（新）`CropRectItem` + `ui/drawingpanel.py::CropControls`/`InpaintPanel`/`RectPanel`）：画布框选裁剪 + 比例下拉 + Crop mode + Inpaint/Clear mask；画笔修复工具与框选工具共用 `CropControls`，单一裁剪状态在 `DrawingPanel` 同步；框内画笔/框选累积 mask 并冻结；Inpaint 按钮把裁剪区发 LLM 修复（`ui/module_manager.py` 在线修复线程 + 非阻塞"修复中"画布浮层 + 完成提示）。
- **图像 profile 管理**（`utils/profile_manager.py` +610）：图像可用 profile（image-model/image-base-url/image-prompt/图像归属 proxy/RPM），`set_image_profile`/`get_image_profile_names`/`find_profile` 供 LLMInpaint 读取；`DEFAULT_INPAINT_PROMPT` 强通用提示词作默认。
- **比例说明**：各模型支持比例说明移到共享 `ui/drawingpanel.py::CropControls`（画笔修复工具与框选工具两处都能看到，仅 LLM 修复时随裁剪控件显示；i18n 上下文 InpaintPanel→CropControls，qm 重编译）。
- 配套：`ui/image_edit.py`（+5）、`ui/module_parse_widgets.py`（+35）、`utils/config.py`（+2）。

**排障记录：** ① cyan 高亮 + 长提示词拼接 → 蓝边；改红 + 仅短专注提示词后正常。② 换比例报 `UNEXPECTED_EOF_WHILE_READING` 经排查为代理/网络 TLS 握手被掐（走 http_proxy 隧道），与比例无关——比例由 `_meshy_aspect_ratio` 吸附到支持集，模型不支持的比例根本不会发出；若真不支持会返回 HTTP 4xx 而非 SSL EOF。③ 比例说明之前只放进 `InpaintPanel`（画笔修复工具），而在线裁剪修复实际走 `RectPanel`（框选工具），故用户看不到——移到共享 `CropControls` 后两处皆有。

**涉及文件：** `modules/inpaint/inpaint_llm.py`（新）、`ui/crop_rect_item.py`（新）、`ui/drawingpanel.py`、`ui/image_edit.py`、`ui/module_manager.py`、`ui/module_parse_widgets.py`、`utils/config.py`、`utils/profile_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/daily_log.md`

---

## 2026-08-26

### 图像修复区：工具图标重绘 + 面板可读性 + 控件自定义样式收尾

**问题/需求：** 修复区四工具图标里框选借用了「文字块」图标（语义错 + 常规/hover 颠倒）、AI 修图**完全没图标**（点开是空白蓝块）、手型/画笔与它们风格不统一；画笔/框选两页各内嵌一份「永不显示」的 CropControls（LLMInpaint 摘出后已从这两页引擎下拉过滤）；三页字段排版不齐、`ToolNameLabel` 标签太宽会缩字越缩越小、修复面板控件仍用裸 Qt `QCheckBox`/`QComboBox`，与项目 `ui/custom_widget` 库的自定义样式不一致。经用户确认按「图标重置 + 去死重量 + 统一排版 + 换自定义控件」实施。

**改动要点：**

- **图标统一重绘为同源 Material 填充集**（`config/stylesheet.css` + `icons/drawingtools_*.svg`）：4 图标各 base（`#96a4cd`）/`_activate`（`#697186`）两版——手型 `pan_tool`、画笔 `brush`、框选 `crop_free`（选区框角）、AI 修图 `auto_fix_high`（魔棒+火花）。`DrawRectTool` 纠正常规/hover（原来常规用 `_activate` 暗色、hover 用亮色，与其它三家相反），`DrawAiTool` 补齐三条 QSS 规则；删除 `DrawPenTool` 三条死规则与 `drawingtools_pen_*` SVG（`scripts/audit_registry.json` 登记 deprecated）。图标仍由 `set_icon_theme` 按深浅主题换色。
- **删掉画笔/框选两页永不显示的 CropControls**：`ui/drawingpanel.py::InpaintPanel`/`RectPanel` 移除各自 `CropControls` 实例与 `cropRatioChanged/cropModeChanged/inpaintClicked/clearMaskClicked/llmActiveChanged` 信号接线及 `_on_inpainter_changed/set_crop_mode_active/crop_*` 方法；裁剪控件收敛为 `AIConfigPanel` 一份权威副本，`_sync_crop_controls`/`_update_crop_active` 的三份同步循环改为仅同步 AI 页。
- **统一三页字段排版**：新增 `TOOL_LABEL_WIDTH=110` 常量，替换三组 `ToolNameLabel(100/130, …)` 为统一定宽；`ToolNameLabel` 去掉「标签太宽就缩字号」逻辑（改固定宽不缩字，保证可读）；三页在「引擎/遮罩选择」与「参数设置」间、以及 AI 页裁剪区前加 `SeparatorWidget` 做逻辑分组。
- **修复面板控件换成项目自定义样式**：`ui/drawingpanel.py` 的裸 `QComboBox`→`ComboBox`（`ui/custom_widget`，保留现有布局拉伸与定宽），裸 `QCheckBox`（Auto/Crop mode）→`ConfigCheckBox`（主题化勾选指示器）。

**排障记录：** 图标 SVG 必须用 `fill="#…"` 填充字形（非 stroke），因为 `ui/misc.py::set_icon_theme` 按 `fill="<色>"` 正则做主题换色，stroke 不会被替换（浅色主题会残留错色）。Combo 换 `ConfigComboBox` 会强制 `setFixedWidth` 步进宽度，破坏现有 `addWidget(combo, 1)` 拉伸填充，故选基类 `ComboBox` 保留布局。

**涉及文件：** `ui/drawingpanel.py`、`config/stylesheet.css`、`icons/drawingtools_hand.svg`、`icons/drawingtools_inpaint.svg`、`icons/drawingtools_rect.svg`（新）、`icons/drawingtools_rect_activate.svg`（新）、`icons/drawingtools_ai.svg`（新）、`icons/drawingtools_ai_activate.svg`（新）、`icons/drawingtools_hand_activate.svg`、`icons/drawingtools_inpaint_activate.svg`、`icons/drawingtools_pen.svg`（删）、`icons/drawingtools_pen_activate.svg`（删）、`scripts/audit_registry.json`、`docs/技术实现/图像修复区_审计与优化讨论.md`、`docs/daily_log.md`

