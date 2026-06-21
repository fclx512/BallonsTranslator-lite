"""PP-OCRv6 ONNX Runtime OCR module — recognition‑only mode.

Uses ``TextRecognizer`` from the ``onnxocr`` package to run PP-OCRv6
recognition via ONNX Runtime.  Operates on pre‑detected text blocks
(cropped regions), *not* the full image.

The recognition model is bundled under ``data/models/ppocrv6_onnx/medium/``
and auto‑downloaded on first use.
"""

import os
import os.path as osp
from typing import List

import numpy as np

from modules.ocr.base import DEVICE_SELECTOR, OCRBase, TextBlock, register_OCR

# ── Paths ────────────────────────────────────────────────────────────────────

_MODEL_DIR = osp.join("data", "models", "ppocrv6_onnx")
_MODEL_SIZE = "medium"


# ── Module registration ──────────────────────────────────────────────────────


@register_OCR("paddleocr_v6_onnx")
class PaddleOCRv6ONNX(OCRBase):
    params = {
        "device": DEVICE_SELECTOR(),
        "model_size": {
            "type": "selector",
            "options": [_MODEL_SIZE],
            "value": _MODEL_SIZE,
            "description": "Model size (only medium available)",
        },
        "rec_batch_num": {
            "value": 6,
            "description": "Recognition batch size (higher = faster, more VRAM)",
        },
        "description": "PP-OCRv6 ONNX recognition-only — crops text blocks then recognizes via ONNX Runtime",
    }

    requires_packages = ["onnxruntime", "onnxocr"]

    # Note: paths use "data/models/ppocrv6_onnx/" directly instead of _MODEL_DIR
    # because the lazy AST scanner (SafeEval) cannot resolve string
    # interpolation or osp.join at scan time.
    download_file_list = [
        {
            "url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/main/inference.onnx",
            "files": "data/models/ppocrv6_onnx/medium/rec.onnx",
        },
    ]

    _load_model_keys = {"recognizer"}

    def __init__(self, **params):
        super().__init__(**params)
        self.recognizer = None

    # ── Model path helpers ───────────────────────────────────────────────

    @staticmethod
    def _model_path(name: str) -> str:
        """Resolve path to a model file in the model-size subdirectory."""
        return osp.abspath(osp.join(_MODEL_DIR, _MODEL_SIZE, name))

    # ── Device / param updates ──────────────────────────────────────────

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        self.recognizer = None  # force re-init on next call

    # ── Model lifecycle ─────────────────────────────────────────────────

    @staticmethod
    def _ensure_cuda_dll_path():
        """Add PyTorch's CUDA 12.x runtime DLLs to the Windows search path.

        onnxruntime-gpu loads ``onnxruntime_providers_cuda.dll`` at session
        creation, which dynamically links against ``cublasLt64_12.dll``
        and other CUDA 12 / cuDNN 9 DLLs.  PyTorch ships these in its
        ``lib/`` directory, so we add that directory to the loader's search
        path so the CUDA ExecutionProvider can initialise successfully.
        """
        try:
            import torch
        except ImportError:
            return  # torch not installed — CUDA EP won't work; fine
        lib_dir = osp.join(osp.dirname(torch.__file__), "lib")
        if osp.isdir(lib_dir):
            os.add_dll_directory(lib_dir)

    def _load_model(self):
        """Initialise the OnnxOCR recognizer.

        Called by ``BaseModule.load_model()`` after dependency & model-file
        checks have passed.  Only loads the recognition model (``rec.onnx``);
        text detection is handled by a separate detector module.
        """
        # 1. Add PyTorch's bundled CUDA 12.x runtime DLLs to the DLL search
        #    path.  onnxruntime-gpu needs ``cublasLt64_12.dll`` etc. at runtime,
        #    and PyTorch ships them in ``torch/lib/``.  Without this the CUDA
        #    ExecutionProvider fails to load and silently falls back to CPU.
        self._ensure_cuda_dll_path()

        # 2. Monkey-patch onnxocr's CUDA provider config *before* any session
        #    is created.  onnxocr hardcodes ``cudnn_conv_algo_search="DEFAULT"``
        #    which limits available CUDA kernels — PP-OCRv6 models have ~120
        #    Conv ops that fall back to extremely slow generic implementations
        #    (203 ms vs. 11.6 ms with EXHAUSTIVE).
        import onnxruntime as _ort

        _providers = _ort.get_available_providers()
        _use_gpu = self.get_param_value("device") == "cuda"
        if _use_gpu and "CUDAExecutionProvider" not in _providers:
            self.logger.warning(
                "CUDA device selected but onnxruntime CUDA provider not found.\n"
                "  Install onnxruntime-gpu for GPU acceleration:\n"
                "    pip install onnxruntime-gpu\n"
                "  Falling back to CPU for this session."
            )

        from onnxocr import predict_base as _pb

        _orig_session = _pb.PredictBase.get_onnx_session

        def _patched_session(self_, model_dir, use_gpu):
            if use_gpu:
                providers = [
                    ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "EXHAUSTIVE"}),
                    "CPUExecutionProvider",
                ]
            else:
                providers = ["CPUExecutionProvider"]
            return _ort.InferenceSession(model_dir, None, providers=providers)

        _pb.PredictBase.get_onnx_session = _patched_session

        # 3. Create the recognizer directly — no detector involved.
        from onnxocr.predict_rec import TextRecognizer

        import argparse

        rec_path = self._model_path("rec.onnx")
        dict_path = osp.abspath(osp.join(_MODEL_DIR, "ppocrv6_dict_proper.txt"))
        for p, label in [
            (rec_path, "medium/rec.onnx"),
            (dict_path, "dict"),
        ]:
            if not osp.isfile(p):
                raise FileNotFoundError(
                    f"PP-OCRv6 ONNX model file not found: {p}\n\n"
                    "Try restarting the app so model files are auto-downloaded,\n"
                    "or download them manually from:\n"
                    "  https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx"
                )

        args = argparse.Namespace(
            rec_algorithm="SVTR_LCNet",
            rec_model_dir=rec_path,
            rec_image_shape="3, 48, 320",
            rec_batch_num=self.get_param_value("rec_batch_num"),
            max_text_length=25,
            rec_char_dict_path=dict_path,
            use_space_char=True,
            use_gpu=_use_gpu,
            drop_score=0.5,
        )
        self.recognizer = TextRecognizer(args)

        # 4. On GPU, CUDA kernel caches are per-shape — any change to the
        #    batch dimension *or* the spatial width invalidates the cache
        #    and triggers full kernel recompilation (~1.8 s per batch).
        #    The recognizer's ``resize_norm_img`` computes a dynamic width
        #    per batch (e.g. 320 for narrow crops, 374 for wide ones),
        #    destroying the cache across calls.
        #
        #    Two-part fix:
        #      a) Force ``resize_norm_img`` to *always* pad to
        #         ``rec_image_shape`` width (320) — wide crops get clamped,
        #         which is fine because the model was trained at this width.
        #      b) Pad the last batch to ``rec_batch_num`` entries so the batch
        #         dimension never changes either.
        if _use_gpu:
            _orig_resize = self.recognizer.resize_norm_img

            def _fixed_resize(img, max_wh_ratio):
                result = _orig_resize(img, max_wh_ratio)
                _, _, fixed_w = (3, 48, 320)  # rec_image_shape
                if result.shape[2] != fixed_w:
                    import numpy as _np

                    fixed = _np.zeros(
                        (result.shape[0], result.shape[1], fixed_w), dtype=_np.float32
                    )
                    w = min(result.shape[2], fixed_w)
                    fixed[:, :, :w] = result[:, :, :w]
                    result = fixed
                return result

            self.recognizer.resize_norm_img = _fixed_resize

            self.recognizer.__class__.__call__ = self._make_uniform_batch_rec(
                self.recognizer,
                self.get_param_value("rec_batch_num"),
            )

    def unload_model(self, empty_cache=False):
        self.recognizer = None

    # ── GPU uniform-batch patch ─────────────────────────────────────────

    @staticmethod
    def _make_uniform_batch_rec(recognizer, batch_num):
        """Return a patched ``__call__`` for the recognizer that pads the
        last inference batch to ``batch_num`` entries.

        onnxruntime CUDA recompiles kernels every time the batch dimension
        changes.  For 15 text lines and ``batch_num=6`` the recognizer makes
        three inference calls with shapes ``[6] → [6] → [3]`` — the last
        ``[3]`` triggers full kernel recompilation on the *next* page, and
        the switch back to ``[6]`` recompiles again.  By padding the last
        batch with dummy zero-tensors we keep every call at ``[6]`` shape,
        so the kernel cache stays valid.
        """
        import copy as _copy

        _orig_call = recognizer.__class__.__call__

        def _patched(self_, img_list):
            remainder = len(img_list) % batch_num
            if remainder:
                # Clone the first N crops as padding (a few rows of zeros)
                pad_count = batch_num - remainder
                img_list = img_list + _copy.deepcopy(img_list[:pad_count])
            results = _orig_call(self_, img_list)
            return results[: len(results) - (pad_count if remainder else 0)]

        return _patched

    # ── OCRBase interface — crop-based recognition ──────────────────────

    def _ocr_blk_list(
        self, img: np.ndarray, blk_list: List[TextBlock], *args, **kwargs
    ):
        """Recognise text in pre‑detected blocks by cropping each polygon.

        Iterates each ``TextBlock.lines``, crops the region from the source
        image using perspective-correct cropping, and runs the ONNX
        recognizer on all crops in a single batched call.
        """
        if not blk_list:
            return

        if self.recognizer is None:
            self.load_model()

        from onnxocr.utils import get_rotate_crop_image

        # Collect all line crops alongside their block index
        all_crops: list[np.ndarray] = []
        crop_to_blk: list[int] = []  # parallel: blk index for each crop

        for blk_idx, blk in enumerate(blk_list):
            for line in blk.lines:
                poly = np.array(line, dtype=np.float32)
                crop = get_rotate_crop_image(img, poly)
                all_crops.append(crop)
                crop_to_blk.append(blk_idx)

        if not all_crops:
            return

        # Single batched call to the recognizer
        rec_results = self.recognizer(all_crops)

        # Group recognised text back to each block
        block_texts: list[list[str]] = [[] for _ in range(len(blk_list))]
        for i, blk_idx in enumerate(crop_to_blk):
            if i < len(rec_results):
                text, score = rec_results[i]
                if text and score >= 0.3:
                    block_texts[blk_idx].append(text)

        for blk_idx, texts in enumerate(block_texts):
            if texts:
                blk_list[blk_idx].text = texts

    def ocr_img(self, img: np.ndarray) -> str:
        self.logger.warning(
            "ocr_img() is not supported in recognition-only mode — "
            "use a text detector + OCR pipeline instead."
        )
        return ""
