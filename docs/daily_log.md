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

## 2026-11-04

### 块气泡背景（`TextBlock.bubble` / `BubbleItem` / `SetBlockBubbleCommand` / `bubble_dock_open`）

**摘要：** 气泡功能原只有几何层（`ui/bubble.py`），宿主全空。本轮补上块气泡：`TextBlock.bubble` 落盘 fork 独有键（`None`=无气泡／`{}`=默认样式，必须能区分，否则画不出纯白矩形气泡），`BubbleItem` 挂 `TextBlkItem` 子项，撤销走文本命令栈，入口是左缘窄栏第 5 个图标。**最关键的一条**：设计文档原写 `setZValue(-1)` 就能把子项画到父项之下——离屏实测证明无效，Qt 子项永远画在父项之上，必须 `ItemStacksBehindParent`；漏了会把译文整块盖住且不报任何错。
**涉及文件：** `ui/bubble.py`、`ui/block_bubble_panel.py`（新）、`ui/textitem.py`、`ui/textedit_commands.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`utils/textblock.py`、`utils/config.py`、`icons/rail_bubble.svg`（新）、`tests/test_block_bubble.py`（新）、`scripts/upstream_drift_baseline.json`、`docs/技术实现/气泡绘制_设计方案.md`、`AGENTS.md`
**验证：** 新增 24 例全绿（含气泡在文字下的像素级双向断言：正面 == 无气泡基线、反面去掉 flag 立即掉到近 0）；`verify.py` 全绿；真机出图确认椭圆/圆角/半透明/粗描边/尾巴各形态观感正常，块缩放 260×150→420×220 时气泡跟随、无残影。
**遗留：** 独立气泡（页面级 `bubbleLayer` + 拖拽创建 + `BubbleUndoCommand` + `page_bubbles`）与批量按掩膜生成未做；粗描边（如 14px）会盖住贴边的文字，属已知参数耦合，面板只给提示不自动改参数。

---

