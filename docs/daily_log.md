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

## 2026-10-18

### ysg_ctd 混合检测器 + 「跳过简单情况」只填修复掩膜（`detector_ysg.py::YSGYoloCTDDetector` / `InpainterBase.inpaint` / `check_need_inpaint`）

**摘要：** 从上游 1.5.19 更新包移植 PR #1365／#1364。① 新增 `ysg_ctd`：ysgyolo 出框、ComicTextDetector 在框内出精细字形掩膜，两者求交，框外拟声词像素仍被排除；恢复 `modules/textdetector/ctd/` 实现包与 `comictextdetector.pt(.onnx)` 权重，但不恢复 `ctd` 检测器注册。② 新增派生模块参数继承 `TEXTDETECTOR_PARAM_INHERITANCE`，`ysg_ctd` 首次建配置时从已保存的 `ysgyolo` 播种，避免回退到 OBB 权重。③ 「跳过简单情况」纯色填充改为只覆盖修复掩膜内像素（块路径用块局部掩膜），默认值改 `False`。
**涉及文件：** `modules/textdetector/detector_ysg.py`、`modules/textdetector/ctd/`、`modules/base.py`、`modules/__init__.py`、`modules/textdetector/__init__.py`、`ui/module_manager.py`、`modules/inpaint/base.py`、`ui/drawingpanel.py`、`utils/config.py`、`utils/lazy_registry.py`、`scripts/audit_registry.json`、`tests/test_ysg_ctd_detector.py`、`tests/test_textdetector_param_inheritance.py`、`tests/test_inpaint_mask_scope.py`
**验证：** 真实工程「织姬嵌字【完成】」20 页／127 块：53 个简单块旧实现会填 2,042,425 px、新实现 1,510,757 px，越界涂平 531,668 px；修复后掩膜外 0 像素被改。`ysg_ctd` 三页实测块几何与 ysgyolo 完全一致、框外像素 0。

---

