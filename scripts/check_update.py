"""Standalone update checker — downloads latest source from GitHub.

Two update modes:
  1. Manifest delta (preferred): downloads remote manifest.json, compares with
     local, downloads only changed/new files, records deleted files.
  2. Full ZIP (fallback): downloads the entire repo as a zip archive.

No project imports (runs before launch.py, outside the app environment).
Requires only stdlib.

Exit codes:
  0 — OK (up-to-date, update downloaded, offline, or rate-limited)
  1 — Python too old (< 3.8)
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_OWNER = "fclx512"
REPO_NAME = "BallonsTranslator-lite"
BRANCH = "main"
CHECK_TIMEOUT = 15  # seconds

# GitHub mirror support (set via env var, e.g. https://gitclone.com)
_GITHUB_MIRROR = os.environ.get("GITHUB_MIRROR", "").rstrip("/")
if _GITHUB_MIRROR:
    _GH = _GITHUB_MIRROR
    _API = _GITHUB_MIRROR.replace("//github.com", "//api.github.com")
    if _API == _GITHUB_MIRROR:
        _API = f"https://api.{_GITHUB_MIRROR.removeprefix('https://')}"
else:
    _GH = "https://github.com"
    _API = "https://api.github.com"

API_URL = f"{_API}/repos/{REPO_OWNER}/{REPO_NAME}/commits/{BRANCH}"
ZIP_URL = f"{_GH}/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{BRANCH}.zip"
# Raw file base URL for manifest-based delta updates
RAW_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}"

# Marker file written after successful download
UPDATE_DIR = "_update"
READY_MARKER = "ready"
LAST_SHA_FILE = "last_sha"
DELETED_LIST = "deleted.txt"
FILES_SUBDIR = "files"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _update_dir() -> Path:
    return _project_root() / UPDATE_DIR


def _http_get(url: str) -> tuple:
    """Return (response_bytes, headers_dict) or raise."""
    req = Request(
        url,
        headers={
            "User-Agent": f"{REPO_NAME}/update-check",
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(req, timeout=CHECK_TIMEOUT) as resp:
        return resp.read(), dict(resp.headers)


def _check_remote_sha() -> str | None:
    """Get latest commit SHA from GitHub API. Returns None on failure."""
    try:
        data, headers = _http_get(API_URL)
        info = json.loads(data)
        sha = info.get("sha", "")
        remaining = headers.get("X-RateLimit-Remaining", "?")
        print(f"  GitHub API: rate limit remaining = {remaining}")
        return sha
    except URLError as e:
        print(f"  Network unavailable: {e.reason}")
        return None
    except Exception as e:
        print(f"  Update check failed: {e}")
        return None


def _read_local_sha() -> str | None:
    p = _update_dir() / LAST_SHA_FILE
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None


def _write_local_sha(sha: str):
    upd = _update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    (upd / LAST_SHA_FILE).write_text(sha, encoding="utf-8")


def _read_local_manifest() -> dict | None:
    """Read local manifest.json, return None if missing or invalid."""
    p = _project_root() / "manifest.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── Manifest-based delta update ─────────────────────────────────────


def _download_manifest_update(remote_sha: str) -> bool:
    """Download changed files via manifest comparison.

    Returns True if the delta was prepared, False if it should fall back to ZIP.
    """
    # Step 1: Download remote manifest
    manifest_url = f"{RAW_URL}/manifest.json"
    print("  Downloading remote manifest...")
    try:
        data, _ = _http_get(manifest_url)
        remote_manifest: dict = json.loads(data)
    except Exception as e:
        print(f"  Manifest download failed ({e}), falling back to ZIP...")
        return False

    remote_files: dict[str, str] = remote_manifest.get("files", {})
    if not remote_files:
        print("  Remote manifest has no files, falling back to ZIP...")
        return False

    # Step 2: Compare with local manifest
    local_manifest = _read_local_manifest()
    local_files: dict[str, str] = (
        local_manifest.get("files", {}) if local_manifest else {}
    )

    changed = []
    deleted = []
    for path, remote_hash in remote_files.items():
        local_hash = local_files.get(path)
        if local_hash != remote_hash:
            changed.append(path)

    for path in local_files:
        if path not in remote_files:
            deleted.append(path)

    if not changed and not deleted:
        print("  Files unchanged (manifest match).")
        # Still record the SHA so we don't re-check
        return True

    # Threshold: if too many files changed, fall back to single ZIP download
    # to avoid hundreds of individual HTTP requests.
    DELTA_THRESHOLD = 50
    if len(changed) > DELTA_THRESHOLD:
        print(
            f"  {len(changed)} files changed — exceeds threshold ({DELTA_THRESHOLD}), "
            "falling back to full ZIP download..."
        )
        return False

    print(f"  Changed: {len(changed)}, Deleted: {len(deleted)}")

    # Step 3: Download changed files
    upd = _update_dir()
    files_dir = upd / FILES_SUBDIR

    # Clean any partial previous delta
    if files_dir.exists():
        shutil.rmtree(files_dir, ignore_errors=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    failed = []
    for path in changed:
        dest = files_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        file_url = f"{RAW_URL}/{path}"
        try:
            data, _ = _http_get(file_url)
            dest.write_bytes(data)
            downloaded += 1
        except Exception as e:
            failed.append(path)
            print(f"  Failed to download {path}: {e}")

    if failed:
        print(f"  Warning: {len(failed)} files failed to download")
        # Continue anyway — the update will be partial

    print(f"  Downloaded {downloaded}/{len(changed)} files")

    # Step 4: Write deleted list
    if deleted:
        (upd / DELETED_LIST).write_text("\n".join(deleted) + "\n", encoding="utf-8")

    # Step 5: Write ready marker
    (upd / READY_MARKER).touch()

    # Step 6: Copy remote manifest for the applying step
    (upd / "manifest.json").write_text(
        json.dumps(remote_manifest, indent=2), encoding="utf-8"
    )

    print(f"  Delta prepared in {upd}")
    return True


# ── Full ZIP fallback ──────────────────────────────────────────────


def _download_and_extract():
    """Download entire repo ZIP and extract to _update/. Returns True on success."""
    upd = _update_dir()

    print("  Downloading full repository ZIP...")
    try:
        zip_data, _ = _http_get(ZIP_URL)
    except Exception as e:
        print(f"  Download failed: {e}")
        return False

    # Extract to temp dir first, then move
    tmp = tempfile.mkdtemp(prefix="bt_update_")
    try:
        tmp_zip = os.path.join(tmp, "repo.zip")
        with open(tmp_zip, "wb") as f:
            f.write(zip_data)

        extract_to = os.path.join(tmp, "extracted")
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(extract_to)

        # GitHub zip wraps everything in a single top-level dir
        items = os.listdir(extract_to)
        top_dirs = [d for d in items if os.path.isdir(os.path.join(extract_to, d))]
        if len(top_dirs) == 1:
            src = os.path.join(extract_to, top_dirs[0])
        else:
            src = extract_to

        # Remove old _update/ if any, then move new content in
        if upd.exists():
            shutil.rmtree(upd, ignore_errors=True)
        shutil.move(src, str(upd))

        # Write ready marker
        (upd / READY_MARKER).touch()
        return True

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Main check-and-download ───────────────────────────────────────


def check_and_download() -> str:
    """Run one update cycle. Returns status string."""
    upd = _update_dir()

    # If update is already downloaded, just report it
    if (upd / READY_MARKER).exists():
        return "UPDATE_PENDING"

    # Check remote
    print("Checking for updates...")
    remote_sha = _check_remote_sha()
    if remote_sha is None:
        return "OFFLINE"

    local_sha = _read_local_sha()
    if remote_sha == local_sha:
        print("  Already up to date.")
        return "UP_TO_DATE"

    print(f"  New version available ({remote_sha[:8]}...).")

    # Try manifest-based delta first
    if _download_manifest_update(remote_sha):
        _write_local_sha(remote_sha)
        print("  Delta update downloaded. Restart to apply.")
        return "UPDATE_DOWNLOADED"

    # Fall back to full ZIP
    if _download_and_extract():
        _write_local_sha(remote_sha)
        print("  Full update downloaded. Restart to apply.")
        return "UPDATE_DOWNLOADED"
    else:
        return "DOWNLOAD_FAILED"


def main():
    if sys.version_info < (3, 8):
        print("Python 3.8+ required for update check.")
        sys.exit(1)

    try:
        status = check_and_download()
    except Exception:
        traceback.print_exc()
        status = "ERROR"

    # Always exit 0 — update failure should never block launch
    print(f"STATUS: {status}")
    sys.exit(0)


if __name__ == "__main__":
    main()
