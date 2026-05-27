# UI 交互优化与问题修复计划

> 生成日期：2026-05-27

## 概述

对 BallonsTranslator-lite 现有 UI 层进行全面审查后，发现 3 个 bug、若干交互缺失、以及主题系统/响应式设计方面的技术债。本计划按优先级分三轮实施。

---

## 第一轮：Bug 修复 + 高优先级 UX

### 1.1 修复 `only_custom` NameError（Bug）

- **文件**：`ui/mainwindow.py:653-668`
- **问题**：`load_textstyle_from_proj_dir()` 在 line 665 使用 `only_custom` 变量，但函数签名是 `(self, from_proj=False)`，该变量从未定义
- **修复方案**：移除 `only_custom` 分支逻辑，直接使用 `shared.ALL_FONT_FAMILIES`；或如果确实需要区分，给函数增加 `only_custom: bool = False` 参数

### 1.2 修复 `closeEvent` UI 冻结（Bug）

- **文件**：`ui/mainwindow.py:771-782`
- **问题**：`closeEvent` 中用 `while True: time.sleep(0.1)` 忙等保存线程完成，期间冻结整个 UI
- **修复方案**：
  - 监听 `imsave_thread.finished` 信号，在回调中执行 `save_config()` + `super().closeEvent(event)`
  - 在等待期间显示一个轻量级的进度提示（或直接信任线程速度足够快，改为 `QApplication.processEvents()` 保留最小响应性）

### 1.3 修复 `on_merge_finished` 裸 except（Bug）

- **文件**：`ui/mainwindow.py:1184`
- **问题**：`except: pass` 吞掉所有异常
- **修复方案**：改为 `except Exception as e: print(f"merge reload error: {e}")` 或用项目日志记录

### 1.4 AI 聊天工具执行进度显示

- **文件**：`ui/ai_chat_panel.py`、`utils/ai_controller.py`
- **现状**：用户只能看到 "处理中..." 但不知道哪个工具在执行、还剩几个 turn
- **方案**：
  - `AiController` 新增信号 `tool_progress(current_tool: str, turn: int, max_turns: int)`
  - `AiChatPanel` 在聊天流中插入一行进度指示："执行 search_replace (3/10)..."

### 1.5 错误重试按钮

- **文件**：`ui/ai_chat_panel.py`
- **现状**：API 错误后用户需手动重新输入消息
- **方案**：在错误系统消息下方添加"重试"按钮，点击后自动重新发送最后一条用户消息

### 1.6 变更审查 "Reject All" 按钮

- **文件**：`ui/ai_change_review.py`
- **现状**：未 Accept 的项目隐式被拒绝，用户无法显式拒绝全部
- **方案**：在 Apply 按钮旁添加 "Reject All" 按钮，点击后清空所有 ChangeItem

### 1.7 工具执行阶段取消支持

- **文件**：`utils/ai_controller.py`、`ui/ai_chat_panel.py`
- **现状**：取消只对 stream 有效，工具同步执行期间无法中断
- **方案**：在工具执行前检查 `AiChatWorker.cancel_requested` 标志，如果已取消则中断工具循环

---

## 第二轮：主题系统清理

### 2.1 硬编码颜色 → 主题变量

- **文件**：`config/stylesheet.css`
- **问题**：50+ 处硬编码颜色绕过 `@variable` 系统，尤其是 `rgb(30, 147, 229)`（蓝色强调色）在 ~25 处使用
- **方案**：
  - 在 `themes.json` 中新增 `@accent`、`@accent-hover`、`@accent-light`、`@delete`、`@delete-hover`、`@accept`、`@accept-hover` 等变量
  - 将 CSS 中对应颜色替换为变量引用
  - ember 主题使用橙色系替代蓝色系

### 2.2 `theme_helpers.py` 对接主题系统

- **文件**：`ui/theme_helpers.py`
- **问题**：`shortcut_styles()` 和 `scrollbar_colors()` 维护独立的硬编码颜色字典，与主题系统脱节
- **方案**：从当前活动主题字典读取颜色，删除硬编码字典

### 2.3 主题选择器 UI

- **文件**：`ui/configpanel.py`、`utils/config.py`
- **现状**：`ProgramConfig.theme_name` 存在但无 UI 入口，用户只能通过修改 `darkmode` 布尔值切换
- **方案**：在 ConfigPanel 的 Startup 页面添加 `QComboBox` 主题选择器，列出 4 个预置主题，选择后调用 `set_stylesheet()` 实时预览

---

## 第三轮：响应式设计 + 交互细节

### 3.1 面板宽度自适应

- **硬编码位置**：
  - `ui/mainwindow.py:241` — 右侧翻译栈 `setFixedWidth(360)`
  - `ui/ai_chat_panel.py:113` — AI 面板 `setFixedWidth(480)`
  - `ui/ai_chat_panel.py:1408` — 气泡最大宽度 `min(460, ...)`
  - `ui/mainwindow.py:622` — PageList 宽度 `PAGE_LIST_WIDTH = 250`
- **方案**：
  - 改为 `setMinimumWidth()` + `setMaximumWidth()` 允许用户拖拽调整
  - 或按屏幕宽度比例设置（`min(480, screen_width * 0.3)`）

### 3.2 项目加载进度指示

- **文件**：`ui/mainwindow.py:680` (`openDir`)
- **方案**：在 `openDir` 开始时显示一个 `QProgressBar` 或忙碌指示器，加载完成后隐藏

### 3.3 覆盖面板集中管理

- **现状**：4 个 `OverlaySlider`（ConfigPanel、GlobalSearch、PageList、AiChat）的互斥逻辑散落在各处
- **方案**：新建 `OverlayManager` 类，维护当前活跃面板引用，`show(panel)` 时自动隐藏其他面板，统一管理 z-order 和动画

### 3.4 动画曲线调整

- **文件**：`ui/overlay_slide.py:50`
- **现状**：`InOutExpo` 曲线在 350ms 时长下可能感觉突兀
- **方案**：改为 `OutQuad` 或 `InOutCubic`，可考虑在 ConfigPanel 中增加 `duration` 参数允许自定义

### 3.5 变更审查表格列宽自适应

- **文件**：`ui/ai_change_review.py:189`
- **现状**：列宽硬编码为 `80, 280, 40`
- **方案**：使用 `QHeaderView.setSectionResizeMode()` 的 `Stretch` 或 `ResizeToContents` 模式

---

## 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| closeEvent 重构可能引入退出时崩溃 | 高 | 在各种场景（空项目、大项目、AI 对话进行中）测试关闭行为 |
| 主题变量替换影响面广 | 中 | 逐一测试 4 个主题下所有 UI 元素颜色一致性 |
| 面板宽度自适应可能导致布局错乱 | 低 | 设置合理的 min/max 约束，测试不同分辨率 |
| 工具执行取消可能中断 LLM 对话状态 | 中 | 取消后重置 controller 状态，确保下次对话可正常发起 |

---

## 关键文件索引

| 文件 | 涉及改动 |
|------|----------|
| `ui/mainwindow.py` | Bug 修复（1.1, 1.2, 1.3）、面板宽度（3.1）、加载进度（3.2） |
| `ui/ai_chat_panel.py` | 进度显示（1.4）、重试按钮（1.5）、取消（1.7）、面板宽度（3.1） |
| `ui/ai_change_review.py` | Reject All（1.6）、列宽自适应（3.5） |
| `utils/ai_controller.py` | 进度信号（1.4）、取消支持（1.7） |
| `config/stylesheet.css` | 颜色变量替换（2.1） |
| `config/themes.json` | 新增 accent 等颜色变量（2.1） |
| `ui/theme_helpers.py` | 对接主题系统（2.2） |
| `ui/configpanel.py` | 主题选择器（2.3） |
| `ui/overlay_slide.py` | 动画曲线（3.4） |
