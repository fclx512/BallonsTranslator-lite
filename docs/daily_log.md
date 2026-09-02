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

## 2026-08-30

### 效果栈移植立项 + 窄栏图标 SVG 重绘（阶段 A，已验收）

**问题/需求：** 上游 dev 新增 `text_effects`（TextEffectStack 效果栈），移植前先定放置方案；用户同时反馈窄栏字体字形图标（あ/●/⤢/◐）难懂，要求换 SVG。方案定稿见 `docs/技术实现/效果栈移植与窄栏图标重绘_计划.md`（第 5 窄栏入口"效果"取代 ◐、整栈不拆、范围裁剪：AI 生成图/滤镜族/纹理/遮罩记入后续待办；格式条件中效果视为预合成仅复制粘贴；描边卡渐进披露）。

**改动要点（阶段 A）：**

- 新增 `icons/rail_annotation|emphasis|transform|effects.svg` 四枚 24px Material 网格单色填充图标（纯 `fill="#96a4cd"`、无 stroke、无 `_activate` 变体）：注解=三点+正文条（原 deco dots 并入 SVG 本体）、着重号=字身框+下圆点、变换=旋转方框+对角箭头、效果=错位双层方片+挖孔（挖孔几何须完全落在非叠区，否则读成月牙缺口）。
- `ui/icon_rendering.py::render_svg_pixmap` 增 `override_fill` 参数：渲染前替换全部 fill，供窄栏状态色（正常=palette 前景色/选中=白/禁用降 alpha）。
- `ui/panel_rail.py::RailLauncherButton` 弃 drawText 字形与 `deco` 参数改 SVG 渲染；accent 选中底/hover 描边/dot 角标自绘逻辑不动。文本样式 launcher 暂借 rail_effects 图标（阶段 D 整体取代时沿用）。
- 排障：预览脚本大图小图叠画造成"图标有伪影"假象（着重号顶部凸起实为 24px 小图叠在 96px 大图上），先隔离渲染再怀疑路径本身。

**涉及文件：** `icons/rail_*.svg`（新增×4）、`ui/icon_rendering.py`、`ui/panel_rail.py`、`ui/text_panel.py`、`tests/test_panel_rail.py`、`docs/技术实现/效果栈移植与窄栏图标重绘_计划.md`（新增）

---

### 效果栈数据层移植（阶段 B，待实机验收）

**问题/需求：** 计划阶段 B——引入上游 TextEffectStack 效果栈数据层，旧字段经视图保持读写兼容，为阶段 C 渲染层与阶段 D 效果面板铺底。

**改动要点：**

- `utils/text_effects.py`（1417 行纯冻结 dataclass）+ `utils/raster_assets.py` 自上游逐字移植，import 路径零改动。
- `utils/fontformat.py`：`text_effects` 哨兵字段 + `opacity`/`stroke_width`/`srgb` legacy 视图（`__getattribute__`/`__setattr__` 直读直写栈，既有管线/UI 透明）+ `__post_init__` 迁移（无载荷从旧字段合成栈；阴影/渐变阶段 C 前保持字段本体）+ `to_serializable_dict` 双写兼容 + `merge` 感知。显式载荷权威、无效值逐项告警降级。
- **偏差**：base_styles 的 text_effects 感知（DIFF_FIELDS/量化）挪到阶段 D 与效果面板一起落——B 就入列会和 legacy 视图产生关联重复 override 条目，且变体面板尚无编辑控件。
- 新增 `tests/test_text_effects_data.py` 7 例：默认中性栈/旧载荷迁移/视图写穿/序列化 roundtrip/显式载荷权威/merge+deepcopy/DIFF_FIELDS 阶段钉。verify --full 全绿（pytest 478 过）。

**涉及文件：** `utils/text_effects.py`（新增）、`utils/raster_assets.py`（新增）、`utils/fontformat.py`、`tests/test_text_effects_data.py`（新增）

---

## 2026-08-30

### 查找替换重构阶段 3：样式管理器 UI 重构落地（两行树 + 预览卡 + 差异优先分组）+ 效果组改只读摘要 + i18n 补全

**问题/需求：** 按设计方案 §5 实施阶段 3：样式管理器左树改两行节点（样式名/签名 + 灰字参数摘要 + 块数），右栏改"预览卡 + 关键参数 chip 行 + 差异优先四组折叠区 + 块分布页 chip"，替换原"13 行只读属性行 + 平铺编辑控件"双视图。实机验收通过后用户反馈两项调整：① 阴影/渐变等复杂效果详细配置占空间且交互效率低——只笼统标记使用了哪些高级效果，可保存样式但不支持编辑相关字段；② 实机多处 UI 显示英文——StyleFormatEditor/StyleDetail/StyleTreeWidget/ModelCheckPanel/UpdateThread 等 context 的 ts 条目全部 unfinished 未填译文（上批次 ts_auto_fill 只加了条目没填翻译）。

**改动要点：**

- **复用控件层 `ui/style_format_editor.py`（新，阶段 4 格式条件编辑器共用）**：字段清单来自 `utils/style_query.py::FIELD_GROUPS`；模块级标签表（`GROUP_TITLES`/`FIELD_LABELS`/枚举取值表）定义处显式 `QCoreApplication.translate("StyleFormatEditor", …)`；`FieldEditor` 单行编辑器 + `FormatGroupCard` 折叠卡（组头状态徽标"与基准一致 / ● n 已修改"，改动组自动展开、差异字段高亮）+ `FormatEditorPanel` 四组面板——变更收集 `changed_values()` 量化 diff 语义与旧 `_collect_changed` 一致（基线 None + 中性默认不算变更，防误报与默认值压 override）；`set_format(only_fields=…)` 变体模式只渲染 override 字段。
- **效果组只读摘要（验收反馈调整）**：阴影/渐变/变换/斜切不建编辑器，组头徽标 `ui/style_format_editor.py::effects_tokens` 笼统标记（激活语义与预览 chips 一致：radius>0 / enabled / 栈非空 / 角度非零），`set_summary_only` 摘要卡不可展开；保存/应用样式时效果字段原样透传——`changed_values`/`current_values`/`sync_into` 均不含效果字段，对应 `_build_control` 的 shadow_*/gradient_*/text_transform/glyph_slant 编辑分支全部删除（§7 效果栈迁移时整组替换为效果卡片栈）。
- **`ui/fontstyle_manager.py` 重写**：左树 `StyleTreeWidget` 两行节点（`_StyleItemDelegate` 自绘：色板环 + 粗体标题 + 灰字摘要 `_base_summary` + 右对齐块数，选中圆角高亮）；右栏 `StyleDetail`——预览卡 `StylePreviewCard`（QTextDocument 近似渲染，竖排逐字堆叠，编辑实时刷新）、chip 行 `_ChipBar`（关键参数 + 效果 token，点击滚动定位分组）、变体模式"重置为基准"（`_reset_variant_to_base` 数据层重建变更走 `_apply_ffmt_changes`）、未分组提升/预设存删、块分布页 chip（点击跳该页首块）。
- **i18n 补全**：ts_auto_fill --apply（删 12 条孤儿 + 补缺）后一次性填掉全部 50 条 unfinished（含 `{}/{} ready`、线程/更新错误多行串等陈旧欠账）+ 手工补 StyleFormatEditor context 缺失的 Shadow/Gradient 两条；qm 重编译（1333 条），i18n_check 零缺失零孤儿。

**排障记录：** ① PyQt6 原生崩溃（exit 127 无回溯）：Python 自定义 QLayout 子类（经典 FlowLayout 模式）在布局激活时段错误，chip 行改手工 resizeEvent 换行布局根治——**PyQt6 下禁止 Python 侧子类化 QLayout**；且 `QLayout(parent_widget)` 构造即安装为顶层布局，再 setLayout 双重安装同样崩溃。② 离屏空字体库下 font_family 组合框回落首项造成 changed_values 误报——`set_format` 把当前字体族补插为下拉首项（顺带根治旧代码"未安装字体被静默替换"的潜伏 bug）。③ `QStyleOptionWidget` 在 qtpy.QtWidgets 不存在，用 `QStyleOption`。④ ts 占位串（`{n} …`）提取器跳过（`extract_tr_calls` 滤 `{`），`{n} transform(s)`/`Stroke {n}px`/`Blocks: {n}` 须手工补 ts 条目。

**验证：** `tests/test_fontstyle_tree.py` 扩至 8 例（两行节点 display role/sizeHint、四组卡与 only_fields 契约、效果组只读后 changed_values 不含效果字段、变体重置回写块数据含 `_find_blk_item` None 兜底）；全仓 pytest 463 通过 1 跳过；verify 六步全绿；`i18n_check` 全绿。实机验收：阶段 3 通过（本次效果组摘要 + i18n 补全待复验）。

**涉及文件：** `ui/style_format_editor.py`（新）、`ui/fontstyle_manager.py`（重写）、`config/stylesheet.css`、`tests/test_fontstyle_tree.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/查找替换与样式管理器重构_设计方案.md`、`docs/daily_log.md`

---

### 阶段 3 二轮验收反馈：折叠组标题修复 + bold 参数弃用

**问题/需求：** 用户复验反馈两点：① 折叠组只显示"与基准一致"徽标、没有组名标题——真 bug，`FormatGroupCard` 组头 QToolButton 从未 `setText`；② 项目已移除粗体样式、视觉字重完全由字体本身的字重（font_weight）承担，弃置的 `bold` 参数没必要再出现在样式管理器/查询体系里。

**改动要点：**

- **组标题修复**（`ui/style_format_editor.py::FormatGroupCard.__init__`）：补 `self._toggle.setText(self.title())`，四组（文本/颜色与描边/排版/效果）组名正常显示。
- **bold 弃用四层移除**：`utils/style_query.py::FIELD_GROUPS` 文本组、`utils/base_styles.py` 的 `_SIGNATURE_FIELDS`（签名聚类）/`DIFF_FIELDS`（变体 diff）/`_BOOL_TOKENS`（变体自动名 B token）、`ui/style_format_editor.py` 字段清单与编辑器分支、`ui/fontstyle_manager.py` 树摘要 `_SUMMARY_TOKENS` 与预览 chips。`utils/fontformat.py::FontFormat.bold` 数据字段加弃用注释仅为旧项目兼容保留（`ui/text_panel.py`/`ui/textitem.py` 的镜像写入不动，渲染兼容不受影响）。
- **测试同步**：`tests/test_style_query.py` bool 条目 bold→italic；`tests/test_base_styles.py` override diff 断言去 bold（并固化"bold 不参与 diff"语义）+ 变体名 token 用例去 B；`tests/test_global_replace_format.py` 快照载体 bold→opacity。
- i18n：ts 清 3 条 Bold 孤儿，qm 重编译（1330 条）。

**验证：** 相关 6 套件 83 例 + 全仓 463 通过 1 跳过；verify 六步全绿。

**涉及文件：** `ui/style_format_editor.py`、`ui/fontstyle_manager.py`、`utils/style_query.py`、`utils/base_styles.py`、`utils/fontformat.py`、`tests/test_style_query.py`、`tests/test_base_styles.py`、`tests/test_global_replace_format.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/查找替换与样式管理器重构_设计方案.md`、`docs/daily_log.md`

---

### 查找替换重构阶段 4：格式条件/替换格式接入全局搜索（BlockQuery + GlobalReplaceApplier）+ 浮层编辑面板三版定稿

**问题/需求：** 设计方案 §6 落地：全局查找加"格式条件"、替换加"替换格式"入口，复用阶段 3 的 FormatEditorPanel（效果组保持只读摘要）。功能落地后实机三轮 UI 验收：① 内嵌面板太挤（模式下拉被截断、清空孤行、240px 高度拦腰截断字段行）；② 改浮层后宽度仍被钳在侧栏内、面板无圆角边框辨识度差；③ 定稿为画布方向全尺寸浮层。

**改动要点：**

- **查询集成**（`utils/style_query.py` + `ui/global_search_widget.py::commit_search`）：查找格式条件读 `ui/global_search_widget.py::_find_format_conditions` 面板实时 diff（相对 `FontFormat()` 默认基线的 eq 量化条件 → FormatPredicate），与文本谓词 AND 门控；格式激活时范围下拉仅作用文本维度；无文本条件时格式-only 命中以无高亮块级条目展示（点击照常跳块）；无效正则 + 无格式条件才报无效。
- **替换集成**（`ui/global_search_widget.py::_collect_replace_targets`）：格式 patch **收集期不写数据**，统一暂存 `format_changes`（old/new_ffmt 深拷贝，契约 = `build_query_changes`），由 `GlobalReplaceApplier` 施加期一次性落点（当前页 live item + 数据层兜底、非当前页直写标脏）；替换模式二选一——按字段 patch（默认）/整块应用大样式（样式下拉取 `proj.base_styles` 深拷贝整 ffmt，字族/竖排身份字段 patch 会触发样式树重发现）；`replace_finished` 信号增列第 4 参 format_changes（`ui/mainwindow.py::on_global_replace_finished` 透传）；回滚条按 (页,块) 去重计数（`_show_rollback_strip`）。
- **浮层编辑面板三版定稿**：① bar3 两个互斥 checkable 按钮（`QToolButton#FormatToggleBtn`）+ 动过字段数 `(n)` 上按钮文案与浮层标题；② 新控件 `ui/custom_widget/float_drop_panel.py::FloatDropPanel`——左缘钉在搜索栏右缘、向画布方向按内容展开（420×480 MIN 常量），Esc/×/再点按钮关闭，无 pcfg 开合记忆无拉伸手柄（FormatEditorPanel 自带滚动），QSS 复用 `RailDock*` objectName（`RailDockPanel, FloatDropPanel` 共用规则），搜索栏收起时 hideEvent 联动关闭；替换页头「模式下拉 + 清空」、样式下拉仅大样式模式显示，查找页头右侧清空。
- **内嵌版废弃原因（持久结论）**：FormatEditorPanel 字段行（字体下拉等）最小宽 ~390px，内嵌参与布局会把整栏最小宽撑到 ~470px，与 300px 动画列宽冲突 → 布局按最小尺寸摆放、超界部分被裁切——用户截图「背景盖住边框圆角」的真身是右边框被裁切到半途，非 QSS 层叠问题（全局 `QTreeView/QListView` 圆角规则经对照实验确认渲染正常）。
- **顺带**：`docs/基础速查/打包控件功能使用说明.md` 删对 `.tmp_probe/style_showcase.py` 临时文件的引用（活文档不引用临时文件）；AGENTS.md 打包控件表补 `FloatDropPanel` 行。

**排障记录：** ① 浮层 QSS 边框不显示——裸 QWidget 子类需 `WA_StyledBackground` 才绘制边框/背景（`Widget` 基类即为此），改继承 `Widget` 解决；② 宿主错钉侧栏（300px）——构造期控件树未挂进主窗口，`window()` 只解析到直接父级，改为**首次打开时惰性解析**（`_ensure_host` 里 setParent 重钉到 `window.centralWidget()`）；③ 主窗口真实结构里 GlobalSearchWidget 与 centralStackWidget 是兄弟（都在 mainHLayout），host 不能用 centralStackWidget（`mapTo` 跨非祖先树无效）；④ 离屏无翻译器时英文按钮文本虚增布局最小宽（489px），诊断须用真实 qm 或短文本区分语言伪影与真实溢出；⑤ 测试替身 FakeProj 必须带 `base_styles` 属性，否则 `_refresh_style_combo` 在 Qt 槽内抛异常触发 PyQt6 qFatal 原生崩溃（无 Python 回溯）。

**验证：** `tests/test_global_search_fontstyle.py::FormatConditionSearchTest` 七项（格式-only 搜索/AND 门控/patch 不写数据/大样式整块/当前页门控 idx 契约）+ 全仓 pytest 470 通过 1 跳过；verify 六步全绿；`i18n_check` 零缺失零孤儿（新增 Format Conditions/Replace Format/Clear/Patch Fields/Apply Base Style/FloatDropPanel::Close 等条目译文已填）。实机验收：功能四象限通过，UI 三版定稿通过。

**涉及文件：** `ui/custom_widget/float_drop_panel.py`（新）、`ui/custom_widget/__init__.py`、`ui/global_search_widget.py`、`ui/mainwindow.py`、`ui/style_format_editor.py`、`config/stylesheet.css`、`tests/test_global_search_fontstyle.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`docs/技术实现/查找替换与样式管理器重构_设计方案.md`、`AGENTS.md`、`docs/基础速查/打包控件功能使用说明.md`、`docs/daily_log.md`

---
