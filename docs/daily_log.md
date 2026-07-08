# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-07-07

### 全新启动时自动读取系统语言

**问题/需求：** 全新启动（无 `config.json`）时始终显示英文界面，而非用户系统语言（如中文 Windows 应显示中文）。

**根因：** `launch.py` 中 `load_config()` 先执行创建 `ProgramConfig()`（其 `display_lang` 默认值取自 `shared.DEFAULT_DISPLAY_LANG`），而后才检测 `QLocale.system()` 并更新 `shared.DEFAULT_DISPLAY_LANG`——此时 `pcfg` 已固定为 `"English"`。

**改动：**

- `launch.py` — 将系统语言检测提前到 `load_config()` 之前，使首次启动时 `ProgramConfig().display_lang` 直接继承正确的系统语言（如 `"zh_CN"`）而非 `"English"`；同时增加 `VALID_LANG_SET` 校验，不支持的 locale 回退为英文
- 已有 `config.json` 时不受影响——`display_lang` 从文件中读取，覆盖默认值

**涉及文件：** `launch.py`

### 左侧面板展开改为推 canvas 而非遮挡

**问题/需求：** PageList 和全局搜索展开时以 OverlaySlider 浮动在 `centralStackWidget` 上，遮挡画布左侧图片内容。

**改动：**

- `ui/mainwindow.py` — 将 `leftStackWidget`（PageList）和 `global_search_widget` 从 `centralStackWidget` 的浮层子控件改为嵌入 `mainHLayout`（leftBar 与 centralStackWidget 之间）；移除两个 `OverlaySlider` 实例，替换为 `_animate_panel_width()`（timer + `setFixedWidth` 动画，350ms InOutExpo）；展开时直接推 canvas 右移，零遮挡；`setupImgTransUI` 同步简化

**涉及文件：** `ui/mainwindow.py`

**问题/需求：** 竖排文本框的縦中横（tate-chu-yoko）数字/字母存在两个外观问题：（1）横排字符视觉上轻微偏左；（2）文本框边缘的横排数字被边框裁剪。

**改动：**
- `ui/scene_textlayout.py` — `updateDrawOffsets` 中 tate-chu-yoko 分支：添加比例修正值 `xoff += cfmt.tbr.width() * 0.06` 补偿末字右 bearing（替代无法适应字号的固定值）；添加基于 `line_width`（naturalTextWidth）的边界 clamp，在绘制偏移层将 edge 列的溢出拉回 `[0, max_width]` 可见区内，避免裁剪且不干扰描边/阴影对齐

**涉及文件：** `ui/scene_textlayout.py`

---

### 系统诊断 dialog 信号导航接入

**问题/需求：** 系统诊断 dialog 的跳转按钮（[Settings →]、[Details →]、[Check →]）需打通到 ConfigPanel 的对应页面/Tab。

**改动：**
- `ui/configpanel.py` — `_open_system_diagnostic` 接入 `open_tools_requested` → ToolsDialog 自动切 Tab、`open_settings_requested` → `_focus_on_dl_section` 跳管线页；`_open_tools_dialog` 增加 `tab_hint` 参数
- `ui/system_diagnostic_dialog.py` — 新增 `open_tools_requested`/`open_settings_requested` 信号

**涉及文件：** `ui/configpanel.py`、`ui/system_diagnostic_dialog.py`

---

### 系统诊断工具 v2（卡片式）— 完整重写

**需求：** 原诊断对话框仅纯文本信息转储，无法直观识别问题也无法操作。需要改为可交互的健康检查面板，支持管线模块功能测试和问题跳转。

**改动：**

1. **`utils/env_diagnostic.py`** — 新增 `check_module_status()`（遍历四大模块注册表，检测当前配置模块的加载状态）；`test_module_functional()`（四步测试：解析类 → 源码/模型文件 → 实例化 → API 连通性测试或设备状态）；`dependency_summary()`（快速依赖总览供卡片摘要用）

2. **`ui/system_diagnostic_dialog.py`** — 完全重写：
   - `_Card` 卡片组件（`QGroupBox` flat，圆角边框）
   - 四张卡片：运行环境（Python 版本/启动路径/OS）、GPU 状态（显卡/PyTorch CUDA/onnxruntime）、管线模块（每模块加载状态 + [Test] 按钮 + 错误行内展示 + [Settings →] 跳转）、依赖检查（摘要 + [Details →][Check →] 跳转 ToolsDialog）
   - 测试日志仅保留一个实例、每次点击刷新替换
   - `_ModuleTestWorker` 后台线程执行测试

3. **`ui/configpanel.py`** — `_open_system_diagnostic` 接入信号导航

**已知待改进（下轮）：**
- 配色暗色模式适配需实机验证
- 测试按钮缺实时进度反馈
- 翻译器 API 测试依赖 `httpx`，当前可能未声明在 `pyproject.toml` 中

**涉及文件：** `utils/env_diagnostic.py`、`ui/system_diagnostic_dialog.py`、`ui/configpanel.py`

---

### Pipeline 测试功能设计计划

**需求：** 当前诊断测试只做导入+实例化检查，不触发真实推理。需要一张项目内置测试图跑完整管线，让用户获得切实反馈（检测框数、OCR 识别率、翻译返回、修复效果）。

**产出：**
- `docs/pipeline_test_计划.md` — 完整设计方案，涵盖 manifest 场景清单、PipelineTestRunner、TestPreviewWindow 预览图窗、暗色模式修复

**讨论要点：**
- 测试图存放 `assets/test_scenes/`，manifest 管理场景-阶段映射
- 用户勾选阶段，系统自动匹配测试图，无需感知图片路径
- 纯翻译走文本文件，不弹图窗
- 有画面效果的阶段可打开预览窗口（检测框叠加 / 修前修后切换 / OCR 对比表）
- 等待用户制作测试图后实施

**涉及文件：** `docs/pipeline_test_计划.md`

---

### pylibs 依赖目录比对与裁减

**需求：** 旧版依赖目录 `ballontrans_pylibs_win（旧/）（Python 3.13）` 与新目录（Python 3.12）存在大量差异，需比对后处理不一致项。

**改动：**

- `_scripts/diff_pylibs.py` — 新增目录对比脚本（文件数/大小/哈希/分类差异报告）
- 从新版 `ballontrans_pylibs_win` 中卸载 **polars**（~176 MB，ultralytics 传递依赖但非必需）
- 重新安装 **spacy-pkuseg==1.0.1**（旧版有、新版缺失）
- 删除旧版目录 `ballontrans_pylibs_win（旧/）`（~1.1 GB, 33k+ 文件）
- `docs/依赖库说明.md` — 更新包列表和体积说明

**涉及文件：** `_scripts/diff_pylibs.py`、`_scripts/pylibs_diff_report.md`、`docs/依赖库说明.md`

---

### 上游启动逻辑合并（6 批）

**改动：**

1. `utils/lazy_registry.py` — SafeEval 增强（`platform.machine()`、`shared.ON_WINDOWS/ON_MACOS/ON_LINUX`）；`_collect_translator_langs` 支持 `lang_map.update()` 和 `self.lang_map={...}` 完整赋值；`metadata_warnings` 追踪；`validate_lazy_module_specs` 诊断函数
2. `utils/registry.py` — ModuleSpec 新增 `metadata_warnings: List[str]`
3. `utils/shared.py` — 新增 `ON_MACOS`、`ON_LINUX` 常量
4. `utils/core_requirements.py` — 新增：启动时核心依赖 probe（qtpy、numpy、PIL 等），非侵入式提示而非自动安装
5. `utils/network_mirrors.py` — 新增：首次运行自动检测中国 locale/时区，写入 HuggingFace/PyPI 镜像到 config.json
6. `utils/version.py` — 新增：从 pyproject.toml 读取版本号（`0.2.0`），消除 launch.py 硬编码
7. `launch.py` — MSVC 运行时预加载（qtpy 导入前）、core_requirements 集成、网络镜像自动检测、版本号改为动态读取

**涉及文件：** `launch.py`、`utils/lazy_registry.py`、`utils/registry.py`、`utils/shared.py`、`utils/core_requirements.py`（新）、`utils/network_mirrors.py`（新）、`utils/version.py`（新）

---

## 2026-07-06

### 文本框重排——画布右键菜单解除禁用

**需求：** 右键菜单中的 Reorder 子菜单在画布上右键时因 `is_textpanel` 条件被禁用。经核查无技术理由（画布选中与 TextPanel `checked_list` 已双向同步，`move_selected()` 对两者一视同仁），解除限制。

**改动：**
- `ui/canvas.py:1198` — `reorder_menu.setEnabled(is_textpanel and 0 < n_sel < n_total)` → `setEnabled(0 < n_sel < n_total)`

**涉及文件：** `ui/canvas.py`

### 文本框重排面板（撤回记录 — 现状已完整记录，改动已回滚）

**需求：** 右侧 TextPanel 中新增可折叠「重排文本框」面板 + 4 个键盘快捷键，替代仅靠拖拽重排。

**改动文件（5 文件，+285 行）：**

1. `ui/textedit_area.py` — 新增 `TextEditListScrollArea.move_selected()`（支持 up/down/top/bottom/to_pos 五种移动，全排列 diff 确保索引正确）；新增 `ReorderContent` 控件：Row1 = ▲▼⏫⏬ QToolButton，Row2 = sel_info_label + Pos 输入 + Go 按钮；连接 `selection_changed` 信号实时更新 UI
2. `ui/scenetext_manager.py` — `TextPanel` 在切换行下方插入 `CollapsibleSection`（`expanded=False`，默认折叠）；`on_rearrange_blks()` 中 reorder 后 emit `selection_changed` 刷新选择信息
3. `ui/configpanel.py` — `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`/`_SHORTCUT_GROUPS` 新增 4 项（move_up/move_down/move_top/move_bottom）
4. `ui/mainwindow.py` — `_install_shortcuts()` 注册 4 个快捷键 + 对应 handler 方法
5. `translate/zh_CN.ts` — `TextPanel` 上下文新增 `"Reorder Text Blocks" → "重排文本框"`

**撤回原因：** 实机验证发现三个问题：① i18n 理解偏差（面板标题未走 `self.tr`）；② 快捷键触发重排后索引未完整更新（`updateTextBlkItemIdx` 只更新 tgt 位置，被挤占项索引标签错乱）；③ 重排后 `selection_changed` 未 emit 导致 `sel_info_label` 未刷新。已修复 (前 3 个 commit 包含修正) 后决定整体回滚到另一台设备继续排查。

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`

---

### 文本框重排——右键菜单实现

**需求：** 文本框重排面板撤回后改用右键菜单 + 快捷键。面板方案暴露信号覆盖盲区（`selection_changed` 只在列表勾选时 emit，画布选中不触发），四个按钮恒不可用。

**改动要点：**
- `ReorderContent` 控件 + CollapsibleSection 整体删除，重排入口全部移至 TextPanel 区右键菜单
- 保留 `move_selected()`（整组移动，`result_list` 全排列 diff 复用 `_emit_rearrange_from_perm`）和快捷键注册项（默认 `[]`）
- Canvas 新增 `reorder_textblks = Signal(str, int)`，`SceneTextManager` 连接到 `textEditList.move_selected`
- 右键菜单 "Reorder" 子菜单含 Move Up/Down/Top/Bottom + "Move to Position…"（`QInputDialog.getInt`）
- 子菜单按 `is_textpanel` 启用（画布右键不显示），Move up/down/top/bottom 按选中位置细粒度禁用
- i18n：Canvas 上下文加 7 条翻译，回滚 TextPanel/ReorderContent 残留条目；qm 重编译 858 条

**涉及文件：**
- `ui/textedit_area.py` — 删 ReorderContent/reordered，保留 move_selected/_emit_rearrange_from_perm
- `ui/scenetext_manager.py` — 删 CollapsibleSection，连 canvas.reorder_textblks → move_selected
- `ui/canvas.py` — 右键菜单 Reorder 子菜单 + reorder_textblks 信号
- `ui/mainwindow.py` — 4 个快捷键 handler（_reorder_move 守门）
- `ui/configpanel.py` — 快捷键 4 项（子 agent A 完成）
- `translate/zh_CN.ts` + `.qm` — 新增 Canvas 重排条目，回滚面板残余条目

---

### 启动时页面列表默认关闭

**需求：** 左侧项目图片列表在启动时默认打开（由之前会话的配置持久化导致），改为每次启动默认关闭。

**改动：**
- `ui/mainwindow.py:515-518` — 启动时强制 `pcfg.show_page_list = False` + `setChecked(False)`，不再还原上次配置状态

**涉及文件：** `ui/mainwindow.py`

---

## 2026-07-05

### 获取模型列表对话框添加搜索筛选栏

**问题/需求：** 管理 API 配置文件 → 获取模型列表中，模型数量多时「获取模型列表」弹窗为纯单选下拉列表，无搜索功能，在大模型列表（如 OpenRouter 数百个模型）中定位困难。

**改动：**

1. `utils/profile_manager.py` — 新增 `FilterableListDialog` 类（搜索栏 + 可筛选 `QListWidget` + 双击/按钮确认）；`_on_fetch_models()` 中原 `QInputDialog.getItem` 替换为 `FilterableListDialog`，输入即筛选（大小写不敏感），搜索栏自动获取焦点

**涉及文件：** `utils/profile_manager.py`

---

### API 配置文件管理新增测试连接功能

**问题/需求：** ProfileManagerDialog 已有「Fetch Models」间接做连接测试，但需要独立的一键测试连接功能，且要求 Host 和 API Key 均必填才发送请求。

**改动：**

1. `utils/profile_manager.py` — Host 输入框行新增「Test」按钮；新增 `_on_test_connection()` 方法，先校验 Host 和 Key 非空，再用 `GET {host}/models` 验证连通性，区分 HTTP 错误/连接失败/超时等场景分别给出中文提示；支持读取 profile 的 proxy 设置
2. `translate/zh_CN.ts` — 新增 10 条翻译条目，已编译（844 条）

**涉及文件：** `utils/profile_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 画布序号徽标固定 100% 不透明度 + 启动闪退修复

**问题/需求：** ① 画布文本框左上角的序号徽标跟随文本框不透明度变化，用户希望始终 100% 显示；② 闪退：`RowIndexLabel` 调用 `setTextMargins`（QLabel 无此方法），启动时报 `AttributeError`。

**改动：**

1. `ui/textitem.py:811` — `_draw_seq_badge` 中 `painter.save()` 后加 `painter.setOpacity(1.0)`，徽标不受文本框 `setOpacity` 影响
2. `ui/textedit_area.py:318` — `self.setTextMargins` → `self.setContentsMargins`

**涉及文件：** `ui/textitem.py`、`ui/textedit_area.py`

---

### 设置面板改为独立 OS 窗口

**问题/需求：** ConfigPanel 内嵌在 `centralStackWidget` 中，复杂 widget 树与 canvas 在同一渲染表面，已打开项目时显示设置面板仍有明显掉帧。

**改动：**

1. `ui/configpanel.py` — 窗口标志改为 `Qt.WindowType.Tool`（标准标题栏 + 无任务栏入口）；`setWindowTitle("Settings")` + `setMinimumSize(700, 450)` 允许用户拖拽调整大小
2. `ui/overlay_modal.py` — 重写：panel 不再作为 `cover_widget` 子 widget，改为独立 OS 窗口；移除缓存截图动画机制（`_cache`/`_cache_effect`/`_swap_to_real_panel`/`_cleanup_cache`），改用 `setWindowOpacity()`（DWM 合成，无每帧 render-to-texture）；`setFixedSize`→`resize`，允许自由调整；`_center_window()` 用 `mapToGlobal` 映射到屏幕坐标
3. `ui/mainwindow.py` — ConfigPanel 创建 parent 改为 `self`（MainWindow），移除冗余 `setParent`
4. `translate/zh_CN.ts` + `zh_CN.qm` — 新增 `"Settings" → "设置"` 翻译，重新编译（845 条）

**涉及文件：** `ui/configpanel.py`、`ui/overlay_modal.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### RowIndexLabel 双击编辑功能移除

**问题/需求：** 画布右侧文本编辑列表中，左侧的顺序编号（`RowIndexLabel`）双击可切换到 `QLineEdit` 编辑模式，用户修改后导致序号显示错乱。

**改动：**

1. `ui/textedit_area.py` — `RowIndexLabel` 从 `QStackedWidget(QLabel + QLineEdit)` 简化为 `QLabel` 子类，保留 `setSizePolicy(Maximum, Maximum)` 维持原尺寸表现；移除 `mouseDoubleClickEvent`/`startEdit`/`keyPressEvent`/`try_update_idx` 等整条编辑信号链；清理不再使用的 import
2. `ui/scenetext_manager.py` — 移除 `pair_widget.idx_edited` 死连接

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`

---

### onTextBlkItemSizeChanged IndexError 修复

**问题/需求：** 偶发 `IndexError: list index out of range`，位置 `scenetext_manager.py:683 onTextBlkItemSizeChanged`。根因：批量删除/重排 `textblk_item_list` 期间（如 `deleteTextblkItemList` 循环中），`canvas.removeItem()` 触发的场景重排导致剩余 `TextBlkItem` 发出 `doc_size_changed`，携带的是旧 `idx`，但此时列表已缩短尚未调用 `updateTextBlkItemIdx()`，索引越界。

**改动：**
- `ui/scenetext_manager.py:682` — `onTextBlkItemSizeChanged` 开头加 `if idx >= len(self.textblk_item_list): return` 边界守卫，与其姊妹方法（`on_textedit_redo`、`on_pairw_focusout` 等）保持一致模式

**涉及文件：** `ui/scenetext_manager.py`

---

## 2026-07-08

### install_cuda.bat 重写

**问题/需求：** 安装 CUDA PyTorch 脚本存在多个错误：① GPU 检测用多行 Python `-c`，cmd 逐行解析为独立命令导致 `'import' is not recognized`；② 硬编码 `torch==2.7.1` 与 `cu124` 索引不匹配（cu124 仅到 torch 2.6.0）；③ `torchaudio` 在 cu132 索引中不存在导致 pip 安装失败；④ 文件保存为 LF 行尾 + Unicode 字符（`─` `→`），Windows cmd 完全无法解析。

**改动：**

- `install_cuda.bat` — GPU 检测改用单行 Python + `nvidia-smi --query-gpu=compute_cap` 获取计算能力（CC 主版本号），按 CC 映射 CUDA 版本（CC≥10→cu132, CC≥9→cu130, CC≥8→cu126, CC≥7→cu124, CC≥6→cu118），无需硬编码 GPU 型号；去掉版本固定，`-U` 自动升级为 CUDA variant；移除 `torchaudio`；添加 `INSTALL_MODE`（replace/manual）方便环境共存；纯 ASCII + CRLF 行尾；安装成功提示末尾附加 polars 无警告说明，避免用户误解

**涉及文件：** `install_cuda.bat`
