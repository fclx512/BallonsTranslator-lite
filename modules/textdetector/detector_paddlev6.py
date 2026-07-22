"""PP-OCRv6 ONNX Runtime text detector for BallonsTranslator-lite.

Uses ``TextDetector`` from the ``onnxocr`` package to run PP-OCRv6 DBNet
text detection via ONNX Runtime.  Avoids the PaddlePaddle framework
dependency (~400 MB) and its CUDA context conflict with PyTorch.

The detection model is bundled under ``data/models/ppocrv6_onnx/medium/`` and
auto-downloaded on first use.
"""

import os
import os.path as osp
from typing import List, Tuple

import cv2
import numpy as np

from modules.textdetector.base import (
    DEVICE_SELECTOR,
    ProjImgTrans,
    TextBlock,
    TextDetectorBase,
    register_textdetectors,
)
from utils.textblock import examine_textblk, sort_pnts

# ── Paths ────────────────────────────────────────────────────────────────────

_MODEL_DIR = osp.join("data", "models", "ppocrv6_onnx")
_MODEL_SIZE = "medium"


# ── Module registration ──────────────────────────────────────────────────────


@register_textdetectors("ppocrv6_onnx")
class PPOCRv6Detector(TextDetectorBase):
    params = {
        "device": DEVICE_SELECTOR(),
        "model_size": {
            "type": "selector",
            "options": [_MODEL_SIZE],
            "value": _MODEL_SIZE,
            "description": "Model size (only medium available)",
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
        "font size multiplier": {
            "value": 1.0,
            "description": "Scale factor applied to detected font size",
        },
        "font size max": {
            "value": -1,
            "description": "Maximum allowed font size (-1 for unlimited)",
        },
        "font size min": {
            "value": -1,
            "description": "Minimum allowed font size (-1 for unlimited)",
        },
        "mask dilate size": {
            "value": 2,
            "description": "Dilation kernel size for text region mask",
        },
        "description": "PP-OCRv6 ONNX DBNet text detector via ONNX Runtime (no PaddlePaddle)",
    }

    requires_packages = ["onnxruntime", "onnxocr"]

    # Note: paths use "data/models/ppocrv6_onnx/" directly instead of _MODEL_DIR
    # because the lazy AST scanner (SafeEval) cannot resolve string
    # interpolation or osp.join at scan time.
    download_file_list = [
        {
            "url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx/resolve/main/inference.onnx",
            "files": "data/models/ppocrv6_onnx/medium/det.onnx",
        },
    ]

    _load_model_keys = {"detector"}

    def __init__(self, **params):
        super().__init__(**params)
        self.detector = None

    # ── Model path helper ────────────────────────────────────────────────

    @staticmethod
    def _model_path(name: str) -> str:
        """Resolve path to a model file in the model-size subdirectory."""
        return osp.abspath(osp.join(_MODEL_DIR, _MODEL_SIZE, name))

    # ── Reading‑order sorting (operates on TextBlock lists) ──────────────

    def _sort_blocks(self, blk_list: List[TextBlock]) -> List[TextBlock]:
        """Sort TextBlocks according to ``reading_order`` param."""
        if len(blk_list) < 2:
            return blk_list

        order = self.get_param_value("reading_order")

        # Compute centre point for each block (from its first line polygon)
        centres = []
        for blk in blk_list:
            if not blk.lines:
                centres.append((0.0, 0.0))
                continue
            box = np.array(blk.lines[0], dtype=np.float32)
            cx = float(box[:, 0].mean())
            cy = float(box[:, 1].mean())
            centres.append((cx, cy))

        # Auto-detect: if most boxes are tall -> vertical (RTL)
        if order == "auto":
            tall = 0
            for blk in blk_list:
                if not blk.lines:
                    continue
                box = np.array(blk.lines[0], dtype=np.float32)
                w = float(box[:, 0].max() - box[:, 0].min())
                h = float(box[:, 1].max() - box[:, 1].min())
                if h > w:
                    tall += 1
            order = "rtl" if tall > len(blk_list) // 2 else "ltr"

        if order == "rtl":
            sorted_indices = sorted(
                range(len(blk_list)),
                key=lambda i: (-centres[i][0], centres[i][1]),
            )
        else:
            sorted_indices = sorted(
                range(len(blk_list)),
                key=lambda i: (centres[i][1], centres[i][0]),
            )

        return [blk_list[i] for i in sorted_indices]

    # ── Device / param updates ──────────────────────────────────────────

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        self.detector = None  # force re-init on next call

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
            return
        lib_dir = osp.join(osp.dirname(torch.__file__), "lib")
        if osp.isdir(lib_dir):
            os.add_dll_directory(lib_dir)

    def _load_model(self):
        """Initialise the OnnxOCR text detector.

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

        # 3. Create the detector directly.
        import argparse

        from onnxocr.predict_det import TextDetector

        det_path = self._model_path("det.onnx")
        if not osp.isfile(det_path):
            raise FileNotFoundError(
                f"PP-OCRv6 ONNX detection model not found: {det_path}\n\n"
                "Try restarting the app so model files are auto-downloaded,\n"
                "or download them manually from:\n"
                "  https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx"
            )

        args = argparse.Namespace(
            det_algorithm="DB",
            det_model_dir=det_path,
            det_limit_side_len=960,
            det_limit_type="max",
            det_box_type="quad",
            det_db_thresh=self.get_param_value("det_db_thresh"),
            det_db_box_thresh=self.get_param_value("det_db_box_thresh"),
            det_db_unclip_ratio=self.get_param_value("det_db_unclip_ratio"),
            det_db_score_mode="fast",
            max_candidates=self.get_param_value("max_candidates"),
            use_dilation=False,
            use_gpu=_use_gpu,
        )
        self.detector = TextDetector(args)

    def unload_model(self, empty_cache=False):
        self.detector = None

    # ── TextDetectorBase interface ──────────────────────────────────────

    def _detect(
        self, img: np.ndarray, proj: ProjImgTrans
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        """Run PP-OCRv6 DBNet detection on the image.

        Returns a binary mask and a sorted list of TextBlocks.
        """
        # 1. Run detection
        dt_boxes = self.detector(img)  # ndarray [N, 4, 2] or None

        if dt_boxes is None or len(dt_boxes) == 0:
            return np.zeros(img.shape[:2], dtype=np.uint8), []

        # 2. Convert each quad to a TextBlock with direction detection
        im_h, im_w = img.shape[:2]
        blk_list = []
        for box in dt_boxes:
            pts_sorted, is_vertical = sort_pnts(box)
            blk = TextBlock(lines=[pts_sorted.tolist()])
            blk.src_is_vertical = is_vertical
            blk.vertical = is_vertical
            blk.adjust_bbox()
            examine_textblk(blk, im_w, im_h)
            blk_list.append(blk)

        # 3. Sort by reading order
        blk_list = self._sort_blocks(blk_list)

        # 4. Build binary mask from block polygons
        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        for blk in blk_list:
            for line in blk.lines:
                contour = np.array(line, dtype=np.int32).reshape((-1, 1, 2))
                cv2.fillPoly(mask, [contour], 255)

        # 5. Apply font size adjustments
        fnt_rsz = self.get_param_value("font size multiplier")
        fnt_max = self.get_param_value("font size max")
        fnt_min = self.get_param_value("font size min")
        for blk in blk_list:
            sz = blk._detected_font_size * fnt_rsz
            if fnt_max > 0:
                sz = min(fnt_max, sz)
            if fnt_min > 0:
                sz = max(fnt_min, sz)
            blk.font_size = sz
            blk._detected_font_size = sz

        # 6. Mask dilation
        ksize = self.get_param_value("mask dilate size")
        if ksize > 0:
            element = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * ksize + 1, 2 * ksize + 1), (ksize, ksize)
            )
            mask = cv2.dilate(mask, element)

        return mask, blk_list
