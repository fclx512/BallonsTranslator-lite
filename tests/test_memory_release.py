"""``utils/memory_release.py`` 的编排回归（**不碰真 CUDA**）。

"手动释放内存"＝卸载模型 → 销毁 CUDA 上下文 → 交回工作集。这里锁的是**编排与
降级**：步骤顺序、读数记录、卸载失败时不做 reset、单步异常不外抛、`empty_cache`
必须排在 `cudaDeviceReset` 之前。真机数字与语义说明见模块 docstring 与技能
`windows-app-memory-attribution` §6。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_memory_release.py -q
"""

import os
import os.path as osp
import sys
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from utils import memory_release  # noqa: E402
from utils.memory_release import (  # noqa: E402
    ReleaseReport,
    release_cuda_context,
    release_memory,
    return_working_set,
    working_set_mb,
)


class _ScriptedMeasure:
    """按顺序返回预设读数的替身，同时把"读了一次"记进共享日志（断言步骤顺序）。"""

    def __init__(self, values, log=None):
        self._values = list(values)
        self._log = log

    def __call__(self):
        if self._log is not None:
            self._log.append("measure")
        if not self._values:
            return -1.0
        return float(self._values.pop(0))


def _tick(log, name, result=True):
    def _inner():
        log.append(name)
        return result

    return _inner


class ReleaseMemoryOrderTest(unittest.TestCase):
    def test_steps_run_in_order_and_numbers_are_recorded(self):
        log = []
        measure = _ScriptedMeasure([1000, 1500, 1300, 110], log)
        with mock.patch.object(
            memory_release, "release_cuda_context", _tick(log, "reset")
        ), mock.patch.object(
            memory_release, "return_working_set", _tick(log, "trim")
        ):
            report = release_memory(_tick(log, "unload"), measure=measure)

        self.assertEqual(
            log, ["measure", "unload", "measure", "reset", "measure", "trim", "measure"]
        )
        self.assertEqual(report.before_mb, 1000.0)
        self.assertEqual(report.after_unload_mb, 1500.0)
        self.assertEqual(report.after_reset_mb, 1300.0)
        self.assertEqual(report.after_trim_mb, 110.0)
        self.assertTrue(report.unloaded)
        self.assertTrue(report.context_reset)
        self.assertTrue(report.working_set_returned)
        self.assertEqual(report.after_mb, 110.0)
        self.assertEqual(report.freed_mb, 890.0)
        self.assertEqual(report.errors, [])

    def test_reset_is_skipped_when_unload_reports_failure(self):
        """卸载没成功 → 可能有活跃 CUDA 会话，**不能** reset（否则拿到失效指针）。"""
        log = []
        measure = _ScriptedMeasure([1000, 1000, 120], log)
        with mock.patch.object(
            memory_release, "release_cuda_context", _tick(log, "reset")
        ), mock.patch.object(
            memory_release, "return_working_set", _tick(log, "trim")
        ):
            report = release_memory(_tick(log, "unload", result=False), measure=measure)

        self.assertNotIn("reset", log)
        self.assertFalse(report.context_reset)
        self.assertIsNone(report.after_reset_mb)
        self.assertTrue(report.working_set_returned)
        self.assertTrue(any("skipped" in e for e in report.errors))

    def test_unload_exception_is_swallowed_and_blocks_reset(self):
        log = []
        measure = _ScriptedMeasure([1000, 1000, 120], log)

        def boom():
            raise RuntimeError("model still loaded")

        with mock.patch.object(
            memory_release, "release_cuda_context", _tick(log, "reset")
        ), mock.patch.object(memory_release, "return_working_set", _tick(log, "trim")):
            report = release_memory(boom, measure=measure)

        self.assertNotIn("reset", log)
        self.assertFalse(report.unloaded)
        self.assertTrue(any("unload failed" in e for e in report.errors))
        self.assertTrue(report.working_set_returned)

    def test_missing_unload_callable_still_resets(self):
        """调用方没给卸载回调（＝它自己保证没载东西）时，reset 照做。"""
        measure = _ScriptedMeasure([1000, 1000, 800, 120])
        with mock.patch.object(
            memory_release, "release_cuda_context", lambda: True
        ), mock.patch.object(memory_release, "return_working_set", lambda: True):
            report = release_memory(None, measure=measure)

        self.assertIsNone(report.unloaded)
        self.assertTrue(report.context_reset)

    def test_reset_failure_still_trims(self):
        measure = _ScriptedMeasure([1000, 1000, 900, 120])
        with mock.patch.object(
            memory_release, "release_cuda_context", lambda: False
        ), mock.patch.object(memory_release, "return_working_set", lambda: True):
            report = release_memory(lambda: True, measure=measure)

        self.assertFalse(report.context_reset)
        self.assertTrue(report.working_set_returned)
        self.assertEqual(report.after_mb, 120.0)

    def test_reset_exception_is_swallowed(self):
        measure = _ScriptedMeasure([1000, 1000, 1000, 120])

        def boom():
            raise OSError("cuda device lost")

        with mock.patch.object(
            memory_release, "release_cuda_context", boom
        ), mock.patch.object(memory_release, "return_working_set", lambda: True):
            report = release_memory(lambda: True, measure=measure)

        self.assertFalse(report.context_reset)
        self.assertTrue(any("context reset failed" in e for e in report.errors))
        self.assertTrue(report.working_set_returned)

    def test_steps_can_be_disabled(self):
        measure = _ScriptedMeasure([1000, 990])
        report = release_memory(
            lambda: True, reset_context=False, trim_working_set=False, measure=measure
        )
        self.assertIsNone(report.after_reset_mb)
        self.assertIsNone(report.after_trim_mb)
        self.assertEqual(report.after_mb, 990.0)


class ReleaseReportTest(unittest.TestCase):
    def test_empty_report_falls_back_to_before(self):
        report = ReleaseReport(before_mb=500.0)
        self.assertEqual(report.after_mb, 500.0)
        self.assertEqual(report.freed_mb, 0.0)

    def test_freed_never_negative(self):
        report = ReleaseReport(before_mb=100.0, after_trim_mb=300.0)
        self.assertEqual(report.freed_mb, 0.0)

    def test_after_prefers_trim_then_reset_then_unload(self):
        report = ReleaseReport(
            before_mb=1000.0, after_unload_mb=900.0, after_reset_mb=800.0
        )
        self.assertEqual(report.after_mb, 800.0)
        report.after_trim_mb = 700.0
        self.assertEqual(report.after_mb, 700.0)


class PlatformHelpersTest(unittest.TestCase):
    def test_working_set_is_positive_on_windows(self):
        value = working_set_mb()
        if os.name == "nt":
            self.assertGreater(value, 0.0)
        else:
            self.assertEqual(value, 0.0)

    def test_release_cuda_context_returns_false_without_torch(self):
        with mock.patch.object(memory_release, "_torch_module", lambda: None):
            self.assertFalse(release_cuda_context())

    def test_cache_is_emptied_before_reset_and_busy_device_aborts(self):
        """两条安全铁律：先 `empty_cache` 再 reset；设备忙则放弃。"""
        calls = []

        class _FakeCuda:
            @staticmethod
            def is_available():
                return True

            @staticmethod
            def synchronize():
                calls.append("synchronize")

            @staticmethod
            def empty_cache():
                calls.append("empty_cache")

            @staticmethod
            def ipc_collect():
                calls.append("ipc_collect")

        class _FakeTorch:
            cuda = _FakeCuda

        with mock.patch.object(memory_release, "_torch_module", lambda: _FakeTorch):
            with mock.patch.object(
                memory_release, "_find_cudart_library", lambda: None
            ):
                self.assertFalse(release_cuda_context())
        self.assertEqual(calls, ["synchronize", "empty_cache", "ipc_collect"])

        # 同步失败＝还有 CUDA 活在跑：必须原地放弃，不能去 reset
        class _BusyCuda(_FakeCuda):
            @staticmethod
            def synchronize():
                raise RuntimeError("device busy")

        class _BusyTorch:
            cuda = _BusyCuda

        with mock.patch.object(memory_release, "_torch_module", lambda: _BusyTorch):
            self.assertFalse(release_cuda_context())

    def test_return_working_set_returns_bool(self):
        self.assertIsInstance(return_working_set(), bool)


if __name__ == "__main__":
    unittest.main()
