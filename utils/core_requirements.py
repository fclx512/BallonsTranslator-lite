"""Lightweight core-dependency probe for startup robustness.

Checks whether essential Python packages are importable *before* the app
attempts to use them.  Unlike the upstream ``ballontranslator``, this
implementation **never auto-installs** or restarts — it only logs clear
guidance so the user can resolve the issue (or the one-click bundle already
has everything).

Exported interface
------------------
``warn_missing_core_imports()`` -> List[str]
    Probe the package list and return descriptions of any failures.

``CORE_IMPORT_PROBES``
    The probe list, importable for inspection / extension.
"""

import importlib
import sys
from typing import Iterable, List, Tuple

# (module_name, (required_attr, …))
#  — attr tuple is empty when merely importing the module suffices.
CORE_IMPORT_PROBES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("qtpy", ()),
    ("numpy", ()),
    ("PIL", ()),
    ("requests", ()),
    ("tqdm", ()),
)


def _platform_probes() -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    if sys.platform == "win32":
        return (("win32api", ()),)
    return ()


def check_core_imports(
    probes: Iterable[Tuple[str, Tuple[str, ...]]] = None,
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


def warn_missing_core_imports(
    probes: Iterable[Tuple[str, Tuple[str, ...]]] = None,
) -> List[str]:
    """Check core imports and print warnings if any are missing.

    Returns the list of failure descriptions (empty = everything is fine).
    Non-fatal — always returns without raising.
    """
    failures = check_core_imports(probes)
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
