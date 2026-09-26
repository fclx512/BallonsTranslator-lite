"""PatchMatch 修复器在精简包里的可用性契约（阶段三回归）。

精简包的正式口径：预装 `requirements.txt` 基本依赖，**不预装** torch /
ultralytics / onnxruntime / onnxocr / transformers / numba，也**不带模型权重**；
`data/libs/` 下的两个原生 DLL 随包携带——PatchMatch 是随包的基础能力，精简包
用户必须能从 GUI 直接选到它。

本文件钉住四件事：

1. **可见性**：patchmatch 不再被藏在选型界面之外（运行对话框 / 设置页＝画布工具
   的镜像来源 / 底部栏同一份名单），LLM 修复器保持隐藏语义；
2. **不需要 torch**：没有 torch 的进程里也能导入、构造并真的跑完一次修复
   （``launch.py::_ensure_module_fallback`` 的"没 torch 就换 none"必须豁免它）；
3. **缺原生附件可读失败**：不抛裸 OSError——查得到可读原因，真跑时抛
   ``PatchMatchUnavailableError``；只查文件时不加载 DLL；
4. **不进模型下载清单**，但保持逐块能力（``ui/batch_inpaint.py`` 的载体，
   见 tests/test_batch_simple_inpaint.py::PatchmatchCarrierTest）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_inpaint_patchmatch.py -q
"""

import os
import os.path as osp
import subprocess
import sys
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

import numpy as np  # noqa: E402

from modules.inpaint import patch_match  # noqa: E402


def _native_files_present() -> bool:
    """本工作区是否备着 data/libs 原生附件（源码运行的常见状态）。"""
    return all(osp.isfile(p) for p in patch_match.required_native_files())


class SelectorVisibilityTest(unittest.TestCase):
    """patchmatch 必须在各选型入口出现；LLMInpaint 的隐藏语义不变。"""

    @classmethod
    def setUpClass(cls):
        import modules

        modules.init_module_registries()
        cls.modules = modules

    def test_registered_and_not_hidden(self):
        self.assertIn("patchmatch", self.modules.GET_VALID_INPAINTERS())
        self.assertNotIn("patchmatch", self.modules.HIDDEN_INPAINTERS)

    def test_only_the_llm_inpainter_stays_hidden(self):
        hidden = set(self.modules.HIDDEN_INPAINTERS)
        self.assertEqual(hidden, {"LLMInpaint"})
        # 隐藏项必须真的注册着，否则这句"隐藏"落空、注释也会骗人
        self.assertTrue(hidden <= set(self.modules.GET_VALID_INPAINTERS()))

    def test_run_dialog_offers_patchmatch(self):
        from ui.run_pipeline_dialog import RunPipelineDialog

        options = RunPipelineDialog._module_options("inpainter")
        self.assertIn("patchmatch", options)
        self.assertNotIn("LLMInpaint", options)
        self.assertEqual(
            sorted(options),
            sorted(
                m
                for m in self.modules.GET_VALID_INPAINTERS()
                if m not in self.modules.HIDDEN_INPAINTERS
            ),
        )

    def test_bottom_bar_filters_by_the_same_set(self):
        """底部栏不是第二份硬编码名单——它必须读同一份 HIDDEN_INPAINTERS。"""
        path = osp.join(APP_ROOT, "ui", "mainwindow.py")
        with open(path, encoding="utf8") as fh:
            source = fh.read()
        self.assertIn(
            "[m for m in GET_VALID_INPAINTERS() if m not in HIDDEN_INPAINTERS]",
            source,
        )


class SettingsPanelSelectorTest(unittest.TestCase):
    """设置页（Pipeline → Inpaint）的下拉：patchmatch 进、LLMInpaint 不进。

    画布工具面板的引擎下拉是这份下拉的**镜像**（
    ``ui/module_parse_widgets.py::ModuleConfigParseWidget.create_mirror_selector``），
    因此这一条同时覆盖"嵌字页油漆桶/矩形工具能选到 patchmatch"。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from utils.config import load_config

        load_config()
        import modules

        modules.init_module_registries()
        from modules import (
            GET_VALID_INPAINTERS,
            INPAINTERS,
            merge_config_module_params,
        )
        from utils.config import pcfg

        from ui.module_parse_widgets import InpaintConfigPanel

        self.panel = InpaintConfigPanel("")
        params = merge_config_module_params(
            pcfg.module.inpainter_params, GET_VALID_INPAINTERS(), INPAINTERS.get
        )
        self.panel.addModulesParamWidgets(params, None)

    def _items(self, combo) -> list:
        return [
            combo.itemText(i)
            for i in range(combo.count())
            if combo.itemText(i)
        ]

    def test_exclude_list_only_holds_the_llm_inpainter(self):
        self.assertEqual(self.panel.exclude_modules, {"LLMInpaint"})

    def test_combobox_lists_patchmatch_but_not_llm_inpaint(self):
        items = self._items(self.panel.module_combobox)
        self.assertIn("patchmatch", items)
        self.assertNotIn("LLMInpaint", items)

    def test_mirror_selector_used_by_canvas_tools_lists_patchmatch(self):
        mirror = self.panel.create_mirror_selector()
        mirror.sync_items()
        self.assertIn("patchmatch", self._items(mirror))


class LaunchFallbackContractTest(unittest.TestCase):
    """启动兜底不能把无 torch 的 PatchMatch 改成 none。"""

    def test_fallback_source_exempts_patchmatch(self):
        launch_path = osp.join(APP_ROOT, "launch.py")
        with open(launch_path, encoding="utf8") as fh:
            source = fh.read()
        self.assertIn(
            'pcfg.module.inpainter not in ("none", "patchmatch")',
            source,
        )


class TorchFreeTest(unittest.TestCase):
    """没有 torch 的进程里（＝精简包的真实状态）PatchMatch 照旧可用。

    用子进程 + meta_path 拦截，才能真的模拟"根本没装 torch"；同进程里
    torch 早被别的用例导入了，断言不出东西。
    """

    SCRIPT = """
import os
import sys

# 便携/嵌入式解释器带 ._pth 时，``-c`` 脚本的 cwd 不在 sys.path 上
sys.path.insert(0, os.getcwd())

class _BlockTorch:
    def find_spec(self, name, path=None, target=None):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("torch blocked for this test")
        return None

sys.meta_path.insert(0, _BlockTorch())

import numpy as np
from modules.inpaint import base as inpaint_base
from modules.inpaint.inpaint_patchmatch import PatchmatchInpainter

assert "torch" not in sys.modules, "torch 不该被导进来"
assert inpaint_base.torch is None, "base.py 的 torch 兜底没生效"

inpainter = PatchmatchInpainter()
assert inpainter.inpaint_by_block, "逐块能力丢了（批量简单背景要它）"

from modules.inpaint import patch_match
if patch_match.native_lib_status()[0]:
    img = np.full((24, 24, 3), 255, np.uint8)
    img[6:18, 6:18] = 0
    mask = np.zeros((24, 24), np.uint8)
    mask[6:18, 6:18] = 255
    out = inpainter.inpaint(img, mask)
    assert out.shape == img.shape, out.shape
    assert not (out[12, 12] == 0).all(), "遮罩区域没被修"
    print("OK-WITH-NATIVE")
else:
    print("OK-NO-NATIVE")

assert "torch" not in sys.modules, "跑完修复后又把 torch 拉起来了"
"""

    def test_imports_and_runs_without_torch(self):
        proc = subprocess.run(
            [sys.executable, "-c", self.SCRIPT],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            proc.stdout.strip().endswith(("OK-WITH-NATIVE", "OK-NO-NATIVE")),
            proc.stdout,
        )


class NativeLibraryAvailabilityTest(unittest.TestCase):
    """缺/坏原生附件：可读原因 + 可捕获错误，不抛裸 OSError（阶段三第 3 条）。"""

    def setUp(self):
        self._pmlib = patch_match.PMLIB
        patch_match.PMLIB = None
        self.addCleanup(self._restore_pmlib)

    def _restore_pmlib(self):
        patch_match.PMLIB = self._pmlib

    @staticmethod
    def _patch_required(paths):
        return mock.patch.object(
            patch_match, "required_native_files", return_value=list(paths)
        )

    def test_required_files_cover_both_windows_assets(self):
        """Windows 上 patchmatch DLL 依赖 opencv_world——两个都得在名单里。

        实测缺 opencv_world 时加载失败信息只提 patchmatch_inpaint.dll
        （"Could not find module ... or one of its dependencies"），指不到真正
        缺的文件，所以存在性检查要自己把两个都报出来。
        """
        if sys.platform != "win32":
            self.skipTest("Windows 专属的原生附件名单")
        self.assertEqual(
            patch_match.required_native_files("data/libs"),
            [
                "data/libs/patchmatch_inpaint.dll",
                "data/libs/opencv_world455.dll",
            ],
        )

    def test_missing_files_reason_names_them(self):
        missing = [
            "data/libs/native-attachment-missing.dll",
            "data/libs/opencv_world455-missing.dll",
        ]
        with self._patch_required(missing):
            ok, reason = patch_match.native_lib_status()
        self.assertFalse(ok, reason)
        for path in missing:
            self.assertIn(path, reason)
        self.assertIn("data/libs", reason)

    def test_inpaint_raises_readable_error_not_bare_oserror(self):
        missing = "data/libs/native-attachment-missing.dll"
        with self._patch_required([missing]):
            with self.assertRaises(patch_match.PatchMatchUnavailableError) as ctx:
                patch_match.inpaint(
                    np.zeros((8, 8, 3), np.uint8), np.zeros((8, 8), np.uint8)
                )
        self.assertIn(missing, str(ctx.exception))
        self.assertIn("re-extract", str(ctx.exception))
        self.assertIsInstance(ctx.exception, RuntimeError)
        self.assertNotIsInstance(ctx.exception, OSError)

    def test_status_only_checks_files_without_loading(self):
        """只查文件不加载 DLL：55MB 附件不该为了"查一下"就被映射进进程。"""
        patch_match.native_lib_status()
        self.assertIsNone(patch_match.PMLIB)

    def test_constructing_the_inpainter_needs_no_native_lib(self):
        """缺附件时构造必须成功：启动期会按配置构造当前修复器，不能因此弹错。"""
        from modules.inpaint.inpaint_patchmatch import PatchmatchInpainter

        with self._patch_required(["data/libs/native-attachment-missing.dll"]):
            inpainter = PatchmatchInpainter()
            self.assertTrue(inpainter.inpaint_by_block)
            ok, reason = PatchmatchInpainter.native_lib_status()
        self.assertFalse(ok)
        self.assertIn("native-attachment-missing.dll", reason)
        self.assertIsNone(patch_match.PMLIB)

    def test_load_failure_is_reported_without_raising(self):
        existing = osp.abspath(__file__)  # 存在的路径 → 过存在性检查，卡在加载
        with self._patch_required([existing]):
            with mock.patch.object(
                patch_match, "_load", side_effect=OSError("boom-0xc000007b")
            ):
                ok, reason = patch_match.native_lib_status(try_load=True)
                self.assertFalse(ok)
                self.assertIn("boom-0xc000007b", reason)
                with self.assertRaises(patch_match.PatchMatchUnavailableError):
                    patch_match.load_native_lib()

    @unittest.skipUnless(_native_files_present(), "本工作区没有 data/libs 原生附件")
    def test_present_files_report_available_and_load(self):
        ok, reason = patch_match.native_lib_status(try_load=True)
        self.assertTrue(ok, reason)
        self.assertIsNotNone(patch_match.PMLIB)


class RealNativeInpaintTest(unittest.TestCase):
    """本工作区备着附件时：逐块入口能真的调到原生库（不是只过了注册）。"""

    @unittest.skipUnless(_native_files_present(), "本工作区没有 data/libs 原生附件")
    def test_inpaint_fills_the_masked_area(self):
        from modules.inpaint.inpaint_patchmatch import PatchmatchInpainter

        img = np.full((48, 48, 3), 255, np.uint8)
        img[12:36, 12:36] = 0
        mask = np.zeros((48, 48), np.uint8)
        mask[12:36, 12:36] = 255
        out = PatchmatchInpainter().inpaint(img.copy(), mask.copy())
        self.assertEqual(out.shape, img.shape)
        self.assertEqual(out.dtype, np.uint8)
        # 被抹掉的黑块该被周围的白色补上
        self.assertGreater(int(out[24, 24].min()), 0)


class NotInModelDownloadListTest(unittest.TestCase):
    """不进模型下载清单：它没有权重、没有 pip 依赖，也不是 native 附件下载项。"""

    @classmethod
    def setUpClass(cls):
        import modules

        modules.init_module_registries()
        cls.modules = modules

    def test_absent_from_model_packages(self):
        keys = [entry["key"] for entry in self.modules.GET_MODEL_PACKAGES()]
        self.assertNotIn("patchmatch", keys)

    def test_no_requirements_entry(self):
        self.assertIsNone(
            self.modules.GET_MODULE_REQUIREMENTS("inpainter", "patchmatch")
        )

    def test_no_declared_packages_and_no_missing_files(self):
        self.assertEqual(
            self.modules.GET_MISSING_PACKAGES("inpainter", "patchmatch"), []
        )
        # 原生附件不是"缺的模型文件"，别让它落进运行前的"去下载"提示
        self.assertIsNone(
            self.modules.GET_MISSING_MODEL_FILES("inpainter", "patchmatch")
        )

    def test_block_capable_for_batch_simple_inpaint(self):
        """逐块能力是 ``ui/batch_inpaint.py`` 选它当载体的前提。"""
        from modules.inpaint.inpaint_patchmatch import PatchmatchInpainter

        self.assertTrue(PatchmatchInpainter.inpaint_by_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
