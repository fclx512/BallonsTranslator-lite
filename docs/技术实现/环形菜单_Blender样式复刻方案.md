# 环形菜单 Blender 样式复刻方案

> 目标：把当前 `ui/pie_menu.py` 的视觉从“带大圆盘的自定义菜单”改成 Blender 的透明扇区菜单。
> 状态：已实施完毕（2026-08-11，验证清单全部通过）。
> 日期：2026-08-11

> **实施备注（与本文档的偏差，均为实施者即时判断）：**
> 1. 新增 `WINDOW_MARGIN = 40`——窗口尺寸（500×500）与逻辑半径（TOTAL_RADIUS 210）解耦，卡片矩形 clamp 进窗口边界，替代 §7.3 的“压缩间距/截断卡片”，杜绝透明窗口边缘无声裁剪。
> 2. 中心标题未使用 `PIE_MENU_TITLE` 常量——`i18n_check.py` 只扫描 `self.tr("字面量")`，经常量间接调用会产生 orphan 条目；源码字符串按项目规范用英文 `"Actions"`（ts 翻译“操作”），而非文档中的中文字面量。
> 3. §4.2 的扇形角度公式 `-112.5 - sector*45` 继承自旧代码且**画反 180°**（旧 wedge alpha 仅 22 未被发现），实施时修正为 `67.5 - sector*45` 并经预览图实证。

---

## 一、总体策略

不动交互状态机和触发逻辑（holding/pin/commit/cancel），只改渲染、布局、常量与命令定义。

改动范围：

| 文件 | 改动内容 |
|------|----------|
| `ui/pie_menu.py` | 重绘：去掉大圆盘、重做中心指示器、改卡片样式、改多卡片布局 |
| `ui/context_menu_config.py` | 清理 `CmdDef.icon` 字段及所有图标引用 |
| `scripts/pie_menu_test.py` | 同步更新命中测试断言 |
| `scripts/_pie_preview.py` | 同步更新预览图生成 |
| `translate/zh_CN.ts` | 新增中心标题翻译 |

---

## 二、视觉常量调整（`ui/pie_menu.py` 顶部）

```python
TOTAL_RADIUS = 210.0          # 保持不变，窗口总半径
CENTER_RADIUS = 42.0          # 中心圆环半径，保持不变
CENTER_INNER_RADIUS = 22.0    # 新增：中心扇形填充内半径，留一点空心
CENTER_DOT_RADIUS = 4.5       # 删除：Blender 没有中心点
CARD_R_IN = 84.0              # 删除或保留仅作参考
CARD_R_OUT = 192.0            # 删除或保留仅作参考
CARD_RADIUS = 142.0           # 新增：卡片中心到菜单中心的固定半径
CARD_PAD_X = 8.0              # 水平内边距，缩小以放下更多卡片
CARD_PAD_Y = 4.0              # 垂直内边距，缩小让卡片更紧凑
ICON_SIZE = 13.0              # 删除
ICON_MARGIN = 5.0             # 删除
NUM_MARGIN = 8.0              # 数字与右边缘间距加大
NUM_WIDTH_EXTRA = 2.0         # 保持不变
FAN_SPACING = 16.0            # 删除：不再按角度扇开
CARD_STACK_GAP = 4.0          # 新增：同扇区多卡片切向堆叠间距，紧凑
SECTOR_COUNT = 8
SECTOR_MAX_CARDS = 3          # 新增：每扇区最多卡片数
SHORT_PRESS_MS = 250
DEAD_ZONE_RADIUS = 5.0

> `_load_sectors()` 截断每扇区到 `SECTOR_MAX_CARDS`。
CARD_CORNER_RADIUS = 5.0      # 略减，更接近 Blender 的圆角
CENTER_RING_WIDTH = 2.0       # 中心环描边加粗
SECTOR_WEDGE_ALPHA = 22       # 删除：不再画大扇形背景
HOVER_TEXT_COLOR = QColor(255, 255, 255)
PIE_MENU_TITLE = "操作"        # 新增：中心标题，需进 ts 翻译
```

---

## 三、背景：完全去掉大圆盘

当前 `paintEvent` 里会画一个 `TOTAL_RADIUS` 的实心椭圆作为菜单底色。Blender 没有这个圆盘，背后是透明/直接看到画布网格。

**做法**：删除以下绘制：

```python
# 删除
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(bg)
painter.drawEllipse(center, TOTAL_RADIUS, TOTAL_RADIUS)
```

窗口本身仍保持 `WA_TranslucentBackground`，只在有内容的地方绘制。卡片自身带半透明底，不再依赖大圆盘统一压暗背景。

---

## 四、中心指示器：改成扇环 + 扇形填充

### 4.1 Blender 的样子

中心是一个细圆环，圆环内部有一个扇形小块（像饼图），指向当前 hover 的扇区。无 hover 时内部为空。

### 4.2 实现

新增绘制函数 `_paint_center_indicator(painter, center, accent_c)`：

1. **中心圆环**：用细描边画两个同心圆之间的环，或直接用描边椭圆（线宽 2px）。
2. **扇形填充**：当 `_hover` 不为空时，在当前扇区画一个扇形：
   - 内半径：`CENTER_INNER_RADIUS`
   - 外半径：`CENTER_RADIUS - 3`
   - 角度范围：扇区中心 ±22.5°
   - 颜色：`accentPrimary`，alpha 220 左右
3. **删除**：原来的中心点、方向箭头、径向指示线全部删除。

```python
def _paint_center_indicator(self, painter, center, accent_c):
    # 外环描边
    ring_pen = QPen(ring_c, CENTER_RING_WIDTH)
    painter.setPen(ring_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(center, CENTER_RADIUS, CENTER_RADIUS)

    if self._hover is not None:
        sector = self._hover[0]
        fill = QColor(accent_c)
        fill.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        rect = QRectF(
            center.x() - CENTER_RADIUS, center.y() - CENTER_RADIUS,
            2 * CENTER_RADIUS, 2 * CENTER_RADIUS,
        )
        # 扇区中心角：-90 + sector * 45，Qt drawPie 起点为 12 点钟方向逆时针
        start_qt = (-112 - sector * 45) * 16
        painter.drawPie(rect, int(start_qt), 16 * 45)
```

> 注意：这里需要挖掉内圆，形成扇环而不是实心扇形。可用 `QPainterPath` 组合：大扇形减去小扇形。

---

## 五、中心标题

在中心圆环上方绘制菜单标题。

```python
TITLE_OFFSET_Y = 28.0  # 标题基线到中心点的距离
```

绘制逻辑：

```python
title = self.tr(PIE_MENU_TITLE)
title_w = fm.horizontalAdvance(title)
title_x = center.x() - title_w / 2
title_y = center.y() - CENTER_RADIUS - TITLE_OFFSET_Y + fm.ascent()
painter.setPen(QPen(text_c))
painter.drawText(QPointF(title_x, title_y), title)
```

标题颜色使用普通文本色，alpha 略低（如 0.8），避免喧宾夺主。

---

## 六、卡片样式重绘

### 6.1 去掉图标

删除 `_load_icon()` 和 `_paint_cards()` 里的图标绘制代码。

卡片内容只保留：左侧文本、右侧数字。

### 6.2 卡片尺寸计算

由于不再预留图标区，卡片宽度改为：

```python
cw = 2 * CARD_PAD_X + text_w + 2 * NUM_MARGIN + num_w
ch = fm.height() + 2 * CARD_PAD_Y
```

### 6.3 配色

```python
if dark:
    card_bg = QColor(35, 37, 46, 200)      # 半透明深色底
    card_border = accent_c with alpha 160   # 强调色描边
    number_c = QColor(255, 255, 255, 140)
    text_c = get_theme_color("@textColor")
else:
    card_bg = QColor(255, 255, 255, 220)
    card_border = accent_c with alpha 160
    number_c = QColor(0, 0, 0, 120)
    text_c = get_theme_color("@textColor")
```

### 6.4 hover 状态

```python
if hovered:
    fill = QColor(accent_c)
    fill.setAlpha(255)                      # 实色填充
    painter.setPen(QPen(HOVER_TEXT_COLOR, 1.5))
    painter.setBrush(fill)
else:
    painter.setPen(QPen(card_border, 1.5))
    painter.setBrush(card_bg)
```

文本/数字 hover 时为白色；非 hover 时用 `text_c` / `number_c`，禁用状态 alpha 降至 110。

---

## 七、同扇区多卡片布局（解决左侧重叠）

当前用 `FAN_SPACING` 按角度扇开，导致轴对齐矩形重叠。**改为切向堆叠 + 允许占用相邻扇区空间**。

设计原则：
- 相关命令应放在同一扇区，便于识别。
- 多卡片扇区可以“撑开”，视觉上占用约两个常规扇区的面积。
- 每扇区最多 3 张卡片。

### 7.1 单卡片布局

与之前一致，卡片中心位于：

```python
base_angle = radians(-90 + sector * 45)
base_x = TOTAL_RADIUS + CARD_RADIUS * cos(base_angle)
base_y = TOTAL_RADIUS + CARD_RADIUS * sin(base_angle)
```

### 7.2 多卡片切向堆叠

当扇区有 `k > 1` 张卡片时，沿**切线方向**（垂直于半径）堆叠：

```python
tx = -sin(base_angle)   # 切线单位向量 x
ty =  cos(base_angle)   # 切线单位向量 y
offsets = [(i - (k - 1) / 2.0) * (card_h + CARD_STACK_GAP) for i in range(k)]
```

每张卡片的中心：

```python
cx = base_x + offsets[i] * tx
cy = base_y + offsets[i] * ty
```

效果：
- 左/右扇区：卡片垂直堆叠
- 上/下扇区：卡片水平堆叠
- 斜向扇区：沿 45° 切线堆叠

这样 3 张卡片会自然向相邻扇区方向延展，相当于占用了两侧各约半个扇区的空间，整体接近“两个常规扇区面积”。

### 7.3 边界处理

- 若切向堆叠后卡片超出窗口边界，优先压缩 `CARD_STACK_GAP` 到最小 2px；
- 仍超出时，按顺序只显示能完整显示的卡片（最多 3 张），其余截断；
- 代码中强制 `len(sector) <= 3`，配置加载时截断。

### 7.4 命中测试同步

`_hit_test()` 使用新的卡片矩形集合。先计算光标所在扇区，优先检查该扇区的所有卡片；若未命中任何卡片但落在该扇区有效范围内，则 hover 该扇区第一张可用卡片。

---

## 八、删除背景扇形

当前 hover 时会画一个从中心到边缘的大扇形背景。Blender 没有。

**做法**：删除 `_paint_sector_wedge()` 调用，保留函数或删除均可。

---

## 九、图标清理（`ui/context_menu_config.py`）

`CmdDef.icon` 是为饼菜单加的，现在决定不用图标，应彻底清理引用，避免 dead code。

### 9.1 删除字段

```python
@dataclass
class CmdDef:
    id: str
    label_key: str = ""
    build_fn: Optional[Callable] = None
    hidden_in_customize: bool = False
    run_fn: Optional[Callable] = None
    enabled_fn: Optional[Callable] = None
    # icon 字段删除
```

### 9.2 删除所有 icon= 赋值

以下命令的 `icon=` 参数删除：

- `delete`：`chrome-close.svg`
- `align_left`：`fontfmt_alignl.svg`
- `align_right`：`fontfmt_alignr.svg`
- `align_hcenter`：`fontfmt_alignc.svg`
- `translate`：`bottombar_translate.svg`
- `ocr`：`bottombar_ocr.svg`
- `ocr_translate`：`bottombar_ocr.svg`

### 9.3 同步删除 `ui/pie_menu.py` 中的图标引用

- 删除 `_ICON_DIR` 常量
- 删除 `_icon_cache` 属性
- 删除 `_load_icon()` 方法
- 删除 `_paint_cards()` 里的图标绘制代码

---

## 十、默认配置（`utils/config.py` 默认值）

保留当前默认配置，因为 align 命令属于同一语义组，放在同一扇区便于识别：

```python
DEFAULT_PIE_SECTORS = [
    ["ocr_translate"],                       # 1 上
    ["ocr"],                                 # 2 右上
    ["copy"],                                # 3 右
    ["paste"],                               # 4 右下
    ["delete"],                              # 5 下
    ["merge"],                               # 6 左下
    ["align_left", "align_right", "align_hcenter"],  # 7 左：对齐组
    ["translate"],                           # 8 左上
]
```

> 第 7 扇区有 3 张卡片，是新布局的重点验证场景。

---

## 十一、动画（可选，第一阶段可跳过）

Blender 的菜单弹出和扇区切换有轻微过渡。第一阶段建议不做，先把静态样式做对。如果后续要加：

- 弹出：0 → 1 的缩放 + 淡入，约 80ms。
- hover 切换：卡片填充色过渡，可用 `QPropertyAnimation` 或自定义 `QVariantAnimation` 插值 alpha。

---

## 十二、测试同步

`scripts/pie_menu_test.py` 中以下断言需要更新：

1. 命中测试坐标：因卡片位置从角度扇开改为切向堆叠，原有坐标期望失效。
2. 多卡片扇区测试：需要验证新布局下 3 张卡片不重叠且都能被命中。
3. 禁用命令测试：保持不变，逻辑未改。

更新原则：只改几何断言，不改状态机和触发逻辑断言。

---

## 十三、i18n

新增字符串：`PIE_MENU_TITLE = "操作"`，需要在 `translate/zh_CN.ts` 的 `PieMenu` context 下添加 message，并重新编译 `translate/zh_CN.qm`。

---

## 十四、验证清单

改动完成后按以下顺序验证：

1. `scripts/check_syntax.py ui/pie_menu.py ui/context_menu_config.py`
2. `scripts/pie_menu_test.py`（断言全部通过）
3. `scripts/_pie_preview.py` 生成 dark/light 预览图
4. `tests/test_startup_imports.py`
5. 启动 app，切到文本编辑模式，按住 Tab 目视确认

---

## 十五、已确认事项（2026-08-11）

| 问题 | 结论 |
|------|------|
| 1. 中心标题 | 保留 `"操作"`，为后续多菜单区分做准备 |
| 2. 默认配置 | 保留 3 个 align 命令在同一扇区，相关命令应待在一起 |
| 3. 多卡片上限 | 每扇区最多 3 张，超出截断 |
| 4. 动画 | 第一阶段不做，纳入后续规划 |
| 5. 图标 | 彻底清理 `CmdDef.icon` 字段及所有引用 |

补充说明：多卡片扇区通过**缩小卡片尺寸 + 切向紧凑堆叠 + 自然向相邻扇区延展**的方式实现“占用两个常规扇区面积”的效果，而不是动态改变扇区角度。
