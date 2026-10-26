"""Offline source/runtime compatibility. Never install packages or execute incoming code."""

import importlib.metadata as metadata
import json
import platform
import re
import sys
import zipfile
from pathlib import Path

CONTRACT = "runtime_compatibility.json"
MANIFEST = "ballontrans_pylibs_win/runtime-manifest.json"


class RuntimeCompatibilityError(RuntimeError):
    """The source requires a different complete runtime package."""


def read_contract(text):
    try:
        value = json.loads(text)
        if (not isinstance(value, dict) or value.get("schema") != 1
                or not isinstance(value.get("family"), str) or not value["family"]
                or not re.fullmatch(r"\d+\.\d+", value.get("python_series", ""))
                or not isinstance(value.get("profiles"), list) or not value["profiles"]
                or any(p not in {"cpu", "cu126", "cu130", "cu132"} for p in value["profiles"])):
            raise ValueError("unsupported or malformed contract")
        return value
    except (ValueError, TypeError) as exc:
        raise RuntimeCompatibilityError(f"Invalid {CONTRACT}: {exc}") from exc


def check_runtime(root, contract=None):
    """Validate identity/Python and return warnings for legacy, unidentified bundles."""
    root = Path(root)
    contract = contract or read_contract((root / CONTRACT).read_text(encoding="utf-8"))
    series = f"{sys.version_info.major}.{sys.version_info.minor}"
    if series != contract["python_series"] or platform.architecture()[0] != "64bit":
        raise RuntimeCompatibilityError(
            f"Source requires {contract['family']} (64-bit Python {contract['python_series']}). "
            "Extract the matching complete package; no packages were changed."
        )
    path = root / MANIFEST
    if not path.exists():
        return ["Legacy runtime has no runtime-manifest.json; compatibility is checked "
                "against installed requirements, but this is not a verified release bundle."]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (manifest.get("schema") != 1 or not manifest.get("bundle_id")
                or manifest.get("family") != contract["family"]
                or manifest.get("profile") not in contract["profiles"]
                or not str(manifest.get("python", "")).startswith(series + ".")):
            raise ValueError("bundle family, profile or Python does not match this source")
    except (ValueError, AttributeError, OSError) as exc:
        raise RuntimeCompatibilityError(f"Invalid or incompatible runtime manifest: {exc}") from exc
    return []


def source_update_preflight(root, read_incoming):
    """Check effective incoming files. Reader returns text or None; callers handle delta merging."""
    contract_text = read_incoming(CONTRACT)
    if contract_text is None:
        if (Path(root) / CONTRACT).exists():
            raise RuntimeCompatibilityError("Update has no runtime contract; use its matching complete package.")
        return
    contract = read_contract(contract_text)
    if sys.platform == "win32":
        check_runtime(root, contract)
    local_path = Path(root) / CONTRACT
    if sys.platform == "win32" and not (Path(root) / MANIFEST).exists() and local_path.exists():
        local = read_contract(local_path.read_text(encoding="utf-8"))
        if local["family"] != contract["family"]:
            raise RuntimeCompatibilityError("New source requires a different complete runtime package.")
    requirements = read_incoming("requirements.txt")
    project = read_incoming("pyproject.toml")
    if requirements is None or project is None:
        raise RuntimeCompatibilityError("Update is missing its dependency declarations.")
    try:
        import tomllib
        from packaging.requirements import Requirement

        declared = {str(Requirement(line.strip())) for line in requirements.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")}
        project_deps = {str(Requirement(req)) for req in tomllib.loads(project)["project"]["dependencies"]}
        if declared != project_deps:
            raise ValueError("requirements.txt and pyproject.toml disagree")
        from utils.core_requirements import check_declared_core_requirements

        failures = check_declared_core_requirements(requirements_text=requirements)
        if failures:
            raise ValueError("; ".join(failures))
    except (ImportError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeCompatibilityError(
            f"Source update is incompatible with the bundled environment: {exc}. "
            "Get the matching complete package before updating."
        ) from exc


def preflight_directory(root, incoming, *, delta=False, deleted=()):
    root, incoming = Path(root), Path(incoming)
    if set(deleted) & {CONTRACT, "requirements.txt", "pyproject.toml"}:
        raise RuntimeCompatibilityError("Update removes a runtime compatibility declaration.")

    def read(name):
        path = incoming / name
        if not path.exists() and delta:
            path = root / name
        return path.read_text(encoding="utf-8") if path.is_file() else None

    source_update_preflight(root, read)


def preflight_zip(root, zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        candidates = [n for n in names if n == CONTRACT or n.endswith("/" + CONTRACT)]
        if not candidates:
            return source_update_preflight(root, lambda name: None)
        if len(candidates) != 1:
            raise RuntimeCompatibilityError("Ambiguous source runtime contract in update archive.")
        prefix = candidates[0][:-len(CONTRACT)]

        def read(name):
            key = prefix + name
            return archive.read(key).decode("utf-8") if key in names else None

        source_update_preflight(root, read)


def installed_inventory():
    """Do not silently flatten conflicting dist-info records into a fake lock file."""
    versions = {}
    for dist in metadata.distributions():
        name = re.sub(r"[-_.]+", "-", (dist.metadata.get("Name") or "").lower())
        if not name:
            continue
        if name in versions:
            raise RuntimeCompatibilityError(f"Duplicate distribution metadata: {name}")
        versions[name] = dist.version
    return dict(sorted(versions.items()))
