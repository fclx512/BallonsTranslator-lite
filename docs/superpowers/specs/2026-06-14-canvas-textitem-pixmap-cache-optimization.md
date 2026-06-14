# 画布文本框拖拽/旋转 Pixmap 缓存优化

## 问题

拖拽或旋转 `TextBlkItem`（画布文本框）时，CPU/GPU 负载高。

**根因：** 每帧拖拽移动都会触发 `paint()` 调用 `super().paint()`，后者走 `QTextDocument.drawContents()` 进行全量文字排版渲染。显示器刷新率越高（60→144Hz），问题越严重。描边/阴影等样式进一步加剧开销。

上游作者原话：*"这些 textitem 检测到和其它控件的覆盖关系发生变化然后重绘了"*——Qt Graphics View 框架在 item 位置变化时将新旧脏区标记为需重绘，区域内所有 item 被波及。

## 方案

### 核心思想

将每个文本框的完整渲染结果（文字 + 描边 + 阴影）预缓存到一张 `QPixmap` 中。拖拽/旋转时，`paint()` 直接 `drawPixmap()` 走显存拷贝，跳过昂贵的 `QTextDocument` 渲染路径。

### 缓存生命周期

缓存与 `TextBlkItem` 实例同生命周期。切页时 `clearSceneTextitems()` 销毁所有 item，换页后新建的 item 从头构建缓存——无需额外清理逻辑，内存自然随页释放。

### 缓存失效策略

| 触发事件 | 行为 | 原因 |
|---------|------|------|
| 文字内容变化 (`contentsChanged`) | 重建缓存 | 文字变 |
| 字号/字体/颜色等样式变化 | 重建缓存 | 外观变 |
| 描边/阴影参数变化 | 重建缓存 | 外观变 |
| 视图缩放完成 | 重建缓存 | 保证清晰度 |
| **位置变化（拖拽/旋转中）** | **不重建** | 内容没变，直接 blit 缓存 |
| 拖拽/旋转结束 | 不重建 | 缓存仍有效 |
| 进入编辑模式 | 不使用缓存（走 QTextDocument） | 需要实时响应用户输入 |
| 退出编辑模式 | 重建一次缓存 | 切回缓存路径 |

### 缓存大小

单张 pixmap = `width × height × 4 (RGBA)` 字节。

| 场景 | 单张 | 5 张总和 |
|------|------|---------|
| 典型 200×100 px | 80 KB | 400 KB |
| 4K / 400×200 px | 320 KB | 1.6 MB |
| 极大 800×400 px | 1.28 MB | 6.4 MB |

最坏情况 < 7 MB，可忽略。

### 视觉影响

拖拽/旋转中肉眼无差异——用户的注意力在位置/角度上，不会去读文字。视图缩放完成后重建缓存，保持文字清晰。编辑模式完全不受影响（走原 QTextDocument 路径）。

## 实现步骤

### Step 1: `TextBlkItem` 新增状态变量

- `_full_pixmap: Optional[QPixmap]` — 完整渲染缓存
- `_full_pixmap_dirty: bool` — 缓存脏标记，True 表示需要重建
- `_use_full_pixmap: bool` — 当前是否使用缓存路径（编辑态 = False，非编辑态 = True）

### Step 2: 新增 `_build_full_pixmap()` 方法

将当前已有逻辑（`repaint_background()` 负责描边+阴影，`super().paint()` 负责文字）合并到一张 pixmap 中：

```
1. 创建 QPixmap(boundingRect().size().toSize())，透明填充
2. QPainter 渲染到 pixmap：
   a. 如果有描边 → 调用 paint_stroke(painter)（已有，略有调整）
   b. 否则 → doc.drawContents(painter)
   c. 如果有阴影 → 应用阴影效果图（已有逻辑，移自 repaint_background）
3. 赋值到 self._full_pixmap
4. self._full_pixmap_dirty = False
```

注意：`paint_stroke()` 和 `_render_text_only()`（阴影相关）已有绘制逻辑，需微调使其输出到完整 pixmap 而非分开处理。

### Step 3: 修改 `paint()` 方法

```python
def paint(self, painter, option, widget):
    if self._use_full_pixmap and self._full_pixmap is not None and not self._full_pixmap_dirty:
        # 快速路径：直接 blit 缓存（文字+描边+阴影已在 pixmap 里）
        painter.drawPixmap(self.boundingRect().toRect(), self._full_pixmap)
        # 选中边框、序号徽章仍需绘制（不在缓存中）
        self._draw_selection_rect(painter)
        self._draw_seq_badge(painter)
        return

    # 原路径（编辑态 或 缓存无效）
    if self.is_editting():
        self._draw_accessories(painter)
    option.state = QStyle.State_None
    super().paint(painter, option, widget)
    if not self.is_editting():
        painter.setCompositionMode(...)
        self._draw_accessories(painter)
        self._draw_seq_badge(painter)
```

### Step 4: 修改 `repaint_background()` → 扩展为完整缓存

现有 `repaint_background()` 只处理描边/阴影，文字绘图留给 `super().paint()`。重构使其输出**文字 + 描边 + 阴影**到 `_full_pixmap`：

- 如果 `_full_pixmap_dirty`，调用 `_build_full_pixmap()`
- 保持与 `background_pixmap` 的兼容性（或直接替代它），将 `_draw_accessories` 内的逻辑并入

**关键：** `_full_pixmap` 始终包含文字。描边/阴影按需加入。背景纯属透明。

### Step 5: 设置缓存失效点

在以下位置调用 `_invalidate_cache()`（设置 `_full_pixmap_dirty = True`，置空 `_full_pixmap`）：

| 方法 | 原因 |
|------|------|
| `setPlainText()` / `setHtml()` / 编辑器内容变更 | 文字变 |
| `setFontSize()` / `setFontFamily()` / `setFontWeight()` / … | 样式变 |
| `setStrokeWidth()` / `setStrokeColor()` / `setShadow()` | 描边/阴影变 |
| `setRelFontSize()` | 字号缩放 |
| `endEdit()` | 退出编辑切回缓存 |

**不要**在以下位置触发失效：
- `setRect()` / `setPos()` / `setRotation()` / `mouseMoveEvent` → 位置/角度变化不影响文字内容

### Step 6: 视图缩放完成时重建缓存

监听 `Canvas.scalefactor_changed` 信号，缩放完成后对所有 `TextBlkItem` 调用 `invalidate_cache()` + 重建：

```python
# 在 SceneTextManager.adjustSceneTextRect 或 canvas.scaleImage 中
# 缩放操作末尾，延迟重建（QTimer.singleShot 或 next tick）
if self.txtblkShapeControl.blk_item:
    self.txtblkShapeControl.blk_item._invalidate_cache()
```

更理想：使用 `QTimer.singleShot(0, rebuild)` 将重建推迟到缩放完成后的第一个空闲周期，避免缩放过程中反复重建。

### Step 7: 逐步替换 `background_pixmap`

现有 `background_pixmap` + `_draw_accessories()` 中的 `drawPixmap(background_pixmap)` 可以被 `_full_pixmap` 替代。重构后：

- `_full_pixmap` 包含全部内容（文字+描边+阴影）
- `background_pixmap` 不再需要，或保留为中间结果
- 缓存非编辑状态下使用，`_draw_accessories()` 只在编辑态绘制

## 涉及的源代码文件

| 文件 | 主要改动 |
|------|---------|
| `ui/textitem.py` | `TextBlkItem`：新增 `_full_pixmap`、`_build_full_pixmap()`、修改 `paint()`、`repaint_background()`、内容变化失效点 |
| `ui/scenetext_manager.py` | `SceneTextManager`：可选，连接缩放完成信号或设置/清除缓存状态 |
| `ui/canvas.py` | `Canvas`：可选，缩放完成后触发重建通知 |

## 边界情况

- **空文档 / 无文字：** 缓存中只画背景（透明），`isEmpty()` 时不做 sketch 或不建缓存
- **编辑态：** 完全不走缓存路径，使用原有 QTextDocument 实时渲染 → 原生编辑体验
- **多选拖拽：** 被拖拽的多个 item 各自缓存不受影响
- **输入法预编辑：** pre_editing 期间仍实时刷新 `on_content_changed` 会触重建，但不走缓存路径，与原行为一致
- **缩放动画/捏合：** 缩放持续过程中不强刷缓存，缩放完成（`scaleImage` 调用完毕）后重建一次

## 验证

1. 拖拽/旋转单个文本框，用任务管理器/性能分析器观察 CPU 占用率相比主分支下降
2. 帧率测试：在 60Hz 和 144Hz 显示器上拖拽，帧率应保持稳定无掉帧
3. 文字质量：缓存路径下文字清晰度与实时渲染一致（缩放 100%、200% 对比）
4. 交互完整性：双击编辑、修改样式、描边开关、阴影开关，视觉效果与主分支一致
5. 功能回归：新建/删除/复制/粘贴文本框、快捷键、Undo/Redo
