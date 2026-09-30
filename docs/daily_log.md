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

## 2026-09-28

### 备注问号弹层支持演示动画（`ConfigNotePopup` anim 键 + `gen_help_anim.py` 离屏生成管线）

**摘要：** 纯文本备注讲不清"切换后有过程的视觉效果"，增加代码生成演示动画：`scripts/gen_help_anim.py` 离屏渲染（offscreen + `QT_QPA_FONTDIR` 补字体）按帧号确定性步进逐帧 grab，Pillow 编码无损动画 WebP（体积敏感场景实测 33 帧 ≈ 40 KB）落 `config/help_anims/` 同名产物；备注经 `anim="<key>"` 可选键挂载，`ConfigNotePopup` 文字上方 QLabel+QMovie 循环播放，关层即停、全局动画关闭时只显首帧，文件缺失静默跳过。首个实例挂「标点位置」；适用判断与制作规则沉淀在 `docs/基础速查/备注演示动画使用说明.md`，静态左右对比图形态有意留作后续。

**涉及文件：** `scripts/gen_help_anim.py`、`config/help_anims/punctuation_position.webp`、`ui/configpanel.py`、`docs/基础速查/备注演示动画使用说明.md`、`scripts/README.md`

**验证：** 逐帧 PNG 目检（覆盖层 QSS 底色、drawText 基线双减两坑修复后）+ 离屏抓弹层实拍确认动画标签在位；`scripts/verify.py` 全绿（含启动冒烟）。

---

### 检测方向判定修复：合并路径单行块恒判竖排（`mit_merge_textlines` 投票阈值）+ 方向探针/矩阵

**摘要：** ysgyolo「Merge Text Lines」开启时所有检测框经 `utils/textblock.py::mit_merge_textlines` 聚组投票判向，阈值 `nv >= len//2` 在单行块（len//2==0）恒真，横排单行块全被误判竖排（用户实测：横排内容 OCR 正确但渲染方向竖排，曾被误疑为全局样式覆盖）。阈值改 `max(len//2, 1)`：单行块跟随该行自身方向，多行组投票口径不变。ppocrv6 逐框 `sort_pnts` 判向本就不受影响，一并真图核验。

**涉及文件：** `utils/textblock.py`、`scripts/probes/direction_probe.py`、`scripts/probes/direction_matrix.py`、`scripts/probes/README.md`、`scripts/check_audit.py`

**验证：** 合成矩阵 14 项全过（钉住单行修复与「两行平票判竖、三行一票即竖」的既有口径）；ysgyolo 与 ppocrv6 分别对混排测试图实测，宽扁框判横排、高瘦框判竖排；`scripts/verify.py` 全绿。顺带：`check_audit.py` SKIP_DIRS 排除 `.btrans_cache`——自更新缓存 last_version 内的旧版登记表会误报 125 处「删除后残留引用」。

---

### 备注演示动画扩展至 7 场景 + 弹层样式/清晰度四修 + 本机重生成双轨（`help_anims_local` 覆盖层、`gen_help_anim.py` windows 原生渲染）

**摘要：** 动画从 1 场景扩到 7（紧凑标点间距/竖排括号半角/纵横组合/序号徽标/标签徽标/溢出裁剪），场景适配规则立规：行为对比类开关＝改前/改后双文案+明暗互换（边框滑动方案被用户收回），外观展示类开关（两徽标）＝单条常显说明，溢出裁剪三拍演示开关两侧行为。弹层四修：白底直角框＝parentless 顶层弹窗收不到挂 MainWindow 的 QSS（自持样式表 + paintEvent 自绘底 + 原生 `setWindowOpacity`——透明窗口上 `QGraphicsOpacityEffect` 会吃掉 QSS 底且离屏复现不了）；发虚＝offscreen 默认字体落 Arial 且只有灰度 AA（生成器改 windows 平台原生渲染 + 显式 Microsoft YaHei UI + 按屏幕 DPR 出图，显示 1:1 设备像素）；重生成 WinError 5＝开过的弹层 QMovie 握文件句柄（改 QBuffer 内存播放）；既有 webp 脏帧＝`Image.fromarray(QImage)` 只是视图（改 `frombytes`）。新增双轨：仓库 `config/help_anims/`=固化默认，用户重生成写 `config/help_anims_local/` 覆盖层优先展示、可一键清除回退；重生成走 QProcess 子进程（Qt 禁止非 GUI 线程碰 QWidget），系统语言有意不纳入适配。

**涉及文件：** `scripts/gen_help_anim.py`、`ui/configpanel.py`、`config/help_anims/`（7 个 webp）、`translate/zh_CN.ts`、`.gitignore`、`docs/基础速查/备注演示动画使用说明.md`

**验证：** 竖排三场景与真机引擎（TextBlkItem+VerticalTextDocumentLayout）逐字比对格框 0.00px；每轮 `scripts/verify.py` 全绿；用户真机确认弹层样式与动画交互。

---

### 样式参数与画布渲染脱节两处收口（`_strip_paragraph_alignment` + `ui/text_panel.py` 别名保持）

**摘要：** 用户实测「参数居中、渲染靠右」且样式管理器批量修改要改两步才生效——数据层 `blk.fontformat` 与 item 渲染态（QTextDocument）只在 `set_fontformat` 时同步，单侧写即分叉。探针（`scripts/mw_repro.py --scenario fmt-sync`）钉出两根源：① 旧工程 rich_text 段落自带 align 属性，块级 blockFormat 对齐脱离数据层且 `set_fontformat` 治不了（只写 doc 默认 option），加载后统一清回 AlignLeft（Qt 实测：块对齐 ≠ AlignLeft 才覆盖默认 option，AlignAbsolute 反而是显式覆盖）；② 文本面板切回全局格式的整包回写只替 item 侧对象，打断 `initTextBlock` 的 item↔blk 别名，改两侧同对象。样式管理器「两步走」本身不修：空 diff 门（`changed_values`）是防压块级 override 的正确设计。

**涉及文件：** `ui/text_engine/item.py`、`ui/text_panel.py`、`scripts/mw_repro.py`、`scripts/README.md`、`tests/test_format_sync.py`

**验证：** fmt-sync 探针修复前 A1a/A1b/A2 分叉、修复后 6/7 一致（B1 分叉为机制演示属预期）；`tests/test_format_sync.py` 5 断言 + rich-text 路径相关 7 个既有测试全绿。

---
### 启动模块降级链路移除（`_ensure_model_files_fallback` 删除 + `ModuleManager.setXxx(offer_deps=False)`）

**摘要：** 无 CUDA/缺包机器上每次启动把 OCR 强制降级 none_ocr，保存期「换回原值」保护又反复复活旧选择，用户改选永不粘；`_ensure_model_files_fallback` 与运行时自愈链路（`load_model` = `ensure_dependencies` 自动装包 → `_ensure_model_files` 自动补权重 → `MissingModelFilesError` 带指引弹窗）冲突，整体删除。另将启动初始化四个 `ModuleManager.setXxx()` 改为 `offer_deps=False`：启动只按配置静默装载，补下载/GPU 说明窗只响应用户主动选模块。`_ensure_module_fallback`（torch 探针，保启动命）保留不动。

**涉及文件：** `launch.py`、`ui/mainwindow.py`、`ui/module_manager.py`

**验证：** 真机两轮端到端（`tmp/launch_ocr_probe.py` 走真实启动链）：vl_manga 启动安静选中不弹窗、改选 paddleocr_v6_onnx 正确落盘、重启保持不回退；`scripts/verify.py` 全绿。

---
### 启动模块强制降级移除（`_ensure_model_files_fallback` 删除）+ 启动静默装载（`ModuleManager.setXxx(offer_deps=False)`）

**摘要：** 用户实测无 CUDA 机器上「改选的 OCR 模型重启即丢、启动必弹提示/事件链路」。根源一：`launch.py::_ensure_model_files_fallback` 启动时查到缺包/缺权重就把 `pcfg.module.ocr` 换成 `none_ocr`（打印提示），而 `save_config` 的"换回原值"保护（`_suspend_auto_downgrades`）在用户改选未成功落盘时反复把旧值写回 config——选择永远逃不出；且该降级与应用已有的运行时自愈链路（`load_model` = `ensure_dependencies` 自动装包 + `_ensure_model_files` 补文件/加载期弹窗）冲突，把用户锁死在 none。整段删除。根源二：启动初始化四个 `setXxx()` 照常走 `_ensure_module_deps`，对 GPU-only 模块每启必弹拒绝窗——加 `offer_deps` 开关，启动传 False 静默装载，主动选模块才触发补装链路。

**涉及文件：** `launch.py`、`ui/mainwindow.py`、`ui/module_manager.py`

**验证：** 真机启动链探针（`tmp/launch_ocr_probe.py`，实测后未入库）：改选 `paddleocr_v6_onnx` → 关闭落盘 → 重启安静选中新值，全程零弹窗零降级；`scripts/verify.py` 全绿（含冒烟）。

---
