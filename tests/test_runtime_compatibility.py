"""Source/runtime compatibility and update rejection before source mutation."""
import json
import platform
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from utils import runtime_compatibility as compat
from utils.updater import BallonsTranslatorUpdater, ReleaseInfo


class RuntimeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        windows = mock.patch.object(compat.sys, "platform", "win32")
        windows.start()
        self.addCleanup(windows.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = {"schema": 1, "family": "test-v1", "python_series": ".".join(platform.python_version_tuple()[:2]),
                         "profiles": ["cpu"], "required_models": []}
        (self.root / compat.CONTRACT).write_text(json.dumps(self.contract), encoding="utf-8")
        self.incoming = {compat.CONTRACT: json.dumps(self.contract),
                         "requirements.txt": "packaging>=23\n",
                         "pyproject.toml": '[project]\ndependencies = ["packaging>=23"]\n'}

    def test_legacy_bundle_warns_but_same_family_can_update(self):
        self.assertTrue(compat.check_runtime(self.root))
        compat.source_update_preflight(self.root, self.incoming.get)

    def test_family_change_needs_complete_package_even_without_manifest(self):
        self.incoming[compat.CONTRACT] = json.dumps(dict(self.contract, family="test-v2"))
        with self.assertRaises(compat.RuntimeCompatibilityError):
            compat.source_update_preflight(self.root, self.incoming.get)

    def test_new_requirement_cannot_sneak_into_existing_bundle(self):
        self.incoming["requirements.txt"] = "packaging>=9999"
        self.incoming["pyproject.toml"] = '[project]\ndependencies = ["packaging>=9999"]'
        with self.assertRaisesRegex(compat.RuntimeCompatibilityError, "does not satisfy"):
            compat.source_update_preflight(self.root, self.incoming.get)

    def test_declarations_must_agree(self):
        self.incoming["requirements.txt"] = "packaging>=9999"
        with self.assertRaisesRegex(compat.RuntimeCompatibilityError, "disagree"):
            compat.source_update_preflight(self.root, self.incoming.get)

    def test_removing_contract_rejected(self):
        with self.assertRaises(compat.RuntimeCompatibilityError):
            compat.source_update_preflight(self.root, lambda name: None)
        with self.assertRaises(compat.RuntimeCompatibilityError):
            compat.preflight_directory(self.root, self.root, delta=True, deleted=[compat.CONTRACT])

    def test_delta_checks_effective_declarations(self):
        for name, text in self.incoming.items():
            (self.root / name).write_text(text, encoding="utf-8")
        pending = self.root / "pending"
        pending.mkdir()
        compat.preflight_directory(self.root, pending, delta=True)
        (pending / "requirements.txt").write_text("packaging>=9999", encoding="utf-8")
        with self.assertRaises(compat.RuntimeCompatibilityError):
            compat.preflight_directory(self.root, pending, delta=True)

    def test_release_rejection_precedes_backup_stash_and_replace(self):
        archive = self.root / "source.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            for name, text in self.incoming.items():
                if name == compat.CONTRACT:
                    text = json.dumps(dict(self.contract, family="other"))
                stream.writestr("release/" + name, text)
        updater = BallonsTranslatorUpdater(str(self.root), str(self.root / "cache"))
        with mock.patch.object(updater, "download_source_zip", return_value=archive), \
             mock.patch.object(updater, "backup_source") as backup, \
             mock.patch.object(updater, "prepare_git_worktree") as git, \
             mock.patch.object(updater, "install_source_zip") as install:
            with self.assertRaises(compat.RuntimeCompatibilityError):
                updater.apply_update(ReleaseInfo("v2", "2", "url", "zip"), "1")
            backup.assert_not_called()
            git.assert_not_called()
            install.assert_not_called()

    def test_duplicate_distribution_is_not_silently_flattened(self):
        dists = [mock.Mock(metadata={"Name": "Test_Pkg"}, version="1"),
                 mock.Mock(metadata={"Name": "test-pkg"}, version="2")]
        with mock.patch.object(compat.metadata, "distributions", return_value=dists):
            with self.assertRaisesRegex(compat.RuntimeCompatibilityError, "Duplicate"):
                compat.installed_inventory()


class InstallerGuardTests(unittest.TestCase):
    def test_explicit_uv_does_not_use_path_when_bundle_has_no_uv(self):
        from utils import package_installer as installer
        with mock.patch.object(installer, "find_uv", return_value=""):
            command = installer.build_install_command(["numpy"], backend="uv", python_executable="bundle/python.exe")
        self.assertEqual(command[:4], ["bundle/python.exe", "-m", "pip", "install"])

    def test_gpu_ort_not_installed_independently_of_cuda_pair(self):
        from utils import package_installer as installer
        with mock.patch.object(installer.sys, "executable", "C:/app/ballontrans_pylibs_win/python.exe"), \
             mock.patch.object(installer.subprocess, "Popen") as process:
            result = installer.install(["onnxruntime-gpu"])
        self.assertFalse(result.ok)
        self.assertIn("CUDA profile", result.error)
        process.assert_not_called()

    def test_constraints_failure_never_invokes_installer(self):
        from utils import package_installer as installer
        with mock.patch.object(compat, "installed_inventory", side_effect=RuntimeError("broken metadata")), \
             mock.patch.object(installer.subprocess, "Popen") as process:
            result = installer.install(["numpy"])
            self.assertFalse(result.ok)
            self.assertIn("Cannot protect", result.error)
            process.assert_not_called()

    def test_no_deps_only_allowed_for_named_exception(self):
        from utils import package_installer as installer
        for kwargs in ({"requirements": ["numpy"]}, {"requirements_file": "requirements.txt"}, {}):
            with self.subTest(kwargs=kwargs), mock.patch.object(installer.subprocess, "Popen") as process:
                result = installer.install(extra_args="--no-deps", **kwargs)
                self.assertFalse(result.ok)
                process.assert_not_called()

    def test_failed_import_is_not_reported_as_success(self):
        from utils import package_installer as installer
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(installer.subprocess, "Popen", return_value=process), \
             mock.patch.object(installer, "_stream_process_output", return_value="installed"), \
             mock.patch.object(installer.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="DLL failed")):
            result = installer.install(["numpy"], verify_imports=True)
        self.assertFalse(result.ok)
        self.assertIn("DLL failed", result.error)

    def test_extra_children_are_checked(self):
        from importlib.metadata import PackageNotFoundError

        from utils.core_requirements import check_declared_core_requirements
        parent = mock.Mock(version="1", requires=['child>=2; extra == "socks"'])
        def distribution(name):
            if name == "parent":
                return parent
            raise PackageNotFoundError(name)
        with mock.patch("importlib.metadata.distribution", side_effect=distribution):
            self.assertEqual(check_declared_core_requirements(requirements_text="parent"), [])
            self.assertIn("missing child", " ".join(check_declared_core_requirements(requirements_text="parent[socks]")))


class BundleManifestTests(unittest.TestCase):
    def test_failed_validation_cannot_overwrite_manifest(self):
        from scripts import check_bundle
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ballontrans_pylibs_win").mkdir()
            manifest = root / compat.MANIFEST
            manifest.write_text("original inventory", encoding="utf-8")
            with mock.patch.object(check_bundle, "ROOT", root), \
                 mock.patch.object(check_bundle.sys, "platform", "win32"), \
                 mock.patch.object(check_bundle.sys, "executable", str(root / "ballontrans_pylibs_win/python.exe")), \
                 mock.patch.object(check_bundle.sys, "argv", ["check_bundle", "--profile", "cpu", "--write-manifest", "--bundle-id", "candidate"]), \
                 mock.patch.object(check_bundle.os, "chdir"), \
                 mock.patch.object(check_bundle, "inspect_bundle", return_value=(["mixed runtime"], {})):
                self.assertEqual(check_bundle.main(), 1)
            self.assertEqual(manifest.read_text(encoding="utf-8"), "original inventory")

    def test_model_files_collected_without_importing_model_code(self):
        from scripts import check_bundle
        from utils.registry import ModuleSpec
        spec = ModuleSpec(key="dummy", import_path="not_importable", class_name="Dummy", module_type="inpainter",
                          download_file_list=[{"files": "data/libs/test.dll"}])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data/libs").mkdir(parents=True)
            (root / "data/libs/test.dll").write_bytes(b"fixture")
            with mock.patch.object(check_bundle, "ROOT", root), \
                 mock.patch("utils.lazy_registry.init_lazy_module_registries"), \
                 mock.patch("utils.lazy_registry._scan_file", return_value=[spec]), \
                 mock.patch.object(check_bundle, "installed_inventory", return_value={}), \
                 mock.patch("utils.registries.INPAINTERS.get_spec", return_value=None):
                errors, files = check_bundle.collect_models({"required_models": ["inpainter:dummy"]}, False)
            self.assertEqual(errors, [])
            self.assertEqual(files["data/libs/test.dll"]["size"], 7)
            self.assertEqual(len(files["data/libs/test.dll"]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
