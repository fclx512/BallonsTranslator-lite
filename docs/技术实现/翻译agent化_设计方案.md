# 翻译 agent 化设计方案

> 状态：设计基线（2026-08-23 定稿；agent 化已落地，余项见文内标注）——本文随实现维护，非归档文档。
> 前置调研（2026-08-23 的管线现状快照与死路径清单）已随 agent 化收官删除，其结论已消化进本方案。
> 本文只写定稿结论与理由；参数、校验细则等实现细节以 `modules/translators/trans_agent.py` 与 `modules/translators/agent/` 为准，不在此重复。

---

## 1. 目标与非目标

**目标**:把 LLM 翻译从"正则术语表 + 单次补全"重构为类 agent 系统——模型带只读工具、多轮循环、自主决定是否探索上下文,管理全流程翻译(整页/整本/单框)。工程重心不在"教模型翻译",而在**限制与护栏**:防止不按规范运作。

**非目标**:

- 不做跨页连续会话(任务无状态,见 §3);
- 不做对话式助手 UI(`utils/ai_tools.py` 的 ai_chat 血缘只复用积木,不接线聊天入口);
- 不开放样式写入权(翻译与嵌字职责分离);
- 不做双协议自适应(单一原生 function calling,不兼容端点走回退链)。

---

## 2. 已拍板决策

| # | 决策点 | 结论 |
| --- | --- | --- |
| 1 | 工具协议 | **原生 function calling**(OpenAI tools API)。端点不支持时走回退链(§9) |
| 2 | 跨页历史 | **自动注入 + 按需探索**:编排器按预算注入邻近已完成页,agent 可用工具深挖更早页 |
| 3 | 旧实现处置 | **合一,删 beta**:AgentTranslator 成为唯一 LLM 翻译路径,beta 翻译器 `ContextBatchTranslator` 及 Run 对话框 beta 入口删除,`modules/translators/trans_llm_api.py::LLM_API_Translator` 的直译路径降级为回退 |
| 4 | 单框翻译 | **可选策略**配置项:`plain`(单条直译)/ `context`(注入当前页上下文 + 限轮轻量 agent) |
| 5 | 术语表提取 | 术语表供给改由会话式工作台承担(§8);人工介入编辑为最终裁决,agent 不直接写术语表 |

---

## 3. 总体架构

核心原则:**agent 的自由度全部放在"读",写入只有一个收口**。agent loop 本身很薄,工程重心在护栏。

```
管线/单框入口(modules/translators/base.py::translate_textblk_lst 边界,不动其他阶段)
    │
    ▼
AgentTranslator(以翻译器身份注册进 utils/registries.py::TRANSLATORS,复用 profile 体系)
    │  任务单位 = 一页;单框 = 页内一个轻量任务(按策略配置)
    ▼
┌─ agent loop(原生 function calling)──────────────┐
│ 初始注入:本页块 + 邻近已完成页快照 + 命中术语表        │
│ loop:模型每轮发起 tool_calls(只读探索 或 提交)      │
└──────────────────────────────────────────────┘
    │  submit_translations → 校验器 → 落盘
    ▼
```

**任务无状态**:每页是独立 agent 任务,跨页上下文来自"编排器注入的历史快照 + agent 工具探索已完成页",不来自会话记忆。收益:单页可重跑、失败不连坐、与现有页序驱动的管线兼容。这一决策**废弃** `modules/context/history.py` 的 GROW/EVICT/REBUILD 窗口状态机(该状态机已删,§11):它优化的 provider 前缀缓存在多轮 agent 模式下不成立;token 预算裁剪思想保留(`modules/context/token_usage.py` 继续用于注入预算控制)。

**探索是可选路径而非必经路径**:第一轮就允许直接提交译文。初始注入(本页 + 邻近页 + 术语)已比旧模式都全,大部分页 1 轮完成;只有模型自认需要(查专名全书分布、看更早剧情)才多轮。"默认快、按需深"是成本护栏的第一道,先于轮数上限生效。

---

## 4. Agent loop 状态机(原生 function calling)

### 4.1 每轮请求

- 消息结构:`system`(契约 + profile system_prompt + 自动注入的历史/术语)→ `user`(任务:本页块 id→原文)→ 之后每轮 `assistant.tool_calls` / `tool`(结果)交替;
- `tools` = §5 工具集,`tool_choice = "auto"`;
- 采样参数(temperature/top_p/reasoning_effort 等)沿用 profile;多 key 轮换 / RPM 限流 / delay / 连接重试机制沿用 `modules/translators/trans_llm_api.py::LLM_API_Translator`。

### 4.2 轮的判定

| 模型行为 | 处理 |
| --- | --- |
| 调用只读工具 | 执行,结果进 `tool` 消息,计入轮数 |
| 调用 `submit_translations` | **唯一结束路径**:进入校验器(§6) |
| 输出纯文本、不调工具 | 视为思考/跑偏:注入一次提醒"必须通过工具提交",计入轮数;再犯直接进入强制收敛,不再纠缠 |

**格式护栏的表述**:所有生效输出都是 tool call——tool_calls 由 API 层保证结构化,最终提交也必须是 `submit_translations` 调用,协议层面不存在自由文本态的生效输出。

### 4.3 强制收敛(原生协议独有优势)

轮数耗尽 / token 预算耗尽时,下一轮请求:

- `tools` 只剩 `submit_translations`;
- `tool_choice = {"type": "function", "function": {"name": "submit_translations"}}` ——模型**被迫**提交,基于已有信息收卷,而不是失败。**取消不走收敛轮**:每轮开头检查取消标志,立即抛 `modules/translators/agent/loop.py::AgentTaskCancelled`,由调用方中断,不做"抓紧收尾"。

强制轮后仍缺块,loop 直接返回已收部分,由调用方走直译补漏(§10)。

### 4.4 循环上限

- 整页任务:默认 8 轮(参数 `agent_max_turns`,translator_params 可调);单框 context 模式:固定 2 轮;
- 任务级 token 预算参数 `agent_token_budget`,默认 0(不限);
- 每轮循环开头检查线程终止标志(取消即时生效);
- 单条工具结果超字符上限(24000 字符)时截断为预览,并在结果内告知"已截断,请缩小查询范围"。

---

## 5. 工具集定义

翻译 agent 工具面自带 `modules/translators/agent/tools.py::AGENT_TOOL_DEFINITIONS`,比旧 ai_chat 的全量工具表**更窄**:只读 + 提交,砍掉 describe_tool(工具描述进 schema)/ set_font / set_color / set_layout / search_replace / translate_text / get_config——这些写类工具已随工作台阶段 4 从 `utils/ai_tools.py` 移除。

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `list_pages` | 只读 | 页面概览索引(`utils/proj_compact.py::build_index`) |
| `read_pages(start, end)` | 只读 | 页面详情,翻译模式 fields_whitelist 只含 src/trans/竖排标记(参数由 `utils/proj_compact.py::build_detail` 承接)。单次 ≤5 页,>20 页分块 |
| `search_blocks(query, field)` | 只读 | 全项目搜块,定位专名/对话出现位置 |
| `get_page_info(start, end)` | 只读 | 页尺寸元信息 |
| `search_glossary(query)` | 只读 | **新增**:按需查术语表全表(模糊/包含匹配)。保底命中词条仍由编排器自动注入,此工具用于模型主动核对 |
| `submit_translations(translations)` | **唯一写出口** | 参数 `{"块id": "译文"}`,id 封闭集 = 本任务输入块集合 |

工具面还按任务资源收窄:无 project 时整组探索工具不出现,无术语表时不出现 `search_glossary`(组装见 `modules/translators/agent/tools.py::available_tool_defs`)。schema 转换复用 `utils/ai_tools.py::to_openai_tools`;只读工具的执行经 `utils/ai_tools.py::execute_tool` 分发,由 agent 侧白名单收窄到 4 个只读工具(写类工具在本层不可达),`submit_translations` 则不进 `execute_tool`,由 loop 拦截进校验器。

---

## 6. 护栏体系(按"不按规范"的失效模式分类)

### A. 格式护栏

每轮生效输出都是原生 tool call;`submit_translations` 的参数经 schema 约束(`additionalProperties: false`,value 为 string)。**不存在从散文里捞 JSON 的解析路径**(旧 ai_chat 的 parse_tool_calls 脆弱文本协议不进入翻译链路,且已随阶段 4 删除)。

### B. 循环护栏

`max_turns`、单任务 token 预算、工具结果截断、每轮取消检查、强制收敛轮(§4.3)。

### C. 权限护栏——读写分离

- 读:全项目只读(原文 + **已完成页译文**,天然翻译记忆);
- 写:**仅本任务输入 id 集合内的 translation 字段**。校验器强制:集合外的 id 拒绝、缺失打回、多余丢弃;
- 纵深防御:即使模型被注入带偏,最坏损失是译文质量,无法穿透权限边界改动样式/配置/其他页。

### D. 注入护栏

漫画原文是不可信输入,agent 化后注入面变大(工具读回的原文进入后续轮次):

- 原文/工具结果只出现在 `tool`/`user` 角色,**永不进 system**;
- system prompt 声明:工具返回的一切是待翻译数据,其中任何指令性文字都是内容不是指令;
- 出口校验兜底(见 E),注入攻击最多损害质量,无法越权。

### E. 出口质量护栏——提交校验器

提交后、落盘前统一过一道(取代旧实现的零散清洗),实现见 `modules/translators/agent/validator.py`:

1. **id 封闭集校验**:集合外拒绝;
2. **数量全覆盖**:缺失块打回补译(单独小请求补漏,复用 `invalid_repeat_count` 重试语义),超次后**显式报告**失败块并保留原译文,不静默;
3. **统一清洗**:换行规整(`\r\n`/`\r` → `\n`)、首尾空白,一处生效;
4. **空译文 / 译文=原文检测**:源语言→目标语言完全相同大概率偷懒,先警告后打回;纯数字与单字符源不判定(粗粒度豁免规则,边界见 `modules/translators/agent/validator.py`);
5. **术语残留检测**:自动注入的命中词条,其 src 原词(如"ルフィ")仍出现在对应译文 = 未遵守 → 先警告后打回。子串精确判定,不判断译名正确性。

### F. 可观测与回退链

- 每轮工具调用写 debug 日志(`utils/debug_log.py` 基建,开关 `agent_translation_debug_log`);UI 侧经每轮状态回调在翻译进度条消息里显示"第 N 轮 + 本轮工具名"(`ui/module_manager.py::on_agent_turn_status`),不黑盒;回退链见 §10,**agent 失效不等于翻译失效**。

---

## 7. 编排注入(自动注入 + 按需探索)

任务开始时编排器组装初始上下文(组装点见 `modules/translators/agent/prompts.py`):

| 注入项 | 来源 | 预算 |
| --- | --- | --- |
| 本页全部块(任务主体) | `page_key` 对应 `pages` | 无裁剪 |
| 邻近已完成页快照(src+trans) | 按页序向前取,资格 = 整页已有译文(`modules/translators/agent/prompts.py::build_history_snippet` 的资格判定);注入格式沿用散文片段 | `pcfg.module.llm_prior_context_token_budget`(4096,复用现有配置),`modules/context/token_usage.py` 计数;另有页数上限(短块页会把预算吃满、注入几十页,实机反馈),超出部分由探索工具按需深挖 |
| 命中术语表 | `modules/context/glossary.py::select_glossary` matching 模式 | 现状逻辑 |
| 全局梗概(工作台阶段 3 已落地) | 项目级 `llm_compact_memory`(术语工作台「应用」写盘,`modules/context_agent/story.py::project_synopsis` 只读消费),经 `modules/translators/agent/prompts.py::synopsis_section` 进 system 稳定前缀 | 强制注入项:`modules/translators/agent/prompts.py::effective_history_budget` 先于可选历史页扣预算(驱逐后地板 1);开关 `llm_story_context` |

- profile 的 `system_prompt` 在 agent 模式下**生效**,定位仍是"只影响风格措辞";超预算时从最旧页开始裁剪(窗口机制废弃,裁剪逻辑简化为一次性快照选择)。

---

## 8. 术语表供给链

术语表是**项目级一致性资产**,三方共治:工作台供给、人工编辑裁决、agent 翻译消费。这是权限护栏(§6.C)的自然延伸——**agent 不直接写术语表文件**,所有变更经 dialog 人工确认落盘。

```
频次预填充 / agent 整合候选(术语工作台 modules/context_agent/)
        │(候选,带来源标记,只落草稿)
        ▼
人工编辑确认(ui/glossary_agent_panel.py 草稿表;user-owned 冲突由人裁决)
        │ 「应用」落盘 JSON(pcfg.module.llm_glossary_path)
        ▼
术语表文件
        │
        ├─ 编排器自动注入保底(§7)
        ├─ search_glossary 工具按需全查(§5)
        └─ 出口校验·术语残留检测(§6.E)
```

> 原「提取术语表」链路(one-shot LLM + GlossaryExtractorDialog)已于 2026-08-31
> 由会话式术语工作台取代(`modules/context_agent/`,入口=嵌字页窄栏
> rail_glossary 图标);下述 §8.1 修缮方案随之作废。

### 8.1 提取器(已被工作台取代,记录存档)

原 one-shot 提取器(2026-08-31 已删):LLM 单轮宽松解析、3000 行硬截断、
自维护单发调用层等问题,均由工作台的多轮会话 + 统一调用层根治;
频次统计迁入 `modules/context_agent/precollect.py::extract_by_frequency`,
降级为草稿预填充按钮(要求已有译文的语义不变)。

### 8.2 两个使用时机(语义保留)

- **翻译前**:让工作台 agent 通读原 src 产出候选译法 → 人工在草稿表校对 → 「应用」入库,整本翻译时作为注入保底;
- **翻译后**:基于实际 src/trans 对整合(频次预填充 / agent 会话),准确度更高;并可带出 agent 沉淀候选(§8.3)。

### 8.3 agent 沉淀候选回流

- 机制:整本翻译完成后,复用 `modules/context_agent/precollect.py::extract_by_frequency` 的统计逻辑对**本次新产生的译文**跑增量统计,新达到阈值(出现 ≥2 次且译法一致)的词条进入候选列表;
- 候选在工作台草稿表带**来源标记**(base / AI / 人工),人工编辑后才并入库——人工是最终裁决;
- 设计取向:**拉模式**(打开工作台时载入现有数据为草稿基底),不做后台常驻统计线程——精简优先,零常驻状态。

### 8.4 人工编辑(工作台草稿表)

由 `ui/glossary_agent_panel.py` 的草稿表承担:打开即加载 `llm_glossary_path`
已有表 / 增删行 / 双击编辑;user-owned 条目对 AI 提案受保护,冲突以冲突行
呈现由人工裁决;「应用」按钮是唯一落盘路径。

### 8.5 入口

- 主窗口左侧栏 LeftBar 的工作台图标(`ui/mainwindowbars.py::LeftBar` 的 workbenchChecker,面板为 `ui/mainwindow.py` 内嵌左栏 `ui/glossary_agent_panel.py::GlossaryAgentPanel`——与全局搜索同槽位的加宽版,开合走宽度动画 `ui/mainwindow.py::on_set_workbench_widget`);
- Run 对话框翻译区的 glossary 节(beta 清理后重整完成):整本翻译前可见术语表就绪状态(路径 + 勾选)与 matching/all 模式,Browse 不再是死路径(`ui/run_pipeline_dialog.py`)。

---

## 9. 单框翻译策略

配置项 `pcfg.module.single_blk_translate_mode`:

| 档 | 行为 |
| --- | --- |
| `plain` | 单条直译(默认):不注入页面上下文,直接走单次补全(即回退直译路径) |
| `context` | 轻量 agent:当前块 + 自动注入**当前页其余块**(src + 已有译文)+ `max_turns = 2` |

- `ui/module_manager.py::_blktrans_pipeline` 的 page_key 已修:`current_page_key` 由调用方传入,单框任务据此定位所在页。plain 档同样需要它(缺 page_key 时页内其余块无从取),修好后 context 档天然成立;
- UI 入口在 Run 对话框的翻译设置区(`ui/run_pipeline_dialog.py` 的两档下拉),两档可切换,遵守"交互路径越短越好"。

---

## 10. 回退链(显式,不静默)

```
agent loop(任何失败:格式崩/轮数耗尽仍无有效提交/端点不支持 tools 报 400)
    ▼ 降级
单次补全直译(现 modules/translators/trans_llm_api.py 的 JSON id 契约路径,json_schema 兜底)
    ▼ 仍失败
报错(沿用现有重试/错误对话框体系),保留原译文
```

- 端点不支持 function calling 是**预期内的正常回退**,状态栏提示一次即可,不算错误;
- 每次降级写入 debug 日志;缺块同样走这条链:loop 返回的部分结果缺哪些 id,就对哪些 id 单独发直译补漏,补漏也失败时显式置空并报错、不伪装成功(入口见 `modules/translators/trans_agent.py::AgentTranslator`)。

---

## 11. 旧代码处置与债务清理

| 现有物 | 处置 |
| --- | --- |
| `modules/translators/trans_llm_api.py::LLM_API_Translator` | profile 体系 / json_schema 基建 / 多 key 轮换 / RPM 限流 / 重试机制全保留;直译路径降为回退;历史散文注入被编排注入取代 |
| beta 翻译器 `ContextBatchTranslator` | **已删**(2026-08-23,删前在 `scripts/audit_registry.json` 登记 deprecated) |
| Run 对话框 "Context Translation (beta)" 入口 | **已删**;"上下文翻译"从开关变为 agent 模式固有行为 |
| `modules/context/history.py` 窗口状态机 | **已废弃**(HistoryWindow / eligible_history_for_request / recover_context_length 已删,仅留 `modules/context/history.py::RequestContext` 快照模式与 token 计数) |
| `ui/module_manager.py::_blktrans_pipeline` page_key | **已修**(§9) |
| 原 one-shot 提取器后端 + 提取对话框 GlossaryExtractorDialog | **已删**(2026-08-31 术语工作台取代,§8:草稿表/来源标记/落盘走「应用」;频率逻辑迁 `modules/context_agent/precollect.py`) |
| Run 对话框 glossary 路径时序 bug | **已修**:术语表路径读取移到构造前,beta 删除后入口重整时一并处理(§8.5) |
| `translate_context` / `context_translation_debug_log` | 已删(死配置)/ 已并入 `agent_translation_debug_log`(`utils/debug_log.py` 前缀同步为 `agent_translation_*`) |
| `llm_translate_context`(page/history) | 语义简化为"是否注入历史页"开关,默认开(原 history 语义) |

---

## 12. 配置项汇总

| 配置 | 现状 |
| --- | --- |
| `single_blk_translate_mode` | `plain`(默认)/ `context`(§9) |
| translator_params(agent 部分) | `agent_max_turns`(默认 8)、`agent_token_budget`(默认 0 = 不限) |
| `llm_prior_context_token_budget` | 复用(邻近页注入预算,默认 4096) |
| `llm_glossary_path` / `llm_glossary_mode` | 复用(自动注入保底 + search_glossary 工具) |
| `llm_translate_context` | 保留为历史注入开关,默认开 |
| `translate_context` / `context_translation_debug_log` | 前者已删(死配置);后者已并入 `agent_translation_debug_log`(默认关) |
| `llm_story_context` | 剧情梗概注入开关,默认开;仅影响翻译注入,不影响工作台本身 |
| profile 全部字段(`return_json_schema`、`reasoning_effort` 等) | 语义不变;`return_json_schema` 只影响回退直译路径的 response_format(agent 轮次固定带 tools) |

---

## 13. 落地情况(原"实现拆分五阶段",已全部落地)

组件落点 `modules/translators/agent/` 包(loop / validator / prompts / tools 分文件),工具执行复用 `utils/proj_compact.py` 的 build_* 函数;逐阶段现状如下。

| 阶段 | 内容 | 现状 |
| --- | --- | --- |
| 1. 地基 | AgentTranslator 注册 + agent loop + `submit_translations` + 校验器(A/B/C 类护栏)+ 整页翻译接入;直译路径降为回退 | 已落地:`modules/translators/trans_agent.py`、`modules/translators/agent/loop.py` |
| 2. 探索工具 | `read_pages`/`search_blocks`/`get_page_info`/`search_glossary` 接入 loop + 注入护栏(D 类)+ 强制收敛轮 | 已落地:`modules/translators/agent/tools.py`(§4.2/§4.3) |
| 3. 单框策略 | 修 page_key + `single_blk_translate_mode` 配置 + UI 入口 | 已落地:page_key 已通,档位在 Run 对话框翻译区(§9) |
| 4. 债务清理 | 删 beta / 窗口机制 / 死配置,修 glossary 时序 | 已销账,逐项见 §11 |
| 5. 质量护栏 | 术语残留检测 / 空译文检测 / 打回补译(E 类)+ debug 日志与状态栏轮次显示(F 类) | 已落地:`modules/translators/agent/validator.py`(§6.E);测试见 §14 |
| 6a. 术语表供给链 | 提取器 schema 化 / 分批 / 统一调用层 + dialog 加载已有表 / 合并去重 / 增删行编辑 | 已由术语工作台整体取代(2026-09-01 销账):打开即载入已有表 / 合并去重 / 增删行编辑 / 「应用」唯一落盘 |
| 6b. 沉淀回流 | agent 翻译后增量统计 → 候选来源标记 → 人工确认入库 | 已达:频次预填充拉模式统计项目译文产候选,经工作台草稿表 origin 标记(existing/ai/user)呈现,人工确认「应用」入库(§8.3) |

---

## 14. 测试面(契约测试)

现状覆盖:`tests/test_agent_translator.py`(loop 状态机 / 提交校验器 / prompt 组装 / 工具面,假 LLM 驱动)、`tests/test_agent_single_block.py`(单框两档与 translate 入口分支)、`tests/test_story_injection.py`(梗概注入与预算驱逐);术语工作台侧为 `tests/test_context_agent_draft.py`、`tests/test_context_agent_session.py`。全量回归走 `scripts/verify.py --full`;能力约定:输出数量必须等于输入(沿用翻译器测试惯例)。

- **loop 状态机**:假 LLM 驱动——先探索后提交 / 超限强制收敛(tool_choice 锁定)/ 取消即时生效 / 纯文本轮提醒;
- **校验器**:id 越界 / 数量缺失打回 / 清洗规则 / 术语残留 / 空译文,全部失效模式各一例;
- **编排注入**:历史页资格判定 / 预算裁剪顺序 / 术语 matching 注入;
- **回退链**:端点 400(不支持 tools)降级直译、缺块直译补漏——**目前无测试覆盖**,补测入口是 `modules/translators/trans_agent.py` 的 `AgentTranslator` 翻译入口;
- **术语表供给链**:候选草稿 patch/冲突裁决 / 会话轮次与只读白名单(`tests/test_context_agent_draft.py`、`tests/test_context_agent_session.py`)。
