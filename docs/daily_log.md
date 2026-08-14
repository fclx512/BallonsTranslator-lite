# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-08-14

### 测试流程优化：一键 verify.py + i18n 孤儿降噪 + 冒烟单进程化

**问题/需求：** 每次开发功能后 AI 都要依次手动跑 语法检查 / i18n / qm 编译 / 冒烟测试 4 条命令（约 1 分钟、几 KB 输出进上下文），且 i18n 每次都 dump 大量"已知孤儿"（`canvas.tr()`/`self.tr(variable)` 间接调用的合法条目）刷屏；冒烟测试起 5 个独立子进程各冷启动一次 PyQt。目标：一条命令 + 零噪音输出 + 明确的跳过条件，失败时仍给完整报错供 AI 修复。

**改动要点：**

- **`scripts/verify.py`（新增，统一入口）**：`python scripts/verify.py` 依次跑 语法 → i18n → qm → 冒烟；成功每步只打一行（⏭ 跳过 / ✅ 通过 / ⚠ 警告），失败才完整打印报错与退出码。自动判定：语法只查 git 改动涉及的 .py（`--all` 全扫 ui/+utils/）；ts 有改动自动编译 qm；改动命中启动链文件（launch.py / modules/base.py / utils/profile_manager.py / ui/configpanel.py / ui/mainwindow.py）自动触发冒烟（`--smoke` 可强制）。
- **`scripts/i18n_check.py`**：`KNOWN_ORPHAN_CONTEXTS` 白名单（`_ShortcutRow`/`ShortcutEditor`/`ParamWidget`）——间接 tr() 调用条目移入"已知孤儿"，默认不显示、不计退出码，`--show-expected` 查看；`find_missing_and_orphans` 返回三值。verify.py 进一步把全项目孤儿（168 条基线，`canvas.tr()` 等）降级为一行警告：仅硬编码中文（位 1）/缺失条目（位 2）判失败。
- **`tests/test_startup_imports.py`**：5 个 subprocess → 单进程内依次验证（imports 幂等），5 次子进程冷启动（预计 30~60s）→ ~1.5s；`QT_QPA_PLATFORM=offscreen` 提到模块级；import launch 前临时替换 sys.argv（顶层有 parse_known_args）。
- **`scripts/check_syntax.py`**：支持多文件参数（原只取 argv[1]）。

**涉及文件：** `scripts/verify.py`（新增）、`scripts/check_syntax.py`、`scripts/i18n_check.py`、`tests/test_startup_imports.py`、`AGENTS.md`、`docs/项目概述.md`

---

## 2026-08-13

### 检查更新：对齐上游 release 策略 + 界面统一迁入设置

**问题/需求：** 原检查更新是 git 比较 main 分支 commit（开发分支天天有提交，无版本语义）；对齐上游 dmMaze 策略改为查 GitHub latest release + semver 版本比较。且 About 与设置两处检查更新称呼一致易混淆，统一迁入设置页，About 只留干净版本信息。

**改动要点：**

- **release 通道**（`utils/updater.py` 新增，移植自上游适配）：查 `fclx512/BallonsTranslator-lite` `releases/latest` API（api.github.com 无镜像可用），`packaging.version` semver 比较（异常回退 tuple）；更新动作 = 下载源码 zip → 备份 `.btrans_cache/last_version` → git stash 保护本地改动 → 安全解压（防路径穿越）→ 原子替换 → 清理。`SOURCE_UPDATE_DIRS/FILES` 白名单按本仓库平铺结构校准（config/ 拆文件白名单保护 config.json，data/ 与 gitignored 用户数据不更新）。
- **UI**（`ui/update_dialog.py` 新增 + `ui/configpanel.py`）：ConfigPanel Project 页 Updates 区——`[Check update]` 按钮 + Current/Latest version 行 + `check_update_on_startup` 复选框（**默认关**，用户指定）；有新版本弹 `UpdateReleaseDialog`（release notes 按 `## Changelog`/`## 更新说明` 选区段、剥图、主题样式）；`ui/update_thread.py`（新增）QThread 编排 + ProgressMessageBox 进度 + 启动 500ms 静默检查。
- **commit 通道**（`ui/update_checker.py`）：AboutDialog 瘦身（只留版本/commit/branch/链接）；commit 检查 + git reset 更新迁入新 `CommitUpdateDialog`（开发者测试通道，带"按最新提交可能不稳定"风险提示），入口在 Updates 区「检查提交更新」按钮。
- 真实 API 验证：本地 0.5.0 == fork release v0.5.0 → up_to_date。

**涉及文件：** `utils/updater.py`（新增）、`ui/update_thread.py`（新增）、`ui/update_dialog.py`（新增）、`ui/update_checker.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

### 快捷菜单：竖排紧贴光标 + 拖拽 ghost 缩小 + 重置默认按钮（收尾）

**问题/需求：** ① 竖排样式沿用环形菜单模板，弹出时鼠标落在集群外 ~85~120px——环形靠方向甩动瞄准无所谓，竖排是精确指向，距离纯属负担；② 命令池卡片以整张 128×44 图作拖拽 ghost，遮住 0.72 缩放的小预览，看不到放置位置；③ 验收后补一个「重置默认状态」按钮；④ 「样式」「方向」下拉框宽度按英文 item 算的 sizeHint 决定，中文化后"环形/竖排"被下拉箭头挤出（被控件遮挡）。

**改动要点：**

- **竖排面板集群紧贴光标**（`ui/pie_menu.py`，用户确认保持半环阶梯）：删 `LIST_ANCHOR_DIST = 120`，改三个常量 `LIST_ANCHOR_GAP_X = 10`（光标→正侧面板左缘）、`LIST_ANCHOR_GAP_Y = 6`（上下斜与正侧垂直净距）、`LIST_DIAG_INSET = 10`（上下斜左缘缩进，保留阶梯）。`_relayout_list` 锚点重写：先算各面板高度（空面板 1 行幽灵），正侧面板垂直居中于光标、上下斜面板的 y 由 `h_lat` 推导 → 任意行数组合三面板永不相交。命中距离从 ~85~120px 缩到 ~10px（正侧）/~23~49px（上下斜）。状态机/命中/拖放/镜像逻辑零改动。
- **命令卡片拖拽 ghost 缩小**（`ui/pie_menu_editor.py`）：`_CommandCard` 拖拽图按 `_DRAG_SCALE = 0.55` 平滑缩放（128×44 → ~70×24），hotspot 同步缩放。
- **重置默认状态按钮**（`ui/pie_menu_editor.py`）：工具卡片 Row1 「New Menu」旁新增 `Reset to Defaults`（ConfigButton 样式），`_on_reset_defaults` 弹 `QMessageBox.question` 确认后把全部菜单重置为 `DEFAULT_PIE_MENUS`（复用 `context_menu_config._on_reset` 的既有模式）。
- **下拉框加宽**（`ui/pie_menu_editor.py`）：`style_combo`/`direction_combo` `setMinimumWidth(110)`，中文项不再被下拉控件遮挡（sectors 是数字，不动）。
- i18n：`PieMenuEditor` context 新增 3 条（Reset to Defaults/Reset/Reset all quick menus...），qm 1176 条。

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`、`scripts/pie_menu_test.py`（+5 断言：hugs cursor/clear lateral/3-row 不重叠/重置 Yes/No）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/快捷菜单_竖排样式_设计与交接.md`

---

## 2026-08-12

### 快捷菜单：竖排样式 + 设置页清理（杂事）

**问题/需求：** 环形菜单功能项分布太散，不适合堆叠某类大类功能。新增**竖排（List）样式**——复用环形卡片视觉垂直堆叠，用户可在环形/竖排间切换样式，竖排弹出在光标左/右侧（方向可配）。同时处理两件杂事：删除设置页右键菜单编辑入口（菜单本体暂留测试）、「环形菜单」设置页改名「快捷菜单」。

**改动要点：**

- **数据模型**（`ui/pie_menu.py`）：`normalize_pie_menu` 扩展 `layout` 合法值校验（ring/list）+ `direction`（left/right，默认右）+ `items`（竖排扁平命令列，上限 24，过滤非 str 与 `SEPARATOR_SENTINEL`）；新增 `slots_to_items`/`items_to_slots` 双向转换（环形↔竖排样式切换保命令不丢）。
- **竖排渲染/交互**（`ui/pie_menu.py`，环形路径零改动）：`_paint_list`（标题行 + 卡片列，复用 `_card_palette` 配色，无扇区号）、`_hit_test_list`/`_list_insert_index`/`_list_item_rect`、`_move_list_at`（方向锚定 + 垂直居中 + `availableGeometry` 钳制）、新增信号 `list_command_dropped`/`list_item_remove_requested`，mime 复用 `x-pie-src="0,idx"`。状态机/触发流完全复用，MainWindow 零改动。
- **编辑器**（`ui/pie_menu_editor.py`）：属性行二段式（Row1 名称/触发键/冲突 pill；Row2 样式/扇区数/方向，按样式显隐）；`_on_style_changed`/`_on_direction_changed`；提示文案随样式切换；新建菜单上限文案改 "quick menus"。
- **杂事**（`ui/configpanel.py`）：删 Interface 分页「Customize Context Menu...」按钮与 `_open_context_menu_config` 处理器（`ContextMenuCustomizeDialog`/`build_context_menu`/`pcfg.context_menu_order` 保留供测试）；设置页改名「快捷菜单」(Quick Menus)，nav key `pie_menus`→`quick_menus`。
- i18n：`PieMenuEditor` context 新增 7 条；删除右键菜单编辑相关 3 条孤儿；3 处跨行隐式拼接改为单字面量（消除孤儿）；qm 编译 1155 条。

**排障记录：** 竖排测试中发现 `save_config()` 报 `KeyError: 'value'`——追查为既有现象非回归：真实 app 启动时 `ModuleManager` 会 `merge_config_module_params` 把模块参数打包成 `{"value": ...}` 格式，模拟该步骤后 `save_config ok = True`，与本次改动无关。

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`、`ui/configpanel.py`、`scripts/pie_menu_test.py`（+38 断言，共 158 全过）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/快捷菜单_竖排样式_设计与交接.md`（新增）

---

### 竖排样式重做为半环多面板 + 设置页预览修复

**问题/需求：** 上午版竖排是"单面板卡片列"，用户确认方向为**半环多面板**（"把扇形竖着砍一半，在预定位置放简化版右键菜单"）；同时截图排查发现设置页预览被压成细缝（环形/竖排同受害）、页面横向溢出裁掉触发键列、运行时超长 label 被硬裁无省略号。

**改动要点：**

- **数据模型**（`ui/pie_menu.py`）：`items`（扁平列）→ `panels`（恒 3 组 × 每组 ≤3，对齐环形扇区容量）；旧 `items` 配置 normalize 时按 3 个一组自动迁移。转换函数换成 `slots_to_panels`/`panels_to_slots`（按 direction 取/写侧向半环扇区：右 1/2/3、左 7/6/5，顶→底）。
- **竖排几何重做**：`_relayout_list`（统一面板宽 + 3 锚点矩形 + 包围盒偏移，空面板也算 1 行幽灵矩形保证几何稳定）、`_list_row_rect`、`_hit_test_list`（→ `(panel,row)`，空面板不可命中）、`_list_drop_pos`（行中线决定插前/插后，幽灵框可放置）、`_move_list_at`（光标固定点 - 窗口偏移 + 屏幕钳制）。`_slot_at` 改布局感知后，悬停/提交/点击/拖拽与环形**完全共用**；删除两个 list 专用信号，复用环形信号（sector 参数承载面板号）。
- **问题 3 修复**：`changeEvent` 监听 `FontChange` 重算几何（此前字体在 `set_menu_config` 后变化会宽度失配）；行 label 超宽 `elidedText` 右省略（此前硬裁）。
- **设置页修复**（`ui/pie_menu_editor.py`）：删掉预览区内嵌的 `QScrollArea`（空间不足时被压成 ~60px 细缝，是"严重裁剪"根因），Fixed 预览直挂布局随页面滚动；`palette_hint` 加 `setWordWrap(True)`（单行长文本曾把页面撑到 690+px 宽撑出横向裁剪）。`_on_direction_changed` 补预览刷新。
- **测试**：竖排章节 38 项断言全部重写为 panels 模型（共 158 项全过）；启动冒烟 5/5；i18n 无新增字符串。
- **截图脚本**：`_list_preview.py`/`_pie_editor_preview.py` 改 panels 配置；新增 `_ring_editor_preview.py`（环形设置页预览回归用）。**离屏截图勿用 `QT_QPA_PLATFORM=offscreen`（豆腐块）**。

**排障记录：** ① `git` 对象库损坏（`.git/objects/pack` 只剩 .idx），dev/pie-menu 分支无提交，工作区是唯一事实来源，git 还原不可用。② 测试中 `save_config` 偶发 `WinError 5`（`os.replace` 被占用），删 `_pie_test_config.json(.tmp)` 残留重跑即过。

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`、`scripts/pie_menu_test.py`、`scripts/_list_preview.py`、`scripts/_pie_editor_preview.py`、`scripts/_ring_editor_preview.py`（新增）、`docs/技术实现/快捷菜单_竖排样式_设计与交接.md`（重写）

---
## 2026-08-05

### 文本引擎移植 阶段 3：渲染层拆分（节点 A–E 完成）

**问题/需求：** 将渲染职责从"textitem 直画 + 快路径缓存"切换到上游 v1.5.9
`TextEffectRenderer` 结构（描边/阴影/渐变/padding 推导归 renderer），删除本地
快路径，保留本地縦中横/标点右上/`shadow_include_stroke` 独有逻辑。中性态
（空变换栈）零行为变更。

**改动要点：**

- 节点 A：新增 `ui/text_engine/rendering/` 子包（shadow/raster/indexing/glyph），
  `ui/shadow_gradient_dialog.py` import 指向新位置。
- 节点 B：`ui/scene_textlayout.py` 补 render_delegate/layout_generation/
  defer_cursor_paint/input_point_mapper/effectPadding 等机制槽位。
- 节点 C：移植 `ui/text_engine/effect_renderer.py`（约 1370 行）；textitem 删快路径
  （`_full_pixmap`/`paint_stroke`/`_build_full_pixmap` 等）改走 renderer；
  删除 `ui/text_graphical_effect.py`；`setPadding` 改为返回 bool（修复
  `_commit_effect_padding` 梯度刷新分支永不触发的隐患）。
- 节点 D：删除 `text_rendering`/`show_decorations_during_drag` 配置及全部消费
  （configpanel/mainwindow/scenetext_manager/textitem），同步 ts/qm。
- 节点 E：新增 `tests/test_textblkitem_effect.py`（7 用例），全量回归 + 縦中横/
  标点右上像素验证，排障备忘写入阶段 3 文档。

**排障记录：** `setDocumentMargin` 是可撤销操作且禁用 undo 会清空整个 undo 栈，
padding 提交产生恰好 1 个 undo 步（既有行为，非回归）；PyQt6 渐变用 `stops()`
替代 `colorAt()`；描边笔宽 = font_size_px × stroke_width，测试用分数语义 0.1。

**涉及文件：** `ui/text_engine/`（rendering/ 子包 + effect_renderer.py 新增）、
`ui/textitem.py`、`ui/scene_textlayout.py`、`ui/scenetext_manager.py`、
`ui/mainwindow.py`、`ui/texteditshapecontrol.py`、`ui/shadow_gradient_dialog.py`、
`ui/text_engine/_stubs.py`、`ui/configpanel.py`、`utils/config.py`、
`ui/text_graphical_effect.py`（删除）、`tests/test_textblkitem_effect.py`（新增）、
`translate/zh_CN.ts`、`translate/zh_CN.qm`、
`docs/技术实现/文本引擎移植_阶段3_渲染层.md`

---

## 2026-08-10

### 环形菜单前置：旧样式清理（阶段 0）+ 删 preview 与快捷键冲突校验（阶段 1）

**问题/需求：** 画布右键菜单交互不便（高频功能需两次点击+定位），规划 Blender 风格环形菜单（按 Tab 呼出、鼠标方向选择、松开触发，8 扇区 × 径向分层，短按 pin / 长按 release-commit）。正式动工前按用户要求先完成两阶段前置：清理遗留旧样式/无用代码；删除 preview 释放 Tab 并新增快捷键冲突校验。完整方案在 `docs/技术实现/环形菜单_实施方案.md`，阶段 2 起由新 agent 接手。

**改动要点：**

- 阶段 0 清理：stylesheet.css 删 5 块无对应控件的孤立规则（HelpDoc 系列约 100 行/IncrementalBtn/PresetListWidget/MenuSectionLabel/SmallConfigPutton）+ 6 处注释死规则 + 3 处调试色改主题变量（royalblue/#5DADE2→`@accentPrimary`）；删除 6 个未引用图标（`eye`/`image`/`text` 各 2 变体）；删除 `utils/text_normalize_test.py`、`scripts/webengine_memory_test.py`（同步 `scripts/README.md`、`manifest.json` 条目）；清理 10 处无 TODO 注释掉的 Python 代码块（canvas/module_manager/scene_textlayout/drawingpanel/fontformat_commands/scrollbar）。
- 阶段 1：删除 preview 功能（configpanel 三处定义、mainwindow `shortcut_registry["preview"]` 注册与 `shortcutPreview` 槽、canvas `previewLabel`/`previewLayer`/`preview_mode` 初始化与 viewport 橙色边框、`toggle_preview`/`_enter_preview`/`_exit_preview`、`_layout_status_labels` 简化、stylesheet `#PreviewLabel`、ts 的 Preview+PREVIEW 条目），qm 重编译 1128 条，**Tab 键已释放**。
- 阶段 1：快捷键冲突校验——`ShortcutEditor._compute_conflicts()` 汇总全部动作生效键集（`pcfg.shortcuts` 优先，否则 `DEFAULT_SHORTCUTS`）找重复键；`_ShortcutRow` 冲突键渲染红色 pill（theme_helpers 深/浅主题新增 `conflict_pill_bg`/`conflict_pill_text`）；构造时 + 每次编辑后（`_on_row_changed`）刷新。

**排障记录：** PyQt6 在创建 QApplication 之前实例化 QWidget 会直接 abort（无 Python traceback、Git Bash 下 exit 127，易误判为命令缺失）；调研报告的图标清理清单有大量子串误报（`textdetect`/`edit_activate`/`text`/`image` 等实为有引用），删除文件前必须用完整文件名精确 grep 复核。

**涉及文件：** `config/stylesheet.css`、`ui/theme_helpers.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/canvas.py`、`ui/module_manager.py`、`ui/scene_textlayout.py`、`ui/drawingpanel.py`、`ui/fontformat_commands.py`、`ui/custom_widget/scrollbar.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`manifest.json`、`scripts/README.md`、`icons/`（删 6 个 svg）、`utils/text_normalize_test.py`（删）、`scripts/webengine_memory_test.py`（删）、`docs/技术实现/环形菜单_实施方案.md`（新增）

---

## 2026-08-11

### 环形菜单 Tab 触发修复：长按泄漏 + 触发区域限定（阶段 2 修订）

**问题/需求：** 用户反馈按 Tab 唤出环形菜单时右侧文本编辑区同步响应（焦点在面板功能元素间循环切换），长按则向下方文本框插入制表符。排查确认根因：`_pie_handle_keypress` 对自动重复（auto-repeat）Tab 完全未处理——菜单打开期间仅吞首次 Tab，重复 Tab 直接放行落到焦点控件，触发 Qt 默认 Tab 焦点遍历 / QTextEdit 插 `\t`；饼菜单窗口不抢焦点，长按瞄准全程泄漏可见。经与用户确认，同时将触发区域限定为「光标在画布上」。

**改动要点：**

- `ui/mainwindow.py`：
  - `_pie_handle_keypress` 菜单打开分支改为吞掉**所有** Tab（含 auto-repeat）——首次 Tab 仍取消 pin 菜单，重复 Tab 只吞不放，永不落到焦点控件。
  - 新增 `_pie_cursor_on_canvas()`（光标是否落在画布 GraphicsView 上：`gv.mapFromGlobal(QCursor.pos())` 命中 `gv.rect()`），`_pie_handle_keypress` 与 `_pie_handle_shortcut_override` 在模式条件之外追加区域判定：光标不在画布上时 Tab 恢复正常焦点切换，不弹菜单、不吞 ShortcutOverride。
  - 保留：纯文本输入框（源/译文/搜索）内 Tab 惰性吞掉；对话框/独立窗口 Tab 原行为；Ctrl+B/I/U 等文本编辑 QShortcut 不受影响（走 ShortcutOverride 通道，与 KeyPress 吞键互不相干）。
- `scripts/pie_menu_test.py`：FakeMW 增 `_pie_cursor_on_canvas` 存根 + 10 项新断言（auto-repeat 吞掉且不取消、off-canvas 穿透不开菜单、ShortcutOverride off-canvas 不吞、on-canvas 恢复触发等），全量 75 项通过。

**排障记录：** 独立离线脚本复现确认（旧逻辑 auto-repeat 在 QTextEdit 中插入 `\t`；修复后不插入）。区域判定放在文本输入吞并逻辑**之后**——文本输入框的 Tab 吞并必须独立于光标位置，否则光标不在画布时编辑框会恢复插 `\t`（回归 8-10 审查决定）。i18n exit 4 为既有快捷键面板 orphan（`self.tr(variable)` 间接调用，已知问题），本次未新增任何 tr 字符串。

**涉及文件：** `ui/mainwindow.py`、`scripts/pie_menu_test.py`

---

### 环形菜单 Blender 样式复刻（视觉重做）

**问题/需求：** 按 `docs/技术实现/环形菜单_Blender样式复刻方案.md` 把饼菜单从"大圆盘自定义菜单"改为 Blender 透明扇区样式：无圆盘背景、中心圆环+扇形填充指示、去图标卡片、同扇区多卡片切向堆叠。交互状态机与触发逻辑不动。

**改动要点：**

- `ui/pie_menu.py` 重绘：删除大圆盘与 hover 背景扇形；中心指示器改为细圆环 + 空心扇形填充（`QPainterPath` 扇形减内圆）；新增中心标题 `self.tr("Actions")`；卡片去图标、改半透明底 + 强调色描边、紧凑内边距；多卡片扇区从角度扇开改为**切向堆叠**（左/右扇区纵向、上/下扇区横向），命中测试改为"扇区内最近卡片中心"。
- 新增 `WINDOW_MARGIN = 40`：窗口尺寸与逻辑半径解耦（500×500 窗口 / TOTAL_RADIUS 210），卡片矩形 clamp 进窗口边界，杜绝透明窗口边缘无声裁剪。
- 顺手修复既有 bug：旧扇形角度公式（`-112.5 - sector*45`）画反 180°（旧 wedge alpha 仅 22 几乎不可见，一直未被发现），中心扇形填充改用 `67.5 - sector*45`，预览图实证方向正确。
- `ui/context_menu_config.py`：删除 `CmdDef.icon` 字段及全部 `icon=` 赋值（delete / 3 个 align / translate / ocr / ocr_translate）。
- `utils/config.py`：pie_sectors 注释更新为切向堆叠语义（默认配置不变）。
- i18n：ts 新增 `PieMenu` context（Actions→操作），qm 重编译 1129 条；i18n_check exit 4 为既有 orphan（`canvas.tr(label_key)` / `self.tr(variable)` 间接调用，已知），本次未新增问题项。
- `scripts/pie_menu_test.py`：命中测试断言改为切向堆叠几何（逐卡片中心命中 / 堆叠不重叠 / 全部卡片在窗口内 / 扇区边界回退），全量 76 项通过；`scripts/_pie_preview.py` dark/light 预览图已更新。

**排障记录：** i18n_check 只扫 `self.tr("字面量")`，标题必须内联字面量（方案中的 `PIE_MENU_TITLE` 常量经 tr 间接调用会产生 orphan）；预览脚本的 MockCanvas 不加载 qm，标题在预览图中显示英文属预期，实机经 qm 显示"操作"。

**涉及文件：** `ui/pie_menu.py`、`ui/context_menu_config.py`、`utils/config.py`、`scripts/pie_menu_test.py`、`scripts/_pie_preview_dark.png`、`scripts/_pie_preview_light.png`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/环形菜单_Blender样式复刻方案.md`、`docs/daily_log.md`

---

### 多菜单可配置环形菜单（阶段 A–D 全部完成）

**问题/需求：** 环形菜单此前只有单一 Tab 触发入口、内容写死（`pcfg.pie_sectors` 无编辑 UI）。目标：建立若干个菜单（每菜单一个触发键）、用户可在设置中拖拽配置功能项；配置页上半实时预览（与真机样式一致）、下半分类命令池，拖拽摆放/堆叠（最高 3 个）。经与用户逐点确认定稿：3 个默认菜单（Tab=编辑 / X=对齐 / C=视图管线）、命令池适度扩池（四类含视图）、环形+扇区数可调（4/6/8）、ConfigPanel 新页面、完工后补设计/交接文档（供未来多模态 AI 修缮 UI）。

**改动要点：**

- **阶段 A 命令池**：`CmdDef` 加 `category`（basic/text/pipeline/view）；新增 6 命令（undo/redo→`mw.canvas.undo()/redo()`、zoom±→`scaleUp/scaleDown`、fit_window→canvas 新公开 `fitToWindow()` 薄封装、prev/next_page→`mw.shortcutBefore/Next`），均 `hidden_in_customize=True` 只进饼菜单池；`run_cmd(canvas,id)`→`run_cmd(mw,id)`，`run_fn/enabled_fn` 统一收 MainWindow（~13 处 lambda 机械改 `mw.canvas.*`），右键菜单 `build_fn` 不动。
- **阶段 B 数据模型与触发**：`pcfg.pie_menus: List[dict]`（id/name/trigger/sectors/layout/slots）+ `DEFAULT_PIE_MENUS` 三菜单模板 + `migrate_legacy_pie` 迁移（旧默认→新模板；自定义→保留为首菜单+补 2 默认）；`PieMenu` 扇区数参数化（`_card_center`/`_hit_test`/绘制/中心指示器全按 `360/sector_count`）；MainWindow `_pie_menu_for_event` 按键查表 + 文本输入两套规则（Tab 惰性吞保留、字母/组合触发键完全放行照常输入）；冲突校验抽 `utils/shortcut_conflicts.py::find_conflict_keys` 供快捷键面板与触发键共用。
- **阶段 C 配置页**：`PieMenu` 加 preview 模式（无窗口 flag、hover/点击/右键/拖放、painter scale 统一缩放、编辑态虚线扇区引导 + 拖放目标高亮 + 选中环，渲染零复制）；新增 `ui/pie_menu_editor.py`（菜单 tab 管理 + 触发键 QKeySequenceEdit + 冲突红 pill + 扇区数下拉 + 名称行 + 实时预览 + 分类命令池 QTreeWidget）；拖放 mime `x-pie-cmd`/`x-pie-src`，落点解析复用 `sector_at`/`_drop_insert_index`（切线投影定插入位），满 3 拒收红闪、同扇区重排、拖回池移除、右键移除；上限 4 拦截；注册为 ConfigPanel「环形菜单」页（nav key `pie_menus`）。
- **阶段 D 文档**：`docs/技术实现/多菜单环形菜单_设计与交接.md`（设计思路/代码索引/视觉常量/数据模型/Qt 坑/待修缮清单）。

**排障记录：** `QPen` 无 `setAlpha`（应设在 QColor，paint 内抛异常会致 grab 硬崩无回溯）；`QDrag` 在 Qt6 属 QtGui；`QTabBar` 无 `clear()`；`self.tr()` 无 `.arg()` 用 `.replace("%1")`；`KeyboardModifier` Flag 枚举 `int()` 不可用需 `.value`；offscreen 实例化 ConfigPanel 验证页面注册/迁移（`pageStack.indexOf` 返回 -1 是 `_add_page` 包裹层所致，非 bug）。

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`（新增）、`ui/context_menu_config.py`、`ui/mainwindow.py`、`ui/configpanel.py`、`ui/canvas.py`、`utils/config.py`、`utils/shortcut_conflicts.py`（新增）、`scripts/pie_menu_test.py`、`scripts/_pie_preview.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/多菜单环形菜单_设计与交接.md`（新增）、`docs/daily_log.md`
