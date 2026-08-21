"""Tests for the auto-squeeze-after-run toggle.

Locks the behavior introduced to let users keep manually placed text
block sizes when running the pipeline before translation: the squeeze in
``MainWindow.on_pagtrans_finished`` must be gated by
``pcfg.auto_squeeze_after_run`` (default on, preserving the legacy
squeeze-anyway behavior), so users can opt out by unchecking it.

Checked via AST because :meth:`ui.mainwindow.MainWindow.on_pagtrans_finished`
depends on a fully constructed MainWindow, which is too heavy for a unit test.
"""

import ast
import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)


def _pagtrans_finished_squeeze_node(tree, source):
    """Return the AST If node guarding the squeeze loop in
    on_pagtrans_finished, or None if it could not be located."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "on_pagtrans_finished":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            src = ast.get_source_segment(source, sub)
            if src is None:
                continue
            if "squeezeBoundingRect" in src:
                return sub
    return None


class TestAutoSqueezeToggle(unittest.TestCase):
    def test_config_default_is_on(self):
        """Default stays on so existing behavior is unchanged for all users."""
        from utils.config import pcfg

        self.assertIn("auto_squeeze_after_run", dir(pcfg))
        self.assertTrue(pcfg.auto_squeeze_after_run)

    def test_squeeze_gated_by_toggle(self):
        """The on_pagtrans_finished squeeze must reference the toggle."""
        source = open(
            osp.join(APP_ROOT, "ui", "mainwindow.py"), "r", encoding="utf-8"
        ).read()
        tree = ast.parse(source)
        node = _pagtrans_finished_squeeze_node(tree, source)
        self.assertIsNotNone(node, "squeeze loop in on_pagtrans_finished not found")
        self.assertIn("auto_squeeze_after_run", ast.unparse(node))

    def test_toggle_wired_in_configpanel(self):
        """ConfigPanel must restore the toggle from pcfg and persist it."""
        with open(osp.join(APP_ROOT, "ui", "configpanel.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("auto_squeeze_checker.setChecked(pcfg.auto_squeeze_after_run)", src)
        self.assertIn("pcfg.auto_squeeze_after_run = self.auto_squeeze_checker.isChecked()", src)


if __name__ == "__main__":
    unittest.main()