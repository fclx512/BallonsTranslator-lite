# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照从新到旧的顺序撰写。

## 2026-06-25

### 设置面板样式残留修复：焦点框跟随 + 移除多余包裹框

**问题/需求：** 设置面板在 `11ecc36` 从卷轴式重构为分页后出现两处样式残留：(1) NavList 左侧代码绘制的蓝色焦点指示器只在「管线」旁显示，点其他导航项不跟随；(2) 分页内包裹设置项的 PanelGroupBox 框已不再需要，且被强制拉伸到设置窗口高度。另需审视分页排布并产出建议文档。

**改动：**
1. `ui/configpanel.py` — NavList 焦点指示器改由 `currentRowChanged` 信号驱动：移除 `setCurrentRow` 覆盖（Qt 用户点击走 `QItemSelectionModel` 不触发该覆盖，原路径失效），新增 `_on_row_changed(new_row)` 挂到 `currentRowChanged`，跳过不可选的标题/分隔项，以当前 `_indicator_y` 为动画起点平滑滑到新行（保留 `_sync_indicator` 懒初始化兜底）。
2. `ui/configpanel.py` — 分页内 group 去 PanelGroupBox 多余框：`models_group`、`_build_grouped_widget`、`_add_grouped_page` 三处构造点给 group 设 `setProperty("cfgPage", True)`（用 dynamic property 避免覆盖 `GroupDetect` 等 objectName 影响阶段色条选择器）；`_wrap_page` 给被包裹 content 设垂直 `Fixed` 策略防短页被拉满。
3. `config/stylesheet.css` — 追加 `PanelGroupBox[cfgPage="true"]` 选择器：去 1px 外框 + 背景 + 圆角，**保留左侧 3px 阶段色条**；四条 `PanelGroupBox#GroupXxx[cfgPage="true"]` 复合选择器覆盖 detect/ocr/inpaint/trans 阶段配色。快捷键编辑器内 PanelGroupBox 默认框不受影响。
4. `docs/设置面板排布建议.md` — 新建排布审视建议文档（管线页体量失衡、阶段配色不一致、环境页留白、Models 按钮宽度、NavList 标题交互五点，未改结构，列出供决策）。

**涉及文件：** `ui/configpanel.py`、`config/stylesheet.css`、`docs/设置面板排布建议.md`

---

### 静态摸排：选中多个文本框右键翻译偶发报错

**问题/需求：** 用户反馈在画布选中**多个文本框**后右键翻译（`run_blktrans`，mode=-1 纯翻译 / 0 OCR / 1 OCR+翻译 / 2 全流程），有时报错。无可用复现案例，本次仅做静态代码摸排，未改动代码，记录候选根因与修复方案待有测试案例时验证。

**调用链（已核实）：** 画布右键菜单 `run_blktrans` 信号 → `MainWindow.on_run_blktrans`（`ui/mainwindow.py:2200`）→ `translateBlkitemList`（`ui/mainwindow.py:2005`，把选中的 `TextBlkItem` 转成 `blk_list`+`blk_ids`，`blk_ids` 为各 `blkitem.idx` 的快照）→ `ModuleManager.runBlktransPipeline`（`ui/module_manager.py:1456`，`terminateRunningThread` 后 `progress_msgbox.show`）→ `ImgtransThread.runBlktransPipeline`（`ui/module_manager.py:445`）后台线程执行 `_blktrans_pipeline`（`ui/module_manager.py:458`）→ 完成后 `finish_blktrans` 信号 → `ModuleManager.on_finish_blktrans`（`ui/module_manager.py:1488`）→ `blktrans_pipeline_finished` 信号 → `MainWindow.on_blktrans_finished`（`ui/mainwindow.py:2204`）。注意 `ui/mainwindow_mixin.py` 里的同名方法是死代码（`MainWindow` 未继承 `MainWindowMixin`），实际生效的是 `mainwindow.py` 版本。

**候选根因（按崩溃概率排序）：**

1. **`on_blktrans_finished` 索引越界（最可能命中用户"报错"）** — `ui/mainwindow.py:2209` `blkitem_list = [self.st_manager.textblk_item_list[idx] for idx in blk_ids]`。`textblk_item_list` 是**当前页**的文本框列表，`updateSceneTextitems`（`ui/scenetext_manager.py:548`，切页面时调）会先 `clearSceneTextitems` 清空再按新页文本框重建（`scenetext_manager.py:540-546`），`blk.idx` 是页内序号。翻译在后台线程异步执行，`finish_blktrans` 跨线程默认 `QueuedConnection` 排队到主线程；若翻译运行期间用户切了页面（多选翻译耗时长，切页概率高），`textblk_item_list` 已换批，原 `blk_ids` 会指向**不同文本框**或越界 → `IndexError`。`pairwidget_list[blk.idx]`（`mainwindow.py:2213`）同理。`terminateRunningThread`（`module_manager.py:1408`）用 `quit()`，无法中断已运行的同步 `_blktrans_pipeline`，竞态窗口敞开。
2. **`RunBlkTransCommand` 用当前页 `inpainted_array` 配触发页坐标** — `ui/drawing_commands.py:147-168`：`__init__` 新鲜读取 `self.canvas.imgtrans_proj.inpainted_array`（当前页），而 `inpaint_rect` 来自后台线程在**触发翻译时页面**上的 `blk.xyxy` 计算（`module_manager.py:497-524`）。`set_current_img`（`utils/proj_imgtrans.py:295`）切页面会替换 `inpainted_array`/`mask_array`。若 mode=2（含修复）翻译期间切到尺寸更小的页面，`img_array[inpaint_rect[1]:inpaint_rect[3], ...]` numpy 切片越界 → `IndexError`。仅 mode=2 命中。
3. **`_blktrans_pipeline` inpaint 循环无 try/except** — `ui/module_manager.py:494-528` 的 inpaint 循环外无异常保护（OCR/翻译段有 `try/except create_error_dialog`）。若 `maskseg_method` 或 `tgt_mask[y1:y2, ...]` 切片越界，异常会上抛终止线程且**不发 `finish_blktrans`**，进度框可能卡住。
4. **`_blktrans_pipeline` 重复发射 `finish_blktrans`（放大器，本身非 crash）** — `ui/module_manager.py:477 / 493 / 528` 三处 `emit`，528 为末尾无条件发射。mode=-1 发 2 次、mode=0 发 2 次、mode=1/2 发 3 次。每次触发 `on_blktrans_finished` → `push_undo_command(RunBlkTransCommand(...))` 重新写回译文 + 推撤销命令，**污染撤销栈**；mode=2 时中间一次 emit 发生在 inpaint 数据未就绪阶段，`RunBlkTransCommand.__init__` 读 `region_inpaint_dict` 可能读到 None/陈旧值（`drawing_commands.py:150-152`）。多次 emit 把 #1/#2 的竞态窗口放大 2-3 倍。需确认三处 emit 是否为有意"分阶段刷新进度条"——若是，不能简单删 477/493。
5. **`ContextBatchTranslator._apply_cache` 子集索引错位（结果不对，非 crash）** — `modules/translators/context_batch.py:241` `cache.get(idx, blk_list[idx].get_text())`：`non_empty` 是传入**子集**的局部索引（`context_batch.py:120`），`cache` key 是整页 `bidx`（`context_batch.py:222-229` 经 `_collect_target` 扫整页）。整页翻译二者相等所以一直没暴露；只在 `ctx` 残留于 `translate_thread.module`（Run 对话框用上下文翻译后未 restore 时右键翻译，restore 仅在 `on_imgtrans_pipeline_finished:2049` 触发，`runBlktrans` 路径不触发）+ 子集首块恰好匹配某页首块时命中，译文错位/回退为原文。命中条件苛刻，且表现为译文不对而非报错。

**待验证 / 候选修复（未实施）：**

- 针对根因 #1：`on_blktrans_finished` 解引用 `blk_ids` 前校验 `textblk_item_list` 与触发时同源（校验页码未变 / `idx` 仍在范围内 / 失败则忽略本次 emit）；或后台线程捕获并附带页码快照，主线程比对不一致则丢弃回调。
- 针对 #2：`RunBlkTransCommand` 改用触发时页面的 `inpainted_array`（由 `translateBlkitemList`/`_blktrans_pipeline` 传入引用），而非实时读 `self.canvas.imgtrans_proj`。
- 针对 #4：先确认三处 emit 的设计意图；若确为冗余则只在 528 末尾发一次；若 477/493 为进度条刷新，改为只发 `finish_blktrans_stage`（进度）不发 `finish_blktrans`（完成）。
- 针对 #5：若决定保留 `runBlktrans` 下走 `ctx` 的可能，`_apply_cache` 改用整页 `bidx` 寻址（需让 `translate_textblk_lst` 把 `bidx` 透传进来），或在 `runBlktrans` 路径下 `ctx._proj` 非 None 也走 `_direct_call`。
- **需测试案例验证的项：** ①多选翻译期间切页面是否复现 #1 的 `IndexError`；②mode=2 多选 + 切小尺寸页面是否复现 #2；③重复 emit 在单选 vs 多选下撤销栈的实际表现（确认 #4 是否真的影响撤销）；④用过上下文翻译后立即右键多选翻译，看是否走 `ctx` 并触发 #5。

**涉及文件（仅摸排，未改动）：** `ui/mainwindow.py`、`ui/module_manager.py`、`ui/scenetext_manager.py`、`ui/drawing_commands.py`、`utils/proj_imgtrans.py`、`modules/translators/context_batch.py`、`modules/translators/base.py`

---

## 2026-06-23

### 自动化模组分页合并为单页管线 + 按钮换行

**问题/需求：** 自动化模组（DL Module）原拆为 Models / Text Detection / OCR / Inpaint / Translator 5 个独立分页，功能属于同一流程但需切换查看，操作路径长。同时 Models 页内按钮水平排列占用过宽。

**改动：**
1. `ui/configpanel.py` — 新增 `_build_grouped_widget()` 构建 PanelGroupBox 但不注册为独立页面；5 个 PanelGroupBox 合并到同一容器（`dl_container`），一次 `_add_page` 注册为单页；NavList 移除 5 子项、DL Module 标题行和 Models 行，改为 1 个"管线"可选项；`_on_nav_row_changed` 清理 `models_group` 特判；`focusOn*` 统一导航到管线页 + `ensureWidgetVisible` 自动滚动到对应 section
2. 按钮换行：去掉 `QHBoxLayout` 和固定宽度，两按钮直接纵向加入 `models_vlayout`
3. `translate/zh_CN.ts` — ConfigPanel context 新增 `"Pipeline" → "管线"`

**涉及文件：** `ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 设置面板重构：侧滑单页长卷轴 → 中心淡入分页模态

**问题/需求：** 设置面板原为单页长卷轴（NavList ↔ ConfigContent 双向联动滚动），所有 section 堆叠一页，复杂功能下方说明注释易与他项混淆、注释难加。目标：NavList 大标题纯分隔不可选、小标题成为独立分页（右侧 `QStackedWidget`），取消右侧滑入改为中央覆层淡入淡出 + 压暗 scrim（仅覆盖中央画布区，左栏/底栏/标题栏仍可点）；遵从 `pcfg.animation_fps`；关闭时一次性保存不变。

**改动：**
1. `ui/overlay_modal.py` — **新增** `OverlayModal`：scrim（`_Scrim`，rgba 0.55 覆盖 cover_widget）+ panel `QGraphicsOpacityEffect` opacity 淡入淡出，`QEasingCurve.InOutExpo`，`pcfg.animation_fps<0` 或 duration<=0 跳过；`on_before_show`/`on_after_hide` 回调、`set_backdrop_closable(bool)`（子对话框打开时暂停点击关闭）、`is_visible`、反转、`resize()` 居中；panel 固定 1000×700（cover 较小则按其缩）。
2. `ui/configpanel.py` — ConfigContent 长卷轴 → `QStackedWidget`（`self.pageStack`，`self.configContent` 作兼容别名）；引入 `_DeadBlock`/`_DeadLayout` 将原有 `dlConfigPanel`/`generalConfigPanel.addGroupedBlock` 与 `vlayout.addWidget` 路由到 pageStack（`_add_page`/`_add_grouped_page`/`_wrap_page` 每页包成 `QScrollArea`+`AnimatedScrollBar`）；新增 **Models 首页**（`self.models_group`）；NavList 加 Models 行（DL Module: Models/Detect/OCR/Inpaint/Translator，General: Project/Typesetting/Interface/Environment）；`_on_nav_row_changed` 改 `pageStack.setCurrentIndex`（经 `_page_index`），删除 `_on_content_scrolled`/`scrollToWidget` 调用与 `_on_content_scrolled`；`focusOn*` 改为 `_nav_select` 切页；`addConfigBlock` 改 shim 返回 `_DeadBlock`；新增 `_run_modal_dialog` 包裹全部子对话框 `dialog.exec()`，前后切换 backdrop closable；注入 `self._modal_ref`。
3. `ui/mainwindow.py` — `_configSlide = OverlaySlider(split_mode=True)` → `_configModal = OverlayModal(self.configPanel, self.centralStackWidget, duration=350)`；注入 `configPanel._modal_ref`；`_showConfigOverlay`/`_hideConfigOverlay`/`resizeEvent` 改用 `_configModal.show/hide/resize`。
4. 现缩/离屏测试：MainWindow 构造成功、modal 显隐正常（无动画与 60fps 动画两条路径）、scrim 外部点击关闭、backdrop 暂停后点击不关闭、focusOnOCR→page idx 2、hideEvent 仍触发 save_config；每页 QScrollArea 可滚动；qm 编译 770 条且无 `?` 污染；`i18n_check` 仅余已知 `_ShortcutRow` orphan 假阳性。
5. `CLAUDE.md` — 关键文件表新增 `overlay_modal.py`、`overlay_slide` 描述收敛为 GlobalSearch/PageList；动画章节改写 ConfigPanel 分页/模态现状；开发日志 "7 天"→"3 天"。

**涉及文件：** `ui/overlay_modal.py`（新）、`ui/configpanel.py`、`ui/mainwindow.py`、`CLAUDE.md`

---

### 上下文翻译提示词修复 + 日志窗口

**问题：** 上下文翻译效果差，根因是 `ContextBatchTranslator._build_msgs()` 错误地使用了 profile 的 `prompt_template`（"请将以下 {from_lang} 文本翻译为 {to_lang}"）作为 system prompt，完全没有上下文感知指令。同时，进度条标签（89 字符截断）无法展示翻译过程详情。

**改动：**
1. `modules/translators/context_batch.py` — `_build_msgs()` 放弃使用 `self.translation_prompt`，改为始终使用专门为上下文翻译设计的 system prompt，明确描述 `=== CONTEXT ===`/`=== TRANSLATE THESE ===` 双区格式、`[done]`/`[raw]` 含义、术语一致/语气衔接/代词消歧等规则；`_contextual()` 增加逐批详细 `_status()` 调用（batch header、上下文统计、逐条原文→译文、耗时）
2. `ui/context_log_dialog.py` — **新增**，非模态 `QPlainTextEdit` 窗口，显示上下文翻译过程详情，支持自动滚底、Clear 按钮，翻译期间可操作主窗口
3. `ui/mainwindow.py` — 创建 `ContextBatchTranslator` 时同时创建/显示 `ContextLogDialog`，`_ctx_status` 回调同时发给进度条和日志窗口，pipeline 结束时自动关闭
4. `translate/zh_CN.ts` — 新增 `ContextLogDialog` 上下文 2 条翻译

**涉及文件：** `modules/translators/context_batch.py`、`ui/context_log_dialog.py`（新）、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 禁用数字键不透明度调整 + 原图不透明度切换功能

**问题：** 焦点在画布时，数字键 0-9 会按比例调整嵌字层/原图层不透明度（canvas.py 中 `keyPressEvent` 的 `QNUMERIC_KEYS` 分支），功能意义不明且占用过多键位。同时 `Slider` 类缺少 `keyPressEvent` 覆盖，QSlider 内建的数字键映射在滑块聚焦时也可能误触。

**改动：**
1. `ui/canvas.py` — 移除 `QNUMERIC_KEYS` 导入、`keyPressEvent` 中的数字键分支（`set_active_layer_transparency`）、相关的 slider 类型注解和死方法；`set_active_layer_transparency` 联动两个滑块互为 100-complement 的逻辑一并删除
2. `ui/custom_widget/slider.py` — `Slider.keyPressEvent` 新增加，拦截 0-9 键位防止未来焦点误触
3. `ui/mainwindow.py` — 移除已无用的 `originallayer_trans_slider`/`textlayer_trans_slider` 引用赋值；新增 `shortcutToggleOriginalOpacity` 方法（预设值 ↔ 100% 切换，同时更新 slider 手柄位置）
4. `utils/config.py` — 新增 `original_transparency_preset: int = 20`
5. `ui/configpanel.py` — 注册 `toggle_original_opacity` 快捷键（默认无绑定，View 组）；Interface 区段新增 QSpinBox 设置预设值（0-99）
6. `translate/zh_CN.ts`、`translate/zh_CN.qm` — 新增 3 条翻译（`"Toggle Original Opacity"`、`"Original Opacity Toggle"`、`"Toggle Preset (%)"`）

**涉及文件：** `ui/canvas.py`、`ui/custom_widget/slider.py`、`ui/mainwindow.py`、`utils/config.py`、`ui/configpanel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`
