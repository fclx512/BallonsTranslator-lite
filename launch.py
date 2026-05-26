from pathlib import Path
import sys
import argparse
import os.path as osp
import os
import importlib
import subprocess
from platform import platform

BRANCH = 'main'
VERSION = 'beta-20260526-02'

python = sys.executable
git = os.environ.get('GIT', "git")
skip_install = False
index_url = os.environ.get('INDEX_URL', "")
QT_APIS = ['pyqt6', 'pyside6', 'pyqt5', 'pyside2']
stored_commit_hash = None

REQ_WIN = [
    'pywin32'
]

PATH_ROOT=Path(__file__).parent

# Embedded Python's ._pth file overrides sys.path, so ensure project root is in path
if str(PATH_ROOT) not in sys.path:
    sys.path.insert(0, str(PATH_ROOT))

# Add portable site-packages to path (provides torchvision and other bundled deps for GPU mode)
_pylibs_sp = PATH_ROOT / 'ballontrans_pylibs_win' / 'Lib' / 'site-packages'
if _pylibs_sp.exists() and str(_pylibs_sp) not in sys.path:
    sys.path.append(str(_pylibs_sp))

IS_WIN7 = "Windows-7" in platform()

import utils.shared as shared # Earlier import of shared to use default for config_path argument

parser = argparse.ArgumentParser()
parser.add_argument("--reinstall-torch", action='store_true', help="launch.py argument: install the appropriate version of torch even if you have some version already installed")
parser.add_argument("--proj-dir", default='', type=str, help='Open project directory on startup')
if IS_WIN7:
    parser.add_argument("--qt-api", default='pyqt5', choices=QT_APIS, help='Set qt api')
else:
    parser.add_argument("--qt-api", default='pyqt6', choices=QT_APIS, help='Set qt api')
parser.add_argument("--debug", action='store_true')
parser.add_argument("--requirements", default='requirements.txt')
parser.add_argument("--headless", action='store_true', help='run without GUI')
parser.add_argument("--exec_dirs", default='', help='translation queue (project directories) separated by comma')
parser.add_argument("--pages", default='', help='page range to process (e.g. 1-5,7,9-12) when --exec_dirs is used')
parser.add_argument("--ldpi", default=None, type=float, help='logical dots perinch')
parser.add_argument("--export-translation-txt", action='store_true', help='save translation to txt file once RUN completed')
parser.add_argument("--export-source-txt", action='store_true', help='save source to txt file once RUN completed')
parser.add_argument("--frozen", action='store_true', help='run without checking requirements')
parser.add_argument("--update", action='store_true', help="Update the repository before launching") # Add argument --update
parser.add_argument("--config_path", default=shared.CONFIG_PATH, help='Config file to use for translation') # Named config_path to avoid conflict with existing name config
parser.add_argument('--cpu', action='store_true', help="Force CPU mode even if PyTorch with CUDA is available")
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
        result = subprocess.run(command, shell=True, env=os.environ if custom_env is None else custom_env)
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or 'Error running command'}.
Command: {command}
Error code: {result.returncode}""")

        return ""

    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, env=os.environ if custom_env is None else custom_env)

    if result.returncode != 0:

        message = f"""{errdesc or 'Error running command'}.
Command: {command}
Error code: {result.returncode}
stdout: {result.stdout.decode(encoding="utf8", errors="ignore") if len(result.stdout)>0 else '<empty>'}
stderr: {result.stderr.decode(encoding="utf8", errors="ignore") if len(result.stderr)>0 else '<empty>'}
"""
        raise RuntimeError(message)

    return result.stdout.decode(encoding="utf8", errors="ignore")


def run_pip(args, desc=None):
    if skip_install:
        return

    index_url_line = f' --index-url {index_url}' if index_url != '' else ''
    return run(f'"{python}" -m pip {args} --prefer-binary{index_url_line} --disable-pip-version-check --no-warn-script-location', desc=f"Installing {desc}", errdesc=f"Couldn't install {desc}", live=True)


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
    for _path_dir in os.environ.get('PATH', '').split(os.pathsep):
        _pexe = os.path.join(_path_dir, 'python.exe')
        if os.path.exists(_pexe) and os.path.abspath(_pexe) != _current_python:
            _user_python = _pexe
            break

    if not _user_python:
        _user_python = 'python.exe'  # fallback to PATH resolution

    try:
        _result = subprocess.run(
            [_user_python, '-c',
             'import torch; print(torch.__file__); print(torch.cuda.is_available())'],
            capture_output=True, text=True, timeout=30
        )
        if _result.returncode != 0:
            print('No PyTorch found in user system Python.')
            return False

        _lines = _result.stdout.strip().split('\n')
        if len(_lines) < 2 or not _lines[0]:
            return False

        _torch_path = _lines[0]
        _cuda_available = _lines[1].strip() == 'True'
        _site_packages = os.path.dirname(os.path.dirname(_torch_path))

        # Insert user's site-packages before bundled ones to override CPU torch
        if _site_packages not in sys.path:
            sys.path.insert(1, _site_packages)

        if _cuda_available:
            print(f'GPU mode: using user-installed PyTorch with CUDA')
        else:
            print(f'Found user PyTorch at: {_torch_path}')
            print('CUDA is not available in user-installed PyTorch.')
            print('Consider installing PyTorch with CUDA for GPU acceleration.')

        print(f'  PyTorch: {_torch_path}')
        print(f'  Site-packages: {_site_packages}')
        return True

    except subprocess.TimeoutExpired:
        print('Timeout checking user PyTorch installation.')
    except Exception as e:
        print(f'Could not detect user PyTorch: {e}')

    return False


BT = None
APP = None

def restart():
    global BT
    print('restarting...\n')
    if BT:
        BT.close()
    os.execv(sys.executable, ['python'] + sys.argv)


def setup_locks():
    from utils.lock import RUNTIME_LOCKS
    from qtpy.QtCore import QMutex
    RUNTIME_LOCKS['model_loading'] = QMutex()


def main():

    if args.debug:
        os.environ['BALLOONTRANS_DEBUG'] = '1'

    if args.cpu:
        os.environ['BALLOONTRANS_CPU_ONLY'] = '1'
        print('CPU mode forced via --cpu flag')

    os.environ['QT_API'] = args.qt_api

    commit = commit_hash()

    print('Python version: ', sys.version)
    print('Python executable: ', sys.executable)
    print(f'Version: {VERSION}')
    print(f'Branch: {BRANCH}')
    print(f"Commit hash: {commit}")

    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    os.chdir(APP_DIR)

    # GPU mode with bundled Python: detect user's system PyTorch with CUDA
    if not args.cpu and os.environ.get('BTRANSLATOR_GPU_MODE'):
        print('GPU mode: detecting user-installed PyTorch with CUDA...')
        if not _detect_user_torch():
            print('\n' + '=' * 60)
            print('PyTorch with CUDA was not found in your system Python.')
            print('GPU mode requires PyTorch with CUDA support.')
            print('')
            print('To install manually:')
            print('  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124')
            print('')
            print('Switching to CPU mode automatically.')
            print('=' * 60 + '\n')
            args.cpu = True
            os.environ['BALLOONTRANS_CPU_ONLY'] = '1'

    prepare_environment()

    if args.update:
        if getattr(sys, 'frozen', False):
            print('Running as app, skipping update.')
        else:
            print('Checking for updates...')
            try:
                current_commit = commit_hash()
                run(f"{git} fetch origin {BRANCH}", desc="Fetching updates from git...", errdesc="Failed to fetch updates.")
                latest_commit = run(f"{git} rev-parse origin/{BRANCH}").strip()

                if current_commit != latest_commit:
                    print("New updates found. Updating repository...")
                    run(f"{git} pull origin {BRANCH}", desc="Updating repository...", errdesc="Failed to update repository.")
                    print("Repository updated. Restarting to apply updates...")
                    restart()
                    return
                else:
                    print("No updates found.")
            except Exception as e:
                print(f"Update check failed: {e}")
                print("Continuing with the current version.")


    from utils.logger import logger as LOGGER
    from utils import config as program_config

    from qtpy.QtCore import QTranslator, QLocale, Qt

    shared.args = args
    shared.DEFAULT_DISPLAY_LANG = QLocale.system().name().replace('en_CN', 'zh_CN')
    shared.HEADLESS = args.headless
    shared.load_cache()
    program_config.load_config(args.config_path)
    config = program_config.pcfg

    if args.headless:
        config.module.load_model_on_demand = True
        config.module.empty_runcache = False

    if sys.platform == 'win32':
        import ctypes
        myappid = u'BallonsTranslatorLite' # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    import qtpy
    from qtpy.QtWidgets import QApplication
    from qtpy.QtGui import QIcon, QGuiApplication, QFont
    from qtpy import API, QT_VERSION

    LOGGER.info(f'QT_API: {API}, QT Version: {QT_VERSION}')

    shared.DEBUG = args.debug
    shared.USE_PYSIDE6 = API == 'pyside6'
    if qtpy.API_NAME[-1] == '6':
        shared.FLAG_QT6 = True
    else:
        shared.FLAG_QT6 = False
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True) #enable high dpi scaling
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True) #use high dpi icons
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    os.chdir(shared.PROGRAM_PATH)

    app_args = sys.argv
    if args.headless:
        app_args = sys.argv + ['-platform', 'offscreen']
    app = QApplication(app_args)
    app.setApplicationName('BallonsTranslator-lite')
    app.setApplicationVersion(VERSION)

    # import msl.loadlib (required by translators/trans_eztrans) before init QApplication
    # yield QWindowsContext: OleInitialize() failed on py3.10, 
    from modules.base import init_module_registries, TORCH_AVAILABLE
    from modules.prepare_local_files import prepare_local_files_forall
    init_module_registries()
    prepare_local_files_forall()

    # Check for Blackwell GPU incompatibility (skip in CPU mode)
    if TORCH_AVAILABLE and not args.cpu:
        from modules.base import torch as _torch
        if hasattr(_torch, 'cuda') and not _torch.cuda.is_available():
            try:
                _nvsmi = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=10
                )
                _gpu_name = _nvsmi.stdout.strip()
                if any(name in _gpu_name for name in ["RTX 5090", "RTX 5080", "RTX 5070", "RTX 5060", "RTX 50"]):
                    print("\n" + "=" * 60)
                    print(f"WARNING: Detected Blackwell GPU ({_gpu_name}) but CUDA is not available!")
                    print("The installed PyTorch was compiled for an older CUDA version")
                    print("that does not support Blackwell (RTX 50 series) GPUs.")
                    print("")
                    print("To fix, reinstall PyTorch with CUDA 12.8+ support:")
                    print("  pip uninstall torch torchvision torchaudio ultralytics -y")
                    print("  python launch.py --reinstall-torch")
                    print("=" * 60 + "\n")
            except Exception:
                pass

    if not args.headless:
        ps = QGuiApplication.primaryScreen()
        shared.LDPI = ps.logicalDotsPerInch()
        shared.SCREEN_W = ps.geometry().width()
        shared.SCREEN_H = ps.geometry().height()

    lang = config.display_lang
    # Load translations: try .qm first, then supplement with .ts via Python dict
    qmp = osp.join(shared.TRANSLATE_DIR, lang + '.qm')
    if osp.exists(qmp):
        translator = QTranslator()
        translator.load(lang, shared.TRANSLATE_DIR)
        app.installTranslator(translator)
    if lang not in ('en_US', 'English') and not osp.exists(qmp):
        LOGGER.warning(f'target display language file {qmp} doesnt exist.')
    LOGGER.info(f'set display language to {lang}')

    app_font = QFont('Microsoft YaHei UI')
    if not app_font.exactMatch():
        app_font = app.font()
    app_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias)
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
        run_pip(f"install packaging", "install packaging")

    from utils.package import check_req_file, check_reqs

    if getattr(sys, 'frozen', False):
        print('Running as app, skip dependency installation')
        return

    if args.frozen:
        return

    # In CPU mode, all dependencies are bundled in the portable environment
    if args.cpu:
        return

    req_updated = False
    if sys.platform == 'win32':
        for req in REQ_WIN:
            if not check_reqs([req]):
                run_pip(f"install {req}", req)
                req_updated = True

    # Detect NVIDIA GPU architecture to pick the right CUDA version
    _torch_index = "https://download.pytorch.org/whl/cu124"
    try:
        _nvsmi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        _gpu_name = _nvsmi.stdout.strip()
        # Blackwell (RTX 50 series) needs CUDA 12.8+
        if any(name in _gpu_name for name in ["RTX 5090", "RTX 5080", "RTX 5070", "RTX 5060", "RTX 50"]):
            _torch_index = "https://download.pytorch.org/whl/nightly/cu128"
            print(f"Detected Blackwell GPU ({_gpu_name}), using CUDA 12.8+ PyTorch")
        else:
            print(f"Detected GPU: {_gpu_name}")
    except Exception:
        pass
    if "nightly" in _torch_index:
        torch_command = os.environ.get('TORCH_COMMAND', f"pip install torch torchvision torchaudio --index-url {_torch_index} --disable-pip-version-check")
    else:
        torch_command = os.environ.get('TORCH_COMMAND', f"pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url {_torch_index} --disable-pip-version-check")
    if args.reinstall_torch:
        run(f'"{python}" -m {torch_command}', "Installing torch and torchvision", "Couldn't install torch", live=True)
        req_updated = True

    if not check_req_file(args.requirements):
        run_pip(f"install -r {args.requirements}", "requirements")
        req_updated = True

    if req_updated:
        import site
        importlib.reload(site)





if __name__ == '__main__':
    main()
