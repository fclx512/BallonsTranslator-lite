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

## 2026-09-29

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

## 2026-09-27

### 字号输入上限跟随 `pcfg.max_font_size`（`SizeComboBox.set_max_val`）

**摘要：** 右栏文本面板与嵌字页面板的字号框原来分别钉死 200/1000，设置页「最大字号」只约束渲染不约束输入，拖拽/手输都能越过上限。`SizeComboBox` 增加运行时可更新的 `set_max_val`，两处字号框改读 `pcfg.max_font_size`，并在每次回显前同步（面板只在启动时构造一次，不同步会被旧上限钳住）。

**涉及文件：** `ui/custom_widget/combobox.py`、`ui/text_engine/formatting/panel.py`、`ui/text_panel.py`

**验证：** `scripts/verify.py --full` 全绿。

---

### 样式管理器子样式字体迁移（`StyleFontMigration`）+ 大样式编辑快照撤销（`BatchFontformatCommand` 的 `base_snapshot`）

**摘要：** 子样式详情新增「更换字体」卡：只把该子样式的文本框迁到目标大样式（或未分组），迁移前按实时发现重核成员、确认后一步撤销。大样式编辑补两个一致性缺口——目标身份已属于其它大样式时拒绝应用（不再静默生成重复身份）；模板 `fontformat` 的旧/新格式经 `base_snapshot` 随批量命令一起撤销/重做，无匹配块的模板编辑也能撤。

**涉及文件：** `ui/fontstyle_manager.py`、`ui/fontstyle_manager_commands.py`、`scripts/stylemgr_render.py`、`config/stylesheet.css`、`tests/test_fontstyle_tree.py`、`tests/test_global_search_fontstyle.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

**验证：** 新增 7 个用例（迁移/未分组重检/身份冲突/身份变更撤销/base_snapshot 三态）全过；`scripts/stylemgr_render.py` 场景 4 变体验收底图。

---

### 模型文件列表整行点击勾选（`RowTable.set_row_click_toggles_check`）+ 展示台两级目录检索

**摘要：** 模型文件卡片的 13px 勾选框太难点，`RowTable` 增加默认关闭的整行点击勾选（只认「按下-抬起同一行且未拖动」的一次点击，仍走 `_user_toggled` 唯一写路径），仅 `ui/model_files_panel.py` 开启——工作台候选列表靠点行预览、不能开。展示台左侧目录改「分区→控件」两级并与搜索/样式筛选同步，加行计数与 Ctrl+F/Enter/Esc，修样式来源徽章把类型规则误判成类名的问题；给裸 `QToolButton` 补全局紧凑兜底。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/model_files_panel.py`、`scripts/style_showcase.py`、`config/stylesheet.css`、`docs/基础速查/打包控件功能使用说明.md`、`tests/test_model_files.py`

**验证：** 真实鼠标事件 7 用例（主体勾选/复选框单次触发/拖动不勾/默认关闭）全过。

---

### 发版形态改版：发版包＝约 30 MB 引导小包 + 发布 tag 换 `lite-v*` 前缀 + PatchMatch 原生库改 Release 资产用时下载

**摘要：** 用户拍板把发行物从预装式 600 MB 拨回**引导小包**（源码 + 嵌入式 Python + pip/uv、依赖首启现装，约 30 MB、对齐上游 `Ballonstranslator_win_minium.zip`）；600 MB 预装环境只是本机运行状态、不分发。发布 tag 换新格式 **`lite-vX.Y.Z`** 与上游 `v1.x.x` 区分（pyproject 升 `1.0.0`，`utils/updater.py::normalize_version_tag` 剥离新前缀——`lite-v` 必须排在 `v` 之前）。PatchMatch 两个原生 DLL（约 53 MB）不再随包：`scripts/build_win_minimal.ps1` 同批产出 `release_assets\` 独立资产，模块声明 `download_file_list`（`releases/latest/download` 直链 + sha256 钉死），**选中 patchmatch 即后台下载**（`_NO_DOWNLOAD_LIST_KEYS` 摘出后，缺文件检查/选型警示/运行前警告/模型文件页全链路认领同一份声明）。

**涉及文件：** `pyproject.toml`、`utils/updater.py`、`scripts/build_win_minimal.ps1`、`modules/inpaint/inpaint_patchmatch.py`、`modules/inpaint/patch_match.py`、`modules/__init__.py`、`ui/module_parse_widgets.py`、`README.md`、`README_EN.md`、`scripts/README.md`、`docs/项目概述.md`、`docs/基础速查/依赖库说明.md`、`tests/test_updater_version.py`、`tests/test_inpaint_patchmatch.py`、`tests/test_minimal_package_contract.py`

**验证：** `tests/test_updater_version.py`（tag 剥离与升级路径 5 用例）新增全过；patchmatch 契约 58 passed；构建脚本 PSParser 解析通过、`tests/test_minimal_package_contract.py` 4 passed；**首次端到端真实构建产出 32.3 MB 小包 + 53 MB 资产**，开箱首启验收发现 `utils/core_requirements.py` 把 pywin32 的 `.pth` 类失败误判成「重启无用」、装完依赖撞「缺少核心依赖」硬闸门拒启——补 `_restart_resolvable_failures` 子进程复测（等价重启后探测）让首启装完自动重启收尾，3 新用例钉住。**发版：** 已于当日执行，见下方「lite-v1.0.0 发版执行」条。

---

### 发版说明文档落库 `docs/发版说明_lite-v1.0.0.md` + README 发版口径更正

**摘要：** 0.6.0→lite-v1.0.0 共 139 提交的改动整理成仓内更新说明文档（面向老用户，发版页只引用不抄正文）；README/README_EN 撤「尚未正式发布」标注、补 Releases 下载指引，并修掉与发版形态矛盾的三处旧口径（一键完整包段误写「精简包预装依赖」、源码段「PatchMatch 随包需手工补回」、macOS 段附件说法）；「功能展示」占位行按要求继续搁置。

**涉及文件：** `docs/发版说明_lite-v1.0.0.md`、`README.md`、`README_EN.md`、`docs/基础速查/依赖库说明.md`

---

### lite-v1.0.0 发版执行（tag 推送 + 三资产 Release + 旧 7 Release/v0.x tag 清理）

**摘要：** 首个 `lite-v*` 版本正式发布：tag `lite-v1.0.0`（指向 `8f5c5744`）推送，GitHub Release（id 397650844）挂三件资产——小包 ZIP 33,855,393 B + 两个 PatchMatch DLL（50,176 / 55,521,280 B，sha256 与 `modules/inpaint/inpaint_patchmatch.py::download_file_list` 钉死值逐一吻合，`releases/latest/download` 直链自此可用）；旧 7 个零资产 Release（v0.2.0～v0.6.0）连同远端/本地同名 tag 删除，上游 v1.5.x 系 tag 保留（origin 上本就没有）。发版页正文只引用 `docs/发版说明_lite-v1.0.0.md`。本机无 `gh`，全程走 GitHub API（token 取自 git 凭据）。

**涉及文件：** 本条为远端动作记录，仓内仅 `docs/daily_log.md`

**验证：** Release 资产清单 3 项尺寸逐一核对；`releases/latest/download/patchmatch_inpaint.dll` 实拉哈希一致；远端 releases 剩 1 个、`git ls-remote --tags` 只剩 `lite-v1.0.0`。

---

