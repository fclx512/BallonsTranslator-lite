"""后台下载任务层与「加载期不下大模型」的回归。

对应 ``docs/技术实现/模型文件管理_设计方案_存档.md`` §5 定稿的行为：

1. ``ui/module_manager.py::_ensure_module_deps`` **选模块即返回**、把缺的依赖与
   权重交给后台任务，绝不拦模块切换；
2. ``ui/model_downloads.py::ModelDownloadRegistry`` 的状态机：防重复触发、
   可取消、结束后从注册表摘掉、失败只告知一次；
3. ``modules/base.py::BaseModule._ensure_model_files`` 在
   ``background_download_only`` 置位时**只检查不下载**（否则 1.9GB 会在主线程
   同步下完并冻住界面）；未置位的模块保持原行为；
4. 惰性 AST 扫描确实读得到新声明（``background_download_only`` /
   ``model_package`` / ``requires_packages`` / 15 条落盘路径）——这些字段一旦
   扫不到就是**静默失效**（缺文件检查整体失灵），所以必须钉住。

一切都不碰网络：下载器与线程启动都被替身挡掉。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_model_downloads.py -q
"""

import os
import os.path as osp
import sys
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import modules  # noqa: E402
from modules.base import BaseModule, MissingModelFilesError  # noqa: E402


class _FakeModule(BaseModule):
    """最小可加载模块：只用来观察 _ensure_model_files 的行为。"""

    params = None
    _load_model_keys = {"model"}

    def __init__(self, dfl, **kwargs):
        BaseModule.__init__(self)
        self.download_file_list = dfl
        for key, value in kwargs.items():
            setattr(self, key, value)

    def _load_model(self):
        self.model = object()


def _entry(save_path):
    return {"url": "https://example.invalid/", "files": [save_path]}


class TestEnsureModelFiles(unittest.TestCase):
    """加载期：要不要下载"""

    def test_background_only_raises_when_files_are_missing(self):
        mod = _FakeModule(
            [_entry("data/models/_pytest_absent_/model.safetensors")],
            background_download_only=True,
        )
        with mock.patch("utils.download_util.download_and_check_files") as dl:
            with self.assertRaises(MissingModelFilesError) as ctx:
                mod._ensure_model_files()
            dl.assert_not_called()
        self.assertEqual(
            ctx.exception.missing_files,
            ["data/models/_pytest_absent_/model.safetensors"],
        )

    def test_background_only_passes_when_files_exist(self):
        present = "data/models/lama_large_512px.ckpt"
        if not osp.exists(osp.join(APP_ROOT, present)):
            self.skipTest("lama_large_512px.ckpt not present in this checkout")
        mod = _FakeModule([_entry(present)], background_download_only=True)
        with mock.patch("utils.download_util.download_and_check_files") as dl:
            mod._ensure_model_files()
            dl.assert_not_called()

    def test_small_modules_still_download_on_load(self):
        """未声明 background_download_only 的模块行为不变（加载期顺手补下）。"""
        mod = _FakeModule([_entry("data/models/_pytest_absent_/small.pt")])
        with mock.patch(
            "utils.download_util.download_and_check_files", return_value=True
        ) as dl:
            mod._ensure_model_files()
        self.assertEqual(dl.call_count, 1)

    def test_no_declaration_is_a_noop(self):
        mod = _FakeModule([])
        with mock.patch("utils.download_util.download_and_check_files") as dl:
            mod._ensure_model_files()
            dl.assert_not_called()

    def test_error_message_is_english_and_truncated(self):
        """异常只带结构化信息（用户文案在 UI 层组装，modules/ 不写中文）。"""
        err = MissingModelFilesError("demo", ["a.json", "b.bin", "c.onnx", "d.txt"])
        self.assertEqual(err.module_name, "demo")
        self.assertEqual(len(err.missing_files), 4)
        self.assertIn("demo", str(err))
        self.assertIn("(+1 more)", str(err))


class TestRegistryStateMachine(unittest.TestCase):
    """后台任务注册表"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui import model_downloads as md

        self.md = md
        self.registry = md.ModelDownloadRegistry()
        self.info = {
            "module_type": "ocr",
            "key": "demo",
            "display_name": "Demo",
            "package_dir": None,
            "size_hint": None,
            "size_bytes": 0,
            "missing": ["data/models/demo/model.safetensors"],
            "files": ["data/models/demo/model.safetensors"],
            "missing_packages": [],
            "download_file_list": [_entry("data/models/demo/model.safetensors")],
        }

    def _start(self, info=None):
        """起一个「任务对象存在但线程不跑」的任务。"""
        patched = mock.patch.object(
            self.md.ModelDownloadTask, "start", lambda self: None
        )
        patched.start()
        self.addCleanup(patched.stop)
        with mock.patch(
            "modules.GET_MODULE_REQUIREMENTS", return_value=info or self.info
        ):
            return self.registry.start("ocr", "demo")

    def test_start_reports_running_and_dedupes(self):
        self.assertTrue(self._start())
        self.assertTrue(self.registry.is_running("ocr", "demo"))
        self.assertTrue(self.registry.is_busy())
        # 同一个模块重复触发不该起第二个任务
        self.assertFalse(self.registry.start("ocr", "demo"))

    def test_start_is_false_when_nothing_to_do(self):
        nothing = dict(self.info, missing=[], missing_packages=[], files=[])
        self.assertFalse(self._start(nothing))
        self.assertFalse(self.registry.is_running("ocr", "demo"))

    def test_start_is_false_for_module_without_weights(self):
        with mock.patch("modules.GET_MODULE_REQUIREMENTS", return_value=None):
            self.assertFalse(self.registry.start("ocr", "none_ocr"))

    def test_cancel_sets_the_event(self):
        self._start()
        task = self.registry._tasks[("ocr", "demo")]
        self.assertFalse(task.is_cancelled)
        self.assertTrue(self.registry.cancel("ocr", "demo"))
        self.assertTrue(task.is_cancelled)
        self.assertFalse(self.registry.cancel("ocr", "not_running"))

    def test_finished_success_unregisters(self):
        self._start()
        task = self.registry._tasks[("ocr", "demo")]
        self.registry._on_finished("ocr", "demo", task, True)
        self.assertFalse(self.registry.is_running("ocr", "demo"))
        self.assertFalse(self.registry.is_busy())

    def test_cancelled_task_does_not_notify(self):
        self._start()
        task = self.registry._tasks[("ocr", "demo")]
        task.cancelled_by_user = True
        with mock.patch.object(self.md, "create_info_dialog") as dialog:
            self.registry._on_finished("ocr", "demo", task, False)
            dialog.assert_not_called()

    def test_failed_task_notifies_once(self):
        self._start()
        task = self.registry._tasks[("ocr", "demo")]
        task._failure_code = "network_hf_no_mirror"
        with mock.patch.object(self.md, "create_info_dialog") as dialog:
            self.registry._on_finished("ocr", "demo", task, False)
            dialog.assert_called_once()
            message = dialog.call_args.args[0]
        self.assertIn("Demo", message)
        self.assertIn("hf-mirror.com", message)

    def test_signals_fire_on_start_and_finish(self):
        started, finished = [], []
        self.registry.task_started.connect(lambda *a: started.append(a))
        self.registry.task_finished.connect(lambda *a: finished.append(a))
        self._start()
        task = self.registry._tasks[("ocr", "demo")]
        task.cancelled_by_user = True
        self.registry._on_finished("ocr", "demo", task, False)
        self.assertEqual(started, [("ocr", "demo")])
        self.assertEqual(finished, [("ocr", "demo", False)])


class TestFailureMessage(unittest.TestCase):
    def test_pip_failure_names_the_package(self):
        from ui.model_downloads import format_failure_message

        message = format_failure_message("Demo", "pip_failed:transformers>=4.57.1,<5")
        self.assertIn("transformers", message)
        self.assertIn("Model Files", message)

    def test_every_network_code_has_a_hint(self):
        from ui.model_downloads import format_failure_message

        for code in (
            "network_hf_no_mirror",
            "network_hf",
            "network_github",
            "network_other",
            "internal_error",
        ):
            self.assertGreater(len(format_failure_message("Demo", code)), 0, code)

    def test_unknown_code_falls_back_to_manual_placement(self):
        from ui.model_downloads import format_failure_message

        self.assertIn("manually", format_failure_message("Demo", "something_new"))

    def test_missing_hint_points_at_settings(self):
        from ui.model_downloads import missing_model_files_hint

        hint = missing_model_files_hint("PaddleOCR-VL-For-Manga")
        self.assertIn("PaddleOCR-VL-For-Manga", hint)
        self.assertIn("Models", hint)


class TestGpuGate(unittest.TestCase):
    """无加速设备的机器上拒下 GPU-only 模块（``requires_gpu``）。

    开发机自带 CUDA，所以「拒绝」这条分支默认走不到——不钉住就等于没有测试。
    判据与文案的接缝在两处 import（``modules.base.accelerator_available``、
    ``modules.GET_MODULE_REQUIREMENTS``），都按调用点打补丁。
    """

    gpu_info = {
        "module_type": "ocr",
        "key": "demo_vl",
        "display_name": "Demo VL",
        "requires_gpu": True,
    }

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_block_refuses_and_names_the_module(self):
        from ui.model_downloads import gpu_requirement_block

        with mock.patch("modules.base.accelerator_available", return_value=False):
            with mock.patch(
                "modules.GET_MODULE_REQUIREMENTS", return_value=self.gpu_info
            ):
                reason = gpu_requirement_block("ocr", "demo_vl")
        self.assertIn("Demo VL", reason)
        # 拒绝说明必须回答「那我还能怎么办」（两条出路：换 CUDA torch / 换内置模型）
        self.assertIn("install_cuda.bat", reason)
        self.assertIn("built-in models", reason)

    def test_block_is_empty_when_the_machine_has_an_accelerator(self):
        from ui.model_downloads import gpu_requirement_block

        with mock.patch("modules.base.accelerator_available", return_value=True):
            with mock.patch(
                "modules.GET_MODULE_REQUIREMENTS", return_value=self.gpu_info
            ):
                self.assertEqual(gpu_requirement_block("ocr", "demo_vl"), "")

    def test_block_ignores_modules_without_the_declaration(self):
        from ui.model_downloads import gpu_requirement_block

        plain = dict(self.gpu_info, requires_gpu=False)
        with mock.patch("modules.base.accelerator_available", return_value=False):
            with mock.patch("modules.GET_MODULE_REQUIREMENTS", return_value=plain):
                self.assertEqual(gpu_requirement_block("ocr", "demo_vl"), "")

    def test_registry_refuses_before_starting_anything(self):
        from ui import model_downloads as md

        registry = md.ModelDownloadRegistry()
        with mock.patch("modules.base.accelerator_available", return_value=False):
            with mock.patch(
                "modules.GET_MODULE_REQUIREMENTS", return_value=self.gpu_info
            ):
                with mock.patch.object(md, "ModelDownloadTask") as task_cls:
                    self.assertFalse(registry.start("ocr", "demo_vl"))
        task_cls.assert_not_called()
        self.assertFalse(registry.is_running("ocr", "demo_vl"))
        self.assertFalse(registry.is_busy())

    def test_load_hint_speaks_of_the_gpu_instead_of_pointing_at_settings(self):
        from ui.model_downloads import missing_model_files_hint

        with mock.patch("modules.base.accelerator_available", return_value=False):
            blocked = missing_model_files_hint("Demo VL", requires_gpu=True)
        with mock.patch("modules.base.accelerator_available", return_value=True):
            allowed = missing_model_files_hint("Demo VL", requires_gpu=True)
        self.assertIn("install_cuda.bat", blocked)
        # 有卡时仍走原来的「去设置页下载」，不能一律改口径
        self.assertIn("Models", allowed)
        self.assertNotIn("install_cuda.bat", allowed)

    def test_panel_badge_prefers_needs_gpu_over_missing_count(self):
        from ui.model_files_panel import ModelFilesSection

        with mock.patch("modules.base.accelerator_available", return_value=False):
            self.assertTrue(ModelFilesSection._gpu_blocked(dict(self.gpu_info)))
            # 没声明的模块即便缺文件也只是「缺失 N」，不是「需要 GPU」
            self.assertFalse(
                ModelFilesSection._gpu_blocked(
                    dict(self.gpu_info, requires_gpu=False)
                )
            )
        with mock.patch("modules.base.accelerator_available", return_value=True):
            self.assertFalse(ModelFilesSection._gpu_blocked(dict(self.gpu_info)))

    def test_the_vl_module_declares_requires_gpu(self):
        """真实注册表里 pin 住声明——扫描链断一处这条就红。"""
        modules.init_module_registries()
        info = modules.GET_MODULE_REQUIREMENTS("ocr", "paddleocr_vl_manga")
        self.assertTrue(info["requires_gpu"])


class TestEnsureModuleDeps(unittest.TestCase):
    """选模块 = 起后台任务 + 立即返回（不再弹模态、不再回滚选择器）"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_starts_background_task_and_returns_true(self):
        from ui import module_manager

        with mock.patch("ui.model_downloads.model_downloads") as getter:
            self.assertTrue(
                module_manager._ensure_module_deps("ocr", "paddleocr_vl_manga")
            )
        getter.return_value.start.assert_called_once_with("ocr", "paddleocr_vl_manga")

    def test_start_failure_does_not_block_module_switch(self):
        from ui import module_manager

        with mock.patch("ui.model_downloads.model_downloads") as getter:
            getter.return_value.start.side_effect = RuntimeError("boom")
            self.assertTrue(module_manager._ensure_module_deps("ocr", "anything"))


class TestModuleMetadataQueries(unittest.TestCase):
    """清单与依赖缺口查询（面板行与下载任务的唯一采集口）"""

    @classmethod
    def setUpClass(cls):
        modules.init_module_registries()

    def test_no_weight_module_is_reported_as_none(self):
        self.assertIsNone(modules.GET_MODULE_REQUIREMENTS("ocr", "none_ocr"))
        self.assertIsNone(modules.GET_MODULE_REQUIREMENTS("ocr", "no_such_module"))

    def test_ysgyolo_keeps_its_special_case(self):
        """它的文件名允许飘，故按通配判断而不是看声明文件名。"""
        result = modules.GET_MISSING_MODEL_FILES("textdetector", "ysgyolo")
        self.assertIn(result, ([], ["data/models/ysgyolo_*.pt"]))

    def test_packages_list_is_stage_ordered_and_covers_the_new_module(self):
        packages = modules.GET_MODEL_PACKAGES()
        keys = [(p["module_type"], p["key"]) for p in packages]
        self.assertIn(("ocr", "paddleocr_vl_manga"), keys)
        stages = [p["module_type"] for p in packages]
        self.assertEqual(stages, sorted(stages, key=modules._PACKAGE_STAGE_ORDER.index))
        for package in packages:
            # 删除白名单只能是文件，且与展示字段分离
            self.assertTrue(all(not f.endswith("/") for f in package["files"]))

    def test_declared_paths_match_the_declared_names(self):
        info = modules.GET_MODULE_REQUIREMENTS("ocr", "paddleocr_vl_manga")
        entry = info["download_file_list"][0]
        self.assertEqual(len(entry["files"]), len(entry["save_files"]))
        for name, save_path in zip(entry["files"], entry["save_files"]):
            self.assertEqual(osp.basename(save_path), name)
            self.assertTrue(save_path.startswith("data/models/paddleocr_vl_manga/"))


class TestLazyScanOfNewModule(unittest.TestCase):
    """惰性扫描必须读到新声明 —— 读不到就是静默失效（缺文件检查整体失灵）"""

    @classmethod
    def setUpClass(cls):
        from utils.lazy_registry import _scan_file

        specs = _scan_file(
            osp.join(APP_ROOT, "modules", "ocr", "ocr_vl_manga.py"), "ocr"
        )
        cls.spec = next(s for s in specs if s.key == "paddleocr_vl_manga")

    def test_no_metadata_warnings(self):
        self.assertEqual(self.spec.metadata_warnings, [])

    def test_background_download_only_is_scanned(self):
        self.assertTrue(self.spec.background_download_only)

    def test_model_package_is_scanned(self):
        self.assertEqual(self.spec.model_package["dir"], "data/models/paddleocr_vl_manga")
        self.assertEqual(self.spec.model_package["size_hint"], "1.9 GB")
        self.assertEqual(self.spec.model_package["name"], "PaddleOCR-VL-For-Manga")

    def test_requires_packages_is_scanned(self):
        """requirements 必须能静态读到，否则选模块时不会起安装任务。"""
        self.assertTrue(self.spec.requires_packages)
        self.assertTrue(
            any("transformers" in req for req in self.spec.requires_packages)
        )

    def test_download_list_has_matching_files_and_save_files(self):
        dfl = self.spec.download_file_list
        self.assertEqual(len(dfl), 1)
        entry = dfl[0]
        self.assertEqual(entry["concatenate_url_filename"], 1)
        self.assertEqual(len(entry["files"]), 15)
        self.assertEqual(len(entry["save_files"]), 15)

    def test_tokenizer_json_is_declared(self):
        """`tokenizer.json` 是**加载期的硬前提**，不能从声明里掉出去。

        `modules/ocr/ocr_vl_manga.py::PaddleOCRVLManga._load_model` 靠传
        `add_prefix_space=None` 让 transformers 直接吃这个文件（见该处长注释，
        以及 `scripts/probes/ocr_vl_tokenizer.py`）。少了它，加载会退回需要
        sentencepiece 的 slow 路径，并在缺包时直接抛
        "Cannot instantiate this tokenizer from a slow version"。
        """
        entry = self.spec.download_file_list[0]
        self.assertIn("tokenizer.json", entry["files"])
        self.assertIn(
            "data/models/paddleocr_vl_manga/tokenizer.json", entry["save_files"]
        )

    def test_registry_view_matches_the_scan(self):
        """经注册表（而非直接扫文件）读到的信息要和扫描一致。"""
        modules.init_module_registries()
        spec = modules.OCR.get("paddleocr_vl_manga")
        self.assertTrue(spec.background_download_only)
        self.assertEqual(spec.model_package["dir"], "data/models/paddleocr_vl_manga")
        self.assertFalse(modules.GET_MISSING_PACKAGES("ocr", "none_ocr"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
