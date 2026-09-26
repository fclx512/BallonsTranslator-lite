import argparse
import os
import os.path as osp
import subprocess
import sys
from pathlib import Path
from platform import platform

PATH_ROOT = Path(__file__).parent

# Embedded Python's ._pth file overrides sys.path, so ensure project root is in path
# before any project-local imports (e.g. utils.*)
if str(PATH_ROOT) not in sys.path:
    sys.path.insert(0, str(PATH_ROOT))

_pylibs_sp = PATH_ROOT / "ballontrans_pylibs_win" / "Lib" / "site-packages"
if _pylibs_sp.exists() and str(_pylibs_sp) not in sys.path:
    sys.path.append(str(_pylibs_sp))

# ── Python version gate ──────────────────────────────────────────────
# Must reject unsupported interpreters BEFORE importing any project-local
# module (``utils.*`` below): those imports use 3.10+ syntax and would fail
# with a confusing SyntaxError instead of an actionable message.  Mirrors
# ``pyproject.toml`` ``requires-python = ">=3.10"``.
MIN_PYTHON = (3, 10)


def python_version_supported(version_info=None) -> bool:
    """Whether ``version_info`` satisfies the project's minimum Python."""
    info = sys.version_info if version_info is None else version_info
    return tuple(info[:2]) >= MIN_PYTHON


def python_version_error(version_info=None) -> str:
    """Actionable rejection message, or ``""`` when the version is supported."""
    if python_version_supported(version_info):
        return ""
    info = sys.version_info if version_info is None else version_info
    current = ".".join(str(part) for part in tuple(info[:3]))
    return (
        f"BallonsTranslator-lite requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} "
        f"or newer, but this interpreter is Python {current}.\n"
        f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ from "
        "https://www.python.org/downloads/ and launch again with it."
    )


def enforce_python_version() -> None:
    """Exit(1) with a clear message on an unsupported interpreter."""
    message = python_version_error()
    if message:
        print(message, file=sys.stderr)
        sys.exit(1)


enforce_python_version()

import utils.shared as shared  # noqa: E402
from utils.env_diagnostic import detect_gpu_info  # noqa: E402

BRANCH = "main"
from utils.version import (  # noqa: E402  # single source: pyproject.toml
    APP_VERSION as VERSION,
)

python = sys.executable
git = os.environ.get("GIT", "git")
skip_install = False
index_url = os.environ.get("INDEX_URL", "")
QT_APIS = ["pyqt6", "pyside6", "pyqt5", "pyside2"]
stored_commit_hash = None

IS_WIN7 = "Windows-7" in platform()

parser = argparse.ArgumentParser()
parser.add_argument(
    "--reinstall-torch",
    action="store_true",
    help="launch.py argument: install the appropriate version of torch even if you have some version already installed",
)
parser.add_argument(
    "--proj-dir", default="", type=str, help="Open project directory on startup"
)
if IS_WIN7:
    parser.add_argument("--qt-api", default="pyqt5", choices=QT_APIS, help="Set qt api")
else:
    parser.add_argument("--qt-api", default="pyqt6", choices=QT_APIS, help="Set qt api")
parser.add_argument("--debug", action="store_true")
parser.add_argument("--requirements", default="requirements.txt")
parser.add_argument("--headless", action="store_true", help="run without GUI")
parser.add_argument("--no-venv", action="store_true", help="skip auto-venv creation for Store Python")
parser.add_argument(
    "--exec_dirs",
    default="",
    help="translation queue (project directories) separated by comma",
)
parser.add_argument(
    "--pages",
    default="",
    help="page range to process (e.g. 1-5,7,9-12) when --exec_dirs is used",
)
parser.add_argument("--ldpi", default=None, type=float, help="logical dots perinch")
parser.add_argument(
    "--export-translation-txt",
    action="store_true",
    help="save translation to txt file once RUN completed",
)
parser.add_argument(
    "--export-source-txt",
    action="store_true",
    help="save source to txt file once RUN completed",
)
parser.add_argument(
    "--frozen", action="store_true", help="run without checking requirements"
)
parser.add_argument(
    "--update", action="store_true", help="Update the repository before launching"
)  # Add argument --update
parser.add_argument(
    "--config_path",
    default=shared.CONFIG_PATH,
    help="Config file to use for translation",
)  # Named config_path to avoid conflict with existing name config
parser.add_argument(
    "--cpu",
    action="store_true",
    help="Force CPU mode even if PyTorch with CUDA is available",
)
args, _ = parser.parse_known_args()


def run(command, desc=None, errdesc=None, custom_env=None, live=False):
    if desc is not None:
        print(desc)

    if live:
        result = subprocess.run(
            command, shell=True, env=os.environ if custom_env is None else custom_env
        )
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or "Error running command"}.
Command: {command}
Error code: {result.returncode}""")

        return ""

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        env=os.environ if custom_env is None else custom_env,
    )

    if result.returncode != 0:
        message = f"""{errdesc or "Error running command"}.
Command: {command}
Error code: {result.returncode}
stdout: {result.stdout.decode(encoding="utf8", errors="ignore") if len(result.stdout) > 0 else "<empty>"}
stderr: {result.stderr.decode(encoding="utf8", errors="ignore") if len(result.stderr) > 0 else "<empty>"}
"""
        raise RuntimeError(message)

    return result.stdout.decode(encoding="utf8", errors="ignore")


def run_pip(args, desc=None):
    if skip_install:
        return

    index_url_line = f" --index-url {index_url}" if index_url != "" else ""
    return run(
        f'"{python}" -m pip {args} --prefer-binary{index_url_line} --disable-pip-version-check --no-warn-script-location',
        desc=f"Installing {desc}",
        errdesc=f"Couldn't install {desc}",
        live=True,
    )


UV_AVAILABLE = False


def _uv_module_available():
    """Check if uv is importable as a module of this interpreter."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "uv", "--version"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _uv_available():
    """Check whether uv is usable — as a module, or as an executable beside
    this interpreter (the release bundle ships ``uv.exe`` next to
    ``python.exe`` without installing the Python module)."""
    if _uv_module_available():
        return True
    from utils.package_installer import find_uv

    return bool(find_uv())


def ensure_uv():
    """Ensure uv is installed. Bootstraps via pip if missing."""
    global UV_AVAILABLE
    if skip_install or getattr(sys, "frozen", False):
        return False

    if _uv_available():
        UV_AVAILABLE = True
        return True

    try:
        print("Installing uv package manager...")
        run_pip("install uv", "uv")
    except Exception:
        print("Warning: uv not available, falling back to pip")
        return False

    # Verify uv actually works after installation
    if _uv_available():
        UV_AVAILABLE = True
        return True
    else:
        print("Warning: uv installed but not functional, falling back to pip")
        UV_AVAILABLE = False
        return False


def run_uv(args, desc=None):
    if skip_install:
        return
    index_url_line = f" --index-url {index_url}" if index_url != "" else ""
    if _uv_module_available():
        return run(
            f'"{python}" -m uv pip {args}{index_url_line} --disable-pip-version-check',
            desc=f"Installing {desc}",
            errdesc=f"Couldn't install {desc}",
            live=True,
        )

    # 发行包把 uv.exe 与 python.exe 放在同一个 ballontrans_pylibs_win/ 里，
    # 那种 uv 没有 Python 模块，只能直接执行，并显式指定目标解释器。
    from utils.package_installer import find_uv

    uv_exe = find_uv() or "uv"
    return run(
        f'"{uv_exe}" pip {args}{index_url_line} --python "{python}" --disable-pip-version-check',
        desc=f"Installing {desc}",
        errdesc=f"Couldn't install {desc}",
        live=True,
    )


def commit_hash():
    global stored_commit_hash

    if stored_commit_hash is not None:
        return stored_commit_hash

    try:
        stored_commit_hash = run(f"{git} rev-parse HEAD").strip()
    except Exception:
        stored_commit_hash = "<none>"

    return stored_commit_hash


def _probe_import(module_name: str):
    """Import ``module_name``, capturing any failure as data.

    Binary wheels (torch, Pillow, onnxruntime) can raise ``OSError`` — a
    missing VC runtime or a half-installed DLL payload — or a plain
    ``ImportError``.  Both must be diagnosable instead of aborting startup, so
    this returns ``(module, error_message)`` with ``module`` set to ``None``
    and a non-empty message on any failure.
    """
    import importlib

    try:
        return importlib.import_module(module_name), ""
    except Exception as e:  # noqa: BLE001 — any import failure is reportable
        return None, f"{type(e).__name__}: {e}"


def _detect_user_torch():
    """Check if the current Python process has GPU-accelerated PyTorch.

    Unlike the old implementation, this does NOT search other Pythons on
    the system.  It only checks ``import torch`` within the current process,
    then verifies an accelerator is available: CUDA on NVIDIA GPUs or MPS on
    Apple Silicon.  A torch that is present but unloadable (DLL error) is
    reported and treated as "no accelerator", so the app degrades to CPU /
    no-model mode instead of crashing.

    Returns:
        True if an accelerated PyTorch is available in the current process.
        False otherwise.
    """
    torch, _torch_err = _probe_import("torch")
    if torch is None:
        if _torch_err:
            print(f"  PyTorch is installed but failed to load ({_torch_err}).")
            print(
                "  Falling back to CPU/no-model mode. Reinstall PyTorch to "
                "enable local models."
            )
        else:
            print("  PyTorch not installed in this Python environment.")
        return False

    try:
        if torch.cuda.is_available():
            print("  CUDA PyTorch available: " + str(torch.__file__))
            return True

        if (
            hasattr(torch, "backends")
            and hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            print("  MPS (Apple Silicon) PyTorch available: " + str(torch.__file__))
            return True
    except Exception as e:
        print(f"  PyTorch loaded but its accelerator probe failed ({type(e).__name__}: {e}).")
        print("  Using CPU mode.")
        return False

    # torch exists but no GPU accelerator available
    print("  PyTorch found but no GPU accelerator (CUDA/MPS) is available.")
    if sys.platform == "darwin":
        print("  Continuing with CPU mode.")
        return False
    _gpu_info = detect_gpu_info()
    if _gpu_info:
        _gen = _gpu_info["generation"]
        if _gpu_info["torch_index"] is None:
            print(
                f"  PyTorch 2.x does not support your {_gen} GPU."
                "  Use CPU mode: python launch.py --cpu"
            )
        else:
            _cc = _gpu_info.get("compute_cap")
            _cc_txt = f", CC {_cc}.x" if _cc is not None else ""
            _idx = _gpu_info["torch_index"].rsplit("/", 1)[-1]
            print(
                f"  Recommended CUDA {_gpu_info['recommended_cuda']}"
                f" for your {_gen} GPU{_cc_txt}.\n"
                "    If using the one-click bundle, run install_cuda.bat.\n"
                f"    Otherwise: pip install -U torch torchvision"
                f" --index-url {_gpu_info['torch_index']}"
            )
    else:
        print("  Consider installing PyTorch with CUDA for GPU acceleration.")
    return False


BT = None


def _ensure_module_fallback():
    """If torch / onnxruntime are not available, fall back to no-model modules.

    Preserves ModuleConfig defaults while ensuring the app starts when the
    user hasn't installed model dependencies yet.

    ============== =============== ===================== ==============
    Module type    Needs torch?    Needs onnxruntime?    Fallback
    ============== =============== ===================== ==============
    textdetector   yes (all)       no                    ``none``
    ocr            depends         depends               *(kept as configured)*
    inpainter      yes except      no                    ``none`` (patchmatch
                   ``patchmatch``                         is kept)
    llm_ocr        no              no                    *(never)*
    translator     no              no                    *(never)*
    ============== =============== ===================== ==============
    """
    # Probe with _probe_import so a broken binary (DLL load failure) is
    # treated the same as a missing one — degrade to no-model modules rather
    # than letting the exception bubble out of startup.
    _torch_mod, _torch_err = _probe_import("torch")
    _onnx_mod, _onnx_err = _probe_import("onnxruntime")
    _onnxocr_mod, _onnxocr_err = _probe_import("onnxocr")
    _has_torch = _torch_mod is not None
    _has_onnx = _onnx_mod is not None
    _has_onnxocr = _onnxocr_mod is not None

    from utils.config import pcfg, record_auto_downgrade

    changed = []

    # ── Text detector: all real detectors need torch ──
    if not _has_torch and pcfg.module.textdetector not in ("none",):
        _old = pcfg.module.textdetector
        pcfg.module.textdetector = "none"
        record_auto_downgrade(pcfg.module, "textdetector", "none", _old)
        changed.append(f"textdetector: {_old} → none")

    # ── OCR: rely on config default (none_ocr) or user's saved choice ──
    # Previously this block forcibly reset non-none/non-llm OCR modules back
    # to none_ocr; now we trust whatever the user configured.

    # ── Inpainter: torch-backed models need torch; PatchMatch does not ──
    # PatchMatch is the lightweight, non-model repair path shipped with the
    # minimal package.  It loads its native DLL lazily, so missing torch must
    # never replace it with ``none`` during startup.
    if not _has_torch and pcfg.module.inpainter not in ("none", "patchmatch"):
        _old = pcfg.module.inpainter
        pcfg.module.inpainter = "none"
        record_auto_downgrade(pcfg.module, "inpainter", "none", _old)
        changed.append(f"inpainter: {_old} → none")

    if changed:
        _missing = []
        if not _has_torch:
            _missing.append("PyTorch")
        if not _has_onnx:
            _missing.append("onnxruntime")
        if not _has_onnxocr:
            _missing.append("onnxocr")
        print(
            f"{', '.join(_missing)} not available"
            " — automatically switched to no-model modules:"
        )
        for _name, _err in (
            ("PyTorch", _torch_err),
            ("onnxruntime", _onnx_err),
            ("onnxocr", _onnxocr_err),
        ):
            if _err:
                print(f"  {_name} failed to load: {_err}")
        for c in changed:
            print(f"  {c}")
        if not _has_torch:
            print("  Install PyTorch to enable local models, then restart.")
        if not _has_onnx:
            print(
                "  Install onnxruntime (or onnxruntime-gpu)"
                " to enable PP-OCRv6 ONNX, then restart."
            )
        if not _has_onnxocr:
            print(
                "  Install onnxocr (use --no-deps to avoid numpy<2 conflict):"
            )
            print(
                "    pip install onnxocr --no-deps   # numpy >= 2 compatible"
            )
        print()


def _ensure_model_files_fallback():
    """Check if declared model files exist for the currently configured
    detection / OCR / inpainting modules, and whether required Python
    packages are importable.

    If **all** model files for a module are missing on disk (e.g. after a
    directory restructure), silently fall back to the corresponding "none"
    module so the app starts without a blocking dependency dialog.  Also
    falls back if the module declares ``requires_packages`` and they aren't
    importable.  The user can later re-download files via the Model Files
    panel.

    This function is intended to be called **after**
    ``init_lazy_module_registries()`` so that ``download_file_list``
    attributes are accessible.
    """
    import importlib
    import os.path as osp

    from utils import shared
    from utils.config import pcfg, record_auto_downgrade

    # Lazy-import registries (safe after init_lazy_module_registries)
    try:
        from modules import INPAINTERS, OCR, TEXTDETECTORS
    except Exception:
        return

    _REGISTRIES = {
        "textdetector": (TEXTDETECTORS, "none"),
        "ocr": (OCR, "none_ocr"),
        "inpainter": (INPAINTERS, "none"),
    }
    changed = []

    for _type, (_registry, _fallback) in _REGISTRIES.items():
        _cfg_key = _type  # e.g. "textdetector", "ocr", "inpainter"
        _module_name = getattr(pcfg.module, _cfg_key, "")
        if not _module_name or _module_name.startswith("none") or _module_name == "llm_ocr":
            continue

        _spec = _registry.get(_module_name)
        if not _spec:
            continue

        # ── Check requires_packages by resolving the spec ────────
        # This does a real import, only for the currently configured
        # module — acceptable at startup.
        _req_pkgs = []
        try:
            _resolved = _spec.resolve()
            _req_pkgs = getattr(_resolved, "requires_packages", None) or []
        except Exception:
            pass  # can't resolve → skip package check, still try model file check

        _missing_pkg = None
        for _pkg_req in _req_pkgs:
            _pkg_name = _pkg_req.split(">=")[0].split("==")[0].split("!=")[0].strip()
            try:
                importlib.import_module(_pkg_name)
            except ImportError:
                _missing_pkg = _pkg_req
                break

        if _missing_pkg:
            setattr(pcfg.module, _cfg_key, _fallback)
            record_auto_downgrade(pcfg.module, _cfg_key, _fallback, _module_name)
            changed.append(
                f"{_type}: {_module_name} → {_fallback} (package {_missing_pkg} missing)"
            )
            continue

        # ── Check model files on disk ────────────────────────────
        _dfl = getattr(_spec, "download_file_list", None) or []
        if not _dfl:
            continue

        # Check that **every** download entry has at least one file on disk.
        # For multi-entry modules (e.g. ppocrv6_onnx has separate entries for
        # det.onnx, rec.onnx, dict.txt), a single dict file is not enough.
        _all_entries_ok = True
        for _dl_entry in _dfl:
            _paths = _dl_entry.get("save_files") or _dl_entry.get("files") or []
            if isinstance(_paths, str):
                _paths = [_paths]
            _entry_has_file = False
            for _fpath in _paths:
                if not osp.isabs(_fpath):
                    _fpath = osp.join(shared.PROGRAM_PATH, _fpath)
                if osp.exists(_fpath):
                    _entry_has_file = True
                    break
            if not _entry_has_file:
                _all_entries_ok = False
                break

        if not _all_entries_ok:
            setattr(pcfg.module, _cfg_key, _fallback)
            record_auto_downgrade(pcfg.module, _cfg_key, _fallback, _module_name)
            changed.append(
                f"{_type}: {_module_name} → {_fallback} (model files missing)"
            )

    if changed:
        print("Model files not found — automatically switched to no-model modules:")
        for c in changed:
            print(f"  {c}")
        print()


#: Cap on chained automatic startup restarts.  A restart only exists to load
#: freshly installed packages, so if three installs in a row still don't make
#: the app usable, another exec would just repeat the loop.
MAX_STARTUP_RESTARTS = 3
RESTART_COUNT_ENV = "BTRANSLATOR_RESTART_COUNT"


def _restart_count() -> int:
    try:
        return max(0, int(os.environ.get(RESTART_COUNT_ENV, "") or 0))
    except (TypeError, ValueError):
        return 0


def restart(reason: str = "", guard: bool = False) -> bool:
    """Re-exec this interpreter.

    ``guard=True`` marks an automatic startup restart: the number of chained
    re-execs is capped so an install that keeps "succeeding" without making
    imports work cannot loop forever.  Returns ``False`` when the guard
    refuses to restart (callers should then report instead of looping).
    """
    global BT
    if guard:
        count = _restart_count()
        if count >= MAX_STARTUP_RESTARTS:
            print(
                f"Startup restart limit reached ({MAX_STARTUP_RESTARTS} automatic "
                "restarts) — not restarting again.\n"
                "The install did not make the app usable. Resolve the environment "
                "manually, then start again:\n"
                "  pip install -r requirements.txt"
            )
            return False
        os.environ[RESTART_COUNT_ENV] = str(count + 1)

    print(f"restarting... ({reason})\n" if reason else "restarting...\n")
    if BT:
        BT.close()
    os.execv(sys.executable, ["python"] + sys.argv)
    return True


def setup_locks():
    from qtpy.QtCore import QMutex

    from utils.lock import RUNTIME_LOCKS

    RUNTIME_LOCKS["model_loading"] = QMutex()


def setup_startup_logging() -> bool:
    """Attach file logging and crash hooks; never fatal.

    Called before any heavy work so crashes during dependency setup are still
    recorded.  Both ``setup_logging`` and ``install_exception_hooks`` already
    degrade internally; the try/except here is the last line of defence, since
    a logging failure must never be able to block startup.
    """
    try:
        from utils.logger import install_exception_hooks, setup_logging

        ok = setup_logging(shared.LOGGING_PATH, shared.MAX_NUM_LOG)
        install_exception_hooks()
        return ok
    except Exception as e:
        print(f"Warning: file logging unavailable ({e}); using console output only.")
        return False


def main():

    if args.debug:
        os.environ["BALLOONTRANS_DEBUG"] = "1"

    if args.cpu:
        os.environ["BALLOONTRANS_CPU_ONLY"] = "1"
        print("CPU mode forced via --cpu flag")

    os.environ["QT_API"] = args.qt_api

    # ── File logging + crash hooks (stdlib only, never fatal) ──────────
    setup_startup_logging()

    # Preload MSVC runtime DLLs before PyQt6 registers its Qt bin directory
    # (which can make later PyTorch DLL resolution pick up the wrong version).
    # Best-effort; safe to ignore on non-Windows or if VC runtime is unavailable.
    if sys.platform == "win32":
        _msvc_loaded = False
        for _dll in ("vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"):
            try:
                import ctypes

                ctypes.CDLL(_dll)
                _msvc_loaded = True
            except OSError:
                if _dll == "msvcp140.dll":
                    print(
                        "Microsoft Visual C++ Redistributable is not installed or "
                        "not visible to this process. Deep learning modules may "
                        "fail to load until the x64 VC runtime is installed."
                    )

    commit = commit_hash()

    print("Python version: ", sys.version)
    print("Python executable: ", sys.executable)

    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    os.chdir(APP_DIR)

    # ── Microsoft Store Python: auto-create local .venv ──────────────────
    # Store Python lives in a read-only system directory
    # (Program Files\WindowsApps) where pip/uv cannot install packages.
    # Detect it and redirect into a project-local venv.
    if not args.no_venv and (
        "WindowsApps" in sys.executable or "PythonSoftwareFoundation" in sys.executable
    ):
        _venv_dir = os.path.join(APP_DIR, ".venv")
        _venv_python = os.path.join(_venv_dir, "Scripts", "python.exe")
        if not os.path.isfile(_venv_python):
            print("Microsoft Store Python detected — creating local virtual environment...")
            try:
                subprocess.run(
                    [sys.executable, "-m", "venv", _venv_dir],
                    check=True, capture_output=True, timeout=60,
                )
                print(f"  Virtual environment created at {_venv_dir}")
            except Exception as _e:
                print(f"  Warning: failed to create venv ({_e})")
                print("  Continuing with system Python — some operations may fail.")
                _venv_python = None  # Don't re-exec
        if _venv_python and os.path.isfile(_venv_python):
            print("  Switching to virtual environment Python...")
            os.execv(_venv_python, [_venv_python] + sys.argv)

    print(f"Version: {VERSION}")
    print(f"Branch: {BRANCH}")
    print(f"Commit hash: {commit}")

    # ── Network mirror + system proxy bootstrap (must precede installs) ──
    #     首次运行按地区自动补写 config.json 的 mirror 节，并把 pip 源落到
    #     环境变量上。位置必须在 ensure_core_requirements 之前：那是首启动
    #     拉全套依赖的一步，而 config 那时还读不了（它依赖 numpy/PyQt6）。
    #     config.mirror.* 的正式读取仍在下方 config 加载之后。
    #     Windows 系统代理同样在这里落地：它的消费点也是 pip/uv 与 HF 下载，
    #     晚于首次安装就来不及了。用户已显式配置的代理（任意大小写）不覆盖。
    from utils.network_mirrors import (
        apply_pip_mirror_env,
        apply_system_proxy_env,
        auto_fill_mirrors,
    )

    auto_fill_mirrors(shared.CONFIG_PATH)
    _early_index_url = apply_pip_mirror_env(shared.CONFIG_PATH)
    if _early_index_url:
        print(f"Using pip index: {_early_index_url}")

    _system_proxy = apply_system_proxy_env()
    if _system_proxy:
        print(f"Auto-detected system proxy: {_system_proxy}")

    # ── Ensure core requirements before GPU detection ─────────────────
    #     Must run BEFORE the GPU/CPU decision so that numpy, qtpy, etc.
    #     are available regardless of CPU mode.  If packages are missing,
    #     auto-install them and restart.
    from utils.core_requirements import ensure_core_requirements

    if ensure_core_requirements(APP_DIR):
        restart("core requirements installed", guard=True)
        return

    # ── GPU / CPU decision ────────────────────────────────────────────
    # Path A (one-click bundle embedded Python): BTRANSLATOR_GPU_MODE is
    #   set by launch.bat when NVIDIA GPU is detected.  We check whether
    #   this embedded Python itself has CUDA-capable torch (user ran
    #   install_cuda.bat).  If not → friendly hint + automatic CPU mode.
    #
    # Path B (user's own Python, or source run): BTRANSLATOR_GPU_MODE
    #   may or may not be set.  _detect_user_torch() checks the current
    #   process — if the user's Python has CUDA torch it works; if not,
    #   fall back to CPU.
    if not args.cpu:
        _gpu_requested = os.environ.get("BTRANSLATOR_GPU_MODE") == "1"
        if _gpu_requested:
            print("NVIDIA GPU detected — checking CUDA PyTorch availability...")

        if _gpu_requested or "ballontrans_pylibs_win" not in sys.executable:
            # Path A (GPU requested) or Path B (user Python):
            #   check if current Python has CUDA torch
            if _detect_user_torch():
                print("GPU mode: enabled")
            else:
                _is_embedded = "ballontrans_pylibs_win" in sys.executable
                if _is_embedded:
                    print("\n" + "=" * 60)
                    print("CUDA PyTorch not found in the bundled Python environment.")
                    print("To enable GPU acceleration, run: install_cuda.bat")
                    print("Or continue with CPU mode (no action needed).")
                    print("=" * 60 + "\n")
                print("Switching to CPU mode automatically.")
                args.cpu = True
                os.environ["BALLOONTRANS_CPU_ONLY"] = "1"
        # else: Path A without GPU requested → CPU mode by default

    # ── Basic logging and shared state (stdlib only, no third-party deps) ──
    from utils.logger import logger as LOGGER

    shared.args = args
    shared.HEADLESS = args.headless
    shared.load_cache()

    # ── Install missing dependencies first ──
    #     This must run BEFORE importing utils.config (which triggers numpy
    #     via utils.fontformat).  On fresh clones where system Python is used
    #     without the embedded bundle, numpy/PyQt6 aren't installed yet.
    #     Restart if anything was installed — gives a clean process where newly-
    #     installed packages are importable without stale module state.
    if prepare_environment():
        print("核心依赖已安装，正在重启以加载新环境...")
        restart("environment prepared", guard=True)

    # ── Deep probe: packages can satisfy metadata checks yet be broken ──
    #     Check the actual submodule imports the app uses (Pillow's C
    #     extensions are the usual DLL-load culprit) and force-reinstall any
    #     that fail.  This runs BEFORE the fatal core-import check so a broken
    #     binary gets a repair + restart chance instead of a dead end.
    _BROKEN = []
    if _probe_import("PIL.Image")[0] is None:
        _BROKEN.append("pillow")

    if _BROKEN:
        print("[WARN] Some core packages are installed but broken. Forcing reinstall ...")
        _pip = run_uv if UV_AVAILABLE else run_pip
        try:
            for _pkg in _BROKEN:
                _pip(f"install --force-reinstall {_pkg}", f"force-reinstall {_pkg}")
        except Exception as _e:
            # A failed repair must not abort startup; the user gets an
            # actionable command instead (the app can still run without the
            # optional JXL/Image extras in many cases).
            print(f"[WARN] Could not reinstall {', '.join(_BROKEN)}: {_e}")
            print(f"       Run manually: pip install --force-reinstall {' '.join(_BROKEN)}")
        else:
            restart("broken core packages reinstalled", guard=True)

    # ── Verify core imports after potential restart ──
    from utils.core_requirements import warn_missing_core_imports

    missing = warn_missing_core_imports()
    if missing:
        print()
        print("❌ 缺少核心依赖，无法启动。")
        print("   请运行以下命令安装依赖：")
        print(f"   pip install -r {args.requirements}")
        sys.exit(1)

    # ── Config and mirror setup (requires numpy/PyQt6) ──
    from utils import config as program_config

    # 自动镜像是本函数更早那一步（依赖安装之前）做的，这里不再重复。

    # Auto-detect system display language (only applies on first launch,
    # before any saved config.json exists — subsequent launches use the
    # persisted display_lang from config.json instead).
    from qtpy.QtCore import QLocale
    _sys_lang = QLocale.system().name().replace("en_CN", "zh_CN")
    if _sys_lang not in shared.VALID_LANG_SET:
        _sys_lang = "English"
    shared.DEFAULT_DISPLAY_LANG = _sys_lang

    program_config.load_config(args.config_path)
    config = program_config.pcfg

    # Apply mirror/registry settings from config so pip/uv use the correct
    # index and update checks use mirrors.
    from utils.mirror import patch_hf_env

    if config.mirror.pip_index_url:
        os.environ.setdefault("INDEX_URL", config.mirror.pip_index_url)
    if config.mirror.pip_extra_index_url:
        os.environ.setdefault("UV_EXTRA_INDEX_URL", config.mirror.pip_extra_index_url)
    if config.mirror.hf_endpoint:
        patch_hf_env(config.mirror.hf_endpoint)
    if config.mirror.github_mirror:
        os.environ.setdefault("GITHUB_MIRROR", config.mirror.github_mirror)
    # Re-read index_url so run_uv / run_pip pick it up
    global index_url
    index_url = os.environ.get("INDEX_URL", "")

    if args.update:
        if getattr(sys, "frozen", False):
            print("Running as app, skipping update.")
        else:
            print("Checking for updates...")
            try:
                current_commit = commit_hash()
                run(
                    f"{git} fetch origin {BRANCH}",
                    desc="Fetching updates from git...",
                    errdesc="Failed to fetch updates.",
                )
                latest_commit = run(f"{git} rev-parse origin/{BRANCH}").strip()

                if current_commit != latest_commit:
                    print("New updates found. Updating repository...")
                    run(
                        f"{git} pull origin {BRANCH}",
                        desc="Updating repository...",
                        errdesc="Failed to update repository.",
                    )
                    print("Repository updated. Restarting to apply updates...")
                    restart("repository updated", guard=True)
                    return
                else:
                    print("No updates found.")
            except Exception as e:
                print(f"Git update failed: {e}")
                print("Falling back to direct download...")
                try:
                    _check_script = osp.join(
                        osp.dirname(osp.abspath(__file__)),
                        "scripts",
                        "check_update.py",
                    )
                    subprocess.run(
                        [python, _check_script],
                        timeout=120,
                    )
                except Exception as e2:
                    print(f"Direct download also failed: {e2}")
                print("Continuing with the current version.")

    # ── No-model fallback: if torch is still unavailable after
    #     prepare_environment(), switch to "none" modules so the app
    #     remains usable without manual config changes. ──
    _ensure_module_fallback()

    # Install global Qt warning filter before any other Qt setup
    from utils.safe_qt import install_qt_warning_filter

    install_qt_warning_filter()

    from qtpy.QtCore import QEvent, QLocale, QObject, Qt, QTranslator
    from qtpy.QtWidgets import QComboBox

    if args.headless:
        config.module.load_model_on_demand = True
        config.module.empty_runcache = False

    if sys.platform == "win32":
        import ctypes

        myappid = "BallonsTranslatorLite"  # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    import qtpy
    from qtpy import API, QT_VERSION
    from qtpy.QtGui import QFont, QGuiApplication, QIcon
    from qtpy.QtWidgets import QApplication

    LOGGER.info(f"QT_API: {API}, QT Version: {QT_VERSION}")

    shared.DEBUG = args.debug
    shared.USE_PYSIDE6 = API == "pyside6"
    if qtpy.API_NAME[-1] == "6":
        shared.FLAG_QT6 = True
    else:
        shared.FLAG_QT6 = False
        QApplication.setAttribute(
            Qt.AA_EnableHighDpiScaling, True
        )  # enable high dpi scaling
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)  # use high dpi icons
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    os.chdir(shared.PROGRAM_PATH)

    app_args = sys.argv
    if args.headless:
        app_args = sys.argv + ["-platform", "offscreen"]
    app = QApplication(app_args)
    app.setApplicationName("BallonsTranslator-lite")
    app.setApplicationVersion(VERSION)

    # Global filter: prevent QComboBox from scrolling on hover without focus
    class _ComboBoxWheelFilter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Wheel and isinstance(obj, QComboBox):
                if not obj.hasFocus():
                    event.ignore()
                    return True
            return super().eventFilter(obj, event)

    app.installEventFilter(_ComboBoxWheelFilter(app))

    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries()

    if not args.headless:
        ps = QGuiApplication.primaryScreen()
        shared.LDPI = ps.logicalDotsPerInch()
        shared.SCREEN_W = ps.geometry().width()
        shared.SCREEN_H = ps.geometry().height()

    lang = config.display_lang
    # Load translations: try .qm first, then supplement with .ts via Python dict
    qmp = osp.join(shared.TRANSLATE_DIR, lang + ".qm")
    if osp.exists(qmp):
        translator = QTranslator()
        translator.load(lang, shared.TRANSLATE_DIR)
        app.installTranslator(translator)
    if lang not in ("en_US", "English") and not osp.exists(qmp):
        LOGGER.warning(f"target display language file {qmp} doesnt exist.")
    LOGGER.info(f"set display language to {lang}")

    # Check model file existence (registries are now available).  Must stay
    # AFTER the translator install: resolve() really imports the configured
    # module, and module-level QCoreApplication.translate tables (e.g.
    # utils/block_tags.py TAG_DEFS) would otherwise be frozen untranslated.
    _ensure_model_files_fallback()

    app_font = QFont("Microsoft YaHei UI")
    if not app_font.exactMatch():
        app_font = app.font()
    app_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app_font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
    )
    QGuiApplication.setFont(app_font)
    shared.DEFAULT_FONT_FAMILY = app_font.family()
    shared.APP_DEFAULT_FONT = app_font.family()

    if args.ldpi:
        shared.LDPI = args.ldpi

    setup_locks()

    from ui.mainwindow import MainWindow

    ballontrans = MainWindow(app, config, open_dir=args.proj_dir, **vars(args))
    global BT
    BT = ballontrans
    BT.restart_signal.connect(restart)

    if not args.headless:
        ballontrans.setWindowIcon(QIcon(shared.ICON_PATH))
        ballontrans.show()
        ballontrans.resetStyleSheet()
    sys.exit(app.exec())


def prepare_environment() -> bool:
    """GPU / torch dependency setup.

    Core requirements (numpy, qtpy, ...) are already handled by
    ``ensure_core_requirements()`` earlier in ``main()``.

    This function only handles the ``--reinstall-torch`` flag for
    force-reinstalling PyTorch with the appropriate CUDA version.
    Returns False (no restart needed from this function).
    """

    # Bundled portable Python manages its own dependencies
    if "ballontrans_pylibs_win" in sys.executable:
        return False

    if getattr(sys, "frozen", False):
        return False

    if args.frozen:
        return False

    # --reinstall-torch is only meaningful for non-embedded Python
    if not args.reinstall_torch:
        return False

    # Bootstrap uv (fast installer) — falls back to pip if unavailable
    ensure_uv()

    # Detect NVIDIA GPU architecture to pick the right CUDA version.
    # CUDA wheels are NVIDIA-only; without an NVIDIA GPU (e.g. macOS) there
    # is nothing to install from the PyTorch CUDA indexes, so torch should
    # be installed normally instead.
    _gpu_info = detect_gpu_info()
    if _gpu_info:
        print(_gpu_info["message"])
        if not _gpu_info["torch_index"]:
            # torch_index is None → GPU too old for CUDA PyTorch (e.g. Kepler)
            print("  Skipping CUDA PyTorch setup for this GPU.")
            return False
    elif sys.platform != "win32":
        print(
            "--reinstall-torch only handles NVIDIA CUDA PyTorch.\n"
            "  No NVIDIA GPU detected; install torch normally instead:\n"
            "    pip install torch torchvision torchaudio"
        )
        return False

    _torch_index = (_gpu_info or {}).get("torch_index") or "https://download.pytorch.org/whl/cu126"

    # Install the newest torch the chosen index serves, WITHOUT pinning a
    # version.  Pinning was actively harmful: the old hardcoded
    # ``torch==2.7.1`` downgraded users who already had a newer build, and
    # the version was not even present on every index (cu132 serves no
    # 2.7.1 at all, so the command simply failed).
    #
    # torchaudio is excluded for the same reason it is excluded from
    # install_cuda.bat: newer indexes do not ship it, and this app does
    # no audio I/O.
    torch_command = os.environ.get(
        "TORCH_COMMAND",
        f"uv pip install -U torch torchvision --index-url {_torch_index}",
    )

    run(
        f'"{python}" -m {torch_command}',
        "Installing torch and torchvision",
        "Couldn't install torch",
        live=True,
    )

    import importlib
    import site

    importlib.reload(site)

    return False


if __name__ == "__main__":
    main()
