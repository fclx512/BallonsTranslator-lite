"""Standalone update checker — downloads latest source from GitHub.

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
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{BRANCH}"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{BRANCH}.zip"
CHECK_TIMEOUT = 15  # seconds

# Marker file written after successful download
UPDATE_DIR = "_update"
READY_MARKER = "ready"
LAST_SHA_FILE = "last_sha"


def _project_root():
    return Path(__file__).resolve().parent.parent


def _update_dir():
    return _project_root() / UPDATE_DIR


def _http_get(url: str) -> tuple:
    """Return (response_bytes, headers_dict) or raise."""
    req = Request(url, headers={"User-Agent": f"{REPO_NAME}/update-check", "Accept": "application/vnd.github+json"})
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


def _download_and_extract():
    """Download repo zip and extract to _update/. Returns True on success."""
    root = _project_root()
    upd = _update_dir()

    print("  Downloading update...")
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

        # GitHub zip wraps everything in a single top-level dir, e.g.
        # BallonsTranslator-lite-main/.  Find it and move contents into _update/.
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
    if _download_and_extract():
        _write_local_sha(remote_sha)
        print("  Update downloaded. Restart to apply.")
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
    # The bat file reads stdout to decide what to do
    print(f"STATUS: {status}")
    sys.exit(0)


if __name__ == "__main__":
    main()
