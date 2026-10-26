"""模型使用说明：静态文案覆盖与设置页面板跟随引擎切换。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_model_intros.py -q
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.model_intros import INTRO_TEXTS, intro_for  # noqa: E402


class ModelIntroTextTest(unittest.TestCase):
    def test_every_registered_engine_has_copy(self):
        """设置页会展示每个引擎的说明；漏写文案会显示空说明。"""
        for key in (
            "textdetector:ysgyolo",
            "textdetector:ysg_ctd",
            "textdetector:ppocrv6_onnx",
            "textdetector:none",
            "ocr:paddleocr_v6_onnx",
            "ocr:paddleocr_vl_manga",
            "ocr:llm_ocr",
            "ocr:none_ocr",
            "inpainter:patchmatch",
            "inpainter:lama_large_512px",
            "inpainter:none",
            "translator:LLM_Agent_Translator",
            "translator:LLM_API_Translator",
        ):
            self.assertTrue(INTRO_TEXTS.get(key), key)

    def test_unknown_module_and_stage_mismatch_return_empty(self):
        self.assertEqual(intro_for("textdetector", "unknown"), "")
        # 键带阶段前缀：同名模块在别的阶段不算命中。
        self.assertEqual(intro_for("inpainter", "ysg_ctd"), "")


class ModelIntroPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.module_parse_widgets import ModuleConfigParseWidget

        self.panel = ModuleConfigParseWidget(
            "Detector",
            lambda: ["ysgyolo", "ysg_ctd"],
            scrollWidget=None,
        )
        self.panel.stage_key = "textdetector"
        self.panel.addModulesParamWidgets(
            {
                "ysgyolo": {"description": "d"},
                "ysg_ctd": {"description": "d"},
            }
        )
        # 下拉首项是分隔符（空文本），真实流程由模块管理器随后 setModule 选中引擎。
        self.panel.setModule("ysgyolo")

    def test_intro_follows_engine_switch(self):
        self.assertEqual(
            self.panel.intro_label.text(), intro_for("textdetector", "ysgyolo")
        )
        self.panel.setModule("ysg_ctd")
        self.assertEqual(
            self.panel.intro_label.text(), intro_for("textdetector", "ysg_ctd")
        )


if __name__ == "__main__":
    unittest.main()
