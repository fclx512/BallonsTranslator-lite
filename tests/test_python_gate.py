"""Python 版本闸门回归测试（launch.py / launch.bat）。

本轮给启动链路加了两道闸门：`launch.py` 在导入任何项目本地模块**之前**拒绝
Python < 3.10（否则 `utils.*` 的 3.10 语法会先报出难以理解的 SyntaxError），
`launch.bat` 的四条解释器发现分支（嵌入式 / py launcher / python3 / PATH python）
都要做同样的版本检查。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_python_gate.py -v
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import launch  # noqa: E402


class PythonVersionGateTests(unittest.TestCase):
    def test_minimum_is_3_10(self):
        self.assertEqual(launch.MIN_PYTHON, (3, 10))

    def test_supported_boundaries(self):
        self.assertFalse(launch.python_version_supported((3, 9, 9)))
        self.assertFalse(launch.python_version_supported((2, 7, 18)))
        self.assertTrue(launch.python_version_supported((3, 10, 0)))
        self.assertTrue(launch.python_version_supported((3, 13, 1)))

    def test_error_message_is_actionable(self):
        message = launch.python_version_error((3, 9, 12))
        self.assertIn("3.10", message)
        self.assertIn("3.9", message)
        self.assertEqual(launch.python_version_error((3, 10, 0)), "")

    def test_current_interpreter_is_supported(self):
        # The test suite runs on a supported interpreter; a failure here means
        # the gate would reject the very Python this repo is developed on.
        self.assertTrue(launch.python_version_supported())

    def test_gate_runs_before_project_local_imports(self):
        """闸门必须在 ``import utils.shared`` 等本地导入之前执行。"""
        source = (REPO_ROOT / "launch.py").read_text(encoding="utf8")
        call_pos = source.index("\nenforce_python_version()\n")
        first_local_import = source.index("\nimport utils.shared")
        self.assertLess(call_pos, first_local_import)


class LaunchBatVersionGateTests(unittest.TestCase):
    def test_every_discovery_branch_checks_version(self):
        """四条发现分支都要有 ``sys.version_info >= (3,10)`` 检查。

        Option B（py launcher）此前已有；A/C/D 是本轮补的。
        """
        bat = (REPO_ROOT / "launch.bat").read_text(encoding="utf8")
        checks = re.findall(r"sys\.version_info\s*>=\s*\(3,\s*10\)", bat)
        self.assertGreaterEqual(
            len(checks), 4, f"expected a version check per branch, found {len(checks)}"
        )


if __name__ == "__main__":
    unittest.main()
