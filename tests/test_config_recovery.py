"""配置损坏恢复、缺失配置键容错与版本来源的回归测试。

启动可靠性的最后一环是"配置坏了/目录只读时仍能起来，且别把用户数据删了"：

1. ``config.json`` 损坏 → 备份成 ``config.json.corrupt-*``，而不是静默丢弃；
2. 合法但残留的 ``config.json.tmp``（``save_config`` 中断留下，内容是更完整的
   配置）→ 自动恢复并采纳；
3. 手改配置缺 ``module.translator_params`` 等键时 ``ProgramConfig.load`` 不再
   KeyError；
4. 只读的默认文字样式目录只降级（无持久样式），不阻断启动；
5. ``utils/config.py::_get_app_version`` 必须与 ``utils/version.py::APP_VERSION``
   一致（旧实现查错了发行包名 + 不存在的 ``launch.__version__``，回落到硬编码
   的 0.3.0）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_config_recovery.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils import config as program_config  # noqa: E402
from utils import shared  # noqa: E402


class ConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

        self._orig_config_path = shared.CONFIG_PATH
        self._orig_textstyle_path = program_config.pcfg.text_styles_path
        self._orig_textstyle_dir = shared.DEFAULT_TEXTSTYLE_DIR

        shared.DEFAULT_TEXTSTYLE_DIR = str(self.tmp / "textstyles")
        program_config.pcfg.text_styles_path = str(self.tmp / "styles.json")
        shared.CONFIG_PATH = str(self.tmp / "config.json")

    def tearDown(self):
        shared.CONFIG_PATH = self._orig_config_path
        program_config.pcfg.text_styles_path = self._orig_textstyle_path
        shared.DEFAULT_TEXTSTYLE_DIR = self._orig_textstyle_dir

    def _write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf8")
        return path

    def test_corrupt_config_is_quarantined(self):
        cfg = self._write("config.json", "{ this is not json")

        program_config.load_config(str(cfg))

        backups = list(self.tmp.glob("config.json.corrupt-*"))
        self.assertEqual(len(backups), 1, "corrupt config must be kept aside")
        self.assertIn("not json", backups[0].read_text(encoding="utf8"))
        self.assertFalse(cfg.exists(), "corrupt file must be moved, not overwritten in place")

    def test_valid_tmp_is_recovered(self):
        cfg = self._write("config.json", "{ broken")
        self._write(
            "config.json.tmp",
            json.dumps({"display_lang": "简体中文"}, ensure_ascii=False),
        )

        program_config.load_config(str(cfg))

        self.assertTrue(cfg.exists(), "the recovered config must be adopted")
        self.assertEqual(program_config.pcfg.display_lang, "简体中文")
        self.assertEqual(len(list(self.tmp.glob("config.json.corrupt-*"))), 1)

    def test_valid_tmp_is_recovered_without_existing_config(self):
        cfg = self.tmp / "config.json"  # no config.json at all
        self._write(
            "config.json.tmp", json.dumps({"display_lang": "简体中文"})
        )

        program_config.load_config(str(cfg))

        self.assertTrue(cfg.exists())
        self.assertEqual(program_config.pcfg.display_lang, "简体中文")

    def test_missing_module_keys_do_not_raise(self):
        cfg = self._write(
            "config.json",
            json.dumps({"module": {"textdetector": "ctd"}}),
        )

        program_config.load_config(str(cfg))

        # The missing translator_params key must be tolerated, and the stale
        # detector id still migrated by the compat block.
        self.assertEqual(program_config.pcfg.module.textdetector, "ysgyolo")

    def test_bad_startup_fields_do_not_discard_other_settings(self):
        cfg = self._write("config.json", json.dumps({
            "display_lang": None,
            "mirror": None,
            "global_fontformat": [],
            "darkmode": True,
            "module": {
                "translator": "LLM_API_Translator",
                "translator_params": {"LLM_API_Translator": "invalid"},
                "ocr_params": {"paddleocr_v6_onnx": {"device": "cpu"}},
            },
        }))
        with patch.object(program_config, "pcfg", program_config.ProgramConfig()):
            program_config.load_config(str(cfg))
            loaded = program_config.pcfg
            self.assertTrue(loaded.darkmode)
            self.assertEqual(loaded.display_lang, program_config.ProgramConfig().display_lang)
            self.assertIsInstance(loaded.mirror, program_config.MirrorConfig)
            self.assertEqual(loaded.module.translator, "LLM_API_Translator")
            self.assertEqual(loaded.module.translator_params, {})
            self.assertEqual(loaded.module.ocr_params["paddleocr_v6_onnx"]["device"], "cpu")

    def test_unwritable_textstyle_dir_only_degrades(self):
        blocker = self.tmp / "styles_dir"
        blocker.write_text("not a dir", encoding="utf8")
        shared.DEFAULT_TEXTSTYLE_DIR = str(blocker)
        cfg = self._write(
            "config.json",
            json.dumps({"text_styles_path": str(self.tmp / "missing.json")}),
        )

        # Must return normally instead of propagating the write failure.
        program_config.load_config(str(cfg))

    def test_import_failure_restores_current_config(self):
        original = program_config.ProgramConfig(display_lang="en_US")
        original.module.enable_ocr = True
        export = self._write(
            "export.json", '{"display_lang": "简体中文", "module": {"enable_ocr": false}}'
        )
        with patch.object(program_config, "pcfg", original), patch.object(
            program_config, "save_config", return_value=False
        ):
            result = program_config.import_config(str(export))

        self.assertFalse(result["success"])
        self.assertEqual(original.display_lang, "en_US")
        self.assertTrue(original.module.enable_ocr)

    def test_redacted_export_covers_current_and_legacy_credentials(self):
        secret = "audit-placeholder-credential"
        original = program_config.ProgramConfig()
        original.module.model_profiles = json.dumps([
            {"name": "test", "model": "example", "api_key": secret, "multiple_keys": secret}
        ])
        original.module.translator_params = {"LLM_API_Translator": {
            "proxy": {"value": secret},
            "_profiles_storage": {"value": json.dumps([{"name": "old", "proxy": secret}])},
        }}
        share = self.tmp / "share.json"
        private = self.tmp / "private.json"
        with patch.object(program_config, "pcfg", original):
            self.assertTrue(program_config.export_config(str(share)))
            self.assertTrue(program_config.export_config(str(private), exclude_api_keys=False))

        data = share.read_text(encoding="utf8")
        self.assertNotIn(secret, data)
        self.assertIn(secret, private.read_text(encoding="utf8"))
        self.assertIn(secret, original.module.model_profiles)
        export = json.loads(data)
        self.assertEqual(json.loads(export["module"]["model_profiles"])[0]["model"], "example")
        self.assertIn("module.model_profiles[].multiple_keys", export["_export_meta"]["excluded"])

    def test_redacted_export_with_bad_profile_storage_keeps_previous_file(self):
        path = self._write("export.json", '{"previous": true}')
        original = program_config.ProgramConfig()
        original.module.model_profiles = "unparseable credential payload"
        with patch.object(program_config, "pcfg", original):
            self.assertFalse(program_config.export_config(str(path)))
        self.assertEqual(json.loads(path.read_text(encoding="utf8")), {"previous": True})


class AppVersionConsistencyTests(unittest.TestCase):
    def test_export_version_matches_single_source(self):
        from utils.version import APP_VERSION

        self.assertEqual(program_config._get_app_version(), APP_VERSION)

    def test_version_is_not_the_old_hardcoded_fallback(self):
        # 旧实现回落到硬编码 "0.3.0"；当前 pyproject 版本高于它。
        self.assertNotEqual(program_config._get_app_version(), "0.3.0")


if __name__ == "__main__":
    unittest.main()
