# PP-OCRv6 集成审计报告

> 最后更新：2026-06-11 | 状态：待验证

---

## 1. 版本澄清

讨论中 "v6" 可能指以下两者，本文仅关注 PP-OCRv6：

| 混淆项 | 实际名称 | 说明 |
|--------|---------|------|
| **PP-OCRv6** | 模型系列 | 本文主题。检测模型已集成到 HuggingFace Transformers（PyTorch safetensors，无需 PaddlePaddle） |
| **PaddleOCR-VL-1.6** | VLM (0.9B) | 太重（~8.5GB VRAM），不纳入计划 |

本项目曾有三个 PaddleOCR 模块（`ocr_paddle`、`ocr_paddle_VL`、`ocr_paddleVL_manga`），在"精简模块"清理中被删除。

---

## 2. 已知信息

**已确认：**
- 检测模型 `PP-OCRv6_medium_det` 已在 HuggingFace Transformers 以 safetensors 格式提供
  - 仓库：`PaddlePaddle/PP-OCRv6_medium_det_safetensors`
  - 加载：`AutoModelForObjectDetection.from_pretrained()`
  - 需 Transformers ≥v5.10.2 或 main 分支
- 推理速度参考：~30ms (RTX 4060)

**待验证：**
- [ ] `paddleocr` pip 包是否支持 `ocr_version='PP-OCRv6'`？→ 执行 `pip install paddleocr --upgrade && python -c "from paddleocr import PaddleOCR; PaddleOCR(ocr_version='PP-OCRv6')"`
- [ ] 完整模型系列（mobile / medium / server）及精确文件大小
- [ ] 识别模型 PP-OCRv6_rec 是否有 PyTorch 版？若无，仍需 PaddlePaddle

---

## 3. 部署空间

PP-OCRv6 模型文件未公开大小，以 PP-OCRv5 为基准估算：

| 方案 | 额外磁盘 | GPU 显存 | 说明 |
|------|---------|---------|------|
| **A: 原生 Paddle** | ~1.0-1.5 GB | ~300-500 MB | PaddlePaddle 框架 ~717MB 是主要成本 |
| **B: Transformers 检测** | ~30-50 MB+? | ~200-300 MB | 识别模型状态未知，方案不完整 |

**结论**：方案 A 可行但代价明确（700MB+ 框架），方案 B 绕过 PaddlePaddle 的前提是识别模型也有 PyTorch 版。

---

## 4. 适配工作量

### 新增/修改文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/ocr/ocr_paddle_v6.py` | ~100 | PP-OCRv6 模块主体 |
| `modules/base.py` | ~50 | `ensure_dependencies()` 增加进度信号和错误信息 |
| `ui/configpanel.py` | ~80 | OCR 切换时触发依赖预检，缺失时弹出安装向导 |
| `ui/dependency_dialog.py` 或新建 | ~120 | 模块级依赖安装向导 |
| `pyproject.toml` | ~5 | 添加可选依赖组 `[paddle]` |

### 模块代码骨架

```python
@register_OCR("paddle_v6")
class PaddleV6OCR(OCRBase):
    requires_packages = ["paddlepaddle>=3.0.0", "paddleocr>=3.0.3"]

    params = {
        "det_model_dir": {"type": "text", "value": "", "description": "Detection model dir (optional)"},
        "rec_model_dir": {"type": "text", "value": "", "description": "Recognition model dir (optional)"},
        "use_gpu": {"type": "bool", "value": True, "description": "Use GPU"},
        "lang": {"type": "selector", "options": ["ch","en","japan","korean","fr","german"],
                 "value": "ch", "description": "Language"},
        "device": DEVICE_SELECTOR(),
        "description": "PP-OCRv6",
    }

    def _load_model(self):
        from paddleocr import PaddleOCR
        self._ocr_engine = PaddleOCR(
            det_model_dir=self.get_param_value("det_model_dir") or None,
            rec_model_dir=self.get_param_value("rec_model_dir") or None,
            use_gpu=self.get_param_value("use_gpu") and torch.cuda.is_available(),
            lang=self.get_param_value("lang"),
            ocr_version="PP-OCRv6",  # 需验证
        )

    def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
        result = self._ocr_engine.ocr(img)
        # 按坐标匹配 result 到 blk_list

    def ocr_img(self, img):
        result = self._ocr_engine.ocr(img)
        return "\n".join(line[1][0] for line in result[0] if line)
```

---

## 5. 依赖管理器改进

**现状问题**：`ensure_dependencies()` 阻塞安装无 UI 反馈，失败仅打日志，模块选择时无预检。

**改进目标（用户选择 PP-OCRv6 时的流程）：**

1. 扫描 `requires_packages`，检测已安装状态
2. 有缺失 → 弹出安装向导：显示包名 + 大小，提供一键安装 / 取消回退
3. 安装成功 → 继续加载；安装失败 → 显示排障建议（网络镜像 / Python 版本 / CUDA 兼容 / VC++ 运行时）
4. 安装过程有进度条

**主要改动**：`ensure_dependencies()` 加信号通知 → `configpanel.py` 拦截模块切换 → 弹窗交互。

---

## 6. 验证清单（需另一台电脑）

- [ ] paddleocr.ai 确认 PP-OCRv6 文档和模型列表
- [ ] `paddleocr` pip 最新版对 PP-OCRv6 的支持状态
- [ ] 模型文件精确大小和下载方式
- [ ] `ocr_version='PP-OCRv6'` 参数是否有效，无效则需查替代方式

确认后即可按第 4-5 节开始实现。

---

## 参考链接

- HF Transformers 文档：<https://huggingface.co/docs/transformers/main/model_doc/pp_ocrv6_medium_det>
- HF 模型仓库：<https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_safetensors>
- PaddleOCR GitHub：<https://github.com/PaddlePaddle/PaddleOCR>
- 本项目依赖管理：`modules/base.py` (`ensure_dependencies()`) + `ui/dependency_dialog.py`
