# 每日开发日志

> 记录**仓库层面**的改动（功能增删、远端分支变动、规范调整），供变更史查阅。踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见 `AGENTS.md` 的「多代理协作」一节），不进仓库。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

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

## 2026-09-16

### 复核文档补「接手端待办与易错点」一节

**问题/需求：** C 组四个任务的引擎全部落地后，剩余工作只剩 D 组与两处真机复算；但四份文档的记录是分散的（决策在规划、复核结论在复核文档、实现细节在各自模块 docstring），接手端容易按字面理解把已完成的引擎重做一遍（例如又写一个 C2 的审批面板、给 C3 编一个默认扩张量）。

**改动要点：** `docs/技术实现/泛用工作台_复核与拆分.md` 新增 §4「接手端待办与易错点」：① 剩余工作只有三件（D 组 UI、C1 复算、C2 口径与 C3 扩张量复算）；② D 组的接线清单——四个引擎的 `plan`／`apply` 给什么要什么、界面该干什么、必须传对的 `op` 与 `label`、`apply` 报告字段与全部错误码取值；③ 两处只能真机做的复算怎么跑、调哪个参数；④ 「明确不要做」七条（含不动 `sort_regions`、不复用单页删除命令、不用 `updateTextBlkList` 批量写回、不原地改块对象等）；⑤ 提交与协作约束。

**涉及文件：** `docs/技术实现/泛用工作台_复核与拆分.md`、`docs/daily_log.md`

---

### 泛用工作台批次 C3（批量框扩张引擎）+ 几何判据单点化

**问题/需求：** 检测框紧贴文字，译文渲染时"框不够大"（规划 §4 的"需要给渲染留出空间"）。规划只定了上限"碰到邻框即停"，**没定扩多少**。

**改动要点：** 新增 `ui/batch_expand.py`：`plan` 只读算出"这批框会变成什么样"（含"多少框已贴住邻框／页边扩不动""多少框至少一条边被截"的统计，供挑数值），`apply` 整批写回。**扩张量由调用方给定、不设默认值**（`amount` + `mode`＝`px`／短边比例，后者跨分辨率一致）——规划没定义这个量，凭空的默认值等在实机上看渲染效果再定；这是本任务唯一需要真实项目的一环。**只改渲染矩形**：`_bounding_rect` 与 `xyxy` 同步变动（D36），**掩码与修复数据原样保留**（D5 的"不动擦除区域"落到数据上就是 `region_mask`／`region_inpaint_dict` 不动）。**实现中发现并修掉一处算法缺陷**：各框都按**原始**邻框矩形求限时，两个相邻框会互相穿过（双边都越界），直接违反拆分表 C3 的验收点"扩张后无重叠"；改为同页按页块列表顺序**串行**扩张、邻框取"已扩后"的矩形（先者吃空档、后者停在它边缘），并加了一条多框排列的性质用例钉住。写回走 D40 五步 + D35 版本撤回，且写的是块**副本**——版本快照在写回前从内存取，原地改会让快照记成改动后状态、撤销失效（有用例验"撤销后回到原矩形"）。旋转框与退化矩形不参与（与既有批量对齐同一取舍）。另抽出 `utils/block_geometry.py`（`rect_of`／`overlap_ratio`／`gap_length`／`expand_limited`）作为"外扩到碰到邻框为止"的唯一实现，C2 的审批截图改用同一份（C2 的 52 项回归保持全绿）。

**测试：** 新增 `tests/test_batch_expand.py`（22 项：两种扩张量、截断统计、贴住即不变、旋转／退化不参与、未知 mode 报错、缺页尺寸跳过、只读性、双字段同步、掩码保留、不重叠性质、选择子集、stale、无可扩张、页代数与 D40 判据、撤销回原矩形、取消、脏页标记与跳过、跨页、前置对齐顺序）与 `tests/test_block_geometry.py`（15 项：矩形解析、重叠率／间隙、外扩的页边界与邻框截停、判定带不重叠则不截）。

**涉及文件：** `ui/batch_expand.py`、`utils/block_geometry.py`、`ui/batch_merge.py`、`tests/test_batch_expand.py`、`tests/test_block_geometry.py`、`AGENTS.md`、`docs/技术实现/泛用工作台_规划.md`、`docs/技术实现/泛用工作台_复核与拆分.md`、`docs/daily_log.md`

---

### 泛用工作台批次 C4（一键批量删除误框引擎）

**问题/需求：** 规划 §7 的误框队列（样本 94 页里 128 块，平均每页 1.4 块）需要一个人确认后一次删掉整批的出口；D27 明确它不能复用现成的 `DeleteBlkItemsCommand`（只作用于当前页、且带"顺手修复该区域"的支路），并属于要弹窗告知后果的新出口。

**改动要点：** 新增 `ui/batch_delete.py`：只读 `plan` 列队列（队列＝带「误识别文本」标签的块，口径与 `utils/block_tags.py::misread_queue_summary` 同，条目带子类型／驳回态／原文预览，不带块对象），整批 `apply` 跨页删除。选中口径按 D27／D28 落定：缺省删「队列内**未驳回**」的块（驳回＝标签判错、勾选＝框该删，两轴正交，显式勾选不受驳回限制）；给的标识必须仍是队列成员，否则不删并进 `stale`（本出口只负责误框，不是通用删除）。**只删文本框层**：不碰遮罩与修复图（有用例验遮罩文件 mtime 不变），故 D27 弹窗须照实说"已留在 `inpainted/` 的修复痕迹不会因此还原"。写回走 `ui/batch_ops.py` 的 D40 五步（页代数在此步）与 D35 版本撤回；前置对齐同样先于规划，否则队列下标会与面板所见错位。既有单页删除命令保持不动。

**测试：** 新增 `tests/test_batch_delete.py`（17 项：队列计数与条目形态、空队列、只读性、缺省只删待处理、显式勾选可删已驳回、非队列成员进 stale 且不写、跨页与页代数、D40 验收判据、整批撤回、取消不留痕、脏页标记与跳过、删多块后剩余顺序与对象身份、图像层不被写入、前置对齐顺序——最后这条反向验证过"把对齐挪到规划之后必红"）。

**涉及文件：** `ui/batch_delete.py`、`tests/test_batch_delete.py`、`AGENTS.md`、`docs/技术实现/泛用工作台_规划.md`、`docs/技术实现/泛用工作台_复核与拆分.md`、`docs/daily_log.md`

---

### 泛用工作台批次 C2（批量合并相邻框引擎）

**问题/需求：** 规划 §4 用户工作流里最费力的一环——检测模型一行一框（竖排则一列一框），一个气泡内的文字被切成多个框，人工逐个合并量极大（样本 1565 框里 1323 框涉及合并）。C1 的验收（判据命中率 8.3%）也等着"先把框合并成气泡级"再复算。

**改动要点：** 新增 `ui/batch_merge.py`：只读规划 `plan` ＋ 写回 `apply` ＋ 审批截图 `group_crop`。分组判据按规划 §6.3 的"同页 + 行高相近 + 重叠 > 0.6"落成三条——沿一根轴的投影重叠 ≥ 0.6、另一根轴上尺寸比 ≥ 0.6、同轴间隙 ≤ 一个自身交叉尺寸（"相邻"的落地，没有它会整页同带连通），组取连通分量；**精确定义随实测脚本（`tmp/`，已清理）丢失，阈值全收在 `MergeConfig`**，主机上用 `plan()` 只读复算 §6.3 的 435 组口径（该样本不在本机）。方向判定按 D8／D32 三级（组内 `src_is_vertical` 投票 → 块顺序 → 页／全书统计），补上"顺序推断判不出"（成员位置重合）这一分支，否则统计兜底成死代码；交叉验证不一致只标"顺序存疑"、不改判定。按 D30 排除带**未驳回**误识别标签的框（已驳回的视同普通框参与，D28／D30／D32 闭环），旋转框不参与；误聚组按 D33d（85%）默认不勾选。合并块按 D33／D40 契约重建：`text` 逐行展开（可切按块分段）、`lines` 同序、`rich_text` 重建（成员全无注解时留空＝按合并后译文重排；有注解时按阅读顺序把各成员文档插成一份、再整段统一到样式来源块的字体）、样式取页块列表中索引最小的成员、`tags` 取并集（子类型并集；`reviewed` 只在成员**全部**已驳回时保留，不静默洗白）、`xyxy` 与 `_bounding_rect` 都取并集（留旧值会让按数据重建的视觉层落错位置）。写回走 `ui/batch_ops.py` 的 D40 五步与 D35 版本撤回；**前置对齐提前到规划与构建之前**（否则面板上未回写的编辑会被挡在新块之外）；取消发生在落版本之前，语义＝"什么都没发生"。审批界面按 D20／D23 随工作台 UI（D 组）落地，本模块已备齐其所需数据。

**测试：** 新增 `tests/test_batch_merge.py`（52 项：判据边界与连通分量、方向三级与顺序存疑、D30 排除、误聚标、合并块字段契约、写回顺序保持与 D40 验收判据、整批撤回、取消不留痕、截图 100% 比例与外扩遇邻框即停、缺图兜底）。

**涉及文件：** `ui/batch_merge.py`、`tests/test_batch_merge.py`、`AGENTS.md`、`docs/技术实现/泛用工作台_规划.md`、`docs/技术实现/泛用工作台_复核与拆分.md`、`docs/daily_log.md`

---

### 标签文档同步补记（误识别文本 + 审阅表态）

**问题/需求：** 上一提交（dc3521db）批次 A 加了「误识别文本」标签与 `reviewed` 表态，但拆分表 E 组的文档同步没做——`AI辅助功能_规划` §8.3 的标签类型清单与标签使用说明的「标签清单」都还停在 5 个标签。

**改动要点：** 两份文档补上该标签（程序专用、无 AI 动作出口、出口是工作台队列与一键批量删除、批量合并时被排除）与 `reviewed` 的语义（可逆、重跑 OCR 不复活、人工改原文即清）；标签使用说明新增一节写它的挂标落点、队列口径与「测什么」。

**涉及文件：** `docs/技术实现/AI辅助功能_规划.md`、`docs/基础速查/AI辅助标签体系使用说明.md`、`docs/daily_log.md`

---

### 泛用工作台批次 A/B/C1（提交 dc3521db）

**问题/需求：** [泛用工作台_规划](技术实现/泛用工作台_规划.md) 定稿后的头三批落地：问题清理任务族的数据层与批量基座，外加批任务「简单背景纯色修复」的执行体（工作台 UI 属批次 D，尚未接线，故本批全部产物目前只经程序调用与测试触达）。

**改动要点：** ① 标签数据层（批次 A）：新增程序专用标签「误识别文本」（四类子类型存同一记录），程序筛选器走 `modules/ocr/base.py::OCRBase` 的 postprocess hook 注册一次覆盖全部 OCR 模块并短路 `none_ocr`；块级审阅表态 `reviewed`（驳回后重跑 OCR 不复活）；人工改写原文清该块程序标签；程序专用标签不进人工打标途径。② 批量基础设施（批次 B）：`utils/batch_versions.py` 成为全仓唯一批量备份口（项目目录内 `.bt_batch_backup/`、版本轮转、LIFO、跨会话保留），`ui/batch_ops.py` 封装 D40 五步写回范式（验收＝`page_data_needs_sync` 为假）与事务外壳；查找替换的单槽快照归并进本机制。③ 批量简单背景修复（批次 C1）：`modules/inpaint/base.py::classify_simple` 单点判据 + `inpaint` 的 `only_simple` 模式（复杂块完全不动、不加载模型），`ui/batch_inpaint.py` 只读扫描 + 逐页覆盖。样本实测判据命中率 8.3%，根因是样本为逐行框、判据要求气泡级闭合轮廓，按「先保守落地、合并文本框后再复算」处理（复算流程记于 `docs/技术实现/泛用工作台_复核与拆分.md`）。

**涉及文件：** `utils/block_tags.py`、`utils/batch_versions.py`、`ui/batch_ops.py`、`ui/batch_inpaint.py`、`modules/inpaint/base.py`、`modules/ocr/base.py`、`ui/module_manager.py`、`ui/mainwindow.py`、`ui/global_search_widget.py`、`utils/config.py`、`tests/test_batch_versions.py`、`tests/test_batch_writeback.py`、`tests/test_batch_simple_inpaint.py`、`docs/技术实现/泛用工作台_规划.md`、`docs/技术实现/泛用工作台_复核与拆分.md`、`AGENTS.md`

---

### 规划文档失效引用修正（`tmp/` 临时脚本）

**问题/需求：** `scripts/verify.py` 的文档校验在 09-16 提交后转红：`docs/技术实现/泛用工作台_规划.md` 引用了 `tmp/analyze_merge2.py`，而 `tmp/` 被 `.gitignore` 排除（该脚本实测后已清理）。提交当时因文件恰好存在于本机而显示全绿，属潜伏的文档债。

**改动要点：** 该处改为「一次性分析脚本（`tmp/` 工作目录下）」的表述，保留口径说明并写明脚本未入版本控制、复算时按本节度量口径重造；不再出现可被解析为仓库相对路径的失效引用。

**涉及文件：** `docs/技术实现/泛用工作台_规划.md`、`docs/daily_log.md`

---

### 行拖拽锚点改为「卡片纵向居中于光标」

**问题/需求：** 右侧文本列表行拖拽起手时把被拖卡吸附到光标下方（堆顶贴光标＝光标咬住卡顶），实机用下来手感偏：往下拖时整卡吊在光标底下，贴到视口下缘时大半张卡已出视野。

**改动要点：** `ui/textedit_area.py::TextEditListScrollArea.begin_rows_drag` 的聚拢锚点加一个固定偏移 `_pile_grab_dy = -(堆顶卡高 // 2)`，`ui/textedit_area.py::TextEditListScrollArea._update_drag_frame` 的跟手补间目标同加该偏移——抓取卡与光标的相对位置拖拽全程恒定，而不只是首帧偏一下；多选折叠堆同一规则（堆顶卡居中、其余卡按 `PILE_PEEK` 阶梯落在其下）。让位判定仍只看光标 y 且不随锚点变化，故语义变为「卡片中心越过邻行中点即换位」，与视觉一致。副产物：按在卡片中部附近起手时目标 y 与当前 y 相同，`_move_card` 的「目标未变不重启」守卫直接跳过，起手不再有位移跳变。**取舍记录**：居中后光标贴近列表上缘时卡片会有半张伸到列表上边界之外（动画开启时被拖组提层到主窗口绘制、会盖在面板上方，关动画时在该边界被裁），这是居中本身的必然结果，暂不做顶部钳制。

**测试：** `tests/test_row_drag.py` 三处写死「堆顶 = 光标」的断言改为居中口径并补「卡片中心 == 光标」正向断言（`test_begin_pile_visible_and_rest_arranged`／`test_multi_select_gather_to_cursor`／`test_chase_follow_no_teleport`）；`test_chase_follow_no_teleport` 与 `test_chase_settle_durations_and_stagger` 原本把光标放在卡心，改动后聚拢不再产生位移、`_pos_anims` 为空，光标各挪开 40／60px 以重新覆盖跟手补间。24 项全过。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`、`docs/daily_log.md`

---

## 2026-09-14

### 行拖拽抓住缩放 + 提层防父边界裁切

**问题/需求：** 右侧文本列表行拖拽在既有位移动画基础上补「抓住时卡片轻微放大（1.1x）、放下还原」的抓取反馈；首版实测放大溢出被滚动区边缘裁平（用户截图：卡片左缘被切）。

**改动要点：** 缩放是纯绘制层效果——新增 `ui/textedit_area.py::_CardScaleEffect`（QGraphicsEffect 子类）：绘制走 DeviceCoordinates 快照（Qt drop-shadow 同款模式），resetTransform 后围绕源矩形顶中锚的设备坐标做 scale，顶中锚使堆顶咬住光标的抓取点不动；boundingRect 按最大倍率一次性外扩，动画只改 factor 无需重报几何。抓住时 140ms OutCubic 放大与聚拢飞行并行，松手/取消缩回与退应飞行并行，`animation_fps < 0` 时整个缩放不启用。裁切根因是 Qt「子控件画不出父控件边界」：卡片所在的 scrollContent/视口与之同宽，滚动层级内无解——修复为拖拽期间把被拖组提层到主窗口（`self.window()`），落账/取消时映射回内容坐标放回；焦点控件提层前记账、收尾恢复（reparent 会挤掉卡内输入框焦点），吞 hover 的 eventFilter 同步识别提层后的被拖组；提层与缩放同门控。初版动画实现（`_scale_to` 的 QVariantAnimation + 闭包）与拖拽区成引用循环，测试套件批量回收 area 时级联析构重入 Python 回调硬崩——重构为动画由效果自身驱动（`animate_to` + `Property(float)` 属性动画，动画以效果为父），回调链上零拖拽区引用，循环从根上消失；还原到位的摘除延迟到事件循环下一轮并校验效果身份（防析构级联重入/误删新效果）。`tests/test_row_drag.py` 修 chase_follow 断言为窗口坐标参考系，新增提层放回/焦点恢复/关动画不提层三组覆盖（19 项全过）。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`、`docs/daily_log.md`

---

### 抓住缩放两处修复：高 DPI 锚点单位混用 + PyQt 空壳包装器硬崩

**问题/需求：** 上一提交（4d980866）在 125% 缩放的机器上实测，抓住卡片放大后明显偏移、顶行发虚（同一提交在 100% 缩放的 Win10 上正常）。根因是锚点单位混用：`painter.deviceTransform().map()` 输出的是**设备像素**，而 `resetTransform()` 之后 painter 仍按**逻辑坐标**绘制（`sourcePixmap(DeviceCoordinates)` 的 offset 也是逻辑坐标 + 自带 `devicePixelRatio` 的位图，DPR 由 Qt 在绘制时施加）。误差 = (1 − 倍率) × (DPR − 1) × 卡片在窗口里的坐标，DPR=1 时恒等故 Win10 看不出；高 DPI 屏上越靠下/靠右偏得越多，且因外扩只在底部（顶部为 0）把缩放后的上缘推出裁剪区。另跑全量测试发现 **`verify --full` 在 main 上本来就硬崩**（exit 127、无 traceback，二分定位同一提交：父提交 3/3 全绿 / HEAD 4/4 全崩）：`setGraphicsEffect` 后效果归 Qt 所有，Python 实例可以先没掉而 C++ 对象仍装在被拖卡上，此后任意一次重绘都会让 PyQt 用「没走过 `__init__` 的空壳」重建包装器，虚拟方法里读 `_max_factor` 抛 `AttributeError`，而虚拟回调里抛异常 PyQt 直接 qFatal。

**改动要点：** ① 锚点除掉 `deviceTransform` 自身的线性缩放（m11/m22 = 屏幕 DPR）换算回逻辑坐标，DPR=1 时逐像素等价于旧实现（Win10 手感不变）；DPR 1/1.25/1.5/2 实测顶中锚点钉在原位、底中精确落在 1.1 倍处，顶中角标不再被裁。② `ui/textedit_area.py::_CardScaleEffect` 加类属性兜底（`_max_factor` / `_factor` / `_anim`），空壳态退化为「不缩放、画原图」（与 `factor <= 1` 同路径，视觉上等于效果已释放），虚拟方法不再可能抛异常——这不只是测试问题，应用里残留效果被重绘同样会整体 abort。③ `tests/test_row_drag.py::CardScaleAnchorTest` 新增两组用例：DPR 1/1.25/1.5/2 用带 DPR 的 QImage 离屏直渲断言锚点（离屏屏幕本身 DPR=1，直接 grab 看不出来）；空壳实例（真 C++ 对象 + 清空 `__dict__`）虚拟方法不抛。两组都反向验证过「改回旧写法必红」。诊断手法存档：崩点无 traceback 是 pytest 的 fd 捕获吞掉了致命输出，用外挂 `sys.excepthook`（写文件、不经 fd）才抓到 `AttributeError`，再以 `weakref` 探针确认「Python 实例先回收、C++ 仍活着」的时序。`verify.py --full` 恢复全绿（843 passed，3 次连跑稳定）。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`、`docs/daily_log.md`

---

### 行拖拽动画优化（遮罩淡入淡出弃用）

**问题/需求：** 抓住缩放落地后收拾动画手感，前提是**性能优先、不引入投影/模糊**：跟手发飘、落点指示框瞬移、松手瞬间"一亮一暗"、多选堆叠三张一起飞、贴边自动滚动猛启猛停。

**改动要点：** ① 落点指示框交给 `_move_card` 走同一条补间（跨行时跟着行滑过去，不再瞬移到目标槽位而行还在半路）。② `ui/textedit_area.py::_move_card` 加 `duration` 参数：跟手 `CHASE_MS=90`、落位 `SETTLE_MS=140`，并把「目标未变不重启」守卫从跟手路径内联集中进 `_move_card`（高频鼠标事件与连续跨行不再平白新建动画对象）。③ 多选堆叠逐卡落后 `STAGGER_MS=14`：聚拢（`begin_rows_drag`）与展开（`_settle_to_layout` 的 `unstack`）都按 rank 递增时长，读起来是"一张张码下去"。④ 抓取加「按下-弹起」：`_CardScaleEffect.animate_to` 支持 `dip`/`dip_at` 关键值（先缩到 `SCALE_PRESS=0.97` 再弹到 1.1），`draw()` 判据由 `factor <= 1.0` 改 `abs(factor − 1.0) <= 1e-3` 以支持 factor<1；还原 `SCALE_OUT_MS=110` 略快于落位。**约束记录**：`boundingRectFor` 的外扩余量只按 `GRAB_SCALE` 给足，日后改用带回弹过冲的曲线必须同步放大余量，否则两侧被裁。⑤ 自动滚动：速度向目标渐变（`AUTOSCROLL_RAMP=0.3`，离开边缘用 0.55 强衰减收尾）、亚像素累加步进（边缘不再整像素跳步）、定时器间隔改 `_frame_interval()`（沿用 `ui/pie_menu.py::_anim_interval` / `ui/configpanel.py::_scroll_interval` 的 animation_fps/刷新率惯例）。**遮罩淡入淡出（本期唯一被放弃项）**：两版实现——`QPropertyAnimation` 驱动 alpha、area 定时器推 alpha + 淡完进退休名单延迟退役——都引出罕见硬崩（全量 pytest 1/5、`-v` 1/4、standalone 最高 9/12；唯一抓到的栈指向 `ui/textedit_area.py::_DragGapFrame.paintEvent` 的 `drawRoundedRect` access violation），撤掉后全量 0/8、`-v` 0/4、standalone 0/10；`_DragDim` 保留自绘（不逐帧改 QSS、不挂图形效果）但 alpha 固定，视觉与改前一致。**排查手法存档**：standalone 的崩静默（`-X faulthandler` 不落盘 = qFatal 家族），pytest 自带 faulthandler 反而把这类 heisenbug"治好"、拿不到栈；判稳必须两边都收数字。性能实测不升反微降（20 行卡片、400 次重定向、60 帧整区渲染：1.63 vs 改前 1.74 ms/帧，动画对象 419 vs 426）。测试新增 `tests/test_row_drag.py::RowDragTest` 的指示框跟滑、跟手/落位时长与逐卡落后、`CardScaleAnchorTest::test_scale_below_one_shrinks_toward_anchor`（factor<1 渲染路径）各一组。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`、`docs/技术实现/泛用工作台_规划.md`、`docs/daily_log.md`

---

## 2026-09-13

### LLM profile 选取链路修复：全局激活项 + 统一解析层

**问题/需求：** 实测 AI 辅助框级动作时两个报错同源——OCR 校正一直提示「No API key configured for vision profile: OpenAI」，重译抛 `ConnectionError: No available API key`。根因是所有 profile 选择器的空值回退都写死「取列表第一项」，而 `SAMPLE_PROFILES` 第一项是没配 key 的 OpenAI；用户在模型管理里填好 key 的 profile 没有任何机制传导到那四个消费点（翻译器 / llm_ocr / LLMInpaint / 术语工作台），于是「配了却不用」。

**改动要点：** 新增 `utils/profile_manager.py` 的选取解析层——`profile_is_usable`（有 host + 模型名，且 key 非空非占位或本地端点）、`get_default_profile_name` / `set_default_profile`（新配置字段 `utils/config.py::ModuleConfig.default_profile`）、`resolve_profile`（模块显式选的可用项 → 全局激活的 → 候选池第一个可用项）、`heal_profile_selector`（选择器归位，启动自愈，下拉框显示的永远是实际在用的）、`profile_usage_hint`（报错点名缺哪个字段）。四个消费点的「取列表第一项」全部替换；报错信息带 profile 名与缺失字段。模型管理页卡片头部加「使用 / 使用中」按钮标出并切换当前激活的 profile；框级 OCR 校正的 profile 取法从「翻译器的 active profile」改为「OCR 模块选定的 → 全局激活的 → 第一个可用视觉 profile」，并在无可用项时前置引导而非发请求等报错。注意 `merge_config_module_params` 会让 `pcfg` 与模块类参数共用同一份内层 dict，归位结果随常规保存落盘，不需要另行回写。

**涉及文件：** `utils/profile_manager.py`、`utils/config.py`、`modules/translators/trans_llm_api.py`、`modules/translators/trans_agent.py`、`modules/ocr/ocr_llm_api.py`、`modules/inpaint/inpaint_llm.py`、`ui/module_manager.py`、`ui/llm_profile_cards.py`、`ui/mainwindow.py`、`ui/glossary_agent_panel.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`AGENTS.md`、`docs/技术实现/AI辅助功能_规划.md`、`docs/基础速查/AI辅助标签体系使用说明.md`、`docs/daily_log.md`

---

### 行拖拽失焦不取消修复：放行 WM_KILLFOCUS + applicationStateChanged 主路径

**问题/需求：** 行拖拽中触发截图等外部窗口接管前台时，拖拽组冻在原地仍响应滚轮，回主窗口点击会误落账；上游反向移植版（PR #1329）行为正确，fork 第一轮事件/信号修复实机均无效。

**改动要点：** 真因在 `ui/framelesswindow/fw_qt6/win_frameless_window.py` 的 nativeEvent：`WM_KILLFOCUS` 分支自建仓起无条件 `return True` 吞掉 Qt 的失焦处理，`applicationStateChanged`/`ApplicationDeactivate` 从此失灵（2026-08-18 饼菜单「事件不可靠」教训实为误诊此坑）。修复=清理边框强调色后放行失焦消息；`ui/textedit_area.py::TextEditListScrollArea` 失活取消改以 `applicationStateChanged` 信号为主路径、eventFilter 分支降为兜底；`tests/test_row_drag.py` 失焦用例改信号驱动并补兜底分支直发验证（17 项全过 + verify 全绿）。上游 dev 同文件焦点分支从不 return True、无此坑，PR 无需补提交。

**涉及文件：** `ui/framelesswindow/fw_qt6/win_frameless_window.py`、`ui/textedit_area.py`、`tests/test_row_drag.py`、`docs/daily_log.md`

---

### AI 辅助标签体系批次 A–D 实施

**问题/需求：** 落实 [规划文档](../技术实现/AI辅助功能_规划.md) 的批次 A–D——把「人工做着麻烦、全交给 AI 又不靠谱」的校对工作，做成「程序自动筛选 → 人工打标定靶 → AI 定点单轮辅助 → 人工确认写回」的闭环；标签同时充当批量翻译的逐块指令载体。

**改动要点：**

- **批次 A（数据地基）**：新增 `utils/block_tags.py` 声明式标签注册表（5 类：OCR 置信度低 / 手写体 / 语气拟声词 / 译文迷惑 / 译文润色，分「疑点」「指示」两类，含进 prompt 的指令文案）+ 块标签读写 + 「OCR 置信度低」自动挂标；`utils/textblock.py::TextBlock` 加 `tags` 声明字段，随项目 JSON 保存，旧项目靠字段默认值兼容。两个本地 OCR 模块把原本算完即弃的分数透传出来（`modules/ocr/ocr_onnx.py` 的行 score、`modules/ocr/mit48px_ctc.py` 的逐字符 logprob 均值），取**最差行**聚合成块级分数、阈值 0.6 自动挂标，分数存进标签条目不喂给模型。画布徽标 `ui/textitem.py::_TagBadgeItem` 复刻序号徽标模式（多标签字形连排、选中变色、渲染导出前与序号徽标同一路径隐藏）。
- **批次 B（交互入口）**：选中跟随工具栏 `ui/tag_toolbar.py::TagToolbar`（紧凑栏 + 展开面板，按块的标签状态切换「打标 / 处理」钮）+ 显隐开关 `pcfg.show_tag_toolbar` 四件套；右键与饼菜单打标命令（`ui/context_menu_config.py`）、`1`–`5` 标签快捷键。
- **批次 C（框级 AI 动作）**：`utils/block_actions.py` 动作注册表（OCR 校正 / 重译，无工具单轮、代码预组装上下文）+ 执行器 `ui/block_action_runner.py::BlockActionRunner`（线程 + 取消）+ 就地确认卡 `ui/block_action_card.py::BlockActionCard` + 写回 `ui/textedit_commands.py::ApplyBlockTextCommand`（进全局撤销栈，疑点标签随应用消除）；处理中可取消、切页自动取消并提示。
- **批次 D（管线与跳转）**：指示标签进批量翻译 prompt（`modules/translators/agent/prompts.py::build_user_task_message`）；`ui/mainwindow.py::jump_to_tagged_block` 提供全书 `E`/`Q` 带标签块跳转。

**涉及文件：** `utils/block_tags.py`、`utils/block_actions.py`、`utils/textblock.py`、`utils/config.py`、`ui/tag_toolbar.py`、`ui/block_action_runner.py`、`ui/block_action_card.py`、`ui/textitem.py`、`ui/textedit_commands.py`、`ui/scenetext_manager.py`、`ui/canvas.py`、`ui/context_menu_config.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`modules/ocr/ocr_onnx.py`、`modules/ocr/mit48px_ctc.py`、`modules/translators/base.py`、`modules/translators/trans_agent.py`、`modules/translators/agent/prompts.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`config/stylesheet.css`、`tests/test_block_tags.py`、`tests/test_block_actions.py`、`tests/test_tag_toolbar.py`、`AGENTS.md`、`docs/基础速查/AI辅助标签体系使用说明.md`

---

### 框级动作两轮改进：逐行切图重识别 + 数据层一致性 + 补充要求重跑

**问题/需求：** 实测暴露两个痛点。① OCR 校正看着只是「把整块图重发一遍让多模态模型再识一次」，对模型而言输入结构还不如 OCR 模型自己的（轴对齐并集包围盒 vs 逐行透视矫正裁图），手写体上甚至更不可靠；② 选中**合并后**的块点重译，结果来自合并前的第一个子块，且卡片只给一个输入框，像个黑箱版单区翻译。用户诊断出 ② 的根因并拍板：不改存储机制（历史可回溯要求合并只在画布与渲染层生效），动作前把数据层按画布修一次即可。

**改动要点：**

- **数据一致性前置**：新增 `utils/block_actions.py::page_data_needs_sync`（块数 + 对象同一性双判据）与 `ui/mainwindow.py::_sync_block_data`，框级动作发起前若画布与 `proj.current_block_list()` 脱节就重建一次——合并块动作读到的原文从「合并前第一个子块」变为完整合并文本，兄弟类「worker 读到半更新数据」问题一并消除。
- **OCR 校正改逐行**：`utils/block_actions.py::stitch_line_crops` 逐行取透视矫正裁图、统一缩放到目标行高后拼成一张竖排图（保留行序），配行数契约 prompt（`build_ocr_fix_line_messages` 要求「恰好 N 行、正确的行照抄、不加编号引号」）；回包用 `parse_ocr_fix_reply` 去编号、校验行数，对不上就退回整块草稿。
- **主线程组装载荷**：`build_ocr_fix_payload` 在主线程完成切图 + base64 + messages 组装（`OcrFixPayload`），worker 只负责发请求；`ui/block_action_runner.py::BlockActionRunner` 相应改为接收预构建载荷。
- **卡片升级**：`ui/block_action_card.py::BlockActionCard` 增加切图预览（可点开放大）、逐行可勾选核对（取消勾选保留原行）、忙碌中也显示已备好的输入预览、错误态保留编辑器允许手改后应用，以及「补充要求」重跑（`BlockActionRunner` + `modules/translators/trans_agent.py::translate_with_context` 的 `hint` 参数，经 prompt 末尾追加）。
- **测试/文档**：`tests/test_block_actions.py` 扩到 27 例（切图、载荷、行解析、同步判据），`tests/test_tag_toolbar.py` 卡片用例扩到 11 例并修掉 `pcfg` 单例被前置测试文件污染的顺序依赖；使用说明与 `AGENTS.md` 同步。

**涉及文件：** `utils/block_actions.py`、`ui/block_action_runner.py`、`ui/block_action_card.py`、`ui/mainwindow.py`、`modules/translators/trans_agent.py`、`modules/translators/agent/prompts.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_block_actions.py`、`tests/test_tag_toolbar.py`、`AGENTS.md`、`docs/技术实现/AI辅助功能_规划.md`、`docs/基础速查/AI辅助标签体系使用说明.md`

---

### AI 辅助标签体系结单，标注为长期维护优化类型

**问题/需求：** 用户实机验收（「整体来说勉强符合我的整体预期」，后续在使用中挑痛点优化，例如通过工程化或提示词让 AI 被动/主动选择性地筛选要读取的数据——这类打磨急不得），拍板结单，并要求把本模块标注为需长期维护优化的类型。

**改动要点：** 规划文档状态行改为「批次 A–D 已实施，2026-09-13 结单，本模块按需长期维护优化对待」，第 9 节未定项按结单落点逐条收敛，新增第 11 节「长期维护与优化」作为**唯一**余项清单（方向一：AI 读取数据的范围控制——被动筛选与受控只读工具面两种形态；方向二：批次 E 的批量编排与全局扫描建议队列；方向三：阈值入口与分档、多选批量打标与行级标签、交付门禁、管线消费可视化等长尾；另列「已明确不做」清单防重提）。使用说明文档状态行与「已知边界」同步标注长期维护语义并指向第 11 节。按 3 天保留惯例清理 daily_log 中 2026-09-10 及更早条目（历史在 git 中可查）。

**涉及文件：** `docs/技术实现/AI辅助功能_规划.md`、`docs/基础速查/AI辅助标签体系使用说明.md`、`docs/daily_log.md`

---

### 三则小修：PS 路径行两行布局 / 开新项目清撤销历史 / 一键精简文案定位

**问题/需求：** 用户实测报的三则小毛病——①配置页外部编辑器「Photoshop 路径」标签超出 `ConfigFormRow` 固定标签宽被裁剪，且路径本身行内显示空间不足；②打开新项目后历史面板仍显示旧项目的撤销记录（根因更深：`openDir`/`openJsonProj` 从未清栈，旧项目命令锚定旧 blk 残留可被误撤销）；③「一键精简」实为面向中文字体命名混乱的功能（字重后缀家族 + 中英双名 + 简繁变体），文案未点明定位。

**改动要点：** ①PS 路径行改两行布局——标签 + ?说明一行，整宽输入框 + 浏览按钮一行；②两条开项目路径在加载后调用 `canvas.clear_undostack(update_saved_step=True)`，清栈经栈信号自动刷新文本侧/修复区历史面板；③精简按钮 tooltip 改短句「隐藏同一字体的重复命名变体（适用于中文字体）」，源串控制长度避免其他语言翻译超长，功能行为不变（守卫本就防误伤，不加语种门槛）。

**涉及文件：** `ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/daily_log.md`

---

## 2026-09-12

### Tools 下拉栏软键盘项改勾选态并单独分组

**问题/需求：** 软键盘已是"功能开关"响应类型（窄栏图标勾选态驱动），但在 Tools 下拉栏里仍以普通触发项混在文字/样式工具组中，语义不符。

**改动要点：** `ui/mainwindowbars.py` 的 Soft Keyboard 动作 `setCheckable(True)`，从文字/样式组移出，菜单末尾以分隔线单独分组（视图型开关与触发式工具区分）；动作引用存为 `ui/mainwindowbars.py::quickSymbolAction`。`ui/mainwindow.py` 接线双向同步：启动时按 `symbol_launcher.isChecked()` 对齐初始勾选，此后窄栏图标开合经 `toggled` 信号回写菜单勾选态；菜单点击仍走 `toggle_symbol_dock` 原路径。

**涉及文件：** `ui/mainwindowbars.py`、`ui/mainwindow.py`、`docs/daily_log.md`

---

### 行拖拽交互重构：抓取式真身堆叠 + 实时让位 + 追随/退应动画

**问题/需求：** 原生 QDrag 拖拽可视化差（凭输入框上下边缘 hover 样式判断落点）、拖拽中误触发输入框 hover、多选（尤其隔行选）时组内行渲染相互裁剪；用户期望"实时让位"的列表拖拽，状态只在松手后落账。

**改动要点：**

- **抓取式拖拽**：鼠标抓取在视口（`viewport().grabMouse()`）替代 QDrag，hover 误亮从根上隔离（app 级 eventFilter 兜底吞列表内 hover）；`ui/scenetext_manager.py` 的 `drag_move`/`pw_drop` 旧信号接线移除。
- **实时让位**：拖拽中各行移出布局手动定位，落点槽位（gap）按"邻行中点穿越"规则实时移动（基于目标坐标计算避免与动画反馈振荡，while 循环吸收快拖跨行）；块顺序只在松手时经 `rearrange_blks` 既有命令链落账。
- **真身堆叠 + 变暗遮罩**：被拖组保持原生全分辨率渲染（不再抓快照合成半透明幽灵），多选折叠成堆（第 i 张卡下沉 `ui/textedit_area.py::TextEditListScrollArea.PILE_PEEK` 阶梯、后位卡在上露各卡徽标），聚拢锚点=光标位置，gap 槽高取折叠后堆高；非拖拽内容盖约 15% 变暗遮罩（`DIM_ALPHA`）。
- **追随 + 退应动画**：拖拽组永远朝光标做 140ms OutCubic 补间、鼠标移动只重定目标（聚拢动画天然可见、任何时刻不瞬移；目标未变不重启防抖）；松手/取消时快照→布局同步激活落账（消费端 ghost 自动跳过、数据零延迟窗口）→ 行搬回快照位置整体退应飞向终态（`_settle_to_layout`，落账与取消共用）；快速连拖前停净上一局残留动画。
- **卡片样式消重影**：堆叠重影根因是选中/悬停底色为半透明 `@accentPrimary20`——`ui/misc.py::_derive_solid_tints` 在主题解析时派生预混不透明变量 `@accentPrimary20Solid`，卡片选中/悬停底色改实底（视觉一致不透底，自定义主题自动生效）；卡片圆角 4→6px、选中态加强调描边；落点指示框改 `_DragGapFrame` 自绘（3px 加粗虚线 + 半透明强调底，替代 QSS 细虚线）。
- **测试**：新增 `tests/test_row_drag.py` 16 项（聚拢锚点/让位中点判定/落账置换/Esc 取消/追随不瞬移/退应动画/hover 吞噬/自动滚动等）。

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`、`config/stylesheet.css`、`ui/misc.py`、`tests/test_row_drag.py`（新）、`docs/daily_log.md`

---

### 软键盘焦点切换随迁动画

**问题/需求：** 软键盘浮层在切换文本框焦点时重播淡入，观感突兀；期望已在其它编辑器旁显示时改为滑动随迁。

**改动要点：** `ui/quick_symbol_panel.py::SymbolFloatPanel.open_at_editor` 新增分支：已在该编辑器旁不重播动画；在其它编辑器旁（含收起途中）取消进行中收起动画、由 `_move_animated` 从当前位置滑向新锚点（`pcfg.animation_fps` 门控），不重播淡入。

**涉及文件：** `ui/quick_symbol_panel.py`、`docs/daily_log.md`

---

### 底部栏翻译器模型子菜单 + Tools 下拉栏重排 + AI 辅助功能规划文档入库

**问题/需求：** 底部栏 Translator 按钮菜单缺少模型快切入口（上游有但为三类 profile 的 555 行子系统，不合 fork 体量）；「不常用功能工具箱收纳」方向经用户复议废弃——功能不多，只需对 Tools 下拉菜单做一次重排版；另把多轮讨论定稿的 AI 辅助功能规划文档（框级打标 + 标签体系）入库。

**改动要点：**

- **模型子菜单**：`ui/module_tool_button.py::ModuleSelectionWidget` 新增 `model_menu_provider` 回调属性（菜单每次打开重建时取数，保证反映他处改动）+ `model_changed` 信号 + 「模型」子菜单；`ui/mainwindow.py::_trans_model_menu_data` 读活动 profile 的 `model_options`（无清单不显示子菜单），`on_trans_model_changed` 经 `remember_model_option`/`save_all_profiles` 写回 `profile.model` 即改即落盘。翻译器逐请求重读 profile，切换下一次翻译请求即生效；Profile 卡片页 showEvent 重读磁盘覆盖回显。有意裁剪：只做翻译器模型切换，不搬 vision/image 模态与按钮文字显示模型名。
- **Tools 下拉栏重排**：`ui/mainwindowbars.py` 菜单两组化——文字/样式（样式管理器、Quick Symbol、高级对齐、整理换行）+ 页面/图像（区域合并、路径重排、无字图配对）；整理换行从「导出/批量处理」、无字图配对从「外部工具」各自归位，「外部工具」分组取消。方向改定与最终分组记录在 `docs/技术实现/不常用功能工具箱_规划.md`（已重写为「整理工具下拉栏」）。
- **AI 辅助功能规划文档**：新增 `docs/技术实现/AI辅助功能_规划.md`（六轮讨论定稿：§7 框级打标触发、§8 疑点/指示标签体系与五标签定稿、OCR 置信度透传调研、实施批次 A–D），状态「规划定稿，可实施」。

**涉及文件：** `ui/module_tool_button.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/不常用功能工具箱_规划.md`、`docs/技术实现/AI辅助功能_规划.md`

---

### 快捷符号改版为软键盘（贴框跟随浮层 + 焦点驱动弹收 + 撤销链修复）

**问题/需求：** 原 Quick Symbol 是对话框形态、需先选中文本框才能插入，罗马字输入与目标框割裂；实测还发现插入不经撤销历史、且可能透传出画布意外渲染。经三轮迭代定稿：窄栏图标只作功能开关，聚焦编辑器自动弹出软键盘。

**改动要点：**

- **功能开关 + 焦点驱动弹收**：窄栏新图标（`icons/rail_symbol.svg`）勾选态=开关（`pcfg.symbol_keyboard_enabled`）；焦点进入触发范围编辑器弹出、离开即收（`ui/text_panel.py::_sync_symbol_keyboard` 编排，浮层不写开合记忆、不占 dock 互斥名额）。触发范围 `pcfg.symbol_keyboard_source_only` 默认仅原文框，设置页可选两侧。
- **软键盘面板**：新增 `ui/quick_symbol_panel.py`——假名/符号两页键区（清音+浊音/小书き/记号，全内容滚动不裁剪）、罗马字转假名输入行（wapuro 式贪心最长匹配 + 促音/拨音/片假名切换）、全角空格与退格；键区全部 NoFocus 不抢编辑器焦点，页签与假名模式按钮带 checked 激活样式。原 `ui/quick_symbol_dialog.py` 删除（已登记 deprecated）。
- **贴框跟随浮层**：`SymbolFloatPanel`（继承 `ui/custom_widget/float_drop_panel.py::FloatDropPanel`）锚定聚焦编辑器——默认贴编辑器左缘向画布展开、画布过窄改贴右缘并夹回宿主；换编辑器随迁，编辑器滚动/改尺寸与宿主缩放自动重锚；显隐带 16px 滑动过渡（`pcfg.animation_fps` 门控，可打断反向）。`FloatDropPanel` 构造期锚点无父级时的 parent 环死循环已加守卫。
- **撤销/透传修复（根因三连）**：①`ui/textedit_area.py::insert_external_text` 显式登记与文档信号链重复致双登记，删多余调用；②`ui/canvas.py::note_source_edit` 原文无会话时降级忽略，改为重建 before 开会话（纯插入精确可逆）；③插入/退格前 `editor.setFocus()` 让 focus_in 重开会话并使光标可见。
- **文档与测试**：i18n 全量同步（新词条 + 上下文标注）；`tests/test_configpanel_node3.py` 迁移、`tests/test_rail_docks.py` 桩补齐；`docs/项目概述.md`、`docs/基础速查/i18n.md`、`docs/技术实现/反向移植_规范.md`、上游移植完成记录同步。

**涉及文件：** `ui/quick_symbol_panel.py`、`ui/quick_symbol_dialog.py`（删）、`ui/text_panel.py`、`ui/textedit_area.py`、`ui/canvas.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/scenetext_manager.py`、`ui/custom_widget/float_drop_panel.py`、`ui/custom_widget/rail_dock_panel.py`、`utils/config.py`、`icons/rail_symbol.svg`、`icons/text-effect-stroke.svg`、`config/stylesheet.css`、`scripts/audit_registry.json`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_configpanel_node3.py`、`tests/test_rail_docks.py`、`docs/`（4 处）

---

## 2026-09-11

### 分支清理 + 多代理协作约定（当日立规并撤回）（WorkBuddy）

**问题/需求：** 清理仓库中不再使用的分支（`dev`/`master`/`vision_context` 三个上游来源分支须保留）。

**改动要点：**

- **远端分支清理**：`origin` 上 4 个 `feat/backport-*`（当初给上游提 PR 用）全部删除，`origin` 只剩 `main`。依据：PR #1279（快捷键）/ #1280（序号徽标）/ #1281（路径重排）closed 但上游做了修改适配、未直接合并，#1282（点击空白弃选）已合并；4 项功能代码均确认存在于 `main`。上游 `upstream-tmp` 保留 `dev`/`master`/`vision_context` 并更新到上游最新（`dev` → `2653fc41`），`vertical_text_layout` 上游远端早已删除、本地残留引用清除。取上游引用统一加 `--no-tags`。
- **多代理协作约定（当日立规并撤回）**：当日上午在 `AGENTS.md` 新增「多代理协作」一节与一条 Git 规则，并在用户目录建立跨代理共享记忆区 `~/.agent-memory/`（按项目名分目录，**不入库**）。**当晚撤回该方案**：`~/.agent-memory/` 整目录删除，`AGENTS.md`、`docs/daily_log.md` 中指向它的引用与那条 Git 规则一并清除，改用「代码写入、提交与推送以 zcode 为主，WorkBuddy 只承担规划与零散杂活」的分工，只保留「同一时间一个代理有写权」「交替期间只暂存自己的文件」「私人笔记不入库」三条协作规则。

**涉及文件：** `AGENTS.md`、`docs/daily_log.md`

---

### 设置面板死布局清理 + Pipeline 页引擎切换下拉回归

**问题/需求：** 承接 2026-09-10 普查的挂账：`ui/configpanel.py` 里一组死布局类被展示台当活控件展示、牵动覆盖门禁，用户拍板另起一批清理；另拍板把合并 Pipeline 页的引擎切换下拉加回来（原「等用户反馈」的条件触发项兑现），设置面板「上次所在页」记忆不做（页面少）。已实机验收。

**改动要点：**

- **死布局清理（全批 +105/−495 行）**：删 `ui/configpanel.py::ConfigBlock`、`ui/configpanel.py::ConfigContent`（活代码零实例化，仅展示台引用）、configpanel 局部版 `combobox_with_label` / `checkbox_with_label`（唯一调用方是死掉的 ConfigBlock）、`ConfigPanel.addConfigBlock` shim 与 `dlConfigPanel` 死变量。**`_DeadBlock`/`_DeadLayout` 是活的**（General 四页构建器经 `generalConfigPanel.addGroupedBlock` 注册分页），保留；`_scroll_interval` 有 NavList 滚动动画在用，保留；`Tuple`/`QLabel` 两个失去用途的导入一并清。
- **QSS 死规则**：`config/stylesheet.css` 删约 70 行 `ConfigContent *` 下划线风格规则（分页改造后无任何控件再带该祖先类）与 `QScrollArea#ConfigContent` 边框豁免；`ConfigLineEdit`/`ConfigTextEdit`/`ConfigComboBox` 选择器列表里的 `ConfigContent` 前缀分支随之缩减。
- **展示台同步**：`scripts/style_showcase.py` 删 ConfigBlock/ConfigContent 两工厂与两行展示（`EXCLUDED` 里 `combobox_with_label` 指 `ui/custom_widget` 的活函数，保留）；覆盖门禁照常通过（只比对 `ui/custom_widget` 导出）。
- **引擎切换下拉回归**：`_build_pipeline_page` 不再隐藏选择行，四个标签顶栏恢复 `[模块名][引擎下拉][?]`，备注按钮改锚定 `module_combobox` 之后；在设置页切换即真正换引擎（`ui/module_manager.py` 的 `set*` 槽镜像回底部栏）。`ui/module_parse_widgets.py` 的 `engine_label`/`_refresh_engine_label`/`set_module_selector_visible` 整套删除；检测页 note 文案改「直接在上方的下拉框切换引擎」。
- **画布侧改镜像下拉（关键设计）**：原实现 showEvent 时把共享 `module_combobox` reparent 借走且不还——恢复设置页下拉会让修复标签开天窗。改为 `ui/module_parse_widgets.py::ModuleConfigParseWidget.create_mirror_selector`：画布 `ui/drawingpanel.py::InpaintPanel` / `RectPanel` 各持独立镜像（真值源不变，show 时 `sync_items` 重建条目与 tooltip、源变更单向跟随、镜像 `activated` 回流 `setCurrentText` 走正常切换链），reparent 借用 hack 与 `hideEvent` 全删，高度锁定移入镜像构造。与底部栏「各持一份、信号同步」同模式。
- **i18n/测试**：ts 清 3 孤儿（DL Module / Engine: %1 / 旧检测 note）+ 新 note 手填译文，qm 重编；`tests/test_pipeline_page_merge.py` 改断言选择行可见 + 新增镜像下拉行为测试（条目同步/文本跟随/activated 回流）。**坑**：裸 `ConfigPanel()` 的阶段面板没走 `addModulesParamWidgets`，`currentTextChanged → on_module_changed` 连接不存在，引擎切换信号链无法用 addItem 探测，故测镜像行为代替。`verify.py --full` 全绿。

**涉及文件：** `ui/configpanel.py`、`ui/module_parse_widgets.py`、`ui/drawingpanel.py`、`ui/overlay_slide.py`（清注释残留）、`scripts/style_showcase.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_pipeline_page_merge.py`、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`、`docs/基础速查/打包控件功能使用说明.md`
