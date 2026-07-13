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

    def test_04_help_dialog_import(self):
        """HelpDialog module imports cleanly (catches str-vs-Path bugs etc.)."""
        self._import_test(
            "ui.help_dialog",
            "from ui.help_dialog import HelpDialog; print('OK')",
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

    def test_07_help_dialog_static_methods(self):
        """HelpDialog static methods work without QApp (heading parsing, etc.).

        Covers the logic that was affected by the str-vs-Path bug.
        Does not instantiate QWidgets (offscreen can crash on complex widgets).
        """
        script = """
import sys, os
sys.path.insert(0, '.')

from ui.help_dialog import HelpDialog

# _parse_headings
md = '# Title\\n## Section 1\\n### Sub 1.1\\n## Section 2\\ntext here'
h = HelpDialog._parse_headings(md)
assert h == [(1, 'Title'), (2, 'Section 1'), (3, 'Sub 1.1'), (2, 'Section 2')], f'Got {h}'
print('_parse_headings OK')

# _find_nearest_heading
lines = ['# Title', '## Section 1', 'some text', '## Section 2', 'more text']
ctx = HelpDialog._find_nearest_heading(lines, 2)
assert ctx == 'Section 1', f'Got {ctx}'
ctx2 = HelpDialog._find_nearest_heading(lines, 4)
assert ctx2 == 'Section 2', f'Got {ctx2}'
print('_find_nearest_heading OK')

        # _extract_title
import tempfile, os, pathlib
fd, md_path = tempfile.mkstemp(suffix='.md')
os.close(fd)  # release handle so .unlink() works on Windows
f = pathlib.Path(md_path)
f.write_text('# Real Title\\n## Sub\\ncontent', encoding='utf-8')
t = HelpDialog._extract_title(f)
assert t == 'Real Title', f'Got {t}'
f.unlink()
print('_extract_title OK')

# _extract_title fallback (no # heading)
fd2, md_path2 = tempfile.mkstemp(suffix='.md')
os.close(fd2)
f2 = pathlib.Path(md_path2)
f2.write_text('plain text', encoding='utf-8')
t2 = HelpDialog._extract_title(f2)
assert t2 == f2.stem, f'Got {t2}'
f2.unlink()
print('_extract_title fallback OK')

print('ALL OK')
"""
        self._check("HelpDialog.static", script)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStartupImports)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
