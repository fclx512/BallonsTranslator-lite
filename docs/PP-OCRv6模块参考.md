# PP-OCRv6 ONNX 模块参考

## 概述

`paddleocr_v6_onnx` 是 BallonsTranslator-lite 的 OCR 模块，通过 **ONNX Runtime** 运行 PP-OCRv6 medium 模型，彻底摆脱 PaddlePaddle 框架依赖（省 ~400MB）及其与 PyTorch 的 CUDA context 冲突。

**模块文件：** `modules/ocr/ocr_onnx.py`

---

## 依赖

| 包 | 说明 |
|---------|------|
| `onnxruntime` | CPU 推理（必装） |
| `onnxruntime-gpu` | GPU 推理（可选，替换 onnxruntime） |
| `onnxocr` | 第三方 PP-OCR ONNX 封装 |

安装方式：

```bash
pip install -e ".[onnx]"
# onnxocr 需要 --no-deps（numpy 版本声明冲突）：
pip install onnxocr --no-deps
```

---

## 模型文件

medium 模型在首次使用时自动下载（共 2 个 ONNX 文件 + 1 个字典文件）：

### 文件列表

| 文件 | 大小 | 参数 | 来源 |
|------|------|------|------|
| `data/models/ppocrv6_onnx/medium/det.onnx` | 60 MB | 22M | HuggingFace — PP-OCRv6 medium 检测模型 |
| `data/models/ppocrv6_onnx/medium/rec.onnx` | 74 MB | 19.2M | HuggingFace — PP-OCRv6 medium 识别模型 |
| `data/models/ppocrv6_onnx/ppocrv6_dict_proper.txt` | 18 KB | 18,708 字符 | GitHub raw — 字符字典 |

HuggingFace 来源：

- <https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx>
- <https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx>

---

## 参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `device` | 下拉框 | 自动 | `cpu` / `cuda` |
| `lang` | 下拉框 | `ch` | 语言（ch, en, japan, korean, french 等 12 种） |
| `model_size` | 下拉框 | `medium` | 模型尺寸（仅 medium） |
| `det_db_thresh` | 浮点数 | **0.2** | 检测得分阈值（越低检出越多） |
| `det_db_box_thresh` | 浮点数 | **0.45** | 检测框阈值（越低框越多） |
| `det_db_unclip_ratio` | 浮点数 | **1.4** | 检测框扩展系数（越大框越松） |
| `max_candidates` | 整数 | **3000** | 最大候选检测框数 |
| `reading_order` | 下拉框 | `auto` | 阅读顺序：`auto` / `ltr` / `rtl` |
| `rec_batch_num` | 整数 | 6 | 识别批大小（调节以适配显存） |

**注意：** 以上推荐值与 onnxocr 的默认值不同——PP-OCRv6 medium 模型需要使用上述参数才能达到最佳效果。

---

## 阅读顺序（漫画竖排）

`reading_order` 参数处理日文漫画的竖排文字布局：

- **`ltr`**：上到下、左到右（标准横排）
- **`rtl`**：右到左、上到下（漫画竖排）
- **`auto`**（默认）：自动检测——如果大多数字框高度>宽度，判定为竖排用 RTL；否则用 LTR

实现在 `_sort_detections()`（对检测框中心点排序）。

---

## GPU 推理

### 安装

```bash
pip install onnxruntime-gpu --prefer-binary
```

### 工作原理

1. `_ensure_cuda_dll_path()` 将 PyTorch 的 `torch/lib/` 目录加入 Windows DLL 搜索路径（`cublasLt64_12.dll`、cuDNN 9 等）
2. `get_onnx_session` 被 monkey-patch，使用 `CUDAExecutionProvider` +
   `cudnn_conv_algo_search=EXHAUSTIVE`（onnxocr 默认的 `DEFAULT` 会导致极慢的 Conv fallback：203 ms vs 11.6 ms）
3. `device=cuda` 时加载模型并应用两个性能 patch：
   - **固定宽度 patch：** 将 `resize_norm_img` 输出宽度固定为 320，防止动态宽度冲掉 CUDA kernel 缓存
   - **统一批大小 patch：** 最后一批不足 `rec_batch_num` 时填充到整批，避免批维度变化触发 kernel 重编译

### 性能预期（独立测试已验证）

| 场景 | 首次调用 | 后续每页 |
|----------|-----------|-----------------|
| 仅检测 | ~1.4 s | ~26 ms |
| 检测+识别（两个 patch） | ~3.4 s | **~80 ms** |

## 调用流程

```
用户选择 paddleocr_v6_onnx
  → ModuleManager._set_module()
    → load_model()
      → _ensure_cuda_dll_path()        (仅 GPU)
      → patch get_onnx_session         (cudnn_conv_algo_search)
      → 创建 ONNXPaddleOcr 实例
      → 应用 GPU 性能 patch             (仅 GPU)
  → ocr_img() / _ocr_blk_list()
    → _sort_detections()               (阅读顺序)
    → _match_results_to_blocks()       (中心点匹配)
```

---

## 文件结构

```
data/models/ppocrv6_onnx/
├── medium/
│   ├── det.onnx                 60 MB — DB 检测模型
│   └── rec.onnx                 74 MB — SVTR_LCNet 识别模型
└── ppocrv6_dict_proper.txt    18,708 字符 — 字符字典

modules/ocr/
└── ocr_onnx.py                 ← 本模块 (paddleocr_v6_onnx)

> **已移除：** 旧 PaddlePaddle 版（`ocr_paddleocr_v6.py`）已在 2026-06-15 清理——PaddlePaddle 依赖过重，不再安装。
>
```

---
