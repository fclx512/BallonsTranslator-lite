import argparse
import importlib
import os
import os.path as osp
import re
import subprocess
import sys
from pathlib import Path
from platform import platform

BRANCH = "main"
VERSION = "beta-20260531-01"

python = sys.executable
git = os.environ.get("GIT", "git")
skip_install = False
index_url = os.environ.get("INDEX_URL", "")
QT_APIS = ["pyqt6", "pyside6", "pyqt5", "pyside2"]
stored_commit_hash = None
_gpu_info_cache = None

REQ_WIN = ["pywin32"]

PATH_ROOT = Path(__file__).parent

# Embedded Python's ._pth file overrides sys.path, so ensure project root is in path
if str(PATH_ROOT) not in sys.path:
    sys.path.insert(0, str(PATH_ROOT))

# Add portable site-packages to path (provides torchvision and other bundled deps for GPU mode)
_pylibs_sp = PATH_ROOT / "ballontrans_pylibs_win" / "Lib" / "site-packages"
if _pylibs_sp.exists() and str(_pylibs_sp) not in sys.path:
    sys.path.append(str(_pylibs_sp))

IS_WIN7 = "Windows-7" in platform()

import utils.shared as shared  # Earlier import of shared to use default for config_path argument

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


def is_installed(package):
    try:
        spec = importlib.util.find_spec(package)
    except ModuleNotFoundError:
        return False

    return spec is not None


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
    _user_python = None

    # Find system python.exe from PATH (exclude the bundled python itself)
    for _path_dir in os.environ.get("PATH", "").split(os.pathsep):
        _pexe = os.path.join(_path_dir, "python.exe")
        if os.path.exists(_pexe) and os.path.abspath(_pexe) != _current_python:
            _user_python = _pexe
            break

    if not _user_python:
        _user_python = "python.exe"  # fallback to PATH resolution

    try:
        _result = subprocess.run(
            [
                _user_python,
                "-c",
                "import torch; print(torch.__file__); print(torch.cuda.is_available())",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if _result.returncode != 0:
            print("No PyTorch found in user system Python.")
            return False

        _lines = _result.stdout.strip().split("\n")
        if len(_lines) < 2 or not _lines[0]:
            return False

        _torch_path = _lines[0]
        _cuda_available = _lines[1].strip() == "True"
        _site_packages = os.path.dirname(os.path.dirname(_torch_path))

        # Insert user's site-packages before bundled ones to override CPU torch
        if _site_packages not in sys.path:
            sys.path.insert(1, _site_packages)

        if _cuda_available:
            print("GPU mode: using user-installed PyTorch with CUDA")
        else:
            print(f"Found user PyTorch at: {_torch_path}")
            print("CUDA is not available in user-installed PyTorch.")
            _gpu_info = _detect_gpu_info()
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
        return True

    except subprocess.TimeoutExpired:
        print("Timeout checking user PyTorch installation.")
    except Exception as e:
        print(f"Could not detect user PyTorch: {e}")

    return False


def _detect_gpu_info():
    """Detect NVIDIA GPU via nvidia-smi and return architecture info.

    Returns a dict with keys: name, generation, recommended_cuda, torch_index, message.
    Returns None if no NVIDIA GPU is detected. Result is cached (nvidia-smi runs once).
    """
    global _gpu_info_cache
    if _gpu_info_cache is not None:
        return _gpu_info_cache

    try:
        _nvsmi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if _nvsmi.returncode != 0 or not _nvsmi.stdout.strip():
            _gpu_info_cache = None
            return None
        _gpu_name = _nvsmi.stdout.strip()
    except Exception:
        _gpu_info_cache = None
        return None

    name_upper = _gpu_name.upper()

    # 1) Blackwell — RTX 50 series, needs CUDA 12.8+ nightly
    if any(
        n in name_upper
        for n in ["RTX 5090", "RTX 5080", "RTX 5070", "RTX 5060", "RTX 50"]
    ):
        info = dict(
            name=_gpu_name,
            generation="Blackwell",
            recommended_cuda="12.8+",
            torch_index="https://download.pytorch.org/whl/nightly/cu128",
            message=(
                f"Detected Blackwell GPU ({_gpu_name}) — "
                "requires CUDA 12.8+ (nightly PyTorch build)."
            ),
        )
        _gpu_info_cache = info
        return info

    # 2) Try to extract series number from consumer GPU names
    m = re.search(r"(?:RTX|GTX)\s*(\d+)", name_upper)
    if m:
        model = m.group(1)
        series = int(model[:2]) if len(model) == 4 else int(model[0])

        if series >= 40:
            info = dict(
                name=_gpu_name,
                generation="Ada Lovelace",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Ada Lovelace GPU ({_gpu_name}) — "
                    "CUDA 12.4 recommended."
                ),
            )
        elif series >= 30:
            info = dict(
                name=_gpu_name,
                generation="Ampere",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=f"Detected Ampere GPU ({_gpu_name}) — CUDA 12.4 recommended.",
            )
        elif series >= 20 or series == 16:
            info = dict(
                name=_gpu_name,
                generation="Turing",
                recommended_cuda="12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Turing GPU ({_gpu_name}) — "
                    "CUDA 12.4 is supported."
                ),
            )
        elif series >= 10:
            # Pascal — GTX 10 series. CUDA 12.4 works but older drivers
            # may only support CUDA 11.x; flag cu118 as fallback.
            info = dict(
                name=_gpu_name,
                generation="Pascal",
                recommended_cuda="11.8 / 12.4",
                torch_index="https://download.pytorch.org/whl/cu124",
                message=(
                    f"Detected Pascal GPU ({_gpu_name}) — older architecture.\n"
                    "  CUDA 12.4 is supported. If you have an older NVIDIA driver\n"
                    "  and CUDA 12.4 fails, try CUDA 11.8 instead:\n"
                    "    pip uninstall torch torchvision torchaudio -y\n"
                    "    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118"
                ),
            )
        elif series >= 8:
            # Maxwell — GTX 8xx / 9xx
            info = dict(
                name=_gpu_name,
                generation="Maxwell",
                recommended_cuda="11.8",
                torch_index="https://download.pytorch.org/whl/cu118",
                message=(
                    f"Detected Maxwell GPU ({_gpu_name}) — older architecture.\n"
                    "  CUDA 11.8 recommended. If it fails, try CPU mode:\n"
                    "    python launch.py --cpu\n"
                    "  Or install CUDA 11.8 PyTorch:\n"
                    "    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118"
                ),
            )
        else:
            # Kepler — GTX 6xx / 7xx, very old
            info = dict(
                name=_gpu_name,
                generation="Kepler",
                recommended_cuda="N/A",
                torch_index=None,
                message=(
                    f"Detected Kepler GPU ({_gpu_name}) — very old architecture.\n"
                    "  PyTorch 2.x may not support this GPU.\n"
                    "  Consider using CPU mode: python launch.py --cpu"
                ),
            )
        _gpu_info_cache = info
        return info

    # 3) Titan variants without RTX/GTX prefix
    if "TITAN RTX" in name_upper:
        info = dict(
            name=_gpu_name,
            generation="Turing",
            recommended_cuda="12.4",
            torch_index="https://download.pytorch.org/whl/cu124",
            message=f"Detected Turing GPU ({_gpu_name}) — CUDA 12.4 is supported.",
        )
    elif "TITAN" in name_upper:
        info = dict(
            name=_gpu_name,
            generation="Titan (legacy)",
            recommended_cuda="11.8",
            torch_index="https://download.pytorch.org/whl/cu118",
            message=(
                f"Detected legacy Titan GPU ({_gpu_name}) — older architecture.\n"
                "  CUDA 11.8 recommended."
            ),
        )
    else:
        info = dict(
            name=_gpu_name,
            generation="Unknown",
            recommended_cuda="12.4",
            torch_index="https://download.pytorch.org/whl/cu124",
            message=f"Detected GPU: {_gpu_name}",
        )

    _gpu_info_cache = info
    return info


BT = None
APP = None


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
    print(f"Version: {VERSION}")
    print(f"Branch: {BRANCH}")
    print(f"Commit hash: {commit}")

    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    os.chdir(APP_DIR)

    # GPU mode with bundled Python: detect user's system PyTorch with CUDA
    if not args.cpu and os.environ.get("BTRANSLATOR_GPU_MODE"):
        print("GPU mode: detecting user-installed PyTorch with CUDA...")
        if not _detect_user_torch():
            _gpu_info = _detect_gpu_info()
            print("\n" + "=" * 60)
            print("PyTorch with CUDA was not found in your system Python.")
            if _gpu_info and _gpu_info["generation"] == "Kepler":
                print(
                    "Your Kepler GPU may not be supported by PyTorch 2.x."
                )
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
                        "scripts", "check_update.py",
                    )
                    subprocess.run(
                        [python, _check_script],
                        timeout=120,
                    )
                except Exception as e2:
                    print(f"Direct download also failed: {e2}")
                print("Continuing with the current version.")

    from qtpy.QtCore import QLocale, Qt, QTranslator
    from qtpy.QtCore import QEvent, QObject
    from qtpy.QtWidgets import QComboBox

    from utils import config as program_config
    from utils.logger import logger as LOGGER

    shared.args = args
    shared.DEFAULT_DISPLAY_LANG = QLocale.system().name().replace("en_CN", "zh_CN")
    shared.HEADLESS = args.headless
    shared.load_cache()
    program_config.load_config(args.config_path)
    config = program_config.pcfg

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

    # import msl.loadlib (required by translators/trans_eztrans) before init QApplication
    # yield QWindowsContext: OleInitialize() failed on py3.10,
    from modules.base import TORCH_AVAILABLE, init_module_registries
    from modules.prepare_local_files import prepare_local_files_forall

    init_module_registries()
    prepare_local_files_forall()

    # Check for GPU architecture incompatibility (skip in CPU mode)
    if TORCH_AVAILABLE and not args.cpu:
        from modules.base import torch as _torch

        if hasattr(_torch, "cuda") and not _torch.cuda.is_available():
            _gpu_info = _detect_gpu_info()
            if _gpu_info:
                _gen = _gpu_info["generation"]
                _name = _gpu_info["name"]
                print("\n" + "=" * 60)
                print(
                    f"NOTE: GPU detected ({_name}, {_gen})"
                    " but CUDA is not available in the loaded PyTorch."
                )
                if _gen == "Blackwell":
                    print(
                        "\nBlackwell requires CUDA 12.8+ (nightly build).\n"
                        "To fix:\n"
                        "  pip uninstall torch torchvision torchaudio ultralytics -y\n"
                        "  python launch.py --reinstall-torch"
                    )
                elif _gen == "Kepler":
                    print(
                        "\nPyTorch 2.x may not support Kepler GPUs.\n"
                        "Consider using CPU mode: python launch.py --cpu"
                    )
                elif _gen in ("Maxwell", "Pascal"):
                    print(
                        "\nThis older GPU may need CUDA 11.8.\n"
                        "Try:\n"
                        "  pip install torch torchvision torchaudio"
                        " --index-url https://download.pytorch.org/whl/cu118"
                    )
                else:
                    _idx = _gpu_info.get("torch_index",
                                         "https://download.pytorch.org/whl/cu124")
                    print(
                        "\nInstall CUDA PyTorch:\n"
                        f"  pip install torch torchvision torchaudio"
                        f" --index-url {_idx}"
                    )
                print("=" * 60 + "\n")

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

    try:
        import packaging
    except ModuleNotFoundError:
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

    req_updated = False
    if sys.platform == "win32":
        for req in REQ_WIN:
            if not check_reqs([req]):
                run_pip(f"install {req}", req)
                req_updated = True

    # Detect NVIDIA GPU architecture to pick the right CUDA version
    _torch_index = "https://download.pytorch.org/whl/cu124"
    _gpu_info = _detect_gpu_info()
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
            f"pip install torch torchvision torchaudio --index-url {_torch_index} --disable-pip-version-check",
        )
    else:
        torch_command = os.environ.get(
            "TORCH_COMMAND",
            f"pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url {_torch_index} --disable-pip-version-check",
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
        run_pip(f"install -r {args.requirements}", "requirements")
        req_updated = True

    if req_updated:
        import site

        importlib.reload(site)


if __name__ == "__main__":
    main()
