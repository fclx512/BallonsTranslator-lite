# How to Add a New Translator / OCR / Detector / Inpainter

> This guide covers adding new modules to BallonsTranslator-lite.
> The module system uses a **file-based auto-registration** pattern: create a file with the right name prefix, decorate your class, and it's automatically discovered.

## 1. Architecture Overview

All modules live under `modules/`:

```
modules/
├── translators/    →  trans_*.py    (@register_translator)
├── ocr/            →  ocr_*.py      (@register_OCR)
├── textdetector/   →  detector_*.py (@register_textdetectors)  ⚠️ note the plural 's'
└── inpaint/        →  modules defined in base.py (@register_inpainter)

modules/base.py     →  init_module_registries(), scans files matching the patterns above
```

To add a module, create a new `.py` file with the correct prefix in the corresponding directory.
Registration and discovery are handled automatically — no need to edit `__init__.py`.

## 2. Adding a Translator

### 2.1 File location

Create `modules/translators/trans_myengine.py`.

### 2.2 Basic scaffold

```python
from modules.translators.base import BaseTranslator, register_translator
from typing import List, Dict

@register_translator('MyEngine')
class MyEngineTranslator(BaseTranslator):

    concate_text = True

    params: Dict = {
        'api_key': '',
        'device': {
            'type': 'selector',
            'options': ['cpu', 'cuda'],
            'value': 'cpu'
        }
    }

    def _setup_translator(self):
        self.lang_map['日本語'] = 'ja'
        self.lang_map['English'] = 'en'

    def _translate(self, src_list: List[str]) -> List[str]:
        # do translation here
        return src_list
```

### 2.3 Key concepts

- `@register_translator('Name')` — the name shown in the UI
- `params` dict — defines config panel fields (see [config_reference.md](config_reference.md) for full widget type reference)
- `concate_text = True` — texts are concatenated before `_translate()` (fewer API calls); set to `False` for offline models that accept lists
- `_setup_translator()` — set up API clients, load models, populate `self.lang_map`
- `_translate()` — receive source texts, return translations
- `updateParam()` — optional, react to param changes at runtime (e.g., switching device)

### 2.4 Optional: auto-installing pip dependencies

If your module needs extra pip packages (not in the project's `pyproject.toml`), declare `requires_packages` and they'll be auto-installed on first `load_model()`:

```python
class MyEngineTranslator(BaseTranslator):
    requires_packages: List[str] = [
        "some-package>=1.0",
    ]
```

Uses PEP 508 format; prefers `uv`, falls back to `pip`. Also supported on OCR, detector, and inpainter modules.

## 3. Adding an OCR Module

Create `modules/ocr/ocr_myengine.py`:

```python
from modules.ocr.base import OCRBase, register_OCR, TextBlock

@register_OCR('MyOCR')
class MyOCREngine(OCRBase):

    params = {
        'my_param': {
            'type': 'selector',
            'options': [8, 16, 24],
            'value': 16
        },
        'description': 'My custom OCR engine'
    }

    def _ocr_blk_list(self, img, blk_list):
        # return updated blk_list with text fields filled
        pass
```

## 4. Adding a Text Detector

Create `modules/textdetector/detector_myengine.py`:

```python
from modules.textdetector.base import TextDetectBase, register_textdetectors

@register_textdetectors('MyDetector')
class MyDetector(TextDetectBase):

    params = {
        'detect_size': {
            'type': 'selector',
            'options': [1024, 1280],
            'value': 1280
        },
        'description': 'My text detector'
    }

    def _detect(self, img, proj):
        # return (mask, blk_list)
        pass
```

## 5. Adding an Inpainter

Inpainter modules are defined inside `modules/inpaint/base.py` (see `AOTInpainter` / `LamaLarge` for reference):

```python
from modules.inpaint.base import InpainterBase, register_inpainter

@register_inpainter('MyInpainter')
class MyInpainter(InpainterBase):

    params = {
        'inpaint_size': {
            'type': 'selector',
            'options': [1024, 2048],
            'value': 2048
        },
        'device': ...
    }

    def _inpaint(self, img, mask, textblock_list=None):
        # return inpainted image
        pass
```

> **Note**: Inpainters don't currently use the `inpaint_*.py` file-discovery pattern; they're decorated with `@register_inpainter` inside `base.py`. If you create a separate `.py` file, make sure `base.py` imports it.

## 6. i18n Notes

- Module `params` `"description"` fields should be written in **English**
- Translations are handled by `ParamWidget` in the config panel
- Add translation entries under `<name>ParamWidget</name>` in `translate/zh_CN.ts`
- QDialog subclasses in modules (e.g., profile manager) should use standard `self.tr()`
