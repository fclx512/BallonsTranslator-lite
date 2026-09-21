"""``utils/memory_release.py`` 的编排回归（**不碰真 CUDA**）。

"手动释放内存"＝卸载模型 → 交回工作集。这里锁的是**编排与降级**：步骤顺序、读数记录、
单步异常不外抛、以及"销毁 CUDA 上下文"那一步**不再存在**。真机数字与语义说明见模块
docstring 与技能 `windows-app-memory-attribution` §6。

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
        measure = _ScriptedMeasure([1000, 1500, 110], log)
        with mock.patch.object(
            memory_release, "return_working_set", _tick(log, "trim")
        ):
            report = release_memory(_tick(log, "unload"), measure=measure)

        self.assertEqual(log, ["measure", "unload", "measure", "trim", "measure"])
        self.assertEqual(report.before_mb, 1000.0)
        self.assertEqual(report.after_unload_mb, 1500.0)
        self.assertEqual(report.after_trim_mb, 110.0)
        self.assertTrue(report.unloaded)
        self.assertTrue(report.working_set_returned)
        self.assertEqual(report.after_mb, 110.0)
        self.assertEqual(report.freed_mb, 890.0)
        self.assertEqual(report.errors, [])

    def test_unload_reporting_failure_still_trims(self):
        """卸载回调返回 False 只影响读数与提示，不拦后续步骤（那步早就没有了）。"""
        log = []
        measure = _ScriptedMeasure([1000, 1000, 120], log)
        with mock.patch.object(
            memory_release, "return_working_set", _tick(log, "trim")
        ):
            report = release_memory(_tick(log, "unload", result=False), measure=measure)

        self.assertFalse(report.unloaded)
        self.assertIn("trim", log)
        self.assertTrue(report.working_set_returned)
        self.assertEqual(report.after_mb, 120.0)

    def test_unload_exception_is_swallowed(self):
        log = []
        measure = _ScriptedMeasure([1000, 1000, 120], log)

        def boom():
            raise RuntimeError("model still loaded")

        with mock.patch.object(
            memory_release, "return_working_set", _tick(log, "trim")
        ):
            report = release_memory(boom, measure=measure)

        self.assertFalse(report.unloaded)
        self.assertTrue(any("unload failed" in e for e in report.errors))
        self.assertTrue(report.working_set_returned)

    def test_trim_exception_is_swallowed(self):
        measure = _ScriptedMeasure([1000, 1000, 1000])

        def boom():
            raise OSError("EmptyWorkingSet failed")

        with mock.patch.object(memory_release, "return_working_set", boom):
            report = release_memory(lambda: True, measure=measure)

        self.assertFalse(report.working_set_returned)
        self.assertTrue(any("working set trim failed" in e for e in report.errors))
        self.assertEqual(report.after_mb, 1000.0)

    def test_missing_unload_callable_is_recorded_as_none(self):
        measure = _ScriptedMeasure([1000, 1000, 120])
        with mock.patch.object(memory_release, "return_working_set", lambda: True):
            report = release_memory(None, measure=measure)

        self.assertIsNone(report.unloaded)
        self.assertTrue(report.working_set_returned)

    def test_trim_can_be_disabled(self):
        measure = _ScriptedMeasure([1000, 990])
        report = release_memory(
            lambda: True, trim_working_set=False, measure=measure
        )
        self.assertIsNone(report.after_trim_mb)
        self.assertFalse(report.working_set_returned)
        self.assertEqual(report.after_mb, 990.0)


class ReleaseReportTest(unittest.TestCase):
    def test_empty_report_falls_back_to_before(self):
        report = ReleaseReport(before_mb=500.0)
        self.assertEqual(report.after_mb, 500.0)
        self.assertEqual(report.freed_mb, 0.0)

    def test_freed_never_negative(self):
        report = ReleaseReport(before_mb=100.0, after_trim_mb=300.0)
        self.assertEqual(report.freed_mb, 0.0)

    def test_after_prefers_trim_then_unload(self):
        report = ReleaseReport(before_mb=1000.0, after_unload_mb=900.0)
        self.assertEqual(report.after_mb, 900.0)
        report.after_trim_mb = 700.0
        self.assertEqual(report.after_mb, 700.0)


class PlatformHelpersTest(unittest.TestCase):
    def test_working_set_is_positive_on_windows(self):
        value = working_set_mb()
        if os.name == "nt":
            self.assertGreater(value, 0.0)
        else:
            self.assertEqual(value, 0.0)

    def test_return_working_set_returns_bool(self):
        self.assertIsInstance(return_working_set(), bool)


class RemovedCapabilityTest(unittest.TestCase):
    """钉住"别把销毁 CUDA 上下文加回来"这个决定（2026-09-20 用户拍板）。

    实测（`scripts/probes/release_cuda_context_aftermath.py`）：`cudaDeviceReset`
    **不可逆地毁掉本进程的 CUDA**——`torch.cuda.is_available()` 仍返回 True 骗人，
    第一次真实分配报 `cudaErrorInvalidValue`，同一段代码另一次直接段错误
    （exit `0xC0000005`），进程内救不回来（只能重启）。它真还的内存只有 ~70~170MB，
    代价是"本次会话的 GPU 模型全废"；用户实测后确认"正常交回就够"。
    原委：`utils/memory_release.py` docstring、`docs/技术实现/内存释放_设计与实现_存档.md` §4。
    """

    def test_reset_api_is_intentionally_absent(self):
        for name in ("release_cuda_context", "cuda_context_destroyed"):
            self.assertFalse(
                hasattr(memory_release, name),
                f"{name} 是刻意删掉的（见本类 docstring），别加回来",
            )

    def test_release_memory_has_no_reset_knob(self):
        report = release_memory(trim_working_set=False, measure=lambda: 1.0)
        self.assertFalse(hasattr(report, "context_reset"))
        self.assertFalse(hasattr(report, "after_reset_mb"))


if __name__ == "__main__":
    unittest.main()
