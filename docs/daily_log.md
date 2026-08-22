# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

## 2026-08-22

### 上游 v1.5.12 移植节点 2a 收尾提交推送 + 节点 2b 竖排双引擎

**问题/需求：** 完成文本引擎迁移的竖排环节——2a（文件落地 + 渲染入口切换）收尾并提交；随后按规划推进节点 2b（竖排双引擎 + 竖排特性），把 fork 竖排的三个排版设置与縦中横/半宽括号行为接回引擎布局。

**改动要点：**

- **节点 2a 聚合提交 `c097b41` 并推送（用户手动）**：阶段一 26 文件落地 + `editing/upstream_commands.py` + i18n 94 条补齐（qm 1284 条）+ 渲染入口切换（`ui/textitem.py` 改为引擎 TextBlkItem 的 fork 兼容子类，引擎补 `vertical_rotation_chars` 等兼容面）+ 节点 4b 重排闪退修复聚合为一个原子提交。
- **节点 2b 竖排三设置接通引擎布局**（`ui/text_engine/vertical_layout.py`）：构造读 `pcfg` 默认补 `punctuation_position` / `tatechuyoko_threshold` / `halfwidth_jp_corner_brackets` 三成员 + `setPunctuationPosition` setter——configpanel 三处 hasattr 防护调用自动接通，UI 零改动。标点偏移分支按 Simplified（右上/右对齐）/Traditional（居中）切换：`centers_vertical_glyph` 的 PAUSEORSTOP 改随 `punctuation_position`（修复 2a 切换后竖排标点从右上变居中的回归），ALIGNCENTER 补 Simplified 右对齐分支。
- **縦中横自动检测注入**：移植 fork `find_tatechuyoko_runs`（`[A-Za-z0-9]+` run 长度 ≤ threshold）到引擎，`layoutBlock` 中把检测 run 注入注解驱动的 `text_combine_ranges`（与显式 `<tcy>` 注解不重叠），复用引擎 setNumColumns / tate_chu_yoko 渲染。
- **半宽角引号紧凑**：`layoutBlock` 的 `needs_vertical_rotation` 分支加 `PUNSET_CORNER_BRACKET` 保持字形宽度 advance；`updateDrawOffsets` 旋转分支移植 fork 的 opening-mark 上移修正。
- **清理项**：`ui/text_engine/effect_renderer.py` clone-stroke 路径改实例化引擎布局（竖排构造后设三属性，横排用引擎 `HorizontalTextDocumentLayout`），不再引用 fork `scene_textlayout` 布局类；`ui/text_engine/rendering/glyph_slant.py` TYPE_CHECKING 注解改 `..layout.SceneTextLayout`。
- 新增 `tests/test_vertical_engine.py` 13 例：`find_tatechuyoko_runs` 阈值语义、pcfg 默认值、`setPunctuationPosition` 重排触发、`centers_vertical_glyph` 随 Simplified/Traditional、TCY 注入/禁用/不与注解重叠、全特性竖排渲染冒烟。

**排障记录：** 测试两处期望笔误（连续 run 整段判定、`ab 12` 双 run）已修正；`test_dependency_startup` 1 例失败属基线（launch 环境准备子进程断言，与本次改动无关）。

**实机审查修正（同日）：** 用户实机反馈"竖排全角符号变右对齐"——初版把引擎 CLREQ Mainland 右上分支错误接给了 Simplified，导致全角句读（`PAUSEORSTOP`）右对齐；核 fork 布局确认其句读**从不右对齐**（x 居中 + y 顶部，`punctuation_position` 只影响间隔号 `ALIGNCENTER`）。已回退：`centers_vertical_glyph` 的 PAUSEORSTOP 恢复跟随 `standard_vertical_roman_alignment`（默认 True 居中）、删除 ALIGNCENTER Simplified 右对齐分支；`punctuation_position` 成员与 `setPunctuationPosition` 保留（configpanel hasattr 兼容），但不再参与竖排渲染。縦中横/半宽括号/紧凑标点不受影响，测试断言同步更新，13 例 + verify 全绿。

**涉及文件：** `ui/text_engine/vertical_layout.py`、`ui/text_engine/effect_renderer.py`、`ui/text_engine/rendering/glyph_slant.py`、`tests/test_vertical_engine.py`（新增）、`docs/daily_log.md`

---

### 节点 2b 提交推送 + 节点 2c 注解层：主 UI 注解入口初版

**问题/需求：** 用户验收 2b 后授权提交并继续推进；按规划进入节点 2c（Ruby/着重号/连字/onum/手动 TCY/字距 注解层）。探索确认：annotations 字重适配（引擎局部 `font_weight.py` shim）与渲染接通（horizontal/vertical 布局均已接 ruby/emphasis 绘制）在 2a 已完成，**缺口是主 UI 无 Ruby/着重号/TCY/连字/onum 入口**（引擎 `formatting/panel.py` 面板存在但未被主窗口使用，`editing/manager.py` 内嵌的 TextPanel 未接入 mainwindow）。

**改动要点：**

- **节点 2b 提交 `c19a5a6`**（用户验收后）：竖排双引擎三设置接通 + 縦中横注入 + 半宽角引号 + clone-stroke 改引擎布局 + 实机审查修正（全角句读恢复居中）聚合为原子提交，已授权提交（用户自行推送）。
- **`ui/text_panel.py` 增补 `AnnotationFormatGroup` 初版控件**（保持 fork 面板形态）：着重号（样式/位置下拉）、Ruby（类型/读音/位置 + 应用/移除）、縦中横（QFontChecker）、连字（common/discretionary/contextual 三下拉）、旧式数字（onum 下拉）。信号按名分发到引擎 `TextBlkItem` 的 `setEmphasis`/`setRuby`/`setTateChuYoko`/`setLigatureAxis`/`setOldstyleNums`（走文档级 undo）；`_sync_annotation_controls()` 在 `set_textblk_item` 切换时从 `emphasis_values`/`ruby_editor_values`/`tate_chu_yoko_enabled`/`ligature_axis_value`/`oldstyle_nums_value` 读回（QSignalBlocker 防回环），无选中块（global mode）时禁用。
- **Ruby 无选区防护**：`_on_annotation_changed` 捕获 `RubyValidationError`（无选区/读音空/与 TCY 重叠）→ `QMessageBox` 提示，避免面板崩溃。
- **i18n**：`AnnotationFormatGroup` context 14 条 + `FontFormatPanel` context 1 条（Ruby 对话框标题）补齐 ts，qm 1299 条。
- 新增 `tests/test_annotation_controls.py` 11 例：item 注解 setter/读回端到端（emphasis/tcy/ruby+remove/ligature/onum，Ruby 需先 `startEdit` + 选区）、group 信号载荷与 `set_*` 恢复不发声、面板路由（TCY 与 Ruby 互斥用独立 item）。

**排障记录：** `set_*` 恢复 helper 初版未 blocker 子控件导致发信号（改按控件 `QSignalBlocker`）；Ruby 无选区时引擎抛 `RubyValidationError`（面板捕获提示；测试先进编辑态选区）。

**涉及文件：** `ui/text_panel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_annotation_controls.py`（新增）、`docs/daily_log.md`

---

## 2026-08-21

### 上游 v1.5.12 移植推进（节点 0/1/4/5）+ 计划外批次提交推送

**问题/需求：** 按 `docs/技术实现/移植规划_上游v1.5.12.md` 推进上游 v1.5.12 改动向 fork 迁移；先清理计划外挂起批次，再依序完成底层逻辑修复、数据层、角标/路径重排与杂项核对。

**改动要点：**

- **计划外批次聚合提交 `5dc2522` 并推送**：快捷键系统重构（`default_keys_for` 单源解析 / QShortcut 带 `action_id` 语义翻页抑制 / `sanitize_shortcuts` 配置清洗 / 冲突检测组内去重 / 绑定编辑器 Del·Rst）、`auto_squeeze_after_run` 运行后自动收缩开关、设置面板修正（Original Compare、快捷菜单样式），含 `tests/test_auto_squeeze.py`、`tests/test_shortcut_system.py`。mainwindow.py 中节点 0 的 translateBlkitemList hunk 用 `git apply -R --cached` 从暂存区摘出，保证节点 0 留在工作区。
- **节点 0 底层修复**（verify 全过）：LLM 动态 id Schema + 防注入（`modules/translators/trans_llm_api.py::_json_schema/_parse_response`）、SSL 请求局部 context（`utils/download_util.py`）、blk.xyxy 同步（`utils/textblock.py::sync_xyxy_from_bounding_rect` + geometry/textitem/textedit_commands/mainwindow 钩子）、画布光标生命周期（`ui/canvas.py::set_canvas_cursor`）。openai 图片 base url 按决策跳过（本 fork 无 LLM 图片修复）。
- **节点 1 数据层**：FontFormat 增 5 字段（`standard_vertical_roman_alignment` / `ligature_common·discretionary·contextual` / `oldstyle_nums`）；pcfg 增三键（`auto_tate_chu_yoko`(AutoTateChuYokoConfig 含 `allowed_characters()` + `__post_init__` 校验) / `compact_vertical_punctuation_spacing` / `quick_insert_characters` + `ProgramConfig.load` 坏值防护）；`font_weight` 保持本地 int 实现。
- **节点 4**：角标改绘到文本框外——`ui/textitem.py::_OrderBadgeItem` 独立子项（固定像素、`ItemIgnoresTransformations`、NoCache、高 Z、锚定父项 top-left 上方），`refresh_seq_badge()` 同步点接入 mainwindow 开关 / 画布渲染隐藏与重排进出 / scenetext 重编号；路径重排补**接触顺序排序**——`ui/canvas.py::_segment_rect_entry` 移植（段式命中 + RoundCap + 空段椭圆 + 前进距离排序），替代原 childItems 层序编号。
- **节点 5 杂项**：`280e023` 悬停崩溃已修（`ui/texteditshapecontrol.py::ControlBlockItem.hoverMoveEvent` 补 `blk_item is None` 防护）；`af7f1d0` S 键核对无需动作（本地无透射缩放交互，随节点 2 上游机制到来）；`2103976` 字号/Update 行核对本地已达标（`CONFIG_FONTSIZE_CONTENT` 统一 + App 页顶部 Update 状态行）；`c02102e` grid 手柄跟随核对快照已含上游机制（`grid_control_geometry` + `visual_geometry_changed`/`moving` 连接）。
- 文档：`docs/技术实现/反向移植_规范.md` 序号徽标行符号更新（`_draw_seq_badge` → `_OrderBadgeItem`）；`docs/技术实现/移植规划_上游v1.5.12.md` 节点 0/1/4/5 进度标记；新调研与规划文档入库。

**排障记录：** 4a 初版"框内 paint 直接移出框外"方案被 `DeviceCoordinateCache` 超 boundingRect 裁剪否决（padding 可为 0），改走上游式独立子项；verify 的 docs 步抓出 `反向移植_规范.md` 对已删符号 `_draw_seq_badge` 的失效引用，已同步更新。

**涉及文件：** `modules/translators/trans_llm_api.py`、`utils/download_util.py`、`utils/textblock.py`、`utils/config.py`、`utils/fontformat.py`、`utils/shortcut_conflicts.py`、`ui/textitem.py`、`ui/canvas.py`、`ui/mainwindow.py`、`ui/drawingpanel.py`、`ui/scenetext_manager.py`、`ui/textedit_commands.py`、`ui/text_engine/geometry.py`、`ui/texteditshapecontrol.py`、`ui/configpanel.py`、`ui/pie_menu_editor.py`、`ui/theme_helpers.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_auto_squeeze.py`（新增）、`tests/test_shortcut_system.py`（新增）、`docs/技术实现/上游v1.5.11之后提交调研.md`（新增）、`docs/技术实现/移植规划_上游v1.5.12.md`（新增）、`docs/技术实现/反向移植_规范.md`、`docs/daily_log.md`

---

### 路径重排闪退修复（TextBlock 不可哈希 + 路径项重复 addItem）

**问题/需求：** 路径重排（节点 4b）实机点击后报错闪退：`QGraphicsScene::addItem: item has already been added to this scene` 警告后，`ui/canvas.py::_reorder_extend_stroke` 抛 `TypeError: unhashable type: 'TextBlock'`。

**改动要点：**

- 触及块记账 `_reorder_touched_blocks` 改存 **`TextBlkItem`**（按身份可哈希，对齐上游 `_path_reorder_touched`）；原先存 `TextBlock`——nested_dataclass 定义了 `__eq__` 不再有默认 `__hash__`，`set()` 成员判定直接炸。`_reorder_end_stroke` 改用 `id(item.blk)` 映射回 blk 索引。
- 预览虚线路径项去掉重复 `self.addItem(...)`（`setParentItem(self.textLayer)` 本身已把项送入场景），消除 duplicate-add 警告；退出清理沿用 `removeItem` 对 textLayer 子项的既有模式（canvas.py:859）。
- 新增 `tests/test_path_reorder.py` 回归 5 例：行程顺序编号、单帧快拖跨块按 entry 排序、回头路过不重复编号、预览路径项仅一次入场景（parent 为 textLayer）、退出重排模式清理（`_reorder_seq` 复位/取消选中/路径项移除）。

**涉及文件：** `ui/canvas.py`、`tests/test_path_reorder.py`（新增）、`docs/daily_log.md`

---

### 节点 2a 阶段一：引擎文件落地 + 依赖核对（进行中，交接见 `docs/技术实现/移植交接_节点2a文件落地.md`）

**问题/需求：** 计划未完成部分按序推进，先做小节点 —— 节点 2a 的「文件落地 + 依赖核对」，不切渲染入口（入口切换须与落地分开：本地旧快照已 fork 改写，整体覆盖会破坏现行渲染链）。

**改动要点：**

- **落地 26 个上游缺失文件**至 `ui/text_engine/`（annotations/item/layout/horizontal_layout/vertical_layout/shape_control/cache/font_family/pipeline_formatting、editing/context_menu·manager·widgets、formatting/*、rendering/emphasis·native_document·ruby·tate_chu_yoko、transforms/controls·edit_session·grid_numba·modal·projective_control），剥 `ballontranslator.` 前缀、相对导入深度语义一致（最深 `...` = `ui` 包）原样保留。
- **依赖适配**：新建 `ui/text_engine/font_weight.py`（引擎局部 shim：FontWeight IntEnum + coerce/qt 转换 + HTML 字重 helper，数据表由本地 `fontweight_qt5_to_qt6` 派生——节点 1 决策「不引入上游 FontWeight 枚举/HTML 往返进 utils」的落地形态）；导入改写 5 处（item/annotations/formatting·panel/formatting·commands/pipeline_formatting）；`utils/text_processing.py` 增 seg 家族 + `capitalize_sentences`（seg_ch_pkg 删 prepare_pkuseg 依赖、缺 segmenter 降级逐字符）；`utils/text_layout.py`（layout_text）、`ui/icon_rendering.py`、`ui/adaptive_wrap_layout.py`、`ui/spellcheck.py` 落地；`ui/misc.py` 增 themed_icon_path/url、icon_url；`rendering/indexing.py` 换上游版（增 `_grapheme_ranges`，`_grapheme_count` 语义不变，消费方核对无影响）。
- **冒烟**：43 模块 42 可导入，剩 `editing.manager` 缺上游命令类（`ApplyFontformatCommand` 等 14 名）- 推荐方案已写在交接文档（建 `editing/upstream_commands.py` 放上游 15 类 + 模块函数，本地 commands.py 保持不动，改 manager 导入）。i18n 步 exit 6（94 条新 tr() 缺 ts 条目，上游 `resources/translate/zh_CN.ts` 已含，可批量复制 + qm 编译）。

**涉及文件：** 见交接文档 §2 清单（`ui/text_engine/` 新增 26 文件 + `font_weight.py`、`ui/icon_rendering.py`、`ui/adaptive_wrap_layout.py`、`ui/spellcheck.py`、`utils/text_layout.py`、`utils/text_processing.py`、`ui/misc.py`、`ui/text_engine/rendering/indexing.py`、`docs/技术实现/移植交接_节点2a文件落地.md`（新增））