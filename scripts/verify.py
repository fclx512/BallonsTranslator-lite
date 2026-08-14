#!/usr/bin/env python3
"""One-shot dev verification: syntax -> docs -> i18n -> qm -> smoke.

Combines the AGENTS.md 测试流程 steps into a single command so the AI runs
one tool call instead of five.  Prints a one-line summary per step on
success; on failure prints the full output so the error can be fixed
straight from the report.

Step activation:
  - syntax : always, over git-diff .py files (--all to scan ui/+utils/ instead)
  - docs   : always (validates doc file/symbol references via check_docs.py)
  - i18n   : always (scans whole ui/modules/utils; known orphans exempted)
  - qm     : only when a .ts file changed
  - smoke  : with --smoke, or automatically when a startup-chain file changed

Usage:
  python scripts/verify.py              # syntax(diff) + i18n + qm(if ts changed)
  python scripts/verify.py --smoke      # also run the startup smoke test
  python scripts/verify.py --all        # full syntax scan instead of git-diff files

Exit code: 0 all pass, 1 any step failed.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Startup-chain files: touching these auto-triggers the smoke test.
INIT_FILES = (
    "launch.py",
    "modules/base.py",
    "utils/profile_manager.py",
    "ui/configpanel.py",
    "ui/mainwindow.py",
)


def _reconfigure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _run(cmd, timeout=300):
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def changed_files():
    """Return tracked modifications + untracked files from git ([] on git error)."""
    tracked, untracked = [], []
    try:
        status = _run(["git", "status", "--porcelain"])
        if status.returncode != 0:
            return [], []
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            code, path = line[:2], line[3:]
            if " -> " in path:  # rename: "R  old -> new"
                path = path.split(" -> ")[1]
            if code.startswith("??"):
                continue  # untracked dirs are folded; expand via ls-files below
            if code.startswith(("D ", " D")):
                continue  # deleted, nothing to check
            tracked.append(path)
        ls = _run(["git", "ls-files", "--others", "--exclude-standard"])
        if ls.returncode == 0:
            untracked = [p for p in ls.stdout.splitlines() if p]
    except FileNotFoundError:
        return [], []
    return tracked, untracked


def _dump(result):
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())


def _orphan_count(text):
    """Extract the orphan count from i18n_check output (0 if absent)."""
    import re

    m = re.search(r"\[ORPHAN \.ts ENTRIES\] (\d+) active", text)
    return int(m.group(1)) if m else 0


def main():
    _reconfigure_stdout()
    parser = argparse.ArgumentParser(description="One-shot dev verification")
    parser.add_argument("--smoke", action="store_true", help="Force-run the startup smoke test")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Syntax-check all ui/+utils/ files instead of git-diff files",
    )
    args = parser.parse_args()

    tracked, untracked = changed_files()
    changed = [Path(p) for p in tracked + untracked]
    py_files = [p for p in changed if p.suffix == ".py"]
    ts_changed = any(p.suffix == ".ts" for p in changed)

    failures = 0

    # ── 1. Syntax ────────────────────────────────────────────────────────
    if args.all:
        label = "全量 ui/+utils/"
        cmd = [_py(), str(ROOT / "scripts" / "check_syntax.py")]
    elif py_files:
        label = f"{len(py_files)} 个改动文件"
        cmd = [_py(), str(ROOT / "scripts" / "check_syntax.py"), *(str(p) for p in py_files)]
    else:
        label, cmd = None, None
        print("⏭  语法: 无改动 .py 文件")
    if cmd:
        r = _run(cmd)
        if r.returncode == 0:
            print(f"✅ 语法: {label} 通过")
        else:
            failures += 1
            print(f"❌ 语法: 失败（{label}）")
            _dump(r)

    # ── 2. docs ──────────────────────────────────────────────────────────
    r = _run([_py(), str(ROOT / "scripts" / "check_docs.py")])
    if r.returncode == 0:
        print("✅ docs: 文档路径/符号引用有效")
    else:
        failures += 1
        print("❌ docs: 文档存在失效引用")
        _dump(r)

    # ── 3. i18n ──────────────────────────────────────────────────────────
    r = _run([_py(), str(ROOT / "scripts" / "i18n_check.py"), "--ci"])
    code = r.returncode
    if code == 0:
        print("✅ i18n: 通过（无硬编码中文/无缺失/无孤儿）")
    elif code & 3:
        failures += 1
        print(f"❌ i18n: 失败（退出码 {code}，1=硬编码 2=缺失 4=孤儿）")
        _dump(r)
    else:
        # Only the orphan bit. Project-wide orphans are known noise from
        # indirect canvas.tr()/self.tr(variable) calls, hand-maintained in
        # zh_CN.ts — count them but don't fail or dump 160+ lines.
        n = _orphan_count(r.stdout)
        print(
            f"⚠ i18n: {n} 条孤儿条目（间接 tr() 调用已知噪音，可忽略；"
            f"需核对时手动跑 i18n_check.py）"
        )

    # ── 4. qm ────────────────────────────────────────────────────────────
    if ts_changed:
        r = _run(
            [
                _py(),
                str(ROOT / "scripts" / "qm_compile.py"),
                str(ROOT / "translate" / "zh_CN.ts"),
                str(ROOT / "translate" / "zh_CN.qm"),
            ]
        )
        if r.returncode == 0:
            print("✅ qm: zh_CN.ts → zh_CN.qm 已编译")
        else:
            failures += 1
            print("❌ qm: 编译失败")
            _dump(r)
    else:
        print("⏭  qm: ts 无改动，跳过")

    # ── 5. Smoke ─────────────────────────────────────────────────────────
    hit = [p.as_posix() for p in changed if p.as_posix() in INIT_FILES]
    if args.smoke or hit:
        reason = "手动 --smoke" if args.smoke else f"改动命中启动链（{', '.join(hit)}）"
        r = _run([_py(), str(ROOT / "tests" / "test_startup_imports.py")])
        if r.returncode == 0:
            print(f"✅ 冒烟: 通过（{reason}）")
        else:
            failures += 1
            print(f"❌ 冒烟: 失败（{reason}）")
            _dump(r)
    else:
        print("⏭  冒烟: 未触发（未改启动链文件，可用 --smoke 强制）")

    print()
    if failures == 0:
        print("✅ verify: 全部通过")
    else:
        print(f"❌ verify: {failures} 步失败")
        sys.exit(1)


def _py():
    return sys.executable


if __name__ == "__main__":
    main()
