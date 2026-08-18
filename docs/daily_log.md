# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-08-18

### 画布左键框选 + 块拖移动 + 快捷菜单点空白/失焦关闭（批次验收通过）

**问题/需求：** ① 左键/中键拖拽都是拖画布，左键可换更实用的接口——把左键拖拽定义为框选文本框（配合快捷菜单批量编辑）；② 但纯框选又丢掉拖块能力，需求细化为"块上拖拽恢复移动、空白框选"；③ 文本块创建模式（W 切换，默认开）下左键交互完全失效；④ 快捷菜单点空白/主窗失焦无法关闭，只能再按快捷键。

**改动要点：**

- **左键交互重构**（`ui/canvas.py`、`ui/textitem.py`）：视图默认拖拽 `ScrollHandDrag`→`NoDrag`（三处同步，光标显式复位）。**空白处左拖=框选**：橡皮筋仅选中相交 `TextBlkItem`（遍历 `textLayer.childItems()`），Ctrl/Shift 追加否则替换，`MIN_RUBBER_BAND_DRAG=4px/zoom` 阈值防点击误触发，真拖拽释放后才 `_sync_shape_control_to_selection`（单选绑 shape control、0/多选清空）。**块上左拖=原生移动**：`start_box_select` 对 `ControlBlockItem`/`GridControlPointItem` 控制柄与任何 `TextBlkItem` 直接 return，按压交给 `ItemIsMovable` 拖拽（多选联动、snap/undo 信号流与旧版一致）；`TextBlkItem` 悬停置 `SizeAllCursor`（`_update_move_cursor`，编辑中 `unsetCursor` 交还 IBeam）——即"移动样式"触发条件。**textblock 模式**（W/底部栏，默认 `pcfg.imgtrans_textblock`）右键建块收窄进 `btn==RightButton` 条件、第一支改普通 if，左键不再被整链吞掉。原右键橡皮筋整段移除（右键=上下文菜单），中键滚动画布保留，手型工具仅绘图模式显式 ScrollHandDrag。
- **快捷菜单关闭补强**（`ui/mainwindow.py`、`ui/pie_menu.py`）：根因 ① holding 态（按住触发键的弹簧态，环形菜单常态）点击外部被 `is_holding()` 短路；② 全项目仅 `ApplicationDeactivate` 一条失焦通道，Windows 不可靠；③ holding 态点菜单**窗口内**透明区/环隙落到 super() 无响应；④ 实机点画布关闭仍偶发不灵。修法：去掉 holding 短路（任意打开态点击菜单矩形外即关）；主窗 `ActivationChange` 延迟一帧 `_pie_cancel_if_inactive` + `applicationStateChanged` 非 Active 即关（补失焦缺口）；**画布安全网** `_dismiss_open_pie_menu()`——场景收到按压即证明点在菜单窗外、无条件 `cancel()`（不依赖全局坐标换算）；holding 态菜单窗内任意按压即关。`cancel()` 幂等，多信号同时触发安全。
- **测试**：`tests/test_box_select.py` 新增（18 例：框选只选命中块/块上移动精确位移/多选联动/悬停光标/textblock 模式放行/画布安全网关菜单/右键不框选）；`tests/test_pie_menu_dismiss.py` 新增（11 例：PIN/holding 点外关点内不关/主窗失活关/应用失活关/holding 窗内即关/幂等）；状态机 `scripts/pie_menu_test.py` 294 断言全过；`verify.py --smoke` 全绿（6 文件）。

**排障记录：** pytest 下弹出无边框 Tool 顶层窗 + `processEvents()` 会硬崩（EXIT 127 无输出，plain 脚本却正常）；`tests/ui/` 包在 prepend 模式遮蔽仓库根 `ui`（须无条件 `sys.path.insert(0, REPO_ROOT)`，`not in` 守卫会被跳过）；`scripts/pie_menu_test.py` 被硬崩中断会残留 `_pie_test_config.json(.tmp)` 导致下次 PermissionError，删两个文件重跑即可。

**涉及文件：** `ui/canvas.py`、`ui/textitem.py`、`ui/mainwindow.py`、`ui/pie_menu.py`、`tests/test_box_select.py`（新增）、`tests/test_pie_menu_dismiss.py`（新增）

---

## 2026-08-16

### 发版 v0.6.0

- 距 v0.5.0（07-26）18 个提交 / 21 天：快捷菜单环形+竖排双样式、文本引擎移植、TextStyleDialog、拾色器吸色管、检查更新迁入设置页、设置面板排版重构、一键 verify 工具链。
- 发版前验证全绿（verify --all --smoke、i18n 无硬编码/无缺失、项目测试 452 项全过）。
- 随发版清理 6 个 `scripts/_*.py` 开发预览脚本（`_pie_preview`/`_list_preview` 等），`docs/技术实现/快捷菜单_实现总结.md` 同步去引用。
- 流程：bump `pyproject.toml` 0.6.0（提交 cae84bc）→ annotated tag v0.6.0（含 release notes）→ push main + tag → GitHub release（源码 zip 已确认可下载，更新器可用）。

---

### 环形菜单裁剪根因修复 + 层级顺序 + 动画/命令池批次收尾

**问题/需求：** ① 长英文名（如 "OCR, translate and inpaint"）撑爆环形卡片布局、被遮挡；② 菜单呼出无过渡、卡片 hover 无反馈；③ 方向指示器按 8 扇区硬编码、卡片半透明在亮背景可读性差、竖排 3→5 面板后弧形偏移缺失；④ 命令池卡片长名被省略、used 灰显无意义、缺「保存为默认布局」开发工具；⑤ 设置页预览与画布菜单文字**莫名被截断**（环形 `O…`/`OCR并…`/`OCR，翻译并…`，英文环境 Copy→`co…`/Paste→`pa…`），"空间足够却主动裁剪"，重启/离屏复现均无法解释。

**改动要点：**

- **卡片宽度 cap + hover 展开 + pop-in 动画**（`ui/pie_menu.py`）：`CARD_MAX_W` 160→**240**（与竖排 `LIST_MAX_W` 同值，管线英文名全显示，仅高 DPI 超 240 才省略）；hover 卡片展开动画（140ms OutCubic）显示全名、后画置顶；菜单呼出 pop-in（淡入+缩放 0.92→1.0）。均受 `pcfg.animation_fps<0` 调控，帧驱动同 overlay_modal。
- **三处小瑕疵**：方向指示器 sweep 硬编码 45° → 跟随 `span`（4/6/8 扇区）；卡片底色全不透明 255（亮背景可读性）；竖排 5 面板弧形梯度 10/5/0（`LIST_DIAG_INSET` 10→5、新增 `LIST_POLE_INSET=10`）。
- **命令池扩员 + 编辑器 UI**（`ui/context_menu_config.py`、`ui/pie_menu_editor.py`）：新增 4 个 `CAT_TOGGLE`（`seq_badge`/`clip_overflow`/`overflow_mode`/`drag_decorations`），`fit_window` 移出 palette；命令池卡片宽度自适应（`_CARD_W_MAX=400`，长名不省略）；**删除 used 灰显**（命令可复用，灰显无意义）；新增**「保存为默认布局」开发按钮**——覆写 `utils/config.py::DEFAULT_PIE_MENUS`（紧凑 JSON 风格）+ 同步内存常量（`copy.deepcopy`），`_on_reset_defaults` 改动态属性访问，开发阶段该按钮优先级高于重置旧默认。
- **裁剪根因修复**（`ui/pie_menu.py`）：新增模块函数 `_elide_fitting(fm, label, avail)`——**仅当 `horizontalAdvance(label) > avail` 才调用 `elidedText`**，放得下的标签原样返回；环形 `_paint_card` 与竖排 `_paint_list` 均改走它。
- **环形卡片层级顺序**：默认按索引数字决定遮挡（**层级 1 > 层级 2**，每扇区从末张往前画、低索引后画置顶），hover 卡片仍最后画置顶（`_paint_cards`）。

**排障记录（裁剪根因，重点）：** 用户实机（9pt Microsoft YaHei / Windows GDI/ClearType）上 `elidedText("OCR", 24)` 返回 `O…`——根因是 **elide 宽度恰好等于文本宽度（刀刃）**：`elidedText` 内部用浮点精度判断放不放得下，而 `horizontalAdvance` 返回取整整数；真实宽度 24.x 取整成 24 后被判定放不下。本地 offscreen（FreeType）取整方向相反（24.6→25）永不截断，提交历史任意版本也复现不出，最终靠 `scripts/pie_menu_diag.py`（临时诊断脚本，实机跑输出 painter-fm vs widget-fm 与实际 elided 串）实锤。修复后回归测试改用 **spy 断言"放得下的标签从不进入 elidedText"**（PyQt6 可给 QFontMetrics 实例打桩，与字体取整方向无关的强断言）。顺带发现：① 离屏复现 harness 实例化 PieMenuEditor 触发自动保存污染了用户 config.json（管线 slots 变扇区 2+3 重复），已还原——**实例化任何会自保存的 UI 组件必须沙箱 `shared.CONFIG_PATH`**；② 环形↔竖排 `panels_to_slots` 写回侧向扇区 + 保留旧扇区存在**命令重复隐患**，与事项 4（无损往返）相关，待用户拍板后处理。

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`、`ui/context_menu_config.py`、`utils/config.py`、`scripts/pie_menu_test.py`（279 断言，新增 8 个 `_elide_fitting` spy 断言）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/pie_menu_diag.py`（临时诊断工具，待删）

---

### 设置面板 UI 修复（用户审查通过）

**问题/需求：** ① 半角附属项在主项关闭后仍显示/可改（应隐藏/禁用 + 缩进）；② 「縦中横」称呼随上游改为「直排内横排」；③ 字体排除按钮宽度不齐；④ 对照预设标签过长，且序号/溢出与视图栏称呼不一致。

**改动要点：** 半角附属项隐藏/禁用 + 缩进；縦中横→直排内横排；字体排除按钮等宽；对照预设标签缩短 + 序号/溢出与视图栏同步称呼。

**涉及文件：** `ui/configpanel.py`、`docs/基础速查/新增设置项路线参考.md`


### 文本样式统一对话框（PS 式 TextStyleDialog）

**问题/需求：** 文本格式交互割裂——顶部不透明度下拉栏、中间独立阴影/渐变弹窗、下方嵌入式变换面板，三种交互模式混杂、有拼凑感。目标：PS 图层样式式统一窗口。

**改动要点：** `ShadowGradientDialog` 升级为 `TextStyleDialog`（三 tab：Basic 不透明度/行距类型、Shadow、Gradient）；编辑副本 + OK/Apply 机制保留，本地预览新增不透明度支持，记住上次停留 tab；主面板内嵌 `TextAdvancedFormatPanel` 删除，替换为 `TextStyleEntryButton` 胶囊入口按钮（`QPushButton[capsule="true"]` QSS 与胶囊标题同款）；变换面板恢复带标题展示——Grid/Projective 变换需画布拖拽锚点，模态对话框会阻断，故不进对话框（对齐 PS 变换/样式分离）。`opacity_presets` 下拉复用保留；`text_advanced_format_panel`/`expand_tadvanced_panel` config 字段保留以兼容旧 config.json。

**涉及文件：** `ui/shadow_gradient_dialog.py`、`ui/text_advanced_format.py`、`ui/text_panel.py`、`ui/mainwindow.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/排版技术.md`

---

### 快捷菜单：环形↔竖排独立化 + 命令池 used 灰显（已停用）

**问题/需求：** 用户实机反馈「部分卡片不可复用，竖排使用的功能卡片到环形菜单出错，强行拖拽添加导致另一个菜单出现预期外复制卡片」。根因是**竖排借用环形半环、切换互转**（list 由 ring 派生、双向写回）——竖排只有半边，无法表达左右两侧都有卡片的环形，任何转换补丁都治标不治本。用户拍板：**Ring 与 List 是同一菜单的两套完全独立布局**（互不相关）。

**改动要点：**
- `ui/pie_menu.py`：删除半环转换函数 `half_ring_sector_idxs`/`slots_to_panels`/`panels_to_slots`（所有转换 bug 的根源，约 40 行）
- `ui/pie_menu_editor.py`：`_on_style_changed` 只切 `layout` 显示、`_on_direction_changed` 只镜像渲染侧（不动 slots）；`_refresh_used` 按「当前菜单 + 当前样式」（ring 读 slots / list 读 panels）独立检测
- `utils/config.py`：`DEFAULT_PIE_MENUS` 管线菜单补默认竖排布局（3 管线命令放正侧面板）；`migrate_legacy_pie` 从旧环形右半侧播种 panels（首次切换不空白）；对齐菜单默认布局修正（align_top/align_bottom 不再重复占顶扇区）
- **used 灰显恢复又停用**：命令池卡片 property 机制恢复（同菜单同样式每命令一份，与去重守卫一致），但**实机 QSS 灰显不生效**——诊断脚本 `_grey_diag.py`（临时，已删）证明 property 实时更新、offscreen 像素也变灰，app 内却始终不显示（根因未明，待修）。用户拍板**停用视觉**（删 QSS 规则），property 逻辑链保留，之后修只需恢复一行样式
- 测试：转换断言全部改写为**独立性断言**（切换不互转/不覆盖、方向翻转不动 slots、同命令可分属两布局但每布局仅一份、真实 dropEvent 路径 property 实时更新）

**涉及文件：** `ui/pie_menu.py`、`ui/pie_menu_editor.py`、`utils/config.py`、`scripts/pie_menu_test.py`、`docs/技术实现/快捷菜单_实现总结.md`

---

### 拾色器改进：屏幕吸色管 + ColorPickerDialog 统一

**问题/需求：** ① 取色只能手动输入/拖滑块，不能直接吸屏幕颜色；② HEX/RGB 复制不便；③ 字体颜色控件（fontstyle_manager）与拾色器两套实现。

**改动要点：** `pick_screen_color()` 屏幕吸色管（`ui/custom_widget/screen_picker.py`：全屏覆盖 + 8x 放大镜，**冻结帧采样 + 事件驱动模态循环**——结构上不会卡 UI；左键取色、右键/Esc 取消）；`ColorPickerDialog` 重构（HEX 输入聚焦全选方便 Ctrl+C、尺寸自适应、去 alpha spin）；`fontstyle_manager` 颜色控件统一到 ColorPickerDialog；`slider.py` 新增 MD3 式 `SliderValueTip` 数值气泡（主题随 paint 实时解析）；offscreen 测试 11 例全绿，i18n/文档已同步。

**涉及文件：** `ui/custom_widget/screen_picker.py`（新增）、`tests/test_screen_picker.py`（新增）、`ui/custom_widget/color_picker.py`、`ui/custom_widget/slider.py`、`ui/custom_widget/__init__.py`、`ui/fontstyle_manager.py`、`ui/shadow_gradient_dialog.py`、`ui/mainwindowbars.py`、`ui/misc.py`、`utils/shared.py`、`config/palette.json`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/基础速查/打包控件功能使用说明.md`、`AGENTS.md`