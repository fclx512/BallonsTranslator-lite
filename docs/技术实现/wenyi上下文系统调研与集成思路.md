# wenyi（BigDawnGhost/wenyi）调研报告与集成思路

> 撰写日期：2026-07-23
> 用途：供内部 AI 协作，评估 wenyi 的长篇翻译上下文方法论能否融入我们的漫画/图片翻译工具，以提升译文质量和跨页上下文关联能力。
> 阅读对象：需要了解双方技术架构的 AI / 开发者。
> 核心方向：**基本保留我们现有代码逻辑，吸收 wenyi 的上下文架构和术语演化优点，而非照搬管线。**

---

## 一、wenyi 项目概况

| 属性 | 值 |
|------|-----|
| 仓库 | github.com/BigDawnGhost/wenyi |
| 定位 | 命令行小说翻译工具：EPUB / FB2 / TXT / Markdown / HTML / PDF → 中文 |
| 语言 | Python 3.10+，包管理 `uv` |
| 许可证 | MIT |
| 活跃度 | ~1.6k stars，133 forks（2026-07 数据）；近期持续更新，v0.3.3（2026-07-19） |
| 标语 | "将被语言阻隔的作品，带到读者的语言中。Bringing literature into your language." |

### 1.1 设计哲学

wenyi 聚焦**长篇小说翻译质量**，通过五个维度提升译文的连贯性和一致性：

1. **全书预分析**（whole-book analysis）：翻译前通读全书，生成风格指南 + 角色圣经 + 初始术语
2. **滚动上下文**（rolling context）：实时保持最近 N 段译文尾巴，供局部连贯
3. **演化术语库**（evolving glossary）：翻译过程中实时抽取新术语，冲突检测，全书统一
4. **润色**（polishing）：翻译完成后用强模型再跑一遍润色
5. **审校**（review）：最终审校 + 回译抽检 + 一致性扫描

### 1.2 项目结构

```
wenyi/
├── trans_novel/
│   ├── cli.py              # Typer 命令行入口
│   ├── config.py           # YAML 配置加载
│   ├── ingest/             # ★ 文件读取层
│   │   ├── text_reader.py      # TXT/Markdown 读取（章节+段落识别）
│   │   ├── epub_reader.py      # EPUB 读取
│   │   ├── segmenter.py        # 文档加载分发 + 翻译批次切分
│   │   └── models.py           # Document → Chapter[] → Segment[] 统一模型
│   ├── pipeline/           # ★ 管线编排
│   │   ├── orchestrator.py     # 全流程状态机 + 断点续跑
│   │   ├── context.py          # ★ ★ 滚动上下文（核心数据模型）
│   │   ├── runstore.py         # 章级状态持久化（SQLite/JSON）
│   │   └── checks.py           # 前置校验
│   ├── agents/             # ★ LLM Agent 层
│   │   ├── analyzer.py         # ★ 全书预分析（风格/角色/术语）
│   │   ├── synopsis.py         # ★ ★ 全书概览 + 逐章梗概生成
│   │   ├── translator.py       # ★ 翻译 Agent（对齐保证）
│   │   ├── polisher.py         # 润色 Agent
│   │   ├── reviewer.py         # 审校 Agent + 回译抽检
│   │   ├── consistency.py      # 跨章一致性扫描
│   │   ├── prompts.py          # ★ ★ ★ 所有提示词模板（前缀缓存优化）
│   │   └── base.py             # Agent 基类（统一 LLM 调用）
│   ├── glossary/           # ★ 术语系统
│   │   ├── store.py            # ★ SQLite 术语库（冲突检测 + 翻译记忆）
│   │   ├── extractor.py        # ★ 实时术语抽取（翻译后自动抽新词）
│   │   └── resolver.py         # 冲突解决辅助
│   ├── assemble/           # 输出组装
│   │   └── translator.py       # 实际翻译逻辑（对齐重试 → 逐段兜底）
│   └── llm/                # LLM 抽象层
│       ├── base.py             # 抽象 LLMClient 接口
│       ├── factory.py          # Provider 工厂（DeepSeek/OpenAI/Ollama...）
│       └── ...provider 实现...
├── config.yaml             # 用户配置（LLM/切分/管线开关）
└── docs/                   # 使用指南
```

### 1.3 数据流

```
源文件 → ingest/reader → Document(Chapter[Segment[]])
    ↓
agents/analyzer（全书预分析：风格指南 + 角色 + 初始术语）
    ↓
agents/synopsis（全书概览 + 逐章梗概）
    ↓
逐章循环：
  ├── 按批取 Segment[]
  ├── 渲染上下文（全书概览 + 本章梗概 + 术语 + 前文译文）
  ├── translator.translate_batch()  ← 对齐保证 N→N
  ├── 写入 glossary（实时术语抽取）
  ├── 追加滚动上下文
  └── 章末：标点规范化 → 兜底术语抽取 → 回译抽检 → 写 TM
    ↓
全书完成后：
  ├── polisher（可选润色）
  ├── reviewer（审校 + 自动重译严重项）
  ├── consistency QA（一致性扫描）
  └── assemble（输出 EPUB/TXT/HTML/Markdown）
```

---

## 二、wenyi 核心创新详解

### 2.1 ★★★ 三层上下文架构（最大亮点）

wenyi 将上下文分为三个互补层次，各自解决不同粒度的问题：

#### 第 1 层：全书概览（Book Synopsis）— 全局稳定性

| 属性 | 说明 |
|------|------|
| 生成时机 | 翻译开始前，预扫全部源文后 |
| 长度 | ≤ 500 字简体中文 |
| 内容 | 主线剧情走向、人物关系与弧光、核心设定/谜底、整体基调 |
| 更新频率 | **全程恒定**（译完全书都不变） |
| 缓存价值 | 极高——作为恒定前缀，每次翻译调用都命中缓存 |
| 实现 | `agents/synopsis.py` — `Synopsizer.book_synopsis()` |

生成方式：
1. 逐章调用 `digest_chapter()` 压成 ≤200 字梗概（`fast` 档，`max_tokens=600`）
2. 所有梗概 + 分析结果传入 `book_synopsis()`，超长时分组 map-reduce
3. 并发控制：`prescan_concurrency: 4`（默认 4 线程并行预扫）

#### 第 2 层：本章梗概（Chapter Digest）— 本章脉络

| 属性 | 说明 |
|------|------|
| 生成时机 | 翻译开始前，与全书概览并行 |
| 长度 | ≤ 200 字简体中文 |
| 内容 | 本章关键情节推进、登场人物及处境、重要信息或转折 |
| 更新频率 | **每章恒定** |
| 缓存价值 | 高——章内全部批次共享同一前缀 |

#### 第 3 层：滚动上下文（Rolling Context）— 局部连贯

| 属性 | 说明 |
|------|------|
| 生成时机 | 翻译过程中动态累积 |
| 内容 | 最近 N 段译文原文（纯文本） |
| 更新频率 | **每批变化**（追加新译文，裁剪旧尾巴） |
| 默认保留 | 最多 40 段，注入最近 6 段（`rolling_context_segments` 可配） |
| 职责 | 代词指代、人物称谓、语气衔接、跨段句意自然连贯 |
| 实现 | `pipeline/context.py` — `RollingContext` |

**为什么三层互补？**
- 只有滚动上下文 → 早期章节不知道后期伏笔，人名/设定可能翻错
- 只有全局概览 → 当前段落缺乏局部衔接，代词指代可能混乱
- 三者结合 → "知道全书走向 + 知道本章进展 + 知道刚译了什么"

#### 第 4 层（辅助）：风格指南 + 角色信息 + 术语表

- 风格指南来自 `Analyzer` 的预分析结果（体裁、语气、叙事人称、语域等）
- 角色信息包含人物译名、性别、口癖/自称习惯
- 术语表每批按需裁剪（只含本批原文实际出现的词条，节省 token）

### 2.2 ★★★ 提示词前缀缓存优化

**这是 wenyi 最"匠心"的设计**。所有 prompt 模板在 `prompts.py` 中用 `string.Template`（避免与 JSON 花括号冲突），并严格遵守缓存友好原则：

> 注：wenyi 明确面向 DeepSeek 的自动前缀缓存（命中部分输入价 ≈ 0.1×），但此设计对任何提供缓存功能的 LLM provider 都有收益。

**system prompt（全程恒定）**：
```text
你是一位资深的文学翻译，精通将{日语}小说翻译为简体中文...
- 【专有名词对照表】...
- 参考【全书概览】... 参考【本章梗概】... 参考【前文译文】...
- {源语言翻译要点}
- {标点规范}
- 仅输出 JSON：{"translations": ["译文0", "译文1", ...]}
```

**user prompt（稳定 → 动态排列）**：
```text
【角色信息 / 风格指南】 ← 书级恒定
$style

【全书概览】 ← 书级恒定
$book_synopsis

【本章梗概】 ← 章级恒定
$chapter_digest

【专有名词对照表】 ← 批级可能变化
$glossary

【前文译文（最近）】 ← 每批变化
$context

【待译段落】（共 N 段） ← 每批变化
$numbered_source
```

**排列原则**：
- 越靠前的块越稳定 → 前缀缓存命中概率越高
- `$style` + `$book_synopsis` 在全书翻译中**只变化 0 次**（一次写入，全程复用）
- `$chapter_digest` 每章变化一次
- 只有 `$glossary`（术语注入策略设为 `chapter` 时）、`$context`、`$numbered_source` 每批变化

**额外优化**：
- `system` 模板不含任何每批变化的量（如段数 N、裁剪后的术语表）→ system 成为所有同类调用共享的缓存前缀
- `max_tokens` 留足裕量防输出截断，但不设过大浪费
- 梗概等机械任务走 `fast` 档（免思考），降低成本

### 2.3 对齐保证（Alignment Guarantee）

这是避免漏译/少译的关键机制。`translator.py`（实际实现在 `assemble/translator.py`）：

```
translate_batch(sources[N]):
    1. 整批调用 _call_batch() → 要求返回等长 JSON 数组
    2. 校验 len(translations) == N，全为非空字符串
       → 失败则重试，最多 align_retry_limit 次（默认 2）
    3. 重试耗尽仍失败 → 逐段单独翻译兜底（_translate_one）
       → 从结构上保证 1:1 对应
    4. 某段兜底也失败 → 抛出异常保留已有落盘，供续跑
```

> 对比我们当前实现：`LLM_API_Translator` 没有段数校验，模型少译段落时静默吞掉。

### 2.4 术语演化系统（Glossary Evolution）

区别于传统的"一次性导入术语表"：

| 特性 | wenyi | 我们现有 |
|------|-------|---------|
| 存储 | SQLite（glossary + TM + conflicts 三表） | 静态文件（JSON/TXT/TSV） |
| 术语来源 | 初始分析 + 每批实时抽取 | 用户手动维护 |
| 称呼变体 | 自动捕获昵称/敬称/爱称/口癖等变体 | 无 |
| 冲突检测 | 同 source 不同 target → 保留现有，记入待裁决 | 静默覆盖 |
| 注入策略 | `chapter`（只注本章出现词条）或 `full` | `matching`（匹配原文）或 `all` |
| 翻译记忆 | 句群级 source→target 缓存，精确查找复用 | 无 |

**抽取器**（`glossary/extractor.py`）：
- 每批翻译完成后调用，传入原文 + 译文 + 已有术语
- 要求 LLM 抽取：专有实体、称呼变体、需统一的口癖/固定表达
- 同 source 已有不同 target → 记 `conflict` 状态，不自动覆盖

**术语注入函数** `terms_in()`：
- 称谓/口癖/固定表达类型只匹配 `source` 裸名，不匹配 alias，防止派生译法误注入
- 先 NFKC 归一化再 `casefold()` 匹配，容错大小写/全半角差异

### 2.5 管线状态机与断点续跑

`pipeline/orchestrator.py` 实现了完整的章级状态机：

```
状态：PENDING → ANALYZED → DIGESTED → RUNNING → DONE
                                          ↓ 失败
                                       FAILED（保留已落盘 → 重跑自动跳过已完成段落）
```

- 每章有独立的 `ChapterRunState`，`runstore.py` 管理 JSON 落盘
- translation memory（TM）按 `source_hash` 索引，精确命中跳过翻译
- `run_all()` 在全书译完后自动执行 review / QA / assemble 等后处理阶段

### 2.6 TXT 读取器（text_reader.py）

| 特性 | 说明 |
|------|------|
| 编码 | UTF-8 |
| 章节识别 | ① Markdown ATX 标题（#/##）→ ② 日文章节标记（第〇章/第〇話/序章/プロローグ…）→ ③ 无则整篇作一章 |
| 段落分割 | 空行分隔，块内单换行保留 |
| 输出模型 | `Document(title, chapters=[Chapter(index, title, segments=[Segment])])` |
| 与我们的关系 | TXT 读取逻辑本身很简单（~120 行），不构成核心价值 |

---

## 三、我们的现有系统（BallonsTranslator-lite）

### 3.1 我们的上下文系统架构

现有上下文系统位于 `modules/context/` 和 `modules/translators/trans_llm_api.py`：

```
modules/context/
├── history.py        # ★ 核心：HistoryWindow / RequestContext / 预算管理
├── glossary.py       # 术语加载/选择/渲染
├── errors.py         # ContextLengthError 检测
└── token_usage.py    # Token 计数工具

modules/translators/
├── trans_llm_api.py  # ★ 主翻译器：LLM_API_Translator（含上下文注入）
├── context_batch.py  # Beta 批量上下文翻译（独立对话框，非注册模块）
├── base.py           # BaseTranslator（_prepare_textblock_sources 等辅助）
└── ...其他翻译器...
```

### 3.2 LLM_API_Translator 的上下文机制

**工作流程**：
1. 每页有多个 `TextBlock`（文字块），每个有坐标、原文、译文
2. 翻译时逐页进行，每页内的 text blocks 可以批量发
3. 上下文以"页"为单位滑动：

```
RequestContext = {
    system_prompt: str,
    history: tuple[HistoryPage, ...],  # 最近 N 页的翻译记录
    glossary: str,                     # 渲染后的术语表
    token_budget: int,                 # 配置的预算（默认 4096）
}
```

**HistoryWindow 管理**（`history.py`）：
- 跨请求持久化的 `HistoryWindow` 对象
- `eligible_history_for_request()` 决定复用/增长/驱逐/重建窗口：
  - 项目身份比较（`load_identity` 指针）
  - 设置稳定性检查（model, system_prompt, token_budget 是否变化）
  - 页面邻接性（只前进到下一顺序页，跳页则重建）
  - Token 预算低水位线 60% → 驱逐最老页面
- 只有成功解析响应后才提交窗口更新

**消息组装**（`_assemble_request()`）：
```
system prompt（含可选的 history_rule）
    ↓
完整术语表（glossary_mode == ALL 时作为第二条 system message）
    ↓
历史页面交替 user/assistant → 逐页呈现（request_context.history）
    ↓
当前请求 + 匹配的术语表（glossary_mode == MATCHING 时与请求合并）
```

### 3.3 配置参数

来自 `utils/config.py`（`ModuleConfig`）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `llm_translate_context` | `"page"` | `"page"`=无历史；`"history"`=启用页级上下文窗口 |
| `llm_prior_context_token_budget` | 4096 | 历史页面的 token 预算 |
| `llm_glossary_path` | `""` | 术语文件路径 |
| `llm_glossary_mode` | `"matching"` | `"matching"`=只匹配原文出现的；`"all"`=全部注入 |

### 3.4 ContextBatchTranslator（Beta）

独立的批量翻译对话框，未被注册为正式模块：
- 自动根据项目规模选择上下文策略（完整/窗口/窗口+摘要）
- 自带自动学习术语表（source 出现 ≥2 次的词）
- 支持渐进式摘要（超长项目）

---

## 四、双方对比

### 4.1 核心差异

| 维度 | 我们（BallonsTranslator-lite） | wenyi |
|------|-------------------------------|-------|
| **目标领域** | 漫画/图片翻译（气泡文本） | 长篇小说翻译（连续文本） |
| **翻译单元** | 页 → 文字块（TextBlock，含坐标/样式） | 章 → 段落（Segment，纯文本） |
| **文本特征** | 短句、不连续、碎片化 | 长文、连续、叙述性 |
| **上下文粒度** | 页级滑动窗口（最近 N 页） | 三层：全书概览 + 本章梗概 + 滚动尾段 |
| **上下文内容** | 原文+译文成对出现（user/assistant 轮换） | 纯译文尾部（text block） |
| **术语管理** | 静态文件 → 全部加载或按匹配筛选 | SQLite → 实时抽取 → 冲突检测 → 按章裁剪 |
| **对齐保证** | 无 | 严格 N→N + 重试 + 逐段兜底 |
| **润色** | 无 | 独立强模型润色阶段 |
| **审校** | 无 | 最终审校 + 回译抽检 + 一致性扫描 |
| **断点续跑** | 无 | 完整章级状态机 |
| **提示词优化** | 基础 | 刻意前缀缓存优化 |
| **翻译记忆** | 无 | SQLite TM（source_hash 索引） |
| **称谓变体** | 无 | 自动抽取昵称/敬称/口癖等 |

### 4.2 我们的优势（应保留的部分）

1. **图文坐标系统**：我们处理图片上的文字块（位置、字体、颜色、样式），这是小说翻译工具不具备的能力
2. **UI 交互**：可视化编辑、逐块审查、样式管理，wenyi 只是 CLI
3. **Profile 管理系统**：`profile_manager.py` 管理多 API 配置，翻译器和 OCR 共享
4. **模块化注册**：`@register_translator` 等装饰器注册，插件式发现
5. **多管线阶段**：检测 → OCR → 翻译 → 修复 → 渲染，翻译只是五阶段之一

### 4.3 wenyi 的核心可借鉴点（按价值排序）

| 优先级 | 借鉴点 | 复杂度 | 预期收益 |
|--------|--------|--------|---------|
| ★★★ | **多级上下文架构**（项目概览 + 页面摘要 + 滚动译文） | 中 | 跨页一致性大幅提升 |
| ★★★ | **对齐保证**（段数校验 + 重试 + 逐段兜底） | 低 | 杜绝静默漏译 |
| ★★☆ | **术语演化**（实时抽取 + SQLite + 冲突检测） | 中高 | 术语一致性自动维护 |
| ★★☆ | **提示词前缀缓存布局** | 低 | 降低 API 成本（依赖 provider） |
| ★★☆ | **翻译记忆**（TM 精确命中复用） | 中 | 避免重复翻译相同句子 |
| ★☆☆ | **润色/审校阶段** | 中高 | 最终质量提升，但成本翻倍 |
| ★☆☆ | **称谓变体捕获** | 低 | 人物称呼更自然 |

---

## 五、集成思路（方向建议）

### 5.1 核心原则

1. **不动现有管线架构**：检测 → OCR → 翻译 → 修复 → 渲染 的五阶段不变
2. **不动现有 Translator 注册机制**：`trans_llm_api.py` 保持为 `@register_translator` 模块
3. **增量增强**：在现有 `modules/context/` 基础上扩展，不重写
4. **与 page/textblock 模型兼容**：所有上下文方案必须适配我们的按页、按文字块的结构

### 5.2 建议的实施路径

#### Step 1：对齐保证（低难度，高收益）

在 `LLM_API_Translator.translate_textblk_lst()` 中增加：
- 发送前记录 text block 数量 N
- 解析响应后校验 `len(translations) == N`
- 不匹配时重试（可配置次数，默认 2）
- 重试耗尽时逐块翻译兜底

> 改动范围：`trans_llm_api.py` 的 `_parse_response()` 或新增 `_align_response()` 方法

#### Step 2：多级上下文注入（中难度，高收益）

将当前"逐页历史"的单一上下文改为三层结构：

```
【项目概览】      ← 书级恒定（翻译开始前生成，类似 wenyi 的 book synopsis）
  内容：故事背景、主要角色、风格基调
  生成：首次翻译时调用 LLM 摘要源页

【当前语境摘要】  ← 页面级恒定（每页翻译前可选生成）
  内容：当前页的情节作用、关键对话主题
  生成：基于本页原文的简短摘要

【前文译文】      ← 滚动，每批变化
  内容：最近 N 页的原文+译文
  来源：现有的 history window 机制增强
```

架构变化：`modules/context/history.py` 扩展 `RequestContext`，新增：
- `project_overview: str` — 项目概览
- `page_digest: str` — 当前页摘要

`trans_llm_api.py` 的 `_assemble_request()` 重组消息排列顺序，使之缓存友好：
```
system prompt
    ↓
【项目概览】（全程恒定）
【当前语境摘要】（本页恒定）
    ↓
【术语对照表】（可选）
【前文译文】（滚动）
    ↓
当前待译文字块
```

#### Step 3：术语演化（中高难度，中收益）

引入 SQLite 术语库替代静态文件：

```
modules/context/
├── history.py         # 不变
├── glossary.py        # 改为 SQLite GlossaryStore 适配器
├── glossary_store.py  # ★ 新增：SQLite 术语库（参考 wenyi 的 store.py）
├── extractor.py       # ★ 新增：翻译后实时术语抽取
├── errors.py
└── token_usage.py
```

- 兼容现有 `llm_glossary_path` 配置：首次加载 JSON/TXT/TSV 时导入 SQLite
- 新增配置 `llm_glossary_evolution: bool`（默认关闭，避免用户意外消费）
- 翻译完成后回调 `extractor.py` 抽取新术语

#### Step 4（可选）：前缀缓存优化

重组 `_system_prompt()` 和 `_assemble_request()` 的消息顺序，确保：
- system 消息完全静态（不包含段数、术语等变化量）
- user 消息按"最稳定 → 最动态"排列
- 术语表放在 user 消息靠前位置，而非独立的 system 消息

### 5.3 不受影响的部分

以下部分保持不动：
- `modules/translators/base.py` — BaseTranslator 抽象不变
- `utils/config.py` — 模块参数定义方式不变
- `ui/io_thread.py` — I/O 线程不受影响
- `ui/mainwindow.py` + `ui/mainwindow_mixin.py` — UI 不变
- 所有其他翻译器（Sakura/Baidu/DeepL/Google 等）— 上下文增强只影响 `LLM_API_Translator`

### 5.4 配置项变更清单（新增）

```python
# 新配置参数（在现有的 ModuleConfig 中扩展）

llm_project_overview: bool = False
# 启用项目概览生成（首次翻译时自动摘要源页）

llm_context_digest: bool = False
# 启用每页语境摘要（额外一次 LLM 调用）

llm_align_retry_limit: int = 2
# 批量翻译对齐重试次数，0=不重试

llm_glossary_evolution: bool = False
# 启用翻译过程中的实时术语抽取与入库
```

---

## 六、wenyi 关键文件速查表

供后续实现时对照参考：

| wenyi 文件 | 行数 | 核心功能 | 对我们的参考价值 |
|-----------|------|---------|----------------|
| `pipeline/context.py` | ~50 | `RollingContext` 数据类 | ★★★ 可直接借鉴实现 |
| `agents/prompts.py` | ~200 | 所有提示词模板 + `render()` | ★★★ 前缀缓存布局设计 |
| `assemble/translator.py` | ~120 | 对齐保证 + 批量重试 + 逐段兜底 | ★★★ 可直接复用逻辑 |
| `agents/synopsis.py` | ~70 | 逐章梗概 + 全书概览生成 | ★★☆ 适配到我们的页级场景 |
| `agents/analyzer.py` | ~100 | 风格/角色/术语预分析 | ★★☆ 概念可借鉴 |
| `glossary/store.py` | ~250 | SQLite 术语库 + TM + 冲突检测 | ★★☆ 可裁剪后使用 |
| `glossary/extractor.py` | ~? | 实时术语抽取 | ★★☆ 翻译后回调 |
| `ingest/segmenter.py` | ~100 | 分批策略 + 超长段拆分 | ★☆☆ 文本分批通用逻辑 |
| `pipeline/orchestrator.py` | ~800 | 全流程编排 + 状态机 | ★☆☆ 架构参考，不直接复用 |

---

## 七、风险与注意事项

| 风险 | 说明 | 缓解 |
|------|------|------|
| **API 成本增加** | 多级上下文（摘要生成）+ 术语抽取 + 润色审校都会增加 token 消耗 | 默认关闭，用户按需开启 |
| **上下文与漫画场景的适配** | 小说是线性的，漫画气泡在页内无序排列 | 项目概览/页面摘要需基于原文语义，而非文字块序号 |
| **配置复杂度** | 过多的上下文开关会让用户困惑 | 默认保持当前行为，新功能全为 `False` |
| **wenyi 的依赖** | 不引入 wenyi 作为 pip 依赖可以避免冲突 | 只借鉴设计，用自己的 LLM 抽象层 |
| **中文倾向** | wenyi 默认目标语言为中文，我们的 app 翻译方向不限于中文 | 上下文注入的文案需要国际化 |

---

## 八、总结

wenyi 的核心价值**不在 TXT 读取**（那只是 ~120 行的简单逻辑），而在其**三层上下文架构、术语演化系统和提示词缓存优化**。

对我们最直接、最高收益的改进是：
1. **对齐保证**（~1 天开发量）— 彻底杜绝静默漏译
2. **多级上下文注入**（~2-3 天）— 显著提升跨页/跨段一致性
3. **术语演化**（~3-5 天）— 长项目自动维护术语一致性

三者顺序执行、各自独立，可以逐步上线。
