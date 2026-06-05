# 竖排标点符号对齐控制

## 概述

新增全局选项，用于控制竖排文本中标点符号（`，。、·` 等）在字符格内的对齐方式。此前标点始终硬编码为居中渲染，现支持**居中**和**右上**两种对齐模式，以适配东亚竖排排版标准。

## 改动文件

| 文件 | 改动 |
|------|------|
| `utils/fontformat.py` | 新增 `PunctuationAlignment` 枚举和 `FontFormat.punctuation_alignment` 字段 |
| `ui/scene_textlayout.py` | 在 `updateDrawOffsets()` 中以条件判断替换硬编码居中；新增 `setPunctuationAlignment()` |
| `ui/fontformat_commands.py` | 新增 `ffmt_change_punctuation_alignment` 命令 |
| `ui/textitem.py` | 在 `TextBlkItem` 中新增 `setPunctuationAlignment()` |
| `ui/text_advanced_format.py` | 在 `TextAdvancedFormatPanel` 中新增下拉框控件 |
| `translate/zh_CN.ts` | 新增 3 条翻译条目 |

## 实现细节

### 1. 数据模型 — `utils/fontformat.py`

新增枚举：

```python
class PunctuationAlignment(enum.IntEnum):
    Center = 0       # 居中（默认，向后兼容）
    UpperRight = 1   # 右上（东亚排版标准）
```

在 `FontFormat` 数据类中新增字段：

```python
punctuation_alignment: int = PunctuationAlignment.Center
```

该字段会自动被 `FontFormat.params()` 发现，从而被 `funcmaps.py` 的 `build_funcmap` 动态映射到对应的格式化命令。

### 2. 渲染引擎 — `ui/scene_textlayout.py`

核心改动在 `VerticalTextDocumentLayout.updateDrawOffsets()` 方法（约第 483 行）。

**原逻辑**（硬编码居中）：

```python
if char in PUNSET_ALIGNCENTER:
    tbr, br = cfmt.punc_rect(char)
    yoff += (tbr.height() + cfmt.font_metrics.descent() - act_rect[3]) / 2
```

**新逻辑**（条件判断）：

```python
if char in PUNSET_ALIGNCENTER:
    if self.fontformat.punctuation_alignment == PunctuationAlignment.UpperRight:
        xoff = -act_rect[0] + (line_width - act_rect[2])
    else:
        tbr, br = cfmt.punc_rect(char)
        yoff += (tbr.height() + cfmt.font_metrics.descent() - act_rect[3]) / 2
```

**右上对齐的计算依据**：`vertical_force_aligncentel` 分支已将 `yoff = -act_rect[1]` 设置为顶部对齐，因此在 UpperRight 模式下只需将 x 从居中 `(line_width - act_rect[2]) / 2` 覆盖为右对齐 `(line_width - act_rect[2])`（无除法），y 保持顶部对齐即可。

在 `SceneTextLayout` 基类中新增触发重排的方法：

```python
def setPunctuationAlignment(self, value: int):
    self.reLayout()  # 级联调用 updateDrawOffsets()
```

### 3. 格式化命令 — `ui/fontformat_commands.py`

```python
@font_formating(push_undostack=True)
def ffmt_change_punctuation_alignment(param_name, values, act_ffmt, is_global, blkitems, **kwargs):
    restore_cursor = not is_global
    for blkitem, value in zip(blkitems, values):
        blkitem.setPunctuationAlignment(value, restore_cursor=restore_cursor)
```

命名遵循 `ffmt_change_<param_name>` 惯例，由 `funcmaps.py` 自动发现。

### 4. 文本块 — `ui/textitem.py`

```python
def setPunctuationAlignment(self, value, repaint_background=True, ...):
    self.is_formatting = True
    self.fontformat.punctuation_alignment = value
    self.layout.setPunctuationAlignment(value)
    if repaint_background:
        self.repaint_background()
        self.update()
    self.is_formatting = False
```

### 5. UI 控件 — `ui/text_advanced_format.py`

在 `TextAdvancedFormatPanel` 中新增下拉框：

```python
self.punct_align_combobox = SmallComboBox(
    parent=self,
    options=[self.tr("Center"), self.tr("Upper-Right")]
)
```

控件置于 `TextAdvancedFormatPanel`（与行距类型并列），因为标点对齐属于次要排版选项，放在高级面板中可保持主面板简洁。

## 设计决策

- **字段放在 `FontFormat` 而非独立全局配置**：`FontFormat` 既是每个文本块样式的存储，也是全局默认值（通过 `pcfg.global_fontformat`）。放在这里自动获得双重能力——全局默认 + 按样式预设保存，无需额外搭建。
- **仅影响竖排模式**：标点偏移逻辑仅存在于 `VerticalTextDocumentLayout.updateDrawOffsets()`，横排无此需求。
- **默认值为 `Center`**：确保与所有现有配置和预设完全向后兼容。
- **使用 `IntEnum` 而非布尔值**：预留扩展空间（如未来可能添加"左上"等模式）。

## 数据流

```
用户操作下拉框
  → TextAdvancedFormatPanel.on_punct_align_changed()
    → on_format_changed('punctuation_alignment', value)
      → handle_ffmt_change['punctuation_alignment']
        → ffmt_change_punctuation_alignment()
          → TextBlkItem.setPunctuationAlignment(value)
            → fontformat.punctuation_alignment = value
            → SceneTextLayout.setPunctuationAlignment(value)
              → reLayout()
                → updateDrawOffsets()
                  → 读取 fontformat.punctuation_alignment
                  → 设置 xoff / yoff
```

## i18n

| 英文 | 中文 |
|------|------|
| Center | 居中 |
| Upper-Right | 右上 |
| Punctuation Alignment | 标点对齐 |

## 涉及标点集合

来自 `scene_textlayout.py` 中预定义的字符集：

```python
PUNSET_ALIGNCENTER = {'。', '．', '，', '、', '·'}
```

当前仅上述 5 个标点受到对齐控制。如需扩展至 `：；！？` 等，可将其加入 `PUNSET_ALIGNCENTER` 或新增独立的对齐字符集。

## 兼容性

- 默认值 `Center(0)` = 与改动前完全一致的行为
- 旧配置文件缺少该字段时，`nested_dataclass` 装饰器自动填入类默认值
- 旧样式预设加载时同样自动获得默认值
- 撤销/重做栈覆盖该操作
- 描边渲染自动跟随（通过 `_draw_offset` 共享）
