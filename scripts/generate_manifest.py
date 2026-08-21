#!/usr/bin/env python3
"""Generate manifest.json — SHA256 file manifest for the project.

Lists all git-tracked files with their SHA256 hashes, used by the
in-app updater to compute delta updates for ZIP distribution.

Usage:
    python scripts/generate_manifest.py [--output PATH]

    Default output: project_root/manifest.json
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def get_tracked_files(project_root: Path) -> list[Path]:
    """Return all git-tracked file paths relative to project_root."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"Warning: git ls-files failed: {result.stderr.strip()}",
                file=sys.stderr,
            )
            # Fallback: walk the tree, respecting a basic exclude set
            return _fallback_walk(project_root)
        files = [project_root / p for p in result.stdout.strip().splitlines() if p]
        return [f for f in files if f.is_file()]
    except (subprocess.SubprocessError, FileNotFoundError):
        print("Warning: git not available, using fallback walk", file=sys.stderr)
        return _fallback_walk(project_root)


def _fallback_walk(project_root: Path) -> list[Path]:
    """Fallback when git is unavailable — walk directory with basic exclusions."""
    exclude_dirs = {
        ".git",
        "__pycache__",
        ".claude",
        ".github",
        ".vscode",
        ".idea",
        "node_modules",
        "venv",
        "venv_",
        "env",
        "release",
    }
    exclude_extensions = {".pyc", ".pyo"}
    ignore_file = project_root / ".gitignore"
    gitignore_patterns = _parse_gitignore(ignore_file) if ignore_file.exists() else []

    files = []
    for root, dirs, dirnames in list(os.walk(project_root)):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for name in dirs:
            fpath = Path(root) / name
            if fpath.is_file():
                rel = fpath.relative_to(project_root)
                # Skip .gitignore-matched files
                if _matches_any(rel, gitignore_patterns):
                    continue
                if fpath.suffix in exclude_extensions:
                    continue
                files.append(fpath)
        # Also include files directly in this dir
        for name in dirnames:
            if name in exclude_dirs:
                continue
            fpath = Path(root) / name
            if fpath.is_file():
                rel = fpath.relative_to(project_root)
                if _matches_any(rel, gitignore_patterns):
                    continue
                if fpath.suffix in exclude_extensions:
                    continue
                files.append(fpath)
    return files


def _parse_gitignore(path: Path) -> list[str]:
    """Return list of .gitignore patterns (simplified)."""
    patterns = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    except Exception:
        pass
    return patterns


def _matches_any(rel_path: Path, patterns: list[str]) -> bool:
    """Check if a relative path matches any gitignore pattern (basic)."""
    s = str(rel_path).replace("\\", "/")
    for pat in patterns:
        if pat.startswith("/"):
            # Anchored pattern
            if s == pat[1:] or s.startswith(pat[1:] + "/"):
                return True
        elif pat.endswith("/"):
            # Directory pattern
            if s.startswith(pat) or ("/" + s).startswith("/" + pat):
                return True
        else:
            # Simple pattern
            if s == pat or s.endswith("/" + pat):
                return True
    return False


def compute_sha256(file_path: Path) -> str:
    """Return hex SHA256 digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def generate_manifest(project_root: Path) -> dict:
    """Generate manifest dict for the project."""
    files = get_tracked_files(project_root)
    # Sort for deterministic output
    files.sort()

    # Get version from launch.py
    version = _detect_version(project_root)

    manifest: dict[str, str] = {}
    for fp in files:
        try:
            rel = fp.relative_to(project_root)
        except ValueError:
            continue
        # Normalize to forward slashes
        key = str(rel).replace("\\", "/")
        # Skip manifest itself and generated binary files
        if key == "manifest.json":
            continue
        try:
            manifest[key] = compute_sha256(fp)
        except (OSError, PermissionError) as e:
            print(f"Warning: skipping {key}: {e}", file=sys.stderr)

    return {
        "version": version,
        "created": None,  # filled in by caller if desired
        "files": manifest,
    }


def _detect_version(project_root: Path) -> str:
    """Extract version string from launch.py or pyproject.toml."""
    # launch.py 内的字面量（旧式）——当前版本从 utils/version.py 读 pyproject，仅在缺失时回退
    launch_py = project_root / "launch.py"
    if launch_py.exists():
        try:
            for line in launch_py.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("VERSION"):
                    # VERSION = "beta-..."
                    if "=" in line:
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        if val:
                            return val
        except Exception:
            pass
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("version"):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val:
                        return val
        except Exception:
            pass
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Generate manifest.json")
    parser.add_argument(
        "--output",
        default=None,
        help="Output path (default: project_root/manifest.json)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_path = Path(args.output) if args.output else project_root / "manifest.json"

    manifest = generate_manifest(project_root)

    # Fill in creation time
    from datetime import datetime, timezone

    manifest["created"] = datetime.now(timezone.utc).isoformat()

    output_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Manifest written to {output_path}")
    print(f"  Version: {manifest['version']}")
    print(f"  Files:   {len(manifest['files'])}")

    # Validate — no file should be missing
    missing = []
    for key in manifest["files"]:
        fp = project_root / key
        if not fp.exists():
            missing.append(key)
    if missing:
        print(f"Warning: {len(missing)} files listed in manifest are missing on disk:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
