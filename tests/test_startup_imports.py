"""Startup smoke test: verify the critical import chain is free of errors.

Catches missing imports (NameError, ModuleNotFoundError) that slip past
syntax-only checks.  Run after touching configpanel.py, profile_manager.py,
or launch.py init-time code.

Runs all checks in a single process — imports are idempotent, so the old
subprocess-per-check version (5 cold PyQt launches) is unnecessary.

Usage:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_startup_imports.py -v
    # or directly:
    ./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.test_config_sandbox import install_config_sandbox  # noqa: E402

install_config_sandbox()

# Everything runs offscreen — no window pops up during the smoke test.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestStartupImports(unittest.TestCase):
    """Verify that import-level code does not raise."""

    def test_01_utils_config(self):
        from utils import config  # noqa: F401
        print("OK: utils.config imports")

    def test_02_utils_profile_manager(self):
        from utils.profile_manager import (  # noqa: F401
            load_profiles,
            save_all_profiles,
            migrate_old_profiles,
        )
        print("OK: utils.profile_manager imports")

    def test_03_launch_top_level(self):
        # launch.py calls parse_known_args() at module level — feed it an empty
        # argv so it behaves like a plain `python launch.py` run. main() is
        # guarded by `if __name__ == "__main__"`, so nothing actually launches.
        old_argv = sys.argv
        sys.argv = ["launch.py"]
        try:
            import launch  # noqa: F401
            print("OK: launch top-level imports")
        finally:
            sys.argv = old_argv

    def test_05_llm_profile_list_widget_init(self):
        """LLMProfileListWidget can be instantiated (catches missing imports)."""
        import qtpy.QtWidgets as QW
        app = QW.QApplication.instance() or QW.QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )
        from ui.llm_profile_cards import LLMProfileListWidget

        w = LLMProfileListWidget()
        print(f"OK: LLMProfileListWidget created ({w}, {len(w._cards)} cards)")

    def test_06_configpanel_import(self):
        from ui.configpanel import ConfigPanel  # noqa: F401
        print("OK: ui.configpanel imports")

    def test_07_run_pipeline_dialog_init(self):
        """RunPipelineDialog can be built (catches missing icons/imports).

        The desktop run entry is the embedded PipelineWorkbench opened by
        ``MainWindow.run_imgtrans``, which the smoke test cannot reach, so
        build the base dialog here instead.
        """
        import qtpy.QtWidgets as QW
        app = QW.QApplication.instance() or QW.QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )
        from ui.run_pipeline_dialog import RunPipelineDialog

        d = RunPipelineDialog(None, page_names=["001.jpg", "002.jpg"])
        n = len(d._stage_activators)
        print(f"OK: RunPipelineDialog created ({d}, {n} stage activators)")
        d.deleteLater()


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStartupImports)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
