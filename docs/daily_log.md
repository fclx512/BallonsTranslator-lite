# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照从新到旧的顺序撰写。

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
