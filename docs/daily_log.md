# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

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

## 2026-08-05

### 文本引擎移植 阶段 4：变换系统（节点 A–F 完成）

**问题/需求：** 移植上游 v1.5.9 文本引擎变换系统（`transforms/` mapping +
registry、`rendering/` glyph_slant + surface、`SetTextTransformCommand`、控制器
`geometry.py` 接线与布局 custom_rendering 分支）。**中性态（空变换栈 +
glyph_slant 0）= 零行为变更**，逐节点独立验收并写进度文档。

**改动要点：**

- 节点 A：新增 `ui/text_engine/transforms/` 包（mapping/registry/bend/sine/grid），
  grid 按路线图走 NumPy 路径（不移植 grid_numba）。
- 节点 B：`rendering/glyph.py` 占位替换为完整实现（glyph runs、ink shear、fallback
  raster）；新增 `rendering/glyph_slant.py`（逐行旋转 + 縦中横守卫，本地特有）与
  `rendering/surface.py`（cv2.remap 非线性变形）。
- 节点 C：`geometry.py` import 从 `_stubs` 切换到真实模块，删除 `_stubs.py`。
- 节点 D：`scene_textlayout.py` 补 `vertical_rotation_chars`、draw() render_delegate
  分发（uniform 快路径 + 逐行 draw_vertical_line）、hitTest map_input_point 等。
- 节点 E：`textitem.py` 加 `set_text_transform`/`clear_text_transform_preview`；
  新建 `ui/text_engine/editing/commands.py`（快照式 `SetTextTransformCommand`）；
  `fontformat_commands.py` 两个 no-op funcmap 激活为真实快照提交。
- 节点 F：新增 `tests/test_text_transform_engine.py`（21 用例：模型 14 + undo 1 +
  渲染 5 + 本地縦中横 slant 像素 1），全量回归通过；`ts/qm` 补
  `[StyleDetail] "Font Style"` 缺失条目（前置既有债务，顺手修复）。

**排障记录：** 縦中横 slant 测试两根因——逐行旋转只查行首 char（文本须 tcy run
领行）＋ bare reLayout 后 QGraphicsScene.render 跳过 item.paint（需 item.update() +
processEvents）；零 slant preview 下 `_repaint_neutral_background` 本地至多 2 次
（上游 1 次）；stroke 效果 preview→clear 周期 padding 漂移致像素不可精确复现，
改 A/B 差异断言。回归发现 2 项前置既有失败（非本阶段引入）：`launch.py`
`prepare_environment` 无字面 `return True`（测试与代码脱节）、`test_psd_binary.py`
依赖未安装的 pytest。

**涉及文件：** `ui/text_engine/`（transforms/、rendering/、editing/ 新增 +
geometry.py + `_stubs.py` 删除）、`ui/scene_textlayout.py`、`ui/textitem.py`、
`ui/fontformat_commands.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、
`tests/test_text_transform_engine.py`（新增）、
`docs/技术实现/文本引擎移植_阶段4_变换系统.md`（新增）

---

## 2026-08-05

### 阶段 4 验收 + 阶段 5 接手导航文档

**问题/需求：** 阶段 4 经用户审查通过；阶段 5（UI 自研）将新开对话由新 AI 接手，
需要一个唯一入口文档，让接手 AI 读完即有明确目标与参考路径。

**改动要点：**

- 用户已确认阶段 4 验收，未改代码。
- 新增 `docs/技术实现/文本引擎移植_阶段5_UI自研.md`：阶段 5 目标（路线图 §6/§7/§5.6
  原文摘录）、用户约束（UI 样式未定、借鉴上游思路、节点化 + 每节点写文档）、当前基线
  （阶段 0–4 完成、21 测试全绿、i18n exit-4 基线、2 项 pre-existing 失败勿追）、
  参考路径（上游 v1.5.10 `git show` 命令、本地已就绪的 registry/geometry/textitem/
  shape-control API 行号表）、建议节点拆分 G–K、验收口径、工作流约束。
- 接手续读顺序：先读该文档，再按需深入 `docs/技术实现/文本引擎架构跟进.md` §7。

**涉及文件：** `docs/技术实现/文本引擎移植_阶段5_UI自研.md`（新增）、
`docs/daily_log.md`

---

## 2026-08-06

### 文本引擎移植 阶段 5 验收问题修复（节点 L）

**问题/需求：** 用户真实启动 app 验收阶段 5 后报三个问题：① 编辑使用高级变换
的文本框时点击/双击会闪现一个"0.0°"小窗口；② 网格变换在面板改网格数后画布
不出现可拖拽手柄；③ 文本阴影受文本框范围裁剪（"虚拟框"，上游无此现象）。

**改动要点：**

- 问题 1：`ui/texteditshapecontrol.py` 的 `ControlBlockItem.mousePressEvent`
  旋转分支不再调用 `updateAngleLabelPos()`——angleLabel 只在真正旋转拖动
  （mouseMoveEvent）时显示。根因：变换后视觉轮廓把 8 个设备尺寸手柄带进文字
  区域，点击命中手柄旋转区即闪现标签。
- 问题 2：新建 `ui/text_engine/transforms/grid_control.py`（上游 grid_control
  减法版：采样网格线 + 每格点手柄 + 单次手势一轮 begin/preview/commit），
  `ui/canvas.py` 增加 `textGridControl` + `bind_text_grid_control` /
  `clear_text_transform_controls`（清空点/建块/翻页路径同步清理，空点判断改为
  含 `CONTROL_ITEM_DATA_KEY`），`editor.py` `_sync_transform_controller` 改按
  方法 hasattr 守卫（projective 未实现时安全走 clear）。
- 问题 3：`ui/text_engine/effect_renderer.py` `_repaint_neutral_background`
  开头补 `_update_effect_padding()`——中性路径此前漏掉 padding 推导，阴影半径/
  偏移超过旧 `setShadow()` 启发式时被 boundingRect 边缘截断。

**排障记录：** PyQt6 无法实例化 `QGraphicsSceneMouseEvent`（合成点击不可行，
改静态命中验证 + 直接驱动会话回调）；`refresh_controls` 在 `controls=None` 时
提前 return（真实 app 面板恒非 None）；未 show 的 view 下 QLabel 断言用
`isHidden()` 而非 `isVisible()`。

**涉及文件：** `ui/text_engine/transforms/grid_control.py`（新增）、
`ui/canvas.py`、`ui/texteditshapecontrol.py`、`ui/text_engine/effect_renderer.py`、
`ui/text_engine/transforms/editor.py`、`tests/test_text_transform_ui.py`
（+5 用例 → 30）、`docs/技术实现/文本引擎移植_阶段5_UI自研.md`（节点 L 记录）

---

## 2026-08-07

### 文本引擎移植收尾：变换面板样式 + 拖拽装饰开关 + 高缩放缓存策略

**问题/需求：** 移植阶段 3-5 为保稳定性未带项目自定义样式，新增文本变换面板
（正弦波/弯曲/网格/透视卡片）视觉与主题规范不符；随后实测发现两个性能体验问题：
① 重样式（描边/阴影）文本块拖拽调整时每帧重建背景（含高斯模糊）导致帧率极低；
② 画布缩放到 300% 以上平移时，新进入视口的文本块需构建设备分辨率级巨型缓存位图
（1000% 下一个 300px 块 ≈ 3000px 宽），产生明显卡顿。

**改动要点：**

- 变换面板样式：`config/stylesheet.css` 补编辑器/下拉框 focus/hover/disabled 态、
  整数步进器 padding-right（ID+attr 组合选择器解决特异性）、Add 按钮 pressed 态、
  透明容器；`panel.py` 步进器 hover 高亮改 `get_theme_color(@accentPrimary, alpha=32)`。
- 拖拽装饰开关：恢复 `show_decorations_during_drag`（默认 False）；`effect_renderer`
  `repaint_background` 在 reshaping 且关闭时跳过（背景保持清除、paint 走原生文本）；
  `startReshape` 开启时立即重建；设置面板 General→Performance 恢复勾选项（含 i18n）。
- 高缩放缓存策略：`textitem.py` 新增 `HIGH_ZOOM_CACHE_LIMIT=3.0`，`refresh_cache_policy`
  按 `get_scale()` 切换 DeviceCoordinateCache↔NoCache；`scenetext_manager._rebuild_item_caches`
  （zoom 防抖）补策略刷新，越过阈值自动切换。

**排障记录：** 离屏基准实测 DeviceCoordinateCache 光栅化成本随缩放平方增长
（8 块：1×=29ms / 2×=108ms / 4×=420ms / 10×=2420ms），NoCache 任意缩放 ~2ms；
阈值初定 4.0，用户实测仍有感知后下探至 3.0。

**涉及文件：** `config/stylesheet.css`、`ui/text_engine/transforms/panel.py`、
`utils/config.py`、`ui/text_engine/effect_renderer.py`、`ui/textitem.py`、
`ui/configpanel.py`、`ui/scenetext_manager.py`、`translate/zh_CN.ts`、
`translate/zh_CN.qm`、`docs/daily_log.md`

---

## 2026-08-08

### 快捷键设置面板页面化重构（删除子对话框 + hover tooltip）

**问题/需求：** 快捷键编辑器沿用早期"单页滚动 + 分组"设计，经 Interface 页按钮弹
`ShortcutDialog` 子对话框编辑；设置面板已是"导航树 + 独立页面"的现代布局，快捷键窗口
显得割裂。另外行内 + / Del / Rst 按钮的 `setToolTip` 在 Windows 上渲染为黑底黑字不可读，
功能理解成本低，决定删除。

**改动要点：**

- 快捷键编辑改为设置面板独立页面：导航树 General 分组新增 "Shortcuts"（Interface 与
  Config Management 之间），注册走 `_add_page` 平铺模式（与 LLM Profile 页一致，
  页面统一提供滚动）。
- 删除 `ShortcutDialog` 类、`_open_shortcut_dialog`、Interface 页 "Edit Shortcuts..." 按钮；
  `ShortcutEditor` 去掉内部 `QScrollArea` 与 `setMinimumHeight`。
- 快捷键**实时生效**：编辑任一动作即 `save_config()` + emit `shortcuts_changed` →
  `MainWindow.refreshShortcuts()`（原为对话框关闭后才刷新）。
- 删除 `_ShortcutRow` 三处 `setToolTip`（Add shortcut / Disable this shortcut /
  Reset to Default），修复黑底黑字问题。
- i18n：清 `ShortcutDialog` context、"Edit Shortcuts..." 与三个 tooltip 的 ts 条目；
  `ContextMenuCustomizeDialog` 的 "Reset to Default" 按钮翻译保留。

**涉及文件：** `ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、
`docs/daily_log.md`

---

### View 菜单增强：画板/编辑器互斥 + 序号徽标/溢出裁剪快捷开关

**问题/需求：** 画板与文本编辑器模式此前只能经底部栏切换，View 菜单的入口不可见；
序号徽标与溢出裁剪开关仅存在于设置面板，高频操作路径偏长。

**改动要点：**

- `TitleBar` 的 Drawing Board / Text Editor 动作改为 checkable，加入互斥
  `QActionGroup`，勾选态=当前激活模式；底部栏模式切换时经 `_sync_view_mode_actions`
  回写 View 菜单勾选。
- View 菜单新增 Sequence Badge / Overflow Clip 两个可勾选动作，与设置面板
  `seq_badge_checker` / `clip_overflow_checker` 双向同步（`blockSignals` 防回环），
  勾选即改 `pcfg` 并 `save_config()`。
- i18n：`TitleBar` 上下文补 "Drawing Board" / "Text Editor" / "Sequence Badge" /
  "Overflow Clip" 词条（部分与 `_ShortcutRow`/`ConfigPanel` 上下文重复，需各自保留），
  qm 重编译。

**涉及文件：** `ui/mainwindowbars.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、
`translate/zh_CN.qm`、`docs/daily_log.md`

---

### PSD 导出重做：JSX 批量路线（弃用二进制 EngineData）

**问题/需求：** 封存的 PSD 导出有三条旧路线的顽疾——二进制直写（`TySh`+`EngineData`
手写序列化，PS 打开兼容问题无法解决）、COM 自动化（已删）、旧 JSX（每页一个脚本，
要手动逐个跑）。调研 LabelPlus PS 脚本分支
[ZsIsMe/PS-Script](https://github.com/ZsIsMe/PS-Script) 后确认：**可编辑文本层只能在
PS 进程内用 DOM API 创建**。重做为"Python 生成单个自包含 .jsx → PS 里跑一次 → 全部页
产出可编辑文本层 PSD"，修复三个痛点（EngineData 弃用 / 批量单脚本 / 中心对齐兜底保真度）。

**改动要点：**

- `utils/psd_jsx_exporter.py` 重写：内嵌 Meo 风格 JSON 的批量脚本模板（UTF-8 with BOM）；
  文本层用 `artLayers.add()+LayerKind.TEXT`+`textItem.*`（contents/font/size_pt/color/
  fauxBold/direction/justification/autoLeading/position）；旋转取负（BT 逆时针→PS 顺时针）；
  描边/投影走图层样式 ActionDescriptor（frameFX/dropShadow）；**中心对齐兜底**（按 bounds
  平移使文本中心=块盒中心）；新增 `export_batch()` 一次产出全部页脚本。
- payload 换算对齐渲染器：`size_pt = font_size×72/图片DPI`；`stroke_size = font_size×
  stroke_width`（纠正旧代码 `×0.5`，与 effect_renderer 一致）；竖排 rotation 直取
  `blk.angle`（-90 已内置，无需修正）。
- `utils/psd_exporter.py`：`ExportOptions.export_method` 默认改 `jsx`、新增 `center_align`；
  `AbstractPsdExporter` 加 `export_batch` 基类默认实现。
- `ui/psd_export_dialog.py`：去掉 Export Method 下拉（JSX-only），新增「居中」勾选，更新说明。
- `ui/mainwindow.py`：`on_export_psd` 改同步 `export_batch`，删除线程/进度相关 4 个 handler。
- `ui/mainwindowbars.py`：菜单项**暂时禁用**（"Under Repair"），等修复后再启用。
- i18n：ts 新增 9 条 + 清 11 条旧 PSD 条目，qm 重编译，i18n_check 全绿。
- `pyproject.toml`：pytest 加 `pythonpath = ["."]`（捆绑 python 为 isolated 模式，忽略
  PYTHONPATH/cwd，此前 test_psd_binary 无法收集）；捆绑环境补装 pytest。
- 新增 `tests/test_psd_jsx_export.py`（14 用例），全量 pytest 159 通过（1 项为既有
  `prepare_environment` 失败，非本次引入）。
- 新文档 `docs/技术实现/PSD导出_JSX路线.md`（机制/保真度策略/已知限制/Phase 2 扩展点）。

**涉及文件：** `utils/psd_jsx_exporter.py`、`utils/psd_exporter.py`、`ui/psd_export_dialog.py`、
`ui/mainwindow.py`、`ui/mainwindowbars.py`、`pyproject.toml`、`translate/zh_CN.ts`、
`translate/zh_CN.qm`、`tests/test_psd_jsx_export.py`（新增）、`tests/test_psd_binary.py`、
`docs/技术实现/PSD导出_JSX路线.md`（新增）、`docs/daily_log.md`

> **⚠️ 状态：用户已审查，反馈"可见问题较多"（问题清单未提供）。2026-08-09 决定
> **暂且禁用**（菜单恢复 "Under Repair" 禁用态）并随本次提交入库，修复后再启用。
> 续作先取评审问题清单逐项修复，确认后启用入口并提交。

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
