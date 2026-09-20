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

## 2026-09-20

### 依赖兼容上游 + 共享环境「只增不升」补装（解 pillow 钉子 / `ensure_core_requirements`）

**摘要：** 为与上游共享同一依赖库，解除 `pillow>=10.0,<11` 钉子（上游 pillow 12.2.0 + pillow-jxl-plugin 实测 JXL roundtrip 正常，该钉子已过时）。补装语义改为**只增不升**：`utils/core_requirements.py::ensure_core_requirements` 补装前对已装发行版生成 `名称==版本` 快照，经 `utils/package_installer.py::build_install_command` 新增的 `constraints_file` 参数作 pip `-c` 约束传入——缺的包照补，任何会升级既有包的解析直接失败、快照留盘交人工决策。另 `pyproject.toml` 增 `acc` extra（numba 可选加速，缺失照旧回退纯 NumPy）。

**验证：** 带约束的 `pip install -r requirements.txt --dry-run` 解析无报错无升级；`tests/test_dependency_startup.py` 8 通过。

**涉及文件：** `pyproject.toml`、`requirements.txt`、`utils/package_installer.py`、`utils/core_requirements.py`、`docs/基础速查/依赖库说明.md`

---

### 左栏打开钮回归图标轴线（去 QToolBar 包裹 + `ui/mainwindowbars.py::OpenBtn` 菜单自持）

**摘要：** openBtn 偏左、与下方四个 33px checker 不同轴，两个根因：外面包的 33px `QToolBar` 自带边距把 28px 按钮挤出轴线；左栏 vlayout 实为**左锚排列**（各项同点 x=7），宽度不同即中心错开。修法＝去掉 QToolBar 包裹、openBtn 直接 33×33 进布局（QSS `image` 随盒缩放，`OpenBtn` 规则加 `padding: 3px` 把字形压回原尺寸）；菜单改 `OpenBtn` 自持（`assignMenu` + 点击手动弹出），因为 `QToolButton.setMenu` 的菜单指示器会在右侧保留箭头位把 QSS image 挤到左边，且 QSS 归零 width/height/image 都收不回那块保留区。

**验证：** 离屏渲染实测五图标字形中心 28.5~29.5 对齐（余量 ±0.75px 为 SVG 内容 1px 不对称）。

**涉及文件：** `ui/mainwindowbars.py`、`config/stylesheet.css`

---

### 依赖政策：CPU 发行包刻意不装 transformers（`paddleocr_vl_manga` 专属依赖）

**摘要：** `paddleocr_vl_manga` 是唯一走 transformers 的模块，且该导入写在 `_load_model` 内（不在模块顶层），所以「把注册模块全 import 一遍」查不出缺包。它是 GPU 才实用的生成式 VLM（CPU 上逐块自回归无实用价值），而 CPU 包面向的正是无独显用户，`transformers` 本体 113 MB 属白占 ⇒ **决定不随 CPU 包分发**；GPU 用户换 CUDA torch 后选中该模块即由 `modules/base.py::ensure_dependencies` 自动补装（区间收在 `modules/ocr/ocr_vl_manga.py::requires_packages`，不需要 sentencepiece）。CPU 包验收范围相应移出 VL OCR 自检。

**验证：** 卸载后发行版数回到 79（与改动前一致）、`pip check` 与开发包同基线、`tests/test_startup_imports.py` 6 通过、14 个注册模块强制解析零失败、各模块声明的权重齐备。顺带实测该模块在 CPU 包上确实可跑（合成竖排日文识别出 `ざわ / ざわ`，加载 3.0s、单块 1.7s、fp32），只是不随包分发。

**涉及文件：** `docs/基础速查/依赖库说明.md`（依赖包本体在仓库外）

---

### GPU 硬闸门：无可用加速设备时拒绝下载 GPU-only 模型（`requires_gpu`）

**摘要：** 新增模块侧声明 `modules/base.py::BaseModule.requires_gpu`（贯通 `utils/registry.py::ModuleSpec`、`utils/lazy_registry.py::LAZY_CLASS_ATTRS`、`modules/__init__.py::GET_MODULE_REQUIREMENTS`），首用例 `modules/ocr/ocr_vl_manga.py`；判据 `modules/base.py::accelerator_available` 取 `DEFAULT_DEVICE != "cpu"`（**刻意不用** `torch.cuda.is_available()`——xpu/mps/directml 同样跑得动）；硬闸门落在 `ui/model_downloads.py::ModelDownloadRegistry.start`（命中即拒，pip 依赖与权重都不碰），文案与判据同处一地（`gpu_required_message`／`gpu_requirement_block`）。两个入口各按自己的惯例弹同一文案：「模型文件」页下载钮，与 `ui/module_manager.py::_ensure_module_deps`（后者是 §5.1「选模块不弹窗」的有意例外，模块照常切换）；面板徽章改「需要 GPU」而行仍可点（`ui/model_files_panel.py::_badge_of`），加载期缺文件提示也换 GPU 口径、不再指路去一个会拒绝你的下载钮。

**闸门只拦下载、不设运行侧障碍**（用户拍板：提示到位就够了，没必要故意妨碍）——第二段文案相应说「不提供下载」，与实现同口径；运行侧仍是 `_resolve_device` 警告后退回 CPU，手工放入权重照样能跑。文案措辞**由用户 2026-09-20 定稿，改动前先问**；取舍的完整记录见 `docs/技术实现/模型文件管理_设计方案.md` §5.5。

**顺带修掉一个 i18n 提取器盲区：** `scripts/i18n_common.py` 的 `QCoreApplication.translate` 正则原先只认「两个参数同一种引号」，`translate("ctx", '文本')` 这种混合写法两条正则都匹配不到、静默漏进 .ts（三条既有文案因此在中文界面一直显示英文）。

**验证：** CPU 依赖包实测闸门拒绝（`start()` 返回 False、注册表无任务、其他 OCR 模块不受影响）、开发包放行、开发包加 `BALLOONTRANS_CPU_ONLY=1` 也拒绝；`tests/test_model_downloads.py::TestGpuGate` 新增 7 项钉住拒绝分支（开发机自带 CUDA，不打补丁走不到）；i18n 三查 + qm 重编 + `QTranslator` 实测载出中文，渲染图见 `tmp/00_gpu_refused_zh.png` 等三张。

**涉及文件：** `modules/base.py`、`modules/ocr/ocr_vl_manga.py`、`modules/__init__.py`、`utils/registry.py`、`utils/lazy_registry.py`、`ui/model_downloads.py`、`ui/model_files_panel.py`、`ui/module_manager.py`、`scripts/i18n_common.py`、`tests/test_model_downloads.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`AGENTS.md`、`docs/技术实现/模型文件管理_设计方案.md`（新增 §5.5）、`docs/基础速查/依赖库说明.md`

### 开发日志规范改「可检索优先」+ AGENTS.md 瘦身（不常用技术点只留钩子）

**摘要：** 日志定位改为**主要给 AI 读的时间索引**（用户基本不看它）：格式固定三行——标题（带关键符号名／配置字段名／D 编号，供 grep）、`**摘要：**`（2~4 句，只写决策与理由）、`**涉及文件：**`（最常用的检索键），实现过程留给 commit body 与设计文档、一条 4~8 行；三天存量按此重写（25741 → 16666 字符，降 35%，19 条不丢）。3 天窗口保留，另写明更早记录的 git 回查口令（`git log --grep`／`git log --follow -p`／`git show <rev>:docs/daily_log.md`），配套要求**提交信息与日志标题用同一批关键词**，日志被裁掉也不丢线索。AGENTS.md 侧按「不常用的技术点只留一句定位 + 指针」收一遍：`modules/ocr/ocr_vl_manga.py` 的实现坑（整块裁剪不逐行、`use_cache`、`add_prefix_space=None`）已在 `docs/技术实现/paddle-ocr-for-manga_接入调研.md` 里，行内不再重复；模型文件管理四行与 `utils/memory_release.py` 同样只留约束与指针。

**验证：** `scripts/trim_daily_log.py --check` 确认新格式仍能被窗口裁剪正确解析；`scripts/verify.py` 全绿。

**涉及文件：** `AGENTS.md`、`docs/daily_log.md`

---

## 2026-09-19

### 工作台批量任务页加刷新钮（重扫候选列表，`ui/workbench_batch_view.py::RefreshButton`）

**摘要：** 候选列表是懒规划快照，用户在画布上删框／改框后列表不跟随，出现「计数有数、列表空／过期」（误识别清理尤其常见）。新增 `RefreshButton`（QPainter 自绘循环箭头图标钮，主题取色、悬停描边，不引 SVG 资源），在 `BatchTaskView._build_actions` 里置于执行行首位，点击即重跑既有 `replan()`（连带 `plan_changed` → 导航计数同步）。术语／剧情两页是草稿镜像、无此需求，不加。

**验证：** `scripts/verify.py` 全绿；`scripts/workbench_render.py` 渲染并放大目视确认图标可认。

**涉及文件：** `ui/workbench_batch_view.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 行拖拽逐帧绘制优化（多选掉帧）+ 抓住缩放改 1.05（`ui/textedit_area.py`）

**摘要：** 掉帧主因＝被拖卡装着 `_CardScaleEffect`，而 Qt 对几何变化的控件会失效效果源 pixmap 缓存 ⇒ 跟手补间每帧都要把整张卡（两个 QTextEdit 的富文本排版）重渲到设备分辨率离屏图，多选即 N 次/帧。三处改动：① **效果源 pixmap 缓存**——拖拽期间卡片内容冻结，装上后首帧抓一次、`draw()` 复用缓存做缩放 blit，factor 回 1.0 弃缓存；② **跟手重定向 2px 死区**（只作用于 `_update_drag_frame` 的追手路径），避免光标慢移时反复停旧建新 `QPropertyAnimation`；③ `GRAB_SCALE` 1.1→1.05。中途踩到一处缓存坐标错误：`sourcePixmap(DeviceCoordinates)` 的 `offset` 是**当帧控件在窗口里的绝对位置**（随移动逐帧变化），直接缓存会把卡片钉死在抓取时刻的位置——改为缓存「pixmap 左上相对控件原点」的常量偏移，每帧用 `deviceTransform` 现算原点。

**验证：** `tests/test_row_drag.py` 新增三组（缓存只抓一次、快照跟手移动、死区重定向），27 项连跑 10 次全绿。新增用例曾引出套件约半数用例的退出期 access violation（悬挂效果的控件在解释器退出期 GC 级联中销毁；stdout 缓冲让崩溃看似发生在更早的用例），用例结束前 `QTest.qWait(300)` 排干收尾动画后 10/10 稳定。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`

---

### RowTable 自绘行列表控件替换工作台裸 QTableWidget（`ui/custom_widget/row_table.py`）

**摘要：** 裸 `QTableWidget` 逐格 QSS 边框渲染不全、背景兜底规则只按裸 `QTableView` 匹配不及子类。新增 `RowTable`（经 `ui/custom_widget/__init__.py` 导出）：`MODE_TABLE`＝列对齐紧凑表格（淡行分隔线、无竖网格），`MODE_CARD`＝主文+次行元数据+右侧徽章的圆角审批卡；底色／选中染底／勾选框／徽章全在 delegate 自绘（行数据经 `Qt.UserRole` 传 dict），QSS 只管底色与列头，主题配色走 `get_theme_color`、缓存于 delegate 且 `StyleChange` 时失效。工作台四个批量任务候选列表切到该控件，展示台与 AGENTS.md 控件表同步。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/custom_widget/__init__.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`scripts/style_showcase.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_panel.py`、`AGENTS.md`

---

### 本批收尾：RowTable 可读性打磨 + 扩张页改版 + 文档瘦身等（分类型提交）

**摘要：** 把当天挂起的修改按类型分批提交；另修一处对齐——左栏「运行」按钮与设置图标同点左锚但宽度 33 vs 28，中心错开 2~3px。要点：RowTable 悬停行淡染，选中染底上的文字按实际观感色在 `@dragTextColor`／`@inverseTextColor` 里挑对比更高的（部分主题 `@dragTextColor` 是暗色，固定取会看不清）；`ExpandTask` 列由「旧矩形/新矩形」坐标改单列「增长」描述，并新增真机探针 `scripts/probes/expand_centering_probe.py`（扩张后译文按新框重排，「是否居中」取决于块自身 alignment）；`utils/font_scan.py::scan_font_faces` 的日志压制改为整棵 `fontTools.*` 树一起压（全局 `setLoggerClass` 后子 logger 自带 console handler，压根 logger 压不住）；新增 `scripts/trim_daily_log.py` 与 `scripts/hooks/pre-commit`（daily_log 3 天窗口自动裁剪）；文档瘦身（AGENTS.md 关键文件表改「一句话定位 + 最小约束 + 指针」，多篇速查砍历史细节与过期内容），并新增 `docs/技术实现/paddle-ocr-for-manga_接入调研.md`。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`ui/mainwindowbars.py`、`utils/font_scan.py`、`config/stylesheet.css`、`scripts/trim_daily_log.py`（新）、`scripts/hooks/pre-commit`（新）、`scripts/probes/expand_centering_probe.py`（新）、`scripts/README.md`、`scripts/probes/README.md`、`AGENTS.md`、`docs/基础速查/`（6 篇）、`docs/技术实现/`（2 篇新）

---

## 2026-09-18

### 工作台 UI 审计与逐项优化（D44 浮层形态收口 + 一批观感／状态同步修复）

**摘要：** 用 `scripts/workbench_render.py` 把六个任务页渲染成图做了一轮观感审计（列 11 项），用户逐条拍板后落地。要点：审批浮层（D44，`ui/workbench_preview.py`）尺寸随图自适应（用户拖过之后不再自动改）、去掉关闭钮改点画布／Esc 关（两者走一个只在浮层可见期挂着、**一律不吞事件**的应用级过滤器）、不再抢键盘焦点（原先 `setFocus` 会把焦点从候选列表夺走，↑/↓ 翻行当场失效）、标题改报「哪一页哪个框」（`ui/workbench_tasks.py::row_caption`）、重规划与换任务时自动收起（原先会留着已被删掉那个块的截图）；参数区 bool 控件改 `ConfigCheckBox`、数值框后缀随单位走；新增 `plan_changed` 让导航计数随参数刷新、无选中行时「跳到画布」禁用；四个任务的说明与空态文案收短；渲染台宿主改 `WA_DontShowOnScreen` + `show()`（直接 `show()` 会被窗口管理器压到屏幕大小，截图随环境变）。

**验证：** `tests/test_workbench_preview.py` 16 项、`tests/test_workbench_panel.py` 35 项；`scripts/verify.py` 七步全绿。

**遗留：** 浮层位置每次仍锚画布左上角（只记住「用户调过尺寸」这一状态）；设计文档 §17 第 9 条的同批待改项 ①③④ 仍开着。

**涉及文件：** `ui/workbench_preview.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`ui/glossary_agent_panel.py`、`ui/configpanel.py`、`config/stylesheet.css`、`scripts/workbench_render.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_preview.py`、`tests/test_workbench_panel.py`、`docs/技术实现/AI辅助功能_设计与实现.md`

---

### 三处实测缺陷修复（批量写版本崩溃 / 误识别清理列表空白 / 单行浮层弹不出）

**摘要：** 三处都是真机报的，根因各自具体：① `utils/batch_versions.py::_capture_pixels` 一页多矩形直接 `np.concatenate(axis=0)`，该调用**要求各裁片同宽**，而简单背景修复一页有多条不同宽的纯色带必抛异常（实测 228 vs 130），`begin()` 吞掉后整批在写版本这步中止 ⇒ 新增 `_stack_crops` 按最大宽度**左侧对齐补零**（还原侧逐条取 `strip[y:y+h, :w]`，多出的右边距是死区、不写回图像，旧版本仍可读）。② `ui/glossary_agent_panel.py::GlossaryAgentPanel.refresh_project_state` 在面板不可见时跳过规划，却已无条件把首个任务从 `_dirty_tasks` 摘掉 ⇒ 之后 `showEvent` 也不再补规划，列表永空而导航计数照旧显示 15 条；改为脏位只由 `_ensure_current_planned` 清。③ 只有一行候选时关掉浮层后再点该行，表格选中行没变 ⇒ 不发 `itemSelectionChanged`，浮层再也弹不出来（多行时点别行碰巧能弹，故表现为「时好时坏」）⇒ 补 `cellClicked` 取预览，并让浮层 `closed` 信号经面板转达成 `forget_preview`。

**验证：** 三条回归用例，且都实测过「旧实现下必失败」；相关 10 个测试模块 214 项全过。

**涉及文件：** `utils/batch_versions.py`、`ui/glossary_agent_panel.py`、`ui/workbench_batch_view.py`、`tests/test_batch_versions.py`、`tests/test_batch_simple_inpaint.py`、`tests/test_workbench_panel.py`

---

### D5：单块 Alt + 拖手柄 = 以中心缩放（PS 式即时修饰键，`ui/texteditshapecontrol.py`）

**摘要：** 用户实测两轮后把口径钉成 PS 那种**换算**而非「重定基」：参考点与「场景↔本地」坐标映射全取自起手那一刻（由 `_pinResizeAnchor` 统一落定），切换时用上一次光标位置把当前帧重算一遍 ⇒ 被拖手柄始终钉在光标、对侧当场镜像出去／收回来，来回按 Alt 不累积误差。鼠标不动时也要生效 ⇒ 新增 `_ResizeModifierWatcher`（挂 `QApplication` 的键盘旁听器，`return False` 只旁听、不拦截不抢焦点，以免顶掉画布 Alt+WASD 切块）。两个坑：Qt 的 `event.modifiers()` 返回**事件之前**的状态（一律按 `key()/type()` 判断）；`_beginProxyDrag` 的跟手锚点必须仍是手柄位置。撤销沿用 `ReshapeItemCommand`。

**验证：** `tests/test_text_transform_ui.py` 新增 6 条（PyQt6 不能构造 `QGraphicsSceneMouseEvent`，桩事件提为模块级）；真机探针 9 组情形（旋转 0／30°／−45°／120°、角与边手柄、两种切换方向）跟手误差、中心漂移、对角漂移**恒为 0**。**手感仍待用户实机验收。**

**遗留：** 手动版不套「碰到邻框即停」（那是批量扩张的引擎侧行为）。

**涉及文件：** `ui/texteditshapecontrol.py`、`tests/test_text_transform_ui.py`

---

### 工作台两个参数设置项 + 「工作台（临时）」设置页

**摘要：** 复核时点出两处「定了但没做」：D33d 要求的「设置内参数接口」只有 `ui/batch_merge.py::MergeConfig` 里的硬编码默认值，C3 的批量扩张量则明确「不给默认值」导致每次都要手填。两项都做成设置项并**先放临时页**：`utils/config.py::ProgramConfig` 新增 `workbench_merge_oversize_ratio`（0.85）与 `workbench_expand_px`（10，用户定的默认），设置面板新增「工作台（临时）」页（导航 key `workbench_temp`，页数 9→10）。**注入在任务层而非引擎**：`ui/workbench_tasks.py` 的 `MergeTask._engine` 传 `MergeConfig(oversize_ratio=...)`、`ExpandTask.options_spec` 初值取 pcfg，引擎继续保持「参数由调用方给」，裸脚本复算不受设置影响。

**验证：** `tests/test_settings_app_page.py` 页数断言 9→10 并新增「初值取自 pcfg／改动回写」；`tests/test_workbench_panel.py` 两条按新语义重写。

**遗留：** 临时页的归并——用户拍板**先不动**，不作为待办再问。

**涉及文件：** `utils/config.py`、`ui/configpanel.py`、`ui/workbench_tasks.py`、`tests/test_settings_app_page.py`、`tests/test_workbench_panel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 工作台参数复算台与真机探针常驻（`scripts/workbench_recalc.py`、`scripts/probes/`）

**摘要：** C1／C2／C3／C4／D39 这些参数是「测出来的」，此前复算脚本全在 `tmp/`，一清理就失去复算能力。新增 `scripts/workbench_recalc.py`（六个**只读**子命令 `merge`／`c1`／`expand`／`queue`／`review`／`hook`／`list`，`--sweep` 加阈值扫描，`--project` 指工作副本，脚本末尾自证样本顶层文件 mtime 未变）与 `scripts/probes/`（11 个真机探针 + 自带 README，写明各自前提与期望数字）。登记到位：`scripts/README.md` 与 AGENTS.md 同步；`scripts/check_docs.py` 只扫 `scripts/` 顶层，子目录不强制登记。

**涉及文件：** `scripts/workbench_recalc.py`（新）、`scripts/probes/`（新）、`scripts/README.md`、`AGENTS.md`

---

### AI 辅助功能文档：三合一 + 删 6 份过期文档 + 全仓引用改指

**摘要：** 围绕泛用工作台的四份文档（规划／复核与拆分／术语剧情工作台_交接／AI辅助功能_规划）互相交叠，合并为 `docs/技术实现/AI辅助功能_设计与实现.md`（总纲 → 体系总览 → Part I 标签体系与框级动作 → Part II 工作台 → Part III 调参与待办）；**保留 D 编号体系**——代码注释里约 200 处按编号与「设计 §X」引用，节号与编号不要乱动；翻译 agent 的架构基线仍独立在 `docs/技术实现/翻译agent化_设计方案.md`。删 6 份并在 `scripts/audit_registry.json` 登记（现 55 条）。另外代码里还散着 30 处**不带文件名**的旧节号引用（检查器不管但语义已失效），用带期望命中数的映射脚本集中替换、落到 15 个文件。踩坑：**`check_audit` 的语料包含 `.agents/`**（SKIP_DIRS 里没有它），技能文档里提到已删文件名同样会被判残留引用。

**验证：** `check_docs` 22 篇全绿（原 27）／`check_audit` 55 条通过／`check_syntax` 通过／受影响 10 个测试文件全绿。

**涉及文件：** `docs/技术实现/AI辅助功能_设计与实现.md`（新）与 6 份删除、`docs/项目概述.md`、`docs/基础速查/AI辅助标签体系使用说明.md`、`docs/技术实现/区域再检测_设计与实现.md`、`AGENTS.md`、`scripts/audit_registry.json`，以及 15 个代码／测试文件里的注释引用

---

### 页范围数值框：修「单跑过、连跑必红」的用例间残留（`tests/test_page_range_progress.py`）

**摘要：** 全量 pytest 里唯一的红是 `test_clicks_elsewhere_reach_the_native_editor`（`lineEdit().hasFocus()` 为假），单跑 PASS、与同文件前一用例连跑必失败。定位＝**用例间残留**：offscreen 平台上 `deleteLater` 不会立刻销毁前一个 top-level 窗口，它仍占着激活态 ⇒ 本用例新 `show()` 的窗口拿不到激活，Qt 就不把焦点交给它的 `QLineEdit`（控件本身经探针证明正常）。改法：`_spin()` 里补 `activateWindow()`；清理从裸 `deleteLater` 换成 `_close_spin`（`close()` + `deleteLater()` + `processEvents()`）。

**验证：** 判据是**先用 `git show HEAD:<path>` 导出改前版本复现出同一条失败**（1 failed / 9 passed），再跑新版本连跑 3 次全绿。

**涉及文件：** `tests/test_page_range_progress.py`

---

### 软键盘不再随启动自动启用（`pcfg.symbol_keyboard_enabled`）

**摘要：** 软键盘是「功能开关」型入口（勾选态即 `pcfg.symbol_keyboard_enabled`），此前每次启动都会按上次会话的值自动启用，用户要求纳入「启动不自动展开」清账名单 ⇒ `ui/mainwindow.py::MainWindow` 的启动清账名单加入 `symbol_keyboard_enabled`。注意该字段在 `install_symbol_launcher`（构造 SceneTextManager 时，**早于清账段**）就已写进图标勾选态，所以除清字段还要把图标复位，否则会出现「图标亮着、开关是关的」；复位走 `toggled`，槽里对此时尚为 `None` 的 `symbol_dock` 是空操作。清账只作用于启动，手点图标仍能正常启用。

**验证：** 真机探针（**必须窗口模式**，offscreen 起不来 `FramelessWindow`；`config/config.json` 先备份后还原）8 项断言全 PASS。

**涉及文件：** `ui/mainwindow.py`

---

### 工作台 UI 优化（一）：导航两级化（D42）+ 底部状态条（D43）+ 目视验收渲染台

**摘要：** ① **导航两级化（D42）**：一级＝管线阶段大类（文字与 OCR／图像修复／翻译），二级＝该大类下的任务 chip；计数两级都缀，切大类落回**该大类上次用过的任务**；`WORKBENCH_ORDER` 改由 `WORKBENCH_CATEGORIES` 摊平 ⇒ 前四项仍是 `CLEANUP_TASK_IDS`、跳步提示口径不变（既有用例未改一行）。大类标签的翻译上下文必须用 `WorkbenchTaskNav`——`GlossaryAgentPanel` 里 `"Translation"` 已被术语表列头占为「译文」，同 context 同 source 只能有一个译文。② **底部状态条（D43）**：日志区用 ID 选择器抹掉 `ConfigTextEdit` 的输入框外观；日志跨任务共用，故批量任务的行前缀任务名、空列表时不再把页面摘要抄进日志。③ 容器底色原先空转（纯 `QWidget` 不上屏 QSS background），按仓库既有做法开 `WA_StyledBackground`。④ `tmp/wb_render.py` 正式化为 `scripts/workbench_render.py`，**不许设 `QT_QPA_PLATFORM=offscreen`**（离屏平台字体族实测为 0，任何文字都是豆腐块）。

**遗留（已登记进设计文档 §17 第 9 项）：** 空态下日志与当前任务无关、无候选时执行钮仍用带计数文字而非禁用＋原因、参数改动 180ms 防抖像「没反应」、预览区固定高度留白等，逐条与用户确认后再改。

**涉及文件：** `ui/glossary_agent_panel.py`、`ui/workbench_batch_view.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/workbench_render.py`（新）、`scripts/README.md`、`tests/test_workbench_panel.py`、`AGENTS.md`、`docs/技术实现/AI辅助功能_设计与实现.md`

> **过程说明：** 本轮中途工作区被一次 `git reset --hard origin/main` 清过（HEAD 由 `5ecfad32` 前进到 `518398ba`），当时未提交的 `ui/`、`config/`、`translate/` 改动随之丢失，上述改动按同一口径逐条重做。工作区里同时有别的在途改动时，动 `reset --hard`／`checkout` 前请先看 `git status`（AGENTS.md「多代理协作」）。

---

### 工作台 UI 优化（二）：审批预览改浮层（D44）+ 简单背景判据补两条（D45）

**摘要：** ① **预览改浮层（D44）**：新增 `ui/workbench_preview.py::WorkbenchPreviewPanel`——in-window child（同 `ui/custom_widget/rail_dock_panel.py::RailDockPanel` 做法，不占工作台宽度），滚轮**以光标为锚点**缩放、拖拽平移、双击适应窗口；页内预览区撤掉，改由 `preview_requested` 信号把图与标题交给浮层，**D23 的「100% 原比例」口径不变**（缩放只能由用户发起）。② **简单背景判据（D45，阈值不动）**：诊断出「判不出」是**几何／遮罩**判定失败而非颜色判据失败——判据靠最外那条 1px 兜底外框围出闭合轮廓，于是遮罩贴到裁剪边界时会把外框擦掉、邻块遮罩落进裁剪窗会把包围盒撑成跨块并集。改法：`utils/textblock_mask.py::extract_ballon_mask` 分析前**外补 3px**，新增 `modules/inpaint/base.py::block_local_mask`（按连通域只留与本块矩形相交的遮罩、筛不出时原样返回）并由 `classify_simple` 的新参数 `blk_rect` 与两条调用路径传入本块矩形。量化「判不出」**4/4 → 0/4**，两张渐变反向对照改前改后都判复杂（没被放过）。注意合成场景的遮罩必须画成**笔画**——盖满整块会把渐变背景一起盖掉。

**验证：** 新增 `tests/test_workbench_preview.py` 14 项、`tests/test_batch_simple_inpaint.py` 7 项；另修正一条旧用例（它用 `img[:50,:50]` 却声称「裁剪区内没有掩码」，实际有、只是贴在裁剪边界上）。真工程命中率对比跑 `scripts/workbench_recalc.py c1 --project <目录>`（改前基线 401 简单／131 复杂／250 判不出）。

**遗留：** 气泡轮廓被裁剪区切断的块（如贴页边且框比气泡小的）仍走兜底判**复杂**（保守跳过、不会误涂）；进一步吃下属判据语义变更，等用户定。

**涉及文件：** `ui/workbench_preview.py`（新）、`ui/workbench_batch_view.py`、`ui/glossary_agent_panel.py`、`modules/inpaint/base.py`、`utils/textblock_mask.py`、`ui/batch_inpaint.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/workbench_render.py`、`tests/test_workbench_preview.py`（新）、`tests/test_workbench_panel.py`、`tests/test_batch_simple_inpaint.py`、`AGENTS.md`、`docs/技术实现/AI辅助功能_设计与实现.md`

---

### 默认文字检测器改为 ysgyolo（`label.other` 同步默认关闭）

**摘要：** `utils/config.py::ModuleConfig` 的 `textdetector` 默认值由 `ctd` 改为 `ysgyolo`（`ctd` 仍在注册表里，照旧可选）；`modules/textdetector/detector_ysg.py::YSGYoloDetector` 的 `label.other` 默认由 `True` 改为 `False`——`other` 拉的是覆盖整个气泡的**气泡级框**（不只是多一层遮罩），而检出框一律进 `utils/textblock.py::mit_merge_textlines` 的合并池，于是多出若干「整只气泡」的 `TextBlock` 会被 OCR／翻译／渲染（实测同一页块数 9→15、遮罩覆盖 5.09%→15.03%）；对修复侧则把裁剪窗吃光，简单背景判据直接判不出（实测 6/15）。其余参数代码默认已与当前配置一致；`device` 保持 `modules/base.py::DEVICE_SELECTOR`（跟随本机可用设备），未钉成 `cpu`。

**注意：** ysgyolo 需 `ultralytics` 与 `data/models/ysgyolo_yolo26_2.0.pt`（模型文件被 gitignore），两者缺失时 `launch.py` 按既有兜底静默降级为 `none` 检测器——与原先默认 `ctd` 情形一致。

**验证：** `scripts/verify.py` 七步全绿（含启动冒烟）；另静态确认全新 `ModuleConfig()` 取到 `ysgyolo`、`YSGYoloDetector()` 默认有效标签为五项（不含 `other`）。

**遗留（用户已确认后做）：** 在检测器参数区加一段模型行为特性备注，本轮不做。

**涉及文件：** `utils/config.py`、`modules/textdetector/detector_ysg.py`

---
