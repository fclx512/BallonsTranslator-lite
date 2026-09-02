# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-09-02

### 术语工作台迁入主窗口左侧栏 + 面板 i18n 补全 + worker 未创建崩溃修复

**问题/需求：** 实机反馈三问题：① 工作台此前误做成嵌字页窄栏 RailDockPanel（画布区浮层），应放主窗口左侧栏位（与全局搜索同槽位）并给更宽面板；② 面板视觉全是英文——zh_CN.ts 里 GlossaryAgentPanel/GlossaryAgentWorker 两个 context 的 24 条翻译全为空串（`<translation type="finished" />`），另有模块级表 `_STOP_REASONS`/`_ORIGIN_LABELS` 未包翻译；③ 未发送过指令时（worker 尚未创建）编辑梗概后焦点离开 → `eventFilter` → `_on_synopsis_edited` 读 `self._worker` NoneType AttributeError。

**改动要点：**

- 左侧栏迁移：`ui/mainwindow.py` 把 `GlossaryAgentPanel` 直接嵌入 `mainHLayout`（leftStackWidget 与 centralStackWidget 之间，global_search 旁），常量 `WORKBENCH_WIDTH = 460`（全局搜索 300 的加宽版）；复用 `_animate_panel_width` 宽度动画；新增 `_showWorkbenchPanel`/`_hideWorkbenchPanel`/`on_set_glossary_widget`，与页面列表、全局搜索三方互斥（`_showSearchOverlay`/`_showPageListOverlay` 反向隐藏工作台）。入口换到 `ui/mainwindowbars.py::LeftBar` 的 `glossaryChecker`（objectName 供 QSS 挂图标）。
- 右侧入口拆除：`ui/text_panel.py` 删 `install_glossary_launcher`/`_ensure_glossary_dock` 等 4 方法及 `_iter_docks`、面板恢复元组里的 glossary 项；`ui/scenetext_manager.py` 删安装调用；`utils/config.py::ProgramConfig` 删失效字段 `glossary_dock_open`（旧 config.json 残留键由 nested_dataclass 静默忽略）；死图标 `icons/rail_glossary.svg` 删除并在 `scripts/audit_registry.json` 登记。
- i18n：ts 填 24 条空翻译 + 新增 8 条（origin 标签 base/AI/you、`(no reply)`、3 条停止原因、LeftBar 的「术语与剧情」）；`_STOP_REASONS`/`_ORIGIN_LABELS` 在字面量定义处用 `QCoreApplication.translate` 显式标注上下文（注意：`translate(...)` 括号内尾逗号会让 i18n_check 的提取正则失配 → 误报孤儿）；FontFormatPanel context 的孤儿条目「Glossary & Story」删除。
- worker 兜底：`GlossaryAgentPanel.showEvent` 即 `_ensure_worker()`（对齐文档「打开工作台时的基底载入」，草稿不再等首条指令）；全部用户编辑路径（梗概/双表/删除/停止/预填充）统一经 `_ensure_worker()` 兜底，NoneType 崩溃不可能复现。
- 验证：`tests/test_glossary_agent_panel.py` 新增 None-worker 回归测试（8 例全过）；`verify.py` 除既有 tmp 缺失外全绿；实机目视验收——左栏书本图标开合、三页中文（对话/术语表/剧情、原文/译文/备注/来源、全局梗概/页段摘要）、梗概编辑焦点离开无报错、与页面列表互斥正常、控制台零 traceback。

**涉及文件：** `ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/glossary_agent_panel.py`、`utils/config.py`、`config/stylesheet.css`、`icons/leftbar_glossary.svg`（新增）、`icons/leftbar_glossary_activate.svg`（新增）、`icons/rail_glossary.svg`（删除）、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/audit_registry.json`、`tests/test_glossary_agent_panel.py`、`docs/技术实现/翻译agent化_设计方案.md`、`docs/技术实现/翻译管线现状调研.md`

---

## 2026-09-01

### agent 模式翻译报错修复（配置字段声明错类 ×2）+ pcfg 字段静态校验兜底

**问题/需求：** 实机反馈 agent 模式无法翻译报错。排查确认并非计划未完成导致：`agent_translation_debug_log` 声明在 `ProgramConfig` 而 trans_agent 读 `pcfg.module.` → agent loop 每轮状态回调 AttributeError 必崩、永远静默回退直译（回退再失败才弹「翻译失败」错误框）；另术语工作台会话轮读不存在的 `pcfg.source_lang/target_lang` 必崩。系「新增配置字段未声明进对应 Config 类」第三次翻车（前有 glossary_dock_open）。

**改动要点：**

- 字段挪进 `utils/config.py::ModuleConfig`（含 `__post_init__` bool 归一；旧 config.json 残留键由 nested_dataclass 静默忽略，安全）；`ui/glossary_agent_panel.py` 改读 `pcfg.module.translate_source/translate_target`。
- 新增 `tests/test_config_fields.py`：AST 静态校验全仓 pcfg 引用链与 Config 类声明一致（47 文件 607 链；休眠拷贝 editing/、formatting/ 排除；类方法与 hasattr 兼容读取走白名单），同类 bug 此后有测试兜底；AGENTS.md 配置系统节加纪律条目。
- 验证：离线复现（真实 config + 脚本化 client 三路径：无 project 直提 / 带 project 探索后提交 / 单框 plain）修复前全崩、修复后全通；全量 pytest 564 绿；verify 全绿；实机已验收。

**涉及文件：** `utils/config.py`、`ui/glossary_agent_panel.py`、`tests/test_config_fields.py`（新增）、`AGENTS.md`、`docs/daily_log.md`

---

### 工作台阶段 3 剧情注入管线 + 阶段 4 ai_tools 瘦身 + 计划销账

**问题/需求：** agent 翻译修复验收通过后推进剩余节点：工作台阶段 3（剧情注入翻译管线）、阶段 4（ai_tools 瘦身）；核对翻译 agent 化 6a/6b 状态并销账。

**改动要点：**

- 阶段 3 剧情注入：全局梗概（项目级 `llm_compact_memory`，工作台「应用」写盘）进 agent system 稳定前缀——`modules/translators/agent/prompts.py` 新增 `synopsis_section`/`effective_history_budget`，梗概为强制注入项、先于可选历史页扣预算（驱逐后地板 1：`build_history_snippet` 把 0 视为不限额）；`trans_agent.py::_run_agent_task` 接线；开关 `pcfg.module.llm_story_context`（默认开，仅影响翻译注入）。
- **持久化补洞**：工作台 `apply_story` 此前经 `setattr` 写的 `llm_compact_memory` 未进 `to_dict`/`load_from_dict`——项目保存后梗概会丢；`utils/proj_imgtrans.py::ProjImgTrans` 补字段声明 + 序列化往返 + 坏值归一。
- 阶段 4 ai_tools 瘦身：776→约 195 行，删旧 AI 助手全部残留（含写类工具的 `TOOL_DEFINITIONS`、`get_active_tools` 过滤、set_*/search_replace/translate_text 执行器、`parse_tool_calls`/`parse_changes`、get_config），只留 `ToolError`/`to_openai_tools`/`execute_tool`（收窄 4 只读：list_pages/read_pages/search_blocks/get_page_info，与 agent 侧 `READONLY_TOOLS`、工作台侧 `CONTEXT_READ_TOOLS` 白名单对齐）。
- 设计文档销账：`docs/技术实现/翻译agent化_设计方案.md` §7 注入表加梗概行、§12 加 `llm_story_context`、§13 阶段 6a 标注「已由术语工作台整体取代」、6b 标注「验收已达（Prefill frequency 拉模式 + origin 三态标记 + 人工「应用」入库）」。
- 测试：新增 `tests/test_story_injection.py` 9 例（system 梗概段 / 预算驱逐 / 只读 reader / 持久化往返 / 编排接线三态）；AGENTS.md ai_tools 条目描述同步。
- 验证：全量 pytest 573 绿；`verify.py --full` 全绿；待实机验收（工作台产梗概→应用→整页翻译，确认 agent 轮次 system 带梗概、译名随剧情一致）。

**涉及文件：** `utils/proj_imgtrans.py`、`utils/config.py`、`utils/ai_tools.py`、`modules/context_agent/story.py`、`modules/translators/agent/prompts.py`、`modules/translators/trans_agent.py`、`tests/test_story_injection.py`（新增）、`docs/技术实现/翻译agent化_设计方案.md`、`AGENTS.md`、`docs/daily_log.md`

---

## 2026-08-31

### 效果栈渲染层落地 + exit 127 硬崩修复（阶段 C 完成收尾）

**问题/需求：** 接手阶段 C 中断交接——全量 pytest 83% 处 `test_text_transform_engine` 变换往返测试 Qt 硬崩（exit 127，faulthandler 抓不到、崩溃点随调用路径漂移）。

**改动要点：**

- 根因：移植转换脚本丢符号的残留——`ui/text_engine/effects/renderer.py::paint_stroke` 引用未导入的 `VerticalTextDocumentLayout`（本地别名 `EngineVerticalTextDocumentLayout`），NameError 经 Qt 虚回调 → PyQt6 abort → fast-fail；另补 `LOGGER` 导入（`utils.logger` 惯例）。全 effects 包未定义名 AST 扫描清零。教训：port 转换产物必须做未定义名扫描；monkeypatch 包装须处理 staticmethod，否则插桩自身造伪 TypeError 误导排查。
- `tests/test_text_transform_engine.py::test_zero_glyph_slant_restores_effects_inside_nonlinear_stack` 适配新渲染器惰性重绘语义：新 `finalize_neutral_cache` 与上游逐字一致、只标脏缓存不再同步 repaint（旧 v1.5.9 版会同步 repaint_background），patch 块内补 `_render_scene` 触发重建；`_transformed_effect_state` 断言换 `_preview_effect_raster_state`。
- 验证：全量 pytest 478 passed + 1 skipped；`verify.py --full` 全绿。计划文档第七节交接内容删除、阶段 C 标完成。

**涉及文件：** `ui/text_engine/effects/renderer.py`、`tests/test_text_transform_engine.py`、`docs/技术实现/效果栈移植与窄栏图标重绘_计划.md`

---

### 面板效果设置写入断链修复（实机验收缺陷）

**问题/需求：** 实机验收发现描边/阴影/渐变设置全部无效。根因：fork 双格式惯例（`ui/text_panel.py::set_textblk_item`/`get_fontformat()` 深拷贝解耦渲染副本与模型）× 新渲染器按上游语义读 `blk.fontformat` canonical 栈——面板写入只落渲染副本，渲染器不可见。引擎层/测试层不受影响（测试直写模型）。

**改动要点：**

- `ui/text_engine/item.py` 新增 `_commit_effect_fields`：canonical 栈 FontFormat 深拷贝探针 + legacy 视图写入 → `ui/text_engine/effects/renderer.py::set_text_effects` 提交（同时写 model+render 两格式，`_finish_effect_transition` 自带完整失效链：opacity 应用、描边对齐、padding、缓存策略、repaint、update）。
- 七个效果 setter 接入（setStrokeWidth/setStrokeColor/setShadow/setBGAttribute/setGradientAttribute/setGradientEnabled/setOpacity）；`setOpacity` 上游模式化（native 透明度由 `_apply_effective_opacity` 应用）；`setBGAttribute`/`setGradientAttribute` 对非 legacy 名保留原通道。
- `set_fontformat` bulk 路径：描边宽+色合并一次提交，色只随非零宽度进栈（0 宽卡是 neutral，`paint_item` 已短路，无伪影）。
- 新增回归 `tests/test_textblkitem_effect.py::test_legacy_setters_reach_canonical_stack_after_panel_decoupling`（解耦后七 setter 写透 canonical + 渲染像素跟进）；全量 pytest 479 passed + 1 skipped、`verify.py --full` 全绿。

**涉及文件：** `ui/text_engine/item.py`、`tests/test_textblkitem_effect.py`、`docs/技术实现/效果栈移植与窄栏图标重绘_计划.md`

---
