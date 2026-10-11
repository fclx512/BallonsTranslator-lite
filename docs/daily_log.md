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

## 2026-10-11

### LLM Profile 卡片页批次：双击改名落定+状态点+模型清单批量编辑（`_EditableNameLabel` / `rename_profile` / `_ModelListEditDialog`）

**摘要：** 卡片标题双击就地改名，数据层新增 `rename_profile` 把激活项与四个模块存储的 profile 引用（`_PROFILE_REF_SLOTS`）一并迁移；修复提交后标题标签不刷新（`_on_rename_requested` 成功分支按新名补 setText）。模型行新增清单批量编辑对话框（勾选保留/取消勾选删除 + 搜索 + 批量添加输入框），`+` 快速添加支持逗号/空白分隔一次多个（`_split_model_names`）；OCR/在线修复参数面板含 profile 选择器时显示「Manage…」跳转入口（`navigate_to_llm_profile`）；卡片加可用性状态点（复用 `profile_is_usable`）。样式表补 `QDialog QListView::indicator`（勾选列表指示器）与 `LLMProfileModelManageButton` 规则，修图标被全局 QToolButton 兜底裁剪、复选框显示不全两处观感。
**涉及文件：** `ui/llm_profile_cards.py`、`utils/profile_manager.py`、`ui/configpanel.py`、`ui/module_parse_widgets.py`、`config/stylesheet.css`、`icons/model_list.svg`、`translate/zh_CN.ts`
**验证：** 离屏探针实测改名全链与批量编辑各路径（多粘贴/勾删/取消/空清单引导）；`scripts/verify.py` 全绿。

---

### 启动期字体警告收敛：safe_qt 前缀过滤 DirectWrite + ghost fonts 打印 DEBUG 门控

**摘要：** `DirectWrite: CreateFontFaceFromHDC() failed` ＝幽灵字体探针触碰 GDI 老光栅字体的平台层噪音，`utils/safe_qt.py` 抑制列表加前缀；`shared._drop_unloadable_families` 的 ghost fonts 输出本是闸门正常结果，改 `BALLOONTRANS_DEBUG_QT_WARNINGS`/DEBUG 门控不再刷屏。均无行为改动。
**涉及文件：** `utils/safe_qt.py`、`utils/shared.py`

