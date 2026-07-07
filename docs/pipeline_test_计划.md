# Pipeline 真机测试功能 + 暗色模式修复

## Context

当前诊断工具的模块测试只做「导入 + 实例化」检查，不触发任何真实推理链路，用户无法确认各管线模块在实际负载下是否真的能工作。同时，诊断对话框的暗色模式适配使用了不可靠的 `palette(base)` 而非显式色值，导致暗色模式下各卡片框体仍显示白色。

本方案新增一条「Pipeline Test」测试路径，用项目内置的测试样图跑完整或部分推理链路，给用户切实的反馈（检测框数、OCR 识别率、翻译返回、修复效果），并修复暗色模式配色。

---

## 总体架构

```
assets/test_scenes/           ← 用户提供测试素材
  manifest.json               ← 场景清单
  scene_*.png                 ← 各场景测试图
  trans_sample.txt            ← 纯翻译测试文本

utils/pipeline_test_runner.py  ← [新增] PipelineTestRunner(QThread)
  - 按序执行选中的阶段
  - 阶段间传递数据 (mask, blk_list, inpainted)
  - 每步计时 + 异常隔离 + fail-continue

ui/test_preview_window.py     ← [新增] TestPreviewWindow(QDialog)
  - 图片显示 + 检测框叠加 / 修前修后切换
  - OCR 对比表格
  - 关闭自动清理

ui/system_diagnostic_dialog.py ← [修改]
  - 暗色模式: palette(base)→ 显式 hex
  - 新增 Pipeline Test 卡片
  - 加载 manifest、复选框、结果日志、预览按钮
```

---

## 步骤详解

### Step 1: 创建 `assets/test_scenes/` 目录与 manifest.json

**目录结构：**
```
assets/test_scenes/
├── manifest.json
├── scene_detect_ocr.png      ← 用户制作: 纯文字页, ~8-12 区域
├── scene_full.png             ← 用户制作: 漫画页, ~4-8 区域
├── scene_inpaint.png          ← 用户制作: 大块文字/特殊字体
└── trans_sample.txt           ← 用户编写: 3-5 行日文/英文
```

**manifest.json 核心设计：**

```json
{
  "version": 1,
  "scenes": [
    {
      "id": "detect_ocr",
      "display_name": "检测+OCR 测试",
      "file": "scene_detect_ocr.png",
      "stages": ["textdetector", "ocr"],
      "expected": {
        "textdetector": { "min_regions": 5, "max_regions": 20 },
        "ocr": { "contains": ["指定文字"] }
      }
    },
    {
      "id": "full_pipeline",
      "display_name": "全流程测试",
      "file": "scene_full.png",
      "stages": ["textdetector", "ocr", "translator", "inpainter"],
      "expected": { ... }
    }
  ],
  "text_samples": [
    {
      "id": "translate_only",
      "display_name": "纯翻译测试",
      "file": "trans_sample.txt",
      "stages": ["translator"],
      "expected": {}
    }
  ]
}
```

**选择逻辑**（根据用户勾选的 stages 匹配最佳 scene）：
- 优先选择 `stages` 字段与勾选集合完全一致的 scene
- 无完全匹配时，选包含所有勾选 stages 的 superset scene
- 纯翻译勾选 → 自动切到 text_samples 条目（不弹预览图）
- 匹配结果展示在卡片上：「已匹配：全流程测试图」

---

### Step 2: 新增 `utils/pipeline_test_runner.py`

**`PipelineTestRunner(QThread)`：**

```python
class PipelineTestRunner(QThread):
    stage_finished = Signal(str, dict)   # stage_name, result
    finished = Signal(dict)              # overall_result

    def __init__(self, stages, scene_config, parent=None):
        # stages: ["textdetector", "ocr", ...] 保持管线顺序
        # scene_config: manifest 中对应条目的 dict
```

**run() 方法执行流程：**

```
1. 加载图片: cv2.imread(shared.PROGRAM_PATH / manifest["file"])
   - 如果是纯文本翻译, 读文本文件, 构建虚拟 TextBlock 列表
2. 按顺序遍历 stages (detect → ocr → translate → inpaint)
   每步:
   a. time.perf_counter() 开始计时
   b. registry.resolve_module(key) → cls
   c. 从 pcfg 取 params 实例化模块
      - translator 特殊处理: 传 lang_source/lang_target
   d. 调用 stage API
   e. 计时结束
   f. emit stage_finished(stage, result_dict)
   g. 检查 manifest expected 字段, 输出验证警告
3. emit finished(overall_result)
```

**result_dict 结构：**
```python
{
    "success": bool,
    "output": str,           # 日志文本
    "duration_ms": float,    # 保留小数位
    "mask": np.ndarray | None,       # detect
    "blk_list": list | None,         # detect/ocr/translate
    "inpainted": np.ndarray | None,  # inpaint
}
```

**fail-continue 规则：**
- detect 失败 → 跳过 ocr / translate / inpaint（数据依赖）
- ocr 失败 → 跳过 translate，但 inpaint 可继续（只需要 mask）
- translate 失败 → inpaint 可继续
- inpaint 失败 → pipeline 结束

**关键注意：**
- 复用 `env_diagnostic.py:test_module_functional()` 的模块实例化模式
- 通过 `modules.base.DEFAULT_DEVICE` 获取设备
- 显式调用 `module.load_model()` 确保模型就绪
- 文本翻译时动态创建虚拟 TextBlock: `TextBlock(xyxy=[0,0,1,1], text=[line])`
- `duration_ms` 使用 `round(elapsed * 1000, 1)` 保留小数

---

### Step 3: 新增 `ui/test_preview_window.py`

**`TestPreviewWindow(QDialog)`：**

窗口布局：
```
┌─ 流水线测试结果 ────────────────────────────── [×] ─┐
│ ┌─ 图片区 ──────────┐  ┌─ 控制区 ────────────┐      │
│ │                   │  │ ☑ 检测框           │      │
│ │  QLabel + QPixmap │  │ ☐ 修复效果         │      │
│ │  自适应缩放       │  │                    │      │
│ │                   │  │ 阶段状态:           │      │
│ │                   │  │ ✓ 检测: 12 区域     │      │
│ │                   │  │ ✓ OCR: 10/10 匹配  │      │
│ │                   │  │ ✗ 翻译: 超时       │      │
│ └───────────────────┘  └────────────────────┘      │
│ ┌─ OCR 对比 ─────────────────────────────────────┐ │
│ │ # │ 预期原文       │ OCR 识别      │ 匹配      │ │
│ │ 1 │ "発射"         │ "発射"        │ ✓         │ │
│ │ 2 │ "なに"         │ "なに"        │ ✓         │ │
│ └────────────────────────────────────────────────┘ │
│ 状态: 通过  (总耗时 3.2s)                [关闭]    │
└────────────────────────────────────────────────────┘
```

**关键实现：**
- 图片区: QLabel 承载 QPixmap, 缩放保持比例 (`Qt.KeepAspectRatio`)
- 检测框叠加: QPainter 在 QPixmap 副本上画绿色矩形 + 序号
- 修复切换: checkbox 控制显示原图还是 inpainted 结果
- OCR 表格: QTableWidget, 列 = 区域号/预期/识别/匹配
  - "匹配"依据 manifest 中 `expected.ocr.contains` 做子串匹配
  - 仅 OCR 阶段运行后才显示
- closeEvent 中清理: 释放 numpy 引用, `deleteLater()`
- 窗口最小尺寸: 800x600
- 不含图片的纯翻译场景不弹此窗口

---

### Step 4: 修改 `ui/system_diagnostic_dialog.py`

#### 4a. 暗色模式修复

**`_theme_colors()` 改造：**
```python
def _theme_colors():
    dark = bool(getattr(pcfg, 'darkmode', False))
    return {
        "card_border": "#555" if dark else "#ccc",
        "card_bg": "#2b2b2b" if dark else "#fcfcfc",
        "sep": "#555" if dark else "#ddd",
        "log_border": "#666" if dark else "#ccc",
        ...
    }
```

**`_Card.__init__` 中样式表更新：**
- `background: {tc['card_bg']}` 从 `palette(base)` 改为显式 hex
- 子 QLabel 添加 `background: transparent` 防止白色遮盖

**`_on_test_result()` 日志框背景：**
- `background: palette(window)` → `background: {bg_hex}`（暗 `#1e1e1e` / 亮 `#f5f5f5`）

#### 4b. Pipeline Test 卡片

在 `_refresh_all()` 的管线模块卡片之后新增：

```python
# ── Card: Pipeline Test ──
pt_card = _Card(self.tr("Pipeline Test"))
self._build_pipeline_test_card(pt_card)
self._cards_layout.addWidget(pt_card)
```

**`_build_pipeline_test_card(card)` 实现：**

1. **加载 manifest.json** — 从 `shared.PROGRAM_PATH / "assets/test_scenes/manifest.json"` 读取，异常时显示「未找到测试资源」提示

2. **阶段复选框行** — 4 个 QCheckBox（文本检测 / OCR / 翻译 / 修复），根据 `pcfg.module.enable_*` 预勾选。勾选变化时自动更新已匹配场景提示

3. **已匹配场景提示** — QLabel 显示「已匹配：全流程测试图」或「已匹配：纯翻译测试」

4. **运行按钮** — QPushButton「运行流水线测试」，点击 → `_run_pipeline_test()`

5. **日志区** — QPlainTextEdit (readonly, monospace, max 200px), 运行期间增量追加

6. **查看结果按钮** — QPushButton「查看结果」，默认隐藏，pipeline 完成后显示。点击 → 创建 TestPreviewWindow

**`_run_pipeline_test()` 方法：**
```python
def _run_pipeline_test(self):
    active = [s for s in ORDER if self._pt_check[s].isChecked()]
    if not active: return
    scene = self._match_scene(active)  # manifest 匹配
    self._pt_runner = PipelineTestRunner(active, scene, self)
    self._pt_runner.stage_finished.connect(self._on_pt_stage)
    self._pt_runner.finished.connect(self._on_pt_done)
    self._pt_runner.start()
```

**`reject()` 覆写：** 用户关闭 dialog 时，如有 running runner 则 `terminate()` + `wait()`

---

### Step 5: 翻译条目

涉及以下新字符串的 `self.tr()` + `translate/zh_CN.ts` + `qm` 更新：
- "Pipeline Test" / "流水线测试"
- "Run Pipeline Test" / "运行流水线测试"
- "View Results" / "查看结果"
- "Testing..." / "测试中..."
- Matched scene labels

---

## 文件总表

| 文件 | 操作 | 说明 |
|------|------|------|
| `assets/test_scenes/manifest.json` | 新增 | 场景清单 |
| `utils/pipeline_test_runner.py` | 新增 | PipelineTestRunner(QThread) |
| `ui/test_preview_window.py` | 新增 | 预览图窗 |
| `ui/system_diagnostic_dialog.py` | 修改 | 暗色模式 + Pipeline Test 卡片 |
| `translate/zh_CN.ts` | 修改 | 新增翻译条目 |
| `translate/zh_CN.qm` | 修改 | 重新编译 |

---

## 验证方式

1. **暗色模式** — 开启暗色模式 → 打开系统诊断 → 确认四张卡片背景为深色（`#2b2b2b`），文字可读，日志框背景为 `#1e1e1e`
2. **Pipeline test** — 勾选「文本检测」→ 自动显示「已匹配：检测+OCR 测试图」→ 点击运行 → 日志逐步追加检测和 OCR 结果 → 完成后出现「查看结果」→ 点击弹窗显示检测框叠加、OCR 对比表
3. **纯翻译测试** — 仅勾选「翻译」→ 提示「已匹配：纯翻译测试」→ 运行 → 日志显示翻译结果，完成后不弹图窗
4. **fail-continue** — 断开网络/停用翻译 API 后运行全流程 → 检测/OCR 通过，翻译失败标记，修复仍可运行
5. **关闭清理** — 运行后关闭 dialog → 确认无残留线程
