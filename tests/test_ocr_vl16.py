"""Regression for the real first-generation mask error and visible failures."""
import sys
import types
import unittest
from unittest import mock

from modules.ocr.ocr_vl16 import PaddleOCRVL16


class PaddleOCRVL16CompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.module = PaddleOCRVL16()
        self.remote = types.ModuleType("vl16_mask_test")
        self.module.model = type("Model", (), {"__module__": self.remote.__name__})()
        self.patch = mock.patch.dict(sys.modules, {self.remote.__name__: self.remote})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_first_generation_keyword_is_adapted_and_reload_is_idempotent(self):
        received = []

        def mask(input_embeds, **kwargs):
            received.append((input_embeds, kwargs))
            return input_embeds

        self.remote.create_causal_mask = mask
        self.module._prepare_loaded_model()
        adapted = self.remote.create_causal_mask
        tensor = object()
        self.assertIs(adapted(inputs_embeds=tensor, use_cache=True), tensor)
        self.assertEqual(received, [(tensor, {"use_cache": True})])
        self.module._prepare_loaded_model()
        self.assertIs(self.remote.create_causal_mask, adapted)

    def test_compatible_official_interface_is_left_intact(self):
        def mask(inputs_embeds):
            return inputs_embeds

        self.remote.create_causal_mask = mask
        self.module._prepare_loaded_model()
        self.assertIs(self.remote.create_causal_mask, mask)

    def test_broken_inference_reaches_caller_without_partial_text_writeback(self):
        blocks = [types.SimpleNamespace(text=["before"]), types.SimpleNamespace(text=["before"])]
        with mock.patch.object(self.module, "all_model_loaded", return_value=True), \
             mock.patch.object(self.module, "crop_block", return_value=object()), \
             mock.patch.object(self.module, "_run_batch", side_effect=[[["recognized"]], RuntimeError("CUDA failure")]):
            with self.assertRaisesRegex(RuntimeError, "CUDA failure"):
                self.module._ocr_blk_list(object(), blocks)
        self.assertEqual([block.text for block in blocks], [["before"], ["before"]])

    def test_failed_model_load_releases_lock_for_switching_engines(self):
        with mock.patch.object(self.module, "ensure_dependencies"), \
             mock.patch.object(self.module, "_ensure_model_files"), \
             mock.patch.object(self.module, "_load_model", side_effect=RuntimeError("bad model")), \
             mock.patch("modules.base.aquire_model_loading_lock"), \
             mock.patch("modules.base.release_model_loading_lock") as release:
            with self.assertRaisesRegex(RuntimeError, "bad model"):
                self.module.load_model()
        release.assert_called_once_with()

    def test_device_change_unloads_model_but_length_change_does_not(self):
        self.module.params = {key: value.copy() if isinstance(value, dict) else value
                              for key, value in self.module.params.items()}
        self.module.set_param_value("device", "cuda")
        with mock.patch.object(self.module, "unload_model") as unload:
            self.module.updateParam("max_new_tokens", "512")
            unload.assert_not_called()
            self.module.updateParam("device", "cpu")
            unload.assert_called_once_with(empty_cache=True)


if __name__ == "__main__":
    unittest.main()
