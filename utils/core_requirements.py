"""Core-dependency probe for startup robustness.

Two entry points:

``ensure_core_requirements()``
    Auto-installs missing core packages and returns ``True`` if a restart is
    needed.  Called early in ``launch.py``, **before** Qt / config init.

``warn_missing_core_imports()``
    Lightweight secondary check that only prints warnings.  Called after
    ``prepare_environment()`` as a safety net.
"""

import importlib
import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from utils.package_installer import install as _install_packages

# (module_name, (required_attr, …))
#  — attr tuple is empty when merely importing the module suffices.
CORE_IMPORT_PROBES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("packaging", ()),
    ("qtpy", ()),
    ("qtpy.QtCore", ("Qt",)),
    ("numpy", ()),
    ("PIL", ()),
    ("pillow_jxl", ()),
    ("requests", ()),
    ("tqdm", ()),
    ("termcolor", ()),
    ("colorama", ()),
    ("natsort", ()),
    ("cv2", ("IMREAD_COLOR", "IMREAD_GRAYSCALE", "cvtColor")),
)


def _platform_probes() -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    if sys.platform == "win32":
        return (("win32api", ()),)
    return ()


def check_core_imports(
    probes: Optional[Iterable[Tuple[str, Tuple[str, ...]]]] = None,
) -> List[str]:
    """Return human-readable descriptions of packages that cannot be imported.

    Example:
        >>> check_core_imports([("math", ("sqrt",))])
        []
        >>> len(check_core_imports([("math", ("nonexistent_attr",))])) > 0
        True
    """
    failures: List[str] = []
    probes = tuple(probes or CORE_IMPORT_PROBES) + _platform_probes()
    for module_name, attrs in probes:
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            failures.append(f"  {module_name}: {e}")
            continue
        missing = [a for a in attrs if not hasattr(module, a)]
        if missing:
            failures.append(
                f"  {module_name}: missing required attribute(s): {', '.join(missing)}"
            )
    return failures


def _drop_probe_modules(
    probes: Iterable[Tuple[str, Tuple[str, ...]]],
):
    """Remove failed probe modules from sys.modules so a future re-import
    actually re-executes the real code after installation."""
    for module_name, _ in probes:
        root = module_name.split(".", 1)[0]
        for loaded_name in list(sys.modules):
            if loaded_name == root or loaded_name.startswith(root + "."):
                sys.modules.pop(loaded_name, None)
    importlib.invalidate_caches()


def _write_constraints_snapshot() -> Tuple[str, int]:
    """Snapshot every installed distribution as a pip/uv constraints file.

    Returns ``(path, count)``.  The file lists ``name==version`` for each
    installed dist, so an install run with it as ``-c`` is additive-only:
    missing packages are added, but nothing already present is upgraded —
    a requirement that would force an upgrade fails resolution instead.

    This is what makes pointing the app at an environment shared with
    another project (e.g. upstream BallonsTranslator's bundled env) safe:
    the supplement can add our extra packages without ever touching the
    versions the other project is running.
    """
    import tempfile

    versions = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name and dist.version:
            versions.setdefault(name, dist.version)

    fd, path = tempfile.mkstemp(prefix="bt_lite_constraints_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "# Auto-generated snapshot of the currently installed distributions.\n"
            "# Additive-only install guard: nothing listed here may be upgraded.\n"
        )
        for name in sorted(versions, key=str.lower):
            f.write(f"{name}=={versions[name]}\n")
    return path, len(versions)


def ensure_core_requirements(
    repo_root: str = "",
    requirements_file: str = "",
    backend: str = "auto",
    env: Optional[dict] = None,
    force: bool = False,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Check core imports and auto-install any missing packages.

    Returns ``True`` if packages were installed (caller should restart
    the process so the new packages are fully loaded).  Returns ``False``
    if everything is already satisfied.

    When installation fails, prints an error and returns ``False`` instead
    of raising — the app continues with warnings so the user can manually
    ``pip install -r requirements.txt``.
    """
    if getattr(sys, "frozen", False):
        return False

    repo_path = Path(repo_root or Path(__file__).resolve().parent.parent)
    req_path = (
        Path(requirements_file)
        if requirements_file
        else repo_path / "requirements.txt"
    )

    probes = tuple(CORE_IMPORT_PROBES) + _platform_probes()
    failures = check_core_imports(probes)
    if not force and not failures:
        return False

    print("―" * 50)
    print("Some core Python packages could not be imported.")
    print("The application may not work correctly until they are installed.")
    if failures:
        print()
        print("Missing packages:")
        for f in failures:
            print(f)
    print()
    print(f"Installing core requirements from {req_path}...")
    print("―" * 50)

    # Additive-only guard: pin every installed dist so the install can only
    # ADD packages, never upgrade existing ones (safe for envs shared with
    # other projects).  On a resolution conflict we keep the snapshot file
    # and let the user decide rather than silently bumping shared versions.
    constraints_path = ""
    try:
        constraints_path, pinned = _write_constraints_snapshot()
        print(
            f"Additive-only guard: {pinned} installed distributions pinned "
            "via constraints — existing packages will NOT be upgraded."
        )
    except Exception as e:
        print(f"Warning: could not write constraints snapshot ({e}); "
              "installing without the no-upgrade guard.")

    result = _install_packages(
        requirements_file=str(req_path),
        backend=backend,
        env=env or os.environ.copy(),
        progress_callback=progress_callback,
        constraints_file=constraints_path,
    )

    if not result.ok:
        print()
        print("!" * 50)
        print("Failed to install core Python requirements.")
        print(f"  Command: {result.command_text}")
        print(f"  Exit code: {result.returncode}")
        if result.stderr:
            print(f"  Error: {result.stderr[:1000]}")
        if constraints_path:
            print()
            print(f"Constraints snapshot kept for inspection: {constraints_path}")
            print("A conflict here means a missing package needs a version that")
            print("would upgrade something already installed — resolve manually")
            print("or delete the snapshot file to retry unrestricted.")
        print()
        print("Please run manually:")
        print(f"  pip install -r {req_path}")
        print("!" * 50)
        print()
        return False

    if constraints_path:
        try:
            os.remove(constraints_path)
        except OSError:
            pass

    _drop_probe_modules(probes)
    print()
    print("Core Python requirements installed successfully.")
    print("Restarting to load new packages...")
    return True


def warn_missing_core_imports(
    probes: Optional[Iterable[Tuple[str, Iterable[str]]]] = None,
) -> List[str]:
    """Check core imports and print warnings if any are missing.

    Non-fatal — always returns without raising.  This is a secondary check
    that runs after ``prepare_environment()``; it only warns, never installs.

    Returns the list of failure descriptions (empty = everything is fine).
    """
    failures = check_core_imports(probes)

    # Deep probe PIL.Image (a submodule, not loaded by import PIL alone)
    try:
        import PIL.Image  # noqa: F401
    except Exception as e:
        failures.append(f"  PIL.Image: {e}")

    if failures:
        print("―" * 50)
        print("Some core Python packages could not be imported.")
        print("The application may not work correctly until they are installed.")
        print()
        print("Missing packages:")
        for f in failures:
            print(f)
        print()
        if "ballontrans_pylibs_win" in sys.executable:
            print("You are running the bundled portable Python.")
            print("If packages are missing, the bundle may be incomplete.")
            print("Try re-downloading the full one-click package from the releases page.")
        else:
            print("Install missing packages with:")
            print("  pip install -r requirements.txt")
        print("―" * 50)
        print()

    return failures
