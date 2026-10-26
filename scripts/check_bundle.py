"""Offline release-directory inspection. Does not install or download anything.

The manifest is an inventory, not evidence of end-to-end model inference.
"""

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils.runtime_compatibility import (  # noqa: E402
    CONTRACT,
    MANIFEST,
    RuntimeCompatibilityError,
    installed_inventory,
    read_contract,
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_imports(profile):
    """Called only in a disposable process; no GPU or model is initialized."""
    from utils.bundled_runtime import prepare_bundled_dlls
    prepare_bundled_dlls()
    from utils.core_requirements import check_core_imports
    errors = check_core_imports()
    bundle = (ROOT / "ballontrans_pylibs_win").resolve()
    loaded = {}
    for distribution, module_name in (("torch", "torch"), ("torchvision", "torchvision"),
                                      ("onnxruntime" if profile == "cpu" else "onnxruntime-gpu", "onnxruntime")):
        try:
            module = importlib.import_module(module_name)
            actual, recorded = str(module.__version__), metadata.version(distribution)
            loaded[distribution] = actual
            if actual != recorded:
                errors.append(f"{distribution}: metadata {recorded} != imported {actual}")
            if not Path(module.__file__).resolve().is_relative_to(bundle):
                errors.append(f"{distribution}: imported from outside the bundle")
            if module_name in {"torch", "torchvision"} and actual.split("+", 1)[-1] != profile:
                errors.append(f"{distribution}: {actual} does not match profile {profile}")
        except Exception as exc:
            errors.append(f"{distribution}: {exc}")
    # Core imports must also be supplied by the bundle, including native wheels.
    for name in ("numpy", "cv2", "PyQt6.QtCore", "PIL.Image"):
        module = sys.modules.get(name)
        if module and not Path(module.__file__).resolve().is_relative_to(bundle):
            errors.append(f"{name}: imported from outside the bundle")
    return {"errors": errors, "imported_versions": loaded}


def collect_models(contract, include_vl):
    from utils.core_requirements import check_declared_core_requirements
    from utils.lazy_registry import _scan_file, init_lazy_module_registries
    from utils.model_files import declared_save_files
    from utils.registries import MODULETYPE_TO_REGISTRIES
    init_lazy_module_registries()
    # LaMa is declared in base.py rather than an inpaint_*.py plugin.
    base_specs = {spec.key: spec for spec in _scan_file(str(ROOT / "modules/inpaint/base.py"), "inpainter")}
    inventory = installed_inventory()
    selected = list(contract["required_models"])
    if include_vl:
        selected.append("ocr:paddleocr_vl_manga")
    errors, files = [], {}
    for entry in selected:
        kind, key = entry.split(":", 1)
        spec = MODULETYPE_TO_REGISTRIES[kind].get_spec(key)
        if spec is None and kind == "inpainter":
            spec = base_specs.get(key)
        if spec is None:
            errors.append(f"Unknown required model: {entry}")
            continue
        requirements = []
        for requirement in spec.requires_packages or []:
            # The app accepts the GPU distribution for the same ORT import.
            if requirement.startswith("onnxruntime") and "onnxruntime-gpu" in inventory:
                requirement = requirement.replace("onnxruntime", "onnxruntime-gpu", 1) if not requirement.startswith("onnxruntime-gpu") else requirement
            requirements.append(requirement)
        errors.extend(check_declared_core_requirements(requirements_text="\n".join(requirements)))
        paths = declared_save_files(spec.download_file_list)
        if not paths:
            errors.append(f"No declared model files: {entry}")
        for rel in paths:
            path = (ROOT / rel).resolve()
            if not any(path.is_relative_to(ROOT / "data" / folder) for folder in ("models", "libs")) or not path.is_file():
                errors.append(f"Missing or invalid model file: {rel}")
            elif rel not in files:
                files[rel] = {"size": path.stat().st_size, "sha256": digest(path)}
    return errors, files


def inspect_bundle(profile, include_vl=False, *, verify_manifest=True):
    from utils.core_requirements import check_declared_core_requirements
    from utils.runtime_compatibility import source_update_preflight
    contract = read_contract((ROOT / CONTRACT).read_text(encoding="utf-8"))
    errors = []
    if profile not in contract["profiles"]:
        errors.append(f"Unsupported profile: {profile}")
    if profile == "cpu" and include_vl:
        errors.append("PaddleOCR-VL requires a GPU profile")
    if platform.python_version_tuple()[:2] != tuple(contract["python_series"].split(".")) or platform.architecture()[0] != "64bit":
        errors.append("Python ABI does not match the source contract")
    # Check declaration parity without accepting an old manifest as proof.
    try:
        source_update_preflight(ROOT, lambda name: (ROOT / name).read_text(encoding="utf-8"))
    except RuntimeCompatibilityError as exc:
        errors.append(str(exc))
    errors.extend(check_declared_core_requirements())
    inventory = installed_inventory()
    bundle_root = (ROOT / "ballontrans_pylibs_win").resolve()
    for dist in metadata.distributions():
        if not Path(dist.locate_file("")).resolve().is_relative_to(bundle_root):
            errors.append(f"Distribution is outside bundled Python: {dist.metadata.get('Name')}")
    if "onnxruntime" in inventory and "onnxruntime-gpu" in inventory:
        errors.append("CPU and GPU ONNX Runtime distributions overlap")
    if profile == "cpu" and "onnxruntime-gpu" in inventory:
        errors.append("CPU profile contains GPU ONNX Runtime")
    if profile != "cpu":
        expected = "1.20.1" if profile == "cu126" else "1.27.0"
        if inventory.get("onnxruntime-gpu") != expected:
            errors.append(f"{profile} requires onnxruntime-gpu=={expected}")
    probe = subprocess.run([sys.executable, "-I", __file__, "--profile", profile, "--probe-imports"],
                           capture_output=True, text=True, timeout=120, cwd=ROOT)
    try:
        result = json.loads(probe.stdout.strip().splitlines()[-1])
        errors.extend(result["errors"])
    except (ValueError, IndexError, KeyError):
        errors.append(f"Import probe failed: {probe.stderr[-1000:]}")
    model_errors, files = collect_models(contract, include_vl)
    errors.extend(model_errors)
    manifest = {"schema": 1, "family": contract["family"], "profile": profile,
                "python": platform.python_version(), "distributions": inventory,
                "include_vl": include_vl, "model_files": files,
                "requirements_sha256": digest(ROOT / "requirements.txt")}
    manifest_path = ROOT / MANIFEST
    if verify_manifest and manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        for field in ("profile", "python", "distributions", "include_vl", "model_files"):
            if previous.get(field) != manifest[field]:
                errors.append(f"Installed bundle differs from its release inventory: {field}")
    return errors, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("cpu", "cu126", "cu130", "cu132"))
    parser.add_argument("--include-vl", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--bundle-id")
    parser.add_argument("--probe-imports", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32" or Path(sys.executable).resolve() != (ROOT / "ballontrans_pylibs_win/python.exe").resolve():
        parser.error("Use the complete package's bundled Windows Python")
    os.chdir(ROOT)
    if args.probe_imports:
        print(json.dumps(inspect_imports(args.profile)))
        return 0
    if args.write_manifest and not args.bundle_id:
        parser.error("--write-manifest requires --bundle-id")
    try:
        errors, manifest = inspect_bundle(args.profile, args.include_vl, verify_manifest=not args.write_manifest)
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print("No manifest written; obtain or prepare a matching complete package.")
        return 1
    if args.write_manifest:
        manifest["bundle_id"] = args.bundle_id
        path = ROOT / MANIFEST
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        print(f"Inventory written: {path}")
    print(f"PASS: {len(manifest['distributions'])} distributions; {len(manifest['model_files'])} model files.")
    print("Static/import checks only. Application startup and model inference still require release acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
