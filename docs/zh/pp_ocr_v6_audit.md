# PP-OCRv6 集成审计报告

> 最后更新：2026-06-11 | 状态：已验证可实施

---

## 1. 版本澄清

讨论中 "v6" 可能指以下两者，本文仅关注 PP-OCRv6：

| 混淆项 | 实际名称 | 说明 |
|--------|---------|------|
| **PP-OCRv6** | 模型系列 | 本文主题。今天（2026-06-11）随 PaddleOCR v3.7.0 正式发布 |
| ~~PaddleOCR-VL-1.6~~ | VLM (0.9B) | 太重（~8.5GB VRAM），不纳入计划 |

本项目曾有三个 PaddleOCR 模块（`ocr_paddle`、`ocr_paddle_VL`、`ocr_paddleVL_manga`），在"精简模块"清理中被删除。

---

## 2. 已验证信息 ✅

### 2.1 PaddleOCR v3.7.0 已正式发布 PP-OCRv6

- **发布日期**：2026-06-11（今天）
- **发布版本**：PaddleOCR v3.7.0
- **GitHub**：[PaddleOCR v3.7.0 release](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)

**paddleocr pip 包原生支持 PP-OCRv6**（已验证源码）：


```python
# paddleocr/_pipelines/ocr.py
_SUPPORTED_OCR_VERSIONS = ["PP-OCRv3", "PP-OCRv4", "PP-OCRv5", "PP-OCRv6"]
```

使用方式：
```python
from paddleocr import PaddleOCR
ocr = PaddleOCR(ocr_version="PP-OCRv6", lang="japan")
# 或显式指定模型名：
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv6_medium_det",
    text_recognition_model_name="PP-OCRv6_medium_rec",
)
```

> **注意**：paddleocr v3.7.0 架构重构，旧参数名（`det_model_dir`、`rec_model_dir`）已废弃，映射到新名（`text_detection_model_dir`、`text_recognition_model_dir`），仍兼容但发警告。

### 2.2 三档模型规格

| 档位 | 参数量 | 场景 | 加速表现 |
| --- | --- | --- | --- |
| tiny | 1.5M | edge | Apple M4 6.1× |
| small | 7.7M | mobile | — |
| medium | 34.5M | server | 5.2× CPU, 0.13s A100 |

对照旧版估算：medium 精度比 PP-OCRv5_server 检测提升 +4.6%，识别提升 +5.1%。

### 2.3 识别模型可用性

- **检测模型 ✅**：`PP-OCRv6_medium_det` — 默认模型名
- **识别模型 ✅**：`PP-OCRv6_medium_rec` — 默认模型名（`paddleocr/_models/text_recognition.py`）
- 两个模型都使用 PaddleX 后端统一管理下载和推理

### 2.4 语言覆盖

- 50 种语言统一：中文、英文、日文 + 46 种拉丁语系语言
- 无需为不同语种切换模型
- 特别排除：`pi`（Pali）不受 PP-OCRv6 支持

### 2.5 专业场景增强

- 数码显示屏
- 点阵字符
- 轮胎印字
- 工业字符识别

---

## 3. 待确认信息 ❓

| 项目 | 状态 | 说明 |
|------|------|------|
| 模型文件精确大小 | **未获取** | HuggingFace 被企业网络限制，无法获取 safetensors 文件大小 |
| PP-OCRv6_rec 纯 PyTorch 版 | **不存在** | 识别模型需 PaddlePaddle/PaddleX 后端；只有检测模型有 HF Transformers 版 (`PP-OCRv6_medium_det_safetensors`) |
| tiny/small 档模型名 | **待查** | 完整的模型名称列表（含 tiny/small）需从 PaddleX 配置中提取 |

---

## 4. 部署空间

PP-OCRv6 模型文件未公开大小，以 PP-OCRv5 为基准估算。**注意 PP-OCRv6 使用 PaddleX 管理模型下载，不再需要完整的 PaddlePaddle 框架安装**：

| 方案 | 额外磁盘 | GPU 显存 | 说明 |
|------|---------|---------|------|
| **A: paddleocr v3.7.0 (推荐)** | ~500-800 MB | ~200-400 MB | 使用 PaddleX 推理后端 |
| **B: Transformers 检测**       | ~30-50 MB  | ~200-300 MB | 仅检测，识别仍需 Paddle 后端 |

**结论**：方案 A 是唯一的完整方案，且 PaddleX 后端比旧版 PaddlePaddle 更轻量。

---

## 5. 适配工作量

### 新增/修改文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/ocr/ocr_paddle_v6.py` | ~100 | PP-OCRv6 模块主体 |
| `modules/base.py` | ~50 | `ensure_dependencies()` 增加进度信号和错误信息 |
| `ui/configpanel.py` | ~80 | OCR 切换时触发依赖预检，缺失时弹出安装向导 |
| `ui/dependency_dialog.py` 或新建 | ~120 | 模块级依赖安装向导 |
| `pyproject.toml` | ~5 | 添加可选依赖组 `[paddle]` |

### 模块代码骨架（根据 paddleocr v3.7.0 新 API 更新）

```python
@register_OCR("paddle_v6")
class PaddleV6OCR(OCRBase):
    requires_packages = ["paddleocr>=3.7.0"]

    params = {
        "text_detection_model_dir": {"type": "text", "value": "",
            "description": "Detection model dir (optional)"},
        "text_recognition_model_dir": {"type": "text", "value": "",
            "description": "Recognition model dir (optional)"},
        "use_textline_orientation": {"type": "bool", "value": True,
            "description": "Use textline orientation classification"},
        "lang": {"type": "selector",
            "options": ["ch","en","japan","korean","fr","de","it","es","pt","ru"],
            "value": "ch", "description": "Language"},
        "ocr_version": {
            "type": "hidden", "value": "PP-OCRv6",
            "description": "OCR version (fixed to PP-OCRv6)"},
    }

    def _load_model(self):
        from paddleocr import PaddleOCR
        self._ocr_engine = PaddleOCR(
            text_detection_model_dir=self.get_param_value("text_detection_model_dir") or None,
            text_recognition_model_dir=self.get_param_value("text_recognition_model_dir") or None,
            use_textline_orientation=self.get_param_value("use_textline_orientation"),
            lang=self.get_param_value("lang"),
            ocr_version="PP-OCRv6",
        )

    def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
        result = self._ocr_engine.ocr(img)
        # 按坐标匹配 result 到 blk_list

    def ocr_img(self, img):
        result = self._ocr_engine.ocr(img)
        return "\n".join(line[1][0] for line in result[0] if line)
```

---

## 6. 依赖管理器改进

**现状问题**：`ensure_dependencies()` 阻塞安装无 UI 反馈，失败仅打日志，模块选择时无预检。

**改进目标（用户选择 PP-OCRv6 时的流程）：**

1. 扫描 `requires_packages`，检测已安装状态
2. 有缺失 → 弹出安装向导：显示包名 + 大小，提供一键安装 / 取消回退
3. 安装成功 → 继续加载；安装失败 → 显示排障建议（网络镜像 / Python 版本 / CUDA 兼容 / VC++ 运行时）
4. 安装过程有进度条

**主要改动**：`ensure_dependencies()` 加信号通知 → `configpanel.py` 拦截模块切换 → 弹窗交互。

---

## 7. 已知风险

| 风险 | 说明 |
|------|------|
| **paddleocr v3.7.0 API 变更** | 参数名整体重构，旧版 `det_model_dir` 等废弃。模块实现需使用新 API |
| **PaddleX 依赖** | 模型下载由 PaddleX 管理，首次运行自动下载（需网络）。用户无法控制下载位置 |
| **企业网络限制** | HuggingFace 等域名可能被拦截，影响模型自动下载 |
| **GPU 兼容性** | PaddlePaddle GPU 版对 CUDA 版本有要求，需在安装前检测 |

---

## 参考链接

- PaddleOCR v3.7.0 Release：<https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0>
- PaddleOCR GitHub：<https://github.com/PaddlePaddle/PaddleOCR>
- HF Transformers 文档：<https://huggingface.co/docs/transformers/main/model_doc/pp_ocrv6_medium_det>
- HF 模型仓库：<https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_safetensors>
- 本项目依赖管理：`modules/base.py` (`ensure_dependencies()`) + `ui/dependency_dialog.py`
