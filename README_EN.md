# BallonsTranslator-lite

A lightweight manga translation tool based on BallonsTranslator. Preserves the full lettering pipeline with drastically reduced size and redundant modules removed. Supports fully offline CPU inference.

## Features

- **One-click translation pipeline**: text detection → OCR → translation → inpainting → typesetting, with batch processing support
- **Image editing**: inpainting brush, selection tools (rectangle / lasso), mask editing
- **Rich text editing**: WYSIWYG typesetting with font, size, color, stroke, alignment, line spacing adjustments
- **Text style presets**: save and reuse font style configurations
- **Search & replace**: page-level search and full-text search across project
- **Manga support**: continuous multi-page reading and translation, Word document export

## What's Stripped

Compared to upstream [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator), this fork:

- **Translators**: removed Baidu, Caiyun, DeepL, DeepLX, Google, Youdao, Papago, Sugoi, M2M100, Yandex and others (traditional machine translation no longer competes with LLMs for non-real-time scenarios)
  - Kept and added mainstream LLM API translators (note: some domestic API providers apply content filters that may reject sensitive input)
- **OCR engines**: removed PaddleOCR, Google Vision, Bing Lens, macOS/Windows native OCR, Manga OCR, OneOCR, Stariver and other rarely-used or low-quality engines
  - Local OCR defaults to MIT48px-CTC; LLM API OCR is retained for vision-capable models
- **Inpainting**: removed Flux Inpaint pipeline (heavy dependencies, large size) and other models with no clear advantage; kept `lama_large_512px` (best quality) and AOT (lightweight, decent quality)
- **Other removals**: Saladict dictionary integration, keyword substitution panels, headless continuous mode, system HuggingFace cache option
- **Dependencies**: trimmed requirements.txt, removed keyboard, deeplx, saladict and other unnecessary packages

The complete project including models, dependencies, and CPU PyTorch is approximately **2.3 GB** and can be easily packaged for portability.

## What's New

- **Offline CPU inference**: bundled CPU-only PyTorch, run with `launch_cpu.bat` or `--cpu` flag — no GPU required
- **UI improvements**: reorganized font panel layout, removed redundant configuration items
- **Python 3.13**: bundled environment upgraded to Python 3.13
- **Dual launcher**: `launch_win.bat` for GPU mode, `launch_cpu.bat` for CPU-only mode
- **Enhanced font selection**: added independent style selector (Regular / Bold / Italic, etc.)
- **More small improvements coming…**

## Quick Start

Note: The upstream project supports macOS and AMD GPUs, but I have no such hardware to test. This fork targets Windows with CUDA or CPU only.

Do NOT use the Microsoft Store version of Python. The WindowsApps directory leaves a `python.exe` placeholder that causes redirects even after uninstalling. If you already installed it, search for "Python opens Windows Store" for solutions.

### Windows

1. Download the source code and extract to a local directory
2. Run `launch_win.bat` (GPU mode) or `launch_cpu.bat` (CPU-only mode)
3. Model files will be downloaded automatically on first launch — keep your internet connection active

### Run from source

```bash
git clone https://github.com/yourname/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU mode
python launch.py

# CPU mode
python launch.py --cpu
```

To update: `python launch.py --update`

On first launch, PyTorch and other dependencies will be installed automatically, and model files (~700MB) will be downloaded. If the download fails, place the `data` directory in the project root manually. If you encounter network issues, you can download the models and dependencies separately from the link provided.

GPU mode uses your system's PyTorch installation (with CUDA). If not found, a console notice is shown and it falls back to CPU mode automatically.

> **Note for Windows users:** If you need Chinese text segmentation (better word breaking in Chinese), install `spacy-pkuseg` manually:
> ```bash
> pip install spacy-pkuseg
> ```
> This package requires **Microsoft Visual C++ 14.0 or greater** (Build Tools with "Desktop development with C++" workload). Without it, Chinese text will still work but use character-level segmentation.

## Usage

### One-click translation

1. Open a folder containing manga/comic images
2. Configure source and target languages in the settings panel
3. Click "Run" and wait for the pipeline to finish
4. Edit unsatisfactory translations directly on the canvas

### Text editing

- Double-click a text block on the canvas to enter edit mode
- Adjust font, size, color, stroke and other properties in the right-side font panel
- Save and apply text style presets for quick reuse

### Inpainting tools

- Use the inpainting brush to paint over areas to be restored
- Use rectangle/lasso tools to select regions for batch removal

## FAQ

**Model download failed?**
Manually download the `data` directory from Releases and extract it to the project root.

**How to switch UI language?**
The app follows your system language automatically; you can also switch manually in the settings panel.

**How to update?**
Run `python launch.py --update` in the project root directory.

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project
