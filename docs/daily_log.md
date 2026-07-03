# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-07-02

### 底部栏精简：移除翻译源/目标语言选择器

**问题/需求：** 底部快捷栏的翻译区域同时显示 Source + 源语言下拉、Target + 目标语言下拉，占用空间大但调整频率低（源语言默认自动识别，目标语言很少切换），底部空间本就局促。

**改动：**

1. `ui/mainwindowbars.py` — `TranslatorSelectionWidget` 移除 `label_src`、`src_selector`、`label_tgt`、`tgt_selector` 及对应布局/信号/初始化逻辑；`finishSetTranslator` 只设置翻译器名，不再填充语言列表
2. `ui/mainwindow.py` — 移除 `src_selector`/`tgt_selector` 的信号连接和同步逻辑；`on_trans_src_changed`/`on_trans_tgt_changed` 仅同步配置面板

**影响：** 源/目标语言仍在配置面板（Translate 设置页）可修改，底部栏仅保留「Translate 标签 + 翻译器选择器 + 配置齿轮按钮」。

**涉及文件：** `ui/mainwindowbars.py`、`ui/mainwindow.py`

---

### YSGYoloDetector 多项改进：默认模型更新/工具列表下载/设置项宽度整理

**问题/需求：** ① 旧默认模型 `ysgyolo_1.2_OS1.0.pt` 已有作者推荐的新版 `ysgyolo_yolo26_2.0.pt`（YOLO26, 1600px 训练, 2-4x 数据）；② 工具列表中该模型无下载源（`source: ""`），复选框不可勾选；③ 缺少原作者仓库链接；④ 设置项过宽：label 六个复选框横排撑满、model path combobox 宽达 332px 且下拉项带 `data/models/` 前缀冗余。

**改动：**

1. `modules/textdetector/detector_ysg.py` — 文件头添加原作者 HuggingFace/GitHub/Telegram 链接；默认模型改为 `ysgyolo_yolo26_2.0.pt`；新增 `download_file_list` 类属性（HF resolve URL + `concatenate_url_filename: 2`）；`params["model path"]["size"]` 降为 `"short"` (200px)；`update_ckpt_list()` 只存文件名去掉 `data/models/` 前缀；`_load_model()` 补充路径解析逻辑（裸文件名 → 拼接 `MODEL_DIR`，绝对路径直接使用）

2. `ui/model_check_dialog.py` — 移除 `_EXTRA_FILES`（ysgyolo 走 `download_file_list` 自动发现后，复选框可正常勾选下载）；同步移除 `ysgyolo_note` 残余逻辑

3. `ui/module_parse_widgets.py` — `ParamCheckGroup` 从 `QHBoxLayout` 改为 `QGridLayout` (3 列)，水平占用减半

4. `docs/配置参考.md` — 默认模型路径同步为新版

**兼容性：** 用户自放的 `ysgyolo*`/`ultralyticsyolo*` 模型仍被 `CKPT_LIST` 前缀扫描自动发现，不受影响；已保存的旧配置（含 `data/models/` 前缀路径）因 `_load_model` 的 `startswith(MODEL_DIR)` 判断仍可直接使用。

**涉及文件：** `modules/textdetector/detector_ysg.py`、`ui/model_check_dialog.py`、`ui/module_parse_widgets.py`、`docs/配置参考.md`

---

## 2026-06-30

### 文本框序号徽标显隐开关

**问题/需求：** 画布文本框左上角的顺序徽标（`_draw_seq_badge`）在小字体场景会遮挡内容，需要设置开关控制显隐。

**改动：**

1. `utils/config.py` — `ProgramConfig` 新增 `show_seq_badge: bool = True` 字段
2. `ui/textitem.py` — `_draw_seq_badge` 增加 `pcfg.show_seq_badge` 检查
3. `ui/configpanel.py` — Interface 区新增 "Show sequence number on text blocks" 复选框 + `seq_badge_changed` 信号 + 槽函数
4. `ui/mainwindow.py` — 连接信号，遍历画布 TextBlkItem 调用 `update()` 即时刷新
5. `translate/zh_CN.ts` + `.qm` — 新增 3 条翻译（标签/分组名/备注说明），已编译

**涉及文件：** `utils/config.py`、`ui/textitem.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

### 新增 AI 快捷参考文档

**改动：** `docs/新增设置项路线参考.md` — 记录 5 层实施路线（Config → Render → UI → Signal → Translation），含关键文件速查、代码片段、验证清单。

---

### 无字图配对工具深度改造

**问题/需求：** 漫画汉化中修图最耗时且质量不稳定，图源自带无字版时需工具辅助配对。现有配对工具交互繁琐，需改进为高效的手动匹配流程。

**改动：**

1. **拖拽交互增强** — `ImageSlot` 新增 `_drag_over` 标志位 + `_update_style()`，拖拽经过时显示青色描边反馈；`dragMoveEvent`/`dragLeaveEvent`/`dropEvent` 组合确保提示正常消失；Ctrl+拖拽走 `QDrag.exec_(CopyAction)` 实现复制而非移动。

2. **差分剧情底图共享** — 多选槽位后右键「设置为同一底图」，将首选的 `image_path`/`original_name` 复制到其余选中槽，保留各自 `display_name`；`shared_label` 标记共享底图的槽位。

3. **导入方式扩展** — 支持多选文件导入（`QFileDialog.getOpenFileNames`），按序填充空槽；导入文件夹保持顺序填充，不做用户不可预期的自动匹配。

4. **导出改进** — `QProgressDialog` 进度条 + 覆盖前 `QMessageBox.question` 确认；导出完成仅状态栏提示，不弹无关对话框。

5. **预览弹窗增强** — `PreviewDialog._render_diff()` 用 `QPainter.CompositionMode_Difference` 实现差异叠加模式，`_toggle_diff()` 切换并排/差异视图。

6. **快捷键速查** — `ShortcutDialog` 类 + F1/`?` 弹出快捷键面板。

7. **工具栏精简** — 去除了自动匹配（`auto_match`、`SequenceMatcher`），重排为 `[打开有字图] [导入无字图] [选择文件] [导出到notext] [更多 ▼]` 五按钮布局。

8. **窗口持久化** — `_load_persist`/`_save_persist` 读写 `tools/.sort_history.json`，保存上次文件夹路径和窗口几何；`closeEvent` 清理 `_thumbnail_cache`。

**涉及文件：** `tools/无字图配对工具.py`

> 本脚本由群友提供原始代码与使用授权，在此表示感谢🙏

---

### 主项目无字图工具入口

TitleBar Tools 菜单新增「Pair No-text Images…」启动配对工具，`subprocess.Popen` 调用。

**涉及文件：** `ui/mainwindowbars.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-03

### 分发包降级 Python 3.12 + GPU 检测改进

**问题/需求：** 当前分发的 `ballontrans_pylibs_win/` 是用 Python 3.13 构建的。用户若安装 Python 3.12 + CUDA torch，捆绑的 3.13 无法注入 3.12 的 C 扩展（`.pyd` 版本绑死），导致 `_detect_user_torch()` 跳过用户的 CUDA torch，降级到 CPU 模式。

**改动：**

1. **`launch.py` `_detect_user_torch()` PATH 搜索动态化** — 原硬编码 `("python.exe", "python3.exe", "python3.13.exe")` 改为 `Path(_path_dir).glob("python*.exe")`，自动覆盖所有版本号后缀，无需手动维护。

2. **`launch.py` 版本不匹配追踪 + 提示** — 版本不匹配时不再静默跳过：记录找到的 CUDA torch 信息，循环结束后输出包含解决方向的诊断信息（指明分发包 Python 版本不匹配，给出重建命令）。

3. **`scripts/build_portable.py` 确认默认 `3.12.4`** — 构建脚本已默认 Python 3.12，CI（`build-portable.yml`）也已用 `3.12`。重新运行 `python scripts/build_portable.py` 即可产出 3.12 的分发包。

**不处理的方案：**
- re-exec 到系统 Python：用户不接受（依赖安装会占用 C 盘系统 site-packages）
- `sympy`：可选传递依赖，项目代码零引用，无需处理

**涉及文件：** `launch.py`

---

### ballontrans_pylibs_win 重建为 Python 3.12 + 依赖库文档

**问题/需求：** 代码侧就绪后需要实际重建 `ballontrans_pylibs_win/`。原 3.13 环境不含 torch，本次新增 CPU torch + ultralytics + onnxruntime 以保证所有模块在 CPU 模式下功能可用（GPU 仍靠系统 CUDA torch 自动注入）。

**改动：**

1. **重建 `ballontrans_pylibs_win/`** — 删除旧的 Python 3.13 embeddable 环境，从 `python-3.12.4-embed-amd64.zip` 重新搭建；安装 `requirements_core.txt` 核心依赖 + CPU torch（`--index-url cpu`）+ ultralytics + onnxruntime/onnxocr

2. **`docs/依赖库说明.md`** — 新文档记录包列表、体积分析（~1.0 GB → ~1.6 GB 的膨胀原因为首次引入 torch CPU 529 MB + 传递依赖）、手动安装步骤（网络/安全机制失败时的 fallback）、多机同步说明

**体积说明：** 膨胀非 Python 版本切换导致。旧环境不含 torch，新增 torch CPU（529 MB）+ ultralytics + sympy（74 MB）/ matplotlib（33 MB）等传递依赖合计约 +600 MB。

**涉及文件：** `docs/依赖库说明.md`
