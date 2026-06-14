# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

<!-- SCREENSHOT: Main interface overview -->

A lightweight manga/comic translation tool based on [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator).

> **Note**: This project is under active refactoring. The README may not reflect all recent changes — when in doubt, the actual behavior takes precedence.

---

## About This Project

BallonsTranslator was a full-featured open-source lettering tool covering numerous translation engines, OCR backends, and inpainting pipelines. But more features meant a steeper learning curve — new users faced a dozen translator options and OCR engines with little guidance on what to pick, while most of them had already been superseded by LLM-based translation in everyday use.

This fork exists to **keep the full lettering pipeline while drastically lowering the barrier to entry**:

### Streamlined modules

Dozens of translators (Baidu, Caiyun, DeepL, Google, Youdao, Papago…) and OCR engines (PaddleOCR, Google Vision, Bing Lens…) were removed — traditional machine translation no longer competes with LLMs for non-real-time scenarios, and low-quality OCR engines only add noise. Fewer choices means a cleaner config panel and less decision fatigue.

Heavy inpainting pipelines like Flux Inpaint were dropped in favor of LaMa (best quality) and AOT (lightweight). Features like Saladict dictionary lookup, keyword substitution, and headless mode — which added complexity without daily utility — were removed as well.

### Simplified interaction

The UI and interaction flow have been reworked to be more intuitive. Specifics are covered in the workflow section and video tutorials rather than listed here.

### Lower hardware requirements

Bundled CPU-only PyTorch means the full translation pipeline runs without an NVIDIA GPU. GPU architecture is auto-detected and matched to the appropriate CUDA version — no manual environment setup needed.

### Bottom line

The complete project (models + CPU PyTorch) is approximately **1.8 GB** and easy to package for portability.

---

## Quick Start

### Windows One-Click

1. Download the source code and extract to a local directory
2. Run a launch script:
   - `launch.bat` — auto-detects GPU/CPU mode; Git users get automatic update checks (recommended)
   - `launch.bat --cpu` — force CPU-only mode
3. Model files (~700 MB) are downloaded automatically on first launch — keep your internet connection active

### Run from Source

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU mode: auto-detects system PyTorch + CUDA, falls back to CPU
python launch.py

# CPU mode: force CPU-only
python launch.py --cpu

# Update code
python launch.py --update
```

Dependencies and model files are installed automatically on first launch. If auto-download fails, place the `data` directory in the project root manually.

GPU mode auto-detects NVIDIA GPU architecture (Kepler through Blackwell) and selects the appropriate CUDA version. RTX 50 series (Blackwell) auto-switches to CUDA 12.8+ nightly; older cards receive generation-appropriate recommendations.

> The upstream supports macOS and AMD GPUs, but lacking test hardware this fork targets Windows CUDA / CPU only.

---

## Feature Overview

(Detailed workflow is covered in video tutorials.)

**One-click translation pipeline** — text detection → OCR → translation → inpainting → typesetting, fully automatic

<!-- SCREENSHOT: Before/after pipeline comparison -->

**Typesetting** — double-click any text block on the canvas to edit. Font, size, stroke color, alignment, line spacing, shadow and gradient are all adjustable. Style presets let you apply consistent formatting across pages

<!-- SCREENSHOT: Canvas editing with effects -->

**Inpainting** — brush tool for targeted text removal, rectangle/lasso selection for batch clearing, and mask editing for fine control

<!-- SCREENSHOT: Before/after inpainting comparison -->

**AI assistant** — modify translations and styles through natural language in the chat panel. Changes are reviewed item by item before being applied

<!-- SCREENSHOT: AI chat panel + review window -->

**Search & replace** — within a page or across pages, search source text and/or translations with batch replace

**Customization** — multiple color themes (light/dark), rebindable keyboard shortcuts

---

## Module Reference

| Stage | Available Modules |
|-------|------------------|
| Text Detection | CTD (default), YSG |
| OCR | MIT48px-CTC (default), LLM API OCR, LM Studio, Disabled |
| Translation | LLM API (OpenAI-compatible), Sakura |
| Inpainting | LaMa 512px (default), AOT |

> Want to add your own module? See [Module Developer Guide](docs/模块开发指南.md)

---

## FAQ

**PyTorch + CUDA not detected?**

Make sure your system Python has CUDA-enabled PyTorch:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**RTX 50 series CUDA not available?**

Blackwell requires CUDA 12.8+. Manually reinstall:

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**Older GPUs (GTX 10 series, etc.) — CUDA not available?**

Maxwell/Pascal and other older architectures may work better with CUDA 11.8:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

Kepler (GTX 6xx / 7xx) may not be supported by PyTorch 2.x — use CPU mode: `python launch.py --cpu`

**How to update?**

- Portable users: use `launch.bat` — auto-detects Git/ZIP mode; ZIP builds check for updates on each launch and apply on restart. No git required.
- Source users: `launch.bat --update` (Git mode uses `git pull`; ZIP mode downloads directly)
- Or click About → Check for Updates in the app.

**How to customize shortcuts?**

See [Shortcuts Guide](docs/快捷键.md)

---

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project
