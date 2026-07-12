"""Startup smoke test: verify the critical import chain is free of errors.

Catches missing imports (NameError, ModuleNotFoundError) that slip past
syntax-only checks.  Run after touching configpanel.py, profile_manager.py,
or launch.py init-time code.

Usage:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_startup_imports.py -v
    # or directly:
    ./ballontrans_pylibs_win/python.exe tests/test_startup_imports.py
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


class TestStartupImports(unittest.TestCase):
    """Verify that import-level code does not raise."""

    def _check(self, label: str, script: str):
        """Run `script` as Python code and fail if stderr is non-empty."""
        result = subprocess.run(
            [PYTHON, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            msg = (
                f"::{label}:: exit {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
            self.fail(msg)

    def _import_test(self, label: str, import_stmt: str):
        """Assert that `import_stmt` runs without error."""
        self._check(label, f"import sys; sys.path.insert(0, '.'); {import_stmt}")

    # ── Individual import chains ────────────────────────────────────────

    def test_01_utils_config(self):
        """utils.config imports cleanly."""
        self._import_test("utils.config", "from utils import config; print('OK')")

    def test_02_utils_profile_manager(self):
        """utils.profile_manager imports cleanly."""
        self._import_test(
            "utils.profile_manager",
            "from utils.profile_manager import (load_profiles, save_all_profiles, "
            "migrate_old_profiles, ProfileManagerWidget); print('OK')",
        )

    def test_03_launch_top_level(self):
        """launch.py top-level imports execute (before main())."""
        self._import_test(
            "launch top-level",
            # only run the top-of-file imports, not main()
            "import launch; print('OK')",
        )

    def test_05_profile_manager_widget_init(self):
        """ProfileManagerWidget can be instantiated (catches missing QFrame etc.)."""
        script = """
import sys, os
sys.path.insert(0, '.')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import qtpy.QtWidgets as QW
app = QW.QApplication.instance() or QW.QApplication(sys.argv + ['--platform', 'offscreen'])

from utils.profile_manager import ProfileManagerWidget

# Instantiate without parent — exercises _build_ui() which uses QFrame, QLabel, etc.
w = ProfileManagerWidget()
print(f'OK: ProfileManagerWidget created ({w})')
"""
        self._check("ProfileManagerWidget()", script)

    def test_06_configpanel_import(self):
        """ConfigPanel module imports without syntax errors."""
        self._import_test(
            "ui.configpanel",
            "from ui.configpanel import ConfigPanel; print('OK')",
        )


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStartupImports)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
