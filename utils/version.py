"""Read the application version from pyproject.toml (single source of truth).

Falls back gracefully when the file or ``tomllib`` is unavailable.
"""

from pathlib import Path


def _read_version_from_toml(path: Path) -> str | None:
    try:
        import tomllib

        with path.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version")
    except Exception:
        return None


def _read_version_via_importlib() -> str | None:
    try:
        from importlib.metadata import version

        return version("BallonsTranslator-lite")
    except Exception:
        return None


def get_version(program_path: str | None = None) -> str:
    """Return the application version string.

    Priority:
    1. ``pyproject.toml`` ``[project] version`` field
    2. ``importlib.metadata.version("BallonsTranslator-lite")``
    3. ``"0.0.0"`` as final fallback
    """
    if program_path:
        toml = Path(program_path) / "pyproject.toml"
    else:
        # Walk up from this file's location to find the project root.
        toml = Path(__file__).resolve().parent.parent / "pyproject.toml"

    if toml.exists():
        v = _read_version_from_toml(toml)
        if v:
            return v

    v = _read_version_via_importlib()
    if v:
        return v

    return "0.0.0"


APP_VERSION = get_version()
