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

## 2026-10-14

### 演示动画：有效时间轴同源 + 覆盖层层级修复 + README 几何/时序收敛（`anim_kit.resolve_timeline` / `CameraScene` / `PopupPalette`）

**摘要：** 弹层与 README 两条演示流程的检查假绿灯与观感缺陷一并修掉。① 时间轴校验改读 `scripts/anim_kit.py::resolve_timeline`（渲染侧 `apply_timeline` 同源）——旧检查只读类属性，`RHYTHM` 场景实际校验到继承的旧时间轴（`clip_text` 的 N_FRAMES/F_FADE 校验成 20/15 而非实例 36/31）。② 覆盖层改为每帧 `raise_()`（徽标场景的单条文案建在覆盖层之后、把光标盖住）+ 置顶内容 save/restore 隔离（手绘下拉残留的 clip/opacity 会裁掉/调暗光圈与光标）。③ README 两个场景的手绘色板/下拉收成 `PopupPalette`/`PopupDropdown`（绘制与落点同体）、镜头字幕收成 `CameraScene`、每拍移动收成 `CursorPlan.move_after_press`。首版试验脚本 `scripts/gen_readme_demo.py` 效果未达预期，用户授权删除并登记 deprecated。
**涉及文件：** `scripts/anim_kit.py`、`scripts/gen_help_anim.py`、`scripts/gen_readme_anim.py`、`scripts/gen_readme_showcase.py`、`scripts/check_anim_rhythm.py`、`scripts/check_anim_cursor.py`、`scripts/verify.py`、`scripts/README.md`、`scripts/audit_registry.json`、`tests/test_anim_checks.py`、`docs/基础速查/备注演示动画使用说明.md`
**验证：** 弹层 7 场景逐帧哈希回归——迁移类场景 0 差异；`punctuation_position` 3 帧、`tag_badge` 1 帧、README 两场景各 4 帧为**预期内的层级修复差异**（只在光标/涟漪处）。`tests/test_anim_checks.py` 用「还原旧写法」的对照组证明节奏/层级/裁剪检查器确实报错。

---

