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

### PP-OCRv6 可行性验证

**背景：** 审计文档 `docs/zh/pp_ocr_v6_audit.md`（上次创建）处于"待验证"状态。本次通过网络搜索和 GitHub API 收集 PP-OCRv6 的实际发布状态和接口信息。

**关键发现：**

- **PP-OCRv6 今天（2026-06-11）随 PaddleOCR v3.7.0 正式发布**，不再是"未来"版本
- `paddleocr` pip 包的 `PaddleOCR()` 原生支持 `ocr_version="PP-OCRv6"`，确认源码 `_SUPPORTED_OCR_VERSIONS`
- 三档模型：**tiny** (1.5M) / **small** (7.7M) / **medium** (34.5M)
- 检测模型默认名 `PP-OCRv6_medium_det`，识别模型默认名 `PP-OCRv6_medium_rec`
- **paddleocr v3.7.0 API 重构**：参数名改为新风格（`det_model_dir` → `text_detection_model_dir`），旧参数仍兼容但不推荐
- PaddleX 管理模型下载，不再需要完整 PaddlePaddle 框架，比旧版更轻量
- 纯 PyTorch 版仅有检测模型（HF Transformers safetensors），识别模型仍需 Paddle 后端（不构成障碍）

**改动文件：**

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `.mcp.json` | 新建 | AnySearch MCP 服务器配置（streamable-http） |
| `C:\Users\duham\.claude\settings.local.json` | 修改 | 启用 `anysearch` MCP 服务器 |
| `docs/zh/pp_ocr_v6_audit.md` | 修改 | 状态改为"已验证可实施"，补充 v3.7.0 API、模型规格、风险项 |
| `docs/daily_log.md` | 修改 | 本记录 |

**待继续：**

- PP-OCRv6 模块实现（`modules/ocr/ocr_paddle_v6.py`）
- 依赖管理器改进（`ensure_dependencies()` 进度信号 + UI 安装向导）
- tiny/small 模型完整名称需从 PaddleX 配置中提取

---

## 2026-06-16

### 二进制 PSD 导出（Phase 1-4 完成）

**背景：** 现有 ExtendScript (.jsx) 导出存在字体位置偏移等问题，且依赖 Photoshop 运行脚本。新增直接二进制 PSD 导出器，移植自 Koharu-psd（Rust crate），可一键生成 .psd 文件并嵌入可编辑文字元数据（TySh + EngineData）。

**新增文件（6）：**

| 文件 | 行数 | 移植来源 |
| ------ | ------ | --------- |
| `utils/psd_binary_writer.py` | ~110 | koharu `writer.rs` — 大端二进制累加器 |
| `utils/psd_packbits.py` | ~100 | koharu `packbits.rs` — PackBits RLE 通道编码 |
| `utils/psd_descriptor.py` | ~150 | koharu `descriptor.rs` — Photoshop 描述符 |
| `utils/psd_engine_data.py` | ~300 | koharu `engine_data.rs` — PostScript 式 EngineDict |
| `utils/psd_binary_exporter.py` | ~450 | koharu `export.rs` — PSD 主组装（8BPS 头/图层栈/TySh/RLE） |
| `tests/test_psd_binary.py` | ~250 | 46 个单元测试 |

**修改文件（3）：**

| 文件 | 改动 |
| ------ | ------ |
| `utils/psd_exporter.py` | 工厂支持 `export_method`，默认 `"binary"` |
| `ui/psd_export_dialog.py` | ComboBox 方法选择器（Binary / ExtendScript），动态 info 文字 |
| `ui/mainwindow.py` | 透传导出方法，完成消息区分 |

**已知问题（记录在 `docs/psd_binary_export.md`）：**

1. **文字被栅格化** — TySh/EngineData 已嵌入但 PS 未识别为可编辑文字。可能原因包括 EngineData 结构不完整、Descriptor 格式偏差
2. **背景色块** — 从 result/ 裁剪时框到文字周围背景，未做 alpha 遮罩裁剪
3. **依赖 result/** — 若未渲染结果图，文字层退化为透明占位

**测试：** `python -m pytest tests/test_psd_binary.py -v` 46 passed
