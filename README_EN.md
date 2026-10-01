# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

A fork of BallonsTranslator for comic/image translation. The upstream five-stage pipeline (text detection → OCR → translation → image inpainting → text rendering) is kept fully intact; the changes focus on shortening common operation paths and trimming what is not used (a personal preference).

---

## Before You Start

**Development** This repository is 100% developed and maintained via vibe coding. The author cannot guarantee the project's stability and has not verified every possible use case. For serious problems such as crashes or startup failures, please report them in an issue and attach the terminal output. Feedback from real use is equally welcome.

**Feature showcase — under construction……**

## System Requirements

- **OS**: Windows 10+ x64 (macOS see below; other platforms unverified)
- **GPU**: Optional. An NVIDIA GPU can accelerate (see [GPU Acceleration](#gpu-acceleration)); CPU-only works
- **Disk space**: The release package is approx. **30 MB** (bootstrap; after the first-run install the local environment is approx. **600 MB**, models excluded); the offline full bundle is approx. **1.7–2.1 GB** (including model backends and weights); enabling GPU acceleration adds approx. **2.2 GB** (CUDA PyTorch ~2 GB + onnxruntime-gpu ~214 MB)
- **VC++ Runtime**: [VC++ Redistributable 2015-2022 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe) (required by the embedded Python)

## Quick Start

### Release Package (recommended)

The release package is a small bootstrap bundle containing only the **basic boot environment** (source + embedded Python, approx. 30 MB compressed): dependencies are not pre-installed — running `launch.bat` after extracting installs the basic `requirements.txt` dependencies and restarts, leaving a local environment of roughly 600 MB. It ships no torch, YOLO/ONNX/Transformers backends and no model weights; the two PatchMatch native libraries (approx. 53 MB) are downloaded in the background the first time that inpainter is selected. Model backends and weights can then be downloaded on demand from the GUI model files page.

Download `BallonsTranslator-lite_win_min.zip` from the [Releases page](https://github.com/fclx512/BallonsTranslator-lite/releases/latest) and extract it to use; see `scripts/build_win_minimal.ps1` and the "发版包发行" section in `scripts/README.md` for how it is built.

### Full One-Click Bundle (offline fallback)

Download the complete bundle (dependencies and models included) from the file hosts, extract it to any directory and run `launch.bat`:

- [139 Cloud (China Mobile)](https://yun.139.com/shareweb/#/w/i/2xTrJSfJTx8m8) (extraction code: `8xys`)
- [Baidu Netdisk](https://pan.baidu.com/s/1QfPub2loKQ7Zp9lyhYtSnA?pwd=1111) (extraction code: `1111`)
- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing) (updates may lag behind)

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # Embedded Python 3.12 + all dependencies (~1.4 GB)
├── data/                     # Model files and PatchMatch native libraries (~700 MB)
├── config/                   # Configuration and themes (config.json is created on first run)
├── ui/  modules/  utils/     # Application source
└── launch.bat                # Double-click to start
```

`launch.bat` locates a usable Python by itself (the bundled environment first), determines whether a usable GPU is present, and distinguishes the one-click bundle from a git install — no manual configuration needed.

The full bundle ships the basic dependencies and model backends pre-installed, so it enters the GUI without network access: manual typesetting, multi-page workbench operations, Photoshop integration, PatchMatch simple-background repair and the automated pipeline all work out of the box (any weights not bundled download on demand in the GUI). Use this bundle when no usable network is available — the release package needs the network on its first launch to install dependencies.

### Running from Source

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

python launch.py            # first launch installs missing dependencies, then restarts
python launch.py --cpu      # force CPU mode
python launch.py --update   # pull code updates before launching
```

- Requires **Python 3.10+** (the official installer or the Microsoft Store version; the Store version creates a project-local `.venv` automatically).
- All dependency and model downloads default to the **official sources**. If you need a mirror (e.g. in mainland China), configure it manually under **Settings → Network & Mirrors** (the `mirror` section of `config/config.json`).
- If automatic dependency installation fails, run `pip install -r requirements.txt` manually. The heavy dependencies needed for model inference (`torch` / `transformers` etc.) are not part of it — install them as needed (see [GPU Acceleration](#gpu-acceleration)).
- The **PatchMatch inpainter** is a low-overhead basic capability. Its two native libraries (`data/libs/patchmatch_inpaint.dll` and `data/libs/opencv_world455.dll`, approx. 53 MB) are distributed as Release assets: they download in the background the first time the inpainter is selected, or fetch them manually on the Settings → Models → Model Files page (the full bundle already includes them). The app still starts without them, but using PatchMatch will show a readable missing-attachment message.
- See `python launch.py --help` for all arguments (e.g. `--proj-dir` to open a project at startup, `--headless` to run without a GUI).

## GPU Acceleration

NVIDIA GPUs only (AMD / Intel GPUs run on CPU). **One-click bundle users** just run:

```cmd
install_cuda.bat
```

The script detects the GPU's compute capability, matches the corresponding CUDA index, and installs CUDA PyTorch (approx. 2 GB) plus onnxruntime-gpu (approx. 214 MB) into the embedded Python environment; anything already installed is skipped. Afterwards launch as usual with `launch.bat` — GPU mode takes effect automatically.

- `install_cuda.bat --manual`: prints the commands only, installs nothing (for source installs, your own venv, conda environments)
- `install_cuda.bat --replace`: force installation into the one-click bundle's embedded environment

The index is selected by compute capability (same thresholds as the mapping in `utils/env_diagnostic.py`):

| GPU | Compute capability | CUDA index |
| --- | --- | --- |
| GTX 10 series / GTX 16 series / RTX 20·30·40 series | 6.x – 8.9 | `cu126` |
| Hopper and other data-center cards | 9.x | `cu130` |
| RTX 50 series (Blackwell) | 10.x / 12.x | `cu132` |
| Kepler (GTX 6xx / 7xx) and older | < 6 | Not supported, use CPU |

**Source installs** should not run `install_cuda.bat` (it installs into the bundle's embedded environment by default); install into your own environment instead:

```bash
pip install -U torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

You can also run `install_cuda.bat --manual` to print the command matching your GPU, or `python launch.py --reinstall-torch` to let the launcher pick the index automatically.

Two indexes to avoid:

- **`cu124`**: the newest version on that index is torch 2.6.0, while this project's baseline is torch 2.13.x — using it silently downgrades an existing torch.
- **`nightly/cu128`**: it does install, but nightly is a nightly-build channel whose version drifts every day; on CC 12.x use the stable `cu132` index instead.

## Model Files

- Weights are not stored in the repository; they all live under `data/models/`.
- They download automatically when you select a module that needs weights; modules marked as large models (e.g. PaddleOCR-VL) download in the background instead — progress goes to the terminal only, with no dialog and no blocking.
- Models that require a GPU are refused outright on machines without a usable accelerator, with the reason explained.
- **Settings → Models → "Model Files"** shows each module's on-disk status and size, and lets you download / cancel / delete manually (deletion goes to the recycle bin).

## Updating

- **In-app (recommended)**: **Settings → App → Updates → "Check update"**. When a new version is found the release notes appear; after you confirm, the program source is downloaded and replaced automatically and the app restarts — `data/`, models, configuration and the embedded environment are untouched. "Check update on startup" performs a silent check at launch.
- **Developer channel**: "Check commit updates" in the same section follows the latest commit — unverified changes that may not work on every machine.
- **One-click bundle (no git)**: `launch.bat --update` fetches changed source files incrementally by manifest and applies them on the next restart (`launch.bat --check-update` only checks). You can also download the new source archive and overwrite the existing directory.
- **Git installs**: `python launch.py --update` or `git pull`.

## macOS

**Not verified on a Mac, so usability is not guaranteed**, and there is no one-click bundle. Run from source:

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install torch torchvision transformers diffusers   # Apple Silicon uses MPS acceleration
python launch.py
```

- Requires Python 3.10 or newer; 3.12 recommended (the bundle ships 3.12; 3.13 and above are unverified).
- **Apple Silicon (M series)**: PyTorch uses MPS acceleration automatically, no CUDA needed; Intel Macs or systems without MPS fall back to CPU.
- `launch.bat` and `install_cuda.bat` are Windows-only.
- PatchMatch native libraries are Windows-only (downloaded automatically from Release assets when selected; the full bundle includes them); macOS has no corresponding library, so use a PyTorch-based inpainter instead.

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project

## License

[GPL-3.0](LICENSE), same as upstream.
