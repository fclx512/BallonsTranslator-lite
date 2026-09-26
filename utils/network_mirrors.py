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
DEFAULT_PYPI_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"

#: WinINET key holding the user's system proxy settings.
_WINDOWS_INTERNET_SETTINGS = (
    r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
)

#: Every spelling of the proxy variables ``requests``/``pip``/``httpx`` read.
#: Checked case-insensitively: a user (or corporate image) may set only the
#: lowercase form, and the old code only looked at ``HTTP_PROXY``.
PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

#: Hosts that must never be proxied; filled alongside the detected proxy.
NO_PROXY_VALUE = "localhost,127.0.0.1,.local"
NO_PROXY_ENV_VARS = ("NO_PROXY", "no_proxy")

# 本模块用简名指代两个镜像，落盘时必须写进 ``utils/config.py::MirrorConfig``
# 的实际字段——**节名是单数 ``mirror``、键名是字段名**（如 ``pip_index_url``）。
# 曾经写成 ``mirrors.pypi`` 这种自造结构，而全仓没有任何读取点，自动配置一直
# 是空转（2026-09-20 修）。``github_mirror`` 刻意不自动填：没有可靠默认值，
# 填错会让更新检查整条失效。
_MIRROR_CONFIG_SECTION = "mirror"
_MIRROR_FIELD_KEYS = {
    "huggingface": "hf_endpoint",
    "pypi": "pip_index_url",
}
#: 自动配置能填的镜像字段（简名）。
MIRROR_FIELDS = tuple(_MIRROR_FIELD_KEYS)


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
# Windows system proxy
# ---------------------------------------------------------------------------

def normalize_proxy_server(raw: str) -> str:
    """Turn a WinINET ``ProxyServer`` value into an ``http://host:port`` URL.

    The registry stores either a bare ``host:port`` or a protocol list such as
    ``http=host:port;https=host:port``.  Returns ``""`` when nothing usable is
    present.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if "=" in text:
        entries = {}
        for part in text.split(";"):
            key, sep, value = part.partition("=")
            if sep:
                entries[key.strip().lower()] = value.strip()
        text = entries.get("http") or entries.get("https") or ""
        if not text:
            return ""
    if "://" not in text:
        text = "http://" + text
    return text


def _read_windows_proxy_registry():
    """Return ``(enable, server)`` from HKCU Internet Settings.  May raise."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_INTERNET_SETTINGS) as key:
        enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        server = winreg.QueryValueEx(key, "ProxyServer")[0]
    return bool(enabled), str(server or "")


def detect_windows_system_proxy(reader=None) -> str:
    """Return the system proxy as a URL, or ``""`` when none applies.

    ``reader`` is injectable for tests; the default reads the Windows registry
    (and returns ``""`` immediately on non-Windows).  Any failure — including a
    missing registry key — is non-fatal and reported as "no proxy".
    """
    if reader is None:
        if os.name != "nt":
            return ""
        reader = _read_windows_proxy_registry
    try:
        enabled, server = reader()
    except Exception:
        return ""
    if not enabled:
        return ""
    return normalize_proxy_server(server)


def has_explicit_proxy(env: Optional[dict] = None) -> bool:
    """Whether any proxy variable is already set (any casing)."""
    target = os.environ if env is None else env
    return any(str(target.get(name) or "").strip() for name in PROXY_ENV_VARS)


def apply_system_proxy_env(env: Optional[dict] = None, reader=None) -> str:
    """Export the Windows system proxy when the user has not set one.

    Runs before the first dependency install, so ``pip``/``uv`` and later HF
    downloads use the proxy the user already configured in Windows.  An
    explicitly configured proxy (either casing) always wins, and environment
    variables that are already present are never overwritten.  Returns the
    proxy URL that was applied (``""`` when nothing changed).
    """
    target = os.environ if env is None else env
    if has_explicit_proxy(target):
        return ""
    server = detect_windows_system_proxy(reader=reader)
    if not server:
        return ""
    for name in PROXY_ENV_VARS:
        target.setdefault(name, server)
    for name in NO_PROXY_ENV_VARS:
        target.setdefault(name, NO_PROXY_VALUE)
    return server


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


def _mirror_section(config_path: str) -> dict:
    """Return the persisted ``mirror`` config section, ``{}`` when absent."""
    data = _read_raw_config(config_path)
    if not isinstance(data, dict):
        return {}
    section = data.get(_MIRROR_CONFIG_SECTION)
    return section if isinstance(section, dict) else {}


def _mirror_fields_missing(config_path: str) -> Set[str]:
    """Return mirror fields that are absent from the persisted config.

    ``MirrorConfig`` documents an empty string as "use the official source",
    which is a deliberate user choice — so only a **missing** key counts as
    unconfigured, never an empty one.
    """
    section = _mirror_section(config_path)
    return {
        field for field, key in _MIRROR_FIELD_KEYS.items() if key not in section
    }


def auto_fill_mirrors(config_path: str) -> list:
    """If config.json is missing the mirror fields and the system is in
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
    section = data.setdefault(_MIRROR_CONFIG_SECTION, {})
    if not isinstance(section, dict):
        return []
    for field in missing:
        if field == "huggingface":
            section[_MIRROR_FIELD_KEYS[field]] = DEFAULT_HUGGINGFACE_MIRROR
        elif field == "pypi":
            section[_MIRROR_FIELD_KEYS[field]] = DEFAULT_PYPI_MIRROR

    try:
        tmp = config_path + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, config_path)
    except Exception:
        return []

    print(f"Auto-configured network mirrors for mainland China: {', '.join(missing)}")
    return list(missing)


def apply_pip_mirror_env(config_path: str, env: Optional[dict] = None) -> str:
    """Export the persisted pip mirror as ``INDEX_URL`` for installers.

    ``launch.py`` installs core requirements **before** ``utils.config`` can be
    imported (that needs numpy/PyQt6), so the usual ``config.mirror.*`` →
    env-var path is not available yet on a first run — exactly the run that
    pulls the whole dependency set.  This reads the raw JSON with stdlib only
    and sets the variables ``pip`` / ``uv`` and ``utils.package_installer``
    consume, so a first-run install goes through the mirror too.

    Returns the effective index URL (empty when none is configured).
    """
    target = os.environ if env is None else env
    section = _mirror_section(config_path)

    index_url = str(section.get(_MIRROR_FIELD_KEYS["pypi"]) or "").strip()
    if index_url:
        target.setdefault("INDEX_URL", index_url)

    extra_url = str(section.get("pip_extra_index_url") or "").strip()
    if extra_url:
        target.setdefault("UV_EXTRA_INDEX_URL", extra_url)

    return index_url
