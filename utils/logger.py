import datetime
import functools
import logging
import os
import os.path as osp
import sys
import threading
import traceback
from glob import glob

try:
    import termcolor

    _HAS_TERMCOLOR = True
except ImportError:
    _HAS_TERMCOLOR = False

if os.name == "nt":  # Windows
    try:
        import colorama

        colorama.init()
    except ImportError:
        pass


COLORS = {
    "WARNING": "yellow",
    "INFO": "white",
    "DEBUG": "blue",
    "CRITICAL": "red",
    "ERROR": "red",
}


class ColoredFormatter(logging.Formatter):
    def __init__(self, fmt, use_color=True):
        logging.Formatter.__init__(self, fmt)
        self.use_color = use_color

    def format(self, record):
        levelname = record.levelname
        use_color = self.use_color and _HAS_TERMCOLOR and levelname in COLORS

        if use_color:
            _colored = functools.partial(
                termcolor.colored,
                color=COLORS[levelname],
                attrs={"bold": True},
            )

            record.levelname2 = _colored("{:<7}".format(record.levelname))
            record.message2 = _colored(record.getMessage())

            asctime2 = datetime.datetime.fromtimestamp(record.created)
            record.asctime2 = termcolor.colored(asctime2, color="green")

            record.module2 = termcolor.colored(record.module, color="cyan")
            record.funcName2 = termcolor.colored(record.funcName, color="cyan")
            record.lineno2 = termcolor.colored(record.lineno, color="cyan")
        else:
            record.levelname2 = "{:<7}".format(record.levelname)
            record.message2 = record.getMessage()
            asctime2 = datetime.datetime.fromtimestamp(record.created)
            record.asctime2 = asctime2
            record.module2 = record.module
            record.funcName2 = record.funcName
            record.lineno2 = record.lineno
        return logging.Formatter.format(self, record)


FORMAT = "[%(levelname2)s] %(module2)s:%(funcName2)s:%(lineno2)s - %(message2)s"


class ColoredLogger(logging.Logger):
    def __init__(self, name):
        logging.Logger.__init__(self, name, logging.WARNING)

        color_formatter = ColoredFormatter(FORMAT)

        console = logging.StreamHandler()
        console.setFormatter(color_formatter)

        self.addHandler(console)
        return


def _trim_old_logs(logfile_dir: str, max_num_logs: int) -> int:
    """Delete the oldest ``*.log`` files so at most ``max_num_logs`` remain.

    Returns the number of files removed.  Never raises — a log directory the
    process cannot clean is not a reason to fail startup.
    """
    removed = 0
    try:
        old_logs = sorted(glob(osp.join(logfile_dir, "*.log")))
    except Exception:
        return 0
    if max_num_logs > 0 and len(old_logs) >= max_num_logs:
        to_remove = len(old_logs) - max_num_logs + 1
        for path in old_logs[:to_remove]:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


#: Attribute stamped on the file handler so repeated ``setup_logging`` calls
#: can recognise (and never duplicate) the one already attached.
_FILE_HANDLER_FLAG = "_bt_file_handler"

#: Path of the currently attached log file; empty until file logging is active.
_LOG_FILE_PATH = ""


def setup_logging(logfile_dir: str, max_num_logs: int = 14) -> bool:
    """Attach a per-run file handler to the application logger.

    Idempotent and failure-tolerant by design: callers invoke this on the
    startup path, so a missing/unwritable log directory or an unwritable file
    must degrade to console-only logging instead of raising.  Returns ``True``
    when file logging is active, ``False`` when it was skipped.
    """
    global _LOG_FILE_PATH

    if _LOG_FILE_PATH:
        # Already configured in this process — do not stack a second handler.
        return True

    try:
        os.makedirs(logfile_dir, exist_ok=True)
    except Exception as e:
        print(f"[logger] file logging disabled, cannot create {logfile_dir}: {e}")
        return False

    _trim_old_logs(logfile_dir, max_num_logs)

    logfilename = datetime.datetime.now().strftime("_%Y_%m_%d-%H_%M_%S.log")
    logfilep = osp.join(logfile_dir, logfilename)
    try:
        fh = logging.FileHandler(logfilep, mode="w", encoding="utf-8")
    except Exception as e:
        print(f"[logger] file logging disabled, cannot open {logfilep}: {e}")
        return False

    fh.setFormatter(
        logging.Formatter(
            ("[%(levelname)s] %(module)s:%(funcName)s:%(lineno)s - %(message)s")
        )
    )
    fh.setLevel(logging.DEBUG)
    setattr(fh, _FILE_HANDLER_FLAG, True)
    logger.addHandler(fh)
    _LOG_FILE_PATH = logfilep
    return True


# ── Uncaught-exception hooks ─────────────────────────────────────────
# Nothing else reports a main-thread crash after the window is up, and a
# worker-thread exception is swallowed by Qt's event loop.  Route both into
# the logger so the failure is diagnosable from ``logs/*.log``.
_hooks_installed = False
_original_sys_excepthook = None
_original_threading_excepthook = None


def _log_uncaught(exc_type, exc_value, exc_traceback, where: str) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        return
    try:
        logger.critical(
            f"Uncaught exception in {where}",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
    except Exception:
        # The logging call itself must never mask or replace the crash.
        try:
            traceback.print_exception(exc_type, exc_value, exc_traceback)
        except Exception:
            pass


def _handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    if not issubclass(exc_type, KeyboardInterrupt):
        _log_uncaught(exc_type, exc_value, exc_traceback, "main thread")
        return
    if _original_sys_excepthook is not None:
        _original_sys_excepthook(exc_type, exc_value, exc_traceback)


def _handle_thread_exception(args) -> None:
    exc_type = getattr(args, "exc_type", None)
    exc_traceback = getattr(args, "exc_traceback", None)
    if exc_type is None or exc_traceback is None:
        return
    if issubclass(exc_type, KeyboardInterrupt):
        if _original_threading_excepthook is not None:
            _original_threading_excepthook(args)
        return
    thread = getattr(args, "thread", None)
    name = getattr(thread, "name", "?") if thread is not None else "?"
    _log_uncaught(exc_type, getattr(args, "exc_value", None), exc_traceback, f"thread {name}")


def install_exception_hooks() -> bool:
    """Route uncaught main-thread / worker-thread exceptions into the logger.

    Idempotent; returns ``True`` when installed.  Never raises: hook setup or
    logging failing must not be able to break startup.
    """
    global _hooks_installed, _original_sys_excepthook, _original_threading_excepthook
    if _hooks_installed:
        return True
    try:
        _original_sys_excepthook = sys.excepthook
        sys.excepthook = _handle_uncaught_exception
        if hasattr(threading, "excepthook"):
            _original_threading_excepthook = threading.excepthook
            threading.excepthook = _handle_thread_exception
        _hooks_installed = True
        return True
    except Exception:
        return False


logging.setLoggerClass(ColoredLogger)
logger = logging.getLogger("BallonsTranslator-lite")
logger.setLevel(logging.DEBUG)
logger.propagate = False
