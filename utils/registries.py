"""Minimal registry definitions — no torch imports, no imports from ``modules.*``.

This is the single source of truth for registry instances and module-discovery
configuration.  Everything that needs to iterate registered modules or know
where to find them imports from here, so ``init_lazy_module_registries()`` can
be called without triggering the eager import chain (torch, cv2, model code,
…).
"""

from utils.registry import Registry

# ---------------------------------------------------------------------------
# Registry instances  (empty containers — populated later by lazy AST scan or
# by eager decorator import of the sub-package ``base.py`` files).
# ---------------------------------------------------------------------------
TEXTDETECTORS = Registry("textdetectors")
OCR = Registry("OCR")
TRANSLATORS = Registry("translators")
INPAINTERS = Registry("inpainters")

# ---------------------------------------------------------------------------
# Module-discovery configuration  (path patterns for the lazy AST scanner)
# ---------------------------------------------------------------------------
MODULE_SCRIPTS = {
    "translator": {
        "module_dir": "modules/translators",
        "module_pattern": r"trans_(.*?).py",
    },
    "textdetector": {
        "module_dir": "modules/textdetector",
        "module_pattern": r"detector_(.*?).py",
    },
    "inpainter": {
        "module_dir": "modules/inpaint",
        "module_pattern": r"inpaint_(.*?).py",
    },
    "ocr": {
        "module_dir": "modules/ocr",
        "module_pattern": r"ocr_(.*?).py",
    },
}

# Maps the string labels used by ``launch.py`` / config to the registry objects.
MODULETYPE_TO_REGISTRIES = {
    "textdetector": TEXTDETECTORS,
    "ocr": OCR,
    "inpainter": INPAINTERS,
    "translator": TRANSLATORS,
}

# Decorator function names for each module type.
# Matches variables assigned in each sub-package's base.py, e.g.:
#   register_textdetectors = TEXTDETECTORS.register_module
DECORATORS = {
    "translator": {"register_translator"},
    "textdetector": {"register_textdetectors"},
    "inpainter": {"register_inpainter"},
    "ocr": {"register_OCR"},
}
