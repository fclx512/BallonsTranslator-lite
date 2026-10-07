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

### 字体排除「老旧→精简」互覆盖修复 + 幽灵字体闸门（`FontExcludeDialog._move_to_hidden` / `shared._drop_unloadable_families`）

**摘要：** 用户实测两问题。①「添加老旧字体」后点「一键精简」会互相覆盖：精简曾整表重建列表、只认持久态，把会话内未确定的隐藏条目冲回；改三入口（手动＞/老旧/精简）统一走增量移动 `_move_to_hidden`，一次确定全部落盘。②幽灵字体（有选项、无样式、应用仍是默认）＝QFontDatabase 能枚举、DirectWrite 加载不了的家族被 Qt 静默替换：`init_font_list` 加 QFontInfo 探针闸门过滤（解析落回同归并组视为可用，>20% 失配保险丝），次级下拉补插的未安装/被排除条目置灰加 tooltip（`combobox.py::mark_unlisted_combo_item`）。测试口径收紧：仅新增一条钉该用户实测缺陷的回归用例。
**涉及文件：** `ui/configpanel.py`、`utils/shared.py`、`ui/custom_widget/combobox.py`、`ui/style_format_editor.py`、`ui/fontstyle_manager.py`、`tests/test_font_exclude_dialog.py`、`scripts/probes/font_ghost_probe.py`、`translate/zh_CN.ts`
**验证：** 本机探针 221 显示家族唯一幽灵 `'Courier'`→`'Courier New'`、本地化双名零误判；`scripts/verify.py` 全绿（docs 一步为上游遗留失效引用，与本批无关）。

---

### 死代码清理：text_engine 内嵌格式面板与 `FONT_FAMILIES` 地雷 + check_audit 误伤修复

**摘要：** 删除未接入主窗口的 `ui/text_engine/formatting/panel.py` 与 `ui/text_engine/editing/manager.py`（约 2600 行，活实现是 `ui/text_panel.py::FontFormatPanel`）及恒为 None 的 `shared.FONT_FAMILIES`（仅死代码引用的 TypeError 地雷）；两文件由 suspended 移入 deprecated 登记，ts 顺带清 45 条孤儿。check_audit 修两处误伤：basename 在仓库有同名存活文件时只按全路径匹配（panel.py 在 effects/、transforms/ 各一份）；模块名改完整点分段匹配，`profile_manager`/`contextmanager` 类词尾同名不再误报。
**涉及文件：** `scripts/check_audit.py`、`scripts/audit_registry.json`、`ui/text_engine/transforms/edit_session.py`、`translate/zh_CN.ts`
**遗留：** 字体应用失败的静默回退（`ui/text_panel.py` 守卫与 Qt 替换）未加提示，按减法原则有意不做。

---

### 文档清理：删三份已完结存档，活约束并入代码注释（`ocr_vl_manga.py` / `effects/cards.py` / `panel_rail.py::RailLauncherButton`）

**摘要：** 用户拍板删除效果栈两份存档与 paddle-ocr-for-manga 接入调研（批次均已完结、调研论证过时）。仍活的约束搬迁：「长期搁置勿复活」清单与效果卡分层定稿规则并入 `ui/text_engine/effects/cards.py` 模块 docstring，rail_* 图标 SVG 硬规范并入 `ui/panel_rail.py::RailLauncherButton` docstring（原「禁 stroke」条已过时，`ui/misc.py::set_icon_theme` 现已同步替换 stroke），参数坑与实测数字并入 `modules/ocr/ocr_vl_manga.py` docstring（顺带修正「比 onnx 系慢两个数量级」的过时估算——实测单块中位 192ms、约 6s/页）。三份文件登记 `scripts/audit_registry.json` deprecated；AGENTS.md 与 `docs/项目概述.md` 索引指针同步改指新落点。
**涉及文件：** `docs/技术实现/paddle-ocr-for-manga_接入调研_存档.md`、`docs/技术实现/效果卡渐进披露_验收清单_存档.md`、`docs/技术实现/效果栈移植与窄栏图标重绘_计划_存档.md`、`modules/ocr/ocr_vl_manga.py`、`ui/text_engine/effects/cards.py`、`ui/text_engine/effects/panel.py`、`ui/text_engine/effects/renderer.py`、`ui/panel_rail.py`、`AGENTS.md`、`docs/项目概述.md`、`docs/技术实现/模型文件管理_设计方案_存档.md`、`docs/技术实现/模型文件管理_测试流程.md`、`docs/技术实现/模型说明卡片_规划.md`、`docs/技术实现/查找替换与样式管理器重构_设计方案_存档.md`、`scripts/probes/README.md`、`scripts/audit_registry.json`
**验证：** `scripts/check_docs.py` 与 `scripts/check_audit.py` 全绿。

---
