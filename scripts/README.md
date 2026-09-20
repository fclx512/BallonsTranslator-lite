# Scripts 目录使用说明

本项目脚本按用途分为三类。所有路径相对于项目根目录。
更新脚本清单时记得同步本文件（`scripts/check_docs.py` 会自检：scripts/ 下
每个可执行脚本都必须在本文件登记）与 `manifest.json`（发版前跑
`scripts/generate_manifest.py` 重新生成）。

---

## 一、质量保障 / 自动化检查

**统一入口是 `verify.py`**（语法 → 文档 → 审计 → 展示台覆盖 → i18n → qm → 冒烟），
日常开发跑它即可；发版前加 `--full`（追加 ruff + pytest）。

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/verify.py` | **一键检查**：语法 → 文档 → 审计 → 展示台覆盖 → i18n → qm → 冒烟；成功每步只打一行 | `python scripts/verify.py`（`--smoke` 强制冒烟，`--all` 全量语法，`--full` 发版门禁追加 ruff+pytest） |
| `scripts/check_syntax.py` | 语法检查（编译 + 混合缩进 + UTF-8 BOM） | `python scripts/check_syntax.py <文件...>` |
| `scripts/check_docs.py` | 校验 `AGENTS.md` 与 `docs/` 活文档的路径/符号引用 + scripts/README 登记齐全 | `python scripts/check_docs.py` |
| `scripts/check_audit.py` | 审计登记表契约（deprecated 残留引用 / suspended 被 import） | `python scripts/check_audit.py` |
| `scripts/check_showcase.py` | 展示台覆盖校验：`ui/custom_widget` 每个导出必须在 `scripts/style_showcase.py` 展示或 `EXCLUDED` 登记（纯 AST，不导入 Qt） | `python scripts/check_showcase.py` |
| `scripts/i18n_check.py` | 审计 i18n（硬编码中文、缺失/多余的 .ts 条目），发版前 `--ci` | `python scripts/i18n_check.py` |
| `scripts/qm_compile.py` | 编译 `.ts` → `.qm`（Qt 二进制翻译文件） | `python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm` |
| `scripts/ts_auto_fill.py` | 自动同步 `self.tr()` 调用与 `.ts` 文件，`--apply` 后自动重编 .qm | `python scripts/ts_auto_fill.py --apply` |
| `scripts/trim_daily_log.py` | 将 `docs/daily_log.md` 裁剪到最近 3 天（按 `## YYYY-MM-DD` 标题解析，无日期段恒保留）；`--check` 仅校验，pre-commit 钩子（`scripts/hooks/`）自动执行 | `python scripts/trim_daily_log.py [--check]` |
| `scripts/i18n_common.py` | i18n_check / ts_auto_fill 共用的提取逻辑与孤儿白名单（非入口，勿单独运行） | — |

### verify.py 两级用法

- 默认（无参数）：开发时频繁跑的快循环 —— 语法/文档/审计/i18n/qm/冒烟，
  不含 pytest（测试需要重依赖、开销大）。
- `--full`：合入或发版前的全量门禁 —— 追加 ruff 风格检查和 `tests/` 的
  pytest（两者未安装时自动跳过并提示）。

---

## 二、开发辅助

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/style_showcase.py` | 控件样式展示台（人工目视）：Tab1 原生 vs 封装对照（识别哪些类必须用 `ui/custom_widget` 封装），Tab2 按面板分区的全部控件（封装类 + 应用层复合控件），每行带样式来源徽章（类名/objectName/自绘/内联/全局兜底/无规则）+ `路径::符号` 一键复制，支持搜索、目录跳转、样式来源筛选、亮暗主题切换、状态矩阵（正常/禁用/悬停/聚焦）。系统 Python 启动时自动切便携解释器重跑；`--selftest` 无界面自检全部工厂 | `python scripts/style_showcase.py` |
| `scripts/style_showcase.bat` | 展示台一键启动（双击即可，可透传参数如 `--selftest`）；优先用便携解释器，退回 `py`/`python`，非零退出码才 pause | 双击 或 `scripts\style_showcase.bat` |
| `scripts/pie_menu_test.py` | 饼菜单/快捷菜单离线功能测试（状态机/命中判定/命令注册，独立进程沙箱配置）；功能已上线，后续加功能卡片等小修小补可复用 | `python scripts/pie_menu_test.py` |
| `scripts/region_redetect_order.py` | **区域再检测的落点回归台**：把真工程每页的**每个已有框自己**当作拉框区域重跑检测，核对新块是否**连续**落回被替换块原来的下标（依赖"该页顺序已正确"这一功能前提；合成单测锁不到"这片漫画该怎么读"）。两段式——`--detect` 真跑检测并写缓存 `tmp/region_redetect_plans.json`（分钟级，顺带跑端到端核对），不带 `--detect` 则读缓存**离线**判（秒级，只走落点逻辑，不建 ONNX 会话）；`--candidates` 打印候选判据对比表（含被否掉的"逐块各自算"与"折线投影"），改判据前先用它确认新方案真的赢过现役。判据口径与实测数字见 `docs/技术实现/区域再检测_设计与实现.md` §5 | `python scripts/region_redetect_order.py [--project DIR] [--pages A,B] [--detect] [--candidates]` |
| `scripts/mw_repro.py` | **MainWindow 在线演练台**：拉起真实主窗口（必须窗口模式，offscreen 起不来 FramelessWindow）做模拟复现与交互驱动——真实绘制路径/原生模态框/GC 时机类问题的排查工具。`--scenario group-undo` 跑组化撤销全链路（自动点确认弹窗，延迟须 ≥200ms），`--project` 只读打开真实工程，`--no-show`/`--no-panel`/`--watchdog` 控制形态；faulthandler 常开。起源=确认弹窗 GC 悬空 AV 闪退排查（经验教训 §3.3） | `python scripts/mw_repro.py [--scenario group-undo\|none] [--project DIR] [--pages 2 --blocks 8]` |
| `scripts/workbench_recalc.py` | **泛用工作台的参数复算台（只读）**：C1 判据命中率／C2 分组口径与阈值敏感度／C3 扩张量候选与可扩空间／C4 队列口径／已驳回链路自检（D28／D30／D33c）／D39 程序筛选器端到端（真机跑一次 OCR）——这些参数是"测出来的"，改判据或调默认值前后各跑一次做对比。六个子命令 `merge`（`--sweep` 加阈值扫描）/`c1`/`expand`/`queue`/`review`/`hook`/`list`，脚本末尾自证样本顶层文件 mtime 未变。实测数字与调参方向见 `docs/技术实现/AI辅助功能_设计与实现.md` 的「长期需要实测调整的参数」节 | `python scripts/workbench_recalc.py merge --project DIR [--sweep]` |

| `scripts/workbench_render.py` | **工作台布局的目视验收台**：合成一个带内容的工程，把 `ui/glossary_agent_panel.py::GlossaryAgentPanel` 的每个任务页（含未开项目的空态页）渲染成 PNG（默认 2 倍率），并在 stdout 回显两级导航结构（大类页签文字／chip 文字／可见 chip／计数）——不看图也能核对结构。**必须走默认 windows 平台插件**：offscreen 下 `QFontDatabase.families()` 为空，文字全是豆腐块（且设 `setFont` 救不回来）；同时按 `launch.py` 装字体＋语言＋主题，否则截图是白底裸控件 | `python scripts/workbench_render.py [--out DIR] [--scale N]` |

渲染同步回归已迁至 `tests/test_render_sync.py`（pytest/直接运行均可）。

**真机探针**在 `scripts/probes/`（内存归因与释放阶梯、区域再检测真机验收）——那是"测出来的"结论的可复算工具，有真机／模型依赖，前提与每个脚本的期望数字见 `scripts/probes/README.md`。本目录的登记检查只覆盖顶层，子目录自带说明。

---

## 三、构建 / 部署 / 更新

| 脚本 | 平台 | 用途 |
|---|---|---|
| `scripts/check_update.py` | 跨平台 | 启动时检查更新：git 增量 / manifest delta / zip 三种模式（`launch.bat`/`launch.py` 调用） |
| `scripts/generate_manifest.py` | 跨平台 | 生成 `manifest.json`（全文件 SHA256 清单，供 delta 更新用）。**发版前必须重新生成并随版本提交** |
| `scripts/download_models.sh` | Linux/macOS | 编译 PyPatchMatch（`modules/inpaint/patch_match.py` 仍依赖）。**其中的模型下载段是上游遗留、已与当前模型集合脱节**——权重请在应用内「设置 → Models → 模型文件」获取（见 `docs/技术实现/模型文件管理_设计方案.md`） |
| `scripts/local_gitpull.bat` | Windows | 使用便携环境执行 `git pull` |

---

## i18n 翻译工作流

```
# 1. 在代码中用 self.tr("English text") 包裹新字符串
# 2. 需要时自动填充缺失条目 + 清理冗余条目（写回后自动重编 .qm）
python scripts/ts_auto_fill.py --apply

# 3. 验证完整性
python scripts/i18n_check.py --ci
python scripts/verify.py --full       # 发版全量门禁（含 ruff + pytest）
```

---

## 注意事项

- **所有 `*.bat` 脚本仅限 Windows。** 请在命令提示符或 PowerShell 中运行，不要双击（部分脚本含暂停逻辑）。
- **所有 `*.sh` 脚本仅限 Linux/macOS。** 需要 `chmod +x`。
- **`config/config.json` 已 gitignore**（含 API 密钥），请勿提交。
- **运行检查脚本前**，请确保工作目录是项目根目录：
  ```bash
  cd BallonsTranslator-lite
  python scripts/verify.py
  ```
- **pytest 在缺少 opencv/torch/numpy 等重依赖时会自动跳过相关测试**，不影响其他检查。
- **`manifest.json` 是 `generate_manifest.py` 的产物**，不要手工改；发版流程里记得重新生成。
