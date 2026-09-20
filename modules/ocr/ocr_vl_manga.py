"""PaddleOCR-VL-For-Manga —— 日文漫画专精的生成式（VLM）识别模块。

模型：``jzhang533/PaddleOCR-VL-For-Manga``（0.9B，Apache-2.0），Manga109-s 文本
区域 crop 微调；Manga109-s 整句准确率 70%（基座同测 27%）。走 HuggingFace
transformers（PyTorch）加载，**不需要** paddlepaddle。定位是「质量优先」的
日文漫画 OCR：逐块自回归解码，比 onnx 系慢约两个数量级，**不要**当默认识别器。

实现要点（调研与实测依据见 ``docs/技术实现/paddle-ocr-for-manga_接入调研.md``）：

1. **整块裁剪，不逐行**。模型在整块文本区域 crop 上训练，块内多行/多列一起喂
   才能借到模型自己的阅读顺序。取块内 ``lines`` 顶点集的轴对齐外接范围
   （``utils/block_geometry.py::poly_bands``，口径同区域再检测），**不做**透视
   校正与重采样：Manga109-s 的训练 crop 就是轴对齐外接框，再采样只会引入网点
   失真。也**不用** ``utils/textblock.py::TextBlock.get_transformed_region``——
   那是渲染用的，会把竖排转成横排，竖排 crop 必须保持原始朝向。
2. **``use_cache`` 必须显式传 True**。权重仓库的 config 与 generation_config 都
   写死 ``use_cache: false``（训练侧习惯），不覆盖则自回归解码不走 KV cache，
   慢数倍。官方两份推理代码都显式传了 True。
3. **``dtype`` 按设备能力选**：Ampere 及以后（CC≥8）用 bf16（权重即 bf16），
   更老的卡用 fp16，CPU 用 fp32。
4. **不做置信度标签**。生成式模型只有 token 概率、没有可信的分数，故
   ``utils/block_tags.py::apply_ocr_confidence_tag`` 在本模块下不启用——
   「OCR 置信度低」自动标签对生成式 OCR 不生效（调研 §4）。
5. **权重走后台下载**（``background_download_only``）：1.9GB 在加载期同步下会
   冻住主界面。缺文件时抛 ``modules.base.MissingModelFilesError``，由 UI 指路到
   设置页 Models → 模型文件。
6. **processor 必须传 ``add_prefix_space=None``**（见 ``_load_model`` 里的长注释）：
   仓库 ``tokenizer_config.json`` 的 ``add_prefix_space: false`` 会把
   ``LlamaTokenizerFast`` 推去走 slow 版转换，缺 sentencepiece 时报
   ``Cannot instantiate this tokenizer from a slow version`` —— 就是 2026-09-20 用户
   跑管线时遇到的「OCR 失败」。两条路径 token id 实测等价，故不必引入 sentencepiece
   依赖；权重更新后重跑 ``scripts/probes/ocr_vl_tokenizer.py`` 再下结论。
"""

import os.path as osp
from typing import List

import numpy as np

from modules.base import BF16_SUPPORTED
from modules.ocr.base import DEVICE_SELECTOR, OCRBase, TextBlock, register_OCR

# ── Paths ────────────────────────────────────────────────────────────────────
# 运行期用这个常量；download_file_list 里必须逐条写全字面量——惰性 AST 扫描
# （utils/lazy_registry.py）不会求值 osp.join / f-string（同 ocr_onnx 的注释）。
_MODEL_DIR = osp.join("data", "models", "paddleocr_vl_manga")

# 必需文件（22 个仓库文件里排除 .gitattributes、README.md 与 5 张 examples/*.png）。
# 其中 4 个 .py 是远端代码，trust_remote_code=True 时会被执行——来源就是这里的
# HF 仓库，别从别处拿。
_REQUIRED_FILES = [
    "added_tokens.json",
    "chat_template.jinja",
    "config.json",
    "configuration_paddleocr_vl.py",
    "generation_config.json",
    "image_processing.py",
    "model.safetensors",
    "modeling_paddleocr_vl.py",
    "preprocessor_config.json",
    "processing_paddleocr_vl.py",
    "processor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
]


@register_OCR("paddleocr_vl_manga")
class PaddleOCRVLManga(OCRBase):
    params = {
        "device": DEVICE_SELECTOR(),
        "max_new_tokens": {
            "value": 256,
            "description": "Maximum tokens to generate per block. 256 is enough for a speech bubble; raise it for dense narration (much slower).",
        },
        "batch_size": {
            "value": 1,
            "description": "Blocks decoded per generate() call. Keep 1 unless you want to experiment: batched left-padding with this model's mrope positions is not verified yet, and a wrong batch is silently a wrong result.",
        },
        "description": "PaddleOCR-VL-For-Manga — Japanese manga OCR quality-first (generative VLM, GPU strongly recommended)",
    }

    # 只声明 transformers：本模块刻意不依赖 onnxocr（裁剪自己算，见模块 docstring）
    requires_packages = ["transformers>=4.57.1,<5"]

    # 只在有加速设备的机器上才放行下载：CPU 上逐块自回归解码没有实用价值（
    # ``_resolve_device`` 里 "impractically slow on CPU" 那句警告就是这件事），
    # 而 1.9GB 权重 + 128MB transformers 都不便宜。本机没有加速设备时，「模型文件」
    # 页点下载与选中模块时的自动补装都会被拒绝并弹窗说明利害——见
    # ``ui/model_downloads.py::gpu_required_message``、``modules/base.py::accelerator_available``。
    requires_gpu = True

    # 1.9GB 权重只能在后台取，加载期只检查（modules/base.py::BaseModule）
    background_download_only = True

    model_package = {
        "name": "PaddleOCR-VL-For-Manga",
        "dir": "data/models/paddleocr_vl_manga",
        "size_hint": "1.9 GB",
    }

    # 一条条目 + 多文件：concatenate_url_filename=1 就是 HF 的 resolve 形态
    # （url + 文件名），这样安装/下载只报一行、不刷屏；镜像由
    # utils/mirror.py::maybe_mirror_url 自动改写，模块侧不用管。
    download_file_list = [
        {
            "url": "https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga/resolve/main/",
            "concatenate_url_filename": 1,
            "files": _REQUIRED_FILES,
            "save_files": [
                "data/models/paddleocr_vl_manga/added_tokens.json",
                "data/models/paddleocr_vl_manga/chat_template.jinja",
                "data/models/paddleocr_vl_manga/config.json",
                "data/models/paddleocr_vl_manga/configuration_paddleocr_vl.py",
                "data/models/paddleocr_vl_manga/generation_config.json",
                "data/models/paddleocr_vl_manga/image_processing.py",
                "data/models/paddleocr_vl_manga/model.safetensors",
                "data/models/paddleocr_vl_manga/modeling_paddleocr_vl.py",
                "data/models/paddleocr_vl_manga/preprocessor_config.json",
                "data/models/paddleocr_vl_manga/processing_paddleocr_vl.py",
                "data/models/paddleocr_vl_manga/processor_config.json",
                "data/models/paddleocr_vl_manga/special_tokens_map.json",
                "data/models/paddleocr_vl_manga/tokenizer.json",
                "data/models/paddleocr_vl_manga/tokenizer.model",
                "data/models/paddleocr_vl_manga/tokenizer_config.json",
            ],
        },
    ]

    _load_model_keys = {"model", "processor"}

    def __init__(self, **params):
        super().__init__(**params)
        self.model = None
        self.processor = None

    # ── 设备 / 精度 ─────────────────────────────────────────────────────

    def _resolve_device(self) -> str:
        device = self.get_param_value("device")
        try:
            import torch

            if device.startswith("cuda") and not torch.cuda.is_available():
                self.logger.warning(
                    "CUDA was selected but is not available — falling back to CPU. "
                    "This model decodes autoregressively and is impractically slow "
                    "on CPU."
                )
                return "cpu"
        except ImportError:
            return "cpu"
        return device

    def _resolve_dtype(self, device: str):
        import torch

        if device.startswith("cuda"):
            # 权重是 bf16；Ampere 以前（CC<8）没有原生 bf16 支持，退 fp16
            return torch.bfloat16 if BF16_SUPPORTED else torch.float16
        if device == "mps":
            return torch.bfloat16 if BF16_SUPPORTED else torch.float32
        return torch.float32

    # ── 模型生命周期 ────────────────────────────────────────────────────

    def _load_model(self):
        """从本地目录加载权重与 processor（离线，不联网）。"""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ImportError as e:
            raise RuntimeError(
                "This OCR module needs the 'transformers' package "
                "(pip install \"transformers>=4.57.1,<5\")."
            ) from e

        model_dir = osp.abspath(_MODEL_DIR)
        if not osp.isdir(model_dir):
            # 正常路径下 BaseModule._ensure_model_files 已经拦过一道
            raise FileNotFoundError(f"Model directory not found: {model_dir}")

        device = self._resolve_device()
        dtype = self._resolve_dtype(device)

        self.model = (
            AutoModelForCausalLM.from_pretrained(
                model_dir,
                trust_remote_code=True,
                dtype=dtype,
                attn_implementation="sdpa",
            )
            .to(device)
            .eval()
        )
        # add_prefix_space=None 是**必需**的，不是可有可无的调优：
        # 仓库 tokenizer_config.json 里写着 ``add_prefix_space: false``，而
        # transformers 的 LlamaTokenizerFast 一见到这个字段非 None，就会覆写
        # from_slow=True（它认为 fast 后端无法事后改前缀空格），于是绕开仓库自带
        # 的 tokenizer.json 去要 sentencepiece；缺它直接抛
        # "Cannot instantiate this tokenizer from a slow version…"。
        # 实测该 tokenizer.json 的 pre_tokenizer 为 null（add_prefix_space 对它
        # 语义上不适用），且「吃 tokenizer.json」与「装 sentencepiece 走 slow→fast
        # 转换」两条路径的 token id 逐条一致（17/17 样本、词表 101314 全同、chat
        # 模板 token 相同）。故传 None 抑制 from_slow 与作者路径等价，还免掉一个
        # sentencepiece 依赖。权重若更新请重跑 scripts/probes/ocr_vl_tokenizer.py。
        self.processor = AutoProcessor.from_pretrained(
            model_dir,
            trust_remote_code=True,
            use_fast=True,
            add_prefix_space=None,
        )
        # 批量生成必须左 padding（右 padding + 贪心解码会跨样本串味）
        tokenizer = getattr(self.processor, "tokenizer", None)
        if tokenizer is not None:
            tokenizer.padding_side = "left"
        self._device = device
        self.logger.info(
            f"PaddleOCR-VL-For-Manga loaded on {device} ({dtype})."
        )

    def unload_model(self, empty_cache=False):
        self.model = None
        self.processor = None
        return super().unload_model(empty_cache=empty_cache)

    # ── 裁剪 ────────────────────────────────────────────────────────────

    @staticmethod
    def crop_block(img: np.ndarray, blk: TextBlock):
        """块的轴对齐外接裁剪（RGB ndarray），取不到顶点返回 ``None``。

        用 ``lines`` 的顶点集而不是 ``xyxy``：含倾斜框时两者不同，而检测器返回的
        DB 四边形实测可倾斜 11.9°~16.7°（见 utils/block_geometry.py 模块 docstring）。
        """
        from utils.block_geometry import poly_bands

        bands = poly_bands(blk)
        if bands is None:
            return None
        h, w = img.shape[:2]
        x1 = max(0, min(int(bands[0]), w - 1))
        y1 = max(0, min(int(bands[1]), h - 1))
        x2 = max(0, min(int(bands[2]), w))
        y2 = max(0, min(int(bands[3]), h))
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None
        # 纯切片＝无重采样，保住网点与细笔画
        return img[y1:y2, x1:x2]

    # ── 推理 ────────────────────────────────────────────────────────────

    def _run_batch(self, images: List[np.ndarray]) -> List[str]:
        """一批 crop 一次 generate，返回每块的多行文本（已按换行 split）。"""
        import torch
        from PIL import Image

        device = getattr(self, "_device", None) or self._resolve_device()
        pil_images = [
            Image.fromarray(np.ascontiguousarray(crop)).convert("RGB")
            for crop in images
        ]
        messages = [
            [
                {"role": "user", "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": "OCR:"},
                ]}
            ]
            for pil_image in pil_images
        ]
        texts = [
            self.processor.apply_chat_template(
                message,
                tokenize=False,
                add_generation_prompt=True,
            )
            for message in messages
        ]
        inputs = self.processor(
            text=texts, images=pil_images, return_tensors="pt"
        )
        inputs = {
            key: (value.to(device) if isinstance(value, torch.Tensor) else value)
            for key, value in inputs.items()
        }
        prompt_len = inputs["input_ids"].shape[1]

        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=int(self.get_param_value("max_new_tokens")),
                do_sample=False,        # 贪心
                use_cache=True,         # 必传：config 里写死 false
            )
        decoded = self.processor.batch_decode(
            output[:, prompt_len:], skip_special_tokens=True
        )

        results = []
        for text in decoded:
            lines = [line.strip() for line in (text or "").splitlines()]
            results.append([line for line in lines if line])
        return results

    def _ocr_blk_list(
        self, img: np.ndarray, blk_list: List[TextBlock], *args, **kwargs
    ):
        if not blk_list:
            return
        if not self.all_model_loaded():
            self.load_model()

        crops: list[np.ndarray] = []
        crop_to_blk: list[int] = []
        for blk_idx, blk in enumerate(blk_list):
            crop = self.crop_block(img, blk)
            if crop is None:
                self.logger.warning(
                    f"Block {blk_idx} has no usable quadrilateral, skipped."
                )
                continue
            crops.append(crop)
            crop_to_blk.append(blk_idx)
        if not crops:
            return

        batch_size = max(1, int(self.get_param_value("batch_size")))
        for start in range(0, len(crops), batch_size):
            chunk = crops[start : start + batch_size]
            chunk_idx = crop_to_blk[start : start + batch_size]
            try:
                texts = self._run_batch(chunk)
            except Exception as e:
                # 一块失败不该让整页 OCR 崩掉——记日志、跳过这一批
                self.logger.error(
                    f"PaddleOCR-VL inference failed for blocks {chunk_idx}: {e}"
                )
                continue
            for blk_idx, lines in zip(chunk_idx, texts):
                if lines:
                    blk_list[blk_idx].text = lines

    def ocr_img(self, img: np.ndarray) -> str:
        self.logger.warning(
            "ocr_img() is not supported in recognition-only mode — "
            "use a text detector + OCR pipeline instead."
        )
        return ""
