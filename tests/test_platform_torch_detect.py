"""launch.py 平台加速器检测与旧 --reinstall-torch 安全兼容入口测试。

只测平台决策逻辑，不真实 import torch（用最小 fake module 注入 sys.modules），
也不触发真实 pip / nvidia-smi（mock detect_gpu_info / ensure_uv / run）。
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import launch  # noqa: E402 — repository path is needed by standalone unittest runs


def _fake_torch(cuda=False, mps=False, mps_attr=True):
    """构造最小 torch 替身。mps_attr=False 时 backends 上没有 mps 属性。"""
    backends = types.SimpleNamespace()
    if mps_attr:
        backends.mps = types.SimpleNamespace(is_available=lambda: mps)
    return types.SimpleNamespace(
        __file__="/fake/torch/__init__.py",
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=backends,
    )


class DetectTorchTests(unittest.TestCase):
    def _run(self, torch_mod, platform="linux"):
        with mock.patch.dict(sys.modules, {"torch": torch_mod}), mock.patch.object(
            sys, "platform", platform
        ), mock.patch.object(launch, "detect_gpu_info", lambda: None):
            return launch._detect_user_torch()

    def test_cuda_available_is_accelerated(self):
        self.assertTrue(self._run(_fake_torch(cuda=True), platform="win32"))

    def test_mps_available_is_accelerated(self):
        self.assertTrue(self._run(_fake_torch(cuda=False, mps=True), platform="darwin"))

    def test_no_accelerator_is_false(self):
        self.assertFalse(self._run(_fake_torch(cuda=False, mps=False), platform="darwin"))

    def test_missing_mps_attr_does_not_crash(self):
        self.assertFalse(
            self._run(_fake_torch(cuda=False, mps_attr=False), platform="linux")
        )

    def test_torch_missing_is_false(self):
        with mock.patch.dict(sys.modules, {"torch": None}), mock.patch.object(
            sys, "platform", "darwin"
        ):
            self.assertFalse(launch._detect_user_torch())


class BrokenTorchDegradeTests(unittest.TestCase):
    """坏 torch（DLL 加载失败）必须降级，而不是让异常冒泡出启动路径。"""

    def test_unloadable_torch_is_treated_as_absent(self):
        with mock.patch.object(
            launch, "_probe_import", lambda name: (None, "OSError: DLL load failed")
        ):
            self.assertFalse(launch._detect_user_torch())

    def test_accelerator_probe_exception_degrades_to_cpu(self):
        def _boom():
            raise OSError("cuda dll broken")

        torch_mod = types.SimpleNamespace(
            __file__="/fake/torch/__init__.py",
            cuda=types.SimpleNamespace(is_available=_boom),
        )
        with mock.patch.dict(sys.modules, {"torch": torch_mod}):
            self.assertFalse(launch._detect_user_torch())


class ReinstallTorchPlatformTests(unittest.TestCase):
    def _prepare(self, platform, gpu_info):
        args = types.SimpleNamespace(reinstall_torch=True, frozen=False)
        # 即便来自旧的源码启动命令，兼容参数也不得直接重装 CUDA 库。
        with mock.patch("sys.executable", "/usr/bin/python3"), mock.patch.object(
            sys, "platform", platform
        ), mock.patch.object(
            launch, "args", args
        ), mock.patch.object(launch, "detect_gpu_info", lambda: gpu_info), mock.patch.object(
            launch, "ensure_uv", lambda: None
        ), mock.patch.object(
            launch, "run", mock.MagicMock()
        ) as run_mock:
            ret = launch.prepare_environment()
        return ret, run_mock

    def test_non_windows_without_nvidia_skips_cuda(self):
        ret, run_mock = self._prepare("linux", None)
        self.assertFalse(ret)
        run_mock.assert_not_called()  # 未尝试 CUDA wheel 安装

    def test_windows_nvidia_legacy_flag_does_not_install(self):
        info = {"message": "x", "generation": "Ampere", "torch_index": "https://cu126"}
        ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_not_called()

    def test_legacy_flag_points_to_explicit_maintenance(self):
        """新硬件同样不能通过旧启动参数绕过完整包/CUDA 维护入口。"""
        info = {"message": "x", "generation": "Blackwell", "torch_index": "https://cu132"}
        with mock.patch("builtins.print") as output, \
             mock.patch("subprocess.run") as subprocess_run, \
             mock.patch("utils.package_installer.install") as install:
            ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_not_called()
        subprocess_run.assert_not_called()
        install.assert_not_called()
        self.assertIn("install_cuda.bat --install", output.call_args.args[0])

    def test_old_nvidia_no_index_skips(self):
        info = {"message": "old", "generation": "Kepler", "torch_index": None}
        ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
