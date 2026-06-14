"""PP-OCRv6 OCR module for BallonsTranslator-lite.

Requires ``paddleocr>=3.7.0`` — install manually via the Dependencies dialog
or ``pip install paddleocr>=3.7.0``.

Models are auto-downloaded by PaddleOCR on first use to ``~/.paddleocr/``.

CUDA isolation
--------------
PyTorch and PaddlePaddle cannot share a CUDA context in the same process
(see docs/PP-OCRv6-部署参考文档.md §6.1).  This module handles it by:

* **In-process** inference when PyTorch runs on CPU (no conflict).
* **Subprocess** inference when PyTorch uses CUDA — inference runs in a
  separate child process so each framework keeps its own CUDA context.
"""

import multiprocessing
import os
from typing import List

import cv2
import numpy as np

from modules.base import DEFAULT_DEVICE
from modules.ocr.base import DEVICE_SELECTOR, OCRBase, TextBlock, register_OCR

# ── Module registration ──────────────────────────────────────────────────────


@register_OCR("paddleocr_v6")
class PaddleOCRv6(OCRBase):
    params = {
        "device": DEVICE_SELECTOR(),
        "ocr_version": {
            "type": "selector",
            "options": [
                "PP-OCRv6",
                "PP-OCRv6_small",
                "PP-OCRv6_tiny",
            ],
            "value": "PP-OCRv6",
            "description": "PP-OCRv6 model scale (medium / small / tiny)",
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
        "use_textline_orientation": {
            "type": "checkbox",
            "value": False,
            "description": "Enable text line orientation detection (needed for vertical Japanese text in manga)",
        },
        "gpu_mem": {
            "value": 8000,
            "data_type": int,
            "description": "GPU memory limit in MB (subprocess mode only)",
        },
        "description": "PP-OCRv6 — Baidu's latest OCR (medium/small/tiny, 50 languages)",
    }

    # ── No auto-install ─────────────────────────────────────────────────
    # PaddleOCR / PaddlePaddle is a heavy dependency (~400 MB) so we
    # require the user to install it explicitly via the Dependencies dialog
    # (Tools → Check Dependencies → "Install All (incl. optional)").
    requires_packages = []

    # Models are auto-downloaded by PaddleOCR on first use to
    # ~/.paddleocr/ — no download_file_list needed.
    download_file_list = []

    _load_model_keys = {"_paddle_ocr" if False else "_dummy"}
    # ^ Guarded by False so load_model() is a no-op until _load_model().
    #   The real loading happens lazily inside _ocr_image() so we can
    #   check for `paddleocr` availability and decide CPU/subprocess at
    #   call time.

    def __init__(self, **params):
        super().__init__(**params)
        self._paddle_ocr = None
        self._needs_subprocess = self._detect_subprocess_needed()

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _detect_subprocess_needed() -> bool:
        """Return True if PaddleOCR must run in a subprocess.

        Subprocess isolation is only needed when BOTH:
        1. PyTorch holds a CUDA context (``DEFAULT_DEVICE == "cuda"``), AND
        2. PaddlePaddle itself is GPU-capable (has CUDA support).

        With CPU-only PaddlePaddle there is no CUDA context conflict, so we
        can safely run in-process with ``CUDA_VISIBLE_DEVICES=""``.
        """
        if DEFAULT_DEVICE != "cuda":
            return False
        try:
            import paddle  # type: ignore[import-untyped]

            return paddle.is_compiled_with_cuda()
        except (ImportError, Exception):
            return False

    @staticmethod
    def _check_paddleocr_installed():
        """Raise ``ImportError`` with a helpful message if missing."""
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            raise ImportError(
                "paddleocr is not installed.\n\n"
                "Open  Tools → Check Dependencies  and click\n"
                '"Install All (incl. optional)", or run:\n\n'
                "    pip install paddleocr>=3.7.0"
            )

    # ── Device updates ──────────────────────────────────────────────────

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        if param_key == "device":
            self._needs_subprocess = self._detect_subprocess_needed()
            # Force re-init on next call
            self._paddle_ocr = None
        if param_key in ("ocr_version", "lang", "use_textline_orientation"):
            self._paddle_ocr = None

    # ── Model lifecycle ─────────────────────────────────────────────────

    def _load_model(self):
        """Verify dependencies are met (actual PaddleOCR init is lazy)."""
        self._check_paddleocr_installed()

        if self._needs_subprocess:
            self.logger.info(
                "PyTorch holds CUDA context → PaddleOCR will run in a "
                "subprocess for CUDA isolation."
            )
        else:
            self.logger.info("PyTorch is on CPU → PaddleOCR runs in-process.")

    def all_model_loaded(self):
        try:
            self._check_paddleocr_installed()
            return True
        except ImportError:
            return False

    def unload_model(self, empty_cache=False):
        self._paddle_ocr = None

    # ── PaddleOCR initialisation (in-process) ───────────────────────────

    def _init_paddle_ocr(self):
        """Create a PaddleOCR instance in this process (CPU only)."""
        from paddleocr import PaddleOCR

        ocr_version = self.get_param_value("ocr_version")
        lang = self.get_param_value("lang")
        use_textline_orientation = self.get_param_value("use_textline_orientation")

        # Force CPU by hiding CUDA devices from PaddlePaddle.
        saved = os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        try:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            self._paddle_ocr = PaddleOCR(
                ocr_version=ocr_version,
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=use_textline_orientation,
            )
        finally:
            if saved is not None:
                os.environ["CUDA_VISIBLE_DEVICES"] = saved
            else:
                os.environ.pop("CUDA_VISIBLE_DEVICES", None)

    # ── OCR ─────────────────────────────────────────────────────────────

    def _ocr_image(self, img: np.ndarray) -> list:
        """Run PaddleOCR on the full image; return raw result list."""
        self._check_paddleocr_installed()

        if self._needs_subprocess:
            return self._run_subprocess(img)

        # In-process
        if self._paddle_ocr is None:
            self._init_paddle_ocr()
        raw = self._paddle_ocr.predict(img)
        return raw

    def _run_subprocess(self, img: np.ndarray) -> list:
        """Run PaddleOCR in a child process for CUDA isolation."""
        _, buf = cv2.imencode(".png", img)
        img_bytes = buf.tobytes()

        queue: multiprocessing.Queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=_subprocess_worker,
            args=(
                img_bytes,
                self.get_param_value("ocr_version"),
                self.get_param_value("lang"),
                self.get_param_value("use_textline_orientation"),
                self.get_param_value("gpu_mem"),
                queue,
            ),
        )
        proc.start()
        proc.join()

        if queue.empty():
            self.logger.error("PaddleOCR subprocess returned no result.")
            return []
        return queue.get()

    # ── TextBlock matching ──────────────────────────────────────────────

    @staticmethod
    def _match_results_to_blocks(raw_result: list, blk_list: List[TextBlock]):
        """Assign recognised text to TextBlocks via centre-point matching.

        PaddleOCR predict() returns a list of dicts (one per page) with
        keys ``rec_texts``, ``rec_scores`` and ``dt_polys`` inside ``['res']``.
        Each detection polygon is matched to the TextBlock whose axis-aligned
        bounding box contains its centre point.
        """
        if not raw_result or not raw_result[0].get("res"):
            return

        res = raw_result[0].get("res", {})
        rec_texts = res.get("rec_texts", [])
        rec_scores = res.get("rec_scores", [])
        dt_polys = res.get("dt_polys", [])
        if not rec_texts or dt_polys.size == 0:
            return

        assigned: dict[int, list[str]] = {i: [] for i in range(len(blk_list))}
        blk_boxes = [blk.xyxy for blk in blk_list]  # cache

        for i in range(len(rec_texts)):
            text = rec_texts[i]
            conf = float(rec_scores[i]) if i < len(rec_scores) else 0.0
            if not text or conf < 0.3:
                continue

            # dt_polys shape: (N, 4, 2)  — N detections, 4 corners, (x,y)
            poly = dt_polys[i]
            cx = float(poly[:, 0].mean())
            cy = float(poly[:, 1].mean())

            # Match to first containing TextBlock
            for idx, (x1, y1, x2, y2) in enumerate(blk_boxes):
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    assigned[idx].append(text)
                    break

        # Write back
        for idx, texts in assigned.items():
            if texts:
                blk_list[idx].text = texts

    # ── OCRBase interface ───────────────────────────────────────────────

    def _ocr_blk_list(
        self,
        img: np.ndarray,
        blk_list: List[TextBlock],
        *args,
        **kwargs,
    ):
        raw = self._ocr_image(img)
        self._match_results_to_blocks(raw, blk_list)

    def ocr_img(self, img: np.ndarray) -> str:
        raw = self._ocr_image(img)
        if not raw:
            return ""
        page = raw[0]
        res = (
            page.get("res")
            if hasattr(page, "get")
            else (page if isinstance(page, dict) else {})
        )
        if not res:
            return ""
        texts = res.get("rec_texts", []) if isinstance(res, dict) else []
        return "\n".join(texts) if texts else ""


# ── Subprocess worker (module-level for Windows pickling) ─────────────────


def _subprocess_worker(
    img_bytes: bytes,
    ocr_version: str,
    lang: str,
    use_textline_orientation: bool,
    gpu_mem: int,
    queue: multiprocessing.Queue,
):
    """PaddleOCR inference entry point for ``multiprocessing.Process``.

    Defined at module level so it is picklable on Windows.
    """
    import cv2
    import numpy as np
    from paddleocr import PaddleOCR

    try:
        ocr = PaddleOCR(
            ocr_version=ocr_version,
            lang=lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=use_textline_orientation,
            gpu_mem=gpu_mem,
        )
        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        result = ocr.predict(img)
        queue.put(result)
    except Exception:
        queue.put([])
        raise
