# BallonsTranslator-lite

> **Note**: This project is under active refactoring. The README is AI-generated and may not reflect all recent changes. A manual update will follow once the codebase stabilizes.

A lightweight manga/image translation tool based on [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator). Preserves the full lettering pipeline with drastically reduced size and redundant modules removed. Supports fully offline CPU inference.

## Features

### Translation Pipeline

- **One-click pipeline**: text detection → OCR → translation → inpainting → typesetting, with batch processing and page range selection
- **Offline CPU inference**: bundled CPU-only PyTorch, no GPU required. GPU mode auto-detects system PyTorch with CUDA, falls back to CPU if unavailable

### Image Editing

- **Inpainting brush**: paint over text regions to remove
- **Selection tools**: rectangle and lasso selection for batch removal
- **Mask editing**: manually adjust text region masks

### Typesetting

- **WYSIWYG editing**: edit translations directly on canvas with font, size, color, stroke, alignment, line spacing, letter spacing controls
- **Shadow & gradient**: PS-style clock dial for intuitive control over shadow angle, distance, blur, and gradient direction
- **Text style presets**: save and reuse font style configurations, with partial override support
- **Font filtering**: exclude unused fonts to streamline the font list

### Search & Replace

- **Page search**: find and replace within the current page
- **Global search**: slide-in panel with cross-page search (full text / source / translation)
- **Batch replace**: replace matching text across all pages at once

### AI Assistant

- **Natural language interaction**: control the project via chat panel — modify translations, adjust styles, query text blocks, etc.
- OpenAI-compatible API with configurable model and parameters

### Other

- **Theme switcher**: multiple built-in color themes with light/dark mode support
- **Keyboard shortcuts**: customizable shortcuts with save/load profiles
- **Export**: Word document export
- **Continuous reading**: multi-page reading and translation for long strips
- **Preview mode**: quick source/translation comparison

## Module Inventory

### Text Detection

| Module | Description |
|------|------|
| CTD (default) | CNN-based text detection, fast and accurate |
| YSG | YOLO-based text detection |

### OCR

| Module | Description |
|------|------|
| MIT48px-CTC (default) | Local OCR, no GPU required |
| LLM API OCR | Vision-capable LLM API for OCR |
| LM Studio | Local LM Studio integration |
| None | Disable OCR (manual text input) |

### Translation

| Module | Description |
|------|------|
| LLM API | Generic LLM API translator, OpenAI-compatible protocol |
| Sakura | Sakura translation model |

### Inpainting

| Module | Description |
|------|------|
| LaMa 512px (default) | Best quality, clean removal |
| AOT | Lightweight and fast, low resource usage |

## What's Stripped

Compared to upstream [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator), this fork:

- **Translators**: removed Baidu, Caiyun, DeepL, DeepLX, Google, Youdao, Papago, Sugoi, M2M100, Yandex and others (traditional MT no longer competes with LLMs for non-real-time scenarios)
  - Kept and added mainstream LLM API translators (note: some domestic API providers apply content filters that may reject sensitive input)
- **OCR engines**: removed PaddleOCR, Google Vision, Bing Lens, macOS/Windows native OCR, Manga OCR, OneOCR, Stariver and other rarely-used or low-quality engines
  - Local OCR defaults to MIT48px-CTC; LLM API OCR and LM Studio integration retained
- **Inpainting**: removed Flux Inpaint pipeline (heavy dependencies, large size) and other models with no clear advantage; kept LaMa 512px (best quality) and AOT (lightweight)
- **Other removals**: Saladict dictionary integration, keyword substitution panel, continuous headless mode, system HuggingFace cache option
- **Dependencies**: trimmed requirements.txt, removed keyboard, deeplx, saladict and other unnecessary packages

The complete project including models, dependencies, and CPU PyTorch is approximately **1.8 GB** and can be easily packaged for portability.

## Quick Start

> Note: The upstream project supports macOS and AMD GPUs, but lacking test hardware, this fork targets Windows CUDA / CPU only.
> Upstream macOS build scripts are included for your own testing (`scripts/build-macos-app.sh`, `scripts/macos-build-script-arm64.sh`); no technical support is provided for the reasons above.

Do NOT use the Microsoft Store version of Python. The WindowsApps directory leaves a `python.exe` placeholder that causes redirects even after uninstalling. If you already installed it, search for "Python opens Windows Store" for solutions.

### Windows Quick Launch

1. Download the source code and extract to a local directory
2. Run `launch_win.bat` (GPU mode) or `launch_cpu.bat` (CPU-only mode)
3. Model files will be downloaded automatically on first launch — keep your internet connection active

### Run from Source

```bash
git clone https://github.com/dmMaze/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU mode (auto-detects system PyTorch + CUDA, falls back to CPU)
python launch.py

# CPU mode (force bundled CPU PyTorch)
python launch.py --cpu

# Update
python launch.py --update
```

On first launch, PyTorch and other dependencies will be installed automatically, and model files (~700MB) will be downloaded. If the download fails, place the `data` directory in the project root manually.

GPU mode auto-detects PyTorch with CUDA from your system Python. If an RTX 50 series (Blackwell) GPU is detected, it automatically switches to CUDA 12.8+ nightly builds.

## Usage

### Basic Workflow

1. Open a folder containing manga/comic images
2. Configure source and target languages in the settings panel
3. Click "Run" and wait for the pipeline to finish
4. Double-click text blocks on the canvas to edit unsatisfactory translations

### Text Editing

- Double-click a text block on the canvas to enter edit mode
- Adjust font, size, color, stroke and other properties in the right-side font panel
- Save text styles as presets for quick reuse

### Inpainting Tools

- Use the inpainting brush to paint over areas to be restored
- Use rectangle/lasso tools to select regions for batch removal

## FAQ

**PyTorch + CUDA not detected?**
Ensure your system Python has PyTorch with CUDA installed:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**RTX 50 GPU CUDA not available?**
Blackwell architecture requires CUDA 12.8+. The app auto-switches to nightly builds. To manually reinstall:

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**How to update?**
Run `python launch.py --update` in the project root directory.

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project
