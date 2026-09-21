"""Environment diagnostic utilities — no GUI dependencies.

Extracted from ``launch.py`` so the same checks can be re-used by the
Environment section in the config panel (and at startup where needed).
"""

import importlib.metadata
import importlib.util
import platform
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

# ── GPU detection ──────────────────────────────────────────────────────────

_GPU_INFO_CACHE: Optional[dict] = None

# ── CUDA index selection (single source of truth) ──────────────────────────
#
# The CUDA index a machine should install from is determined by compute
# capability (CC), NOT by GPU marketing names.  ``install_cuda.bat`` uses
# exactly the same thresholds, so the two never disagree.
#
# Why cu124 must never come back: the cu124 index stops at torch 2.6.0
# while this project ships 2.13.x, so selecting it silently downgrades
# torch by several minor versions.  Verified against the live indexes:
#
#   cu118 -> 2.0.0 .. 2.7.1        (alive, but not offered: CC < 6 is unsupported)
#   cu124 -> 2.4.0 .. 2.6.0        (EOL)
#   cu126 -> 2.6.0 .. 2.14.0       (default for CC 6..8)
#   cu130 -> 2.9.0 .. 2.14.0
#   cu132 -> 2.12.0 .. 2.14.0
#
# ``nightly/cu128`` used to be handed out for Blackwell.  It is NOT dead
# (it serves nightly 2.12.0) — it is simply the wrong thing to hand a
# normal user, because a nightly channel drifts every night.  CC >= 10
# maps to the stable cu132 instead.
_CUDA_TIERS = (
    # (min_compute_capability, index_tag, label, onnxruntime-spec)
    (10, "cu132", "13.2", "onnxruntime-gpu>=1.20,<1.29"),
    (9, "cu130", "13.0", "onnxruntime-gpu>=1.20,<1.29"),
    (6, "cu126", "12.6", "onnxruntime-gpu>=1.20,<1.29"),
)


def pick_cuda_index(compute_cap: Optional[int]) -> Optional[dict]:
    """Map a compute capability major version to a CUDA install target.

    Returns a dict with ``index_tag``, ``url``, ``label`` (for messages)
    and ``onnxruntime_spec``, or ``None`` when the GPU is too old for any
    CUDA PyTorch build (CC < 6 — Kepler and earlier).

    This is the authoritative mapping shared with ``install_cuda.bat``;
    keep the two in step when indexes are added or retired.
    """
    if compute_cap is None:
        return None
    for min_cc, tag, label, ort_spec in _CUDA_TIERS:
        if compute_cap >= min_cc:
            return {
                "index_tag": tag,
                "url": f"https://download.pytorch.org/whl/{tag}",
                "label": label,
                "onnxruntime_spec": ort_spec,
            }
    return None


def _query_compute_cap() -> Optional[int]:
    """Return the GPU's compute capability major version, or None.

    Prefers ``nvidia-smi --query-gpu=compute_cap``; older drivers do not
    support that field, in which case we fall back to inferring it from
    the GPU name (see :func:`_cc_from_name`).
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first = proc.stdout.strip().splitlines()[0].split(".")[0].strip()
            if first.isdigit():
                return int(first)
    except Exception:
        pass
    return None


# Name → compute capability major, for drivers whose nvidia-smi predates
# the compute_cap query field.  Only the families this app realistically
# meets are listed; unknown names fall through to the default tier.
def _cc_from_name(name_upper: str) -> Optional[int]:
    """Infer a compute capability major from the GPU's marketing name.

    Only used when ``nvidia-smi`` cannot answer the ``compute_cap`` query
    (pre-Turing drivers).  Unknown names return ``None`` so the caller can
    pick a safe default.
    """
    m = re.search(r"(?:RTX|GTX)\s*(\d+)", name_upper)
    if m:
        model = m.group(1)
        series = int(model[:2]) if len(model) == 4 else int(model[0])
        if series >= 50:
            return 12  # Blackwell
        if series >= 30:
            return 8  # Ada Lovelace (8.9) / Ampere (8.6)
        if series >= 20 or series == 16:
            return 7  # Turing (7.5)
        if series >= 10:
            return 6  # Pascal (6.1)
        if series >= 8:
            return 5  # Maxwell
        return 3  # Kepler
    if "TITAN RTX" in name_upper:
        return 7
    if "TITAN" in name_upper:
        return 6
    return None


def detect_gpu_info() -> Optional[dict]:
    """Detect NVIDIA GPU via ``nvidia-smi`` and return architecture info.

    Returns a dict with keys:
        name, generation, compute_cap, recommended_cuda, torch_index, message.
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
        _gpu_name = _nvsmi.stdout.strip().splitlines()[0].strip()
    except Exception:
        _GPU_INFO_CACHE = None
        return None

    name_upper = _gpu_name.upper()

    # Compute capability decides the CUDA target, exactly like the .bat.
    cc = _query_compute_cap()
    if cc is None:
        cc = _cc_from_name(name_upper)
    if cc is None:
        cc = 8  # unknown name but a real NVIDIA card: assume a safe modern tier

    # Human-readable generation, derived from the same CC number.
    if cc >= 10:
        generation = "Blackwell"
    elif cc >= 9:
        generation = "Hopper"
    elif cc >= 8:
        generation = "Ada Lovelace / Ampere"
    elif cc >= 7:
        generation = "Turing"
    elif cc >= 6:
        generation = "Pascal"
    elif cc >= 5:
        generation = "Maxwell"
    else:
        generation = "Kepler"

    target = pick_cuda_index(cc)
    if target is None:
        info = dict(
            name=_gpu_name,
            generation=generation,
            compute_cap=cc,
            recommended_cuda="N/A",
            torch_index=None,
            message=(
                f"Detected {generation} GPU ({_gpu_name}, CC {cc}.x) — "
                "very old architecture.\n"
                "  PyTorch 2.x may not support this GPU.\n"
                "  Consider using CPU mode: python launch.py --cpu"
            ),
        )
        _GPU_INFO_CACHE = info
        return info

    info = dict(
        name=_gpu_name,
        generation=generation,
        compute_cap=cc,
        recommended_cuda=target["label"],
        torch_index=target["url"],
        message=(
            f"Detected {generation} GPU ({_gpu_name}, CC {cc}.x) — "
            f"CUDA {target['label']} recommended."
        ),
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
    result = {
        "available": False,
        "cuda_available": False,
        "version": None,
        "import_error": None,
    }

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

    return info


# ── Module registry checks ─────────────────────────────────────────────────


def check_module_status() -> List[Dict[str, Any]]:
    """Check each pipeline stage's currently-configured module.

    Returns a list of dicts, one per stage, with keys:
        stage (str): textdetector / ocr / translator / inpainter
        active_key (str): the module key currently configured
        enabled (bool): whether this pipeline stage is toggled on
        resolved (bool): True if module class was successfully imported
        error (str | None): import error message, if any
        available (list[str]): all registered module keys for this stage
        has_params (bool): whether config params exist for this module
    """
    from utils.config import pcfg

    module = pcfg.module

    stages: List[Dict[str, Any]] = [
        {
            "stage": "textdetector",
            "active_key": module.textdetector,
            "enabled": module.enable_detect,
        },
        {
            "stage": "ocr",
            "active_key": module.ocr,
            "enabled": module.enable_ocr,
        },
        {
            "stage": "translator",
            "active_key": module.translator,
            "enabled": module.enable_translate,
        },
        {
            "stage": "inpainter",
            "active_key": module.inpainter,
            "enabled": module.enable_inpaint,
        },
    ]

    # Map stage names to registries — imported lazily to avoid circular imports
    from modules import INPAINTERS, OCR, TEXTDETECTORS, TRANSLATORS

    _registry_map = {
        "textdetector": TEXTDETECTORS,
        "ocr": OCR,
        "translator": TRANSLATORS,
        "inpainter": INPAINTERS,
    }
    _params_map = {
        "textdetector": module.textdetector_params,
        "ocr": module.ocr_params,
        "translator": module.translator_params,
        "inpainter": module.inpainter_params,
    }

    results: List[Dict[str, Any]] = []
    for s in stages:
        stage_name = s["stage"]
        registry = _registry_map[stage_name]
        key = s["active_key"]
        enabled = s["enabled"]

        resolved = False
        error: Optional[str] = None
        available: List[str] = list(registry.module_dict.keys())
        has_params = bool(_params_map[stage_name].get(key))

        if key and enabled:
            try:
                registry.resolve_module(key)
                resolved = True
            except Exception as e:
                error = str(e)

        results.append({
            "stage": stage_name,
            "active_key": key,
            "enabled": enabled,
            "resolved": resolved,
            "error": error,
            "available": available,
            "has_params": has_params,
        })

    return results


def test_module_functional(stage: str, module_key: str) -> Dict[str, Any]:
    """Run a functional test for a specific module.

    Returns a dict with keys:
        success (bool): whether the test passed
        output (str): log/diagnostic output
        duration_ms (int): how long the test took
    """
    from modules import INPAINTERS, OCR, TEXTDETECTORS, TRANSLATORS

    _registry_map = {
        "textdetector": TEXTDETECTORS,
        "ocr": OCR,
        "translator": TRANSLATORS,
        "inpainter": INPAINTERS,
    }

    registry = _registry_map.get(stage)
    if not registry:
        return {"success": False, "output": f"Unknown stage: {stage}", "duration_ms": 0}

    import time
    from pathlib import Path

    from utils import shared

    start = time.perf_counter()

    try:
        # Step 1: resolve (import) the module class
        try:
            cls = registry.resolve_module(module_key)
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return {
                "success": False,
                "output": f"Failed to import module '{module_key}': {e}",
                "duration_ms": elapsed,
            }

        output_parts: List[str] = []
        output_parts.append(f"[1/4] Module class resolved: {cls.__name__}")

        # Step 2: get spec details
        spec = registry.get_spec(module_key)
        if spec:
            output_parts.append(f"[2/4] Source: {spec.import_path}.{spec.class_name}")
            # Check declared model files
            dl_files = spec.download_file_list
            if dl_files:
                total_files = 0
                found_files = 0
                for entry in dl_files:
                    raw_files = entry.get("files") or []
                    if isinstance(raw_files, str):
                        raw_files = [raw_files]
                    save_files = entry.get("save_files")
                    paths = save_files if save_files else raw_files
                    for fpath in paths:
                        total_files += 1
                        if not fpath:
                            continue
                        abs_path = fpath if Path(fpath).is_absolute() else Path(shared.PROGRAM_PATH) / fpath
                        if Path(abs_path).exists():
                            found_files += 1
                if total_files:
                    output_parts.append(
                        f"  Model files: {found_files}/{total_files} on disk"
                    )
            else:
                output_parts.append("[2/4] No download_file_list declared")
        else:
            output_parts.append("[2/4] Module spec not available")

        # Step 3: instantiate the module
        try:
            # Try with current params from config
            from utils.config import pcfg

            params_map = {
                "textdetector": pcfg.module.textdetector_params,
                "ocr": pcfg.module.ocr_params,
                "translator": pcfg.module.translator_params,
                "inpainter": pcfg.module.inpainter_params,
            }
            mod_params = params_map.get(stage, {}).get(module_key, {})
            if isinstance(mod_params, dict):
                # Filter out meta keys
                filtered = {k: v for k, v in mod_params.items() if not k.startswith("description")}
                instance = cls(**filtered) if filtered else cls()
            else:
                instance = cls()
            output_parts.append(f"[3/4] Instance created: {type(instance).__name__}")
            # Check device param
            device = getattr(instance, "get_param_value", None)
            if device and callable(device):
                try:
                    dev_val = instance.get_param_value("device")
                    output_parts.append(f"  Device config: {dev_val}")
                except Exception:
                    pass
        except Exception as e:
            output_parts.append(f"[3/4] Instance creation failed (may be normal): {e}")

        # Step 4: stage-specific deeper check
        if stage == "translator" and module_key not in ("None", "none", ""):
            params = pcfg.module.translator_params.get(module_key, {})
            profile_name = ""
            if isinstance(params, dict):
                profile_name = params.get("profile", params.get("model_profile", ""))
            if profile_name:
                from utils.profile_manager import load_profiles

                profiles = load_profiles()
                profile = None
                for p in profiles:
                    if p.get("name") == profile_name:
                        profile = p
                        break

                if profile:
                    host = profile.get("host", "")
                    api_key = profile.get("api_key", "")
                    proxy = profile.get("proxy", "")
                    output_parts.append(f"[4/4] Profile: {profile_name}")
                    if host and api_key:
                        try:
                            import httpx

                            client_kwargs: Dict[str, Any] = {"timeout": 10}
                            if proxy:
                                client_kwargs["proxy"] = proxy
                            with httpx.Client(**client_kwargs) as client:
                                resp = client.get(
                                    f"{host.rstrip('/')}/models",
                                    headers={"Authorization": f"Bearer {api_key}"},
                                )
                                if resp.status_code == 200:
                                    output_parts.append(f"  API test: OK ({host})")
                                else:
                                    output_parts.append(
                                        f"  API test: HTTP {resp.status_code}"
                                    )
                                    output_parts.append(f"  Response: {resp.text[:200]}")
                        except Exception as e:
                            output_parts.append(f"  API test failed: {e}")
                    else:
                        output_parts.append("  API test skipped: host/key empty")
                else:
                    output_parts.append(
                        f"[4/4] Profile '{profile_name}' not found in saved profiles"
                    )
            else:
                output_parts.append("[4/4] No API profile configured")
        else:
            # Compute module: GPU availability
            from modules.base import DEFAULT_DEVICE

            output_parts.append(f"[4/4] Inference device: {DEFAULT_DEVICE}")

        elapsed = int((time.perf_counter() - start) * 1000)
        return {
            "success": True,
            "output": "\n".join(output_parts),
            "duration_ms": elapsed,
        }

    except Exception as e:
        elapsed = int((time.perf_counter() - start) * 1000)
        return {
            "success": False,
            "output": f"Test error: {e}",
            "duration_ms": elapsed,
        }


# ── Dependency summary ─────────────────────────────────────────────────────


def dependency_summary() -> Dict[str, Any]:
    """Return a quick summary of dependency health.

    Returns dict with keys:
        total, installed, missing, mismatched, skipped
    """
    total = installed = missing = mismatched = skipped = 0
    try:
        from ui.dependency_dialog import _check_req, _load_declared_deps

        raw = _load_declared_deps()
        for req_str, _dep_type in raw:
            total += 1
            status, _ver = _check_req(req_str)
            if status == "installed":
                installed += 1
            elif status == "missing":
                missing += 1
            elif status == "mismatch":
                mismatched += 1
            elif status == "skipped":
                skipped += 1
    except Exception:
        return {"total": 0, "installed": 0, "missing": 0, "mismatched": 0, "skipped": 0}

    return {
        "total": total,
        "installed": installed,
        "missing": missing,
        "mismatched": mismatched,
        "skipped": skipped,
    }
