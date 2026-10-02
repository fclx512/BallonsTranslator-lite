# 每日开发日志

> 记录**仓库层面**的改动（功能增删、远端分支变动、规范调整）。**主要读者是 AI**（用户基本不看），按**可检索**写、不按可通读写。每次在对应日期末尾写入，每条之间用 `---` 分隔：

```markdown
### <一句话标题：改了哪 + 关键符号名/配置字段名/D 编号>   ← 标题里带检索键，供 grep
**摘要：** 问题 → 做法与理由（2~4 句，写决策不写实现细节）
**涉及文件：** `路径`、`路径`                            ← 最常用的检索键，必填
```

> 可选再补一行 `**验证：**`（真机实测／测量类证据，或值得钉住的回归）与一行 `**遗留：**`（有意留开、已知不做的事）。**细节不写在这里**：设计决策写进 `docs/技术实现/`、就地约束写进代码注释、实现过程写进 commit body（本仓库 commit 已相当详细）——同一件事不要在四处各写一遍。长度参考**一条 4~8 行**，动辄 30~60 行的长段落既是反面教材、也让 grep 到的人被迫整段读完。踩坑细节、方案草稿与跨代理交接留在各代理侧的私有记忆（见 `AGENTS.md` 的「多代理协作」一节），不进仓库。
>
> 仅保留最近 3 天的记录（超出窗口的日期节由 `scripts/trim_daily_log.py` 在提交时经 pre-commit 钩子自动清理，无需手工维护）；被裁掉的日期节仍完整留在提交历史里，用 `git log --grep <关键词>`／`git log --follow -p -- docs/daily_log.md`／`git show <rev>:docs/daily_log.md` 回查。

## 2026-10-02

### 高级对齐改名「整本对齐」并重构交互（`PointAlignDialog` / `canvas.enter_align_mode` / `pcfg.point_align_*`）

**摘要：** 该功能实际只服务全竖排差分本，原名不达意，改为「整本对齐 / Whole-book Alignment」（内部符号与快捷键 id `advanced_align` 不变）。交互从「选轴→手填/隐藏对话框取点坐标→确定」重做为非模态＋画布对齐模式：点块取对齐边、基准线可拖实时回推、当前页幽灵落点预览、打开时目标＝当前页对齐边众数（`smart_default_target`）、方向/对齐边/范围口径记忆在 `pcfg.point_align_axis/edge_y/edge_x/all_pages`；对齐模式换 `_SegmentedBar` 自绘分段条（带示意图标，仅对话框专用不入 custom_widget）。用户验收报「退出后鼠标粘住精确选择」＝`leave_align_mode` 漏清 `baseLayer` 场景层十字丝（同 `exitReorderMode` 三件套），已修并钉进演练台断言。
**涉及文件：** `ui/point_align_dialog.py`、`ui/canvas.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/configpanel.py`、`ui/textitem.py`、`ui/text_engine/editing/manager.py`、`utils/config.py`、`scripts/mw_repro.py`、`translate/zh_CN.ts`
**验证：** 演练台新场景 `align-dialog` 全链路（智能默认/拖线/点块/换轴/幽灵/确定落位/画布清理/光标两处清）；`scripts/verify.py` 全绿；pytest 1542 passed；用户真机验收（交互）通过。

---

### 单选按钮全局改「圆环＋主题色圆点」（`config/stylesheet.css` QRadioButton）

**摘要：** 原选中态是整圆涂满强调色，观感笨重。改为细描边圆环＋radial gradient 硬过渡画的中心圆点，走 `@accentPrimary` 主题变量随深浅主题换色；未选中态与复选框统一 1px 边框＋输入底色。不用 SVG 图标方案：`icons/checkbox_checked.svg` 对勾色写死 `#1e93e5` 深色主题不换色（既有不一致，用户确认维持现状）。
**涉及文件：** `config/stylesheet.css`
**验证：** 离屏渲染浅色/深色/禁用四态目视；真机验收通过。

---

## 2026-10-01

### Ruff 检查整理（`scripts/verify.py` / `NUMBA_CACHE_DIR`）

**摘要：** 安装并启用 Ruff，整理导入与空白，删除无用绑定并补齐类型注解；保留用于检查可选依赖的导入和 Numba 缓存设置的先后顺序。验证入口改用追加规则，遵守 `pyproject.toml` 已声明的忽略项，避免命令行选择器重新启用长行检查。
**涉及文件：** `scripts/verify.py`、`pyproject.toml`（沿用原配置）、`ui/`、`utils/`、`modules/`
**验证：** `ruff check --extend-select W ui/ utils/ modules/` 无报错，`scripts/verify.py --full` 全部通过；真窗口正常关闭通过。

---

### 统一文字外观浮层（`TextAppearancePanel` / `AppearanceEntry`）与镂空删除（`set_hollow_enabled`）

**摘要：** 效果和变换共用画布侧浮层，右栏保留固定高度摘要，避免效果数量挤压原文／译文输入区。参数卡改为单项展开并去掉重复套框，重绘侧栏、镂空与关闭图标；镂空关闭即从栈删除，避免隐藏的禁用条目留下计数。同步修复变换后效果表面偏移、网格拖拽收尾及多选／取色的编辑目标保持。
**涉及文件：** `ui/text_panel.py`、`ui/text_appearance_panel.py`、`ui/text_engine/appearance.py`、`ui/text_engine/effects/`、`ui/text_engine/transforms/`、`ui/textitem.py`、`ui/canvas.py`、`ui/panel_rail.py`、`ui/custom_widget/rail_dock_panel.py`、`config/stylesheet.css`、`icons/`、`tests/`、`scripts/probes/probe_text_appearance_ui.py`、`scripts/probes/probe_transform_effect_ui.py`
**验证：** 真窗口检查输入区高度、镂空开关计数与撤销、拖拽取消／提交、切页、渐变取色、多选；125%／150% 缩放布局通过，`scripts/verify.py --full` 全部通过。

---

### 下载渠道官方源化（删 `utils/network_mirrors.py::auto_fill_mirrors` 地区自动填镜像）+ README 网盘换 139/百度

**摘要：** 用户实测阿里云源极慢、开代理无改善、反不如官方源，且第三方源依赖不齐，拍板所有依赖/模型下载默认走官方源：删首启按地区自动填镜像整套（含 HF 镜像与两个默认常量、locale/时区探测），镜像仅留设置页手动配置（小白由一键完整包兜底）；保留手动配置读回（`apply_pip_mirror_env`）与系统代理探测；已装机 config.json 里已写入的镜像不受影响。README/README_EN 一键包渠道删 123 云盘，换 139 移动云盘（提取码 8xys）+ 百度网盘（提取码 1111）。
**涉及文件：** `utils/network_mirrors.py`、`launch.py`、`ui/network_settings_dialog.py`、`tests/test_bootstrap_launch.py`、`README.md`、`README_EN.md`
**验证：** `scripts/verify.py` 全绿（冒烟命中 launch.py）；`tests/test_bootstrap_launch.py` 17 用例通过（自动填充契约随功能删除）。

---

### 上游样式导入同步右栏快速样式条（`_maybe_seed_upstream_styles`）+ 样式管理器清理未使用样式与观感修复（`_clean_unused_styles`/`StylePreviewCard`/`FormatEditorPanel`）

**摘要：** 上游项目导入的命名样式原本只进项目 `base_styles`、右栏快速样式条（全局 `text_styles`）看不到：播种后按预设名去重 deepcopy 并入并刷新面板，只在导入发生一次、不随重开回灌。管理器左栏新增「清理未使用样式」（判据 discovery `total_count == 0`，只删零引用项目大样式、不碰样式库模板）；预览卡改为按文档实尺寸水平垂直居中绘制（不传裁剪矩形，字形上缘不再被裁）；参数行标签宽随当前语言最长译文自适应（原钉 100px 裁「竖排罗马字对齐」），分组卡 `FormatGroupCard` 加描边圆角；管理器按钮收 24px 紧凑尺寸（`StyleDetail`/`#StyleMgrBtnRow` 域内 QSS，后置规则钉回 ParamChip 胶囊尺寸）。
**涉及文件：** `ui/mainwindow.py`、`ui/fontstyle_manager.py`、`ui/style_format_editor.py`、`config/stylesheet.css`、`translate/zh_CN.ts`
**验证：** `scripts/verify.py` 全绿；离屏行为验证清理只删零引用样式、取消与空路径不误删；渲染探针暗/亮两主题目视验收（预览居中、长标签完整、边框与紧凑按钮生效）。

---

### 上游兼容提示窗闪退修复（`_maybe_seed_upstream_styles` 复选框挂父）+ 提示补样式刷新说明

**摘要：** 用户实测打开上游工程「弹出兼容提示后卡死闪退、终端无报错」：提示窗的「不再提示」复选框用了无父 `QCheckBox` 临时对象，PyQt6 认定它归 Python 所有、语句结束即回收 C++ 控件，`QMessageBox` 内部指针随之悬空，弹窗一绘制就是 access violation（同一坑此前在画布组化确认弹窗踩过，`ui/canvas.py` 留有注释）。改为构造期挂父 `QCheckBox(tr(...), box)`；提示文案补一句「带来的样式要应用到文本块一次后才会正确刷新」（用户实测口径）。
**涉及文件：** `ui/mainwindow.py`、`translate/zh_CN.ts`、`tests/test_upstream_notice_dialog.py`、`scripts/probes/probe_open_upstream_project.py`、`scripts/probes/README.md`
**验证：** 真机探针在 94 页上游工程复现原崩（faulthandler 报 access violation）；修后两条打开路径（UI `openDir`／构造期开项目）与有/无命名样式两个分支均干净退出；`tests/test_upstream_notice_dialog.py` 红绿各一次；全量 pytest 1512 通过。

---

### 上游可读的落盘口径（`FontFormat.to_serializable_dict` 只写非默认值 + `TextBlock.text_layout_version`）

**摘要：** lite 工程在上游打开时逐块报 `Ignoring unsupported font format fields`、且竖排对齐被整体改成靠右，两条都是落盘口径问题：四个 fork 独有字段（`strikeout`/`stroke_color_custom`/`shadow_include_stroke`/`punctuation_alignment`）被无条件写进每个块，上游把未知键收进 `deprecated_attributes`、警告后清空并在它自己的保存里丢掉；块缺上游 v1.5.13 引入的 `text_layout_version`，上游按版本 0 旧数据升级＝竖排块一律 `alignment=Right` 并回写文件（用户报的「改一个块靠右后全部靠右」实为此，触发点是 lite 的任意一次保存）。现在四个字段只在非默认值时写（`punctuation_alignment` 已废弃成全局设置，一律不写），`TextBlock` 补上 `text_layout_version: int = 1` 并原样保留上游给的值。
**涉及文件：** `utils/fontformat.py`、`utils/textblock.py`、`tests/test_upstream_writer_compat.py`
**验证：** `scripts/probes/probe_upstream_style_compat.py fork-to-upstream` 四条未知字段警告清零、上游加载后 `alignment` 保持 1（竖排/横排各一例）；真工程 JSON 往返只少了三个默认值键、块级多出 `text_layout_version`；`scripts/verify.py --full` 全绿。

---

### 清理未使用样式前先冲画布数据（`FontStyleManager._flush_canvas_edits` + 删除后 toast）

**摘要：** 「清理未使用样式」判据取自 `discover_style_tree` 的 `total_count`，而 discovery 直读 `proj.pages`——画布上删掉的块要等切页保存才回写，所以刚删完块点清理会把样式仍算作在用，用户必须先切一次页。现在清理前按 `ui/glossary_agent_panel.py` 同一套两道判据（撤销栈脏 / `page_data_needs_sync`）先冲一次当前页再重扫，不必再记得切页；删除成功后补一条 `notification` toast 报数量（此前该操作没有任何反馈）。
**涉及文件：** `ui/fontstyle_manager.py`、`tests/test_fontstyle_tree.py`、`translate/zh_CN.ts`（qm 同步编译）
**验证：** 新增 `test_clean_unused_styles_syncs_canvas_first`（撤销栈脏／结构性增删两路参数化）红绿；`scripts/verify.py --full` 全绿。

---

## 2026-09-30

### 样式管理器字体预览自动反色（`StylePreviewCard._contrast_ratio`）+ 分组标题完整显示（`FormatGroupCard` 宽度自适应）

**摘要：** 预览卡文字色直接取样式 `frgb`（常为黑），暗色主题底 `@emptyContentBackgroundColor` 上不可读；改为绘制时按 WCAG 对比度判定，低于 3.0 就按文字明度反相铺底（深字浅底/浅字深底），够对比则保持主题原底。主题色读取沿用按主题缓存模式（`_theme_var_color`）避免绘制路径反复读 JSON。另修复「颜色与描边」被钉 110px 宽裁成「颜…边」：宽度按译文实宽自适应（Qt6 对放不下的 QToolButton 文字做中缀省略），加粗从内联 QSS 移到 `setFont` 保证度量一致。

**涉及文件：** `ui/fontstyle_manager.py`、`ui/style_format_editor.py`

**验证：** `scripts/stylemgr_render.py` 暗/亮两主题 4 场景目验 + 白/灰/黑文字 × 双主题 6 张预览卡逐一核对反色分支；`scripts/verify.py` 全绿。

---

### 上游项目字体样式兼容导入（`seed_base_styles_from_style_names` + 主窗口弹窗）+ 上游数据两处保命兼容

**摘要：** 上游项目没有项目级样式表，命名样式只是块级 `fontformat._style_name`（预设名缓存），lite 打开后全部掉进未分组——用户反馈的"样式不兼容"实为此。现打开无 `base_styles` 键的项目时弹窗询问一次（`ui/mainwindow.py::_maybe_seed_upstream_styles`，挂 `openDir`/`openJsonProj`），确认后按「预设名 × 身份键」播种大样式（同身份去重、代表格式取组首块、无名块照旧未分组），随下次保存落盘。捎带修两处真实数据丢失：上游 `synthetic_bold` 假粗体效果由丢弃改为 `UnknownEffect` 原样透传（渲染/面板自然跳过、序列化原样回吐，FilterEffect 同款设计）；`llm_compact_memory` 上游 dict 结构取 `text` 字段兼容。不做设置项开关（用户拍板）。

**涉及文件：** `utils/base_styles.py`、`utils/proj_imgtrans.py`、`ui/mainwindow.py`、`utils/text_effects.py`、`translate/zh_CN.ts`

**验证：** 真 94 页工程（782 块）构造上游格式实测：播种 2 样式全量覆盖、假粗体保存存活、llm 梗概恢复、二次打开不重复弹窗（`tmp/accept_seed_popup.py` 走真实主窗口弹窗链路）；`tests/test_base_styles.py`/`test_text_effects_data.py`/`test_story_injection.py` 补钉。

---

### 补钉应用页分节卡契约数量（`test_config_section_cards.py` app 页 4→5）

**摘要：** `0c0c4cbc` 往应用页新增「Note Animations」节后没更新这份契约钉子，`verify.py --full` 自该提交起一直红。按现状补 4→5，非本次改动引入。

**涉及文件：** `tests/test_config_section_cards.py`

---

