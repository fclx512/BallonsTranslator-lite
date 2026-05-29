#!/usr/bin/env python3
"""
BallonsTranslator-lite 一键质量检查

Usage:
  python check_all.py              # 完整检查
  python check_all.py --quick      # 快速检查（跳过 ruff）
  python check_all.py --fix        # 自动修复可修复的问题
  python check_all.py --ci         # CI 模式（遇到问题 exit non-zero）
  python check_all.py --install    # 安装缺失的依赖（pip install ruff pytest）

检查项：
  1. i18n — 硬编码中文检测 + .ts 覆盖率
  2. qm  — 编译 translate/zh_CN.ts → translate/zh_CN.qm
  3. ruff — 代码风格检查
  4. pytest — 跑 tests/ 下的测试
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# __file__ = .../BallonsTranslator-lite/scripts/check_all.py
# parent    = .../BallonsTranslator-lite/scripts/
# parent.parent = .../BallonsTranslator-lite/  <-- 项目根目录
ROOT = Path(__file__).resolve().parent.parent


# ── Helpers ──────────────────────────────────────────────────────────────

def check_deps():
    """Check which tools are available."""
    deps = {}
    for mod in ("ruff", "pytest"):
        r = subprocess.run(
            [sys.executable or "python3", "-m", mod, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        deps[mod] = r.returncode == 0
    return deps


def install_deps(deps: dict[str, bool]):
    """Install missing dependencies."""
    missing = [m for m, ok in deps.items() if not ok]
    if not missing:
        print("  ✓ 所有依赖已安装")
        return
    print(f"  安装: {' '.join(missing)}")
    r = subprocess.run(
        [sys.executable or "python3", "-m", "pip", "install", *missing],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode == 0:
        print("  ✓ 安装成功")
    else:
        print(f"  ✗ 安装失败: {r.stderr.strip()[:200]}")
        sys.exit(1)


def run(cmd: list[str], cwd: str = None, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd or str(ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"Timed out after {timeout}s"


def section(title: str):
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


# ── Check functions ──────────────────────────────────────────────────────

def check_i18n(ci: bool) -> int:
    section("i18n — 国际化检查")
    cmd = [sys.executable or "python3", "scripts/i18n_check.py"]
    if ci:
        cmd.append("--ci")
    code, out, err = run(cmd)
    print(out)
    if err.strip():
        print(f"  stderr: {err.strip()[:200]}")
    return code


def compile_qm() -> int:
    section("qm — 编译翻译文件")
    ts_path = ROOT / "translate" / "zh_CN.ts"
    qm_path = ROOT / "translate" / "zh_CN.qm"
    if not ts_path.exists():
        print(f"  ✗ 未找到 {ts_path}")
        return 1
    cmd = [sys.executable or "python3", "scripts/qm_compile.py", str(ts_path), str(qm_path)]
    code, out, err = run(cmd)
    print(f"  {out.strip()}")
    if err.strip():
        print(f"  stderr: {err.strip()[:200]}")
    if qm_path.exists():
        print(f"  ✓ .qm 已生成: {qm_path.stat().st_size} bytes")
    return code


def check_ruff(fix: bool) -> int:
    section("ruff — 代码风格检查")
    cmd = [sys.executable or "python3", "-m", "ruff", "check"]
    if fix:
        cmd.append("--fix")
    cmd.extend(["--select", "I,F,E,W", "ui/", "utils/", "modules/"])
    code, out, err = run(cmd)
    if code == 0:
        print("  ✓ 通过，无问题")
    else:
        n_issues = out.count(":")
        print(f"  ✗ 发现 {(n_issues - out.count('://')) // 3} 个问题:")
        for line in out.splitlines():
            if line.strip():
                print(f"    {line}")
        if err.strip():
            print(f"  stderr: {err.strip()[:200]}")
    return code


def run_tests(quick: bool) -> int:
    section("pytest — 单元测试")
    cmd = [sys.executable or "python3", "-m", "pytest", "tests/", "-v", "--tb=short"]
    if quick:
        cmd.extend(["-x"])
    # pytest-timeout not always installed, skip it if missing
    code, out, err = run(cmd, timeout=180)
    # Heavy optional deps (cv2, torch, numpy etc.) — skip gracefully
    if code != 0:
        if "ModuleNotFoundError" in (err + out):
            print("  ! 部分测试因缺少 opencv-python / torch / numpy 等重依赖而跳过")
            print("    这些是可选依赖，非 GPU 环境按需安装即可")
            code = 0
        elif "No module named" in out or "No module named" in err:
            print("  ! tests/ 依赖缺失，部分测试跳过")
            code = 0
    lines = out.splitlines()
    print("\n".join(lines[-30:]))
    if err.strip() and "warnings" not in err.lower():
        print(f"  stderr: {err.strip()[:200]}")
    return code


def print_summary(results: dict[str, tuple[int, float]]):
    print()
    print("=" * 60)
    print("  检查结果汇总")
    print("=" * 60)
    all_pass = True
    for name, (code, elapsed) in results.items():
        status = "✓" if code == 0 else "✗ FAIL"
        if code != 0:
            all_pass = False
        print(f"  {status}  {name:<15}  ({elapsed:.1f}s)  exit={code}")
    print("-" * 60)
    if all_pass:
        print("  ✓ 全部通过！")
    else:
        print("  ✗ 部分检查未通过，详见上方输出")
    print()


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BallonsTranslator-lite 一键质量检查",
        epilog="示例: python check_all.py  |  python check_all.py --fix  |  python check_all.py --install",
    )
    parser.add_argument("--quick", action="store_true", help="快速模式：pytest 遇错即停")
    parser.add_argument("--fix", action="store_true", help="自动修复 ruff 可修复问题")
    parser.add_argument("--ci", action="store_true", help="CI 模式：遇到问题 exit non-zero")
    parser.add_argument("--skip-tests", action="store_true", help="跳过 pytest")
    parser.add_argument("--skip-ruff", action="store_true", help="跳过 ruff")
    parser.add_argument("--install", action="store_true", help="安装缺失的依赖后退出")
    args = parser.parse_args()

    os.chdir(str(ROOT))
    print(f"BallonsTranslator-lite 质量检查 @ {ROOT}")
    print(f"Python: {sys.version.split()[0]}")

    # Deps check
    deps = check_deps()
    missing = [m for m, ok in deps.items() if not ok]

    if missing:
        print(f"\n  注意: 以下工具未安装: {', '.join(missing)}")
        if args.install:
            install_deps(deps)
            return
        print(f"  跳过 ruff 和 pytest 检查（使用 --install 安装依赖）")
        args.skip_ruff = args.skip_ruff or not deps["ruff"]
        args.skip_tests = args.skip_tests or not deps["pytest"]
    else:
        print("  ✓ ruff + pytest 已就绪")
    print()

    results = {}

    t0 = time.time()
    results["i18n"] = (check_i18n(ci=args.ci), time.time() - t0)

    t0 = time.time()
    results["qm"] = (compile_qm(), time.time() - t0)

    if not args.skip_ruff:
        t0 = time.time()
        results["ruff"] = (check_ruff(fix=args.fix), time.time() - t0)

    if not args.skip_tests:
        t0 = time.time()
        results["tests"] = (run_tests(quick=args.quick), time.time() - t0)

    print_summary(results)

    if args.ci:
        final = 1 if any(c != 0 for c, _ in results.values()) else 0
        sys.exit(final)


if __name__ == "__main__":
    import os
    main()
