# PP-OCRv6 ONNX 迁移记录

> 2026-06-14 手写。PaddlePaddle → ONNX Runtime 迁移的状态记录。

## 现状：ONNX 方案已通过验证

PP-OCRv6 ONNX Runtime 方案已验证可行：

| 测试项 | 结果 |
|--------|------|
| 模型下载 | 62MB det + 73MB rec，HuggingFace 直连下载 |
| 字符字典 | 18,708 条目，从 `inference.yml` 提取 |
| OCR 测试 | `"Hello PaddleOCR v6"` (conf=0.987) |
| 初始化 | ~0.4s |
| 推理速度 | ~0.8s（CPU, 600x200 图片） |

## 文件位置

### ONNX 模型（项目内）

```
models/ppocrv6_onnx/
├── det.onnx                   (62MB) — DB 检测模型
├── rec.onnx                   (73MB) — SVTR_LCNet 识别模型
├── ppocrv6_dict_proper.txt    (18,708 字符) — 字符字典
└── ppocrv6_dict.txt           (旧版，18707 条目，已弃用)
```

来源：
- det: `huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx`
- rec: `huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx`
- 原始 config: `ppocrv6_rec_inference.yml`（含完整字符字典）

### 当前 PaddlePaddle 依赖（在 C 盘，待清理）

```
C:\Users\duham\AppData\Local\Packages\...\Python313\site-packages\
├── paddle/                    (~400MB)
├── paddleocr/                 (~100MB)
└── paddlex/                   (~50MB)

~/.paddlex/official_models/    (193MB) — PP-OCRv6 原生模型缓存
~/.paddleocr/                  (模型缓存)
```

### 测试图片

```
test_ocr.png                   — 用于验证 OCR 的测试图
```

## 关键参数（PP-OCRv6 vs OnnxOCR 默认值）

| 参数 | OnnxOCR 默认 | PP-OCRv6 需用 |
|------|--------------|---------------|
| `det_db_thresh` | 0.3 | **0.2** |
| `det_db_box_thresh` | 0.6 | **0.45** |
| `det_db_unclip_ratio` | 1.5 | **1.4** |
| `max_candidates` | 1000 | **3000** |
| `rec_image_shape` | 3,48,320 | 3,48,320（同） |
| `rec_algorithm` | SVTR_LCNet | SVTR_LCNet（同） |
| `rec_char_dict_path` | ppocrv5_dict.txt (18,383 条目) | **ppocrv6_dict_proper.txt (18,708 条目)** |
| `use_space_char` | True | **True**（必须） |

## 已验证的 PP-OCRv6 ONNX 参数字典（PostProcess）

```yaml
PostProcess:
  name: DBPostProcess
  thresh: 0.2
  box_thresh: 0.45
  unclip_ratio: 1.4
  max_candidates: 3000
```

## 技术要点

### 为什么不能用 OnnxOCR 捆绑的 ppocrv5 dict

PP-OCRv6 的字符集比 v5 多 361 个条目，必须用 v6 专有字典。
两者共有 18,346 条，v6 独有的 361 条多是 Unicode CJK 扩展区字符。

### 字典提取细节

字典来自 rec 模型 `inference.yml` 的 `PostProcess.character_dict` 字段。
YAML 格式为 block 序列（`  - 'char'`），共 18,708 项。

**注意事项：**
- YAML 中第 1,749 项是 `- 　`（全角空格 U+3000），Python `str.strip()` 会去掉它导致丢失——必须用 `rstrip('\n\r')` 而非 `strip()` 解析。
- UTF-8 编码写入文件，不能用 GBK（会乱码）。
- CTCLabelDecode 会自动在 dict 末尾追加空格（`use_space_char=True`）和开头添加 `"blank"`。

### 使用 OnnxOCR 的初始化参数

```python
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

ocr = ONNXPaddleOcr(
    det_model_dir="models/ppocrv6_onnx/det.onnx",
    rec_model_dir="models/ppocrv6_onnx/rec.onnx",
    rec_char_dict_path="models/ppocrv6_onnx/ppocrv6_dict_proper.txt",
    use_angle_cls=False,          # 不用方向分类器
    det_db_thresh=0.2,
    det_db_box_thresh=0.45,
    det_db_unclip_ratio=1.4,
    max_candidates=3000,
    use_gpu=False,                # CPU 推理
    rec_batch_num=6,
    drop_score=0.5,
    use_space_char=True,          # 必须 True
)
```

### API

```python
results = ocr.ocr(img)  # img: numpy.ndarray (BGR)
# 返回: [[[box, (text, score)], ...]]  — PaddleOCR 兼容格式
```

## 字符字典对比

| | PP-OCRv5 | PP-OCRv6 |
|---|---|---|
| 总条目 | 18,383 | **18,708** |
| CJK 条目 | ~15,565 | ~15,565 |
| 共有 | 18,346 | 18,346 |
| 独有 | 37 | **361** |

## 已知问题

### Python 环境现状

当前 `ONNXPaddleOcr` 安装在 Hermes venv（Python 3.11）：
```
C:\Users\duham\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\onnxocr\
```
需要在项目内重建这个环境，或者确保启动时能导入。

### 遗留问题 A：模块参数不在 UI 显示

原 PaddleOCR 模块（`modules/ocr/ocr_paddleocr_v6.py`）在 UI 下拉菜单中看不到参数控件。
`PP-OCRv6-部署状态记录.md` 记录了可能原因（AST 扫描失败 / import 崩溃 / 注册过滤）。
ONNX 模块可能遇到相同问题，实现时注意验证。

### 遗留问题 B：方向分类器

当前测试 `use_angle_cls=False`。对于竖排日文（manga），可能需要启用方向分类器。
OnnxOCR 默认 cls 模型是 PP-OCRv4 的，PP-OCRv6 没有单独的 cls ONNX 模型。
如果 manga 竖排文字识别不准，需要额外处理。

## 待办事项（给接手 AI）

### 1. 创建正式 OCR 模块

创建 `modules/ocr/ocr_onnx.py`，注册为 `@register_OCR("paddleocr_v6_onnx")`。

参考现有 `ocr_paddleocr_v6.py` 的结构实现 `OCRBase` 接口：
- `_ocr_blk_list(img, blk_list)` — 检测→识别→匹配 TextBlock
- `ocr_img(img)` — 纯识别返回文本
- 参数：`device`（selector），`lang`（selector），可加 `det_db_thresh` 等微调参数
- `requires_packages = ["onnxruntime", "onnxocr"]`
- 不需要子进程隔离（ONNX Runtime 不同 CUDA context 问题）

匹配逻辑参考 `ocr_paddleocr_v6.py` 的 `_match_results_to_blocks()`（用检测框中心点匹配 TextBlock）。
OnnxOCR 返回的坐标格式是 `[x, y]`（浮点），需要转成 `np.ndarray`。

### 2. 清理 PaddlePaddle 依赖

确认 ONNX 模块正常工作后：
```bash
pip uninstall paddlepaddle paddleocr paddlex
rm -rf ~/.paddlex/official_models/
rm -rf ~/.paddleocr/
```
Python 3.13 Microsoft Store 环境中的 Paddle 相关包。

### 3. 将 OCR 环境搬入项目目录

**目标**：项目自包含，不依赖 Hermes venv 或 Microsoft Store Python。

策略选项：
- **方案 A**：项目级 venv（如 `venv/`），安装 `onnxruntime onnxocr opencv-python numpy`
- **方案 B**：使用 `pyproject.toml` 的 `[project.optional-dependencies]` 声明依赖
- **方案 C**：PEX 或 zipapp 打包

推荐方案 A 或 B，与项目现有启动方式一致（参考 `launch.py`）。

### 4. 验证

1. 从 UI 中能选到 `paddleocr_v6_onnx` 并显示参数
2. 跑真实漫画页 OCR（含中文、英文、数字）
3. 可选：对比速度 vs 原 PaddlePaddle 方案
4. 测试日语竖排文字

## 相关文档

- `docs/PP-OCRv6-部署状态记录.md` — PaddlePaddle 方案的状态记录（含 GPU 计划）
- `docs/PP-OCRv6-部署参考文档.md` — PaddleOCR 部署参考
- `modules/ocr/ocr_paddleocr_v6.py` — 现有 PaddlePaddle OCR 模块（待替换）
- `modules/ocr/base.py` — OCRBase 基类
