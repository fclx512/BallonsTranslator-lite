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
import subprocess
import sys
from pathlib import Path


def get_tracked_files(project_root: Path) -> list[Path]:
    """Return all git-tracked file paths relative to project_root."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed: {result.stderr.strip()}（发版清单必须基于 git，"
            "请在本仓库内运行）"
        )
    files = [project_root / p for p in result.stdout.strip().splitlines() if p]
    return [f for f in files if f.is_file()]


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
