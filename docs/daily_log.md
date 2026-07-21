# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在文档末尾写入。


## 2026-07-19

### Fold 按钮布局改造：左侧拖拽区 + 双模式切换

**需求：** 原左侧编号栏压缩后拖拽交互困难、选中蓝条不可见。将 fold 按钮升级为完整布局模式切换。

**改动：**

1. **布局重构**（`ui/textedit_area.py`）：
   - 新增 `drag_area` QFrame（22px），置于 accent_bar 与文本内容之间
   - 双 badge：`badge_vp`（viewport 右上角，fold=OFF 使用）/ `badge_drag`（drag_area 内居中，fold=ON 使用）
   - `setFold(fold)`：fold=ON → accent_bar 3px + drag_area 显示 + badge 在左侧；fold=OFF → accent_bar 3px + drag_area 隐藏 + badge 回 viewport
   - 移除原 fold 对 QTextEdit（NoWrap/min_height）的影响

2. **滚动条**（`ui/custom_widget/scrollbar.py`、`ui/textedit_area.py`）：
   - `scrollbar.py` 替换为上游版本（支持 `hover_style` / `fadeout` 参数）
   - `TextEditListScrollArea` 加回 `ScrollBar(Qt.Vertical, self, fadeout=True)` — 默认淡出，hover 展开

3. **默认值**（`utils/config.py`）：
   - `fold_textarea: False → True`

4. **按钮改名**（`ui/scenetext_manager.py`）：
   - `CheckableLabel("Unfold", "Fold")` → `CheckableLabel("Edit", "Review")`

5. **样式**（`config/stylesheet.css`）：
   - 新增 `QLabel#TextBlockIndexBadge[folded="true"]` 拖拽区徽章样式（`font-size: 13px`，透明底、主题色字）
   - 新增 `TransPairWidget #dragArea` 透明区样式

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - "Unfold" → "Edit"（编辑）/ "Fold" → "Review"（审阅）

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅、qm 编译 1021 条 ✅

**涉及文件：** `ui/textedit_area.py`、`ui/custom_widget/scrollbar.py`、`ui/scenetext_manager.py`、`utils/config.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 文本框模式蓝色边框修复 + Run 对话框始终显示 + 默认编辑模式

**需求/问题：**

1. **Run 对话框**：`run` 功能只在项目页数 > 1 时弹出设置窗口，单页项目直接执行上次流程。改为无条件弹出。
2. **默认模式**：每次启动右侧文本框区模式默认为「审阅」（Review），需改为「编辑」（Edit）。
3. **蓝色边框不显示**：按 W 进入文本框模式时，已有文本框的蓝色边框「几乎不可见」。手动调整任一文本框尺寸后才恢复正常。

**根因与修复：**

1. **Run 对话框**（`ui/mainwindow.py:2750`）— 移除 `if num_pages > 1:` 条件判断，Run 对话框始终弹出。

2. **默认编辑模式**（`ui/mainwindow.py:603,606`）— `foldTextBtn.setChecked(True)` 和 `fold_textarea(True)` 硬编码启动，忽略配置值。

3. **蓝色边框**（`ui/textitem.py`）：

   - **根因：** 文本渲染模式设为「清晰（矢量渲染）」时，`_use_full_pixmap = False` + `CacheMode = NoCache`，走 `paint()` 慢路径。慢路径中 `_draw_accessories()` 以 `DestinationOver` 合成模式绘制，将蓝色边框画在 QTextDocument 内容**后面**，被文字遮盖不可见。流畅（位图渲染）模式走快路径，边框通过 `_draw_border_rect()` 以 `SourceOver` 画在文字**上面**，一切正常。
   - **修复：** 慢路径非编辑状态下，背景（stroke/shadow）仍用 `DestinationOver` 画在文字后，边框改用 `_draw_border_rect()` 以 `SourceOver` 画在文字上方。新增 `_draw_background_only()` 方法剥离边框绘制。
   - **辅助**（`ui/scenetext_manager.py`）：`showTextblkItemRect()` 中加上 `_invalidate_cache()` + `update()` 确保 Qt `DeviceCoordinateCache` 失效。

**验证：** 语法检查 ✅（`ui/textitem.py` + `ui/scenetext_manager.py`）

**涉及文件：** `ui/mainwindow.py`、`ui/textitem.py`、`ui/scenetext_manager.py`

---

## 2026-07-19

### 右侧文本框区 hover 跳变修复 + 编辑光标偏移修复 + documentMargin 恢复

**需求/问题：**

1. **hover 跳变**：鼠标悬停原文/译文框时，`QGraphicsDropShadowEffect`（blurRadius=12）改变渲染管道，导致内部文字轻微偏移跳动。尤其在当前紧凑布局下影响明显。
2. **编辑光标偏移**：进入编辑模式时，由于 `documentMargin=0` 文字紧贴边框边缘，光标比字形高，Qt 会重新排版使光标完整显示，导致文本行向上/下偏移。

**修复：**

1. **hover 改用 CSS**:（`ui/textedit_area.py`）— 移除 `QGraphicsDropShadowEffect`，`setHoverEffect` 改为空方法。视觉反馈由 CSS `:hover` 伪类接管（边框变 accent 色）。
2. **新增淡边框**（`config/stylesheet.css`）— `SourceTextEdit` / `TransTextEdit` 添加 `1px solid rgba(128,128,128,0.20)` 永久淡边框稳定内容区域；`:hover` / `:focus` 边框变 `@accentPrimary`。
3. **恢复 documentMargin**（`ui/textedit_area.py:97`）— 从 `0` 改为 `2`，保留 2px 上下气口使光标完整显示，消除编辑点击时的文本偏移。

**验证：** 语法检查 ✅

**涉及文件：** `ui/textedit_area.py`、`config/stylesheet.css`

---

### PPOCRv6 ONNX 竖排文本框方向修复

**问题：** ppocrv6_onnx 文本检测模型识别出的竖排文本框未正确应用文本方向参数（`src_is_vertical` / `vertical` 始终为 False），导致 OCR 虽然能正确识别竖排文字，但框的方向参数仍为横排。

**根因：** `detector_paddlev6.py` 的 `_detect()` 将检测框直接转为 `TextBlock`，跳过了 `sort_pnts()` 方向判断和 `examine_textblk()` 计算，`src_is_vertical` 在 `__post_init__` 中默认走 `self.vertical`（`False`）。

**修复：** 对每个检测框调用 `sort_pnts()` 判断竖排/横排，设置 `src_is_vertical` / `vertical` 标志，调用 `examine_textblk()` 正确计算角度和字号。

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅

**涉及文件：** `modules/textdetector/detector_paddlev6.py`

---

### Auto Layout 功能完整移除

**理由：** 该功能（根据气泡 mask 自动分割译文为多行）的 mask 提取基于硬编码 Canny 阈值 + flood fill，对非常规场景完全不可靠。遵循减法原则：不维护半桶水功能。

**清理范围：**

1. **配置层**（`utils/config.py`）— 删除 `let_autolayout_flag`
2. **UI**（`ui/configpanel.py`）— 删除复选框、处理器、setChecked
3. **触发逻辑**（`ui/mainwindow.py`）— 删除 `auto_textlayout_flag` 设置/重置
4. **核心逻辑**（`ui/scenetext_manager.py`）— 删除 imports、属性、addTextBlock 拦截分支、`onAutoLayoutTextblks`、`layout_textblk`（217行）、`get_text_size` / `get_words_length_list` 死函数
5. **撤销命令**（`ui/textedit_commands.py`）— 删除 `AutoLayoutCommand`
6. **信号**（`ui/canvas.py`）— 删除 `layout_textblks = Signal()`
7. **布局引擎**（`utils/text_layout.py`）— 整文件删除（623行）
8. **分词**（`utils/text_processing.py`）— 删除 `seg_text` 及全部下游依赖，保留 `full_len`/`half_len`/`is_cjk`
9. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）— 删除 2 条翻译，重编译

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅、i18n 检查 ✅、qm 编译 1019 条 ✅

**涉及文件：**
- 删除：`utils/text_layout.py`
- 修改：`utils/config.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/scenetext_manager.py`、`ui/textedit_commands.py`、`ui/canvas.py`、`utils/text_processing.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-19

### 上游 Context 系统适配 + Profile Manager 清理 + 提示词策略替换

**需求：** 借鉴上游的 LLM translation context 系统（glossary / history window / token budget / context recovery），替换现有的三段式提示词；清理 Profile Manager 中废弃的「翻译设置」；新增「返回 JSON Schema」勾选框和「额外翻译指令」编辑框。

**改动：**

1. **新增 Context 基础设施**（`modules/context/`）：
   - `glossary.py` — 术语表加载（JSON/TXT/TSV）、LRU 缓存、大小写不敏感匹配、稳定渲染
   - `history.py` — `HistoryPage`/`RenderedHistoryPage`/`HistoryWindow` 不可变快照；`eligible_history_for_request()` 智能历史选择（60% low-water mark）；`recover_context_length()` context overflow 恢复；`ContextDiagnostic` 诊断日志
   - `token_usage.py` — tiktoken 精确计数 + fallback 估算；`format_token_usage()` 兼容各厂商 usage 字段
   - `errors.py` — `ContextLengthError` + `is_context_length_error()` 三阶段识别（status code / error code / message regex）

2. **配置层**（`utils/config.py`）：
   - 新增 `TranslateContext`、`LLMTranslateContext`、`LLMGlossaryMode` 枚举
   - `ModuleConfig` 新增 5 个字段：`translate_context`、`llm_translate_context`、`llm_prior_context_token_budget`、`llm_glossary_path`、`llm_glossary_mode`
   - `__post_init__` 验证逻辑

3. **Profile Manager 重构**（`utils/profile_manager.py`）：
   - **删除**「Translation Settings (optional)」整个 section（Response Format ComboBox、Prompt Template、Few-Shot Examples、Frequency Penalty、Presence Penalty）
   - **删除** `DEFAULT_PROMPT_TEMPLATE`、`DEFAULT_CHAT_SAMPLES` 常量
   - **新增**「返回 JSON Schema」`ConfigCheckBox`（字段 `return_json_schema`，默认 False）
   - **新增**「Extra Translation Instructions (optional)」可折叠 `ConfigTextEdit`（字段 `system_prompt`，留空则纯用硬编码 contract）
   - 同步更新 `PROFILE_FIELDS`、`SAMPLE_PROFILES`、`ProfileManagerDialog` 和 `ProfileManagerWidget` 的 UI/保存/填充/清空方法

4. **LLM 翻译器重写**（`modules/translators/trans_llm_api.py`）：
   - **移除**三段式：`DEFAULT_SYSTEM_PROMPT`、`_assemble_prompts()`、`_parse_chat_samples()`、`build_copy_prompt()`
   - **新增**上游 contract 策略：`_system_prompt()`（JSON 输出合约 + history_rule + 可选额外指令）、`_render_user_prompt()`（"Translate from X to Y:\nINPUT:\n{json}" + 可选 GLOSSARY）、`_assemble_request()`（cache-friendly 前缀顺序：system → glossary → history → current user）
   - **集成 Context**：`_snapshot_request_context()` 冻结 glossary + eligible history pages；`_history_window` 实例级缓存；`translate()`/`_translate()` 支持 `project`/`page_key`/`commit_history_window`；ContextLengthError recovery 自动重试
   - 保留原有 profile 访问、API key 管理、rate limiting、响应解析

5. **管线集成**（`ui/module_manager.py`、`ui/mainwindow.py`）：
   - `translate_textblk_lst()` 调用传入 `project` 和 `page_key`
   - `mainwindow.py` 中 `context_batch` 引用从 `prompt_template` 改为 `system_prompt`

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 移除旧翻译设置相关条目（Response Format / Prompt Template / Few-Shot Examples / Frequency Penalty / Presence Penalty）
   - 新增 6 条翻译（Return JSON Schema / Extra Translation Instructions / Instructions 等）
   - 重编译为 1013 条

**验证：** 语法检查 ✅、i18n 检查 ✅、启动导入测试 5/5 ✅、Context 模块独立导入验证 ✅

**涉及文件：**
- 新增：`modules/context/__init__.py`、`modules/context/glossary.py`、`modules/context/history.py`、`modules/context/token_usage.py`、`modules/context/errors.py`
- 修改：`modules/translators/trans_llm_api.py`、`utils/config.py`、`utils/profile_manager.py`、`ui/module_manager.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### Run 对话框简化 + 上游 LLM Context 设置集成 + 尺寸锁定

**需求：**
1. Run 对话框样式过多（2×2 网格 / 可折叠 Settings），仅上下文翻译相关设置有用，其他接口设置无用
2. 缺少上游的 LLM Context（page/+history）设置项
3. 窗口高度应在内容折叠时自动收缩，不可手动拉伸
4. 下拉框使用自定义 `ConfigComboBox` 样式，边框颜色需与背景有区分
5. Glossary 上传后无法清除，退出窗口后应自动清理

**改动：**

1. **Run 对话框 UI 简化**（`ui/mainwindow.py`）：
   - 去掉 Activate Modules 2×2 网格 + Settings 可折叠章节
   - 还原为简单逐行勾选框列表（Enable Text Detection / Enable OCR / Enable Translation / Enable Inpainting）
   - 去掉 Text Detection（Keep Existing Lines）和 Inpainting（Skip simple cases）设置项
   - Translation 行 inline 放置 Context Translation (beta) 复选框

2. **添加上游 LLM Context 设置**（`ui/mainwindow.py`）：
   - 新增 LLM Context 下拉框（page / +history），绑定 `llm_translate_context`
   - 新增 Token Budget `NoArrowsSpinBox`（512-16384），绑定 `llm_prior_context_token_budget`，仅 +history 时显示
   - Glossary 设置（文件路径 + Matching/All 模式）保留并优化
   - 仅 CT beta 勾选时显示 Context 区域，删除冗余的普通 Context（textblock/page）

3. **复选框联动**（`ui/mainwindow.py`）：
   - 勾选 CT beta → 自动勾选 Enable Translation
   - 取消 Enable Translation → 自动取消 CT beta

4. **尺寸锁定**（`ui/mainwindow.py`）：
   - `_resize_to_fit()` 辅助函数：先解锁 → `adjustSize()` → `setFixedSize()` 锁定
   - 所有可见性切换（CT beta toggle、LLM Context 模式、Glossary toggle）均调用 `_resize_to_fit()`

5. **自定义控件替换**（`ui/mainwindow.py`）：
   - 3 个下拉框：`QComboBox` → `ConfigComboBox`（主题感知圆角样式）
   - Token Budget：`QSpinBox` → `NoArrowsSpinBox`
   - Browse 按钮：加 `setFixedHeight(27)` 匹配 QLineEdit 行高
   - Glossary 路径在 dialog 关闭时自动清空

6. **边框颜色调亮**（`config/stylesheet.css`）：
   - `ConfigComboBox`/`ParamComboBox` 边框：`@borderColor` → `@accentPrimary80`
   - 浅色/深色主题下均有明显蓝色边框，与输入框背景形成对比

7. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 3 条翻译（LLM Context / +history / Token Budget）
   - 编译为 1036 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/mainwindow.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 合并功能简化：移除 LTR/RTL 方向，改为按列表次序合并

**需求：** 右键合并的 LTR/RTL 方向判断对上下排列的文本框无意义，且不尊重用户手动排好的阅读顺序。改为始终按文本框 `idx`（列表顺序）合并，去掉「默认从右到左合并」开关。

**改动：**

1. **配置层**（`utils/config.py`）— 删除 `merge_rtl` 字段

2. **信号变更**（`ui/canvas.py`）— `merge_textblks = Signal(str)` → `Signal()`，不再传方向

3. **右键菜单**（`ui/context_menu_config.py`）：
   - `_build_merge()`：去掉 direction 判断，直接 emit
   - `_build_behavior()`：去掉「Merge Right-to-Left」切换和分隔线
   - 删除 `_toggle_merge_rtl()` 函数

4. **合并执行**（`ui/scenetext_manager.py`）：
   - `on_merge_textblks()`：排序改为 `b.idx`（列表顺序），不再按 `center_x` 位置排
   - 合并前增加 **UI→blk 文字同步**：用户手动在画布输入的文字存于 QTextDocument，未写回 `blk.translation`/`blk.text`。合并前遍历选中块，从 `b.toPlainText()` 和 `pw.e_source.toPlainText()` 同步到 `blk`，确保原文和译文不丢失（原有 bug，与本次方向改动无关）
   - `_build_merged_blk()`：删除无用的 `direction` 参数

5. **快捷键**（`ui/mainwindow.py`）— `shortcutMergeBlks()` 直接 emit 无参数

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）— 删除 Left-to-Right、Right-to-Left、Merge Right-to-Left 三条翻译，重编译为 1032 条

**行为变更：** 合并不再区分 LTR/RTL/上下，直接按文本框在侧栏列表中的顺序（`idx`）拼接文字/译文。用户先排好顺序再合并即可获得预期的文字顺序。

**验证：** 语法检查 ✅、i18n 检查 ✅、qm 编译 1032 条 ✅、手动合并测试 ✅（含手动创建文本框输入文字的场景）

**涉及文件：** `utils/config.py`、`ui/canvas.py`、`ui/context_menu_config.py`、`ui/scenetext_manager.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 自定义术语 AI 转换 + 文件路径替换为状态指示器 + 独立日志窗口替换为调试日志文件

**需求：**
1. 在 Run 对话框术语表 Browse 按钮旁加「Custom...」按钮，用户可用自然语言描述角色/术语，运行前由 AI 转为结构化术语表
2. 删除文件路径地址栏，改为紧凑状态指示器（○/✓）
3. 删除独立 ContextLogDialog 窗口，改为默认关闭的调试日志文件输出

**改动：**

1. **自定义术语对话框**（`ui/glossary_dialog.py` — 从 git 恢复并增强）：
   - `CustomGlossaryDialog(parent, initial_text="")` 支持回显上次输入
   - 提示文字支持自然语言描述（示例改用 Dragon Ball / One Piece 等常见作品）
   - `get_raw_text()` 返回编辑器原始内容供 AI 转换
   - 移除分隔符说明文字，按钮宽度从 90px 加宽至 110px

2. **Run 对话框 Glossary UI 改造**（`ui/mainwindow.py`）：
   - 删除 `glossary_path_edit`（QLineEdit），改用 `glossary_status_label` 显示 ○/✓
   - 新增 `glossary_custom_btn`（Custom...），复选框与 `_custom_glossary_text` 闭包变量配合
   - 对话框关闭时清理自定义文本和 glossary 路径

3. **AI 术语表生成**（`modules/translators/context_batch.py`）：
   - `ContextBatchTranslator` 新增 `custom_glossary_text` 参数
   - `set_project()` 中调用 `_generate_custom_glossary()` 将用户自然语言转为 `GlossaryEntry` 列表
   - `_raw_llm_call()` / `_parse_glossary_response()` — JSON 响应解析（含 markdown fence 处理）
   - 优先级：自定义术语 > 文件术语 > 自动学习术语

4. **调试日志替代独立窗口**：
   - `ui/context_log_dialog.py` — 删除
   - `utils/debug_log.py` — 新建 `DebugLogger`，输出到 `debug/context_translation_<timestamp>.log`
   - `utils/config.py` — `ProgramConfig` 新增 `context_translation_debug_log: bool = False`（默认关闭，config.json 已 gitignore）
   - `ui/mainwindow.py` — 删除 `ContextLogDialog` 创建/显示/关闭逻辑，`_ctx_status` 在开关开启时写入调试日志文件

5. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 CustomGlossaryDialog 提示文字翻译（含 Dragon Ball / Goku 等示例）
   - 删除 ContextLogDialog 上下文（2 条 message）
   - 编译为 1035 条

**验证：** 语法检查 ✅、i18n 检查 ✅（仅剩 orphan，均为预存间接调用）、qm 编译 ✅、启动导入测试 5/5 ✅

**使用方式：** `config/config.json` 中设 `"context_translation_debug_log": true` 启用调试日志，输出至 `debug/context_translation_*.log`

**涉及文件：**
- 新增：`utils/debug_log.py`
- 删除：`ui/context_log_dialog.py`
- 修改：`ui/glossary_dialog.py`、`ui/mainwindow.py`、`modules/translators/context_batch.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-20

### 术语表提取工具 — 频率启发 + LLM 语义提取

**需求：** 从已有翻译项目中自动提取术语表，支持两种模式：快速频率统计和 LLM 语义分析。参考 AiNiee 的术语表提取方式设计。

**研究结论：** AiNiee 使用两阶段 LLM 管线（提取 → 去重合并）识别角色名/专有名词/不翻译项，结合结构化 prompt + JSON 输出。本项目已有 glossary 系统（`modules/context/glossary.py`）和 `ContextBatchTranslator._generate_custom_glossary()` 作为 LLM glossary 参考实现，但缺少从已有翻译项目自动提取的功能。

**改动：**

1. **核心提取逻辑**（`modules/glossary_extractor.py` — 新建）：
   - `extract_by_frequency(proj, min_count=2)` — 遍历项目统计词频，从高频重复且有对应译文的 source 中提取术语
   - `extract_by_llm(proj, api_config, status_cb)` — 收集项目原文/译文对，发送给 LLM 识别重要命名实体和术语
   - `save_glossary_json(entries, path)` — 保存为标准 JSON glossary 格式
   - LLM prompt 聚焦角色/地点/组织/特殊术语/非直译术语，输出 `[{"src", "dst", "info"}]` 格式

2. **提取对话框 UI**（`ui/glossary_extractor_dialog.py` — 新建）：
   - `GlossaryExtractorDialog(QDialog)` — LLM 配置选择 + 提取模式切换（频率/LLM）
   - 后台线程 `_ExtractWorker` 避免 UI 卡顿
   - 可编辑的预览表格（QTableWidget），支持提取结果编辑
   - 保存后可选立即设置为活动术语表
   - 覆盖缺少数据/无配置等边界情况

3. **集成：Tools 菜单**（`ui/mainwindowbars.py` + `ui/mainwindow.py`）：
   - 在顶部 TitleBar 的「Tools」菜单中添加「Extract Glossary…」菜单项
   - 点击打开 `GlossaryExtractorDialog` 作为独立窗口（不依赖 Run 对话框）
   - 自动读取当前翻译器激活的 profile 作为默认 LLM 配置
   - 提取保存后自动设置 `pcfg.module.llm_glossary_path`
   - 移除之前 Run 对话框中的「Extract...」按钮，解耦运行管线与提取流程

4. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - 新增 `GlossaryExtractorDialog` 上下文（27 条翻译）
   - 新增 `_ExtractWorker` 上下文（4 条翻译）
   - `TitleBar` 新增「Extract Glossary…」翻译
   - `MainWindow` 移除已删除的「Extract...」翻译
   - 重编译为 1064 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1064 条 ✅、启动导入测试 5/5 ✅、glossary_extractor 模块独立导入验证 ✅、save/load 双向测试 ✅

**涉及文件：**
- 新增：`modules/glossary_extractor.py`、`ui/glossary_extractor_dialog.py`
- 修改：`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`


### 字体样式管理器修复 + 一键应用预设

**问题/需求：**

1. **"应用修改"不生效** — `_apply_all()` 中 `BatchFontformatCommand.redo()` 的 `_first_redo` 跳过机制导致实际块的 fontformat 从未被修改，仅更新了内存中的代表副本。
2. **操作顺序错误** — 修改在创建命令之前执行，构造函数捕获的是新状态而非旧状态，undo 无法正确还原。
3. **离线页面不更新** — 修改离线页块的数据后缺少画布重建机制。
4. **一键应用预设** — 希望从已保存的字体样式预设中选取应用到当前风格的所有块。

**改动：**

1. **修复应用不生效**（`ui/fontstyle_manager.py`）：
   - 拆分 `_apply_all()` 操作顺序为：① 创建命令（捕获旧状态）→ ② push 到撤销栈 → ③ 直接应用到所有块 → ④ `updateSceneTextitems()` 全局刷新
   - 新增 `_apply_changes_to_blocks()` 统一处理当前页（`set_fontformat`）和离线页（`blk.fontformat = new_ffmt`）的修改

2. **一键应用预设**（`ui/fontstyle_manager.py`）：
   - Batch Edit 区新增 "Preset" 行：`QComboBox`（列出 `utils.config.text_styles`）+ "Apply Preset" 按钮
   - 新增 `_load_presets()` 加载预设列表
   - 新增 `_apply_preset()` 复用完整 apply 流程：创建命令 → push → 直接应用 → 全局刷新 → 同步控件值
   - `show_entry()` 中调用 `_load_presets()` 保持下拉与最新预设同步
   - 新增 `_make_change_dict_from_ffmt()` 以预设 `FontFormat` 直接构建 change list

3. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - `StyleDetail` 上下文新增 6 条翻译（Preset / Apply Preset / (Select a preset) / (unnamed) / Apply preset style）
   - 重编译为 1070 条

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1070 条 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/fontstyle_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

## 2026-07-21

### 上游 PR #1238 调研：独立文字缩放与倾斜变换

**需求：** 调研上游 https://github.com/dmMaze/BallonsTranslator/pull/1238 的内容，评估可学习点，形成文档后暂搁置。

**调研结论：**

该 PR 为 Advanced Text Format 添加了四个独立变换维度（Horizontal Scale 10%–400%、Vertical Scale 10%–400%、Box Slant -85°–85°、Glyph Slant -45°–45°），15 文件 ~5800 行净改动。

**核心亮点：**
- `text_glyph_renderer.py`（新）— 只读字形级倾斜渲染引擎，路径优先 + 栅格回退
- `text_transform.py`（新）— 旋转补偿矩阵解决 Qt 旋转先于 setTransform 的问题
- 多边形 shape control 使 Box Slant 后手柄仍然贴合
- 预览系统 + 批量更新合并防抖
- 项目格式版本化迁移

**决定：** 暂搁置，功能太大不急于实装。详细调研文档见 `docs/上游PR-1238-文字变换调研.md`。

**涉及文件：** `docs/上游PR-1238-文字变换调研.md`（新）、`docs/README.md`

---

### 术语表提取：导出崩溃 + 结果持久化 + i18n `\n` 陷阱修复

**问题/需求：**

1. **导出崩溃**：`_on_save()` 引用不存在的 `pcfg.lastdir`（`ProgramConfig` 无此属性，且该文件未 import `pcfg`），AttributeError。
2. **结果不持久**：对话框关闭后提取条目丢失，再次打开需重新提取。
3. **i18n 中文不显示**："Note" 列头和保存确认对话框虽在 `.ts` 有对应条目，运行时仍显示英文。

**根因与修复：**

1. **导出崩溃**（`ui/glossary_extractor_dialog.py:366`）— `pcfg.lastdir` → `""`（直接用文件名作默认路径）。

2. **结果持久化**（`ui/glossary_extractor_dialog.py` / `ui/mainwindow.py`）：
   - `__init__` 新增 `existing_entries` 参数，传入即有历史结果时恢复显示
   - 新增 `done()` 重写，对话框关闭前将 `self._entries` 存到 `self.parent()._glossary_extractor_entries`
   - `mainwindow.py` 打开对话框前用 `getattr(self, "_glossary_extractor_entries", ())` 取回
   - 效果：条目存活于 MainWindow 实例，随主窗口生命周期

3. **i18n `\n` 陷阱**（`translate/zh_CN.ts`）：
   - **根因**：`.ts` 是 XML，其中 `\n` 是**字面反斜杠+字母 n**，不是换行符。但 `self.tr("...\n...")` 的 `\n` 是 Python 转义得到真正的换行符（0x0A）。两边字符串不同 → Qt 的 ELF hash 不匹配 → 翻译查找失败 → 回退显示英文。
   - **修复**：将 `GlossaryExtractorDialog` 上下文中 3 组 `<source>`/`<translation>` 的 `\n` 替换为真正的换行符。
   - 同步新增 `Previously extracted {} terms.` 翻译条目。

**验证：** 语法检查 ✅、i18n 检查 ✅（无缺失条目）、qm 编译 1065 条 ✅、Qt QTranslator 运行时查找全部返回正确中文 ✅

**涉及文件：** `ui/glossary_extractor_dialog.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 全局搜索 UI 布局调整 + 替换后界面卡死修复

**需求/问题：**

1. **UI 布局**：全局搜索替换输入框比查找输入框窄太多，右侧搜索区域下拉栏占空间过多，替换框宽度应与查找框一致。
2. **替换后界面卡死**：多次替换操作后界面持续显示"内容已更新，请按回车刷新搜索"，按回车无响应，搜索和替换功能失效。

**根因分析：**

**UI 布局问题：** `hlayout_bar1`（查找行）将 `search_editor` 放在独立的 `hlayout_bar1_0` 子布局中（可自由伸缩），而 `hlayout_bar2`（替换行）让 `replace_editor` 与 `range_label` + `range_combobox`（固定 300px）平铺在同一层，导致替换框被严重挤压。

**替换后卡死根因：**

1. **焦点问题** — `replace_editor`（替换输入框）也是 `SearchEditor`，按下 Enter 会发射 `enter_pressed` 信号，但该信号**没有被连接**。替换操作完成后，用户焦点通常落在替换框（刚输完替换文本），此时按 Enter → 信号发射但无人监听 → 用户感觉"没反应"。
2. **线程重入** — `on_replace()`（简单替换）启动后台线程后，若线程尚未结束用户再次点击"Replace All"，`self.start()` 被 Qt 忽略（线程已在运行），新设置的 `job` lambda 在旧线程结束时被 `self.job = None` 覆盖，第二次替换静默丢失。

**改动：**

1. **UI 布局**（`ui/global_search_widget.py`）：
   - `ConfigComboBox` 改为 `fix_size=False` + `setMaximumWidth(120)`，不再固定 300px
   - `hlayout_bar2` 拆分为 `hlayout_bar2_0`（`replace_editor` 伸缩区）+ `hlayout_bar2_1`（`range_label` + `range_combobox` 紧凑区），完全镜像 `hlayout_bar1` 结构

2. **替换框回车响应**（`ui/global_search_widget.py`）：
   - `replace_editor.enter_pressed` 连接到 `commit_search()`，焦点在替换框时按 Enter 也能触发重新搜索

3. **线程忙碌守卫**（`ui/global_search_widget.py`）：
   - `on_replace()` 入口增加 `if self.replace_thread.isRunning(): return`，防止线程重入导致替换丢失

**验证：** 语法检查 ✅

**涉及文件：** `ui/global_search_widget.py`

---

### 过界模式（Overflow Mode）— 画布边界视觉指示 + 文字块跨边界裁剪

**需求：** 翻译画布边缘文本时，文字块常超出图片边界，难以判断边界位置和处理溢出内容。

**改动：**

1. **配置项**（`utils/config.py`）：
   - `ProgramConfig` 新增 `overflow_mode: bool = False`

2. **画布视觉指示**（`ui/canvas.py`）：
   - 新增 `drawForeground()` 重写：图片边界外区域半透明暗化遮罩（`QColor(0,0,0,60)`）+ 红色 1px 边界线（cosmetic pen）
   - 新增 `_overflow_scene_rect()`：过界模式开启时，场景矩形向四周扩展 30%（`OVERFLOW_MARGIN_RATIO=0.3`），使滚动条可滚动到画布外
   - 修改 `setSceneRect()` 调用（`scaleImage`、`_fitToWindow`、`_set_scene_scale`）统一使用 `_overflow_scene_rect()`
   - 新增 `setOverflowMode(enabled)`：切换模式并刷新场景

3. **文字块渲染裁剪**（`ui/textitem.py`）：
   - 新增 `_get_overflow_clip_rect()` 辅助方法：计算图片边界在 item 本地坐标中的裁剪区域
   - 修改 `paint()` 快速/慢速路径：渲染文字内容前设置 `painter.setClipRect()`，边框和序号 badge 始终在 clip 外部绘制

4. **View 菜单 Toggle**（`ui/mainwindowbars.py`）：
   - TitleBar View 菜单新增 checkable "Overflow Mode" action

5. **信号连接**（`ui/mainwindow.py`）：
   - `overflow_trigger` → `canvas.setOverflowMode`，初始化时同步 `pcfg.overflow_mode`

6. **i18n**（`translate/zh_CN.ts`、`translate/zh_CN.qm`）：
   - TitleBar 上下文新增 "Overflow Mode" → "过界模式"，重编译 1073 条

**验证：** 语法检查 ✅、i18n 检查 ✅（orphan 4 为预存问题）、qm 编译 ✅、启动导入测试 5/5 ✅

**涉及文件：** `utils/config.py`、`ui/canvas.py`、`ui/textitem.py`、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 移除翻译后符号格式强制转换 + 画布徽章编号即时刷新

**问题/需求：**

1. **符号格式强制转换**：`postprocess_translations()` 在 CJK→CJK 场景下调 `full_len()` 强制将全部 ASCII 字符（含标点、字母、数字）转为全角，覆盖了 LLM 模型自身的标点判断能力。用户希望 LLM 自由决定符号格式。
2. **徽章编号不即时刷新**：增删文本框后 `updateTextBlkItemIdx()` 更新了 `blk_item.idx` 和侧栏徽章文字，但未调用 `blk_item.update()` 触发画布重绘，需切页或 hover 才刷新。

**改动：**

1. **移除符号转换**（`ui/mainwindow.py`）：
   - `postprocess_translations()` 中删除 `full_len(blk.translation)`（CJK→CJK）和两处 `half_len(blk.translation)`
   - 保留非 CJK→CJK 场景的标点后多余空格清除（`r'([?.!"])\s+'`）、非 CJK 目标的竖排强制取消、以及大写转换
   - 清理 import：`from utils.text_processing import is_cjk`（移除 `full_len, half_len`）

2. **徽章即时刷新**（`ui/scenetext_manager.py`）：
   - `updateTextBlkItemIdx()` 中 `blk_item.idx = ii` 后追加 `blk_item.update()` 触发画布重绘

**验证：** 语法检查 ✅、启动导入测试 5/5 ✅

**涉及文件：** `ui/mainwindow.py`、`ui/scenetext_manager.py`