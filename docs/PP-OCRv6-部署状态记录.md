# PP-OCRv6 部署状态记录

> 2026-06-14 环境安装与兼容性记录。后续 agent 接手时先读此文档。

## 环境快照

| 项 | 值 |
|----|-----|
| OS | Windows 11 Pro, x64 |
| Python (paddle 所在) | 3.13 (Microsoft Store) |
| Python (shell 默认) | 3.11.15 (Hermes venv) |
| PaddlePaddle | **3.2.0** CPU (降级自 3.3.1) |
| PaddleOCR | **3.7.0** |
| GPU | NVIDIA RTX 5070 (12GB) + CUDA 13.1 驱动 |
| PyTorch | 2.12.0+cu128 (在 Python 3.13 下) |
| 模型缓存 | `~/.paddlex/official_models/` (PP-OCRv6 medium 已下载) |

## 已完成的修改

### 2026-06-14 一轮修复

所有改动在 `modules/ocr/ocr_paddleocr_v6.py` 和 `ui/model_check_dialog.py`。

| 问题 | 修复 |
|---|---|
| PaddlePaddle 3.3.1 oneDNN+PIR bug | 降级到 **3.2.0** — 旧 IR 不触发 bug |
| `ocr.ocr()` API 已弃用 | 改为 `ocr.predict()`，返回格式改为 `result[0].get('res')['rec_texts']` |
| `use_angle_cls` / `use_textline_orientation` 互斥 | 去掉 `use_angle_cls`，统一用 `use_textline_orientation` |
| 子进程误判（CPU paddle 也走子进程） | `_detect_subprocess_needed()` 加 `paddle.is_compiled_with_cuda()` 检查 |
| 依赖对话框只扫 `~/.paddleocr/whl/` | `_scan_paddleocr_cache()` 额外扫描 `~/.paddlex/official_models/` |

### 独立验证通过的

```bash
# PaddlePaddle 可用
python -c "import paddle; paddle.utils.run_check()"
# → PaddlePaddle is installed successfully!

# PaddleOCR predict() API 可用
python -c "
from paddleocr import PaddleOCR
ocr = PaddleOCR(ocr_version='PP-OCRv6', lang='ch')
result = ocr.predict(img)
print(result[0].get('res')['rec_texts'])
"
# → ['Hello PaddleOCR']
```

## 遗留问题（待后续 agent 处理）

### 问题 A：模块参数不在 UI 显示

**症状：** OCR 下拉框能选到 "paddleocr_v6"，但下方没有参数控件（无 device/version/lang 等选择器）。

**可能原因（未验证）：**

1. **AST 扫描失败** — `utils/lazy_registry.py` 的 `_scan_file()` 在启动时用 Python `ast` 模块静态解析 `ocr_paddleocr_v6.py`，提取 `params` dict。如果解析失败，模块注册为 `ModuleSpec` 但没有 `params`。确认方式：
   - 在 `_scan_file()` 加 try/except 看是否抛异常
   - 或在启动后 `print(OCR.get("paddleocr_v6").params)` 看是不是空的

2. **模块 import 时崩溃** — `OCR.resolve_module("paddleocr_v6")` 首次 import 模块文件。如果 import 报错（如缺少 paddlepaddle），注册不会回滚但模块实例化会失败。确认方式：
   - 手动 `from modules.ocr.ocr_paddleocr_v6 import PaddleOCRv6` 看是否抛异常
   - 检查 `_load_model_keys` 的守卫逻辑（`{"_paddle_ocr" if False else "_dummy"}`）

3. **模块未注册到 UI 列表** — `GET_VALID_OCR()` 返回的列表里可能没有 paddleocr_v6，或 `merge_config_module_params` 时被过滤。确认方式：
   - 在 `ModuleManager.setupThread()` 断点 `GET_VALID_OCR()` 和 `OCR.get("paddleocr_v6")`

### 问题 B：依赖对话框仍显示模型未下载

**症状：** Tools → Check Dependencies 中 paddleocr_v6 的模型条目标红（missing）。

**可能原因：**

1. `_build_paddleocr_custom_checks()` 在模块加载时调用一次，缓存的扫描结果没有更新。需要重启应用使新代码生效。

2. 如果重启后仍不行，检查 `_scan_paddleocr_cache()` 在 PaddleX 路径下的键名是否匹配 `_PPOCR_MODELS` 的 `file` 字段（预期是 `PP-OCRv6_medium_det` 等）。

### 问题 C：GPU 推理未配置

**现状：** 当前 paddlepaddle 是 CPU 版。RTX 5070 可用，但 GPU 推理需要额外步骤。

详见下方「GPU 推理计划」。

### 模块代码待补充

`modules/ocr/ocr_paddleocr_v6.py` 目前暴露的参数：
- `device`（selector）
- `ocr_version`（selector）
- `lang`（selector）
- `use_textline_orientation`（checkbox）
- `gpu_mem`（int）

**还需暴露的 PaddleOCR 参数**（详见 `docs/PP-OCRv6-部署参考文档.md` §5.2）：
- `det_db_thresh` — 检测得分阈值
- `det_db_box_thresh` — 检测框阈值
- `det_db_unclip_ratio` — 检测框扩展系数
- `rec_batch_size` — 识别批量大小
- `det_limit_side_len` — 检测时图像最长边缩放限制

---

## PP-OCRv6 ONNX Runtime 方案计划

### 为什么需要 ONNX

彻底避免 PaddlePaddle 框架依赖：
- 不用降级、不用踩 oneDNN+PIR 的坑
- 节省 ~400MB 磁盘（paddlepaddle 框架）
- 子进程隔离不再需要（ONNX Runtime 和 PyTorch 没有 CUDA context 冲突）
- 推理速度可能更快（ONNX Runtime 优化）

### 现有方案评估

| 方案 | pip 包 | 优点 | 缺点 |
|---|---|---|---|
| **OnnxOCR** | `onnxocr` | API 最接近 PaddleOCR，直接替换 | 需验证是否支持 PP-OCRv6；社区维护 |
| **RapidOCR** | `rapidocr-onnxruntime` | 轻量、活跃、成熟 | API 不同，需适配；默认用轻量模型 |
| **手动 ONNX Runtime** | `onnxruntime` + 自定义 | 完全控制管线 | 需实现 DB 后处理、CTC 解码等 |
| **速查：** 优先评估 OnnxOCR 和 RapidOCR，两者都支持 PP-OCRv6 模型。

### 计划步骤

#### Step 1：调研候选库（预计 < 1 天）

- 看 OnnxOCR GitHub：https://github.com/longyoudong/OnnxOCR
  - 是否支持 PP-OCRv6 的模型
  - API 是否容易适配现有模块
- 看 RapidOCR GitHub：https://github.com/RapidAI/RapidOCR
  - 是否支持 PP-OCRv6 ONNX 模型
  - 检测+识别精度对比
- 主要判断标准：**能否用 HuggingFace 上的 PP-OCRv6 ONNX 模型**（https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx）

#### Step 2：原型验证（预计 < 0.5 天）

```bash
pip install onnxocr  # 或 rapidocr-onnxruntime
```

用一张漫画页测试：
```python
# OnnxOCR
from onnxocr import ONNXPaddleOcr
ocr = ONNXPaddleOcr()
result = ocr.ocr("test_page.jpg")

# RapidOCR
from rapidocr_onnxruntime import RapidOCR
ocr = RapidOCR()
result, elapse = ocr("test_page.jpg")
```

#### Step 3：模块代码适配（预计 1 天）

创建新模块文件 `modules/ocr/ocr_onnx.py`（或改造现有 `ocr_paddleocr_v6.py`）：
- 注册名：`@register_OCR("paddleocr_v6_onnx")` 或重写现有模块
- `requires_packages = ["onnxruntime", "opencv-contrib-python"]`
- 根据选定的 ONNX 库实现 `_ocr_blk_list()` 和 `ocr_img()`
- 参数：保持与现有 `ocr_paddleocr_v6.py` 一致的参数集
- 不需要子进程隔离逻辑（ONNX Runtime 和 PyTorch 不冲突）

#### Step 4：验证（预计 < 0.5 天）

- 在 BallonsTranslator-lite 内加载 ONNX 模块
- 跑真实漫画页 OCR
- 对比精度和速度 vs PaddlePaddle 方案

### 模型下载

PP-OCRv6 ONNX 模型已由 PaddlePaddle 官方提供：
- https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx
- https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx

不需要额外转换。

### 风险

- ONNX 模型可能与 PaddlePaddle 原生模型有微小精度差异
- 竖排文字（manga 垂直 text）的支持取决于所选库的实现
- 如果 OnnxOCR/RapidOCR 不支持 PP-OCRv6 的某些算子，可能需要降级用 PP-OCRv4 ONNX

---

## GPU 推理计划

### 方案 A：ONNX Runtime GPU（推荐）

与 ONNX 迁移合并实施。只需装 `onnxruntime-gpu` 替代 `onnxruntime`，然后在模型加载时指定 `providers=['CUDAExecutionProvider']`。

### 方案 B：PaddlePaddle GPU

如果 ONNX 方案出现问题，备选：

```bash
pip uninstall paddlepaddle
pip install paddlepaddle-gpu==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```

但需注意：
- 子进程隔离逻辑已写好，但需要验证子进程内 GPU 推理是否正常工作
- 磁盘额外 ~1.2GB
- GPU 版 paddlepaddle 无 oneDNN bug，所以不用降级到 3.2.0——如果能用 3.3.x GPU 版，可能更好

---

## 参考资料

- PaddlePaddle 降级讨论：https://github.com/PaddlePaddle/PaddleOCR/discussions/17350
- PP-OCRv6 ONNX 模型集合：https://huggingface.co/collections/PaddlePaddle/pp-ocrv6
- PaddleOCR 3.7.0 文档：https://paddlepaddle.github.io/PaddleOCR/main/
- OnnxOCR：https://github.com/longyoudong/OnnxOCR
- RapidOCR：https://github.com/RapidAI/RapidOCR
- 部署参考文档：`docs/PP-OCRv6-部署参考文档.md`
- 模块代码：`modules/ocr/ocr_paddleocr_v6.py`
