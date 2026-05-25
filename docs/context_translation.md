# 上下文翻译（Context Translation）技术文档

## 概述

上下文翻译是 Run 对话框中的一个可选功能，勾选后在批量翻译时通过 LLM 感知相邻页面内容（原文/译文、术语表），实现更准确、一致的角色语气和术语翻译。

## 架构

```
Run 对话框 (mainwindow.py:run_imgtrans)
    │
    ├─ 勾选 "上下文翻译 (beta)"
    │      │
    │      ├─ 读取 AI 助手的 api_config (host/key/model/temp/proxy)
    │      ├─ 读取 AI 助手的自定义翻译提示词 (custom_prompt)
    │      │
    │      └─ 创建 ContextBatchTranslator 实例
    │             ├─ 注入上下文策略参数
    │             └─ 临时替换管线的 translator
    │
    └─ 管线运行
           │
           ├─ set_project() → 注入项目引用
           ├─ translate_textblk_lst() → 每页调用，带上下文
           │     ├─ 根据策略构建上下文
           │     ├─ 组装 LLM messages
           │     └─ 调用 OpenAI API → 解析 → 缓存
           └─ finalize() → 清理
                  │
                  └─ mainwindow.on_imgtrans_pipeline_finished
                        └─ 恢复原始翻译器
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `modules/translators/context_batch.py` | `ContextBatchTranslator` — 轻量批量翻译器，不含 profile 系统 |
| `ui/mainwindow.py:1816-1835` | Run 对话框逻辑：读取 AI 助手配置 → 创建翻译器 → 替换管线 |
| `ui/mainwindow.py:1457-1462` | 管线完成后的翻译器恢复 |
| `utils/ai_controller.py` | AI 助手的配置来源（`api_config`、`custom_prompt`） |

## ContextBatchTranslator 设计

### 与普通 translator 模块的区别

| 方面 | 普通 translator（如 AI_Chat_Translator） | ContextBatchTranslator |
|------|-----------------------------------------|------------------------|
| 注册方式 | `@register_translator` 装饰器 | 不注册，Run 对话框直接实例化 |
| 配置来源 | `cfg_module.translator_params[name]` — 独立 profile 系统 | AI 助手的 `api_config` + `custom_prompt` |
| 继承链 | `BaseTranslator → LLM_API_Translator → AI_Chat_Translator` | 独立类，无继承 |
| 生命周期 | 全局单例（配置面板管理） | 每次 Run 创建，运行后销毁 |
| API 凭据 | 自己管理 API key 轮换、限速 | 直接读取 AI 助手的已配置凭据 |

### 管线接口

管线只需要三个方法，`ContextBatchTranslator` 全部实现：

```python
def set_project(self, proj: ProjImgTrans)    # 注入项目，建立页面索引
def translate_textblk_lst(self, textblk_lst)  # 每页调用，驱动上下文翻译
def finalize(self)                             # 清理缓存
```

辅助属性：
- `low_vram_mode = False`
- `is_computational_intensive() → True`（强制同步路径，确保页面顺序处理）

### 上下文策略

三种策略通过 `context_strategy` 属性控制：

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| `full` | 整批（≤batch_size 页）一次 API 调用 | 短篇 1-20 页 |
| `sliding_window` | 每页翻译时，前后 N 页作为上下文 | 中篇 10-50 页 |
| `progressive_summary` | 每组翻译后压缩摘要，后续批次带上摘要+术语表 | 长篇 50-300+ 页 |

### 消息组装

1. System prompt 优先使用 AI 助手的 `custom_prompt`（含 `{from_lang}`、`{to_lang}` 占位符），无自定义时使用内置 fallback
2. 如果启用术语一致性，向 system prompt 追加术语表
3. User prompt 包含上下文页面（原文/译文对）和本次待翻译块

### API 调用

- 使用 `openai.OpenAI` 客户端直连（不走 translator 的 profile 系统）
- `response_format = json_schema` 强制结构化输出（Pydantic 模型 `_CtxResponse`）
- 重试：解析失败重试 3 次（2s 间隔），API 错误重试 3 次（5s 间隔）

## 缓存机制

- 按 page_key 缓存翻译结果 `_cached: Dict[str, Dict[int, str]]`
- `full` 模式下，第一个触发页调用 API，同 batch 的其他页读取缓存
- `sliding_window` 逐页触发 API，不跨页缓存
- `progressive_summary` 按 batch 触发 API，同 batch 内缓存

## 术语一致性

- `use_glossary=True` 时追踪同一项目中出现 ≥2 次的源文本
- 术语表最多 50 条，超出时淘汰最早条目
- 每次 API 调用时注入 system prompt（"Term consistency guide"）

## AI 助手配置关联

上下文翻译的 API 配置**完全复用 AI 聊天面板**的设置：

1. 用户在 AI 聊天面板设置中选择 API Profile → 写入 `config/ai_chat_config.json`
2. `AiController` 加载该文件，`api_config` 和 `custom_prompt` 作为属性暴露
3. Run 对话框通过 `self._ai_controller.api_config` / `self._ai_controller.custom_prompt` 读取

因此用户只需在 AI 聊天面板配好 API，即可在 Run 对话框中使用上下文翻译，无需额外配置。
