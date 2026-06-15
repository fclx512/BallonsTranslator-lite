# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 7 天的记录，超期内容自动清理。

---

## 2026-06-15

### PP-OCRv6 下载弹窗改为 QThread 后台安装 + 中文翻译

**问题：** 选择 PP-OCRv6 ONNX 模块后弹窗提示下载依赖，但 pip install 和模型下载都在 GUI 线程同步执行，窗口冻结无响应。

**改动：**
1. 将 pip 安装和模型下载迁移到 `QThread` 后台线程，通过信号非阻塞更新 UI
2. 日志区 `QLabel` → `QPlainTextEdit`（带滚动条），失败时变红
3. 进度条改用 indeterminate 模式，状态标签显示当前阶段
4. 下载失败时显示中文错误提示 + 解决建议（镜像配置指引）
5. 所有 UI 字符串统一 `self.tr()`，补充 `.ts` 中文翻译并重编 `.qm`

**涉及文件：**
- `ui/module_manager.py` — 新增 `_InstallWorker(QThread)`，重写 `_InstallDialog`
- `translate/zh_CN.ts` / `.qm` — 新增 8 条翻译

### 拖拽调整文本框时"假文本框"闪烁修复

**现象：** 拖拽文本框调整大小时，放弃实时渲染/禁用描边后，仍有一个闪烁的虚线框（"假文本框"）在调整时显示，与文字内容错位。

**根因：** 拖拽时 `TextBlkShapeControl`（虚线控制框）通过 `mouseMoveEvent` 立即更新到新位置，而 `TextBlkItem`（文字块）的几何更新被 30ms debounce 定时器延迟。二者之间存在 30ms 的追赶窗口期，用户看到两个错位的虚线框（控制框 XOR 虚线 + 文字块粉色选择虚线），XOR 合成加剧了闪烁感知。

**改动：** `textitem.py` 新增 `setRectFast()` 方法，仅更新位置和 `_display_rect`（O(1)），不触发布局重算；`texteditshapecontrol.py` 的 `mouseMoveEvent` 中每个 frame 都调用它同步文字块几何。昂贵的 `layout.setMaxSize()` 仍由原 30ms 定时器执行。

**涉及文件：**
- `ui/textitem.py` — 新增 `TextBlkItem.setRectFast()` (行 501)
- `ui/texteditshapecontrol.py` — `ControlBlockItem.mouseMoveEvent` 中调用 setRectFast (行 244)
