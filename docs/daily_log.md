# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照从新到旧的顺序撰写。

## 2026-06-21

### 动画控制补充：FadeLabel + CollapsibleSection

**需求：** 设置面板"动画"项（`pcfg.animation_fps`）此前只控制了 `OverlaySlider`/`IndicatorListWidget`/`ConfigContent` 等 UI 面板动画，漏掉了两处画面感明显且有性能影响的动画。

**改动：**
1. `ui/custom_widget/label.py` — `FadeLabel.startFadeAnimation()`：动画关闭时跳过 1200ms 淡出，改为全不透明度瞬显、1.2s 后直接隐藏（`hide_timer`）。快速连续缩放时 timer 自动刷新，不会误隐藏。
2. `ui/collapsible_section.py` — `CollapsibleSection.setExpanded()`：动画关闭时直接调用 `_apply_final_state()` 跳转到展开/收起终态，跳过 350ms 的 layout 过渡动画。

**涉及文件：**
- `ui/custom_widget/label.py`
- `ui/collapsible_section.py`

### PP-OCRv6 检测/识别分离

**背景：** 原 `ocr_onnx.py` 通过 `ONNXPaddleOcr` 同时处理文字检测和识别，在实际管线中检测阶段由 `textdetector` 独立完成，OCR 阶段只需识别。两者耦合导致管线职责重叠。

**改动：**
1. `modules/ocr/ocr_onnx.py` — 重构为纯识别模式（recognition-only）：移除检测参数（`det_db_thresh`、`det_db_box_thresh`、`det_db_unclip_ratio`、`max_candidates`、`reading_order`）、移除 `det.onnx` 模型下载和加载、移除中心点匹配逻辑；改为直接使用 `TextRecognizer` 对 `blk.lines` 裁剪区域做批量识别，单次 batched call。
2. `modules/textdetector/detector_paddlev6.py` — **新增**，PP-OCRv6 DBNet 文字检测器，通过 `TextDetector` 独立提供检测功能，与识别模块解耦。

**涉及文件：**
- `modules/ocr/ocr_onnx.py`
- `modules/textdetector/detector_paddlev6.py`

### 移除 OCR 启动强制覆盖

**需求：** 此前 `launch.py` 在启动时将所有非 `none_ocr`/`llm_ocr` 的 OCR 模块强制重置为 `none_ocr`（避免本地模型文件缺失导致崩溃）。现在改为信任用户配置：无配置时默认 `none_ocr`（模块默认值），有用户配置时原样保留。

**改动：** 移除 `launch.py` 中 OCR 强制覆盖代码块，替换为注释说明。

**涉及文件：**
- `launch.py`

### 打开图片时自适应窗口选项

**需求：** 设置面板 Interface 下新增"打开图片时自适应窗口"开关，开启后以 95% 比例自动适配视口；子选项控制切换页面时是否同样自适应（默认关，保持当前缩放）。

**改动：**
1. `utils/config.py` — `ProgramConfig` 新增 `open_image_fit_window`（主开关）、`fit_window_on_page_switch`（切换页面时也自适应）
2. `ui/canvas.py` — 新增 `_fitToWindow()` 方法计算适配比例；`updateCanvas()` 根据配置和标志决定是否自适应；新增 `_fit_to_window` 标志供 MainWindow 设置
3. `ui/mainwindow.py` — `pageListCurrentItemChanged` 中根据 `opening_dir` 和配置设置标志
4. `ui/configpanel.py` — Interface 区段加两个 QCheckBox，子选项随主开关显隐；`interface_layout.setSpacing(0)` → `setSpacing(8)` 修复挤在一起的问题

**涉及文件：** `utils/config.py`、`ui/canvas.py`、`ui/mainwindow.py`、`ui/configpanel.py`

---

## 2026-06-19

### 高级对齐扩展 X 轴对齐

**需求：** 高级对齐支持 X 轴（左/中/右），与 Y 轴互斥选择。

**改动：**
1. `utils/point_alignment.py` — 新增 `_blk_x_bounds()`；`compute_offsets()` 加 `axis` 参数分发 X/Y 计算
2. `ui/point_align_dialog.py` — 顶部加 Axis 单选（X 轴/Y 轴），切换时动态更新坐标标签（X:/Y:）和对齐模式单选按钮文字（左/中/右 ↔ 顶/中/底）。信号 `pick_y_clicked` → `pick_clicked`，方法 `target_y()` → `target_value()`，`set_picked_y()` → `set_picked_value()`
3. `ui/canvas.py` — 泛化拾取模式：移除 `y_picking`/`y_pick_line`/`y_picked`，改为 `_pick_axis`/`_pick_line`/`position_picked`。`enter_y_pick_mode()` → `enter_pick_mode(axis)`，X 轴时画垂直品红线
4. `ui/mainwindow.py` — `on_open_advanced_align`/`execute_advanced_align` 加 `axis` 参数，视觉偏移方向随轴变化（`QPointF(dx,0)`/`QPointF(0,dy)`）
5. `translate/zh_CN.ts` — PointAlignDialog 新增 7 条翻译；重新编译 qm

**涉及文件：** `utils/point_alignment.py`、`ui/point_align_dialog.py`、`ui/canvas.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

### 移除 PP-OCRv6 字典的冗余下载条目

**问题：** `ppocrv6_dict_proper.txt` 已在仓库中通过 git 跟踪，但被列在 `download_file_list` 中，URL 指向本仓库（循环下载），且地址无效。

**修复：** 移除 `modules/ocr/ocr_onnx.py` 中 `# ── shared dictionary ──` 对应的字典下载条目。文件已由 git 同步，无需额外下载。

**涉及文件：** `modules/ocr/ocr_onnx.py`

### 修复 QRadioButton 暗色模式下不可见

**问题：** Qt 原生单选框渲染忽略 Windows 暗色调色板，indicator 圆圈无显式样式，在暗色背景下难以辨识。
**修复：** 在 `config/stylesheet.css` 中添加 `QRadioButton::indicator` 完整样式（边框、底色、选中态/悬停态/禁用态），配套 `QCheckBox` 文本颜色与间距基础样式。

**涉及文件：** `config/stylesheet.css`

---

## 2026-06-18

### 高级对齐功能实现 + 修复

**需求：** 工具菜单新增"高级对齐"，用户指定 Y 坐标后将选定页面内非旋转文本框的上边/垂直中心/下边对齐到该 Y 值，仅调整垂直位置，支持拾取 Y 值和撤销。

**实现（第1轮）：**
1. `utils/point_alignment.py` — **新增**，纯计算函数 `compute_offsets()`，基于 `_bounding_rect`/`xyxy` 计算 dy
2. `ui/point_align_dialog.py` — **新增**，对话框 UI（Y 输入 + 拾取按钮 + 对齐单选 + RangeSlider 范围选择 + 全页复选框）
3. `ui/canvas.py` — 添加 Y 拾取模式：y_picking 标志、y_pick_line（洋红虚线跟随鼠标）、y_picked 信号、exit_y_pick_mode()/restore_drag_mode()
4. `ui/mainwindowbars.py` — "工具"菜单增加 "Advanced Alignment" 条目
5. `ui/mainwindow.py` — **新增** `_PointAlignCommand`（QUndoCommand，跨页数据+当前页视觉撤销）、`on_open_advanced_align()`（QEventLoop 避免 hide() 取消模态）、`execute_advanced_align()`
6. `ui/configpanel.py` — 注册 `advanced_align` 快捷键（默认无）

**修复（第2轮）：**
- 拾取值应用后无响应 — 根因：dialog.hide() 在 exec_() 中导致 Qt 返回 Rejected。改用 show() + QEventLoop 替代 exec_()
- 对话框关闭后画布拖拽残留 — exit_y_pick_mode() 不恢复 ScrollHandDrag，统一在 on_accepted/on_rejected 中调用 restore_drag_mode()
- "All Pages" 默认勾选时横条未禁用 — setChecked(True) 移到 toggled.connect() 之后
- 规范设计文档 `docs/superpowers/specs/2026-06-18-point-alignment-design.md` 删除

### 工具栏与对话框 i18n 补全

**问题：** 工具栏和工具对话框中有多处硬编码英文未包裹 self.tr() 或 ts 条目缺失：Quick Symbol 分组名、高级对齐窗口标签、画布 PREVIEW 提示、快捷键 "Advanced Alignment" 等。

**修复：**
1. `ui/quick_symbol_dialog.py` — 分组名 `self.tr(group_name)` 包裹
2. `ui/point_align_dialog.py` — `QLabel("Y:")` → `self.tr("Y:")`
3. `ui/canvas.py` — `"PREVIEW"` → `self.tr("PREVIEW")`
4. `translate/zh_CN.ts` — 补全 PointAlignDialog、QuickSymbolDialog、TitleBar、_ShortcutRow、MainWindow 等 20+ 条目；重新编译 zh_CN.qm

**涉及文件（两次改动合并）：** `ui/mainwindow.py`、`ui/canvas.py`、`ui/point_align_dialog.py`、`ui/mainwindowbars.py`、`ui/configpanel.py`、`utils/point_alignment.py`、`ui/quick_symbol_dialog.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

