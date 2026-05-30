"""
Update-cache persistence for the update-check system.
Stores last_check_time, last_remote_commit, and cooldown_minutes
in .btrans_cache/update_cache.json.
"""

import json
import os
import os.path as osp
from datetime import datetime, timezone

import utils.shared as shared

UPDATE_CACHE_PATH = osp.join(shared.cache_dir, "update_cache.json")


def _load():
    if osp.exists(UPDATE_CACHE_PATH):
        try:
            with open(UPDATE_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(cache):
    if not osp.exists(shared.cache_dir):
        os.makedirs(shared.cache_dir)
    with open(UPDATE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4)


def get_last_check_time():
    """Return ISO timestamp of the last successful check, or None."""
    return _load().get("last_check_time")


def get_last_remote_commit():
    """Return the full remote commit hash from the last successful check, or None."""
    return _load().get("last_remote_commit")


def get_cooldown_minutes():
    """Return the cooldown period in minutes (default 30)."""
    return _load().get("cooldown_minutes", 30)


def is_within_cooldown():
    """Return True if the last check was within the cooldown window."""
    cache = _load()
    tstr = cache.get("last_check_time")
    if not tstr:
        return False
    try:
        last = datetime.fromisoformat(tstr)
    except ValueError:
        return False
    elapsed_sec = (datetime.now(timezone.utc) - last).total_seconds()
    return elapsed_sec < (cache.get("cooldown_minutes", 30) * 60)


def record_check(remote_commit):
    """Persist a successful check: timestamp now + the remote commit hash."""
    cache = _load()
    cache["last_check_time"] = datetime.now(timezone.utc).isoformat()
    cache["last_remote_commit"] = remote_commit
    _save(cache)


def human_readable_last_check():
    """Return a user-facing string like '3 minutes ago' or 'Never'."""
    tstr = get_last_check_time()
    if not tstr:
        return "Never"
    try:
        last = datetime.fromisoformat(tstr)
    except ValueError:
        return "Never"
    diff = datetime.now(timezone.utc) - last
    minutes = int(diff.total_seconds() / 60)
    if minutes < 1:
        return "Less than a minute ago"
    elif minutes == 1:
        return "1 minute ago"
    elif minutes < 60:
        return f"{minutes} minutes ago"
    elif minutes < 1440:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = minutes // 1440
        return f"{days} day{'s' if days > 1 else ''} ago"
