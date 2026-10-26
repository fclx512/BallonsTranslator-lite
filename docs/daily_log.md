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

## 2026-10-26

### 字形掩膜字号估计（`font_size_estimate` / `provides_glyph_mask` / `_glyph_font_metrics`）

**摘要：** ysg_ctd 检测阶段在掩膜合并**之前**对原始 CTD 字形掩膜把每行墨迹指标挂到块上（内存属性、不进工程 JSON），OCR 后整页管线写回 `font_size`／`_detected_font_size`；估计器纯 numpy/cv2（`em_short`×1.103 与 `em_len` 取小 + 逐页 KDE 规范字号吸附）。关键坑：合并后的页级掩膜会整行填充未精修行，直接量会跳过大量块，指标只认原始 fine_mask。仅 `provides_glyph_mask` 检测器生效；单框翻译、区域再检测不挂（有意）。
**涉及文件：** `utils/font_size_estimate.py`、`modules/textdetector/detector_ysg.py`、`utils/textblock.py`、`ui/module_manager.py`、`ui/pipeline_runner.py`、`tests/test_font_size_estimate.py`、`docs/技术实现/字形掩膜字号估计_设计与实现.md`
**验证：** 织姬工程 24 页：`em` 与人工成品字号之比中位 0.979；单测 22 项。

---

### 运行时收成「源码+完整包」唯一形态（`runtime_compatibility.json` / `bundled_runtime` / `install_cuda_bundle` / `check_bundle`）

**摘要：** Windows 只支持 `ballontrans_pylibs_win` 完整包：`launch.bat` 只认包内解释器；`launch.py` 重定向进包并按根目录 `runtime_compatibility.json`（cpu/cu126/cu130/cu132 四档契约，入库）校验；`install_cuda.bat` 425 行砍成薄包装，逻辑移入 `scripts/install_cuda_bundle.py`——默认只读实测 torch/ORT，`--install` 才成对替换并支持回滚；依赖安装收口 `utils/package_installer.py`，更新链路统一预检；启动不再自动装依赖，联网修复须显式 `--repair-deps`。收尾小修：`launch.py` 解释器重定向加 `__name__` 守卫（`import launch` 不再 execve 劫持测试进程）、删除无引用的 `tests/cuda_sandbox.py`（audit 登记）、`out/` 探针产物整体 gitignore。
**涉及文件：** `launch.py`、`launch.bat`、`install_cuda.bat`、`utils/bundled_runtime.py`、`utils/runtime_compatibility.py`、`runtime_compatibility.json`、`scripts/install_cuda_bundle.py`、`scripts/check_bundle.py`、`utils/package_installer.py`、`utils/updater.py`、`docs/技术实现/CUDA环境与索引_说明.md`、`docs/基础速查/依赖库说明.md`
**验证：** cu132 本机全链路验收（安装/启动/包清单/全量 pytest 1802 通过/verify --full 全绿），产物在 `out/bundle-cu132-accept-20261023`（本机）。
**遗留：** CPU 完整包、干净系统、其他 GPU 未实测；`pip check` 保留 7 条声明例外。

---

### pytest 配置沙箱（`install_config_sandbox` / `tests/conftest.py` / `_check_config_unchanged`）

**摘要：** 全量测试不再写真实 `config/`：`tests/conftest.py` 收集前装 `scripts/test_config_sandbox.py::install_config_sandbox`（默认路径改临时目录 + `sys.addaudithook` 拒绝真实配置的写/改名/删除），`scripts/verify.py` 增加配置快照校验，`tests/test_global_styles.py` 改走 `tmp_path`。起因是 10-23 验收首次全量 verify 时 `test_global_styles` 写真实 `global_styles.json` 报 PermissionError。
**涉及文件：** `tests/conftest.py`、`scripts/test_config_sandbox.py`、`tests/test_config_sandbox.py`、`scripts/verify.py`、`tests/test_global_styles.py`

---

### 工作台入口迁移（`PipelineWorkbench` / `menu_mode` / `workbench_tool_requested`）

**摘要：** 左栏从术语面板换成管线常驻侧栏 `PipelineWorkbench`（继承 `RunPipelineDialog` 复用参数表单，340px 嵌入；原运行按钮只开侧栏，启动走 `start_pipeline_workbench`）；术语/批量工作台挪进非模态工具窗口（`GlossaryAgentPanel(menu_mode=True)`），Tools 菜单重组出批量处理／待办／翻译资料／取色与角色四组入口，右栏格式区加取色按钮。引擎与语言在底部栏、设置页、侧栏间双向同步；运行期统一禁用。顺手清掉 `_run_dialog` 死槽位并整理五个文件的 import。
**涉及文件：** `ui/run_pipeline_dialog.py`、`ui/mainwindow.py`、`ui/mainwindowbars.py`、`ui/glossary_agent_panel.py`、`ui/pipeline_tasks.py`、`config/stylesheet.css`、`tests/test_run_pipeline_dialog.py`
**验证：** `tests/test_run_pipeline_dialog.py` 5 个新用例（完成度轨/范围切换/嵌入式/入口/引擎行几何）。

---

### 竖排列坐标 1/64 网格稳定化（`_stable_column_x`）

**摘要：** 宽度变化重排时，落在 QTextLine 26.6 定点边界附近的小数列距会向不同方向舍入，整篇列位置偏移 1 单位；首列 x、列间距累减与末列位置统一经 `_stable_column_x` 收到 1/64 网格。与 10-01 的「字体变换×描边双影」无关（那案仍未修）。
**涉及文件：** `ui/text_engine/vertical_layout.py`、`tests/test_text_transform_engine.py`

---

