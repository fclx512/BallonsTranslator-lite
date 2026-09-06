# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-09-06

### 挂账清理批次：加粗拖拽残留 / 攸望竹竖排撑爆 / 饼菜单灰显 / PSD 导出删除 / face 元组崩溃 / 历史面板自开

**问题/需求：** 记忆挂账批量清理 + 用户实机新报两 bug。全部经实机验收。

**改动要点：**

- **设置面板导航加粗拖拽残留**：`ui/configpanel.py::ConfigTable.selectionChanged` 以 `currentIndex()` 作加粗目标，但 Qt 按住拖拽路径下 select 信号先于 current 更新发出（滞后一拍），旧项被清粗体后又被重新加粗且无后续清理路径。改用本次真正选中的项（`selected.indexes()[0]`）。
- **攸望竹带体竖排撑爆（方案 B）**：`ui/text_engine/layout.py::get_punc_rect` 加 `_rect_is_sane` 防护——DirectWrite 对零 advance 字形返回近 1e5 哨兵矩形，超字号 50 倍判退化回退 `boundingRect`，缓存层生效横竖排全覆盖。新增 `tests/test_punc_rect_sentinel.py`（mock 哨兵，offscreen 无法真复现）。
- **饼菜单 used 灰显恢复**：2026-08-16 因实机不生效下线的视觉已恢复——`[used="true"]` QSS 规则加回 `ui/pie_menu_editor.py::CommandPalette.set_commands`，且不再单靠后代选择器刷新（当年实机根因）：`_CommandCard.set_used` 对 name_label 内联直接套 disabled_clr + 卡片及子控件一并 unpolish/polish。实机确认生效。
- **PSD 导出功能整体删除**：JSX 路线实机问题无法收敛，等更强的 AI 修复能力再重启。删 11 文件（utils/psd_* 7 个 + font_mapping + ui/psd_export_dialog + 两个测试），连带清 mainwindow/mainwindowbars/io_thread 死线程/text_style_dock 提示/ts 条目/manifest 重生成；技术状态存档 `docs/技术实现/PSD导出_存档.md`（重启凭据=git fd90b31）；全部登记 audit_registry deprecated。`FONT_PS_NAMES` 保留（一键精简别名补录消费）。原技术文档已于 09-02 先行删除，存档文档为重启唯一凭据。
- **face_resolver 元组崩溃（实机报错）**：`utils/face_resolver.py::resolve_face` 多候选并列兜底分支 `min(candidates, key=...)` 返回整个 `(名,字重,斜体)` 元组而非 `f[0]` 名字，写入 `_style_name` 后下游 `findText(tuple)` 崩。补 `[0]`；`utils/fontformat.py::__post_init__` 加非 str 归一（防已落盘的 JSON 数组残留）；回归测试入 test_face_resolver。
- **历史面板启动自开**：`ui/mainwindow.py` 启动清 `*_dock_open` 清单漏了阶段 4 新增的 `history_dock_open`，上次会话的开合记忆复活。补入清单。
- **ConfigComboBox(options=) 构造参数**：原只支持「先构造再 addItems」，构造期传 `options=` 直接 TypeError。`ui/custom_widget/combobox.py` 构造函数接受 `options=`（等价构造后 addItems）。回归测试 `tests/test_config_combobox_options.py`（新）。
- **杂项清理**：删 `.git/backup-stale/`（08-13 仓库损坏事件的 88MB 残留包，远端恢复早已验证）；AGENTS.md/项目概述清掉「scene_textlayout.py 已废弃待删」过时描述（实际已随 a629ca5 删除）。

**涉及文件：** `ui/configpanel.py`、`ui/text_engine/layout.py`、`tests/test_punc_rect_sentinel.py`（新）、`ui/pie_menu_editor.py`、`scripts/pie_menu_test.py`、`utils/face_resolver.py`、`utils/fontformat.py`、`tests/test_face_resolver.py`、`ui/mainwindow.py`、PSD 批次（`utils/psd_*.py`×7、`utils/font_mapping.py`、`ui/psd_export_dialog.py`、`tests/test_psd_*.py`×2、`ui/io_thread.py`、`ui/mainwindowbars.py`、`ui/text_style_dock.py`、`utils/shared.py`、`tests/test_font_scan.py`、`docs/技术实现/PSD导出_存档.md`（新）、`docs/项目概述.md`、`docs/技术实现/反向移植_规范.md`、`scripts/audit_registry.json`、`manifest.json`、`translate/zh_CN.ts`）

---

### 撤销体系阶段 4 第二批落地：跨页批量组化 + 撤销确认弹窗（含 GC 悬空闪退修复）+ MainWindow 在线演练台

**问题/需求：** 三批节奏的第二批（决策 4）：整理换行/高级对齐等跨页批量命令撤销前弹确认框。用户另拍板两事：修复历史并入全局栈定稿（第三批方案，单一栈双视图+端点快照）、不常用功能工具箱收纳另立规划。本批已实机验收。

**改动要点：**

- **组化标记**：`ui/textedit_commands.py::NormalizeBreaksCommand` 与 `ui/mainwindow.py::_PointAlignCommand` 暴露 `group_undo_summary()`（页名→块数）+ `group_page_generations` 构造期多页代数快照——任一涉及页被管线重写即整组僵尸化（此前只查标签页，跨页端点快照会写错块）；`command_page_stale` 对组化命令逐页比对。
- **撤销确认门**：`ui/canvas.py::_confirm_group_undo`——跨页组命令 undo 前弹确认框（标题=操作名、每页块数明细、勾选「同时重渲染」）；拒绝则本步不执行。豁免三路：僵尸步、历史面板跳转（auto_cross_page）、仅影响当前页；redo 不设确认。
- **「同时重渲染」保留撤销历史**：`_rerender_dirty_pages` 加 `clear_history` 参数，组化撤销路径传 False（数据未变仅重渲，清栈会丢 redo 能力）；既有调用方不变。
- **标脏补缺**：两条组化命令 redo/undo 对非当前涉及页 `mark_page_needs_rerender`（此前改他页数据不标脏）；**`_PointAlignCommand` 补齐锚点化**（item 引用改 blk 身份，执行期 `resolve_blk_item` 重解析——原实现重放到场景重建后的隐形 item）。
- **闪退修复（实机验收发现，GC 悬空 AV）**：确认弹窗复选框无父构造传 `setCheckBox` 不留引用 → PyQt6/Qt6.11 下被 GC 回收 → 悬空指针 access violation 无声闪退，AV 行号随 GC 时机漂移极难定位。修复=构造期挂父 `QCheckBox(text, box)`。完整教训入经验教训 §3.3（PyQt6 所有权陷阱 + 竞态排查纪律：每配置 ≥4 轮，单轮结论不可信）。
- **MainWindow 在线演练台常驻化**：`scripts/mw_repro.py`——拉真实主窗口（必须窗口模式）跑预设场景（`--scenario group-undo` 组化撤销全链路，自动点确认弹窗 ≥200ms）或 `--project` 只读打开真实工程；faulthandler+看门狗常开。登记 scripts/README 与 AGENTS 测试流程第 8 步。
- **规划记录**：第三批定稿（修复并入全局栈：单一栈双视图+端点快照+页代数扩展图像侧+3a/3b 分步）与工具箱收纳规划回填计划文档；新立 `docs/技术实现/不常用功能工具箱_规划.md`（无排期）。
- **护网**：`tests/test_undo_group_confirm.py`（新，11 条：摘要/多页代数僵尸/确认拒绝与放行/auto 豁免/单页豁免/标脏/blk 锚点重解析/面板摘要）；i18n 补 Canvas/HistoryPanel 上下文 6 条。

**涉及文件：** `ui/canvas.py`、`ui/textedit_commands.py`、`ui/mainwindow.py`、`ui/history_panel.py`、`tests/test_undo_group_confirm.py`（新）、`translate/zh_CN.ts`、`scripts/mw_repro.py`（新）、`scripts/README.md`、`AGENTS.md`、`docs/技术实现/撤销体系阶段4计划.md`、`docs/技术实现/不常用功能工具箱_规划.md`（新）、`docs/基础速查/经验教训.md`

---

## 2026-09-05

### 撤销体系阶段 4 第一批落地：跨页编辑历史 + 历史面板二期（按页分组/紧凑化/PS 式操作名）

**问题/需求：** 撤销主重构（0→3b）收官后的阶段 4 演进：跨页历史（决策 3）、批量组化（决策 4）、修复瘦身（决策 5）分三批实施。用户三拍板：跨页边界体验=「再按一次才继续」、全局替换保持快照分治不并入栈、先做跨页历史。计划文档 `docs/技术实现/撤销体系阶段4计划.md` 入库。本批为第一批，已实机验收（含两项 UI 细化反馈当场落地）。

**改动要点：**

- **命令 blk 锚点化**：活文本命令不再持 `TextBlkItem`/widget 引用（切页重建后 Python 对象仍活但脱离场景，重放会静默写隐形对象），改持 `(pagename, TextBlock 对象)` 锚点 + 快照，undo/redo 前按 blk 身份从 `ui/scenetext_manager.py` 场景列表重解析 live item/配对面板（`ui/textedit_commands.py::resolve_blk_entry`；键入/格式/几何/替换/粘贴/管线运行命令全量改造）。原「RuntimeError 僵尸防御」实际防不住脱离场景的活对象，重解析是根治。
- **切页不清文本栈**：`ui/mainwindow.py` 两处切页改 `commit_edit_sessions()`（旧页会话落账）+ `ui/canvas.py::prepare_page_switch`（仅清页级绘制栈）；清栈语义重定义——切页/翻译回填/当前页重载只作废该页历史，工程重载/txt 导入/批量重渲仍整栈 clear。
- **页屏障双通道**：检测管线整体换 blk_list → `utils/proj_imgtrans.py::bump_page_generation`（页代数计数，命令入栈捕获、执行期不一致=僵尸）；原地整页重写（整页翻译回填 `updateTranslation`、txt 导入非当前页）→ `ui/canvas.py::invalidate_text_history_for_page` 显式标记。僵尸命令保留栈位置、undo/redo 无操作 + 「已跳过」toast + 面板灰显。
- **armed 跨页撤销门**：`ui/canvas.py::_gate_cross_page`——下一命令所属页非当前页时第一按只 toast 提示、第二按经 `page_jump_requested` 信号 → `ui/mainwindow.py::on_undo_page_jump_requested` 走完整切页链路后续撤（一次按键=跳页+撤一步）；僵尸步不拦不跳页；历史面板行点击走 `auto_cross_page=True` 直接跳页（显式意图）。跳页链路可能落账旧页 transform 提交：门内重查下一栈位，已变则交还下次按键（线性序不被打破）。
- **项目级保存点**：cleanIndex 语义升级为「此位置前所有页编辑均已保存」，与切页条件保存/项目落盘天然对齐；越 cleanIndex 撤销即正确转脏。
- **历史面板二期**：QUndoStack 无公开列表模型（QUndoView 走私有模型不可分组），`ui/history_panel.py` 重写为自定义 `QAbstractListModel`——页头分组行 + 命令状态行 + 首行原始状态，行号=栈位置；紧凑行高/小字号/176px 默认宽/超长省略；保存点圆点、僵尸灰显加「已过期」后缀。
- **PS 式操作名**：格式化手势落账对比前后 FontFormat 字段归组，行名细化为「格式化：字号/字体/字重/颜色/描边宽度/对齐/文字样式/行距/字距/不透明度/阴影/渐变/变换/效果/排版细节」，多组=「格式化（多项）」，纯逐字符改动回退笼统「格式化」；`ui/text_engine/editing/commands.py::SetTextTransformCommand` 补「变换」名（原空名）。
- **存量测试修复**（HEAD 即失败，独立提交 54ca468）：test_base_styles 期望对齐 bold→font_weight 真值化语义；test_rail_docks 桩补 history launcher/dock 属性（`_iter_docks` 五键清单扩容后缺属性在 `__new__` 桩上 RuntimeError 并中断整条 pytest）。
- **测试**：新增 `tests/test_undo_cross_page.py` 9 条（页标签/代数捕获、armed 二次确认、redo 对称、僵尸跳过、代数失效、切页保栈、场景重建重解析、面板分组）；test_history_panel/test_page_list_dirty_click 适配；`verify.py --full` 全绿。

**涉及文件：** `utils/proj_imgtrans.py`、`ui/textedit_commands.py`、`ui/canvas.py`、`ui/drawing_commands.py`、`ui/mainwindow.py`、`ui/scenetext_manager.py`、`ui/module_manager.py`、`ui/history_panel.py`、`ui/text_engine/editing/commands.py`、`tests/test_undo_cross_page.py`（新）、`tests/test_history_panel.py`、`tests/test_page_list_dirty_click.py`、`tests/test_base_styles.py`、`tests/test_rail_docks.py`、`translate/zh_CN.ts`、`docs/技术实现/撤销体系阶段4计划.md`（新）、`docs/技术实现/撤销体系人工验收场景.md`

---

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

### 撤销体系增强：步数上限一期（决策 1）+ 撤回行为名提示 + PS 式历史面板一期（决策 2，文本栈）

**问题/需求：** 撤销体系 3b 收官后的阶段 4 演进第一批：落实验收文档 §五 决策 1（Blender 式最大撤销步数）与决策 2（撤销历史可视化），另加撤回时显示行为名；用户拍板历史面板一期仅文本栈（绘制栈在窄栏无入口，搁置；撤回本身不受影响）。

**改动要点：**

- **撤销步数上限**：`utils/config.py::ProgramConfig` 新增 `undo_steps_limit`（默认 0=无限，范围 0–500，UI 直接显示数字 0 不用特殊文本）；`ui/canvas.py::apply_undo_limit` 两栈同效，`push_draw_command` 截断时同步平移绘制栈手工计数器（保存点被截落 -1 哨兵，保持「未保存」不误报）。**⚠️ Qt 限制（实测纠正）**：`setUndoLimit` 仅在空栈上生效、非空栈调用被忽略（Qt 文档化行为，Qt4 起如此；早期探针恰在空栈设置误判「惰性缩容」）——启动时栈空立即生效，会话中途改设置由下次清栈（切页/整页管线的各 `clear_*` 路径再应用）落地，UI 说明已注明。前置实测另固化：保存点被截断时 cleanIndex 落 -1、`isClean()` 恒 False，不假报「已保存」，无需自造判定。
- **命令命名清单**：活代码 26 个 QUndoCommand 类（textedit_commands/scenetext_manager/drawing_commands/canvas/fontstyle_manager_commands/mainwindow）构造时统一带 `UndoCommand` 上下文翻译文本（原先全为空名），历史面板行名与撤回 toast 共用此清单。
- **撤回 toast**：`ui/canvas.py::_notify_undo`——文本栈 undo 后通知中心显示「撤销：<行为名>」，key="undo" 去重刷新（连按不弹一排），空步不提示；历史面板跳转期间经 `_suppress_undo_toast` 抑制。Ctrl+Z 两条入口（`undo`/`undo_textedit`）都接。
- **PS 式历史面板**：新增 `ui/history_panel.py::HistoryPanel`——QUndoView 渲染（`setEmptyLabel` 首行=原始状态；探针实测行号=栈位置直接映射，当前位高亮/截断/清栈自动跟随），点击行循环调用 `canvas.undo()/redo()` 逐步跳转（复用已验收的每步记账），cleanIndex 行右缘圆点标保存点；新增 `icons/rail_history.svg`，窄栏 launcher（`ui/text_panel.py::install_history_launcher`，开合记忆 `history_dock_open`，`_iter_docks` 互斥扩五）。
- **测试**：新增 `tests/test_undo_limit.py`（10 例：Qt 截断×clean 语义固化 + 绘制栈计数镜像同步 + 非空栈 setUndoLimit 无效 + 中途改设置经切页生效）、`tests/test_history_panel.py`（6 例：命令命名抽查 / toast 守卫 / 跳转映射 / 截断后行映射）。
- **文档**：验收场景 §五 决策 1/2 回填落地记录（决策 2 形态由「历史弹窗」改定为窄栏面板，跨页分组日后并入）。
- 验证：`verify.py` 全绿（12 改动文件语法/文档/审计/i18n/qm/冒烟）；两测试文件全绿；实机已验收。

**涉及文件：** `utils/config.py`、`ui/canvas.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/history_panel.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/textedit_commands.py`、`ui/drawing_commands.py`、`ui/fontstyle_manager_commands.py`、`icons/rail_history.svg`、`tests/test_undo_limit.py`、`tests/test_history_panel.py`、`translate/zh_CN.ts`、`docs/技术实现/撤销体系人工验收场景.md`

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

### 拖拽吸附对齐失效修复 + 开关状态记忆

**问题/需求：** 用户实机反馈吸附对齐从前几天起失效——拖拽时参考线正常显示但块不吸附；另要求给饼菜单「吸附对齐」开关加状态记忆（此前每次启动默认开）。

**改动要点：**

- **失效根因（回归定位）**：c097b41（上游 v1.5.12 移植节点2a）重写 `ui/textitem.py::TextBlkItem.mouseMoveEvent` 时把 `_apply_snap()` 从 `super().mouseMoveEvent(event)` 之后挪到了之前——Qt 默认移动按事件增量覆写位置，吸附修正随即被本次增量抵消，最终停靠位永远差一个增量；compute_snap 照跑所以参考线仍显示，呈现「UI 对齐有反应、实际不吸附」。修复=吸附移回 super() 之后并注释顺序约束。
- **复现手法**：仿 `tests/test_box_select.py` 离屏 harness，QMouseEvent 直发 view 驱动真实 ItemIsMovable 拖拽链路，插桩记录 `_apply_snap` 前后 `absBoundingRect`——修复前右缘 225 不落 220，修复后精确吸附。
- **状态记忆**：`utils/config.py::ProgramConfig` 加 `snap_alignment=True`；`ui/canvas.py` 初始化 `alignment_enabled` 改读 pcfg；`ui/context_menu_config.py::_snap_alignment_run` 切换时写回 pcfg 并 `save_config()`（与 seq_badge 等饼菜单开关持久化方式一致）。
- 验证：`verify.py` 全绿；`tests/test_config_fields.py`、`tests/test_box_select.py`（18 例）通过。

**涉及文件：** `ui/textitem.py`、`utils/config.py`、`ui/canvas.py`、`ui/context_menu_config.py`

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
