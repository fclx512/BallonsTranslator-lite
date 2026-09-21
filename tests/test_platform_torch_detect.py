"""launch.py 平台加速器检测与 --reinstall-torch 平台分支的单元测试。

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

import launch


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


class ReinstallTorchPlatformTests(unittest.TestCase):
    def _prepare(self, platform, gpu_info):
        args = types.SimpleNamespace(reinstall_torch=True, frozen=False)
        # prepare_environment 对捆绑解释器（路径含 ballontrans_pylibs_win）直接
        # 早退，仓库测试恰用该解释器，须伪装成普通路径才走得到分支逻辑。
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

    def test_windows_nvidia_selects_cuda_index(self):
        info = {"message": "x", "generation": "Ampere", "torch_index": "https://cu126"}
        ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_called_once()
        command = run_mock.call_args[0][0]
        self.assertIn("cu126", command)

    def test_reinstall_torch_does_not_pin_versions(self):
        """不得再钉死 pytorch 版本。

        旧实现硬编码 ``torch==2.7.1``：对已有更新 torch 的用户是降级，
        而且 cu132 索引根本不提供 2.7.1，命令必然失败。
        """
        info = {"message": "x", "generation": "Blackwell", "torch_index": "https://cu132"}
        ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_called_once()
        command = run_mock.call_args[0][0]
        self.assertNotIn("torch==", command, "仍在钉死 torch 版本：" + command)
        self.assertNotIn("torchvision==", command)
        self.assertNotIn("2.7.1", command)
        # torchaudio 不装（新索引不提供它，且本应用无音频 I/O）
        self.assertNotIn("torchaudio", command)

    def test_old_nvidia_no_index_skips(self):
        info = {"message": "old", "generation": "Kepler", "torch_index": None}
        ret, run_mock = self._prepare("win32", info)
        self.assertFalse(ret)
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
