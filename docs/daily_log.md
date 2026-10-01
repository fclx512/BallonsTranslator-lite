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

## 2026-09-29

### README 大画幅演示动画流程落地（`scripts/gen_readme_anim.py` 首个场景 `format_tour` + `anim_kit` 流式/裁切改造）

**摘要：** 首个 README 演示落地，同时实战验证组件化机制层：场景（主页面右侧格式面板基础排版巡礼，镜头跟随光标一镜到底）全部用既有原语组装，机制层只动了两处——`scripts/anim_kit.py::iter_frames` 增视口裁切钩子（camera 回调逐帧裁切）、`save_webp` 改流式（生成器喂 `append_images`，大画幅内存至多持有当前一帧）。画布文本块走真机引擎（`ui/textitem.py::TextBlkItem` + 引擎 setter），竖排几何天然正确、弹层流程的手绘探针验收消失；实测无损编码仍优于有损（纯色 UI），镜头目标须钳在画布内否则帧尺寸漂移。制作坑（布局惰性激活致航点全错、真弹层 grab 抓不到须画覆盖层、须从仓库根运行）写入使用说明 README 流程节。

**涉及文件：** `scripts/gen_readme_anim.py`（新增）、`scripts/anim_kit.py`、`scripts/README.md`、`docs/基础速查/备注演示动画使用说明.md`

**验证：** 弹层 7 场景重生成产物逐字节一致（`anim_kit` 改造零回归）；真机 `mw_repro.py` 临时场景查看通过（测后接线已拆除）；产物 1760×1120 无损 2.9MB，210 帧 @20fps。

---

### 演示动画生成管线组件化（`scripts/anim_kit.py` 机制库 + `CursorPlan`/`AnimScene` + `CheckboxDemoScene` 预设）

**摘要：** 原 `_DemoScene` 把机制与"复选框单点叙事"的版式/节奏焊死，叙事不匹配的功能只能硬套或整个重写。拆成共享机制库 `scripts/anim_kit.py`（确定性时间轴原语、光标编排 `CursorPlan`、场景基类 `AnimScene`、文案/绘制组件、渲染编码，对画布尺寸/文案位置零假设）+ `scripts/gen_help_anim.py`（弹层版式常量与 7 个场景）；复选框场景走 `CheckboxDemoScene` 预设，下拉选择类照 `PunctuationScene` 自行组装，`clip_text` 三拍光标交 `CursorPlan`、删掉整段 `set_state` 覆写。为 README 大画幅流程留好接口，流程约束（独立注册表、镜头跟随光标、可损编码）写入使用说明，脚本首个演示落地时再建。

**涉及文件：** `scripts/anim_kit.py`（新增）、`scripts/gen_help_anim.py`、`docs/基础速查/备注演示动画使用说明.md`、`scripts/README.md`

**验证：** 重构前后 `--all --dump-frames` 逐帧逐像素比对，7 场景 161 帧 0 差异（仓库 webp 产物零变更）；`scripts/verify.py` 全绿。

---

### 入库演示动画钉定 DPR 1.25（`gen_help_anim.py::_resolve_dpr`）+ 重生成总数改读 `TOTAL` 行

**摘要：** 仓库默认动画此前按生成机屏幕 DPR 渲染，换工作机重出尺寸会漂。改为双轨：写仓库默认目录自动钉 DPR 1.25（`QT_ENABLE_HIGHDPI_SCALING=0` + `QT_SCALE_FACTOR=1.25`，先于 QApplication 设置），跨机重出逐字节一致（实测与既有产物逐字节相同）；本机覆盖层（设置页按钮）仍按本机真实 DPR（覆盖层的意义就是本机适配）；`--dpr` 显式覆盖。README 流程不受此约束。附带：`ui/configpanel.py` 重生成状态条总数不再写死 7，从子进程 stdout 的 `TOTAL n` 行解析。

**涉及文件：** `scripts/gen_help_anim.py`、`ui/configpanel.py`、`docs/基础速查/备注演示动画使用说明.md`、`scripts/README.md`

**验证：** 本机连续两次 `--all` 产物逐字节一致；与仓库既有 webp 逐字节相同（原生 125% 渲染与钉定渲染等价，产物无需变更）。

---
