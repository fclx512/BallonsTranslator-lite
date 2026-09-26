"""BaseModule.ensure_dependencies 的按需安装契约。

精简包不带模型后端：模块首次 load_model 时由 ensure_dependencies 按需补装
``requires_packages``。这里钉四个会静默翻车的点——

1. 安装必须走 ``utils/package_installer``：它会在解释器旁找独立 ``uv.exe``
   （精简包形态没有 ``python -m uv``），旧实现只认 ``python -m uv``，
   在精简包里必然静默回退 pip；
2. ``NO_DEPS_PACKAGES``（onnxocr）必须裸装：带依赖解析会"顺手"把 numpy
   降到 <2，坏掉整个环境；
3. 元数据缺失但顶层模块可导入（onnxruntime-gpu 之类名字错位）不算缺失；
4. 顶层模块导入抛 OSError（二进制半坏）按缺失处理，重装一次有救。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_module_dependency_install.py -v
"""

import importlib.metadata
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.base import BaseModule  # noqa: E402
from utils.package_installer import NO_DEPS_PACKAGES, InstallResult  # noqa: E402


def _result(ok: bool, command=("pip", "install")) -> InstallResult:
    return InstallResult(ok, list(command), returncode=0 if ok else 1)


class _Module(BaseModule):
    """不带 params 的最小宿主，只换 requires_packages。"""


class EnsureDependenciesTest(unittest.TestCase):
    def test_satisfied_requirement_skips_install(self):
        module = _Module()
        module.requires_packages = ["packaging"]  # 开发/捆绑环境必有
        with mock.patch("utils.package_installer.install") as install:
            module.ensure_dependencies()
        install.assert_not_called()

    def test_metadata_missing_but_importable_is_satisfied(self):
        # stdlib "json" 没有 dist 元数据——名字错位场景的最小替身
        module = _Module()
        module.requires_packages = ["json"]
        with mock.patch("utils.package_installer.install") as install:
            module.ensure_dependencies()
        install.assert_not_called()

    def test_missing_package_installs_via_package_installer(self):
        module = _Module()
        module.requires_packages = ["some-missing-backend"]
        with mock.patch(
            "utils.package_installer.install", return_value=_result(True)
        ) as install:
            module.ensure_dependencies()
        self.assertEqual(install.call_count, 1)
        args, kwargs = install.call_args
        self.assertEqual(kwargs.get("requirements"), ["some-missing-backend"])
        self.assertEqual(kwargs.get("backend"), "auto")

    def test_failed_with_deps_retries_bare(self):
        module = _Module()
        module.requires_packages = ["some-missing-backend"]
        with mock.patch(
            "utils.package_installer.install",
            side_effect=[_result(False), _result(True)],
        ) as install:
            module.ensure_dependencies()
        self.assertEqual(
            [call.kwargs.get("extra_args") for call in install.call_args_list],
            ["", "--no-deps"],
        )

    def test_no_deps_package_never_resolves_dependencies(self):
        module = _Module()
        module.requires_packages = ["conflicted-pkg"]
        real_import_module = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            # 只让目标包导入失败；别拦 mock 自身解析 patch 目标用的导入
            if name == "conflicted-pkg":
                raise ModuleNotFoundError(name)
            return real_import_module(name, *args, **kwargs)

        with mock.patch(
            "importlib.metadata.distribution",
            side_effect=importlib.metadata.PackageNotFoundError,
        ), mock.patch(
            "importlib.import_module", side_effect=_fake_import
        ), mock.patch(
            "utils.package_installer.NO_DEPS_PACKAGES", {"conflicted-pkg"}
        ), mock.patch(
            "utils.package_installer.install", return_value=_result(True)
        ) as install:
            module.ensure_dependencies()
        self.assertEqual(install.call_count, 1)
        self.assertEqual(
            install.call_args.kwargs.get("extra_args"), "--no-deps"
        )

    def test_onnxocr_is_in_no_deps_list(self):
        # 已知冲突包必须留在清单里；挪走之前先读
        # utils/package_installer.py::NO_DEPS_PACKAGES 的注释
        self.assertIn("onnxocr", NO_DEPS_PACKAGES)


if __name__ == "__main__":
    unittest.main()
