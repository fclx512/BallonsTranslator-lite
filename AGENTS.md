# CLAUDE.md

> 本项目 CLAUDE.md 已提交到仓库中（不在 `.gitignore`），多设备间通过 git 同步。

## 项目概述

漫画/图片翻译工具。五阶段管线：文字检测 → OCR → 翻译 → 图像修复 → 文字渲染。PyQt6 桌面应用，插件式模块系统。

本分支是减法导向的 fork：精简优先，不添加未被要求的功能；交互路径越短越好。

## 架构

每个管线阶段使用注册器模式（`utils/registry.py`），模块通过装饰器自注册：

```text
modules/
  textdetector/  →  detector_*.py  (@register_textdetector)
  ocr/           →  ocr_*.py       (@register_ocr)
  translators/   →  trans_*.py     (@register_translator)
  inpaint/       →  *.py           (@register_inpainter in inpaint/base.py)
  base.py        ←  BaseModule, 模块发现, 设备检测
```

`modules/base.py` 的 `init_module_registries()` 按文件命名模式扫描各目录，动态导入匹配的模块。

## 关键文件

| 路径 | 用途 |
| ------ | ------ |
| `launch.py` | 入口，命令行参数，PyTorch 设备 |
| `utils/proj_imgtrans.py` | 项目管理（页面、文字块、撤销栈） |
| `utils/textblock.py` | 核心数据单元（坐标、原文、译文、字体、遮罩） |
| `utils/config.py` | 配置读写 |
| `utils/shared.py` | 路径常量 |
| `utils/structures.py` | `nested_dataclass`，`Config`/`Dict` 基类 |
| `utils/profile_manager.py` | LLM API 配置管理（翻译器/OCR 共用） |
| `utils/ai_tools.py` | AI 辅助工具函数 |
| `ui/mainwindow.py` | 主窗口 |
| `ui/mainwindow_mixin.py` | MainWindow 业务逻辑 mixin |
| `ui/configpanel.py` | 配置面板、快捷键编辑 |
| `ui/text_panel.py` | 文本编辑面板 |
| `ui/io_thread.py` | 管线编排（检测→OCR→翻译→修复） |
| `ui/scene_textlayout.py` | 画布文字渲染 |
| `ui/overlay_modal.py` | `OverlayModal` — 中心淡入/淡出模态（scrim 覆盖中央画布区，ConfigPanel 用它） |
| `ui/overlay_slide.py` | `OverlaySlider` — 覆盖面板滑入滑出动画（GlobalSearchWidget、PageList 用它） |
| `ui/custom_widget/` | 可复用控件库（`__init__.py` 统一导出，见下方"打包控件功能"） |
| `config/` | `config.json`(gitignore), `stylesheet.css`, `themes.json`, `custom_themes.json`, `textstyles/` |
| `scripts/` | `run_module.py`, `qm_compile.py`, `i18n_check.py` |

## 打包控件功能

`ui/custom_widget/` 封装了完整的可复用控件库，通过 `__init__.py` 统一导出：
`from ui.custom_widget import ConfigCheckBox, NoArrowsSpinBox, …`。

**核心模式**（详见 [`docs/打包控件功能使用说明.md`](docs/打包控件功能使用说明.md)）：

| 模式/控件 | 一句话说明 |
|-----------|----------|
| `ConfigSubBlock` 禁用自动变灰 | `changeEvent` 自动处理禁用态 label 颜色 |
| "—" 占位符模式 | 禁用数值字段时以 "—" 替代，`blockSignals` 防误触 |
| `NoArrowsSpinBox` 族 | 无箭头、主题感知的数字/文本/下拉/滚动条控件族 |
| `ColorSwatchBtn` | 色块按钮，`setColor()`/`color()` + `colorChanged` 信号 |
| `ConfigScrollBar` | 全局统一的 8px 圆角滚动条（含悬停动画） |
| `ClockDial` | 指针式角度/距离选择（影子方向用） |
| `ConfigSectionHeader` | 配置面板章节标题 |
| `GroupFrame` | 圆角边框分组容器 |

新增控件时更新上表即可，无需展开详细用法。优先使用已有方案而非重新实现。

## 文档规范

所有文档放 `docs/`，文件名中文化，无英文版本。

## 配置系统

- `config/config.json` 已 gitignore（含 API 密钥），其余配置/样式文件被跟踪。
- 模块声明 `params: Dict` 自动渲染为 UI 表单。以 `_` 开头的 key 为内部参数，save/load 时保留。

全局配置 `pcfg`（`utils/config.py:301`）是模块级单例：

- 改 `pcfg` 后须显式调用 `save_config()`。仅 `closeEvent` 和 `ConfigPanel.hideEvent` 触发自动保存。
- 启动顺序：`launch.py` 先 `load_config()` 再 `init_module_registries()`。

## Git 规则

- **除非用户要求，否则不提交。** 改动先展示审查。
- **"提交" = `git add -A`** — 暂存工作区全部修改（含未跟踪文件）。
- **提交聚合原则：** 工作完成后，用 `git reset --soft <基准>` + `git commit` 将同批次逻辑相关的多个提交聚合为一个原子提交再推送。避免给远端推送碎片化小提交。聚合前先向用户确认消息内容。
- **禁止 `git commit --amend`：** amend 会重写 commit hash。如果旧 hash 已被推送（包括 git GUI 自动推送），本地与远程历史分叉，`git pull` 必然产生多余的 merge 提交。要修改已推送的 commit，用 `git reset --soft <基准>` + 重提交 + `git push --force-with-lease`，且须先经用户同意。
- **禁止在 commit 信息中添加 AI 署名。** 作者只为 `提交者自己`，不添加 `Co-Authored-By` 或任何其他协作署名行。
- **禁止使用 `git push --tags`：** 会把本地所有 tag（含上游 remote 拉下来的残留 tag）一并推送到 fork 远端，造成混淆。**发版推送 tag 时用精准推送：`git push origin vx.y.z`**（只推送指定 tag）。如果误推了多余 tag，用 `git push --delete origin <tag>` 逐个清理。

## i18n 翻译

流程：`self.tr("English")` → `translate/zh_CN.ts` → `translate/zh_CN.qm`。

- 所有 UI 文字用 `self.tr()` 包裹，严禁硬编码中文。
- ts 中 `<context>` 对应类名，`<message>` 对应 tr 字符串。
- 编译：`python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
- 验证：`python scripts/i18n_check.py`；发版前 `--ci`。
- `self.tr()` 字符串必须是单个字符串，不要用隐式拼接（`"a" "b"`）—— `i18n_check.py` 按行扫描，检测不到跨行拼接。长字符串在 `tr(` 后换行即可。
- `--ci` 对 orphan 条目（ts 有但代码无对应 `self.tr()`）退出码 4。通常是 `self.tr(variable)` 间接调用，运行时正常。
- **⚠️ 快捷键面板等使用 `self.tr(variable)` 间接调用的地方最易漏翻译**。`_ShortcutRow` 通过 `self.tr(_ACTION_NAMES[id])` 渲染动作名，`ShortcutEditor` 通过 `self.tr(group_name)` 渲染分组标题——这些字符串在 i18n_check 中永远显示为 orphan，需手动在 ts 对应 `<context>` 中添加 `<message>`。新增/删除快捷键动作时，ts 的 `_ShortcutRow` 和 `ShortcutEditor` 上下文要同步更新。
- 模块参数 `description` 用英文，翻译放 `<context><name>ParamWidget</name></context>`。
- 无需翻译：日志、LLM prompt、字体测试字符、语言映射字典。
- 常见问题：source 大小写不一致；context 放错；`type="obsolete"`。批量编辑 ts 用 Python 脚本直接操作文本。
- **⚠️ QM 编码陷阱**：`scripts/qm_compile.py` 旧版用 `latin-1` 编码，会把 `—`/`→`/`⚠`/`✓` 等非 Latin-1 字符静默替换成 `?`，导致 Qt 哈希查找失败、翻译回退为英文。`self.tr()` 正确但运行时仍显英文 → 查 qm 是否被污染，确保 `_iso8859_str()` 用 `"utf-8"` 后重新编译。诊断脚本与细节见 [`docs/i18n.md`](docs/i18n.md)「常见问题」。

## 测试流程

改代码后按以下顺序验证，避免遗漏回归：

1. **语法检查**（必做）：`./ballontrans_pylibs_win/python.exe scripts/check_syntax.py <改动的文件...>`
   - 检查 Python 语法编译 + tab 字符 + UTF-8 BOM
2. **i18n 检查**（涉及 UI 字符串时必做）：`./ballontrans_pylibs_win/python.exe scripts/i18n_check.py`
   - 新增 `self.tr()` 必须在 `translate/zh_CN.ts` 对应 `<context>` 中有 `<message>`
   - 编译：`./ballontrans_pylibs_win/python.exe scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
3. **启动冒烟测试**（修改了 `profile_manager.py` / `configpanel.py` / `launch.py` 等初始化代码时必做）：`./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py`
   - 模拟关键导入链，捕捉 `NameError` / `ImportError`（如漏 import `QFrame`）
   - 包含 `ProfileManagerWidget` 实例化测试（offscreen QApplication）
4. **启动 app 目视确认**（可选，但推荐）：双击 `launch.bat` 或 `python launch.py`
   - 确认导航、页面切换、新功能视觉效果正常

## 快捷键系统

定义在 `ui/configpanel.py` 的 `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`，`_SHORTCUT_GROUPS` 分组。安装/刷新见 `ui/mainwindow.py` 的 `_install_shortcuts()`/`refreshShortcuts()`。用户配置持久化在 `pcfg.shortcuts`（`config.json`）。详见 `docs/快捷键.md`。

## 动画系统

`ui/overlay_modal.py` 的 `OverlayModal`（中心淡入/淡出 + 压暗 scrim，scrim 仅覆盖 `centralStackWidget`；ConfigPanel 用它，duration 350ms, easing `InOutExpo`，`pcfg.animation_fps<0` 跳过）与 `ui/overlay_slide.py` 的 `OverlaySlider`（侧滑滑入；GlobalSearchWidget、PageList 用它）。`MainWindow` 中分别为 ConfigPanel（`_configModal`）、GlobalSearchWidget、PageList 各创建一个实例。`StateChecker`（`QCheckBox` 子类）实现 LeftBar 面板互斥切换。ConfigPanel 现为**内部分页**（`QStackedWidget`），NavList 点击切页而非滚动；子 `QDialog` 打开时经 `_run_modal_dialog` 暂停 backdrop 点击。

## 开发日志

功能增删或修复经用户确认无误后，在 [`docs/daily_log.md`](docs/daily_log.md) 写简要记录。格式参照已有条目：日期标题 → 问题/需求描述 → 改动要点 → 涉及文件列表。每条之间用 `---` 分隔。仅保留最近 3 天记录。
