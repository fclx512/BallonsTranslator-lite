"""Auto-detect network mirror configuration on first run.

Detects whether the system is in mainland China via locale / timezone,
and writes default Hugging Face / PyPI mirrors to config.json so that
dependency installation and model downloads work without manual setup.

The existing mirror read-back in launch.py (config.mirror.* → env vars)
handles subsequent launches — this module is only needed for **first run**
when ``config.json`` does not yet exist.
"""

import json
import locale
import os
import time
from typing import Iterable, Optional, Set

HUGGINGFACE_ORIGIN = "https://huggingface.co"
DEFAULT_HUGGINGFACE_MIRROR = "https://hf-mirror.com"
DEFAULT_PYPI_MIRROR = "https://mirrors.aliyun.com/pypi/simple"
MIRROR_FIELDS = ("huggingface", "pypi")


# ---------------------------------------------------------------------------
# Locale / timezone heuristics
# ---------------------------------------------------------------------------

def _collect_locale_names() -> list:
    candidates = [
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
    ]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    return _unique_nonempty(candidates)


def _collect_timezone_names() -> list:
    candidates = [os.environ.get("TZ", "")]
    candidates.extend(name for name in time.tzname if name)
    return _unique_nonempty(candidates)


def _has_mainland_china_locale(names: Iterable[str]) -> bool:
    for value in names:
        if not value:
            continue
        normalized = str(value).strip().split(".", 1)[0].replace("-", "_")
        lower = normalized.lower()
        if lower == "zh_cn" or lower.endswith("_cn") or "_cn_" in lower:
            return True
    return False


def _has_mainland_china_timezone(names: Iterable[str]) -> bool:
    for value in names:
        if not value:
            continue
        normalized = str(value).strip().lower().replace("\\", "/")
        if normalized in {"asia/shanghai", "prc"}:
            return True
        if "china standard time" in normalized or "中国标准时间" in normalized:
            return True
    return False


def _unique_nonempty(values: Iterable[str]) -> list:
    seen = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen


def should_use_china_mirrors() -> bool:
    """Return whether the system locale/timezone hints at mainland China."""
    return _has_mainland_china_locale(
        _collect_locale_names()
    ) or _has_mainland_china_timezone(_collect_timezone_names())


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

def _read_raw_config(config_path: str) -> Optional[dict]:
    if not config_path or not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf8") as f:
            return json.load(f)
    except Exception:
        return None


def _mirror_fields_missing(config_path: str) -> Set[str]:
    """Return mirror fields that are absent from the persisted config."""
    data = _read_raw_config(config_path)
    if not isinstance(data, dict):
        return set(MIRROR_FIELDS)
    mirrors = data.get("mirrors")
    if not isinstance(mirrors, dict):
        return set(MIRROR_FIELDS)
    return {f for f in MIRROR_FIELDS if f not in mirrors}


def auto_fill_mirrors(config_path: str) -> list:
    """If config.json is missing the mirrors section and the system is in
    mainland China, write sensible defaults and return the updated field names.

    Returns an empty list when no action was taken.
    """
    if not config_path:
        return []

    missing = _mirror_fields_missing(config_path)
    if not missing:
        # Everything already configured — nothing to do.
        return []

    if not should_use_china_mirrors():
        return []

    # Read existing config, merge mirrors in, write back.
    data = _read_raw_config(config_path) or {}
    if not isinstance(data, dict):
        return []
    data.setdefault("mirrors", {})
    for field in missing:
        if field == "huggingface":
            data["mirrors"]["huggingface"] = DEFAULT_HUGGINGFACE_MIRROR
        elif field == "pypi":
            data["mirrors"]["pypi"] = DEFAULT_PYPI_MIRROR

    try:
        tmp = config_path + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, config_path)
    except Exception:
        return []

    print(f"Auto-configured network mirrors for mainland China: {', '.join(missing)}")
    return list(missing)
