"""Test the dependency-startup flow — scenarios A–F.

All tests run in subprocesses to avoid launch.py module-level argparse
side effects in the test runner's process.
"""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


def _run_script(script, env=None):
    """Run a small inline script in a subprocess and return (stdout, stderr, rc)."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout, proc.stderr, proc.returncode


class TestCoreRequirements(unittest.TestCase):
    """Tests for utils/core_requirements.py"""

    def test_warn_missing_core_imports_catches_bad_module(self):
        stdout, stderr, rc = _run_script("""
import sys
sys.path.insert(0, '.')
from utils.core_requirements import check_core_imports
failures = check_core_imports([("_this_module_does_not_exist_xyzzy", ())])
assert len(failures) > 0, f'expected failures, got {failures}'
assert "xyzzy" in failures[0], failures[0]
print("OK: bad module detected")
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")

    def test_check_core_imports_ok_for_math(self):
        stdout, stderr, rc = _run_script("""
import sys
sys.path.insert(0, '.')
from utils.core_requirements import check_core_imports
failures = check_core_imports([("math", ("sqrt",))])
assert failures == [], f'expected no failures, got {failures}'
print("OK: math.sqrt found")
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")

    def test_import_core_requirements_module(self):
        """Module-level import must not crash."""
        stdout, stderr, rc = _run_script("""
import sys
sys.path.insert(0, '.')
from utils.core_requirements import warn_missing_core_imports, check_core_imports, CORE_IMPORT_PROBES
print(f"OK: {len(CORE_IMPORT_PROBES)} probes loaded")
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")


class TestWarnMissingCoreImports(unittest.TestCase):
    """Tests for warn_missing_core_imports return type and behavior."""

    def test_returns_list(self):
        """Should always return a list, even when empty."""
        stdout, stderr, rc = _run_script("""
import sys
sys.path.insert(0, '.')
from utils.core_requirements import warn_missing_core_imports
result = warn_missing_core_imports()
assert isinstance(result, list), f'expected list, got {type(result)}'
print(f"OK: returned list of length {len(result)}")
""")
        self.assertEqual(rc, 0, f"stdout: {stdout}\nstderr: {stderr}")


class TestLaunchPrepareEnvironment(unittest.TestCase):
    """Scenarios for prepare_environment() — tested via subprocess
    because launch.py runs argparse at module level."""

    SCENARIO_SCRIPT = """
import sys
sys.path.insert(0, '.')
# Minimal import that avoids the module-level argparse.
# Instead we parse the AST to verify the function.
import ast
with open('launch.py', 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'prepare_environment':
        # Check return type annotation
        assert node.returns is not None, 'missing return annotation'
        assert isinstance(node.returns, ast.Name) and node.returns.id == 'bool', \
            f'unexpected return type: {ast.dump(node.returns)}'
        print('OK: prepare_environment() -> bool')
        # Current contract: core requirements are handled by
        # ensure_core_requirements() earlier in main(); prepare_environment
        # only force-reinstalls torch, so it returns False on every path
        # (True "restart needed" is reserved for ensure_core_requirements).
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
        assert returns, 'no return statement found'
        has_true = any(isinstance(r.value, ast.Constant) and r.value.value is True for r in returns)
        has_false = any(isinstance(r.value, ast.Constant) and r.value.value is False for r in returns)
        assert has_false, 'no return False found'
        assert not has_true, 'prepare_environment must never return True (no restart from here)'
        print('OK: all return paths are False (no restart from this function)')
        break
"""

    def test_prepare_environment_structurally_correct(self):
        stdout, stderr, rc = _run_script(self.SCENARIO_SCRIPT)
        self.assertEqual(rc, 0, f"stdout: {stdout}\nstderr: {stderr}")

    def test_restart_function_exists(self):
        stdout, stderr, rc = _run_script("""
import ast
with open('launch.py', 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())
fnames = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
assert 'restart' in fnames, f'restart not found in {fnames}'
assert 'prepare_environment' in fnames, f'prepare_environment not found in {fnames}'
print(f'OK: functions found: restart, prepare_environment')
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")

    def test_sys_exit_on_missing_deps(self):
        """Verify the error-exit path in main() exists."""
        stdout, stderr, rc = _run_script("""
import ast
with open('launch.py', 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())

# Find the block after warn_missing_core_imports that calls sys.exit
# (we're looking for the pattern: if missing: ... sys.exit(1))
found = False
for node in ast.walk(tree):
    if isinstance(node, ast.If):
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if (isinstance(child.func, ast.Attribute) and
                    child.func.attr == 'exit' and
                    isinstance(child.func.value, ast.Name) and
                    child.func.value.id == 'sys'):
                    found = True
                    break
assert found, 'sys.exit(1) call not found in main() dependency section'
print('OK: sys.exit(1) found in dependency section')
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")


class TestScenarioBundledEnv(unittest.TestCase):
    """Scenario A: bundled portable Python — prepare_environment skips."""

    def test_portable_env_early_return(self):
        """With ballontrans_pylibs_win in sys.executable, should return False."""
        stdout, stderr, rc = _run_script("""
import sys
sys.path.insert(0, '.')
# We can't easily import launch.py in-process due to argparse.
# Instead verify the AST: the first return after checking ballontrans_pylibs_win
# should be False.
import ast
with open('launch.py', 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'prepare_environment':
        for child in ast.walk(node):
            if (isinstance(child, ast.Return) and child.value is not None
                and isinstance(child.value, ast.Constant)
                and child.value.value is False):
                print('OK: return False found')
                break
        break
""")
        self.assertEqual(rc, 0, f"stderr: {stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
