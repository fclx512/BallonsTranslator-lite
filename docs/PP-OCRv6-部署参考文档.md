# PP-OCRv6 部署参考文档

> 百度 PaddleOCR 最新一代通用文字识别方案  
> 基于 PPLCNetV4 统一骨干，2026-06-11 随 PaddleOCR 3.7.0 正式发布

---

## 一、模型体系说明

PP-OCRv6 提供 **tiny（1.5M）、small（7.7M）、medium（34.5M）** 三档模型。

**关键理解：** 每档包含 **两个独立模型**——文字检测模型（det）和文字识别模型（rec），共 **6 个模型文件**。PaddleOCR 的 `ocr()` 方法内部 = 检测 + 识别两步串行，一次调用完成全流程。

```
PP-OCRv6 (medium)  →  medium_det + medium_rec  =  2 文件
PP-OCRv6_small     →  small_det + small_rec     =  2 文件
PP-OCRv6_tiny      →  tiny_det + tiny_rec       =  2 文件
```

**模型格式**：PaddlePaddle 原生推理格式（`.pdmodel` + `.pdiparams`），同一份文件同时支持 CPU 和 GPU 推理，由框架在运行时选择后端。另有社区提供的 ONNX 导出可供选择，但非必需。

---

## 二、核心指标总表

### 2.1 下载大小

| 模型档位 | 检测模型 | 识别模型 | 合计 | 方向分类器（可选） |
|---------|---------|---------|------|----------------|
| **tiny** | ~2 MB | ~3 MB | **~5 MB** | +~2 MB |
| **small** | ~8 MB | ~14 MB | **~22 MB** | +~2 MB |
| **medium** | ~27 MB | ~78 MB | **~105 MB** | +~2 MB |

### 2.2 参数量

| 模型档位 | 总参数量 | 检测子模型（估） | 识别子模型（估） |
|---------|---------|----------------|----------------|
| **tiny** | **~1.5M** | ~0.6M | ~0.9M |
| **small** | **~7.7M** | ~2.5M | ~5.2M |
| **medium** | **~34.5M** | ~10M | ~24.5M |

### 2.3 推理内存 / 显存

| 模型档位 | GPU VRAM（推理） | CPU RAM（推理） | 最低硬件要求 |
|---------|----------------|---------------|------------|
| **tiny** | **<200 MB** | **~80 MB** | 任何 GPU（含集成显卡）、低端 CPU |
| **small** | **~200–400 MB** | **~150 MB** | 4GB 以下低端 GPU |
| **medium** | **~500–1000 MB** | **~350 MB** | 6GB+ GPU 推荐 |

> **框架固定开销**：PaddlePaddle 框架本身在首次加载时有额外占用。
> - CPU 版（`paddlepaddle`）：~200–400 MB RAM
> - GPU 版（`paddlepaddle-gpu`）：~300–500 MB VRAM + ~200 MB RAM

### 2.4 磁盘总占用（pip 安装 + 模型下载）

| 方案 | 框架 | 模型 | 合计 |
|------|------|------|------|
| CPU + tiny | ~400 MB | ~5 MB | **~0.4 GB** |
| CPU + medium | ~400 MB | ~105 MB | **~0.5 GB** |
| GPU + tiny | ~1.2 GB | ~5 MB | **~1.2 GB** |
| GPU + medium | ~1.2 GB | ~105 MB | **~1.3 GB** |

### 2.5 推理速度

| 硬件 | 后端 | medium | small | tiny |
|------|------|--------|-------|------|
| NVIDIA A100 | PaddlePaddle | **0.29s** | 0.25s | **0.13s** |
| Intel Xeon | OpenVINO | **1.40s** | 0.59s | **0.20s** |
| Apple M4 | PaddlePaddle | 8.82s | 3.07s | 0.96s |
| Apple M4 | ONNX Runtime | **5.55s** | 1.29s | **0.35s** |

> tiny + OpenVINO CPU 达到 **0.20s/张**，中等分辨率（800×600）漫画页可 < 0.5s，完全满足实时/批量处理。

---

## 三、基准配置

### 3.1 Python 版本

| 场景 | 最低 Python |
|------|-------------|
| 纯 OCR（检测+识别） | **3.8+** |
| 文档解析/信息抽取/翻译（可选依赖组） | **3.9+** |

**本项目推荐：** Python 3.10–3.12（与项目现有 Python 3.13 兼容，注意 PaddlePaddle GPU 版对 Python 3.13 的支持情况）。

### 3.2 硬件推荐

| 硬件 | 推荐模型 | 备注 |
|------|---------|------|
| 无 GPU（纯 CPU） | tiny / small | OpenVINO 加速效果明显 |
| 低端 GPU（4GB 以下） | tiny / small | 可使用 PaddlePaddle 推理 |
| 中高端 GPU（6GB+） | medium | 推荐 NVIDIA GPU + CUDA |
| Apple Silicon (M1–M4) | tiny / small / medium | 支持 ONNX Runtime 后端 |

### 3.3 选型建议（本项目漫画场景）

| 你的场景 | 推荐 | 理由 |
|---------|------|------|
| 纯 CPU 推理、低配笔记本 | **tiny + OpenVINO** | 0.20s/张，仅 5MB 模型 |
| 想省磁盘但想质量好点 | **small** | 22MB 模型，质量接近 medium |
| 有 GPU、追求最佳效果 | **medium** | SOTA 精度，超越大 VLM |
| 硬盘空间极度紧张（<1GB） | **tiny 不开 angle_cls** | ~5MB 模型 + 框架裁剪 |

---

## 四、安装

### 4.1 基础安装

```bash
pip install paddleocr>=3.7.0
```

**关键依赖链条：**
```
paddleocr >= 3.7.0
  └── paddlepaddle (CPU) 或 paddlepaddle-gpu (GPU)
```

### 4.2 PaddlePaddle 框架安装

```bash
# CPU 版（自动被 paddleocr 依赖拉取）
pip install paddlepaddle

# GPU 版（CUDA 11/12，需自行安装 CUDA）
# CUDA 11.8:
pip install paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
# CUDA 12.x:
pip install paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu12/
```

### 4.3 可选加速后端

```bash
pip install openvino              # Intel CPU 加速，效果显著
pip install onnxruntime           # CPU 通用加速
pip install onnxruntime-gpu       # GPU 通用加速
pip install tensorrt              # NVIDIA GPU 极致加速
```

### 4.4 本项目中作为可选依赖

PaddleOCR 是 **可选依赖**，不自动安装。用户在 Tools → Check Dependencies 中通过 "Install All (incl. optional)" 安装，或手动 `pip install paddleocr>=3.7.0`。

对应 `pyproject.toml`:

```toml
[project.optional-dependencies]
paddle = ["paddleocr>=3.7.0"]
```

---

## 五、PaddleOCR API 与可调参数

### 5.1 核心参数（当前模块已暴露）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ocr_version` | selector | `PP-OCRv6` | 模型档位：medium / small / tiny |
| `lang` | selector | `ch` | 语言，`ch`=中英文，`japan`=日文漫画 |
| `use_angle_cls` | checkbox | `True` | 文字方向分类 |
| `device` | selector | (自动) | 推理设备 |
| `gpu_mem` | int | 8000 | GPU 显存限制（MB） |

### 5.2 待暴露参数（计划补充）

以下 PaddleOCR 构造函数参数对漫画场景有价值，计划逐步加入 UI：

| 参数 | 默认值 | 作用 | 漫画推荐值 |
|------|--------|------|-----------|
| `det_db_thresh` | 0.3 | 检测得分阈值，越低检出越多假阳性 | 密集文字可降到 0.2 |
| `det_db_box_thresh` | 0.6 | 检测框阈值，越低框越多 | 倾斜文字可降到 0.4 |
| `det_db_unclip_ratio` | 1.5 | 检测框扩展系数，越大框越松 | 文字贴太近可调为 2.0 |
| `rec_batch_size` | 6 | 识别批量大小，越大越快但吃显存 | 显存小设为 2–4 |
| `det_limit_side_len` | 960 | 检测时图像最长边缩放限制 | 大图设为 1280–1600 |
| `use_textline_orientation` | `False` | 文本行方向检测 | **竖排漫画设 `True`** |

### 5.3 快速开始

```python
from paddleocr import PaddleOCR

# 初始化（首次运行自动下载模型）
ocr = PaddleOCR(ocr_version="PP-OCRv6", use_angle_cls=True, lang="ch")

# 推理（检测 + 识别一步到位）
result = ocr.ocr("test_image.png", cls=True)

# 解析结果
for line in result[0]:
    box = line[0]       # 4 个角点坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    text = line[1][0]   # 识别的文本
    confidence = line[1][1]  # 置信度
    print(f"{text} ({confidence:.2f})")
```

### 5.4 新版 API（paddleocr 3.5+）

```python
result = ocr.predict("test_image.png")
for res in result:
    res.print()
    # res.save_to_json("output")
```

---

## 六、接入 BallonsTranslator-lite 的模块架构

### 6.1 模块位置

```
modules/ocr/ocr_paddleocr_v6.py
```

注册名：`@register_OCR("paddleocr_v6")`

### 6.2 设计要点

| 机制 | 说明 |
|------|------|
| `requires_packages` | **空** — 用户通过依赖对话框手动安装 |
| `download_file_list` | **空** — PaddleOCR 自动下载模型到 `~/.paddleocr/` |
| 自动发现 | 文件名 `ocr_paddleocr_v6.py` 匹配模块扫描规则 `ocr_(.*?).py` |

### 6.3 CUDA 冲突处理

PyTorch 和 PaddlePaddle **不能共用同一个 CUDA context**。策略：

| PyTorch 状态 | PaddleOCR 运行方式 |
|-------------|------------------|
| 运行在 CPU | **同进程 CPU 推理** — 设置 `CUDA_VISIBLE_DEVICES=""` 后加载 PaddleOCR |
| 运行在 CUDA | **子进程推理** — `multiprocessing.Process` 隔离，子进程内 PaddleOCR 自由使用 GPU |

```python
# 子进程 worker（模块级函数，确保 Windows 可 pickle）
def _subprocess_worker(img_bytes, ocr_version, lang, use_angle_cls, gpu_mem, queue):
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(ocr_version=ocr_version, lang=lang, use_angle_cls=use_angle_cls, ...)
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    result = ocr.ocr(img, cls=True)
    queue.put(result)
```

### 6.4 TextBlock 匹配逻辑

PaddleOCR 返回整图检测结果，需要匹配到已有 TextBlock：

```python
# 对每个检测结果计算中心点 (cx, cy)
# 遍历 blk_list，找到 xyxy 包含该中心点的 TextBlock
# 将识别文本分配到对应 TextBlock.text
```

### 6.5 后续可扩展方向

1. **参与文本检测管线** — PaddleOCR 的 `ocr.det()` 可替代当前 textdetector 阶段，跳过现有检测器直接获取 bbox
2. **竖排文字优化** — `use_textline_orientation=True` 配合预处理旋转检测
3. **ONNX Runtime 后端** — 避免 PaddlePaddle 框架依赖，减小磁盘占用约 400MB
4. **批量推理** — 多页漫画可并行 OCR，利用子进程模式的独立性

---

## 七、关键注意事项

### ⚠️ 1. PaddleOCR 与 PyTorch 的 CUDA 冲突

同上文 §6.3。子进程隔离是推荐方案。备选方案：
- 使用 PaddleOCR 的 ONNX 导出模式，用 ONNX Runtime 推理（避免 PaddlePaddle 框架依赖）
- 在 CPU 上运行 PaddleOCR（速度慢但无冲突）

### ⚠️ 2. 模型版本与 API 匹配

- `ocr_version="PP-OCRv6"` 需要 paddleocr >= **3.7.0**
- 旧版 `PaddleOCR(use_angle_cls=True, lang='ch')` 默认使用 v4 模型，不会自动升级到 v6

### ⚠️ 3. 首次运行慢

首次 `PaddleOCR()` 会自动从 HuggingFace / 百度 CDN 下载模型。
缓存目录：`~/.paddleocr/`（Linux/macOS）或 `C:\Users\<用户名>\.paddleocr\`（Windows）

### ⚠️ 4. 漫画竖排文本

日本漫画常见竖排文字。PP-OCRv6 的 `use_textline_orientation` 参数默认关闭。
建议开启或在预处理阶段做旋转检测。

### ⚠️ 5. 依赖体积

`pip install paddleocr` 会拉取 paddlepaddle（CPU 版约 400MB）。
如需裁剪：
```bash
pip install paddleocr --no-deps
pip install paddlepaddle==3.0.0
```
但需自行确保依赖完整性。

---

## 八、模型下载链接（HuggingFace）

PP-OCRv6 全部模型集合：https://huggingface.co/collections/PaddlePaddle/pp-ocrv6

| 模型 | HuggingFace 链接 |
|------|-----------------|
| **检测 — medium** | https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det |
| **检测 — small** | https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det |
| **检测 — tiny** | https://huggingface.co/PaddlePaddle/PP-OCRv6_tiny_det |
| **识别 — medium** | https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec |
| **识别 — small** | https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec |
| **识别 — tiny** | https://huggingface.co/PaddlePaddle/PP-OCRv6_tiny_rec |
| **ONNX 检测 medium** | https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx |
| **safetensors 检测 medium** | https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_safetensors |
| **safetensors 识别 medium** | https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_safetensors |

---

## 九、参考链接

- PaddleOCR GitHub：https://github.com/PaddlePaddle/PaddleOCR
- PP-OCRv6 官方文档：https://paddlepaddle.github.io/PaddleOCR/main/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html
- PaddleOCR 安装文档：https://paddlepaddle.github.io/PaddleOCR/main/version3.x/installation.html
- PaddleX 通用 OCR 产线：https://paddlepaddle.github.io/PaddleX/3.3/pipeline_usage/tutorials/ocr_pipelines/OCR.html
- PyPI paddleocr：https://pypi.org/project/paddleocr/
- HuggingFace PP-OCRv6 集合：https://huggingface.co/collections/PaddlePaddle/pp-ocrv6
- arXiv PP-OCRv6 论文：https://arxiv.org/html/2606.13108v1

---

*本文档由 BallonsTranslator-lite 项目维护，供后续 AI agent 阅读。基于 PaddleOCR 3.7.0 / PP-OCRv6 最新发布版本。*
