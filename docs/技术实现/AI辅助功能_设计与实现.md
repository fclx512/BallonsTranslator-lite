# AI 辅助功能 · 设计与实现

> **本文是三合一的唯一设计文档**：AI 辅助功能（标签体系 + 框级动作）与工作台（术语／剧情任务 + 泛用批量任务）。
> 前身三份文档（AI 辅助功能规划、术语剧情工作台交接、泛用工作台规划）已删除并登记于 `scripts/audit_registry.json`，
> 其中仍然有效的内容全部收进本文。**翻译 agent 的架构基线另见** [翻译agent化_设计方案](翻译agent化_设计方案.md)——
> 本文只在体系总览（§2）与术语供给链（§9）处引用它。
> 状态：标签与框级动作批次 A–D 已落地（2026-09-13 结单）；工作台与四个批量任务全部落地（2026-09-18）。
> 余项集中在 §16／§17；**改这块之前先读 §18 易错点**。代码注释里的「设计 §X」即指本文节号。

---

## 1. 总纲

1. **不做通用 chat 对话。** 对话形态必然过度承诺：散文是合法出口 → 模型总能给出一段像样的回答 → 用户按「能说」的广度推断「能做」的广度 → 撞墙时落差极大。
   **能力边界必须由形态自证**（按钮本身就自证了边界），不能靠 prompt 里写自我限定。
2. **AI 只做定点单轮判断，筛选与信息组装由代码承担。** 置信度、正则、几何这类确定性判据一律程序算；AI 的每次调用都是「固定 prompt 骨架 + 精选上下文 + 单一出口」。
3. **读写分离。** AI 的写只有一个收口（翻译侧 `submit_translations`、工作台侧两个 patch 工具），且**只写草稿、零写盘**；落盘一律经人工确认。
4. **人审查优先。** 除明确可完全放心交由代码处理的事务，都应由用户确认，不擅自修改；批量行为一律弹窗告知后果。
5. **细致调整永远留给人**（措辞语气、单框间距、存疑行取舍），AI 只标注、不代劳。

**已明确不做**（不再重新提议）：外部宿主与 MCP（评估结论见 §2）、通用对话／自由探索入口、把置信度分数喂给模型、标签进右侧文本区。
「不做通用对话」是这套设计的起点，不是限制条件——§3 的标签、§5 的框级动作都是它的落地形态。

## 2. 体系总览

```text
   项目原文 ──只读工具──▶ ┌──────────────────────┐
                         │ 工作台会话 agent      │──两个 patch 工具──▶ 草稿表 ──人审＋Apply──▶ 落盘
                         │ （术语／剧情两个任务） │   （只写草稿，零写盘）                │
                         └──────────────────────┘                                       │
                                                                                        ▼
   人打标定靶 ──▶ 框级动作（无工具、单轮）──写回原文/译文（进全局撤销栈）        术语表 JSON ＋ 项目内 每页摘要/全局梗概
                                                                                        │
                                                                                        ▼
                                                       翻译 agent（见 翻译agent化_设计方案）按页消费：术语命中 + 梗概 + 前页历史

   程序筛选器（OCR 后处理钩子）──自动挂标──▶ 标签体系 ◀──人工打标── 画布四件套入口
                                                    │
                                                    ▼
                                     泛用工作台的「问题清理」四个批量任务（人审 → 一次执行 → 整批可撤）
```

三方分工是这套体系的骨架：**程序做筛选（零 AI 消耗）→ AI 做判断（定点单轮）→ 人做取舍（确认／驳回）**。

**MCP 评估结论（留档，不再重议）**：MCP 是传输与复用协议、不是能力；能力上限等于工具面有多大。当前两条硬需求里，R1「状态感知」项目内直接读内存更容易；R2「适时介入」是决定性的——外部宿主是拉模式，无从知道用户何时刚调完一个气泡，而「适时」的前提恰恰是应用自己知道那个时机。故不采用。

### 用户工作流与痛点（这四件事决定了上面四条总纲）

| 步骤 | 用户做法 | 痛点／优化机会 |
|---|---|---|
| 概览 + 选默认样式 | 概览全文、选原文最常用的字体样式作项目默认样式 | 无（自述为省后续功夫）；vision 辅助样式推荐低优先、不做 |
| 检测 + OCR | 跑管线 | — |
| **逐页人工审校 OCR** | 误识别删除；手写字补全；错误分隔的文本合并 | 惯用检测模型**一行一框**（竖排则一列一框），**合并量极大** → 批量合并相邻框；另需给渲染留空间 → 批量框扩张 |
| **清理原图嵌字** | 因修复管线不可控，基本纯手动 | 希望**只对「简单背景」一键纯色覆盖**，复杂背景保留手动 |
| 术语表 + 剧情概览 | 导出原文 → 外部 agent 生成 → 一到两轮人工补充 | 项目内功能还不够好；翻译功能项太多太杂、难以判断用哪个 |
| 审查译文 + 调样式 | 逐页审查译文、精细调字号间距 | 最费力；**最折磨：原文／译文文本量差异大 → 气泡填不满／塞不下** |

**结构性观察**：把这条工作流过一遍，AI 真正非它不可的只有两处——手写字猜测、词不达意的重译；其余全部是「程序筛选 → 人看一眼 → 确认／驳回」。故本体系的定位比「AI 交互入口」更冷一档：**问题队列 + 人机确认台**。

---

## Part I · 标签体系与框级动作

### 3. 标签数据模型

标签 = `utils/textblock.py::TextBlock` 的 `tags` 字段（dict，键为类型 id），随项目 JSON 自动持久化（`to_dict` 是 `vars` 全量导出，加载侧无字段白名单，旧项目靠默认值兼容）。

**类型注册表**（`utils/block_tags.py` 的 `TagDef` / `TAG_DEFS`，现 **6 类**）：

| 阶段 | id | 语义 | AI 动作出口 | 性质 | 来源 |
|---|---|---|---|---|---|
| OCR | `ocr_low_conf` | 内容不准确或缺失 | 视觉 AI 看图校正 | 疑点 | 程序 + 人工 |
| OCR | `ocr_misread` | 程序按正则判定的四类误识别 | **无**（AI 看图也认不出「纯数字框是什么内容」） | 疑点 | **仅程序**（`program_only`） |
| OCR | `handwritten` | 手写体且 OCR 几乎不可识别 | 视觉 AI 结合上下文猜测 | 指示 | 人工 |
| 翻译 | `onomatopoeia` | 无实际含义，按发声直译 | 翻译指令：发声直译 | 指示 | 人工 |
| 翻译 | `trans_confusing` | 译文难理解或可能有错 | 结合上下文重译（可大幅改写） | 疑点 | 人工 |
| 翻译 | `trans_polish` | 含义大致正确但表述待优化 | 结合上下文重译（保意微调） | 疑点 | 人工 |

**条目形态**：`{"source": "program"|"manual"|"ai", ...}`，扩展字段随类型语义自由携带、不进注册表——`score`（OCR 分数，**只存不喂 AI**）、`subtypes`（误识别子类型）、`reviewed`（审阅表态）。

**两类标签的区分**（决定了审阅语义）：**疑点标签**是一次性任务，AI 处理并确认后消除；**指示标签**是块的持久属性，处理不消除，且会被**批量管线翻译消费**（`modules/translators/trans_agent.py` 组装块 prompt 时纳入其指示标签）——标签体系由此不止是疑点队列，还是**逐块翻译指令的载体**。

**多标签共存**：异类按阶段各自消费、互不干扰；同类并挂取更保守动作（迷惑 + 润色并存按迷惑处理）。

**程序专用标签（`program_only`）**：「误识别文本」不进标签工具栏、不进右键菜单、不进自定义菜单可选列表——判据是这条声明，**不是 `source`**（`ocr_low_conf` 的 source 同为 program，却仍可人工打标）。
⚠️ 它挡的是**菜单入口**，不是全量注册点：`ui/mainwindow.py::MainWindow` 仍会对 `TAG_REGISTRY` 全量注册快捷键盘位（当前不可达，因为默认快捷键表与 `ui/context_menu_config.py::DEFAULT_ORDER` 都不含该 id）。新增这类标签时要连这个注册点一起检查。

**审阅表态 `reviewed`**（`utils/block_tags.py::set_tags_reviewed`）：布尔、**可逆**、**块级**（一次驳回作用于该块全部 program 条目）；驳回后条目仍在，**不从队列消失**；挂标处对带 `reviewed` 的条目跳过，故**重跑 OCR 不会让驳回复活**。
状态模型只有这一个持久表态字段——**不存在「已确认通过」态，未驳回即待处理**。

**人工编辑即知情**：人工改写某块**原文**后，清除该块全部程序标签（整条删除、不区分是否带 `reviewed`），不做自动重判；**译文编辑不清**（两个程序标签判的都是原文的 OCR 结果）。落点：原文面板链路走 `utils/block_tags.py::prune_program_tags_after_source_edit`，写原文的框级动作命令直接调 `utils/block_tags.py::clear_program_tags`。标签不进撤销栈——撤销文本编辑不会恢复被清标签（可接受：重跑 OCR 会重算）。

### 4. 程序筛选器

**落点＝OCR 后处理钩子**：`ui/module_manager.py` 对 `modules/ocr/base.py::OCRBase` 注册一次即覆盖全部 OCR 模块（`register_postprocess_hooks` 是 classmethod，含零标签的 LLM OCR），零遍历代码。
**回调必须短路 `none_ocr`**：钩子遍历在 `run_ocr` 的 none_ocr 分支之外，而 none_ocr 的语义是「保留已有文本」，不短路会在「用户选择不 OCR」的项目上凭空挂出一批空文本／纯符号标签。

**四类判定**（`utils/block_tags.py::classify_misread_text`，存条目内 `subtypes`，一块可命中多类）：

- `empty` 空文本（单独短路，否则「无假名无汉字」会被空块平凡命中、把队列灌满）；
- `numeric` 纯数字；`symbolic` 纯符号；`no_japanese` 无假名无汉字（疑似 OCR 乱码，**会误伤漫画中真实存在的拉丁文本**——英文拟声词、外来语，这正是「人审查误杀」的必要性所在，也是四类里最需要人工过滤的一类）。

数字判据刻意**不用** `str.isdigit()`／`str.isalnum()`：两者都把 `①`（Unicode 类别 No）算作数字，会让「纯符号」漏判；`str.isdecimal()` 恰只认十进制数字。

**队列口径**（工作台「误识别清理」的数据源，`utils/block_tags.py::iter_misread_blocks`）：带 `ocr_misread` 标签的块，**按块去重**；`ocr_low_conf` **不单独入队**（其出口是框级 OCR 校正动作）。`待处理计数 = 队列内块数 − 已驳回块数`（`utils/block_tags.py::misread_queue_summary`）。

**置信度现状**：分数已透传并落 `score` 字段（本地两个 OCR 模型本来就在算，只是过去算完即弃）；`modules/ocr/ocr_llm_api.py` 的 LLM OCR 无真置信度，宁缺勿滥。
**分档阈值仍是未落地项**（见 §17）：现只有猜测值，且**没有设置入口**；不同模型的分数分布不可横向比较，阈值须按模型分设、按实机分数分布定。方向明确：**分数存下来做分档标注（信息无损），而不是调高硬丢弃阈值**（调高会直接丢行导致缺文本）。

### 5. 框级动作

**结构比翻译 agent 更轻一级**：**无工具、单轮、预组装上下文、固定出口**。边界不靠 prompt 自我限定，靠「根本没有工具」在结构上保证。
三方分工：人给靶心（打标选动作）→ 代码按固定规则组装信息包（该框原文／图裁剪、同页相邻块、页摘要与全局梗概、术语表命中项）→ AI 只做单轮判断。

| 动作 | 出口 | 上下文 |
|---|---|---|
| 疑难 OCR（视觉 AI 看图猜内容） | 草稿就地确认后写回**原文** | 块图裁剪 + 上下文 |
| 单块重译（按术语表与前后文重译） | 草稿就地确认后写回**译文** | 同页邻近块 + 术语命中 |

- **动作 → 标签**的白名单方向：`utils/block_actions.py` 的 `BlockActionDef.consumes`，`actions_for_block` 只对 `consumes` 命中的块出按钮——所以「某类标签没有动作出口」是天然成立的、零改动（这正是 `ocr_misread` 不需要任何声明的道理）。
- **注册表驱动**：`utils/block_actions.py` 是动作注册表 + vision 调用 + 选中跟随工具栏的载荷组装；执行器 `ui/block_action_runner.py`、确认卡 `ui/block_action_card.py`、写回撤销命令 `ui/textedit_commands.py::ApplyBlockTextCommand`（**进全局撤销栈**）。
- **动作在白名单内**：每加一项必须有唯一明确出口；「自由提问」入口永不添加（防打标入口膨胀成第二个聊天框）。
- **视觉 profile 依赖**：需要 `vision_support` 点亮的 profile；无可用时明确引导用户点亮。

### 6. 入口与展现

**四件套，各司其职**（打标是轻量元数据，**不进撤销栈**——误点再点即撤，进栈只制造噪音）：

| 入口 | 定位 | 落点 |
|---|---|---|
| 选中跟随浮动工具栏 | 选中态常驻主入口（承载标签面板展开） | `ui/tag_toolbar.py`；显示/重锚由 `ui/canvas.py::Canvas` 的选中变化信号驱动 |
| 右键命令 / 饼菜单 | 不改变焦点的最短打标路径 | `ui/context_menu_config.py::COMMAND_REGISTRY` |
| 画布徽标 | 静息态状态显示（`pcfg.show_tag_badge` 开关） | `ui/textitem.py::TextBlkItem` 的 `refresh_tag_badge`（全仓唯一组装标签列表处） |
| 跨页跳转 | 「上一个／下一个带标签块」，与徽标配合成「扫过去 → 跳过去 → 处理掉」的闭环 | `ui/mainwindow.py::MainWindow` 的 `jump_to_tagged_block` |

**徽标行为不受 `reviewed` 影响**：驳回只在工作台界面呈现（列／取消勾选），画布照常显示该块的标签徽标。

### 7. Part I 落点与测试护网

| 关注点 | 落点 |
|---|---|
| 类型注册与读写 | `utils/block_tags.py`（`TagDef`／`TAG_DEFS`／`set_tag`／`remove_tag`／`toggle_on_blocks`） |
| 程序挂标 | `utils/block_tags.py::apply_ocr_confidence_tag`、`utils/block_tags.py::apply_misread_tag` |
| 审阅与清理 | `utils/block_tags.py::set_tags_reviewed`／`clear_program_tags`／`prune_program_tags_after_source_edit` |
| 队列查询 | `utils/block_tags.py::iter_misread_blocks`／`misread_queue_summary`／`misread_subtype_counts` |
| 框级动作 | `utils/block_actions.py`＋`ui/block_action_runner.py`＋`ui/block_action_card.py` |
| 标签工具栏 | `ui/tag_toolbar.py`、`ui/textitem.py`、`ui/context_menu_config.py` |

**测试**：`tests/test_block_tags.py`（挂标三分支／`reviewed` 一族／四类判定）、`tests/test_tag_toolbar.py`（面板行数与程序专用标签不出现在入口）、`tests/test_tag_prune_on_source_edit.py`（D29 两条链路的接线）、`tests/test_block_actions.py`（动作注册与确认卡）。

⚠️ **新增一个标签类型必须同步四处**：`utils/block_tags.py` 的 `TAG_DEFS`、`ui/context_menu_config.py` 的 `DEFAULT_ORDER`、`translate/zh_CN.ts`、`tests/test_tag_toolbar.py`（`_merge_default_order` 会把新条目自动补进老用户的右键菜单，所以「加进 DEFAULT_ORDER」是必要条件）。

---

## Part II · 工作台

### 8. 容器与六个任务

容器＝`ui/glossary_agent_panel.py::GlossaryAgentPanel`，**三段式**：① 一级导航 `ui/glossary_agent_panel.py::WorkbenchTaskNav`（六个互斥任务钮）→ ② 候选列表（批量任务为列表 + 列表下方 **100% 原比例**预览；术语／剧情是各自的草稿页）→ ③ 执行行（无勾选即禁用，批量执行前一律走告知弹窗）。
「批处理与审批是同一任务的两端，不分家」——D20 定的就是这个。

**默认顺序＝用户工作流顺序**：① 误识别清理 → ② 合并相邻框 → ③ 框扩张 → ④ 简单背景修复 → ⑤ 术语提取 → ⑥ 剧情摘要。
前四项属「问题清理」任务族、导航钮上缀「还有 N 个未处理」；术语／剧情是探索性任务、无「未处理」概念，不缀数。**运行时不门禁**：跳步时弹窗提示「还有 N 个未处理的该步骤标记」（可勾选不再提示，写 `utils/config.py::ProgramConfig` 的 `workbench_warn_skip_order`）。

**队列刷新＝惰性查询**：面板持一个「标脏任务集」，切到某任务时才跑它的只读 `plan`；批量写回与回滚后把所有任务重新标脏。计数口径见 `ui/glossary_agent_panel.py::earlier_pending`（只有前四项参与）。

**入口＝左栏单入口**（`ui/mainwindowbars.py::LeftBar` 的 workbenchChecker），跳转走 `ui/mainwindow.py::MainWindow` 的 `on_workbench_jump`（复用具脏页惰性重渲的 pageList 链路）；面板宽度 `WORKBENCH_WIDTH = 460`（依据见 §16）。

**空态限制**：判据 `ui/glossary_agent_panel.py::has_project`（`proj.directory` 非空）；面板根是 QStackedWidget，page 0 空态提示页（objectName `WorkbenchEmptyHint`）、page 1 三页内容 ⇒ 空态时天然禁用全部交互。刷新点在 `ui/mainwindow.py::MainWindow` 的 `openDir`／`openJsonProj` 成功路径末尾。

### 9. 术语表供给链

术语表是**项目级一致性资产**，三方共治：提取器供给、人工编辑裁决、agent 翻译消费。这是读写分离的自然延伸——**agent 不直接写术语表文件**。

```text
词频预填充（零 AI）／agent 会话整合候选  ──▶ 草稿表（带来源标记，只落内存草稿）
                                                │ 人工编辑／裁决冲突行
                                                ▼
                                    「Apply draft…」唯一落盘出口
                                       ├── 术语 json（utils/config.py::ModuleConfig 的 llm_glossary_path）
                                       └── 剧情：项目内存（随项目保存）
                                                │
                        ┌───────────────────────┼───────────────────────┐
                 编排器自动注入保底        按需全表查询（工具）      出口校验·术语残留检测
                 （翻译 agent，见其文档）  （翻译 agent）           （翻译 agent 校验器）
```

- **草稿保护（不要破坏的契约）**：`modules/context_agent/draft.py` 的 patch 规则——用户手编行（origin=user）与基底行（origin=existing）**受保护**，AI 提议冲突时返回 conflict 行而不是应用，AI 只能改自己创建的行。`origin` 三态经 `ui/glossary_agent_panel.py` 的展示映射为 base／AI／you 显示并着色。**不要把「AI 直接覆盖用户行」当优化点——这是设计。**
- **两个使用时机**：翻译前（通读 src 产候选，人工校对后入库）；翻译后（基于实际 src/trans 整合，准确度更高）。
- **沉淀回流**：整本翻译完成后用频次统计对**本次新产生的译文**跑增量统计（出现 ≥2 次且译法一致）产候选，带来源标记进草稿表、人工确认才入库。设计取向是**拉模式**（打开工作台时载入数据为草稿基底），不做后台常驻统计线程。
- **一键准备**：`ui/glossary_agent_panel.py::_on_prepare` = 词频 prefill（零 AI）+ 组合 AI 指令（补术语 + 补缺失摘要 + 更新梗概）。属耗时／耗费操作，执行前经确认弹窗说明步骤与 API 花销；「不再询问」写 `workbench_confirm_costly`（默认 True），恢复入口在设置面板「应用 → Workbench」。

### 10. 剧情摘要数据流

| 数据 | 存储位置 | 写入时机 |
|---|---|---|
| 术语表 | json 文件，路径取 `utils/config.py::ModuleConfig` 的 `llm_glossary_path` | 工作台 Apply（`ui/glossary_agent_panel.py::GlossaryAgentPanel`）；翻译设置区 Browse 只改路径 |
| 每页剧情摘要 | `proj._image_info[页名]["llm_visual_summary"]`（键常量 `modules/context_agent/story.py::PAGE_SUMMARY_KEY`） | 工作台 Apply → 随项目保存持久化 |
| 全局梗概 | `proj` 的 `llm_compact_memory`（`modules/context_agent/story.py::SYNOPSIS_KEY`） | 同上。**注意**：曾因 setattr 不落 `to_dict` 导致「保存即丢」，已修 |
| 翻译时用不用剧情 | 布尔开关 `utils/config.py::ModuleConfig` 的 `llm_story_context` | 翻译设置区 checkbox |
| 草稿（内存态） | `modules/context_agent/draft.py::GlossaryDraft`／`modules/context_agent/draft.py::StoryDraft`，worker 线程内为权威副本 | 打开面板时从上述存储载入基底 |

**已知缺口（待办）**：全书视野。现有实现是多轮只读工具（读页工具单次上限 5 页、单结果 24000 字，轮数软限 12）⇒ 读完全书 94 页需 ≥19 次调用，**结构性做不到**。修法是启动前预取全书原文注入会话 + 页名格式归一化（AI 输出时按映射还原）；规模可行（全书原文实测仅 8756 字）。见 §17。

### 11. 线程模型（契约，改动前必读）

`ui/glossary_agent_panel.py::GlossaryAgentWorker` 是 QObject、`moveToThread` 到自有 QThread。**它通过信号／槽（跨线程自动 QueuedConnection）执行，不能直接方法调用**——直接调用会在调用者（主）线程同步执行，长任务冻结 UI（历史 bug，已修）。

- 面板 → worker 的入口全部是 `*_requested` 系列信号（`instruction_requested`／`prefill_requested`／`glossary_edit_requested`／`glossary_remove_requested`／`synopsis_edit_requested`／`summary_edit_requested`／`summary_remove_requested`／`glossary_apply_requested`／`story_apply_requested`），在 `ui/glossary_agent_panel.py::GlossaryAgentPanel` 的接线处连接。
- **唯一例外**：`request_stop` 直调（只写取消标志，越早生效越好）。
- worker → UI 是单向信号刷新（`log_line`／`glossary_synced`／`story_synced`／`round_finished`／`round_failed`／`applied`／`busy_changed`）；UI 永不直接读 worker 状态（busy 判据是 worker 的布尔字段，GIL 下安全）。
- **批量 worker 的回调寻址原则**：批量／跨页编排的完成回调必须按 `(pagename, block_idx)` 寻址，**不得持有块对象引用**——跨页处理期间用户可能切页，块对象会重建。
- **日志落点**：worker 的 `log_line` 与执行回执落到面板底部只读日志条（`WorkbenchLogView`，固定高、文档最多 200 块、超长自动丢最旧行）。
- worker 生命周期：懒创建（面板 showEvent 且已打开项目）；项目切换时先 shutdown 再按需重建。
- **翻译 agent 路径的线程语义不同**：它跑在管线线程（`ui/io_thread.py`），与本面板无关，不要耦合。
- **流式能力当前无消费方**：`modules/translators/trans_agent.py::AgentTranslator` 的 `_agent_chat` 支持 `on_delta` 流式回调、`modules/context_agent/session.py::run_agent_session` 也有 `stream_cb` 参数，但**砍掉 Chat（D19）之后没有任何调用方传入**——两条链路都是非流式。将来若要恢复逐字显示，接这里即可（`modules/translators/trans_agent.py::_consume_agent_stream` 已实现增量聚合与端点不兼容时的自动降级）。

### 12. UI 结构与 i18n

- **面板底色**：内容页与各任务页（objectName `WorkbenchSurface`）涂 `@emptyContentBackgroundColor`（比画布页深一档）以保持与主页面的边界，页内 QLabel 显式透明透出底色；规则在 `config/stylesheet.css`「泛用工作台」段。
- **表格样式**：`config/stylesheet.css`「Item views: tables」段（QTableWidget／QHeaderView::section／QTableCornerButton 全套主题化）**是 Windows 10/11 原生 style 绘制分歧的修复**（Win10 windowsvista 与 Win11 windows11 画法不同导致表格融入背景）——不要移除。
- **Origin 列**只读、文字按来源着色（base 灰／AI 强调色／you 绿，走 `ui/misc.py` 的主题变量解析，同步时计算——主题切换后需下次同步才刷新，可接受）。
- 所有颜色走主题占位符（`ui/misc.py::build_stylesheet_from_dict` 替换），禁止硬编码色值。
- **i18n**：面板实例方法内的 `self.tr()` 正常走扫描；模块级翻译表（`ui/glossary_agent_panel.py` 的 `_STOP_REASONS`／`_ORIGIN_LABELS` 等）在字面量定义处用 `QCoreApplication.translate` 显式标注上下文。同步命令 `python scripts/ts_auto_fill.py --fill-missing --apply`，译文手工补（注意 XML 转义差异：脚本写 `"`、手工用 `&quot;`）。

### 13. 泛用工作台：四个批量任务

四个引擎是**同一形状**：只读 `plan` ＋ 整批 `apply`（+ 需要时出审批材料）。界面只做三件事——调 `plan` 填列表／算数字 → 收勾选 → 把标识传回 `apply`。
**界面不写几何、不碰 `proj.pages`、不绕开 `ui/batch_ops.py`**；引擎不塞 UI 逻辑（这样才好被裸脚本调用做复算）。

| 任务 | 引擎 | 界面数据侧 | 出口 |
|---|---|---|---|
| 误识别清理 | `ui/batch_delete.py::BatchDeleteMisread` | `ui/workbench_tasks.py::MisreadTask` | 驳回标记／一键批量删除已确认 |
| 合并相邻框 | `ui/batch_merge.py::BatchMerge` | `ui/workbench_tasks.py::MergeTask` | 合并建议（组级、列表级审批） |
| 框扩张 | `ui/batch_expand.py::BatchExpand` | `ui/workbench_tasks.py::ExpandTask` | 只扩渲染区域 |
| 简单背景修复 | `ui/batch_inpaint.py::BatchSimpleInpaint` | `ui/workbench_tasks.py::SimpleInpaintTask` | 简单块纯色覆盖 |

数据侧收在 `ui/workbench_tasks.py::BatchTask`（Qt-free），界面是共用视图 `ui/workbench_batch_view.py::BatchTaskView`（勾选列 + 列表下方 100% 原比例预览 + 参数控件 + 执行行）。

- **简单背景修复**：只覆盖「简单背景」的框，**复杂块完全不动**（不加载修复模型）；判据**一律算原图**（防手动修过的复杂块在 `inpainted` 图上被误判为简单而遭覆盖）；只写 `inpainted/` 层、跑完重载当前页。
- **合并相邻框**：几何聚类 → 排除带**未驳回**误识别标签的框（已驳回的视同普通框参与，否则「审批现场转人工」的出口会被卡死）→ 组级方向判定 → 列表级审批。
  **方向判定三级**：主判＝组内 `src_is_vertical` 多数；交叉验证＝块顺序一致性（不一致只在该审批行标「顺序存疑」，**不改判定、不自动剔除**）；兜底＝组内无有效值时用块顺序，整页判不出才用全文投票。审批界面提供**「反转该组方向」**。
  写回细则：`text` **逐行展开**为默认（可选按块分段）、样式来源＝**索引最小的成员**、`tags` 取成员**并集**（不静默洗白低置信）、误聚组**默认不勾选 + 警示标**（不静默剔除）。
  审批截图＝成员**并集虚拟框** + 外扩（短边 50%、遇邻框即停），**必须 100% 原始比例**。
  ⚠️ **缺口**：原决策里「审批现场若确认某组有问题，就地给成员块打误识别标签、送进清理队列消化」这一半**未实现**（把块送进队列的唯一入口是 OCR 钩子）——见 §17。
- **框扩张**：只扩**渲染区域**（`_bounding_rect` 与 `xyxy` 同步变动），掩码与修复数据原样保留；上限「碰到邻框即停」；**同页串行扩张**（邻框取已扩后的矩形，否则两个相邻框会互相穿过）；旋转框与退化矩形不参与。**扩张量由调用方显式给出**（引擎**不设默认值**，裸脚本复算才不受设置影响），界面输入框初值取设置项 `workbench_expand_px`（默认 10 px，依据见 §16）。
- **误识别清理**：队列＝带 `ocr_misread` 标签的块；缺省只删**未驳回**的块（显式勾选不受驳回限制——驳回＝「标签判错、框是正常文本」，勾选＝「框本身该删」，两轴正交）；**只删文本框层**，不碰遮罩与修复图（弹窗须照实说：已留在 `inpainted/` 的修复痕迹不会因此还原）；跨页批量**不能复用** `ui/scenetext_manager.py` 的单页删除命令（它按 `idx` 索引当前页 widget 与图像缓冲、且带「顺手修复该区域」的支路）。
  **审批现场可用的动作只有「驳回／取消驳回」**（`ui/workbench_tasks.py::MisreadTask` 的 `run_action`）。

**`apply()` 的报告字段与错误码**（界面据此给反馈，别自己猜含义）：

- 报告：`started`（假＝没执行、数据未动）／`version`（撤回要用它的 `seq`）／`writeback`（其 `verified` 为假即 §14 的判据没过，属缺陷）／各自的计数（`groups`／`blocks`／`deleted`／`pages`／`stale`）；
- `error` 取值：`cancelled`（用户取消）／`stale`（传回的标识已对不上，应重新 `plan`）／`no-groups`（没有可合并的组）／`empty-queue`（队列空）／`nothing-to-expand`（都贴住邻框或页边）／`commit before snapshot failed`、`batch version not written`（落盘失败，须提示用户且**不要重试写数据**）。

### 14. 批量写回契约与撤销

**三条契约**（`ui/batch_ops.py::BatchOperation`）：

1. **同序**：新块的 `text`／`rich_text`／`lines` 必须同序一致（「反转该组方向」同时改三者）；
2. **`rich_text` 必须重建**（渲染实际消费它，留旧值会导致渲染与数据不一致）；
3. **形态澄清**：`utils/textblock.py::TextBlock` 的 `get_text()` 是**空串拼接**（仅字母边界补空格、不插换行），故「逐行展开／按块分段」的差异体现在**列表结构与导出、编辑框**上，不在渲染。

**五步顺序（不可交错）**：① 前置对齐（主窗口的数据一致性修复；内部按判据决定是否写回）→ ② 只改 `proj.pages` → ③ 页代数（动图像时加图像代数）→ ④ 当前页重建视觉层（数据 → UI 方向）→ ⑤ **此后禁止**再调 `ui/scenetext_manager.py` 的 `updateTextBlkList`（UI → 数据方向：用旧面板值覆盖新数据、并把逐行列表折叠成单元素）。
**可验证判据**：操作完成后 `utils/block_actions.py::page_data_needs_sync` 必须为假。
**为何第 ④ 步必须紧接**：该判据只比长度与对象身份、不比内容——数据层换成新块对象后视觉层仍是旧对象，此时任何后续框级动作都会触发前置对齐、从而**用旧视觉层整页覆盖刚写的数据**。

**不标脏**：批量修复照搬管线路径——**不**调 `utils/proj_imgtrans.py::mark_page_needs_rerender`，也不更新 `result/`（前期流程在产出译文前不主动处理脏页，后续管线迟早推进覆盖）。

**撤销**：单框小操作留内存（沿用 `QUndoStack`、步数上限）；**批量操作一律落盘、整体一条撤回**（`utils/batch_versions.py::BatchVersionStore`，**仓库唯一的批量备份口**）：

- 版本轮转制（不分任务类型，避免交叉编辑冲突）：执行前写入一版（项目数据 + 受影响矩形的**像素前图**）到**项目目录内**子目录，撤销＝取最新版本覆盖并消耗掉它；
- 版本数取 `utils/config.py::ProgramConfig` 的 `batch_backup_versions`（默认 1、上限 5），跨会话随项目保留；
- 查找替换（`ui/global_search_widget.py`）与工作台四个任务共用同一套——**不要再新增第二套快照／回滚机制**；
- 像素路径必须自己拼精确路径（`utils/batch_versions.py::_exact_inpainted_path`），`ProjImgTrans.get_inpainted_path` 有按页序的模糊兜底会删错文件；共用版本池 ⇒ 一律用版本号校验，宁可拒绝也不撤错。

**所有批量行为须弹窗告知确认后的行为**（`ui/workbench_batch_view.py::BatchTaskView` 统一弹，正文由各任务的 `confirm_html` 提供）。

### 15. 决策一览（D1–D41）

编号体系保留：代码注释与 commit 信息按这套编号引用。

**问题载体与标签**（D1／D2／D9／D15／D17／D28／D29／D38／D39）

| 编号 | 结论 |
|---|---|
| D1 | 问题载体**在标签体系上扩建**（现 6 个类型）。 |
| D2 | 误识别类**纯程序化**：OCR 跑完正则自动挂标；**不提供人工打标入口**。 |
| D9 | 问题框按四类实现（空文本／纯数字／纯符号／无假名汉字），人工只审查误杀。 |
| D15 | 审阅状态的展示**仅约束工作台界面**；画布徽标行为完全不变；工作台内不逐个堆叠程序标签（现为「子类型合并成一格 + 审阅状态一列」）。 |
| D17 | 标签二分：临时标签（OCR／译文问题）程序与用户共同维护消费；持久标签（手写、拟声词）完全由用户标注。 |
| D28 | 审阅表态＝`reviewed`（布尔、可逆、块级）；挂标处跳过已驳回条目 ⇒ 重跑 OCR 后驳回记忆存活；**不存在「已确认通过」态**。 |
| D29 | **人工编辑即知情**：原文被改写后清除该块全部程序标签（整条删除），不做自动重判；译文编辑不清。 |
| D38 | 误识别＝**1 个标签 + 条目内子类型字段**（一块可命中多类），而非 4 个 id；配套加「程序专用」声明。 |
| D39 | 程序筛选器**走 OCR postprocess hook**（对 `OCRBase` 注册一次覆盖全部；**必须短路 `none_ocr`**）。 |

**四个批量任务**（D3／D5／D7／D8／D16／D30／D31／D32／D33／D34／D36／D41）

| 编号 | 结论 |
|---|---|
| D3／D34 | 批量简单背景修复：只覆盖简单块、复杂块完全不动（默认关闭的入口，不改管线既有行为）；判据算原图、只写 `inpainted/` 层、跑完重载当前页。 |
| D7／D16 | **手动排序为准**（合并重建不得自动重排）；执行顺序上**合并排在误识别清理之后**。 |
| D8／D32 | 组级方向判定三级（主判 `src_is_vertical` 多数 / 顺序交叉验证只标注 / 全文投票兜底）；审批界面提供「反转该组方向」。**注**：本决策的另一半——现场打误识别标签转人工——未实现，见 §17。 |
| D30 | 合并**排除带「未驳回」误识别标签的框**（判据 `reviewed != True`）；`ocr_low_conf` 不排除。 |
| D31 | 审批截图＝并集虚拟框 + 外扩（短边 50%、遇邻框即停），**必须 100% 原始比例**。 |
| D33 | 写回细则：`text` 逐行展开／按块分段可选；样式来源＝索引最小的成员；`tags` 取并集；误聚阈值默认「包围盒任一边超页面对应边 85%」（设置项可调），超限组默认不勾选 + 警示标。 |
| D5／D36 | 框扩张只扩渲染区域（两字段同步），掩码与修复数据保留；除批处理外支持单块 Alt + 拖拽＝以框中心为基准缩放（即时修饰键，语义同 PS）。 |
| D41 | **不修 `utils/textblock.py::sort_regions`**（它被两个检测器调用，改动即变更所有检测器的初始顺序），只自建组级判定；其两处缺陷就地登记。 |

**批量骨架与撤销**（D4／D10／D18／D21／D27／D35／D40）

| 编号 | 结论 |
|---|---|
| D4／D21 | 撤销按操作类型分：单框留内存、**批量一律落盘**、整体一条撤回。 |
| D18 | 沿用现有步数上限，**不新增**「撤销占用上限」设置项。 |
| D10 | 批量修复的撤销只要可用、**优先低内存**。 |
| D35 | 版本轮转制（默认 1、上限 5、项目目录内、含受影响矩形像素前图）；`BatchVersionStore` 是仓库唯一批量备份口。 |
| D27 | 误框清理提供一键批量删除已确认；**所有批量行为弹窗告知**；「勾选待删除」是会话内界面态、不落盘，与驳回两轴正交。 |
| D40 | 批量写回三条契约 + 五步顺序（详见 §14）。 |

**容器与界面**（D12／D19／D20／D22／D23／D24／D25／D26／D37）

| 编号 | 结论 |
|---|---|
| D12／D24 | 容器在左侧工作台重做、**适度加宽**：以能否 100% 全幅显示审批图为唯一判据，宽度定稿 460。 |
| D19 | **砍掉 Chat tab**；人工提示词入口降级为「消费临时标签时在窗口内提供纠正入口」；worker 日志落面板底部日志条。 |
| D20／D23 | 三段式 + 审批预览**在列表下方展开**（不并排、不弹窗），100% 原比例。 |
| D22／D25 | 一级导航**复用主窗口左侧 48px 图标栏**，**只设一个「工作台」入口**。 |
| D26 | 队列跳转只对条目所在的页与块负责，面板内不做翻页控件。 |
| D37 | **不强制步骤**：跳步弹提示、可勾选不再提示；顺序是推荐而非门禁。 |

**延后**：D6／§6.5 翻译策略「自动」档（先规则化、AI 只兜底真模糊项；启用后必须界面备注其可能执行的行为）、§6.6 自动排版（框内锚定 + 字号上下限内自适应 + 溢出／欠满检测；**注意「极端不匹配」是译文长短问题，排版解决不了**）、D14 概览阶段不做 vision 推荐。

---

## Part III · 调参与待办

### 16. 长期需要实测调整的参数

这些参数都是「测出来的」，换样本、换检测器、换框的粒度都会漂。**改判据或改默认值前后各跑一次复算台**（常驻只读工具，末尾自证样本 mtime 未变）：

```bash
./ballontrans_pylibs_win/python.exe scripts/workbench_recalc.py <merge|c1|expand|queue|review|hook|list> --project <项目目录> [--sweep]
```

| 参数 | 现值／落点 | 依据与调参方向 |
|---|---|---|
| 误识别四类判定 | `utils/block_tags.py::classify_misread_text` | 「无假名汉字」必然误伤真实拉丁文本（拟声词、外来语）——这正是人工审查误杀的必要性所在。数字判据用 `isdecimal`（`isdigit` 会把 `①` 算作数字）。 |
| 简单背景判据命中率 | `modules/inpaint/base.py::classify_simple` | 判据要求裁剪区内有**一条闭合气泡轮廓盖住全部遮罩像素**，故命中率**与框的粒度绑定**：逐行框仅 **8.3%**，**合并后升到 51.3%**（401 简单／131 复杂／250 判不出）⇒ **「先合并、后修简单背景」才是正确用法，判据不用改**。残留 32% 判不出是预期的。将来再降的方向是「块局部遮罩」（实测 39%）而非换判据；换手动框选那套 `utils/textblock_mask.py` 的判据实测 90%，但它不找气泡、会把压在复杂背景上的字一起涂掉。 |
| 合并分组判据三条 | `ui/batch_merge.py::MergeConfig`（判据只有 `ui/batch_merge.py::_pair_axis` 一处） | 投影重叠 ≥ 0.6、另一轴尺寸比 ≥ 0.6、同轴间隙 ≤ 一个自身交叉尺寸（没有第三条整页同带框会一路连通）。样本 94 页／1565 框：旧口径 435 组／排除后 393 组／**默认 380 组**；36 组合阈值扫描落在 346~394（**无跳变**）。调参顺序：先 `max_gap_ratio`，再 `size_ratio_min`（**唯一有实质影响的门限**），最后 `overlap_min`（几乎无影响）。 |
| 误聚阈值 | 设置项 `workbench_merge_oversize_ratio`，默认 **0.85** | 「组包围盒任一边超页面对应边 85%」即误聚（默认不勾选 + 警示标）。实测仅命中 1 组（跨栏气泡），**阈值不敏感**（≥50% 4 组、≥90% 0 组），无需精调。 |
| 审批截图外扩量 | `ui/workbench_tasks.py` 的预览外扩比例＝短边 **0.5** | 组到邻框间隙中位 300+ px、p10 约 80~93 px、最小 0~1 px（相邻气泡会贴住）⇒ 取短边 50%（约 40 px）、硬上限「碰到邻框即停」。外扩后截图宽 min／中位／p90／max＝64／164／256／2063 px，**超 460 px 的只有 14/380（3.7%）**且都是跨栏大组（本就默认不勾选）。 |
| 扩张量 | 设置项 `workbench_expand_px`，默认 **10 px**（只作界面初值） | 引擎不设默认值。依据：合并后框宽中位 71 px、高 120 px，**可扩空间四边最小值中位 89 px**；`px 10` 时 **88% 的框四边都能完整扩张**（宽 +28%／高 +17%）。候选：px 5 → 91.3%／px 16 → 83.6%／ratio 0.15 → 87.7%（**ratio 按短边取比例，对竖排窄框退化**，故默认取 px）。改成 0 会让列表为空、执行按钮禁用。 |
| 备份版本数 | 设置项 `batch_backup_versions`，默认 1、上限 5 | 即「可连续撤销的批量操作步数」。像素版只存前图，约 43 MB/版；`inpainted/` 全量 140.6 MB（1.53 MB/页）。 |
| 队列规模基线 | 样本 94 页 → **128 块／51 页**（每页 1.36 块） | 子类型命中合计 163（无假名汉字 92／空文本 36／纯数字 20／纯符号 15，约 35 块跨类）。**合并前后队列不变**。换样本后先跑 `queue` 复核量级，再定列表信息密度。 |
| 工作台宽度 | `ui/mainwindow.py::MainWindow` 的 `WORKBENCH_WIDTH = 460` | 判据＝能否 100% 全幅显示审批图（见上）。改宽度只改这个常量，面板内部自适应、零布局改动。 |
| 置信度分档阈值 | **未落地**（无设置入口） | 见 §17。方向＝存分数做分档，不调高硬丢弃阈值。 |
| 审批截图比例 | **硬约束、不是参数** | 必须 100% 原始比例；交互形态须在此约束下设计（预览只滚动不缩放）。 |
| 单块 Alt 缩放 | `ui/texteditshapecontrol.py::TextBlkShapeControl` | 手动版**不套**「碰到邻框即停」（那是批量扩张的保护）。修改注意：reshape 走几何控制器，不能直接改 `xyxy`。 |

**样本与复算前提**：真机复算用 `D:\汉化\施工区\` 的 94 页样本（**只读**；可写副本在 `施工区副本\`，测写只在副本做）。该样本全部页的 `det_model` 是 `ppocrv6_onnx`；合并前的项目快照留在副本的 `.bt_batch_backup` 目录里（v00001，1565 框）。**跨版本比对先看每块的 `det_model`。**

### 17. 未落地与待办

| # | 事项 | 状态 |
|---|---|---|
| 1 | **全书视野**：术语／剧情任务改为「启动前预取全书原文注入会话」+ 页名格式归一化（AI 输出时按映射还原） | **待办（用户 2026-09-18 拍板）**。现状是多轮只读工具，读完全书需 ≥19 次调用（§10）。规模可行：全书原文实测 8756 字，`TextBlock.text` 是**逐行列表**不是字符串。落点：`modules/context_agent/prompts.py::build_system_prompt` 的注入位与工具面。 |
| 2 | **合并审批现场「打误识别标签转人工」** | **待拍板**。原决策（D32／D33）留了这条出口——现场发现问题块就把它送进清理队列消化；现状**只有「反转方向」一个动作**，把块送进队列的唯一入口是 OCR 钩子，而 D2 又挡住人工打标途径 ⇒ 没有第二条通道。要么补一个审批内动作，要么明确判定为不需要（改由画布手动处理）。 |
| 3 | 置信度分档阈值与设置入口 | 未做（§4）。需按模型实机跑分数分布后定。 |
| 4 | D5 单块 Alt 拖拽的手感 | 只能人验（灵敏度、有没有「想撤销却发现撤不动」的场景）。 |
| 5 | 设置页「工作台（临时）」两个数值项是否即时生效 | 只能实机验。 |
| 6 | 设置页「工作台（临时）」页的排版归并 | **用户拍板「暂不动、保留临时页」**；排版方案定了再并入既有页面。 |
| 7 | §15 末尾的延后项（翻译「自动」档、自动排版） | 明确延后，涉及新增功能。 |
| 8 | 长尾未做项 | 多选批量打标与行级标签、交付门禁（导出前检查残留疑点标签）、指示标签被管线消费时的可视化、处理中锁定工具栏失焦隐藏、逐行 OCR 校正不做可疑行预选。 |
| 9 | 术语／剧情侧已知薄弱点 | 表格全量重建（术语上千行时可能卡顿，可做增量 diff）；流式降级只覆盖报错含 stream 的端点；`llm_glossary_path` 为空时 Apply 走文件对话框，用户取消会连剧情草稿一起不应用（两个 apply 是原子批）；表格与工作面边界观感在 Win10/Win11 双检。 |

### 18. 易错点与纪律

1. **`updateTextBlkList` 是 UI→数据方向**，会用面板旧值覆盖数据、并把 `blk.text` 折叠成单元素 ⇒ 批量写回后**禁止再调它**（§14 第 ⑤ 步）。
2. **数据一致性检查只比长度与对象身份、不比内容** ⇒ 换新块对象后若不同步重建视觉层，后续任何框级动作都会用旧视觉层整页覆盖新数据。
3. **`set_tag` 是覆盖写**、`remove_tag` 是整条删除 ⇒ 给标签加持久状态必须**先保证条目存在**。
4. **OCR 钩子的遍历在 `none_ocr` 分支之外** ⇒ 回调不短路它，会在「不跑 OCR」的项目上凭空挂标。
5. **worker 只能经信号／槽调用**（跨线程 QueuedConnection），直调会冻结 UI；唯一例外是 `request_stop`。
6. **`utils/io_utils.py::imread` 走 PIL 返回 RGB（不是 BGR）**，文件不存在返回 `None` ⇒ 批量截图须自检并对 `None` 兜底。
7. **`TextBlock.vertical` ≠ `src_is_vertical`**：前者是渲染方向（用户可改）、后者是检测产物（原文方向）⇒ 判「竖排」一律取后者。
8. **不要在批量路径里原地改块对象**：要么换新对象（合并）、要么写副本（扩张）——原对象要留给版本快照，否则撤销失效。
9. **不要给引擎塞 UI 逻辑**、**不要在引擎里给扩张量编默认值**、**不要给合并判据加「自动剔除组」**（存疑只标注）。
10. **不要为某个任务另写审批面板**（共用三段式）；**不要绕开 `ui/batch_ops.py`** 自己写回。
11. **左栏互斥是不对称的**：两个展示函数内部各自隐藏工作台，而工作台自己不隐藏任何面板；改互斥时按「2 个展示函数 + 3 个槽」的位置分布去改。
12. **新增标签类型须同步四处**（见 §7）；**程序专用标签还要检查全量快捷键注册点**（见 §3）。
13. **i18n 两条硬规则**：`scripts/i18n_common.py` 的提取器**跳过含 `{` 的字符串**、**不剥注释** ⇒ 占位符一律用 `%1`／`%2` + `.replace`，字面量后面必须紧跟 `)`；注释里别写翻译调用的字面形态。
14. **性能点**：右键菜单「跳转上／下一个带标签块」的可用性判定是**全量遍历所有页所有块**的扫描，程序批量挂标后块数级增长，需留意。
15. **改动工作台批量任务时，验收判据是 `utils/block_actions.py::page_data_needs_sync` 为假**（§14）。

### 19. 关联文档

- [翻译agent化_设计方案](翻译agent化_设计方案.md)：翻译 agent 的架构基线（护栏体系／回退链／工具面／编排注入），本体系的下游消费者。
- [AI辅助标签体系使用说明](../基础速查/AI辅助标签体系使用说明.md)：标签清单的功能地图与逐项测试清单。
- [设置面板概述](设置面板概述.md)：管线选项与「工作台（临时）」区的接线现状。
- [区域再检测_设计与实现](区域再检测_设计与实现.md)：同期的另一条「画布内局部 AI 处理」链路（不是工作台任务）。
