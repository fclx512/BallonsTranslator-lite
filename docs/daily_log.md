# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 7 天的记录，超期内容自动清理。

---

## 2026-06-11

### 上下文翻译功能恢复

**背景：** 上下文翻译原本是 Run 对话框中的一个可选功能，在清理 AI 助手时被整体移除。现在从 git 历史恢复，但配置来源改为复用当前翻译器（`LLM_API_Translator`）的 `active_profile`。

**改动文件：**

| 文件 | 操作 | 说明 |
|------|------|------|
| `modules/translators/context_batch.py` | 新建 | `ContextBatchTranslator` — 轻量上下文批量翻译器，使用 profile 配置 |
| `ui/mainwindow.py` | 修改 | Run 对话框添加"上下文翻译"UI + 配置来源替换 + 管线恢复逻辑 |
| `translate/zh_CN.ts` | 修改 | 清理孤儿条目，重新编译 qm |
| `translate/zh_CN.qm` | 修改 | 编译后二进制 |
| `docs/zh/context_translation.md` | 修改 | 更新配置来源描述（AI 助手 → profile） |
| `docs/en/context_translation.md` | 修改 | 同上 |
| `docs/daily_log.md` | 新建 | 本文件 |

**架构变化：**

- 旧：`self._ai_controller.api_config` + `self._ai_controller.custom_prompt`（已删除）
- 新：`self.module_manager.translator._active_profile` → `api_host`/`api_key`/`model` + `prompt_template`

**逻辑版本：** 使用 `cbf42ad` 迭代版（非 `a23e554` 初版）：
- 自适应策略（自动按页数选择 full/windowed/summary），无手动策略选择器
- `status_callback` 回调 → 进度条显示翻译进度
- 移除 `response_format=json_schema`（兼容性更好）
- Pydantic 模型公开类 `CtxElement`/`CtxResponse`

**UI：** Run 对话框中：
- "Enable Translation" 右侧内联"Context Translation (beta)"复选框
- 勾选后展开设置面板：自适应模式标签、Batch Size、Context Pages、Glossary
- 管线完成后恢复原始翻译器
