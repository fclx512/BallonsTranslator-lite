# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在对应日期中末尾写入日志。

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