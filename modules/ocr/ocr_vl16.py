"""PaddleOCR-VL-1.6 recognition of detector-provided text blocks.

Uses the project's existing PyTorch runtime, not PaddlePaddle or a server.
The model revision and Transformers version are fixed to the combination
verified on Windows. Official model code is kept intact on disk; the mask
keyword compatibility adapter is scoped to this model's imported module.
See docs/技术实现/模型文件管理_测试流程.md for installation and comparison.
"""

import inspect
import sys
from functools import wraps

from .base import DEVICE_SELECTOR, register_OCR
from .ocr_vl_manga import PaddleOCRVLManga


@register_OCR("paddleocr_vl_1_6")
class PaddleOCRVL16(PaddleOCRVLManga):
    params = {
        "device": DEVICE_SELECTOR(),
        "max_new_tokens": {
            "type": "selector",
            "options": [128, 256, 512, 1024],
            "value": 256,
            "description": "Maximum output length per text block. Keep 256 for dialogue; try 512 or 1024 if dense text is cut off. Higher limits can take longer.",
        },
        "description": "PaddleOCR-VL-1.6 — multilingual block recognition using local models; GPU recommended.",
    }
    requires_packages = ["transformers==4.57.6", "einops>=0.8.0,<0.9"]
    requires_gpu = True
    background_download_only = True
    model_package = {
        "name": "PaddleOCR-VL-1.6",
        "dir": "data/models/paddleocr_vl_1_6",
        "size_hint": "1.9 GB",
    }
    # Literal paths are required by the lazy metadata scanner. Include the
    # processor, tokenizer and remote code, not just the safetensors weight.
    download_file_list = [
        {
            "url": "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/resolve/c5630abae1d940eafe0697512a0325494b02ab42/",
            "concatenate_url_filename": 1,
            "files": [
                "LICENSE",
                "added_tokens.json",
                "chat_template.jinja",
                "config.json",
                "configuration_paddleocr_vl.py",
                "generation_config.json",
                "image_processing_paddleocr_vl.py",
                "model.safetensors",
                "modeling_paddleocr_vl.py",
                "preprocessor_config.json",
                "processing_paddleocr_vl.py",
                "processor_config.json",
                "special_tokens_map.json",
                "tokenizer.json",
                "tokenizer.model",
                "tokenizer_config.json",
            ],
            "save_files": [
                "data/models/paddleocr_vl_1_6/LICENSE",
                "data/models/paddleocr_vl_1_6/added_tokens.json",
                "data/models/paddleocr_vl_1_6/chat_template.jinja",
                "data/models/paddleocr_vl_1_6/config.json",
                "data/models/paddleocr_vl_1_6/configuration_paddleocr_vl.py",
                "data/models/paddleocr_vl_1_6/generation_config.json",
                "data/models/paddleocr_vl_1_6/image_processing_paddleocr_vl.py",
                "data/models/paddleocr_vl_1_6/model.safetensors",
                "data/models/paddleocr_vl_1_6/modeling_paddleocr_vl.py",
                "data/models/paddleocr_vl_1_6/preprocessor_config.json",
                "data/models/paddleocr_vl_1_6/processing_paddleocr_vl.py",
                "data/models/paddleocr_vl_1_6/processor_config.json",
                "data/models/paddleocr_vl_1_6/special_tokens_map.json",
                "data/models/paddleocr_vl_1_6/tokenizer.json",
                "data/models/paddleocr_vl_1_6/tokenizer.model",
                "data/models/paddleocr_vl_1_6/tokenizer_config.json",
            ],
        },
    ]

    def _prepare_loaded_model(self):
        """Adapt the official code's 4.55 mask keyword to pinned 4.57.6.

        Loading weights alone does not exercise this call: without the
        adapter, the first generate() raises TypeError. Keep it idempotent
        because Transformers caches this module across model unload/reload.
        """
        module = sys.modules[type(self.model).__module__]
        mask = module.create_causal_mask
        if getattr(mask, "_ballontrans_vl16_compat", False):
            return
        parameters = inspect.signature(mask).parameters
        if "inputs_embeds" in parameters:
            return
        if "input_embeds" not in parameters:
            raise RuntimeError(
                "Unsupported PaddleOCR-VL-1.6 attention interface. "
                "Use the matching complete package with transformers==4.57.6."
            )

        @wraps(mask)
        def compatible_mask(*args, **kwargs):
            if "inputs_embeds" in kwargs:
                kwargs["input_embeds"] = kwargs.pop("inputs_embeds")
            return mask(*args, **kwargs)

        compatible_mask._ballontrans_vl16_compat = True
        module.create_causal_mask = compatible_mask

    def _load_model(self):
        try:
            super()._load_model()
        except Exception:
            self.unload_model(empty_cache=True)
            raise

    def updateParam(self, param_key, param_content):
        previous_device = self.get_param_value("device")
        super().updateParam(param_key, param_content)
        if param_key == "device" and self.get_param_value("device") != previous_device:
            self.unload_model(empty_cache=True)

    def _run_batch(self, images):
        # This engine deliberately exposes only single-block decoding.
        if len(images) != 1:
            raise ValueError("PaddleOCR-VL-1.6 expects one text block per call.")
        return super()._run_batch(images)

    def _ocr_blk_list(self, img, blk_list, *args, **kwargs):
        if not blk_list:
            return
        if not self.all_model_loaded():
            self.load_model()
        recognized = []
        for index, block in enumerate(blk_list):
            crop = self.crop_block(img, block)
            if crop is None:
                self.logger.warning(f"Block {index} has no usable quadrilateral, skipped.")
                continue
            # Propagate failures to the pipeline's existing error UI. An
            # empty-looking successful page would hide a broken environment.
            lines = self._run_batch([crop])[0]
            recognized.append((block, lines))
        for block, lines in recognized:
            block.text = lines
