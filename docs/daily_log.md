# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-07-15

### PaddleOCRv6ONNX — 三处 Bug 修复（OCR 不输出 + 宽文本截断 + 依赖误报）

**问题：** paddleocr_v6_onnx 模型加载时反复出依赖安装失败警告，且 OCR 输出为空或残缺——即便检测框完整包裹文本，长文本后半部分也丢失。

**修复：**

1. **批处理填充逻辑 Bug**（`modules/ocr/ocr_onnx.py:217-241`）：
   - GPU 模式下 `_make_uniform_batch_rec` 用 `img_list[:pad_count]` 取填充元素。当 `pad_count > len(img_list)` 时（如 1 个 crop、`batch_num=6` → `pad_count=5`），切片只返回 1 个元素而非 5 个，导致总 batch 仅 2 个 → `results[:-3]` 为空列表
   - 修复：改用 `(img_list * repeats)[:pad_count]` 循环重复，确保精确填满

2. **GPU 宽度裁剪导致长文本截断**（`modules/ocr/ocr_onnx.py`）：
   - 原 GPU patch 将 `resize_norm_img` 输出强制裁剪到 320px 宽，但 ONNX 模型输入为动态宽度（`['DynamicDimension.0', 3, 48, 'DynamicDimension.1']`），原生支持任意宽度
   - 宽文本行（如 500px crop → 缩放到 48px 高后 480px 宽）右侧 160px 被切掉，后半部分丢失
   - 修复：移除宽度裁剪部分，保留纯批次填充（不影响精度）

3. **`ensure_dependencies` 对 onnxruntime-gpu 误判**（`modules/base.py:327`）：
   - `requires_packages` 写 `"onnxruntime"` 但安装的是 `onnxruntime-gpu`，metadata 名称不匹配 → `PackageNotFoundError` → pip 重装失败 → 警告
   - 修复：metadata 找不到时回退 `importlib.import_module(req.name)` 直接导入确认

**涉及文件：** `modules/ocr/ocr_onnx.py`、`modules/base.py`

### QColor RGB 越界警告修复

**问题：** `QColor::fromRgb: RGB parameters out of range` 警告在运行日志中反复出现。`FontFormat.frgb`/`srgb` 字段无类型约束，多处直接访问原始字段传入 `QColor()` 未做钳位。

**修复：**

1. **改用安全方法**（4 处）：
   - `ui/text_style_presets.py:227` — `fontfmt.frgb` → `fontfmt.foreground_color()`
   - `ui/textitem.py:1425` — `fontformat.frgb` → `fontformat.foreground_color()`
   - `ui/fontstyle_manager.py:174,183` — `ffmt.frgb`/`sfmt.srgb` → `foreground_color()`/`stroke_color()`

2. **内联钳位**（12 处）：
   - `ui/textitem.py:1536` — `QColor(*value)` 加 `max(0, min(255, int(c)))`
   - `ui/fontstyle_manager.py:773,790` — `_pending_fg`/`_pending_stroke_color` 钳位
   - `ui/shadow_gradient_dialog.py` — 全部 7 处 `QColor(*[...])` 加钳位
   - `ui/drawingpanel.py:451` — `QColor(*color)` 加钳位

**涉及文件：** `ui/text_style_presets.py`、`ui/textitem.py`、`ui/fontstyle_manager.py`、`ui/shadow_gradient_dialog.py`、`ui/drawingpanel.py`

---

### install_cuda.bat 集成 onnxruntime-gpu

**需求：** onnxruntime（CPU）与 onnxruntime-gpu 是独立包且互斥。用户有 CUDA 显卡但 ONNX Runtime 无 `CUDAExecutionProvider`，OCR 阶段被降级到 CPU。

**改动：**

1. **CC 映射表**增加 `ONNX_RT_SPEC` 列：CC≥7 选 `onnxruntime-gpu>=1.20,<1.29`（CUDA 12.x 构建，13.x driver 向下兼容），CC≥6 选 `>=1.17,<1.19`（CUDA 11.8 构建）

2. **Step 2b**（新）：检测 `onnxruntime.get_available_providers()` 中是否有 `CUDAExecutionProvider`，输出 GOOD/CPU/MISSING

3. **Step 6**：PyTorch 安装增加跳过逻辑——若 `torch.__version__` 含 `+cu` 则跳过

4. **Step 7**（新）：`pip uninstall onnxruntime -y` + `pip install onnxruntime-gpu<版本约束>`

5. **报告区**：逐个组件显示 SKIP/Install 状态，用户知情后再开始下载

6. **Manual 模式**：同时打印 PyTorch 和 ONNX 两条命令

7. **修复两个 bug**：
   - `>`/`<` 未转义导致 `ONNX_RT_SPEC` 变量被重定向截断 → 改用 `set "VAR=VAL"` 语法
   - `nvidia-smi` 找不到时 `subprocess.run` 崩溃 → 改用 `os.popen`

**涉及文件：** `install_cuda.bat`

---

### Smart Reorder — 网格排序后处理（Tools 菜单）

**需求：** PP-OCRv6 按行检测，排序 `(center_y, center_x)` 导致跨象限文本块顺序 zigzag（如角色介绍分布在四角时左上→右上混排），后续合并工具按距离合并后语序错乱。

**改动：**

1. **新增 `sort_by_grid()`**（`utils/textblock.py:1043`）：
   - 将画布划分为 `grid_rows × grid_cols` 网格，块按质心分配到网格单元
   - 按阅读顺序遍历单元（LTR/RTL），单元内按 `(y, x)` 排序
   - 直接解决象限 zigzag：同一区域块连续 → 合并工具语序正确

2. **入口位置**：
   - 顶部栏 **Tools → Smart Reorder…**（与「Region Merge Tool」并列）
   - 弹窗选预设：LTR / RTL / **2×2 Grid** / 3×3 Grid / Custom
   - 确认后即时重排 + 刷新画布和文本列表

3. **新增文件/改动**：`utils/textblock.py`（+45 行）、`ui/mainwindow.py`（SmartReorderDialog + on_smart_reorder）、`ui/mainwindowbars.py`（Tools 菜单项）

**验证：** 语法检查 ✅、qm 编译（969 条）✅、启动测试 ✅

**涉及文件：** `utils/textblock.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`translate/zh_CN.ts`

---

### 模块管理器依赖检查 — 兼容 onnxruntime-gpu

**问题：** `ui/module_manager.py` 检出模块依赖时用 `importlib.metadata.distribution("onnxruntime")` 按包名查找。因 `onnxruntime-gpu` 与 `onnxruntime` 互斥，安装 GPU 版后元数据名不匹配导致 `PackageNotFoundError`，对话框误报"缺少 onnxruntime"。

**修复：** 在 `PackageNotFoundError` 处理中增加回退——`importlib.import_module(req.name)` 直接尝试模块级导入。`onnxruntime-gpu` 提供同名的 `onnxruntime` 顶层模块，导入成功则不报缺失。该模式与 `modules/base.py:327-336` 的 `ensure_dependencies()` 一致。

**涉及文件：** `ui/module_manager.py`

---

### 右键菜单自定义对话框 — 暗色模式修复 + UI 优化

**问题：** 对话框的列表区域使用 `setStyleSheet()` + `palette(window)` 引用系统调色板，在 Windows 上始终解析为亮色，覆盖了全局 stylesheet 的暗色变量。同时默认拖拽指示器（粗黑线）挡视野、无拖拽手柄提示。

**改动：**

1. **渲染方案更换**（`ui/context_menu_config.py`）：
   - 删除 `_MenuPreviewDelegate`（native `CE_MenuItem` 绘制），改用标准 QListWidget 默认渲染
   - 删掉 `list_widget` 的本地 stylesheet 中所有 `palette()` 引用，背景/文字/边框色从全局 stylesheet 继承（自动适配亮/暗主题）
   - 分隔线改用 `QFrame(HLine)` 作为 item widget，不再依赖 delegate

2. **UI 交互优化**：
   - 拖拽指示器改为 2px 细线 + `palette(highlight)` 颜色，不再挡视野
   - 列表项前面加 `⠿` 拖拽手柄视觉提示
   - 增加 ↑↓ 移动按钮（选中项后点击），作为拖拽的替代方案
   - 列表区域 `border-radius: 6px` 圆角 + `::item:hover` 悬停高亮
   - 基于 fontMetrics 的固定按钮宽度，防止中/英文标签在多语言下被裁剪

3. **顺手修复**：
   - `_on_add_separator` 插入的分隔符未创建 QFrame 部件
   - 移动操作时 `takeItem` 会删除 item 的 widget，加 `setParent(None)` 保护

**涉及文件：** `ui/context_menu_config.py`

---

### Tools 菜单重排 + PSD 封存 + 路径重排工具

**需求：** Tools 菜单工具过多需重新分组；PSD 导出有兼容问题需封存；智能重排改为画路径排序。

**改动：**

1. **Tools 菜单重排**（`ui/mainwindowbars.py`）：
   - 重新分为 4 组：「页面布局工具」「文字/样式工具」「导出/批量处理」「外部工具」
   - 减少分隔线，逻辑更清晰

2. **PSD 导出封存**：
   - 菜单项灰色禁用，文本标注 "(维修中)" + tooltip 说明原因

3. **路径重排工具（替换 Smart Reorder）**：
   - **`ui/canvas.py`**：新增路径绘制模式。用户拖拽画路径（笔刷半径 20px），文本框首次被路径碰到时高亮选中并显示序号。松手后发射触碰顺序。`enterReorderMode()` / `exitReorderMode()` 等方法
   - **`ui/textitem.py`**：新增 `_reorder_seq` 属性，重排模式下 badge 显示触碰序号而非 `idx+1`
   - **`ui/mainwindow.py`**：删除 `SmartReorderDialog` 内嵌类和 `on_smart_reorder`；新增 `on_path_reorder()` + `_on_reorder_path_done()`，弹窗三选一「应用/继续绘制/取消」
   - 块索引用 `id(blk)` 身份映射，避免 dataclass `__eq__` 崩溃
   - 块列表从 `canvas.textLayer.childItems()` 取（含未保存的新增块），而非 `current_block_list()`

**涉及文件：** `ui/mainwindow.py`, `ui/canvas.py`, `ui/textitem.py`, `ui/mainwindowbars.py`, `translate/zh_CN.ts`, `translate/zh_CN.qm`

---

### 文字块合并 — 修复视觉扩张 + 重新加入右键菜单

**问题：** 此前实现的文字块合并（Merge Text Blocks）后端完整（`MergeTextBlksCommand` + undo/redo）但 UI 已暂撤：
1. 合并后文本框视觉区域未扩张——`setPos`/`setTextWidth` 不动 `_display_rect`，`boundingRect()` 仍返回宿主原尺寸
2. 右键菜单无入口（仅可通过无默认绑定的快捷键调 LTR 方向）

**改动：**

1. **视觉扩张修复 + xyxy 格式转换**（`ui/scenetext_manager.py`）：
   - `MergeTextBlksCommand.redo()/undo()` 中替换 `setPos` + `setTextWidth` 为 `setRect(xywh, padding=False)`
   - **⚠️ 关键：** `setRect` 调用 `QRectF(*list)` 期望 `[x, y, w, h]` 格式，而 `merged.xyxy` 是 `[x1, y1, x2, y2]` 格式。直接传 `xyxy` 会导致 `width=x2`、`height=y3`（异常增大）。修复：传 `[x1, y1, x2-x1, y2-y1]`

2. **边界计算用实际位置**（`_build_merged_blk`）：将 `blk.xyxy`（保存时的原始坐标）改为 `b.absBoundingRect()`（item 当前可视位置），避免因拖动导致并集偏移

3. **右键菜单集成**（`ui/context_menu_config.py`）：
   - 新增 `_build_merge()` 子菜单函数：选中 ≥2 块时启用，提供 "Left-to-Right" / "Right-to-Left" 两个方向
   - 注册 `CmdDef("merge", "Merge", build_fn=_build_merge)` 到 `COMMAND_REGISTRY`
   - 将 `"merge"` 加入 `DEFAULT_ORDER`（在 `"align"` 与 `"snap_alignment"` 之间）
   - 新增 `_merge_default_order()`：已有保存配置的用户自动将新 `DEFAULT_ORDER` 项（如 `merge`）插入到其前驱项之后

4. **配置默认同步**（`utils/config.py`）：`context_menu_order` dataclass 默认也加上 `"merge"`

**验证：** 语法检查 ✅、qm 编译（1003 条）✅、i18n 检查 ✅、启动测试 ✅

5. **撤回定位修复**（`MergeTextBlksCommand`）： 
   - `undo()` 原来用 `survivor_original_blk.xyxy`（保存到文件时的原始坐标）恢复位置。若用户拖动过块再合并，此值过时导致撤回后宿主跳到旧位置
   - 修复：合并瞬间用 `survivor.absBoundingRect()` 记录实际位置 `survivor_original_xyxy`，撤回时用此值恢复

6. **Merge 改一级按钮 + 方向设置移至 Behavior 子菜单**：
   - 用户反馈：Merge 是频繁操作，二级菜单增加点击层级。改为单次点击，方向设置放入新 "Behavior" 子菜单
   - `_build_merge` 从子菜单改为单 `QAction`，方向根据 `pcfg.merge_rtl` 动态决定
   - 新增 `_build_behavior` 子菜单：内含 Snap Alignment + Merge Right-to-Left + Normalize Breaks and Shrink 三个勾选项
   - `DEFAULT_ORDER` 中 `"merge"` 后跟 `"behavior"` 替代原 `"snap_alignment"`；`"normalize_breaks_shrink"` 从一级菜单移除
   - `normalize_breaks` 改为检查 `pcfg.normalize_shrink` 决定是否收缩（`emit(pcfg.normalize_shrink)`）
   - 新增配置 `pcfg.merge_rtl: bool` + `pcfg.normalize_shrink: bool`（`utils/config.py`）

**验证：** 语法检查 ✅、qm 编译（1005 条）✅

**涉及文件：** `ui/scenetext_manager.py`、`ui/context_menu_config.py`、`ui/mainwindow.py`、`utils/config.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

## 2026-07-13

### 帮助系统框架（HelpDialog）

**需求：** 软件内帮助文档阅读器，支持文档浏览、标题导航、跨文档搜索。

**改动：**

1. **`ui/help_dialog.py`（新）** — `HelpDialog(QDialog)` 完整实现：
   - 非模态窗口，左侧栏（文档列表 + 本节目录），主内容区 `QTextBrowser.setMarkdown()`
   - 跨文档全文搜索，结果以 PanelGroupBox 风格渲染到主内容区（主题色自适应），点击跳转

2. **`ui/mainwindowbars.py`** — Help 菜单新增"使用手册" action

3. **`ui/mainwindow.py`** — 连接信号 + `show_help_dialog()` 懒加载

4. **`translate/zh_CN.ts`** / **`.qm`** — 新增 HelpDialog 上下文翻译 12 条

5. **`tests/test_startup_imports.py`** — 新增 HelpDialog 导入测试 + 静态方法测试

6. **`docs/help/测试文档.md`（新）** — 用于验证样式渲染和标题跳转的测试文档

⚠️ **当前状态：框架已实现，文档正文和体验细节需后续细化。**

**涉及文件：** `ui/help_dialog.py`（新）、`ui/mainwindowbars.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_startup_imports.py`、`docs/help/测试文档.md`（新）

## 2026-07-14

### HelpDialog UI 重构 — Tab 栏 + 折叠目录 + Scroll-Spy

**需求：** 原有帮助系统对"文档少（3~5 篇）、单篇长"的场景适配不佳——文档列表挤占目录空间、目录平铺无折叠、滚动时目录不追踪当前位置、整体直角 UI 过硬。

**改动要点：**

1. **文档切换从侧栏列表 → 顶部药丸 Tab 栏**：每个文档一个 QPushButton（`HelpDocTab`），用动态属性 `current` 标记选中。释放侧栏全部空间给目录。

2. **目录树形折叠**：QListWidget → QTreeWidget，H1/H2/H3 层级折叠（H1 展开、子级默认折叠），原生缩进替代全角空格模拟。

3. **Scroll-Spy**：文档加载后用 `QTextBlock.blockFormat().headingLevel()` 构建标题块号映射。`verticalScrollBar().valueChanged` → 150ms debounce → `cursorForPosition(QPoint(0,0))` 取 viewport 顶部块号 → 反查最近标题 → 高亮树节点。

4. **导航防抖**：点击目录导航时设 `_navigating` 标志，100ms 后释放，避免 scroll-spy 与 `find()` 触发的滚动互相拉扯。

5. **搜索栏下移**：从顶栏移到侧栏底部（目录树下方），与原有替换内容区的搜索结果展示配合。

6. **全局圆角化 & 主题集成**：在 `config/stylesheet.css` 新增 10+ 条 HelpDialog 专用 QSS 规则（全部使用 `@variable` 主题变量），Tab 8px 圆角药丸、目录树 8px、Content 8px，选中态 `accentPrimary20` 半透明背景。搜索结果 HTML 同行圆角化（`border-radius: 8px`），高亮行用 `rgba(accent, 0.15)` 替代硬编码灰底。

**涉及文件：** `ui/help_dialog.py`（重写）、`config/stylesheet.css`（追加）、`translate/zh_CN.ts`（删"文档"+增"关键词"/"点击结果跳转到对应位置"）、`translate/zh_CN.qm`（重新编译）

---

### HelpDialog — 字体/代码块/搜索修复

**问题：**
1. 英文字符（尤其 `{}`）显示为宋体风格——body CSS 的 `font-family` 与 `setMarkdown()` 内部字体格式冲突
2. 代码块无视觉区分——CSS 对 `<pre>` 无效，因为 `setMarkdown()` 内部设了 inline QTextCharFormat 覆盖 document stylesheet
3. 搜索跳转错位——全文 `find()` 对重复文本跳到第一个出现处

**修复：**

1. **字体**：移除 body CSS 的 `font-family`，改用 `document().setDefaultFont(QFont("Microsoft YaHei", 10))`——document 级默认字体优先级低于 setMarkdown 的 block 级格式，互不冲突

2. **代码块**：新增 `_style_code_blocks()` 程序化后处理——`setMarkdown()` 后遍历所有 block，检测 `charFormat().fontFixedPitch()` 识别围栏代码块，直接设置 `QTextBlockFormat` 的 background/margin。连续代码块合并 margin 消除块间间隙

3. **搜索跳转**：搜索结果锚点从 `(doc_idx, matched)` 扩展为 `(doc_idx, matched, heading)`。跳转时先 `find(heading)` 将光标定位到目标小节，再 `find(matched)` 在正确区间内匹配

**涉及文件：** `ui/help_dialog.py`

---

### HelpDialog — 代码块/搜索/间距统一 + 已知问题文档

**问题/需求：**
1. 代码块样式仍未呈现——`fontFixedPitch()` 对绝大多数代码块返回 False
2. 搜索跳转仍不准——heading 范围限定被代码块/正文中相同文字截胡
3. 正文区右侧、上下零间距；侧栏"本节目录"标签与正文区顶部不对齐；搜索框与正文区底部不对齐；目录树左侧展开图标被裁剪

**改动：**

1. **代码块检测修复**（`_block_is_code_block()` 静态方法）：放弃 `fontFixedPitch()`，改用 `fontFamilies()` 检测 Courier New 等等宽字体。两端扫描（样式 + 合并连续间隙）均用新方法。（⚠️ 检测已修，但视觉效果仍未达预期，见 `docs/已知问题.md`）

2. **搜索导航简化**：放弃 heading 限定，`_search_anchors` 从 `(doc_idx, matched, heading)` 降为 `(doc_idx, matched)`，点击结果直接全文 `find(plain)`。（⚠️ 重复文本仍跳到首次出现处，见 `docs/已知问题.md`）

3. **间距统一**：
   - QSplitter 边距 `(0, 8, 0, 8)` → `(8, 8, 8, 8)`，左右 8px 让正文区右边框不再贴窗边
   - QTextBrowser 加 `document().setDocumentMargin(8)`
   - 去掉"本节目录"标签（`_outline_label` 整条删除）
   - Sidebar 边距 `(8, 8, 8, 8)` → `(8, 9, 8, 9)`，上下 9px 匹配正文区 `1px border + 8px docMargin`，目录框顶部与正文区首行平齐、搜索框底部与正文区尾行平齐
   - 目录树去掉展开折叠箭头（`rootIsDecorated(True)→False`），缩进 16→12，CSS `::branch` 隐藏

4. **`docs/已知问题.md`**：新文件，记录代码块样式和搜索跳转两个待修问题的根因与后续方向。

**涉及文件：** `ui/help_dialog.py`、`config/stylesheet.css`、`docs/已知问题.md`（新）

---

### 文字块合并功能（后端，UI 已暂撤）

**需求：** 右键菜单支持合并多个选中文字块为一个，支持 LTR/RTL 方向控制合并后文本排列顺序。

**改动要点：**
1. **信号+后端**（`ui/canvas.py`、`ui/scenetext_manager.py`）：添加 `merge_textblks` 信号 + `MergeTextBlksCommand`（完整 undo/redo）+ `_build_merged_blk` 合并逻辑（xyxy 并集、原文/译文串联、过滤空白译文）
2. **快捷键**（`ui/configpanel.py`、`ui/mainwindow.py`）：注册 `merge_blks`（默认无绑定），触发时默认 LTR
3. **UI 暂撤**：右键菜单条目已删除，因文本框扩张未生效 + 交互层级待优化。后端保留，通过快捷键可调
4. **已知问题：** `setPos`/`setTextWidth` 无法让 TextBlkItem 视觉区域覆盖合并后的 xyxy 并集，需后续排查 TextBlkItem 的 boundingRect/paint 逻辑

**涉及文件：** `ui/canvas.py`、`ui/scenetext_manager.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`