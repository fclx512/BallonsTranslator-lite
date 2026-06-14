# 无模型运行模式 — 设计文档

## 概述

目标：项目能运行在完全不安装 PyTorch 的环境下（零 torch 依赖），同时支持按需增删本地模型模块和相关依赖。

## 用户旅程

### 1. 下载启动

用户从 Releases 下载打包好的版本：
- **极小体积**：不包含 PyTorch、模型权重、ultralytics 等重量级依赖
- **即开即用**：解压后直接运行，UI 和 API 翻译（LLM API Translator、SakuraLLM）完全正常
- **无模型依赖的功能**：项目管理、文本编辑、PSD 导出、全局搜索等所有 UI 功能均可使用

### 2. 管线阶段选择

四个管线阶段的 ComboBox 下拉列表新增分组展示：

```
文字检测:
  ──────────
  none                    ← 跳过检测
  ── 需安装依赖 ──
  ctd                 需要 PyTorch
  ysg                 需要 PyTorch

OCR:
  ──────────
  none_ocr                ← 直接返回原文
  ── 需安装依赖 ──
  mit48px_ctc         需要 PyTorch + torchvision
  paddleocr_v6        需要 PaddleOCR + PaddlePaddle

翻译:
  ──────────
  None                    ← 不翻译
  Source                  ← 保留原文
  ── 无需本地模型 ──
  LLM_API_Translator
  SakuraLLM

图像修复:
  ──────────
  none                    ← 跳过修复
  ── 需安装依赖 ──
  lama_large_512px    需要 PyTorch + diffusers
  ffc                 需要 PyTorch
  aot                 需要 PyTorch
```

### 3. 首次选择模型模块

用户选中 `mit48px_ctc` 后：

```
┌──────────────────────────────────────────────────────┐
│  此模块需要额外依赖                                  │
│                                                      │
│  Python 包：                                         │
│    ■ torch .................. [未安装 — 380 MB]      │
│    ■ torchvision ............ [未安装 — 50 MB]       │
│                                                      │
│  模型文件（自动从 HuggingFace 下载）：               │
│    ■ mit48px_ctc.pt ......... [未下载 — 120 MB]      │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │ ⚠ 网络受限？打开 设置 → 镜像配置 调整下载源   │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│         [  安装全部 (~550 MB)  ]  [  稍后再说  ]     │
└──────────────────────────────────────────────────────┘
```

点击"安装全部"：
1. `pip install torch torchvision`（调用 `ensure_dependencies()`）
2. 自动下载 `mit48px_ctc.pt`（调用 `_ensure_model_files()`）
3. 安装完成后该模块立即可用

### 4. 模型文件管理面板

`Tools → Model Files` 改为可选交互：

```
┌───────────────────────────────────────────────────────┐
│  ☑  模型名称             类型         状态      大小  │
│  ───────────────────────────────────────────────────   │
│  [✓] mit48px_ctc.pt      OCR         已安装  120 MB  │
│  [ ] lama_large.pt       图像修复     缺失   350 MB  │
│  [ ] detector_ctd.pt     文字检测     缺失    80 MB  │
│  [ ] ffc.pt              图像修复     缺失   120 MB  │
│                                                        │
│  已选 2 个模型，总计 430 MB                            │
│                                                        │
│  [ 下载选中 ]  [ 全选 ]  [ 取消全选 ]  [ 刷新 ]      │
└───────────────────────────────────────────────────────┘
```

- 第一列为复选框，第二列为状态指示
- 底部显示选中合计大小
- "下载选中"逐个调用 `download_and_check_files()`
- **不做 "一键下载全部"**（PaddleOCR 系列模型过多过重，不合适应）

## 架构改动

### 文件清单

| # | 文件 | 动作 | 说明 |
|---|------|------|------|
| 1 | `pyproject.toml` | 修改 | `ultralytics>=8.4.14` 从核心依赖移入 `[gpu]` 可选组 |
| 2 | `modules/textdetector/detector_none.py` | 新增 | 无操作文字检测模块 |
| 3 | `modules/inpaint/inpaint_none.py` | 新增 | 无操作图像修复模块 |
| 4 | `ui/module_manager.py` | 修改 | `_set_module()` 加入安装前依赖检查 + 安装弹窗 |
| 5 | `ui/module_parse_widgets.py` | 修改 | ComboBox 分组展示 + 标注依赖状态 |
| 6 | `ui/model_check_dialog.py` | 修改 | 新增复选框列 + "下载选中"按钮 |
| 7 | `modules/base.py` | 验证 | `soft_empty_cache()` 无 torch 时仅 `gc.collect()`（已实现） |
| 8 | `modules/inpaint/base.py` | 微调 | `torch` 引用加 try/except 保护 |
| 9 | `ui/mainwindow_mixin.py` | 微调 | 模块切换时接入依赖预检查 |

### 改动详述

#### 1. pyproject.toml

```toml
[project]
dependencies = [
  # ... 保留所有非 torch 依赖 ...
  # ultralytics>=8.4.14  ← 移除
]

[project.optional-dependencies]
gpu = ["torch", "torchvision", "transformers", "diffusers", "ultralytics>=8.4.14"]
paddle = ["paddleocr>=3.7.0"]
```

#### 2. detector_none.py

```python
@register_textdetectors("none")
class TextDetectorNone(TextDetectorBase):
    params = {"description": "Skip text detection. No model needed."}
    def _detect(self, img, proj):
        return np.zeros(img.shape[:2], dtype=np.uint8), []
    def setup_detector(self):
        pass
```

#### 3. inpaint_none.py

```python
@register_inpainter("none")
class InpainterNone(InpainterBase):
    inpaint_by_block = False
    check_need_inpaint = False
    params = {"description": "Skip inpainting. No model needed."}
    def _inpaint(self, img, mask, textblock_list=None):
        return img
```

#### 4. module_manager.py — 依赖预检查

`ModuleThread._set_module()` 增加流程：

```python
def _set_module(self, module_name):
    spec = self.module_register.get_spec(module_name)
    if spec and not self._deps_satisfied(spec):
        self._show_install_dialog(spec)  # 安装弹窗
    # ... 原有 resolve / 实例化逻辑 ...
```

安装弹窗展示：
- `spec.dependencies` 中的 Python 包（pip 安装）
- `spec.download_file_list` 中的模型文件
- 底部"网络受限"提示，链接到镜像配置

#### 5. module_parse_widgets.py — 下拉框增强

ComboBox 渲染时，扫描每个可选模块的依赖状态，加入视觉分组和状态图标：

```
默认分组:
  - "none" / "None" / "Source" 类无操作模块

依赖分组:
  - "需安装依赖" 分组标题
  - 模块名 + 状态标注（"需要 PyTorch" / "需要 Paddle"）
```

#### 6. model_check_dialog.py — 多选下载

- `QTableWidget` 第0列改为 `QCheckBox`
- 第4列为"大小"列（可选项，留空或从已知信息推算）
- 底部新增按钮区："下载选中"、"全选"、"取消全选"
- "下载选中"遍历已勾选条目，调用 `download_and_check_files(**dl_entry)`

### torch 相关代码安全分析

| 文件 | torch 引用 | 风险 |
|------|-----------|------|
| `modules/base.py:387-441` | `try: import torch` | **安全** — try/except 兜底 |
| `modules/inpaint/base.py:75` | `torch.cuda.OutOfMemoryError` | 需加 try/except |
| `modules/inpaint/base.py:80` | `torch.cuda.OutOfMemoryError` | 同上 |
| `modules/ocr/mit48px_ctc.py` | `import torch` 模块级 | **安全** — 懒加载，用户选才导入 |
| `modules/textdetector/*` | 全部在函数级或 try/except 内 | **安全** |

唯一需要在 `modules/inpaint/base.py` 加的：
```python
try:
    TorchOOMError = torch.cuda.OutOfMemoryError
except (AttributeError, ImportError):
    TorchOOMError = type("_NoTorchOOM", (Exception,), {})
```

## 不在此范围的内容

- 不作为独立 PyPI 包分发（仍走 Releases 打包）
- 不改动现有模块的代码逻辑（只加保护）
- 不引入新的依赖管理工具（复用现有的 `ensure_dependencies()` + `uv/pip`）
- 不移除 `modules/base.py` 的 torch 检测逻辑（不影响零 torch 运行）
