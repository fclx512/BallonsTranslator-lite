# 字体样式局部覆盖 — 技术总结

## 问题描述

在 BallonsTranslator 中，文本框（`TextBlkItem`）底层基于 Qt 的 `QTextDocument`，天然支持逐字符级别的独立字体样式（同一个文本框内可以有不同的字体、字号、粗斜体等）。然而，字体格式面板的控件和文本样式预设（Text Style Presets）在点击应用时，会直接覆盖**整个文本框**的所有文字格式，即使当前只选中了部分文字，也会无视选区，全框覆盖。

目标：改为**智能覆盖**——选中整个文本框时保持全框覆盖，但只选中部分文字时，仅覆盖选中部分的字体样式。

---

## 架构概览

### 核心数据模型

```
utils/fontformat.py ─── FontFormat 数据类
    ├── font_family, font_size, bold, italic, underline
    ├── frgb (前景色), srgb (描边色), stroke_width
    ├── alignment, vertical, line_spacing, letter_spacing
    ├── opacity, shadow_*, gradient_*
    └── _style_name (字体样式名, 如 "Bold", "Regular")

utils/textblock.py ─── TextBlock 数据类
    └── fontformat: FontFormat  (块级默认字体格式)

ui/textitem.py ─── TextBlkItem (QGraphicsTextItem 子类)
    ├── QTextDocument 内部存储逐字符的 QTextCharFormat
    ├── get_fontformat() ── 从当前光标读取格式，合并到 FontFormat
    ├── set_fontformat() ── 将 FontFormat 写入 QTextDocument (块级替换)
    ├── setFontFamily() ── 设置字体家族 (逐片段迭代)
    ├── setFontWeight/Italic/Underline/Color/Size ── 单属性方法
    ├── _before_set_ffmt() ── 格式应用前的光标/选区管理
    └── _after_set_ffmt() ── 格式应用后的光标恢复
```

### 格式变更的两条路径

**路径 A：单独属性变更**（如点击粗体、改变字体家族下拉框）

```
FontFormatPanel.on_param_changed()
  → fontformat_commands.ffmt_change_xxx()
    → TextBlkItem.setFontXxx()
      → _before_set_ffmt(set_selected, restore_cursor)  // 选区管理
      → 执行格式变更
      → _after_set_ffmt(...)                             // 光标恢复
```

- 全局模式 (`is_global=True`): `set_selected=False` → 总是全文档
- 局部模式 (`is_global=False`): `set_selected=True` → 检查是否有文字选区

**路径 B：样式预设应用**（点击预设标签的 → 箭头）

```
TextStyleLabel.on_applybtn_clicked()
  → TextStylePresetPanel.apply_fontfmt 信号
    → SceneTextManager.onFormatTextblks()
      → apply_fontformat(fmt)
        → ApplyFontformatCommand.redo()
          → TextBlkItem.set_fontformat(fmt, set_char_format=True)
            → 整个 QTextDocument 格式被替换
```

---

## 根因分析

### 1. `setFontFamily` 无视选区参数

**文件**: `ui/textitem.py`, 方法 `setFontFamily`（原行号 767-776）

```python
# 原代码
def setFontFamily(self, value, style_name="", repaint_background=True,
                  set_selected=False, restore_cursor=False):
    self.repainting = True
    cursor = self.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)  # ← 强制全选！
    self._doc_set_font_family(value, style_name, cursor)
    ...
```

问题：参数 `set_selected` 和 `restore_cursor` 被声明但**从未使用**。方法内部总是创建一个新光标并选中整个文档，导致 `_doc_set_font_family`（该方法本身已经支持按选区范围过滤片段）收到的光标总是全选状态。

其他同类方法 (`setFontWeight`, `setFontItalic`, `setFontUnderline`, `setFontColor`, `setFontSize`, `setGradientEnabled`) 都正确使用了 `_before_set_ffmt` / `_after_set_ffmt` 模式，唯独 `setFontFamily` 没有。

### 2. `set_fontformat` 总是全文档替换

**文件**: `ui/textitem.py`, 方法 `set_fontformat`（原行号 633-711）

此方法是样式预设"Apply"按钮的最终执行者，被 `ApplyFontformatCommand.redo()` 调用时传入 `set_char_format=True`。

```python
# 原代码关键片段 (行号 670-678)
cursor.setCharFormat(format)
cursor.select(QTextCursor.SelectionType.Document)  # ← 强制全选
cursor.setBlockCharFormat(format)
if set_char_format:
    cursor.setCharFormat(format)  # ← 全选状态下，替换所有字符格式
cursor.clearSelection()
```

问题：无论当前是否有文字选区，总是执行 `cursor.select(Document)` 选中全文档，然后 `setCharFormat` 覆盖所有字符的格式。没有给调用方提供「保留选区」的选项。

---

## 修复方案

### 修改一：`setFontFamily` 使用 `_before_set_ffmt` / `_after_set_ffmt` 模式

**文件**: `ui/textitem.py:778-787`

```python
def setFontFamily(self, value: str, style_name: str = "", repaint_background: bool = True,
                  set_selected: bool = False, restore_cursor: bool = False):
    cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
    self.repainting = True
    self._doc_set_font_family(value, style_name, cursor)
    self.repainting = False
    if not set_selected or after_kwargs['has_set_all']:
        self.fontformat.font_family = value          # 仅全框覆盖时更新块级格式
        if style_name:
            self.fontformat._style_name = style_name
    self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)
```

**关键点**：

| 场景 | `set_selected` | `has_selection()` | `has_set_all` | 光标行为 | `fontformat` 更新 |
|------|:---:|:---:|:---:|------|:---:|
| 全局模式 | False | — | False | 创建新光标，选全文档 | ✅ 是 |
| 局部模式，无文字选区 | True | False | True | 光标扩展为全文档 | ✅ 是 |
| **局部模式，有文字选区** | **True** | **True** | **False** | **保持原选区** | **❌ 否** |

- `_before_set_ffmt(set_selected, restore_cursor)` 返回的 cursor 携带正确的选区范围
- `_doc_set_font_family` 内部已按 `cursor.selectionStart()` / `cursor.selectionEnd()` 过滤片段——无需修改
- 只有当格式应用到**整个文本框**时才更新 `self.fontformat.font_family`（块级默认格式），局部覆盖时保留原有块级默认值
- `_after_set_ffmt` 负责光标位置恢复、背景重绘、`endEditBlock()` 收尾

### 修改二：`set_fontformat` 支持选区感知

**文件**: `ui/textitem.py:633-689`

```python
def set_fontformat(self, ffmat: FontFormat, set_char_format=False,
                   set_stroke_width=True, set_effect=True, respect_selection=False):
    ...
    after_kwargs = None
    if respect_selection:
        cursor, after_kwargs = self._before_set_ffmt(set_selected=True, restore_cursor=True)
    else:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)  # 原有逻辑
    ...
    # 格式模板构建 (font, format 配置) 不变
    ...
    if respect_selection:
        cursor.setCharFormat(format)
        cursor.setBlockCharFormat(format)
        if set_char_format:
            cursor.setCharFormat(format)           # 仅作用于选区
        self._after_set_ffmt(cursor, repaint_background=False,
                            restore_cursor=True, **after_kwargs)
    else:
        cursor.setCharFormat(format)
        cursor.select(QTextCursor.SelectionType.Document)  # 原有全选逻辑
        cursor.setBlockCharFormat(format)
        if set_char_format:
            cursor.setCharFormat(format)
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
    ...
```

**新增参数** `respect_selection=False`：

- `False`（默认）：行为与原代码完全一致，全框覆盖
- `True`：使用 `_before_set_ffmt` 获取选区感知的光标，字符级属性（字体家族、字号、粗斜体、颜色等）仅作用于选区，块级属性（对齐、行距、描边、阴影、透明度等）仍作用于全框

### 修改三：`ApplyFontformatCommand` 传递选区标志

**文件**: `ui/textedit_commands.py:84-87`

```python
def redo(self):
    for item, edit in zip(self.items, self.trans_widget_lst):
        item.set_fontformat(self.new_fmt, set_char_format=True,
                           respect_selection=True)   # ← 新增参数
        edit.document().clearUndoRedoStacks()
```

`undo()` 不传 `respect_selection=True`，因为撤销操作通过 `setHtml()` 恢复全框内容，必须全量替换。

---

## 未修改的路径

以下方法**无需修改**，因为它们要么已经正确使用选区，要么是块级属性不应受选区影响：

| 方法 | 状态 | 说明 |
|------|:---:|------|
| `setFontWeight` | ✅ 已正确 | 使用 `_before_set_ffmt(set_selected, restore_cursor)` |
| `setFontItalic` | ✅ 已正确 | 同上 |
| `setFontUnderline` | ✅ 已正确 | 同上 |
| `setFontColor` | ✅ 已正确 | 同上 |
| `setFontSize` | ✅ 已正确 | 同上 |
| `setGradientEnabled` | ✅ 已正确 | 同上 |
| `setStrokeWidth` | ⏭️ 跳过 | 块级属性，硬编码 `set_selected=False` |
| `setAlignment` | ⏭️ 跳过 | 块级属性，硬编码 `set_selected=False` |
| `setLineSpacing` | ⏭️ 跳过 | 布局级属性，通过 `self.layout.setLineSpacing()` 设置 |
| `setLetterSpacing` | ⏭️ 跳过 | 布局级属性，对竖排通过 layout 设置，横排全框覆盖 |
| `setOpacity` | ⏭️ 跳过 | QGraphicsItem 级别的特效，不是逐字符属性 |
| `setRelFontSize` | ⏭️ 跳过 | 相对字号缩放，逐片段迭代但未过滤选区（低频操作，不影响主要体验） |

---

## `_before_set_ffmt` / `_after_set_ffmt` 详解

这两个方法是本次修复的核心机制，理解它们有助于后续扩展。

### `_before_set_ffmt(set_selected, restore_cursor)`

```python
def _before_set_ffmt(self, set_selected: bool, restore_cursor: bool):
    self.is_formatting = True
    cursor = self.textCursor()

    cursor_pos = None
    if restore_cursor:
        cursor_pos = (cursor.position(), cursor.anchor().__pos__())

    if set_selected:
        has_set_all = not cursor.hasSelection()
        if has_set_all:
            cursor.select(QTextCursor.SelectionType.Document)
    else:
        has_set_all = False
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)

    cursor.beginEditBlock()
    return cursor, dict(cursor_pos=cursor_pos, has_set_all=has_set_all)
```

**返回值**：
- `cursor`: 带有正确选区范围的 QTextCursor
- `after_kwargs`: `{cursor_pos: (pos, anchor) | None, has_set_all: bool}`
  - `cursor_pos` 在 `restore_cursor=True` 时为原始光标位置元组，用于后续恢复
  - `has_set_all` 为 `True` 时表示光标无文字选区（或 `set_selected=False` 全框模式……注意例外）

**关键语义区分**：

`has_set_all` 的语义是"光标从无选区被扩展为全选"，**不是**"格式是否应用到全文档"。当 `set_selected=False` 时，即便光标确实选中了全文档，`has_set_all` 也被设为 `False`。因此判断"格式是否应用到全框"应使用：

```python
is_full_block = not set_selected or has_set_all
```

### `_after_set_ffmt(cursor, repaint_background, restore_cursor, cursor_pos, has_set_all)`

```python
def _after_set_ffmt(self, cursor, repaint_background, restore_cursor,
                    cursor_pos, has_set_all):
    if restore_cursor:
        if cursor_pos is not None:
            pos1, pos2 = cursor_pos
            if has_set_all:
                cursor.setPosition(pos1)                        # 恢复单点光标
            else:
                cursor.setPosition(min(pos1, pos2))
                cursor.setPosition(max(pos1, pos2), KeepAnchor) # 恢复选区
            self.setTextCursor(cursor)

    if repaint_background:
        self.repaint_background()

    cursor.endEditBlock()
    self.is_formatting = False
```

---

## 调用链全貌

```
用户点击字体格式控件 (如 Bold 复选框 / Font Family 下拉框)
│
├── 路径 A: 单独属性变更
│   FontFormatPanel.on_param_changed(param_name, value)
│   │
│   ├── global_mode() == True
│   │   └── func(..., is_global=True)
│   │       └── set_kwargs = {set_selected: False, restore_cursor: False}
│   │           └── TextBlkItem.setFontXxx(value, set_selected=False, ...)
│   │               └── _before_set_ffmt(False, False)
│   │                   └── 新建 cursor, 全选 Document → 全框覆盖 ✅
│   │
│   └── global_mode() == False
│       └── func(..., is_global=False, blkitems=[textblk_item])
│           └── set_kwargs = {set_selected: True, restore_cursor: True}
│               └── TextBlkItem.setFontXxx(value, set_selected=True, ...)
│                   └── _before_set_ffmt(True, True)
│                       ├── 有文字选区 → 保持选区 → 仅覆盖选区 ✅ (修复后)
│                       └── 无文字选区 → 扩展为全选 → 全框覆盖 ✅
│
└── 路径 B: 样式预设 Apply
    TextStyleLabel.on_applybtn_clicked()
    └── apply_fontfmt.emit(fontfmt)
        └── SceneTextManager.onFormatTextblks(fmt)
            └── apply_fontformat(fmt)
                └── ApplyFontformatCommand(items, ...)
                    ├── redo(): set_fontformat(fmt, set_char_format=True, respect_selection=True)
                    │   └── _before_set_ffmt(True, True)
                    │       ├── 有文字选区 → 字符属性仅覆盖选区 ✅ (修复后)
                    │       └── 无文字选区 → 全框覆盖 ✅
                    │
                    └── undo(): setHtml(html) + set_fontformat(fmt)
                        └── 全框恢复 (不传 respect_selection)
```

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `ui/textitem.py:778-787` | `setFontFamily` 重构为 `_before_set_ffmt` / `_after_set_ffmt` 模式 |
| `ui/textitem.py:633-689` | `set_fontformat` 新增 `respect_selection` 参数和选区感知分支 |
| `ui/textedit_commands.py:84-87` | `ApplyFontformatCommand.redo` 传递 `respect_selection=True` |
