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
- **Disk space**: The recommended full bundle is approx. **1.7–2.1 GB** (matching Python environment, model backends and some models). CUDA bundles vary by profile. The approx. 30 MB lightweight package requires explicit online dependency repair and is intended for maintainers or advanced users.
- **VC++ Runtime**: [VC++ Redistributable 2015-2022 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe) (required by the embedded Python)

## Quick Start

### Full One-Click Bundle (recommended)

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

`launch.bat` uses only the bundled `ballontrans_pylibs_win/python.exe`; it does not depend on system Python, PATH, or user site-packages. If the runtime is missing, it asks you to extract the matching full bundle. It also detects an available GPU and selects the CPU/GPU path.

The full bundle includes a Python environment matching the source, with dependencies and model backends already installed, so it can start offline. Included model weights depend on the selected bundle; missing weights can be downloaded from the GUI. Choose the matching CPU or CUDA profile; avoid manually mixing CUDA components into a release environment.

### Lightweight Release Package (maintainers / advanced users)

The approx. 30 MB lightweight package contains source and embedded Python only; core dependencies, model backends, and weights are not pre-installed. Normal startup does not install dependencies online. Run `launch.bat --repair-deps` only when you explicitly agree to an online repair. This requires network access, and some model files or native assets may also need separate downloads. Ordinary and offline users should use the full bundle above.

The [Releases page](https://github.com/fclx512/BallonsTranslator-lite/releases/latest) provides `BallonsTranslator-lite_win_min.zip`, a maintainer bootstrap package rather than a complete runtime. See `scripts/build_win_minimal.ps1` and the "Lightweight Package Build" section in `scripts/README.md`.

### Running from Source

Windows source users should also download the matching full bundle and extract its `ballontrans_pylibs_win/` beside the source, then start with `launch.bat`. Do not depend on system Python as the default or fallback.

```cmd
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite
rem After placing the matching ballontrans_pylibs_win/ here:
launch.bat --cpu
launch.bat --update
```

- If the matching environment is absent, startup stops with a message when core dependencies are missing. Use `launch.bat --repair-deps` only to explicitly opt into online repair; first-run internet installation is not the recommended setup.
- All dependency and model downloads default to the **official sources**. If you need a mirror (e.g. in mainland China), configure it manually under **Settings → Network & Mirrors** (the `mirror` section of `config/config.json`).
- Model inference dependencies vary by CPU/CUDA profile. Prefer the matching full environment bundle; avoid manual mixing in a release environment.
- The **PatchMatch inpainter** is a low-overhead basic capability. Its two native libraries (`data/libs/patchmatch_inpaint.dll` and `data/libs/opencv_world455.dll`, approx. 53 MB) are distributed as Release assets: they download in the background the first time the inpainter is selected, or fetch them manually on the Settings → Models → Model Files page (the full bundle already includes them). The app still starts without them, but using PatchMatch will show a readable missing-attachment message.
- See `python launch.py --help` for all arguments (e.g. `--proj-dir` to open a project at startup, `--headless` to run without a GUI).

## GPU Acceleration

NVIDIA GPUs only (AMD / Intel GPUs run on CPU). Ordinary users should download a CUDA full bundle matching their GPU profile. `install_cuda.bat` is an online maintenance tool: it only checks and displays a plan by default. To deliberately modify the bundled environment, run:

```cmd
install_cuda.bat --install
```

`install_cuda.bat` performs a read-only check by default; `install_cuda.bat --json` prints machine-readable results and remains read-only. `--install` downloads and changes the bundled PyTorch/ONNX Runtime components. This is a maintainer operation; recheck runtime behavior and the package manifest afterwards. The tool operates only on `ballontrans_pylibs_win`, never system Python or an arbitrary venv.

The index is selected by compute capability (same thresholds as the mapping in `utils/env_diagnostic.py`):

| GPU | Compute capability | CUDA index |
| --- | --- | --- |
| GTX 10 series / GTX 16 series / RTX 20·30·40 series | 6.x – 8.9 | `cu126` |
| Hopper and other data-center cards | 9.x | `cu130` |
| RTX 50 series (Blackwell) | 10.x / 12.x | `cu132` |
| Kepler (GTX 6xx / 7xx) and older | < 6 | Not supported, use CPU |

For a separately maintained CUDA environment, use the official PyTorch installation selector and independently validate the Torch, TorchVision, and ONNX Runtime combination. A single command does not guarantee a compatible release environment:

[Official PyTorch version pairs](https://pytorch.org/get-started/previous-versions/)

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
