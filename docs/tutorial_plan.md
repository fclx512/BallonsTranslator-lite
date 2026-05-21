# BallonsTranslator-lite 教程大纲

> 本文档规划了本项目所需的教程文档结构，待后续逐步细化。

## 一、现状

| 文档 | 状态 |
|------|------|
| `config_reference.md` | ✅ 已完成 |
| `how_to_add_new_translator.md` | ✅ 已重写为大纲，适配本 fork `modules/translators/` 架构（含 OCR/检测/修复） |
| `加别的翻译器.md` | ✅ 已重写为大纲，中文版同步 |
| `tutorial_plan.md` | ✅ 本规划文件 |
| 根目录 `README.md` / `README_EN.md` | ⚠️ 存在但未审查 |
| `user_guide_CN.md` | ❌ 未开始（用户操作手册） |
| `dev_guide.md` | ❌ 未开始（开发者指南） |

---

## 二、待写文档

### 1. `user_guide_CN.md` — 用户操作手册

**目标读者**：不懂编程的普通用户。

| 章节 | 内容 |
|------|------|
| 1. 安装与启动 | 下载、Python 环境、`python launch.py`、`--cpu` 参数说明 |
| 2. 界面导览 | 主窗口布局：画布区 / 左侧工具栏 / 右侧编辑面板 / 底栏，截图标注 |
| 3. 四步管线流程 | 打开项目 → 文本检测 → OCR → 翻译 → 修复 → 导出 |
| 4. 手动编辑文字 | 选择文本块、编辑翻译结果、文字样式（字体/字号/颜色/描边/阴影/渐变） |
| 5. 画笔与修复工具 | Draw Board 画笔、Inpaint 涂抹修复、Rect Tool 区域选择 |
| 6. 区域合并工具 | 什么是合并、合并模式、标签策略、参数说明 |
| 7. 快捷键一览 | 所有快捷键列成表（可引用 config_reference.md 的快捷键章节） |
| 8. 常见问题 | 如何切语言、如何改配置、日志怎么看 |

**截图素材**：需重新截图本 fork 的 UI（老截图已删除）。

---

### 2. `how_to_add_new_translator.md` — 重写

**目标读者**：有 Python 基础、想自己加翻译器的开发者。

写翻新，当前文档需修正的问题：

| 原文档内容 | 应改为 |
|------------|--------|
| `ballontranslator/dl/translators/__init__.py` | `modules/translators/trans_xxx.py`（独立文件，自动注册） |
| `@register_translator('name')` | 保留（注册机制未变） |
| `params` 字典格式 | 补充新控件类型：`check_group`、`pushbtn`、`editor` 等 |
| 引用 `LANGMAP_GLOBAL` | 改为 `lang_map` 字典在 `_setup_translator` 中赋值 |
| `concate_text` 说明 | 保留 |
| `tests/test_translators.py` | 确认该文件是否仍存在，若不存在则移除引用 |
| 多语言翻译版本链接 | 删除 PT-BR/ES/RU 链接（已清理） |

**可选补充章节**：

- 如何添加 OCR / 文本检测 / 修复模块（不止翻译器）
- 模块注册机制总览（`@register_translator` / `@register_ocr` / `@register_textdetector` / `@register_inpainter`）
- 新模块的 i18n 注意事项（description 用英文，翻译加到 ParamWidget context）

---

### 3. `加别的翻译器.md` — 重写

与 `how_to_add_new_translator.md` 同步更新，中文版本。

删掉旧的 PT-BR / ES / RU 多语言切换链接（已清理）。

---

### 4. `dev_guide.md` — 开发者指南（可选，优先级最低）

**目标读者**：想深入理解项目架构或贡献代码的开发者。

| 章节 | 内容 |
|------|------|
| 1. 项目架概述 | 四步管线 + 模块注册器 + 配置系统 |
| 2. 模块系统详解 | `BaseModule`、`init_module_registries()`、文件命名约定、DEVICE_SELECTOR |
| 3. params 字典完全指南 | 所有控件类型 + 字段含义 + 示例（引用 config_reference.md 附录） |
| 4. 配置系统 | `ProgramConfig` dataclass、`pcfg` 全局单例、配置持久化 |
| 5. i18n 规范 | 引用 CLAUDE.md 中的 UI i18n Checklist |
| 6. 数据流 | 从打开项目 → 四步管线 → 画布渲染的完整路径 |

---

## 三、写入顺序建议

```
优先级 P0 (核心用户文档)
  └── user_guide_CN.md     ← 下一个写

优先级 P1 (开发者入口文档)
  └── how_to_add_new_translator.md     ← 重写（当前的教程链接项目结构不对）
  └── 加别的翻译器.md                    ← 同步重写

优先级 P2 (内部参考, 不紧急)
  └── dev_guide.md
```
