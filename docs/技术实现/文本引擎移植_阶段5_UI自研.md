# 文本引擎移植 阶段 5：UI 自研 — 接手导航

> **本文档是阶段 5 工作的唯一入口文档。** 接手的新 AI（或新会话）请先完整阅读本文，
> 再按需深入第三节"参考路径"中的文件。阶段 5 的详细进度文档按节点**追加到本文档**
> （仿 `文本引擎移植_阶段4_变换系统.md` 的"每节点一节 + 排障备忘"结构），不要另起新文档。

---

## 一、目标（读完本文必须明确的事）

### 1.1 阶段 5 官方目标（路线图 §6，原文摘录）

> - 按本 fork 习惯重新设计变换面板与交互（见第七章），底层只依赖阶段 1–4 的公开接口
>   （`set_text_transform` / `TextTransformState` / 编译产物）。
> - 形状控制升级：矩形 → 跟随视觉轮廓（多边形/handle）。

路线图全文：`docs/技术实现/文本引擎架构跟进.md`（§6 阶段 5、§7 设计边界声明、§5.2/#6、
§5.6）。**先把 §7 读完再动手。**

### 1.2 阶段 5 重挂的本地功能（路线图 §5.6 / §5.2 表格）

以下功能在阶段 3 拆分时已按"保留 + 阶段 5 重挂"处理，代码仍存活在本地：

| 功能 | 现状代码 | 阶段 5 动作 |
|------|---------|------------|
| 序号角标 `show_seq_badge` | `ui/textitem.py:769`（`_draw_seq_badge`）、`utils/config.py:293`、`ui/configpanel.py:1900` | 重挂：与变换/形状控制并存时行为一致 |
| 过界模式 `overflow_mode` / 裁剪 `clip_text_overflow` | `ui/textitem.py:597`、`617`（`_get_overflow_clip_rect`）、`utils/config.py:294-295` | 重挂（路线图 §5.2/#6 标注"风险最高，需 controller sync_origin 语义专门设计"） |
| 对齐吸附（对齐/分布/吸附） | `utils/text_alignment.py`（全文件本地独有）+ `ui/textitem.py:918`（`_apply_snap`） | 重挂：纯交互层，只依赖 setPos/boundingRect 公共接口 |
| `NormalizeBreaksCommand`（跨页换行整理） | `ui/textedit_commands.py:548-658` | **需重做**：按上游命令结构重写 |

### 1.3 设计边界（路线图 §7 摘要）

- **照搬上游（底层）**：`TextTransform` 冻结规范值模型与持久化、几何所有权与输入映射
  机制、编译管线、非线性表面 warp 渲染、撤销命令结构（状态快照 + 多选批量）、交互不变量
  （previous_source 连续性、冻结拖拽坐标系、全量 update）。
- **自研（UI/交互）**：变换参数面板（上游是卡片式变换栈列表，本 fork 按自己的设置面板
  习惯设计）、控制手柄（上游 Grid 覆盖层 / Projective 3D ring gizmo 可参考但不照搬）、
  预览/提交交互（上游拖拽标签 + Escape 取消，本 fork 可沿用打包控件风格）、变换入口位置。
- **明确不引入**：`formatting/`、`editing/` 包结构与上游完全对齐（本地已有
  `fontstyle_manager`、`text_panel` 体系，保持现状）；上游 `resources/` 变换图标与样式；
  Numba 依赖。

### 1.4 用户约束（必须遵守）

> 阶段 5 的 UI 最终样式**暂时没有决定**。实现时**暂且借鉴上游的思路即可**，
> 后续文字样式编辑板块的 UI 会一块全部重做。你认为复杂的部分**该拆就拆**（节点化），
> 且**做完一个节点就写文档**记录，以便上下文压缩后不丢失信息。

推论（不要擅自扩大范围）：

- 变换面板不必做成最终形态，功能通、结构可复用即可，不必花精力打磨视觉。
- 每个节点独立可验收：尽量保持"中性态（空变换栈 + glyph_slant 0）= 零行为变更"，
  阶段 5 新增 UI 不得改变无变换时的渲染/交互行为。
- 节点文档写到本文档（追加小节），重要排障写"排障备忘"小节。

---

## 二、当前基线（接手时的状态）

- **阶段 0–4 全部完成**：模型层（`utils/fontformat.py`）→ 控制器层
  （`ui/text_engine/geometry.py`）→ 渲染层（`ui/text_engine/rendering/` +
  `effect_renderer.py`）→ 变换系统（`ui/text_engine/transforms/` +
  `editing/commands.py` + glyph_slant）。参考 `docs/技术实现/文本引擎移植_阶段4_变换系统.md`。
- **变换测试**：`tests/test_text_transform_engine.py` 共 **21 用例全绿**
  （14 模型 + 1 undo + 5 渲染 + 1 本地縦中横）。完整回归命令见第六节。
- **i18n 基线**：`i18n_check.py --ci` 退出码 **4**（仅 orphan 条目——已知的
  `self.tr(variable)` 间接调用模式，属正常基线）；MISSING 条目为 0。qm 已重新编译。
- **已知 pre-existing 失败（不要追）**：
  1. `tests/test_dependency_startup.py::test_prepare_environment_structurally_correct`
     断言 `prepare_environment` 含 `return True`，而 `launch.py` 只返回 False —— 老债。
  2. `tests/test_psd_binary.py` L14 `import pytest`，而内置 venv 无 pytest —— 老债，
     该文件无法运行。
- 分支 `dev/text-engine-stage2`，工作区当前干净。

---

## 三、参考路径

### 3.1 上游参考（v1.5.10 tag，read-only，勿改）

```bash
git show v1.5.10:ballontranslator/ui/text_engine/transforms/editor.py     # 变换编辑会话（核心）
git show v1.5.10:ballontranslator/ui/text_engine/transforms/panel.py      # 变换面板（卡片式，仅借鉴思路）
git show v1.5.10:ballontranslator/ui/text_engine/transforms/controls.py   # 面板控件（参数行、slider、checkbox 等）
git show v1.5.10:ballontranslator/ui/text_engine/transforms/modal.py      # ModalPointTransform（TRANSLATE/ROTATE/SCALE）
git show v1.5.10:ballontranslator/ui/text_engine/transforms/projective_control.py  # 3D ring gizmo（PROJECTIVE_CONTROL_RADIUS 68.0）
git show v1.5.10:ballontranslator/ui/text_engine/transforms/grid_control.py       # Grid 覆盖层控制
git show v1.5.10:ballontranslator/ui/text_engine/shape_control.py         # ControlBlockItem / TextBlkShapeControl
git show v1.5.10:ballontranslator/ui/text_engine/editing/manager.py       # EditingManager（会话所有权/转发表）
```

- v1.5.9→v1.5.10 delta 极小（仅 `editing/manager.py`、`editing/widgets.py`、
  `geometry.py`、`item.py`、`rendering/glyph.py` 5 文件），**以 v1.5.10 为准**。
- 上游 `transforms/editor.py` 的 `TextTransformEditSession` 关键信号（可参考命名）：
  `transform_commit_requested` / `preview_requested` / `drag_commit_requested` /
  `preview_canceled` / `add_requested` / `remove_requested` / `move_requested` /
  `selected`；核心方法 `select_transform` / `refresh_controls` / `_commit_states` /
  `_sync_transform_controller`（后者演示如何按栈索引分发到 grid/projective 场景控制）。

### 3.2 本地已就绪的底层接口（阶段 5 UI 只依赖这些，勿再改底层）

**控制规格（已移植，`ui/text_engine/transforms/registry.py`）**：

| 符号 | 行号 | 用途 |
|------|------|------|
| `TransformControlSpec` | 54 | 单个变换的控制项规格（参数名/最小值/默认等） |
| `TextTransformVariantSpec` | 72 | 变体规格 |
| `GLYPH_SLANT_CONTROL` | 216 | glyph 倾斜控制规格 |
| `TEXT_TRANSFORM_VARIANTS` | 390 | 4 个已注册变体（bend/sine/grid/projective） |
| `TEXT_TRANSFORM_VARIANTS_BY_TYPE` | 428 | type→变体映射，**必须匹配 `TEXT_TRANSFORM_TYPES`** |
| `compile_text_transform_stack` | 493 | 编译变换栈 → `CompiledTextTransform` |

**几何控制 API（已移植，`ui/text_engine/geometry.py` 的 `TextItemGeometryController`）**：

| 方法 | 行号 | 阶段 5 用途 |
|------|------|------------|
| `canonical()` / `effective()` / `is_neutral()` / `visual_is_neutral()` | 135/144/147/154 | 面板/手柄显示的当前状态 |
| `set(state, preview=False)` / `clear_preview()` | 1224/1294 | 预览/提交的唯一下沉接口 |
| `begin_input_mapping()` / `end_input_mapping()` | 279/283 | 冻结拖拽坐标系（交互不变量） |
| `map_source_to_visual()` / `map_visual_to_source()` | 260/264 | 输入映射（hitTest、手柄→源坐标） |
| `source_handle_points()` | 504 | 源矩形 4 角（源坐标系） |
| `visual_handle_points_in_scene()` | 517 | **视觉轮廓 8 点**（场景坐标系，含变换后位置） |
| `visual_handle_tangents_in_scene()` | 523 | 手柄切线（曲线变换拉直用） |
| `visual_rotation_center_in_scene()` | 539 | 旋转中心（视觉） |
| `visual_bounds_in_scene()` | 544 | 变换后视觉包围盒 |
| `grid_control_geometry(stack_index)` | 307 | grid 变换的网格控制几何 |
| `projective_control_center_in_scene(stack_index)` | 347 | projective 控制中心（3D gizmo 锚点） |
| `capture_scene_to_grid_output_mapper(stack_index)` | 363 | grid 输出坐标→场景的拖拽映射器 |
| `capture_scene_to_source_mapper()` | 477 | 通用拖拽映射器 |

**TextBlkItem（`ui/textitem.py`）**：

| 成员 | 行号 | 用途 |
|------|------|------|
| `set_text_transform(state, *, preview=False)` | 1402 | 变换入口（emit `visual_geometry_changed` 当 changed） |
| `clear_text_transform_preview()` | 1419 | 取消预览 |
| `startEdit(pos)` | 820 | 进入编辑（直接 `self.layout.hitTest`，**注意此处已走输入映射**） |
| `shape()` / `contains()` | 429/435 | 委托 geometry controller（选中判定已感知变换） |
| `setRect()` | 373 | 设置源矩形，emit `visual_geometry_changed` |
| `_get_overflow_clip_rect()` / `_apply_snap()` / `_draw_seq_badge()` | 617/918/769 | 阶段 5 重挂的本地功能 |

**命令层**：`SetTextTransformCommand` 是 `ui/text_engine/editing/commands.py` 唯一类
（65 行，快照式 QUndoCommand，经 `SW.canvas.push_undo_command` 入本地撤销栈）。
形状控制手柄走 `beginResize`/`endReshape` 时复用它的 undo 语义。

**形状控制（当前状态）**：`ui/texteditshapecontrol.py` 的 `TextBlkShapeControl`
（L300，矩形 8 手柄）由 `ui/scenetext_manager.py` 持有（`txtblkShapeControl`，
L525/590/597/621/627 等接线）。**这就是阶段 5 要升级成"跟随视觉轮廓"的对象。**
`ControlBlockItem`（L54）已定义 `DRAG_NONE/DRAG_RESHAPE/DRAG_ROTATE` 三种拖拽模式。
`ui/cursor.py` 存在（上游有 resize/rotate 光标族，可直接参考）。

### 3.3 本地与上游差异速查

| 关注点 | 上游（v1.5.10） | 本地 | 阶段 5 动作 |
|--------|----------------|------|------------|
| 变换编辑会话 | `transforms/editor.py` `TextTransformEditSession` | 无（模型/命令已有） | 新建会话层（可简化） |
| 变换面板 | `transforms/panel.py` 卡片式栈列表 | 无 | 自研（仅借鉴思路） |
| 场景控制 | grid/projective/control + `shape_control.py` | `texteditshapecontrol.py` 矩形版 | 升级形状控制 |
| 撤销 | `SetTextTransformCommand` | 已移植同名单类 | 复用 |
| 选中/绘制 | `item.py` | `textitem.py`（含本地独有渲染） | 不动底层，只接 UI |
| 编辑会话挂接 | `editing/manager.py` + `editing/widgets.py` | 本地有自己的 text_panel/字体面板体系 | **不引入**上游 editing 包 |

---

## 四、建议节点拆分（初版，可调整；每节点完成即写文档到本文档）

### 节点 G — 变换编辑会话层（新建 `ui/text_engine/transforms/editor.py` 本地版）

- 职责：持有目标 items / 当前栈状态 / 预览与提交 / undo 边界；面板与场景控件只跟
  会话对话，不直接碰 `TextBlkItem`。
- 参照上游 `editor.py` 的信号命名与 `select_transform`/`_sync_transform_controller`
  的"按栈索引分发到 grid/projective 场景控制"思路，但按本地 `geometry.py` 的
  公开 API（3.2 表）重写，去掉上游依赖的 `C.active_format` 全局槽（本地格式走
  `utils/fontformat.py`）。
- 验收：合成冒烟——对 1 个 item 走 `add(projective) → preview → commit`，
  undo/redo 状态往返；空栈→回中性态 `is_neutral()` 为 True。

### 节点 H — 变换参数面板 UI（自研，`ui/custom_widget` 风格）

- 用本地打包控件（`ConfigSubBlock`/`NoArrowsSpinBox` 族/`ColorSwatchBtn` 等，
  见 `docs/基础速查/打包控件功能使用说明.md`）渲染 `TransformControlSpec` 参数行。
- 入口位置按用户约束"可沿用打包控件风格"，先放最顺手的路径（如文本编辑面板内
  `QToolButton` 展开，或独立 `OverlaySlider` 面板），**最终位置由 UI 重做时定**。
- 新增 UI 文案一律 `self.tr()` + 同步 `translate/zh_CN.ts`（i18n 规则见第六节）。
- 验收：面板可增删/重排变换、改参数实时 preview、Escape 取消、提交进 undo 栈。

### 节点 I — 形状控制升级：矩形 → 跟随视觉轮廓（`ui/texteditshapecontrol.py` 改造）

- 手柄数据源改为 `geometry_controller.visual_handle_points_in_scene()`（8 点）+
  `visual_handle_tangents_in_scene()` + `visual_rotation_center_in_scene()`；
  角点拖拽经 `begin_input_mapping()`/`map_visual_to_source()` 冻结坐标系后回写源矩形。
- 曲线变换（bend/sine）时手柄沿视觉轮廓排布；`visual_bounds_in_scene()` 兜底。
- 保留既有 `beginResize`/`endReshape` 的 undo 语义（复用 `SetTextTransformCommand`）。
- 验收：projective/grid 变换后选中框跟随视觉轮廓；拖拽角点实时 preview + undo 往返。

### 节点 J — 重挂本地功能

- 序号角标 / 对齐吸附：验证与新的形状控制共存不冲突（吸附本身是纯交互层，改动小）。
- 裁剪/过界模式：**路线图 §5.2/#6 标注风险最高**——`overflow_mode`/`clip_text_overflow`
  与 `geometry_controller` 的 `sync_origin` 语义需要专门设计，先读本地
  `textitem.py:597-660` 与 `geometry.py` 的 `_apply_box`/`sync_origin` 相关段落再动手。
- `NormalizeBreaksCommand`：按上游命令结构重写（参照 `editing/commands.py` 已有的
  快照式命令写法）。
- 验收：每项单独验收，写回基线行为对照。

### 节点 K — 测试 + 全量回归 + 文档

- 新增 `tests/test_text_transform_ui.py`（仿 `test_text_transform_engine.py` 的模块级
  环境引导；offscreen 下可测会话状态机与面板控件，场景拖拽可用 `QTest.mouseMove` 模拟）。
- 全量回归顺序见第六节。

---

## 五、验收口径（延续阶段 4）

- 中性态零变更：空变换栈 + glyph_slant 0 时，渲染与交互与阶段 4 前完全一致。
- 持久化：`TextTransformState` 经 `FontFormat` 序列化不受 UI 改动影响。
- 输入映射：变换后 hitTest/选中/手柄拖拽坐标正确。
- 撤销：所有变换编辑（面板 + 手柄）都是 1 个 undo 步，undo/redo 状态往返。

---

## 六、工作流约束（AGENTS.md 摘要，必须遵守）

1. **不主动 commit**，除非用户要求；改动先展示审查。
2. **验证顺序**（每节点必跑）：
   ```bash
   ./ballontrans_pylibs_win/python.exe scripts/check_syntax.py <改动的文件...>
   ./ballontrans_pylibs_win/python.exe scripts/i18n_check.py     # 涉及 UI 字符串时必做
   ./ballontrans_pylibs_win/python.exe scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm
   ./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py
   cmd //c "set QT_API=pyqt6&&set QT_QPA_PLATFORM=offscreen&&ballontrans_pylibs_win\python.exe -u tests\<文件>.py"
   ```
   （内置 venv 无 pytest，测试逐文件直接运行。）
3. **i18n**：UI 文字用 `self.tr("English")`，严禁硬编码中文；新增字符串同步
   `translate/zh_CN.ts`；`--ci` 退出码 4 = 仅 orphan 属正常基线，MISSING 必须为 0。
   批量编辑 ts 用 Python 脚本直接操作文本。
4. **文档**：节点完成即写文档到本文档；开发日志写 `docs/daily_log.md`
   （仅保留最近 3 天）。
5. 用户已确认的阶段 1–4 工作**不得回改**；本阶段只做 UI/交互层，底层接口冻结。

---

## 七、节点进度（按完成顺序追加）

### 节点 G — 变换编辑会话层【完成 2026-08-05】

#### 改动要点

1. **新建 `ui/text_engine/transforms/editor.py`**（626 行）——上游 v1.5.10
   `TextTransformEditSession`（`transforms/editor.py`，603 行）的本地移植。会话
   独占变换目标/预览/undo 边界；节点 H 面板与节点 I 场景控件只与会话对话，不直接
   碰 `TextBlkItem`。
2. **本地差异（设计决策，对应第四节"节点 G"规划）：**
   - `controls` 参数**可选**（默认 `None`）：所有面板交互以 `if self.controls is
     not None` 守卫，会话状态机在无真实控件下可完整测试。面板（节点 H）挂接后，
     8 个信号自动接线：`transform_commit_requested`→`commit_value`、
     `transform_preview_requested`→`preview_delta`、
     `transform_drag_commit_requested`→`commit_drag`、
     `transform_preview_canceled`→`cancel_preview`、
     `transform_add_requested`→`add_transform`、
     `transform_remove_requested`→`remove_transform`、
     `transform_move_requested`→`move_transform`、
     `transform_selected`→`select_transform`。
   - **去掉上游 `C.active_format` 全局槽与 host（文本面板）依赖**：状态只经
     `item.blk.fontformat` 读写（`_state_for_item`）；保留可选 `global_format`
     属性供"无选中项"路径（`_current_states` 兜底），供节点 H 面板直接改全局格式。
   - `_sync_transform_controller` **保留上游 hasattr 守卫**（
     `hasattr(canvas, 'clear_text_transform_controls')`）：本地 canvas 在节点 I
     补上 `bind_text_grid_control` / `bind_text_projective_control` /
     `clear_text_transform_controls` 后，会话自动开始派发，节点 G 无需改任何代码。
   - 完整移植 grid / projective 模态编辑：`begin_grid_edit` /
     `preview_grid_points` / `commit_grid_points` / `cancel_grid_edit`、
     `begin_projective_edit` / `preview_projective_transform` /
     `commit_projective_transform` / `cancel_projective_edit`，
     `GLYPH_SLANT_INDEX = -1` 哨兵索引走 state 级 `glyph_slant_angle`。
3. **新建 `tests/test_text_transform_ui.py`**（9 用例，节点 K 计划中该文件的雏形）：
   仿 `test_text_transform_engine.py` 的模块级环境引导（`QT_API=pyqt6` + offscreen
   + `QApplication.instance() or QApplication([])`）；会话用上游测试同款驱动方式——
   `SW.canvas = SimpleNamespace(push_undo_command=stack.push)` 喂真实 `QUndoStack`。

#### 验收

| 检查 | 结果 |
| ---- | ---- |
| 节点 G 验收①：1 个 item `add(projective) → preview → commit`，undo/redo 状态往返 | ✅ |
| 节点 G 验收②：空栈 → 回中性态 `_text_transform_is_neutral()` 为 True | ✅ |
| `check_syntax.py`（editor.py + test_text_transform_ui.py） | ✅ 通过 |
| `test_text_transform_ui.py`（新增） | ✅ 9/9 |
| `test_text_transform_engine.py`（阶段 4 回归） | ✅ 21/21 |
| `test_startup_imports.py` | ✅ 5/5 |
| `i18n_check.py` | 退出码 4（仅既有 orphan 基线；无新增硬编码中文、MISSING 为 0） |

#### 排障备忘

- **add 中性值不改变 `is_neutral()`**：`add_transform('bend')` 后
  `_text_transform_is_neutral()` 仍为 True——bend=0.0 是中性值，运行时编译器跳过。
  非中性断言改用 `add_transform('projective')` + `commit_value(0,'rotation_z',15.0)`。
- **mixed stack 语义**（与上游一致）：`commit_value` 要求选中项 stack 形状一致，
  混合形状拒绝提交（count 不增）；`add_transform` **始终允许追加**，混合选择只把
  `selected_index` 置 None。测试 `test_mixed_stack_structures_only_allow_append`
  按此口径写。
- **SimpleNamespace 上的函数属性会被描述符协议绑定**：`SimpleNamespace(
  push_undo_command=lambda c: None)` 访问 `canvas.push_undo_command` 时 lambda 被
  绑定为方法，调用报 "takes 1 positional argument but 2 were given"——统一改用真实
  `QUndoStack` 作 push 目标。
- **信号接线断言**：`connections.items()` 的 value 是 list（`setdefault(_name, [])`
  累积），需内层再遍历 `callable(callback)`。

### 节点 H — 变换参数面板 UI【完成 2026-08-05】

#### 改动要点

1. **新建 `ui/text_engine/transforms/panel.py`**（约 900 行）——本地自研面板，
   融合上游 `controls.py` + `panel.py`，**会话接口与上游逐字节一致**，可直接作为
   `TextTransformEditSession` 的 `controls` 挂接：
   - 8 信号：`transform_commit_requested` / `transform_preview_requested` /
     `transform_drag_commit_requested` / `transform_preview_canceled` /
     `transform_add_requested` / `transform_remove_requested` /
     `transform_move_requested` / `transform_selected`。
   - 面板方法：`set_transform_items` / `set_transform` / `set_active_format` /
     `select_transform` / `clear_transform_selection` /
     `finish_pending_transform_edits` / `cancel_transform_previews` /
     `cancel_pending_transform_edits`。
   - 控件层级：`TransformDragLabel`（`SizeControlLabel` 子类，拖拽预览 +
     Escape 取消）→ `CommittedTransformControl`（IDLE/PENDING_TEXT/DRAG_PREVIEW
     状态机，显示因子/范围钳制/拖拽 Δ 显示/"—"混合占位）→
     `TransformParameterPanel`（变换卡片：标题 + 上移/下移/删除 + 分组参数网格，
     悬停显示操作按钮，选中高亮）→ `TextTransformPanel`（`PanelArea` 可折叠区，
     标题展开态持久化 `pcfg.expand_ttransform_panel`）。
   - 本地化差异：数值编辑器用本地主题 `QLineEdit`（整数编辑器绘制本地
     `icons/chevron-up|down.svg` 步进按钮，仿 page-range 步进器）；Add 菜单纯文本
     （本地无 variant SVG）；样式走 `config/stylesheet.css` 新增的
     `TextTransformParameterPanel` 等 objectName 选择器（`@hoverBackgroundColor`
     卡片、`@accentPrimary` 选中、`@dangerColor` 删除悬停）。
2. **接线 `ui/text_panel.py` FontFormatPanel**（仿上游 formatting/panel.py）：
   - `self.texttransform_panel = TextTransformPanel(self.tr("Text Transform"), …)`
     + `self.text_transform_editor = TextTransformEditSession(self.texttransform_panel)`，
     `session.global_format = self.global_format` 保持同步（无选中项路径写全局格式）。
   - 面板加入 `vl0`（textstyle/textadvanced 之后），并随二者从 View 菜单注册表移除
     （`shared.config_name_to_view_widget.pop("text_transform_panel", None)`）。
   - `set_textblk_item`：`finish_pending_edits()` → 计算 `transform_items`
     （单选中 `[item]` / 多选 `SW.canvas.selected_text_items()` / 无→`[]`）→
     移植上游 `preserve_local_owner`（格式焦点短暂清空画布选中时保留本地 item 作
     会话目标）→ `replace_targets` → 非空时 `panel.set_transform_items`。
   - `set_active_format`：同步 `session.global_format` + `panel.set_active_format`。
   - 生命周期包装：`resolve_…_for_save/history_change/page_change` +
     `cancel_…_for_scene_change`。
3. **接线 `ui/mainwindow.py`**：`closeEvent`/`conditional_save` →
   `resolve_text_transform_edits_for_save`；`pageListCurrentItemChanged` +
   `_on_stylemgr_navigate` → `resolve_…_for_page_change`；`on_undo`/`on_redo` →
   `resolve_…_for_history_change`（对应上游 mainwindow 四处）。
4. **`utils/config.py`**：新增 `text_transform_panel` / `expand_ttransform_panel`
   pcfg 字段（PanelArea 展开态持久化用）。
5. **i18n**：新增 7 条 `self.tr()`（FontFormatPanel "Text Transform"；
   TextTransformPanel "Add"/"Add Transform"/"Mixed"；TransformParameterPanel
   "Move Up"/"Move Down"/"Delete Transform"）+ 补齐 registry.py 中
   `QCoreApplication.translate('TextTransformPanel', …)` 的 30 条（stage 4 遗留，
   面板渲染前未入 ts）——脚本批量写入 `translate/zh_CN.ts` 并重编译 qm。
6. **测试**：`tests/test_text_transform_ui.py` 新增 `TextTransformPanelTest`
   （7 用例）：add 菜单来自 registry 并触发 `transform_add_requested`；卡片/混合
   标签渲染；卡片点击/参数交互选择；**节点 H 验收主路径**——面板菜单 add →
   拖拽 rotation_z 预览（不入栈）→ 提交进 undo 栈 → undo/redo 往返；输入提交 +
   Escape 取消；移除/重排按钮信号；整数编辑器步进与边界钳制。

#### 验收

| 检查 | 结果 |
| ---- | ---- |
| 节点 H 验收：面板增删/重排变换、改参数实时 preview、Escape 取消、提交进 undo 栈 | ✅（`test_value_drag_previews_and_commits_through_session` + `test_typed_value_commits_and_escape_cancels` + `test_remove_and_move_buttons_emit_signals`） |
| `check_syntax.py`（panel.py/text_panel.py/mainwindow.py/config.py/test_text_transform_ui.py） | ✅ 通过 |
| `test_text_transform_ui.py` | ✅ 16/16（9 会话 + 7 面板） |
| `test_text_transform_engine.py` / `test_textblkitem_geometry.py` / `test_textblkitem_effect.py` / `test_proj_compact.py` | ✅ 21/21、8/8、7/7、17/17 |
| `test_startup_imports.py` | ✅ 5/5 |
| `i18n_check.py` | MISSING 归零；退出码 4 仅剩余 orphan（含 30 条 registry `translate` 间接调用基线，AGENTS.md 已记录） |
| `qm_compile.py` | ✅ 1132 条翻译编译成功 |
| offscreen 构造 `TextPanel`（真实 FontFormatPanel→TextTransformPanel→session 链路） | ✅ 面板类型/会话/global_format 同步/Add 菜单均正确 |

#### 排障备忘

- **`shared.register_view_widget` 是注解不是赋值**：`utils/shared.py` 中
  `register_view_widget: lambda …` 是变量注解，属性在 MainWindow 启动时才赋值。
  面板测试须仿上游 `_make_panel` 先装 no-op。
- **PyQt6 信号参数截断**：`Signal(int, int)` 连 `list.append` 这类 1 参 Python
  槽时只传第一个参数（moved 收到 `[0]` 而非 `[(0, 1)]`）——测试用
  `lambda *args: moved.append(args)` 捕获完整元组。
- **Escape 走 eventFilter 不走 keyPressEvent**：`editor.keyPressEvent(escape)`
  直调会绕过 `eventFilter`，需 `QTest.keyClick(editor, Key_Escape)` 走事件派发。
- **`MainWindow` 无法 offscreen 构造**：L307 `FramelessMoveResize.toggleMaxState`
  win32 `PostMessage` 无效窗口句柄（环境限制，早于本阶段任何接线）——用
  `TextPanel` 级构造冒烟替代。
- **面板注册表冲突**：`register_view_widget` 断言 config_name 唯一；新增
  "text_transform_panel" 前须确认未注册。

### 节点 I — 形状控制升级：矩形 → 跟随视觉轮廓【完成 2026-08-05】

#### 改动要点

1. **重写 `ui/texteditshapecontrol.py`**（约 900 行）——移植上游 v1.5.10
   `text_engine/shape_control.py`，本地适配（`startReshape`/`endReshape` +
   `reshaped`/`rotated` 信号流 + 本地 cursor 助手）：
   - **虚线框**：`paint()` 改绘 `geometry_controller.visual_outline_in_scene()`
     映射回 parent 坐标的路径（`updateBoundingRect`），bend/sine/grid/projective
     都会塑造外框；`_updating_bounds` 守卫下 `setRect`/`setPos`/`setRotation`
     只重算轮廓不打架。
   - **8 个手柄**：位置取自 `visual_handle_points_in_scene()`；手柄
     `ItemIgnoresTransformations` 保持固定设备尺寸，`visible_rect` 命中区 +
     外扩法向摆放（`_outward_handle_scene_point`，含视口外代理到边缘的
     `_beginProxyDrag`/`_proxySceneTarget`）。法向/朝向：曲线变换
     （`compiled.needs_local_handle_frames`，本地=非中性 grid/bend/sine 的
     surface mapper 或透视 projective）用 `visual_handle_tangents_in_scene()`
     tangent 对齐（`_handle_frames_device`），否则用全局框法向
     （`_handle_outward_vectors_device`）。
   - **角点拖拽**：`beginResize` 冻结 `capture_scene_to_source_mapper()` +
     初始源手柄 + 对侧视觉手柄；`resizeFromScene` 把场景指针映射回源坐标 →
     计算新源矩形 → `item.setRect(new_abs)` → 对侧锚定补偿
     `item.setPos(item.pos() + parent_delta)`。undo 语义不变：release →
     `endReshape` → `reshaped` 信号 → 管理器推 `ReshapeItemCommand`（本地既有）。
   - **旋转**：`beginRotation`/`rotateFromScene` 围绕
     `visual_rotation_center_in_scene()`；`finishRotationPreview` 先还原模型角
     再 emit `rotated`（`RotateItemCommand` 拥有模型角）。
   - 旧 `previewPixmap`（旋转预览快照）删除——上游直接 `setRotation` 实时预览。
2. **`ui/cursor.py`**：补 `scene_angle_to_cursor_index` /
   `resize_handle_scene_angle` 两个助手（上游 cursor.py 同款，doctest 保留）。
3. **零行为影响确认**：`needs_local_handle_frames` 本地语义=非中性非线性 surface
   mapper 或透视 projective（stage 4 编译：中性变换被跳过 → 中性 grid 也是
   identity，不触发 tangent 帧）。空变换栈/中性态下 `_handle_points` 走
   `rect_polygon` 兜底 → 手柄=源矩形 8 点，与旧矩形控制完全一致（回归为零）。
4. **测试**：`tests/test_text_transform_ui.py` 新增 `TextBlkShapeControlTest`
   （5 用例）：中性态手柄=源矩形（零回归）；projective/grid 变换后手柄=视觉轮廓
   8 点；grid（非中性）触发 tangent 帧；**角点拖拽**：视觉手柄 0 拖到目标、
   对侧视觉手柄 4 精确锚定、`ReshapeItemCommand` undo/redo 往返；**旋转**：
   围绕视觉中心 90° 数学 + `RotateItemCommand` 往返。

#### 验收

| 检查 | 结果 |
| ---- | ---- |
| 节点 I 验收：projective/grid 变换后选中框跟随视觉轮廓 | ✅（`test_control_follows_visual_outline_under_projective/grid`） |
| 节点 I 验收：拖拽角点实时 preview + undo 往返 | ✅（`test_corner_drag_previews_source_rect_and_undo_roundtrip`） |
| 中性态零回归（手柄=源矩形 8 点） | ✅ `test_neutral_item_control_matches_plain_rect` |
| `check_syntax.py`（texteditshapecontrol.py/cursor.py/test_text_transform_ui.py） | ✅ 通过 |
| `test_text_transform_ui.py` | ✅ 21/21 |
| 全量回归（engine 21 / geometry 8 / effect 7 / proj_compact 17 / startup 5） | ✅ 全绿 |
| offscreen 构造 `Canvas`（含 `TextBlkShapeControl` + baseLayer 父子 + updateScale） | ✅ 正常 |

#### 排障备忘

- **本地 `logical_rect()` 原点恒为 (0,0)**：源矩形的位置在 `item.pos()` 里，不在
  rect 的 x/y 里（`source_rect()` = `QRectF(QPointF(), display_rect.size())`）。
  断言 resize 结果要用 `item.absBoundingRect(qrect=True)`，不要用
  `logical_unpadded_rect()`——否则 left/top 永远读 0。
- **旋转/缩放矩阵以矩形中心为 pivot**：`set_rect` → `refresh_compiled_geometry`
  按新中心重算矩阵 → 源矩形边缘相对"对侧视觉手柄"会有合理偏移（旋转 15° 时
  4.5px）。对侧锚定不变量在**视觉手柄**上验证（`_item_handle_points_in_scene`），
  不在源矩形边缘上验证。
- **`beginResize` 的指针位置必须传视觉手柄**：`_beginProxyDrag` 把按下点重定向
  到手柄位置，测试若传源矩形角点会引入代理偏移——传
  `_item_handle_points_in_scene(item)[0]`（等价真实鼠标按在手柄上）。
- **中性 grid 是 identity**：本地编译器跳过 `is_neutral()` 的变换，2×2 默认网格
  = 中性 → `needs_local_handle_frames=False`。测试须先变形网格控制点再断言
  tangent 帧。
- **旋转测试绕开代理**：`beginRotation(scene_pos)` / `rotateFromScene(scene_pos,
  start)` 传 `idx=None` 直接验证 pivot 数学；`idx` 非 None 时代理把手柄位置当作
  按下点（真实输入正确，但遮蔽角度算术）。

### 节点 J — 重挂本地功能（序号角标 / 对齐吸附 / 裁剪过界 / NormalizeBreaks）【完成 2026-08-05】

#### 结论先行

四项本地功能与阶段 5 变换系统**全部共存无冲突**，无需改生产代码——阶段 1–4 移植
时这些功能在 item 层（`textitem.py`）保留完好，阶段 5 只动了 UI/交互层。节点 J
产出 = 逐项验证 + 基线行为对照（测试固化）。

#### 逐项验证（`LocalFeaturesCoexistenceTest`，4 用例）

1. **裁剪/过界（风险最高项）**：
   - 机制梳理：`_get_overflow_clip_rect`（`textitem.py:617`）把画布边界
     `scene.baseLayer.sceneBoundingRect()` 经 `mapFromScene` 映射进 item 本地坐标，
     `_paint_native` 在 SourceOver 合成前 `setClipRect`。native-matrix 路径
     （projective）item 自身变换含矩阵 → clip 边界映射回场景=画布边界，**精确**；
     surface-warp 路径（grid/bend/sine）clip 先作用于源空间再被 warp → 视觉边界
     跟随文字弯曲（可接受的语义偏差，非回归——阶段 4 前无此路径）。
   - 与 `sync_origin`：`set_rect` → `refresh_compiled_geometry` + `sync_origin`
     （只调 transformOriginPoint，不改 pos）→ 节点 I 形状控制拖拽时的矩阵重算由
     对侧视觉手柄锚定补偿覆盖（见节点 I 排障）；`_text_overflows` 在
     `startReshape` 时清除（既有行为）。
   - **验收**：A/B 像素对比——overflow_mode 开 vs 关，边界跨界文字的可见像素
     显著减少（neutral 406 / grid 753），三条路径（neutral/projective/grid）全过。
2. **序号角标**：`_draw_seq_badge` 走 `_paint_native`（变换路径的
   `paint_source`），`show_seq_badge` 开/关 A/B 像素差异断言 + 变换下渲染无崩溃。
3. **对齐吸附**：`_apply_snap` 用 `absBoundingRect()`（源矩形+pos，不受变换影响，
   且 `angle==0` 才吸附——变换不改变 `item.angle`），fake canvas
   （textLayer 子项 + scale_factor + snap 助手）下变换 item 仍正常吸附并画引导线。
4. **NormalizeBreaksCommand**：**上游 v1.5.10 无此命令**（grep 确认），本地
   `ui/textedit_commands.py:548` 已是快照式结构（当前页 `old_html/old_rect/
   old_ffmt` + 跨页 `translation/rich_text` + squeeze 并入）——按文档"参照
   `editing/commands.py` 快照式命令写法"的口径**已达标，无需重写**。验证其
   item 级操作（`set_fontformat(get_fontformat(), set_char_format=True)` +
   `setPlainTextAndKeepUndoStack`）不丢 `blk.fontformat.text_transform`
   （`get_fontformat` deepcopy 含变换栈）。

#### 验收（基线行为对照）

| 本地功能 | 阶段 4 前行为 | 现在 | 结论 |
| -------- | ------------ | ---- | ---- |
| 过界裁剪 | 画布边界裁剪文本 | neutral/projective 精确；surface-warp 边界随弯曲 | ✅ 无回归 |
| 溢出裁剪（clip_text_overflow） | 文本超出框体隐藏 + 黄色提示框 | 不变（startReshape 清除溢出态，拖拽恢复） | ✅ |
| 序号角标 | 左上角序号 | 变换下正常渲染 | ✅ |
| 对齐吸附 | 5px 阈值吸附 + 引导线 | 变换 item 仍吸附（源矩形） | ✅ |
| NormalizeBreaks | 一次 Ctrl+Z 全回退 + squeeze 并入 | 快照式命令已达标，变换保留 | ✅ |

新增测试 4 个（`LocalFeaturesCoexistenceTest`），全量回归全绿（engine 21 /
geometry 8 / effect 7 / proj_compact 17 / startup 5 / ui 25），i18n MISSING 归零。

#### 排障备忘

- **`_apply_snap` 只遍历 `textLayer.childItems()`**：测试 item 必须
  `setParentItem(canvas.textLayer)` 而非 `scene.addItem`，否则目标列表为空、永远
  不吸附（真实 canvas 中 item 都在 textLayer 下）。
- **吸附阈值按场景缩放**：`SNAP_THRESHOLD / canvas.scale_factor`，测试场景
  `scale_factor=1` 时阈值 5px——dragged 与 target 顶边须差 ≤5px 才触发。
- **surface-warp 的过界裁剪是语义偏差不是 bug**：clip 在源空间、warp 在后，视觉
  边界 = 弯曲后的图像边界。要精确直边需在 warp 后 clip（surface renderer 层），
  成本高收益低，留作后续 UI 重做时再评估。

### 节点 K — 测试 + 全量回归 + 文档【完成 2026-08-05】

#### 改动要点

`tests/test_text_transform_ui.py` 自节点 G 起建、逐节点扩展，最终 **25 用例 / 4 个
测试类**，仿 `test_text_transform_engine.py` 的模块级环境引导（`QT_API=pyqt6` +
offscreen + `QApplication.instance() or QApplication([])`），`SW.canvas =
SimpleNamespace(push_undo_command=stack.push)` 喂真实 `QUndoStack` 驱动会话：

| 测试类 | 用例 | 覆盖 |
| ------ | ---- | ---- |
| `TextTransformEditSessionTest` | 9 | 会话状态机：add/preview/commit/undo 往返、中性态、多选结构编辑、controls=None 守卫、信号接线、grid/projective 模态编辑、glyph slant 哨兵、mixed stack append-only、global_format 无选中路径 |
| `TextTransformPanelTest` | 7 | 面板：add 菜单来自 registry、卡片/混合标签、点击/参数交互选中、**拖拽预览→提交→undo 往返**、输入提交 + Escape 取消、移除/重排按钮、整数编辑器步进 |
| `TextBlkShapeControlTest` | 5 | 形状控制：中性态=源矩形零回归、projective/grid 视觉轮廓 8 点、非中性 grid tangent 帧、**角点拖拽视觉锚定 + ReshapeItemCommand 往返**、**旋转 pivot 数学 + RotateItemCommand 往返** |
| `LocalFeaturesCoexistenceTest` | 4 | 本地功能：过界裁剪三路径 A/B、序号角标变换下渲染、对齐吸附变换共存、NormalizeBreaks 变换保留 |

#### 验收（全量回归）

| 检查 | 结果 |
| ---- | ---- |
| `check_syntax.py`（阶段 5 全部改动文件） | ✅ 通过 |
| `test_text_transform_ui.py`（新增，25 用例） | ✅ 25/25 |
| `test_text_transform_engine.py` | ✅ 21/21 |
| `test_textblkitem_geometry.py` / `test_textblkitem_effect.py` / `test_proj_compact.py` | ✅ 8/8、7/7、17/17 |
| `test_startup_imports.py` | ✅ 5/5 |
| `i18n_check.py` | MISSING 归零；退出码 4 = 仅 orphan 基线（含 30 条 registry `translate` 间接调用） |
| `qm_compile.py` | ✅ 1132 条 |
| offscreen 构造冒烟（TextPanel→FontFormatPanel→TextTransformPanel→session 链路 + Canvas→TextBlkShapeControl） | ✅ 正常 |
| `MainWindow` 级构造 | ⚠️ 无法 offscreen（win32 PostMessage 无效窗口句柄，环境限制，早于本阶段接线）——用 TextPanel/Canvas 级冒烟替代，建议用户真实启动 app 目视确认 |

#### 排障备忘（跨节点汇总）

- 阶段 5 五个节点全部完成，节点 G/H/I/J 的排障备忘各自记录在本文档对应小节；
  跨节点共性：PyQt6 信号参数截断（lambda *args 捕获）、offscreen 下
  `MainWindow`/win32 交互不可测、`shared.register_view_widget` 启动才赋值、
  本地 `logical_rect()` 原点恒 0（位置在 `item.pos()`）。
- 遗留评估项（非本阶段阻塞）：surface-warp 路径过界裁剪的弯曲边界（节点 J）；
  变换面板最终位置待 UI 整体重做时定（节点 H 已按"最顺手路径"接入 FontFormatPanel）。

### 节点 L — 验收问题修复【完成 2026-08-06】

用户真实启动 app 验收阶段 5 后报三个问题，均在本节点修复并补测试。

#### 问题 1：高级变换文本框点击/双击时闪现小窗口

- **根因**：`ControlBlockItem.mousePressEvent` 在按下旋转区（可见方块外的
  30px 命中盒）时立即调用 `updateAngleLabelPos()` → 显示 `angleLabel`
  （一个挂在 QGraphicsView 上的小 QLabel，显示 "0.0°"）。变换后视觉轮廓把
  8 个固定设备尺寸手柄带进文本框区域，点击文字即命中手柄旋转区 → 每次点击
  闪现一个"0.0° 小窗"（松开即隐藏）。未变换文本框的手柄在源矩形角上、远离
  文字，故无此现象。
- **修复**：`mousePressEvent` 旋转分支不再调用 `updateAngleLabelPos()`；
  标签只在 `mouseMoveEvent`（真正旋转拖动时）出现。点击不再闪现；拖动旋转
  时角度标签照常显示。
- 附带说明：点击落在手柄命中盒上会进入旋转拖拽模式（不动则不旋转），这是
  手柄设计的既有语义，与上游一致，未改动。

#### 问题 2：网格变换编辑网格数后不显示可拖拽手柄

- **根因**：会话 `_sync_transform_controller` 的 `hasattr` 守卫通过后
  应派发到 canvas 的 `bind_text_grid_control` / `clear_text_transform_controls`，
  但本地 canvas 从未实现这两个方法（节点 I 遗留项）→ 选择 Grid 卡片后无任何
  场景覆盖层。
- **修复**：
  1. 新建 `ui/text_engine/transforms/grid_control.py`——上游 grid_control 的
     **减法版**：网格线采样输出 mapper 绘制（不做 overlay surface renderer）、
     每个控制点一个固定设备尺寸手柄、拖拽经
     `capture_scene_to_grid_output_mapper` 冻结映射 → `begin_grid_edit` /
     `preview_grid_points` / `commit_grid_points` / `cancel_grid_edit` 每次手势
     恰好一轮（零移动自动 cancel）。覆盖层 `shape()` 为空 + 不接受鼠标按钮，
     文字点击穿透；只有手柄可交互。
  2. `ui/canvas.py`：新增 `textGridControl`（挂 baseLayer，z=20）+
     `bind_text_grid_control` / `clear_text_transform_controls`；在
     `_clear_canvas` / `updateCanvas` / `startCreateTextblock` / 空点点击清空
     覆盖层；空点判断改为 `isinstance(item, TextBlkItem) or item.data(
     CONTROL_ITEM_DATA_KEY)`，避免点手柄时误清。
  3. `editor.py` `_sync_transform_controller` 改为**按方法 hasattr 守卫**
     （grid 绑定时 canvas 有 `bind_text_grid_control` 才派发；projective 因
     canvas 未实现 `bind_text_projective_control` 仍走 clear，不再有
     AttributeError 风险）。
- **行为**：选择 Grid 卡片 → 画布出现网格覆盖层 + 每格点手柄；面板改网格数
  → `visual_geometry_changed` → 覆盖层实时按新分割数拆分手柄（2×2→3×2：
  9→12 手柄）；拖手柄实时 preview 扭曲，松开 1 步 undo。

#### 问题 3：文本阴影被文本框范围裁剪（"虚拟框"）

- **根因**：中性路径 `_repaint_neutral_background` 把 shadow 画进恰好
  `boundingRect()` 大小的 pixmap，而 padding 只由旧 `setShadow()` 启发式
  （= max_font_size）维护；`setBGAttribute("shadow_radius", …)` 不更新
  padding。半径/偏移超过该值时阴影被 pixmap 边缘截断——"调大半径出现虚拟框
  裁剪"。非中性路径（`repaint_background`）已有 `_update_effect_padding()`
  而中性路径漏了。
- **修复**：`_repaint_neutral_background` 开头调用
  `self._update_effect_padding()`，使保守 padding（stroke + radius +
  max(|offset|)）覆盖完整阴影。`setPadding` 只增不减，逻辑
  `absBoundingRect` 不变。
- **A/B 验证**：radius=1.5（font 24px）时 padding 24→36，阴影 ink 不再触边；
  offset=(0.6,0.6) 时 padding 33.6，右/下边不再截断。

#### 测试与回归

| 检查 | 结果 |
| ---- | ---- |
| `test_text_transform_ui.py`（新增 `GridTransformControlTest` 5 用例 → 30 用例） | ✅ 30/30 |
| 新增用例 | 手柄数=（h+1)(v+1)（中性 2×2 也显示 9 手柄）；改分割数实时 9→12；拖拽 begin→preview→commit + 零移动 cancel；会话派发 grid 绑定/清除 + projective 走 clear；angleLabel 只在旋转移动时显示 |
| `test_text_transform_engine.py` / `test_textblkitem_geometry.py` / `test_textblkitem_effect.py` / `test_proj_compact.py` / `test_startup_imports.py` | ✅ 21/21、8/8、7/7、17/17、5/5 |
| `check_syntax.py`（canvas / texteditshapecontrol / effect_renderer / editor / grid_control / 测试） | ✅ 通过 |
| `i18n_check.py` | MISSING 归零；退出码 4 = 仅既有 orphan 基线（本批无新增 UI 字符串） |
| Canvas offscreen 构造 + bind/clear 冒烟 | ✅ 正常（9 手柄 → clear 隐藏） |

#### 排障备忘

- **PyQt6 无法实例化 `QGraphicsSceneMouseEvent`**：画布点击级联无法用合成事件
  驱动，手柄命中用 `scene.items(pos)` 静态验证（shape 含文字中心、手柄点落在
  文字区）+ 直接调用 `begin_handle_drag/move/finish` 驱动会话回调。
- **`refresh_controls` 在 `controls=None` 时提前 return**：会话无面板时
  commit 后不触发 `_sync_transform_controller`；真实 app 面板恒非 None，派发
  正常。测试直接走 `select_transform` / `replace_targets` / `detach_scene_owner`
  验证派发。
- **`isVisible()` vs `isHidden()`**：未 show 的 QGraphicsView 下 QLabel
  `setVisible(True)` 后 `isVisible()` 仍 False（父链不可见），断言用
  `isHidden()`。
