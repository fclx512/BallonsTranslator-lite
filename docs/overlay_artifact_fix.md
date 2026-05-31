# OverlaySlider 合成渲染伪影修复

## 问题

左侧 AI Chat 面板（480px 宽）在以下场景出现 75px 宽的纯白竖条：

1. 先打开"全局查找"（GlobalSearch，300px）或"图片列表"（PageList，250px）这类左滑覆盖栏
2. 再点击展开 AI Chat 面板
3. 滑入动画结束后，白条出现在之前打开的那个侧栏右边缘位置，宽度恒为 75px
4. 纯白 #FFFFFF，与软件任何主题色均不匹配
5. 动画过程中无问题，仅在动画完全结束后出现
6. 快速反复切换不会触发

## 根因

`OverlaySlider` 的 `show()` 方法使用一套复杂的合成渲染流程：

1. 将真实控件置于屏幕外（x = -480），`show()` 使其可见
2. `widget.grab()` 抓取控件外观为 QPixmap
3. `widget.hide()` **隐藏真实控件**
4. 通过 `_SharedOverlay`（一个覆盖在父控件之上的透明合成层）将抓取的像素图合成到背景快照上
5. 动画驱动像素图位置从 -480 → 0（滑入效果）
6. 动画完成 → `_cleanup_animation()` 移除合成层 → 销毁 `_SharedOverlay` → **重新 show() 真实控件**

问题出在第 6 步的衔接点：从"合成层展示"切换到"真实控件展示"时，存在一个时间窗口。`_SharedOverlay` 的背景像素图在构建/扩展时通过 `parent.grab()` 抓取父控件外观，在多个覆盖面板同时动画（PageList 隐藏 + GlobalSearch 隐藏 + AI Chat 显示）的复杂场景下，抓取的背景可能在特定区域（原侧栏右边缘附近）含有不完整的像素，表现为 75px 宽纯白带。

此外，`widget.hide()` 之后重新 `show()` 的循环导致 Qt 需要重新映射控件树、重新布局子控件，增加了不确定性。

## 修复

**方案：放弃合成渲染，改用真实控件直接动画。**

修改 `OverlaySlider.show()`，跳过整个 grab/hide/SharedOverlay 流程，改为：

1. 将真实控件置于起始位置（x = -480）
2. 直接 `widget.show()` 使其可见
3. `widget.raise_()` 确保在 Z 序最前
4. 用 `widget.move(current_x, 0)` 直接驱动控件位置（代码中原有的 fallback 路径）
5. 每帧通过 `raise_()` 维持 Z 序在 `_SharedOverlay`（来自其他面板的动画）之上

这样 AI Chat 面板从动画开始到结束**始终保持真实控件可见**，没有抓图、隐藏、合成、重建的循环。

### 涉及文件

| 文件 | 改动 |
|------|------|
| `ui/overlay_slide.py` | `show()` 跳过合成，直接 show + move 动画；`_update_animation()` 的 fallback 路径加 `raise_()` |
| `ui/ai_chat_panel.py` | QScrollArea viewport 加 `setAutoFillBackground(True)`（辅助防御） |
| `config/stylesheet.css` | 加 `#AIChatArea > QWidget { background-color... }` 给 viewport 设主题色（辅助防御） |

## 技术要点

- `QWidget.move()` 驱动的动画在控件可见状态下工作，不需要 grab/composite
- `raise_()` 在每帧调用以确保 Z 序不被 `_SharedOverlay`（其他面板的动画层）压制
- 隐藏动画（`hide()`）仍使用 `_SharedOverlay` 合成，因为 AI Chat 隐藏时不需要与其他面板的内容同时可见
- `_reverse()` 逻辑在直接动画和合成动画之间切换时需要处理：若正在显示动画（直接模式）被 `hide()` 反转，动画字段和回调正确切换
- 75px 的精确值源自场景中其他侧栏宽度（250/300px）与合成层背景抓取的交互，非固定常数
