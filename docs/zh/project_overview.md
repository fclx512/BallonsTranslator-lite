# BallonsTranslator-lite — 项目概述

目标读者：无法直接访问代码库的 AI 助手。
目的：了解本项目的架构、UI 约定和约束条件，
以便为 UI 改进提供有依据的建议。

---

## 1. 这是什么项目

一款用于翻译漫画/图片的桌面应用程序。管线包含四个阶段：
文字检测 → OCR → 翻译 → 图像修复 → 文字渲染。

基于 **PyQt6** 构建，支持 Windows/macOS/Linux。

---

## 2. 技术栈

| 层级 | 技术 |
|-------|-----------|
| GUI 框架 | PyQt6（通过 `qtpy` 兼容层） |
| 语言 | Python 3.10+ |
| 样式 | CSS 样式表（`config/stylesheet.css`），支持 `@variable` 占位符，运行时解析 |
| 动画 | `QPropertyAnimation` 封装于 `ui/overlay_slide.py`（`OverlaySlider`） |
| 异步 | 基于 `QThread` 的工作线程，用于 LLM 调用和管线执行 |
| 国际化 | Qt Linguist：`self.tr("English")` → `.ts` → `.qm` |

---

## 3. 应用结构

```
BallonsTranslator-lite/
├── launch.py                  入口点
├── modules/                   管线模块（OCR、翻译器、检测器、修复器）
│   ├── base.py                BaseModule、模块发现、设备检测
│   ├── textdetector/          文字检测模块
│   ├── ocr/                   OCR 模块
│   ├── translators/           翻译模块
│   └── inpaint/               图像修复模块
├── utils/
│   ├── config.py              ProgramConfig 数据类、加载/保存
│   ├── proj_imgtrans.py       项目管理（页面、文字块、撤销栈）
│   ├── textblock.py           核心数据单元（坐标、原文、译文、字体、遮罩）
│   ├── registry.py            模块注册装饰器模式
│   ├── ai_controller.py       AI 助手控制器（基于信号，无 widget 耦合）
│   ├── ai_tools.py            AI 工具执行
│   ├── ai_prompts.py          LLM 提示词模板
│   └── proj_compact.py        项目序列化（用于 LLM 上下文）
├── ui/
│   ├── mainwindow.py          主窗口
│   ├── io_thread.py           管线编排
│   ├── scene_textlayout.py    画布文字渲染
│   ├── overlay_slide.py       OverlaySlider — 滑入/滑出动画辅助工具
│   ├── ai_chat_panel.py       AI 聊天滑入面板
│   ├── ai_chat_model.py       ChangeItem、ChatMessage 数据类（无 Qt 依赖）
│   ├── ai_chat_worker.py      LLM API 调用线程
│   ├── ai_change_review.py    变更审查对话框（独立窗口）
│   └── misc.py                共享工具函数
├── config/
│   ├── stylesheet.css         全局样式表，含 @variable 占位符
│   ├── themes.json            主题定义
│   └── textstyles/            字体样式预设
├── translate/
│   ├── zh_CN.ts               中文翻译源文件
│   └── zh_CN.qm               编译后的翻译文件
├── docs/
│   ├── README.md              文档结构说明
│   ├── en/                    英文文档
│   └── zh/                    中文文档
```

---

## 4. UI 架构模式

### 4.1 主窗口布局

`MainWindow` 采用侧边栏 + 主内容区布局：
- **左侧栏**：工具按钮（StateChecker — QCheckBox 子类，实现面板互斥切换）
- **中央**：画布区域，用于图片预览和文字编辑
- **右侧面板容器**：承载滑入面板（AI 聊天、配置等）

### 4.2 滑入面板

所有浮动面板均使用 `ui/overlay_slide.py` 中的 `OverlaySlider`：
- 固定宽度（例如 AI 聊天为 480px）
- 从左侧或右侧边缘滑入
- 350ms 动画，`InOutExpo` 缓动函数
- 提供 `show()`、`hide()`、`resize()` API

### 4.3 独立对话框

部分 UI 组件为独立的 `QDialog` 子类（非模态）：
- `ChangeReviewWindow` — AI 变更审查
- 配置面板设置对话框

这些组件使用标准 `QDialog` 布局，根布局为 `QVBoxLayout`。

### 4.4 Widget 组合模式

UI 类遵循以下模式：
1. `__init__` 调用 `_build_ui()` 组装所有 widget
2. 内部状态存储为实例变量（以 `_` 为前缀）
3. 信号在类级别定义，用于外部通信
4. 通过 `setObjectName()` 设置样式，与 stylesheet.css 中的 CSS 选择器匹配

示例：
```python
class MyPanel(QWidget):
    my_signal = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MyPanel")
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        # ... 构建子 widget ...
        btn = QPushButton(self.tr("Do Thing"))
        btn.setObjectName("MyActionBtn")
        btn.clicked.connect(self._on_action)
        root.addWidget(btn)
```

### 4.5 样式系统

`config/stylesheet.css` 中所有样式使用 `@variable` 占位符：
```css
#MyWidget {
    background-color: @widgetBackgroundColor;
    border: 1px solid @borderColor;
    border-radius: 6px;
    color: @qwidgetForegroundColor;
    font-size: 13px;
    padding: 8px;
}
```

变量由 `ui/misc.py` 中的 `parse_stylesheet()` 在运行时解析。
可用变量包括：`@widgetBackgroundColor`、`@emptyContentBackgroundColor`、
`@accentTranslate`、`@accentPrimary20`、`@borderColor`、`@successColor`、
`@dangerColor`、`@qwidgetForegroundColor`、`@inverseTextColor` 等。

约定：对象名称使用帕斯卡命名法，并带项目前缀，例如 `#AIReviewCard`、
`#AIChatPanel`、`#AIChangeReviewWindow`。

### 4.6 国际化

所有用户可见字符串必须使用 `self.tr("...")` 包裹：
```python
label = QLabel(self.tr("Accept All"))
```

例外情况：日志消息、LLM 提示词、字体预览字符串、语言映射字典。

---

## 5. AI 助手子系统

### 5.1 架构

信号驱动，数据/控制器层无 widget 耦合：

```
AI Chat Panel (UI)
    ↕ 信号
AiController (编排)
    ↕
AiChatWorker (LLM API 线程)
    ↕
ai_tools.py (工具执行)
proj_compact.py (项目序列化)
```

### 5.2 数据模型（`ui/ai_chat_model.py`）

**`ChangeItem`** — 单字段级别的修改：
- `block_id: str` — 文字块标识符（格式：`"<page>:<block>"`）
- `field: str` — 修改的属性（src、trans、ff、fs、fg、bg、b、i、a、sw、ls）
- `old_value: Any` — 原始值
- `new_value: Any` — 提议值
- `accepted: Optional[bool]` — 三态：None=待定、True=已接受、False/other=已拒绝
- `src_text: str` — 用于显示的原文

**`ChatMessage`** — 对话中的一条消息：
- `role: str` — "user"、"assistant" 或 "system"
- `content: str` — 消息文本
- `changes: List[ChangeItem]` — 提议变更的 assistant 消息非空
- `segments: List[Dict]` — 用于历史重建的显示片段

### 5.3 字段类型及其含义

| 字段 | 含义 | 值类型 |
|-------|---------|-----------|
| `src` | 原文（原始文本） | `str` |
| `trans` | 译文 | `str` |
| `ff` | 字体系列 | `str` |
| `fs` | 字号 | `int` 或 `float` |
| `fg` | 字体颜色 | `str`（十六进制或颜色名称） |
| `bg` | 背景颜色 | `str` |
| `b` | 粗体 | `bool` |
| `i` | 斜体 | `bool` |
| `a` | 对齐方式 | `str` 或 `int` |
| `sw` | 描边宽度 | `int` 或 `float` |
| `ls` | 行间距 | `int` 或 `float` |

### 5.4 变更审查流程

1. AI 提议变更 → `AiChatPanel.set_changes()` 在聊天中构建变更卡片
2. 发出 `open_review_requested` 信号
3. `MainWindow` 创建/显示 `ChangeReviewWindow`
4. 审查窗口按页面分组变更，在 UI 中展示
5. 用户逐项或批量接受/拒绝
6. 点击"Apply Changes" → 发出 `apply_changes_requested` 信号
7. `MainWindow._on_apply_ai_changes()` 将接受的变更应用到 `TextBlock` 对象

### 5.5 审查面板必须处理的场景

| 场景 | 变更内容 | 展示内容 |
|----------|-------------|-------------|
| 仅翻译 | `trans` | 原文 → 新译文 |
| 重新翻译 | `trans`（旧值非空） | 原文 → 旧译文 + 新译文 |
| 仅字体样式 | `ff`、`fs`、`fg`、`b`、`i` 等 | 原文 + 字体预览（旧 → 新） |
| 混合 | `trans` + 样式字段 | 原文 → 译文对比 + 字体预览 |
| 原文重写 | `src` + `trans` | 旧原文 → 新原文，旧译文 → 新译文 |

---

## 6. 关键设计约束

1. **禁止硬编码中文** — 所有 UI 文本通过 `self.tr()` 处理
2. **样式表变量** — 禁止硬编码颜色，始终使用 `@variable` 引用
3. **基于信号的解耦** — 数据/模型层不得导入 Qt widget
4. **`qtpy` 兼容** — 使用 `qtpy` 导入，而非直接 `PyQt6` 导入
5. **对象命名约定** — 帕斯卡命名法，带有有含义的前缀（AI、Config 等）
6. **配置持久化** — `pcfg` 是模块级单例；修改后必须显式调用 `save_config()`
7. **模块注册** — 管线模块通过装饰器自动注册，启动时发现

---

## 7. UI 样式参考

现有 UI 使用以下视觉模式：
- **卡片**：圆角（6-8px）、边框、内边距 8-12px
- **按钮**：扁平带边框、悬停背景色变化、高度 26-36px
- **滚动区域**：细滚动条、无边框
- **文字**：基础字号 13px，标签启用自动换行
- **间距**：兄弟 widget 之间 6-8px，容器外边距 12px
- **分隔线**：细 QFrame 线条，高度 1-2px
