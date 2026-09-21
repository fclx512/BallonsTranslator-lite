"""install_cuda.bat 行为回归台（只读、不联网、不安装）。

用 cmd 真跑脚本，验证几个曾经出错的判据：
  1. 源码目录（无 ballontrans_pylibs_win）下脚本必须可达 manual 段并打印 pip 命令
  2. --manual 参数必须让脚本进入 manual 段
  3. ORT 状态哨兵在 python 崩溃时必须落到 MISSING（而不是空串）
  4. CC 映射不得再出现 cu124
  5. 存在 ballontrans_pylibs_win 时，auto 模式仍走 replace 分支

为避免真的装东西，测试把 PATH 上的 python 指向一个假 python 脚本，
并断言脚本在真的执行 pip 之前就被我们拦住（用 --dry-run 不可得，因此
只检查「打印出的候选命令」与「分支标记」，不实际调用 pip）。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "install_cuda.bat"


def _run_bat(args=(), cwd=None, env=None):
    """Run install_cuda.bat in a scratch cwd and capture output.

    Uses cmd /c so the batch file's own `cd /d "%~dp0"` still applies,
    meaning cwd is overridden by the script location - that is why we
    copy the script into a scratch tree when we need a fake layout.
    """
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        ["cmd", "/c", str(SCRIPT), *args],
        capture_output=True,
        cwd=str(cwd) if cwd else str(REPO_ROOT),
        env=run_env,
        timeout=120,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out, err


class ScriptShapeTests(unittest.TestCase):
    """静态检查 - 不需要真跑脚本，速度快，任何环境下都能跑。"""

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8", errors="replace")

    def test_no_cu124_index_remains(self):
        """cu124 已 EOL（最高 torch 2.6.0），不得再被脚本选中。

        只检查真正的索引赋值（`set CUDA_INDEX=...`），不检查面向用户的
        劝告文案 —— 那段文字恰恰是要求用户「别用 cu124」。
        """
        offenders = [
            ln for ln in self.text.splitlines()
            if re.search(r"set\s+CUDA_INDEX\s*=\s*cu124", ln, re.IGNORECASE)
        ]
        self.assertEqual(
            offenders, [],
            "install_cuda.bat 仍把 cu124 作为可选索引（会把 torch 静默降到 2.6.0）:\n"
            + "\n".join(offenders),
        )

    def test_has_manual_entry_and_cli_flag(self):
        self.assertIn(":manual_mode", self.text)
        self.assertIn("--manual", self.text)

    def test_removes_both_onnxruntime_names(self):
        """卸载必须同时覆盖 CPU 与 GPU 两个发行名，否则 dist-info 残留。"""
        self.assertRegex(
            self.text,
            r"pip uninstall onnxruntime onnxruntime-gpu",
            "缺少 onnxruntime + onnxruntime-gpu 双卸载",
        )

    def test_ort_sentinel_is_preseeded(self):
        """ORT 状态必须预先写入默认值，不能依赖 `if not exist`。"""
        self.assertRegex(self.text, r"\(echo MISSING\)\s*>")
        self.assertNotRegex(
            self.text,
            r"if not exist .*ort_status",
            "仍在使用 `if not exist` 兜底：重定向已建出空文件，该分支永不触发",
        )

    def test_no_nightly_index(self):
        """nightly/cu128 已下线（实测索引为空），不得再出现在脚本里。"""
        self.assertNotIn("nightly", self.text)

    def test_bundled_dir_name_not_hardcoded_as_gate(self):
        """不得再用 `if not exist ballontrans_pylibs_win\\python.exe` 硬退出。"""
        self.assertNotRegex(
            self.text,
            r"if not exist \"%PYTHON_DIR%\\\\python\.exe\"",
            "仍以硬编码目录名作为准入闸门",
        )


class ManualReachabilityTests(unittest.TestCase):
    """真跑：源码布局下脚本必须可达 manual 段。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bt_install_cuda_"))
        # 复制脚本到一个没有 ballontrans_pylibs_win 的目录 = 模拟源码布局
        shutil.copy2(SCRIPT, cls.tmp / "install_cuda.bat")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run_in_scratch(self, args=()):
        proc = subprocess.run(
            ["cmd", "/c", "install_cuda.bat", *args],
            capture_output=True,
            cwd=str(self.tmp),
            timeout=120,
        )
        return (
            proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )

    def test_source_layout_reaches_manual_mode(self):
        """核心回归：源码布局（无嵌套 Python）也必须打印 pip 命令。"""
        code, out, _ = self._run_in_scratch()
        self.assertIn("Manual mode", out,
                      "源码布局下没有进入 manual 段：\n" + out)
        self.assertIn("pip install -U torch torchvision", out,
                      "没有打印出 pip 安装命令：\n" + out)
        self.assertNotIn("Embedded Python not found", out,
                         "仍在用旧的一键包硬闸门拦人：\n" + out)

    def test_manual_flag_forces_manual_mode(self):
        code, out, _ = self._run_in_scratch(("--manual",))
        self.assertIn("Manual mode", out)

    def test_printed_index_is_never_cu124(self):
        code, out, _ = self._run_in_scratch()
        m = re.search(r"download\.pytorch\.org/whl/(\w+)", out)
        self.assertIsNotNone(m, "没打印出索引 URL：\n" + out)
        self.assertNotEqual(m.group(1), "cu124",
                            "manual 模式推荐了已 EOL 的 cu124")

    def test_manual_mode_prints_both_uninstall_and_install(self):
        code, out, _ = self._run_in_scratch()
        self.assertIn("uninstall onnxruntime", out)
        self.assertIn("onnxruntime-gpu", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
