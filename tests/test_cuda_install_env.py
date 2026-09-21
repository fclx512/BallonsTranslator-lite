"""install_cuda.bat / CUDA 索引选择的回归台。

分层设计（见 `docs/技术实现/CUDA环境与索引_说明.md` 的「三层验证台」一节）：

  L1 静态检查   —— 读脚本/映射源码，断言「不该再出现的写法」与
                   「脚本与 Python 侧映射必须一致」。秒级、不联网。
  L2 离网模拟   —— 用 `tests/cuda_sandbox.py` 造一个假环境，把脚本的
                   各条分支（CC 档位、ORT 状态、降级保护、失败退出）
                   逐个跑通。不联网、不装东西。
  L3 真环境     —— 在真解释器/真索引上验证（另见 `scripts/check_cuda_env.py`）。

本文件覆盖 L1 + L2。设计目标：把「普通用户无法自救」的故障挡在发版前 ——
每一项都对应一次真实事故或一次差点漏掉的缺陷。
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

from cuda_sandbox import Sandbox, StubConfig  # noqa: E402

INSTALL_SCRIPT = REPO_ROOT / "install_cuda.bat"
ENV_DIAG = REPO_ROOT / "utils" / "env_diagnostic.py"


# ══════════════════════════════════════════════════════════════════════
# L1 静态检查
# ══════════════════════════════════════════════════════════════════════


class ScriptStaticTests(unittest.TestCase):
    """静态检查：不需要真跑脚本，任何环境都能跑。"""

    @classmethod
    def setUpClass(cls):
        cls.text = INSTALL_SCRIPT.read_text(encoding="utf-8", errors="replace")

    def test_no_cu124_index_is_ever_selected(self):
        """cu124 已 EOL（最高 torch 2.6.0），不得再作为可选索引。

        只检查真正的赋值语句；面向用户的「别用 cu124」劝告文案要保留。
        """
        offenders = [
            ln for ln in self.text.splitlines()
            if re.search(r"set\s+CUDA_INDEX\s*=\s*cu124", ln, re.IGNORECASE)
        ]
        self.assertEqual(
            offenders, [],
            "install_cuda.bat 仍把 cu124 作为可选索引"
            "（会把 torch 静默降到 2.6.0）:\n" + "\n".join(offenders),
        )

    def test_no_nightly_index(self):
        """nightly/cu128 索引已下线（实测为空），不得再出现。"""
        self.assertNotIn(
            "nightly", self.text,
            "install_cuda.bat 仍引用 nightly 索引（已下线，装不上）",
        )

    def test_all_quoted_interpreter_calls_use_call(self):
        """所有带引号路径的 for /f 调用必须写 `call`。

        缺 call 时 cmd 会把引号内的路径当成一个命令名；路径含空格
        （用户把一键包放进 "D:\\My Tools\\"）就会静默失败、CC 判成 0，
        表现为「明明有显卡却说检测不到」。
        """
        bad = []
        for ln in self.text.splitlines():
            stripped = ln.lstrip()
            # 注释行（:: 或 rem）里说明这条约定时也会出现同样的字形，
            # 不能当成真调用。
            if stripped.startswith("::") or stripped.lower().startswith("rem "):
                continue
            if "for /f" not in ln:
                continue
            # 找 for /f 里被引号包住的解释器调用
            for m in re.finditer(r'`\s*"', ln):
                start = m.start()
                segment = ln[start:start + 12]
                if "call" not in ln[:start]:
                    bad.append(ln.strip()[:100])
                    break
        self.assertEqual(
            bad, [],
            "以下 for /f 调用缺 `call`，路径含空格时会静默失败:\n" + "\n".join(bad),
        )

    def test_ort_sentinel_preseeded_not_if_not_exist(self):
        """ORT 状态哨兵必须预写默认值，不能依赖 `if not exist`。

        `>` 重定向在解析阶段就建出空文件，因此 `if not exist` 永不触发；
        而 `set /p` 读空文件会留下未定义变量，既不等于 GOOD 也不等于
        MISSING，脚本会走错分支去重装 onnxruntime-gpu。
        """
        self.assertRegex(self.text, r"\(echo MISSING\)\s*>")
        self.assertNotRegex(
            self.text, r"if not exist .*ort_status",
            "仍用 `if not exist` 兜底 ORT 状态（该分支永不触发）",
        )

    def test_uninstalls_both_onnxruntime_names(self):
        """卸载必须同时覆盖 CPU 与 GPU 两个发行名。

        onnxruntime 与 onnxruntime-gpu 共用同一 import 名与 capi DLL，
        只卸一个会留下混合状态：`pip uninstall onnxruntime` 找不到自己
        的 dist-info 而拒绝卸载，CUDA DLL 永远清不干净。
        """
        self.assertRegex(
            self.text, r"pip uninstall onnxruntime onnxruntime-gpu",
            "缺少 onnxruntime + onnxruntime-gpu 双卸载",
        )

    def test_manual_mode_exists_and_is_reachable_without_bundle(self):
        """源码用户必须在「没有 ballontrans_pylibs_win」时也能拿到命令。"""
        self.assertIn(":manual_mode", self.text)
        self.assertIn("--manual", self.text)
        # 不得再用硬编码目录名当准入闸门
        self.assertNotRegex(
            self.text, r"if not exist \"%PYTHON_DIR%",
            "仍以硬编码目录名作为准入闸门",
        )

    def test_cc_guard_accepts_decimal(self):
        """CC 守卫必须容忍 "12.0" 这种带小数点的输出。

        旧守卫用 `^[0-9][0-9]*$`，遇到 "12.0" 直接判非法并清成 0，
        是「有显卡却报检测不到」的另一个成因。
        """
        guard = [ln for ln in self.text.splitlines()
                 if "findstr /R" in ln and "^[0-9]" in ln]
        self.assertTrue(guard, "找不到 CC 守卫语句")
        for ln in guard:
            self.assertIn(
                ".", ln,
                "CC 守卫不接受小数点，会把 12.0 误判为非法: " + ln.strip(),
            )


class MappingConsistencyTests(unittest.TestCase):
    """`.bat` 与 Python 侧的 CC→CUDA 映射必须一致。

    两套映射曾经打架：脚本按 CC 给 40 系选 cu126，而
    `utils/env_diagnostic.py` 按型号给 40 系选 cu124 —— 用户照哪个改都
    有一部分是对的，正是「静默降级」的根源。
    """

    @classmethod
    def setUpClass(cls):
        cls.bat = INSTALL_SCRIPT.read_text(encoding="utf-8", errors="replace")
        sys.path.insert(0, str(REPO_ROOT))
        from utils.env_diagnostic import pick_cuda_index  # noqa: E402
        cls.pick = staticmethod(pick_cuda_index)

    def _bat_tier(self, cc: int) -> str:
        """从脚本源码里解析出某 CC 档位对应的索引。"""
        # 形如：) else if !GPU_CC! GEQ 9 (\n    set CUDA_INDEX=cu130
        pairs = re.findall(
            r"GEQ\s+(\d+)\s*\(\s*\r?\n\s*set CUDA_INDEX=(\w+)", self.bat
        )
        for geq, idx in sorted(((int(a), b) for a, b in pairs), reverse=True):
            if cc >= geq:
                return idx
        return ""

    def test_no_tier_is_cu124_on_either_side(self):
        for cc in range(0, 15):
            self.assertNotEqual(
                self._bat_tier(cc), "cu124",
                f"脚本在 CC {cc} 档位选了 cu124",
            )
            t = self.pick(cc)
            self.assertNotEqual(
                (t or {}).get("index_tag"), "cu124",
                f"env_diagnostic 在 CC {cc} 档位选了 cu124",
            )

    def test_bat_and_python_agree_on_every_tier(self):
        """两侧对同一 CC 必须给出同一个索引。"""
        mismatches = []
        for cc in range(0, 15):
            bat_idx = self._bat_tier(cc)
            info = self.pick(cc)
            py_idx = (info or {}).get("index_tag", "")
            if bat_idx != py_idx:
                mismatches.append(
                    f"  CC {cc}: bat={bat_idx!r} python={py_idx!r}"
                )
        self.assertEqual(
            mismatches, [],
            "install_cuda.bat 与 utils/env_diagnostic.py 的映射不一致:\n"
            + "\n".join(mismatches),
        )

    def test_bat_and_python_agree_on_oldest_supported_cc(self):
        """CC < 6 两侧都应判为不支持。"""
        self.assertEqual(self._bat_tier(3), "")
        self.assertIsNone(self.pick(3))

    def test_nightly_gone_from_python_side_too(self):
        src = ENV_DIAG.read_text(encoding="utf-8", errors="replace")
        live = [
            ln for ln in src.splitlines()
            if "nightly" in ln and not ln.strip().startswith("#")
        ]
        self.assertEqual(
            live, [],
            "env_diagnostic.py 仍在使用已下线的 nightly 索引:\n"
            + "\n".join(live),
        )


class ReadmeTests(unittest.TestCase):
    """README 不得再把人引到已失效的索引。"""

    def _docs(self):
        return [
            p for p in (
                REPO_ROOT / "README.md",
                REPO_ROOT / "README_EN.md",
                REPO_ROOT / "README（修改中）.md",
            ) if p.exists()
        ]

    def test_no_readme_hands_out_cu124_install_command(self):
        bad = []
        for p in self._docs():
            for i, ln in enumerate(p.read_text(encoding="utf-8",
                                                errors="replace").splitlines(), 1):
                if "pip install" in ln and "cu124" in ln:
                    bad.append(f"{p.name}:{i}: {ln.strip()}")
        self.assertEqual(bad, [], "README 仍在推荐 cu124 安装命令:\n"
                                  + "\n".join(bad))

    def test_no_readme_points_at_dead_nightly(self):
        bad = []
        for p in self._docs():
            txt = p.read_text(encoding="utf-8", errors="replace")
            for i, ln in enumerate(txt.splitlines(), 1):
                if "nightly" in ln and "pip" in ln:
                    bad.append(f"{p.name}:{i}: {ln.strip()}")
        self.assertEqual(bad, [], "README 仍指向已下线的 nightly 索引:\n"
                                  + "\n".join(bad))

    def test_source_users_told_not_to_use_the_bat_naively(self):
        """源码用户章节必须点明 bat 默认装进嵌入式环境。"""
        p = REPO_ROOT / "README.md"
        if not p.exists():
            self.skipTest("README.md 不存在")
        txt = p.read_text(encoding="utf-8", errors="replace")
        self.assertIn("--manual", txt,
                      "README 没告诉源码用户可以用 --manual 拿命令")


# ══════════════════════════════════════════════════════════════════════
# L2 离网模拟
# ══════════════════════════════════════════════════════════════════════


class SandboxReachabilityTests(unittest.TestCase):
    """源码布局（无嵌入式 Python）下脚本仍必须可达 manual 段。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bt_src_layout_"))
        shutil.copy2(INSTALL_SCRIPT, cls.tmp / "install_cuda.bat")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, args=()):
        p = subprocess.run(
            ["cmd", "/c", "install_cuda.bat", *args],
            capture_output=True, cwd=str(self.tmp), timeout=120,
            input=b"\r\n",
        )
        return p.returncode, p.stdout.decode("utf-8", errors="replace")

    def test_source_layout_reaches_manual_mode(self):
        _rc, out = self._run()
        self.assertIn("Manual mode", out, "源码布局下没进 manual 段:\n" + out)
        self.assertIn("pip install -U torch torchvision", out,
                      "没打印 pip 命令:\n" + out)
        self.assertNotIn("Embedded Python not found", out,
                         "仍在用一键包硬闸门拦人:\n" + out)

    def test_manual_flag_forces_manual_mode(self):
        _rc, out = self._run(("--manual",))
        self.assertIn("Manual mode", out)

    def test_help_is_available(self):
        _rc, out = self._run(("--help",))
        self.assertIn("--manual", out)


class SandboxBranchTests(unittest.TestCase):
    """用假环境驱动脚本的每一条分支。"""

    def setUp(self):
        self._boxes = []

    def tearDown(self):
        for b in self._boxes:
            b.cleanup()

    def box(self, **kw) -> Sandbox:
        cfg = StubConfig(**kw)
        sb = Sandbox(cfg)
        self._boxes.append(sb)
        return sb

    # -- CC 档位 ---------------------------------------------------------

    def _index_for_cc(self, cc: str) -> str:
        sb = self.box(compute_cap=cc, ort_providers="CUDAExecutionProvider")
        out = sb.run().stdout_text
        m = re.search(r"Selected CUDA:.*?\((cu\d+)\)", out)
        return m.group(1) if m else ""

    def test_cc12_selects_cu132(self):
        self.assertEqual(self._index_for_cc("12.0"), "cu132")

    def test_cc9_selects_cu130(self):
        self.assertEqual(self._index_for_cc("9.0"), "cu130")

    def test_cc8_selects_cu126(self):
        self.assertEqual(self._index_for_cc("8.9"), "cu126")

    def test_cc7_selects_cu126(self):
        self.assertEqual(self._index_for_cc("7.5"), "cu126")

    def test_cc6_selects_cu126(self):
        self.assertEqual(self._index_for_cc("6.1"), "cu126")

    def test_cc5_is_unsupported_and_keeps_cpu_torch(self):
        sb = self.box(compute_cap="5.2")
        out = sb.run().stdout_text
        self.assertIn("No supported NVIDIA GPU", out)
        self.assertEqual(sb.pip_calls(), [], "不支持的显卡不该触发任何安装")

    def test_no_gpu_is_unsupported(self):
        sb = self.box(compute_cap="")
        out = sb.run().stdout_text
        self.assertIn("No supported NVIDIA GPU", out)
        self.assertEqual(sb.pip_calls(), [])

    # -- 降级保护 --------------------------------------------------------

    def test_downgrade_is_reported(self):
        """索引装出来的 torch 比原来旧时，必须明确告警。"""
        sb = self.box(
            torch_version="2.13.0+cpu",
            torch_version_after_install="2.6.0+cu124",
            downgrade_result="1",
        )
        out = sb.run().stdout_text
        self.assertIn("DOWNGRADED", out, "降级没有被报告:\n" + out)

    def test_no_downgrade_no_warning(self):
        sb = self.box(
            torch_version="2.13.0+cpu",
            torch_version_after_install="2.14.0+cu132",
            downgrade_result="0",
        )
        out = sb.run().stdout_text
        self.assertNotIn("DOWNGRADED", out)

    # -- ORT 状态 --------------------------------------------------------

    def test_ort_missing_still_installs_gpu_variant(self):
        """ORT 探测失败（空输出）必须落到 MISSING，而不是空串走错分支。"""
        sb = self.box(ort_providers="")
        out = sb.run().stdout_text
        self.assertIn("MISSING", out,
                      "ORT 状态没落到 MISSING（空串会让分支错判）:\n" + out)

    def test_ort_already_good_skips_install(self):
        sb = self.box(ort_providers="CUDAExecutionProvider,CPUExecutionProvider")
        out = sb.run().stdout_text
        self.assertIn("GOOD", out)
        self.assertNotIn("uninstall onnxruntime", out,
                         "已满足时不该再动 ORT:\n" + out)

    def test_ort_cpu_variant_is_replaced(self):
        sb = self.box(ort_providers="CPUExecutionProvider")
        out = sb.run().stdout_text
        self.assertIn("CPU", out)
        calls = " ".join(sb.pip_calls())
        self.assertIn("uninstall onnxruntime onnxruntime-gpu", calls,
                      "替换 ORT 时必须两个发行名都卸:\n" + calls)

    # -- CUDA torch 已就位时跳过 ------------------------------------------

    def test_existing_cuda_torch_is_skipped(self):
        sb = self.box(
            torch_version="2.13.0+cu132",
            ort_providers="CUDAExecutionProvider",
        )
        out = sb.run().stdout_text
        self.assertIn("already installed", out)
        self.assertEqual(sb.pip_calls(), [], "不该重复装 torch:\n"
                         + str(sb.pip_calls()))

    # -- 安装命令形态 -----------------------------------------------------

    def test_torch_command_has_no_version_pin(self):
        sb = self.box()
        sb.run()
        calls = " ".join(sb.pip_calls())
        self.assertNotIn("torch==", calls, "不应钉死 torch 版本:\n" + calls)
        self.assertNotIn("torchaudio", calls,
                         "不应装 torchaudio（新索引不提供）:\n" + calls)
        self.assertIn("torch torchvision", calls)

    def test_pip_failure_exits_nonzero(self):
        sb = self.box(pip_exit=3)
        proc = sb.run()
        self.assertNotEqual(proc.returncode, 0,
                           "pip 失败时脚本应以非 0 退出")


if __name__ == "__main__":
    unittest.main(verbosity=2)
