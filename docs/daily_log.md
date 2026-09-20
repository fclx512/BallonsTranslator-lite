# 每日开发日志

> 记录**仓库层面**的改动（功能增删、远端分支变动、规范调整），供变更史查阅。踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见 `AGENTS.md` 的「多代理协作」一节），不进仓库。仅保留最近 3 天的记录（超出窗口的日期节由 `scripts/trim_daily_log.py` 在提交时经 pre-commit 钩子自动清理，无需手工维护），每次在对应日期中末尾写入日志。

## 2026-09-20

### 依赖兼容上游 + 共享环境「只增不升」补装

**问题/需求：** 上游 BallonsTranslator 的依赖库两边高度通用，fork 想兼容共享同一环境（如把上游 `ballontrans_pylibs_win/` 链接过来用），需消除唯一硬版本冲突并保证补装不污染上游正在用的版本。

**改动要点：** 解除 `pillow>=10.0,<11` 钉子（上游环境 pillow 12.2.0 + pillow-jxl-plugin 1.3.7 实测 JXL roundtrip 正常，2026-06 钉子已过时）；`pyproject.toml` 增 `acc` extra（numba 可选加速，缺失照旧回退纯 NumPy）；`utils/core_requirements.py::ensure_core_requirements` 补装前用 `importlib.metadata` 对已装发行版生成 `名称==版本` 快照，经 `utils/package_installer.py::build_install_command` 新增的 `constraints_file` 参数作 `-c` 约束传入——**只增不升**：缺的包照补，任何会升级既有包的解析直接失败、快照留盘交人工决策；`docs/基础速查/依赖库说明.md` 增「与上游共用同一依赖库」一节（差异清单 + 联接用法）。

**测试：** `scripts/verify.py` 全绿；带约束的 `pip install -r requirements.txt --dry-run` 解析无报错无升级；`tests/test_dependency_startup.py` 8 通过。

**涉及文件：** `pyproject.toml`、`requirements.txt`、`utils/package_installer.py`、`utils/core_requirements.py`、`docs/基础速查/依赖库说明.md`

---

### 左栏打开钮图标回归轴线（去 QToolBar 包裹 + 菜单自持）

**问题/需求：** 左栏图标重绘与边距调整后，文件夹打开钮（openBtn）偏左、右侧显得多出边距，与下方四个 33px checker 不在同一条轴上。

**改动要点：** 根因有二：① openBtn 外包一层 33px `QToolBar` 装 28px 按钮，QToolBar 自带边距把按钮挤出轴线；② 左栏 vlayout 实为**左锚排列**（各项同点 x=7），宽度不同即中心错开（与上次运行按钮 28→33 同理）。修法：去掉 QToolBar 包裹、openBtn 直接 33×33 进布局与 checker 同规格；QSS `image` 随按钮盒缩放，`OpenBtn` 规则加 `padding: 3px` 把字形压回原尺寸（实测 24.8 逻辑px 与旧版一致）；菜单改 `ui/mainwindowbars.py::OpenBtn` 自持（`assignMenu` + 点击手动弹出）——不走 `QToolButton.setMenu`，因 InstantPopup 的菜单指示器在右侧保留箭头位会把 QSS image 挤到左边，且 QSS 归零 width/height/image 均无法收回保留区。离屏渲染实测五图标中心 28.5~29.5 对齐（剩余 ±0.75px 为 SVG 内容 1px 不对称）。

**测试：** `scripts/verify.py` 全绿；离屏拉起真实 LeftBar 逐图标量字形中心/宽度确认。

**涉及文件：** `ui/mainwindowbars.py`、`config/stylesheet.css`

---

### 依赖政策：CPU 发行包刻意不装 transformers（`paddleocr_vl_manga` 专属依赖）

**问题/需求：** 准备用 CPU 版依赖包重新打包分发，核对依赖完整性时发现包内缺 `transformers`——`paddleocr_vl_manga` 是唯一走 transformers 的模块，且该导入写在 `_load_model` 内（不在模块顶层），所以「把全部注册模块 import 一遍」这类模块级探针全绿也查不出来，缺包只在用户真正加载这个 OCR 时炸。补装并实测跑通后评估：该模块是「质量优先、GPU 强烈建议」的生成式 VLM，CPU 上逐块自回归解码无实用价值，而 CPU 发行包面向的正是没有独显、跑不了 CUDA 的用户，`transformers` 本体 113 MB 属白占 ⇒ **决定不随 CPU 包分发**。

**改动要点：** `docs/基础速查/依赖库说明.md` 第 6 步由「`ultralytics` 等按需依赖打包时必须预装」改为加入一段明确的例外说明——CPU 包刻意不装 `transformers`，理由（GPU-only 模块 + 目标用户无独显 + 113 MB）、以及 GPU 用户换装 CUDA torch 后在应用里选中该模块即由 `modules/base.py::ensure_dependencies` 自动补装 `>=4.57.1,<5`（区间收在 `modules/ocr/ocr_vl_manga.py::requires_packages`）、不需要 sentencepiece；验证一节把 VL OCR 自检移出 CPU 包验收范围、注明属 GPU 包（`scripts/probes/ocr_vl_manga_accept.py` 要读 `torch.cuda.*` 显存读数，本就只能在 CUDA 包跑）。

**测试：** 补装与卸载都用约束快照（对已装发行版生成 `名称==版本` 作 `-c`，只增不升；`--dry-run` 先确认解析为纯新增）。CPU 包实测：卸载后发行版数回到 79（与改动前一致）、`pip check` 与开发包同一基线（onnxocr 的 `--no-deps` 跳过项、ultralytics/polars 均为既有已知项）、`tests/test_startup_imports.py` 6 通过、14 个注册模块强制解析零失败、各模块声明的权重文件齐备（含 1.84GB 的 `paddleocr_vl_manga`）。顺带实测：该模块在 CPU 包上确实可跑（合成竖排日文图识别出 `ざわ / ざわ`，加载 3.0s、单块 1.7s，fp32），只是不随包分发。

**涉及文件：** `docs/基础速查/依赖库说明.md`（依赖包本体在仓库外）

---

### GPU 硬闸门：无加速设备的机器上拒绝下载 GPU-only 模型（`requires_gpu`）

**问题/需求：** CPU 发行包里 `paddleocr_vl_manga` 只在有 GPU 的机器上有实用价值，但用户在「模型文件」页点它的下载（或在选型界面选中它）仍会真的去拉 1.9GB 权重 + 128MB `transformers`，下完才发现 CPU 上逐块自回归慢到没法用。用户拍板：**CPU 环境弹窗说明利害并拒绝下载，只在 GPU 环境放行**。

**改动要点：** 新增模块侧声明 `modules/base.py::BaseModule.requires_gpu`（贯通 `utils/registry.py::ModuleSpec`、`utils/lazy_registry.py::LAZY_CLASS_ATTRS`、`modules/__init__.py::GET_MODULE_REQUIREMENTS` 的新字段 `requires_gpu`），首用例 `modules/ocr/ocr_vl_manga.py` 置 True；判据 `modules/base.py::accelerator_available` 取 `DEFAULT_DEVICE != "cpu"`（刻意不用 `torch.cuda.is_available()`——xpu/mps/directml 同样跑得动，`BALLOONTRANS_CPU_ONLY=1` 也判无）；硬闸门放在 `ui/model_downloads.py::ModelDownloadRegistry.start`（命中即拒，pip 依赖与权重都不碰），文案与判据同处一地（`gpu_required_message` / `gpu_requirement_block`）。两个入口各按自己的惯例弹同一文案：「模型文件」页下载钮与 `ui/module_manager.py::_ensure_module_deps`（后者是 §5.1「选模块不弹窗」的有意例外，模块照常切换）；面板徽章由「缺失 15」改为「需要 GPU」（`ui/model_files_panel.py::_badge_of` 的 `_gpu_blocked` 分支），行仍可点、点下去就是解释；加载期缺文件提示也换成 GPU 口径（`missing_model_files_hint` 接 `requires_gpu`，`MissingModelFilesError` 带同名属性），不再指路去一个会拒绝你的下载按钮。

**顺带修掉一个 i18n 提取器盲区：** `scripts/i18n_common.py` 的 `QCoreApplication.translate` 正则原先只认「两个参数同一种引号」，`translate("ctx", '文本')` 这种混合写法**两条正则都匹配不到、静默漏进 .ts**——`ui/model_downloads.py` 里三条文案（缺文件提示、pip 装失败、模型文件准备失败）因此在中文界面里一直显示英文。改为每个参数各自匹配引号风格后这 4 条（连同本次新增的拒绝说明）全部进 .ts 并补上中文译文。

**文案由用户定稿**（`ui/model_downloads.py::gpu_required_message`，三句短话：需要 GPU / 下载已拒绝 → CPU 上时间开销远大于模型能力 → 有 N 卡换 CUDA torch、只有 CPU 用内置模型），比初稿删去「已经手工拿到文件」那条出路与 1.9 GB 体积数字。**文案里那句「禁止了 CPU torch 环境下运行和下载」按用户口径保留，而实现只拦下载**——运行侧仍是 `_resolve_device` 警告后退回 CPU，两者不一致已在设计 §5.5 注明。

**测试：** CPU 依赖包实测：闸门拒绝（`start()` 返回 False、注册表无任务、其他 OCR 模块不受影响）；开发包（CUDA 可用）放行；开发包加 `BALLOONTRANS_CPU_ONLY=1` 也拒绝。`tests/test_model_downloads.py::TestGpuGate` 新增 7 项钉住拒绝分支（开发机自带 CUDA，不打补丁根本走不到这条路径）：判据开关、不误伤未声明模块、闸门先于任务创建、加载提示与面板徽章的口径切换、真实注册表里 `paddleocr_vl_manga` 的声明。i18n 三查通过、qm 重编、译文经 `QTranslator` 实测可载出中文。弹窗与徽章渲染图见 `tmp/00_gpu_refused_zh.png`、`tmp/01_gpu_refused_en.png`、`tmp/02_model_files_panel_zh.png`。`scripts/verify.py` 全绿；pytest 1322 通过，2 个失败（`test_tag_toolbar` 的 `context_menu_order` 残留、`test_workbench_panel::test_expand_needs_an_explicit_amount` 列改版后陈旧断言）与本次改动无关、为存量失败。

**涉及文件：** `modules/base.py`、`modules/ocr/ocr_vl_manga.py`、`modules/__init__.py`、`utils/registry.py`、`utils/lazy_registry.py`、`ui/model_downloads.py`、`ui/model_files_panel.py`、`ui/module_manager.py`、`scripts/i18n_common.py`、`tests/test_model_downloads.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`AGENTS.md`、`docs/技术实现/模型文件管理_设计方案.md`（新增 §5.5）、`docs/基础速查/依赖库说明.md`

---

## 2026-09-19

### 工作台批量任务页加刷新钮（重扫候选列表）

**问题/需求：** 工作台页面没有主动刷新入口：导航的「还有 N 个未处理」每次现算，而候选列表是懒规划快照，用户在画布上删框／改框后列表不跟随，出现「计数有数、列表空／过期」（误识别清理尤其常见）。

**改动要点：** 新增 `ui/workbench_batch_view.py::RefreshButton`（QPainter 自绘循环箭头图标钮，主题取色、悬停描边，不引 SVG 资源），在 `BatchTaskView._build_actions` 里置于执行行首位，点击即重跑既有 `replan()`（连带 `plan_changed` → 导航计数同步）。术语／剧情两页是草稿镜像、无此需求，不加。

**测试：** `scripts/verify.py` 全绿；`scripts/workbench_render.py` 渲染并放大目视确认图标可认。

**涉及文件：** `ui/workbench_batch_view.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 行拖拽逐帧绘制优化（多选掉帧）+ 抓住缩放改 1.05

**问题/需求：** 用户实测多选拖拽时性能占用偏高、掉帧（疑似线程繁忙）。

**改动要点：** 定位掉帧主因＝被拖卡装着 `ui/textedit_area.py::_CardScaleEffect`（抓住 1.1× 缩放），而 Qt 对几何变化的控件失效效果源 pixmap 缓存 ⇒ 跟手补间每帧都要把整张卡（两个 QTextEdit 的富文本排版）重渲到设备分辨率离屏图，多选即 N 次/帧。三处改动（`ui/textedit_area.py`）：① **效果源 pixmap 缓存**——拖拽期间卡片内容冻结，装上后首帧抓一次、`draw()` 复用缓存做缩放 blit，factor 回 1.0 弃缓存；缓存字段挂类属性兜底（同空壳实例防崩路径）。② **跟手重定向 2px 死区**（`_move_card` 新增 `dead_zone` 参数，只作用于 `_update_drag_frame` 的追手路径）——光标慢移 1px 步进不再反复停旧建新 `QPropertyAnimation`。③ `GRAB_SCALE` 1.1→1.05。中途实测暴露一处缓存坐标错误：`sourcePixmap(DeviceCoordinates)` 的 `offset` 是**当帧控件在窗口里的绝对位置**（随移动逐帧变化），直接缓存会把卡片钉死在抓取时刻的位置（被拖卡不跟手、只在损伤区露碎片）——改为缓存「pixmap 左上相对控件原点」的常量偏移（padding），每帧用 `deviceTransform` 现算原点重算 offset。

**测试：** `tests/test_row_drag.py` 新增三组：缓存只抓一次（含回 1.0 弃缓存后重抓）、快照跟手移动（控件移动后绘制随新位置走）、跟手重定向死区；27 项连跑 10 次全绿，`scripts/verify.py` 七步全绿。新增用例曾引出套件约半数的退出期 access violation（悬挂效果的控件在解释器退出期 GC 级联中销毁；stdout 缓冲让崩溃看似发生在更早的用例）——用例结束前 `QTest.qWait(300)` 排干收尾动画后 10/10 稳定。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`

---

### RowTable 自绘行列表控件替换工作台裸 QTableWidget

**问题/需求：** 工作台批量候选列表用裸 `QTableWidget`，逐格 QSS 边框渲染不全、背景兜底规则只按裸 QTableView 匹配不及子类，观感与主题接入受限。

**改动要点：** 新增 `ui/custom_widget/row_table.py::RowTable`（经 `ui/custom_widget/__init__.py` 导出）：`MODE_TABLE`＝列对齐紧凑表格（淡行分隔线、无竖网格）、`MODE_CARD`＝主文+次行元数据+右侧徽章的圆角审批卡；底色/选中染底/勾选框/徽章全在 delegate 自绘（行数据经 `Qt.UserRole` 传 dict），QSS 只管底色列头（`RowTable#WorkbenchRowTable`，`config/stylesheet.css`）；主题配色走 `get_theme_color`、缓存于 delegate 且 `StyleChange` 时失效。工作台四个批量任务候选列表切换到该控件（`ui/workbench_batch_view.py` 勾选回调改 `_on_check_toggled`，`ui/workbench_tasks.py` 补卡片元数据与行说明文案）；展示台（`scripts/style_showcase.py`）与 AGENTS.md 控件表同步。

**测试：** `tests/test_workbench_panel.py` 35 项过（勾选用例改走新回调）；i18n 新增 `%1 · %2 block(s)` 一条，`scripts/i18n_check.py` 与 qm 编译过。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/custom_widget/__init__.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`scripts/style_showcase.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_panel.py`、`AGENTS.md`

---

### 本批收尾：RowTable 可读性打磨 + 扩张页改版 + 文档瘦身等（分类型提交）

**问题/需求：** 整理工作区挂起的修改按类型分批提交，另修一处对齐：左栏「运行」按钮与上方设置图标中心错开 2~3px（两者同点左锚但宽度不同：33 vs 28）。

**改动要点：**

- **RowTable 可读性**：悬停行淡染高亮（`setMouseTracking` 收 move，滚动后重算）；选中染底上的文字按染底实际观感色在 `@dragTextColor`／`@inverseTextColor` 里挑对比更高的（部分主题 `@dragTextColor` 是暗色，固定取会看不清），次行元数据选中时用降不透明度主色；全局 QSS 表格选中色同步改 `@dragTextColor` 且 `:hover` 规则置前防盖选中底色。
- **扩张页改版**：`ui/workbench_tasks.py::ExpandTask` 列从「旧矩形/新矩形」坐标改单列「增长」描述（每边长多少、哪边受限，`stretch_column` 改 3）；hint 文案改说清用途与居中重排条件。新增探针 `scripts/probes/expand_centering_probe.py`：真机验证扩张后译文按新框重排，「是否居中」取决于块自身 alignment（块属性，扩张页决定不了）。`ui/workbench_batch_view.py` 加「重扫列表」刷新钮（自绘循环箭头 `RefreshButton`）并清理重复 import 块。
- **font_scan 日志压制修复**：`utils/font_scan.py::scan_font_faces` 原先只压根 logger，压不住 fontTools 模块级子 logger 的自有 handler（`utils/logger.py` 全局 `setLoggerClass` 后每个子 logger 自带 console handler）——改为整棵 `fontTools.*` 树一起压，并预导入 `name`/`OS-2` 表模块防懒加载躲过压制。
- **daily_log 3 天窗口自动裁剪**：新增 `scripts/trim_daily_log.py`（按 `## YYYY-MM-DD` 标题解析，无日期段恒保留）与 `scripts/hooks/pre-commit`（提交时自动裁，版本化副本，重 clone 后 cp 启用）。
- **文档瘦身**：AGENTS.md 关键文件表改「一句话定位 + 最小约束 + 指针」；《经验教训》《上游参考》《依赖库说明》《新增设置项路线参考》《快捷键》砍历史细节与过期内容；《打包控件功能使用说明》RowTable 行同步。
- **对齐修复**：`ui/mainwindowbars.py` 运行按钮宽度 28→33 与 configChecker 一致，中心重合（离屏探针实测 center 均 x=23）。
- **新增调研文档**：`docs/技术实现/paddle-ocr-for-manga_接入调研.md`、`docs/技术实现/模型说明卡_规划.md`。

**测试：** `scripts/verify.py` 七步全绿；对齐用离屏几何探针验证。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`ui/mainwindowbars.py`、`utils/font_scan.py`、`config/stylesheet.css`、`scripts/trim_daily_log.py`（新）、`scripts/hooks/pre-commit`（新）、`scripts/probes/expand_centering_probe.py`（新）、`scripts/README.md`、`scripts/probes/README.md`、`AGENTS.md`、`docs/基础速查/`（6 篇）、`docs/技术实现/`（2 篇新）

---

## 2026-09-18

### 工作台 UI 审计与逐项优化（D44 浮层形态收口 + 一批观感/状态同步修复）

**问题/需求：** 用 `scripts/workbench_render.py` 把六个任务页渲染成图做了一轮观感审计（列出 11 项），用户逐条拍板后落地。

**改动要点：**

- **审批浮层（D44）形态收口**（`ui/workbench_preview.py`）：① 尺寸**随图自适应**（图片 + 标题条 + 边距，上限仍是 620×520；用户拖过标题条／拉伸过之后就不再自动改）——原先恒开 620×520，单气泡截图 134×43 泡在空底里白占画布；② **去掉关闭钮**，点画布（宿主区域）或 Esc 关闭：两者走一个**只在浮层可见期间挂着**的应用级事件过滤器，且**一律不吞事件**（画布自己的 Esc 语义不受影响），关掉即摘掉过滤器不给全局留开销；③ **不再抢键盘焦点**——原 `show_content` 收尾 `setFocus` 会把焦点从候选列表夺走，↑/↓ 翻行当场失效；④ 标题改报「哪一页哪个框」（`ui/workbench_tasks.py::row_caption`，各任务按自己的行粒度覆盖；页面名过长才按省略号截断），原先那句「100% 原比例预览（134 × 43 px）」既每行都不同又与缩放读数重复；⑤ 重规划（含批量执行完）与换任务时自动收起——原先会留着**已被删掉那个块**的截图。
- **按钮可读性**：标题条两个动作钮（适应窗口／`1:1`）加 1px 描边 + 正常前景色（`config/stylesheet.css`）；「回到 100%」改名 `1:1`，与右侧同为小字灰字的缩放读数彻底区分。
- **参数控件**：`ui/workbench_batch_view.py` 的 bool 参数改用 `ConfigCheckBox`（裸 `QCheckBox` 的 indicator 走原生样式，白底深勾与表格里的勾选框两套观感）；数值框后缀随「单位」选项走（`suffix_map`：px 模式 `10 px`、比例模式 `10 %`），`px` 与 `1:1` 按通用单位记号**不翻译**（含设置页那个默认扩张量输入框）。
- **状态同步**：参数一变即刷新导航上的「还有 N 个未处理」（新增 `plan_changed` 信号——原先改扩张量到 0、列表都空了，chip 还写着 (7)）；无选中行时「跳到画布」禁用（原先装了能点却什么都不做）。
- **文案收短**：四个任务的说明、空态提示，以及术语表／剧情页各补一行说明（原先只有空表）；扩张页提示里「请先设定扩张量——没有默认值」与输入框预填 10 自相矛盾，删该句；设置页「工作台（临时）」两条备注重写为两句话。
- **渲染台**：`scripts/workbench_render.py` 的宿主改 `WA_DontShowOnScreen` + `show()`——直接 `show()` 会被窗口管理器压到屏幕大小（本机 960×540 屏上 1180×900 变 962×531），截图随环境变且浮层被夹成"几乎铺满中央区"。

**测试：** `tests/test_workbench_preview.py` 16 项（新增自适应尺寸、手动尺寸优先、点画布关闭 + 过滤器随开关挂摘）、`tests/test_workbench_panel.py` 35 项（新增换任务收起、参数变计数刷新）；`scripts/verify.py` 七步全绿。

**遗留：** 浮层位置每次仍锚画布左上角（只记住"用户调过尺寸"这一状态）；设计文档 §17 第 9 条的同批待改项 ①③④ 仍开着。

**涉及文件：** `ui/workbench_preview.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`ui/glossary_agent_panel.py`、`ui/configpanel.py`、`config/stylesheet.css`、`scripts/workbench_render.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_preview.py`、`tests/test_workbench_panel.py`、`docs/技术实现/AI辅助功能_设计与实现.md`

---

### 三处实测缺陷修复（批量写版本崩溃 / 误识别清理列表空白 / 单行浮层弹不出）

**问题/需求：** 用户实机跑「简单背景修复」报错、误识别清理「显示有 15 个处理项但点进去看不到任何内容」、审批浮层在只有一行候选时一旦关掉就再也呼不出来。

**改动要点：**

- **批量写版本崩在拼带**（`utils/batch_versions.py::_capture_pixels`）：一页的多个矩形裁片直接 `np.concatenate(axis=0)`，**要求各裁片同宽**；简单背景修复一页有多条不同宽的纯色带，必抛 "all the input array dimensions ... must match exactly"（实测 228 vs 130），`begin()` 吞异常返回 `None` ⇒ 整批在写版本这步中止。新增 `_stack_crops`：按最大宽度**左侧对齐补零**（还原侧逐条取 `strip[y:y+h, :w]`，多出来的右边距是死区、不会写回图像，故还原侧代码不动、旧版本仍可读）。
- **误识别清理列表长期空白**（`ui/glossary_agent_panel.py::GlossaryAgentPanel.refresh_project_state`）：面板不可见时（打开项目时工作台通常还没开）跳过规划，却已经无条件把首个任务从 `_dirty_tasks` 摘掉 ⇒ 之后 `showEvent` 也不再补规划，列表永远空、而导航计数照旧显示 15 条。改为**标脏位只由 `_ensure_current_planned` 清**（它才真正跑规划）。
- **单行浮层关掉后弹不出来**（`ui/workbench_batch_view.py`）：只有一行候选时，关掉浮层后再点那行，表格的选中行没变 ⇒ 不发 `itemSelectionChanged`，浮层再也不会被呼出（多行时点别行碰巧能弹，故表现为"时好时坏"）。补 `cellClicked` 补取预览，并让浮层的 `closed` 信号经面板转达成 `forget_preview`（视图据此知道"预览已收起"、下次点击该重新取图）。

**测试：** 三条回归用例，且都实测过"旧实现下必失败"——`tests/test_batch_versions.py` 不等宽矩形写版本并逐条还原、`tests/test_batch_simple_inpaint.py` 一页两条不等宽简单块端到端（旧实现下 `report["started"]` 为假）、`tests/test_workbench_panel.py` 关掉后再点同一行可重开 + 面板后开时首任务仍会被规划。相关 10 个测试模块 214 项全过。

**涉及文件：** `utils/batch_versions.py`、`ui/glossary_agent_panel.py`、`ui/workbench_batch_view.py`、`tests/test_batch_versions.py`、`tests/test_batch_simple_inpaint.py`、`tests/test_workbench_panel.py`

---

### D5：单块 Alt + 拖手柄 = 以中心缩放（PS 式即时修饰键）

**问题/需求：** 泛用工作台 D5 要求给单块加「按住 Alt 拖手柄」的缩放：与默认的"拖哪条边/角、对侧钉住"不同，Alt 下中心不动、两侧对称扩张。这条此前从未实现（仓里与 Alt 有关的只有画布的笔刷尺寸缩放）。用户实测两轮后把口径钉成 PS 那种**换算**而非"重定基"：「在拖拽时按 alt 会将目前的拖拽进度转换为按住 alt 下的变换程度」。

**改动要点：**

- 落点 `ui/texteditshapecontrol.py`：`ControlBlockItem` 按下手柄时读 `AltModifier` → `beginResize(..., center_anchor=...)`；移动事件**每帧重读**修饰键 → `TextBlkShapeControl.setResizeCenterAnchor(bool)`。
- **口径＝换算，不是重定基**：参考点与"场景↔本地"坐标映射**全部取自起手那一刻**（默认＝起手对角手柄位置、中心模式＝起手框中心，由 `_pinResizeAnchor` 统一落定），切换时用**上一次的光标位置把当前这一帧重算一遍** ⇒ 被拖的手柄始终钉在光标上、对侧当场镜像出去／收回来，"中途切换的结果 ≡ 一开始就处于该状态拖到同一位置"，来回按 Alt 不累积误差。中心模式的几何在 `resizeFromScene` 里按 `2*center - 被拖侧` 生成，并把"当前中心"回钉到初始中心。
- **鼠标不动时也要生效** ⇒ 新增 `_ResizeModifierWatcher`（挂在 `QApplication` 上的键盘旁听器，`return False` 只旁听不拦截、不抢焦点以免顶掉画布 Alt+WASD 切块；`TextBlkShapeControl` 是 `QGraphicsRectItem` 不是 QObject，不能自己当过滤器）；一次手势装一次、`finishResize` 卸掉。过滤器里 `RuntimeError` 必须吞——**PyQt6 从事件过滤器逃出的异常会走 `qFatal` 直接终止进程**。
- **两个坑**：① Qt 的 `event.modifiers()` 返回的是**事件之前**的状态——`KeyPress(Key_Alt)` 里读不到 `AltModifier`、`KeyRelease` 里反而读得到，所以一律按 `key()/type()` 判断；② `_beginProxyDrag` 的跟手锚点**必须仍是手柄位置**，把它改成中心会导致位移从中心量起（第一版踩过，已回退）。
- 撤销沿用 `ReshapeItemCommand`，无新增命令。

**测试：** `tests/test_text_transform_ui.py` 新增 6 条（Alt 拖角中心不动且对角镜像／同手势两种模式结果不同／中途按下与松开 Alt 的换算与收回／鼠标不动的键盘路径／切换本身不动几何／撤销往返）；PyQt6 不能构造 `QGraphicsSceneMouseEvent`，桩事件 `_StubDragEvent` 提为模块级、按下与移动共用。真机探针 `tmp/_alt_probe3.py` 9 组情形（旋转 0／30°／−45°／120°、角手柄与边手柄、中性块与 projective 形变块、两个切换方向）⇒ 跟手误差、中心漂移、对角漂移**恒为 0**。**手感仍待用户实机验收。**

**遗留：** 手动版不套"碰到邻框即停"（那是批量扩张 D5 的引擎侧行为）。

**涉及文件：** `ui/texteditshapecontrol.py`、`tests/test_text_transform_ui.py`

---

### 工作台两个参数设置项 + 「工作台（临时）」设置页

**问题/需求：** 复核时点出两处"定了但没做"：D33d 要求的「设置内参数接口」只有 `ui/batch_merge.py::MergeConfig` 里的硬编码默认值；C3 的批量扩张量则明确"不给默认值"导致界面上每次都要手填。用户拍板：两项都做成设置项，**先放一个临时页**、排版方案定了再并入既有页；C3 默认量定 **10px**。

**改动要点：**

- `utils/config.py::ProgramConfig` 新增 `workbench_merge_oversize_ratio`（0.85）与 `workbench_expand_px`（10），字段注释里写明取值依据（0.85 在样本 435 组里只命中 2 组且 0.5~0.9 之间不敏感；10px 时 88% 的框四边可完整扩张、宽 +28%／高 +17%）。
- 设置面板新增「工作台（临时）」页（`ui/configpanel.py`，导航 key `workbench_temp`，归在 General 下；页数 9 → 10）：误聚阈值（百分比）与默认扩张量（px）两个数值框，初值取自 pcfg、改动即回写。
- **注入在任务层而非引擎**：`ui/workbench_tasks.py` 的 `MergeTask._engine` 传 `MergeConfig(oversize_ratio=...)`、`ExpandTask.options_spec` 的初值取 `pcfg.workbench_expand_px`；引擎继续保持"参数由调用方给"（`ExpandTask.plan` 在扩张量为 0 时仍返回空列表、执行按钮禁用），**裸脚本复算不受设置影响**。

**测试：** `tests/test_settings_app_page.py` 页数断言 9 → 10 并新增"两个数值项初值取自 pcfg / 改动回写 pcfg"；`tests/test_workbench_panel.py` 两条按新语义重写。

**遗留：** 临时页的归并——用户拍板**先不动**，不作为待办再问。

**涉及文件：** `utils/config.py`、`ui/configpanel.py`、`ui/workbench_tasks.py`、`tests/test_settings_app_page.py`、`tests/test_workbench_panel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 工作台参数复算台与真机探针常驻（`scripts/`）

**问题/需求：** C1／C2／C3／C4／D39 这些参数是"测出来的"，此前复算脚本全在 `tmp/`，一清理就失去复算能力（`ui/batch_merge.py` 里那句"实测脚本随 tmp 清理已不存在"就是欠的账）。

**改动要点：**

- 新增 `scripts/workbench_recalc.py`：六个**只读**子命令 `merge`（`--sweep` 加阈值扫描）／`c1`／`expand`／`queue`／`review`／`hook`／`list`，支持 `--project` 指向工作副本，脚本末尾**自证样本顶层文件 mtime 未变**（只读自证）。
- 新增 `scripts/probes/`（11 个真机探针 + 自带 `README.md`）：内存归因与释放阶梯、区域再检测真机验收等——有真机/模型依赖，写明各自的前提与期望数字。
- 登记到位：`scripts/README.md` 与 `AGENTS.md` 的 `scripts/` 行同步；`scripts/check_docs.py` 只扫 `scripts/` 顶层，子目录不强制登记（其说明由子目录自带）。

**涉及文件：** `scripts/workbench_recalc.py`（新）、`scripts/probes/`（新）、`scripts/README.md`、`AGENTS.md`

---

### AI 辅助功能文档：三合一 + 删 6 份过期文档 + 全仓引用改指

**问题/需求：** 泛用工作台的 A／B／C1～C4／D 全部落地后，围绕它的文档散成四份（规划／复核与拆分／术语剧情工作台_交接／AI辅助功能_规划）且互相交叠，读者要拼着看；另有 2 份上游移植期的过程材料已过期。

**改动要点：**

- **合并为一份**：`docs/技术实现/AI辅助功能_设计与实现.md`（400 行）＝总纲 → 体系总览 → Part I 标签体系与框级动作 → Part II 工作台（术语／剧情／四个批量任务＋写回契约＋**决策一览 D1–D41**）→ Part III 调参与待办。**保留 D 编号体系**——代码注释里约 200 处按编号与「设计 §X」引用，节号与编号不要乱动。用户拍板**翻译 agent 的架构基线仍独立**在 `docs/技术实现/翻译agent化_设计方案.md`，不并进来。
- **删除 6 份并登记** `scripts/audit_registry.json`（现 55 条）：`AI辅助功能_规划`、`术语剧情工作台_交接`、`泛用工作台_规划`、`泛用工作台_复核与拆分`、`上游v1.5.12移植_完成记录`、`不常用功能工具箱_规划`。最后两份用户判定直接删（不迁移）；`上游v1.5.12移植_完成记录` 的「与上游的持久分歧」节改以 `ui/text_engine/` 各模块注释为准。
- **引用改指**：除带文件名的引用（`check_audit` 会报漏）外，代码注释里还散着 30 处**不带文件名**的「规划 §X」节号——检查器不管但语义已失效。做法＝写脚本集中做「旧节号 → 新节号（`设计 §X`）」映射替换、**每对带期望命中数回显**，一次跑完再逐条核对（30 处里 1 处因同文件重复文本漏掉，补手工修），落到 15 个文件；另改 `.agents/skills/audit-docs/SKILL.md` 里指向已删文档的两处措辞。
- 顺带修 `docs/技术实现/区域再检测_设计与实现.md` 一处指向 `tmp/` 生成物的失效引用（`verify.py` 的 docs 步由此转绿）。
- 踩坑：**`check_audit` 的语料包含 `.agents/`**（SKIP_DIRS 里没有它）⇒ 技能文档里提到已删文件名同样会被判残留引用。

**测试：** `check_docs` 22 篇全绿（原 27）／`check_audit` 55 条已删通过／`check_syntax` 通过／受影响测试 10 个文件全绿。

**涉及文件：** `docs/技术实现/AI辅助功能_设计与实现.md`（新）与 6 份删除、`docs/项目概述.md`、`docs/基础速查/AI辅助标签体系使用说明.md`、`docs/技术实现/区域再检测_设计与实现.md`、`AGENTS.md`、`scripts/audit_registry.json`，以及 15 个代码／测试文件里的注释引用

---

### 页范围数值框：修「单跑过、连跑必红」的用例间残留

**问题/需求：** 全量 pytest 里唯一的红是 `tests/test_page_range_progress.py::PageRangeSpinBoxTest::test_clicks_elsewhere_reach_the_native_editor`（`lineEdit().hasFocus()` 为假），单跑 PASS、与同文件前一个用例连跑 3/3 必失败。定位＝**用例间残留**：offscreen 平台上 `deleteLater` 不会立刻销毁前一个 top-level 窗口，它仍占着激活态 ⇒ 本用例新 `show()` 的窗口拿不到激活，Qt 就不把焦点交给它的 `QLineEdit`。控件本身经探针证明正常。

**改动要点：** `_spin()` 里补 `spin.activateWindow()`；清理从裸 `deleteLater` 换成 `_close_spin`（`close()` + `deleteLater()` + `processEvents()`）。

**测试：** 判据＝**先用 `git show HEAD:<path>` 导出改前版本复现出同一条失败**（1 failed / 9 passed），再跑新版本连跑 3 次全绿；全量 pytest 1185 passed / 1 skipped / 0 failed。

**涉及文件：** `tests/test_page_range_progress.py`

---

### 软键盘不再随启动自动启用

**问题/需求：** 软键盘（窄栏图标）是"功能开关"型入口，勾选态就是 `pcfg.symbol_keyboard_enabled`。它此前与其他窄栏浮层不同——**每次启动都会按上次会话的值自动启用**（其余浮层有"启动不自动展开"的清账名单），用户要求把软键盘也纳入名单。

**改动要点：** `ui/mainwindow.py::MainWindow` 的启动清账 `for flag in (...)` 名单加入 `symbol_keyboard_enabled`。该字段在 `install_symbol_launcher`（构造 SceneTextManager 时，**早于清账段**）就已写进图标勾选态，所以除清字段外还要把图标复位，否则会出现「图标亮着、开关是关的」；复位走 `toggled`，槽里对此时尚为 `None` 的 `symbol_dock` 是空操作。清账只作用于启动，手点图标仍能正常启用。

**测试：** 真机探针 `tmp/_s18_softkb_startup.py`（**必须窗口模式**，offscreen 起不来 `FramelessWindow`；`config/config.json` 先备份后还原）：模拟上次会话开着键盘构造主窗口 ⇒ 开关与图标都归零、dock 未创建；随后手点开／关仍正确。8 项断言全 PASS。

**涉及文件：** `ui/mainwindow.py`

---

### 工作台 UI 优化（一）：导航两级化 + 底部状态条 + 目视验收渲染台

**问题/需求：** 用户启动工作台 UI 优化流程，点出三处：① 顶部六个任务钮平铺，对不了解工作台的用户没有分类线索——应当先给「OCR／图像修复／翻译」这种管线大类，大类下再放细分任务；② 底部「草稿已载入：术语 0 条…」明明只读却画成输入框（圆角描边），意义不明；③ 我方渲染脚本出的验收图全是方块（编码/字体问题），需要修好以便逐项判断。

**改动要点：**

- **导航两级化（D42）**：`ui/glossary_agent_panel.py::WorkbenchTaskNav` 由「六个互斥任务钮的 2×3 网格」改为「一级大类页签 + 二级任务 chip」。大类用**管线阶段名**（文字与 OCR／图像修复／翻译），归属：文字与 OCR＝误识别清理／合并相邻框／框扩张，图像修复＝背景修复，翻译＝术语表／剧情。`WORKBENCH_ORDER` 改为由 `WORKBENCH_CATEGORIES` 摊平 ⇒ 前四项仍是 `CLEANUP_TASK_IDS`，跳步提示的下标比较与 `earlier_pending` 口径不变（既有用例未改一行）。切大类落到**该大类上次用过的任务**（不是每次重置成第一个）。计数两级都缀：chip 缀自己的，一级页签缀本大类之和（切到别的大类也看得见还有活）。大类标签的翻译上下文用 `WorkbenchTaskNav` 而非 `GlossaryAgentPanel`——后者里 `"Translation"` 已被术语表列头占为「译文」，同 context 同 source 只能有一个译文（实测页签一度真的显示成「译文」）。
- **底部状态条（D43）**：日志区改 `objectName="WorkbenchStatusBar"` 的容器承载，`QTextEdit#WorkbenchLogView` 规则（ID 选择器优先于类名选择器）抹掉 `ConfigTextEdit` 的输入框外观（平底色、去圆角、无边框、`NoFrame`），靠顶边线与候选列表分开；「撤销上次批量」移到右端、对齐顶部。顺带修两处日志噪声：日志跨任务共用，故批量任务的行**前缀自己的任务名**（`_append_log(text, task_id)`，`_toast` 同）；`ui/workbench_batch_view.py::BatchTaskView.replan` 在空列表时**不再把页面摘要抄进日志**（它就是页面上那行，抄进全局日志后会出现在别的任务页上）。
- **容器底色原先空转**：`QWidget#WorkbenchSurface`／`WorkbenchTaskNav` 的 QSS 底色一直在，但纯 `QWidget` 不上屏 QSS background ⇒ 规则静默失效。按仓库既有做法（`ui/tag_toolbar.py`）统一走新增的 `_styled()` 开 `WA_StyledBackground`。
- **目视验收渲染台**：`tmp/wb_render.py`（未纳入版本控制）正式化为 `scripts/workbench_render.py`，修两个致命问题——**不许设 `QT_QPA_PLATFORM=offscreen`**（离屏平台 `QFontDatabase.families()` 实测为 0，任何文字都是豆腐块，且 `setFont` 救不回来）、**要按 `launch.py` 装字体＋语言＋主题**（原先截图是白底裸控件，观感毫无参考价值）。默认 2 倍率，stdout 回显两级导航结构（页签文字／chip 文字／可见 chip／计数），不看图也能核对。

**测试：** `tests/test_workbench_panel.py` 新增 4 条（两级结构与大类切换／页签计数＝本大类之和／切回大类落回上次任务／任务→页下标一一对应）＋ 1 条（日志行前缀任务名、worker 行不缀）。`test_workbench_panel.py` 31 项、`test_glossary_agent_panel.py` 12 项全绿。渲染台实跑：六页 + 空态页均正常出图，中文正常显示。

**遗留（已登记进设计文档 §17 第 9 项）：** 空态下日志只有「草稿已载入」与当前任务无关；图像修复页无候选时仍有「跳到画布」；执行钮在无候选时用带计数的文字而非禁用＋原因；参数改动走 180ms 防抖、观感像"没反应"；预览区固定高度留白。逐条与用户确认后再改。

**涉及文件：** `ui/glossary_agent_panel.py`、`ui/workbench_batch_view.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/workbench_render.py`（新）、`scripts/README.md`、`tests/test_workbench_panel.py`、`AGENTS.md`、`docs/技术实现/AI辅助功能_设计与实现.md`

> **过程说明：** 本轮中途工作区被一次 `git reset --hard origin/main` 清过（HEAD 由 `5ecfad32` 前进到 `518398ba`），当时未提交的 `ui/`、`config/`、`translate/` 改动随之丢失（未纳入版本控制的渲染脚本与测试文件存活），上述改动按同一口径逐条重做，并在新基线上复核。工作区里同时有别的在途改动时，动 `reset --hard`／`checkout` 前请先看 `git status`（AGENTS.md「多代理协作」）。

---

### 工作台 UI 优化（二）：审批预览改浮层 + 简单背景判据补两条

**问题/需求：** ① 预览框尺寸过小且固定（120~280px 高），用户实测 433×395px 的审批图就被截；要求支持滚轮缩放与拖拽平移，并倾向**放到工作台外面**做成浮层（不占工作台宽度与纵向空间），先做一版看过再定方向。② 背景修复里很多"明明是简单背景（甚至 100% 纯色气泡）却判不出"，需要优化检查逻辑。

**改动要点：**

- **预览浮层（D44）**：新增 `ui/workbench_preview.py::WorkbenchPreviewPanel`——in-window child of `MainWindow.centralStackWidget`（与 `ui/custom_widget/rail_dock_panel.py::RailDockPanel` 同款做法，跟着窗口走、盖在画布页上），拖标题条移动、四边四角拉伸、Esc/× 关闭；内容区滚轮**以光标为锚点**缩放（0.1x~8x）、左键拖拽平移（内容小于视口时自动居中，拖不出边界）、双击适应窗口，标题条给「适应窗口／100%」与当前倍率。`ui/workbench_batch_view.py::BatchTaskView` 删掉页内预览区（`_preview_box`／`_preview_area`／`_preview_image` 一并撤掉），改为新增 `preview_requested` 信号把 **100% 原比例**的图与标题交给浮层；面板懒建浮层并接线，换项目时收掉。**D23 的"100% 原比例"口径不变**：打开与换图都回 100%，不自动缩到适应窗口，缩放只能由用户发起。
- **简单背景判据补两条（D45，阈值不动）**：先诊断清楚「判不出」是**几何/遮罩**判定失败而非颜色判据失败——判据靠"最外那条 1px 兜底外框"围出一个包住全部遮罩像素的闭合轮廓，于是 ① 遮罩贴到裁剪边界时 `AND (255 - mask)` 会把外框那一截擦掉，一个闭合轮廓都不剩；② 裁剪窗口是块的 1.7 倍外扩，**邻块遮罩落进来**会把遮罩包围盒撑成跨块并集，没有任何轮廓包得住它。改法：`utils/textblock_mask.py::extract_ballon_mask` 分析前**外补 3px**（图像 replicate、遮罩补 0），外框永远擦不掉；新增 `modules/inpaint/base.py::block_local_mask`（按连通域只留与本块矩形相交的遮罩，筛不出时原样返回、不把情况改坏），由 `modules/inpaint/base.py::classify_simple` 的新参数 `blk_rect` 与两条调用路径（`InpainterBase.inpaint` 逐块分支、`ui/batch_inpaint.py` 的扫描）一起传入本块矩形。
- **量化（合成失败场景 A/B，"pad0＋无矩形"＝旧行为 对照 "pad3＋矩形"）**：「判不出」**4/4 → 0/4**；其中"紧邻框 + 纯色气泡"由判不出变**简单**，"遮罩压裁剪边界"由判不出变可判；两张反向对照（同样场景但气泡是渐变）**改前改后都判复杂**，没有被放过成简单。注意合成场景的遮罩必须画成**笔画**——盖满整块会把渐变背景一起盖掉，渐变块也会判成简单（第一版探针踩过）。
- **文档口径**：「审批预览在列表下方展开、只滚动不缩放」这条随 D44 改写为「默认 100%、不自动适应窗口，缩放由用户显式发起」。

**测试：** 新增 `tests/test_workbench_preview.py`（14 项：默认 100%／适应窗口只缩不放／小图不放大／滚轮以光标为锚点／缩放上下限／拖拽平移与边界钉住／小图居中／双击适应／无图显示原因／标题条拖动与宿主内夹取／角手柄拉伸与地板尺寸／左缘只动左缘／Esc 关闭／关闭后重选重开仍回 100%）。`tests/test_workbench_panel.py` 的预览用例改为断言「交给浮层的图不缩放 + 浮层默认 100%」；`tests/test_batch_simple_inpaint.py` 新增 7 项（块局部遮罩两条单元 + 邻块与渐变对照 + 遮罩贴边 + 补边不改干净区结论 + 端到端 plan 计数），并修正一条旧用例——它用 `img[:50, :50]` 却声称"裁剪区内没有掩码"（实际有、只是贴在裁剪边界上），补边后该裁剪区能正确判出「纯色气泡＝简单」，用例改用真的没有掩码的 `[:30, :30]`。`scripts/verify.py --smoke` 全绿。

**遗留：** 气泡轮廓被裁剪区切断的块（如贴页边且框比气泡小的），轮廓围不出来时仍走兜底区域判成**复杂**（保守跳过，不会误涂）——要进一步吃下这批，方向是「兜底区域由整窗改为遮罩包围盒 + 小外扩」或镜像补边，属判据语义变更，等用户定。真工程命中率请跑 `scripts/workbench_recalc.py c1 --project <目录>` 与改前基线（401 简单／131 复杂／250 判不出）对比。

**涉及文件：** `ui/workbench_preview.py`（新）、`ui/workbench_batch_view.py`、`ui/glossary_agent_panel.py`、`modules/inpaint/base.py`、`utils/textblock_mask.py`、`ui/batch_inpaint.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/workbench_render.py`、`tests/test_workbench_preview.py`（新）、`tests/test_workbench_panel.py`、`tests/test_batch_simple_inpaint.py`、`AGENTS.md`、`docs/技术实现/AI辅助功能_设计与实现.md`

---

### 默认文字检测器改为 ysgyolo（`label.other` 同步默认关闭）

**问题/需求：** 用户确认把 ysgyolo 作为默认文字检测器（它刻意忽略难以识别的拟声词、只认清晰的气泡文本，与本项目主流工作流一致），并把当前配置里的参数作为代码默认；其中 `label.other` 保持关闭。

**改动要点：**

- `utils/config.py::ModuleConfig` 的 `textdetector` 默认值由 `ctd` 改为 `ysgyolo`（`ctd` 仍在注册表里，用户在设置页／运行对话框照旧可选）。
- `modules/textdetector/detector_ysg.py::YSGYoloDetector` 的 `label.other` 默认由 `True` 改为 `False`，并在该处写明理由：`other` 拉的是覆盖整个气泡的**气泡级框**而不只是多一层遮罩——检出框一律进 `utils/textblock.py::mit_merge_textlines` 的合并池，于是多出若干「整只气泡」的 `TextBlock` 会被 OCR／翻译／渲染（实测同一页块数 9→15、页面遮罩覆盖 5.09%→15.03%）；对修复侧则是遮罩把裁剪窗吃光，简单背景判据的「无非文字像素」分支直接判不出（实测 6/15）。要不要利用它的「气泡本身」信息另议。
- 其余参数（model path／confidence 0.3／IoU 0.5／detect size 1024／merge text lines／mask dilate size 2 等）代码默认已与当前配置一致，未动；`device` 保持 `modules/base.py::DEVICE_SELECTOR`（跟随本机可用设备），未钉成 `cpu`。

**测试：** `scripts/verify.py` 七步全绿（含启动冒烟，因同批改动命中 `ui/configpanel.py`）。另静态确认全新 `utils/config.py::ModuleConfig()` 取到 `ysgyolo`、`YSGYoloDetector()` 默认有效标签为五项（不含 `other`）；`tests/` 与 `docs/` 中无「默认检测器＝ctd」的断言或表述需要同步。**注意**：ysgyolo 需 `ultralytics` 与 `data/models/ysgyolo_yolo26_2.0.pt`（模型文件被 gitignore），两者缺失时 `launch.py` 按既有兜底静默降级为 `none` 检测器——与原先默认 `ctd`（模型同样 gitignore）情形一致。

**涉及文件：** `utils/config.py`、`modules/textdetector/detector_ysg.py`

**遗留（用户已确认后做）：** 在检测器参数区加一段模型行为特性备注，方便用户选择，本轮不做。

---

## 2026-09-17

### 区域再检测（人工拉框 → 只在框内跑「检测 + OCR」）

**问题/需求：** 实际工作流是 ysgyolo 检测 + paddleocr_v6 识别。ysgyolo 干净，但会成片漏检**横排文本**（实测漏检率横排 20.5% vs 竖排 7.7%，全书约 47 个框），而全量双跑复核没有油水（两模型一致率 88%~92%）。做法：人工在画布上拉一个大概的矩形 → 只在该矩形内跑一次「检测 + OCR」→ 检出的新块并入本页数据。等于用第二个检测器的精细度补第一检测器的漏检，同时把它"到处拉框"的敏感性限制在人手指定的区域里。机制是小裁剪按原尺寸送检、等效比全页大 1.5 倍（ppocrv6 的 `limit_type='max'` 只缩不放）；主动再放大 2×/3× 实测零增益，故**不提供放大参数**。

**改动要点：**

- 新增 `ui/region_redetect.py`（任务层，无 Qt widget）：`plan` 只读 / `build_page` 纯函数 / `apply` 数据层写回。七项已拍板设计落成代码——区域外过滤按**四边形中心点**、重叠 **>50%** 的已有块替换（分母取两者较小者，才抓得住"ysgyolo 区域级大框被 ppocrv6 逐行小框压住"）、裁剪掩码**贴回页级**（否则后续修复不清原文，译文会叠在原文上）、新块样式继承同页最近块、阅读顺序按坐标插入且**三重兜底为追加到末尾**（宁可顺序不理想也不插错位置）。几何判据一律按 `TextBlock.lines` 四边形算，**不用 `xyxy`**（后者只是轴对齐外接矩形，倾斜框会误判邻居——DB 四边形实测 11.9°~16.7°）。
- 新增 `ui/region_redetect_tool.py`（UI 层）：`RedetectCommand` 把"替换 + 新增 + OCR 文本 + 标签"合成**一步撤销**；检测（要建 ONNX 会话）与 OCR 都放后台线程；控制器接画布手势、给通知中心进度提示（决策 8：不预热，但"正在加载哪个模型"要说清楚）。OCR **不用** `ui/module_manager.py` 的 `_blktrans_pipeline`——那条路径收尾会压一条 `RunBlkTransCommand`，一次拉框就产生两个撤销步；改为与它同口径直接走 OCR 模块的 `run_ocr`（自动挂标随之保留）。
- 掩码是**栈外图像写入**（`save_mask` + `bump_page_image_generation`，与管线修复阶段同构），撤销不回退；页代数**不** bump（栈内写入，与 `DeleteBlkItemsCommand` 同性质，bump 会把本命令自己的撤销一并僵尸化）。
- 触发入口＝**底部栏新增独立开关**（`ui/mainwindowbars.py` 的 `RedetectChecker` + `icons/bottombar_redetect.svg`／`_activate` + QSS 两条），复用手势、只换落点：开关打开时画布**左键**拉框改发 `ui/canvas.py::Canvas.region_redetect_rect`（右键保持原语义，不被模式顶掉）。**不放 `ui/image_edit.py::ImageEditMode`**——`Canvas.setPaintMode(False)` 会把它重置为 `NONE`（进文本框编辑页必然被清掉），且绘图模式下文本框层是隐藏的、检出的框看不见。
- 设置项「再检测用的检测器」落在 Interface 页 Canvas 区（`ui/configpanel.py`），值存 `utils/config.py::ProgramConfig.region_redetect_detector`（默认 `ppocrv6_onnx`），与底部栏的管线选择器解耦；换检测器时旧实例显式卸载重建。
- `utils/block_geometry.py` 增补四边形判据（`poly_of`／`poly_center`／`poly_bands`／`poly_overlap_ratio`），与既有的"外扩碰到邻框即停"共用同一份实现。

**测试：** 新增 `tests/test_region_redetect.py`（52 项：坐标回映射／区域外过滤／四种跳过码／替换判据／倾斜框按真实四边形／掩码并集与落盘／样式继承与字号量算／插入算法各分支与三重兜底／命令 redo-undo 双端点复原／落盘重开后新块仍在／画布手势落点分流／设备与释放策略／控制器接线与拒收路径）；`tests/test_block_geometry.py` 补 9 项四边形判据（含"倾斜框按外接矩形会误判"）。真机只读验收（可写副本 `D:\汉化\施工区副本`，脚本 `tmp/_rr_accept.py`）：063.jpg (350,265)-(690,435) 检出 **5 块、全部倾斜**（angle −11~−12，OCR 出 5 行日文）、047.jpeg 底部 **3 块**／左中 **2 块**，与方案实测一致；同一区域 ysgyolo 检出 **0 块**（`tmp/_rr_probe.py`）。`scripts/verify.py --full` 全绿（1112 项）；**用户已 GUI 验收通过**。

**涉及文件：** `ui/region_redetect.py`、`ui/region_redetect_tool.py`、`ui/canvas.py`、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`ui/configpanel.py`、`utils/block_geometry.py`、`utils/config.py`、`config/stylesheet.css`、`icons/bottombar_redetect.svg`、`icons/bottombar_redetect_activate.svg`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_region_redetect.py`、`tests/test_block_geometry.py`、`docs/技术实现/区域再检测_设计与实现.md`、`docs/基础速查/i18n.md`（补 4 条踩坑问答：隐式拼接的 tr 提取不到、硬编码中文检查的两处豁免边界、手写 ts 条目三条约束）、`AGENTS.md`、`docs/daily_log.md`

---

### 区域再检测：内存归因（CUDA 会话 +835MB）与默认改 CPU

**问题/需求：** 用户验收时发现一次区域再检测后进程内存从 600MB 涨到 1700MB，且设置里的「清除模型缓存」无效。

**改动要点（先测量后动手，脚本 `tmp/_mem_probe.py`／`_mem_probe2.py`／`_mem_probe3b.py`／`_mem_after_fix.py`）：**

- **归因**：元凶是给这条路径**新建的 ONNX Runtime CUDA 会话**——同一个小裁剪，`CUDAExecutionProvider` 一次 `detect` 让主机工作集 **+835MB**（会话建立 +165、首次推理再 +670），而 `unload_model()` **只回收 50MB**（GPU 侧倒是能还）。cudnn 搜索模式（EXHAUSTIVE→DEFAULT/HEURISTIC）、`enable_cpu_mem_arena`、`arena_extend_strategy` 三个开关都试过，只把峰值从 835 压到 730、量级不变 ⇒ **事后清缓存救不回来**（设置里的「清除模型缓存」只卸管线模块，何况 ORT 分配器已长在进程里）。
- **改法**：`RedetectConfig.device`／新增设置项 `utils/config.py::ProgramConfig` 的 `region_redetect_device` **默认 `cpu`**（CPU 同裁剪只 +89~134MB 且卸载可回收，单次推理 19~93ms vs CUDA 7~21ms——一次手势只差约 20ms，会话重建仅 0.17s）；控制器在**每次手势收尾**调 `unload_detector`（用完即卸，不留常驻）。设置面板新增「Region Re-detect Device」行（CPU／GPU）。
- **复测**：峰值 **+148MB**（原 +835）、卸完相对基线 **+47MB**、单次手势 0.21~0.32s。链路里剩下的内存波动来自 **OCR 模块**（管线共用实例，首次 `run_ocr` 才懒加载其 CUDA 会话）——那是 app 既有行为，用户已跑过管线时不新增。
- 设计与全部实测数字落进 `docs/技术实现/区域再检测_设计与实现.md`（原方案草稿在 `tmp/`，会随临时目录清理）。新增 `tests/test_region_redetect.py` 的 4 项设备/释放策略用例（设备按配置覆盖且换值重建、用完即卸三条路径）。

**涉及文件：** `ui/region_redetect.py`、`ui/region_redetect_tool.py`、`ui/configpanel.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_region_redetect.py`、`docs/技术实现/区域再检测_设计与实现.md`、`AGENTS.md`、`docs/daily_log.md`

---

### 手动释放内存（卸载模型 → 销毁 CUDA 上下文 → 交回工作集）

**问题/需求：** 上一节的内存归因结论是「ORT 的 CUDA 会话长在进程里，卸载只还 50MB、**事后救不回来**」。用户问：如果能接受卸载的等待，能不能做成「跑完管线后用户按需手动卸载内存」？本轮先测再答，**推翻「事后救不回来」的后半句**，并把它做成设置页的一个手动按钮。

**改动要点：**

- **实测阶梯**（脚本 `tmp/_rr_release_probe.py`／`_rr_modules_probe.py`／`_rr_after_trim_probe.py`／`_rr_torch_after_reset.py`，读数用 `GetProcessMemoryInfo` 并逐条落盘防崩丢数据）：590MB（基线）→ **1503**（检测+OCR 各跑一次 CUDA）→ 1503（卸载全部模型：**几乎不掉**）→ 1503（`empty_cache`：不掉）→ **1295**（`cudaDeviceReset`：**−208MB，真还**，GPU 侧完全归还）→ **111**（`EmptyWorkingSet`：交回工作集）→ 之后再用一次 CUDA 只回升到 **420**（首次 0.33s、第二次 0.01s）。**两处更正**：① 不是"拉起了一个 CUDA 进程"——上下文与 ORT 会话都在本进程内，同时把驱动侧 DLL 加载了进来（模块工作集合计 350 → 407MB，`cublasLt64_13.dll` 单项 144MB），叠加私有内存才到 1500MB；② 不止"关进程"一条路。
- **语义必须分清**：`cudaDeviceReset` 是**真释放**；`EmptyWorkingSet` 是**交回**——页退回系统备用内存（DLL 的干净文件映射页系统可立即丢弃给别的程序，私有脏页进 pagefile），任务管理器数字立刻回落、别的程序能拿到，但页表映射还在、**下次访问要 fault in**。对外文案一律按这个口径写，不写成"释放内存"。
- **三条安全铁律（都有实测依据）**：① `cudaDeviceReset` 前**必须** `torch.cuda.empty_cache()` ——不先清就 reset，torch 进入损坏状态（`matmul` 报 `CUBLAS_STATUS_INTERNAL_ERROR`，随后任何分配报 `an illegal memory access was encountered`）；先清再 reset，则 torch 与 ORT 重建后都正常。② reset 时不能有 CUDA 活在跑（管线、四个"切换模块"线程、区域再检测的后台线程——它的检测器可配成 CUDA）。③ 卸载失败就不 reset（可能有活跃会话），此时只交回工作集并在提示里说明。
- 新增 `utils/memory_release.py`：三步编排（读数函数与卸载回调都可注入，单测用替身、不碰真 CUDA）+ 报告对象（记录各步读数与成败，供界面拼提示）。
- 入口＝设置页 Models → Management 新增「释放内存」按钮（`ui/configpanel.py::ConfigPanel` 发信号），流程在 `ui/mainwindow.py::MainWindow`：先挡"后台有活在跑"，再弹**确认框写清接下来会怎样**（下次跑管线／AI 修图会重新加载模型、重建会话、首次慢几秒；释放后首次交互可能短暂卡顿；释放期间别开跑），完成后用通知中心报读数 `工作集 %1 MB → %2 MB`。**不自动触发**——交回工作集会把界面也要用的页换出去，紧接着的交互会卡一下。`ui/module_manager.py` 的 `unload_all_models` 改为返回"是否卸干净"供此处判断。
- **不做**：子进程隔离（那才是"真释放到进程外"，重建约 2~3s，但要把管线阶段搬出 GUI 进程，收益不抵架构改动）；也不新增第二套清缓存语义（`modules/base.py` 的 `soft_empty_cache` 仍是管线侧日常清理）。

**测试：** 新增 `tests/test_memory_release.py`（14 项：步骤顺序与读数记录、卸载失败不做 reset、单步异常不外抛、`empty_cache` 必须排在 reset 之前、设备忙则放弃、报告取值回退）。`scripts/verify.py --full` 全绿。

**涉及文件：** `utils/memory_release.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/module_manager.py`、`tests/test_memory_release.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/内存释放_设计与实现.md`、`docs/技术实现/区域再检测_设计与实现.md`、`AGENTS.md`、`docs/daily_log.md`

---

### 运行窗口页码区复刻上游 + 拖拽调值的按下区补挂到编辑框

**问题/需求：** ① 之前移植的 Blender 式拖拽调值在 `NoArrowsDoubleSpinBox` 上"看着支持、实际用不了"——只有边框那几像素能拖。② 运行窗口的页数区（`RangeSlider` + 起止框 + All Pages 复选框）交互自相矛盾：勾上「全部页面」就把滑条禁用，三者谁说了算看不出来。

**改动要点：**

- **根因**：数值框的编辑区是一个几乎铺满控件的 `QLineEdit` 子控件，鼠标按下先落到它身上、不会冒泡到 `QAbstractSpinBox.mousePressEvent`，于是按下区被原生文本选区行为接管；`SizeComboBox` 当年是用「lineEdit 事件过滤器」绕过这个坑的，拖拽混入却把这条路写在了子类里，数值框没接上。修法：把 lineEdit 代理收进 `ui/custom_widget/spinbox.py::DragAdjustMixin`（新增 `ui/custom_widget/spinbox.py::DragAdjustMixin._install_drag_edit_proxy`），数值框与可编辑组合框共用同一条三段式入口，`SizeComboBox` 里那份重复实现删除。数值框边框几像素的宿主直拖保留，两条入口行为一致（编辑区拖拽 / 单击全选进编辑态 / 悬停 ↔ 光标）。
- **页码区换成上游控件**：新增 `ui/custom_widget/page_range_progress.py`（复刻上游 `ballontranslator/ui/page_range_progress.py` 三件套：`PageRangeSpinBox` 右端自绘 chevron 步进、`PageProgressRangeBar` 完成度轨 + 可拖闭区间 + 悬停读出页名/页码、`PageRangeProgressWidget` 页码行）。**「全部页面」复选框取消**——满区间就是全部页面；`page_filter()` 在区间覆盖全书时仍返回 `None`（沿用原语义，mainwindow 的"全部页面"路径不变）。完成度由 `ui/mainwindow.py::MainWindow` 开窗时按 `get_page_progress` 算好传入；区间跨窗记忆（与折叠区同款类级状态）。
- **与上游的偏差**：强调色取 `themeColor()`（上游硬编码 `#2E93E5`，fork 换主题要跟着走）；去掉 PyQt5 兼容 shim；`PageRangeSpinBox` 不做拖拽调值（右端已是 chevron 按钮、页码是离散序号）。样式按类名选择器 `PageRangeSpinBox` 落在 `config/stylesheet.css`（`padding-right: 36px` 给 chevron 让位，实测编辑区 9~44px、按钮 45~78px 不重叠）。
- `utils/proj_imgtrans.py` 的 `get_page_progress` 改成缺条目时按未跑完处理：逐页调用是它现在的唯一用法，缺 `_image_info` 条目的页会当场 KeyError、整个运行窗口开不出来。

**测试：** 新增 `tests/test_spinbox_drag.py`（6 项：编辑区按下起拖、拖拽调值且只提交一次、单击进编辑态全选、边框直拖仍在、禁用态拒拖、`SizeComboBox` 同一代理）与 `tests/test_page_range_progress.py`（10 项：chevron 步进/框内其他位置落回原生编辑、区间夹取与去重发信号、就近手柄、两端重合按拖动方向拆开、完成度补齐截断、页码框与轨道双向同步、无页时禁用）；`tests/test_run_pipeline_dialog.py` 页码区用例改写（满区间 → `None`、区间切片、区间记忆跨窗、完成度喂给轨道共 5 项，注意还原类级 `_page_range`）。`scripts/style_showcase.py` 新增三行展示（`check_showcase.py` 强制登记）。真机（窗口模式、eva-dark）截图确认版面：`debug/real_dialog.png`／`debug/real_range_frame.png`；实测页码框编辑区 9~44px、chevron 45~78px 不重叠，悬停页名与页码同轴居中。`scripts/verify.py` 全绿。

**涉及文件：** `ui/custom_widget/page_range_progress.py`、`ui/custom_widget/spinbox.py`、`ui/custom_widget/combobox.py`、`ui/custom_widget/__init__.py`、`ui/run_pipeline_dialog.py`、`ui/mainwindow.py`、`utils/proj_imgtrans.py`、`config/stylesheet.css`、`scripts/style_showcase.py`、`tests/test_spinbox_drag.py`、`tests/test_page_range_progress.py`、`tests/test_run_pipeline_dialog.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/基础速查/打包控件功能使用说明.md`、`docs/基础速查/设置面板排版思路.md`、`docs/技术实现/设置面板概述.md`、`docs/daily_log.md`

---

### 区域再检测：新块插入顺序改为「整组按行插入」

**问题/需求：** 用户反馈区域再检测检出的新块"总是倾向于插到最前面"，与就近落位的预期不符。

**改动要点：**

- **两个根因**：① 旧判据按"主方向带是否重叠"分叉，**跨行的两块也去比 x**，于是右下角的框因为"比右上角的更靠右"被判成该排在最前面；② `ui/region_redetect.py::page_direction` 的页方向投票把**每对相邻块**都算进去，8 票里 3 票是跨行噪声，页上少一个块（替换时必定少一个）就能把结论从右到左翻成左到右。另外逐块各自算下标会让同一次手势的框散开（实测一个气泡的 2 个框落到第 6 与第 8 位）。
- **改法**：`ui/region_redetect.py::insert_index` 改为**整组一个落点**（组中心取各块四边形中心均值，新增 `group_center`，整组插到同一下标、组内保持检测器给的顺序）；单元判据 `new_precedes` 与管线排顺序的 `utils/textblock.py::sort_regions` 同源——**先纵向排行**（新块中心在对方纵向跨度之下／之上／之内），只有落在跨度**之内**（同一行）才比 x；页方向投票只让**同一行**的两块参与；`RedetectConfig.min_neighbors` 由 2 降为 1（1 个参照块的落点已确定）。语义前提是**已有顺序正确**（本功能在误识别与顺序都处理完之后才人工触发），不做整页重排。
- **实测口径**：`projects/004_819b9e93` 三页共 21 个样本（把每个已有框自己当作拉框区域重检测，看新块是否连续落回原下标），旧判据 **5/21**、现判据 **21/21**。
- **落点流水常驻**：新增 `scripts/region_redetect_order.py`（原为三个一次性 tmp 脚本，已合并删除）。落点判据是几何启发式、合成单测锁不到"这片漫画该怎么读"，故按 `scripts/mw_repro.py` 的先例把排查工具变成常驻：`--detect` 真跑检测 + 写缓存（分钟级）并跑端到端核对，不带 `--detect` 读缓存离线判（秒级），`--candidates` 打印候选判据对比表（现役 21/21、逐块各自算 20/21、折线投影 0/21）——**改判据前先跑它**。已登记 `scripts/README.md`，`AGENTS.md` 的 `ui/region_redetect.py` 行注明改动须过回归台。

**测试：** `tests/test_region_redetect.py` 插入相关用例改写（合成件换成 sequence 入参、页方向只取同行票、1 个参照块也落位），新增「右下角的框不该插到最前面」「整组共用一个落点」「`group_center` 取值」三项回归；全文件 58 项全过。`scripts/verify.py` 除文档步外全绿——文档步报的 5 处 `tmp/` 失效引用（内存归因的 `_mem_probe*.py`／`_mem_after_fix.py`、真机验收的 `_rr_accept.py`）是**跨机器差异**：那些探针脚本留在主力机上，本机没有，与本次改动无关，暂按已知项放过。

**待办（主力机接手时做）：** 把 `tmp/` 里还活着的实测脚本（内存归因三件套、`_mem_after_fix`、`_rr_accept`）拉出来同步进仓库并登记 `scripts/README.md`——本功能是"测出来的"，结论要能复算，否则下次改动无从对比。已在 `docs/技术实现/区域再检测_设计与实现.md` §7 末尾立「待办」小节。

**涉及文件：** `ui/region_redetect.py`、`tests/test_region_redetect.py`、`scripts/region_redetect_order.py`、`scripts/README.md`、`AGENTS.md`、`docs/技术实现/区域再检测_设计与实现.md`、`docs/daily_log.md`


### 泛用工作台 D 组（工作台 UI）——规划收尾

**问题/需求：** 泛用工作台的 A／B／C1～C4 六批引擎此前已落地，只剩 D 组界面层：把只服务术语／剧情的 `GlossaryAgentPanel` 改组成"任务容器"（规划 D19～D27），让四个批量清理任务与术语／剧情共用一套三段式。用户口述"此前已做完底层、剩下最后的 UI 层"，本批即该层。

**改动要点：**

- **三段式骨架（D20／D23）**：① 一级导航 `ui/glossary_agent_panel.py::WorkbenchTaskNav` 六个互斥任务钮，按 D16 的用户工作流顺序排列（误识别清理→合并→框扩张→背景修复→术语提取→剧情摘要），前四项缀「还有 N 个未处理」（D37）；② 每任务一整页（候选列表 + **列表下方 100% 原比例预览**，只滚动不缩放，D11／D23）；③ 执行行，无勾选即禁用，批量执行前一律弹 D27 告知窗。术语／剧情两页沿用原草稿表。
- **砍 Chat（D19）及其三处连带**：`_on_prepare` 不再走 `_send_text`／用户气泡，直接 emit `instruction_requested`（信号面不变）；`_append_log` 的下家改成面板底部只读日志条（`setMaximumBlockCount` 自动丢最旧行）；`_on_busy_changed` 只切按钮可用性、不再切 tab 页。气泡流与 Tab 相关代码、QSS（`AIChat*`／`WorkbenchChatFlow`／`WorkbenchInputStack`）一并删除。
- **新增数据侧适配层 `ui/workbench_tasks.py`（无 QWidget）**：四个 `BatchTask` 子类（`MisreadTask`／`MergeTask`／`ExpandTask`／`SimpleInpaintTask`）负责 plan 摆行、100% 原比例审批截图与叠加框、交回 apply 标识、D27 弹窗正文、D37 计数。界面据此**不写几何、不碰 `proj.pages`、不绕开 `ui/batch_ops.py`**（复核文档 §4.2 的接线纪律）；唯一的几何动作是截图外扩，复用 `utils/block_geometry.py::expand_limited`。扩张量按复核文档 §4.3 仍**不设默认值**（0 时列表为空、执行禁用）。
- **新增 `ui/workbench_batch_view.py`（四个任务共用的视图）**：勾选列候选列表 + 预览 + 参数控件（由 `BatchTask.options_spec` 驱动的描述式控件）+ 执行行；错误码 → 文案表也在此。
- **引擎补一处能力缺口**：`ui/batch_merge.py` 的 `apply` 新增 `reversed_groups`，落实 D32 的「反转该组方向」——只翻转本次重规划结果里该组的拼接方向（`text`／`rich_text`／`lines` 同序，D40 契约一），不改样式来源与勾选范围。
- **入口改名（D25 单入口）**：左栏槽位由 `glossaryChecker` 改 `workbenchChecker`（objectName `WorkbenchChecker`、tooltip「Workbench」），QSS 3 条选择器与新增图标 `icons/leftbar_workbench.svg`／`_activate.svg` 同步；`ui/mainwindow.py` 的方法改 `on_set_workbench_widget`，并新增 `on_workbench_jump`（复用具脏页惰性重渲的 pageList 链路，D26）与 `on_workbench_rollback`（D35 的整批撤销）。宽度按 D24 维持 460。
- **跳步提示可禁用（D37）**：新增 `utils/config.py::ProgramConfig.workbench_warn_skip_order`（默认 True），弹窗内「不再提示」与设置面板「应用 → Workbench」互相同步；计数口径抽成 `ui/glossary_agent_panel.py::earlier_pending`（只有前四项参与）。
- **i18n 顺带修一个静默漏译**：`scripts/i18n_common.py::extract_tr_calls` 的正则原先要求引号后紧跟 `)`，多行 `QCoreApplication.translate(...)` 按 Black 风格末尾带逗号时**整条被漏掉且不报缺失**——本批新代码踩到 26 处，另有 `ui/llm_profile_cards.py` 8 处长期漏译。正则放宽为容忍尾随逗号，112 条新条目补 ts 并手填中文，陷阱写入 `docs/基础速查/i18n.md`。

**测试：** 新增 `tests/test_workbench_panel.py`（26 项：四任务的 plan／预览不缩放／勾选口径／驳回可逆／误聚默认不勾选／扩张量必填／D27 正文含后果；面板侧：导航顺序、切入即规划、无候选禁用执行、预览 100%、apply 记版本、整批撤回、跳转信号、跳步提示可取消可禁用、砍 Chat 后日志仍在）。`tests/test_glossary_agent_panel.py` 与 `tests/test_settings_app_page.py` 随结构调整更新，均通过。

**涉及文件：** `ui/glossary_agent_panel.py`、`ui/workbench_tasks.py`（新）、`ui/workbench_batch_view.py`（新）、`ui/batch_merge.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/configpanel.py`、`utils/config.py`、`config/stylesheet.css`、`icons/leftbar_workbench.svg`（新）、`icons/leftbar_workbench_activate.svg`（新）、`scripts/i18n_common.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_panel.py`（新）、`tests/test_glossary_agent_panel.py`、`AGENTS.md`、`docs/技术实现/泛用工作台_规划.md`、`docs/技术实现/泛用工作台_复核与拆分.md`、`docs/技术实现/术语剧情工作台_交接.md`、`docs/技术实现/翻译agent化_设计方案.md`、`docs/基础速查/i18n.md`

**遗留（两处只能真机复算，转交主力机）：** C1 判据命中率、C2 分组判据口径与 C3 扩张量默认值——方法与调参方向见 `docs/技术实现/泛用工作台_复核与拆分.md` §4.3。

---