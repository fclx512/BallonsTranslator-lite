"""Keep automated tests away from the developer's real configuration files."""

import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = None
_PROTECTED = (Path(__file__).resolve().parent.parent / "config").resolve()
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _is_real_config(path):
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    resolved = Path(os.fsdecode(path)).resolve()
    return resolved == _PROTECTED or _PROTECTED in resolved.parents


def _guard_config_writes(event, args):
    paths = ()
    if event == "open":
        path, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & _WRITE_FLAGS:
            paths = (path,)
    elif event in {"os.rename", "os.link"}:
        paths = args[:2]
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
        paths = args[:1]
    elif event == "os.symlink":
        paths = args[1:2]
    if any(_is_real_config(path) for path in paths):
        raise PermissionError("Tests cannot modify the real config directory; use a temporary path")


def install_config_sandbox():
    """Redirect defaults before imports and reject explicit real-config writes.

    Called by pytest's conftest before test collection, and by the standalone
    startup smoke test. The audit hook lives until process exit, including Qt
    object destruction; local fixture cleanup cannot remove it.
    """
    global _SANDBOX
    if _SANDBOX is not None:
        return Path(_SANDBOX.name)
    _SANDBOX = tempfile.TemporaryDirectory(prefix="btl-test-config-")
    sandbox = Path(_SANDBOX.name)
    from utils import shared

    shared.CONFIG_PATH = str(sandbox / "config.json")
    shared.GLOBAL_STYLES_PATH = str(sandbox / "global_styles.json")
    shared.CUSTOM_THEME_PATH = str(sandbox / "custom_themes.json")
    shared.DEFAULT_TEXTSTYLE_DIR = str(sandbox / "textstyles")
    shared.LOGGING_PATH = str(sandbox / "logs")
    shared.cache_dir = str(sandbox / "cache")
    shared.cache_path = str(sandbox / "cache/cache.json")
    # A plugin may have imported these functions before pytest configuration.
    # Patch bound defaults too, rather than relying only on shared constants.
    styles = sys.modules.get("utils.global_styles")
    if styles is not None:
        styles.load_global_styles.__defaults__ = (shared.GLOBAL_STYLES_PATH,)
        styles.save_global_styles.__defaults__ = (shared.GLOBAL_STYLES_PATH,)
    config = sys.modules.get("utils.config")
    if config is not None:
        config.load_config.__defaults__ = (shared.CONFIG_PATH,)
    sys.addaudithook(_guard_config_writes)
    return sandbox
