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

| 路径 | 用途 |
| ------ | ------ |
| `launch.py` | 入口，命令行参数，PyTorch 设备 |
| `utils/proj_imgtrans.py` | 项目管理（页面、文字块、撤销栈） |
| `utils/textblock.py` | 核心数据单元（坐标、原文、译文、字体、遮罩） |
| `utils/font_scan.py` | 字体名称表扫描与家族名归并（canonical 优先中文名）+ 精确 PS 名索引供 PSD 导出；`shared.init_font_list` 消费其结果；`compute_simplify_map` 为 FontExcludeDialog「一键精简」规则，产物写 `pcfg.simplified_font_map`（细则见各函数 docstring） |
| `utils/base_styles.py` | 项目级大样式 + 变体发现（身份键 `(font_family, vertical)`，override 量化 diff 聚类派生子样式）；`discover_style_tree` 驱动样式管理器树 |
| `modules/translators/trans_agent.py` + `modules/translators/agent/` | 翻译 agent：`AgentTranslator`（继承 `LLM_API_Translator`）作唯一 LLM 翻译路径；function calling 多轮循环 + 只读探索工具 + 唯一 `submit_translations` 提交出口；循环/工具/prompts/validator 纯逻辑在 `agent/` 包，设计见 `docs/技术实现/翻译agent化_设计方案.md` |
| `utils/config.py` | 配置读写 |
| `utils/shared.py` | 路径常量 |
| `utils/structures.py` | `nested_dataclass`，`Config`/`Dict` 基类 |
| `utils/profile_manager.py` | LLM Profile 数据层（加载/保存/查找/网络探测，翻译器/OCR/在线修复共用）+ 同文件的选取解析层（`profile_is_usable`/`get_default_profile_name`/`resolve_profile`/`heal_profile_selector`，语义见 docstring；消费点不回退「列表第一项」） |
| `ui/llm_profile_cards.py` | LLM Profile 卡片式设置页（形态对齐上游 `ballontranslator/ui/llm_profile_widgets.py`） |
| `utils/ai_tools.py` | 翻译 agent/术语工作台共享的只读探索工具执行器（4 只读工具 + `to_openai_tools`） |
| `utils/block_tags.py` | 块标签体系数据层：类型注册表（6 标签，其中「误识别文本」为程序专用、不提供人工打标途径）+ 读写 + 审阅表态（`reviewed`）+ 人工改写原文后清该块程序标签（D29）+ 「OCR 置信度低」「误识别文本」自动挂标（分数存条目不喂 AI）；`TextBlock.tags` 随项目 JSON 保存；详见 `docs/基础速查/AI辅助标签体系使用说明.md` |
| `utils/block_actions.py` | 框级 AI 动作（OCR 校正/重译）注册表 + vision 调用 + 选中跟随工具栏；载荷在主线程按前端观感组装；配套：执行器 `ui/block_action_runner.py`、确认卡 `ui/block_action_card.py`、撤销写回 `ui/textedit_commands.py::ApplyBlockTextCommand`、动作前数据一致性修复 `ui/mainwindow.py::_sync_block_data`；详见 `docs/基础速查/AI辅助标签体系使用说明.md` |
| `ui/batch_ops.py` | 批量操作的写回范式（五步顺序：前置对齐 → 只改 `proj.pages` → 页代数 → 当前页 `updateSceneTextitems` → 此后禁止 `updateTextBlkList`）与事务外壳（落盘 → 写版本 → 写回 → 落盘）；验收判据＝完成后 `utils/block_actions.py::page_data_needs_sync` 为假 |
| `ui/batch_inpaint.py` | 批量「简单背景」纯色修复（工作台任务，D3／D34）：把 `modules/inpaint/base.py::classify_simple` 的判据跑遍全书页（该判据 D45 补了「块局部遮罩」与「裁剪区外补 3px」，只降「判不出」、不动阈值），**简单块纯色覆盖、复杂块完全不动**（`inpaint` 的 `only_simple` 模式，不加载模型），判据一律算原图、只写 `inpainted/` 层、跑完重载当前页、不标脏；整批走 `ui/batch_ops.py::BatchOperation` 的版本撤回 |
| `ui/batch_merge.py` | 批量合并相邻框（工作台任务，设计 §13／§15 与 D7／D8／D30～D33／D40）：`plan` 只读聚组（相邻判据只看几何、不加载图像）＋ `apply` 一次性重建（保留既有顺序、`text`／`rich_text`／`lines` 同序、样式取组内最小索引成员、`tags` 取并集）＋ `group_crop` 审批截图（100% 原比例、外扩遇邻框即停）；排除带未驳回误识别标签的框（D30）、误聚组默认不勾选（D33d）。**分组判据的实测口径不确定，阈值收在 `ui/batch_merge.py::MergeConfig`**，主机复算见设计 §16；审批界面已随工作台 UI 落地（`apply` 另收 `reversed_groups`＝D32 的「反转该组方向」） |
| `ui/batch_delete.py` | 一键批量删除误框（工作台任务，D2／D27／D28）：队列＝带「误识别文本」标签的块（`utils/block_tags.py::iter_misread_blocks` 的口径），**只删文本框层、不碰遮罩与修复图**；缺省只删未驳回的块（驳回与「勾选待删除」两轴正交，显式勾选不受限），非队列成员一律不删（进 `stale`）；跨页批量，写回走 `ui/batch_ops.py::BatchOperation`（D40 五步 + D35 版本撤回），删除前须弹窗告知后果（D27） |
| `ui/batch_expand.py` | 批量框扩张（工作台任务，D5／D36）：只改 `_bounding_rect` 与 `xyxy`（渲染区域），**掩码与修复数据原样保留**；**扩张量由调用方给定**（`amount` + `mode`＝`px`／短边比例，设计文档未定义这个量，故不设默认值）；同页**串行**扩张（邻框取已扩后的矩形）以保证结果两两不重叠，代价是顺序相关；旋转框与退化矩形不参与；写回走 `ui/batch_ops.py::BatchOperation`，且写的是块**副本**（原地改会让版本快照记成改动后状态、撤销失效） |
| `ui/region_redetect.py` | 区域再检测的任务层（**人工在画布上拉一个矩形 → 只在矩形内跑「文本检测 + OCR」**，无 Qt widget）：`ui/region_redetect.py::RegionRedetect` 的 `plan(page_key, rect)` 只读（裁剪外扩 24px → 检测 → 坐标回映射 → **按四边形中心点**做区域外过滤 → 重叠 >50% 的已有块判为替换）／`build_page(plan)` 纯函数（一次手势的新块**整组**插到同一下标，判据与 `utils/textblock.py::sort_regions` 同源＝先纵向排行、只在同一行内才比 x；兜底＝追加到末尾）／`apply(plan)` 数据层写回（`pages` + 页级掩码并集 + 落盘）。几何判据一律按 `TextBlock.lines` 四边形算，**不用 `xyxy`**（倾斜框会误判）；新块样式继承同页最近块、字号按实际检出框量出来；检测器取 `utils/config.py::ProgramConfig` 的 `region_redetect_detector`（默认 `ppocrv6_onnx`），**设备取 `region_redetect_device`（默认 `cpu`：CUDA 会话一次吃 +835MB 且卸载只回收 50MB，CPU 只 +89~134MB、单次手势只慢约 20ms）**。设计与实测数字见 `docs/技术实现/区域再检测_设计与实现.md`；**落点判据是几何启发式，改动必须过回归台 `scripts/region_redetect_order.py`**（真工程上核对新块是否落回原位） |
| `ui/region_redetect_tool.py` | 区域再检测的 UI 层：`ui/region_redetect_tool.py::RedetectCommand`（**一次拉框 = 一步撤销**：替换 + 新增 + OCR 文本 + 标签；掩码是栈外图像写入、撤销不回退）、后台线程（检测要建 ONNX 会话，OCR 也放后台；**不碰 QWidget**）、`ui/region_redetect_tool.py::RegionRedetectTool` 控制器（接画布手势、通知中心进度提示、**每次手势收尾卸掉检测器**、拒收管线运行中／已切页的结果）。OCR 走模块本身的 `run_ocr`（**不直接调识别模型**，否则丢自动挂标） |
| `ui/workbench_preview.py` | 工作台审批预览浮层 `WorkbenchPreviewPanel`（D44）：挂在 `MainWindow.centralStackWidget` 之上的 in-window 浮层（同 `ui/custom_widget/rail_dock_panel.py::RailDockPanel` 的做法，跟着窗口走、盖在画布页上，**不占工作台宽度**），拖标题条移动 + 四边四角拉伸 + Esc/× 关闭。**默认 100% 原比例**（D23 口径不变：不自动适应窗口），滚轮以光标为锚点缩放、左键拖拽平移、双击适应窗口。原先嵌在任务页里固定 120~280px 高，大图看不全（实测 433×395px 就被截） |
| `ui/glossary_agent_panel.py` | **泛用工作台的容器**（设计见 `docs/技术实现/AI辅助功能_设计与实现.md` 的「工作台」部分；D19～D27：工作台＝任务容器，不是聊天窗）：两级导航 `ui/glossary_agent_panel.py::WorkbenchTaskNav`（D42：一级＝管线阶段大类「文字与 OCR／图像修复／翻译」，二级＝该大类下的任务 chip；计数「还有 N 个未处理」两级都缀）+ 每任务一页 + 底部状态条（只读日志 + 「撤销上次批量」，D43。日志行按任务缀名，页面级摘要不重复进日志）；`GlossaryAgentWorker` 仍是术语／剧情的权威草稿持有者，日志落面板底部（D19 已砍 Chat）；批量任务的事务外壳在此建好注入（带主窗口 `_sync_and_commit_project`／`_sync_block_data`，见设计 §14 的 D40 第 ① 步）。入口＝左栏 `ui/mainwindowbars.py::LeftBar` 的 workbenchChecker（D25 单入口），跳转走 `ui/mainwindow.py::on_workbench_jump`（复用具脏页惰性重渲的 pageList 链路，D26） |
| `ui/workbench_tasks.py` | 工作台四个批量任务的**数据侧适配层**（`MisreadTask`／`MergeTask`／`ExpandTask`／`SimpleInpaintTask`，**无 QWidget**）：调引擎只读 `plan` 摆成列表行、备 100% 原比例审批截图与叠加框、把勾选标识交回 `apply`，并给 D27 的告知弹窗正文与 D37 的未处理计数。**界面不写几何、不碰 `proj.pages`、不绕开 `ui/batch_ops.py`**：几何唯一动作是审批截图外扩，复用 `utils/block_geometry.py::expand_limited` |
| `ui/workbench_batch_view.py` | 工作台四个批量任务共用的三段视图 `BatchTaskView`（D20／D23／D27）：勾选列候选列表 → 参数控件 + 执行行（无勾选即禁用）；执行前统一弹 D27 告知窗，错误码文案表在本模块；列表由任务的 `plan` 驱动，界面只做"填列表／收勾选／交回标识"。审批图不在本页（D44）：选中行经 `ui/workbench_batch_view.py::BatchTaskView` 的 `preview_requested` 信号交给浮层 |
| `utils/block_geometry.py` | 文本框几何小工具：矩形部分（`rect_of`／`overlap_ratio`／`gap_length`／`expand_limited`）＝**"外扩到碰到邻框为止"的唯一实现**（`ui/batch_merge.py` 审批截图 D31、`ui/batch_expand.py` 批量扩张 D5 共用）；四边形部分（`poly_of`／`poly_center`／`poly_bands`／`poly_overlap_ratio`）供 `ui/region_redetect.py` 的倾斜框判据共用 |
| `utils/batch_versions.py` | **仓库唯一的批量备份口**：执行前写一版（项目数据 + 受影响矩形的像素前图）到项目目录内 `.bt_batch_backup/`，撤销取最新一版覆盖并消耗（`restore_latest`／`discard_latest` 支持 `expect_seq` 版本号校验）；版本数取 `utils/config.py::ProgramConfig` 的 `batch_backup_versions`（默认 1、上限 5），跨会话随项目保留。查找替换（`ui/global_search_widget.py`）与工作台批量任务共用 |
| `utils/memory_release.py` | 手动释放内存（设置页「释放内存」按钮，`ui/mainwindow.py::MainWindow` 收确认弹窗与忙闲判据）：卸载模型 → 销毁 CUDA 上下文（真还 ~200MB、GPU 侧全还）→ 交回工作集（`EmptyWorkingSet`，实测 1503MB → 111MB，**是"交回"不是 free**，下次访问要 fault in）。编排收一份可注入的读数／卸载回调（单测用替身、不碰真 CUDA）；三条硬约束＝先 `torch.cuda.empty_cache()` 再 reset、没有 CUDA 活在跑、卸载失败就不 reset。设计与全部实测数字见 `docs/技术实现/内存释放_设计与实现.md` |
| `ui/mainwindow.py` | 主窗口 |
| `ui/configpanel.py` | 配置面板、快捷键编辑；四个管线页合并为一项「Pipeline」（页内标签，`ui/configpanel.py::_build_pipeline_page`），阶段只编辑当前引擎的参数 |
| `ui/run_pipeline_dialog.py` | 运行对话框：启用模块网格（阶段图标开关 + 模块下拉）+ 各阶段折叠选项区；模块下拉写回底部栏选择器 |
| `ui/text_panel.py` | 文本编辑面板 |
| `ui/panel_rail.py` | 嵌字页格式区左缘窄栏：功能图标列（画布浮层面板入口，见 `ui/custom_widget/rail_dock_panel.py`） |
| `ui/io_thread.py` | 管线编排（检测→OCR→翻译→修复） |
| `ui/textitem.py` / `ui/text_engine/` | 画布文字渲染（textitem 是 fork 适配层，渲染实现在 engine） |
| `ui/overlay_modal.py` | `OverlayModal` — 中心淡入/淡出模态（scrim 覆盖中央画布区，ConfigPanel 用它） |
| `ui/overlay_slide.py` | `OverlaySlider` — 覆盖面板滑入滑出动画（GlobalSearchWidget、PageList 用它） |
| `ui/custom_widget/` | 可复用控件库（`__init__.py` 统一导出，见下方"打包控件功能"） |
| `config/` | `config.json`(gitignore), `stylesheet.css`, `themes.json`, `custom_themes.json`, `textstyles/` |
| `scripts/` | `verify.py`, `check_docs.py`, `check_syntax.py`, `check_audit.py`, `check_showcase.py`, `qm_compile.py`, `i18n_check.py`, `workbench_recalc.py`（工作台参数复算台，六个只读子命令）、`workbench_render.py`（工作台各任务页渲染成 PNG，目视验收布局用）；`scripts/probes/` 是真机探针（内存归因/释放阶梯、区域再检测验收），自带说明 |

## 打包控件功能

`ui/custom_widget/` 封装了完整的可复用控件库，通过 `__init__.py` 统一导出：
`from ui.custom_widget import ConfigCheckBox, NoArrowsSpinBox, …`。

**输入类控件必须用封装类，禁止直接实例化 `QComboBox`/`QSpinBox`/`QLineEdit`/`QTextEdit`**：
它们的圆角输入样式走类名选择器，原生类静默掉样式（`QSpinBox`/`QTextEdit` 甚至完全无规则）。
下拉框/数字/单行/多行/复选框分别用 `ConfigComboBox`/`NoArrowsSpinBox`/`ConfigLineEdit`/`ConfigTextEdit`/`ConfigCheckBox`。
按钮、滚动条、菜单、单选、tab 等有全局 QSS 兜底，原生类即可。详见使用说明文档「样式生效机制：全局兜底 vs 类名选择器」一节。

**核心模式**（详见 [`docs/基础速查/打包控件功能使用说明.md`](docs/基础速查/打包控件功能使用说明.md)）：

| 模式/控件 | 一句话说明 |
|-----------|----------|
| `ConfigSubBlock` 禁用自动变灰 | `changeEvent` 自动处理禁用态 label 颜色 |
| "—" 占位符模式 | 禁用数值字段时以 "—" 替代，`blockSignals` 防误触 |
| `NoArrowsSpinBox` 族 | 无箭头、主题感知的数字/文本/下拉/滚动条控件族；支持 Blender 式横向拖拽调值（拖拽中静默改显示、松手经 `drag_finished` 提交一次）。**数值输入默认用本族**，禁止裸 `QSpinBox`/`QDoubleSpinBox`；变换面板/效果卡两个特例见使用说明「模式 E」 |
| `ColorSwatchBtn` | 色块按钮，`setColor()`/`color()` + `colorChanged` 信号 |
| `pick_screen_color()` | 屏幕吸色管：全屏覆盖 + 8x 放大镜，左键取色、右键/Esc 取消（冻结帧采样，事件驱动不卡 UI） |
| `ConfigScrollBar` | 全局统一的 8px 圆角滚动条（含悬停动画） |
| `ClockDial` | 指针式角度/距离选择（影子方向用） |
| `ConfigSectionHeader` | 配置面板章节标题 |
| `GroupFrame` | 圆角边框分组容器 |
| `NotificationCenter`（`ui/custom_widget/notification.py`） | 统一画布通知中心：toast / 活动 spinner / 状态角标；模块级单例 `notification`，Canvas 初始化时 attach 后由各模块调用 |
| `RailDockPanel` | 画布区浮层面板（硬连接锚定窄栏左侧，开合记忆 `pcfg`） |
| `FloatDropPanel`（`ui/custom_widget/float_drop_panel.py`） | 按钮锚定下拉浮层（`ui/global_search_widget.py` 用；无开合记忆）；与 RailDockPanel 的选型对比见使用说明 |

新增控件时更新上表即可，无需展开详细用法。优先使用已有方案而非重新实现。

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

## 快捷键系统

定义在 `ui/configpanel.py` 的 `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`，`_SHORTCUT_GROUPS` 分组。安装/刷新见 `ui/mainwindow.py` 的 `_install_shortcuts()`/`refreshShortcuts()`。用户配置持久化在 `pcfg.shortcuts`（`config.json`）。详见 `docs/基础速查/快捷键.md`。

## 动画系统

- `ui/overlay_modal.py::OverlayModal` — 中心淡入/淡出模态（scrim 仅覆盖 `centralStackWidget`；ConfigPanel 用它）；`pcfg.animation_fps<0` 跳过
- `ui/overlay_slide.py::OverlaySlider` — 侧滑滑入面板（GlobalSearchWidget、PageList 用它）
- `ui/mainwindowbars.py::StateChecker`（`QCheckBox` 子类）实现 LeftBar 面板互斥切换；ConfigPanel 为**内部分页**（`QStackedWidget`，NavList 点击切页），子 `QDialog` 打开时经 `_run_modal_dialog` 暂停 backdrop 点击

## 开发日志

功能增删或修复经用户确认无误后，在 [`docs/daily_log.md`](docs/daily_log.md) 写简要记录。格式参照已有条目：日期标题 → 问题/需求描述 → 改动要点 → 涉及文件列表。每条之间用 `---` 分隔。仅保留最近 3 天记录。**本文件只记仓库层面的改动**（功能增删、远端分支变动、规范调整）；踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见上方「多代理协作」），不进仓库。
