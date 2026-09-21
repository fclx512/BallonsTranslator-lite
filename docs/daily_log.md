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

## 2026-09-20

### 引导包发行链路：`scripts/build_win_minimal.ps1` + 首启动镜像/uv/按需依赖（附四条修正）

**摘要：** 用户拍板跟进上游发行策略：改发「源码 + 裸嵌入式 Python + pip + `uv.exe`」的小包（约 32 MB），重依赖首启动由 `utils/core_requirements.py::ensure_core_requirements` 装、模型推理包在选中模块时由 `modules/base.py::ensure_dependencies` 装、权重由 `ui/model_downloads.py` 后台下，预装一体包留作网盘兜底。构建脚本源码取材走 `git ls-files`，被 gitignore 的 `config/config.json`（含 API 密钥）由构造保证不入包，脚本另加硬校验；两条渠道共用同一个 `launch.bat`。这条路线把四件被「预装包」掩盖的问题暴露出来并逐个修掉：① 自动配镜像**一直是空转**——`utils/network_mirrors.py::auto_fill_mirrors` 写的是自造节 `mirrors.pypi`，真实读取点是 `mirror.pip_index_url`；且首启动装依赖发生在 `utils.config` 能加载之前（它依赖 numpy/PyQt6），故新增 `apply_pip_mirror_env` 在 `launch.py` 前段把 pip 源落到 `INDEX_URL` 环境变量、`auto_fill_mirrors` 前移。② 发行包的 `uv.exe` 与 `python.exe` 同目录却不入 PATH（`launch.bat` 按绝对路径调解释器），只看 PATH 会静默退回 pip，新增 `utils/package_installer.py::find_uv`（PATH → 解释器同目录）并让 `launch.py::run_uv` 兼容「无 Python 模块、只能直接执行」的 uv 形态。③ `launch.py` 的自动降级（缺依赖/缺模型 → 模块换 `none`）会**落盘**、把用户与默认的选择永久抹掉，而它自己的提示语还写着「then restart」，新增 `utils/config.py::record_auto_downgrade` 把降级登记为「仅本次运行有效」，`save_config` 落盘时换回原值（界面上另选了别的模块则以用户为准）。④ 默认修复器 `lama_large_512px` 需要 torch 却没声明（`requirements.txt` 刻意不含 torch），引导包里就没人会装它，补 `requires_packages`；`ui/mainwindow.py` 的运行前检查同时报缺包，覆盖「选中模块后后台还在装」的那段窗口。

**验证：** `tests/test_bootstrap_launch.py` 14 例（镜像节名与「空串是用户选择、只有缺键才算未配置」、环境变量不被覆盖、`find_uv` 三级回退与命令形态、降级不落盘且用户另选优先、torch 声明被 `GET_MISSING_PACKAGES` 读到）；`scripts/verify.py --full` 全绿。**构建脚本未随本次提交实际出包**——本机既无 `build_temp` 也无产物 zip。

**涉及文件：** `scripts/build_win_minimal.ps1`、`launch.py`、`utils/network_mirrors.py`、`utils/package_installer.py`、`utils/config.py`、`modules/inpaint/base.py`、`ui/mainwindow.py`、`.gitignore`、`tests/test_bootstrap_launch.py`、`docs/基础速查/依赖库说明.md`、`scripts/README.md`

**遗留：** 引导包尚未实际发版（顺序：提交 → `scripts/generate_manifest.py` → 打 tag → 构建脚本或 CI → 上传）；CI 仍未接。

---

### 修 `ConfigFlatContainer`：卡内排版容器的默认底色盖掉卡片描边、卡片之间看不出分界

**摘要：** 用户实机反馈两条——分节卡的左右描边从标题下方整段消失/下缘被方角色块啃掉，卡片之间也分不开。真因是页内中间容器（裸 `QWidget`、`ConfigSubBlock`、`QLabel`）都没有自己的背景规则，落回全局 `QWidget` 底色，而内置主题里 `@qwidgetBackgroundColor == @widgetBackgroundColor`（卡片色），同色所以看不出来；描边却画在卡片矩形上、子控件后画且不随圆角裁剪，被盖个正着。修法：新增 `ui/configpanel.py::ConfigFlatContainer`（**只排版不画底色**，`ConfigSubBlock`/`ConfigFormRow` 改挂它、页内 17 处裸 `QWidget` 一并换掉）+ stylesheet 同名规则，标签与复选框再由 `#ConfigPanel QLabel, #ConfigPanel QCheckBox` 关掉兜底底色；模型文件状态条（`ui/model_files_panel.py::ModelFilesSection`）只留顶边分隔线。**不做阴影**（用户明确因性能取舍后置）。

**验证：** `scripts/settings_render.py --diag`（本次给渲染台新增的诊断配色：页面凹面/Widget 面/裸 QWidget/描边 = 品红/绿/橙/白）逐页比对——7 个分节页的卡片四边描边连续、卡片间隙全为页面凹面色、卡外无残留底色。新增 `tests/test_config_card_painting.py` 三条用例钉住（逐边查描边连续性 + 间隙必须是页面凹面色），并用负向对照验过它抓得住（把 `ConfigSubBlock` 换回 `Widget` 基类 / 恢复状态条底色 → 三条用例全红）。

**涉及文件：** `ui/configpanel.py`、`ui/model_files_panel.py`、`config/stylesheet.css`、`scripts/settings_render.py`、`tests/test_config_card_painting.py`、`docs/基础速查/经验教训.md`（新增 §3.6：本机实测的 QSS 选择器语义四条 + 排查手法）、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

---

### 新增 `docs/基础速查/设置面板_功能项清单.md`：管线外 10 页功能项逐条落表（为分节样式与页面重排备料）

**摘要：** 设置面板 10 页只有「粗体标题 + 8px 空隙」做分节（`ui/configpanel.py::_section_header` 无边框无底色），而侧边导航的组标题行与叶子项共用同一套悬停/padding 规则、选中态又只靠粗体（`config/stylesheet.css` 的 `#ConfigNavList::item:selected` 背景透明），两者辨识度都低。先只写现状清单：逐页列出功能项 → 分节 / 控件 / 写入字段 / 门控显隐，并列分节与导航各 4 条样式候选（含代价与「快捷键页那种分组框」做法的双层框风险）、以及重排会被哪些测试断言拦住（`tests/test_configpanel_node3.py::ConfigPanelNode3Test`、`tests/test_settings_app_page.py::SettingsAppPageTest`）。当天稍后据此落地了卡片化（见下一条），清单第 2、3 节已改写成改版记录。

**涉及文件：** `docs/基础速查/设置面板_功能项清单.md`、`docs/技术实现/设置面板概述.md`、`docs/基础速查/设置面板排版思路.md`

---

### 设置面板卡片化：导航条换掉 `QTreeView` + 页内分节改成卡片（`ConfigNavRail` / `_section_body`）

**摘要：** 用户观感「列表样式怎么调都丑、分节只有粗体分不开」，故**弃用树控件改卡片式导航条**（`ConfigNavRail`/`ConfigNavGroup`/`ConfigNavItem`：分组卡 + 可勾选 chip，一个 `QButtonGroup` 保证唯一选中，checked 态用 `@accentPrimary20Solid` 底 + 强调描边——旧树的「选中只靠粗体」与组标题/叶子共用 item 规则的问题一并消失）；**页面内分节改成卡片**（复用 `PanelGroupBox` 的 `compact` 变体，21 处旧标题 + 模型文件节 + 快捷键 8 组全改走 `_section_body`：一个分节＝一张卡，行物理上在卡里）。**浮起不靠投影**——`QGraphicsEffect` 在本仓有 qFatal / 滚动区渲染两处实测坑，改用三层色调：面板与页面底凹陷（`@emptyContentBackgroundColor`，`#ConfigPanel` + `Widget#ConfigPageBody`）、卡片浮起 + 1px `@borderColor` 描边，露出凹陷色当"阴影槽"。导航条保持旧 API（`section_pressed`/`addSection`/`section_items`/`setCurrentSection`），分页连线与 `focusOn*` 一行未动；管线标签页刻意不套卡、也不凹陷（`_wrap_page(recessed=False)`）。

**验证：** 新增 `scripts/settings_render.py`（10 页 × 暗/亮出 PNG）逐页目视；`tests/` 1349 通过 / 1 跳过（改版新增 `test_config_nav_rail.py`、`test_config_section_cards.py`，重写 node3 的分节次序断言）；`scripts/verify.py` 全绿。

**涉及文件：** `ui/configpanel.py`、`ui/model_files_panel.py`、`config/stylesheet.css`、`scripts/settings_render.py`、`scripts/README.md`、`translate/zh_CN.ts`、`tests/test_config_nav_rail.py`、`tests/test_config_section_cards.py`、`tests/test_configpanel_node3.py`

**遗留：** 页面功能的归并重排未做（线索见清单第 6 节）；管线标签页要不要也卡片化未定。

---

### 依赖兼容上游 + 共享环境「只增不升」补装（解 pillow 钉子 / `ensure_core_requirements`）

**摘要：** 为与上游共享同一依赖库，解除 `pillow>=10.0,<11` 钉子（上游 pillow 12.2.0 + pillow-jxl-plugin 实测 JXL roundtrip 正常，该钉子已过时）。补装语义改为**只增不升**：`utils/core_requirements.py::ensure_core_requirements` 补装前对已装发行版生成 `名称==版本` 快照，经 `utils/package_installer.py::build_install_command` 新增的 `constraints_file` 参数作 pip `-c` 约束传入——缺的包照补，任何会升级既有包的解析直接失败、快照留盘交人工决策。另 `pyproject.toml` 增 `acc` extra（numba 可选加速，缺失照旧回退纯 NumPy）。

**验证：** 带约束的 `pip install -r requirements.txt --dry-run` 解析无报错无升级；`tests/test_dependency_startup.py` 8 通过。

**涉及文件：** `pyproject.toml`、`requirements.txt`、`utils/package_installer.py`、`utils/core_requirements.py`、`docs/基础速查/依赖库说明.md`

---

### 左栏打开钮回归图标轴线（去 QToolBar 包裹 + `ui/mainwindowbars.py::OpenBtn` 菜单自持）

**摘要：** openBtn 偏左、与下方四个 33px checker 不同轴，两个根因：外面包的 33px `QToolBar` 自带边距把 28px 按钮挤出轴线；左栏 vlayout 实为**左锚排列**（各项同点 x=7），宽度不同即中心错开。修法＝去掉 QToolBar 包裹、openBtn 直接 33×33 进布局（QSS `image` 随盒缩放，`OpenBtn` 规则加 `padding: 3px` 把字形压回原尺寸）；菜单改 `OpenBtn` 自持（`assignMenu` + 点击手动弹出），因为 `QToolButton.setMenu` 的菜单指示器会在右侧保留箭头位把 QSS image 挤到左边，且 QSS 归零 width/height/image 都收不回那块保留区。

**验证：** 离屏渲染实测五图标字形中心 28.5~29.5 对齐（余量 ±0.75px 为 SVG 内容 1px 不对称）。

**涉及文件：** `ui/mainwindowbars.py`、`config/stylesheet.css`

---

### 依赖政策：CPU 发行包刻意不装 transformers（`paddleocr_vl_manga` 专属依赖）

**摘要：** `paddleocr_vl_manga` 是唯一走 transformers 的模块，且该导入写在 `_load_model` 内（不在模块顶层），所以「把注册模块全 import 一遍」查不出缺包。它是 GPU 才实用的生成式 VLM（CPU 上逐块自回归无实用价值），而 CPU 包面向的正是无独显用户，`transformers` 本体 113 MB 属白占 ⇒ **决定不随 CPU 包分发**；GPU 用户换 CUDA torch 后选中该模块即由 `modules/base.py::ensure_dependencies` 自动补装（区间收在 `modules/ocr/ocr_vl_manga.py::requires_packages`，不需要 sentencepiece）。CPU 包验收范围相应移出 VL OCR 自检。

**验证：** 卸载后发行版数回到 79（与改动前一致）、`pip check` 与开发包同基线、`tests/test_startup_imports.py` 6 通过、14 个注册模块强制解析零失败、各模块声明的权重齐备。顺带实测该模块在 CPU 包上确实可跑（合成竖排日文识别出 `ざわ / ざわ`，加载 3.0s、单块 1.7s、fp32），只是不随包分发。

**涉及文件：** `docs/基础速查/依赖库说明.md`（依赖包本体在仓库外）

---

### GPU 硬闸门：无可用加速设备时拒绝下载 GPU-only 模型（`requires_gpu`）

**摘要：** 新增模块侧声明 `modules/base.py::BaseModule.requires_gpu`（贯通 `utils/registry.py::ModuleSpec`、`utils/lazy_registry.py::LAZY_CLASS_ATTRS`、`modules/__init__.py::GET_MODULE_REQUIREMENTS`），首用例 `modules/ocr/ocr_vl_manga.py`；判据 `modules/base.py::accelerator_available` 取 `DEFAULT_DEVICE != "cpu"`（**刻意不用** `torch.cuda.is_available()`——xpu/mps/directml 同样跑得动）；硬闸门落在 `ui/model_downloads.py::ModelDownloadRegistry.start`（命中即拒，pip 依赖与权重都不碰），文案与判据同处一地（`gpu_required_message`／`gpu_requirement_block`）。两个入口各按自己的惯例弹同一文案：「模型文件」页下载钮，与 `ui/module_manager.py::_ensure_module_deps`（后者是 §5.1「选模块不弹窗」的有意例外，模块照常切换）；面板徽章改「需要 GPU」而行仍可点（`ui/model_files_panel.py::_badge_of`），加载期缺文件提示也换 GPU 口径、不再指路去一个会拒绝你的下载钮。

**闸门只拦下载、不设运行侧障碍**（用户拍板：提示到位就够了，没必要故意妨碍）——第二段文案相应说「不提供下载」，与实现同口径；运行侧仍是 `_resolve_device` 警告后退回 CPU，手工放入权重照样能跑。文案措辞**由用户 2026-09-20 定稿，改动前先问**；取舍的完整记录见 `docs/技术实现/模型文件管理_设计方案_存档.md` §5.5。

**顺带修掉一个 i18n 提取器盲区：** `scripts/i18n_common.py` 的 `QCoreApplication.translate` 正则原先只认「两个参数同一种引号」，`translate("ctx", '文本')` 这种混合写法两条正则都匹配不到、静默漏进 .ts（三条既有文案因此在中文界面一直显示英文）。

**验证：** CPU 依赖包实测闸门拒绝（`start()` 返回 False、注册表无任务、其他 OCR 模块不受影响）、开发包放行、开发包加 `BALLOONTRANS_CPU_ONLY=1` 也拒绝；`tests/test_model_downloads.py::TestGpuGate` 新增 7 项钉住拒绝分支（开发机自带 CUDA，不打补丁走不到）；i18n 三查 + qm 重编 + `QTranslator` 实测载出中文，渲染图见 `tmp/00_gpu_refused_zh.png` 等三张。

**涉及文件：** `modules/base.py`、`modules/ocr/ocr_vl_manga.py`、`modules/__init__.py`、`utils/registry.py`、`utils/lazy_registry.py`、`ui/model_downloads.py`、`ui/model_files_panel.py`、`ui/module_manager.py`、`scripts/i18n_common.py`、`tests/test_model_downloads.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`AGENTS.md`、`docs/技术实现/模型文件管理_设计方案_存档.md`（新增 §5.5）、`docs/基础速查/依赖库说明.md`

### 开发日志规范改「可检索优先」+ AGENTS.md 瘦身（不常用技术点只留钩子）

**摘要：** 日志定位改为**主要给 AI 读的时间索引**（用户基本不看它）：格式固定三行——标题（带关键符号名／配置字段名／D 编号，供 grep）、`**摘要：**`（2~4 句，只写决策与理由）、`**涉及文件：**`（最常用的检索键），实现过程留给 commit body 与设计文档、一条 4~8 行；三天存量按此重写（25741 → 16666 字符，降 35%，19 条不丢）。3 天窗口保留，另写明更早记录的 git 回查口令（`git log --grep`／`git log --follow -p`／`git show <rev>:docs/daily_log.md`），配套要求**提交信息与日志标题用同一批关键词**，日志被裁掉也不丢线索。AGENTS.md 侧按「不常用的技术点只留一句定位 + 指针」收一遍：`modules/ocr/ocr_vl_manga.py` 的实现坑（整块裁剪不逐行、`use_cache`、`add_prefix_space=None`）已在 `docs/技术实现/paddle-ocr-for-manga_接入调研_存档.md` 里，行内不再重复；模型文件管理四行与 `utils/memory_release.py` 同样只留约束与指针。

**验证：** `scripts/trim_daily_log.py --check` 确认新格式仍能被窗口裁剪正确解析；`scripts/verify.py` 全绿。

**涉及文件：** `AGENTS.md`、`docs/daily_log.md`

---

## 2026-09-19

### 工作台批量任务页加刷新钮（重扫候选列表，`ui/workbench_batch_view.py::RefreshButton`）

**摘要：** 候选列表是懒规划快照，用户在画布上删框／改框后列表不跟随，出现「计数有数、列表空／过期」（误识别清理尤其常见）。新增 `RefreshButton`（QPainter 自绘循环箭头图标钮，主题取色、悬停描边，不引 SVG 资源），在 `BatchTaskView._build_actions` 里置于执行行首位，点击即重跑既有 `replan()`（连带 `plan_changed` → 导航计数同步）。术语／剧情两页是草稿镜像、无此需求，不加。

**验证：** `scripts/verify.py` 全绿；`scripts/workbench_render.py` 渲染并放大目视确认图标可认。

**涉及文件：** `ui/workbench_batch_view.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 行拖拽逐帧绘制优化（多选掉帧）+ 抓住缩放改 1.05（`ui/textedit_area.py`）

**摘要：** 掉帧主因＝被拖卡装着 `_CardScaleEffect`，而 Qt 对几何变化的控件会失效效果源 pixmap 缓存 ⇒ 跟手补间每帧都要把整张卡（两个 QTextEdit 的富文本排版）重渲到设备分辨率离屏图，多选即 N 次/帧。三处改动：① **效果源 pixmap 缓存**——拖拽期间卡片内容冻结，装上后首帧抓一次、`draw()` 复用缓存做缩放 blit，factor 回 1.0 弃缓存；② **跟手重定向 2px 死区**（只作用于 `_update_drag_frame` 的追手路径），避免光标慢移时反复停旧建新 `QPropertyAnimation`；③ `GRAB_SCALE` 1.1→1.05。中途踩到一处缓存坐标错误：`sourcePixmap(DeviceCoordinates)` 的 `offset` 是**当帧控件在窗口里的绝对位置**（随移动逐帧变化），直接缓存会把卡片钉死在抓取时刻的位置——改为缓存「pixmap 左上相对控件原点」的常量偏移，每帧用 `deviceTransform` 现算原点。

**验证：** `tests/test_row_drag.py` 新增三组（缓存只抓一次、快照跟手移动、死区重定向），27 项连跑 10 次全绿。新增用例曾引出套件约半数用例的退出期 access violation（悬挂效果的控件在解释器退出期 GC 级联中销毁；stdout 缓冲让崩溃看似发生在更早的用例），用例结束前 `QTest.qWait(300)` 排干收尾动画后 10/10 稳定。

**涉及文件：** `ui/textedit_area.py`、`tests/test_row_drag.py`

---

### RowTable 自绘行列表控件替换工作台裸 QTableWidget（`ui/custom_widget/row_table.py`）

**摘要：** 裸 `QTableWidget` 逐格 QSS 边框渲染不全、背景兜底规则只按裸 `QTableView` 匹配不及子类。新增 `RowTable`（经 `ui/custom_widget/__init__.py` 导出）：`MODE_TABLE`＝列对齐紧凑表格（淡行分隔线、无竖网格），`MODE_CARD`＝主文+次行元数据+右侧徽章的圆角审批卡；底色／选中染底／勾选框／徽章全在 delegate 自绘（行数据经 `Qt.UserRole` 传 dict），QSS 只管底色与列头，主题配色走 `get_theme_color`、缓存于 delegate 且 `StyleChange` 时失效。工作台四个批量任务候选列表切到该控件，展示台与 AGENTS.md 控件表同步。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/custom_widget/__init__.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`scripts/style_showcase.py`、`config/stylesheet.css`、`translate/zh_CN.ts`、`translate/zh_CN.qm`、`tests/test_workbench_panel.py`、`AGENTS.md`

---

### 本批收尾：RowTable 可读性打磨 + 扩张页改版 + 文档瘦身等（分类型提交）

**摘要：** 把当天挂起的修改按类型分批提交；另修一处对齐——左栏「运行」按钮与设置图标同点左锚但宽度 33 vs 28，中心错开 2~3px。要点：RowTable 悬停行淡染，选中染底上的文字按实际观感色在 `@dragTextColor`／`@inverseTextColor` 里挑对比更高的（部分主题 `@dragTextColor` 是暗色，固定取会看不清）；`ExpandTask` 列由「旧矩形/新矩形」坐标改单列「增长」描述，并新增真机探针 `scripts/probes/expand_centering_probe.py`（扩张后译文按新框重排，「是否居中」取决于块自身 alignment）；`utils/font_scan.py::scan_font_faces` 的日志压制改为整棵 `fontTools.*` 树一起压（全局 `setLoggerClass` 后子 logger 自带 console handler，压根 logger 压不住）；新增 `scripts/trim_daily_log.py` 与 `scripts/hooks/pre-commit`（daily_log 3 天窗口自动裁剪）；文档瘦身（AGENTS.md 关键文件表改「一句话定位 + 最小约束 + 指针」，多篇速查砍历史细节与过期内容），并新增 `docs/技术实现/paddle-ocr-for-manga_接入调研_存档.md`。

**涉及文件：** `ui/custom_widget/row_table.py`、`ui/workbench_batch_view.py`、`ui/workbench_tasks.py`、`ui/mainwindowbars.py`、`utils/font_scan.py`、`config/stylesheet.css`、`scripts/trim_daily_log.py`（新）、`scripts/hooks/pre-commit`（新）、`scripts/probes/expand_centering_probe.py`（新）、`scripts/README.md`、`scripts/probes/README.md`、`AGENTS.md`、`docs/基础速查/`（6 篇）、`docs/技术实现/`（2 篇新）

---

