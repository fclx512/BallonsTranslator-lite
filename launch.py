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

import utils.shared as shared  # noqa: E402
from utils.env_diagnostic import detect_gpu_info  # noqa: E402

BRANCH = "main"
from utils.version import APP_VERSION as VERSION  # single source: pyproject.toml

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


def _uv_available():
    """Check if uv is available in the target Python interpreter."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "uv", "--version"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


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
    return run(
        f'"{python}" -m uv pip {args}{index_url_line} --disable-pip-version-check',
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


def _detect_user_torch():
    """Check if the current Python process has CUDA-capable PyTorch.

    Unlike the old implementation, this does NOT search other Pythons on
    the system.  It only checks ``import torch`` within the current process,
    then verifies with ``torch.cuda.is_available()``.

    Returns:
        True if CUDA-capable PyTorch is available in the current process.
        False otherwise.
    """
    try:
        import torch
    except ImportError:
        print("  PyTorch not installed in this Python environment.")
        return False

    if torch.cuda.is_available():
        print("  CUDA PyTorch available: " + str(torch.__file__))
        return True

    # torch exists but CUDA not available
    print("  PyTorch found but CUDA is not available.")
    _gpu_info = detect_gpu_info()
    if _gpu_info:
        _gen = _gpu_info["generation"]
        if _gen == "Kepler":
            print("  PyTorch 2.x may not support your Kepler GPU.")
        elif _gen == "Blackwell":
            print(
                "  Blackwell GPU requires CUDA 12.8+.\n"
                "    If using the one-click bundle, run install_cuda.bat.\n"
                "    Otherwise: pip install torch --index-url https://download.pytorch.org/whl/nightly/cu128"
            )
        else:
            print(
                f"  Recommended CUDA {_gpu_info['recommended_cuda']}"
                f" for your {_gen} GPU."
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
    inpainter      yes (all)       no                    ``none``
    mit48px_ctc    yes             no                    ``none_ocr``
    llm_ocr        no              no                    *(never)*
    translator     no              no                    *(never)*
    ============== =============== ===================== ==============
    """
    try:
        import torch  # noqa: F401

        _has_torch = True
    except ImportError:
        _has_torch = False

    try:
        import onnxruntime  # noqa: F401

        _has_onnx = True
    except ImportError:
        _has_onnx = False

    try:
        import onnxocr  # noqa: F401

        _has_onnxocr = True
    except ImportError:
        _has_onnxocr = False

    from utils.config import pcfg

    changed = []

    # ── Text detector: all real detectors need torch ──
    if not _has_torch and pcfg.module.textdetector not in ("none",):
        _old = pcfg.module.textdetector
        pcfg.module.textdetector = "none"
        changed.append(f"textdetector: {_old} → none")

    # ── OCR: rely on config default (none_ocr) or user's saved choice ──
    # Previously this block forcibly reset non-none/non-llm OCR modules back
    # to none_ocr; now we trust whatever the user configured.

    # ── Inpainter: all real inpainters need torch ──
    if not _has_torch and pcfg.module.inpainter not in ("none",):
        _old = pcfg.module.inpainter
        pcfg.module.inpainter = "none"
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

    from utils.config import pcfg
    from utils import shared

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
            changed.append(
                f"{_type}: {_module_name} → {_fallback} (model files missing)"
            )

    if changed:
        print("Model files not found — automatically switched to no-model modules:")
        for c in changed:
            print(f"  {c}")
        print()


def restart():
    global BT
    print("restarting...\n")
    if BT:
        BT.close()
    os.execv(sys.executable, ["python"] + sys.argv)


def setup_locks():
    from qtpy.QtCore import QMutex

    from utils.lock import RUNTIME_LOCKS

    RUNTIME_LOCKS["model_loading"] = QMutex()


def main():

    if args.debug:
        os.environ["BALLOONTRANS_DEBUG"] = "1"

    if args.cpu:
        os.environ["BALLOONTRANS_CPU_ONLY"] = "1"
        print("CPU mode forced via --cpu flag")

    os.environ["QT_API"] = args.qt_api

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
            print(f"  Switching to virtual environment Python...")
            os.execv(_venv_python, [_venv_python] + sys.argv)

    print(f"Version: {VERSION}")
    print(f"Branch: {BRANCH}")
    print(f"Commit hash: {commit}")

    # ── Ensure core requirements before GPU detection ─────────────────
    #     Must run BEFORE the GPU/CPU decision so that numpy, qtpy, etc.
    #     are available regardless of CPU mode.  If packages are missing,
    #     auto-install them and restart.
    from utils.core_requirements import ensure_core_requirements

    if ensure_core_requirements(APP_DIR):
        restart()
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

    # ── Auto-detect Windows system proxy ──
    if os.name == "nt" and not os.environ.get("HTTP_PROXY"):
        try:
            import platform

            if platform.system() == "Windows":
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                ) as key:
                    enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                    server = winreg.QueryValueEx(key, "ProxyServer")[0]
                    if enabled and server:
                        if not server.startswith("http://"):
                            server = f"http://{server}"
                        os.environ.setdefault("HTTP_PROXY", server)
                        os.environ.setdefault("HTTPS_PROXY", server)
                        os.environ.setdefault("http_proxy", server)
                        os.environ.setdefault("https_proxy", server)
                        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,.local")
                        print(f"Auto-detected system proxy: {server}")
        except Exception:
            pass  # non-fatal — user can set env vars manually

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
        restart()

    # ── Verify core imports after potential restart ──
    from utils.core_requirements import warn_missing_core_imports

    missing = warn_missing_core_imports()
    if missing:
        print()
        print("❌ 缺少核心依赖，无法启动。")
        print("   请运行以下命令安装依赖：")
        print(f"   pip install -r {args.requirements}")
        sys.exit(1)

    # ── Deep probe: some packages may satisfy metadata checks yet be broken ──
    #     Check the actual submodule imports the app uses and force-reinstall
    #     any that fail.  This runs AFTER the core-imports check so the more
    #     common case (package entirely missing) is handled first with a clear
    #     message above.
    _BROKEN = []
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        _BROKEN.append("pillow")

    if _BROKEN:
        print("[WARN] Some core packages are installed but broken. Forcing reinstall ...")
        _pip = run_uv if UV_AVAILABLE else run_pip
        for _pkg in _BROKEN:
            _pip(f"install --force-reinstall {_pkg}", f"force-reinstall {_pkg}")
        restart()

    # ── Config and mirror setup (requires numpy/PyQt6) ──
    from utils import config as program_config

    # Auto-detect network mirrors on first run (before config load, so the
    # written mirrors are picked up immediately).
    from utils.network_mirrors import auto_fill_mirrors

    auto_fill_mirrors(shared.CONFIG_PATH)

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
                    restart()
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

    # Check model file existence (registries are now available)
    _ensure_model_files_fallback()

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

    # Detect NVIDIA GPU architecture to pick the right CUDA version
    _gpu_info = detect_gpu_info()
    if _gpu_info:
        print(_gpu_info["message"])
        if not _gpu_info["torch_index"]:
            # torch_index is None → GPU too old for CUDA PyTorch (e.g. Kepler)
            print("  Skipping CUDA PyTorch setup for this GPU.")
            return False

    _torch_index = (_gpu_info or {}).get("torch_index") or "https://download.pytorch.org/whl/cu124"

    if "nightly" in _torch_index:
        torch_command = os.environ.get(
            "TORCH_COMMAND",
            f"uv pip install torch torchvision torchaudio --index-url {_torch_index}",
        )
    else:
        torch_command = os.environ.get(
            "TORCH_COMMAND",
            f"uv pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url {_torch_index}",
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
