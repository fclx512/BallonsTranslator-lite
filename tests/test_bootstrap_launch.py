"""精简包（bootstrap 发行）相关改动的回归测试。

用户 2026-09-20 拍板跟进上游发行策略：发「源码 + 嵌入式 Python + pip + uv.exe + 基本依赖」
的精简包，重依赖/模型按需安装（见 `scripts/build_win_minimal.ps1`）。这条路线把
四件原本被「预装好的一体包」掩盖的问题暴露出来，本文件逐个钉住：

1. `utils/network_mirrors.py::auto_fill_mirrors` 写错了配置节（自造的
   ``mirrors.pypi``，而读取点是 ``mirror.pip_index_url``），首次运行自动配镜像
   其实一直是空转；且首启动装依赖发生在 config 能加载之前，镜像必须更早落到
   环境变量上（`utils/network_mirrors.py::apply_pip_mirror_env`）。
2. 发行包把 ``uv.exe`` 放在 ``python.exe`` 旁边而不入 PATH，只看 PATH 会静默
   退回 pip（`utils/package_installer.py::find_uv`）。
3. ``launch.py`` 的自动降级（缺依赖/缺模型 → 模块换成 none）会**落盘**，把用户
   或默认的选择永久抹掉，而它的提示语还写着「then restart」——重启后配置已是
   none，那句话就落空（`utils/config.py::record_auto_downgrade`）。
4. 默认修复器 ``lama_large_512px`` 需要 torch 却没有声明，精简包里没人会装它
   （`modules/inpaint/base.py` 的 ``LamaLarge.requires_packages``）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_bootstrap_launch.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import config as program_config  # noqa: E402
from utils import network_mirrors  # noqa: E402
from utils import shared  # noqa: E402
from utils.package_installer import build_install_command, find_uv, resolve_backend  # noqa: E402


def _write_config(path: Path, mirror_section: dict) -> None:
    path.write_text(
        json.dumps({"mirror": mirror_section}, ensure_ascii=False), encoding="utf8"
    )


class TestAutoFillMirrorsWritesRealSection(unittest.TestCase):
    """自动补镜像必须写进 utils/config.py::MirrorConfig 真正读的节与键名。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self._tmp.name) / "config.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_mirror_section_with_config_field_names(self):
        original = network_mirrors.should_use_china_mirrors
        network_mirrors.should_use_china_mirrors = lambda: True
        try:
            updated = network_mirrors.auto_fill_mirrors(self.config_path)
        finally:
            network_mirrors.should_use_china_mirrors = original

        self.assertEqual(sorted(updated), ["huggingface", "pypi"])
        data = json.loads(Path(self.config_path).read_text(encoding="utf8"))
        self.assertIn("mirror", data)
        self.assertNotIn("mirrors", data)
        # 关键不变量：写进去的每个键都必须是 MirrorConfig 的真实字段——原先的
        # bug 正是自造了 mirrors.pypi 这种没有读取点的结构。
        valid_fields = set(program_config.MirrorConfig.__dataclass_fields__)
        written = set(data["mirror"])
        self.assertTrue(
            written <= valid_fields, f"unknown config keys written: {written - valid_fields}"
        )
        self.assertEqual(
            data["mirror"]["pip_index_url"], network_mirrors.DEFAULT_PYPI_MIRROR
        )
        self.assertEqual(
            data["mirror"]["hf_endpoint"], network_mirrors.DEFAULT_HUGGINGFACE_MIRROR
        )

    def test_absent_keys_are_filled_but_empty_values_are_left_alone(self):
        """空串是「用官方源」的显式选择（MirrorConfig docstring），不能覆盖。"""
        _write_config(Path(self.config_path), {"pip_index_url": ""})
        self.assertNotIn(
            "pypi", network_mirrors._mirror_fields_missing(self.config_path)
        )
        self.assertIn(
            "huggingface", network_mirrors._mirror_fields_missing(self.config_path)
        )

    def test_no_config_file_means_everything_is_missing(self):
        missing = network_mirrors._mirror_fields_missing(self.config_path)
        self.assertEqual(sorted(missing), ["huggingface", "pypi"])


class TestApplyPipMirrorEnv(unittest.TestCase):
    """首启动装核心依赖时，pip 源要能走到镜像（config 那时还读不了）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self._tmp.name) / "config.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_exports_index_url_from_raw_config(self):
        _write_config(
            Path(self.config_path),
            {
                "pip_index_url": "https://mirrors.example/simple",
                "pip_extra_index_url": "https://extra.example/simple",
            },
        )
        env = {}
        url = network_mirrors.apply_pip_mirror_env(self.config_path, env=env)
        self.assertEqual(url, "https://mirrors.example/simple")
        self.assertEqual(env["INDEX_URL"], "https://mirrors.example/simple")
        self.assertEqual(env["UV_EXTRA_INDEX_URL"], "https://extra.example/simple")

    def test_does_not_override_an_explicit_env_value(self):
        _write_config(Path(self.config_path), {"pip_index_url": "https://mirror.example"})
        env = {"INDEX_URL": "https://from-launch-bat.example"}
        url = network_mirrors.apply_pip_mirror_env(self.config_path, env=env)
        self.assertEqual(url, "https://mirror.example")
        self.assertEqual(env["INDEX_URL"], "https://from-launch-bat.example")

    def test_empty_when_nothing_configured(self):
        env = {}
        self.assertEqual(
            network_mirrors.apply_pip_mirror_env(self.config_path, env=env), ""
        )
        self.assertNotIn("INDEX_URL", env)


class TestSystemProxyHelpers(unittest.TestCase):
    """Windows 系统代理探测抽成 stdlib 辅助函数后的契约。"""

    def test_normalize_proxy_server_forms(self):
        self.assertEqual(
            network_mirrors.normalize_proxy_server("127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            network_mirrors.normalize_proxy_server("http://127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            network_mirrors.normalize_proxy_server(
                "http=10.0.0.1:8080;https=10.0.0.1:8443"
            ),
            "http://10.0.0.1:8080",
        )
        self.assertEqual(network_mirrors.normalize_proxy_server(""), "")

    def test_detect_uses_injected_reader_and_tolerates_failure(self):
        self.assertEqual(
            network_mirrors.detect_windows_system_proxy(
                reader=lambda: (True, "127.0.0.1:7890")
            ),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            network_mirrors.detect_windows_system_proxy(
                reader=lambda: (False, "127.0.0.1:7890")
            ),
            "",
        )

        def _boom():
            raise OSError("registry unavailable")

        self.assertEqual(
            network_mirrors.detect_windows_system_proxy(reader=_boom), ""
        )

    def test_apply_sets_every_casing_and_no_proxy(self):
        env = {}
        applied = network_mirrors.apply_system_proxy_env(
            env=env, reader=lambda: (True, "127.0.0.1:7890")
        )
        self.assertEqual(applied, "http://127.0.0.1:7890")
        for name in network_mirrors.PROXY_ENV_VARS:
            self.assertEqual(env[name], "http://127.0.0.1:7890")
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_apply_never_overwrites_explicit_proxy_any_casing(self):
        env = {"http_proxy": "http://mine:1"}
        applied = network_mirrors.apply_system_proxy_env(
            env=env, reader=lambda: (True, "10.0.0.1:8080")
        )
        self.assertEqual(applied, "")
        self.assertEqual(env["http_proxy"], "http://mine:1")
        self.assertNotIn("HTTP_PROXY", env)

    def test_apply_noop_when_registry_disabled(self):
        env = {}
        self.assertEqual(
            network_mirrors.apply_system_proxy_env(env=env, reader=lambda: (False, "x")),
            "",
        )
        self.assertEqual(env, {})


class TestStartupNetworkOrdering(unittest.TestCase):
    """代理与日志必须在首次核心依赖安装之前就位。"""

    def setUp(self):
        self.source = (REPO_ROOT / "launch.py").read_text(encoding="utf8")

    def test_proxy_applied_before_core_requirements(self):
        self.assertLess(
            self.source.index("apply_system_proxy_env()"),
            self.source.index("ensure_core_requirements(APP_DIR)"),
        )

    def test_logging_setup_before_core_requirements(self):
        self.assertLess(
            self.source.index("\n    setup_startup_logging()\n"),
            self.source.index("ensure_core_requirements(APP_DIR)"),
        )


class TestBundledUvDiscovery(unittest.TestCase):
    """uv.exe 与 python.exe 同目录时必须能发现（发行包正是这种布局）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.py_dir = Path(self._tmp.name)
        self.python_exe = str(self.py_dir / "python.exe")
        Path(self.python_exe).write_text("", encoding="utf8")

    def tearDown(self):
        self._tmp.cleanup()

    def _empty_path_env(self):
        # PATH 指向空目录：模拟 launch.bat 那种「不加环境目录进 PATH」的启动。
        empty = Path(self._tmp.name) / "empty_path"
        empty.mkdir(exist_ok=True)
        return {"PATH": str(empty)}

    def test_finds_uv_next_to_interpreter(self):
        uv = self.py_dir / "uv.exe"
        uv.write_text("", encoding="utf8")
        env = self._empty_path_env()
        self.assertEqual(find_uv(env, self.python_exe), str(uv))
        self.assertEqual(resolve_backend("auto", env=env, python_executable=self.python_exe), "uv")

    def test_falls_back_to_pip_when_no_uv_anywhere(self):
        env = self._empty_path_env()
        self.assertEqual(find_uv(env, self.python_exe), "")
        self.assertEqual(resolve_backend("auto", env=env, python_executable=self.python_exe), "pip")

    def test_uv_command_uses_the_discovered_executable(self):
        uv = self.py_dir / "uv.exe"
        uv.write_text("", encoding="utf8")
        cmd = build_install_command(
            ["torch"], backend="auto", env=self._empty_path_env(), python_executable=self.python_exe
        )
        self.assertEqual(cmd[0], str(uv))
        self.assertEqual(cmd[1:3], ["pip", "install"])


class TestAutoDowngradeIsNotPersisted(unittest.TestCase):
    """自动降级只对本次运行有效：落盘时写回原值，用户另选则以用户为准。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = shared.CONFIG_PATH
        shared.CONFIG_PATH = str(Path(self._tmp.name) / "config.json")
        self._original_values = (
            program_config.pcfg.module.textdetector,
            program_config.pcfg.module.inpainter,
        )
        # pcfg 是单例，别的用例往里塞过模块参数（形状未必带 "value"），而
        # get_saving_params() 会遍历它们 —— 全量跑时这就是 KeyError 的来源。
        # 本用例只关心 module.textdetector/inpainter 两个键，先把参数清空。
        self._original_params = {
            name: getattr(program_config.pcfg.module, name)
            for name in (
                "textdetector_params",
                "ocr_params",
                "translator_params",
                "inpainter_params",
            )
        }
        for name in self._original_params:
            setattr(program_config.pcfg.module, name, {})
        # 同一字段只登记第一次，用例之间要清干净。
        program_config._AUTO_DOWNGRADES.clear()

    def tearDown(self):
        program_config._AUTO_DOWNGRADES.clear()
        program_config.pcfg.module.textdetector, program_config.pcfg.module.inpainter = (
            self._original_values
        )
        for name, value in self._original_params.items():
            setattr(program_config.pcfg.module, name, value)
        shared.CONFIG_PATH = self._original_path
        self._tmp.cleanup()

    def _saved_module_section(self) -> dict:
        data = json.loads(Path(shared.CONFIG_PATH).read_text(encoding="utf8"))
        return data["module"]

    def test_downgraded_selection_is_not_written(self):
        program_config.pcfg.module.inpainter = "none"
        program_config.record_auto_downgrade(
            program_config.pcfg.module, "inpainter", "none", "lama_large_512px"
        )

        self.assertTrue(program_config.save_config())
        self.assertEqual(self._saved_module_section()["inpainter"], "lama_large_512px")
        # 落盘期间不得改写内存里的运行态：本次运行仍在降级状态。
        self.assertEqual(program_config.pcfg.module.inpainter, "none")

    def test_user_choice_after_downgrade_wins(self):
        program_config.pcfg.module.inpainter = "none"
        program_config.record_auto_downgrade(
            program_config.pcfg.module, "inpainter", "none", "lama_large_512px"
        )
        program_config.pcfg.module.inpainter = "patchmatch"

        self.assertTrue(program_config.save_config())
        self.assertEqual(self._saved_module_section()["inpainter"], "patchmatch")


class TestLamaDeclaresTorch(unittest.TestCase):
    """默认修复器要有 torch 声明，选中它才会触发后台安装。"""

    def test_lazy_spec_declares_torch(self):
        from utils.lazy_registry import _scan_file

        specs = _scan_file(str(REPO_ROOT / "modules" / "inpaint" / "base.py"), "inpainter")
        by_key = {s.key: s for s in specs}
        self.assertIn("lama_large_512px", by_key)
        self.assertIn("torch", by_key["lama_large_512px"].requires_packages)

    def test_get_missing_packages_reads_the_declaration(self):
        import modules

        spec = modules.INPAINTERS.get("lama_large_512px")
        self.assertIsNotNone(spec)
        self.assertIn("torch", list(spec.requires_packages or []))


if __name__ == "__main__":
    unittest.main()
