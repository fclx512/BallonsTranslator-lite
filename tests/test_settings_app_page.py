"""Offscreen tests for the General → App page carrying the two ex-Misc options.

The App page absorbed what used to be a separate "Misc" staging page: the
Photoshop executable path (was Settings → Inpainter) and the workbench
confirmation toggle (was Settings → Translator).

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_settings_app_page.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.config import pcfg  # noqa: E402


class SettingsAppPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.configpanel import ConfigPanel

        cls.app = QApplication.instance() or QApplication([])
        cls.panel = ConfigPanel()

    def setUp(self):
        cfg = pcfg
        snapshot = (cfg.drawpanel.photoshop_path, cfg.workbench_confirm_costly)
        self.addCleanup(
            lambda: (
                setattr(pcfg.drawpanel, "photoshop_path", snapshot[0]),
                setattr(pcfg, "workbench_confirm_costly", snapshot[1]),
            )
        )

    def test_misc_page_is_gone(self):
        self.assertNotIn("misc", self.panel._nav_section_to_widget)
        self.assertFalse(hasattr(self.panel, "misc_block"))
        self.assertFalse(hasattr(self.panel, "label_misc"))

    def test_page_count_is_ten(self):
        # Modules (Models / Pipeline / LLM Profile) + General (Project /
        # Typesetting / Interface / Shortcuts / Quick Menus / App) + the
        # Workbench page (2026-09-18, made a regular page 2026-09-21).
        self.assertEqual(self.panel.pageStack.count(), 10)
        self.assertEqual(len(self.panel._nav_section_to_widget), 10)

    def test_app_page_carries_both_options(self):
        self.assertTrue(hasattr(self.panel, "ps_path_edit"))
        self.assertTrue(hasattr(self.panel, "confirm_costly_checker"))

    def test_photoshop_path_writes_config(self):
        self.panel.ps_path_edit.setText(r"D:\Adobe\Photoshop.exe")
        self.panel.on_ps_path_changed()
        self.assertEqual(
            pcfg.drawpanel.photoshop_path, r"D:\Adobe\Photoshop.exe"
        )

    def test_workbench_toggle_writes_config(self):
        # Drive both directions explicitly: the panel is shared across tests,
        # so a setChecked that matches the current state emits nothing.
        self.panel.confirm_costly_checker.setChecked(True)
        self.assertTrue(pcfg.workbench_confirm_costly)
        self.panel.confirm_costly_checker.setChecked(False)
        self.assertFalse(pcfg.workbench_confirm_costly)

    def test_workbench_page_writes_both_numbers(self):
        """工作台页（2026-09-18 建，2026-09-21 去掉「临时」字样）的两个数值项：
        初值取自 pcfg，改动回写 pcfg。"""
        snapshot = (
            pcfg.workbench_merge_oversize_ratio,
            pcfg.workbench_expand_px,
        )
        self.addCleanup(
            lambda: (
                setattr(pcfg, "workbench_merge_oversize_ratio", snapshot[0]),
                setattr(pcfg, "workbench_expand_px", snapshot[1]),
            )
        )
        self.assertIn("workbench_temp", self.panel._nav_section_to_widget)

        self.panel.merge_oversize_spin.setValue(70)
        self.assertAlmostEqual(pcfg.workbench_merge_oversize_ratio, 0.70, places=4)
        self.panel.expand_default_spin.setValue(16)
        self.assertEqual(pcfg.workbench_expand_px, 16)

        # 与设置初值对齐：改成 85% / 10 后回到默认
        self.panel.merge_oversize_spin.setValue(85)
        self.panel.expand_default_spin.setValue(10)
        self.assertAlmostEqual(pcfg.workbench_merge_oversize_ratio, 0.85, places=4)
        self.assertEqual(pcfg.workbench_expand_px, 10)

    def test_pipeline_panels_no_longer_carry_them(self):
        self.assertFalse(hasattr(self.panel.inpaint_config_panel, "ps_path_edit"))
        self.assertFalse(
            hasattr(self.panel.trans_config_panel, "_confirm_costly_checker")
        )

    def test_halfwidth_sublock_follows_its_parent(self):
        """子行走统一写法（ConfigFormRow 套 24px 缩进 wrapper），随父项开关。

        用 ``isHidden()`` 而不是 ``isVisibleTo``：Typesetting 不是当前页，
        QStackedWidget 把它显式隐藏了，``isVisibleTo(panel)`` 恒为 False。
        """
        from ui import shared_widget as SW
        from ui.configpanel import ConfigFormRow

        snapshot = pcfg.halfwidth_jp_corner_brackets
        self.addCleanup(
            setattr, pcfg, "halfwidth_jp_corner_brackets", snapshot
        )
        # The handler also re-applies the setting to every text item on the
        # canvas.  Earlier tests leave a stub there, so neutralize it — this
        # test is about layout only.
        canvas = SW.canvas
        SW.canvas = None
        self.addCleanup(setattr, SW, "canvas", canvas)

        checker = self.panel.halfwidth_corner_bracket_checker
        wrapper = self.panel._halfwidth_horizontal_sublock_wrapper
        self.assertIsInstance(
            self.panel._halfwidth_horizontal_sublock, ConfigFormRow
        )

        checker.setChecked(False)
        self.assertTrue(wrapper.isHidden())
        checker.setChecked(True)
        self.assertFalse(wrapper.isHidden())

    def test_glossary_panel_mirrors_the_confirm_toggle(self):
        """「不再提示」写 False 后，设置页复选框必须跟着变。

        ``ConfigPanel.setupConfig`` 只在启动跑一次，不回写的话复选框会停在
        启动时的旧值；回写还得屏蔽信号，否则 toggled 会把 pcfg 又翻回 True。
        """
        from qtpy.QtWidgets import QWidget

        from ui.glossary_agent_panel import GlossaryAgentPanel

        # Keep the stand-in window alive on the test instance: if the local
        # dropped it, Qt would destroy the child panel before cleanups run.
        self._fake_window = QWidget()
        self._fake_window.configPanel = self.panel
        workbench = GlossaryAgentPanel(None, self._fake_window)

        self.panel.confirm_costly_checker.setChecked(True)
        pcfg.workbench_confirm_costly = False  # what the glossary panel writes
        workbench._sync_confirm_costly_checkbox()

        self.assertFalse(self.panel.confirm_costly_checker.isChecked())
        self.assertFalse(pcfg.workbench_confirm_costly)


if __name__ == "__main__":
    unittest.main()
