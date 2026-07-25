# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，每次在文档末尾写入。

## 2026-07-23

### Medium 字重渲染变形 — `_doc_set_font_family` 误将 Medium 设为 Bold

**问题：** Medium 字重渲染时字形部首纵向/横向变形（过粗），切换粗体等样式触发重渲染后恢复正常。

**根因：** `ui/textitem.py` 的 `_doc_set_font_family()` 中，当 `actual_style = "Medium"` 时：

1. `font.setWeight(QFont.Weight.Medium)` 正确设字重 500
2. `font.setBold(True)` **紧随其后被调用**，因为 `"Medium"` 被错误列入粗体列表
3. Qt 中 `setBold(true)` 等价于 `setWeight(QFont::Bold)`（700），覆盖了 Medium 字重
4. 系统收到 Bold 请求，对无 Bold 变体的字体触发合成加粗（synthetic bold），导致字形变形

切换粗体走 `ffmt_change_bold` → `setFontWeight` 路径，不含 styleName 干扰，故恢复正常。

**修复：** 从 `setBold(True)` 列表中移除 `"Medium"`、`"SemiBold"`、`"DemiBold"`——这三个是中间字重（500/600），不是 Bold(700)。只保留真正的粗体样式：`Bold`、`ExtraBold`、`UltraBold`、`Black`、`Heavy`。

**涉及文件：** `ui/textitem.py:1454-1466`

---

### 切换字体家族/样式后加粗按钮状态不同步（总显示为 Regular + 加粗激活）

**问题：** 切换字体家族或样式后，样式组合框总是变成 Regular，且加粗按钮保持激活状态，需手动再次点击取消。

**根因：** `ui/text_panel.py` 的 `apply_font_change()` 只更新了 `_style_name`，未同步更新 `bold` 和 `font_weight` 到 `act_ffmt`，导致 UI 加粗按钮状态与底层格式不一致。同时 `on_familybox_changed()` 总是硬编码样式组合框为 "Regular"，丢弃了用户上次选择的样式名。

**修复两项：**

1. **`apply_font_change()`** — 设置 `_style_name` 后，通过 `QFontDatabase.weight(family, style)` 获取数字字重，用 `fix_fontweight_qt()` 做 Qt5/Qt6 兼容转换，同步更新 `act_ffmt.bold`（>= Bold 700 才为 True）和 `act_ffmt.font_weight`，最后调用 `boldBtn.setChecked()` 同步 UI。

2. **`on_familybox_changed()`** — 清空填充样式组合框前，从 `act_ffmt._style_name` 读取当前样式名；新家族有此样式则保留，不再无条件回退到 "Regular"。

**涉及文件：** `ui/text_panel.py:679-742`、`utils/fontformat.py`（新增 `fix_fontweight_qt` 模块级导出）

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

---

## 2026-07-24

### PS 外部编辑图像修复 — 不依赖扩散模型的复杂背景修复方案

**需求：** 内置轻量修复模型（AOT/LaMa）对密集复杂背景效果差，且不愿添加体积大的扩散模型。借用已安装的 Photoshop 作为外部编辑器处理背景修复。

**设计决策：**
1. 一键导出当前 `inpainted` 图为 PNG → 启动 PS 打开该文件 → PS 中编辑 → Ctrl+S 保存 → 回到本工具点「从 PS 刷新」读回
2. 中转格式固定 PNG，直接写入 `inpainted/{basename}.png`，不新增额外中转文件
3. PS 路径发现仅用 Windows 注册表（`App Paths\Photoshop.exe` + `SOFTWARE\Adobe\Photoshop`），不猜安装路径
4. 设置面板 `InpaintConfigPanel` 底部新增「External Editor」区域，文本框 + 浏览按钮可手动配置 PS 路径

**实现要点：**
- `utils/config.py`：`DrawPanelConfig.photoshop_path` 新字段
- `ui/module_parse_widgets.py`：`InpaintConfigPanel` 底部加路径配置 UI，`ConfigLineEdit` + `QPushButton`(Browse) + `ConfigSubBlock`
- `ui/drawingpanel.py`：底部加「Edit in Photoshop」「Refresh from Photoshop」两个按钮；`_find_photoshop_path_registry()` 静态方法查注册表；`on_edit_in_photoshop()` 保存 PNG + `QProcess.startDetached` 启动 PS；`on_refresh_from_photoshop()` 读回 PNG、校验尺寸、`InpaintUndoCommand` 全图撤销、`save_inpainted()` 持久化
- `translate/zh_CN.ts`：新增 15 条翻译条目

**验证：** 语法检查 ✅、i18n 检查 ✅（15 条新增均匹配）、QM 编译 1097 条 ✅、启动导入测试 5/5 ✅

**涉及文件：** `utils/config.py`、`ui/module_parse_widgets.py`、`ui/drawingpanel.py`、`translate/zh_CN.ts` + `zh_CN.qm`

---

## 2026-07-25

### 竖排直角引号半角紧凑样式（「」『』 half-width compact）

**需求：** 竖排文本中「」『』（U+300C/D/E/F）默认全角宽度与其他 CJK 字符一致，需要类似 PS「日文间距挤压」的半角紧凑显示，仅限这 4 个特指字符，简单开关即可。

**实现要点（竖排）：**
- `utils/config.py`：新增 `halfwidth_jp_corner_brackets: bool = False`
- `ui/scene_textlayout.py`：新增 `PUNSET_CORNER_BRACKET` 字符集；`SceneTextLayout` 基类增加 `halfwidth_jp_corner_brackets` 参数传递
- `VerticalTextDocumentLayout.layoutBlock()`：在已旋转标点分支中，对直角引号跳过 `tbr_h = line.naturalTextWidth()` 覆盖，保留 `tbr.width() * text_len` 紧凑列高
- `VerticalTextDocumentLayout.draw()`：左括号（「『）在 `updateDrawOffsets` 中 `xoff -= reduction` 上移补偿列高缩减——这里用 x 而非 y 是因为旋转变换 `QTransform(0,1,-1,0,…)` 中 pre-rotation x 映射到 post-rotation 垂直方向
- `ui/configpanel.py`：`ConfigCheckBox` + `ConfigSubBlock`，开关切换时遍历所有 `TextBlkItem` 更新 layout 属性并重绘

**竖排调试教训：**
- `tightBoundingRect().width()` vs `naturalTextWidth()`：前者对直角引号约为后者 50%，直接用作 tbr_h 即可
- `space_w` 是 `layoutBlock` 局部变量，`updateDrawOffsets` 中用 `cfmt.space_width` 替代
- 左括号位移方向用 `xoff` 而非 `yoff`，因为旋转坐标系下 x→纵向

### 横排半角直角引号附属开关

**需求：** 主开关下新增「也处理横排」附属子开关。

**实现要点（横排）：**
- `utils/config.py`：新增 `halfwidth_jp_corner_brackets_horizontal: bool = False`
- 横排模式通过**文本替换**实现：`「`(U+300C)→`｢`(U+FF62)、`」`(U+300D)→`｣`(U+FF63)，利用 Unicode 半角引号自然获得半宽布局。`『』`无半角等价字符，横排下保持全宽。
- `ui/textitem.py`：新增 `_on_contents_change_for_hw` 信号处理器（自动拦截「/」插入）、`apply_horizontal_halfwidth_corner_brackets`（全量替换）、`restore_horizontal_halfwidth_corner_brackets`（反向还原），均内置 `self.fontformat.vertical` 守卫跳过竖排。`_block_hw_sub` 标志防递归。
- `ui/configpanel.py`：`halfwidth_horizontal_checker`（32px 左缩进），主开关关闭时自动禁用；`_apply_halfwidth_corner_bracket_settings` 扩展为同时处理竖排 layout 属性 + 横排文档文本替换。

**涉及文件：** `utils/config.py`、`ui/scene_textlayout.py`、`ui/textitem.py`、`ui/configpanel.py`、`translate/zh_CN.ts` + `zh_CN.qm`
