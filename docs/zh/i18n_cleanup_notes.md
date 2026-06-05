# i18n 清理指南（面向 AI 代理）

本文档记录了 i18n 清理过程中的模式、陷阱和设计决策，以便未来的 AI 代理避免重复相同的工作。

## 当前状态（2026 年 5 月）

| 检查项 | 数量 | 说明 |
|-------|-------|-------|
| 硬编码中文 | 0 | 已全部处理 |
| 缺失 .ts 条目 | 0 | 已完全同步 |
| 孤立条目（报告数） | 37 | **均为误报** — 见下文 |
| .ts 消息总数 | 639 | 编译通过 |
| 语言数量 | 2 | 英语 + zh_CN |

## 37 个"孤立条目"均为误报

i18n_check.py 的孤立条目检测通过正则表达式扫描 Python 源码中的
`self.tr("literal string")`。它无法检测到：

### 1. 基于变量的 tr() — 27 个条目

| 上下文 | 数量 | 模式 |
|---------|-------|---------|
| `ThreadBase` | 6 | `self.tr(self._thread_error_msg)` — 在子类中赋值的类变量 |
| `_ShortcutRow` | 21 | `self.tr(_ACTION_NAMES.get(action_id, action_id))` — `ui/configpanel.py` 中的字典查找 |

**运行时均正常工作。** Qt 解析字符串值并在 .qm 中按正确上下文查找。正则表达式只是因为字符串不是字面量（间接引用）而无法看到字面量。

### 2. 多行 tr() — 9 个条目

| 上下文 | 数量 | 模式 |
|---------|-------|---------|
| `UpdateThread` | 2 | `self.tr('line1\nline2')` — 字符串跨 2 行源码 |
| `ProfileManagerDialog` | 1 | `self.tr("Enable this for models..."\n"Vision-capable...")` — 跨行隐式字符串拼接 |
| `ImgtransThread` | 1 | `self.tr(' is required for ' + self.translator.name)` — 与变量拼接 |

正则表达式 `self\.tr\("((?:[^"\\]|\\.)*)"\)` 仅匹配单行字符串。
多行或拼接参数对它不可见。

### 决定：保留全部 37 个

它们会产生退出码 4（孤立条目位）。切勿清除这些条目：
- 它们位于正确的 .ts 上下文中，并在运行时正确解析。
- 清除它们会破坏快捷键、错误消息和进度文本的翻译。

## 关键模式总结

### 类重命名时，需更新 .ts 上下文

如果 `class OldName` 重命名为 `class NewName`，则其 .ts 中所有位于
`<name>OldName</name>` 下的消息必须移至 `<name>NewName</name>`。否则
翻译将静默失效——Qt 通过类名查找。

这发生在以下类中：
- `ShortcutEditor` → `_ShortcutRow`（27 条操作名称消息）
- `AiChatPanel` → `ChangeReviewWindow`（2 条消息）
- `FontFormatPanel` → `ConfigPanel`（"Effect"）
- `TranslateThread` → `ImgtransThread`（" is required for "）
- `ProgressMessageBox` → `ImgtransProgressMessageBox`（4 条进度消息）

### .ts 中的重复上下文块

`ts_auto_fill.py` 中的 `_normalize_ts()` 函数会合并具有相同 `<name>`
的重复 `<context>` 块。`MainWindow` 曾有两个独立的上下文块（61 + 11 条消息），
第一个块中的条目被静默丢弃。

### 包含 `{` 的格式化字符串自动跳过

`ts_auto_fill.py` 中的正则 `_extract_tr_calls` 会跳过包含 `{` 的字符串。
这些被视为 Python 格式化字符串。如果确实需要翻译，
必须手动添加 .ts 条目。

### 模块参数描述

`utils/config.py` 中含有 `description` 字段的模块参数
在运行时使用 `self.tr(variable_name)`（description 的值被传递给 tr()）。
正则表达式无法检测到这些。它们在 .ts 中位于 `<context>ParamWidget</context>`
下，并明确排除在孤立条目检测之外。

## 已完成的工作

### 第 1 批：填充缺失条目
`python scripts/ts_auto_fill.py --fill-missing --apply`
添加了 24 条缺失的 `self.tr()` 条目，标记为 `type="unfinished"`。

### 第 2 批：删除真正的孤立条目 + 移动上下文条目
创建了 `scripts/_ts_cleanup.py`（使用后已删除），该脚本：
- 删除了 56 条真正孤立的消息（类/代码已移除）
- 将 31 条消息移至正确上下文
- 移除了 9 个空上下文块

### 第 3 批：处理硬编码中文
`ui/` 中的 49 个字符串：

| 操作 | 数量 | 类别 |
|--------|-------|----------|
| 包裹 `self.tr()` | 7 | API 错误消息（ai_chat_worker.py） |
| 改为英文 | 12 | LOGGER + 错误数据（io_thread.py） |
| 将文档字符串改为英文 | 16 | 单行文档字符串 |
| 添加排除项 | 11 | 字体测试字符"啊""木""木fg"，CJK 范围"一""鿿"，语言参数"简体中文" |
| 通过检查器修复跳过 | 4 | 改进了单行文档字符串检测 |

### i18n_check.py 改进
- 为字体测试字符和 CJK 范围检查添加了 `NON_UI_PATTERNS` 集合
- 添加了单行三引号文档字符串跳过逻辑
- 控制台编码：在 Windows 上将标准输出重新配置为 UTF-8（之前报 `UnicodeEncodeError`）

## 运行流程

### 添加新的 tr() 调用

```bash
python scripts/ts_auto_fill.py --fill-missing --apply
# 然后编辑 zh_CN.ts 为 type="unfinished" 条目填写翻译
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm
python scripts/i18n_check.py
```

### 清除孤立条目

```bash
python scripts/ts_auto_fill.py --prune           # 试运行
python scripts/ts_auto_fill.py --prune --apply    # 执行写入
```

### 完整 CI 检查

```bash
python scripts/i18n_check.py --ci
```

若发现任何问题则返回非零退出码：
- bit 1：硬编码中文
- bit 2：缺失 .ts 条目
- bit 4：孤立 .ts 条目

当前预期状态：`i18n_check.py --ci` 退出码为 4（仅孤立条目，均为误报）。
