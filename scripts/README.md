# Scripts 目录使用说明

本项目脚本按用途分为三类。所有路径相对于项目根目录。
更新脚本清单时记得同步本文件与 `manifest.json`（发版前跑 `scripts/generate_manifest.py` 重新生成）。

---

## 一、质量保障 / 自动化检查

**首选统一入口是 `verify.py`**（语法 → 文档 → i18n → qm → 冒烟，每步一行结果），
日常开发跑它即可；`check_all.py` 是更重的全量门禁（额外含 ruff + pytest）。

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/verify.py` | **首选一键检查**：语法 → 文档 → i18n → qm → 冒烟；成功每步只打一行 | `python scripts/verify.py`（`--smoke` 强制冒烟，`--all` 全量语法） |
| `scripts/check_syntax.py` | 语法检查（编译 + tab 字符 + UTF-8 BOM） | `python scripts/check_syntax.py <文件...>` |
| `scripts/check_docs.py` | 校验 `AGENTS.md` 与 `docs/` 活文档的路径/符号引用 | `python scripts/check_docs.py` |
| `scripts/i18n_check.py` | 审计 i18n（硬编码中文、缺失/多余的 .ts 条目），发版前 `--ci` | `python scripts/i18n_check.py` |
| `scripts/qm_compile.py` | 编译 `.ts` → `.qm`（Qt 二进制翻译文件） | `python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm` |
| `scripts/ts_auto_fill.py` | 自动同步 `self.tr()` 调用与 `.ts` 文件 | `python scripts/ts_auto_fill.py --apply` |
| `scripts/check_all.py` | **全量门禁**：i18n + QM 编译 + ruff + pytest（ruff 未装自动跳过） | `python scripts/check_all.py`（`--quick`/`--fix`/`--ci`/`--install`/`--skip-tests`/`--skip-ruff`） |

### verify.py 与 check_all.py 的分工

- `verify.py`：开发时频繁跑的快循环 —— 覆盖语法/文档引用/i18n/编译/启动链冒烟，
  不含 pytest（测试需要重依赖、开销大）。
- `check_all.py`：合入或发版前的全量门禁 —— 额外跑 ruff 风格检查和 `tests/` 的 pytest。
  首次使用先 `--install` 装 ruff 和 pytest。

---

## 二、开发辅助

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/run_module.py` | CLI 测试文本检测器 | `python scripts/run_module.py run_detector --proj_dir <路径>` |
| `scripts/pie_menu_test.py` | 饼菜单/快捷菜单离线功能测试（状态机/命中判定/命令注册，独立进程沙箱配置）；功能已上线，后续加功能卡片等小修小补可复用 | `python scripts/pie_menu_test.py` |

---

## 三、构建 / 部署 / 更新

| 脚本 | 平台 | 用途 |
|---|---|---|
| `scripts/build_win.bat` | Windows | Nuitka 编译为独立 `.exe` |
| `scripts/build_portable.py` | Windows | 构建便携版（embedded Python，`launch.bat` 使用） |
| `scripts/check_update.py` | 跨平台 | 启动时检查更新：git 增量 / manifest delta / zip 三种模式（`launch.bat`/`launch.py` 调用） |
| `scripts/generate_manifest.py` | 跨平台 | 生成 `manifest.json`（全文件 SHA256 清单，供 delta 更新用）。**发版前必须重新生成并随版本提交** |
| `scripts/download_models.bat` | Windows | 下载模型文件到 `data/models/` |
| `scripts/download_models.sh` | Linux/macOS | 下载模型 + 编译 PyPatchMatch（`modules/inpaint/patch_match.py` 仍依赖） |
| `scripts/local_gitpull.bat` | Windows | 使用便携环境执行 `git pull` |

---

## i18n 翻译工作流

```
# 1. 在代码中用 self.tr("English text") 包裹新字符串
# 2. 需要时自动填充缺失条目 + 清理冗余条目
python scripts/ts_auto_fill.py --apply

# 3. 编译为 .qm
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm

# 4. 验证完整性
python scripts/i18n_check.py --ci
python scripts/check_all.py           # 全量门禁（含 ruff + pytest）
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
- **`check_all.py` 在缺少 opencv/torch/numpy 等重依赖时会自动跳过测试**，不影响其他检查。
- **ruff 发现的 E501（行太长）等风格问题是项目历史遗留**，不影响功能。
- **`manifest.json` 是 `generate_manifest.py` 的产物**，不要手工改；发版流程里记得重新生成。