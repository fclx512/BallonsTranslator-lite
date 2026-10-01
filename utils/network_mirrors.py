"""Read manually configured mirrors into the environment for installers.

Mirrors are **manual, advanced-user settings** (Settings → network page);
there is no region auto-configuration any more — every dependency/model
download defaults to the official source, and the offline full bundle
covers users who cannot set up mirrors themselves.  What remains here is
the stdlib-only read-back of ``config.json``'s ``mirror`` section for the
first-run dependency install (``launch.py`` installs core requirements
before ``utils.config`` can be imported — that needs numpy/PyQt6), plus
the Windows system proxy detection.

The existing mirror read-back in launch.py (config.mirror.* → env vars)
handles subsequent launches.
"""

import json
import os
from typing import Optional

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

# 本模块用简名指代镜像，读取点必须是 ``utils/config.py::MirrorConfig``
# 的实际字段——**节名是单数 ``mirror``、键名是字段名**（如 ``pip_index_url``）。
# 曾经写成 ``mirrors.pypi`` 这种自造结构，而全仓没有任何读取点（2026-09-20 修）。
_MIRROR_CONFIG_SECTION = "mirror"
_MIRROR_FIELD_KEYS = {
    "pypi": "pip_index_url",
}


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


def apply_pip_mirror_env(config_path: str, env: Optional[dict] = None) -> str:
    """Export the manually configured pip mirror as ``INDEX_URL`` for installers.

    ``launch.py`` installs core requirements **before** ``utils.config`` can be
    imported (that needs numpy/PyQt6), so the usual ``config.mirror.*`` →
    env-var path is not available yet on a first run — exactly the run that
    pulls the whole dependency set.  This reads the raw JSON with stdlib only
    and sets the variables ``pip`` / ``uv`` and ``utils.package_installer``
    consume.  Only a **manually configured** mirror lands here; nothing is
    auto-filled any more, so an untouched install goes through the official
    source.

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
