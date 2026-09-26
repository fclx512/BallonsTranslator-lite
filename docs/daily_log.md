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

## 2026-09-26

### 标签待办体系重构（`ocr_review_pending` / `trans_review_pending` + `consumed_tags` 撤销一致性）+ 工作台六项扁平导航与非删除待办队列（D46/D48/D49）

**摘要：** 前台标签由「六个 id 都是按钮」收成**两个人工待办**（稍后校对／稍后重译）＋**两个持久翻译指示**（手写字／拟声词）：旧 ID（人工来源 `ocr_low_conf`、`trans_confusing`、`trans_polish`）**只读兼容、加载时不迁移**，只在用户取消那条待办时顺手清（`_clear_legacy_review`），避免静默重写整本项目；`reviewed` 粒度由块级收窄到**单个问题 ID**，否则驳回一条误框会把同块的低置信度建议一起永久冻结。确认卡「应用」的文字写回与标签出队并进**同一条撤销命令**（`ui/textedit_commands.py::ApplyBlockTextCommand` 的 `consumed_tags`），撤销时文字与待办一起复原。工作台导航由两级收成**扁平六项**（`WORKBENCH_ORDER`），形态＝按需换行的 **chip 流**（`ui/glossary_agent_panel.py::WorkbenchTaskNav`：组标题行去掉、只用组间细分隔线，激活项完整显示、放不下的非激活项压到可用宽度并在渲染层省略），新增两个**非删除待办队列**（`ui/workbench_review_view.py::ReviewQueueView`，只有「跳画布／出队」两条出口，复用 `BatchTask.apply` 会把「稍后处理」显示成「勾选＝要删」）；跳步弹窗与 `workbench_warn_skip_order` 一并删除（顺序是推荐，导航顺序本身就是提示）。校对原文与重译改为**单选块即出现场钮**，不再要求先挂标签。

**涉及文件：** `utils/block_tags.py`、`utils/block_actions.py`、`ui/textedit_commands.py`、`ui/mainwindow.py`、`ui/tag_toolbar.py`、`ui/context_menu_config.py`、`ui/glossary_agent_panel.py`、`ui/workbench_tasks.py`、`ui/workbench_review_view.py`、`translate/zh_CN.ts`、`docs/技术实现/AI辅助功能_设计与实现.md`

**验证：** `scripts/verify.py --full` 六步全过；pytest 1398 passed / 1 skipped（ruff 未安装跳过）；只读真样本复算（`D:/汉化/施工区副本`）通过。

**遗留：** 双检测器（`ppocrv6_onnx` ＋ `ysgyolo`）仍为**离线实验、未接入默认流程**：九页 A/B 只有几何覆盖等自动指标、无人工真值，人工看图已见 ysg 漏真文字（`047.jpeg`／`063.jpg`）；不加配置、不改默认检测/OCR 流程。

---

### 批量框扩张退役（`ui/batch_expand.py`／`workbench_expand_px`／复算台 `expand`，D47）+ 跳步提示退役（`workbench_warn_skip_order`，D37 → D49）

**摘要：** 用户实测结论是「扩了也不解决填不满／塞不下」——批量框扩张只有机械几何写入（改 `_bounding_rect`／`xyxy`），没有可靠的自动排版消费，故整条删除：引擎、工作台适配 `ExpandTask`、设置项 `workbench_expand_px`、复算台 `expand` 子命令、导航项与那条真机探针一并清掉，删前按审计规范登记。审批截图仍在用的 `utils/block_geometry.py::expand_limited`（「碰到邻框即停」）与单块 Alt 拖拽缩放不在退役范围。同批删除跳步弹窗与其配置项：顺序仍是推荐，但不做跳步拦截。误框批量删除补一条默认口径——命中 `no_japanese` 子类型的行默认不勾选（该类最容易误伤真实拉丁文本与拟声词，含多子类型时以含它为准）。

**涉及文件：** `ui/batch_expand.py`（删）、`tests/test_batch_expand.py`（删）、`scripts/probes/expand_centering_probe.py`（删）、`ui/workbench_tasks.py`、`utils/config.py`、`ui/configpanel.py`、`ui/glossary_agent_panel.py`、`scripts/workbench_recalc.py`、`scripts/audit_registry.json`、`docs/基础速查/设置面板_功能项清单.md`

**验证：** 三个保留的批量任务（可疑框清理／合并／简单背景修复）照常运行；`scripts/verify.py --full` 全过。

---

### 符号连字自动转换（`utils/symbol_convert.py` + `ProgramConfig.symbol_convert_enabled`）+ 重做提示统一画布 toast

**摘要：** 右栏编辑器打字／粘贴时把可合并符号序列自动换成连字（`！！`→`‼`、`！？`→`⁉`，映射表在 `utils/symbol_convert.py`）：只对**聚焦中的编辑器**生效，页面加载／翻译回填等程序性写入与撤销重放不转换，替换处就地短暂高亮；嵌字页窄栏开关 `rail_convert`，状态存 `ProgramConfig.symbol_convert_enabled`。撤销／重做提示统一由画布侧发（`ui/canvas.py::_notify_redo` 与撤销对称、同 key 刷新同一条），主窗口只兜「Ctrl+Z 撤到底还有批量替换版本」的场景。

**涉及文件：** `utils/symbol_convert.py`、`ui/textedit_area.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/canvas.py`、`ui/mainwindow.py`、`utils/config.py`、`icons/rail_convert.svg`、`tests/test_symbol_convert.py`

---

### 启动可靠性止血 + 精简包构建闭环（`scripts/build_win_minimal.ps1`）+ PatchMatch 随包可用与按需安装对齐

**摘要：** 针对「用户机启动失败难诊断」：Python 3.10+ 闸门、文件日志 + `sys.excepthook`/`threading.excepthook`、损坏配置隔离（`.corrupt-*`）与 `.tmp` 恢复、坏 torch（OSError）按缺失降级、重启循环守卫、pip 镜像在装依赖前落到环境变量。精简包定义定稿＝嵌入式 Python + pip/uv + **预装 `requirements.txt`** + 随包 `data/libs` 两个 PatchMatch DLL（约 550–600 MB 估算、不带模型后端与权重），构建脚本带发行门禁（干净树 + manifest 覆盖与哈希归一 + 版本一致）。`modules/base.py::ensure_dependencies` 复用 `utils/package_installer`（支持解释器旁独立 `uv.exe`），onnxocr 强制 `--no-deps`（`utils/package_installer.py::NO_DEPS_PACKAGES`，numpy<2 冲突）；PatchMatch 原生库改惰性加载、缺 DLL 给可读提示且不再从修复器选项隐藏。文档全渠道同步、README 标注精简包尚未发布。

**涉及文件：** `launch.py`、`launch.bat`、`utils/logger.py`、`utils/network_mirrors.py`、`utils/core_requirements.py`、`utils/config.py`、`utils/shared.py`、`modules/base.py`、`modules/inpaint/patch_match.py`、`modules/inpaint/inpaint_patchmatch.py`、`ui/module_parse_widgets.py`、`ui/run_pipeline_dialog.py`、`ui/model_downloads.py`、`utils/package_installer.py`、`scripts/build_win_minimal.ps1`、`scripts/README.md`、`README.md`、`README_EN.md`、`docs/基础速查/依赖库说明.md`、`docs/项目概述.md`

**验证：** `scripts/verify.py` 全绿；pytest 1474 passed / 1 skipped。**端到端真实构建未跑**（需干净树 + 网络下载），发版前按 `scripts/README.md`「精简包发行」流程执行。

---

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

