# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-09-05

### 全局替换静默空转修复（3a 回归：保存路径广播 textstack_changed）+ 当前页脏标记语义定稿

**问题/需求：** 3a 人工验收抽查 S18-3 发现：全局搜索替换确认后无任何效果——文本不替换、不产生脏页、无回滚条。离线复现（真实 MainWindow + 两页项目）定位：`on_replace` 的 prepare 阶段同步落盘经 `ui/canvas.py::update_saved_undostep`，3a 重写该方法（clean 机制）时在末尾调 `on_textstack_changed()` 无条件广播 `textstack_changed`，主窗口接收槽误判"文档变更"调 `set_document_edited()`，把 `searched_pattern` 在收集前抹成 None → 收集器零命中走"无改动"分支静默返回。副作用不止替换：每次项目保存都会清空全局搜索结果面板。3a 前该方法只被动记账不广播，属回归。

**改动要点：**

- `ui/canvas.py`：拆出私有 `_refresh_save_state()`（脏判定计算）；`on_textstack_changed` = 刷新 + 广播；`update_saved_undostep`（保存路径）只刷新保存状态、不再广播——保存不改文档内容。
- **当前页脏标记语义定稿（用户决策）**：全局替换后当前页不标脏、不进重渲询问——`utils/proj_imgtrans.py::mark_page_needs_rerender` 的当前页排除语义维持（画布文本实时更新、结果图随保存/切页重渲）；删除 `ui/mainwindow.py::on_global_replace_finished` 里对当前页的无效标记调用（被排除语义吞掉的死代码）。决策记录补入验收文档附录第 7 条。
- **回归测试**：`tests/test_format_gesture_undo.py` 增 `test_update_saved_undostep_does_not_emit_textstack_changed`（保存不广播、真实栈变更如 undo 仍广播）。
- 验证：端到端离线复现脚本（真实 MainWindow + 两页项目）修复前复现、修复后替换落到画布/面板/数据/JSON 且脏页/回滚条正常；`verify.py` 全绿；test_format_gesture_undo / test_undo_safety_net / test_global_replace_commit / test_render_sync 全过；实机已验收。

**涉及文件：** `ui/canvas.py`、`ui/mainwindow.py`、`tests/test_format_gesture_undo.py`、`docs/技术实现/撤销体系人工验收场景.md`

---

### 撤销体系 3b 落地：文档栈全面禁用 + 旧机制删除（单栈快照命令制收官）

**问题/需求：** 按《撤销体系重构计划》执行阶段 3b——文档私有栈已无存在意义（3a 起撤销/重放全走 text_undo_stack 快照命令），全面禁用并删除旧排水/记账/joint 机制，消除双栈并存的心智负担与内存开销。

**改动要点：**

- **文档栈禁用**：`ui/text_engine/item.py::TextBlkItem.__init__` 与 `ui/textedit_area.py::SourceTextEdit.__init__`（覆盖 TransTextEdit 子类）构造时即 `setUndoRedoEnabled(False)`——所有写入路径（键入/粘贴/程序写入/HTML 装载）天然覆盖；F 清单四处暂存文档（注解装载/剪贴板 MIME/描边克隆/字形测量/全局替换暂存）核实不冲突（保存旧值恢复或独立文档）。
- **C 排水口删除**：item 与面板编辑器的文档级 `undo()`/`redo()` 方法（`document().undo()/redo()` + 步数回读）整体删除——活代码已无调用方（Ctrl+Z 走 `undo_signal` → canvas）。
- **D 步数记账删除**：`old_undo_steps` 读写点、`updateUndoSteps` 方法（item/面板两侧）、`ui/textedit_commands.py::_refresh_undo_steps` 全删；`_suppress_change_sync` 的 `in_redo_undo` 抑制旗保留（重放守卫仍用）。信号签名瘦身：`push_undo_stack` 去步数参数、`propagate_user_edited` 去 `joint_previous` 参数，四个分接 handler（`ui/scenetext_manager.py`）同步改签名。
- **E joint 链删除**：`ui/textedit_commands.py::sync_text_by_diff` 删 `joint_previous` 参数与 `joinPreviousEditBlock` 分支（对账恒为独立编辑块），删水位回写。
- **H 观察项核实**：`setPlainTextAndKeepUndoStack` 的「保留文档历史」语义自然消解，调用方只依赖「全文替换保留区外字符格式」效果，名字保留。
- **测试适配**：test_undo_safety_net / test_format_gesture_undo / test_render_sync / test_global_replace_format 按新信号签名更新布线；全部通过。
- 验证：`verify.py` 全绿（含启动链冒烟）；**剩余：3b 实机人工验收（重点中文 IME 输入）**。

**涉及文件：** `ui/text_engine/item.py`、`ui/textedit_area.py`、`ui/textedit_commands.py`、`ui/scenetext_manager.py`、`tests/test_undo_safety_net.py`、`tests/test_format_gesture_undo.py`、`tests/test_render_sync.py`、`tests/test_global_replace_format.py`、`docs/技术实现/撤销调用点普查.md`、`docs/技术实现/撤销体系重构计划.md`

---

### 技术文档清理：已完成计划/作废文档 3 篇删除 + 索引与状态同步

**问题/需求：** 全量审计 `docs/技术实现/` 中「疑似计划而非技术记录」的文档，按 148483ca 先例清理已完结/作废的计划文档，同步索引与状态行，未决项集中盘点。

**改动要点：**

- **删除 3 篇**：《文本面板选中态与字重失效_修复计划》（阶段 1–5 已于 2026-09-04 全部落地）、《文本面板选中态与字重失效_问题分析》（根因已消化）、《反向移植_i18n补充计划》（2026-09-03 已作废，结论早已记录在反向移植规范 §10 记录表）。有长期价值的内容压缩沉淀：Qt styleName/字重关键事实与 face 派生架构、面板选中态语义 → `docs/基础速查/经验教训.md` 新增 §7「字体与文本渲染」。
- **索引同步**（`docs/项目概述.md` §四）：删 2 行、改 2 行陈旧状态（撤销体系重构计划「待评审」→ 代码层完成剩余 3b 实机验收；查找替换设计方案标注已完结归档），补登记此前缺索引的 `撤销调用点普查.md` 与 `撤销体系人工验收场景.md`。
- **状态回填**：查找替换设计方案 §8 阶段 3/4 行的「待实机目视验收」——阶段 3 与状态头对齐（已实机验收）；阶段 4 注明全局替换链路 2026-09-05 实机端到端复验、格式条件 UI 未单独目视验收。
- **死链清理**：`docs/技术实现/反向移植_规范.md` §10 i18n 补充行删指向已删文档的引用；`ui/textitem.py` 与 2 个测试文件的注释中指向已删修复计划的「见 docs/...」指针摘除（正文说明自洽保留）。
- 保留不动：撤销体系三篇（3b 验收进行中）、效果栈计划（挂起待重启基线）、翻译 agent 化/术语工作台（活交接）、其余记录类。

**涉及文件：** `docs/项目概述.md`、`docs/基础速查/经验教训.md`、`docs/技术实现/反向移植_规范.md`、`docs/技术实现/查找替换与样式管理器重构_设计方案.md`、`ui/textitem.py`、`tests/test_fontfamily_style.py`、`tests/test_selection_state_panel.py`；删除 `docs/技术实现/文本面板选中态与字重失效_修复计划.md`、`docs/技术实现/文本面板选中态与字重失效_问题分析.md`、`docs/技术实现/反向移植_i18n补充计划.md`

---

### 收尾决策回填 + CUSTOM_FONT_FAMILIES 死常量清理

**问题/需求：** 技术文档审计收尾：用户对遗留问题逐项拍板——① 查找替换格式条件 UI 已实机检视，验收关账；② 样式管理器与查找替换查询入口重叠问题暂无好方案，搁置；③ 上游 font_registry 字重选择器经评测不如本 fork 的 face 派生设计，不移植、缝关闭；④ 效果栈（阶段 D 重启 + §7.1 效果组缝）等有空处理；⑤ `CUSTOM_FONT_FAMILIES` 可选清理项按审计意见执行。

**改动要点：**

- **决策回填**：查找替换设计方案状态头改「已完结归档并实机验收」、§7.2 判定改「不移植（保留 face 派生设计）」、§7.3 移植顺序更新（效果组缝保留，指向效果栈计划文档）、§8 阶段 4 验收补检视确认、§10 观察项标搁置；项目概述索引行同步。
- **死常量清理**：`utils/shared.py::CUSTOM_FONT_FAMILIES` 恒空且全库无写入点（`ALL_FONT_FAMILIES` 于 `utils/shared.py::init_font_list` 一次合并写入），删除该常量及两处死分支——`ui/text_panel.py::apply_fontfamily` 的「CUSTOM 归并映射」前置分支（条件恒 False）与 `ui/mainwindow.py::load_textstyle_from_proj_dir` 的 `only_custom` 参数分支（无任何调用方传 True，走该分支会得到空字体列表）。
- 验证：pytest（fontfamily_style / selection_state_panel / font_exclude_dialog / font_scan / global_search_fontstyle）通过；`verify.py` 全绿。

**涉及文件：** `utils/shared.py`、`ui/text_panel.py`、`ui/mainwindow.py`、`docs/技术实现/查找替换与样式管理器重构_设计方案.md`、`docs/项目概述.md`、`docs/基础速查/经验教训.md`

---

### 撤销体系 3b 实机人工验收通过（重构收官）+ 状态回填

**问题/需求：** 用户实机跑完《撤销体系人工验收场景》全部场景（含中文 IME），确认满足预期，问题记录表为空——撤销体系重构（甲-1/甲-2 止血 + 阶段 0 护网/普查/探针 + 3a 单栈快照命令制 + 3b 文档栈禁用）全部收官。

**改动要点：**

- 《撤销体系重构计划》状态头改「已收官（2026-09-05）」，后续演进指向验收场景文档 §五；《撤销调用点普查》标注使命完成留作存档；《撤销体系人工验收场景》头部记录验收结果，保留双重价值（回归参考 + §五 阶段 4 决策记录：撤销上限/历史弹窗/跨页历史等 6 项已拍板待另立计划）；项目概述索引行同步。
- 效果栈（阶段 D 重启 + 上游 §7.1 效果组缝）维持延后，等用户有空处理。

**涉及文件：** `docs/技术实现/撤销体系重构计划.md`、`docs/技术实现/撤销调用点普查.md`、`docs/技术实现/撤销体系人工验收场景.md`、`docs/项目概述.md`

---

## 2026-09-04

### 文本面板选中态与字重真值化五阶段重构落地 + 中间图截断闪退修复 + 启动日志噪音修复

**问题/需求：** 按《文本面板选中态与字重失效_问题分析/修复计划》落地已审计的五阶段修复（字重编辑有时不生效：Qt 的 styleName 精确匹配压过字重、多来源脏 face 名污染渲染）；实机验收另发现两问题：① 样式编辑器改字重后批量重渲染触发闪退（实为损坏的中间图穿透页面加载链路）；② Win10 启动终端刷 `fontTools _n_a_m_e stringOffset incorrect` ERROR ×3 与 `undefined param name: font_family` ×2。

**改动要点：**

- **阶段1 真值化基建**：新增 `utils/face_resolver.py`（`resolve_face` 就近匹配 face / `sync_face` 派生缓存 / `weight_of_face` / `invalidate_face_cache`，Qt styleStringHelper 阈值逻辑作 tie-break）；`font_weight` 成为唯一真值，`_style_name` 降级为派生显示缓存（`utils/fontformat.py::FontFormat` post_init bold 折算进字重）；`utils/shared.py::init_font_list` 尾部失效 face 缓存。
- **渲染端收口**：删 `ui/textitem.py` 的 fork set_fontformat 钉 face 补丁与 setFontFamily 的 style_name 通道（字重失效根因）；`ui/text_engine/item.py` set_fontformat 显式写数据层 face、`_sync_face_char_format` 同事务派生（char format + defaultFont 同步含字重）、get_fontformat 回读 styleName。
- **写入点重派生**：`ui/fontformat_commands.py` 包装器两处 act_ffmt 写点接 `_sync_active_face`；`utils/base_styles.py`（flatten/variant）、`utils/style_query.py`、`ui/fontstyle_manager.py`（sig/preset）undo 快照前 `sync_face`；`ui/mainwindow.py` 管线建块删 `blk.bold` 残留。
- **阶段2 多选镜像+混合态**：`ui/text_panel.py` 新增 `_active_multi_items`；`set_textblk_item` 支持 `multi_items`（优先于单选），`_set_multi_selection` 以活动块（默认最后选中）为镜像 + `_mixed_fields` 逐字段量化混合检测；编辑经包装器重定向广播到全部选中块，退出多选仅单选→闲置做整格式回写。
- **阶段3 闲置态**：无选中时面板镜像 `global_format`（标题「新块默认格式」），编辑实时落地全局默认（`_mirror_to_global_format`），新增 Reset 按钮（`_reset_global_format` 回退 FontFormat() 默认）。
- **阶段4 样式编辑器**：`ui/style_format_editor.py` font_weight 编辑器 getter 对未触碰项透传存量值（治愈 350 被静默改写成 400），"(default)"=None 语义，显式变更经 `weight_of_face`/`resolve_face`。
- **闪退修复**：`utils/proj_imgtrans.py` 修复图/无字图/遮罩三处中间图读取容错（损坏按"文件缺失"降级：修复图回退原图、遮罩置零），根因=早前写入中断留下的截断 PNG 使 `imread` 抛 `OSError: image file is truncated` 穿透 Qt 槽；`load_inpainted_by_imgname` 顺带修了 imread 返回 None 时的 `.shape` 二次崩溃。
- **启动噪音**：`ui/text_panel.py::global_mode` 加 None 守卫（`global_format` 在 mainwindow 构造尾部才注入，此前 `id(None)==id(None)` 误判全局模式，启动期字体下拉填充触发的 font_family 信号被派发到 None 上——既有行为，非本次重构引入）；`utils/font_scan.py::scan_font_faces` 扫描期临时压制 fontTools 日志（Win10 系统字体 name 表畸形只刷 ERROR 不抛错）。
- **测试**：`tests/test_face_resolver.py`（22 例：阈值锚点/就近/幂等/写入点快照保真）、`tests/test_selection_state_panel.py`（7 例多选广播/闲置跟随/回读）、`tests/test_fontfamily_style.py` 重写适配新契约、`tests/test_intermediate_img_robustness.py`（4 例截断中间图降级）。
- **i18n**：ts 新增 3 条（New Block Default Format/Reset/Reset the new-block default format）+ qm 重编译。
- ⚠️ 环境注意：测试曾误触 ultralytics 对 `PIL.Image.open` 的补丁（打开失败即 pip 装 pi-heif），pip 把 Pillow 卸到一半失败；已重装同版本 pillow==10.4.0 修复，pillow_jxl 完好。勿在测试中触达 `load_inpainted_by_imgname` 非当前页分支。
- 验证：`verify.py --full` 全绿（ruff/pytest 未装跳过）；上述 4 个测试套件 + test_font_scan 30 例 + test_batch_backup/test_page_list_dirty_click/test_dependency_startup 全过；实机已验收重构成果与闪退修复。

**涉及文件：** `utils/face_resolver.py`（新增）、`utils/fontformat.py`、`utils/shared.py`、`utils/base_styles.py`、`utils/style_query.py`、`utils/proj_imgtrans.py`、`utils/font_scan.py`、`ui/textitem.py`、`ui/text_engine/item.py`、`ui/fontformat_commands.py`、`ui/fontstyle_manager.py`、`ui/mainwindow.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/style_format_editor.py`、`scripts/audit_registry.json`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_face_resolver.py`（新增）、`tests/test_selection_state_panel.py`（新增）、`tests/test_fontfamily_style.py`、`tests/test_intermediate_img_robustness.py`（新增）、`docs/技术实现/文本面板选中态与字重失效_修复计划.md`、`docs/技术实现/文本面板选中态与字重失效_问题分析.md`

---

### 撤销体系：效果参数并入格式化手势（一次手势=一步）+ 描边色自动跟随文字反色（含设置开关）

**问题/需求：** 实机验收发现：① 描边/行距/字距等效果参数改一次要按好几次 Ctrl+Z 才撤完（除真正修改那一下其余按撤销无视觉变化）——根因=效果类 setter 走 per-emission `push_undostack=True` 单命令（一步一压），与内容参数的手势宏聚合不一致；② 追加新功能：未手动指定的描边色自动取文字颜色反色（黑字白边/白字黑边），默认无声机制、改字色即时跟随，手动指定即置「自定义」标记永久生效（无恢复），设置面板嵌字节加全局开关默认开。

**改动要点：**

- **效果参数并入手势**：`ui/fontformat_commands.py` 删 `TextStyleUndoCommand` 与 `font_formating` 的 `push_undostack` 分支；装饰器 wrapper 在格式变更时对画布会话 `note_formatting_edit` 显式登记（幂等——效果类 setter 不触发 on_content_changed 自登记）；闭合以「基线↔终值」一条 `FormatGestureCommand` 落账（一次手势一步）。隔离调用（无画布，单测直调 ffmt_change_*）跳过手势、仅应用。
- **描边自动反色**：`utils/fontformat.py::FontFormat` 加块级标志 `stroke_color_custom` + `effective_stroke_color(*, auto_follow=True)`（默认自动取前景反色、手动则按 srgb）；`ui/text_engine/item.py` 的 set_fontformat/setFontColor/setStrokeWidth/setStrokeColorCustom 派生站点接入；`setFontColor` 改字色即时重派生反色（零延迟，面板 swatch 取色/右键应用两路径同步刷新）。
- **设置开关**：`utils/config.py::ProgramConfig` 加 `stroke_auto_follow=True`；`ui/configpanel.py` 嵌字→Text formatting 加「描边色跟随文字颜色」勾选（默认开），关闭后未手动指定的块按存档 srgb 渲染、不再联动；手动指定的块（`stroke_color_custom=True`）不受开关影响。
- **护网/测试**：`tests/test_format_gesture_undo.py` 增效果参数一次手势=一步回归用例（每参数独立单块画布隔离 QUndoStack 计数态）。
- 验证：`verify.py` 全绿（语法/docs/审计/i18n/qm/冒烟——configpanel 属启动链）；pytest 相关套件（test_format_gesture_undo / test_fontfamily_style / test_selection_state_panel / test_config_fields / test_startup_imports）全过；实机已验收。

**涉及文件：** `ui/fontformat_commands.py`、`ui/text_engine/item.py`、`ui/text_panel.py`、`ui/configpanel.py`、`utils/fontformat.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_format_gesture_undo.py`、`docs/技术实现/撤销体系人工验收场景.md`

---

## 2026-09-03

### 效果栈面板阶段 D-2 重做 + 两 bug 修复 + 点角标闪现弹窗修复

**问题/需求：** 阶段 D 首版直译上游 UI 在 340px 窄栏不适配（下游反馈：Add 按钮无响应、快速预览幽灵小号文本、配色与部分控件样式不统一、混合模式/渐变交互可借鉴）。随后实机复验再反馈：加描边后点击文本框触发调整大小时必现一个"出现即消失的弹窗"。

**改动要点：**

- **面板重做（均衡裁剪）**：`ui/text_engine/effects/gradient_editor.py` 保留渐变条+时钟表盘+色块，砍停点不透明度/位置数值框，留角度+缩放；渐变编辑器卡片内联；混合模式保留上游二级菜单。
- **卡片重构**：`ui/text_engine/effects/cards.py` 头部瘦身（类型/位置选择器移入参数区），参数区对齐 TransformParameterPanel 规范（标签右对齐/22px 填充输入框/两列网格，span2=填充+色块、混合、渐变编辑器）；GroupFrame 包卡片+空栈隐藏；`_fit_effect_selector` 按自身最长条目采样宽度。
- **QSS**：`config/stylesheet.css` 重皮下划线→fork 填充风格；`QFrame#TextEffectCardsFrame`；`GradientValueEditor`（QDoubleSpinBox）也走 `TextEffectParamEditor` objectName，需补 QDoubleSpinBox 规则（QLineEdit 选择器匹配不到 spinbox）。
- **Bug① Add 无响应**：`ui/text_engine/effects/panel.py` 漏 `setMenu(add_menu)`——menu/action 都建好但没挂按钮，InstantPopup 无菜单点击无反应。
- **Bug② 幽灵小号文本**：`ui/textitem.py::_draw_effects_pixmap` 点绘制依赖 pixmap DPR，而 `renderer.py::_new_effect_pixmap` 仅在 `render_scale >= 1.0` 设 DPR → 0.5 档缓存被 1:1 画出半尺寸。修复=scale<1 时矩形拉伸绘制（对齐渲染器内部 `_draw_surface_pixmap` 语义）。
- **闪现弹窗**：`ui/text_engine/shape_control.py` 旋转角标——`ControlBlockItem.mousePressEvent` 的 rotate-zone 分支裸击就调 `updateAngleLabelPos()`（显示 "0.0°"），`mouseReleaseEvent` 再 hide，一次 press+release 无拖拽=角标闪现。修复=去掉 press 里的 `updateAngleLabelPos()`（mouseMoveEvent DRAG_ROTATE 分支已会在真拖拽时显示）。
- 聚合本批：VisitedLink 一行修复（`ui/mainwindow.py` 删 `QPalette.ColorRole.VisitedLink` 颜色覆盖）。

**涉及文件：** `ui/text_engine/effects/panel.py`、`ui/text_engine/effects/cards.py`、`ui/text_engine/effects/gradient_editor.py`、`ui/text_engine/effects/edit_session.py`、`ui/textitem.py`、`ui/text_engine/shape_control.py`、`ui/custom_widget/combobox.py`、`ui/custom_widget/view_panel.py`、`ui/style_format_editor.py`、`ui/text_panel.py`、`ui/mainwindow.py`、`ui/text_engine/rendering/shadow.py`、`ui/text_engine/rendering/__init__.py`、`ui/text_engine/editing/upstream_commands.py`、`utils/base_styles.py`、`utils/config.py`、`utils/style_query.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`icons/text-effect-*.svg`、`tests/*`、`scripts/audit_registry.json`、`ui/text_style_dock.py`（删除）

---
