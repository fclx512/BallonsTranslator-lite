# 每日开发日志

> 记录**仓库层面**的改动（功能增删、远端分支变动、规范调整）。**主要读者是 AI**（用户基本不看），按**可检索**写、不按可通读写。每次在对应日期末尾写入，每条之间用 `---` 分隔：

```markdown
### <一句话标题：改了哪 + 关键符号名/配置字段名/D 编号>   ← 标题里带检索键，供 grep
**摘要：** 问题 → 做法与理由（2~4 句，写决策不写实现细节）
**涉及文件：** `路径`、`路径`                            ← 最常用的检索键，必填
```

> 可选再补一行 `**验证：**`（真机实测／测量类证据，或值得钉住的回归）与一行 `**遗留：**`（有意留开、已知不做的事）。**细节不写在这里**：设计决策写进 `docs/技术实现/`、就地约束写进代码注释、实现过程写进 commit body（本仓库 commit 已相当详细）——同一件事不要在四处各写一遍。长度参考**一条 4~8 行**，动辄 30~60 行的长段落既是反面教材、也让 grep 到的人被迫整段读完。踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见 `AGENTS.md` 的「多代理协作」一节），不进仓库。
>
> 仅保留最近 3 天的记录（超出窗口的日期节由 `scripts/trim_daily_log.py` 在提交时经 pre-commit 钩子自动清理，无需手工维护）；被裁掉的日期节仍完整留在提交历史里，用 `git log --grep <关键词>`／`git log --follow -p -- docs/daily_log.md`／`git show <rev>:docs/daily_log.md` 回查。

## 2026-09-25

### 竖排描边克隆 _draw_offset 形状失配闪退修复（updateDrawOffsets 守卫）+ 演练台 stroke-switch 场景

**摘要：** 用户反馈快速切图闪退（IndexError @ `ui/text_engine/vertical_layout.py::vertical_line_placement`）：描边渲染的克隆文档与原布局共享 `_draw_offset`，而字号/文本应用等事务在 `relayout_on_changed=False` 窗口内改文档后，同步 contentsChanged → `repaint_background` 生成描边光栅走克隆路径，拿旧表索引新结构，行数变多即越界。修法＝`ui/text_engine/vertical_layout.py::updateDrawOffsets` 守卫先形状校验（`_draw_offset_shape_matches`），失配时 rebind 新列表按本文档重建、不 clear 共享对象。漂移真因是抑制窗口本身（不是字距——竖排每字符占一行，字号不改 lineCount）；上游同款代码，反向移植时连带。

**验证：** `tests/test_vertical_engine.py::StrokeCloneOffsetGuardTest` 红绿（还原修复即复现用户同款调用栈）；演练台 `scripts/mw_repro.py --scenario stroke-switch` 修复前复现同款栈、修复后通过。

**涉及文件：** `ui/text_engine/vertical_layout.py`、`tests/test_vertical_engine.py`、`scripts/mw_repro.py`、`scripts/README.md`

---

### 工作台批量任务刷新前置对齐（pre_replan）+ 内容改动过时灯（content_modified → mark_stale）

**摘要：** 用户实测「刷新无效」：`plan` 直读数据层 `proj.pages`，画布上手动增删框／键入只落在视觉层。修法＝`ui/workbench_batch_view.py::BatchTaskView` 重扫前先跑面板注入的 `pre_replan`（`ui/glossary_agent_panel.py::_sync_before_plan`：`text_change_unsaved` 门控 `updateTextBlkList` + `_sync_block_data` 兜结构性增删，对齐失败不挡重扫）。另加**只亮灯不自动重扫**的过时提示：`Canvas.content_modified`（置脏每次调用都广播——状态翻转信号对重复置脏会漏报）→ `mark_content_changed` 给已规划页亮「列表可能过时」，replan 熄灯、换项目 `forget_plan` 作废；管线完成与全局替换两条不走画布置脏口的路径直连广播。

**验证：** `tests/test_workbench_panel.py` 新增重扫顺序与过时灯生命周期两用例；`scripts/verify.py` 全绿。

**涉及文件：** `ui/canvas.py`、`ui/mainwindow.py`、`ui/glossary_agent_panel.py`、`ui/workbench_batch_view.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_panel.py`

---

## 2026-09-23

### 样式管理器左树区带化（库/本项目/未分组）+ `setExpanded` 顺序 bug + 动作按钮 `btnRole` 色彩语义

**摘要：** 用户反馈「收藏库和项目样式混在一起、辨识度低」，查下来三条成因：①**真 bug**——`ui/fontstyle_manager.py::StyleTreeWidget.populate` 两个区头行在 `addTopLevelItem` **之前**调 `setExpanded(True)`，Qt 下是空操作（item 未入 view，展开态存不下来），于是打开时库区/未分区都收起、库标题紧贴项目样式连成一片；②行内只有块数一个区分位，而库条目该位是空的，库条目与项目样式可字面同形（行1近似、行2相同）；③大样式是顶层行、库条目是子项，缩进深浅与归属感相反。做法：三区都改**自绘区带行**（委托新增 `section` 分支＝半透明底+加粗标题+条目计数），库区仍在最上；库条目右侧块数槽换成「模板」胶囊标签（`_theme_accent()` 描边，**不能用 `palette.Highlight`**——暗色主题下它就是选中底色，标签会整块隐形）；`select_payload` 改递归（多一层）。顺带修选中高亮的**双层接缝**（QSS 铺 `@accentPrimary20`、委托叠 `palette.Highlight`，两色不同在色板处留缝）与清掉 `#StyleList` 三条死规则（对应的 `StyleBlockList` 早删）。动作按钮加 `btnRole` 属性（`primary`/`accent`/`danger`），QSS 按角色上色——文案之外再给一层视觉语义。**补漏（用户看图回报）**：六个动作按钮的显隐各自写在对模式的 `show_*` 里，「没有模式」这条路谁都没隐藏，刚打开时六个挤一行被裁且全都无从触发；右栏加无选中态（`StyleDetail.show_empty`：内容整体收起、只留引导语），并在重选失败时也退回该态——此前选中项被删后右栏会停在被删样式的快照上。

**验证：** `scripts/verify.py` 全绿；新增 `tests/test_global_styles_ui.py::test_actions_hidden_until_a_style_is_selected`（未选中态按钮全隐，`isVisibleTo` 判可见性）；其余只在既有用例上加钉子（`test_two_line_tree_nodes` 补区带行已展开、`test_library_section_in_tree` 补「模板」标签键、删除用例补退回无选中态）——按用户口径：只留会动的行为契约，把自己刚写的结构再断言一遍的用例不留；暗/亮两套渲染沿选中行中线扫色，确认两主题整行单色（接缝消失）。

**涉及文件：** `ui/fontstyle_manager.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/stylemgr_render.py`、`scripts/README.md`、`tests/test_fontstyle_tree.py`、`tests/test_global_styles_ui.py`、`docs/技术实现/全局样式库_设计方案_存档.md`、`docs/技术实现/查找替换与样式管理器重构_设计方案_存档.md`

**遗留：** 区带行不响应点击（不可选中、无 payload，只作分区）；左树仍无搜索/过滤框，库条目涨到几十条时要靠折叠区带自己找。另顺手补了 `docs/技术实现/模型文件管理_设计方案_存档.md` 一处失效路径引用（引 `tmp/` 下备份文件，本就不入库，`check_docs` 一直红着）。

---

