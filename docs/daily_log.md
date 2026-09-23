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

## 2026-09-23

### 样式管理器左树区带化（库/本项目/未分组）+ `setExpanded` 顺序 bug + 动作按钮 `btnRole` 色彩语义

**摘要：** 用户反馈「收藏库和项目样式混在一起、辨识度低」，查下来三条成因：①**真 bug**——`ui/fontstyle_manager.py::StyleTreeWidget.populate` 两个区头行在 `addTopLevelItem` **之前**调 `setExpanded(True)`，Qt 下是空操作（item 未入 view，展开态存不下来），于是打开时库区/未分区都收起、库标题紧贴项目样式连成一片；②行内只有块数一个区分位，而库条目该位是空的，库条目与项目样式可字面同形（行1近似、行2相同）；③大样式是顶层行、库条目是子项，缩进深浅与归属感相反。做法：三区都改**自绘区带行**（委托新增 `section` 分支＝半透明底+加粗标题+条目计数），库区仍在最上；库条目右侧块数槽换成「模板」胶囊标签（`_theme_accent()` 描边，**不能用 `palette.Highlight`**——暗色主题下它就是选中底色，标签会整块隐形）；`select_payload` 改递归（多一层）。顺带修选中高亮的**双层接缝**（QSS 铺 `@accentPrimary20`、委托叠 `palette.Highlight`，两色不同在色板处留缝）与清掉 `#StyleList` 三条死规则（对应的 `StyleBlockList` 早删）。动作按钮加 `btnRole` 属性（`primary`/`accent`/`danger`），QSS 按角色上色——文案之外再给一层视觉语义。**补漏（用户看图回报）**：六个动作按钮的显隐各自写在对模式的 `show_*` 里，「没有模式」这条路谁都没隐藏，刚打开时六个挤一行被裁且全都无从触发；右栏加无选中态（`StyleDetail.show_empty`：内容整体收起、只留引导语），并在重选失败时也退回该态——此前选中项被删后右栏会停在被删样式的快照上。

**验证：** `scripts/verify.py` 全绿；新增 `tests/test_global_styles_ui.py::test_actions_hidden_until_a_style_is_selected`（未选中态按钮全隐，`isVisibleTo` 判可见性）；其余只在既有用例上加钉子（`test_two_line_tree_nodes` 补区带行已展开、`test_library_section_in_tree` 补「模板」标签键、删除用例补退回无选中态）——按用户口径：只留会动的行为契约，把自己刚写的结构再断言一遍的用例不留；暗/亮两套渲染沿选中行中线扫色，确认两主题整行单色（接缝消失）。

**涉及文件：** `ui/fontstyle_manager.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`scripts/stylemgr_render.py`、`scripts/README.md`、`tests/test_fontstyle_tree.py`、`tests/test_global_styles_ui.py`、`docs/技术实现/全局样式库_设计方案_存档.md`、`docs/技术实现/查找替换与样式管理器重构_设计方案_存档.md`

**遗留：** 区带行不响应点击（不可选中、无 payload，只作分区）；左树仍无搜索/过滤框，库条目涨到几十条时要靠折叠区带自己找。另顺手补了 `docs/技术实现/模型文件管理_设计方案_存档.md` 一处失效路径引用（引 `tmp/` 下备份文件，本就不入库，`check_docs` 一直红着）。

---

## 2026-09-21

### 设置面板三件收尾：管线页并入分节卡、应用页导入导出并成一节、工作台页摘掉「临时」

**摘要：** 三处「上一轮没做完/明显该合」的收尾。①**管线标签页补上卡片**（09-20 卡片化时唯一被留在门外的页）：外层页与标签页体都改凹陷面，`ModuleConfigParseWidget` 里的「参数」与 `TranslatorConfigPanel` 的「API Profile」改为 `ui/custom_widget/view_panel.py::add_section_card` 建的卡（`ui/configpanel.py::_section_body` 降为它的薄封装——管线面板不能反向 import `ui/configpanel.py`）；API 配置卡排在参数卡之前，顺带把「参数」标题从只盖住 API 配置块纠正为真盖住参数表。②**应用页「导出配置」「导入配置」两张卡并成一张「导入导出 / Import / Export」**（同一件事的两个方向，导出那两条含纯界面态的「排除 API 密钥」，紧邻才有意义；应用页 5 卡 → 4 卡）。③**工作台页去掉「临时」字样**：导航与页标题都改「工作台 / Workbench」，两行的 `note=` 收成一句话（实测数字本来就在设计文档的参数表里，气泡里重复一遍既冗长、又和表里数字对不上）；导航 key 仍留 `workbench_temp`（改名只动显示文案，key 一动要连坐 4 处测试与渲染脚本）。

**验证：** `scripts/settings_render.py` 暗/亮两套逐页目视（管线页新增卡片、应用页 4 卡）；`tests/test_config_section_cards.py`（应用页卡数 5→4、新增「管线标签页也套卡 + API 配置卡在参数卡之前」）、`tests/test_config_card_painting.py`（管线页纳入描边连续性 + 新增「阶段面板不得画底色」一条）随改同步；`scripts/i18n_check.py` PASS（`ts_auto_fill` 增 4 条、删 3 条孤儿，中文补齐后重编 qm）。

**涉及文件：** `ui/configpanel.py`、`ui/module_parse_widgets.py`、`ui/custom_widget/view_panel.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_config_section_cards.py`、`tests/test_config_card_painting.py`、`tests/test_settings_app_page.py`、`docs/基础速查/设置面板_功能项清单.md`、`docs/基础速查/设置面板排版思路.md`、`docs/技术实现/设置面板概述.md`、`docs/技术实现/AI辅助功能_设计与实现.md`

**遗留：** `ui/custom_widget/section_header.py::ConfigSectionHeader` 现已无应用内调用者（只剩 `scripts/style_showcase.py` 展示行），本轮决定留作控件库原语不删；要收就删「类 + 展示行 + `__init__` 导出」三处并登记 `audit_registry.json`。另：渲染台里管线四标签的参数表本来就是空的（进程没有 `ui/module_manager.py`），本轮用一次性脚本注入假参数补了目视核查，真机四个标签未逐个点过。

---

### README 换新（部署/更新段按代码重写）+ 技术文档归档压缩（9 篇 `_存档`）

**摘要：** ①README 正式替换：定位段与用前须知改写成「取舍标准 + 面向的用法」，去掉宣言式表述；**部署/更新段按代码核对后重写**——原「一键包不含 git、无法经启动脚本或应用内更新，应用内检查在 Help→About」三条全不成立：`launch.bat` 有 ZIP 形态分支（`--update`/`--check-update` → `scripts/check_update.py` 按 `manifest.json` 增量取源文件、下次启动应用），应用内更新在**设置 → 应用 → 更新**（`utils/updater.py::BallonsTranslatorUpdater` 下 release 源码 zip、原子替换白名单目录、不碰 `data/`），另有「开发者通道：检查提交更新」；CUDA 索引表补 `cu130` 档（`utils/env_diagnostic.py::_CUDA_TIERS` 是唯一真相）、模型下载时机改述为「选中模块时下载」。②技术文档归档：9 篇已完结文档改 `_存档` 后缀并压成「结论 + 约束与坑 + 指针」（效果栈 355→85 行、模型文件 352→205、区域再检测 267→134），「处理」那篇去前缀后清理陈旧表述（对照代码纠出 6 处与实现不符，如 guardrails.py 实为 validator.py、工具面复用 `utils/ai_tools.py::execute_tool`、`TOOL_RESULT_CHAR_CAP`=24000）。③`docs/项目概述.md` 索引拆成「活文档 / 已归档」两表并写明 `_存档` 命名约定。

**验证：** `scripts/check_docs.py` 通过；归档改名引发的 64 处引用（代码 docstring、`AGENTS.md`、`scripts/audit_registry.json`、probes README）同步完毕。

**涉及文件：** `README.md`、`README_EN.md`、`AGENTS.md`、`docs/项目概述.md`、`docs/技术实现/`（9 篇 `_存档` + `翻译agent化_设计方案.md` + `CUDA环境与索引_说明.md`）、`scripts/audit_registry.json`、`utils/global_styles.py`、`utils/memory_release.py`、`ui/fontstyle_manager.py`、`manifest.json`

---

### CUDA 索引分档收成唯一真相 + `install_cuda.bat` 重写 + 三层回归台

**摘要：** ①分档阈值收进 `utils/env_diagnostic.py::_CUDA_TIERS`（CC≥10 → `cu132`、≥9 → `cu130`、≥6 → `cu126`、更低不支持），`install_cuda.bat` 与它同阈值，两侧不再各写一套。②删掉「`cu124`」「`nightly/cu128`」两条推荐：cu124 最后一个版本是 torch 2.6.0，装了会把已有更新版本的 torch 静默降级；nightly 通道版本天天漂。`ui/network_settings_dialog.py` 的 pip extra index 占位符同步改 `cu126`。③`launch.py` 取消 torch 版本钉死（原 `torch==2.7.1` 对已有更新版本的用户是降级，且 cu132 根本不发该版本、命令直接失败），`--reinstall-torch` 改为 `-U torch torchvision` 且不带 torchaudio（新索引不发、本项目无音频 IO）。④验证做成三层：L1 静态（`tests/test_cuda_install_env.py` 断言禁用写法与两侧映射一致）、L2 离网沙箱（`tests/cuda_sandbox.py` 用假 python.exe 驱动脚本全分支）、真机行为台（`tests/test_install_cuda_script.py` 真跑 cmd），另补只读体检台 `scripts/check_cuda_env.py`（回答"某索引还活着吗/本机现在什么状态"，不装不卸）。

**验证：** 相关 6 个测试文件 pytest 111 passed；`scripts/verify.py` 全绿。

**涉及文件：** `install_cuda.bat`、`utils/env_diagnostic.py`、`launch.py`、`ui/network_settings_dialog.py`、`scripts/check_cuda_env.py`、`tests/test_cuda_install_env.py`、`tests/cuda_sandbox.py`、`tests/test_install_cuda_script.py`、`tests/test_platform_torch_detect.py`、`docs/技术实现/CUDA环境与索引_说明.md`

---

