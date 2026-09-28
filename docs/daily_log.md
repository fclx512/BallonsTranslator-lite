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

## 2026-09-26

### 标签待办体系重构（`ocr_review_pending` / `trans_review_pending` + `consumed_tags` 撤销一致性）+ 工作台六项扁平导航与非删除待办队列（D46/D48/D49）

**摘要：** 前台标签由「六个 id 都是按钮」收成**两个人工待办**（稍后校对／稍后重译）＋**两个持久翻译指示**（手写字／拟声词）：旧 ID（人工来源 `ocr_low_conf`、`trans_confusing`、`trans_polish`）**只读兼容、加载时不迁移**，只在用户取消那条待办时顺手清（`_clear_legacy_review`），避免静默重写整本项目；`reviewed` 粒度由块级收窄到**单个问题 ID**，否则驳回一条误框会把同块的低置信度建议一起永久冻结。确认卡「应用」的文字写回与标签出队并进**同一条撤销命令**（`ui/textedit_commands.py::ApplyBlockTextCommand` 的 `consumed_tags`），撤销时文字与待办一起复原。工作台导航由两级收成**扁平六项**（`WORKBENCH_ORDER`），形态＝按需换行的 **chip 流**（`ui/glossary_agent_panel.py::WorkbenchTaskNav`：组标题行去掉、只用组间细分隔线，激活项完整显示、放不下的非激活项压到可用宽度并在渲染层省略），新增两个**非删除待办队列**（`ui/workbench_review_view.py::ReviewQueueView`，只有「跳画布／出队」两条出口，复用 `BatchTask.apply` 会把「稍后处理」显示成「勾选＝要删」）；跳步弹窗与 `workbench_warn_skip_order` 一并删除（顺序是推荐，导航顺序本身就是提示）。校对原文与重译改为**单选块即出现场钮**，不再要求先挂标签。

**涉及文件：** `utils/block_tags.py`、`utils/block_actions.py`、`ui/textedit_commands.py`、`ui/mainwindow.py`、`ui/tag_toolbar.py`、`ui/context_menu_config.py`、`ui/glossary_agent_panel.py`、`ui/workbench_tasks.py`、`ui/workbench_review_view.py`、`translate/zh_CN.ts`、`docs/技术实现/AI辅助功能_设计与实现.md`

**验证：** `scripts/verify.py --full` 六步全过；pytest 1398 passed / 1 skipped（ruff 未安装跳过）；只读真样本复算（`D:/汉化/施工区副本`）通过。

**遗留：** 双检测器（`ppocrv6_onnx` ＋ `ysgyolo`）仍为**离线实验、未接入默认流程**：九页 A/B 只有几何覆盖等自动指标、无人工真值，人工看图已见 ysg 漏真文字（`047.jpeg`／`063.jpg`）；不加配置、不改默认检测/OCR 流程。

---

### 批量框扩张退役（`ui/batch_expand.py`／`workbench_expand_px`／复算台 `expand`，D47）+ 跳步提示退役（`workbench_warn_skip_order`，D37 → D49）

**摘要：** 用户实测结论是「扩了也不解决填不满／塞不下」——批量框扩张只有机械几何写入（改 `_bounding_rect`／`xyxy`），没有可靠的自动排版消费，故整条删除：引擎、工作台适配 `ExpandTask`、设置项 `workbench_expand_px`、复算台 `expand` 子命令、导航项与那条真机探针一并清掉，删前按审计规范登记。审批截图仍在用的 `utils/block_geometry.py::expand_limited`（「碰到邻框即停」）与单块 Alt 拖拽缩放不在退役范围。同批删除跳步弹窗与其配置项：顺序仍是推荐，但不做跳步拦截。误框批量删除补一条默认口径——命中 `no_japanese` 子类型的行默认不勾选（该类最容易误伤真实拉丁文本与拟声词，含多子类型时以含它为准）。

**涉及文件：** `ui/batch_expand.py`（删）、`tests/test_batch_expand.py`（删）、`scripts/probes/expand_centering_probe.py`（删）、`ui/workbench_tasks.py`、`utils/config.py`、`ui/configpanel.py`、`ui/glossary_agent_panel.py`、`scripts/workbench_recalc.py`、`scripts/audit_registry.json`、`docs/基础速查/设置面板_功能项清单.md`

**验证：** 三个保留的批量任务（可疑框清理／合并／简单背景修复）照常运行；`scripts/verify.py --full` 全过。

---

### 符号连字自动转换（`utils/symbol_convert.py` + `ProgramConfig.symbol_convert_enabled`）+ 重做提示统一画布 toast

**摘要：** 右栏编辑器打字／粘贴时把可合并符号序列自动换成连字（`！！`→`‼`、`！？`→`⁉`，映射表在 `utils/symbol_convert.py`）：只对**聚焦中的编辑器**生效，页面加载／翻译回填等程序性写入与撤销重放不转换，替换处就地短暂高亮；嵌字页窄栏开关 `rail_convert`，状态存 `ProgramConfig.symbol_convert_enabled`。撤销／重做提示统一由画布侧发（`ui/canvas.py::_notify_redo` 与撤销对称、同 key 刷新同一条），主窗口只兜「Ctrl+Z 撤到底还有批量替换版本」的场景。

**涉及文件：** `utils/symbol_convert.py`、`ui/textedit_area.py`、`ui/text_panel.py`、`ui/scenetext_manager.py`、`ui/canvas.py`、`ui/mainwindow.py`、`utils/config.py`、`icons/rail_convert.svg`、`tests/test_symbol_convert.py`

---

### 启动可靠性止血 + 精简包构建闭环（`scripts/build_win_minimal.ps1`）+ PatchMatch 随包可用与按需安装对齐

**摘要：** 针对「用户机启动失败难诊断」：Python 3.10+ 闸门、文件日志 + `sys.excepthook`/`threading.excepthook`、损坏配置隔离（`.corrupt-*`）与 `.tmp` 恢复、坏 torch（OSError）按缺失降级、重启循环守卫、pip 镜像在装依赖前落到环境变量。精简包定义定稿＝嵌入式 Python + pip/uv + **预装 `requirements.txt`** + 随包 `data/libs` 两个 PatchMatch DLL（约 550–600 MB 估算、不带模型后端与权重），构建脚本带发行门禁（干净树 + manifest 覆盖与哈希归一 + 版本一致）。`modules/base.py::ensure_dependencies` 复用 `utils/package_installer`（支持解释器旁独立 `uv.exe`），onnxocr 强制 `--no-deps`（`utils/package_installer.py::NO_DEPS_PACKAGES`，numpy<2 冲突）；PatchMatch 原生库改惰性加载、缺 DLL 给可读提示且不再从修复器选项隐藏。文档全渠道同步、README 标注精简包尚未发布。

**涉及文件：** `launch.py`、`launch.bat`、`utils/logger.py`、`utils/network_mirrors.py`、`utils/core_requirements.py`、`utils/config.py`、`utils/shared.py`、`modules/base.py`、`modules/inpaint/patch_match.py`、`modules/inpaint/inpaint_patchmatch.py`、`ui/module_parse_widgets.py`、`ui/run_pipeline_dialog.py`、`ui/model_downloads.py`、`utils/package_installer.py`、`scripts/build_win_minimal.ps1`、`scripts/README.md`、`README.md`、`README_EN.md`、`docs/基础速查/依赖库说明.md`、`docs/项目概述.md`

**验证：** `scripts/verify.py` 全绿；pytest 1474 passed / 1 skipped。**端到端真实构建未跑**（需干净树 + 网络下载），发版前按 `scripts/README.md`「发版包发行」流程执行（该节 2026-09-27 已改口径：发行物＝约 30 MB 引导小包，预装式不再发行）。

---

