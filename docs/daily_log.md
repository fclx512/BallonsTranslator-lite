# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-06-25

### 译文软换行整理工具

**问题/需求：** LLM 翻译被动模仿原文换行，往译文塞 `\n` 导致排版炸裂、撑破文本框。半自动校对流程需快速清理无意义换行：无引号整段压空格交排版器重排，`「」` 块内换行清空、块间单换行分隔（每句独占一行）。

**修复（右键菜单不显示 + 批量无效果）：**
1. 右键菜单看不到——根因：`ui/canvas.py` 的 `gv.setContextMenuPolicy(NoContextMenu)` 阻止 `contextMenuEvent` 传到 QGraphicsItem，`TextBlkItem.contextMenuEvent` 是死代码。项目所有画布右键菜单走 `mouseReleaseEvent` → `context_menu_requested` → `canvas.on_create_contextmenu`。改为在该画布级菜单的 Squeeze 项后加"整理换行"/"整理换行并收缩框"，仅对选中的横排块启用，经新增 `canvas.normalize_break_requested(bool)` 信号 → `SceneTextManager.on_normalize_break`（仿 `onSqueezeBlk`）；删除 textitem 里的死代码与 `normalize_break_requested` 信号、还原 `contextMenuEvent`。
2. 批量执行无效果——根因：当前页 live 文档里的换行只在 `QTextDocument`，`blk.translation` 是过时值（仅翻页/翻译前 `updateTextBlkList` 才同步）。批量前 `on_open_normalize_breaks_dialog` 缺刷新，`normalize_softbreaks` 读到无换行的旧值就跳过。打开对话框前补调 `self.st_manager.updateTextBlkList()`；`on_normalize_break` 同样先刷新再遍历选中块。

**改动：**
1. `utils/text_normalize.py`（新建）— 纯函数 `normalize_softbreaks`：按最外层 `「」` 分块，无引号/引号内换行压单空格，引号块之间 plain 段删换行紧贴、两相邻引号块间补单个 `\n`，嵌套取最外层配对，幂等。无 Qt 依赖。
2. `utils/text_normalize_test.py`（新建）— 10 条手动运行测试，全部 PASS。
3. `ui/textedit_commands.py` — 新增 `NormalizeBreaksCommand`：仿 `BatchFontformatCommand` 范式，`__init__` 时对当前页 live item 捕获旧 HTML/rect/fontformat，redo 写新文本（清 `rich_text` 交排版器）+ `set_fontformat` 刷新 + 可选 `squeezeBoundingRect`，undo 用 `setHtml` 全量还原 + 清 e_trans undo 栈；非当前页只写 `blk.translation`/`rich_text`。squeeze 并入命令保证一次 Ctrl+Z 全回退。
4. `ui/canvas.py` — 新增 `normalize_break_requested(bool)` 信号；`on_create_contextmenu` 在 Squeeze 项后加"整理换行"/"整理换行并收缩框"，仅对选中的横排块启用（无选中或全竖排则禁用）。
5. `ui/scenetext_manager.py` — 连接 `normalize_break_requested`，新增 `on_normalize_break(squeeze)`：先 `updateTextBlkList()` 刷新当前页 live 文档，再遍历选中横排块构造 `NormalizeBreaksCommand` 推栈（仿 `onSqueezeBlk`）；移除 `addTextBlkItem` 对死信号 `normalize_break_requested` 的连接。
6. `ui/mainwindowbars.py` — Tools 菜单加"批量整理换行…"动作（仿 Quick Symbol）。
7. `ui/mainwindow.py` — 连槽 `on_open_normalize_breaks_dialog`：无页时警告，否则先 `updateTextBlkList()` 刷新当前页再弹 `NormalizeBreaksDialog`，应用后 `QMessageBox` 报告处理数/跳过数。
8. `ui/textitem.py` — 还原 `contextMenuEvent` 为 `super()`（原自定义版是死代码），移除 `normalize_break_requested` 信号与 `QMenu` 导入。
9. `ui/normalize_breaks_dialog.py`（新建）— 批量对话框：全部页切换 + 页码复选框列表 + 可选自动收缩框，内置竖排跳过过滤。
10. `translate/zh_CN.ts` / `translate/zh_CN.qm` — 新增 `TextBlkItem`/`NormalizeBreaksDialog` 两个 context 及 `TitleBar`/`MainWindow` 新条目；qm 用 utf-8 编译，验证无 Latin-1 污染。

**涉及文件：** `utils/text_normalize.py`、`utils/text_normalize_test.py`、`ui/textedit_commands.py`、`ui/textitem.py`、`ui/scenetext_manager.py`、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`ui/normalize_breaks_dialog.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

### CTD 检测器选中后窗口无响应修复

**问题/需求：** 从下拉栏选中 CTD 检测器后主窗口卡死（终端无报错）。根因是 `_ensure_module_deps()` 在**主线程**调用了 `ModuleSpec.resolve()` → `importlib.import_module("modules.textdetector.detector_ctd")`，触发 torch、cv2、einops 等重型 C 扩展导入，Windows 下会堵塞事件循环数秒。

**改动：**
1. `ui/module_manager.py` — `_ensure_module_deps()` 对 `ModuleSpec` 使用 AST 扫描缓存的 `dependencies` / `download_file_list` 直接检查，不再在主线程 resolve/import 模块；仅当依赖确实缺失、需要弹出安装对话框时才懒加载。

**涉及文件：** `ui/module_manager.py`


### 设置面板样式残留修复：焦点框跟随 + 移除多余包裹框

**问题/需求：** 设置面板在 `11ecc36` 从卷轴式重构为分页后出现两处样式残留：(1) NavList 左侧代码绘制的蓝色焦点指示器只在「管线」旁显示，点其他导航项不跟随；(2) 分页内包裹设置项的 PanelGroupBox 框已不再需要，且被强制拉伸到设置窗口高度。另需审视分页排布并产出建议文档。

**改动：**
1. `ui/configpanel.py` — NavList 焦点指示器改由 `currentRowChanged` 信号驱动：移除 `setCurrentRow` 覆盖（Qt 用户点击走 `QItemSelectionModel` 不触发该覆盖，原路径失效），新增 `_on_row_changed(new_row)` 挂到 `currentRowChanged`，跳过不可选的标题/分隔项，以当前 `_indicator_y` 为动画起点平滑滑到新行（保留 `_sync_indicator` 懒初始化兜底）。
2. `ui/configpanel.py` — 分页内 group 去 PanelGroupBox 多余框：`models_group`、`_build_grouped_widget`、`_add_grouped_page` 三处构造点给 group 设 `setProperty("cfgPage", True)`（用 dynamic property 避免覆盖 `GroupDetect` 等 objectName 影响阶段色条选择器）；`_wrap_page` 给被包裹 content 设垂直 `Fixed` 策略防短页被拉满。
3. `config/stylesheet.css` — 追加 `PanelGroupBox[cfgPage="true"]` 选择器：去 1px 外框 + 背景 + 圆角，**保留左侧 3px 阶段色条**；四条 `PanelGroupBox#GroupXxx[cfgPage="true"]` 复合选择器覆盖 detect/ocr/inpaint/trans 阶段配色。快捷键编辑器内 PanelGroupBox 默认框不受影响。
4. `docs/设置面板排布建议.md` — 新建排布审视建议文档（管线页体量失衡、阶段配色不一致、环境页留白、Models 按钮宽度、NavList 标题交互五点，未改结构，列出供决策）。

**涉及文件：** `ui/configpanel.py`、`config/stylesheet.css`、`docs/设置面板排布建议.md`


### 旋转文本框跳过拖动吸附对齐

**问题/需求：** 有旋转角度的文本框（`angle != 0`）是定制型手动排布，不应该在拖动时触发吸附对齐逻辑，且旋转状态下计算对齐没有实际意义。

**改动：**
`ui/textitem.py` — `_apply_snap()` 开头早返（拖动块自身有角度不吸附），收集目标框时跳过 `child.angle != 0` 的旋转块。

**涉及文件：** `ui/textitem.py`

---

### 快速符号面板插入失焦修复

**问题/需求：** `QuickSymbolDialog` 插入符号时直接用 `cursor.insertText(symbol)`，但目标 `SourceTextEdit` 可能没有焦点，导致画布文本框看不到插入内容。

**改动：**
1. `ui/textedit_area.py` — 新增 `insert_external_text()` 方法，手动设置 change tracking 状态并调用 `handle_content_change()` 强制传播到画布
2. `ui/quick_symbol_dialog.py` — 对目标使用 `insert_external_text`（若可用），否则回退旧路径

**涉及文件：** `ui/textedit_area.py`、`ui/quick_symbol_dialog.py`

---

## 2026-06-26

### 译文软换行整理：无视觉反馈与批量窗口闪退修复

**问题/需求：** 右键菜单"整理换行"和批量对话框操作后无任何视觉反馈，批量窗口打开即崩。

**修复：**
1. `ui/textedit_commands.py` — `NormalizeBreaksCommand._first_redo` 初始值 `True` → `False`。根因：`QUndoStack.push()` 自动调用 `redo()`，但 `_first_redo=True` 跳过了首次调用，构造器只捕获旧状态不应用新文本，导致文本从未被修改。改为 `False` 让 push 时的 redo 正常执行 `_apply("new")` 写新文本 + 触发重绘。
2. `ui/normalize_breaks_dialog.py` — `self.tr("Page %1 — %2").arg(i).arg(pname)` → `.replace("%1", str(i)).replace("%2", pname)`。根因：PyQt6 的 `tr()` 返回 Python `str`，没有 `.arg()` 方法，其余文件均用 `.replace()` 模式。
3. `config/stylesheet.css` — 新增 `QMenu::item:disabled { color: @disabledForegroundColor; }`。根因：深色主题下 `QMenu::item` 自定义了样式但缺 `:disabled` 规则，全竖排选中时菜单项被禁用但外观无区分。

**涉及文件：** `ui/textedit_commands.py`、`ui/normalize_breaks_dialog.py`、`config/stylesheet.css`

---

### 多选右键翻译偶发报错 — 五项候选根因修复

**问题/需求：** 选中多个文本框后右键翻译（`run_blktrans`），偶发报错/行为异常。静态摸排产出 5 项候选根因，本次逐项修复。

**修正说明：** 根因 #1（翻译期间切页面致索引越界）和 #2（修复期间切页面致 numpy 切片越界）的前提是管线运行期间页面可切换——实际管线执行时右下进度提示框**禁用交互**，无法切页，此场景不成立。修复代码作为防御性保留，不影响正常路径。

**改动：**

1. **`ui/module_manager.py` — 精简 `finish_blktrans` 为单次 emit**（针对根因 #4）
   - 移除非末尾的两处冗余 `self.finish_blktrans.emit`（原 OCR 后 line 477 / 翻译后 line 493）
   - 保留末尾（现 line 535）唯一一次 finish emit，各阶段进度改由 `finish_blktrans_stage` 传递
   - **效果：** 消除 `on_blktrans_finished` 回调被多次触发的撤销栈污染，每种模式均只推 1 次 `RunBlkTransCommand`

2. **`ui/module_manager.py` — inpaint 循环异常保护**（针对根因 #3）
   - inpaint `for` 循环包裹 `try/except Exception`，异常时弹出错误对话框
   - **效果：** inpaint 抛异常时 finish_blktrans 仍正常发射，进度框不卡死

3. **`ui/mainwindow.py` — `on_blktrans_finished` 防御性守卫**（针对根因 #1）
   - `translateBlkitemList` 记录调用时的 `current_img` 页码
   - `on_blktrans_finished` 校验页码一致性 + `blk_ids`/`blk.idx` 全部在范围内
   - **效果：** 任何导致回调过时的路径（即便非交互导致）均被静默忽略

4. **`ui/drawing_commands.py` — `RunBlkTransCommand` numpy 切片防护**（针对根因 #2）
   - `__init__` 中的 `img_array[rect]` / `mask_array[rect]` 包裹 `try/except (IndexError, ValueError)`
   - **效果：** 页尺寸变更等极端场景下不因 numpy 切片越界崩溃

5. **`modules/translators/context_batch.py` — `_apply_cache` 子集索引修复**（针对根因 #5）
   - `_apply_cache` 通过 `id(blk)` 在 `self._proj.pages[page_key]` 中查找真实的页级 `bidx`，替代原先传入的子集局部索引
   - **效果：** 上下文翻译缓存残留时，选中子集块不再查错位缓存（译文不再回退为原文）

6. **`translate/zh_CN.ts` / `translate/zh_CN.qm`** — 新增 `ImgtransThread` 上下文 `"Inpaint Failed."` 条目，qm 编译 784 条无 Latin-1 污染

**涉及文件：** `ui/mainwindow.py`、`ui/drawing_commands.py`、`ui/module_manager.py`、`modules/translators/context_batch.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

**已验证：** 代码阅读验证通过；i18n_check 仅余预知的 orphan 假阳性与历史遗漏（`[Canvas]` 两条目、`[TextBlkItem]` 两条目、`_ShortcutRow` 批量条目）

---

### i18n 修复：中文 tr() source 改为英文

**问题/需求：** 06-25 新增的「整理换行」功能多处将中文直接作为 `self.tr("中文")` 参数，导致切英文界面时菜单项仍显中文；i18n_check 报 missing；`[TextBlkItem]` 旧代码的 ts 条目也需清理。

**改动：**
1. `ui/canvas.py` — `"整理换行"`→`"Normalize Breaks"`、`"整理换行并收缩框"`→`"Normalize Breaks and Shrink"`
2. `ui/mainwindowbars.py` — `"批量整理换行…"`→`"Batch Normalize Breaks…"`
3. `ui/normalize_breaks_dialog.py` — 7 处 `self.tr("中文")` 全部改为英文 source，其中 `"第"`/`"页"` 合并为 `"Page %1 — %2"` Qt arg 模式
4. `translate/zh_CN.ts` — **Canvas** +2 条、**TitleBar** 更新 source、**NormalizeBreaksDialog** 替换 7 条 source、**TextBlkItem** 删除 2 条过时条目
5. `translate/zh_CN.qm` — 重新编译，783 条无 Latin-1 污染

**涉及文件：** `ui/canvas.py`、`ui/mainwindowbars.py`、`ui/normalize_breaks_dialog.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

**已验证：** `i18n_check` — HARDCODED_CHINESE 无、MISSING 清零、orphan 59 条均为已知假阳性（变量调用/多行隐式拼接，运行时正常）

---

### Models 页样式实验 + ConfigNotePopup 备注浮层投产

**问题/需求：** 2026-06-26 建的 `ConfigSubBlock.note` + `ConfigNotePopup` 基础设施处于零消费者状态，需绑定首个实际用例。选 Models 页做样式试验。

**改动：**

1. `ui/configpanel.py` — Models 页重构：
   - 间距 `setSpacing(4)`→`8`
   - 新增加载/管理两组内部分组标题（`ConfigSubBlock` name-only）
   - Checkbox 改手动 `ConfigSubBlock` 构造（替代 `checkbox_with_label()`），启用 `note` 参数→? 按钮+弹出浮层
   - 按钮（Unload / Profiles）加 `name` + `note`，围入 Management 分组
   - `PanelGroupBox` 加 `objectName="GroupModels"` 为未来色条预留
   - import 补 `QGraphicsOpacityEffect`

2. **ConfigNotePopup 调试修复：**
   - `_show_note_popup` 中 popup 存为 `self._note_popup`（原局部变量被 GC 回收致点击无响应）
   - 移除 `destroyed.connect`（连点竞态：旧 popup GC→`_destroy_note_popup` 置 Null→新点击 AttributeError）
   - 构造器加 `setAutoFillBackground(True)`（FramelessWindowHint 下背景不自动填充，暗主题显黑方块）

3. `config/stylesheet.css` — 新增 `ConfigNotePopup` 规则（背景色+边框+圆角+padding）

4. `translate/zh_CN.ts` — ConfigPanel context 新增 8 条（4 分组标签 + 4 备注文本），qm 编译 798 条

**涉及文件：** `ui/configpanel.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

**已验证：** 离屏测试—类构造/导入/PageStack 6 页/导航全切/备注浮层弹出与快速连点均通过；i18n—0 missing。样式初版可用但需后续打磨。

---

### ConfigNotePopup 备注系统全量铺开

**问题/需求：** Models 页试验后，需将备注系统推广至所有设置项。28 个设置项需要备注文本、6 个无名称项需补 name 标签，全部需 i18n。

**改动：**

1. `ui/configpanel.py` — 辅助函数新增 `note` 参数：
   - `combobox_with_label()`、`checkbox_with_label()`、`_build_grouped_widget()` 加 `note` 透传
   - `_env_button()` 改接受 `name` + `note`

2. `ui/configpanel.py` — 28 项备注全覆盖：

   | 页 | 项 |
   |---|---|
   | Pipeline (4) | Detection / OCR / Inpaint / Translator 阶段说明 |
   | Project (5) | 启动 / 结果格式 / 自动匹配 / 质量 / 中间格式 |
   | Typesetting (7) | 默认字体 / 自动排版 / 大写 / 独立样式 / 标点位置 / 排除字体 / 最大字号 |
   | Interface (4) | 窗口适配 / 动画 / 快捷键 / 切换预设 |
   | Environment (4) | 网络 / 工具 / 诊断 / MCP |

3. `ui/configpanel.py` — 6 项补 name 标签：Font Exclusion、Toggle Preset、Network、Tools、Diagnostic、MCP Server（此前有按钮/输入框但无 name label）。

4. `translate/zh_CN.ts` — ConfigPanel context 新增 30 条 `<message>`（24 备注 + 6 名称），全部含中文翻译

5. `translate/zh_CN.qm` — 重新编译，798→828 条

**涉及文件：** `ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

**已验证：** `py_compile` 语法通过；`i18n_check` — 0 missing、0 hardcoded Chinese、59 条 orphan 全为已知假阳性（与改造前一致）
