# AGENTS.md

> 本项目 AGENTS.md 已提交到仓库中（不在 `.gitignore`），多设备间通过 git 同步。

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

一句话定位 + 改动前必须知道的最小约束；设计背景、实测数字与决策编号（D/N 编号）在指针指向的文档里，**改动相关文件前先读指针**。**不常用的技术点只留一句定位 + 指针**，细节写进文档——这份文件每轮都进上下文，篇幅要省着用。

| 路径                                                                              | 用途与最小约束                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `launch.py`                                                                     | 入口，命令行参数，PyTorch 设备                                                                                                                                                                                                                                                                                                                                      |
| `utils/proj_imgtrans.py`                                                        | 项目管理（页面、文字块、撤销栈）                                                                                                                                                                                                                                                                                                                                         |
| `utils/textblock.py`                                                            | 核心数据单元（坐标、原文、译文、字体、遮罩）                                                                                                                                                                                                                                                                                                                                   |
| `utils/font_scan.py`                                                            | 字体名称表扫描与家族名归并（canonical 优先中文名）+ 精确 PS 名索引供 PSD 导出；`compute_simplify_map` 为 FontExcludeDialog「一键精简」规则；细则见各函数 docstring                                                                                                                                                                                                                                    |
| `utils/base_styles.py`                                                          | 项目级大样式 + 变体发现（身份键 `(font_family, vertical)`），`discover_style_tree` 驱动样式管理器树                                                                                                                                                                                                                                                                              |
| `modules/translators/trans_agent.py` + `modules/translators/agent/`             | 唯一 LLM 翻译路径：function calling 多轮循环 + 只读探索工具 + 唯一 `submit_translations` 提交出口；设计见 `docs/技术实现/翻译agent化_设计方案.md`                                                                                                                                                                                                                                              |
| `utils/config.py`                                                               | 配置读写（`ProgramConfig`/`ModuleConfig`）。**新增配置字段必须声明到对应 Config 类**，否则运行时 AttributeError（`tests/test_config_fields.py` 静态校验）                                                                                                                                                                                                                                 |
| `utils/shared.py`                                                               | 路径常量                                                                                                                                                                                                                                                                                                                                                     |
| `utils/structures.py`                                                           | `nested_dataclass`，`Config`/`Dict` 基类                                                                                                                                                                                                                                                                                                                    |
| `utils/profile_manager.py`                                                      | LLM Profile 数据层 + 选取解析层（`profile_is_usable`/`resolve_profile` 等，语义见 docstring；**消费点不回退「列表第一项」**）；翻译器/OCR/在线修复共用                                                                                                                                                                                                                                          |
| `utils/global_styles.py`                                                        | 全局样式库数据层：跨项目持久样式模板（`config/global_styles.json`），条目与大样式同构；**库是纯模板存储、绝不自动应用到块**，双向复制语义与冲突规则见 `docs/技术实现/全局样式库_设计方案_存档.md`                                                                                                                                                                                                                                  |
| `ui/llm_profile_cards.py`                                                       | LLM Profile 卡片式设置页（形态对齐上游 `ballontranslator/ui/llm_profile_widgets.py`）                                                                                                                                                                                                                                                                                  |
| `utils/ai_tools.py`                                                             | 翻译 agent/术语工作台共享的只读探索工具执行器（4 只读工具 + `to_openai_tools`）                                                                                                                                                                                                                                                                                                   |
| `utils/block_tags.py`                                                           | 块标签体系数据层：前台只有两个人工待办（`ocr_review_pending` 稍后校对／`trans_review_pending` 稍后重译）+ 两个持久翻译指示（`handwritten`／`onomatopoeia`），旧 ID（`ocr_low_conf` 人工来源／`trans_confusing`／`trans_polish`）只读兼容、只在用户取消那条待办时清；审阅表态 `reviewed` 按**单个问题 ID** 记，人工写入口唯一＝`set_manual_tag`；活动待处理问题查询 `active_review_ids`／`has_active_review` 供 E/Q 跳转与菜单可用性共用；详见 `docs/基础速查/AI辅助标签体系使用说明.md` |
| `utils/block_actions.py`                                                        | 框级 AI 动作（OCR 校正/重译）注册表 + vision 调用；**两个动作对任何单选块都开放**（不用先挂标签），`BlockActionDef.consumes` 只声明应用后清哪几条问题记录（含旧 ID）；载荷在主线程按前端观感组装；详见 `docs/基础速查/AI辅助标签体系使用说明.md`                                                                                                                                                                                                |
| `ui/block_action_runner.py`、`ui/block_action_card.py`、`ui/textedit_commands.py` | 框级动作的执行器 / 确认卡 / 撤销写回（`ApplyBlockTextCommand`）；动作前数据一致性修复在 `ui/mainwindow.py::_sync_block_data`                                                                                                                                                                                                                                                          |
| `ui/batch_ops.py`                                                               | 批量操作的写回范式（五步顺序 + 事务外壳，细则见模块 docstring 与设计 §14）；验收判据＝完成后 `utils/block_actions.py::page_data_needs_sync` 为假                                                                                                                                                                                                                                                |
| `ui/batch_inpaint.py`                                                           | 批量「简单背景」纯色修复（D3/D34）：判据跑 `modules/inpaint/base.py::classify_simple`（D45 补「块局部遮罩」与「裁剪区外补 3px」，只降「判不出」、不动阈值），**简单块纯色覆盖、复杂块完全不动**，判据一律算原图、不标脏；整批走 `ui/batch_ops.py::BatchOperation` 版本撤回                                                                                                                                                                    |
| `ui/batch_merge.py`                                                             | 批量合并相邻框（D7/D8/D30～D33/D40）：`plan` 只读聚组（判据只看几何）+ `apply` 一次性重建（样式取组内最小索引成员、`tags` 取并集）+ `group_crop` 审批截图；**分组判据阈值收在 `ui/batch_merge.py::MergeConfig`**，主机复算用 `scripts/workbench_recalc.py`                                                                                                                                                               |
| `ui/batch_delete.py`                                                            | 一键批量删除误框（D2/D27/D28）：队列＝带「误识别文本」标签的块，**只删文本框层、不碰遮罩与修复图**；缺省只删未驳回的块，**命中 `no_japanese` 子类型的行默认不勾选**（D27 补充），非队列成员一律不删；删除前须弹窗告知后果（D27）                                                                                                                                                                                  |
| `ui/region_redetect.py`                                                         | 区域再检测任务层（`plan`/`build_page`/`apply`，无 Qt widget）；设计与实测数字见 `docs/技术实现/区域再检测_设计与实现_存档.md`；**落点判据是几何启发式，改动必须过回归台 `scripts/region_redetect_order.py`**                                                                                                                                                                                                      |
| `ui/region_redetect_tool.py`                                                    | 区域再检测 UI 层：**一次拉框 = 一步撤销**，检测/OCR 在后台线程（**不碰 QWidget**），每次手势收尾卸掉检测器；OCR 走模块本身的 `run_ocr`（不直接调识别模型，否则丢自动挂标）                                                                                                                                                                                                                                               |
| `ui/workbench_preview.py`                                                       | 工作台审批预览浮层（D44）：in-window 浮层（同 `ui/custom_widget/rail_dock_panel.py::RailDockPanel` 做法），不占工作台宽度；**默认 100% 原比例**（D23 口径不变），滚轮锚点缩放、拖拽平移、双击适应窗口                                                                                                                                                                                                              |
| `ui/glossary_agent_panel.py`                                                    | 泛用工作台的容器（D19～D27、D42/D43/D44）：**扁平六项导航**（可疑框清理／合并／原文待校对／简单背景修复／翻译准备／译文待重译，组标题只做视觉分段）+ 每任务一页 + 底部状态条；`GlossaryAgentWorker` 仍是术语/剧情的权威草稿持有者（两块收在「翻译准备」一个入口的两个子页里）；入口＝左栏 `ui/mainwindowbars.py::LeftBar` 的 workbenchChecker（D25 单入口）；见 `docs/技术实现/AI辅助功能_设计与实现.md`「工作台」部分                                                                                   |
| `ui/workbench_review_view.py`                                                   | 工作台两个待办队列的视图（`ReviewQueueView`）：分区列表（人工待办／程序建议各一段）+ 跳画布 + 出队/忽略；**没有删除、没有整批写回、没有版本**；审批图同样交 D44 浮层                                                                                                                                                                                                                                                       |
| `ui/workbench_tasks.py`                                                         | 工作台任务数据侧（**无 QWidget**）：三个批量任务（`MisreadTask`/`MergeTask`/`SimpleInpaintTask`，只读 `plan` + 整批 `apply` + 版本快照）与两个**非删除待办队列**（`OcrReviewTask`/`TransReviewTask`，只有「跳画布」与「从列表移除／忽略」，不复用 `apply` 语义）；**界面不写几何、不碰 `proj.pages`、不绕开 `ui/batch_ops.py`**                                                                                                          |
| `ui/workbench_batch_view.py`                                                    | 工作台三个批量任务共用的三段视图（D20/D23/D27）；执行前统一弹 D27 告知窗；审批图不在本页（D44），经 `preview_requested` 信号交浮层                                                                                                                                                                                                                                                                    |
| `utils/block_geometry.py`                                                       | 文本框几何小工具：矩形部分＝**「外扩到碰到邻框为止」的唯一实现**（`expand_limited`，批量合并与待办队列的审批截图共用）；四边形部分（`poly_*`）供区域再检测共用                                                                                                                                                                                                                                                            |
| `utils/batch_versions.py`                                                       | **仓库唯一的批量备份口**：执行前写一版（项目数据 + 受影响矩形像素前图）到项目内 `.bt_batch_backup/`，撤销取最新一版覆盖并消耗（`restore_latest`/`discard_latest` 支持 `expect_seq` 校验）；查找替换与工作台批量任务共用                                                                                                                                                                                                        |
| `utils/memory_release.py`                                                       | 手动释放内存（设置页按钮）：卸载模型 → 交回工作集。**不做** `cudaDeviceReset`——实测会不可逆地毁掉本进程 CUDA，该能力已删、`tests/test_memory_release.py::RemovedCapabilityTest` 钉着别加回来；实测数字与约束见 `docs/技术实现/内存释放_设计与实现_存档.md`                                                                                                                                                                          |
| `utils/model_files.py`                                                          | 模型文件管理数据层：落盘判据、体积、删除走回收站（只删声明过的 `save_files`）。**新增带权重的模块必须随附 `download_file_list` 落盘路径 + `model_package` 包描述**（`save_dir` 字段不可用），否则缺文件检查与体积展示全部失效；方案与验收见 `docs/技术实现/模型文件管理_设计方案_存档.md`、`docs/技术实现/模型文件管理_测试流程.md`                                                                                                                                        |
| `ui/model_downloads.py`                                                         | 后台下载任务层（单例注册表：起任务／防重复／取消／进度），「选模块」与设置页「模型文件」节共用；**下载不弹窗不阻断交互，进度只进终端**。模块侧两条声明由本层兜：大模型置 `background_download_only`（否则 `load_model` 里同步下载冻界面）、要 GPU 的置 `requires_gpu`（硬闸门在 `ModelDownloadRegistry.start`，判据与文案见设计 §5.5）                                                                                                                                    |
| `ui/model_files_panel.py`                                                       | 设置页 Models →「模型文件」节（`ui/configpanel.py` 只实例化 + 接线）：`ui/custom_widget/row_table.py::RowTable` 卡片列表 + 动作行（下载／取消／删除／打开目录／刷新）+ 状态条；其余细节见设计文档                                                                                                                                                                                                                 |
| `modules/ocr/ocr_vl_manga.py`                                                   | PaddleOCR-VL-For-Manga（HF transformers 后端；日文漫画质量优先、GPU-only、逐块自回归）；接入依据、参数坑与实测数字见 `docs/技术实现/paddle-ocr-for-manga_接入调研_存档.md`                                                                                                                                                                                                                            |
| `ui/mainwindow.py`                                                              | 主窗口                                                                                                                                                                                                                                                                                                                                                      |
| `ui/configpanel.py`                                                             | 配置面板、快捷键编辑；四个管线页合并为一项「Pipeline」（页内标签，`_build_pipeline_page`），阶段只编辑当前引擎的参数                                                                                                                                                                                                                                                                                |
| `ui/run_pipeline_dialog.py`                                                     | 运行对话框：启用模块网格（阶段图标开关 + 模块下拉）+ 各阶段折叠选项区；模块下拉写回底部栏选择器                                                                                                                                                                                                                                                                                                       |
| `ui/text_panel.py`                                                              | 文本编辑面板                                                                                                                                                                                                                                                                                                                                                   |
| `ui/panel_rail.py`                                                              | 嵌字页格式区左缘窄栏：功能图标列（画布浮层面板入口，见 `ui/custom_widget/rail_dock_panel.py`）                                                                                                                                                                                                                                                                                       |
| `ui/io_thread.py`                                                               | 管线编排（检测→OCR→翻译→修复）                                                                                                                                                                                                                                                                                                                                       |
| `ui/textitem.py` / `ui/text_engine/`                                            | 画布文字渲染（textitem 是 fork 适配层，渲染实现在 engine）                                                                                                                                                                                                                                                                                                                 |
| `ui/overlay_modal.py`                                                           | `OverlayModal` — 中心淡入/淡出模态（scrim 覆盖中央画布区，ConfigPanel 用它）                                                                                                                                                                                                                                                                                                 |
| `ui/overlay_slide.py`                                                           | `OverlaySlider` — 覆盖面板滑入滑出动画（GlobalSearchWidget、PageList 用它）                                                                                                                                                                                                                                                                                             |
| `ui/custom_widget/`                                                             | 可复用控件库（见下方「打包控件功能」与 `docs/基础速查/打包控件功能使用说明.md`）                                                                                                                                                                                                                                                                                                           |
| `config/`                                                                       | `config.json`(gitignore), `stylesheet.css`, `themes.json`, `custom_themes.json`, `textstyles/`                                                                                                                                                                                                                                                           |
| `scripts/`                                                                      | `verify.py`, `check_docs.py`, `check_syntax.py`, `check_audit.py`, `check_showcase.py`, `qm_compile.py`, `i18n_check.py`, `trim_daily_log.py`（daily_log 3 天窗口清理，pre-commit 钩子调用）、`workbench_recalc.py`（工作台参数复算台，只读子命令 merge/c1/queue/review/hook/list）、`workbench_render.py`（工作台各任务页渲染成 PNG，目视验收布局用）；`scripts/probes/` 是真机探针，自带说明                                                      |

## 打包控件功能

`ui/custom_widget/` 是可复用控件库，`__init__.py` 统一导出：`from ui.custom_widget import ConfigCheckBox, NoArrowsSpinBox, …`。

**输入类控件必须用封装类，禁止直接实例化 `QComboBox`/`QSpinBox`/`QLineEdit`/`QTextEdit`**：  
它们的圆角输入样式走类名选择器，原生类静默掉样式（`QSpinBox`/`QTextEdit` 甚至完全无规则）。  
下拉框/数字/单行/多行/复选框分别用 `ConfigComboBox`/`NoArrowsSpinBox` 族/`ConfigLineEdit`/`ConfigTextEdit`/`ConfigCheckBox`。  
按钮、滚动条、菜单、单选、tab 等有全局 QSS 兜底，原生类即可。

完整控件速查表、样式生效机制（全局兜底 vs 类名选择器）、核心模式（`ConfigSubBlock` 禁用自动变灰、"—" 占位符、`get_theme_color` 主题色取值、Blender 式拖拽调值、`RailDockPanel` vs `FloatDropPanel` 选型）见 [`docs/基础速查/打包控件功能使用说明.md`](docs/基础速查/打包控件功能使用说明.md)；控件目视展示台 `scripts/style_showcase.py`（`scripts/check_showcase.py` 强制校验新导出的展示登记）。新增控件时在上述文档表格与展示台各追加一行即可；优先使用已有方案而非重新实现。

## 文档规范

所有文档放 `docs/`，文件名中文化，无英文版本。

- 引用文件用**仓库相对路径**（如 `ui/configpanel.py`）；引用符号用 **`路径::符号`**（如 `ui/configpanel.py::DEFAULT_SHORTCUTS`），**不写行号**（行号易漂移）。**禁止裸符号名**（如 `TextStyleDialog`）——check_docs 查表校验不到裸名，符号被删后静默漏网（2026-08-23 教训）；符号一律带文件路径。
- 改动后跑 `scripts/check_docs.py` 校验文档里的路径/符号引用是否失效（已并入 `verify.py`）。仅归档/日志类文档（`daily_log.md`、`经验教训.md`、`上游参考.md`）不在校验范围；`技术实现/` 已纳入校验，引用上游仓库路径时须带 `ballontranslator/`（或 `BallonsTranslator/`、`resources/`）前缀以触发跨库豁免。
- 深度审计（死代码/休眠登记表、删除残留引用）用 `/audit-docs` 技能（`verify.py` 第 3 步已自动跑核心检查）。

## 多代理协作

本仓库可能被多个 AI 代理（WorkBuddy、zcode 等）交替接管，各代理的私有记忆互不可见、也不在版本控制内（项目 `.workbuddy/`、`.zcode/` 与全局的 zcode 记忆库），**不设仓库外的共享记忆区**。**分工：代码写入、提交与推送以 zcode 为主；WorkBuddy 只承担规划与零散杂活。**

- **同一时间只让一个代理有写权：** 两个代理同时写同一工作区时，事后无法分辨谁的改动。切换代理前先收尾（提交，或留一份 `git status` 快照标明哪些是对方的半成品）。
- **多代理交替期间，提交只暂存自己的文件**（`git add <自己的文件>`），不要用 `git add -A`——否则会把对方未完成的改动一起卷进提交；推送交回该提交的代理或用户执行。
- **与 `docs/daily_log.md` 的分工：** daily_log 只记**仓库层面**的改动（功能增删、远端分支变动、规范调整），供变更史查阅。
- **隐私纪律：** 除本文件外，仓库不再新增私人开发笔记——私有记忆、踩坑记录、方案草稿留在各代理侧。既有 `docs/` 文档按原规范维护，不做增重。

## 配置系统

- `config/config.json` 已 gitignore（含 API 密钥），其余配置/样式文件被跟踪。
- 模块声明 `params: Dict` 自动渲染为 UI 表单。以 `_` 开头的 key 为内部参数，save/load 时保留。

全局配置 `pcfg`（`utils/config.py`）是模块级单例：

- 改 `pcfg` 后须显式调用 `save_config()`。仅 `closeEvent` 和 `ConfigPanel.hideEvent` 触发自动保存。
- 新增配置字段必须声明到对应 Config 类（`utils/config.py::ProgramConfig`/`utils/config.py::ModuleConfig` 等）：代码读到未声明字段是运行时 AttributeError（glossary_dock_open、agent_translation_debug_log、source_lang/target_lang 三度翻车）；`tests/test_config_fields.py` 静态校验 pcfg 引用与声明一致。
- 启动顺序：`launch.py` 先 `load_config()` 再 `init_module_registries()`。

## Git 规则

- **除非用户要求，否则不提交。** 改动先展示审查。
- **「完事/收工/收尾」语义：** 用户以上述收尾表述结束任务时，即视为要求**整理工作区并提交**（按逻辑分批组织，消息先展示），但**不推送**——`git push` 一律由用户自己执行；仅当用户明确描述需要"提交+推送"（如「提交并推送」「推上去」）时才执行推送。
- **"提交" = `git add -A`** — 暂存工作区全部修改（含未跟踪文件）。
- **提交聚合原则：** 工作完成后，用 `git reset --soft <基准>` + `git commit` 将同批次逻辑相关的多个提交聚合为一个原子提交再推送。避免给远端推送碎片化小提交。聚合前先向用户确认消息内容。
- **禁止 `git commit --amend`：** amend 会重写 commit hash。如果旧 hash 已被推送（包括 git GUI 自动推送），本地与远程历史分叉，`git pull` 必然产生多余的 merge 提交。要修改已推送的 commit，用 `git reset --soft <基准>` + 重提交 + `git push --force-with-lease`，且须先经用户同意。
- **提交文案：** 标题保留前缀（`fix(ui):` / `feat:` / `docs:`），但正文直白——写成「修复了 xxx 的 xxx 问题」这类一眼看懂的说法，不用代码抽象名（不写"区带化收成唯一真相"这种只有作者懂的措辞）。正文只写"改了哪里 + 为什么"，不逐条罗列实现；要看细节直接读代码。
- **禁止在 commit 信息中添加 AI 署名。** 作者只为 `提交者自己`，不添加 `Co-Authored-By` 或任何其他协作署名行。
- **禁止使用 `git push --tags`：** 会把本地所有 tag（含上游 remote 拉下来的残留 tag）一并推送到 fork 远端，造成混淆。**发版推送 tag 时用精准推送：`git push origin vx.y.z`**（只推送指定 tag）。如果误推了多余 tag，用 `git push --delete origin <tag>` 逐个清理。

## i18n 翻译

流程：`self.tr("English")` → `translate/zh_CN.ts` → `translate/zh_CN.qm`。


- 所有 UI 文字用 `self.tr()` 包裹，严禁硬编码中文。
- ts 中 `<context>` 对应类名，`<message>` 对应 tr 字符串。
- 编译：`python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
- 验证：`python scripts/i18n_check.py`；发版前 `--ci`（报孤儿/缺失即真实问题，须修复）。
- `self.tr()` 字符串必须是单个字符串，不要用隐式拼接（`"a" "b"`）—— `i18n_check.py` 按行扫描，检测不到跨行拼接。长字符串在 `tr(` 后换行即可。
- **模块级翻译表禁止 `self.tr(variable)` 间接查表**（检查器看不见，必漏翻译）：在**字面量定义处**用 `QCoreApplication.translate("上下文", "...")` 显式标注上下文（如 `ui/configpanel.py::_ACTION_NAMES`、`ui/context_menu_config.py`），下游一律直接使用已翻译值（tr 对非匹配串原样返回）。
- 模块参数 `description`（纯数据）由 `scripts/i18n_common.py::extract_param_descriptions` AST 提取，跑 `ts_auto_fill.py` 自动同步；提取范围细则见 [`docs/基础速查/i18n.md`](docs/基础速查/i18n.md)。
- **饼菜单默认名**经 `ui/pie_menu.py::_DEFAULT_MENU_NAME_TR` 锚点字面量供工具链提取（config.py 导入早于翻译器安装，不能就地翻译），勿删。
- 无需翻译：日志、LLM prompt、字体测试字符、语言映射字典。
- 其余坑（ts 结构嵌套、source 大小写、QM 编码陷阱等）统一见 [`docs/基础速查/i18n.md`](docs/基础速查/i18n.md)「常见问题」。

## 测试流程

改代码后按以下顺序验证，避免遗漏回归。**首选统一入口**：

`./ballontrans_pylibs_win/python.exe scripts/verify.py`

一条命令依次跑 语法 → 文档 → 审计 → 展示台覆盖 → i18n → qm → 冒烟；**成功每步只打一行，失败才完整打印报错（据此修复）**。判定要点：语法只查 git 改动涉及的 .py（`--all` 改查全部 ui/+utils/），其余各步全量；qm 在 ts 有改动时自动编译；冒烟在改动命中启动链文件（`launch.py`/`modules/base.py`/`utils/profile_manager.py`/`ui/configpanel.py`/`ui/mainwindow.py`）时自动触发，`--smoke` 可强制；`--full` 为发版门禁，追加 ruff 风格检查 + pytest（`tests/`，未装或重依赖缺失时自动跳过并提示）。判定细节见 `scripts/verify.py` 模块注释。

需要时手动分步跑：

1. **语法检查**：`./ballontrans_pylibs_win/python.exe scripts/check_syntax.py <文件...>`（支持多文件；查编译 + tab 字符 + UTF-8 BOM）
2. **文档校验**：`./ballontrans_pylibs_win/python.exe scripts/check_docs.py`
3. **审计登记表**：`./ballontrans_pylibs_win/python.exe scripts/check_audit.py`（**删除文件前先在 `scripts/audit_registry.json` 登记 `deprecated`**）
4. **i18n 检查**：`./ballontrans_pylibs_win/python.exe scripts/i18n_check.py`；发版前 `--ci`
5. **qm 编译**：`./ballontrans_pylibs_win/python.exe scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm`
6. **启动冒烟测试**：`./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py`（约 2s，捕捉 NameError/ImportError）
7. **启动 app 目视确认**（可选，但推荐）：双击 `launch.bat` 或 `python launch.py`，确认导航、页面切换、新功能视觉效果正常
8. **MainWindow 在线演练台**（可选，排查无声崩溃/模态框/GC 时机类问题）：`./ballontrans_pylibs_win/python.exe scripts/mw_repro.py`——拉起真实主窗口（须窗口模式，offscreen 起不来 FramelessWindow）跑预设场景或 `--project` 只读打开真实工程；用法见 `scripts/README.md`，方法论见经验教训 §3.3

**写用例的口径：只钉"会动"的契约。** 只有会被后续改动碰到的行为契约值得新写用例（用户实测反馈过的缺陷；改个键名就静默失效的协议）。其余不留：功能定型后几乎不再变动的结构/配置断言、"把自己刚写的实现再断言一遍"的用例都不要——默认只跑冒烟即可，真要钉就在既有用例上补一两行。

## 快捷键系统

定义在 `ui/configpanel.py` 的 `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`，`_SHORTCUT_GROUPS` 分组。安装/刷新见 `ui/mainwindow.py` 的 `_install_shortcuts()`/`refreshShortcuts()`。用户配置持久化在 `pcfg.shortcuts`（`config.json`）。详见 `docs/基础速查/快捷键.md`。

## 动画系统

- `ui/overlay_modal.py::OverlayModal` — 中心淡入/淡出模态（scrim 仅覆盖 `centralStackWidget`；ConfigPanel 用它）；`pcfg.animation_fps<0` 跳过
- `ui/overlay_slide.py::OverlaySlider` — 侧滑滑入面板（GlobalSearchWidget、PageList 用它）
- `ui/mainwindowbars.py::StateChecker`（`QCheckBox` 子类）实现 LeftBar 面板互斥切换；ConfigPanel 为**内部分页**（`QStackedWidget`，NavList 点击切页），子 `QDialog` 打开时经 `_run_modal_dialog` 暂停 backdrop 点击

## 开发日志

功能增删或修复经用户确认无误后，在 [`docs/daily_log.md`](docs/daily_log.md) 记一条（**该文件的文件头即写法规范，照它写**）。**这份日志主要读者是 AI**（用户基本不看），因此按**可检索**写、不按可通读写：标题带关键符号名／配置字段名／D 编号，`**摘要：**` 2~4 句只写决策与理由，`**涉及文件：**` 必填；实现细节留给 commit body 与设计文档，一条 4~8 行。

- **只保留最近 3 天记录，由脚本强制、不靠人记**：提交时 `pre-commit` 钩子（`.git/hooks/pre-commit`，版本化副本在 `scripts/hooks/pre-commit`；重新 clone 后 `cp scripts/hooks/pre-commit .git/hooks/pre-commit` 一次性启用）自动跑 `scripts/trim_daily_log.py` 裁掉超出窗口的日期节——写日志时直接写到当天日期标题下即可，无需清理旧条目。
- **更早的记录去 git 找**（被裁掉的日期节仍完整留在提交历史里，这也是敢开 3 天窗口的前提）：`git log --grep <关键词>`／`git log --follow -p -- docs/daily_log.md`／`git show <rev>:docs/daily_log.md`；**提交信息与日志标题用同一批关键词**，`--grep` 才能一步命中，日志被裁掉也不丢线索。
- 本文件只记**仓库层面**的改动（功能增删、远端分支变动、规范调整）；踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见上方「多代理协作」），不进仓库。
