"""PP-OCRv6 ONNX Runtime OCR module for BallonsTranslator-lite.

Uses ``ONNXPaddleOcr`` from the ``onnxocr`` package to run PP-OCRv6 medium
models via ONNX Runtime.  Avoids the PaddlePaddle framework dependency (~400 MB)
and its CUDA context conflict with PyTorch.

The medium model is bundled under ``data/models/ppocrv6_onnx/medium/`` and
auto-downloaded on first use.
"""

import os
import os.path as osp
from typing import List

import numpy as np

from modules.ocr.base import DEVICE_SELECTOR, OCRBase, TextBlock, register_OCR

# ── Paths ────────────────────────────────────────────────────────────────────

_MODEL_DIR = osp.join("data", "models", "ppocrv6_onnx")


# ── Module registration ──────────────────────────────────────────────────────


@register_OCR("paddleocr_v6_onnx")
class PaddleOCRv6ONNX(OCRBase):
    params = {
        "device": DEVICE_SELECTOR(),
        "model_size": {
            "type": "selector",
            "options": ["medium"],
            "value": "medium",
            "description": "Model size: medium (34.5M, most accurate)",
        },
        "lang": {
            "type": "selector",
            "options": [
                "ch",
                "en",
                "japan",
                "korean",
                "french",
                "german",
                "italian",
                "spanish",
                "portuguese",
                "russian",
                "arabic",
                "tamil",
            ],
            "value": "ch",
            "description": "Language — 'ch' covers Chinese + English",
        },
        "det_db_thresh": {
            "value": 0.2,
            "description": "Detection score threshold (lower = more detections)",
        },
        "det_db_box_thresh": {
            "value": 0.45,
            "description": "Detection box threshold (lower = more boxes)",
        },
        "det_db_unclip_ratio": {
            "value": 1.4,
            "description": "Detection box unclip ratio (higher = looser boxes)",
        },
        "max_candidates": {
            "value": 3000,
            "description": "Maximum candidate detection boxes",
        },
        "reading_order": {
            "type": "selector",
            "options": ["auto", "ltr", "rtl"],
            "value": "auto",
            "description": "Reading order: ltr=left-to-right, rtl=right-to-left (manga), auto=detect",
        },
        "rec_batch_num": {
            "value": 6,
            "description": "Recognition batch size (higher = faster, more VRAM)",
        },
        "description": "PP-OCRv6 ONNX — medium model via ONNX Runtime (no PaddlePaddle)",
    }

    requires_packages = ["onnxruntime", "onnxocr"]

    # Note: paths use "data/models/ppocrv6_onnx/" directly instead of _MODEL_DIR
    # because the lazy AST scanner (SafeEval) cannot resolve string
    # interpolation or osp.join at scan time.
    download_file_list = [
        # ── medium ──
        {
            "url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx/resolve/main/inference.onnx",
            "files": "data/models/ppocrv6_onnx/medium/det.onnx",
        },
        {
            "url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/main/inference.onnx",
            "files": "data/models/ppocrv6_onnx/medium/rec.onnx",
        },
    ]

    _load_model_keys = {"_onnx_ocr"}

    def __init__(self, **params):
        super().__init__(**params)
        self._onnx_ocr = None

    # ── Model path helpers ───────────────────────────────────────────────

    @staticmethod
    def _model_path(name: str) -> str:
        """Resolve path to a model file in the ``medium`` subdirectory."""
        return osp.abspath(osp.join(_MODEL_DIR, "medium", name))

    # ── Detection sorting for reading order ─────────────────────────────

    def _sort_detections(self, detections: list) -> list:
        """Sort OCR detections according to ``reading_order``.

        Returns a new sorted list.  Each detection is ``[box, (text, score)]``
        where ``box`` is a 4×2 list ``[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]``.
        """
        if not detections:
            return detections

        order = self.get_param_value("reading_order")

        # Compute centre point for each detection
        centres = []
        for det in detections:
            if len(det) < 1:
                continue
            box = np.array(det[0], dtype=np.float32)
            cx = float(box[:, 0].mean())
            cy = float(box[:, 1].mean())
            centres.append((cx, cy))

        if not centres:
            return detections

        # Auto-detect: if most boxes are tall → vertical (RTL)
        if order == "auto":
            tall = 0
            for det in detections:
                if len(det) < 1:
                    continue
                box = np.array(det[0], dtype=np.float32)
                w = float(box[:, 0].max() - box[:, 0].min())
                h = float(box[:, 1].max() - box[:, 1].min())
                if h > w:
                    tall += 1
            order = "rtl" if tall > len(detections) // 2 else "ltr"

        # Create index-sorted list so we never lose detections
        if order == "rtl":
            # Right-to-left: sort by x descending, then y ascending
            sorted_indices = sorted(
                range(len(detections)),
                key=lambda i: (-centres[i][0], centres[i][1]),
            )
        else:
            # Left-to-right (default): sort by y ascending, then x ascending
            sorted_indices = sorted(
                range(len(detections)),
                key=lambda i: (centres[i][1], centres[i][0]),
            )

        return [detections[i] for i in sorted_indices]

    # ── Device / param updates ──────────────────────────────────────────

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        self._onnx_ocr = None  # force re-init on next call

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
        """Initialise the OnnxOCR engine.

        Called by ``BaseModule.load_model()`` after dependency & model-file
        checks have passed.
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

        # Warn early if user selected CUDA but onnxruntime-gpu is not installed.
        # The import succeeds with onnxruntime (CPU), but providers will lack CUDA.
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

        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        det_path = self._model_path("det.onnx")
        rec_path = self._model_path("rec.onnx")
        dict_path = osp.abspath(osp.join(_MODEL_DIR, "ppocrv6_dict_proper.txt"))

        # Validate model files exist (helpful error if download failed)
        for p, label in [
            (det_path, "medium/det.onnx"),
            (rec_path, "medium/rec.onnx"),
            (dict_path, "dict"),
        ]:
            if not osp.isfile(p):
                raise FileNotFoundError(
                    f"PP-OCRv6 ONNX model file not found: {p}\n\n"
                    "Try restarting the app so model files are auto-downloaded,\n"
                    "or download them manually from:\n"
                    "  https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx\n"
                    "  https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx"
                )

        device = self.get_param_value("device")
        use_gpu = device == "cuda"

        self._onnx_ocr = ONNXPaddleOcr(
            det_model_dir=det_path,
            rec_model_dir=rec_path,
            rec_char_dict_path=dict_path,
            use_angle_cls=False,
            det_db_thresh=self.get_param_value("det_db_thresh"),
            det_db_box_thresh=self.get_param_value("det_db_box_thresh"),
            det_db_unclip_ratio=self.get_param_value("det_db_unclip_ratio"),
            max_candidates=self.get_param_value("max_candidates"),
            use_gpu=use_gpu,
            rec_batch_num=self.get_param_value("rec_batch_num"),
            drop_score=0.5,
            use_space_char=True,
        )

        # 4. On GPU, CUDA kernel caches are per-shape — any change to the
        #    batch dimension *or* the spatial width invalidates the cache
        #    and triggers full kernel recompilation (~1.8 s per batch).
        #    The recognizer's ``resize_norm_img`` computes a dynamic width
        #    per batch (e.g. 320 for narrow crops, 374 for wide ones),
        #    destroying the cache across calls.
        #
        #    Two-part fix:
        #      a) Force ``resize_norm_img`` to *always* pad to ``rec_image_shape``
        #         width (320) — wide crops get clamped, which is fine because the
        #         model was trained at this width.
        #      b) Pad the last batch to ``rec_batch_num`` entries so the batch
        #         dimension never changes either.
        if use_gpu:
            _orig_resize = self._onnx_ocr.text_recognizer.resize_norm_img

            def _fixed_resize(img, max_wh_ratio):
                result = _orig_resize(img, max_wh_ratio)
                # result shape: (C, H, W) — clamp W to rec_image_shape[2]
                import numpy as _np

                _, _, fixed_w = (3, 48, 320)  # rec_image_shape
                if result.shape[2] != fixed_w:
                    fixed = _np.zeros(
                        (result.shape[0], result.shape[1], fixed_w), dtype=_np.float32
                    )
                    w = min(result.shape[2], fixed_w)
                    fixed[:, :, :w] = result[:, :, :w]
                    result = fixed
                return result

            self._onnx_ocr.text_recognizer.resize_norm_img = _fixed_resize

            self._onnx_ocr.text_recognizer.__class__.__call__ = (
                self._make_uniform_batch_rec(
                    self._onnx_ocr.text_recognizer,
                    self.get_param_value("rec_batch_num"),
                )
            )

    def unload_model(self, empty_cache=False):
        self._onnx_ocr = None

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

    # ── OCR ─────────────────────────────────────────────────────────────

    def _ocr_image(self, img: np.ndarray) -> list:
        """Run OnnxOCR on the full image; return raw result list.

        Returns the old PaddleOCR-style format::

            [[[box, (text, score)], ...]]

        where ``box`` is a 4×2 list of corner points
        ``[[x1,y1], [x2,y2], [x3,y3], [x4,y4]]``.
        """
        if self._onnx_ocr is None:
            self.load_model()
        return self._onnx_ocr.ocr(img)

    def _match_results_to_blocks(self, raw_result: list, blk_list: List[TextBlock]):
        """Assign recognised text to TextBlocks via centre-point matching.

        OnnxOCR returns the old PaddleOCR format where each detection is
        ``[box, (text, score)]``.  The box centre point is tested against
        each TextBlock's axis-aligned bounding box.
        """
        if not raw_result or not raw_result[0]:
            return

        detections = self._sort_detections(raw_result[0])

        assigned: dict[int, list[str]] = {i: [] for i in range(len(blk_list))}
        blk_boxes = [blk.xyxy for blk in blk_list]

        for det in detections:
            if len(det) < 2:
                continue
            box, (text, score) = det
            if not text or float(score) < 0.3:
                continue

            poly = np.array(box, dtype=np.float32)
            cx = float(poly[:, 0].mean())
            cy = float(poly[:, 1].mean())

            for idx, (x1, y1, x2, y2) in enumerate(blk_boxes):
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    assigned[idx].append(text)
                    break

        for idx, texts in assigned.items():
            if texts:
                blk_list[idx].text = texts

    # ── OCRBase interface ───────────────────────────────────────────────

    def _ocr_blk_list(
        self, img: np.ndarray, blk_list: List[TextBlock], *args, **kwargs
    ):
        raw = self._ocr_image(img)
        self._match_results_to_blocks(raw, blk_list)

    def ocr_img(self, img: np.ndarray) -> str:
        raw = self._ocr_image(img)
        if not raw or not raw[0]:
            return ""
        texts = []
        for det in self._sort_detections(raw[0]):
            if len(det) < 2:
                continue
            _, (text, _) = det
            if text:
                texts.append(text)
        return "\n".join(texts)
