"""启动期文件日志与未捕获异常钩子的回归测试。

`utils/logger.py::setup_logging` 原先从未被调用，且一旦被调用就会：重复调用
就叠加 handler；日志目录不可写时直接抛异常冒泡到启动路径。`launch.py` 现在在
启动最早期接通它，因此这三条契约必须钉住：

1. 幂等 —— 重复调用不叠加文件 handler；
2. 降级 —— 日志目录不可写时返回 False 且不抛异常，控制台 handler 仍在；
3. 异常钩子 —— `sys.excepthook` / `threading.excepthook` 把未捕获异常写进日志，
   且 KeyboardInterrupt 仍走原钩子、日志自身失败也不得再抛。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_logger_hooks.py -v
"""

import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils import logger as btlogger  # noqa: E402


class _LoggerStateMixin:
    """快照 / 恢复 logger 的全局状态，避免用例互相污染。"""

    def setUp(self):
        self._saved_log_path = btlogger._LOG_FILE_PATH
        self._saved_handlers = list(btlogger.logger.handlers)

    def tearDown(self):
        for handler in list(btlogger.logger.handlers):
            if handler not in self._saved_handlers:
                btlogger.logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        btlogger._LOG_FILE_PATH = self._saved_log_path


class SetupLoggingTests(_LoggerStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Own temp dir with cleanup after tearDown (which closes handlers):
        # TemporaryDirectory's own context would try to delete the still-open
        # log file first on Windows.
        self._dir = tempfile.mkdtemp(prefix="bt_logger_test_")
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)

    def test_setup_logging_is_idempotent(self):
        self.assertTrue(btlogger.setup_logging(self._dir))
        self.assertEqual(len(self._file_handlers()), 1)

        self.assertTrue(btlogger.setup_logging(self._dir))
        self.assertEqual(len(self._file_handlers()), 1)

    def test_unwritable_dir_degrades_without_raising(self):
        # A *file* where the log directory should be — makedirs fails.
        blocker = Path(self._dir) / "logs"
        blocker.write_text("not a dir", encoding="utf8")

        self.assertFalse(btlogger.setup_logging(str(blocker)))
        self.assertEqual(self._file_handlers(), [])
        # Console logging must still work after the degrade.
        self.assertTrue(btlogger.logger.handlers)

    def test_trim_old_logs_keeps_window(self):
        for i in range(6):
            (Path(self._dir) / f"{i:02d}.log").write_text("x", encoding="utf8")
        btlogger._trim_old_logs(self._dir, 3)
        self.assertLessEqual(len(list(Path(self._dir).glob("*.log"))), 3)

    def _file_handlers(self):
        return [
            h
            for h in btlogger.logger.handlers
            if getattr(h, btlogger._FILE_HANDLER_FLAG, False)
        ]


class ExceptionHookTests(unittest.TestCase):
    def setUp(self):
        self._saved_sys_hook = sys.excepthook
        self._saved_thread_hook = getattr(threading, "excepthook", None)
        self._saved_installed = btlogger._hooks_installed
        btlogger._hooks_installed = False

    def tearDown(self):
        sys.excepthook = self._saved_sys_hook
        if self._saved_thread_hook is not None:
            threading.excepthook = self._saved_thread_hook
        btlogger._hooks_installed = self._saved_installed

    def test_install_routes_main_thread_exception_to_logger(self):
        self.assertTrue(btlogger.install_exception_hooks())
        self.assertIs(sys.excepthook, btlogger._handle_uncaught_exception)

        with self.assertLogs("BallonsTranslator-lite", level="CRITICAL") as cm:
            try:
                raise ValueError("boom-hook-marker")
            except ValueError:
                sys.excepthook(*sys.exc_info())

        self.assertTrue(any("boom-hook-marker" in line for line in cm.output))

    def test_install_is_idempotent(self):
        btlogger.install_exception_hooks()
        installed = sys.excepthook
        btlogger.install_exception_hooks()
        self.assertIs(sys.excepthook, installed)

    def test_keyboard_interrupt_is_delegated(self):
        btlogger.install_exception_hooks()
        seen = []
        btlogger._original_sys_excepthook = lambda *a: seen.append(a)
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        self.assertEqual(len(seen), 1)

    def test_thread_exception_is_logged(self):
        btlogger.install_exception_hooks()

        def _boom():
            raise RuntimeError("worker-boom-marker")

        with self.assertLogs("BallonsTranslator-lite", level="CRITICAL") as cm:
            worker = threading.Thread(target=_boom, name="bt-hook-test")
            worker.start()
            worker.join()

        self.assertTrue(any("bt-hook-test" in line for line in cm.output))
        self.assertTrue(any("worker-boom-marker" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
