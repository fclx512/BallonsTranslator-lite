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

## 2026-10-07

### 文档清理：删三份已完结存档，活约束并入代码注释（`ocr_vl_manga.py` / `effects/cards.py` / `panel_rail.py::RailLauncherButton`）

**摘要：** 用户拍板删除效果栈两份存档与 paddle-ocr-for-manga 接入调研（批次均已完结、调研论证过时）。仍活的约束搬迁：「长期搁置勿复活」清单与效果卡分层定稿规则并入 `ui/text_engine/effects/cards.py` 模块 docstring，rail_* 图标 SVG 硬规范并入 `ui/panel_rail.py::RailLauncherButton` docstring（原「禁 stroke」条已过时，`ui/misc.py::set_icon_theme` 现已同步替换 stroke），参数坑与实测数字并入 `modules/ocr/ocr_vl_manga.py` docstring（顺带修正「比 onnx 系慢两个数量级」的过时估算——实测单块中位 192ms、约 6s/页）。三份文件登记 `scripts/audit_registry.json` deprecated；AGENTS.md 与 `docs/项目概述.md` 索引指针同步改指新落点。
**涉及文件：** `docs/技术实现/paddle-ocr-for-manga_接入调研_存档.md`、`docs/技术实现/效果卡渐进披露_验收清单_存档.md`、`docs/技术实现/效果栈移植与窄栏图标重绘_计划_存档.md`、`modules/ocr/ocr_vl_manga.py`、`ui/text_engine/effects/cards.py`、`ui/text_engine/effects/panel.py`、`ui/text_engine/effects/renderer.py`、`ui/panel_rail.py`、`AGENTS.md`、`docs/项目概述.md`、`docs/技术实现/模型文件管理_设计方案_存档.md`、`docs/技术实现/模型文件管理_测试流程.md`、`docs/技术实现/模型说明卡片_规划.md`、`docs/技术实现/查找替换与样式管理器重构_设计方案_存档.md`、`scripts/probes/README.md`、`scripts/audit_registry.json`
**验证：** `scripts/check_docs.py` 与 `scripts/check_audit.py` 全绿。

---
