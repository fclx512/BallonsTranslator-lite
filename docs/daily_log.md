# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在文档末尾写入。

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

---

## 2026-07-22

### 文档翻新：子目录分类 + 合并精简 + 内容刷新

**需求：** 将散乱在 `docs/` 根目录的 15 个文档进行结构性重组，按内容性质分类存放；合并 README / 项目概述 / AGENTS.md 中的项目介绍部分；合并零散短文为综合性技术文档；刷新过时的行号引用。

**改动：**

1. **新目录结构**：
   ```
   docs/
   ├── 项目概述.md            ← 综合文档（三合一）
   ├── daily_log.md
   ├── 基础速查/              ← 开发日常参考
   │   ├── 快捷键.md
   │   ├── i18n.md
   │   ├── 新增设置项路线参考.md
   │   ├── 打包控件功能使用说明.md
   │   ├── 配置导入导出.md
   │   ├── 依赖库说明.md
   │   ├── 经验教训.md
   │   └── 上游参考.md
   └── 技术实现/              ← 难点技术记录
       ├── 设置面板概述.md    ← 刷新行号引用
       └── 排版技术.md        ← 合并 3 篇
   ```

2. **合并**（`docs/项目概述.md`）：
   - 三合一：原 `README.md`（文档索引）+ 原 `项目概述.md`（架构介绍）+ `AGENTS.md` 项目介绍部分
   - 修正目录树（移除不存在的 `en/` / `zh/` 子目录）
   - 删除原 `README.md`

3. **合并**（`docs/技术实现/排版技术.md`）：
   - 三合一：`标点对齐.md` + `縦中横实现.md` + `上游PR-1238-文字变换调研.md`
   - 删除三篇原文件

4. **刷新**（`docs/技术实现/设置面板概述.md`）：
   - 更新所有行号引用为实际代码行号
   - 移除已删除符号（`_nav_items`、`navList`、`_open_profile_manager` 等）
   - 精简离屏测试脚本（删除使用已删除 API 的部分）
   - 移除对不存在的 `设置面板排布建议.md` 的引用

5. **移动**（无需改动的文件）：
   - 8 个文档移入 `docs/基础速查/`
   - 1 个文档移入 `docs/技术实现/` 后刷新

6. **更新 `AGENTS.md`** 中的文档路径引用。

**验证：** 语法检查 ✅、目录结构确认 ✅（15 文件 → 11 文件 + 2 子目录）

**涉及文件：**
- 新增：`docs/技术实现/排版技术.md`
- 修改：`docs/项目概述.md`、`docs/技术实现/设置面板概述.md`、`AGENTS.md`、`docs/daily_log.md`
- 删除：`docs/README.md`、`docs/标点对齐.md`、`docs/縦中横实现.md`、`docs/上游PR-1238-文字变换调研.md`
- 移动（git mv）：`docs/快捷键.md`、`docs/i18n.md`、`docs/新增设置项路线参考.md`、`docs/打包控件功能使用说明.md`、`docs/配置导入导出.md`、`docs/依赖库说明.md`、`docs/经验教训.md`、`docs/上游参考.md` → `docs/基础速查/`
- 移动（git mv）：`docs/设置面板概述.md` → `docs/技术实现/`

---

### Ruff 全量代码检查 — 244→0 问题清除

**需求：** 对重构后的项目跑 `ruff check .`（已选规则 E/F/I），清理代码问题至零。

**前置条件：** `pyproject.toml` 已配置忽略 E501（行太长），`__init__.py` 忽略 F401。

---

#### 修复流程（按顺序 + 关键陷阱标注）

**1. 自动修复**（`ruff check --fix .`）
- I001 导入排序（39 个）✅、F541 无占位 f-string（19 个）✅、F401 未用导入（42/45 个）✅

**2. `launch.py:24` E402 — BRANCH 在 import 前（预期行为）**
- `from utils.version import APP_VERSION` 在 `BRANCH = "main"` 之后
- 修复：追加 `# noqa: E402`，与同文件 line 20-21 的已有模式保持一致
- ⚠️ **陷阱**：auto-fix 换行后 noqa 落在续行（`APP_VERSION as VERSION,  # noqa: E402`），ruff 仍报 E402。必须将 noqa 放在 `import` 的**头部行**。

**3. `scripts/` + `tools/` 遗留文件跳过**
- 6 个临时分析/调试脚本（上游遗留，待清理）：docstring 后追加 `# ruff: noqa`
- ⚠️ **踩坑**：`tatechuyoko_render_test.py` 的 `# ruff: noqa` 误放进 docstring 内部 → 不生效。必须放在 `"""` 闭合之后

**4. E101 tab/空格混缩进（2 文件）**
- `utils/config.py:305-310` — `context_menu_order` 列表：`\t    "align"` → 8 spaces
- `ui/configpanel.py:1562-1567` — `temp_clean_sublock` 参数区：`\t            note=self.tr(` → 12 spaces

**5. F821 `package_installer.py` 缺 `select` 导入**
- `_run_with_pty()` 中 `select.select()` 缺少 `import select`
- 函数被 `_can_stream_with_pty()` 守卫（Windows 永不执行），但 Unix 路径下是真实 bug
- 修复：函数内本地 `import select`，与同函数 `import pty` 模式一致

**6. F841 未用变量 ×8**

| 文件 | 变量 | 处理 |
|------|------|------|
| `context_batch.py:228` | `total` | 删除 |
| `trans_llm_api.py:961` | `RETRYABLE_EXCEPTIONS` | 删除（异常捕获用内联写法） |
| `configpanel.py:1362` | `dlConfigPanel` | `_DeadBlock` 兼容占位 → `# noqa: F841` |
| `configpanel.py:2252` | `idx` | 改为无赋值调用 |
| `mainwindow.py:2201/2221` | `cancel_btn`×2 | QMessageBox 按钮 → 无赋值调用 |
| `module_manager.py:1070` | `failure_reason` | 删除（从未读写） |
| `normalize_breaks_dialog.py:120` | `current_pname` | 删除（死赋值） |
| `textedit_area.py:511` | `step` | 删除（与 `self._accent_step` 混淆） |

**7. 零散修复**
- `ui/scenetext_manager.py:1167` — E702：分号合并的 4 个 append → 4 个独立语句
- `utils/psd_descriptor.py:161` — E731：`mk = lambda` → `def mk`

---

**最终结果：** `ruff check .` → **All checks passed! ✅**（244 → 0）

**涉及文件：** `launch.py`、`utils/config.py`、`utils/package_installer.py`、`utils/psd_descriptor.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`ui/module_manager.py`、`ui/normalize_breaks_dialog.py`、`ui/scenetext_manager.py`、`ui/textedit_area.py`、`modules/translators/context_batch.py`、`modules/translators/trans_llm_api.py`、`scripts/compare_tysh_items.py`、`scripts/debug_ref_items.py`、`scripts/parse_tysh.py`、`scripts/extract_psd_tysh.py`、`scripts/tatechuyoko_render_test.py`、`tools/无字图配对工具.py`

---

## 2026-07-22

### 上下文翻译解析失败导致整批回退原文 + 字体样式管理器缺失字重控件

#### 上下文翻译批量解析宽容处理

**问题：** 上下文翻译时，LLM 返回的响应缺少某个页面（如写了 `043.jpg` 而非 `043.jpeg`），`_parse_txt_response` 的 all-or-nothing 校验直接 `return None`，3 次重试全部失败后返回 `{}`，导致整个批次所有块回退到原文。术语表虽注入到 system prompt，但因解析失败、LLM 输出被丢弃而用不上。

**改动（`modules/translators/context_batch.py`）：**

1. **`_parse_txt_response`**（第 697-707 行）— 移除严格的 all-or-nothing 校验，改为收集缺失页面列表打 warning，保留已成功解析的部分结果。
2. **`_llm_call`**（第 844-853 行）— 数量校验从 `len(result) != expected_count` 改为只拒绝完全空的结果；部分结果只打 warning 不报错。
3. **`_build_msgs`**（第 502 行）— output format 说明末尾加 `- Include ALL pages listed above — do not skip any.`，从源头减少 LLM 遗漏页面的概率。

**涉及文件：** `modules/translators/context_batch.py`

#### 字体样式管理器新增字重/样式选择控件

**问题：** 用户自建的 `font_weight` 参数（独立于上游）在字体样式管理器的批量编辑区域没有对应控件，创建字体样式时无法设置和保存字重。

**改动（`ui/fontstyle_manager.py`）：**

1. **导入** — 新增 `QFontDatabase`
2. **`StyleDetail.__init__`** — Batch edit 区域 Font Family 下方新增 **Font Style** 下拉框（`_style_combo`），显示当前字体族支持的样式列表（来自 `shared.FONT_STYLES`）
3. **`_sync_controls`** — 同步控件时调用 `_populate_style_combo`，按 `_style_name` → `font_weight` 数值优先匹配选中
4. **新增 `_populate_style_combo`** — 填充样式列表，顶部提供 `(default)` 条目；切换字体族时自动更新
5. **新增 `_on_family_for_style_changed`** — 字体族切换回调
6. **`_apply_all`** — override dict 新增 `_style_name` 和 `font_weight`，从下拉选中计算字重值

**涉及文件：** `ui/fontstyle_manager.py`

---

### CLI 工具：AI agent 项目文本读写助手

**需求：** 需要让 AI agent 能读取和修改项目内的原文/译文，但搞 MCP server 太重（只是偶尔使用）。需要一个轻量方案，且能控制上下文体量——避免一次加载整个项目 JSON（含坐标、遮罩、字体等无关字段）。

**方案：** 利用已有的 `utils/proj_compact.py`（废弃 AI Chat 子系统幸存的两层+分块+选择性字段 API），写一个薄 CLI 包装层。

**改动（`tools/proj_text.py`，新文件）：**

1. **4 个子命令 + 1 个辅助命令：**
   - `index <proj_dir>` — 项目概览（页面列表、块数、字符统计），调用 `build_index()`，体量约 700 字节/3页
   - `read <proj_dir> --pages <spec> [--fields src,trans] [--paginate N]` — 读取指定页面文本，默认只返回 src/trans 字段，超过 N 页自动分块。每块 ~2KB/5页。带 `meta.hash` 供后续校验
   - `search <proj_dir> <keyword> [--field src|trans|both]` — 全文搜索，返回匹配块 ID + 上下文片段，默认最多 50 条
   - `hash <proj_dir>` — 获取当前项目 hash（8 位 SHA-256）
   - `apply <proj_dir> <patch.json> [--hash xxx]` — 应用增量修改，可选 hash 校验防冲突

2. **上下文控制机制：**
   - 默认 `--fields src,trans`，不输出坐标/字体/遮罩/渲染等无关字段
   - `compact_block()` 将每块从原始 JSON ~1KB 压缩到 ~100-200 字节
   - 超过 `--paginate`（默认 10 页）自动分块输出
   - `search` 遍历时不加载全部页面数据

3. **安全设计：**
   - `apply` 走 `proj_compact.apply_modifications()` 的 hash 校验，用户同时在 GUI 中修改后 apply 会抛 `StaleProjectError`
   - 修改后自动 `proj.save()` 持久化

**验证：** 全部命令实际项目测试通过 ✅（index → read → search → hash → apply → verify → revert）

---

### 清晰（始终矢量）模式切页后描边/阴影不渲染修复

**问题：** 设置描边或阴影效果的文本框在清晰模式（始终矢量）下，切页后描边/阴影不显示，需拖拽文本框触发刷新后才能渲染。位图模式无此问题。

**根因：** 慢路径 `paint()` 在 `super().paint()` 之后通过 `DestinationOver` 合成 `background_pixmap`（描边+阴影），但 `QGraphicsTextItem::paint()` 在 NoCache 模式下可能破坏 QPainter 的 clip/transform 状态，导致后续合成失效。仅在切页新创 `TextBlkItem` 的首次绘制触发。

**改动（`ui/textitem.py`）：**

1. **`paint()` 慢路径顺序调整**（核心）— 描边/阴影（`background_pixmap`）改在 `super().paint()` **之前** 用 `SourceOver` 绘制，文字画在上面覆盖即可。不再依赖 `DestinationOver` 和后置合成，消除 `QGraphicsTextItem::paint()` 污染 painter 状态的影响。

2. **`__init__`** — 清晰模式 `_use_full_pixmap = False`，保持 `NoCache`，走原生矢量路径。

3. **`startReshape()`** — 清晰模式 `_use_full_pixmap = False`，保留 `background_pixmap` 使拖拽时装饰样式可见。

4. **`endReshape()`** — 清晰模式调用 `repaint_background()` 重建最终尺寸的 `background_pixmap`。

5. **`repaint_background()`** — 末尾追加 `self.update()`，失效 DeviceCoordinateCache 确保下次 paint 重建 `_full_pixmap`。

6. **`_build_full_pixmap()`** — 跳过空尺寸时不再将 `_full_pixmap_dirty` 置 False，避免 fast path 永久跳过重建导致装饰永久丢失。

7. **`endFormatting()`** — 抑制 `endEditBlock()` 触发的冗余 `reLayoutEverything`，避免 `minSize` 不一致。

**相关修复（`ui/scene_textlayout.py`）：**

- **`punc_actual_rect()`** — 零宽/零高文本行返回 `[0,0,1,1]` 而非创建空 QImage，避免 `QImage`/`QPainter` 构造崩溃。

**验证：** 语法检查 ✅、i18n 检查 ✅（无新增条目）、启动导入测试 ✅

**涉及文件：** `ui/textitem.py`、`ui/scene_textlayout.py`
