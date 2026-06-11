"""Environment diagnostic utilities — no GUI dependencies.

Extracted from ``launch.py`` so the same checks can be re-used by the
Environment section in the config panel (and at startup where needed).
"""

import importlib.metadata
import importlib.util
import os
import platform
import re
import subprocess
import sys
from typing import Optional


# ── GPU detection ──────────────────────────────────────────────────────────

_GPU_INFO_CACHE: Optional[dict] = None


def detect_gpu_info() -> Optional[dict]:
    """Detect NVIDIA GPU via ``nvidia-smi`` and return architecture info.

    Returns a dict with keys:
        name, generation, recommended_cuda, torch_index, message.
    Returns ``None`` if no NVIDIA GPU is detected.
    Result is cached (nvidia-smi runs once).
    """
    global _GPU_INFO_CACHE
    if _GPU_INFO_CACHE is not None:
        return _GPU_INFO_CACHE

    try:
        _nvsmi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if _nvsmi.returncode != 0 or not _nvsmi.stdout.strip():
            _GPU_INFO_CACHE = None
            return None
        _gpu_name = _nvsmi.stdout.strip()
    except Exception:
        _GPU_INFO_CACHE = None
        return None

    name_upper = _gpu_name.upper()

    # 1) Blackwell — RTX 50 series, needs CUDA 12.8+ nightly
    if any(
        n in name_upper
        for n in ["RTX 5090", "RTX 5080", "RTX 5070", "RTX 5060", "RTX 50"]
    ):
        info = dict(
            name=_gpu_name,
            generation="Blackwell",
            recommended_cuda="12.8+",
            torch_index="https://download.pytorch.org/whl/nightly/cu128",
            message=(
                f"Detected Blackwell GPU ({_gpu_name}) — "
                "requires CUDA 12.8+ (nightly PyTorch build)."
            ),
        )
        _GPU_INFO_CACHE = info
        return info

    # 2) Try to extract series number from consumer GPU names
    m = re.search(r"(?:RTX|GTX)\s*(\d+)", name_upper)
    if m:
        model = m.group(1)
        series = int(model[:2]) if len(model) == 4 else int(model[0])

        if series >= 40:
            info = dict(
                name=_gpu_name,
                generation="Ada Lovelace",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Ada Lovelace GPU ({_gpu_name}) — "
                    "CUDA 12.4 recommended."
                ),
            )
        elif series >= 30:
            info = dict(
                name=_gpu_name,
                generation="Ampere",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=f"Detected Ampere GPU ({_gpu_name}) — CUDA 12.4 recommended.",
            )
        elif series >= 20 or series == 16:
            info = dict(
                name=_gpu_name,
                generation="Turing",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Turing GPU ({_gpu_name}) — "
                    "CUDA 12.4 is supported."
                ),
            )
        elif series >= 10:
            info = dict(
                name=_gpu_name,
                generation="Pascal",
                recommended_cuda="11.8 / 12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Pascal GPU ({_gpu_name}) — older architecture.\n"
                    "  CUDA 12.4 is supported. If you have an older NVIDIA driver\n"
                    "  and CUDA 12.4 fails, try CUDA 11.8 instead:\n"
                    "    pip uninstall torch torchvision torchaudio -y\n"
                    "    pip install torch torchvision torchaudio"
                    " --index-url https://download.pytorch.org/whl/cu118"
                ),
            )
        elif series >= 8:
            info = dict(
                name=_gpu_name,
                generation="Maxwell",
                recommended_cuda="11.8",
                torch_index="https://download.pytorch.org/whl/cu118",
                message=(
                    f"Detected Maxwell GPU ({_gpu_name}) — older architecture.\n"
                    "  CUDA 11.8 recommended. If it fails, try CPU mode:\n"
                    "    python launch.py --cpu\n"
                    "  Or install CUDA 11.8 PyTorch:\n"
                    "    pip install torch torchvision torchaudio"
                    " --index-url https://download.pytorch.org/whl/cu118"
                ),
            )
        else:
            info = dict(
                name=_gpu_name,
                generation="Kepler",
                recommended_cuda="N/A",
                torch_index=None,
                message=(
                    f"Detected Kepler GPU ({_gpu_name}) — very old architecture.\n"
                    "  PyTorch 2.x may not support this GPU.\n"
                    "  Consider using CPU mode: python launch.py --cpu"
                ),
            )
        _GPU_INFO_CACHE = info
        return info

    # 3) Titan variants without RTX/GTX prefix
    if "TITAN RTX" in name_upper:
        info = dict(
            name=_gpu_name,
            generation="Turing",
            recommended_cuda="12.4",
            torch_index="https://download.pytorch.org/whl/cu124",
            message=f"Detected Turing GPU ({_gpu_name}) — CUDA 12.4 is supported.",
        )
    elif "TITAN" in name_upper:
        info = dict(
            name=_gpu_name,
            generation="Titan (legacy)",
            recommended_cuda="11.8",
            torch_index="https://download.pytorch.org/whl/cu118",
            message=(
                f"Detected legacy Titan GPU ({_gpu_name}) — older architecture.\n"
                "  CUDA 11.8 recommended."
            ),
        )
    else:
        info = dict(
            name=_gpu_name,
            generation="Unknown",
            recommended_cuda="12.4",
            torch_index="https://download.pytorch.org/whl/cu124",
            message=f"Detected GPU: {_gpu_name}",
        )

    _GPU_INFO_CACHE = info
    return info


# ── Torch helpers ─────────────────────────────────────────────────────────


def check_torch() -> dict:
    """Check PyTorch availability and CUDA capability *without importing* it
    when possible.  Returns a dict::

        {"available": bool,
         "cuda_available": bool,     # only meaningful if available
         "version": str | None,
         "import_error": str | None}
    """
    result = {"available": False, "cuda_available": False,
              "version": None, "import_error": None}

    spec = importlib.util.find_spec("torch")
    if spec is None:
        result["import_error"] = "torch package not found"
        return result

    # Try metadata first (no import side-effects)
    try:
        result["version"] = importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        pass

    # Import to get CUDA status — this is the one place we do it
    try:
        import torch  # noqa: E402
    except (ImportError, Exception) as e:
        result["import_error"] = str(e)
        return result

    result["available"] = True

    if result["version"] is None:
        result["version"] = getattr(torch, "__version__", "unknown")

    if hasattr(torch, "cuda") and torch.cuda.is_available():
        result["cuda_available"] = True
        result["cuda_version"] = torch.version.cuda
    else:
        result["cuda_available"] = False

    return result


# ── MCP check ────────────────────────────────────────────────────────────────


def check_mcp() -> dict:
    """Check if the MCP package is installed and whether the server module loads.

    Returns a dict::

        {"available": bool,
         "version": str | None,
         "server_loadable": bool}
    """
    result: dict = {"available": False, "version": None, "server_loadable": False}

    try:
        result["version"] = importlib.metadata.version("mcp")
        result["available"] = True
    except importlib.metadata.PackageNotFoundError:
        pass

    try:
        import mcp_server.main  # noqa: F401
        result["server_loadable"] = True
    except ImportError:
        pass

    return result


# ── Full diagnostic ───────────────────────────────────────────────────────────


def run_diagnostic() -> dict:
    """Run all environment checks and return a structured report.

    Returns a dict with keys:
        python_version, python_path, os_platform,
        torch, gpu, diagnostic_lines
    """
    lines: list[str] = []
    info: dict = {}

    # Python
    info["python_version"] = sys.version
    info["python_path"] = sys.executable
    lines.append(f"Python: {sys.version.split()[0]} ({sys.executable})")
    lines.append(f"OS: {platform.platform()}")

    # Torch
    torch_info = check_torch()
    info["torch"] = torch_info
    if torch_info["available"]:
        cuda = torch_info["cuda_available"]
        ver = torch_info["version"] or "unknown"
        lines.append(f"PyTorch: {ver} (CUDA: {'yes' if cuda else 'no'})")
        if cuda:
            lines.append(f"  CUDA version: {torch_info.get('cuda_version', 'unknown')}")
    else:
        err = torch_info["import_error"]
        lines.append(f"PyTorch: not installed ({err})")

    # GPU via nvidia-smi
    gpu_info = detect_gpu_info()
    info["gpu"] = gpu_info
    if gpu_info:
        lines.append(f"GPU: {gpu_info['name']} ({gpu_info['generation']})")
        lines.append(f"Recommended CUDA: {gpu_info['recommended_cuda']}")
        lines.append(f"Recommendation: {gpu_info['message']}")
    else:
        lines.append("GPU: not detected (no nvidia-smi or no NVIDIA GPU)")

    info["diagnostic_lines"] = lines

    # MCP
    mcp_info = check_mcp()
    info["mcp"] = mcp_info
    if mcp_info["available"]:
        lines.append(
            f"MCP: {mcp_info['version']} "
            f"(server loadable: {mcp_info['server_loadable']})"
        )
    else:
        lines.append(
            "MCP: not installed (needed for AI agent project editing; "
            "install via 'pip install \"mcp>=1.0.0\"' or 'pip install -e \".[mcp]\"')"
        )

    return info
