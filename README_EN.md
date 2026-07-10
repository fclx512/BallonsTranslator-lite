# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

A lightweight fork of [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) focused on the core manga/comic translation pipeline.

---

## Differences from Upstream

### Module Selection

Available modules in lite vs. upstream:

| Stage | lite Modules | Upstream-Only Modules (not in lite) |
|-------|-------------|--------------------------------------|
| Text Detection | CTD, YSG | Same |
| OCR | MIT48px-CTC, LLM API OCR, LM Studio, Disabled | PaddleOCR, Google Vision, Bing Lens, etc. |
| Translation | LLM API (OpenAI-compatible), Sakura | Baidu, Caiyun, DeepL, Google, Youdao, Papago, etc. |
| Inpainting | LaMa 512px, AOT, Lama MPE | — |

Other streamlined features:
- Saladict dictionary lookup
- Keyword substitution
- Headless mode

### Interaction Changes

| Item | lite | Upstream |
|------|------|----------|
| Settings panel | Internal tabbed pages + center modal (OverlayModal), scrim covers canvas only | Right-side long scroll panel |
| Left panels (PageList/Global Search) | Push canvas to the right when expanded, zero occlusion | Overlay on top of canvas |
| About page | Help menu (About + MCP info) | Standalone About dialog |
| Text block reordering | Context menu + keyboard shortcuts | Panel-based |

### Deployment Differences

- **Bundled CPU PyTorch**: Full pipeline runs without an NVIDIA GPU
- **Auto GPU detection**: Detects GPU architecture generation and matches CUDA version (RTX 50 series auto-switches to CUDA 12.8+)
- **Embedded Python environment**: `ballontrans_pylibs_win/` is a self-contained Python 3.12 environment with all dependencies, no system Python required
- **No cross-Python ABI torch injection**: No longer attempts to share site-packages across different Python versions

> The upstream supports macOS and AMD GPUs. This fork targets Windows x64 CUDA/CPU only, due to lack of test hardware.

---

## Deployment

### System Requirements

- **OS**: Windows 10+ x64
- **GPU**: Optional (NVIDIA GPU provides acceleration, CPU-only works)
- **Disk Space**: ~**2.1 GB** (models ~700 MB + embedded Python ~1.4 GB)
- **VC++ Runtime**: [VC++ Redistributable 2015-2022](https://aka.ms/vs/17/release/vc_redist.x64.exe) (required by embedded Python)

### Windows One-Click

1. Download the source (ZIP or git clone) and extract to a local directory
2. Run the launch script:
   - `launch.bat` — auto-detects GPU/CPU mode (recommended)
   - `launch.bat --cpu` — force CPU-only mode
3. Model files (~700 MB) download automatically on first launch — keep your internet connection active

If downloads fail due to network issues, get the full package from cloud storage:

- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing)

Extract the downloaded archives into the project root:

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # Embedded Python environment
└── data/                     # Model files
```

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

Dependencies install automatically on first launch. If auto-install fails:

```bash
pip install -r requirements.txt
```

### Model Download

Models download automatically on first launch. For manual setup, place model files in `data/models/`.

### GPU Acceleration

#### Portable Users (embedded Python)

Run `install_cuda.bat` to install CUDA PyTorch into the embedded Python environment:

```cmd
install_cuda.bat
```

The script auto-detects GPU compute capability and selects the appropriate CUDA version.

#### Source Users

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

**Kepler (GTX 6xx / 7xx)** is not supported by PyTorch 2.x — use CPU mode: `python launch.py --cpu`

### Updating

- **ZIP distribution users**: `launch.bat` checks for GitHub updates on each launch and applies them on restart
- **Git users**:
  - Using batch script: `launch.bat --update`
  - Running Python directly: `python launch.py --update`
- **In-app**: Help → About → Check for Updates

## FAQ

**How to customize keyboard shortcuts?**

See [Shortcuts Guide](docs/快捷键.md)

**How to configure translation API?**

Settings → Models → API Profiles.

**How to use MCP?**

See [MCP User Guide](docs/MCP用户指南.md)

---

## Acknowledgement

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — upstream project
- All open-source models and libraries used by this project
