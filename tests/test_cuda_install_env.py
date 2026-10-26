"""Offline contract checks for the bundled CUDA converter."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import install_cuda_bundle as cuda  # noqa: E402
from utils.env_diagnostic import pick_cuda_index  # noqa: E402


def state_for(tag="cpu", good=False):
    return {"torch": "2.13.0+" + tag, "vision": "0.28.0+" + tag,
            "torch_import": "2.13.0+" + tag, "vision_import": "0.28.0+" + tag,
            "ort_cpu": "1.20.1" if tag == "cpu" else None,
            "ort_gpu": None if tag == "cpu" else "1.27.0",
            "torch_ok": good, "ort_ok": good, "details": []}


class CudaPairTests(unittest.TestCase):
    def test_compute_capability_selects_matching_ort_major(self):
        expected = {6: ("cu126", "onnxruntime-gpu==1.20.1"),
                    8: ("cu126", "onnxruntime-gpu==1.20.1"),
                    9: ("cu130", "onnxruntime-gpu==1.27.0"),
                    10: ("cu132", "onnxruntime-gpu==1.27.0")}
        self.assertIsNone(pick_cuda_index(None))
        self.assertIsNone(pick_cuda_index(5))
        for cc, (tag, ort_spec) in expected.items():
            with self.subTest(cc=cc):
                self.assertEqual(pick_cuda_index(cc)["index_tag"], tag)
                self.assertEqual(pick_cuda_index(cc)["onnxruntime_spec"], ort_spec)

    def test_good_runtime_is_preserved(self):
        state = state_for("cu132", True)
        self.assertEqual(cuda.plan({}, state), (False, False))

    def test_known_cpu_pair_may_be_converted(self):
        state = state_for()
        self.assertEqual(cuda.plan({}, state), (True, True))

    def test_unknown_or_mixed_pair_refuses_conversion(self):
        for pair in (("2.14.0+cpu", "0.28.0+cpu"),
                     ("2.13.0+cu132", "0.28.0+cpu")):
            with self.subTest(pair=pair):
                with self.assertRaises(RuntimeError):
                    cuda.plan({}, {"torch": pair[0], "vision": pair[1],
                                   "torch_ok": False, "ort_ok": False})


class ScriptTests(unittest.TestCase):
    def test_cpu_metadata_with_cuda_files_is_rejected(self):
        state = state_for()
        state["vision_import"] = "0.28.0+cu132"
        with self.assertRaises(RuntimeError):
            cuda.plan({}, state)

    def test_missing_package_is_not_a_convertible_bundle(self):
        state = state_for()
        state["vision"] = None
        with self.assertRaises(RuntimeError):
            cuda.plan({}, state)

    def test_cpu_vision_cannot_pass_cuda_probe(self):
        torch = mock.Mock(__version__="2.13.0+cu132")
        vision = mock.Mock(__version__="0.28.0+cpu")
        versions = {"torch": torch.__version__, "torchvision": vision.__version__,
                    "onnxruntime": None, "onnxruntime-gpu": "1.27.0"}
        with mock.patch.dict(sys.modules, {"torch": torch, "torchvision": vision}), \
             mock.patch.object(cuda, "version", side_effect=versions.get):
            state = cuda.runtime_state({"cuda_major": "13", "index_tag": "cu132", "ort_version": "1.27.0"})
        self.assertFalse(state["torch_ok"])
        self.assertIn("build tags disagree", " ".join(state["details"]))

    def test_batch_uses_only_exact_bundled_python(self):
        bat = (ROOT / "install_cuda.bat").read_text(encoding="utf-8")
        self.assertIn("ballontrans_pylibs_win\\python.exe", bat)
        self.assertIn("scripts\\install_cuda_bundle.py", bat)
        self.assertNotIn("where python", bat.lower())
        self.assertIn("--install", bat)

    def test_default_is_read_only(self):
        with mock.patch.object(cuda, "require_bundled_python"), \
             mock.patch.object(cuda, "_query_compute_cap", return_value=10), \
             mock.patch.object(cuda, "probe_state_subprocess", return_value=state_for()), \
             mock.patch.object(cuda, "run") as install_command, \
             mock.patch.object(sys, "argv", ["install_cuda_bundle.py"]):
            self.assertEqual(cuda.main(), 0)
        install_command.assert_not_called()


class ConversionTests(unittest.TestCase):
    target = {"index_tag": "cu132", "url": "https://download.pytorch.org/whl/cu132", "ort_version": "1.27.0"}

    def test_offline_preparation_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cuda.tempfile, "mkdtemp", return_value=tmp), \
             mock.patch.object(cuda, "download_wheels", side_effect=RuntimeError("offline")), \
             mock.patch.object(cuda, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "before any installed package"):
                cuda.convert(self.target, state_for(), True, True, Path(tmp) / "constraints")
            run.assert_not_called()

    def test_verification_failure_restores_original_wheels(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cuda.tempfile, "mkdtemp", return_value=tmp), \
             mock.patch.object(cuda, "download_wheels", side_effect=[
                 ["target-torch.whl", "target-vision.whl"], ["old-torch.whl", "old-vision.whl"],
                 ["target-ort.whl"], ["old-ort.whl"]]) as download, \
             mock.patch.object(cuda, "run") as run, \
             mock.patch.object(cuda, "probe_state_subprocess", return_value=state_for()):
            with self.assertRaisesRegex(RuntimeError, "Original wheels restored"):
                cuda.convert(self.target, state_for(), True, True, Path(tmp) / "constraints")
            self.assertEqual(download.call_count, 4)
            restored = run.call_args.args[0]
            self.assertIn("--no-index", restored)
            self.assertEqual(restored[-3:], ["old-torch.whl", "old-vision.whl", "old-ort.whl"])

    def test_healthy_torch_is_not_replaced_during_ort_conversion(self):
        state = state_for("cu132", True)
        state["ort_ok"] = False
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cuda.tempfile, "mkdtemp", return_value=tmp), \
             mock.patch.object(cuda, "download_wheels", return_value=["ort.whl"]) as download, \
             mock.patch.object(cuda, "run") as run, \
             mock.patch.object(cuda, "probe_state_subprocess", return_value=state_for("cu132", True)):
            cuda.convert(self.target, state, False, True, Path(tmp) / "constraints")
            for call in download.call_args_list:
                self.assertFalse(any(req.startswith("torch") for req in call.args[0]))
            self.assertTrue(all("torch" not in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
