#!/usr/bin/env python3
"""Build a portable BallonsTranslator-lite package.

Produces a self-contained directory with embedded Python + pre-installed core
dependencies.  Extract-and-run, no system Python required.

Usage:
    python scripts/build_portable.py                         # default: Python 3.12
    python scripts/build_portable.py --python-ver 3.12.4     # specific version
    python scripts/build_portable.py --no-7z                 # skip compression
    python scripts/build_portable.py --keep-temp             # keep temp dir for debugging

Requires (on host, outside the embedded Python):
    - Python 3.10+  (to drive the build)
    - 7z CLI        (for compression; skip with --no-7z)

Output (when compressed):
    release/BallonsTranslator-lite-portable-<version>.7z

Output (uncompressed):
    release/BallonsTranslator-lite-portable-<version>/
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BASE_URL = "https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Default Python version for the portable package.
# Must match `requires-python` in pyproject.toml (>=3.10).
# 3.12 is chosen for broad package compatibility.
DEFAULT_PYTHON_VER = "3.12.4"

# Optional groups (gpu, onnx) are NOT included — they ship in model packs.


# ── Utilities ────────────────────────────────────────────────────────────


def _info(msg: str):
    print(f"[INFO] {msg}")


def _step(num: int, total: int, msg: str):
    print(f"\n[{num}/{total}] {msg}")
    print("=" * 60)


def _download(url: str, dest: Path, desc: str = "") -> None:
    """Download *url* to *dest* with a minimal progress indicator."""
    if dest.exists():
        _info(f"{desc or url} already cached at {dest}")
        return
    _info(f"Downloading {desc or url}...")
    urllib.request.urlretrieve(url, dest)
    size_mb = dest.stat().st_size / 1024 / 1024
    _info(f"  → {size_mb:.1f} MB downloaded")


def _run(cmd: list, cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run *cmd* and return the result; raises on non-zero exit."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        # Show stdout too — pip errors often have useful info there
        print(f"  stdout: {result.stdout[:800]}", file=sys.stderr)
        print(f"  stderr: {result.stderr[:800]}", file=sys.stderr)
        result.check_returncode()
    return result


def _find_7z() -> str | None:
    """Locate 7z CLI on the system."""
    for candidate in ["7z", "7za", "C:/Program Files/7-Zip/7z.exe"]:
        try:
            subprocess.run([candidate], capture_output=True, timeout=5)
            return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _generate_requirements_core(project_root: Path) -> Path:
    """Generate ``config/requirements_core.txt`` from ``pyproject.toml``.

    Returns the path to the generated file.
    Dependency lines retain their original PEP 508 markers — pip evaluates
    them at install time against the target Python environment.
    """
    deps = _parse_core_deps(project_root)
    req_path = project_root / "config" / "requirements_core.txt"
    lines = [
        "# BallonsTranslator-lite core dependencies",
        "# Auto-generated from pyproject.toml -- do not edit manually",
        "# Supports PIP_INDEX_URL for mirror installation",
        "",
    ]
    for d in deps:
        lines.append(d)
    lines.append("")
    req_path.write_text("\n".join(lines), encoding="utf-8")
    return req_path


# ── Core logic ───────────────────────────────────────────────────────────


def _parse_core_deps(project_root: Path) -> list[str]:
    """Parse ``[project.dependencies]`` from ``pyproject.toml``.

    Returns a flat list of PEP 508 requirement strings.
    Only ``[project.dependencies]`` is included (core only);
    ``[project.optional-dependencies]`` groups (gpu, onnx) are excluded.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")

    raw = pyproject_path.read_text(encoding="utf-8")
    deps: list[str] = []

    # TOML format:
    #   [project]
    #   dependencies = [
    #     "pkg1",
    #     "pkg2>=1.0",
    #   ]
    #
    # We scan for ``dependencies = [`` marker inside the ``[project]`` section,
    # then collect every quoted string until the closing ``]``.
    in_project = False
    in_deps_block = False
    for line in raw.splitlines():
        stripped = line.strip()

        if stripped == "[project]":
            in_project = True
            continue

        # Another section header → exit project scope
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_project and in_deps_block:
                break  # deps block ended by next section (shouldn't happen, but safety)
            if stripped.startswith(("[project.", "[build-system]")):
                # Stay within project for sub-tables like [project.scripts]
                continue
            in_project = False
            in_deps_block = False
            continue

        if not in_project:
            continue

        # Detect start: ``dependencies = [``
        if re.match(r'^dependencies\s*=\s*\[', stripped):
            in_deps_block = True
            continue

        if in_deps_block:
            if stripped == "]":
                break
            # Extract quoted strings from TOML array entries
            for m in re.finditer(r'"([^"]+)"', stripped):
                req = m.group(1)
                # Skip inline comments (TOML doesn't support them in arrays,
                # but be safe)
                if req and not req.startswith("#"):
                    deps.append(req)

    if not deps:
        # Fallback: minimal deps for UI launch
        _info("Could not parse pyproject.toml deps; using hardcoded fallback")
        deps = [
            "PyQt6>=6.8.1",
            "PyQt6-Qt6>=6.8.1",
            "numpy",
            "opencv-python>=4.10.0.84",
            "packaging>=23.0",
            "pillow>=10.0,<11",
            "qtpy",
            "requests",
            "httpx[socks,brotli]",
            "tqdm",
        ]

    return deps


def _get_embedded_python_ver(python_ver: str) -> str:
    """Normalize version string: ``3.12`` → ``3.12.4`` (latest micro)."""
    parts = python_ver.split(".")
    if len(parts) == 2:
        # For now, construct the URL. Python.org reliably has the last micro of each minor.
        # Use the default's micro version.
        _info(f"Minor version '{python_ver}' specified; using {DEFAULT_PYTHON_VER}")
        return DEFAULT_PYTHON_VER
    return python_ver


def build_portable(
    python_ver: str = DEFAULT_PYTHON_VER,
    output_dir: Path | None = None,
    skip_7z: bool = False,
    keep_temp: bool = False,
):
    """Main build routine.

    Returns the path to the built portable package (directory or .7z).
    """
    python_ver = _get_embedded_python_ver(python_ver)
    short_ver = ".".join(python_ver.split(".")[:2])  # e.g. "3.12"
    package_ver = _get_app_version()
    build_label = f"{package_ver}_py{short_ver}"

    output_dir = Path(output_dir or PROJECT_ROOT / "release")
    output_dir.mkdir(parents=True, exist_ok=True)

    # We build in a temp directory, then optionally 7z and/or copy to output.
    build_root = Path(tempfile.mkdtemp(prefix="btl_portable_"))
    try:
        python_dir = build_root / "python_embeded"

        total_steps = 8
        step = 0

        # ── Step 1: Generate requirements_core.txt ──
        step += 1
        _step(step, total_steps, "Generate requirements_core.txt")
        _generate_requirements_core(PROJECT_ROOT)
        _info("requirements_core.txt updated")

        # ── Step 2: Download embedded Python ──
        step += 1
        _step(step, total_steps, f"Download embedded Python {python_ver}")
        cache_dir = PROJECT_ROOT / ".build_cache"
        cache_dir.mkdir(exist_ok=True)
        embed_url = PYTHON_BASE_URL.format(ver=python_ver)
        zip_path = cache_dir / f"python-{python_ver}-embed-amd64.zip"
        _download(embed_url, zip_path, desc=f"Python {python_ver} embeddable")

        # ── Step 3: Extract embedded Python ──
        step += 1
        _step(step, total_steps, "Extract embedded Python")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(python_dir)
        _info(f"Extracted to {python_dir}")

        # ── Step 4: Configure ._pth to enable site-packages ──
        step += 1
        _step(step, total_steps, "Configure ._pth for site-packages")
        _enable_site_packages(python_dir, short_ver)

        # ── Step 5: Install pip into embedded Python ──
        step += 1
        _step(step, total_steps, "Install pip via get-pip.py")
        pip_script = cache_dir / "get-pip.py"
        _download(GET_PIP_URL, pip_script, desc="get-pip.py")
        _run([str(python_dir / "python.exe"), str(pip_script), "--no-warn-script-location"],
             cwd=build_root)
        _info("pip installed")

        # ── Step 6: Copy application code ──
        step += 1
        _step(step, total_steps, "Copy application code")
        _copy_app_code(python_dir, PROJECT_ROOT)

        # ── Step 7: Create run.bat ──
        step += 1
        _step(step, total_steps, "Create run script")
        run_bat = build_root / "run.bat"
        run_bat.write_text(_RUN_BAT_TEMPLATE.format(build_label=build_label), encoding="utf-8")
        _info("Created run.bat")

        # ── Step 8: 7z compress (optional) ──
        step += 1
        _step(step, total_steps, "Prepare output")

        if skip_7z:
            # Just copy the directory
            final_dir = output_dir / f"BallonsTranslator-lite-portable-{build_label}"
            if final_dir.exists():
                shutil.rmtree(final_dir)
            shutil.copytree(build_root, final_dir)
            result_path = final_dir
            _info(f"Portable directory: {result_path}")
        else:
            seven_zip = _find_7z()
            if seven_zip:
                archive_name = f"BallonsTranslator-lite-portable-{build_label}.7z"
                archive_path = output_dir / archive_name
                _info(f"Compressing with {seven_zip}...")
                _run(
                    [seven_zip, "a", "-mx=7", "-mmt=on", str(archive_path), "."],
                    cwd=build_root,
                    timeout=600,
                )
                size_mb = archive_path.stat().st_size / 1024 / 1024
                _info(f"Compressed package: {archive_path} ({size_mb:.1f} MB)")
                result_path = archive_path
            else:
                _info("7z not found; skipping compression, keeping directory.")
                final_dir = output_dir / f"BallonsTranslator-lite-portable-{build_label}"
                if final_dir.exists():
                    shutil.rmtree(final_dir)
                shutil.copytree(build_root, final_dir)
                result_path = final_dir
                _info(f"Portable directory: {result_path}")

        _info("Build complete!")
        return result_path

    finally:
        if not keep_temp:
            shutil.rmtree(build_root, ignore_errors=True)
            _info(f"Cleaned up temp directory: {build_root}")
        else:
            _info(f"Temp directory kept: {build_root}")


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_app_version() -> str:
    """Read version from pyproject.toml or git tag."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if pyproject.exists():
        try:
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                m = re.match(r'^version\s*=\s*["\']([^"\']+)["\']', line.strip())
                if m:
                    return m.group(1)
        except Exception:
            pass

    # Fallback: git describe
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty=-dirty"],
            capture_output=True, text=True, timeout=5,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return "0.0.0"


def _enable_site_packages(python_dir: Path, short_ver: str):
    """Uncomment ``import site`` in ``python*._pth`` so pip-installed
    packages in ``site-packages/`` are found on ``sys.path``.

    Embedded Python uses ``python312._pth`` (no dots in version number)
    inside a flat zip.  We search by glob to be version-agnostic.
    """
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        pth_files = list(python_dir.glob("*.pth"))
    if not pth_files:
        _info("No ._pth file found; creating one")
        no_dot_ver = short_ver.replace(".", "")
        pth_path = python_dir / f"python{no_dot_ver}._pth"
        pth_path.write_text(
            f"python{no_dot_ver}.zip\n.\nimport site\n",
            encoding="utf-8",
        )
        return

    pth_path = pth_files[0]
    content = pth_path.read_text(encoding="utf-8")
    if "import site" in content and "#import site" not in content:
        _info(f"site-packages already enabled in {pth_path.name}")
        return

    # Uncomment the import site line
    new_content = content.replace("#import site", "import site")
    # In case there's a space: "# import site"
    new_content = new_content.replace("# import site", "import site")
    pth_path.write_text(new_content, encoding="utf-8")
    _info(f"Enabled site-packages in {pth_path.name}")


def _copy_app_code(python_dir: Path, project_root: Path):
    """Copy application source into the portable package.

    Layout in the portable package::

        python_embeded/
            python.exe
            ...
        modules/
        ui/
        utils/
        config/
        translate/
        fonts/
        launch.py
        ...
    """
    portable_root = python_dir.parent  # build_root

    # Directories to copy (relative to project root)
    dirs_to_copy = [
        "modules",
        "ui",
        "utils",
        "config",
        "translate",
        "fonts",
    ]

    for dir_name in dirs_to_copy:
        src = project_root / dir_name
        dst = portable_root / dir_name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            _info(f"  Copied {dir_name}/")

    # Files to copy (relative to project root)
    files_to_copy = [
        "launch.py",
        "pyproject.toml",
    ]

    for fname in files_to_copy:
        src = project_root / fname
        dst = portable_root / fname
        if src.is_file():
            shutil.copy2(src, dst)
            _info(f"  Copied {fname}")

    # Copy manifest.json if present
    manifest_src = project_root / "manifest.json"
    if manifest_src.exists():
        shutil.copy2(manifest_src, portable_root / "manifest.json")
        _info("  Copied manifest.json")

    _info("Application code copied")


# ── Templates ────────────────────────────────────────────────────────────


_RUN_BAT_TEMPLATE = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: ============================================
::  BallonsTranslator-lite Portable Launcher
::  Build: {build_label}
:: ============================================

set "PYTHON=%~dp0python_embeded\python.exe"

:: Quick sanity check: verify embedded Python works
"%PYTHON%" -c "" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Embedded Python not found or broken.
    pause
    exit /b 1
)

:: ── --pypi-mirror subcommand: save mirror and exit ──
if /i "%~1"=="--pypi-mirror" (
    if "%~2"=="" (
        echo Usage: run.bat --pypi-mirror ^<URL^>
        echo Example: run.bat --pypi-mirror https://mirrors.aliyun.com/pypi/simple/
        pause
        exit /b 1
    )
    echo %~2 > "%~dp0config\pypi_mirror.txt"
    echo [OK] PyPI mirror saved: %~2
    echo.
    echo Run run.bat again to apply.
    pause
    exit /b 0
)

:: ── Check and install core dependencies ──

:: 1. Try a key import to see if deps are already installed
"%PYTHON%" -c "import qtpy" >nul 2>nul
if %ERRORLEVEL% NEQ 0 goto :install_deps
goto :launch_app

:install_deps
echo.
echo ============================================
echo  First launch — installing dependencies...
echo ============================================
echo.
echo  This installs %~dp0config\requirements_core.txt
echo  If you're in China, pre-configure a mirror:
echo    run.bat --pypi-mirror https://mirrors.aliyun.com/pypi/simple/
echo.

:: Check for persistent mirror config
set "PIP_INDEX_URL="
if exist "%~dp0config\pypi_mirror.txt" (
    set /p PIP_INDEX_URL=<"%~dp0config\pypi_mirror.txt"
)

:: Also let the user pass PIP_INDEX_URL via environment
if not "%PIP_INDEX_URL%"=="" (
    echo [INFO] Using PyPI mirror: %PIP_INDEX_URL%
    "%PYTHON%" -m pip install -r "%~dp0config\requirements_core.txt" --index-url "%PIP_INDEX_URL%" --no-warn-script-location
) else (
    "%PYTHON%" -m pip install -r "%~dp0config\requirements_core.txt" --no-warn-script-location
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to install core dependencies.
    echo.
    echo Possible solutions:
    echo   - Check your network connection
    echo   - If you are in China, set a PyPI mirror:
    echo       run.bat --pypi-mirror https://mirrors.aliyun.com/pypi/simple/
    echo   - Or set the PIP_INDEX_URL environment variable manually
    pause
    exit /b 1
)

echo.
echo [OK] Core dependencies installed successfully!
echo.

:launch_app
echo [OK] Starting BallonsTranslator-lite...
"%PYTHON%" launch.py %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
echo.
echo Application exited with code %LAUNCH_EXIT%.
pause
exit /b %LAUNCH_EXIT%
"""


# ── CLI ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Build BallonsTranslator-lite portable package",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--python-ver",
        default=DEFAULT_PYTHON_VER,
        help=f"Python version for embedded distribution (default: {DEFAULT_PYTHON_VER})",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: PROJECT_ROOT/release)",
    )
    parser.add_argument(
        "--no-7z",
        action="store_true",
        help="Skip 7z compression, keep as directory",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary build directory for inspection",
    )
    args = parser.parse_args()

    output = build_portable(
        python_ver=args.python_ver,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        skip_7z=args.no_7z,
        keep_temp=args.keep_temp,
    )
    print(f"\n[OK] Portable package: {output}")


if __name__ == "__main__":
    main()
