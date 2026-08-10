# Scripts 目录使用说明

本项目脚本按用途分为三类。所有路径相对于项目根目录。

---

## 一、质量保障 / 自动化检查

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/check_all.py` | **一键检查**：i18n + QM 编译 + ruff + pytest | `python scripts/check_all.py` |
| `scripts/i18n_check.py` | 审计 i18n（硬编码中文、缺失/多余的 .ts 条目） | `python scripts/i18n_check.py` |
| `scripts/ts_auto_fill.py` | 自动同步 `self.tr()` 调用与 `.ts` 文件 | `python scripts/ts_auto_fill.py --apply` |
| `scripts/qm_compile.py` | 编译 `.ts` → `.qm`（Qt 二进制翻译文件） | `python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm` |
| `scripts/update_translation.py` | 调用 `pylupdate5` 扫描 UI 代码生成/更新 `.ts` | `python scripts/update_translation.py` |
| `scripts/regenerate_translations.bat` | 同上，Windows 批处理版 | 双击或命令行执行 |

### check_all.py 详细用法

```
python scripts/check_all.py              # 完整检查
python scripts/check_all.py --quick      # 快速（跳过 ruff）
python scripts/check_all.py --fix        # 自动修复 ruff 问题
python scripts/check_all.py --ci         # CI 模式（exit non-zero 表示失败）
python scripts/check_all.py --install    # 安装缺失依赖（ruff, pytest）
python scripts/check_all.py --skip-tests # 跳过测试
python scripts/check_all.py --skip-ruff  # 跳过 ruff
```

首次使用建议先跑 `--install` 安装 ruff 和 pytest。

---

## 二、开发辅助

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `scripts/run_module.py` | CLI 测试文本检测器 | `python scripts/run_module.py run_detector --proj_dir <路径>` |
| `scripts/pie_menu_test.py` | 环形菜单离线功能测试（状态机/命中判定/命令注册，独立进程沙箱配置） | `python scripts/pie_menu_test.py` |
| `scripts/svgscript.py` | SVG 图标工具（替换颜色、压缩路径） | `from scripts.svgscript import set_svgcolor, minify_svg` |
| `scripts/BTjson_to_LPtxt.pyw` | 将 BallonsTranslator JSON 转为 LabelPlus TXT 格式 | 双击（GUI 文件选择） |
| `scripts/export to photoshop/` | 导出到 Photoshop 的脚本 | 见目录内说明 |

---

## 三、构建 / 部署

| 脚本 | 平台 | 用途 |
|---|---|---|
| `scripts/build_win.bat` | Windows | Nuitka 编译为独立 `.exe` |
| `scripts/download_models.bat` | Windows | 下载模型文件到 `data/models/` |
| `scripts/download_models.sh` | Linux/macOS | 下载模型 + 编译 PyPatchMatch |
| `scripts/local_gitpull.bat` | Windows | 使用便携环境执行 `git pull` |

---

## i18n 翻译工作流

```
# 1. 在代码中用 self.tr("English text") 包裹新字符串
# 2. 扫描代码生成/更新 .ts 文件
python scripts/update_translation.py

# 3. （推荐）自动填充缺失条目 + 清理冗余条目
python scripts/ts_auto_fill.py --apply

# 4. 编译为 .qm
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm

# 5. 验证完整性
python scripts/i18n_check.py --ci
python scripts/check_all.py           # 一键跑完所有检查
```

---

## 注意事项

- **所有 `*.bat` 脚本仅限 Windows。** 请在命令提示符或 PowerShell 中运行，不要双击（部分脚本含暂停逻辑）。
- **所有 `*.sh` 脚本仅限 Linux/macOS。** 需要 `chmod +x`。
- **`config/config.json` 已 gitignore**（含 API 密钥），请勿提交。
- **运行检查脚本前**，请确保工作目录是项目根目录：
  ```bash
  cd BallonsTranslator-lite
  python scripts/check_all.py
  ```
- **`check_all.py` 在缺少 opencv/torch/numpy 等重依赖时会自动跳过测试**，不影响其他检查。
- **ruff 发现的 E501（行太长）等风格问题是项目历史遗留**，不影响功能。
