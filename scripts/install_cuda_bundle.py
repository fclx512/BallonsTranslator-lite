"""Inspect or explicitly convert the bundled inference runtime to CUDA.

The default is read-only.  ``--install`` is the only mutation path.  It uses
the bundled interpreter, a known torch/torchvision pair, and an ORT build
matched to that pair's CUDA major version.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PYTHON = (ROOT / "ballontrans_pylibs_win" / "python.exe").resolve()

sys.path.insert(0, str(ROOT))
from utils.env_diagnostic import _query_compute_cap, pick_cuda_index  # noqa: E402

TORCH_PUBLIC = "2.13.0"
VISION_PUBLIC = "0.28.0"
MODEL = ROOT / "data" / "models" / "ppocrv6_onnx" / "medium" / "rec.onnx"


def require_bundled_python():
    if sys.platform != "win32" or Path(sys.executable).resolve() != EXPECTED_PYTHON:
        raise SystemExit("Run this script with the matching bundled Windows Python.")


def version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime_state(target: dict) -> dict:
    """Actual import, CUDA allocation and ORT session; provider listing alone is insufficient."""
    from utils.bundled_runtime import prepare_bundled_dlls
    prepare_bundled_dlls()
    state = {"torch": version("torch"), "vision": version("torchvision"),
             "ort_cpu": version("onnxruntime"), "ort_gpu": version("onnxruntime-gpu"),
             "torch_ok": False, "ort_ok": False, "details": []}
    cuda_major = target["cuda_major"]
    try:
        import torch
        import torchvision

        state["torch_import"] = torch.__version__
        state["vision_import"] = torchvision.__version__
        if state["torch"] != torch.__version__ or state["vision"] != torchvision.__version__:
            state["details"].append("torch/torchvision files and package metadata disagree")
        elif torch.__version__.split("+", 1)[-1] != torchvision.__version__.split("+", 1)[-1]:
            state["details"].append("torch and torchvision build tags disagree")
        elif torch.__version__.split("+", 1)[-1] != target["index_tag"]:
            state["details"].append(f"torch build does not match {target['index_tag']}")
        elif str(torch.version.cuda or "").split(".")[0] != cuda_major:
            state["details"].append(f"torch CUDA runtime does not match CUDA {cuda_major}")
        elif not torch.cuda.is_available():
            state["details"].append("torch.cuda.is_available() is false")
        else:
            torch.empty(1, device="cuda")
            torchvision.ops.nms(torch.tensor([[0., 0., 1., 1.]], device="cuda"),
                                torch.tensor([1.], device="cuda"), 0.5)
            state["torch_ok"] = True
    except Exception as exc:
        state["details"].append(f"PyTorch CUDA probe failed: {exc}")

    if not state["torch_ok"]:
        return state
    if state["ort_cpu"] is not None:
        state["details"].append("both ONNX Runtime distribution names must not coexist")
        return state
    if state["ort_gpu"] != target["ort_version"]:
        state["details"].append(f"ONNX Runtime GPU must be {target['ort_version']}")
        return state
    if not MODEL.is_file():
        state["details"].append(f"model missing for CUDA session probe: {MODEL}")
        return state
    try:
        import onnxruntime as ort
        import torch
        if ort.__version__ != state["ort_gpu"]:
            raise RuntimeError("ONNX Runtime import and package metadata disagree")

        dll_dir = Path(torch.__file__).parent / "lib"
        dll_handle = os.add_dll_directory(str(dll_dir))
        try:
            options = ort.SessionOptions()
            options.log_severity_level = 3
            session = ort.InferenceSession(str(MODEL), options,
                                           providers=["CUDAExecutionProvider"])
            state["ort_ok"] = "CUDAExecutionProvider" in session.get_providers()
        finally:
            dll_handle.close()
        if not state["ort_ok"]:
            state["details"].append("CUDA provider could not create a model session")
    except Exception as exc:
        state["details"].append(f"ONNX Runtime CUDA session failed: {exc}")
    return state


def probe_state_subprocess() -> dict:
    """Probe in a disposable process; loaded CUDA DLLs must not block replacement."""
    result = subprocess.run(
        [str(EXPECTED_PYTHON), "-I", str(Path(__file__).resolve()), "--json"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"CUDA probe produced no JSON: {result.stderr[-1000:]}")
    return json.loads(lines[-1])["state"]


def protected_constraints(excluded: set[str]) -> Path:
    """Pin everything except packages intentionally being replaced."""
    fd, raw = tempfile.mkstemp(prefix="bt-cuda-constraints-", suffix=".txt")
    path = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            from utils.runtime_compatibility import installed_inventory
            found = {name: value for name, value in installed_inventory().items() if name not in excluded}
            for name, installed in sorted(found.items()):
                stream.write(f"{name}=={installed}\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def run(command: list[str]) -> None:
    print("Executing:", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT)


def plan(target: dict, state: dict) -> tuple[bool, bool]:
    torch_version = state["torch"]
    vision_version = state["vision"]
    replace_torch = not state["torch_ok"]
    mixed = any(state.get(key + "_import") != state[key] for key in ("torch", "vision"))
    if mixed or (replace_torch and (torch_version != TORCH_PUBLIC + "+cpu" or
                                    vision_version != VISION_PUBLIC + "+cpu")):
        raise RuntimeError(
            "Unknown or mixed torch/torchvision build. Re-extract a matching complete "
            "package before converting it to CUDA; existing packages were not changed."
        )
    replace_ort = not state["ort_ok"]
    if state["ort_cpu"] and state["ort_gpu"]:
        raise RuntimeError("CPU/GPU ONNX Runtime distributions overlap; restore a clean complete package.")
    return replace_torch, replace_ort


def download_wheels(requirements, directory, index=None):
    directory.mkdir(parents=True, exist_ok=True)
    command = [str(EXPECTED_PYTHON), "-m", "pip", "download", "--no-deps",
               "--only-binary=:all:", "-d", str(directory)]
    if index:
        command += ["--index-url", index]
    run(command + requirements)
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != len(requirements):
        raise RuntimeError("Downloaded wheel count does not match the conversion plan")
    return [str(path) for path in wheels]


def convert(target, state, change_torch, change_ort, constraints):
    """Prepare target AND recovery wheels before mutation. Retain both on failure."""
    cache = Path(tempfile.mkdtemp(prefix="bt-cuda-conversion-"))
    targets, recovery, packages = [], [], []
    try:
        if change_torch:
            targets += download_wheels([f"torch=={TORCH_PUBLIC}+{target['index_tag']}",
                                        f"torchvision=={VISION_PUBLIC}+{target['index_tag']}"],
                                       cache / "target-torch", target["url"])
            recovery += download_wheels([f"torch=={state['torch']}", f"torchvision=={state['vision']}"],
                                        cache / "recovery-torch", "https://download.pytorch.org/whl/cpu")
            packages += ["torch", "torchvision"]
        if change_ort:
            targets += download_wheels([f"onnxruntime-gpu=={target['ort_version']}"], cache / "target-ort")
            old_name = "onnxruntime" if state["ort_cpu"] else "onnxruntime-gpu"
            old_version = state["ort_cpu"] or state["ort_gpu"]
            if old_version:
                recovery += download_wheels([f"{old_name}=={old_version}"], cache / "recovery-ort")
            packages += ["onnxruntime", "onnxruntime-gpu"]
    except Exception as exc:
        raise RuntimeError(f"Preparation failed before any installed package was changed: {exc}; cache: {cache}") from exc

    pip = [str(EXPECTED_PYTHON), "-m", "pip"]
    try:
        if change_ort:
            run(pip + ["uninstall", "-y", "onnxruntime", "onnxruntime-gpu"])
        run(pip + ["install", "--no-index", "--no-deps", "--force-reinstall", "-c", str(constraints), *targets])
        after = probe_state_subprocess()
        if not after["torch_ok"] or not after["ort_ok"]:
            raise RuntimeError("; ".join(after["details"]) or "CUDA verification failed")
    except Exception as exc:
        print(f"Conversion failed: {exc}. Restoring original package versions from local wheels.")
        try:
            run(pip + ["uninstall", "-y", *packages])
            if recovery:
                run(pip + ["install", "--no-index", "--no-deps", *recovery])
        except Exception as restore_exc:
            raise RuntimeError(f"Recovery also failed: {restore_exc}. Re-extract the complete package. Recovery wheels: {cache}") from exc
        raise RuntimeError(f"Original wheels restored; conversion was not accepted. Cache: {cache}") from exc
    print(f"Conversion/recovery wheels retained for maintenance: {cache}")


def main() -> int:
    require_bundled_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="Explicitly download and replace bundled CUDA packages")
    parser.add_argument("--json", action="store_true", help="Print read-only probe as JSON")
    args = parser.parse_args()
    if args.json and args.install:
        parser.error("--json is read-only")
    cc = _query_compute_cap()  # Strict hardware query; unknown GPUs are not guessed.
    target = pick_cuda_index(cc)
    if not target:
        message = "No supported GPU compute capability was reported by nvidia-smi; no changes made."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    target["cuda_major"] = "13" if target["index_tag"] in ("cu130", "cu132") else "12"
    target["ort_version"] = "1.27.0" if target["cuda_major"] == "13" else "1.20.1"
    if args.json:
        state = runtime_state(target)
        print(json.dumps({"compute_cap": cc, "target": target, "state": state},
                         ensure_ascii=False))
        return 0 if state["torch_ok"] and state["ort_ok"] else 1
    try:
        state = probe_state_subprocess()
    except Exception as exc:
        print(f"Could not probe the bundled runtime: {exc}")
        return 1
    print(f"GPU CC {cc}; torch index {target['index_tag']}; "
          f"ONNX Runtime GPU {target['ort_version']}")
    print(f"Installed: torch={state['torch']}, torchvision={state['vision']}, "
          f"onnxruntime={state['ort_cpu']}, onnxruntime-gpu={state['ort_gpu']}")
    for detail in state["details"]:
        print("  -", detail)
    try:
        change_torch, change_ort = plan(target, state)
    except RuntimeError as exc:
        print(exc)
        return 1
    if not change_torch and not change_ort:
        print("CUDA torch and ONNX Runtime session are ready. No changes needed.")
        return 0
    if change_torch:
        print(f"Plan: replace torch/torchvision with {TORCH_PUBLIC}/{VISION_PUBLIC} "
              f"from {target['index_tag']} using bundled Python {EXPECTED_PYTHON}")
    if change_ort:
        print(f"Plan: replace ONNX Runtime with onnxruntime-gpu=={target['ort_version']}")
    if not args.install:
        print("Read-only check complete. Run install_cuda.bat --install to apply this plan.")
        return 0

    if not MODEL.is_file():
        print(f"Missing model required for the CUDA session check: {MODEL}. No packages changed.")
        return 1

    excluded = {"torch", "torchvision", "onnxruntime", "onnxruntime-gpu"}
    try:
        constraints = protected_constraints(excluded)
    except Exception as exc:
        print(f"Cannot protect installed packages; refusing conversion: {exc}")
        return 1
    try:
        convert(target, state, change_torch, change_ort, constraints)
    except Exception as exc:
        print(f"CUDA installation failed ({exc}). Check the bundle and retry.")
        print(f"Constraints snapshot retained: {constraints}")
        return 1
    constraints.unlink(missing_ok=True)
    print("CUDA torch allocation and ONNX Runtime CUDA model session verified.")
    print("The bundled environment changed; rerun complete-package validation before distribution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
