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