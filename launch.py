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
VERSION = "beta-20260614"

python = sys.executable
git = os.environ.get("GIT", "git")
skip_install = False
index_url = os.environ.get("INDEX_URL", "")
QT_APIS = ["pyqt6", "pyside6", "pyqt5", "pyside2"]
stored_commit_hash = None

REQ_WIN = ["pywin32"]

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
        f'"{python}" -m uv pip {args} --prefer-binary{index_url_line} --disable-pip-version-check',
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
    """Find user's system Python with GPU PyTorch and inject into sys.path.

    When GPU mode runs with the bundled Python (ballontrans_pylibs_win), the bundled
    CPU-only torch would load by default. This function locates the user's
    system-installed PyTorch (with CUDA) and adds its site-packages path
    with higher priority than the bundled environment.
    """
    _current_python = os.path.abspath(sys.executable)
    _candidates: list[str] = []
    _seen: set[str] = set()

    # Collect all python*.exe from PATH directories (excluding bundled python)
    for _path_dir in os.environ.get("PATH", "").split(os.pathsep):
        for _name in ("python.exe", "python3.exe", "python3.13.exe"):
            _pexe = os.path.join(_path_dir, _name)
            _norm = os.path.abspath(_pexe)
            if (
                os.path.exists(_pexe)
                and _norm != _current_python
                and _norm not in _seen
            ):
                _candidates.append(_pexe)
                _seen.add(_norm)

    # Fallback: let OS resolve python.exe on PATH
    _candidates.append("python.exe")

    # Try each candidate until we find one with CUDA torch
    for _idx, _user_python in enumerate(_candidates):
        try:
            _result = subprocess.run(
                [
                    _user_python,
                    "-c",
                    "import torch; print(torch.__file__); print(torch.cuda.is_available()); "
                    "import sys; print('.'.join(map(str, sys.version_info[:2])))",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if _result.returncode != 0:
                continue

            _lines = _result.stdout.strip().split("\n")
            if len(_lines) < 3 or not _lines[0]:
                continue

            _torch_path = _lines[0]
            _cuda_available = _lines[1].strip() == "True"
            _torch_py_version = _lines[2].strip()
            _site_packages = os.path.dirname(os.path.dirname(_torch_path))

            # Skip if the torch was installed for a different Python version —
            # injecting incompatible C extensions (numpy, torch, etc.) will crash.
            _our_version = ".".join(map(str, sys.version_info[:2]))
            if _torch_py_version != _our_version:
                print(
                    f"  Skipping PyTorch at {_torch_path}"
                    f" (built for Python {_torch_py_version},"
                    f" running Python {_our_version})"
                )
                continue

            # Insert user's site-packages before bundled ones to override CPU torch
            if _site_packages not in sys.path:
                sys.path.insert(1, _site_packages)

            if _cuda_available:
                print("GPU mode: using user-installed PyTorch with CUDA")
                print(f"  PyTorch: {_torch_path}")
                print(f"  Site-packages: {_site_packages}")
                return True

            # Found torch but CUDA not available — keep this candidate for diagnosis
            print(f"Found user PyTorch at: {_torch_path}")
            print("CUDA is not available in user-installed PyTorch.")
            _gpu_info = detect_gpu_info()
            if _gpu_info:
                _gen = _gpu_info["generation"]
                if _gen == "Kepler":
                    print(
                        "  PyTorch 2.x may not support your Kepler GPU."
                        " Consider CPU mode instead."
                    )
                elif _gen == "Blackwell":
                    print(
                        "  Blackwell GPU requires CUDA 12.8+. Reinstall PyTorch:\n"
                        "    python launch.py --reinstall-torch"
                    )
                else:
                    print(
                        f"  Recommended CUDA {_gpu_info['recommended_cuda']}"
                        f" for your {_gen} GPU."
                    )
            else:
                print("Consider installing PyTorch with CUDA for GPU acceleration.")
            print(f"  PyTorch: {_torch_path}")
            print(f"  Site-packages: {_site_packages}")
            return False

        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue

    # No candidate had torch at all
    print("No PyTorch found in user system Python.")
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

    if _has_torch and _has_onnx:
        return  # everything available

    from utils.config import pcfg

    changed = []

    # ── Text detector: all real detectors need torch ──
    if not _has_torch and pcfg.module.textdetector not in ("none",):
        _old = pcfg.module.textdetector
        pcfg.module.textdetector = "none"
        changed.append(f"textdetector: {_old} → none")

    # ── OCR: selective fallback depending on what's missing ──
    _ocr = pcfg.module.ocr
    if _ocr not in ("none_ocr", "llm_ocr"):
        if _ocr == "mit48px_ctc" and not _has_torch:
            pcfg.module.ocr = "none_ocr"
            changed.append("OCR: mit48px_ctc → none_ocr")
        elif _ocr == "paddleocr_v6_onnx" and not _has_onnx:
            pcfg.module.ocr = "none_ocr"
            changed.append("OCR: paddleocr_v6_onnx → none_ocr (onnxruntime missing)")

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
                "  Install onnxruntime (or onnxruntime-gpu) + onnxocr"
                " to enable PP-OCRv6 ONNX, then restart."
            )
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

    # GPU mode with bundled Python: detect user's system PyTorch with CUDA
    if not args.cpu and os.environ.get("BTRANSLATOR_GPU_MODE"):
        print("GPU mode: detecting user-installed PyTorch with CUDA...")
        if not _detect_user_torch():
            _gpu_info = detect_gpu_info()
            print("\n" + "=" * 60)
            print("PyTorch with CUDA was not found in your system Python.")
            if _gpu_info and _gpu_info["generation"] == "Kepler":
                print("Your Kepler GPU may not be supported by PyTorch 2.x.")
                print("CPU mode will be used instead.")
            else:
                print("GPU mode requires PyTorch with CUDA support.")
                if _gpu_info and _gpu_info["torch_index"]:
                    print(
                        f"\nTo install for your {_gpu_info['generation']}"
                        f" GPU ({_gpu_info['name']}):\n"
                        f"  pip install torch torchvision torchaudio"
                        f" --index-url {_gpu_info['torch_index']}"
                    )
                else:
                    print(
                        "\nTo install manually:\n"
                        "  pip install torch torchvision torchaudio"
                        " --index-url https://download.pytorch.org/whl/cu124"
                    )
            print("\nSwitching to CPU mode automatically.")
            print("=" * 60 + "\n")
            args.cpu = True
            os.environ["BALLOONTRANS_CPU_ONLY"] = "1"

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

    # ── Early config load: mirror settings needed before deps/update ──
    from utils import config as program_config
    from utils.logger import logger as LOGGER

    shared.args = args
    shared.HEADLESS = args.headless
    shared.load_cache()
    program_config.load_config(args.config_path)
    config = program_config.pcfg

    # Apply mirror/registry settings from config BEFORE prepare_environment
    # and --update so pip/uv use the correct index and update checks use mirrors.
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

    prepare_environment()

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

    from qtpy.QtCore import QEvent, QLocale, QObject, Qt, QTranslator
    from qtpy.QtWidgets import QComboBox

    shared.DEFAULT_DISPLAY_LANG = QLocale.system().name().replace("en_CN", "zh_CN")

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


def prepare_environment():

    # When using the bundled portable Python (ballontrans_pylibs_win),
    # all dependencies are pre-installed — skip package management.
    # This portably-distributed Python does not have pip or uv.
    if "ballontrans_pylibs_win" in sys.executable:
        print("Running from portable Python environment, skip dependency installation")
        return

    import importlib.util

    if importlib.util.find_spec("packaging") is None:
        run_pip("install packaging", "install packaging")

    from utils.package import check_req_file, check_reqs

    if getattr(sys, "frozen", False):
        print("Running as app, skip dependency installation")
        return

    if args.frozen:
        return

    # In CPU mode, all dependencies are bundled in the portable environment
    if args.cpu:
        return

    # Bootstrap uv (fast installer) — falls back to pip if uv can't be installed
    ensure_uv()

    # Use uv for all subsequent package operations
    _pip = run_uv if UV_AVAILABLE else run_pip

    req_updated = False
    if sys.platform == "win32":
        for req in REQ_WIN:
            if not check_reqs([req]):
                _pip(f"install {req}", req)
                req_updated = True

    # Detect NVIDIA GPU architecture to pick the right CUDA version
    _torch_index = "https://download.pytorch.org/whl/cu124"
    _gpu_info = detect_gpu_info()
    if _gpu_info:
        print(_gpu_info["message"])
        if _gpu_info["torch_index"]:
            _torch_index = _gpu_info["torch_index"]
        else:
            # torch_index is None → GPU too old for CUDA PyTorch (e.g. Kepler)
            print("  Skipping CUDA PyTorch setup for this GPU.")
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
    if args.reinstall_torch:
        run(
            f'"{python}" -m {torch_command}',
            "Installing torch and torchvision",
            "Couldn't install torch",
            live=True,
        )
        req_updated = True

    if not check_req_file(args.requirements):
        _pip(f"install -r {args.requirements}", "requirements")
        req_updated = True

    if req_updated:
        import site

        importlib.reload(site)


if __name__ == "__main__":
    main()
