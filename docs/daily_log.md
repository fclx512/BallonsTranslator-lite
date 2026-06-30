# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

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

## 2026-06-28

### JXL 重新激活

**问题/需求：** 上游 JXL 格式正常可用，本分支因 `pillow-jxl-plugin` 与 Pillow 11+ 不兼容而封存。现清理封存状态并锁定依赖版本。

**改动：**
1. `requirements.txt` / `config/requirements_core.txt` / `pyproject.toml` / `scripts/build_portable.py` — Pillow 约束 `>=11.0` → `>=10.0,<11`
2. `ui/configpanel.py` — JXL 恢复到结果格式（PNG/JPG/WEBP/JXL）和中间格式（PNG/JXL）选择器
3. `utils/io_utils.py` — `imwrite` JXL 路径加 try/except 失败回退 PNG；清理封存注释
4. `utils/proj_imgtrans.py` — 清除 JXL 封存注释
5. `docs/经验教训.md` — §4.1 更新为 ✅ 已重新激活

**涉及文件：** `requirements.txt`、`config/requirements_core.txt`、`pyproject.toml`、`scripts/build_portable.py`、`ui/configpanel.py`、`utils/io_utils.py`、`utils/proj_imgtrans.py`、`docs/经验教训.md`

---

### 画布区右侧文本按钮移出字体折叠区

**问题/需求：** `foldTextBtn`（折叠文本框）、`sourceBtn`（原文）、`transBtn`（译文）被包在 `CollapsibleSection`（字体样式）内，折叠字体样式时一并收纳。希望在收纳字体样式时这三个按钮仍然显示。

**改动：**
1. `ui/text_panel.py` — `FontFormatPanel` 中移除三个按钮的定义和 hl4 布局，清理不再使用的 import
2. `ui/scenetext_manager.py` — `TextPanel` 新增三个按钮，置于 `format_section` 与 `textEditList` 之间
3. `ui/mainwindow.py` — 更新所有引用路径（`formatpanel.xxx` → 直指 `self.textPanel.xxx`）
4. `translate/zh_CN.ts` + `.qm` — 将 4 条翻译从 `FontFormatPanel` 上下文迁移到 `TextPanel` 上下文

---

### 删除快捷键面板 hover 备注 + 「原图不透明度」更名为「原文对照」

**问题/需求：** ① 快捷键面板的 `ConfigSubBlock` 带 `note` 备注，样式纯黑框不可用，界面简单无需备注。② 「原图不透明度」名称冗长，改「原文对照」更简洁准确地表达快速对照原图的目的。

**改动：**
1. `ui/configpanel.py` — ShortcutEditor 的 ConfigSubBlock 移除 `note` 参数；`_ACTION_NAMES`、settings 各标签统一从 "Original Opacity Toggle" 等改为 "Original Compare" / "Preset"
2. `ui/mainwindowbars.py` — 底部栏 `originalSlider` 工具提示从 "Original image opacity" 改为 "Original Compare"
3. `translate/zh_CN.ts` + `.qm` — 更新 7 条 source/translation，删除 1 条 shortcut note 条目

---

### 下拉框预设值默认值调整

**需求：** 字号大小使用 Photoshop 预置尺寸（排除 300px）；轮廓宽度改为用户个人配置值。

**改动：**
1. `utils/config.py` — `font_size_presets` 默认值改为 PS 标准尺寸列表（6→240，21 项）；`stroke_width_presets` 改为 `[0.1, 0.15, 0.2, 1.0]`
2. `config/config.json` — `font_size_presets` 同步更新为 PS 尺寸

---

### 设置面板左侧导航同步上游外观

**问题/需求：** 上游左侧导航列表文字渲染平滑（DirectWrite），本分支自定义 `NavItemDelegate` 用 `painter.drawText()` 走 GDI 路径，文字锯齿明显。且导航项背景色 #21252B 与右侧面板 #282C34 不一致。希望同步上游样式的同时保留 accent indicator 动画。

**改动：**

1. **`config/stylesheet.css`** — 移除 `ConfigBlock`、`ConfigSubBlock`、`ConfigTextLabel` 的 `@emptyContentBackgroundColor`（#21252B），注释掉使其继承 `Widget` 的 `@widgetBackgroundColor`（#282C34），与右侧面板色一致。
2. **`ui/configpanel.py`** — 新增 `TableItem`(QStandardItem)、`TreeModel`(QStandardItemModel)、`ConfigTable`(QTreeView) 三个类，端口自上游。替换原 `NavItemDelegate` + `NavList`（QListWidget）。
   - 禁用折叠（`setItemsExpandable(False)` + `setRootIsDecorated(False)`）但保留树状缩进（`setIndentation(20)`）
   - `expandAll()` 确保子项展开
   - 选中加粗逻辑来自上游 `ConfigTable.selectionChanged()`
   - accent indicator 动画从旧 `NavList.paintEvent()` 移植到 `ConfigTable.paintEvent()`
3. **`utils/shared.py`** — 删除不再使用的 `NAVLIST_HEADER_FONTSIZE`、`NAVLIST_ITEM_FONTSIZE`

**涉及文件：** `config/stylesheet.css`、`ui/configpanel.py`、`utils/shared.py`

---

### 设置面板样式打磨：字号同步上游 + 导航列表内边距 + 移除双指示器

**问题/需求：** 多项视觉问题：① 设置面板字号比上游大（16pt vs 13pt）需要同步；② 导航列表项无内边距，文本贴窗口边缘；③ 选中项同时显示两个指示器（上游的默认 QTreeView 高亮 + 本 fork 的 accent 竖条动画），且 accent 竖条在边缘 x=0 不好看；④ 点击后加粗状态无法自动清除（header 被误加粗且 never un-bold）。

**改动：**

1. **`utils/shared.py`** — `CONFIG_FONTSIZE_HEADER 18→15`、`CONFIG_FONTSIZE_TABLE 16→13`、`CONFIG_FONTSIZE_CONTENT 16→13`，与上游一致。
2. **`config/stylesheet.css`** —
   - 新增 `#ConfigNavList::item` padding（左 14px / 右 10px / 上下 5px），文本不再贴左边缘
   - 新增 `#ConfigNavList::item:selected { background: transparent; color: @qwidgetForegroundColor; }`，抑制 Win11 QTreeView 默认选中高亮色块和文字变色（Win10 无此问题），仅保留加粗表示选中
3. **`ui/configpanel.py` — ConfigTable 瘦身**：
   - 删除 `paintEvent`、`_on_selection_changed`、`_start_indicator_anim`、`_tick`、`_sync_indicator` 及全部动画状态变量（accent 竖条指示器）
   - `selectionChanged` 加 `item.isSelectable()` 守卫，防止 header 项被误设加粗后永远无法清除

**涉及文件：** `utils/shared.py`、`config/stylesheet.css`、`ui/configpanel.py`

---

### 设置面板淡入动画掉帧修复：缓存快照替代实时栅格化

**问题/需求：** 打开项目后点击设置面板，`OverlayModal` 的淡入动画掉帧明显。

**根因：** `QGraphicsOpacityEffect.setOpacity()` 每帧将整个 ConfigPanel（六页近百控件）全量渲染到离屏 buffer。定时器在高刷屏上以 ~6ms 间隔（~166fps）触发，远超单帧渲染预算。打开项目后 canvas 有图像/文字块，paint 争抢更严重。

**改动：** `ui/overlay_modal.py` — 缓存快照策略：

- `show()` 时先将面板渲染一次（`grab()` → QPixmap），随即隐藏真面板
- 用一个 QLabel 承载截图，对此 QLabel 做 QGraphicsOpacityEffect 动画（单图，无 widget 树，≈0 开销）
- 动画结束 → 销毁 QLabel，显示真面板
- 真面板渲染从 ~44 帧/350ms 降为 2 帧（1 次截取 + 1 次最终显示）
- 定时器锁定 60fps 上限（`max(16, ...)`），消除高刷屏上的无意义空转
- hide/反向/清理分支均适配缓存场景；`resize()` 兼容缓存可见期间的重定位

**涉及文件：** `ui/overlay_modal.py`

---

### 设置面板视觉打磨：导航列表顶部间距 + 无描述复选框防裁剪

**问题/需求：** 设置面板左侧导航列表顶部紧贴窗口边缘，无呼吸空间；部分纯复选框（如"To uppercase"，无 description 文字）指示器被水平布局挤压裁剪。

**改动：**

1. **`config/stylesheet.css`** — `#ConfigNavList` 加 `padding-top: 8px`，列表首项不再紧贴上边缘（此前 `main_layout.setContentsMargins(0,0,0,0)` 无顶部内边距，样式表 `::item` padding 仅作用于项内部）
2. **`ui/configpanel.py` — `checkbox_with_label()`** — 无 `description` 参数时（纯复选框，如 `let_uppercase_checker`），设 `checkbox.setMinimumWidth(24)`，确保 HBoxLayout 中仅显示指示器的复选框有足够水平空间

**涉及文件：** `config/stylesheet.css`、`ui/configpanel.py`

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

---

### 未打开项目时操作闪退修复

**问题/需求：** 未打开项目时点击"在每个项目下建立独立的字体样式"复选框导致崩溃。排查发现另有 3 处 UI 入口也无 `directory` 守卫。

**修复：**
1. `load_textstyle_from_proj_dir` — `from_proj=True` 时加 `directory is None` 守卫（复选框 toggle 后 emit signal 调用此路径）
2. `on_reveal_file` — 页面列表右键"在文件管理器中显示"（无 try-except，直接崩溃）
3. `on_export_psd` — 标题栏 PSD 导出菜单（打开空对话框后操作异常）
4. `on_export_txt` — 左侧栏导出 TXT（已有 try-except 不崩溃，但迷惑报错）

**涉及文件：** `ui/mainwindow.py`
