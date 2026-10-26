"""各管线引擎的使用说明（静态 UI 文案）。

键 = ``阶段:注册模块名``：模块名在阶段内唯一，但 ``none`` 跨阶段重名
（检测器与修复器各有一个），必须带阶段前缀。设置页「管线」各标签随引擎下拉
自动切换本表文案。

文案在字面量定义处用 ``QCoreApplication.translate`` 显式标注上下文
（``"ModelIntros"``，见 AGENTS.md「i18n 翻译」）；英文为源串，中文在
``translate/zh_CN.ts``。新增引擎时在此补一行即可。
"""

from qtpy.QtCore import QCoreApplication

INTRO_TEXTS = {
    # ── 文字检测 ──────────────────────────────────────────────
    "textdetector:ysgyolo": QCoreApplication.translate(
        "ModelIntros",
        "YSG-Yolo detector (default)\nIdentifies manga balloon text cleanly and separates body text from sound effects; the output mask is rectangle-level.\nRequires ultralytics + torch; a GPU is recommended.",
    ),
    "textdetector:ysg_ctd": QCoreApplication.translate(
        "ModelIntros",
        "YSG-Yolo + ComicTextDetector hybrid\nYSG proposes boxes and CTD refines the glyph mask inside them. The mask hugs the strokes, suits outlined text on busy backgrounds, and feeds the glyph-mask font-size estimation;\nrequires both model sets, so VRAM use and runtime are higher than ysgyolo.",
    ),
    "textdetector:ppocrv6_onnx": QCoreApplication.translate(
        "ModelIntros",
        "Runs on CPU and is relatively fast; the default model for region re-detection.\nTends to produce one box per line and is less robust than ysgyolo, so misdetections are more likely.",
    ),
    "textdetector:none": QCoreApplication.translate(
        "ModelIntros",
        "Skips text detection entirely.",
    ),
    # ── OCR ──────────────────────────────────────────────────
    "ocr:paddleocr_v6_onnx": QCoreApplication.translate(
        "ModelIntros",
        "PP-OCRv6 ONNX recognition\nRuns on CPU and is fast, suitable for conventional black-and-white manga dialogue.",
    ),
    "ocr:paddleocr_vl_manga": QCoreApplication.translate(
        "ModelIntros",
        "Vision-language recognition for Japanese manga; autoregressive per block with quality first.\nGPU-only, the slowest option with high VRAM use.",
    ),
    "ocr:llm_ocr": QCoreApplication.translate(
        "ModelIntros",
        "Recognizes text with a multimodal model from your LLM profile. Suits stylized lettering and handwriting that regular OCR misses; requires a vision-capable API, with speed and cost set by the provider.",
    ),
    "ocr:none_ocr": QCoreApplication.translate(
        "ModelIntros",
        "Skips OCR. Use it when source text already exists (e.g. an imported project) or only later stages need to run.",
    ),
    # ── 图像修复 ──────────────────────────────────────────────
    "inpainter:patchmatch": QCoreApplication.translate(
        "ModelIntros",
        "PatchMatch (classical inpainting): no model, no VRAM, and the fastest option; suits flat or simple backgrounds, weaker on complex textures. If the native library is missing it downloads in the background on first use.",
    ),
    "inpainter:lama_large_512px": QCoreApplication.translate(
        "ModelIntros",
        "LaMa Large 512px neural inpainting: better results; needs torch and VRAM, suits complex backgrounds and screentone.",
    ),
    "inpainter:none": QCoreApplication.translate(
        "ModelIntros",
        "Skips inpainting: original lettering stays in place and translated text is drawn over it.",
    ),
    # ── 翻译 ─────────────────────────────────────────────────
    "translator:LLM_Agent_Translator": QCoreApplication.translate(
        "ModelIntros",
        "Translation agent (recommended): multi-turn tool calls plus a whole-story overview, with glossary, speaker hints and batch draft review for consistent names and tone; the slowest and most expensive option.",
    ),
    "translator:LLM_API_Translator": QCoreApplication.translate(
        "ModelIntros",
        "Single-turn translation straight through the LLM API: fast and cheap, but without global context or the review workflow; prefer the translation agent for new projects.",
    ),
}


def intro_for(stage: str, module_name: str) -> str:
    """当前引擎的说明文本；未收录的模块返回空串（设置页显示空说明）。"""
    return INTRO_TEXTS.get(f"{stage}:{module_name}", "")
