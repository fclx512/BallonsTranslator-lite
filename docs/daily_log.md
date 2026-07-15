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