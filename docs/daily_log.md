# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照从新到旧的顺序撰写。

## 2026-06-23

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
