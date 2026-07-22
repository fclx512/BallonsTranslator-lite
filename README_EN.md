# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

A comic/image translation tool with a five-stage pipeline: text detection → OCR → translation → image inpainting → text rendering.

---

## System Requirements

- **OS**: Windows 10+ x64
- **GPU**: Optional (NVIDIA acceleration supported, CPU-only works)
- **Disk Space**: ~**2.1 GB** (models ~700 MB + embedded Python ~1.4 GB)
- **VC++ Runtime**: [VC++ Redistributable 2015-2022](https://aka.ms/vs/17/release/vc_redist.x64.exe) (required by embedded Python)

## Quick Start

### One-Click Bundle (recommended)

Download the complete dependencies and models (includes embedded Python 3.12, all dependencies, and model files) from:

- [123 Cloud Drive](https://1815181720.share.123865.com/123pan/sKBtVv-Zs1Vd) (preferred, mainly for CN users)
- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing) (may lag behind)

Extract and run `launch.bat`.

You can also download a lightweight bundle (without model files) — models (~700 MB) will be downloaded automatically on first launch:

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # Embedded Python 3.12 environment (~1.4 GB)
└── launch.bat
```

### Run from Source

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU mode: auto-detects CUDA, falls back to CPU
python launch.py

# Force CPU mode
python launch.py --cpu

# Update code
python launch.py --update
```

Dependencies install automatically on first launch. If auto-install fails:

```bash
pip install -r requirements.txt
```

### Model Files

Models download automatically to `data/models/` on first launch. You can also extract them from the full cloud storage package.

## GPU Acceleration

### One-Click Bundle Users

Run `install_cuda.bat` to install CUDA PyTorch into the embedded Python environment:

```cmd
install_cuda.bat
```

The script auto-detects GPU compute capability and selects the appropriate CUDA version (supports GTX 10 series and newer).

### Source Users

Ensure your system Python has CUDA-enabled PyTorch:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**RTX 50 series (Blackwell)** requires CUDA 12.8+:

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**Older GPUs (GTX 10 series, etc.)** may work better with CUDA 11.8:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

**Kepler (GTX 6xx / 7xx)** is not supported by PyTorch 2.x — use CPU mode.

## Updating

- **ZIP distribution users** (one-click bundle): Download the latest source from [Releases](https://github.com/fclx512/BallonsTranslator-lite/releases), extract and overwrite into your existing directory. The one-click bundle does not include git, so the launch script and in-app update features are unavailable.
- **Git users**: `python launch.py --update` or `git pull`
- **In-app check**: Help → About → Check for Updates (requires git on your system, shows commit logs only — does not auto-update)

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project
