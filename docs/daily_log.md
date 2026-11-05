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

## 2026-11-05

### 气泡面板重排：标签控件同行 + 参数按需出现（`_ShapeSegments` / 图标分段钮）

**摘要：** 用户反馈气泡面板丑且效率低。实测 260 最小宽下旧排法每个组标题独占一行、区域四数值格挤一行无标签、W/H 显示被截断、英文 "Rectangle" 分段截成 "ectang"。重排为**标签与控件同行、参数只在相关时出现**：默认态 6 行、全展开 9 行；形状改自绘图标分段钮 `_ShapeSegments`（文案与语言无关放不下，名称走 tooltip）；X/Y/W/H 与长/宽用 spinbox 前缀内嵌；跟随态收起数值格、尾巴关闭收起长宽行。`ShapeFillPanel.sync_border_controls` 式「禁用占位」在主参数区改为「隐藏」。
**涉及文件：** `ui/block_bubble_panel.py`、`tests/test_block_bubble.py`、`scripts/probes/render_bubble_panel.py`（新）、`translate/zh_CN.ts`、`docs/技术实现/气泡绘制_设计方案.md`
**验证：** 中英双语 260/300 宽出图确认无截断（探针 `--lang en` 专门钉英文形态）；气泡 59 例全绿；`verify.py` 全绿（i18n 无孤儿）。
**遗留：** 面板滑块连拖逐改动的合并问题照旧（前一日遗留项不变）。

### 气泡面板展开自动长高 + 圆角/描边输入上限（`_grow_to_fit_content` / `_apply_limits`）

**摘要：** 用户报勾上「尾巴」/切「自定义区域」后新行被压扁、要手动拖一下才回正，且圆角与描边能拖出没有意义的值。`RailDockPanel` 原来只在首次打开按 sizeHint 定一次尺寸，现监听内容区及后代的 `LayoutRequest`（动态子控件走 `ChildAdded` 补挂），内容 sizeHint 变大就单向长高——只长不缩，用户拖小的尺寸不会被抢回。面板侧按气泡短边收紧输入上限：圆角 ≤ 短边/2、描边 ≤ 短边/4（绝对上限仍保留 200/64），换块与改区域尺寸都重算；`params.addStretch(1)` 让多余高度留在底部。顺带修了切块时自定义区域 W/H 沿用上一块数值的问题。
**涉及文件：** `ui/custom_widget/rail_dock_panel.py`、`ui/block_bubble_panel.py`、`ui/text_panel.py`、`ui/textitem.py`、`tests/test_block_bubble.py`、`tests/test_rail_docks.py`、`translate/zh_CN.ts`、`docs/技术实现/气泡绘制_设计方案.md`
**验证：** 真控件离屏探针 260/300 宽全展开 9 行无截断；浮层长高、手动缩小不被长回、上下限四例新增用例；`pytest tests` 1780 通过；`verify.py` 全绿、ruff 通过。

### 演练台默认接管模态弹窗 + 页列表用例补运行窗依赖（`_install_dialog_guard` / `run_pipeline_dialog`）

**摘要：** `scripts/mw_repro.py` 无人值守跑真实主窗口时，错误框与原生静态弹窗会卡住等人点：新增默认弹窗守卫 `_install_dialog_guard`——内容打到 stdout 后立即返回肯定值，QMessageBox 静态方法一并替换（其内部走 C++ exec，类级 patch 拦不住），`--interactive-dialogs` 还原真实弹窗；同时置 `SEM_NOGPFAULTERRORBOX` 让崩溃静默快速失败。`tests/test_page_list_dirty_click.py` 的 harness 补上 `run_pipeline_dialog`——65786f34 起切页末尾会调用它，缺了会让全量 pytest 在 55% 处 exit 127。
**涉及文件：** `scripts/mw_repro.py`、`scripts/README.md`、`tests/test_page_list_dirty_click.py`
**验证：** 页列表用例单独跑 5 例通过；全量 `pytest tests` 1780 通过。

---

## 2026-11-04

### 气泡合并为「块气泡 + 自定义区域」并修尾巴消失（`region` / `BubbleSpec` / `_curve_mouth`）

**摘要：** 用户报三个问题：修复区的独立气泡多处不工作、尾巴转到四个直角区会压扁到看不见、并质疑「自定义气泡为何要在修复区再放一套和文本区一样的功能」。尾巴消失是 `_flat_mouth` 按边展开导致的确定性 bug（corner=20 时 40°~50° 全灭），改为沿**外轮廓**取对称弦后 3 尺寸×3 圆角×360° 无一条缺失。独立气泡实测出六处缺陷（拖动不落盘、改参丢选中、取消勾选抛异常、面板首次不回显、退出绘图页仍抢文字块点击、数据跨页而撤销不跨页），决定**整体删除**，自由气泡改为块气泡载荷里的 `region` 键（相对块内容区左上角的偏移+尺寸），入口收成文本编辑区一个。
**涉及文件：** `ui/bubble.py`、`ui/block_bubble_panel.py`、`ui/textitem.py`、`ui/text_panel.py`、`ui/canvas.py`、`ui/drawingpanel.py`、`ui/mainwindow.py`、`ui/textedit_commands.py`、`ui/image_edit.py`、`ui/configpanel.py`、`utils/proj_imgtrans.py`、`config/stylesheet.css`、`scripts/upstream_drift_baseline.json`、`tests/test_bubble_geometry.py`、`tests/test_block_bubble.py`、`docs/技术实现/气泡绘制_设计方案.md`、`AGENTS.md`
**验证：** 几何 30 例 + 块气泡 29 例全绿；真实主窗口实测自定义区域 `[-40,-30,420,300]` 渲染同值、块 setPos 后仍相对块、开气泡/切自定义/改数值均可撤销、取消勾选不再报错；`verify.py` 全绿。
**删除：** `ui/bubble_layer.py`、`tests/test_page_bubble.py`、`icons/drawingtools_bubble*.svg`、`ImageEditMode.BubbleTool`、`page_bubbles` 顶层键、`PageBubbleCommand`、`bubble_tool` 快捷键。
**遗留：** `BlockBubblePanel` 的 `alpha_spin` 是整数百分比，面板滑块连拖时每次改动各入一条命令（靠 undo 合并逻辑兜，未做拖拽期间合并）。

---

### 块气泡背景（`TextBlock.bubble` / `BubbleItem` / `SetBlockBubbleCommand` / `bubble_dock_open`）

**摘要：** 气泡功能原只有几何层（`ui/bubble.py`），宿主全空。本轮补上块气泡：`TextBlock.bubble` 落盘 fork 独有键（`None`=无气泡／`{}`=默认样式，必须能区分，否则画不出纯白矩形气泡），`BubbleItem` 挂 `TextBlkItem` 子项，撤销走文本命令栈，入口是左缘窄栏第 5 个图标。**最关键的一条**：设计文档原写 `setZValue(-1)` 就能把子项画到父项之下——离屏实测证明无效，Qt 子项永远画在父项之上，必须 `ItemStacksBehindParent`；漏了会把译文整块盖住且不报任何错。
**涉及文件：** `ui/bubble.py`、`ui/block_bubble_panel.py`（新）、`ui/textitem.py`、`ui/textedit_commands.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`utils/textblock.py`、`utils/config.py`、`icons/rail_bubble.svg`（新）、`tests/test_block_bubble.py`（新）、`scripts/upstream_drift_baseline.json`、`docs/技术实现/气泡绘制_设计方案.md`、`AGENTS.md`
**验证：** 新增 24 例全绿（含气泡在文字下的像素级双向断言：正面 == 无气泡基线、反面去掉 flag 立即掉到近 0）；`verify.py` 全绿；真机出图确认椭圆/圆角/半透明/粗描边/尾巴各形态观感正常，块缩放 260×150→420×220 时气泡跟随、无残影。
**遗留：** 粗描边（如 14px）会盖住贴边的文字，属已知参数耦合，面板只给提示不自动改参数。

---

### 独立气泡（`page_bubbles` / `BubbleLayer` / `PageBubbleCommand` / `BubbleTool=5`）

**摘要：** 补上页面级气泡：`bubbleLayer` 夹在 drawing 与 text 之间（`setParentItem` 顺序即 z 序），数据落顶层键 `page_bubbles`，工具 `ImageEditMode.BubbleTool=5`，撤销走**页级绘制栈**（切页即丢，用户已拍板：气泡编辑丢失可接受，不为它改项目撤销架构）。两处推翻设计稿：① 图层显隐**不跟随 `textLayer`**——气泡工具在绘图页而绘图页 `textLayer` 是隐藏的，跟随即等于「画完立刻看不见」；② 拖拽建框复用 `startCreateTextblock` 而非新造 rubber band（区域再检测的既有范式）。仓库没有「清空 baseLayer 子项」的通用逻辑，所以 `BubbleLayer` 自带切页重建 + 关项目清理两个钩子，否则换页会把旧页气泡留在新页。
**涉及文件：** `ui/bubble_layer.py`（新）、`ui/canvas.py`、`ui/textedit_commands.py`、`ui/image_edit.py`、`ui/drawingpanel.py`、`ui/mainwindow.py`、`ui/configpanel.py`、`utils/proj_imgtrans.py`、`utils/config.py`、`config/stylesheet.css`、`icons/drawingtools_bubble*.svg`（新）、`tests/test_page_bubble.py`（新）、`scripts/upstream_drift_baseline.json`、`docs/技术实现/气泡绘制_设计方案.md`、`AGENTS.md`
**验证：** 新增 16 例全绿（含图层 z 序、切页重建、坏条目跳过、命令跨页保护）；真实工程落盘/重载 4 条气泡往返一致（默认值不落键）；`verify.py --smoke` 全绿；出图确认椭圆/圆角/半透明/粗描边各形态正常。
**遗留：** 批量按掩膜生成未做；气泡只能画/选/删/改样式，**不能拖手柄改尺寸**（改尺寸需删了重画）；绘制栈语义下切页即丢历史。

---

### 演练台弹窗自动关闭（`mw_repro` / `_install_dialog_guard` / `--interactive-dialogs`）

**摘要：** `scripts/mw_repro.py` 拉起真实主窗口时报错会弹 win32 对话框卡住脚本，必须真人点掉。根因是 `ui/mainwindow.py::MainWindow` 的错误框走 `err.exec()`（模态嵌套事件循环），而演练台原先只在 group-undo 场景 patch 了 `QMessageBox.exec`，其余场景零保护（仓库虽有 `shared.HEADLESS` 开关，但演练台从没设过）。改为默认装 `_install_dialog_guard`：统一替换 `QMessageBox.exec` / `QDialog.exec` / `exec_`，把弹窗内容（正文 + detailedText traceback）打到 stdout 后立即返回接受值，无人值守能跑到底且失败原因不丢。**只改测试台，未动生产代码**（用户口径：不为可选功能改主干）。
**涉及文件：** `scripts/mw_repro.py`、`scripts/README.md`
**验证：** 合成弹窗探针确认三类弹窗（错误框/普通 QDialog/MessageBox）全部 0 秒返回且内容完整打印；`--scenario group-undo` 与 `stroke-switch` 实跑均 `SCENARIO OK` 且退出码 0，全程无人工干预。

---

