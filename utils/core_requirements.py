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

    result = _install_packages(
        requirements_file=str(req_path),
        backend=backend,
        env=env or os.environ.copy(),
        progress_callback=progress_callback,
    )

    if not result.ok:
        print()
        print("!" * 50)
        print("Failed to install core Python requirements.")
        print(f"  Command: {result.command_text}")
        print(f"  Exit code: {result.returncode}")
        if result.stderr:
            print(f"  Error: {result.stderr[:1000]}")
        print()
        print("Please run manually:")
        print(f"  pip install -r {req_path}")
        print("!" * 50)
        print()
        return False

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
