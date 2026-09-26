"""自动重启守卫与核心依赖复检的回归测试。

`launch.py` 的启动重启用 ``os.execv`` 顶替进程：如果依赖安装"成功"但导入仍不
可用，旧代码会无限 exec 循环。本轮加了两处闸门：

1. ``launch.py::ensure_core_requirements`` 安装后在本进程复检，仍然失败时返回
   False（调用方不再重启）；
2. ``launch.py::restart(guard=True)`` 给自动重启计数，超过上限就拒绝，并给出
   可操作错误；用户手动触发的重启（``restart_signal``）不受计数限制。

同时钉住核心探针的边界：实际硬导入在内、模型后端不在。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_restart_guard.py -v
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import launch  # noqa: E402
from utils import core_requirements as core  # noqa: E402


class RestartGuardTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(launch.RESTART_COUNT_ENV, None)

    def tearDown(self):
        os.environ.pop(launch.RESTART_COUNT_ENV, None)
        if self._saved is not None:
            os.environ[launch.RESTART_COUNT_ENV] = self._saved

    def test_count_parsing_tolerates_garbage(self):
        os.environ[launch.RESTART_COUNT_ENV] = "2"
        self.assertEqual(launch._restart_count(), 2)
        os.environ[launch.RESTART_COUNT_ENV] = "garbage"
        self.assertEqual(launch._restart_count(), 0)
        os.environ.pop(launch.RESTART_COUNT_ENV, None)
        self.assertEqual(launch._restart_count(), 0)

    def test_guarded_restart_increments_counter(self):
        os.environ[launch.RESTART_COUNT_ENV] = "0"
        with mock.patch.object(launch.os, "execv") as execv:
            self.assertTrue(launch.restart("unit-test", guard=True))
        execv.assert_called_once()
        self.assertEqual(os.environ[launch.RESTART_COUNT_ENV], "1")

    def test_guard_stops_after_limit(self):
        os.environ[launch.RESTART_COUNT_ENV] = str(launch.MAX_STARTUP_RESTARTS)
        with mock.patch.object(launch.os, "execv") as execv:
            self.assertFalse(launch.restart("unit-test", guard=True))
        execv.assert_not_called()

    def test_manual_restart_is_not_guarded(self):
        os.environ[launch.RESTART_COUNT_ENV] = str(launch.MAX_STARTUP_RESTARTS)
        with mock.patch.object(launch.os, "execv") as execv:
            self.assertTrue(launch.restart())
        execv.assert_called_once()
        # Manual restart must not bump (or reset) the automatic counter.
        self.assertEqual(
            os.environ[launch.RESTART_COUNT_ENV], str(launch.MAX_STARTUP_RESTARTS)
        )


def _fake_install_result(ok=True):
    return SimpleNamespace(ok=ok, command_text="", returncode=0, stderr="")


class CoreRequirementsReprobeTests(unittest.TestCase):
    """安装成功后必须复检；仍不可导入时不得让调用方重启。"""

    def _run(self, probe_results):
        results = iter(probe_results)
        with mock.patch.object(
            core, "check_core_imports", side_effect=lambda *a, **k: next(results)
        ), mock.patch.object(
            core, "_install_packages", lambda **k: _fake_install_result()
        ), mock.patch.object(
            core, "_write_constraints_snapshot", lambda: ("", 0)
        ), mock.patch.object(
            core, "_drop_probe_modules", lambda probes: None
        ):
            return core.ensure_core_requirements(repo_root=str(REPO_ROOT))

    def test_nothing_missing_skips_install(self):
        self.assertFalse(self._run([[]]))

    def test_install_then_importable_returns_restart(self):
        self.assertTrue(self._run([["  fake: missing"], []]))

    def test_still_missing_after_install_returns_no_restart(self):
        self.assertFalse(self._run([["  fake: missing"], ["  fake: still missing"]]))


class CoreProbeScopeTests(unittest.TestCase):
    def _probe_names(self):
        return {name for name, _ in core.CORE_IMPORT_PROBES}

    def test_hard_imports_are_probed(self):
        names = self._probe_names()
        for expected in ("shapely", "httpx", "pydantic", "yaml"):
            self.assertIn(expected, names)

    def test_model_backends_are_not_core(self):
        names = self._probe_names()
        for banned in ("torch", "ultralytics", "onnxruntime", "transformers", "numba"):
            self.assertNotIn(banned, names)


if __name__ == "__main__":
    unittest.main()
